"""Persistent configuration versions and auditable, redacted structured drift."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from collections import defaultdict
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from prometheus_client.core import GaugeMetricFamily

BACKUP_LOCK = threading.Lock()
SECRET = re.compile(r'password|secret|community|private.key|api.key|phash|encrypted|authentication.key|token', re.I)


def root_path():
    return Path(os.getenv('AIOPS_CONFIG_HISTORY_PATH', '/app/data/config_history'))


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def private_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    with os.fdopen(os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as file:
        file.write(text)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temp, path)


@contextmanager
def connect():
    root = root_path()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root / 'history.sqlite3'
    if not path.exists():
        path.touch(mode=0o600, exist_ok=True)
    db = sqlite3.connect(path, timeout=15)
    db.row_factory = sqlite3.Row
    db.executescript('''
      CREATE TABLE IF NOT EXISTS versions (
        seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE, device_id TEXT, device TEXT,
        host TEXT, platform TEXT, created_at REAL, fingerprint TEXT, raw_hash TEXT,
        raw_file TEXT, model_file TEXT, exercise_id TEXT, phase TEXT, checked INTEGER DEFAULT 0);
      CREATE INDEX IF NOT EXISTS versions_device ON versions(device_id,seq);
      CREATE TABLE IF NOT EXISTS version_observations (
        version_id TEXT PRIMARY KEY, last_observed_at REAL NOT NULL,
        baseline_observed_at REAL);
      CREATE TABLE IF NOT EXISTS drifts (
        id TEXT PRIMARY KEY, before_id TEXT, after_id TEXT UNIQUE, device_id TEXT, device TEXT,
        detected_at REAL, origin TEXT, exercise_id TEXT, phase TEXT,
        unowned INTEGER, change_ref TEXT, status TEXT, changes TEXT,
        resolved_at REAL, resolved_by TEXT, resolution TEXT);
      CREATE TABLE IF NOT EXISTS attempts (
        device_id TEXT PRIMARY KEY, device TEXT, attempted_at REAL, success INTEGER, error TEXT);
      CREATE TABLE IF NOT EXISTS changes (
        id TEXT PRIMARY KEY, device_id TEXT, owner TEXT, starts_at REAL, ends_at REAL,
        contexts TEXT, created_at REAL);
      CREATE TABLE IF NOT EXISTS backup_observations (
        id TEXT PRIMARY KEY, device_id TEXT, device TEXT, version_id TEXT,
        observed_at REAL, status TEXT, source TEXT, error TEXT);
      CREATE INDEX IF NOT EXISTS observations_device_time ON backup_observations(device_id,observed_at);
      CREATE TABLE IF NOT EXISTS config_settings (key TEXT PRIMARY KEY, value TEXT);
      CREATE TABLE IF NOT EXISTS config_audit (
        seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE, created_at REAL,
        action TEXT, actor TEXT, device_id TEXT, status TEXT, details TEXT);
      CREATE TABLE IF NOT EXISTS restore_previews (
        id TEXT PRIMARY KEY, created_at REAL, expires_at REAL, device_id TEXT,
        version_id TEXT, baseline_fingerprint TEXT, actor TEXT, change_ref TEXT,
        status TEXT, details TEXT);
    ''')
    try:
        if not db.execute("SELECT 1 FROM config_settings WHERE key='observations_migrated'").fetchone():
            with db:
                db.execute('BEGIN IMMEDIATE')
                if not db.execute("SELECT 1 FROM config_settings WHERE key='observations_migrated'").fetchone():
                    # Only reconstruct times for which durable evidence already exists.
                    db.execute('''INSERT OR IGNORE INTO backup_observations
                        SELECT 'legacy-first-'||id,device_id,device,id,created_at,'saved','legacy',NULL FROM versions''')
                    db.execute('''INSERT OR IGNORE INTO backup_observations
                        SELECT 'legacy-last-'||v.id,device_id,device,v.id,o.last_observed_at,'unchanged','legacy',NULL
                        FROM versions v JOIN version_observations o ON o.version_id=v.id
                        WHERE o.last_observed_at>v.created_at''')
                    db.execute("INSERT INTO config_settings VALUES ('observations_migrated','true')")
        with db:
            yield db
    finally:
        db.close()


def sensitive(value):
    return '<sensitive:' + digest(value) + '>' if SECRET.search(value) else value


def normalize_config(raw, platform):
    """Keep rule order and hierarchy; ignore CLI display headers, never config statements."""
    if len(raw.encode()) > 16*1024*1024:
        raise ValueError("Configuration exceeds bounded size")
    groups = defaultdict(list)
    if platform == 'paloalto_panos':
        root = ET.fromstring(raw)
        if root.tag != 'config':
            raise ValueError('Expected running config XML')
        def walk(node, parents):
            tag = node.tag + ('[' + node.get('name') + ']' if node.get('name') else '')
            path = parents + [tag]
            if len(node):
                # Child position detects policy/ACL reordering, including identical values.
                for i, child in enumerate(node):
                    walk(child, path + [str(i)])
            else:
                context = '/'.join(path[:-1])
                value = (node.text or '').strip()
                groups[context].append(tag + '=' + (sensitive(tag+' '+value) if SECRET.search(tag) else value))
        walk(root, [])
    elif platform in {'arista_eos', 'cisco_nxos'}:
        if not re.search(r'^hostname\s+\S+', raw, re.M) or re.search(r'^\s*%\s*(Invalid|Error|Incomplete)', raw, re.M|re.I):
            raise ValueError('Invalid or incomplete running configuration')
        if platform == 'arista_eos' and not re.search(r'^end\s*$',raw,re.M):
            raise ValueError('Truncated EOS running configuration')
        stack = []
        for line in raw.splitlines():
            value = line.strip()
            if not value or value.startswith('!') or value == 'end' or value.startswith(('Building configuration', 'Current configuration', 'Time:')):
                continue
            indent = len(line)-len(line.lstrip())
            while stack and stack[-1][0] >= indent:
                stack.pop()
            safe = sensitive(value)
            context = stack[0][1] if stack else safe
            parents = [v for _,v in stack]
            groups[context].append({'path': parents, 'command': safe})
            stack.append((indent,safe))
    else:
        raise ValueError('Unsupported configuration platform')
    if not groups:
        raise ValueError('Empty normalized configuration')
    return dict(groups)


def category(context):
    lower = context.lower()
    if 'access-list' in lower or '/security/' in lower or '/nat/' in lower:
        return 'acl_policy'
    if 'bgp' in lower:
        return 'bgp'
    if 'interface' in lower:
        return 'interface'
    return 'other'


def differences(before, after):
    changes = []
    for key in sorted(set(before) | set(after)):
        if before.get(key) == after.get(key):
            continue
        # Fingerprints preserve secret-change detection, but never leave the private model.
        public = lambda value: json.loads(re.sub(r'<sensitive:[a-f0-9]{64}>', '<redacted>', json.dumps(value)))
        changes.append({'context': public(key), 'category': category(key),
                        'operation': 'added' if key not in before else 'removed' if key not in after else 'modified',
                        'before': public(before.get(key)), 'after': public(after.get(key))})
    return changes


def record_backup(device, raw, exercise_id=None, phase='observation', source='manual'):
    model = normalize_config(raw, device.platform)
    serialized = json.dumps(model, sort_keys=True, ensure_ascii=False)
    fingerprint = digest(serialized)
    with connect() as db:
        # Serialize the version and its observation boundary with other writers/detection.
        db.execute('BEGIN IMMEDIATE')
        stamp = time.time()
        previous = db.execute('''SELECT v.*, o.last_observed_at FROM versions v
            LEFT JOIN version_observations o ON o.version_id=v.id
            WHERE device_id=? ORDER BY seq DESC LIMIT 1''',(device.id,)).fetchone()
        db.execute('INSERT OR REPLACE INTO attempts VALUES (?,?,?,?,?)',(device.id,device.name,stamp,1,None))
        if previous and previous['fingerprint'] == fingerprint and previous['host'] == device.host and previous['platform'] == device.platform:
            db.execute('''INSERT INTO version_observations(version_id,last_observed_at) VALUES (?,?)
                ON CONFLICT(version_id) DO UPDATE SET last_observed_at=excluded.last_observed_at''',
                (previous['id'],stamp))
            db.execute('INSERT INTO backup_observations VALUES (?,?,?,?,?,?,?,?)',
                       (uuid.uuid4().hex,device.id,device.name,previous['id'],stamp,'unchanged',source,None))
            return {'device':device.name,'status':'unchanged','version_id':previous['id'],'last_observed_at':stamp}
        version = uuid.uuid4().hex
        folder = Path(str(uuid.UUID(device.id)))
        raw_file = str(folder/(version+('.xml' if device.platform=='paloalto_panos' else '.txt')))
        model_file = str(folder/(version+'.json'))
        private_write(root_path()/raw_file, raw)
        private_write(root_path()/model_file, serialized)
        db.execute('''INSERT INTO versions(id,device_id,device,host,platform,created_at,fingerprint,raw_hash,raw_file,model_file,exercise_id,phase)
                      VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                   (version,device.id,device.name,device.host,device.platform,stamp,fingerprint,digest(raw),raw_file,model_file,exercise_id,phase))
        # Freeze the BEFORE boundary now; later observations must not reattribute this transition.
        baseline = (previous['last_observed_at'] if previous['last_observed_at'] is not None
                    else previous['created_at']) if previous else None
        db.execute('INSERT INTO version_observations VALUES (?,?,?)',(version,stamp,baseline))
        db.execute('INSERT INTO backup_observations VALUES (?,?,?,?,?,?,?,?)',
                   (uuid.uuid4().hex,device.id,device.name,version,stamp,'saved',source,None))
        return {'device':device.name,'status':'saved','version_id':version,'raw_file':raw_file,
                'fingerprint':fingerprint,'last_observed_at':stamp}


def detect_drift():
    created = []
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        for current in db.execute('''SELECT v.*, o.baseline_observed_at FROM versions v
                LEFT JOIN version_observations o ON o.version_id=v.id
                WHERE checked=0 ORDER BY seq''').fetchall():
            previous = db.execute('SELECT * FROM versions WHERE device_id=? AND seq<? ORDER BY seq DESC LIMIT 1',
                                  (current['device_id'],current['seq'])).fetchone()
            if previous and previous['host']==current['host'] and previous['platform']==current['platform']:
                models=[]
                for revision in (previous,current):
                    content=(root_path()/revision['model_file']).read_text()
                    if digest(content)!=revision['fingerprint']:
                        raise ValueError('Configuration model integrity check failed')
                    models.append(json.loads(content))
                before,after=models
                changes=differences(before,after)
                if changes:
                    # Legacy versions have no observation evidence: retain their original boundary.
                    baseline = (current['baseline_observed_at'] if current['baseline_observed_at'] is not None
                                else previous['created_at'])
                    planned = None
                    for change in db.execute('SELECT * FROM changes WHERE device_id=? AND starts_at<=? AND ends_at>=? AND created_at<=?',
                            (current['device_id'],baseline,current['created_at'],baseline)):
                        # Entire observation window and all changed sections must be covered.
                        if all(x['context'] in json.loads(change['contexts']) for x in changes):
                            planned = change['id'];break
                    drift_id = uuid.uuid4().hex
                    db.execute('''INSERT INTO drifts(id,before_id,after_id,device_id,device,detected_at,origin,exercise_id,phase,unowned,change_ref,status,changes)
                                  VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                        (drift_id,previous['id'],current['id'],current['device_id'],current['device'],time.time(),
                         'exercise' if current['exercise_id'] else 'observed',current['exercise_id'],current['phase'],
                         int(planned is None),planned,'open',json.dumps(changes,ensure_ascii=False)))
                    created.append(drift_id)
            db.execute('UPDATE versions SET checked=1 WHERE id=?',(current['id'],))
    return {'created':len(created),'drift_ids':created}


def current_lab_devices(settings, requested=None):
    from .inventory import load_inventory
    from .nautobot import NautobotClient
    if not settings.nautobot_url or not settings.nautobot_token:
        raise ValueError('Current Nautobot scope is required')
    current={r['id']:r for r in NautobotClient(settings.nautobot_url,settings.nautobot_token,15).devices()}
    devices=[d for d in load_inventory() if d.id and d.site=='lab'
             and current.get(d.id,{}).get('site')=='lab' and current[d.id].get('host')==d.host
             and current[d.id].get('name')==d.name]
    if requested:
        if set(requested)-{key for d in devices for key in (d.id,d.name)}:
            raise ValueError('Requested device is not in current Nautobot lab scope')
        devices=[d for d in devices if d.name in requested or d.id in requested]
    return devices


def fetch_config(device, settings):
    if device.platform=='paloalto_panos':
        from .panos_evidence import PanosAPI
        root=PanosAPI(device,settings).call(type='config',action='show',xpath='/config')
        config=root.find('./result/config')
        if config is None:raise ValueError('Missing PAN running configuration')
        return ET.tostring(config,encoding='unicode')
    if device.platform not in {'arista_eos','cisco_nxos'}:
        raise ValueError('Unsupported configuration platform')
    from .collector import Collector
    from netmiko import ConnectHandler
    user,password,secret=Collector(settings,[])._device_credentials(device)
    with ConnectHandler(device_type=device.platform,host=device.host,port=device.port,
                        username=user,password=password,secret=secret or '',conn_timeout=15,auth_timeout=15,banner_timeout=15) as conn:
        return conn.send_command('show running-config',read_timeout=30)


def backup(settings, requested=None, exercise_id=None, phase='observation', source='manual'):
    if not BACKUP_LOCK.acquire(blocking=False):
        return {'status':'busy','devices':[]}
    try:
        devices=current_lab_devices(settings,requested)
        def collect(device):
            try:
                return record_backup(device,fetch_config(device,settings),exercise_id,phase,source)
            except Exception as exc:
                with connect() as db:
                    db.execute('INSERT OR REPLACE INTO attempts VALUES (?,?,?,?,?)',
                               (device.id,device.name,time.time(),0,type(exc).__name__))
                    db.execute('INSERT INTO backup_observations VALUES (?,?,?,?,?,?,?,?)',
                               (uuid.uuid4().hex,device.id,device.name,None,time.time(),'error',source,type(exc).__name__))
                return {'device':device.name,'status':'error','error':type(exc).__name__}
        # Initialize schema before worker threads contend for it.
        with connect():pass
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(collect,devices))
        detection=detect_drift()
        return {'status':'ok' if all(r['status']!='error' for r in results) else 'partial',
                'scope':'lab','devices':results,'detection':detection}
    finally:
        BACKUP_LOCK.release()


def list_drifts(unowned=None, status=None, origin=None, limit=100):
    clauses=[];args=[]
    for key,value in [('unowned',unowned),('status',status),('origin',origin)]:
        if value is not None:
            clauses.append(key+'=?');args.append(value)
    where=' WHERE '+' AND '.join(clauses) if clauses else ''
    with connect() as db:
        count=db.execute('SELECT count(*) FROM drifts'+where,args).fetchone()[0]
        rows=[dict(r) for r in db.execute('SELECT * FROM drifts'+where+' ORDER BY detected_at DESC LIMIT ?',args+[limit])]
    for row in rows:
        row['changes']=json.loads(row['changes']);row['unowned']=bool(row['unowned'])
    return {'total':count,'records':rows}


def resolve_drift(drift_id, actor, reason):
    with connect() as db:
        if not db.execute('SELECT id FROM drifts WHERE id=?',(drift_id,)).fetchone():
            raise KeyError('Drift not found')
        db.execute("UPDATE drifts SET status='resolved',resolved_at=?,resolved_by=?,resolution=? WHERE id=? AND status='open'",
                   (time.time(),actor,reason,drift_id))
    return {'status':'resolved','id':drift_id}


def register_change(change_id, device_id, owner, starts_at, ends_at, contexts):
    if not contexts or not 0<ends_at-starts_at<=86400 or ends_at<=time.time():
        raise ValueError('Expected explicit contexts and a future window of at most 24 hours')
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('INSERT INTO changes VALUES (?,?,?,?,?,?,?)',
                   (change_id,device_id,owner,starts_at,ends_at,json.dumps(contexts),time.time()))
    return {'id':change_id,'status':'registered'}


class DriftCollector:
    def describe(self):
        return []

    def collect(self):
        # No device connections and no raw configuration in Prometheus labels.
        available=GaugeMetricFamily('aiops_config_history_available', 'Configuration history database readable.')
        try:
            db=sqlite3.connect((root_path()/'history.sqlite3').as_uri()+'?mode=ro', uri=True, timeout=1)
            try:
                db.row_factory=sqlite3.Row
                groups=db.execute('SELECT device,origin,status,unowned,count(*) AS n,MAX(detected_at) AS latest FROM drifts GROUP BY device,origin,status,unowned').fetchall()
                devices=db.execute('SELECT * FROM attempts').fetchall()
            finally:
                db.close()
        except (sqlite3.Error, ValueError):
            available.add_metric([],0)
            yield available
            return
        available.add_metric([],1)
        yield available
        metric=GaugeMetricFamily('aiops_config_drift_records','Persisted drift records by source, ownership and handling state.',
                                 labels=['device','origin','status','unowned'])
        counts={(r['device'],r['origin'],r['status'],str(bool(r['unowned'])).lower()):r['n'] for r in groups}
        for device in devices:
            for origin in ('observed','exercise'):
                for status in ('open','resolved'):
                    for unowned in ('true','false'):
                        labels=(device['device'],origin,status,unowned)
                        metric.add_metric(list(labels),counts.get(labels,0))
        yield metric
        metric=GaugeMetricFamily('aiops_config_drift_last_detected_timestamp_seconds',
                                 'Latest detection among currently open unowned drift records.',labels=['device','origin'])
        for row in groups:
            if row['status']=='open' and row['unowned']:
                metric.add_metric([row['device'],row['origin']],row['latest'])
        yield metric
        metric=GaugeMetricFamily('aiops_config_backup_failure_info',
                                 'Latest failed backup attempt with a bounded error category.',labels=['device','reason'])
        for device in devices:
            if not device['success']:
                metric.add_metric([device['device'],backup_failure_reason(device['error'])],1)
        yield metric
        for name,description,column in [('success','Latest configuration backup attempt succeeded.','success'),
                                        ('last_attempt_timestamp_seconds','Last configuration backup attempt.','attempted_at')]:
            metric=GaugeMetricFamily('aiops_config_backup_'+name,description,labels=['device'])
            for device in devices:metric.add_metric([device['device']],device[column])
            yield metric


def backup_failure_reason(error):
    # Only exception class names are persisted by backup(). Never expose an
    # arbitrary error message or a device response as a metric label.
    categories={
        'NetmikoAuthenticationException':'authentication', 'AuthenticationException':'authentication',
        'NetmikoTimeoutException':'timeout', 'ReadTimeout':'timeout', 'TimeoutError':'timeout',
        'ConnectTimeout':'timeout', 'ReadTimeoutError':'timeout',
        'ConnectionError':'connection', 'SSHException':'connection', 'ConnectError':'connection',
        'ConnectionRefusedError':'connection', 'ConnectionResetError':'connection',
        'HTTPError':'api_error', 'HTTPStatusError':'api_error',
        'ValueError':'invalid_response', 'ParseError':'invalid_response',
        'PermissionError':'storage_permission', 'OperationalError':'storage_error',
        'DatabaseError':'storage_error',
    }
    return categories.get(error,'unknown')
