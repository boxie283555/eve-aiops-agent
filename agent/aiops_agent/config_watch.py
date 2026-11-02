"""Independent, bounded lab configuration polling; no dependency on metric collection."""
from __future__ import annotations

import json
import os
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor

from . import config_history as h
from .config_management import audit


def interval_seconds():
    return max(30, int(os.getenv('AIOPS_CONFIG_WATCH_INTERVAL', '60')))


def enabled():
    return os.getenv('AIOPS_CONFIG_WATCH_ENABLED', 'true').lower() in {'1','true','yes'}


def save_run(**value):
    with h.connect() as db:
        db.execute('INSERT OR REPLACE INTO config_settings VALUES (?,?)',
                   ('drift_watch_run', json.dumps(value)))


def run_once(settings):
    if not enabled():return {'status':'disabled'}
    started=time.time()
    if not h.BACKUP_LOCK.acquire(blocking=False):
        save_run(status='busy',started_at=started,completed_at=time.time())
        return {'status':'busy'}
    try:
        save_run(status='running',started_at=started)
        devices=h.current_lab_devices(settings)
        def check(device):
            attempted=time.time()
            try:
                raw=h.fetch_config(device,settings)
                result=h.record_backup(device,raw,source='drift_watch')
                detection=h.detect_drift(device.id)
                with h.connect() as db:
                    db.execute('INSERT OR REPLACE INTO config_watch_devices VALUES (?,?,?,?,?,?,?)',
                               (device.id,device.name,attempted,result['last_observed_at'],result['version_id'],'ok',None))
                if result['status']=='saved' or detection['created']:
                    audit('drift_watch','scheduler',device.id,status='ok',version_id=result['version_id'],
                          created=detection['created'],drift_ids=detection['drift_ids'])
                return {'device_id':device.id,'status':'ok','created':detection['created']}
            except Exception as exc:
                with h.connect() as db:
                    db.execute('''INSERT INTO config_watch_devices(device_id,device,last_attempt_at,status,error)
                        VALUES (?,?,?,?,?) ON CONFLICT(device_id) DO UPDATE SET
                        device=excluded.device,last_attempt_at=excluded.last_attempt_at,status=excluded.status,error=excluded.error''',
                               (device.id,device.name,attempted,'error',h.backup_failure_reason(type(exc).__name__)))
                return {'device_id':device.id,'status':'error','created':0}
        with ThreadPoolExecutor(max_workers=2,thread_name_prefix='config-watch') as pool:
            results=list(pool.map(check,devices))
        status='ok' if all(r['status']=='ok' for r in results) else 'partial'
        result=dict(status=status,started_at=started,completed_at=time.time(),
                    checked=len(results),failed=sum(r['status']=='error' for r in results),
                    created=sum(r['created'] for r in results))
        save_run(**result)
        return result
    except Exception as exc:
        result=dict(status='error',started_at=started,completed_at=time.time(),error=h.backup_failure_reason(type(exc).__name__))
        save_run(**result)
        return result
    finally:
        h.BACKUP_LOCK.release()


def live_status(records, now=None):
    """Read durable drift counts directly, without Prometheus/cache or device I/O.

    Inventory records come from the already loaded collector, not a new discovery.
    Missing/failed watcher evidence is shown separately from the durable count.
    """
    now=time.time() if now is None else now
    interval=interval_seconds()
    base=dict(available=False,enabled=enabled(),interval_seconds=interval,scope='lab',sampled_at=now,
              total=None,devices=[],watch_devices=[],run={})
    try:
        with sqlite3.connect((h.root_path()/'history.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=1) as db:
            db.row_factory=sqlite3.Row
            counts={r['device_id']:dict(r) for r in db.execute('''SELECT device_id,count(*) AS count,MAX(detected_at) AS detected_at
                FROM drifts WHERE origin='observed' AND status='open' AND unowned=1 GROUP BY device_id''')}
            watched={r['device_id']:dict(r) for r in db.execute('SELECT * FROM config_watch_devices')}
            run=db.execute("SELECT value FROM config_settings WHERE key='drift_watch_run'").fetchone()
        rows=[];watch_rows=[]
        for record in records:
            key=record['id'];count=counts.get(key,{})
            rows.append(dict(id=key,device=record['name'],count=count.get('count',0),detected_at=count.get('detected_at')))
            if record.get('site')!='lab':continue
            row=watched.get(key,{})
            stamp=row.get('last_success_at')
            state=('disabled' if not enabled() else 'error' if row.get('status')=='error' else
                   'ok' if stamp is not None and 0<=now-stamp<=max(180,interval*3) else 'stale')
            watch_rows.append(dict(id=key,device=record['name'],state=state,last_success_at=stamp,
                                   last_attempt_at=row.get('last_attempt_at'),reason=row.get('error')))
        return {**base,'available':True,'total':sum(r['count'] for r in rows),'devices':rows,
                'watch_devices':watch_rows,'run':json.loads(run['value']) if run else {}}
    except (OSError,ValueError,sqlite3.Error):
        return base
