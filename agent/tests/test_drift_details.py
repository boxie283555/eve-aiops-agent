import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from aiops_agent import config_history as h
from aiops_agent.config_api import router

BASE='hostname demo\nusername admin secret 5 private-secret\nusername other secret 5 another-secret\ninterface Ethernet1\n   description before\n!\nend\n'


class DriftDetailsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'AIOPS_CONFIG_HISTORY_PATH':self.temp.name});self.env.start()
        self.device=SimpleNamespace(id='11111111-1111-4111-8111-111111111111',name='lab-demo',host='192.0.2.1',platform='arista_eos',site='lab')
        app=FastAPI();app.include_router(router(SimpleNamespace()));self.client=TestClient(app)
        with patch.object(h.time,'time',return_value=10):self.before=h.record_backup(self.device,BASE)
        h.detect_drift()
        with patch.object(h.time,'time',return_value=20):h.record_backup(self.device,BASE)
        with patch.object(h.time,'time',return_value=30):self.after=h.record_backup(self.device,BASE.replace('before','after'))
        h.detect_drift();self.drift=h.list_drifts()['records'][0]

    def tearDown(self):
        self.env.stop();self.temp.cleanup()

    def test_detail_pins_versions_and_observation_boundary_despite_later_backups(self):
        with patch.object(h.time,'time',return_value=40):h.record_backup(self.device,BASE.replace('before','after'))
        with patch.object(h.time,'time',return_value=50):h.record_backup(self.device,BASE.replace('before','later-value'))
        h.detect_drift()
        response=self.client.get('/config/drift/'+self.drift['id']+'?full=true')
        self.assertEqual(response.status_code,200)
        detail=response.json()
        self.assertEqual(detail['before']['id'],self.before['version_id'])
        self.assertEqual(detail['after']['id'],self.after['version_id'])
        self.assertEqual(detail['before']['observed_at'],20)
        self.assertEqual(detail['after']['observed_at'],30)
        self.assertNotIn('later-value',response.text)
        self.assertNotIn('private-secret',response.text)
        self.assertNotIn('another-secret',response.text)
        self.assertNotIn('<sensitive:',response.text)
        self.assertNotIn('raw_file',response.text)
        self.assertIn('<redacted>',response.text)
        self.assertEqual(sum(r['context']=='<redacted>' for r in detail['comparison']),2)
        self.assertTrue(any(r['context']=='interface Ethernet1' for r in detail['comparison']))

    def test_filter_by_name_or_id_pagination_and_empty_scope(self):
        with patch.object(h.time,'time',return_value=40):h.record_backup(self.device,BASE.replace('before','another'),'test','change')
        h.detect_drift()
        for device in [self.device.name,self.device.id]:
            j=self.client.get('/config/drift',params={'device':device,'origin':'observed','status':'open','unowned':'true'}).json()
            self.assertEqual(j['total'],1);self.assertEqual(j['records'][0]['id'],self.drift['id'])
        self.assertEqual(self.client.get('/config/drift?device=not-a-device').json()['total'],0)
        a=self.client.get('/config/drift?limit=1&offset=0').json()
        b=self.client.get('/config/drift?limit=1&offset=1').json()
        self.assertEqual(a['total'],2);self.assertNotEqual(a['records'][0]['id'],b['records'][0]['id'])
        self.assertEqual(self.client.get('/config/drift?offset=-1').status_code,422)

    def test_resolution_reference_is_audited_without_rewriting_ownership(self):
        path='/config/drift/'+self.drift['id']
        self.assertEqual(self.client.post(path+'/resolve',json={'actor':' ','reason':'checked'}).status_code,400)
        r=self.client.post(path+'/resolve',json={'actor':'reviewer','reason':'Confirmed planned maintenance','reference':'CHG-42'})
        self.assertEqual(r.status_code,200)
        detail=self.client.get(path).json()
        self.assertTrue(detail['unowned']);self.assertIsNone(detail['change_ref'])
        self.assertEqual(detail['status'],'resolved');self.assertIn('CHG-42',detail['resolution'])
        self.client.post(path+'/resolve',json={'actor':'other','reason':'must not overwrite'})
        audit=self.client.get('/config/audit').json()['records']
        self.assertEqual(len(audit),1);self.assertEqual(audit[0]['action'],'drift_resolve')
        self.assertEqual(audit[0]['details']['reference'],'CHG-42')
        self.assertEqual(self.client.get(path).json()['resolved_by'],'reviewer')
        self.assertEqual(self.client.get('/config/drift?status=open').json()['total'],0)

    def test_missing_or_corrupt_evidence_is_explicit(self):
        self.assertEqual(self.client.get('/config/drift/not-found').status_code,404)
        with h.connect() as db:
            row=db.execute('SELECT model_file FROM versions WHERE id=?',(self.before['version_id'],)).fetchone()
        (Path(self.temp.name)/row['model_file']).write_text('broken')
        self.assertEqual(self.client.get('/config/drift/'+self.drift['id']+'?full=true').status_code,409)
        self.assertEqual(self.client.get('/config/drift/'+self.drift['id']).status_code,200)
        with h.connect() as db:db.execute('DELETE FROM versions WHERE id=?',(self.before['version_id'],))
        self.assertEqual(self.client.get('/config/drift/'+self.drift['id']).status_code,409)

    def test_detail_page_is_static_and_bilingual(self):
        response=self.client.get('/config/drift/ui?device=lab-demo&lang=en')
        self.assertEqual(response.status_code,200)
        self.assertIn('Configuration changes',response.text)
        self.assertIn('配置变化详情',response.text)
