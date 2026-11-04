"""Registered business flows and immutable, evidence-timestamped impact reports."""
from __future__ import annotations

import json
import sqlite3
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .config_history import connect
from .panos_evidence import attach_policy
from .topology import find_path, load_snapshot


class BusinessFlow(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    source_ip: str = Field(max_length=64)
    destination_ip: str = Field(max_length=64)
    protocol: str = Field(default="any", pattern="^(any|tcp|udp|icmp|icmpv6)$")
    source_port: int | None = Field(default=None, ge=0, le=65535)
    destination_port: int | None = Field(default=None, ge=0, le=65535)
    application: str | None = Field(default=None, max_length=128)
    ingress_interface: str | None = Field(default=None, max_length=128)
    vrf: str = Field(default="all", max_length=128)


class FaultScenario(BaseModel):
    kind: Literal["device_down", "interface_down", "route_removed"]
    target_id: str = Field(min_length=1, max_length=128)


def router():
    api = APIRouter(tags=["Business flows"])

    @api.on_event("startup")
    def initialize():
        with connect() as db:
            db.executescript("""
              CREATE TABLE IF NOT EXISTS business_flows (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, definition TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0);
              CREATE TABLE IF NOT EXISTS business_impact_reports (
                id TEXT PRIMARY KEY, flow_id TEXT NOT NULL, flow_name TEXT NOT NULL,
                created_at TEXT NOT NULL, snapshot_at TEXT, report TEXT NOT NULL);
              CREATE INDEX IF NOT EXISTS business_reports_flow ON business_impact_reports(flow_id,created_at);
            """)

    @api.get("/business", response_class=HTMLResponse)
    def page():
        return PAGE

    @api.get("/api/business/flows")
    def list_flows():
        with connect() as db:
            rows = db.execute("SELECT id,name,definition,created_at,updated_at,archived FROM business_flows ORDER BY name").fetchall()
        return {"flows": [{**dict(r), "definition": json.loads(r["definition"])} for r in rows]}

    @api.post("/api/business/flows")
    def create_flow(request: BusinessFlow):
        now, flow_id = datetime.now(timezone.utc).isoformat(), uuid.uuid4().hex
        definition = request.model_dump()
        with connect() as db:
            db.execute("INSERT INTO business_flows(id,name,definition,created_at,updated_at) VALUES(?,?,?,?,?)",
                       (flow_id, request.name, json.dumps(definition, ensure_ascii=False), now, now))
        return {"id": flow_id, "name": request.name, "definition": definition, "created_at": now}

    @api.put("/api/business/flows/{flow_id}")
    def update_flow(flow_id: str, request: BusinessFlow):
        now = datetime.now(timezone.utc).isoformat()
        with connect() as db:
            cur = db.execute("UPDATE business_flows SET name=?,definition=?,updated_at=? WHERE id=? AND archived=0",
                             (request.name, json.dumps(request.model_dump(), ensure_ascii=False), now, flow_id))
            if not cur.rowcount:
                raise HTTPException(404, "Business flow not found or archived")
        return {"id": flow_id, "name": request.name, "definition": request.model_dump(), "updated_at": now}

    @api.post("/api/business/flows/{flow_id}/archive")
    def archive_flow(flow_id: str):
        with connect() as db:
            cur = db.execute("UPDATE business_flows SET archived=1,updated_at=? WHERE id=? AND archived=0",
                             (datetime.now(timezone.utc).isoformat(), flow_id))
        if not cur.rowcount:
            raise HTTPException(404, "Business flow not found")
        return {"status": "archived", "id": flow_id}

    @api.post("/api/business/flows/{flow_id}/analyze")
    def analyze(flow_id: str):
        with connect() as db:
            row = db.execute("SELECT name,definition,archived FROM business_flows WHERE id=?", (flow_id,)).fetchone()
        if not row or row["archived"]:
            raise HTTPException(404, "Active business flow not found")
        snapshot = load_snapshot()
        if snapshot is None:
            raise HTTPException(503, "No topology snapshot is available")
        definition = json.loads(row["definition"])
        evidenced = attach_policy(snapshot)
        path = _evaluate(evidenced, definition)
        report = {"status": path.get("status", "incomplete"), "flow_id": flow_id,
                  "flow_name": row["name"], "flow": definition,
                  "created_at": datetime.now(timezone.utc).isoformat(),
                  "conclusion": _conclusion(path, "当前采集到的网络状态"),
                  "baseline": {"status": path.get("status", "incomplete"), "path_result": path,
                               "conclusion": _conclusion(path, "当前采集到的网络状态")},
                  "scenario": None,
                  "evidence": {"scope": "lab", "topology_observed_at": snapshot.observed_at,
                               "snapshot_schema": snapshot.schema_version,
                               "policy_evidence": [{"device": d.get("name"), "device_id": d.get("id"),
                                                    "status": (d.get("pa_policy") or {}).get("status"),
                                                    "observed_at": (d.get("pa_policy") or {}).get("observed_at") or (d.get("pa_policy") or {}).get("last_success_at"),
                                                    "config_digest": (d.get("pa_policy") or {}).get("config_digest")}
                                                   for d in evidenced.devices if d.get("platform") == "paloalto_panos"]},
                  "path_result": path,
                  "limitations": ["推演基于最新已采集拓扑和策略证据，不是实时探测或流量交付证明。",
                                  "仅使用 Nautobot site=lab 范围内的设备与证据。",
                                  "请检查报告中的告警、候选端点和证据时间戳；缺少或过期证据会导致结果不完整。"]}
        report_id = uuid.uuid4().hex
        with connect() as db:
            db.execute("INSERT INTO business_impact_reports(id,flow_id,flow_name,created_at,snapshot_at,report) VALUES(?,?,?,?,?,?)",
                       (report_id, flow_id, row["name"], report["created_at"], snapshot.observed_at,
                        json.dumps(report, ensure_ascii=False)))
        return {"id": report_id, **report}

    @api.post("/api/business/flows/{flow_id}/scenarios")
    def analyze_scenario(flow_id: str, scenario: FaultScenario):
        with connect() as db:
            row = db.execute("SELECT name,definition,archived FROM business_flows WHERE id=?", (flow_id,)).fetchone()
        if not row or row["archived"]:
            raise HTTPException(404, "Active business flow not found")
        snapshot = load_snapshot()
        if snapshot is None:
            raise HTTPException(503, "No topology snapshot is available")
        definition = json.loads(row["definition"])
        evidenced = attach_policy(snapshot)
        baseline = _evaluate(evidenced, definition)
        simulated = deepcopy(evidenced)
        if scenario.kind == "device_down":
            device = next((d for d in simulated.devices if d.get("id") == scenario.target_id and d.get("site") == "lab"), None)
            if not device:
                raise HTTPException(404, "Lab device not found in current topology")
            device["up"] = False
            target_label = device.get("name", scenario.target_id)
            fault_label = f"模拟设备离线：{target_label}"
        elif scenario.kind == "interface_down":
            interface = next((i for i in simulated.interfaces if i.get("id") == scenario.target_id), None)
            device = next((d for d in simulated.devices if interface and d.get("id") == interface.get("device_id")), None)
            if not interface or not device or device.get("site") != "lab":
                raise HTTPException(404, "Lab interface not found in current topology")
            interface["oper_up"] = False
            target_label = f"{device.get('name')}:{interface.get('name')}"
            fault_label = f"模拟接口中断：{target_label}"
        else:
            route = next((r for r in simulated.routes if r.get("id") == scenario.target_id), None)
            device = next((d for d in simulated.devices if route and d.get("device_id") == route.get("device_id")), None)
            if not route or not device or device.get("site") != "lab":
                raise HTTPException(404, "Lab route not found in current topology")
            simulated.routes = [r for r in simulated.routes if r.get("id") != scenario.target_id]
            target_label = f"{device.get('name')} · {route.get('vrf')} · {route.get('prefix')}"
            fault_label = f"模拟配置变更：移除路由 {target_label}"
        impacted = _evaluate(simulated, definition)
        report = {"status": impacted.get("status", "incomplete"), "flow_id": flow_id,
                  "flow_name": row["name"], "flow": definition,
                  "created_at": datetime.now(timezone.utc).isoformat(),
                  "conclusion": _scenario_conclusion(baseline, impacted, fault_label),
                  "baseline": {"status": baseline.get("status", "incomplete"), "path_result": baseline,
                               "conclusion": _conclusion(baseline, "故障前基线")},
                  "scenario": {"kind": scenario.kind, "target_id": scenario.target_id,
                               "description": fault_label, "simulated": True,
                               "status": impacted.get("status", "incomplete"),
                               "impact": _impact_status(baseline, impacted), "path_result": impacted,
                               "conclusion": _conclusion(impacted, "故障推演后")},
                  "evidence": {"scope": "lab", "topology_observed_at": snapshot.observed_at,
                               "snapshot_schema": snapshot.schema_version},
                  "limitations": ["故障为基于当前 lab 快照的离线模拟，不会改动设备配置或状态。",
                                  "路径推演不等于真实流量探测；证据缺失或过期时结论为不完整。"]}
        report_id = uuid.uuid4().hex
        with connect() as db:
            db.execute("INSERT INTO business_impact_reports(id,flow_id,flow_name,created_at,snapshot_at,report) VALUES(?,?,?,?,?,?)",
                       (report_id, flow_id, row["name"], report["created_at"], snapshot.observed_at,
                        json.dumps(report, ensure_ascii=False)))
        return {"id": report_id, **report}

    @api.get("/api/business/reports")
    def list_reports(flow_id: str | None = None):
        with connect() as db:
            if flow_id:
                rows = db.execute("SELECT id,flow_id,flow_name,created_at,snapshot_at FROM business_impact_reports WHERE flow_id=? ORDER BY created_at DESC LIMIT 100", (flow_id,)).fetchall()
            else:
                rows = db.execute("SELECT id,flow_id,flow_name,created_at,snapshot_at FROM business_impact_reports ORDER BY created_at DESC LIMIT 100").fetchall()
        return {"reports": [dict(r) for r in rows]}

    @api.get("/api/business/reports/{report_id}")
    def get_report(report_id: str):
        with connect() as db:
            row = db.execute("SELECT report FROM business_impact_reports WHERE id=?", (report_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Impact report not found")
        return {"id": report_id, **json.loads(row["report"])}

    return api


def _evaluate(snapshot, definition):
    return find_path(snapshot, definition["source_ip"], definition["destination_ip"],
                     definition["vrf"], site="lab", analysis="flow",
                     protocol=definition["protocol"], source_port=definition["source_port"],
                     destination_port=definition["destination_port"], application=definition["application"],
                     ingress_interface=definition["ingress_interface"])


def _conclusion(path, prefix):
    status = path.get("status", "incomplete")
    steps = path.get("path", []) or [step for branch in path.get("paths", []) for step in branch.get("path", [])]
    devices = []
    for step in steps:
        name = step.get("device") or step.get("from")
        if name and (not devices or devices[-1] != name):
            devices.append(name)
    reason = next((branch.get("reason") for branch in path.get("paths", []) if branch.get("reason")), None)
    if status == "ok":
        outcome = "路径可推演通过"
    elif status == "blocked":
        outcome = "策略明确阻断"
    elif status in {"not_found", "invalid"}:
        outcome = "端点或输入无法解析"
    elif status == "stale":
        outcome = "证据过期或设备采集异常，无法判断"
    else:
        outcome = "路径不完整或无法连通"
    route = " → ".join(devices) if devices else "未形成可确认路径"
    detail = f"；原因：{reason}" if reason else ""
    return f"{prefix}：{outcome}。路径：{route}{detail}。"


def _scenario_conclusion(baseline, impacted, fault_label):
    before = baseline.get("status", "incomplete")
    after = impacted.get("status", "incomplete")
    if before == "ok" and after != "ok":
        if fault_label.startswith("模拟设备离线"):
            effect = "业务路径受影响：该设备离线后，无法沿基线路径继续转发"
        elif fault_label.startswith("模拟接口中断"):
            effect = "业务路径受影响：该接口中断后，基线路径不可用"
        else:
            effect = "业务路径受影响：移除这条路由后，原路径无法继续匹配"
    elif before == "ok" and after == "ok":
        effect = "推演仍找到可用路径，存在绕行或冗余路径"
    elif before != "ok":
        effect = "当前基线本身未能确认可用，暂不能归因该故障的新增影响"
    else:
        effect = "故障情景未显示路径状态变化"
    return f"{fault_label}。{effect}。故障后路径：{_path_summary(impacted)}。"


def _path_summary(path):
    steps = path.get("path", []) or [step for branch in path.get("paths", []) for step in branch.get("path", [])]
    devices = []
    for step in steps:
        name = step.get("device") or step.get("from")
        if name and (not devices or devices[-1] != name):
            devices.append(name)
    return " → ".join(devices) if devices else "未形成可确认路径"


def _impact_status(baseline, impacted):
    before, after = baseline.get("status"), impacted.get("status")
    if before != "ok":
        return "uncertain"
    return "ok" if after == "ok" else "impacted"


PAGE = r'''<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>业务登记 · AIOps</title>
<style>body{font:15px system-ui;background:#0b1220;color:#e5edf8;max-width:1100px;margin:32px auto;padding:0 18px}section,article,.result{background:#142238;border:1px solid #2b405d;border-radius:10px;padding:18px;margin:14px 0}input,select,button{font:inherit;padding:9px;margin:4px;background:#0c1728;color:inherit;border:1px solid #425875;border-radius:6px;max-width:100%}input{min-width:210px}button,a{cursor:pointer;color:#8ec5ff}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr))}small,.muted{color:#a8b9cf}.conclusion{font-size:18px;line-height:1.6}.ok{border-left:4px solid #39c98a}.bad{border-left:4px solid #ff7676}.unknown{border-left:4px solid #e4b84b}.steps{line-height:1.9}.error{color:#ff9b9b}.diagram{margin:12px 0;padding:10px;background:#0c1728;border:1px solid #2b405d;border-radius:8px;overflow-x:auto}.diagram svg{display:block;min-width:520px;width:100%;height:auto}details{margin-top:12px}summary{cursor:pointer;color:#8ec5ff}</style>
<h1>业务登记与影响报告</h1><p>先生成当前网络的正常路径基线，再选择故障做离线推演。报告说明业务是否受影响、路径经过哪些设备以及判断依据。</p>
<section><h2>登记 / 修改业务流</h2><input id="name" placeholder="业务名称"><input id="description" placeholder="说明"><div class="grid"><input id="src" placeholder="源 IP"><input id="dst" placeholder="目的 IP"><select id="protocol"><option>any</option><option>tcp</option><option>udp</option><option>icmp</option><option>icmpv6</option></select><input id="sport" placeholder="源端口"><input id="dport" placeholder="目的端口"><input id="application" placeholder="应用（可选）"><input id="ingress" placeholder="入口接口（可选）"><input id="vrf" value="all" placeholder="VRF"></div><button onclick="save()">保存业务</button><span id="msg"></span></section>
<section><h2>已登记业务</h2><div id="flows"></div></section><section><h2>故障推演</h2><p class="muted">仅允许选当前 Nautobot Site 为 lab 的设备、接口和路由。推演不会改动设备。</p><select id="fault"><option value="device_down">设备离线</option><option value="interface_down">接口中断</option><option value="route_removed">配置变更：路由被移除</option></select><select id="target"></select><button id="scenarioBtn" disabled>推演此故障</button><div id="report"></div></section><section><h2>历史报告</h2><div id="reports"></div></section>
<script>const $=x=>document.getElementById(x);let selected=null,activeFlow=null;async function call(url,opt){let r=await fetch(url,{headers:{'Content-Type':'application/json'},...opt}),d=await r.json();if(!r.ok)throw Error(d.detail||r.statusText);return d}async function save(){try{let d={name:$('name').value,description:$('description').value,source_ip:$('src').value,destination_ip:$('dst').value,protocol:$('protocol').value,source_port:$('sport').value?+ $('sport').value:null,destination_port:$('dport').value?+ $('dport').value:null,application:$('application').value||null,ingress_interface:$('ingress').value||null,vrf:$('vrf').value||'all'};await call('/api/business/flows'+(selected?'/'+selected:''),{method:selected?'PUT':'POST',body:JSON.stringify(d)});selected=null;$('msg').textContent=' 已保存';load()}catch(e){$('msg').textContent=' '+e.message}}function renderGraph(path,flow,scenario,baseline){let raw=path?.path||[];if(!raw.length)raw=(path?.paths||[]).flatMap(x=>x.path||[]);if(!raw.length&&scenario&&baseline)raw=baseline.path||[];let names=[];for(let h of raw){if(h.from&&h.to){if(names.at(-1)!==h.from)names.push(h.from);if(names.at(-1)!==h.to)names.push(h.to)}else if(h.device&&names.at(-1)!==h.device)names.push(h.device)}names=[...new Set(names)];let labels=[flow?.source_ip||'源端',...names,flow?.destination_ip||'目的端'],w=Math.max(520,labels.length*165),y=68,margin=70,dx=(w-2*margin)/Math.max(1,labels.length-1),fault=scenario?.description||'',broken=scenario?.impact==='impacted',svg='<svg viewBox="0 0 '+w+' 140" role="img" aria-label="流量路径图"><defs><marker id="arr" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto"><path d="M0,0 L0,6 L7,3 z" fill="#65b7ff"/></marker><marker id="arrbad" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto"><path d="M0,0 L0,6 L7,3 z" fill="#ff7777"/></marker></defs>';let xs=labels.map((_,i)=>margin+i*dx);for(let i=0;i<xs.length-1;i++){let bad=broken&&names.length>0;svg+='<line x1="'+(xs[i]+48)+'" y1="'+y+'" x2="'+(xs[i+1]-48)+'" y2="'+y+'" stroke="'+(bad?'#ff7777':'#65b7ff')+'" stroke-width="3" '+(names.length?'marker-end="url(#'+(bad?'arrbad':'arr')+')"':'stroke-dasharray="7 6"')+'/>'}if(!names.length)svg+='<text x="'+(w/2)+'" y="'+(y-10)+'" text-anchor="middle" fill="#a8b9cf" font-size="12">路径证据不足</text>';for(let i=0;i<labels.length;i++){let name=labels[i],endpoint=i===0||i===labels.length-1,mark=fault&&fault.includes(name),x=xs[i];svg+='<rect x="'+(x-48)+'" y="'+(y-23)+'" width="96" height="46" rx="8" fill="'+(mark?'#632d37':endpoint?'#193c54':'#203a58')+'" stroke="'+(mark?'#ff7777':endpoint?'#65b7ff':'#527092')+'" stroke-width="2"/><text x="'+x+'" y="'+(y+4)+'" text-anchor="middle" fill="#e5edf8" font-size="12">'+esc(name.length>15?name.slice(0,14)+'…':name)+'</text>';if(mark)svg+='<text x="'+x+'" y="'+(y-29)+'" text-anchor="middle" fill="#ff7777" font-size="11">故障点</text>'}if(scenario)svg+='<text x="'+(w/2)+'" y="125" text-anchor="middle" fill="#ff9b9b" font-size="12">'+esc(fault)+(names.some(n=>fault.includes(n))?'':' · 故障点未落在路径节点中')+'</text>';return '<div class="diagram">'+svg+'</svg></div>'}function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}function card(title,conclusion,path,status,flow,scenario,baseline){let cls=status==='ok'?'ok':(['blocked','not_found','invalid','impacted'].includes(status)?'bad':'unknown');return '<div class="result '+cls+'"><h3>'+esc(title)+'</h3><div class="conclusion">'+esc(conclusion||'暂无结论')+'</div>'+renderGraph(path,flow,scenario,baseline)+'</div>'}async function runBase(id){activeFlow=id;$('scenarioBtn').disabled=true;try{let r=await call('/api/business/flows/'+id+'/analyze',{method:'POST'});renderReport(r);$('scenarioBtn').disabled=false;loadReports()}catch(e){$('report').innerHTML='<p class="error">'+esc(e.message)+'</p>'}}async function runScenario(){if(!activeFlow)return;try{let r=await call('/api/business/flows/'+activeFlow+'/scenarios',{method:'POST',body:JSON.stringify({kind:$('fault').value,target_id:$('target').value})});renderReport(r);loadReports()}catch(e){$('report').innerHTML='<p class="error">'+esc(e.message)+'</p>'}}function renderReport(r){let el=$('report'),b=r.baseline?.path_result;el.innerHTML=card('故障前 · 正常路径基线',r.baseline?.conclusion,b,r.baseline?.status,r.flow,null,null);if(r.scenario)el.innerHTML+=card('故障后 · '+r.scenario.description,r.conclusion,r.scenario.path_result,r.scenario.impact||r.scenario.status,r.flow,r.scenario,b);else el.innerHTML+='<p class="muted">基线已保存。选择一种故障并点击“推演此故障”进行对照。</p>';el.innerHTML+='<p class="muted">证据范围：'+esc(r.evidence?.scope||'lab')+' · 拓扑采集时间：'+esc(r.evidence?.topology_observed_at||'-')+' · '+esc((r.limitations||[]).join(' '))+'</p>';for(let [label,path] of [['基线详情',r.baseline?.path_result],['故障推演详情',r.scenario?.path_result]])if(path)el.innerHTML+='<details><summary>'+label+'（原始证据）</summary><pre>'+esc(JSON.stringify(path,null,2))+'</pre></details>'}async function loadTargets(){let t=await call('/api/topology'),kind=$('fault').value,options=[];if(kind==='device_down')options=t.devices.filter(d=>d.site==='lab').map(d=>[d.id,d.name]);if(kind==='interface_down')options=t.interfaces.filter(i=>t.devices.some(d=>d.id===i.device_id&&d.site==='lab')).map(i=>[i.id,(i.device||'')+':'+i.name]);if(kind==='route_removed')options=t.routes.filter(r=>t.devices.some(d=>d.id===r.device_id&&d.site==='lab')).map(r=>[r.id,r.device+' · '+r.vrf+' · '+r.prefix]);$('target').replaceChildren();for(let [id,label] of options){let o=document.createElement('option');o.value=id;o.textContent=label;$('target').append(o)}$('scenarioBtn').disabled=!activeFlow||!options.length}async function loadReports(){let b=await call('/api/business/reports');$('reports').replaceChildren();for(let r of b.reports){let x=document.createElement('p'),z=document.createElement('a');z.href='#';z.textContent=r.flow_name+' · '+r.created_at+' · 拓扑 '+(r.snapshot_at||'-');z.onclick=async()=>{let v=await call('/api/business/reports/'+r.id);renderReport(v);activeFlow=v.flow_id;$('scenarioBtn').disabled=!activeFlow};x.append(z);$('reports').append(x)}}async function load(){let a=await call('/api/business/flows');$('flows').replaceChildren();for(let f of a.flows){let x=document.createElement('article');x.innerHTML='<b></b> <small></small> ';x.querySelector('b').textContent=f.name;x.querySelector('small').textContent=f.definition.source_ip+' → '+f.definition.destination_ip;for(let [label,fn] of [['生成基线 / 查看',()=>runBase(f.id)],['编辑',()=>{selected=f.id;let d=f.definition;for(let [k,id] of Object.entries({name:'name',description:'description',source_ip:'src',destination_ip:'dst',protocol:'protocol',source_port:'sport',destination_port:'dport',application:'application',ingress_interface:'ingress',vrf:'vrf'}))$(id).value=d[k]||''}],['归档',async()=>{await call('/api/business/flows/'+f.id+'/archive',{method:'POST'});load()}]]){let z=document.createElement('button');z.textContent=label;z.onclick=fn;x.append(z)}$('flows').append(x)}await loadReports();loadTargets()}$('fault').onchange=loadTargets;$('scenarioBtn').onclick=runScenario;load().catch(e=>$('msg').textContent=e.message)</script></html>'''
