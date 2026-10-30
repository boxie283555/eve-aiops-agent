from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import urllib.request
from dataclasses import asdict
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Query, Response
from pydantic import BaseModel, Field
from typing import Optional, Literal
from fastapi.responses import HTMLResponse, PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from .collector import Collector
from .inventory import load_inventory, inventory_status
from .health import network_health, prometheus_health
from .health_view import HEALTH_PAGE
from .nautobot import NautobotClient, NautobotError
from .metrics import NAUTOBOT_SYNC_SUCCESS, NAUTOBOT_SYNC_LAST_SUCCESS, NAUTOBOT_SYNC_DURATION, NAUTOBOT_SYNC_UPDATED
from .panos_evidence import attach_policy, refresh_policy, policy_status, verify_flow
from .path_visualization import PATH_GRAPH_STYLE, PATH_GRAPH_SCRIPT
from .parser import normalize_mac
from .settings import load_settings
from .snapshots import collect_and_snapshot, compare_latest
from .topology import build_snapshot, find_path, load_layout, load_snapshot, save_layout, save_snapshot

settings = load_settings()
devices = load_inventory()
active_inventory_status = inventory_status()
collector = Collector(settings, devices)
scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
interface_sync_task: asyncio.Task[None] | None = None
interface_sync_status = {"status": "not_run", "last_attempt_at": None,
                         "last_success_at": None, "updated_interfaces": 0,
                         "failed_devices": [], "error": None}
logger = logging.getLogger(__name__)
MRTG_TELEMETRY_METRICS_URL = os.getenv("AIOPS_TELEGRAF_METRICS_URL", "http://telegraf:9273/metrics")
MRTG_TELEMETRY_CACHE: dict[tuple[str, str, str], tuple[float, float]] = {}

app = FastAPI(title="EVE AIOps Agent", version="0.1.0")


def _hour_minute(value: str) -> tuple[int, int]:
    hour, minute = value.split(":", maxsplit=1)
    return int(hour), int(minute)


async def _collect_loop_once() -> None:
    global interface_sync_task
    await asyncio.to_thread(collector.collect_all)
    design = None
    if settings.nautobot_url and settings.nautobot_token:
        try:
            design = await asyncio.to_thread(
                NautobotClient(settings.nautobot_url, settings.nautobot_token, max(30.0, settings.nautobot_timeout_seconds)).topology
            )
        except NautobotError:
            # Runtime topology remains usable when Nautobot is temporarily down.
            design = None
    snapshot = await asyncio.to_thread(build_snapshot, collector.latest_results, design, load_snapshot())
    await asyncio.to_thread(save_snapshot, snapshot)
    if settings.nautobot_url and settings.nautobot_token:
        if interface_sync_task is None or interface_sync_task.done():
            interface_sync_task = asyncio.create_task(_sync_interfaces_background())


async def _sync_interfaces_background() -> None:
    started = time.time()
    interface_sync_status.update(status="running", last_attempt_at=started, error=None, failed_devices=[])
    try:
        updated = await asyncio.to_thread(
            NautobotClient(settings.nautobot_url or "", settings.nautobot_token or "", max(30.0, settings.nautobot_timeout_seconds)).sync_interface_states,
            list(collector.latest_results.values()),
        )
        completed = time.time()
        interface_sync_status.update(status="ok", last_success_at=completed, updated_interfaces=updated)
        NAUTOBOT_SYNC_SUCCESS.set(1)
        NAUTOBOT_SYNC_LAST_SUCCESS.set(completed)
        NAUTOBOT_SYNC_UPDATED.set(updated)
    except Exception as exc:
        # Surface sink failures without stopping collection or exposing response payloads.
        updated = getattr(exc, "updated", 0)
        interface_sync_status.update(status="error", error=type(exc).__name__,
                                     updated_interfaces=updated, failed_devices=getattr(exc, "failed_devices", []))
        NAUTOBOT_SYNC_SUCCESS.set(0)
        NAUTOBOT_SYNC_UPDATED.set(updated)
        logger.warning("Nautobot interface sync failed (%s); %s interfaces updated", type(exc).__name__, updated)
    finally:
        NAUTOBOT_SYNC_DURATION.set(time.time() - started)


@app.get("/api/nautobot/sync")
def nautobot_sync_status() -> dict:
    return {**interface_sync_status, "enabled": bool(settings.nautobot_url and settings.nautobot_token)}


async def _pa_policy_once() -> None:
    await asyncio.to_thread(refresh_policy, settings)


async def _snapshot_once() -> None:
    await asyncio.to_thread(collect_and_snapshot, settings, collector)


async def _compare_once() -> None:
    await asyncio.to_thread(compare_latest, settings)


@app.on_event("startup")
async def startup() -> None:
    settings.snapshots_path.mkdir(parents=True, exist_ok=True)
    settings.reports_path.mkdir(parents=True, exist_ok=True)
    scheduler.add_job(
        _collect_loop_once,
        "interval",
        seconds=settings.collector_interval_seconds,
        id="collect_metrics",
        replace_existing=True,
        next_run_time=datetime.now(),
    )

    snap_hour, snap_minute = _hour_minute(settings.snapshot_time)
    scheduler.add_job(
        _snapshot_once,
        "cron",
        hour=snap_hour,
        minute=snap_minute,
        id="daily_snapshot",
        replace_existing=True,
    )

    compare_hour, compare_minute = _hour_minute(settings.compare_time)
    scheduler.add_job(
        _compare_once,
        "cron",
        hour=compare_hour,
        minute=compare_minute,
        id="daily_compare",
        replace_existing=True,
    )
    scheduler.add_job(_pa_policy_once, "interval", seconds=300, id="collect_pa_policy",
                      replace_existing=True, max_instances=1, next_run_time=datetime.now())
    scheduler.start()


@app.on_event("shutdown")
async def shutdown() -> None:
    scheduler.shutdown(wait=False)


@app.get("/healthz", response_class=PlainTextResponse)
def healthz() -> str:
    return "ok"


@app.get("/metrics")
def prometheus_metrics() -> Response:
    return Response(generate_latest() + prometheus_health(health_score()), media_type=CONTENT_TYPE_LATEST)


@app.get("/api/inventory/status")
def active_inventory() -> dict:
    return {**active_inventory_status, "device_count": len(collector.devices),
            "refresh_policy": "Loaded at Agent startup; restart Agent after inventory changes"}


@app.get("/api/health/score")
def health_score(site: str = "all") -> dict:
    return network_health(list(collector.devices), dict(collector.latest_results), load_snapshot(), site,
                          ttl=int(os.getenv("AIOPS_TOPOLOGY_TTL_SECONDS", "600")))


@app.get("/health", response_class=HTMLResponse)
def health_page() -> str:
    return HEALTH_PAGE


@app.get("/state")
def structured_state() -> dict[str, object]:
    return {
        "devices": [
            {
                "name": result.device.name,
                "host": result.device.host,
                "role": result.device.role,
                "site": result.device.site,
                "ok": result.ok,
                "error": result.error,
                "duration_seconds": result.duration_seconds,
                "hardware": asdict(result.hardware),
                "hardware_components": [asdict(component) for component in result.hardware_components],
                "tcam_resources": [asdict(resource) for resource in result.tcam_resources],
                "resources": asdict(result.resources),
                "firewall_metrics": result.firewall_metrics,
                "id": result.device.id,
                "routes": result.routes,
                "collected_at": result.collected_at,
                "dataset_status": result.dataset_status,
                "interfaces": result.interfaces,
                "mac_table": [asdict(entry) for entry in result.mac_table],
                "arp_table": [asdict(entry) for entry in result.arp_table],
                "lldp_neighbors": [asdict(neighbor) for neighbor in result.lldp_neighbors],
                "route_summary": [asdict(entry) for entry in result.route_summary],
                "bgp": [asdict(neighbor) for neighbor in result.bgp],
                "ospf": [asdict(neighbor) for neighbor in result.ospf],
                "vxlan": [asdict(peer) for peer in result.nve],
                "structured_commands": sorted(result.structured_outputs.keys()),
            }
            for result in sorted(collector.latest_results.values(), key=lambda item: item.device.name)
        ]
    }


@app.get("/api/devices")
def devices_api() -> list[dict[str, object]]:
    """Return the latest device state for the network operations UI."""
    return [
        {
            "device": result.device.name,
            "host": result.device.host,
            "role": result.device.role,
            "site": result.device.site,
            "up": result.ok,
            "error": result.error,
            "duration_seconds": result.duration_seconds,
        }
        for result in sorted(collector.latest_results.values(), key=lambda item: item.device.name)
    ]


@app.get("/api/interfaces")
def interfaces_api(
    device: str | None = Query(default=None),
    state: str = Query(default="all", pattern="^(all|up|down)$"),
) -> list[dict[str, object]]:
    """Return interface state without requiring a device login."""
    rows: list[dict[str, object]] = []
    for result in collector.latest_results.values():
        if device and result.device.name != device:
            continue
        for interface in result.interfaces:
            oper_up = interface.get("oper_up")
            if state == "up" and oper_up is not True:
                continue
            if state == "down" and oper_up is not False:
                continue
            rows.append(
                {
                    "device": result.device.name,
                    "host": result.device.host,
                    "role": result.device.role,
                    "site": result.device.site,
                    "interface": interface.get("name"),
                    "admin_up": interface.get("admin_up"),
                    "oper_up": oper_up,
                    "description": interface.get("description", ""),
                    "speed_bps": interface.get("speed_bps"),
                    "device_up": result.ok,
                    "collection_error": result.error,
                }
            )
    return sorted(rows, key=lambda item: (str(item["device"]), str(item["interface"])))


@app.post("/collect", response_class=PlainTextResponse)
async def collect_now() -> str:
    await _collect_loop_once()
    return "collection complete"


@app.post("/snapshot", response_class=PlainTextResponse)
async def snapshot_now() -> str:
    path = await asyncio.to_thread(collect_and_snapshot, settings, collector)
    return f"snapshot written: {path}"


@app.post("/compare", response_class=PlainTextResponse)
async def compare_now() -> str:
    path = await asyncio.to_thread(compare_latest, settings)
    return f"report written: {path}" if path else "not enough snapshots to compare"


@app.get("/tools", response_class=HTMLResponse)
def tools_view() -> str:
    return """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>AI Networking OPS Tools</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    :root { --bg: #f6f8fb; --panel: #fff; --line: #d7dee8; --text: #17202a; --muted: #64748b; --green: #137333; --yellow: #b7791f; --red: #b42318; --blue: #075985; }
    * { box-sizing: border-box; }
    body { margin: 0; background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    header { padding: 20px 24px; background: #fff; border-bottom: 1px solid #d7dee8; }
    h1 { margin: 0; font-size: 20px; letter-spacing: 0; }
    main { padding: 20px 24px 28px; display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); align-items: start; }
    section { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 16px; }
    h2 { margin: 0 0 12px; font-size: 16px; letter-spacing: 0; }
    label { display: block; color: #475569; font-size: 13px; margin-bottom: 6px; }
    input, select, button { height: 36px; border: 1px solid #cbd5e1; border-radius: 6px; padding: 0 10px; font: inherit; background: #fff; }
    input, select { width: 100%; }
    button { background: #0f172a; color: #fff; cursor: pointer; }
    .form-grid { display: grid; gap: 10px; grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .wide { grid-column: 1 / -1; }
    .actions { margin-top: 12px; display: flex; gap: 10px; align-items: center; }
    .result { min-height: 180px; margin-top: 14px; display: grid; gap: 12px; }
    .message { color: var(--muted); background: #f8fafc; border: 1px dashed var(--line); border-radius: 6px; padding: 14px; }
    .summary { display: flex; flex-wrap: wrap; gap: 8px; }
    .pill { display: inline-flex; align-items: center; gap: 6px; border: 1px solid var(--line); border-radius: 999px; padding: 4px 9px; color: #334155; background: #f8fafc; font-size: 12px; }
    .pill.good { color: var(--green); border-color: #b7e2c1; background: #f0fdf4; }
    .pill.warn { color: var(--yellow); border-color: #f2d49b; background: #fffbeb; }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th, td { padding: 8px 9px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
    th { background: #f8fafc; color: #475569; font-weight: 700; }
    .table-wrap { overflow: auto; border: 1px solid var(--line); border-radius: 6px; }
    h3 { margin: 2px 0 8px; font-size: 14px; }
    .path { display: grid; gap: 8px; }
    .step { border: 1px solid var(--line); border-radius: 6px; padding: 10px; background: #fff; }
    .step-title { font-weight: 700; margin-bottom: 4px; }
    .step-meta { color: var(--muted); font-size: 13px; }
    @media (max-width: 680px) { main { grid-template-columns: 1fr; padding: 14px; } .form-grid { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
  <header>
    <h1>AI Networking OPS Tools</h1>
  </header>
  <main>
    <section>
      <h2>MAC / IP Lookup</h2>
      <div class="form-grid">
        <div class="wide">
          <label for="macQuery">MAC or IP</label>
          <input id="macQuery" placeholder="aabb.cc80.b000 or 10.110.0.11">
        </div>
        <div>
          <label for="macDevice">Device</label>
          <select id="macDevice"></select>
        </div>
        <div>
          <label for="macVrf">VRF for IP lookup</label>
          <select id="macVrf"></select>
        </div>
      </div>
      <div class="actions"><button type="button" onclick="lookupMac()">Lookup</button></div>
      <div id="macResult" class="result"><div class="message">Waiting for query...</div></div>
    </section>
    <section>
      <h2>End-to-End Path</h2>
      <div class="actions"><label for="pathSite">Site / 站点</label><select id="pathSite" onchange="loadPathOptions()"></select></div>
      <div class="form-grid">
        <div class="wide">
          <label for="srcQuery">Source MAC, IP or device:interface</label>
          <input id="srcQuery" placeholder="10.110.0.11">
        </div>
        <div>
          <label for="srcDevice">Source Device</label>
          <select id="srcDevice"></select>
        </div>
        <div>
          <label for="srcVrf">Source VRF for IP</label>
          <select id="srcVrf"></select>
        </div>
        <div class="wide">
          <label for="dstQuery">Destination MAC, IP or device:interface</label>
          <input id="dstQuery" placeholder="10.120.0.21">
        </div>
        <div>
          <label for="dstDevice">Destination Device</label>
          <select id="dstDevice"></select>
        </div>
        <div>
          <label for="dstVrf">Destination VRF for IP</label>
          <select id="dstVrf"></select>
        </div>
      </div>
      <div class="form-grid" style="margin-top:12px">
        <div><label for="pathAnalysis">分析方式 / Analysis</label><select id="pathAnalysis" onchange="toggleFlowFields()"><option value="routing">路径推演 / Routing</option><option value="flow">流量 + PA 策略/NAT / Flow</option></select></div>
        <div><label for="flowProtocol">协议 / Protocol</label><select id="flowProtocol"><option value="any">未指定 / Any</option><option value="tcp">TCP</option><option value="udp">UDP</option><option value="icmp">ICMP</option><option value="icmpv6">ICMPv6</option></select></div>
        <div><label for="flowSourcePort">源端口 / Source port</label><input id="flowSourcePort" type="number" min="0" max="65535" placeholder="可选 / Optional"></div>
        <div><label for="flowDestinationPort">目的端口 / Destination port</label><input id="flowDestinationPort" type="number" min="0" max="65535" placeholder="例如 / e.g. 50001"></div>
        <div><label for="flowApplication">App-ID 假设 / Assumed application</label><input id="flowApplication" placeholder="未知时留空 / Leave blank if unknown"></div>
        <div><label for="flowIngress">源设备入口 / Source ingress interface</label><input id="flowIngress" placeholder="设备自身发起时留空 / Optional"></div>
      </div>
      <p class="step-meta">流量分析按新建连接计算；交换源、目的后是独立新连接，不代表已建立会话的回包。</p>
      <p id="flowHint" class="message" hidden>未指定协议/端口时，仅在所有可能命中的安全规则都允许、且 NAT 结果明确时继续。要确定具体规则，请补充实际协议与端口。地址 any-to-any 不代表 Service 为 any。<br>Without protocol/ports, forwarding continues only if every possible security outcome allows and NAT is resolved. Supply the actual flow to identify an exact rule.</p>
      <div class="actions"><select id="pathMode"><option value="auto">Auto</option><option value="routed">Route lookup</option><option value="physical">Physical connectivity</option></select><button type="button" onclick="lookupPath()">Trace Path</button></div>
    </section>
    <section class="path-results">
      <h2>设备路径图 / Device Path</h2>
      <div id="pathResult" class="result"><div class="message">Waiting for query...</div></div>
      <div class="actions"><button type="button" onclick="showRoutes()">Show routes for source device / VRF</button></div>
      <div id="routesResult" class="result"></div>
    </section>
  </main>
  <script>
    const deviceSelects = ["macDevice", "srcDevice", "dstDevice"];
    const vrfSelects = ["macVrf", "srcVrf", "dstVrf"];

    let paChecks = [];
    toggleFlowFields();
    loadOptions();
    bindVrfToggle("macQuery", "macVrf");



    async function lookupMac() {
      const query = document.getElementById("macQuery").value.trim();
      const params = new URLSearchParams({
        query,
        device: document.getElementById("macDevice").value,
        vrf: isIp(query) ? document.getElementById("macVrf").value : "all",
      });
      await renderLookup("/tools/mac?" + params.toString(), "macResult");
    }

    function toggleFlowFields() {
      const enabled = document.getElementById("pathAnalysis").value === "flow";
      for (const id of ["flowProtocol", "flowSourcePort", "flowDestinationPort", "flowApplication", "flowIngress"]) document.getElementById(id).disabled = !enabled;
      document.getElementById("flowHint").hidden = !enabled;
      if (enabled && document.getElementById("pathMode").value === "physical") document.getElementById("pathMode").value = "auto";
    }

    async function lookupPath() {
      const src = document.getElementById("srcQuery").value.trim();
      const dst = document.getElementById("dstQuery").value.trim();
      const params = new URLSearchParams({
        src,
        dst,
        site: document.getElementById("pathSite").value,
        src_device: document.getElementById("srcDevice").value,
        src_vrf: document.getElementById("srcVrf").value,
        mode: document.getElementById("pathMode").value,
        dst_device: document.getElementById("dstDevice").value,
        dst_vrf: document.getElementById("dstVrf").value,
      });
      params.set("analysis", document.getElementById("pathAnalysis").value);
      if (params.get("analysis") === "flow") {
        for (const [key, id] of [["protocol", "flowProtocol"], ["source_port", "flowSourcePort"], ["destination_port", "flowDestinationPort"], ["application", "flowApplication"], ["ingress_interface", "flowIngress"]]) {
          const input = document.getElementById(id);
          if (!input.checkValidity()) { input.reportValidity(); return; }
          if (input.value.trim()) params.set(key, input.value.trim());
        }
      }
      await renderPath("/tools/path?" + params.toString(), "pathResult");
    }

    async function showRoutes() {
      const target = document.getElementById("routesResult");
      target.textContent = "Loading routes...";
      const params = new URLSearchParams({device: document.getElementById("srcDevice").value, vrf: document.getElementById("srcVrf").value, site: document.getElementById("pathSite").value});
      try {
        const payload = await (await fetch("/api/routes?" + params)).json();
        target.innerHTML = tableSection("Collected RIB routes", payload.routes || [], ["device", "vrf", "prefix", "next_hop", "interface", "protocol", "freshness", "last_success_at"]);
      } catch (error) { target.textContent = "Route request failed: " + error; }
    }

    function bindVrfToggle(queryId, vrfId) {
      const input = document.getElementById(queryId);
      input.addEventListener("input", () => updateVrfState(queryId, vrfId));
      updateVrfState(queryId, vrfId);
    }

    function updateVrfState(queryId, vrfId) {
      const value = document.getElementById(queryId).value.trim();
      const select = document.getElementById(vrfId);
      const enabled = !value || isIp(value);
      select.disabled = !enabled;
      if (!enabled) select.value = "all";
      select.title = enabled ? "Used for IP lookup" : "VRF is not used for MAC lookup";
    }

    async function loadOptions() {
      try {
        const response = await fetch("/tools/options", { cache: "no-store" });
        const payload = await response.json();
        for (const id of deviceSelects) fillSelect(id, payload.devices || [], "All devices");
        for (const id of vrfSelects) fillSelect(id, payload.vrfs || [], "All VRFs");
        fillSelect("pathSite", payload.sites || [], "All sites");
        const params = new URLSearchParams(location.search);
        const preferredSite = params.get("site") || ((payload.sites || []).includes("lab") ? "lab" : "all");
        if (["all", ...(payload.sites || [])].includes(preferredSite)) document.getElementById("pathSite").value = preferredSite;
        await loadPathOptions();
        for (const [key, id] of [["src", "srcQuery"], ["dst", "dstQuery"]]) if (params.has(key)) document.getElementById(id).value = params.get(key);
        for (const [key, id] of [["analysis", "pathAnalysis"], ["protocol", "flowProtocol"], ["source_port", "flowSourcePort"], ["destination_port", "flowDestinationPort"], ["application", "flowApplication"], ["ingress_interface", "flowIngress"]]) {
          if (params.has(key)) document.getElementById(id).value = params.get(key);
        }
        toggleFlowFields();
        if (params.has("src") && params.has("dst")) await lookupPath();
      } catch (error) {
        for (const id of deviceSelects) fillSelect(id, [], "All devices");
        for (const id of vrfSelects) fillSelect(id, [], "All VRFs");
        fillSelect("pathSite", [], "All sites");
      }
    }

    async function loadPathOptions() {
      const site = document.getElementById("pathSite").value;
      const payload = await (await fetch("/tools/options?site=" + encodeURIComponent(site), {cache: "no-store"})).json();
      for (const id of ["srcDevice", "dstDevice", "srcVrf", "dstVrf"]) {
        const current = document.getElementById(id).value;
        const values = id.endsWith("Device") ? payload.devices || [] : payload.vrfs || [];
        fillSelect(id, values, id.endsWith("Device") ? "All devices" : "All VRFs");
        if (values.includes(current)) document.getElementById(id).value = current;
      }
    }

    function fillSelect(id, values, allLabel) {
      const select = document.getElementById(id);
      select.innerHTML = "";
      select.append(new Option(allLabel, "all"));
      for (const value of values) select.append(new Option(value, value));
    }

    async function renderLookup(url, target) {
      const node = document.getElementById(target);
      node.innerHTML = '<div class="message">Loading...</div>';
      try {
        const response = await fetch(url, { cache: "no-store" });
        const payload = await response.json();
        node.innerHTML = lookupHtml(payload);
      } catch (error) {
        node.innerHTML = '<div class="message">Request failed: ' + escapeHtml(String(error)) + '</div>';
      }
    }

    async function renderPath(url, target) {
      const node = document.getElementById(target);
      node.innerHTML = '<div class="message">Loading...</div>';
      try {
        const response = await fetch(url, { cache: "no-store" });
        const payload = await response.json();
        node.innerHTML = pathHtml(payload);
      } catch (error) {
        node.innerHTML = '<div class="message">Request failed: ' + escapeHtml(String(error)) + '</div>';
      }
    }

    function lookupHtml(payload) {
      const summary = [
        pill(payload.found ? "Found" : "Not found", payload.found ? "good" : "warn"),
        pill("Query: " + (payload.query || "")),
        pill("Device: " + (payload.device_filter || "all")),
        pill("VRF: " + (payload.vrf_filter || "all")),
        payload.resolved_ip ? pill("IP: " + payload.resolved_ip) : "",
        payload.resolved_mac ? pill("MAC: " + payload.resolved_mac) : "",
      ].join("");
      return '<div class="summary">' + summary + '</div>'
        + tableSection("ARP Matches", payload.arp_matches || [], ["device", "vrf", "ip", "mac", "interface", "site", "role"])
        + tableSection("Local MAC Matches", payload.local_matches || [], ["device", "vlan", "mac", "port", "site", "role"])
        + tableSection("Overlay MAC Matches", payload.overlay_matches || [], ["device", "vlan", "mac", "port", "site", "role"])
        + tableSection("CLF Matches", payload.clf_matches || [], ["device", "vlan", "mac", "port", "site", "location_type"]);
    }

    function pathHtml(payload) {
      pathGraphs = [];
      paChecks = [];
      const candidates = payload.status === "ambiguous"
        ? tableSection("Source candidates — select device and VRF above", payload.source_candidates || [], ["device", "vrf", "interface", "ip", "mac", "vlan"])
          + tableSection("Destination candidates", payload.destination_candidates || [], ["device", "vrf", "interface", "ip", "mac", "vlan"]) : "";
      const branches = payload.paths || [];
      const actionOnly = branches.some(b => (b.path || []).some(s => s.policy_resolution === 'action_only'));
      const paths = branches.map((branch, i) => '<article class="path-branch"><div class="path-branch-header"><h3>Path ' + (i+1) + ' · ' + escapeHtml(branch.status) + '</h3>'
        + '<label><input class="path-port-toggle" type="checkbox" checked>接口 / Interfaces</label></div>'
        + pathGraphHtml(branch, i, payload)
        + (branch.reason ? '<div class="message">' + escapeHtml(branch.reason) + '</div>' : '')
        + '<details class="path-evidence"><summary>完整计算明细 / All evidence (' + (branch.path || []).length + ')</summary><div class="path">' + (branch.path || []).map(stepHtml).join("") + '</div></details></article>').join("");
      return '<div class="summary">' + pill(payload.status || "unavailable", payload.status === "ok" ? "good" : "warn")
        + pill(payload.mode || "auto") + pill(payload.analysis || "routing") + pill("Site: " + (payload.site || "all")) + '</div>'
        + endpointSummary("Source", payload.source || {}) + endpointSummary("Destination", payload.destination || {})
        + (payload.flow ? '<div class="message">新建连接 / New connection · ' + escapeHtml(flowText(payload.flow)) + '</div>' : '')
        + (actionOnly ? '<div class="message">PA 配置推演：所有可能命中的规则均允许，因此路径继续；具体命中规则尚未确定。点击 PA 查看候选规则。此结果不等于实际流量验证。<br>All possible PA security outcomes allow; the exact rule remains unresolved. Click PA for candidate rules. Packet delivery is not verified.</div>' : '')
        + candidates + (paths || '<div class="message">No verified path available.</div>')
        + '<div class="summary">' + (payload.warnings || []).map(n => pill(n, "warn")).join("") + '</div>';
    }

    function endpointSummary(title, endpoint) {
      return '<h3>' + title + '</h3><div class="summary">'
        + ["query", "device", "vrf", "interface", "ip", "mac"].filter(k => endpoint[k])
          .map(k => pill(k + ": " + endpoint[k])).join("") + '</div>';
    }

    function stepHtml(step, index) {
      const title = step.label || String(step.type || "step").replaceAll("_", " ");
      const fields = ["device", "vrf", "prefix", "lookup_ip", "next_hop", "interface", "protocol", "policy", "action", "status", "vsys", "from_zone", "to_zone", "original_to_zone", "security_rule", "nat_rule", "reason", "evidence_level", "ingress_vlan", "empty_fieldsets", "vtep", "vni", "from", "to", "local_interface", "remote_interface", "confirmation", "source", "freshness", "observed_at"];
      const details = fields.filter(key => step[key] != null && step[key] !== "")
        .map(key => pill(label(key) + ': ' + step[key])).join("");
      const evidence = Array.isArray(step.evidence) ? step.evidence.map(e =>
        typeof e === "object" ? pill((e.source || "observation") + ' · ' + (e.last_success_at || e.observed_at || "")) : "").join("") : "";
      let flowInfo = '';
      if (step.flow_before) flowInfo += '<div class="message">进入 / Before: ' + escapeHtml(flowText(step.flow_before)) + '</div>';
      if (step.flow_after) flowInfo += '<div class="message">' + (step.status === 'blocked' ? '候选 NAT（已拒绝） / Candidate NAT (denied): ' : '离开 / After: ') + escapeHtml(flowText(step.flow_after)) + '</div>';
      if (step.policy_resolution === 'action_only') flowInfo += '<div class="message">动作确定为允许；具体规则未确定 / Allow action established; exact rule unresolved</div>';
      if (step.security_candidates) flowInfo += tableSection('可能的首条匹配规则 / Possible first-match rules', step.security_candidates, ['name','source','destination','application','service','action','match'])
        + (step.security_candidates_truncated ? '<div class="message">仅显示前 100 条；判定已检查全部候选 / First 100 shown; all candidates evaluated</div>' : '');
      if (step.type === 'firewall_policy' && step.to_zone && step.original_to_zone && step.config_digest && step.verification_flow.protocol !== 'any') {
        const check = {device_id:step.device_id, flow:step.verification_flow, ingress:step.interface, vsys:step.vsys,
          from_zone:step.from_zone, to_zone:step.to_zone, original_to_zone:step.original_to_zone, config_digest:step.config_digest};
        const index = paChecks.push(check) - 1;
        flowInfo += '<button type="button" class="pa-verify" data-check="' + index + '">PA 原生规则/会话核验 / Verify on PA</button><div class="pa-verification-result"></div>';
      }
      return '<div class="step"><div class="step-title">' + (index + 1) + '. ' + escapeHtml(title) + '</div><div class="step-meta summary">' + details + '</div>'
        + flowInfo + (evidence ? '<details><summary>Evidence</summary><div class="summary">' + evidence + '</div></details>' : '') + '</div>';
    }

    function flowText(flow) {
      return (flow.protocol || 'any').toUpperCase() + ' ' + flow.source_ip + (flow.source_port != null ? ':' + flow.source_port : '')
        + ' → ' + flow.destination_ip + (flow.destination_port != null ? ':' + flow.destination_port : '')
        + (flow.application ? ' · App-ID assumption: ' + flow.application : '');
    }

    document.getElementById('pathResult').addEventListener('click', async event => {
      const button = event.target.closest('.pa-verify');
      if (!button) return;
      const request = paChecks[Number(button.dataset.check)], target = button.nextElementSibling;
      button.disabled = true; target.textContent = '正在核验 / Verifying…';
      try {
        const response = await fetch('/api/pa/verify', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(request)});
        const result = await response.json();
        if (!response.ok) throw new Error(JSON.stringify(result.detail));
        target.innerHTML = '<div class="message">' + escapeHtml(result.status + ' · ' + (result.reason || result.observed_at || '')) + '</div>'
          + (result.security ? tableSection('PA security · ' + result.security.status, result.security.rules || [], ['name','action']) : '')
          + (result.nat ? tableSection('PA NAT · ' + result.nat.status, result.nat.rules || [], ['name','action']) : '')
          + (result.sessions ? '<div class="message">Session: ' + escapeHtml(result.sessions.status) + '</div>' + tableSection('Observed sessions',result.sessions.entries || [],['idx','id','source','dst','sport','dport','application','state']) : '')
          + '<div class="step-meta">规则匹配不等于实际流量通过；没有会话不等于拒绝。 / Rule matching is not proof of delivery; no session is not a deny.</div>';
      } catch (error) { target.textContent = '核验失败 / Verification failed: ' + String(error); }
      finally { button.disabled = false; }
    });

    function tableSection(title, rows, columns) {
      if (!rows.length) return '<div><h3>' + escapeHtml(title) + '</h3><div class="message">No matches</div></div>';
      const head = columns.map(col => '<th>' + escapeHtml(label(col)) + '</th>').join("");
      const body = rows.map(row => '<tr>' + columns.map(col => '<td>' + escapeHtml(String(row[col] ?? "")) + '</td>').join("") + '</tr>').join("");
      return '<div><h3>' + escapeHtml(title) + '</h3><div class="table-wrap"><table><thead><tr>' + head + '</tr></thead><tbody>' + body + '</tbody></table></div></div>';
    }

    function pill(text, cls) {
      return '<span class="pill ' + (cls || "") + '">' + escapeHtml(text) + '</span>';
    }

    function label(value) {
      return value.replaceAll("_", " ").replace(/\\b\\w/g, char => char.toUpperCase());
    }

    function escapeHtml(value) {
      return value.replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
    }

    function isIp(value) {
      const parts = value.split(".");
      if (parts.length !== 4) return false;
      return parts.every(part => /^\\d+$/.test(part) && Number(part) >= 0 && Number(part) <= 255);
    }
  </script>
</body>
</html>
""".replace("</style>", PATH_GRAPH_STYLE + "</style>", 1).replace("<script>", "<script>" + PATH_GRAPH_SCRIPT, 1)


@app.get("/topology", response_class=HTMLResponse)
def topology_view() -> str:
    """Render the normalized topology snapshot as a browser graph."""
    return """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI Networking Topology</title>
<style>
body{margin:0;background:#f5f7fa;color:#17202a;font:14px system-ui,sans-serif}header{padding:16px 22px;background:#17202a;color:#fff;display:flex;justify-content:space-between;align-items:center}h1{font-size:20px;margin:0}button{padding:8px 14px;border:0;border-radius:5px;background:#2f80ed;color:#fff;cursor:pointer}main{padding:16px}.meta{margin-bottom:10px;color:#52606d}.legend{display:flex;gap:14px;margin:8px 0}.legend span:before{content:"";display:inline-block;width:11px;height:11px;border-radius:50%;margin-right:5px;background:#27ae60}.legend .down:before{background:#eb5757}.legend .unknown:before{background:#f2c94c}svg{width:100%;height:calc(100vh - 145px);min-height:500px;background:#fff;border:1px solid #d9e2ec;border-radius:8px}.link{stroke:#9aa5b1;stroke-width:2}.link.evpn{stroke:#9b51e0;stroke-dasharray:7 5}.node{stroke:#fff;stroke-width:2}.node.selected{stroke:#2f80ed;stroke-width:5}.label{font-size:12px;fill:#17202a;text-anchor:middle}.iface{font-size:10px;fill:#52606d;text-anchor:middle}.link-label{font-size:10px;fill:#34495e;text-anchor:middle;paint-order:stroke;stroke:#fff;stroke-width:4px}.empty{padding:40px;text-align:center;color:#52606d}
</style></head><body><header><h1>AI Networking Topology</h1><div><button onclick="load()">Refresh</button> <button onclick="savePositions()">Save layout</button></div></header><main><div id="meta" class="meta">Loading...</div><label style="display:inline-flex;gap:6px;align-items:center;margin-bottom:8px"><input id="showInterfaces" type="checkbox" checked onchange="render()"> Show interface labels</label><div id="deviceInfo" class="meta" style="min-height:20px"></div><div class="legend"><span>Device up</span><span class="down">Device down</span><span class="unknown">Design only / not collected</span></div><svg id="graph" viewBox="0 0 1200 700" preserveAspectRatio="xMidYMid meet"></svg></main>
<script>
let topology={nodes:[],links:[],positions:{},observedAt:''}, dragged=null, selected=new Set(), selecting=null, raf=0;
async function load(){
  const meta=document.getElementById('meta'),svg=document.getElementById('graph');
  try { const response=await fetch('/api/topology',{cache:'no-store'}), data=await response.json();
    if(data.status!=='ok'){meta.textContent='Topology snapshot unavailable';return;}
    // Manual data refresh preserves the positions currently being edited.
    if(dragged||selecting)return;
    const positions=Object.fromEntries((data.devices||[]).map(node=>[node.name,topology.positions[node.name]||data.layout?.[node.name]]));
    topology={nodes:data.devices||[],links:(data.links||[]).filter(link=>link.type==='physical'&&link.confirmation==='confirmed'&&link.freshness==='fresh'),positions,observedAt:data.observed_at||''};
    const cols=Math.max(1,Math.ceil(Math.sqrt(topology.nodes.length))),dx=1100/cols,dy=500/Math.max(1,Math.ceil(topology.nodes.length/cols));
    topology.nodes.forEach((node,i)=>{topology.positions[node.name] ||= {x:60+(i%cols)*dx,y:110+Math.floor(i/cols)*dy};});
    selected=new Set([...selected].filter(name=>Object.hasOwn(topology.positions,name)));
    render(); meta.textContent=`${topology.nodes.length} devices · ${topology.links.length} confirmed links · ${(data.links||[]).filter(l=>l.confirmation!=='confirmed').length} candidates (hidden) · observed ${topology.observedAt}`;
  } catch(error){meta.textContent='Failed to load topology: '+error;}
}
function render(){
  const svg=document.getElementById('graph'), p=topology.positions, escv=esc; let out='';
  topology.links.forEach((link,i)=>{const a=p[link.from],b=p[link.to];if(!a||!b)return;const label=(link.local_interface||'?')+' ↔ '+(link.remote_interface||'?');const showIf=document.getElementById('showInterfaces')?.checked!==false;out+=`<line data-link="${i}" class="link ${link.type==='evpn_vxlan'?'evpn':''}" x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}"/><text class="link-label" style="display:${showIf?'block':'none'}" x="${(a.x+b.x)/2}" y="${(a.y+b.y)/2-5}">${escv(label)}</text><title>${escv(link.from)}:${escv(link.local_interface||'')} → ${escv(link.to)}:${escv(link.remote_interface||'')} (${escv(link.type||'link')})</title>`;});
  topology.nodes.forEach(node=>{const q=p[node.name],color=node.up===true&&node.freshness==='fresh'?'#27ae60':node.up===true||node.error==='not_collected'?'#f2c94c':'#eb5757';out+=`<g data-node="${escv(node.name)}"><circle class="node ${selected.has(node.name)?'selected':''}" cx="${q.x}" cy="${q.y}" r="22" fill="${color}"/><text class="label" x="${q.x}" y="${q.y+42}">${escv(node.name)}</text><text class="iface" x="${q.x}" y="${q.y+57}">${escv(node.role||'')} · ${escv(node.site||'')}</text><title>${escv(node.name)}\n${node.up?'up':'down'}${node.error?' · '+escv(node.error):''}</title></g>`;});
  svg.innerHTML=out;
  svg.querySelectorAll('[data-node]').forEach(group=>{group.addEventListener('pointerdown',startDrag);group.addEventListener('pointerenter',()=>showInfo(group.dataset.node));group.addEventListener('pointerleave',()=>{document.getElementById('deviceInfo').textContent='';});});
}
function updateVisuals(){
  const svg=document.getElementById('graph'), p=topology.positions;
  svg.querySelectorAll('[data-node]').forEach(group=>{const q=p[group.dataset.node];group.querySelector('circle').setAttribute('cx',q.x);group.querySelector('circle').setAttribute('cy',q.y);group.querySelector('.label').setAttribute('x',q.x);group.querySelector('.label').setAttribute('y',q.y+42);group.querySelector('.iface').setAttribute('x',q.x);group.querySelector('.iface').setAttribute('y',q.y+57);});
  svg.querySelectorAll('.link-label').forEach(label=>{const link=topology.links[Number(label.dataset.link||0)],a=p[link.from],b=p[link.to];if(a&&b){label.setAttribute('x',(a.x+b.x)/2);label.setAttribute('y',(a.y+b.y)/2-5);}});svg.querySelectorAll('[data-link]').forEach(line=>{const link=topology.links[Number(line.dataset.link)],a=p[link.from],b=p[link.to];if(a&&b){line.setAttribute('x1',a.x);line.setAttribute('y1',a.y);line.setAttribute('x2',b.x);line.setAttribute('y2',b.y);}});
}
function startDrag(event){event.preventDefault();event.stopPropagation();const name=event.currentTarget.dataset.node;if(!selected.has(name)){selected.clear();selected.add(name);render();}const svg=document.getElementById('graph'),box=svg.getBoundingClientRect();dragged={name,pointerId:event.pointerId,lastX:event.clientX,lastY:event.clientY,box};event.currentTarget.setPointerCapture(event.pointerId);event.currentTarget.addEventListener('pointermove',drag);event.currentTarget.addEventListener('pointerup',endDrag,{once:true});event.currentTarget.addEventListener('pointercancel',endDrag,{once:true});}
function showInfo(name){const n=topology.nodes.find(x=>x.name===name);if(n)document.getElementById('deviceInfo').textContent=`${n.name} · ${n.host||'-'} · ${n.platform||'-'} · ${n.role||'-'} · ${n.site||'-'} · ${n.up?(n.freshness==='fresh'?'UP':'STALE'):'DOWN'} · last success ${n.last_success_at||'-'}${n.error?' · '+n.error:''}`;}
function startBox(event){if(event.target.closest('[data-node]'))return;const svg=document.getElementById('graph'),box=svg.getBoundingClientRect();selecting={x:(event.clientX-box.left)*1200/box.width,y:(event.clientY-box.top)*700/box.height,box};svg.setPointerCapture(event.pointerId);svg.addEventListener('pointermove',boxMove);svg.addEventListener('pointerup',endBox,{once:true});}
function boxMove(event){if(!selecting)return;const r=document.getElementById('graph').getBoundingClientRect(),x=(event.clientX-r.left)*1200/r.width,y=(event.clientY-r.top)*700/r.height;const x1=Math.min(selecting.x,x),x2=Math.max(selecting.x,x),y1=Math.min(selecting.y,y),y2=Math.max(selecting.y,y);selected.clear();topology.nodes.forEach(n=>{const q=topology.positions[n.name];if(q.x>=x1&&q.x<=x2&&q.y>=y1&&q.y<=y2)selected.add(n.name);});render();}
function endBox(){selecting=null;}
function drag(event){if(!dragged||event.pointerId!==dragged.pointerId)return;const svg=document.getElementById('graph'),box=svg.getBoundingClientRect();const dx=(event.clientX-dragged.lastX)*1200/box.width,dy=(event.clientY-dragged.lastY)*700/box.height;selected.forEach(n=>{const q=topology.positions[n];q.x=Math.max(25,Math.min(1175,q.x+dx));q.y=Math.max(25,Math.min(675,q.y+dy));});dragged.lastX=event.clientX;dragged.lastY=event.clientY;if(!raf)raf=requestAnimationFrame(()=>{updateVisuals();raf=0;});}
function endDrag(){dragged=null;}
async function savePositions(){const response=await fetch('/api/topology/layout',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(topology.positions)});document.getElementById('meta').textContent=response.ok?'Layout saved':'Failed to save layout';}
function esc(value){return String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
document.getElementById('graph').addEventListener('pointerdown',startBox);load();
</script></body></html>"""


@app.get("/tools/mac")
def mac_lookup(query: str, device: str = "all", vrf: str = "all") -> dict[str, object]:
    return _lookup_mac(query, device_filter=device, vrf_filter=vrf)


@app.get("/api/topology")
def topology_api() -> dict[str, object]:
    """Return the persisted normalized topology v1 snapshot."""
    snapshot = load_snapshot()
    if snapshot is None:
        return {"status": "unavailable", "devices": [], "interfaces": [], "links": [], "endpoints": []}
    return {"status": "ok", "observed_at": snapshot.observed_at, "devices": snapshot.devices,
            "interfaces": snapshot.interfaces, "links": snapshot.links, "endpoints": snapshot.endpoints,
            "layout": load_layout(), "schema_version": snapshot.schema_version,
            "vrfs": snapshot.vrfs, "routes": snapshot.routes, "warnings": snapshot.warnings}


@app.get("/api/topology/layout")
def topology_layout_get() -> dict[str, object]:
    return {"layout": load_layout()}


@app.put("/api/topology/layout")
def topology_layout_put(payload: dict[str, dict[str, float]]) -> dict[str, object]:
    layout = {str(name): {"x": float(value["x"]), "y": float(value["y"])} for name, value in payload.items()
              if isinstance(value, dict) and "x" in value and "y" in value}
    save_layout(layout)
    return {"status": "ok", "layout": layout}


@app.get("/tools/path")
@app.get("/api/path")
def normalized_path_lookup(src: str, dst: str, vrf: str = "all",
                           src_device: str = "all", dst_device: str = "all",
                           src_vrf: str = "all", dst_vrf: str = "all",
                           site: str = "all",
                           analysis: str = Query("routing", pattern="^(routing|flow)$"),
                           protocol: str = Query("any", pattern="^(any|tcp|udp|icmp|icmpv6)$"),
                           source_port: Optional[int] = Query(None, ge=0, le=65535),
                           destination_port: Optional[int] = Query(None, ge=0, le=65535),
                           application: Optional[str] = Query(None, max_length=128),
                           ingress_interface: Optional[str] = Query(None, max_length=128),
                           mode: str = Query("auto", pattern="^(auto|physical|routed)$")) -> dict[str, object]:
    """Shared engine; new-connection analysis is explicit and legacy routing remains valid."""
    snapshot = load_snapshot()
    if snapshot is None:
        return {"status": "unavailable", "source": {}, "destination": {}, "path": [], "paths": [],
                "warnings": ["topology snapshot is not available"]}
    if analysis == "flow":
        snapshot = attach_policy(snapshot)
    return find_path(snapshot, src, dst, vrf, src_device=src_device, dst_device=dst_device,
                     src_vrf=src_vrf, dst_vrf=dst_vrf, mode=mode, site=site, analysis=analysis,
                     protocol=protocol, source_port=source_port, destination_port=destination_port,
                     application=application, ingress_interface=ingress_interface)


class FlowInput(BaseModel):
    source_ip: str = Field(max_length=64)
    destination_ip: str = Field(max_length=64)
    protocol: Literal["tcp", "udp", "icmp", "icmpv6"]
    source_port: Optional[int] = Field(default=None, ge=0, le=65535)
    destination_port: Optional[int] = Field(default=None, ge=0, le=65535)
    application: Optional[str] = Field(default=None, max_length=128)


class PAVerifyRequest(BaseModel):
    device_id: str = Field(max_length=64)
    flow: FlowInput
    ingress: str = Field(max_length=128)
    vsys: str = Field(max_length=128)
    from_zone: str = Field(max_length=128)
    to_zone: str = Field(max_length=128)
    original_to_zone: str = Field(max_length=128)
    config_digest: str = Field(max_length=64)


@app.get("/api/pa/policy-status")
def pa_policy_status() -> dict:
    return policy_status()


@app.post("/api/pa/verify")
async def pa_verify(request: PAVerifyRequest) -> dict:
    return await asyncio.to_thread(verify_flow, settings, request.model_dump())


@app.get("/api/routes")
def routes_api(device: str = "all", vrf: str = "all", prefix: str = "all", site: str = "all") -> dict[str, object]:
    snapshot = load_snapshot()
    if snapshot is None:
        return {"status": "unavailable", "routes": []}
    allowed = {d["id"] for d in snapshot.devices if site == "all" or d.get("site") == site}
    rows = [r for r in snapshot.routes
            if r["device_id"] in allowed and (device == "all" or device in {r["device"], r["device_id"]})
            and (vrf == "all" or vrf in {r["vrf"], r["vrf_id"]})
            and (prefix == "all" or prefix == r["prefix"])]
    return {"status": "ok", "observed_at": snapshot.observed_at, "routes": rows}


@app.get("/api/topology/anomalies")
def topology_anomalies() -> dict[str, object]:
    """Expose actionable topology freshness and link/interface anomalies."""
    snapshot = load_snapshot()
    if snapshot is None:
        return {"status": "unavailable", "anomalies": ["topology snapshot is not available"]}
    anomalies: list[dict[str, object]] = []
    for device in snapshot.devices:
        if not device.get("up"):
            anomalies.append({"type": "device_down", "device": device.get("name"), "detail": device.get("error")})
    for interface in snapshot.interfaces:
        if interface.get("admin_up") and interface.get("oper_up") is False:
            anomalies.append({"type": "interface_down", "device": interface.get("device"), "interface": interface.get("name")})
    for link in snapshot.links:
        if link.get("confirmation") != "confirmed" or link.get("freshness") != "fresh":
            anomalies.append({"type": "unconfirmed_link" if link.get("confirmation") != "confirmed" else "stale_link",
                              "link_id": link["id"], "from": link["from"], "to": link["to"]})
    return {"status": "ok", "observed_at": snapshot.observed_at, "anomalies": anomalies, "warnings": snapshot.warnings}


@app.get("/tools/options")
def tools_options(site: str = "all") -> dict[str, object]:
    snapshot = load_snapshot()
    inventory = snapshot.devices if snapshot else [asdict(d) for d in devices]
    selected = [d for d in inventory if site == "all" or d.get("site") == site]
    ids = {d.get("id") for d in selected}
    return {
        "devices": sorted(d["name"] for d in selected),
        "sites": sorted({d["site"] for d in inventory if d.get("site")}),
        "vrfs": sorted({v["name"] for v in snapshot.vrfs if v["name"] != "unknown" and v["device_id"] in ids}) if snapshot else [],
    }


def _lookup_mac(query: str, device_filter: str = "all", vrf_filter: str = "all") -> dict[str, object]:
    resolved = _resolve_endpoint(query, device_filter=device_filter, vrf_filter=vrf_filter)
    matches = resolved.get("mac_matches", [])
    arp_matches = resolved.get("arp_matches", [])
    clf_matches = [match for match in matches if match.get("role") == "clf"]
    return {
        "query": query,
        "normalized_query": resolved.get("mac") or query,
        "resolved_ip": resolved.get("ip"),
        "resolved_mac": resolved.get("mac"),
        "device_filter": _normalize_filter(device_filter),
        "vrf_filter": _normalize_filter(vrf_filter),
        "found": bool(matches or arp_matches),
        "clf_matches": clf_matches,
        "local_matches": [match for match in matches if match.get("location_type") == "local"],
        "overlay_matches": [match for match in matches if match.get("location_type") == "overlay"],
        "all_matches": matches,
        "arp_matches": arp_matches,
    }


def _resolve_endpoint(query: str, device_filter: str = "all", vrf_filter: str = "all") -> dict[str, object]:
    query = query.strip()
    normalized_device_filter = _normalize_filter(device_filter)
    normalized_vrf_filter = _normalize_filter(vrf_filter)
    ip = query if _is_ip(query) else ""
    mac = ""
    arp_matches: list[dict[str, object]] = []

    if ip:
        for result in _filtered_results(normalized_device_filter):
            for entry in result.arp_table:
                if entry.ip == ip and _matches_filter(entry.vrf, normalized_vrf_filter):
                    mac = entry.mac
                    arp_matches.append(
                        {
                            "device": result.device.name,
                            "role": result.device.role,
                            "site": result.device.site,
                            **asdict(entry),
                        }
                    )
    else:
        mac = normalize_mac(query)
        for result in _filtered_results(normalized_device_filter):
            for entry in result.arp_table:
                if entry.mac == mac:
                    arp_matches.append(
                        {
                            "device": result.device.name,
                            "role": result.device.role,
                            "site": result.device.site,
                            **asdict(entry),
                        }
                    )

    mac_matches: list[dict[str, object]] = []
    if mac:
        for result in _filtered_results(normalized_device_filter):
            for entry in result.mac_table:
                if entry.mac == mac:
                    mac_matches.append(
                        {
                            "device": result.device.name,
                            "role": result.device.role,
                            "site": result.device.site,
                            "location_type": _mac_location_type(entry.port),
                            **asdict(entry),
                        }
                    )

    return {
        "query": query,
        "device_filter": normalized_device_filter,
        "vrf_filter": normalized_vrf_filter,
        "ip": ip or None,
        "mac": mac or None,
        "arp_matches": sorted(arp_matches, key=lambda item: (item.get("vrf") != "TENANT", item["site"], item["device"], item["ip"])),
        "mac_matches": sorted(mac_matches, key=lambda item: (item["location_type"] != "local", item["site"], item["device"], item["vlan"])),
    }


def _filtered_results(device_filter: str):
    for result in collector.latest_results.values():
        if _matches_filter(result.device.name, device_filter):
            yield result


def _normalize_filter(value: str | None) -> str:
    value = (value or "all").strip()
    return value if value else "all"


def _matches_filter(value: object, selected: str) -> bool:
    return selected == "all" or str(value) == selected


def _mac_location_type(port: str) -> str:
    normalized = port.lower()
    if normalized.startswith(("vx", "vxlan")):
        return "overlay"
    if normalized == "cpu":
        return "control_plane"
    return "local"


def _is_ip(value: str) -> bool:
    parts = value.split(".")
    if len(parts) != 4:
        return False
    try:
        return all(0 <= int(part) <= 255 for part in parts)
    except ValueError:
        return False


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    rows = []
    for device in devices:
        result = collector.latest_results.get(device.name)
        status = "unknown"
        detail = "collection has not run yet"
        if result:
            status = "up" if result.ok else "down"
            detail = result.error or f"{result.duration_seconds:.1f}s"
        rows.append(
            f"<tr><td>{device.site}</td><td>{device.role}</td><td>{device.name}</td>"
            f"<td>{device.host}</td><td class='{status}'>{status}</td><td>{detail}</td></tr>"
        )

    return f"""
<!doctype html>
<html>
<head>
  <title>EVE AIOps Agent</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; color: #17202a; }}
    a {{ color: #075985; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 20px; }}
    th, td {{ border-bottom: 1px solid #d7dee8; padding: 10px; text-align: left; }}
    th {{ background: #f4f7fb; }}
    .up {{ color: #137333; font-weight: 700; }}
    .down {{ color: #b42318; font-weight: 700; }}
    .unknown {{ color: #6b7280; font-weight: 700; }}
  </style>
</head>
<body>
  <h1>EVE AIOps Agent</h1>
  <p><a href="/mrtg">MRTG-style interface view</a> | <a href="/metrics">Prometheus metrics</a></p>
  <table>
    <thead><tr><th>Site</th><th>Role</th><th>Device</th><th>Host</th><th>Status</th><th>Detail</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table>
</body>
</html>
"""


@app.get("/mrtg", response_class=HTMLResponse)
def mrtg_view() -> str:
    return """
<!doctype html>
<html>
<head>
  <title>MRTG Interface View</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    :root {
      color-scheme: light;
      --text: #17202a;
      --muted: #64748b;
      --line: #d7dee8;
      --panel: #ffffff;
      --bg: #f6f8fb;
      --green: #17803d;
      --blue: #2563eb;
      --red: #b42318;
      --yellow: #b7791f;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    header {
      position: sticky;
      top: 0;
      z-index: 3;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 18px 24px;
      background: rgba(255, 255, 255, 0.94);
      border-bottom: 1px solid var(--line);
      backdrop-filter: blur(10px);
    }
    h1 {
      margin: 0;
      font-size: 20px;
      font-weight: 700;
      letter-spacing: 0;
    }
    .toolbar {
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
    }
    select, button {
      height: 34px;
      border: 1px solid #cbd5e1;
      background: #fff;
      border-radius: 6px;
      padding: 0 10px;
      color: var(--text);
      font: inherit;
    }
    button { cursor: pointer; }
    main {
      padding: 20px 24px 28px;
    }
    .statusbar {
      display: flex;
      justify-content: space-between;
      gap: 16px;
      color: var(--muted);
      font-size: 13px;
      margin-bottom: 16px;
    }
    .legend {
      display: flex;
      gap: 14px;
      align-items: center;
      flex-wrap: wrap;
    }
    .legend span {
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }
    .dot {
      width: 10px;
      height: 10px;
      border-radius: 50%;
      display: inline-block;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(420px, 1fr));
      gap: 16px;
    }
    .chart {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
      box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
    }
    .chart-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 12px 14px;
      border-bottom: 1px solid var(--line);
    }
    .title {
      min-width: 0;
      font-weight: 700;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .meta {
      display: flex;
      align-items: center;
      gap: 10px;
      color: var(--muted);
      font-size: 12px;
      white-space: nowrap;
    }
    .state {
      border-radius: 999px;
      padding: 2px 8px;
      font-weight: 700;
      color: #fff;
    }
    .state.up { background: var(--green); }
    .state.down { background: var(--red); }
    canvas {
      display: block;
      width: 100%;
      height: 220px;
    }
    .empty {
      padding: 48px 24px;
      color: var(--muted);
      text-align: center;
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 8px;
    }
    @media (max-width: 640px) {
      header { align-items: flex-start; flex-direction: column; }
      main { padding: 14px; }
      .grid { grid-template-columns: 1fr; }
      canvas { height: 190px; }
    }
  </style>
</head>
<body>
  <header>
    <h1>MRTG Interface View</h1>
    <div class="toolbar">
      <select id="deviceFilter" aria-label="Device"></select>
      <button id="clearHistory" type="button">Clear History</button>
    </div>
  </header>
  <main>
    <div class="statusbar">
      <div id="summary">Loading...</div>
      <div class="legend">
        <span><i class="dot" style="background: var(--green)"></i>RX</span>
        <span><i class="dot" style="background: var(--blue)"></i>TX</span>
        <span><i class="dot" style="background: var(--red)"></i>Down</span>
      </div>
    </div>
    <section id="charts" class="grid"></section>
  </main>
  <script>
    const HISTORY_KEY = "eve-aiops-mrtg-history-v2";
    const MAX_POINTS = 120;
    const REFRESH_MS = 10000;
    const state = {
      samples: loadHistory(),
      latest: [],
      filter: localStorage.getItem("eve-aiops-mrtg-device") || "all",
    };

    const charts = document.getElementById("charts");
    const summary = document.getElementById("summary");
    const deviceFilter = document.getElementById("deviceFilter");
    document.getElementById("clearHistory").addEventListener("click", () => {
      state.samples = {};
      localStorage.removeItem(HISTORY_KEY);
      render();
    });
    deviceFilter.addEventListener("change", () => {
      state.filter = deviceFilter.value;
      localStorage.setItem("eve-aiops-mrtg-device", state.filter);
      render();
    });

    async function refresh() {
      try {
        const response = await fetch("/mrtg/data", { cache: "no-store" });
        const payload = await response.json();
        state.latest = payload.interfaces || [];
        mergeSamples(payload.timestamp, state.latest);
        saveHistory();
        updateDeviceFilter();
        render();
      } catch (error) {
        summary.textContent = "Unable to load interface data";
      }
    }

    function mergeSamples(timestamp, interfaces) {
      for (const item of interfaces) {
        const key = item.device + "::" + item.interface;
        if (!state.samples[key]) state.samples[key] = [];
        const points = state.samples[key];
        const last = points[points.length - 1];
        if (!last || last.t !== timestamp) {
          points.push({
            t: timestamp,
            rx: Number(item.rx_bps || 0),
            tx: Number(item.tx_bps || 0),
            up: Boolean(item.oper_up),
          });
          if (points.length > MAX_POINTS) points.splice(0, points.length - MAX_POINTS);
        }
      }
    }

    function updateDeviceFilter() {
      const devices = Array.from(new Set(state.latest.map(item => item.device))).sort();
      const current = deviceFilter.value || state.filter;
      deviceFilter.innerHTML = "";
      deviceFilter.append(new Option("All devices", "all"));
      for (const device of devices) deviceFilter.append(new Option(device, device));
      deviceFilter.value = devices.includes(current) ? current : "all";
      state.filter = deviceFilter.value;
    }

    function render() {
      const items = state.latest
        .filter(item => state.filter === "all" || item.device === state.filter)
        .sort((a, b) => (a.device + a.interface).localeCompare(b.device + b.interface));
      summary.textContent = `${items.length} interfaces, last refresh ${new Date().toLocaleTimeString()}`;
      charts.innerHTML = "";
      if (!items.length) {
        const empty = document.createElement("div");
        empty.className = "empty";
        empty.textContent = "No interface samples yet";
        charts.append(empty);
        return;
      }
      for (const item of items) {
        const key = item.device + "::" + item.interface;
        const card = document.createElement("article");
        card.className = "chart";
        card.innerHTML = `
          <div class="chart-head">
            <div class="title" title="${escapeHtml(item.device)} ${escapeHtml(item.interface)}">
              ${escapeHtml(item.device)} / ${escapeHtml(item.interface)}
            </div>
            <div class="meta">
              <span>RX ${formatBps(item.rx_bps)}</span>
              <span>TX ${formatBps(item.tx_bps)}</span>
              <span>${escapeHtml(item.traffic_source || "unknown")}</span>
              <span class="state ${item.oper_up ? "up" : "down"}">${item.oper_up ? "up" : "down"}</span>
            </div>
          </div>
          <canvas></canvas>
        `;
        charts.append(card);
        drawChart(card.querySelector("canvas"), state.samples[key] || []);
      }
    }

    function drawChart(canvas, points) {
      const scale = window.devicePixelRatio || 1;
      const rect = canvas.getBoundingClientRect();
      canvas.width = Math.max(1, Math.floor(rect.width * scale));
      canvas.height = Math.max(1, Math.floor(rect.height * scale));
      const ctx = canvas.getContext("2d");
      ctx.scale(scale, scale);
      const width = rect.width;
      const height = rect.height;
      const pad = { top: 18, right: 18, bottom: 28, left: 58 };
      const plotW = width - pad.left - pad.right;
      const plotH = height - pad.top - pad.bottom;
      const max = Math.max(1, ...points.flatMap(p => [p.rx, p.tx]));

      ctx.clearRect(0, 0, width, height);
      ctx.fillStyle = "#ffffff";
      ctx.fillRect(0, 0, width, height);

      ctx.strokeStyle = "#e2e8f0";
      ctx.lineWidth = 1;
      ctx.font = "12px -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif";
      ctx.fillStyle = "#64748b";
      for (let i = 0; i <= 4; i++) {
        const y = pad.top + (plotH / 4) * i;
        ctx.beginPath();
        ctx.moveTo(pad.left, y);
        ctx.lineTo(width - pad.right, y);
        ctx.stroke();
        ctx.fillText(formatBps(max * (1 - i / 4)), 10, y + 4);
      }

      drawSeries(ctx, points, "rx", "#17803d", pad, plotW, plotH, max);
      drawSeries(ctx, points, "tx", "#2563eb", pad, plotW, plotH, max);

      ctx.strokeStyle = "#94a3b8";
      ctx.beginPath();
      ctx.moveTo(pad.left, pad.top);
      ctx.lineTo(pad.left, height - pad.bottom);
      ctx.lineTo(width - pad.right, height - pad.bottom);
      ctx.stroke();
    }

    function drawSeries(ctx, points, field, color, pad, plotW, plotH, max) {
      if (!points.length) return;
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      ctx.beginPath();
      points.forEach((point, index) => {
        const x = pad.left + (points.length === 1 ? plotW : (plotW * index) / (points.length - 1));
        const y = pad.top + plotH - (Math.max(0, point[field]) / max) * plotH;
        if (index === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.stroke();
    }

    function formatBps(value) {
      const num = Number(value || 0);
      if (num >= 1000000000) return (num / 1000000000).toFixed(2) + " Gbps";
      if (num >= 1000000) return (num / 1000000).toFixed(2) + " Mbps";
      if (num >= 1000) return (num / 1000).toFixed(2) + " Kbps";
      return Math.round(num) + " bps";
    }

    function escapeHtml(value) {
      return String(value).replace(/[&<>"']/g, ch => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      }[ch]));
    }

    function loadHistory() {
      try {
        return JSON.parse(localStorage.getItem(HISTORY_KEY) || "{}");
      } catch {
        return {};
      }
    }

    function saveHistory() {
      localStorage.setItem(HISTORY_KEY, JSON.stringify(state.samples));
    }

    refresh();
    setInterval(refresh, REFRESH_MS);
    window.addEventListener("resize", render);
  </script>
</body>
</html>
"""


@app.get("/mrtg/data")
def mrtg_data() -> dict[str, object]:
    interfaces = []
    telemetry_rates = _mrtg_telemetry_rates()
    for result in sorted(collector.latest_results.values(), key=lambda item: item.device.name):
        for iface in result.interfaces:
            telemetry = telemetry_rates.get((result.device.name, str(iface["name"])))
            interfaces.append(
                {
                    "device": result.device.name,
                    "interface": iface["name"],
                    "admin_up": bool(iface["admin_up"]),
                    "oper_up": bool(iface["oper_up"]),
                    "rx_bps": float(telemetry.get("rx_bps") if telemetry else iface["rx_bps"] or 0),
                    "tx_bps": float(telemetry.get("tx_bps") if telemetry else iface["tx_bps"] or 0),
                    "traffic_source": "telemetry" if telemetry else "agent",
                }
            )
    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "interfaces": interfaces,
    }


def _mrtg_telemetry_rates() -> dict[tuple[str, str], dict[str, float]]:
    host_to_device = {device.host: device.name for device in devices}
    counters: dict[tuple[str, str, str], float] = {}
    now = time.time()
    try:
        with urllib.request.urlopen(MRTG_TELEMETRY_METRICS_URL, timeout=3) as response:
            payload = response.read().decode("utf-8", errors="ignore")
    except Exception:
        return {}

    for line in payload.splitlines():
        metric = _parse_telegraf_interface_octet_metric(line)
        if not metric:
            continue
        source, interface, direction, value = metric
        device = host_to_device.get(source)
        if not device and ":" in source:
            device = host_to_device.get(source.rsplit(":", 1)[0])
        if not device:
            continue
        counters[(device, interface, direction)] = value

    rates: dict[tuple[str, str], dict[str, float]] = {}
    for key, value in counters.items():
        previous = MRTG_TELEMETRY_CACHE.get(key)
        MRTG_TELEMETRY_CACHE[key] = (value, now)
        if not previous:
            continue
        previous_value, previous_time = previous
        elapsed = now - previous_time
        if elapsed <= 0 or value < previous_value:
            continue
        device, interface, direction = key
        field = "rx_bps" if direction == "in" else "tx_bps"
        rates.setdefault((device, interface), {})[field] = ((value - previous_value) * 8) / elapsed
    return {
        key: value
        for key, value in rates.items()
        if "rx_bps" in value or "tx_bps" in value
    }


def _parse_telegraf_interface_octet_metric(line: str) -> tuple[str, str, str, float] | None:
    match = re.match(r"^arista_interface_counters_(in|out)_octets\{([^}]*)\}\s+([0-9.eE+-]+)$", line)
    if not match:
        return None
    labels = _parse_prometheus_labels(match.group(2))
    source = labels.get("source", "")
    interface = labels.get("name", "")
    if not source or not interface:
        return None
    try:
        value = float(match.group(3))
    except ValueError:
        return None
    return source, interface, match.group(1), value


def _parse_prometheus_labels(raw: str) -> dict[str, str]:
    labels: dict[str, str] = {}
    for key, value in re.findall(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:\\.|[^"])*)"', raw):
        labels[key] = value.replace('\\"', '"').replace("\\\\", "\\")
    return labels
