"""Nautobot identity, Q3 evidence and isolated firewall-policy analysis."""
from __future__ import annotations

import copy
import difflib
import hashlib
import json
import os
import threading
import time
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from dataclasses import replace
from datetime import datetime, timezone

from . import config_history as history
from . import config_management as management
from .config_redaction import redacted_file_text
from .panos_policy import parse_config
from .panos_evidence import attach_policy, read_cache
from .topology import load_snapshot, find_path
from .flow import FlowState

SLOTS=threading.BoundedSemaphore(2)
CACHE={}
CACHE_LOCK=threading.Lock()


def firewalls(settings):
    return [d for d in history.current_lab_devices(settings) if d.platform=='paloalto_panos']


def target(settings, device_id):
    device=next((d for d in firewalls(settings) if d.id==device_id),None)
    if device is None:raise ValueError('Device is not a current Nautobot lab PAN-OS firewall')
    return device


def device_list(settings):
    states={s['device_id']:s for s in management.backup_status()['devices']}
    live=read_cache().get('devices',{})
    return {'scope':'lab','identity_source':'nautobot','devices':[
        {'id':d.id,'name':d.name,'host':d.host,'site':d.site,'vendor':'palo_alto',
         'latest_backup':states.get(d.id,{}).get('latest_backup'),
         'backup_success':states.get(d.id,{}).get('success'),
         'policy_observed_at':live.get(d.id,{}).get('last_success_at')}
        for d in firewalls(settings)]}


def evidence(settings,device_id,version_id=None):
    d=target(settings,device_id)
    versions=management.list_backups(d.id,1000)['versions']
    selected=next((v for v in versions if v['id']==version_id),None) if version_id else next(iter(versions),None)
    if not selected:raise ValueError('Backup version not found for selected firewall')
    row,_=management.load_version(selected['id'])
    if row['device_id']!=d.id or row['host']!=d.host or row['platform']!=d.platform:
        raise ValueError('Backup identity no longer matches Nautobot device')
    raw=(history.root_path()/row['raw_file']).read_text()
    if history.digest(raw)!=row['raw_hash']:raise ValueError('Backup file integrity check failed')
    return d,selected,raw


def policy_xml(raw):
    """Private adapter: send policy/object subtrees, never entire device secrets."""
    original=ET.fromstring(redacted_file_text(raw,'paloalto_panos'))
    output=ET.Element('config')
    allowed={'address','address-group','service','service-group','rulebase','pre-rulebase','post-rulebase','zone'}
    shared=original.find('shared')
    if shared is not None:
        item=ET.SubElement(output,'shared')
        for c in shared:
            if c.tag in allowed:item.append(copy.deepcopy(c))
    devices=ET.SubElement(output,'devices')
    for dev in original.findall('./devices/entry'):
        item=ET.SubElement(devices,'entry',dev.attrib);vsyses=ET.SubElement(item,'vsys')
        for vs in dev.findall('./vsys/entry'):
            child=ET.SubElement(vsyses,'entry',vs.attrib)
            for c in vs:
                if c.tag in allowed:child.append(copy.deepcopy(c))
    return ET.tostring(output,encoding='unicode')


def analyze(d, version_id, raw):
    clean=policy_xml(raw);key=(d.id,d.name,version_id,history.digest(clean))
    with CACHE_LOCK:cached=CACHE.get(key)
    if cached is not None:return copy.deepcopy(cached)
    base=os.getenv('AIOPS_FIREWALL_POLICY_URL','http://firewall-policy:8081').rstrip('/')
    request=urllib.request.Request(base+'/analyze',data=json.dumps({'device_id':d.id,'device_name':d.name,
        'snapshot_id':version_id,'config_xml':clean}).encode(),headers={'Content-Type':'application/json'})
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request,timeout=25) as response:
        data=response.read(12*1024*1024+1)
    if len(data)>12*1024*1024:raise ValueError('Policy analysis response exceeds size limit')
    result=json.loads(data)
    if result.get('device_id')!=d.id or result.get('snapshot_id')!=version_id:raise ValueError('Analysis identity mismatch')
    with CACHE_LOCK:
        if len(CACHE)>=32:CACHE.pop(next(iter(CACHE)))
        CACHE[key]=copy.deepcopy(result)
    return result


def inventory(settings,device_id,version_id=None):
    d,version,raw=evidence(settings,device_id,version_id)
    result=analyze(d,version['id'],raw)
    result['version']=version
    result['source']='q3_running_backup'
    result['age_seconds']=max(0,time.time()-version['last_observed_at'])
    result['historical']=True
    result['path_url']='/tools?site=lab&analysis=flow'
    result['backup_url']='/config/ui?device='+d.id+'#history'
    return result


def find_rules(root,context,kind):
    candidates=[v for v in root.findall('./devices/entry/vsys/entry') if v.get('name')==context]
    if len(candidates)!=1:raise ValueError('Missing or ambiguous VSYS context')
    rules=candidates[0].find('./rulebase/'+kind+'/rules')
    if rules is None:raise ValueError('Rulebase not found')
    return rules


def modify(raw,context,kind,name,operation,values):
    root=ET.fromstring(raw);rules=find_rules(root,context,kind)
    matches=[r for r in rules if r.get('name')==name]
    if len(matches)!=1:raise ValueError('Rule is missing or ambiguous')
    rule=matches[0]
    if operation in {'disable','enable','action'}:
        field='action' if operation=='action' else 'disabled'
        if operation=='action' and (kind!='security' or len(values)!=1 or values[0] not in {'allow','deny','drop','reset-client','reset-server','reset-both'}):
            raise ValueError('Invalid security action')
        child=rule.find(field)
        if child is None:child=ET.SubElement(rule,field)
        child.text=values[0] if operation=='action' else ('yes' if operation=='disable' else 'no')
    elif operation in {'source','destination','service'}:
        if not values or len(values)>100 or any(not v.strip() or len(v)>255 for v in values):raise ValueError('Explicit nonempty match values required')
        if kind=='nat' and operation=='service' and len(values)!=1:raise ValueError('NAT supports one service')
        child=rule.find(operation)
        if child is not None:rule.remove(child)
        child=ET.SubElement(rule,operation)
        if kind=='nat' and operation=='service':child.text=values[0]
        else:
            for value in values:ET.SubElement(child,'member').text=value
    elif operation in {'move_before','move_after'}:
        peer=next((r for r in rules if values and r.get('name')==values[0]),None)
        if peer is None or peer is rule:raise ValueError('Choose another existing rule in the same rulebase')
        rules.remove(rule);rules.insert(list(rules).index(peer)+(operation=='move_after'),rule)
    else:raise ValueError('Unsupported policy operation')
    result=ET.tostring(root,encoding='unicode')
    if history.normalize_config(result,'paloalto_panos')==history.normalize_config(raw,'paloalto_panos'):
        raise ValueError('No configuration change')
    return result


def flow_impact(device,version,raw,modified,flow):
    packet=FlowState(flow['source_ip'],flow['destination_ip'],flow.get('protocol','any'),
                     flow.get('source_port'),flow.get('destination_port'),flow.get('application'))
    snapshot=load_snapshot()
    if snapshot is None:return {'status':'unavailable','reason':'Topology snapshot unavailable'}
    snapshot=attach_policy(snapshot)
    if not any(d['id']==device.id and d.get('host')==device.host for d in snapshot.devices):
        return {'status':'unavailable','reason':'Firewall identity is absent or changed in topology'}
    results={}
    for label,config in [('before',raw),('after',modified)]:
        model=parse_config(config)
        model.update(last_success_at=datetime.fromtimestamp(version['last_observed_at'],timezone.utc).isoformat(),
                     evidence_source='q3_backup_replay',snapshot_id=version['id'])
        replay=replace(snapshot,devices=[dict(d,pa_policy=model) if d['id']==device.id else d for d in snapshot.devices])
        results[label]=find_path(replay,packet.source_ip,packet.destination_ip,flow.get('vrf','all'),
            site='lab',analysis='flow',protocol=packet.protocol,source_port=packet.source_port,
            destination_port=packet.destination_port,application=packet.application,
            ingress_interface=flow.get('ingress_interface'),mode='auto',policy_replay_ids=frozenset({device.id}))
    results.update(status='modeled',basis='selected_backup_on_current_topology',
        topology_observed_at=snapshot.observed_at,flow=flow,
        limitations=['Selected firewall backup is replayed on the current topology; other firewall evidence uses its own observation time.',
                     'Only the supplied new connection is evaluated. This is not a complete business impact inventory or packet-delivery test.'])
    return results


def preview(settings,request):
    d,version,raw=evidence(settings,request['device_id'],request['version_id'])
    before=analyze(d,version['id'],raw)
    rule=next((r for kind in ['policies','nat_rules'] for r in before[kind] if r['id']==request['rule_id']),None)
    if rule is None:raise ValueError('Rule is not in the selected backup')
    modified=modify(raw,rule['context'],rule['rulebase'],rule['rule_name'],request['operation'],request.get('values',[]))
    candidate_id='candidate-'+history.digest(modified)[:24]
    after=analyze(d,candidate_id,modified)
    before_keys={f['id'] for f in before['findings']};after_keys={f['id'] for f in after['findings']}
    stamp=time.time()
    result={'id':uuid.uuid4().hex,'created_at':stamp,'expires_at':stamp+600,'device_id':d.id,'device':d.name,
        'version':version,'rule_id':rule['id'],'rule_name':rule['rule_name'],'context':rule['context'],
        'kind':rule['rulebase'],'operation':request['operation'],'values':request.get('values',[]),
        'candidate_id':candidate_id,'before_summary':before['summary'],'after_summary':after['summary'],
        'introduced':[f for f in after['findings'] if f['id'] not in before_keys],
        'resolved':[f for f in before['findings'] if f['id'] not in after_keys],
        'diff':'\n'.join(difflib.unified_diff(redacted_file_text(raw,'paloalto_panos').splitlines(),
             redacted_file_text(modified,'paloalto_panos').splitlines(),fromfile='backup',tofile='candidate',n=3)),
        'actor':request['actor'],'reason':request.get('reason',''),'mode':'preview_only',
        'limitations':list(dict.fromkeys(before['limitations']+after['limitations'])),
        'flow_impact':flow_impact(d,version,raw,modified,request['flow']) if request.get('flow') else None,
        'status':'previewed','device_configuration_changed':False}
    with history.connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS firewall_reviews (id TEXT PRIMARY KEY, device_id TEXT, created_at REAL, payload TEXT)')
        db.execute('INSERT INTO firewall_reviews VALUES (?,?,?,?)',(result['id'],d.id,stamp,json.dumps(result,ensure_ascii=False)))
    management.audit('firewall_policy_preview',request['actor'],d.id,status='ok',review_id=result['id'],
                     version_id=version['id'],rule_id=rule['id'],operation=request['operation'])
    return result


def reviews(device_id):
    with history.connect() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='firewall_reviews'").fetchone():return []
        return [json.loads(r['payload']) for r in db.execute('SELECT payload FROM firewall_reviews WHERE device_id=? ORDER BY created_at DESC LIMIT 50',(device_id,))]


def get_review(settings,review_id):
    with history.connect() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='firewall_reviews'").fetchone():raise ValueError('Review not found')
        row=db.execute('SELECT payload FROM firewall_reviews WHERE id=?',(review_id,)).fetchone()
    if not row:raise ValueError('Review not found')
    result=json.loads(row['payload']);target(settings,result['device_id'])
    return result
