"""Passive, bounded read model for the branded monitoring workspace.

Queries are fixed here; the browser cannot supply PromQL or a backend URL.
No device I/O, inventory writes or collector scheduling occur in this module.
"""
from __future__ import annotations

import asyncio
import copy
import json
import math
import os
import time
import urllib.parse
import urllib.request
from collections import OrderedDict, defaultdict
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse
from .inventory import device_classification

RANGES = {'1h': 3600, '6h': 21600, '24h': 86400, '7d': 604800, '30d': 2592000}
METRICS = [
    'aiops_device_up', 'aiops_collect_success', 'aiops_device_hardware_info',
    'aiops_device_cpu_utilization_percent', 'aiops_device_memory_utilization_percent',
    'aiops_device_uptime_seconds', 'aiops_asic_tcam_resource_utilization_percent',
    'aiops_telemetry_asic_tcam_resource_utilization_percent',
    'aiops_interface_admin_up', 'aiops_interface_oper_up',
    'aiops_interface_rx_bps', 'aiops_interface_tx_bps',
    'aiops_pa_interface_rx_bps', 'aiops_pa_interface_tx_bps',
    'aiops_firewall_sessions_active', 'aiops_firewall_sessions_per_second',
    'aiops_firewall_throughput_bps', 'aiops_config_drift_records',
    'aiops_config_backup_success', 'aiops_config_backup_failure_info',
    'aiops_config_backup_last_attempt_timestamp_seconds',
    'aiops_bgp_neighbor_up', 'aiops_ospf_neighbor_up', 'aiops_nve_peer_up',
]
GLOBAL_QUERY = ('{__name__=~"up|aiops_config_history_available|aiops_nautobot_sync_success|'
                'aiops_nautobot_sync_last_success_timestamp_seconds",job="eve-aiops-agent"}')
GATE = ' and on() (up{job="eve-aiops-agent"} == 1)'


def number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError):
        return None


def timestamp(value):
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    except (ValueError, AttributeError, TypeError):
        return None


def inventory_records(collector):
    """Explicit public metadata; never serialize a Device or an exception."""
    results = dict(collector.latest_results)
    rows = []
    for device in list(collector.devices):
        result = results.get(device.name)
        manufacturer, kind = device_classification(device)
        error = (result.error or '').lower() if result else ''
        reason = ('authentication' if 'auth' in error else 'timeout' if 'timeout' in error or 'timed out' in error
                  else 'collection_failed' if result and not result.ok else None)
        allowed = set(METRICS)
        if result:
            for metric, attr in [('aiops_device_cpu_utilization_percent','cpu_percent'),
                                 ('aiops_device_memory_utilization_percent','memory_percent'),
                                 ('aiops_device_uptime_seconds','uptime_seconds')]:
                if getattr(result.resources,attr,None) is None:allowed.discard(metric)
            if not result.dataset_status.get('version'):allowed.discard('aiops_device_hardware_info')
            if not result.dataset_status.get('interfaces'):
                allowed = {m for m in allowed if not m.startswith('aiops_interface_')}
            for metric, field in [('aiops_firewall_sessions_active','sessions_active'),
                                  ('aiops_firewall_sessions_per_second','sessions_per_second'),
                                  ('aiops_firewall_throughput_bps','throughput_bps')]:
                if field not in result.firewall_metrics:allowed.discard(metric)
            for metric, field in [('aiops_bgp_neighbor_up','bgp'),('aiops_ospf_neighbor_up','ospf'),('aiops_nve_peer_up','nve')]:
                if not getattr(result,field):allowed.discard(metric)
            if not result.tcam_resources:allowed.discard('aiops_asic_tcam_resource_utilization_percent')
        rows.append(dict(id=device.id, name=device.name, host=device.host, site=device.site,
                         manufacturer=manufacturer, device_type=kind,
                         collected_at=result.collected_at if result else None,
                         collection_ok=result.ok if result else None, reason=reason, valid_metrics=sorted(allowed)))
    return rows


def selectors(site, device):
    # Equality, not regex: punctuation in inventory names has no query semantics.
    labels = ['job="eve-aiops-agent"']
    if site != 'all':labels.append('site='+json.dumps(site))
    if device != 'all':labels.append('device='+json.dumps(device))
    return 'aiops_device_up{'+','.join(labels)+'}'


def expressions(site, device):
    scope = selectors(site, device)
    current = ('{__name__=~"'+'|'.join(METRICS)+'",job="eve-aiops-agent"}'
               ' and on(device) '+scope)
    valid = '('+scope+' == 1) and on(device) (aiops_collect_success == 1)'
    def named(expr, label):
        return 'label_replace(('+expr+GATE+'),"series","'+label+'","","")'
    trends = []
    for direction in ('rx', 'tx'):
        value = '(aiops_pa_interface_'+direction+'_bps or on(device,interface) aiops_interface_'+direction+'_bps)'
        trends.append(named('max(('+value+') and on(device) ('+valid+'))', direction))
    trends.append(named('sum('+scope+')', 'online'))
    return current, ' or '.join(trends)


def prom_request(endpoint, params):
    base = os.getenv('AIOPS_PROMETHEUS_URL', 'http://prometheus:9090').rstrip('/')
    request = urllib.request.Request(base+'/api/v1/'+endpoint+'?'+urllib.parse.urlencode(params))
    # Internal service access must not inherit a workstation or HTTP proxy.
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=8) as response:
        content = response.read(8_000_001)
    if len(content) > 8_000_000:raise ValueError('Metric response too large')
    data = json.loads(content)
    if data.get('status') != 'success' or data.get('warnings'):
        raise ValueError('Metric query incomplete')
    return data['data']['result']


class EvidenceReader:
    def __init__(self):
        self.cache = OrderedDict()
        self.slots = None

    async def read(self, site, device, period):
        key = (site, device, period)
        if self.slots is None:self.slots = asyncio.Semaphore(2)
        cached = self.cache.get(key)
        if cached and time.monotonic()-cached[0] < 25:
            self.cache.move_to_end(key)
            return copy.deepcopy(cached[1])
        # Bound active backend query groups, including calls made by multiple tabs.
        try:await asyncio.wait_for(self.slots.acquire(), timeout=.15)
        except asyncio.TimeoutError:raise HTTPException(429, 'Monitoring is busy; retry shortly') from None
        try:
            end = int(time.time())
            step = max(30, math.ceil(RANGES[period]/240))
            start = end-RANGES[period]
            current, trend = expressions(site, device)
            specs = [('current', 'query', {'query':current, 'time':end}),
                     ('global', 'query', {'query':GLOBAL_QUERY, 'time':end}),
                     ('trend', 'query_range', {'query':trend, 'start':start, 'end':end, 'step':step})]
            replies = await asyncio.gather(*(asyncio.to_thread(prom_request, endpoint, params)
                                            for _, endpoint, params in specs), return_exceptions=True)
            payload = {'start':start, 'end':end, 'step':step, 'errors':[]}
            for (name, _, _), reply in zip(specs, replies):
                if isinstance(reply, Exception):
                    payload[name] = []
                    payload['errors'].append(name)
                else:payload[name] = reply
            if not payload['errors']:
                self.cache[key] = (time.monotonic(), copy.deepcopy(payload))
                self.cache.move_to_end(key)
                while len(self.cache) > 24:self.cache.popitem(last=False)
            return payload
        finally:self.slots.release()


def summarize(records, health, evidence, now=None, ttl=600):
    now = now or time.time()
    global_values = {row['metric'].get('__name__'):number(row.get('value',[0,None])[1])
                     for row in evidence['global']}
    source_ok = ('global' not in evidence['errors'] and global_values.get('up') == 1
                 and 'current' not in evidence['errors'])
    config_ok = source_ok and global_values.get('aiops_config_history_available') == 1
    grouped = defaultdict(lambda:defaultdict(list))
    for row in evidence['current']:
        name = row['metric'].get('device')
        value = number(row.get('value',[0,None])[1])
        if value is not None:grouped[name][row['metric'].get('__name__')].append((row['metric'], value))
    health_by_name = {r['device']:r for r in health.get('devices', [])}
    devices, interfaces, issues = [], [], []
    versions = defaultdict(int)
    for metadata in records:
        row = dict(metadata)
        allowed = row.pop('valid_metrics', METRICS)
        name = row['name'];series = grouped[name]
        stamp = timestamp(row.get('collected_at'))
        fresh = stamp is not None and 0 <= now-stamp <= ttl
        def values(metric):return series.get(metric, []) if source_ok and metric in allowed else []
        def one(metric):
            nums = [v for _,v in values(metric)]
            return max(nums) if nums else None
        observed_up = one('aiops_device_up')
        row['state'] = ('unknown' if not source_ok or not fresh or observed_up is None else
                        'ok' if observed_up == 1 and row['collection_ok'] is True else 'failed')
        usable = row['state'] == 'ok' and one('aiops_collect_success') == 1
        row['age_seconds'] = max(0,now-stamp) if stamp is not None else None
        h = health_by_name.get(name, {})
        row.update(score=h.get('score') if source_ok and h.get('coverage_percent',0)>0 else None,
                   coverage=h.get('coverage_percent') if source_ok else None,
                   deduction=h.get('deduction_points') if source_ok else None,
                   unassessed=h.get('unassessed_points') if source_ok else None)
        for key, metric in [('cpu','aiops_device_cpu_utilization_percent'), ('memory','aiops_device_memory_utilization_percent'),
                            ('uptime','aiops_device_uptime_seconds'), ('sessions','aiops_firewall_sessions_active'),
                            ('session_rate','aiops_firewall_sessions_per_second'), ('throughput','aiops_firewall_throughput_bps')]:
            val = one(metric) if usable else None
            row[key] = val if val is not None and val >= 0 and (key not in {'cpu','memory'} or val <= 100) else None
        hardware = values('aiops_device_hardware_info') if usable else []
        row['version'] = hardware[0][0].get('version') if len(hardware)==1 else None
        row['model'] = hardware[0][0].get('model') if len(hardware)==1 else None
        if usable:versions[row['version'] or 'unknown'] += 1
        capacities = {}
        if usable:
            for metric in ['aiops_asic_tcam_resource_utilization_percent','aiops_telemetry_asic_tcam_resource_utilization_percent']:
                for labels,value in values(metric):capacities[(labels.get('resource'),labels.get('region'))] = value
        valid_capacity = [v for v in capacities.values() if 0 <= v <= 100]
        row['tcam'] = max(valid_capacity) if valid_capacity else None
        row['invalid_capacity'] = sum(not 0<=v<=100 for v in capacities.values())
        ports = {}
        if usable:
            for suffix in ['admin_up','oper_up','rx_bps','tx_bps']:
                for labels,value in values('aiops_interface_'+suffix):
                    interface = labels.get('interface')
                    ports.setdefault(interface, dict(device=name, interface=interface))[suffix] = value
            for suffix in ['rx_bps','tx_bps']:
                for labels,value in values('aiops_pa_interface_'+suffix):
                    interface = labels.get('interface')
                    ports.setdefault(interface, dict(device=name, interface=interface))[suffix] = value
        row['interfaces'] = len([p for p in ports.values() if 'oper_up' in p]) if ports else None
        row['interface_review'] = sum(p.get('admin_up')==1 and p.get('oper_up')==0 for p in ports.values()) if ports else None
        interfaces.extend(ports.values())
        for key, metric, identity in [('bgp','aiops_bgp_neighbor_up',('neighbor','vrf')),
                                       ('ospf','aiops_ospf_neighbor_up',('neighbor',)),
                                       ('overlay','aiops_nve_peer_up',('peer',))]:
            peers = {}
            for labels,value in values(metric) if usable else []:
                peer = tuple(labels.get(x) for x in identity)
                peers[peer] = min(peers.get(peer,1), value)
            row[key] = {'up':sum(v==1 for v in peers.values()),'total':len(peers)} if peers else None
        drifts = [(labels,v) for labels,v in values('aiops_config_drift_records')
                  if labels.get('origin')=='observed' and labels.get('status')=='open' and labels.get('unowned')=='true']
        row['drift'] = sum(v for _,v in drifts) if config_ok and drifts else None
        row['backup'] = one('aiops_config_backup_success') if config_ok else None
        failures = values('aiops_config_backup_failure_info')
        row['backup_reason'] = failures[0][0].get('reason') if row['backup']==0 and failures else None
        row['backup_attempt'] = one('aiops_config_backup_last_attempt_timestamp_seconds') if config_ok else None
        if row['state'] != 'ok':issues.append(dict(device=name,id=row['id'],kind='collection',reason=row.get('reason') if row['state']=='failed' else 'stale_or_missing'))
        if row['drift']:issues.append(dict(device=name,id=row['id'],kind='drift',count=row['drift']))
        if row['backup']==0:issues.append(dict(device=name,id=row['id'],kind='backup',reason=row['backup_reason']))
        for key in ['cpu','memory','tcam']:
            if row[key] is not None and row[key]>=80:issues.append(dict(device=name,id=row['id'],kind=key,value=row[key]))
        devices.append(row)
    def total(key):
        nums = [r[key] for r in devices if r.get(key) is not None]
        return sum(nums) if nums else None
    def mean(key):
        nums = [r[key] for r in devices if r.get(key) is not None]
        return sum(nums)/len(nums) if nums else None
    # Match health v1: devices lacking all evidence earn no score, rather than
    # silently dropping them from the equal-device mean.
    coverage = mean('coverage')
    score = (sum(r['score'] or 0 for r in devices)/len(devices)
             if source_ok and coverage and devices else None)
    timeline = list(range(evidence['start'], evidence['end']+1, evidence['step']))
    trends = {}
    for row in evidence['trend']:
        key = row['metric'].get('series')
        if key not in {'rx','tx','online'}:continue
        points = {int(float(t)):number(v) for t,v in row['values']}
        trends[key] = [points.get(t) for t in timeline]
    return dict(source_ok=source_ok, config_ok=config_ok, generated_at=now, sampled_at=evidence['end'],
                errors=evidence['errors'], devices=sorted(devices,key=lambda d:(d['manufacturer'],d['device_type'],d['name'])),
                interfaces=sorted(interfaces,key=lambda p:max(p.get('rx_bps',0),p.get('tx_bps',0)),reverse=True),
                issues=issues, versions=[dict(version=k,count=v) for k,v in sorted(versions.items(),key=lambda item:-item[1])],
                summary=dict(total=len(devices),online=sum(r['state']=='ok' for r in devices) if source_ok else None,
                             failed=sum(r['state']=='failed' for r in devices) if source_ok else None,
                             unknown=sum(r['state']=='unknown' for r in devices),score=score,coverage=coverage,
                             deduction=mean('deduction'),unassessed=mean('unassessed'),drift=total('drift'),
                             backup_failures=sum(r['backup']==0 for r in devices) if any(r['backup'] is not None for r in devices) else None,
                             backup_observed=sum(r['backup'] is not None for r in devices),
                             interface_review=total('interface_review')),
                sync=dict(success=global_values.get('aiops_nautobot_sync_success') if source_ok else None,
                          last_success=global_values.get('aiops_nautobot_sync_last_success_timestamp_seconds') if source_ok else None),
                trend=dict(timestamps=timeline,series=trends))


def router(collector, health_provider):
    routes = APIRouter(tags=['Monitoring workspace'])
    reader = EvidenceReader()

    @routes.get('/monitor',response_class=HTMLResponse)
    def page():
        from .monitoring_view import MONITOR_PAGE
        return MONITOR_PAGE

    @routes.get('/api/monitor')
    async def data(site: str = Query('all',max_length=100), device: str = Query('all',max_length=100),
                   period: Literal['1h','6h','24h','7d','30d'] = '24h'):
        all_records = inventory_records(collector)
        sites = sorted({r['site'] for r in all_records if r['site']})
        if site != 'all' and site not in sites:raise HTTPException(404, 'Unknown site')
        records = [r for r in all_records if site=='all' or r['site']==site]
        options = [dict(name=r['name'],id=r['id'],site=r['site']) for r in records]
        if device!='all':
            records = [r for r in records if r['name']==device]
            if not records:raise HTTPException(404, 'Unknown device in selected site')
        evidence = await reader.read(site, device, period)
        health = await asyncio.to_thread(health_provider)
        result = summarize(records,health,evidence,ttl=int(os.getenv('AIOPS_TOPOLOGY_TTL_SECONDS','600')))
        result.update(site=site,device=device,period=period,options=dict(sites=sites,devices=options))
        return result

    return routes
