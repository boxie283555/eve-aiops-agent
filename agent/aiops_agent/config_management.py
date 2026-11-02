"""Daily backup management, timestamp comparison and guarded lab EOS recovery."""
from __future__ import annotations

import base64
import hmac
import json
import math
import os
import re
import secrets
import time
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger
from . import config_history as h

DEFAULT_SCHEDULE = {'enabled': True, 'time': '02:00', 'timezone': 'Asia/Shanghai', 'devices': []}


def audit(action, actor, device_id=None, status='running', **details):
    key = uuid.uuid4().hex
    with h.connect() as db:
        db.execute('INSERT INTO config_audit(id,created_at,action,actor,device_id,status,details) VALUES (?,?,?,?,?,?,?)',
                   (key,time.time(),action,actor,device_id,status,json.dumps(details,ensure_ascii=False)))
    return key


def finish_audit(key, status, **details):
    with h.connect() as db:
        row=db.execute('SELECT details FROM config_audit WHERE id=?',(key,)).fetchone()
        payload=json.loads(row['details']);payload.update(details)
        if status!='running':payload['completed_at']=time.time()
        db.execute('UPDATE config_audit SET status=?,details=? WHERE id=?',
                   (status,json.dumps(payload,ensure_ascii=False),key))


def list_audit(device_id=None, limit=100, offset=0):
    with h.connect() as db:
        where=' WHERE device_id=?' if device_id else '';args=[device_id] if device_id else []
        total=db.execute('SELECT count(*) FROM config_audit'+where,args).fetchone()[0]
        rows=[dict(r) for r in db.execute('SELECT * FROM config_audit'+where+' ORDER BY seq DESC LIMIT ? OFFSET ?',args+[limit,offset])]
    for r in rows:r['details']=json.loads(r['details'])
    return {'total':total,'records':rows}


def get_schedule():
    with h.connect() as db:row=db.execute("SELECT value FROM config_settings WHERE key='daily_schedule'").fetchone()
    return json.loads(row['value']) if row else dict(DEFAULT_SCHEDULE)


def schedule_trigger(value):
    if not re.fullmatch(r'\d{2}:\d{2}',value['time']):raise ValueError('Expected HH:MM')
    hour,minute=map(int,value['time'].split(':'))
    if hour>23 or minute>59:raise ValueError('Invalid daily backup time')
    try:zone=ZoneInfo(value['timezone'])
    except (KeyError,ValueError):raise ValueError('Unknown IANA timezone') from None
    return CronTrigger(hour=hour,minute=minute,timezone=zone)


def schedule_info():
    value=get_schedule();trigger=schedule_trigger(value)
    upcoming=trigger.get_next_fire_time(None,datetime.now(ZoneInfo(value['timezone']))) if value['enabled'] else None
    return {**value,'scope':'lab','next_run_at':upcoming.isoformat() if upcoming else None}


def save_schedule(settings, value, actor):
    schedule_trigger(value)
    # An empty list explicitly means all CURRENT lab devices, not a stale inventory list.
    h.current_lab_devices(settings,value['devices'])
    key=audit('schedule',actor,settings=value)
    with h.connect() as db:
        db.execute("INSERT OR REPLACE INTO config_settings VALUES ('daily_schedule',?)",(json.dumps(value),))
    finish_audit(key,'ok')
    return schedule_info()


def apply_daily_schedule(scheduler, callback):
    value=get_schedule()
    if scheduler.get_job('config_backup'):scheduler.remove_job('config_backup')
    if value['enabled']:
        scheduler.add_job(callback,schedule_trigger(value),id='config_backup',replace_existing=True,
                          max_instances=1,coalesce=True,misfire_grace_time=3600)


def tracked_backup(settings, requested=None, exercise_id=None, phase='observation', actor='operator', source='manual'):
    key=audit('backup',actor,devices=requested or [],source=source,exercise_id=exercise_id)
    try:
        result=h.backup(settings,requested,exercise_id,phase,source)
        finish_audit(key,result['status'],result=result)
        return {**result,'audit_id':key}
    except Exception as exc:
        finish_audit(key,'error',error=type(exc).__name__)
        raise


def history(device_id, start=None, end=None, limit=100, offset=0):
    clauses=['device_id=?'];args=[device_id]
    for column,stamp in [('observed_at>=?',start),('observed_at<=?',end)]:
        if stamp is not None:
            if not math.isfinite(stamp):raise ValueError('Invalid timestamp')
            clauses.append(column);args.append(stamp)
    if start is not None and end is not None and start>end:raise ValueError('Start must be before end')
    where=' WHERE '+' AND '.join(clauses)
    with h.connect() as db:
        total=db.execute('SELECT count(*) FROM backup_observations'+where,args).fetchone()[0]
        records=[dict(r) for r in db.execute('SELECT * FROM backup_observations'+where+' ORDER BY observed_at DESC,id DESC LIMIT ? OFFSET ?',args+[limit,offset])]
    return {'total':total,'records':records}


def load_version(version_id):
    with h.connect() as db:row=db.execute('SELECT * FROM versions WHERE id=?',(version_id,)).fetchone()
    if not row:raise ValueError('Backup version not found')
    row=dict(row)
    model=(h.root_path()/row['model_file']).read_text()
    if h.digest(model)!=row['fingerprint']:raise ValueError('Backup model integrity check failed')
    return row,json.loads(model)


def version_metadata(row):
    return {key:row[key] for key in ['id','device_id','device','platform','created_at','fingerprint']}


def compare(device_id, before_at, after_at):
    if not all(math.isfinite(x) for x in [before_at,after_at]) or before_at>after_at:
        raise ValueError('Expected two finite timestamps in chronological order')
    samples=[]
    with h.connect() as db:
        for stamp in [before_at,after_at]:
            row=db.execute('''SELECT * FROM backup_observations WHERE device_id=? AND observed_at<=?
                AND version_id IS NOT NULL AND status IN ('saved','unchanged') ORDER BY observed_at DESC,id DESC LIMIT 1''',
                (device_id,stamp)).fetchone()
            if not row:raise ValueError('No successful backup at or before the selected time')
            samples.append(dict(row))
    a,b=[load_version(s['version_id']) for s in samples]
    if (a[0]['host'],a[0]['platform'])!=(b[0]['host'],b[0]['platform']):
        raise ValueError('Management address or platform changed; versions are different baselines')
    return {'device_id':device_id,'before':samples[0],'after':samples[1],
            'requested_before_at':before_at,'requested_after_at':after_at,'changes':h.differences(a[1],b[1])}


def restore_supported(device):
    # Initial execution rollout is deliberately limited to the user's selected lab pilot.
    allowed={v.strip() for v in os.getenv('AIOPS_CONFIG_RESTORE_DEVICES','p1-host').split(',') if v.strip()}
    return device.site=='lab' and device.platform=='arista_eos' and (device.id in allowed or device.name in allowed)


def restore_token():
    path=h.root_path()/'restore-token'
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    try:
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    except FileExistsError:pass
    else:
        with os.fdopen(fd,'w') as f:f.write(secrets.token_urlsafe(32))
    return path.read_text().strip()


def authorize_restore(token):
    expected=restore_token()
    return bool(token and expected) and hmac.compare_digest(token,expected)


def checked_target(device, version_id):
    row,model=load_version(version_id)
    if (row['device_id'],row['host'],row['platform'])!=(device.id,device.host,device.platform):
        raise ValueError('Backup does not match current device identity/address/platform')
    raw=(h.root_path()/row['raw_file']).read_text()
    if h.digest(raw)!=row['raw_hash'] or h.normalize_config(raw,device.platform)!=model:
        raise ValueError('Raw backup integrity check failed')
    return row,model,raw


def preview_restore(settings, device_id, version_id, actor, change_ref, exercise_id=None):
    key=audit('restore_preview',actor,device_id,version_id=version_id,change_ref=change_ref)
    if not h.BACKUP_LOCK.acquire(blocking=False):
        finish_audit(key,'busy');raise ValueError('Configuration operation already running')
    try:
        device=h.current_lab_devices(settings,[device_id])[0]
        target,model,_=checked_target(device,version_id)
        current=h.fetch_config(device,settings)
        result=h.record_backup(device,current,exercise_id,source='restore_preview')
        current_model=h.normalize_config(current,device.platform)
        changes=h.differences(current_model,model)
        plan_id=uuid.uuid4().hex;stamp=time.time()
        payload={'device':device.name,'target':version_metadata(target),'changes':changes,
                 'execution_supported':restore_supported(device),'current_version_id':result['version_id'],
                 'exercise_id':exercise_id,'scope':'running-config','timer_seconds':300}
        fingerprint=h.digest(json.dumps(current_model,sort_keys=True,ensure_ascii=False))
        with h.connect() as db:
            db.execute('INSERT INTO restore_previews VALUES (?,?,?,?,?,?,?,?,?,?)',
                       (plan_id,stamp,stamp+600,device.id,version_id,fingerprint,actor,change_ref,'pending',json.dumps(payload)))
        h.detect_drift()
        finish_audit(key,'ok',preview_id=plan_id,changed_contexts=[r['context'] for r in changes])
        return {**payload,'id':plan_id,'expires_at':stamp+600,'actor':actor,'change_ref':change_ref}
    except Exception as exc:
        finish_audit(key,'error',error=type(exc).__name__);raise
    finally:h.BACKUP_LOCK.release()


def eos_connection(device, settings):
    from .collector import Collector
    from netmiko import ConnectHandler
    user,password,secret=Collector(settings,[])._device_credentials(device)
    return ConnectHandler(device_type=device.platform,host=device.host,port=device.port,username=user,
                          password=password,secret=secret or '',conn_timeout=15,auth_timeout=15,banner_timeout=15)


def eos_command(conn, command, timeout=60):
    # EOS redraws long bash command lines; matching their complete echo can time out.
    output=conn.send_command(command,read_timeout=timeout,expect_string=r'[#>]\s*$',
                             cmd_verify=not command.startswith('bash '))
    if re.search(r'%\s*(?:Invalid|Error|Incomplete|Ambiguous|Failed)|Error:|Permission denied|No such file',output,re.I):
        raise ValueError('EOS rejected a recovery command')
    return output


def apply_eos_restore(settings, device, raw, target_model, baseline_fingerprint, plan_id, progress=None):
    """Stage a complete candidate and use EOS commit-confirm to protect management access."""
    session='aiops-'+plan_id[:16];path='/mnt/flash/'+session+'.cfg'
    timed=False;confirmed=False
    def stage(name, commit_attempted=False):
        if progress:progress(name,commit_attempted)
    stage('connect')
    with eos_connection(device,settings) as conn:
        try:
            stage('transfer')
            eos_command(conn,'bash umask 077; : > '+path)
            encoded=base64.b64encode(raw.encode()).decode()
            for pos in range(0,len(encoded),512):
                eos_command(conn,"bash printf %s '"+encoded[pos:pos+512]+"' | base64 -d >> "+path)
            transferred=eos_command(conn,'bash sha256sum '+path)
            if not transferred.split() or transferred.split()[0]!=h.digest(raw):
                raise ValueError('Transferred configuration integrity check failed')
            stage('candidate')
            eos_command(conn,'configure session '+session)
            eos_command(conn,'rollback flash:'+session+'.cfg')
            candidate=eos_command(conn,'show session-config')
            if h.normalize_config(candidate,device.platform)!=target_model:raise ValueError('Candidate differs from target backup')
            running=eos_command(conn,'show running-config')
            if h.digest(json.dumps(h.normalize_config(running,device.platform),sort_keys=True,ensure_ascii=False))!=baseline_fingerprint:
                raise ValueError('Running configuration changed while staging recovery')
            stage('timed_commit',True)
            timed=True  # A timeout here can still mean the device accepted the commit.
            eos_command(conn,'commit timer 00:05:00')
            # A fresh session proves management access survived; recheck live Nautobot scope first.
            fresh=h.current_lab_devices(settings,[device.id])[0]
            if fresh.host!=device.host or fresh.name!=device.name:raise ValueError('Device identity changed during recovery')
            stage('verify_new_connection',True)
            with eos_connection(fresh,settings) as verify:
                actual=eos_command(verify,'show running-config')
                if h.normalize_config(actual,device.platform)!=target_model:raise ValueError('Restored configuration did not verify')
                stage('confirm',True)
                sessions=eos_command(verify,'show configuration sessions')
                if 'Session with pending commit timer: '+session not in sessions:
                    raise ValueError('Recovery session no longer owns the pending commit timer')
                eos_command(verify,'configure session pending-timer commit');confirmed=True
                if 'Session with pending commit timer:' in eos_command(verify,'show configuration sessions'):
                    raise ValueError('Recovery commit timer remains pending')
                final=eos_command(verify,'show running-config')
                if h.normalize_config(final,device.platform)!=target_model:raise ValueError('Post-confirm configuration did not verify')
                return final
        finally:
            # Before commit, abort only our candidate. After a failed timed commit EOS reverts itself.
            if not timed:
                try:eos_command(conn,'abort')
                except Exception:pass
            try:eos_command(conn,'bash rm -- '+path)
            except Exception:pass
            if timed and not confirmed:
                # Caller audits this as uncertain; never report successful rollback without observing it.
                pass


def execute_restore(settings, preview_id, confirm_device):
    if not h.BACKUP_LOCK.acquire(blocking=False):raise ValueError('Configuration operation already running')
    key=None;claimed=False;applied=False;recovery_stage=None
    def progress(stage, commit_attempted):
        nonlocal applied,recovery_stage
        applied=commit_attempted;recovery_stage=stage
        finish_audit(key,'running',recovery_stage=stage,commit_attempted=commit_attempted)
    try:
        with h.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM restore_previews WHERE id=?',(preview_id,)).fetchone()
            if not row or row['status']!='pending' or row['expires_at']<time.time():
                raise ValueError('Preview expired, consumed or unavailable; create a new preview')
            plan=dict(row);payload=json.loads(plan['details'])
            if confirm_device!=payload['device']:raise ValueError('Type the exact device name to confirm')
            db.execute("UPDATE restore_previews SET status='running' WHERE id=?",(preview_id,));claimed=True
        key=audit('restore_execute',plan['actor'],plan['device_id'],preview_id=preview_id,
                  version_id=plan['version_id'],change_ref=plan['change_ref'])
        device=h.current_lab_devices(settings,[plan['device_id']])[0]
        if not restore_supported(device):raise ValueError('Execution is enabled only for the lab EOS pilot device')
        target,target_model,raw=checked_target(device,plan['version_id'])
        current=h.fetch_config(device,settings);model=h.normalize_config(current,device.platform)
        fingerprint=h.digest(json.dumps(model,sort_keys=True,ensure_ascii=False))
        if fingerprint!=plan['baseline_fingerprint']:raise ValueError('Current configuration changed since preview; preview again')
        contexts=[d['context'] for d in h.differences(model,target_model)]
        if contexts:
            now=time.time()
            h.register_change('restore-'+preview_id,device.id,plan['actor'],now-1,now+600,contexts)
        safety=h.record_backup(device,current,payload.get('exercise_id'),source='pre_restore')
        finish_audit(key,'running',safety_version_id=safety['version_id'])
        if contexts:
            final=apply_eos_restore(settings,device,raw,target_model,fingerprint,preview_id,progress)
        else:final=current
        saved=h.record_backup(device,final,payload.get('exercise_id'),source='post_restore')
        close_restore_window(preview_id,saved['last_observed_at'])
        detection=h.detect_drift()
        result={'status':'restored' if contexts else 'unchanged','device':device.name,
                'target_version_id':target['id'],'safety_version_id':safety['version_id'],
                'restored_version_id':saved['version_id'],'verified':True,'scope':'running-config',
                'detection':detection,'audit_id':key}
        finish_audit(key,'ok',result=result)
        with h.connect() as db:db.execute("UPDATE restore_previews SET status='completed' WHERE id=?",(preview_id,))
        return result
    except Exception as exc:
        if key:finish_audit(key,'verification_required' if applied else 'error',error=type(exc).__name__,
                           recovery_stage=recovery_stage,
                           recovery_note='Inspect live configuration and EOS commit timer before retry' if applied else 'No recovery applied')
        if claimed:
            with h.connect() as db:db.execute("UPDATE restore_previews SET status='failed' WHERE id=?",(preview_id,))
        raise
    finally:
        try:
            if claimed:close_restore_window(preview_id,time.time())
        finally:h.BACKUP_LOCK.release()


def close_restore_window(preview_id, stamp):
    # An internal recovery reference must not own subsequent, unrelated edits.
    with h.connect() as db:
        db.execute('UPDATE changes SET ends_at=MIN(ends_at,?) WHERE id=?',(stamp,'restore-'+preview_id))
