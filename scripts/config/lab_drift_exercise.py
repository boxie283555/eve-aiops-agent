#!/usr/bin/env python3
"""Three reversible lab-only configuration changes; default is plan-only.

Run on the Agent host with PYTHONPATH=/app in the container. No startup-config
writes, policy attachment, route changes or BGP session resets are performed.
"""
from __future__ import annotations

import argparse
import json
import re
import time
import urllib.request
import uuid
from pathlib import Path

from netmiko import ConnectHandler
from aiops_agent.collector import Collector
from aiops_agent.config_history import current_lab_devices, normalize_config, differences, private_write, root_path
from aiops_agent.settings import load_settings


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device',default='p1-ext')
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--api',default='http://127.0.0.1:8080')
    args=parser.parse_args();settings=load_settings()
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    exercise='q3-lab-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6]
    report={'exercise_id':exercise,'device':args.device,'origin':'exercise','cases':[],'started_at':time.time()}
    report_path=root_path()/'exercises'/(exercise+'.json')

    def api(path,payload=None):
        request=urllib.request.Request(args.api+path,data=None if payload is None else json.dumps(payload).encode(),
                                       headers={'Content-Type':'application/json'})
        with opener.open(request,timeout=180) as response:return json.load(response)

    def connect():
        device=current_lab_devices(settings,[args.device])[0]
        assert device.site=='lab' and device.platform=='arista_eos','Exercise requires current Nautobot lab EOS'
        user,password,secret=Collector(settings,[])._device_credentials(device)
        return device,ConnectHandler(device_type=device.platform,host=device.host,port=device.port,username=user,password=password,
                                    secret=secret or '',conn_timeout=15,auth_timeout=15,banner_timeout=15)

    def save_report():private_write(report_path,json.dumps(report,indent=2,ensure_ascii=False))
    def backup(phase):
        result=api('/config/backup',{'devices':[args.device],'exercise_id':exercise,'phase':phase})
        assert result['status']=='ok',result
        api('/config/drift/detect',{})
        return result
    def configure(conn,commands):
        output=conn.send_config_set(commands,read_timeout=30,error_pattern=r'% (?:Invalid|Error|Incomplete|Ambiguous)')
        if re.search(r'%\s*(Invalid|Error|Incomplete|Ambiguous)',output,re.I):raise RuntimeError('Configuration command rejected')
    def model(conn):return normalize_config(conn.send_command('show running-config',read_timeout=30),'arista_eos')
    def peers(conn):
        data=json.loads(conn.send_command('show ip bgp summary vrf all | json',read_timeout=30))
        return sorted((vrf,ip,peer['peerState'],peer['upDownTime'])
                      for vrf,table in data['vrfs'].items() for ip,peer in table.get('peers',{}).items())

    device,conn=connect()
    try:
        baseline=model(conn)
        interface='interface Loopback0';assert interface in baseline
        old_desc=next((r['command'] for r in baseline[interface] if r['command'].startswith('description ')),None)
        bgp=next(k for k in baseline if k.startswith('router bgp '))
        neighbor=next(r for r in baseline[bgp] if re.match(r'neighbor \S+ remote-as \d+',r['command']))
        peer=neighbor['command'].split()[1];context=neighbor['path']
        old_peer_desc=next((r['command'] for r in baseline[bgp] if r['path']==context and r['command'].startswith('neighbor '+peer+' description ')),None)
        acl='AIOPS_Q3_'+uuid.uuid4().hex[:8].upper()
        assert 'ip access-list '+acl not in baseline
        cases=[
            ('interface_description',interface,[interface,'description '+exercise],[interface,old_desc or 'no description']),
            ('unbound_acl','ip access-list '+acl,['ip access-list '+acl,'10 permit ip host 192.0.2.100 host 192.0.2.200'],['no ip access-list '+acl]),
            ('bgp_neighbor_description',bgp,context+['neighbor '+peer+' description '+exercise],context+[old_peer_desc or 'no neighbor '+peer+' description']),
        ]
        before_peers=peers(conn)
        assert before_peers, "No BGP state evidence; abort exercise"
        report['baseline_bgp']=before_peers
        print(json.dumps({'mode':'execute' if args.execute else 'plan','device':device.name,'site':device.site,
                          'cases':[{'case':name,'commands':commands,'rollback':undo} for name,_,commands,undo in cases]}),flush=True)
    finally:conn.disconnect()
    if not args.execute:return
    report['baseline_backup']=backup('baseline');save_report()
    for name,expected_context,commands,undo in cases:
        device,conn=connect();changed=False
        result={'case':name,'context':expected_context,'commands':commands,'rollback_commands':undo}
        report['cases'].append(result);save_report()
        try:
            assert model(conn)==baseline,'Configuration changed since planning; abort before editing'
            changed=True  # Restore even if a multi-command operation only partially succeeds.
            configure(conn,commands)
            delta=differences(baseline,model(conn))
            assert len(delta)==1 and delta[0]['context']==expected_context,delta
            result['changed_backup']=backup('change');save_report()
            records=api('/config/drift?unowned=true&origin=exercise&limit=1000')['records']
            detected=[r for r in records if r['exercise_id']==exercise and r['phase']=='change'
                      and r['after_id']==result['changed_backup']['devices'][0]['version_id']]
            assert len(detected)==1,'Expected one unowned exercise drift'
            result['drift_id']=detected[0]['id'];result['category']=detected[0]['changes'][0]['category']
            print(json.dumps({'case':name,'drift_id':result['drift_id'],'category':result['category']}),flush=True)
        finally:
            try:
                if changed:
                    configure(conn,undo)
                    assert model(conn)==baseline,'Rollback differs from the original configuration'
                    result['rollback_verified']=True
                    result['rollback_backup']=backup('rollback')
                    result['bgp_unchanged']=peers(conn)==before_peers
                    save_report()
            finally:
                conn.disconnect()
        assert result['bgp_unchanged'],'BGP peer state changed during the exercise'
    records=api('/config/drift?unowned=true&origin=exercise&limit=1000')['records']
    ours=[r for r in records if r['exercise_id']==exercise]
    assert len([r for r in ours if r['phase']=='change'])==3
    for record in ours:
        api('/config/drift/'+record['id']+'/resolve',{'actor':'lab-exercise','reason':'Authorized simulation; original configuration restored and verified. Exercise is not real unowned-drift acceptance.'})
    report.update(status='passed',completed_at=time.time(),change_records=3,rollback_records=len(ours)-3,
                  baseline_restored=True,record_ids=[r['id'] for r in ours])
    save_report();print(json.dumps({'status':'passed','report':str(report_path),'change_records':3,'rollback_records':len(ours)-3}),flush=True)


if __name__=='__main__':main()
