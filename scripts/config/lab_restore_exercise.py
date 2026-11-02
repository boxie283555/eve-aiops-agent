#!/usr/bin/env python3
"""Validate recovery on the current Nautobot lab p1-host using one interface description.

Default is a read-only plan. --execute performs a bounded description change, API
backup/compare/preview/recovery, and verifies full configuration and link states.
"""
from __future__ import annotations
import argparse
import json
import time
import urllib.request
import uuid

from aiops_agent import config_history as h, config_management as m
from aiops_agent.settings import load_settings


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute',action='store_true');args=parser.parse_args()
    settings=load_settings()
    def scoped():
        device=h.current_lab_devices(settings,['p1-host'])[0]
        assert device.site=='lab' and device.platform=='arista_eos' and m.restore_supported(device)
        return device
    device=scoped()
    print(json.dumps({'mode':'execute' if args.execute else 'plan','device':device.name,'site':device.site,
                      'test':'Ethernet8 description; restore full baseline via API'}),flush=True)
    if not args.execute:return
    exercise='q3-restore-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6]
    report={'exercise_id':exercise,'device':device.name,'device_id':device.id,'started_at':time.time(),'status':'running'}
    report_path=h.root_path()/'exercises'/(exercise+'.json')
    def save():h.private_write(report_path,json.dumps(report,indent=2))
    def api(path,payload=None,token=None):
        headers={'Content-Type':'application/json'}
        if token:headers['X-Config-Restore-Token']=token
        req=urllib.request.Request('http://127.0.0.1:8080/config'+path,
            data=None if payload is None else json.dumps(payload).encode(),headers=headers)
        with urllib.request.urlopen(req,timeout=210) as r:return json.load(r)
    def backup(phase):
        result=api('/backup',{'devices':[device.id],'actor':'lab-recovery-validation',
                             'exercise_id':exercise,'phase':phase})
        assert result['status']=='ok',result
        return result['devices'][0]
    def model(conn):return h.normalize_config(conn.send_command('show running-config',read_timeout=30),device.platform)
    def links(conn):
        data=json.loads(conn.send_command('show interfaces status | json',read_timeout=30))
        return sorted((name,item.get('linkStatus')) for name,item in data['interfaceStatuses'].items())
    with m.eos_connection(scoped(),settings) as conn:
        baseline=model(conn);before_links=links(conn)
    context='interface Ethernet8';assert context in baseline
    old_desc=next((r['command'] for r in baseline[context] if r['command'].startswith('description ')),None)
    initial=backup('baseline');report['baseline_version_id']=initial['version_id'];save()
    changed=False;execute_started=None;success=False
    try:
        with m.eos_connection(scoped(),settings) as conn:
            assert model(conn)==baseline,'Concurrent configuration change before test'
            changed=True
            conn.send_config_set([context,'description '+exercise],read_timeout=30,error_pattern=r'% (?:Invalid|Error|Incomplete|Ambiguous)')
            delta=h.differences(baseline,model(conn))
            assert len(delta)==1 and delta[0]['context']==context
        modified=backup('change');report['changed_version_id']=modified['version_id'];save()
        comparison=api('/compare?device_id='+device.id+'&before_at='+str(initial['last_observed_at'])+'&after_at='+str(modified['last_observed_at']))
        assert len(comparison['changes'])==1 and comparison['changes'][0]['context']==context
        report['comparison_verified']=True
        preview=api('/restore/preview',{'device':device.id,'version_id':initial['version_id'],
            'actor':'lab-recovery-validation','change_ref':exercise,'exercise_id':exercise})
        assert preview['execution_supported'] and len(preview['changes'])==1 and preview['changes'][0]['context']==context
        report['preview_id']=preview['id'];save()
        execute_started=time.time()
        result=api('/restore/execute',{'preview_id':preview['id'],'confirm_device':device.name},m.restore_token())
        report['execution']=result;save()
        assert result['status']=='restored' and result['verified']
        with m.eos_connection(scoped(),settings) as conn:
            assert model(conn)==baseline,'Full configuration did not return to original baseline'
            assert links(conn)==before_links,'Interface link states changed'
            sessions=conn.send_command('show configuration sessions',read_timeout=20)
        report.update(baseline_restored=True,interface_states_unchanged=True,sessions=sessions)
        records=api('/drift?origin=exercise&limit=1000')['records']
        ours=[r for r in records if r['exercise_id']==exercise]
        restored=[r for r in ours if r['after_id']==result['restored_version_id']]
        assert len(restored)==1 and not restored[0]['unowned']
        for row in ours:
            api('/drift/'+row['id']+'/resolve',{'actor':'lab-recovery-validation',
                'reason':'Authorized p1-host recovery test; original complete configuration and interface link states verified.'})
        report.update(status='passed',completed_at=time.time(),drift_ids=[r['id'] for r in ours]);save();success=True
        print(json.dumps({'status':'passed','report':str(report_path),'execution':result,
                          'baseline_restored':True,'interface_states_unchanged':True}),flush=True)
    except Exception as exc:
        report.update(status='failed',error=type(exc).__name__);save()
        raise
    finally:
        if changed and not success:
            # If the API failed during a timed commit, allow the device's protection to settle.
            if execute_started and report.get('preview_id'):
                audits=api('/audit?device_id='+device.id+'&limit=100')['records']
                attempt=next((r for r in audits if r['action']=='restore_execute'
                              and r['details'].get('preview_id')==report['preview_id']),None)
                if attempt and attempt['status']=='error' and attempt['details'].get('commit_attempted') is False:
                    execute_started=None
            if execute_started:
                remaining=execute_started+330-time.time()
                while remaining>0:
                    print('Recovery failed; waiting for EOS commit-confirm protection before cleanup',flush=True)
                    time.sleep(min(30,remaining));remaining=execute_started+330-time.time()
            with m.eos_connection(scoped(),settings) as conn:
                current=model(conn)
                if current!=baseline:
                    delta=h.differences(baseline,current)
                    assert len(delta)==1 and delta[0]['context']==context,'Unexpected configuration differences; manual inspection required'
                    assert any(r['command']=='description '+exercise for r in current[context]),'Description changed externally; stop cleanup'
                    conn.send_config_set([context,old_desc or 'no description'],read_timeout=30,
                        error_pattern=r'% (?:Invalid|Error|Incomplete|Ambiguous)')
                assert model(conn)==baseline,'Emergency description cleanup failed'
            report['fallback_baseline_restored']=True;report['fallback_backup']=backup('rollback');save()
            for row in api('/drift?origin=exercise&limit=1000')['records']:
                if row['exercise_id']==exercise:
                    api('/drift/'+row['id']+'/resolve',{'actor':'lab-recovery-validation',
                        'reason':'Recovery validation failed before completion; original baseline restored and verified by fallback cleanup.'})
            print('Original baseline restored after unsuccessful validation',flush=True)


if __name__=='__main__':main()
