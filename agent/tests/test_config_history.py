import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from prometheus_client import CollectorRegistry, generate_latest
from aiops_agent import config_history as h

BASE='''! Command: show running-config
hostname lab-switch
username admin secret 5 confidential-value
interface Ethernet1
   description original
   no switchport
!
ip access-list BASE
   10 permit ip any any
!
router bgp 65000
   vrf LAB
      neighbor 192.0.2.1 remote-as 65001
      neighbor 192.0.2.1 description original
!
end
'''


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'AIOPS_CONFIG_HISTORY_PATH':self.temp.name});self.env.start()
        self.device=SimpleNamespace(id='11111111-1111-4111-8111-111111111111',name='lab-switch',host='192.0.2.10',platform='arista_eos',site='lab')

    def tearDown(self):
        self.env.stop();self.temp.cleanup()

    def save(self,text=BASE,exercise=None,phase='observation'):
        return h.record_backup(self.device,text,exercise,phase)

    def test_private_persistent_baseline_and_noop(self):
        first=self.save()
        raw=Path(self.temp.name)/first['raw_file']
        self.assertEqual(raw.read_text(),BASE)
        self.assertEqual(raw.stat().st_mode&0o777,0o600)
        self.assertEqual(h.detect_drift()['created'],0)
        self.assertEqual(self.save(BASE.replace('! Command: show running-config','! Time: now'))['status'],'unchanged')
        with h.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM versions').fetchone()[0],1)
        self.assertEqual(h.list_drifts()['total'],0)

    def test_three_changes_structured_drift_and_idempotent_detection(self):
        self.save();h.detect_drift()
        text=BASE
        changes=[('description original','description changed',1),('10 permit ip any any','10 deny ip host 192.0.2.100 any',1),('neighbor 192.0.2.1 remote-as 65001','neighbor 192.0.2.1 remote-as 65002',1)]
        for old,new,n in changes:
            text=text.replace(old,new,n);self.save(text,'test-1','change')
            self.assertEqual(h.detect_drift()['created'],1)
            self.assertEqual(h.detect_drift()['created'],0)
        rows=h.list_drifts(unowned=True,origin='exercise')['records']
        self.assertEqual(len(rows),3)
        self.assertEqual({r['changes'][0]['category'] for r in rows},{'interface','acl_policy','bgp'})
        self.assertEqual(h.list_drifts(origin='observed')['total'],0)
        h.resolve_drift(rows[0]['id'],'tester','restored')
        self.assertEqual(h.list_drifts(status='open')['total'],2)
        self.assertEqual(h.list_drifts(unowned=True)['total'],3)

    def test_secret_change_detected_but_not_exposed(self):
        self.save();h.detect_drift()
        self.save(BASE.replace('confidential-value','different-secret'));h.detect_drift()
        public=json.dumps(h.list_drifts())
        self.assertNotIn('confidential-value',public);self.assertNotIn('different-secret',public)
        self.assertNotIn('<sensitive:',public);self.assertIn('<redacted>',public)

    def test_order_is_preserved_inside_acl(self):
        a=BASE.replace('10 permit ip any any','10 permit ip any any\n   20 deny ip any any')
        b=a.replace('10 permit ip any any\n   20 deny ip any any','20 deny ip any any\n   10 permit ip any any')
        self.assertEqual(h.differences(h.normalize_config(a,'arista_eos'),h.normalize_config(b,'arista_eos'))[0]['category'],'acl_policy')

    def test_pa_xml_rule_order_and_redaction(self):
        a='<config><rules><entry name="first"><action>allow</action></entry><entry name="second"><action>deny</action></entry></rules><password>private-1</password></config>'
        b=a.replace('first','second').replace('private-1','private-2')
        diff=h.differences(h.normalize_config(a,'paloalto_panos'),h.normalize_config(b,'paloalto_panos'))
        self.assertTrue(diff);self.assertNotIn('private-1',json.dumps(diff));self.assertNotIn('private-2',json.dumps(diff))

    def test_failures_do_not_replace_baseline_and_host_change_is_new_baseline(self):
        self.save();h.detect_drift()
        for raw in ['','% Invalid input','hostname lab-switch\ninterface Ethernet1']:
            with self.assertRaises(ValueError):self.save(raw)
        self.assertEqual(h.list_drifts()['total'],0)
        self.device.host='192.0.2.20';self.save(BASE.replace('original','other'));h.detect_drift()
        self.assertEqual(h.list_drifts()['total'],0)

    def test_corrupt_model_not_silently_compared(self):
        first=self.save();h.detect_drift();self.save(BASE.replace('original','other'))
        with h.connect() as db:
            row=db.execute('SELECT model_file FROM versions WHERE id=?',(first['version_id'],)).fetchone()
        (Path(self.temp.name)/row['model_file']).write_text('{}')
        with self.assertRaises(ValueError):h.detect_drift()
        self.assertEqual(h.list_drifts()['total'],0)

    def test_planned_change_exact_context_and_window(self):
        now=time.time()
        h.register_change('CHG-1',self.device.id,'operator',now-1,now+300,['interface Ethernet1'])
        self.save();h.detect_drift();self.save(BASE.replace('description original','description planned',1));h.detect_drift()
        record=h.list_drifts()['records'][0]
        self.assertFalse(record['unowned']);self.assertEqual(record['change_ref'],'CHG-1')
        self.save(BASE.replace('10 permit ip any any','10 deny ip any any'));h.detect_drift()
        self.assertTrue(h.list_drifts()['records'][0]['unowned'])

    def test_retroactive_registration_cannot_approve_previous_interval(self):
        self.save();h.detect_drift();now=time.time()
        h.register_change('late',self.device.id,'operator',now-100,now+300,['interface Ethernet1'])
        self.save(BASE.replace('description original','description late',1));h.detect_drift()
        self.assertTrue(h.list_drifts()['records'][0]['unowned'])

    def save_at(self, stamp, text=BASE):
        with patch.object(h.time,'time',return_value=stamp):
            return self.save(text)

    def register_at(self, stamp, start=200, end=500, contexts=None):
        with patch.object(h.time,'time',return_value=stamp):
            return h.register_change('CHG-existing',self.device.id,'operator',start,end,
                                     contexts or ['interface Ethernet1'])

    def test_unchanged_observation_enables_registered_change_on_existing_device(self):
        first=self.save_at(100);h.detect_drift()
        self.register_at(200)
        observed=self.save_at(300)
        self.assertEqual(observed['version_id'],first['version_id'])
        self.assertEqual(observed['last_observed_at'],300)
        self.save_at(400,BASE.replace('description original','description planned',1))
        # Each operation reopens SQLite, so boundaries must survive connection/process lifetime.
        self.assertEqual(h.detect_drift()['created'],1)
        record=h.list_drifts()['records'][0]
        self.assertFalse(record['unowned']);self.assertEqual(record['change_ref'],'CHG-existing')
        with h.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM versions').fetchone()[0],2)
            self.assertEqual(db.execute('SELECT created_at FROM versions WHERE id=?',
                                       (first['version_id'],)).fetchone()[0],100)
        self.assertEqual(len(list(Path(self.temp.name).glob('*/*.json'))),2)

    def test_delayed_detection_cannot_use_post_change_registration_or_observation(self):
        self.save_at(100)
        changed=BASE.replace('description original','description changed',1)
        version=self.save_at(200,changed)
        self.register_at(250,start=100)
        self.save_at(300,changed)
        # A later rollback has the same fingerprint as the original version; it is a new version.
        self.save_at(350)
        h.detect_drift()
        records={r['after_id']:r for r in h.list_drifts()['records']}
        self.assertTrue(records[version['version_id']]['unowned'])
        self.assertIsNone(records[version['version_id']]['change_ref'])
        self.assertEqual(h.detect_drift()['created'],0)

    def test_later_unchanged_observation_does_not_extend_original_change_window(self):
        self.save_at(100);self.register_at(200)
        self.save_at(300)
        changed=BASE.replace('description original','description planned',1)
        self.save_at(400,changed)
        self.save_at(600,changed)
        h.detect_drift()
        self.assertFalse(h.list_drifts()['records'][0]['unowned'])

    def test_failed_backup_cannot_advance_successful_baseline(self):
        self.save_at(100);self.register_at(200)
        with patch.object(h.time,'time',return_value=300),\
                patch.object(h,'current_lab_devices',return_value=[self.device]),\
                patch.object(h,'fetch_config',side_effect=TimeoutError):
            self.assertEqual(h.backup(SimpleNamespace())['status'],'partial')
        self.save_at(400,BASE.replace('description original','description changed',1))
        h.detect_drift()
        self.assertTrue(h.list_drifts()['records'][0]['unowned'])

    def test_window_must_cover_both_successful_observations(self):
        for start,end in [(350,500),(200,350)]:
            with self.subTest(start=start,end=end):
                with tempfile.TemporaryDirectory() as folder,\
                        patch.dict(os.environ,{'AIOPS_CONFIG_HISTORY_PATH':folder}):
                    self.save_at(100);self.register_at(200,start=start,end=end)
                    self.save_at(300)
                    self.save_at(400,BASE.replace('description original','description changed',1))
                    h.detect_drift()
                    self.assertTrue(h.list_drifts()['records'][0]['unowned'])

    def test_renewed_baseline_still_requires_every_changed_context(self):
        self.save_at(100);self.register_at(200);self.save_at(300)
        self.save_at(400,BASE.replace('original','changed'))  # Interface AND BGP.
        h.detect_drift()
        self.assertTrue(h.list_drifts()['records'][0]['unowned'])

    def test_legacy_database_does_not_invent_successful_observations_or_rewrite_drift(self):
        self.save_at(100)
        changed=BASE.replace('description original','description changed',1)
        self.save_at(200,changed);h.detect_drift()
        existing=h.list_drifts()['records']
        self.save_at(300)
        # Simulate the old schema, with both detected history and an unchecked version.
        with h.connect() as db:db.execute('DROP TABLE version_observations')
        self.register_at(350,start=100)
        self.save_at(400)  # New successful observation of the legacy current version.
        h.detect_drift()
        rows=h.list_drifts()['records']
        self.assertEqual(next(r for r in rows if r['id']==existing[0]['id']),existing[0])
        self.assertTrue(all(r['unowned'] for r in rows))
        self.save_at(450,changed);h.detect_drift()
        self.assertFalse(h.list_drifts()['records'][0]['unowned'])

    def test_failed_collection_and_scope_do_not_create_drift(self):
        with patch.object(h,'current_lab_devices',return_value=[self.device]),patch.object(h,'fetch_config',side_effect=TimeoutError):
            result=h.backup(SimpleNamespace())
        self.assertEqual(result['status'],'partial');self.assertEqual(h.list_drifts()['total'],0)
        with patch.object(h,'current_lab_devices',side_effect=ValueError),patch.object(h,'fetch_config') as fetch:
            with self.assertRaises(ValueError):h.backup(SimpleNamespace())
            fetch.assert_not_called()
        self.assertFalse(h.BACKUP_LOCK.locked())

    def test_metrics_distinguish_exercise_and_observed_and_survive_missing_store(self):
        registry=CollectorRegistry();registry.register(h.DriftCollector())
        self.assertIn('aiops_config_history_available 0.0',generate_latest(registry).decode())
        self.save();h.detect_drift();self.save(BASE.replace('original','exercise'),'test','change');h.detect_drift()
        output=generate_latest(registry).decode()
        self.assertIn('origin="exercise",status="open",unowned="true"} 1.0',output)
        self.assertIn('origin="observed",status="open",unowned="true"} 0.0',output)

    def test_scope_rejects_nonlab_and_address_mismatch_before_connection(self):
        settings=SimpleNamespace(nautobot_url='http://example.invalid',nautobot_token='test')
        record={'id':self.device.id,'name':self.device.name,'site':'lab','host':self.device.host}
        for altered in [dict(record,site='production'),dict(record,host='192.0.2.99')]:
            with patch('aiops_agent.inventory.load_inventory',return_value=[self.device]),patch('aiops_agent.nautobot.NautobotClient') as client:
                client.return_value.devices.return_value=[altered]
                with self.assertRaises(ValueError):h.current_lab_devices(settings,[self.device.name])

    def test_problem_metrics_identify_devices_and_clear_on_recovery(self):
        registry=CollectorRegistry();registry.register(h.DriftCollector())
        self.save();h.detect_drift()
        self.save(BASE.replace('original','observed-change'));h.detect_drift()
        observed=h.list_drifts(origin='observed')['records'][0]
        self.save(BASE.replace('original','exercise-change'),'exercise','change');h.detect_drift()
        latest='aiops_config_drift_last_detected_timestamp_seconds'
        self.assertEqual(registry.get_sample_value(latest,{'device':self.device.name,'origin':'observed'}),observed['detected_at'])
        self.assertIsNotNone(registry.get_sample_value(latest,{'device':self.device.name,'origin':'exercise'}))
        with h.connect() as db:
            db.execute('INSERT OR REPLACE INTO attempts VALUES (?,?,?,?,?)',
                       (self.device.id,self.device.name,12345,0,'NetmikoTimeoutException'))
        failure='aiops_config_backup_failure_info'
        self.assertEqual(registry.get_sample_value(failure,{'device':self.device.name,'reason':'timeout'}),1)
        self.assertEqual(registry.get_sample_value('aiops_config_backup_last_attempt_timestamp_seconds',{'device':self.device.name}),12345)
        h.resolve_drift(observed['id'],'tester','handled')
        self.assertIsNone(registry.get_sample_value(latest,{'device':self.device.name,'origin':'observed'}))
        self.assertIsNotNone(registry.get_sample_value(latest,{'device':self.device.name,'origin':'exercise'}))
        self.save(BASE.replace('original','exercise-change'))
        self.assertIsNone(registry.get_sample_value(failure,{'device':self.device.name,'reason':'timeout'}))

    def test_failure_reason_labels_do_not_expose_arbitrary_errors(self):
        self.save()
        with h.connect() as db:
            db.execute('UPDATE attempts SET success=0,error=?',('password=private-error-secret',))
        registry=CollectorRegistry();registry.register(h.DriftCollector())
        text=generate_latest(registry).decode()
        self.assertNotIn('private-error-secret',text)
        self.assertEqual(registry.get_sample_value('aiops_config_backup_failure_info',{'device':self.device.name,'reason':'unknown'}),1)

    def test_api_filters_resolve_and_no_raw_configuration(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from aiops_agent.config_api import router
        app=FastAPI();app.include_router(router(SimpleNamespace()))
        client=TestClient(app)
        with patch.object(h,'current_lab_devices',return_value=[self.device]),patch.object(h,'fetch_config',return_value=BASE):
            response=client.post('/config/backup',json={'devices':['lab-switch']})
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json()['detection']['created'],0)
        self.save(BASE.replace('original','changed'),'test','change');h.detect_drift()
        rows=client.get('/config/drift?unowned=true&origin=exercise').json()
        self.assertEqual(rows['total'],1)
        self.assertEqual(client.get('/config/drift?origin=observed').json()['total'],0)
        self.assertNotIn('confidential-value',client.get('/config/backups').text)
        versions=client.get('/config/backups',params={'device_id':self.device.id}).json()['versions']
        self.assertTrue(all(v['last_observed_at']>=v['created_at'] for v in versions))
        response=client.post('/config/drift/'+rows['records'][0]['id']+'/resolve',json={'actor':'test','reason':'restored'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(client.get('/config/drift?status=open').json()['total'],0)
        self.assertEqual(client.post('/config/backup',json={'phase':'change'}).status_code,400)
