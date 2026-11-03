import copy
import json
import os
import tempfile
import time
import unittest
from dataclasses import asdict,replace
from types import SimpleNamespace
from unittest.mock import patch,MagicMock
from aiops_agent.discovery import Discovery,expand,classify,save,get,recover,database
from aiops_agent.inventory import Device
from aiops_agent.settings import load_settings
from test_bgp_baseline import TABLE,EMPTY


def record(name='switch',host='192.0.2.1',site='lab'):
 return dict(id=name,name=name,host=host,site=site,role='switch',platform='arista_eos',port=22,use_ssh=True,use_snmp=False,status='Active',username_env=None,password_env=None,secret_env=None)

class Client:
 def __init__(self):
  self.rows=[record(),record('other','192.0.2.9','other')];self.writes=[]
  self.raw={d['id']:{'id':d['id'],'name':d['name'],'status':{'name':'Maintenance'},'custom_fields':{},'location':{'name':d['site']}} for d in self.rows}
 def devices(self):return copy.deepcopy(self.rows)
 def _related_name(self,row):return row.get('name','unknown')
 def _get(self,path):return copy.deepcopy(self.raw[path.strip('/').split('/')[-1]])
 def _get_all(self,path):
  if path.startswith('extras/statuses'):return [{'id':'maintenance','name':'Maintenance','content_types':['dcim.device']},{'id':'connected','name':'Connected','content_types':['dcim.cable']}]
  if path.startswith('extras/custom-fields'):return [{'key':k} for k in ['management_ip','use_ssh','connection_port','aiops_monitoring_enabled','username_env','password_env','secret_env','aiops_bgp_baseline','aiops_igp_baseline']]
  if path.startswith('dcim/interfaces'):return []
  return []
 def _request(self,method,path,payload):
  self.writes.append((method,path,copy.deepcopy(payload)))
  if path=='dcim/devices/' and method=='POST':
   created=[]
   for p in payload:
    ident=p['name'];r=record(ident,p['custom_fields']['management_ip']);r['port']=p['custom_fields']['connection_port'];r['use_ssh']=p['custom_fields']['use_ssh'];self.rows.append(r)
    self.raw[ident]={**p,'id':ident,'status':{'name':'Maintenance'},'location':{'name':'lab'}};created.append(copy.deepcopy(self.raw[ident]))
   return created
  if path=='dcim/devices/' and method=='PATCH':
   for p in payload:
    self.raw[p['id']]['custom_fields'].update(copy.deepcopy(p['custom_fields']))
    row=next(r for r in self.rows if r['id']==p['id'])
    for field,key in [('port','connection_port'),('use_ssh','use_ssh')]:
     if key in p['custom_fields']:row[field]=p['custom_fields'][key]
   return payload
  return {'id':'cable'}

class DiscoveryTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.env=patch.dict(os.environ,{'AIOPS_DISCOVERY_PATH':self.tmp.name});self.env.start();self.addCleanup(self.env.stop)
  settings=replace(load_settings(),device_username='test',device_password='test',snmp_enabled=False)
  self.s=Discovery(settings,None);self.c=Client();self.s.client=lambda:self.c
  self.s.options=lambda:{'profiles':[{'id':'p','available':True,'platform':'arista_eos','refs':{'username_env':None,'password_env':None,'secret_env':None}}],'locations':[{'id':'lab'}],'roles':[{'id':'role'}],'types':[{'id':'type'}],'platforms':[{'id':'eos','driver':'arista_eos'}]}
 def plan(self,text='192.0.2.1'):return self.s.plan(text,[],'tester')
 def probe_result(self,job):
  row=job['candidates'][0];row['probe']={'status':'ok','device_id':row['device_id'],'host':row['host'],'port':row['port'],'platform':row['platform'],'observed_at':__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),'lldp':[],'bgp_candidate':{'intent':'enabled','neighbors':[{'vrf':'default','neighbor':'192.0.2.2','peer_as':65002,'afi_safi':'ipv4_unicast','required':True,'observed_state':'Estab'}]}};save(job);return row
 def test_expand_dedup_and_bounded_ranges(self):
  self.assertEqual(expand('192.0.2.1 192.0.2.1:22,192.0.2.2:2222'),[('192.0.2.1',22),('192.0.2.2',2222)])
  self.assertEqual(len(expand('192.0.2.0/30')),2)
  for value in ['192.0.0.0/16','127.0.0.1','224.0.0.1','host.invalid','192.0.2.1:0','::1']:
   with self.assertRaises(ValueError):expand(value)
 def test_plan_does_not_probe_and_rejects_nonlab_alias_port(self):
  with patch('aiops_agent.discovery.ConnectHandler') as conn:
   j=self.plan('192.0.2.1 192.0.2.9 192.0.2.9:2222 192.0.2.20')
  self.assertEqual([r['inventory_status'] for r in j['candidates']],['known','out_of_scope','out_of_scope','unregistered']);conn.assert_not_called()
 def test_ambiguous_endpoint(self):
  self.c.rows.append(record('duplicate'));self.assertEqual(classify('192.0.2.1',22,self.c.rows)[0],'conflict')
 def test_register_pauses_new_device_and_prevents_duplicates(self):
  j=self.plan('192.0.2.20');row=j['candidates'][0]
  args=(j['id'],[{'id':row['id'],'name':'new-switch'}],'p','lab','eos','role','type','tester')
  saved=self.s.register(*args);self.assertTrue(saved['candidates'][0]['registered_by_job'])
  payload=self.c.writes[-1][2][0];self.assertFalse(payload['custom_fields']['aiops_monitoring_enabled']);self.assertEqual(payload['status'],'maintenance')
  with self.assertRaises(ValueError):self.s.register(*args)
  self.assertEqual(len(self.c.writes),1)
 def test_registration_revalidates_scope(self):
  j=self.plan('192.0.2.20');row=j['candidates'][0];self.c.rows.append(record('moved','192.0.2.20','other'))
  with self.assertRaises(ValueError):self.s.register(j['id'],[{'id':row['id'],'name':'new'}],'p','lab','eos','role','type','tester')
  self.assertFalse(self.c.writes)
 def test_probe_scope_rechecked_before_connection(self):
  j=self.plan();self.c.rows[0]['site']='other'
  with patch('aiops_agent.discovery.ConnectHandler') as conn:
   with self.assertRaises(ValueError):self.s._probe(j['candidates'][0])
  conn.assert_not_called()
 def test_eos_probe_identity_all_vrf_peers_and_inactive_igp(self):
  j=self.plan();conn=MagicMock()
  config='hostname synthetic\nrouter bgp 65001\nend'
  def output(cmd,**kw):
   return {'show version':'Arista vEOS\nSoftware image version: 4.30.0F','show running-config':config,'show ip bgp summary vrf all':TABLE,'show bgp evpn summary':EMPTY,'show lldp neighbors':'Et1 peer Ethernet2 120'}.get(cmd,'')
  conn.send_command_timing.side_effect=output
  with patch('aiops_agent.discovery.ConnectHandler',return_value=conn):result=self.s._probe(j['candidates'][0])
  self.assertEqual(result['status'],'ok');self.assertEqual(len(result['bgp_candidate']['neighbors']),2);self.assertEqual(result['igp_candidate']['ospf']['intent'],'not_applicable');self.assertEqual(result['lldp'][0]['neighbor'],'peer')
  self.assertNotIn('running-config',json.dumps(result));conn.disconnect.assert_called_once()
 def test_failed_or_empty_config_never_creates_disabled_baselines(self):
  j=self.plan();conn=MagicMock();conn.send_command_timing.side_effect=lambda cmd,**kw:'Arista vEOS' if cmd=='show version' else ''
  with patch('aiops_agent.discovery.ConnectHandler',return_value=conn):result=self.s._probe(j['candidates'][0])
  self.assertNotIn('igp_candidate',result);self.assertNotIn('bgp_candidate',result)
 def test_async_failure_persists_without_leaking_exception_message(self):
  j=self.plan();self.s._probe=MagicMock(side_effect=RuntimeError('secret-password'))
  self.s.probe(j['id'],[j['candidates'][0]['id']],'tester')
  for _ in range(100):
   final=get(j['id'])
   if final['status']!='running':break
   time.sleep(.01)
  self.assertEqual(final['status'],'partial');self.assertNotIn('secret-password',json.dumps(final))
 def test_restart_marks_running_job_interrupted(self):
  j=self.plan();j['status']='running';save(j);recover();self.assertEqual(get(j['id'])['status'],'interrupted')
 def test_enable_only_job_created_devices(self):
  j=self.plan();r=self.probe_result(j);self.assertEqual(self.s.enable(j['id'],[r['id']],'tester')['enabled'],0);self.assertFalse(self.c.writes)
  r['registered_by_job']=True;save(j);self.assertEqual(self.s.enable(j['id'],[r['id']],'tester')['enabled'],1)
 def test_baseline_requires_exact_fresh_review(self):
  j=self.plan();r=self.probe_result(j)
  with self.assertRaises(ValueError):self.s.baselines(j['id'],[r['id']],'tester',True)
  review=self.s.baselines(j['id'],[r['id']],'tester');result=self.s.baselines(j['id'],[r['id']],'tester',True,review['review_id']);self.assertEqual(result['saved'],1)
  self.assertEqual(self.c.raw['switch']['custom_fields']['aiops_bgp_baseline']['neighbors'][0]['neighbor'],'192.0.2.2')
  second=self.s.baselines(j['id'],[r['id']],'tester');self.assertEqual(second['preview'][0]['status'],'existing_preserved')
 def test_new_probe_invalidates_baseline_review(self):
  j=self.plan();r=self.probe_result(j);preview=self.s.baselines(j['id'],[r['id']],'tester')
  changed=get(j['id']);changed['candidates'][0]['probe']['bgp_candidate']['neighbors'][0]['peer_as']=65009;save(changed)
  with self.assertRaises(ValueError):self.s.baselines(j['id'],[r['id']],'tester',True,preview['review_id'])
  self.assertFalse(self.c.writes)
 def test_stale_probe_cannot_enable_or_approve(self):
  j=self.plan();r=self.probe_result(j);r['probe']['observed_at']='2020-01-01T00:00:00+00:00';save(j)
  with self.assertRaises(ValueError):self.s.baselines(j['id'],[r['id']],'tester')
 def test_candidate_or_out_of_scope_cable_not_saved(self):
  j=self.plan();self.s.topology=lambda _: {'links':[{'id':'l','type':'adjacency','confirmation':'candidate'}]}
  with self.assertRaises(ValueError):self.s.cables(j['id'],['l'],'tester')
  self.assertFalse(self.c.writes)
 def test_confirmed_cables_created_once_and_existing_preserved(self):
  from datetime import datetime,timezone
  from urllib.parse import urlsplit,parse_qs
  j=self.plan();self.c.rows[1]['site']='lab'
  link={'id':'l','from':'switch','to':'other','local_interface':'Ethernet1','remote_interface':'Ethernet2','type':'physical','confirmation':'confirmed','freshness':'fresh','last_success_at':datetime.now(timezone.utc).isoformat()}
  self.s.topology=lambda _: {'links':[link]}
  original=self.c._get_all
  cabled=[False]
  def rows(path):
   if not path.startswith('dcim/interfaces'):return original(path)
   q=parse_qs(urlsplit(path).query);d=q['device_id'][0]
   return [{'id':'port-'+d,'name':q['name'][0],'device':{'id':d},'cable':{'id':'cable-1'} if cabled[0] else None}]
  self.c._get_all=rows
  original_request=self.c._request
  def request(method,path,payload):
   if path=='dcim/cables/':self.c.writes.append((method,path,payload));return [{'id':'cable-1'}]
   return original_request(method,path,payload)
  self.c._request=request
  result=self.s.cables(j['id'],['l'],'tester');self.assertEqual(result['results'][0]['status'],'created')
  self.assertEqual(self.c.writes[0][2][0]['termination_a_id'],'port-switch')
  cabled[0]=True;result=self.s.cables(j['id'],['l'],'tester');self.assertEqual(result['results'][0]['status'],'existing');self.assertEqual(len(self.c.writes),1)
 def test_changed_platform_cannot_use_old_probe(self):
  j=self.plan();r=self.probe_result(j);self.c.rows[0]['platform']='paloalto_panos'
  with self.assertRaises(ValueError):self.s.baselines(j['id'],[r['id']],'tester')
  self.assertFalse(self.c.writes)
 def test_telnet_plan_register_and_probe_use_same_transport(self):
  j=self.s.plan('192.0.2.20 192.0.2.21:2323',[],'tester','telnet')
  self.assertEqual([c['port'] for c in j['candidates']],[23,2323])
  r=j['candidates'][0]
  j=self.s.register(j['id'],[{'id':r['id'],'name':'new-telnet'}],'p','lab','eos','role','type','tester')
  self.assertFalse(self.c.writes[-1][2][0]['custom_fields']['use_ssh'])
  conn=MagicMock();conn.send_command_timing.return_value='Arista vEOS'
  with patch('aiops_agent.discovery.ConnectHandler',return_value=conn) as connect:
   result=self.s._probe(j['candidates'][0])
  self.assertEqual(connect.call_args.kwargs['device_type'],'arista_eos_telnet')
  self.assertEqual(connect.call_args.kwargs['port'],23)
  self.assertEqual(result['transport'],'telnet')
 def test_known_transport_comes_from_inventory(self):
  self.c.rows[0]['use_ssh']=False
  j=self.s.plan('192.0.2.1:22',[],'tester','ssh')
  self.assertEqual(j['candidates'][0]['transport'],'telnet')
  conn=MagicMock();conn.send_command_timing.return_value='Arista vEOS'
  with patch('aiops_agent.discovery.ConnectHandler',return_value=conn) as connect:self.s._probe(j['candidates'][0])
  self.assertEqual(connect.call_args.kwargs['device_type'],'arista_eos_telnet')
  self.assertEqual(connect.call_args.kwargs['port'],22)
 def test_transport_change_invalidates_probe_evidence(self):
  j=self.plan();r=self.probe_result(j);self.c.rows[0]['use_ssh']=False
  with self.assertRaises(ValueError):self.s.baselines(j['id'],[r['id']],'tester')
  self.assertFalse(self.c.writes)
 def test_unsupported_telnet_driver_rejected_before_registration(self):
  opts=self.s.options();opts['profiles'][0]['platform']='cisco_nxos';opts['platforms'][0]['driver']='cisco_nxos';self.s.options=lambda:opts
  j=self.s.plan('192.0.2.20',[],'tester','telnet');r=j['candidates'][0]
  with self.assertRaisesRegex(ValueError,'Telnet is not supported'):
   self.s.register(j['id'],[{'id':r['id'],'name':'new'}],'p','lab','eos','role','type','tester')
  self.assertFalse(self.c.writes)
 def connection_edit(self,row,transport='telnet',port=23):
  return {'id':row['id'],'transport':transport,'port':port,'expected_port':row['port'],'expected_transport':row.get('transport','ssh')}
 def test_per_device_connections_mixed_known_and_new(self):
  j=self.plan('192.0.2.1 192.0.2.20 192.0.2.21:2222');r=self.probe_result(j)
  j=get(j['id']);j['baseline_review']={'id':'old'};save(j)
  edits=[self.connection_edit(j['candidates'][0]),self.connection_edit(j['candidates'][1],port=2323)]
  result=self.s.connections(j['id'],edits,'tester')
  self.assertEqual([(c['transport'],c['port']) for c in result['candidates']],[('telnet',23),('telnet',2323),('ssh',2222)])
  self.assertEqual(self.c.writes[-1][2],[{'id':'switch','custom_fields':{'use_ssh':False,'connection_port':23}}])
  self.assertNotIn('probe',result['candidates'][0]);self.assertNotIn('baseline_review',result)
  self.assertFalse(self.s.current(result['candidates'][0]).use_ssh)
 def test_new_candidate_connection_edit_does_not_register_or_probe(self):
  j=self.plan('192.0.2.20');r=j['candidates'][0]
  with patch('aiops_agent.discovery.ConnectHandler') as connect:
   result=self.s.connections(j['id'],[self.connection_edit(r)],'tester')
  self.assertFalse(self.c.writes);connect.assert_not_called()
  self.assertEqual(result['candidates'][0]['port'],23)
  self.s.register(j['id'],[{'id':r['id'],'name':'new-telnet'}],'p','lab','eos','role','type','tester')
  self.assertFalse(self.c.writes[-1][2][0]['custom_fields']['use_ssh'])
 def test_connection_edits_reject_scope_changes_stale_values_and_collisions(self):
  j=self.plan();r=j['candidates'][0];edit=self.connection_edit(r)
  self.c.rows[0]['site']='other'
  with self.assertRaises(ValueError):self.s.connections(j['id'],[edit],'tester')
  self.c.rows[0]['site']='lab';self.c.rows[0]['use_ssh']=False
  with self.assertRaises(ValueError):self.s.connections(j['id'],[edit],'tester')
  self.c.rows[0]['use_ssh']=True
  with self.assertRaises(ValueError):self.s.connections(j['id'],[{**edit,'expected_port':99}],'tester')
  self.c.rows.append({**record('occupied'),'port':23})
  with self.assertRaises(ValueError):self.s.connections(j['id'],[edit],'tester')
  self.assertFalse(self.c.writes)
 def test_connection_batch_preflight_prevents_partial_write(self):
  j=self.plan('192.0.2.1 192.0.2.20:22 192.0.2.20:23')
  with self.assertRaises(ValueError):self.s.connections(j['id'],[self.connection_edit(r) for r in j['candidates'][:2]],'tester')
  self.assertFalse(self.c.writes)
  self.c.rows[0]['platform']='cisco_nxos'
  with self.assertRaises(ValueError):self.s.connections(j['id'],[self.connection_edit(j['candidates'][0])],'tester')
  self.assertFalse(self.c.writes)
 def test_connections_blocked_while_discovering_and_noops_keep_evidence(self):
  j=self.plan();r=self.probe_result(j)
  result=self.s.connections(j['id'],[self.connection_edit(r,'ssh',22)],'tester')
  self.assertIn('probe',result['candidates'][0]);self.assertFalse(self.c.writes)
  j['status']='running';save(j)
  with self.assertRaises(ValueError):self.s.connections(j['id'],[self.connection_edit(r)],'tester')
 def test_connections_readback_failure_does_not_claim_success(self):
  j=self.plan();r=j['candidates'][0];original=self.c._get
  self.c._get=lambda path:{'custom_fields':{}}
  with self.assertRaisesRegex(ValueError,'readback failed'):self.s.connections(j['id'],[self.connection_edit(r)],'tester')
  self.assertEqual(get(j['id'])['candidates'][0]['port'],22)
 def test_api_json_scope_and_origin(self):
  from fastapi import FastAPI
  from fastapi.testclient import TestClient
  from aiops_agent.discovery_api import router
  app=FastAPI()
  with patch('aiops_agent.discovery_api.Discovery',return_value=self.s):app.include_router(router(self.s.settings,None))
  client=TestClient(app)
  self.assertEqual(client.post('/discovery/jobs',json={'targets':'192.0.2.1','actor':'tester'},headers={'Origin':'http://evil.invalid'}).status_code,403)
  r=client.post('/discovery/jobs',json={'targets':'192.0.2.1','actor':'tester'});self.assertEqual(r.status_code,200)
  self.assertEqual(client.get('/discovery/jobs/'+r.json()['id']).status_code,200)
  j=r.json();body={'actor':'tester','entries':[self.connection_edit(j['candidates'][0])]}
  url='/discovery/jobs/'+j['id']+'/connections'
  self.assertEqual(client.post(url,json=body,headers={'Origin':'http://evil.invalid'}).status_code,403)
  self.assertEqual(client.post(url,json=body).status_code,200)
  self.assertIn('Network discovery',client.get('/discovery').text)
  r=client.post('/discovery/jobs',json={'targets':'192.0.2.20','actor':'tester','transport':'telnet'})
  self.assertEqual(r.json()['candidates'][0]['port'],23)
  self.assertEqual(client.post('/discovery/jobs',json={'targets':'192.0.2.20','actor':'tester','transport':'ftp'}).status_code,422)
