import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from aiops_agent import config_history as h, config_watch as w
from aiops_agent.monitoring import router, apply_live_drift

BASE='hostname lab-one\ninterface Ethernet1\n   description original\nusername admin secret private-secret\nend\n'
CHANGED=BASE.replace('description original','description changed')


class WatchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'AIOPS_CONFIG_HISTORY_PATH':self.temp.name,'AIOPS_CONFIG_WATCH_ENABLED':'true','AIOPS_CONFIG_WATCH_INTERVAL':'60'})
        self.env.start()
        self.device=SimpleNamespace(id='11111111-1111-4111-8111-111111111111',name='lab-one',host='192.0.2.1',platform='arista_eos',site='lab')
        self.settings=SimpleNamespace()

    def tearDown(self):self.env.stop();self.temp.cleanup()

    def poll(self,raw=BASE,stamp=100):
        with patch.object(h,'current_lab_devices',return_value=[self.device]),patch.object(h,'fetch_config',return_value=raw),patch.object(time,'time',return_value=stamp):
            return w.run_once(self.settings)

    def rows(self,table):
        with h.connect() as db:return [tuple(r) for r in db.execute('SELECT * FROM '+table)]

    def metadata(self):return [dict(id=self.device.id,name=self.device.name,site='lab')]

    def test_baseline_change_repeat_and_rollback_without_daily_backup(self):
        self.assertEqual(self.poll()['created'],0)
        counts={t:len(self.rows(t)) for t in ['versions','backup_observations','attempts','config_audit']}
        files=list(Path(self.temp.name).rglob('*.txt'))
        self.assertEqual(self.poll(stamp=150)['created'],0)
        self.assertEqual(counts,{t:len(self.rows(t)) for t in counts})
        self.assertEqual(files,list(Path(self.temp.name).rglob('*.txt')))
        self.assertEqual(self.poll(CHANGED,200)['created'],1)
        self.assertEqual(self.poll(CHANGED,250)['created'],0)
        self.assertEqual(self.poll(BASE,300)['created'],1)
        self.assertEqual(len(self.rows('versions')),3)
        self.assertEqual(len(self.rows('backup_observations')),3)
        self.assertEqual(self.rows('attempts'),[])
        drift=h.list_drifts(origin='observed',status='open',unowned=True)
        self.assertEqual(drift['total'],2)
        self.assertEqual(h.drift_detail(drift['records'][-1]['id'])['before']['observed_at'],150)
        self.assertNotIn('private-secret',json.dumps(drift))
        metrics=list(h.DriftCollector().collect())
        counts=next(m for m in metrics if m.name=='aiops_config_drift_records')
        self.assertTrue(any(s.value==2 and s.labels['unowned']=='true' for s in counts.samples))

    def test_watcher_does_not_clear_backup_failure_or_create_fake_backup_dates(self):
        with patch.object(time,'time',return_value=50):h.record_backup(self.device,BASE)
        with h.connect() as db:db.execute('INSERT OR REPLACE INTO attempts VALUES (?,?,?,?,?)',(self.device.id,self.device.name,80,0,'TimeoutError'))
        attempts=self.rows('attempts');observations=self.rows('backup_observations');versions=self.rows('version_observations')
        self.poll(stamp=100)
        self.assertEqual(self.rows('attempts'),attempts)
        self.assertEqual(self.rows('backup_observations'),observations)
        self.assertEqual(self.rows('version_observations'),versions)
        self.poll(CHANGED,150)
        self.assertEqual(self.rows('attempts'),attempts)

    def test_registration_uses_last_successful_watch_boundary(self):
        self.poll(stamp=100)
        with patch.object(time,'time',return_value=120):
            h.register_change('CHG-WATCH',self.device.id,'tester',120,300,['interface Ethernet1'])
        self.poll(stamp=150);self.poll(CHANGED,200)
        row=h.list_drifts()['records'][0]
        self.assertFalse(row['unowned']);self.assertEqual(row['change_ref'],'CHG-WATCH')
        self.assertEqual(w.live_status(self.metadata(),now=205)['total'],0)

    def test_failure_retains_baseline_and_recovery_detects_change(self):
        self.poll(stamp=100)
        with patch.object(h,'current_lab_devices',return_value=[self.device]),patch.object(h,'fetch_config',side_effect=TimeoutError('private-secret')),patch.object(time,'time',return_value=150):
            result=w.run_once(self.settings)
        self.assertEqual(result['status'],'partial');self.assertEqual(len(self.rows('versions')),1)
        status=w.live_status(self.metadata(),now=160)
        self.assertEqual(status['watch_devices'][0]['state'],'error')
        self.assertEqual(status['watch_devices'][0]['last_success_at'],100)
        self.assertNotIn('private-secret',json.dumps(status))
        self.assertEqual(self.poll(CHANGED,200)['created'],1)
        self.assertEqual(h.drift_detail(h.list_drifts()['records'][0]['id'])['before']['observed_at'],100)
        self.assertFalse(h.BACKUP_LOCK.locked())

    def test_scope_failure_busy_disabled_and_no_overlapping_reads(self):
        with patch.object(h,'fetch_config') as fetch:
            with patch.object(h,'current_lab_devices',side_effect=ValueError('Nautobot unavailable')):
                self.assertEqual(w.run_once(self.settings)['status'],'error')
            h.BACKUP_LOCK.acquire()
            try:self.assertEqual(w.run_once(self.settings)['status'],'busy')
            finally:h.BACKUP_LOCK.release()
            with patch.dict(os.environ,{'AIOPS_CONFIG_WATCH_ENABLED':'false'}):
                self.assertEqual(w.run_once(self.settings)['status'],'disabled')
            fetch.assert_not_called()
        self.assertFalse(h.BACKUP_LOCK.locked())

    def test_partial_failure_does_not_stop_other_devices(self):
        other=SimpleNamespace(**{**vars(self.device),'id':'22222222-2222-4222-8222-222222222222','name':'lab-two'})
        def fetch(device,settings):
            if device.id==self.device.id:raise TimeoutError('secret')
            return BASE.replace('lab-one','lab-two')
        with patch.object(h,'current_lab_devices',return_value=[self.device,other]),patch.object(h,'fetch_config',side_effect=fetch):
            result=w.run_once(self.settings)
        self.assertEqual(result['checked'],2);self.assertEqual(result['failed'],1)
        self.assertEqual(len(self.rows('versions')),1)

    def test_status_is_scoped_fresh_and_independent_of_prometheus(self):
        self.poll(stamp=100);self.poll(CHANGED,150)
        status=w.live_status(self.metadata(),now=160)
        self.assertTrue(status['available']);self.assertEqual(status['total'],1)
        self.assertEqual(status['watch_devices'][0]['state'],'ok')
        self.assertEqual(w.live_status(self.metadata(),now=400)['watch_devices'][0]['state'],'stale')
        foreign=[dict(id='other',name='nonlab',site='prod')]
        self.assertEqual(w.live_status(foreign,now=160)['watch_devices'],[])
        self.assertEqual(w.live_status(foreign,now=160)['total'],0)
        result={'source_ok':False,'devices':[dict(id=self.device.id,name='lab-one')],
                'summary':{'drift':None},'issues':[dict(kind='collection')]}
        apply_live_drift(result,status)
        self.assertEqual(result['summary']['drift'],1);self.assertEqual(result['issues'][-1]['count'],1)
        app=FastAPI();app.include_router(router(SimpleNamespace(),lambda:{}));client=TestClient(app)
        with patch('aiops_agent.monitoring.inventory_records',return_value=self.metadata()),patch.object(h,'fetch_config') as fetch:
            self.assertEqual(client.get('/api/monitor/drift?site=lab').json()['total'],1)
            self.assertEqual(client.get('/api/monitor/drift?site=prod').status_code,404)
            self.assertEqual(client.get('/api/monitor/drift?device=missing').status_code,404)
            fetch.assert_not_called()
        h.resolve_drift(h.list_drifts()['records'][0]['id'],'tester','reviewed')
        self.assertEqual(w.live_status(self.metadata(),now=160)['total'],0)

    def test_unavailable_database_is_unknown_not_zero(self):
        state=w.live_status(self.metadata())
        self.assertFalse(state['available']);self.assertIsNone(state['total'])
        self.assertFalse((Path(self.temp.name)/'history.sqlite3').exists())

    def test_panos_running_configuration_changes_are_detected(self):
        self.device.platform='paloalto_panos'
        xml='<config><shared><address><entry name="test"><ip-netmask>192.0.2.1</ip-netmask></entry></address></shared></config>'
        self.poll(xml,100)
        self.assertEqual(self.poll(xml.replace('192.0.2.1','192.0.2.2'),150)['created'],1)
