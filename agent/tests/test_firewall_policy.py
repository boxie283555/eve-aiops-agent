import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'firewall-policy'))
from policy_service.analysis import analyze, identity
from policy_service.main import app as service_app
from aiops_agent import firewall_policy as policy, config_history as h
from aiops_agent.firewall_api import router
from aiops_agent.flow import FlowState
from aiops_agent.panos_policy import parse_config, inspect_firewall


def rule(name='allow',action='allow',uuid='native-1',source='any',extra=''):
    return f'<entry name="{name}" uuid="{uuid}"><from><member>trust</member></from><to><member>untrust</member></to><source><member>{source}</member></source><destination><member>any</member></destination><application><member>any</member></application><service><member>any</member></service><action>{action}</action>{extra}</entry>'


def config(rules=None,second=False):
    block=lambda name,rs:f'<entry name="{name}"><address><entry name="clients"><ip-netmask>10.0.0.0/24</ip-netmask></entry></address><zone><entry name="trust"><network><layer3><member>ethernet1/1</member></layer3></network></entry><entry name="untrust"><network><layer3><member>ethernet1/2</member></layer3></network></entry></zone><rulebase><security><rules>{rs}</rules></security></rulebase></entry>'
    return '<config><mgt-config><users><entry name="admin"><phash>top-secret</phash></entry></users></mgt-config><devices><entry name="local"><vsys>'+block('vsys1',rules if rules is not None else rule())+(block('vsys2',rule()) if second else '')+'</vsys></entry></devices></config>'


class AnalysisTests(unittest.TestCase):
    def test_uuid_order_device_and_vsys_identity(self):
        a=analyze('d1','fw','v1',config(second=True))
        b=analyze('d1','fw','v2',config(rule('inserted',uuid='native-2')+rule()))
        c=analyze('d2','fw','v1',config())
        self.assertEqual(a['policies'][0]['id'],b['policies'][1]['id'])
        self.assertNotEqual(a['policies'][0]['id'],a['policies'][1]['id'])
        self.assertNotEqual(a['policies'][0]['id'],c['policies'][0]['id'])
        self.assertFalse(any(f['category'] in {'duplicate','shadowed','conflict'} for f in a['findings']))

    def test_cidr_objects_and_user_scope(self):
        r=rule(source='clients')+rule('narrow',action='deny',uuid='2',source='10.0.0.5')
        result=analyze('d','fw','v',config(r))
        self.assertTrue(any(f['category']=='shadowed' for f in result['findings']))
        r=rule(source='clients',extra='<source-user><member>alice</member></source-user>')+rule('narrow',action='deny',uuid='2',source='10.0.0.5',extra='<source-user><member>bob</member></source-user>')
        self.assertFalse(any(f['category']=='shadowed' for f in analyze('d','fw','v',config(r))['findings']))

    def test_unknown_negation_does_not_claim_coverage(self):
        r=rule(extra='<negate-source>yes</negate-source>')+rule('other',uuid='2')
        self.assertFalse(any(f['category'] in {'duplicate','shadowed'} for f in analyze('d','fw','v',config(r))['findings']))
        with self.assertRaises(ValueError):analyze('d','fw','v','<!DOCTYPE config [<!ENTITY test "secret">]>'+config())

    def test_secret_filter_and_no_device_network_from_analysis(self):
        clean=policy.policy_xml(config())
        self.assertNotIn('top-secret',clean);self.assertNotIn('mgt-config',clean)
        with patch('socket.create_connection',side_effect=AssertionError('No network')):
            x=TestClient(service_app).post('/analyze',json={'device_id':'d','device_name':'fw','snapshot_id':'v','config_xml':clean})
            self.assertEqual(x.status_code,200,x.text)
            self.assertNotIn('top-secret',x.text)

    def test_modification_preserves_other_fields_and_changes_shared_engine_result(self):
        raw=config(rule(extra='<profile-setting><group><member>strict</member></group></profile-setting>'))
        changed=policy.modify(raw,'vsys1','security','allow','action',['deny'])
        self.assertIn('strict',changed)
        flow=FlowState('10.0.0.5','203.0.113.5','tcp',10000,443)
        lookup=lambda address:[('ethernet1/2','route')]
        self.assertEqual(inspect_firewall(parse_config(raw),flow,'ethernet1/1',lookup)['status'],'allow')
        self.assertEqual(inspect_firewall(parse_config(changed),flow,'ethernet1/1',lookup)['status'],'blocked')
        with self.assertRaises(ValueError):policy.modify(raw,'vsys1','security','missing','disable',[])
        with self.assertRaises(ValueError):policy.modify(raw,'vsys1','security','allow','source',[])


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.env=patch.dict(os.environ,{'AIOPS_CONFIG_HISTORY_PATH':self.temp.name});self.env.start()
        self.d=SimpleNamespace(id='11111111-1111-4111-8111-111111111111',name='synthetic-pa',host='192.0.2.1',platform='paloalto_panos',site='lab')
        self.version=h.record_backup(self.d,config())['version_id']
        self.scope=patch.object(h,'current_lab_devices',return_value=[self.d]);self.scope.start()
        self.analysis=patch.object(policy,'analyze',side_effect=lambda d,v,raw:analyze(d.id,d.name,v,policy.policy_xml(raw)));self.analysis.start()
        app=FastAPI();app.include_router(router(SimpleNamespace()));self.client=TestClient(app)

    def tearDown(self):
        self.analysis.stop();self.scope.stop();self.env.stop();self.temp.cleanup()

    def test_inventory_read_does_not_fetch_or_duplicate_device(self):
        with patch.object(h,'fetch_config') as fetch:
            ds=self.client.get('/api/firewall/devices').json()['devices'];self.assertEqual([d['id'] for d in ds],[self.d.id])
            response=self.client.get('/api/firewall/inventory',params={'device_id':self.d.id});self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['snapshot_id'],self.version);self.assertNotIn('top-secret',response.text)
            fetch.assert_not_called()
        self.assertEqual(self.client.get('/api/firewall/inventory?device_id=unknown').status_code,409)

    def test_version_tamper_and_changed_host_rejected(self):
        row,_=policy.management.load_version(self.version)
        path=Path(self.temp.name)/row['raw_file'];path.write_text(config()+'tampered')
        self.assertEqual(self.client.get('/api/firewall/inventory',params={'device_id':self.d.id}).status_code,409)
        path.write_text(config());self.d.host='192.0.2.2'
        self.assertEqual(self.client.get('/api/firewall/inventory',params={'device_id':self.d.id}).status_code,409)

    def test_preview_audited_without_device_changes_and_report_survives(self):
        data=self.client.get('/api/firewall/inventory',params={'device_id':self.d.id}).json()
        request=dict(device_id=self.d.id,version_id=self.version,rule_id=data['policies'][0]['id'],operation='action',values=['deny'],actor='tester')
        with patch.object(h,'fetch_config') as fetch:
            response=self.client.post('/api/firewall/preview',json=request)
            self.assertEqual(response.status_code,200,response.text);value=response.json()
            self.assertEqual(value['status'],'previewed');self.assertFalse(value['device_configuration_changed'])
            fetch.assert_not_called()
        self.assertNotIn('top-secret',response.text)
        self.assertIn('[REDACTED]',value['diff']) if 'phash' in value['diff'] else None
        records=self.client.get('/api/firewall/reviews',params={'device_id':self.d.id}).json()['records']
        self.assertEqual(records[0]['id'],value['id'])
        self.assertEqual(self.client.get('/api/firewall/reviews/'+value['id']+'/report').status_code,200)
        self.assertEqual(policy.management.list_backups(self.d.id)['total'],1)
        self.assertEqual(policy.management.list_audit(self.d.id)['records'][0]['action'],'firewall_policy_preview')

    def test_busy_requests_fail_without_queuing(self):
        policy.SLOTS.acquire();policy.SLOTS.acquire()
        try:self.assertEqual(self.client.get('/api/firewall/devices').status_code,429)
        finally:policy.SLOTS.release();policy.SLOTS.release()


class ExecutionTests(GatewayTests):
    def make_review(self):
        inv=self.client.get('/api/firewall/inventory',params={'device_id':self.d.id}).json()
        return self.client.post('/api/firewall/preview',json=dict(device_id=self.d.id,version_id=self.version,
            rule_id=inv['policies'][0]['id'],operation='action',values=['deny'],actor='tester')).json()

    def test_execution_disabled_without_allowlist_or_operator_token(self):
        from aiops_agent import firewall_execution as e
        review=self.make_review()
        with patch.object(e,'PanosAPI') as api:
            response=self.client.post('/api/firewall/execute',json={'review_id':review['id'],'confirm_device':self.d.name,'confirm_full_commit':True})
            self.assertEqual(response.status_code,403)
            with patch.dict(os.environ,{'AIOPS_FIREWALL_OPERATOR_TOKEN':'test-token','AIOPS_FIREWALL_WRITE_DEVICES':''}):
                response=self.client.post('/api/firewall/execute',headers={'X-Firewall-Operator-Token':'test-token'},json={'review_id':review['id'],'confirm_device':self.d.name,'confirm_full_commit':True})
                self.assertEqual(response.status_code,403)
            api.assert_not_called()

    def run_execution(self,mode='success'):
        import xml.etree.ElementTree as ET
        from aiops_agent import firewall_execution as e
        review=self.make_review();candidate=config();running=config();calls=[]
        def call(**params):
            nonlocal candidate,running
            calls.append(params)
            cmd=params.get('cmd','')
            if params.get('action')=='show' or cmd=='<show><config><candidate/></config></show>':
                root=ET.Element('response');ET.SubElement(root,'result').append(ET.fromstring(candidate if 'candidate' in cmd else running));return root
            if cmd=='<check><pending-changes/></check>':return ET.fromstring('<response><result>'+('yes' if mode=='pending' else 'no')+'</result></response>')
            if params.get('action')=='edit':
                if mode=='write_timeout':raise TimeoutError('unknown device outcome')
                candidate=policy.modify(config(),'vsys1','security','allow','action',['deny'])
                if mode=='concurrent':candidate=candidate.replace('</vsys>','<unexpected/></vsys>')
            if params.get('type')=='commit':
                running=candidate;return ET.fromstring('<response><result><job>42</job></result></response>')
            if '<jobs>' in cmd:return ET.fromstring('<response><result><job><status>FIN</status><result>OK</result></job></result></response>')
            return ET.fromstring('<response><result/></response>')
        fake=SimpleNamespace(call=call)
        thread=lambda target,args,daemon:SimpleNamespace(start=lambda:target(*args))
        with patch.dict(os.environ,{'AIOPS_FIREWALL_OPERATOR_TOKEN':'test-token','AIOPS_FIREWALL_WRITE_DEVICES':self.d.id}),patch.object(e,'PanosAPI',return_value=fake),patch.object(e.threading,'Thread',side_effect=thread),patch.object(e,'refresh_policy'):
            started=e.start(SimpleNamespace(),review['id'],self.d.name,'test-token')
            result=e.state(started['id'])
            with self.assertRaises(ValueError):e.start(SimpleNamespace(),review['id'],self.d.name,'test-token')
        return result,calls

    def test_commit_verified_and_drift_matches_preregistration(self):
        result,calls=self.run_execution()
        self.assertEqual(result['status'],'verified_configuration',result)
        self.assertFalse(result['traffic_verified'])
        self.assertEqual(sum(p.get('type')=='commit' for p in calls),1)
        drifts=h.list_drifts()['records'];self.assertEqual(len(drifts),1)
        self.assertFalse(drifts[0]['unowned'])
        self.assertEqual(drifts[0]['change_ref'],result['change_ref'])

    def test_pending_changes_refuse_all_writes(self):
        result,calls=self.run_execution('pending');self.assertEqual(result['status'],'failed_before_write')
        self.assertFalse(any(p.get('action')=='edit' or p.get('type')=='commit' for p in calls))

    def test_write_timeout_is_not_retried_and_blocks_more_execution(self):
        result,calls=self.run_execution('write_timeout');self.assertEqual(result['status'],'inspection_required')
        self.assertEqual(sum(p.get('action')=='edit' for p in calls),1)
        self.assertFalse(any(p.get('type')=='commit' for p in calls))

    def test_unexpected_candidate_prevents_commit(self):
        result,calls=self.run_execution('concurrent');self.assertEqual(result['status'],'inspection_required')
        self.assertFalse(any(p.get('type')=='commit' for p in calls))


class ImpactReportTests(unittest.TestCase):
    def test_full_path_replay_is_immutable_and_uses_same_engine(self):
        from test_service_path import lab,SRC,DST
        from datetime import datetime,timezone
        snapshot=lab(populate=True)
        raw=(Path(__file__).parent/'fixtures/lab-pa-policy.xml').read_text()
        for d in snapshot.devices:
            if d['platform']=='paloalto_panos':
                d['pa_policy']=parse_config(raw.replace('ae1.10','ae1.20') if d['name']=='PA-172' else raw)
                d['pa_policy']['last_success_at']=datetime.now(timezone.utc).isoformat()
        selected=next(d for d in snapshot.devices if d['name']=='PA-171')
        device=SimpleNamespace(id=selected['id'],host=selected['host'],name=selected['name'])
        original=copy.deepcopy(snapshot)
        changed=policy.modify(raw,'vsys1','security','vlan101-vlan123-1','action',['deny'])
        with patch.object(policy,'load_snapshot',return_value=snapshot),patch.object(policy,'attach_policy',side_effect=lambda s:s):
            result=policy.flow_impact(device,{'id':'version','last_observed_at':100},raw,changed,
                {'source_ip':SRC,'destination_ip':DST,'protocol':'tcp','source_port':40000,'destination_port':50001})
        self.assertEqual(result['before']['status'],'ok',result['before'])
        self.assertEqual(result['after']['status'],'blocked',result['after'])
        self.assertEqual(result['topology_observed_at'],snapshot.observed_at)
        self.assertEqual(snapshot,original)
        selected['pa_policy'].update(last_success_at='1970-01-01T00:01:40+00:00',evidence_source='q3_backup_replay')
        # A normal API path request cannot opt itself into the internal replay.
        normal=policy.find_path(snapshot,SRC,DST,site='lab',analysis='flow',protocol='tcp',source_port=40000,destination_port=50001)
        self.assertEqual(normal['status'],'incomplete')

    def test_html_report_escapes_operator_content(self):
        from aiops_agent.firewall_report import render
        review=dict(id='x',device='<script>bad</script>',device_id='device',actor='<img src=x onerror=bad>',reason='&',created_at=100,
                    version={'id':'v','last_observed_at':100},context='vsys1',kind='security',rule_name='allow',operation='disable',values=[],
                    before_summary={'findings':1},after_summary={'findings':0},introduced=[],resolved=[],flow_impact=None,diff='<script>diff</script>',limitations=[])
        html=render(review)
        self.assertNotIn('<script>',html);self.assertNotIn('<img ',html);self.assertIn('&lt;script&gt;',html)
