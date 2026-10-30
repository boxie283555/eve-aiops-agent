"""Independent, bounded lab-only PAN-OS policy collection and read-only checks."""
from __future__ import annotations

import json
import os
import ssl
import threading
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from .collector import Collector
from .inventory import load_inventory
from .nautobot import NautobotClient
from .panos_policy import parse_config
from .topology import atomic_json, fresh

VERIFY_SLOTS = threading.BoundedSemaphore(2)


def cache_path():
    return Path(os.getenv('AIOPS_PANOS_POLICY_PATH','/app/data/panos-policy.json'))


def now():
    return datetime.now(timezone.utc).isoformat()


def lab_firewalls(settings):
    if not settings.nautobot_url or not settings.nautobot_token:
        raise ValueError('Current Nautobot site validation is required')
    records={r['name']:r for r in NautobotClient(settings.nautobot_url,settings.nautobot_token,15).devices()}
    return [d for d in load_inventory() if d.platform=='paloalto_panos' and d.site=='lab'
            and records.get(d.name,{}).get('site')=='lab'
            and records[d.name].get('host')==d.host and records[d.name].get('id')==d.id]


class PanosAPIError(ValueError):
    def __init__(self, root):
        message = ' '.join(root.itertext()).strip()
        self.no_nat_policy = message == 'No nat-policy'
        super().__init__('PAN API rejected operation: '+str(root.get('code')))


class PanosAPI:
    def __init__(self, device, settings):
        if device.site != 'lab' or device.platform != 'paloalto_panos':
            raise ValueError('PA API access is restricted to lab')
        self.url='https://'+device.host+'/api/'
        # The established lab deployment uses self-signed management certificates.
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(context=ssl._create_unverified_context()))
        self.key=None
        user,password,_=Collector(settings,[])._device_credentials(device)
        self.key=self.call(type='keygen',user=user,password=password).findtext('.//key')
        if not self.key:
            raise ValueError('PA authentication did not return a key')

    def call(self, **params):
        if self.key:params['key']=self.key
        request=urllib.request.Request(self.url,data=urllib.parse.urlencode(params).encode())
        with self.opener.open(request,timeout=15) as response:
            raw=response.read(16*1024*1024+1)
        if len(raw)>16*1024*1024:raise ValueError('PA response exceeds bounded size')
        root=ET.fromstring(raw)
        if root.get('status')!='success':raise PanosAPIError(root)
        return root

    def command(self, path, fields, vsys='vsys1'):
        root=ET.Element(path[0]);node=root
        for tag in path[1:]:node=ET.SubElement(node,tag)
        for name,value in fields.items():
            if value is not None:ET.SubElement(node,name).text=str(value)
        return self.call(type='op',cmd=ET.tostring(root,encoding='unicode'),vsys=vsys)


def read_cache():
    try:
        return json.loads(cache_path().read_text())
    except (OSError,ValueError):
        return {'status':'unavailable','devices':{}}


def refresh_policy(settings):
    """Own scheduler job; no waits in metrics collection or path requests."""
    previous=read_cache();stamp=now()
    try:
        devices=lab_firewalls(settings)
    except Exception as exc:
        data={**previous,'status':'error','last_attempt_at':stamp,'error':type(exc).__name__,
              'devices':{k:{**v,'status':'error','freshness':'stale'} for k,v in previous.get('devices',{}).items()}}
        atomic_json(cache_path(),data)
        return
    def collect(device):
        prior=previous.get('devices',{}).get(device.id,{})
        record={'device_id':device.id,'device':device.name,'host':device.host,'site':'lab','last_attempt_at':stamp}
        try:
            root=PanosAPI(device,settings).call(type='config',action='show',xpath='/config')
            model=parse_config(ET.tostring(root,encoding='unicode'))
            return device.id,{**model,**record,'last_success_at':now(),'freshness':'fresh'}
        except Exception as exc:
            return device.id,{**prior,**record,'status':'error','freshness':'stale','error':type(exc).__name__}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=dict(pool.map(collect,devices))
    atomic_json(cache_path(),{'status':'ok' if all(v['status']=='ok' for v in results.values()) else 'partial',
                            'last_attempt_at':stamp,'completed_at':now(),'devices':results})


def attach_policy(snapshot):
    cache=read_cache()
    rows=[]
    for device in snapshot.devices:
        row={**device}
        if device.get('platform')=='paloalto_panos':
            evidence=cache.get('devices',{}).get(device['id'],{})
            if (device.get('site')=='lab' and evidence.get('host')==device.get('host')
                    and evidence.get('status')=='ok' and fresh(evidence)):
                row['pa_policy']=evidence
            else:
                row['pa_policy']={'status':'unavailable','reason':'Missing, failed, stale or out-of-scope PA policy evidence'}
        rows.append(row)
    return replace(snapshot,devices=rows)


def policy_status():
    cache=read_cache()
    return {'status':cache.get('status'),'last_attempt_at':cache.get('last_attempt_at'),
            'devices':[{k:r.get(k) for k in ['device','device_id','last_success_at','error','config_digest']} |
                       {'status':r.get('status'),'fresh':fresh(r)} for r in cache.get('devices',{}).values()]}


def verify_flow(settings, request):
    if not VERIFY_SLOTS.acquire(blocking=False):
        return {'status':'busy','reason':'Two PA verification requests are already running'}
    try:
        device=next((d for d in lab_firewalls(settings) if d.id==request['device_id']),None)
        if device is None:
            return {'status':'unavailable','reason':'Device is not a current Nautobot lab firewall'}
        from .flow import FlowState
        flow=FlowState(**request['flow'])
        if flow.protocol=='any' or (flow.protocol in {'tcp','udp'} and (flow.source_port is None or flow.destination_port is None)):
            return {'status':'invalid','reason':'Native verification requires a protocol and both TCP/UDP ports'}
        evidence=read_cache().get('devices',{}).get(device.id,{})
        if not fresh(evidence) or evidence.get('status')!='ok' or evidence.get('config_digest')!=request['config_digest']:
            return {'status':'stale','reason':'PA policy evidence changed or expired; rerun the path query'}
        context=evidence.get('vsyses',{}).get(request['vsys'],{})
        zones=context.get('zones',{})
        if zones.get(request['ingress'])!=request['from_zone'] or any(request[k] not in zones.values() for k in ['to_zone','original_to_zone']):
            return {'status':'invalid','reason':'Zone context does not match collected configuration'}
        api=PanosAPI(device,settings)
        protocol={'tcp':6,'udp':17,'icmp':1,'icmpv6':58}[flow.protocol]
        common={'from':request['from_zone'],'to':request['to_zone'],'source':flow.source_ip,
                'destination':flow.destination_ip,'protocol':protocol,'source-port':flow.source_port,
                'destination-port':flow.destination_port}
        if flow.application:common['application']=flow.application
        result={'status':'ok','device':device.name,'observed_at':now(),'flow':flow.data(),
                'evidence_level':'device_query','notes':['Native rule matching does not prove packet delivery. No matching session does not imply a policy deny.']}
        for kind in ['security','nat']:
            fields=dict(common)
            if kind=='nat':
                fields['to']=request['original_to_zone'];fields.pop('application',None)
            try:
                root=api.command(['test',kind+'-policy-match'],fields,request['vsys'])
                rules=[{'name':e.get('name'),'action':e.findtext('action')} for e in root.findall('.//rules/entry')]
                result[kind]={'status':'matched' if rules else 'unavailable','rules':rules}
            except PanosAPIError as exc:
                result[kind]={'status':'no_match' if kind=='nat' and exc.no_nat_policy else 'unavailable'}
            except Exception:
                result[kind]={'status':'unavailable'}
        try:
            fields={k:v for k,v in common.items() if k not in {'from','to','application'}}
            root=api.command(['show','session','all','filter'],fields,request['vsys'])
            allowed={'id','idx','vsys','application','source','src','dst','destination','sport','dport','proto','state','rule','ingress','egress'}
            rows=[{c.tag:c.text for c in e if c.tag in allowed} for e in root.findall('.//entry')]
            rows=[r for r in rows if r]
            empty=len(root.find('result'))==0 and not (root.findtext('result') or '').strip()
            result['sessions']={'status':'observed' if rows else 'not_observed' if empty else 'unavailable',
                                'entries':rows[:20],'truncated':len(rows)>20}
        except Exception:
            result['sessions']={'status':'unavailable','entries':[]}
        return result
    except Exception as exc:
        return {'status':'unavailable','reason':type(exc).__name__}
    finally:
        VERIFY_SLOTS.release()
