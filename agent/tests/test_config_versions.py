"""Offline backup-file visibility and exact version comparison regression tests."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
import test_config_management as fixtures
BASE,CHANGED=fixtures.BASE,fixtures.CHANGED
from aiops_agent import config_history as h, config_management as m
from aiops_agent.config_api import router


class VersionTests(unittest.TestCase):
    setUp=fixtures.ManagementTests.setUp
    tearDown=fixtures.ManagementTests.tearDown
    save=fixtures.ManagementTests.save
    def test_file_metadata_keeps_creation_and_observation_distinct(self):
        first=self.save(stamp=100);self.save(stamp=200)
        result=m.list_backups(self.device.id)
        self.assertEqual(result['total'],1)
        version=result['versions'][0]
        self.assertEqual(version['created_at'],100)
        self.assertEqual(version['last_observed_at'],200)
        self.assertEqual(version['filename'],first['version_id']+'.txt')
        self.assertTrue(version['file_available'])
        self.assertEqual(version['size_bytes'],len(BASE.encode()))
        self.assertNotIn('raw_file',version);self.assertNotIn('raw_hash',version)
        self.save(CHANGED,300)
        self.assertEqual(m.list_backups(self.device.id,1,1)['versions'][0]['id'],first['version_id'])

    def test_failed_attempt_retains_last_successful_file(self):
        first=self.save(stamp=100)
        with patch.object(h.time,'time',return_value=200),patch.object(h,'current_lab_devices',return_value=[self.device]),patch.object(h,'fetch_config',side_effect=TimeoutError('private')):
            m.tracked_backup(self.settings,[self.device.id],actor='tester')
        state=m.backup_status()['devices'][0]
        self.assertFalse(state['success']);self.assertEqual(state['attempted_at'],200)
        self.assertEqual(state['latest_backup']['id'],first['version_id'])
        self.assertEqual(state['latest_backup']['last_observed_at'],100)
        self.assertNotIn('private',json.dumps(state))

    def test_exact_versions_remain_pinned_and_support_reverse_or_same(self):
        a=self.save(stamp=100)['version_id'];b=self.save(CHANGED,200)['version_id']
        self.save(CHANGED.replace('modified','later'),300)
        result=m.compare_versions(self.device.id,a,b)
        self.assertEqual(result['mode'],'version')
        self.assertEqual(result['after']['id'],b)
        self.assertNotIn('later',json.dumps(result))
        reverse=m.compare_versions(self.device.id,b,a)
        self.assertEqual(reverse['changes'][0]['after'],result['changes'][0]['before'])
        self.assertEqual(m.compare_versions(self.device.id,a,a)['changes'],[])

    def test_cross_device_and_changed_baselines_rejected(self):
        a=self.save()['version_id']
        with self.assertRaisesRegex(ValueError,'selected device'):m.compare_versions('other',a,a)
        self.device.host='192.0.2.99';b=self.save(CHANGED,200)['version_id']
        with self.assertRaisesRegex(ValueError,'baselines'):m.compare_versions(self.device.id,a,b)
        with self.assertRaisesRegex(ValueError,'baselines'):m.compare(self.device.id,100,200)

    def test_file_preview_redacted_and_preserves_colliding_contexts(self):
        version=self.save(BASE.replace('end\n','username admin secret another-secret\nend\n'))['version_id']
        result=m.backup_preview(version)
        public=json.dumps(result)
        for secret in ['confidential','another-secret','<sensitive:']:self.assertNotIn(secret,public)
        self.assertIn('<redacted>',public)
        self.assertEqual(len([r for r in result['blocks'] if r['context']=='<redacted>']),2)

    def test_missing_raw_file_visible_and_model_integrity_enforced(self):
        first=self.save();(Path(self.temp.name)/first['raw_file']).unlink()
        self.assertFalse(m.list_backups()['versions'][0]['file_available'])
        self.assertIsNone(m.list_backups()['versions'][0]['size_bytes'])
        row,_=m.load_version(first['version_id'])
        (Path(self.temp.name)/row['model_file']).write_text('{}')
        with self.assertRaisesRegex(ValueError,'integrity'):m.backup_preview(first['version_id'])
        with self.assertRaisesRegex(ValueError,'integrity'):m.compare_versions(self.device.id,first['version_id'],first['version_id'])

    def test_http_read_endpoints_do_not_fetch_or_restore(self):
        a=self.save()['version_id'];b=self.save(CHANGED,200)['version_id']
        app=FastAPI();app.include_router(router(self.settings));client=TestClient(app)
        with patch.object(h,'fetch_config') as fetch,patch.object(m,'apply_eos_restore') as restore:
            for path in ['/backup-status','/backups?limit=1&offset=1','/backups/'+a,
                         '/compare/versions?device_id='+self.device.id+'&before_id='+a+'&after_id='+b,
                         '/compare?device_id='+self.device.id+'&before_at=100&after_at=200']:
                response=client.get('/config'+path);self.assertEqual(response.status_code,200,response.text)
            fetch.assert_not_called();restore.assert_not_called()
        self.assertEqual(client.get('/config/backups?offset=-1').status_code,422)
        self.assertEqual(client.get('/config/backups/missing').status_code,400)
        self.assertEqual(client.get('/config/compare/versions',params={'device_id':'wrong','before_id':a,'after_id':b}).status_code,400)
