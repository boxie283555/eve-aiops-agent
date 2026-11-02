"""Opt-in lab PA execution. An offline preview never invokes this module's writes."""
from __future__ import annotations

import hmac
import json
import os
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from . import firewall_policy as p, config_history as h, config_management as m
from .panos_evidence import PanosAPI, refresh_policy

SLOT=threading.BoundedSemaphore(1)


def enabled(device_id):
    return device_id in {x.strip() for x in os.getenv('AIOPS_FIREWALL_WRITE_DEVICES','').split(',') if x.strip()} and bool(os.getenv('AIOPS_FIREWALL_OPERATOR_TOKEN'))


def authorized(token):
    expected=os.getenv('AIOPS_FIREWALL_OPERATOR_TOKEN','')
    return bool(expected and token and hmac.compare_digest(expected,token))


def config(api,candidate=False):
    response=api.call(type='op',cmd='<show><config><candidate/></config></show>') if candidate else api.call(type='config',action='show',xpath='/config')
    root=response.find('./result/config')
    if root is None:raise ValueError('PAN configuration unavailable')
    return ET.tostring(root,encoding='unicode')


def fingerprint(raw):
    return h.digest(json.dumps(h.normalize_config(raw,'paloalto_panos'),sort_keys=True,ensure_ascii=False))


def lock_command(kind,action,comment=''):
    root=ET.Element('request');child=ET.SubElement(ET.SubElement(root,kind),action)
    if comment:ET.SubElement(child,'comment').text=comment
    return ET.tostring(root,encoding='unicode')


def literal(value):
    if "'" not in value:return "'"+value+"'"
    if '"' not in value:return '"'+value+'"'
    raise ValueError('Rule/context name requires unsupported XPath quoting')


def initialize():
    with h.connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS firewall_executions (id TEXT PRIMARY KEY, review_id TEXT UNIQUE, device_id TEXT, created_at REAL, status TEXT, payload TEXT)')


def state(job_id):
    initialize()
    with h.connect() as db:row=db.execute('SELECT payload,status FROM firewall_executions WHERE id=?',(job_id,)).fetchone()
    if not row:raise ValueError('Execution not found')
    return dict(json.loads(row['payload']),status=row['status'])


def update(job_id,status,**values):
    with h.connect() as db:
        row=db.execute('SELECT payload FROM firewall_executions WHERE id=?',(job_id,)).fetchone()
        payload=json.loads(row['payload']);payload.update(values,updated_at=time.time())
        db.execute('UPDATE firewall_executions SET status=?,payload=? WHERE id=?',(status,json.dumps(payload),job_id))


def recover_interrupted():
    initialize()
    with h.connect() as db:
        for row in db.execute("SELECT id,payload FROM firewall_executions WHERE status IN ('queued','running')").fetchall():
            payload=json.loads(row['payload']);payload.update(reason='Agent restarted; inspect candidate, commit job and device locks before any further execution',updated_at=time.time())
            db.execute("UPDATE firewall_executions SET status='inspection_required',payload=? WHERE id=?",(json.dumps(payload),row['id']))


def start(settings,review_id,confirm_device,token):
    if not authorized(token):raise PermissionError('Firewall operator token required')
    review=p.get_review(settings,review_id);d=p.target(settings,review['device_id'])
    if not enabled(d.id):raise PermissionError('Device is not enabled for firewall writes')
    if d.name!=confirm_device:raise ValueError('Exact device name confirmation required')
    if review['expires_at']<time.time():raise ValueError('Review expired; generate a new preview')
    if not SLOT.acquire(blocking=False):raise ValueError('Another firewall execution is running')
    try:
        initialize();job_id=uuid.uuid4().hex
        value={'id':job_id,'review_id':review_id,'device_id':d.id,'device':d.name,'actor':review['actor'],
               'created_at':time.time(),'status':'queued','operation':review['operation']}
        with h.connect() as db:
            # Pending/uncertain executions block further writes, even after a restart.
            if db.execute("SELECT 1 FROM firewall_executions WHERE device_id=? AND status IN ('queued','running','inspection_required')",(d.id,)).fetchone():
                raise ValueError('Previous execution requires device inspection')
            if db.execute('SELECT 1 FROM firewall_executions WHERE review_id=?',(review_id,)).fetchone():raise ValueError('Review already consumed')
            db.execute('INSERT INTO firewall_executions VALUES (?,?,?,?,?,?)',(job_id,review_id,d.id,time.time(),'queued',json.dumps(value)))
        threading.Thread(target=execute,args=(settings,review,job_id),daemon=True).start()
        return value
    except Exception:
        SLOT.release();raise


def execute(settings,review,job_id):
    api=None;locks=[];write_attempted=False;job=None;backup_locked=False;audit_id=None;change_id='firewall-'+job_id
    try:
        backup_locked=h.BACKUP_LOCK.acquire(blocking=False)
        if not backup_locked:raise ValueError('Configuration backup/recovery is running')
        d=p.target(settings,review['device_id'])
        if not enabled(d.id):raise ValueError('Firewall execution disabled')
        d,version,raw=p.evidence(settings,d.id,review['version']['id'])
        if review['expires_at']<time.time():raise ValueError('Review expired')
        modified=p.modify(raw,review['context'],review['kind'],review['rule_name'],review['operation'],review['values'])
        if 'candidate-'+h.digest(modified)[:24]!=review['candidate_id']:raise ValueError('Candidate review fingerprint mismatch')
        api=PanosAPI(d,settings)
        audit_id=m.audit('firewall_execute',review['actor'],d.id,review_id=review['id'],execution_id=job_id)
        update(job_id,'running',phase='validating')
        # Device locks are mandatory; never force, steal or remove another operator's lock.
        for kind in ['config-lock','commit-lock']:
            existing=api.call(type='op',cmd='<show><'+kind+'s/></show>')
            if existing.find('./result') is None or existing.findall('.//entry'):
                raise ValueError('Existing or unreadable device locks; execution refused')
            api.call(type='op',cmd=lock_command(kind,'add',change_id));locks.append(kind)
        running=config(api);candidate=config(api,True)
        pending=api.call(type='op',cmd='<check><pending-changes/></check>').findtext('./result')
        if pending is None or pending.strip()!='no' or fingerprint(running)!=fingerprint(candidate):
            raise ValueError('Device has pending changes; full commit refused')
        if fingerprint(running)!=fingerprint(raw):raise ValueError('Running configuration changed since the reviewed backup')
        changes=h.differences(h.normalize_config(running,d.platform),h.normalize_config(modified,d.platform))
        contexts=[c['context'] for c in changes]
        if not contexts:raise ValueError('No configuration change')
        stamp=time.time();h.register_change(change_id,d.id,review['actor'],stamp-1,stamp+600,contexts)
        baseline=h.record_backup(d,running,source='firewall_prechange')
        update(job_id,'running',phase='saving_candidate',change_ref=change_id,baseline_version=baseline['version_id'])
        roots=ET.fromstring(modified).findall('./devices/entry')
        dev=next((x for x in roots if any(v.get('name')==review['context'] for v in x.findall('./vsys/entry'))),None)
        if dev is None:raise ValueError('Missing device context')
        base='/config/devices/entry[@name='+literal(dev.get('name'))+']/vsys/entry[@name='+literal(review['context'])+']/rulebase/'+review['kind']+'/rules'
        xpath=base+'/entry[@name='+literal(review['rule_name'])+']'
        params={'type':'config','xpath':xpath}
        if review['operation'].startswith('move_'):
            params.update(action='move',where=review['operation'].split('_')[1],dst=review['values'][0])
        else:
            rules=p.find_rules(ET.fromstring(modified),review['context'],review['kind'])
            entry=next(x for x in rules if x.get('name')==review['rule_name'])
            params.update(action='edit',element=ET.tostring(entry,encoding='unicode'))
        write_attempted=True
        api.call(**params)
        # The full candidate must equal the reviewed result. Never commit unrelated edits.
        if fingerprint(config(api,True))!=fingerprint(modified) or fingerprint(config(api))!=fingerprint(running):
            raise ValueError('Candidate/running changed unexpectedly; inspect device before commit')
        update(job_id,'running',phase='committing')
        response=api.call(type='commit',cmd='<commit></commit>');job=response.findtext('./result/job')
        if not job or not job.isdigit():raise ValueError('Commit response has no job ID; inspect device')
        update(job_id,'running',phase='waiting_for_commit',commit_job=job)
        deadline=time.monotonic()+120
        while time.monotonic()<deadline:
            response=api.call(type='op',cmd='<show><jobs><id>'+job+'</id></jobs></show>')
            row=response.find('./result/job')
            if row is not None and row.findtext('status')=='FIN':
                if row.findtext('result')!='OK':raise ValueError('PAN commit did not finish successfully')
                break
            time.sleep(2)
        else:raise ValueError('Commit still pending; inspect the reported job')
        observed=config(api)
        if fingerprint(observed)!=fingerprint(modified):raise ValueError('Committed configuration does not match reviewed target')
        saved=h.record_backup(d,observed,source='firewall_postchange');h.detect_drift()
        update(job_id,'verified_configuration',phase='complete',after_version=saved['version_id'],
               traffic_verified=False,reason='FIN/OK and running configuration verified; business traffic still requires verification')
        m.finish_audit(audit_id,'ok',change_ref=change_id,commit_job=job,after_version=saved['version_id'],traffic_verified=False)
    except Exception as exc:
        status='inspection_required' if write_attempted else 'failed_before_write'
        reason=str(exc) if isinstance(exc,ValueError) else type(exc).__name__
        update(job_id,status,reason=reason,commit_job=job)
        if audit_id:m.finish_audit(audit_id,status,error=reason,commit_job=job)
    finally:
        if api:
            for kind in reversed(locks):
                try:api.call(type='op',cmd=lock_command(kind,'remove'))
                except Exception:
                    update(job_id,'inspection_required',reason='Device lock release failed; inspect lock ownership',commit_job=job)
        try:
            with h.connect() as db:db.execute('UPDATE changes SET ends_at=MIN(ends_at,?) WHERE id=?',(time.time(),change_id))
        finally:
            if backup_locked:h.BACKUP_LOCK.release()
            SLOT.release()
    # Refresh policy evidence separately from the locked execution/backup section.
    if state(job_id)['status']=='verified_configuration':
        try:refresh_policy(settings)
        except Exception:pass
