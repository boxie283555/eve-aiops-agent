"""Bounded, auditable lab onboarding; inventory registration precedes device I/O."""
from __future__ import annotations
from .transport import cli_driver
import hashlib
import ipaddress
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import asdict, fields
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from netmiko import ConnectHandler
from .inventory import Device
from .collector import Collector
from .nautobot import NautobotClient
from .parser import parse_device_hardware, parse_panos_hardware, parse_lldp_neighbors
from .bgp_baseline import parse_eos_ipv4_all_vrfs, validate as validate_bgp
from .igp_baseline import applicability_from_evidence, validate as validate_igp
from .panos_evidence import PanosAPI
from .topology import load_snapshot, fresh
from .routing import command_ok

MUTATIONS = threading.Lock()
PROBE_SLOT = threading.Lock()
SUPPORTED = ('arista_eos','cisco_nxos','paloalto_panos')


def now(): return datetime.now(timezone.utc).isoformat()


@contextmanager
def database():
    path=Path(os.getenv('AIOPS_DISCOVERY_PATH','/app/data/discovery'))
    path.mkdir(parents=True,exist_ok=True,mode=0o700)
    file=path/'discovery.sqlite3'
    file.touch(mode=0o600,exist_ok=True)
    db=sqlite3.connect(file,timeout=15);db.row_factory=sqlite3.Row
    db.executescript('''CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,created REAL,status TEXT,actor TEXT,payload TEXT);
    CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT,at TEXT,job_id TEXT,actor TEXT,action TEXT,details TEXT);''')
    try:yield db;db.commit()
    finally:db.close()


def audit(job_id,actor,action,details):
    with database() as db:db.execute('INSERT INTO audit(at,job_id,actor,action,details) VALUES(?,?,?,?,?)',(now(),job_id,actor,action,json.dumps(details)))


def save(job):
    with database() as db:db.execute('INSERT OR REPLACE INTO jobs VALUES(?,?,?,?,?)',(job['id'],job['created'],job['status'],job['actor'],json.dumps(job)))


def get(job_id):
    with database() as db:row=db.execute('SELECT payload FROM jobs WHERE id=?',(job_id,)).fetchone()
    if not row:raise ValueError('Job not found')
    return json.loads(row['payload'])


def history():
    with database() as db:
        return [dict(r) for r in db.execute('SELECT id,created,status,actor FROM jobs ORDER BY created DESC LIMIT 30')]


def recover():
    with database() as db:
        rows=db.execute("SELECT payload FROM jobs WHERE status='running'").fetchall()
        for row in rows:
            job=json.loads(row['payload']);job['status']='interrupted'
            for c in job['candidates']:
                if c.get('probe',{}).get('status')=='running':c['probe']={'status':'interrupted'}
            db.execute('UPDATE jobs SET status=?,payload=? WHERE id=?',('interrupted',json.dumps(job),job['id']))


def expand(text, default_port=22):
    """Candidate address expansion only. Never sends network packets."""
    endpoints=[]
    for token in re.split(r'[\s,;]+',text.strip()):
        if not token:continue
        if '/' in token:
            net=ipaddress.ip_network(token,strict=False)
            if net.version!=4 or net.num_addresses>256:raise ValueError('IPv4 ranges must be /24 or smaller')
            values=[(str(ip),default_port) for ip in net.hosts()]
        else:
            host,sep,port=token.partition(':')
            ip=ipaddress.ip_address(host)
            if ip.version!=4:raise ValueError('Use IPv4 management addresses')
            values=[(str(ip),int(port) if sep else default_port)]
        for host,port in values:
            ip=ipaddress.ip_address(host)
            if ip.is_loopback or ip.is_multicast or ip.is_unspecified or ip.is_link_local or not 1<=port<=65535:
                raise ValueError('Invalid management endpoint')
            if (host,port) not in endpoints:endpoints.append((host,port))
            if len(endpoints)>256:raise ValueError('At most 256 candidate addresses')
    return endpoints


def classify(host,port,inventory):
    matches=[d for d in inventory if d['host']==host and int(d['port'])==port]
    if len(matches)>1:return 'conflict',None
    if matches:return ('known' if matches[0]['site']=='lab' else 'out_of_scope'),matches[0]
    # Do not use an alternative port to expand discovery to non-lab peers.
    if any(d['host']==host and d['site']!='lab' for d in inventory):return 'out_of_scope',None
    return 'unregistered',None


def device(row):
    allowed={f.name for f in fields(Device)}
    data={k:v for k,v in row.items() if k in allowed and k!='snmp'}
    return Device(**data)


class Discovery:
    def __init__(self,settings,collector):self.settings,self.collector=settings,collector
    def client(self):return NautobotClient(self.settings.nautobot_url,self.settings.nautobot_token,30)
    def inventory(self):return self.client().devices()

    def profiles(self,inventory):
        profiles={}
        for d in inventory:
            if d['site']!='lab' or d['platform'] not in SUPPORTED:continue
            refs={k:d.get(k) for k in ('username_env','password_env','secret_env')}
            key=hashlib.sha256(json.dumps([d['platform'],refs],sort_keys=True).encode()).hexdigest()[:16]
            user=os.getenv(refs['username_env'] or '') or self.settings.device_username
            password=os.getenv(refs['password_env'] or '') or self.settings.device_password
            profiles[key]={'id':key,'platform':d['platform'],'refs':refs,'available':bool(user and password),
                           'label':d['platform']+' / '+(refs['username_env'] or 'default')}
        for p in json.loads(os.getenv('AIOPS_DISCOVERY_PROFILES','[]')):
            if p['platform'] not in SUPPORTED:continue
            refs={k:p.get(k) for k in ('username_env','password_env','secret_env')}
            # Named profiles must refer to configured environment values, never inline secrets.
            key=hashlib.sha256(json.dumps([p['platform'],refs],sort_keys=True).encode()).hexdigest()[:16]
            profiles[key]={'id':key,'platform':p['platform'],'refs':refs,'label':p.get('name') or p['platform'],
                           'available':bool(os.getenv(refs['username_env'] or '') and os.getenv(refs['password_env'] or ''))}
        return list(profiles.values())

    def options(self):
        c=self.client();inv=c.devices()
        def rows(path):return c._get_all(path+'?limit=100')
        locations=[r for r in rows('dcim/locations/') if r['name']=='lab']
        types=rows('dcim/device-types/');platforms=rows('dcim/platforms/');roles=rows('extras/roles/')
        return {'scope':'lab','profiles':self.profiles(inv),
                'devices':[{'id':d['id'],'name':d['name'],'host':d['host'],'port':d['port'],'transport':'ssh' if d.get('use_ssh',True) else 'telnet','platform':d['platform']} for d in inv if d['site']=='lab'],
                'locations':[{'id':r['id'],'name':r['name']} for r in locations],
                'types':[{'id':r['id'],'name':r.get('model') or r.get('display'),'manufacturer':c._related_name(r.get('manufacturer'))} for r in types],
                'platforms':[{'id':r['id'],'name':r['name'],'driver':r.get('network_driver')} for r in platforms if r.get('network_driver') in SUPPORTED],
                'roles':[{'id':r['id'],'name':r['name']} for r in roles if 'dcim.device' in r.get('content_types',[])],
                'nautobot_url':self.settings.nautobot_url}

    def plan(self,targets,device_ids,actor,transport='ssh'):
        if transport not in ('ssh','telnet'):raise ValueError('Choose SSH or Telnet')
        inv=self.inventory();endpoints=expand(targets,23 if transport=='telnet' else 22)
        for ident in device_ids:
            d=next((d for d in inv if d['id']==ident and d['site']=='lab'),None)
            if not d:raise ValueError('Selected device is not in current lab inventory')
            if (d['host'],d['port']) not in endpoints:endpoints.append((d['host'],d['port']))
        if not endpoints or len(endpoints)>256:raise ValueError('Select 1 to 256 targets')
        job={'id':uuid.uuid4().hex,'created':time.time(),'status':'planned','actor':actor,'candidates':[]}
        for host,port in endpoints:
            status,d=classify(host,port,inv)
            job['candidates'].append({'id':uuid.uuid4().hex,'host':host,'port':port,'inventory_status':status,
                'device_id':d['id'] if d else None,'name':d['name'] if d else 'new-'+host.replace('.','-'),
                'platform':d['platform'] if d else None,'transport':('ssh' if d.get('use_ssh',True) else 'telnet') if d else transport})
        save(job);audit(job['id'],actor,'plan',{'targets':len(endpoints)})
        return job

    def selected(self,job,ids):
        if not ids or len(ids)>32 or len(set(ids))!=len(ids):raise ValueError('Select 1 to 32 unique candidates')
        rows=[c for c in job['candidates'] if c['id'] in ids]
        if len(rows)!=len(ids):raise ValueError('Unknown candidate')
        return rows

    def current(self,candidate,client=None):
        c=client or self.client()
        inv=c.devices();status,d=classify(candidate['host'],candidate['port'],inv)
        if status!='known' or d['id']!=candidate.get('device_id'):raise ValueError('Current inventory identity or lab scope changed')
        return device(d)

    def connections(self,job_id,entries,actor):
        """Save per-candidate connection edits; only registered lab rows write inventory."""
        with MUTATIONS:
            job=get(job_id)
            if job['status']=='running' or PROBE_SLOT.locked():raise ValueError('Wait for discovery completion before editing connections')
            selected=self.selected(job,[e['id'] for e in entries])
            changes={e['id']:e for e in entries};c=self.client();inv=c.devices();updates=[];review=[]
            for row in selected:
                edit=changes[row['id']]
                if edit['transport'] not in ('ssh','telnet') or not 1<=edit['port']<=65535:raise ValueError('Invalid CLI transport or port')
                before={'port':row['port'],'transport':row.get('transport','ssh')}
                if (edit['expected_port'],edit['expected_transport'])!=(before['port'],before['transport']):raise ValueError('Connection settings changed; reload the job before editing')
                if row['inventory_status'] not in ('known','unregistered'):raise ValueError('Candidate is conflicting or outside lab')
                status,current=classify(row['host'],row['port'],inv)
                if row.get('device_id'):
                    if status!='known' or current['id']!=row['device_id']:raise ValueError('Current inventory identity or lab scope changed')
                    d=device(current)
                    if ('ssh' if d.use_ssh else 'telnet')!=before['transport']:raise ValueError('Nautobot transport changed; create a new preview')
                    from dataclasses import replace
                    cli_driver(replace(d,use_ssh=edit['transport']=='ssh'))
                elif status!='unregistered':raise ValueError('Candidate inventory changed; create a new preview')
                target_status,target=classify(row['host'],edit['port'],inv)
                if target_status!='unregistered' and not (target_status=='known' and target['id']==row.get('device_id')):
                    raise ValueError('Requested endpoint is registered, conflicting or outside lab')
                after={'port':edit['port'],'transport':edit['transport']}
                if before==after:continue
                if row.get('device_id'):updates.append({'id':row['device_id'],'custom_fields':{'use_ssh':edit['transport']=='ssh','connection_port':edit['port']}})
                review.append({'candidate':row['id'],'device':row['name'],'device_id':row.get('device_id'),'before':before,'after':after})
            endpoints=[(r['host'],changes[r['id']]['port'] if r['id'] in changes else r['port']) for r in job['candidates']]
            if len(set(endpoints))!=len(endpoints):raise ValueError('Candidate endpoints would overlap; use a distinct port or a new preview')
            if not review:return job
            if updates:
                available={f['key'] for f in c._get_all('extras/custom-fields/?limit=100')}
                if not {'use_ssh','connection_port'}<=available:raise ValueError('Provision CLI connection custom fields first')
            audit(job_id,actor,'connections_requested',{'changes':review})
            if updates:
                c._request('PATCH','dcim/devices/',updates)
                for update in updates:
                    raw=c._get('dcim/devices/'+update['id']+'/').get('custom_fields') or {}
                    if any(raw.get(k)!=v for k,v in update['custom_fields'].items()):raise ValueError('Connection readback failed; inspect Nautobot and create a new preview')
            changed={r['candidate'] for r in review}
            for row in selected:
                if row['id'] not in changed:continue
                row.update(port=changes[row['id']]['port'],transport=changes[row['id']]['transport'])
                row.pop('probe',None)
            job.pop('baseline_review',None);job['status']='planned';save(job)
            audit(job_id,actor,'connections_saved',{'changes':review})
            return job

    def register(self,job_id,entries,profile_id,location,platform,role,device_type,actor):
        if not MUTATIONS.acquire(False):raise ValueError('Another inventory operation is running')
        try:
            job=get(job_id)
            if job['status']=='running':raise ValueError('Wait for the discovery job')
            selected=self.selected(job,[e['id'] for e in entries]);names={e['id']:e['name'].strip() for e in entries}
            inv=self.inventory();opts=self.options()
            p=next((p for p in opts['profiles'] if p['id']==profile_id and p['available']),None)
            if not p:raise ValueError('Configured credential profile unavailable')
            if location not in {r['id'] for r in opts['locations']} or role not in {r['id'] for r in opts['roles']} or device_type not in {r['id'] for r in opts['types']}:
                raise ValueError('Choose existing lab Location, role and device type')
            if not any(r['id']==platform and r['driver']==p['platform'] for r in opts['platforms']):raise ValueError('Platform and credential profile must match')
            c=self.client();statuses=c._get_all('extras/statuses/?limit=100')
            maintenance=next(r['id'] for r in statuses if r['name']=='Maintenance' and 'dcim.device' in r['content_types'])
            fields={r['key'] for r in c._get_all('extras/custom-fields/?limit=100')}
            required={'management_ip','connection_port','aiops_monitoring_enabled','use_ssh','username_env','password_env','secret_env'}
            if not required<=fields:raise ValueError('Provision Agent connection and lifecycle custom fields first')
            existing_names={d['name'].lower() for d in inv};proposed=set()
            for row in selected:
                state,_=classify(row['host'],row['port'],inv)
                if state!='unregistered':raise ValueError('Target already registered, conflicting or outside lab; create a new preview')
                name=names[row['id']]
                if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}',name) or name.lower() in existing_names|proposed:raise ValueError('Device names must be valid and unique')
                proposed.add(name.lower())
            # Nautobot bulk create is atomic; one request avoids partial registration.
            from types import SimpleNamespace
            for r in selected:cli_driver(SimpleNamespace(platform=p['platform'],use_ssh=r.get('transport','ssh')=='ssh'))
            payload=[{'name':names[r['id']],'location':location,'platform':platform,'role':role,'device_type':device_type,
                      'status':maintenance,'custom_fields':{'management_ip':r['host'],'connection_port':r['port'],
                         'aiops_monitoring_enabled':False,'use_ssh':r.get('transport','ssh')=='ssh',**p['refs']}} for r in selected]
            audit(job_id,actor,'register_requested',{'candidates':[r['id'] for r in selected]})
            created=c._request('POST','dcim/devices/',payload)
            byname={d['name']:d for d in created}
            for r in selected:
                r.update(device_id=byname[names[r['id']]]['id'],name=names[r['id']],inventory_status='known',platform=p['platform'],registered_by_job=True)
            save(job);audit(job_id,actor,'registered',{'devices':[r['device_id'] for r in selected]})
            return job
        finally:MUTATIONS.release()

    def probe(self,job_id,ids,actor):
        if not PROBE_SLOT.acquire(False):raise ValueError('A discovery job is already running')
        try:
            with MUTATIONS:
                job=get(job_id);selected=self.selected(job,ids)
                for row in selected:
                    if not row.get('device_id'):raise ValueError('Register candidates in lab before probing')
                    row['probe']={'status':'queued'}
                job['status']='running';job['probe_actor']=actor;save(job);audit(job_id,actor,'probe_started',{'candidates':ids})
            threading.Thread(target=self._run,args=(job,selected),daemon=True).start()
            return job
        except Exception:
            PROBE_SLOT.release();raise

    def _run(self,job,selected):
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures={pool.submit(self._probe,row):row for row in selected}
                for future in as_completed(futures):
                    row=futures[future]
                    try:row['probe']=future.result()
                    except Exception as exc:row['probe']={'status':'failed','error':type(exc).__name__,'observed_at':now()}
                    save(job)
            job['status']='complete' if all(r['probe']['status']=='ok' for r in selected) else 'partial'
            audit(job['id'],job.get('probe_actor',job['actor']),'probe_finished',{'status':job['status']})
        except Exception as exc:job['status']='failed';job['error']=type(exc).__name__
        finally:save(job);PROBE_SLOT.release()

    def _probe(self,candidate):
        # Fresh authoritative scope check immediately precedes each device connection.
        d=self.current(candidate)
        result={'status':'ok','observed_at':now(),'device_id':d.id,'host':d.host,'port':d.port,'platform':d.platform,'transport':'ssh' if d.use_ssh else 'telnet','lldp':[]}
        if d.platform=='paloalto_panos':
            api=PanosAPI(d,self.settings)
            root=api.call(type='op',cmd='<show><system><info/></system></show>')
            result['identity']={k:root.findtext('.//'+k) for k in ('hostname','model','serial','sw-version')}
            config=api.call(type='config',action='show',xpath='/config/devices/entry/network/virtual-router')
            routers=[]
            for vr in config.findall('.//virtual-router/entry'):
                protocols={}
                for key in ('ospf','ospfv3','rip','bgp'):
                    p=vr.find('./protocol/'+key)
                    protocols[key]={'present':p is not None,'enabled':p.findtext('enable') if p is not None else None}
                routers.append({'virtual_router':vr.get('name'),'protocols':protocols})
            evidence={'site':'lab','platform':d.platform,'virtual_routers':routers}
            if routers and all(v['protocols']['bgp']['present'] is False or v['protocols']['bgp']['enabled']=='no' for v in routers):
                result['bgp_candidate']={'intent':'not_applicable','neighbors':[]}
        elif d.platform in ('arista_eos','cisco_nxos'):
            u,p,s=Collector(self.settings,[])._device_credentials(d)
            if not u or not p:raise ValueError('Configured credentials unavailable')
            conn=None
            try:
                conn=ConnectHandler(device_type=cli_driver(d),host=d.host,port=d.port,username=u,password=p,secret=s or '',timeout=12,conn_timeout=12,auth_timeout=12,banner_timeout=12)
                def read(command):return conn.send_command_timing(command,last_read=1,read_timeout=25)
                version=read('show version')
                if not command_ok(version):raise ValueError('Identity command unavailable')
                result['identity']=asdict(parse_device_hardware(version))
                lldp=read('show lldp neighbors')
                result['lldp']= [asdict(p) for p in parse_lldp_neighbors(lldp)] if command_ok(lldp) else []
                result['lldp_available']=command_ok(lldp)
                if d.platform=='arista_eos':
                    config=read('show running-config')
                    match=re.search(r'^hostname\s+(\S+)',config,re.M)
                    result['identity']['hostname']=match[1] if match else None
                    valid=bool(match and re.search(r'^end\s*$',config,re.M))
                    evidence={'site':'lab','platform':d.platform,'config_valid':valid,
                              'igp_config_lines':['present'] if re.search(r'\b(ospf\w*|isis|is-is|rip|eigrp)\b',config,re.I) else []}
                    ipv4,ok4=parse_eos_ipv4_all_vrfs(read('show ip bgp summary vrf all'))
                    evpn,oke=parse_eos_ipv4_all_vrfs(read('show bgp evpn summary'))
                    if ok4 and oke:
                        neighbors=[{'vrf':n.vrf,'neighbor':n.neighbor,'peer_as':int(n.peer_as),'afi_safi':af,'required':True,'observed_state':n.session_state} for af,rows in [('ipv4_unicast',ipv4),('l2vpn_evpn',evpn)] for n in rows]
                        if neighbors:result['bgp_candidate']={'intent':'enabled','neighbors':neighbors}
                        elif valid and not re.search(r'^router bgp\b',config,re.M):result['bgp_candidate']={'intent':'not_applicable','neighbors':[]}
                else:evidence={}
            finally:
                if conn:conn.disconnect()
        else:raise ValueError('Platform not supported for discovery')
        try:result['igp_candidate']=applicability_from_evidence(evidence)
        except (ValueError,KeyError):pass
        result['observed_at']=now()
        return result

    def enable(self,job_id,ids,actor):
        with MUTATIONS:
            job=get(job_id)
            if job['status']=='running':raise ValueError('Wait for discovery completion')
            selected=self.selected(job,ids);updates=[]
            c=self.client()
            for row in selected:
                probe=self.fresh_probe(row);d=self.current(row,c)
                self.match_probe(probe,d)
                # Only devices created by this job may have lifecycle changed here.
                if not row.get('registered_by_job'):continue
                current=c._get('dcim/devices/'+d.id+'/')
                if c._related_name(current.get('status'))!='Maintenance':raise ValueError('Lifecycle changed; review in Nautobot')
                updates.append({'id':d.id,'custom_fields':{'aiops_monitoring_enabled':True}})
            if updates:c._request('PATCH','dcim/devices/',updates)
            audit(job_id,actor,'collection_enabled',{'devices':[r['id'] for r in updates]})
            return {'enabled':len(updates),'note':'Maintenance retained; inventory reloads within 60 seconds'}

    def fresh_probe(self,row):
        probe=row.get('probe',{})
        if probe.get('status')!='ok' or probe.get('device_id')!=row.get('device_id'):raise ValueError('Successful discovery required')
        age=time.time()-datetime.fromisoformat(probe['observed_at']).timestamp()
        if not 0<=age<=900:raise ValueError('Discovery evidence expired; run discovery again')
        return probe

    def match_probe(self,probe,d):
        if (probe.get('host'),probe.get('port'),probe.get('platform'),probe.get('transport','ssh'))!=(d.host,d.port,d.platform,'ssh' if d.use_ssh else 'telnet'):
            raise ValueError('Device connection identity changed; discover again')

    def baselines(self,job_id,ids,actor,apply=False,review_id=None):
        with MUTATIONS:
            job=get(job_id)
            if job['status']=='running':raise ValueError('Wait for discovery completion')
            c=self.client();updates=[];preview=[]
            for row in self.selected(job,ids):
                probe=self.fresh_probe(row);d=self.current(row,c)
                self.match_probe(probe,d)
                raw=c._get('dcim/devices/'+d.id+'/');custom=raw.get('custom_fields') or {};changes={}
                for kind in ('bgp','igp'):
                    key='aiops_'+kind+'_baseline';candidate=probe.get(kind+'_candidate')
                    if custom.get(key) is not None:
                        preview.append({'device':d.name,'protocol':kind,'status':'existing_preserved'});continue
                    if candidate is None:
                        preview.append({'device':d.name,'protocol':kind,'status':'insufficient_evidence'});continue
                    value={'schema_version':1,'device_id':d.id,'revision':1,'approval_status':'approved','approved_by':actor,'approved_at':now(),
                           'source':'discovery wizard '+job_id,'observed_at':probe['observed_at']}
                    if kind=='bgp':
                        value.update(intent=candidate['intent'],neighbors=[{k:v for k,v in n.items() if k!='observed_state'} for n in candidate['neighbors']]);validate_bgp(value,d.id)
                    else:value['protocols']=candidate;validate_igp(value,d.id)
                    changes[key]=value
                    preview.append({'device':d.name,'protocol':kind,'status':'ready','value':value,'observations':candidate})
                if changes:updates.append({'id':d.id,'custom_fields':changes})
            reviewed=[{'device':p['device'],'protocol':p['protocol'],'status':p['status'],
                       'observations':p.get('observations'),'observed_at':p.get('value',{}).get('observed_at')} for p in preview]
            digest=hashlib.sha256(json.dumps(reviewed,sort_keys=True).encode()).hexdigest()
            if apply:
                review=job.get('baseline_review',{})
                if not review_id or review.get('id')!=review_id or review.get('digest')!=digest or review.get('ids')!=sorted(ids) or time.time()-review.get('created',0)>900:
                    raise ValueError('Baseline preview changed or expired; review again before approval')
            else:
                job['baseline_review']={'id':uuid.uuid4().hex,'digest':digest,'ids':sorted(ids),'created':time.time()}
                save(job)
            if apply and updates:
                existing={f['key'] for f in c._get_all('extras/custom-fields/?limit=100')}
                if any(k not in existing for u in updates for k in u['custom_fields']):raise ValueError('Provision baseline custom fields before approval')
                audit(job_id,actor,'baseline_approval_requested',{'preview':preview})
                c._request('PATCH','dcim/devices/',updates)
                for update in updates:
                    saved=c._get('dcim/devices/'+update['id']+'/').get('custom_fields') or {}
                    if any(saved.get(k)!=v for k,v in update['custom_fields'].items()):raise ValueError('Baseline readback failed; inspect Nautobot before retrying')
                audit(job_id,actor,'baselines_saved',{'devices':[u['id'] for u in updates]})
            token=job.get('baseline_review',{}).get('id')
            if apply:
                job.pop('baseline_review',None);save(job)
            return {'preview':preview,'saved':len(updates) if apply else 0,'review_id':token}

    def topology(self,job_id):
        job=get(job_id);inv=self.inventory();lab={d['name']:d for d in inv if d['site']=='lab'}
        selected={c['device_id'] for c in job['candidates'] if c.get('device_id')}
        snap=load_snapshot();links=[]
        for link in snap.links if snap else []:
            a,b=lab.get(link['from']),lab.get(link['to'])
            if a and b and (a['id'] in selected or b['id'] in selected):links.append(link)
        suggestions=[]
        names={d['name'].lower() for d in inv}
        for row in job['candidates']:
            for n in row.get('probe',{}).get('lldp',[]):
                if n.get('neighbor','').lower() not in names:suggestions.append({'observed_by':row['name'],**n})
        return {'links':links,'unregistered_neighbors':suggestions,'observed_at':snap.observed_at if snap else None}

    def cables(self,job_id,link_ids,actor):
        if not link_ids or len(link_ids)>32:raise ValueError('Select 1 to 32 links')
        with MUTATIONS:
            links={r['id']:r for r in self.topology(job_id)['links']};c=self.client();inv=c.devices();lab={d['name']:d for d in inv if d['site']=='lab'}
            statuses=c._get_all('extras/statuses/?limit=100')
            status=next(r['id'] for r in statuses if r['name'] in ('Connected','Active') and 'dcim.cable' in r.get('content_types',[]))
            results=[];pending=[];used=set()
            for ident in dict.fromkeys(link_ids):
                link=links.get(ident)
                if not link or link['type']!='physical' or link['confirmation']!='confirmed' or not fresh(link):raise ValueError('Fresh confirmed physical evidence required')
                ports=[]
                for name,port in [(link['from'],link['local_interface']),(link['to'],link['remote_interface'])]:
                    if name not in lab:raise ValueError('Both cable endpoints must be in lab')
                    rows=c._get_all('dcim/interfaces/?'+urlencode({'device_id':lab[name]['id'],'name':port,'limit':100}))
                    rows=[r for r in rows if r['name']==port and r['device']['id']==lab[name]['id']]
                    if len(rows)!=1:raise ValueError('Wait for interface synchronization or resolve duplicate ports')
                    ports.append(rows[0])
                existing=[p.get('cable') for p in ports]
                cable_id=lambda x:x.get('id') if isinstance(x,dict) else x
                if all(existing) and cable_id(existing[0])==cable_id(existing[1]):
                    results.append({'link':ident,'status':'existing'});continue
                if any(existing):raise ValueError('An interface is already cabled; review the conflict in Nautobot')
                if any(p['id'] in used for p in ports):raise ValueError('Selected cables share a physical interface')
                used.update(p['id'] for p in ports)
                pending.append((ident,{'termination_a_type':'dcim.interface','termination_a_id':ports[0]['id'],
                    'termination_b_type':'dcim.interface','termination_b_id':ports[1]['id'],'status':status}))
            if pending:
                audit(job_id,actor,'cables_requested',{'links':[ident for ident,_ in pending]})
                cables=c._request('POST','dcim/cables/',[payload for _,payload in pending])
                for (ident,_),cable in zip(pending,cables):
                    results.append({'link':ident,'status':'created','cable_id':cable['id']})
                audit(job_id,actor,'cables_saved',{'results':results})
            return {'results':results}
