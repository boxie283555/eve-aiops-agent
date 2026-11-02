import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from apscheduler.schedulers.background import BackgroundScheduler
from aiops_agent import config_history as h, config_management as m
from aiops_agent.config_api import router

BASE='hostname p1-host\ninterface Ethernet1\n   description original\nusername admin secret confidential\nend\n'
CHANGED=BASE.replace('description original','description modified')


class ManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'AIOPS_CONFIG_HISTORY_PATH':self.temp.name,'AIOPS_CONFIG_RESTORE_DEVICES':'p1-host'})
        self.env.start()
        self.device=SimpleNamespace(id='11111111-1111-4111-8111-111111111111',name='p1-host',host='192.0.2.10',platform='arista_eos',site='lab')
        self.settings=SimpleNamespace()

    def tearDown(self):
        self.env.stop();self.temp.cleanup()

    def save(self,text=BASE,stamp=100):
        with patch.object(h.time,'time',return_value=stamp):return h.record_backup(self.device,text)

    def preview(self,target=None,current=CHANGED):
        target=target or self.save()['version_id']
        with patch.object(h,'current_lab_devices',return_value=[self.device]),patch.object(h,'fetch_config',return_value=current):
            return m.preview_restore(self.settings,self.device.id,target,'tester','CHG-1')

    def execute(self,plan,current=CHANGED,restored=BASE):
        with patch.object(h,'current_lab_devices',return_value=[self.device]),patch.object(h,'fetch_config',return_value=current),\
                patch.object(m,'apply_eos_restore',return_value=restored) as apply:
            result=m.execute_restore(self.settings,plan['id'],'p1-host')
            return result,apply.call_count

    def test_each_successful_observation_survives_dedup_and_time_selection(self):
        first=self.save(stamp=100);self.save(stamp=200);self.save(CHANGED,300)
        records=m.history(self.device.id)['records']
        self.assertEqual(len(records),3)
        self.assertEqual(records[1]['version_id'],first['version_id'])
        result=m.compare(self.device.id,250,350)
        self.assertEqual(result['before']['observed_at'],200)
        self.assertEqual(result['after']['observed_at'],300)
        self.assertEqual(result['changes'][0]['context'],'interface Ethernet1')
        self.assertEqual(m.compare(self.device.id,100,200)['changes'],[])
        self.assertEqual(m.history(self.device.id,150,250)['total'],1)
        self.assertEqual(len(m.history(self.device.id,limit=1,offset=1)['records']),1)
        for a,b in [(0,200),(300,100),(float('nan'),300),(100,float('inf'))]:
            with self.assertRaises(ValueError):m.compare(self.device.id,a,b)

    def test_failures_have_history_but_cannot_supply_comparison_baseline(self):
        self.save(stamp=100)
        with patch.object(h.time,'time',return_value=200),patch.object(h,'current_lab_devices',return_value=[self.device]),\
                patch.object(h,'fetch_config',side_effect=TimeoutError('secret must not escape')):
            result=m.tracked_backup(self.settings,[self.device.id],actor='tester')
        self.assertEqual(result['status'],'partial')
        self.assertEqual(m.history(self.device.id)['records'][0]['status'],'error')
        self.assertEqual(m.compare(self.device.id,100,250)['after']['observed_at'],100)
        self.assertNotIn('secret must not escape',json.dumps(m.list_audit()))

    def test_current_compare_is_forward_redacted_and_does_not_save_evidence(self):
        first=self.save(stamp=100);self.save(stamp=200)
        tables=['versions','version_observations','backup_observations','attempts','drifts','restore_previews','changes']
        def snapshot():
            with h.connect() as db:
                return {t:[tuple(r) for r in db.execute('SELECT * FROM '+t)] for t in tables}
        before=snapshot();files={p.relative_to(self.temp.name) for p in Path(self.temp.name).rglob('*')}
        current=CHANGED.replace('confidential','new-private-secret')
        with patch.object(h,'current_lab_devices',return_value=[self.device]),\
                patch.object(h,'fetch_config',return_value=current) as fetch,\
                patch.object(h,'record_backup') as record,patch.object(h,'detect_drift') as detect,\
                patch.object(m,'apply_eos_restore') as apply:
            result=m.compare_current(self.settings,self.device.id,first['version_id'],'tester')
            fetch.assert_called_once_with(self.device,self.settings)
            record.assert_not_called();detect.assert_not_called();apply.assert_not_called()
        self.assertEqual(result['before']['created_at'],100)
        self.assertEqual(result['before']['observed_at'],200)
        self.assertGreaterEqual(result['after']['observed_at'],result['after']['read_started_at'])
        change=next(c for c in result['changes'] if c['context']=='interface Ethernet1')
        self.assertIn('original',json.dumps(change['before']));self.assertIn('modified',json.dumps(change['after']))
        self.assertFalse(result['persisted']);self.assertIn('<redacted>',json.dumps(result))
        self.assertNotIn('confidential',json.dumps(result));self.assertNotIn('new-private-secret',json.dumps(result))
        self.assertEqual(before,snapshot())
        self.assertEqual(files,{p.relative_to(self.temp.name) for p in Path(self.temp.name).rglob('*')})
        audit=m.list_audit()['records'][0]
        self.assertEqual(audit['action'],'compare_current');self.assertEqual(audit['status'],'ok')
        self.assertNotIn('description',json.dumps(audit));self.assertNotIn('new-private-secret',json.dumps(audit))

    def test_current_compare_scope_identity_corruption_and_lock_block_device_reads(self):
        first=self.save()
        with patch.object(h,'fetch_config') as fetch:
            with patch.object(h,'current_lab_devices',side_effect=ValueError('outside lab')):
                with self.assertRaises(ValueError):m.compare_current(self.settings,self.device.id,first['version_id'],'tester')
            with patch.object(h,'current_lab_devices',return_value=[self.device]):
                self.device.host='192.0.2.99'
                with self.assertRaises(ValueError):m.compare_current(self.settings,self.device.id,first['version_id'],'tester')
                self.device.host='192.0.2.10'
                (Path(self.temp.name)/first['raw_file']).write_text('corrupt')
                with self.assertRaises(ValueError):m.compare_current(self.settings,self.device.id,first['version_id'],'tester')
            h.BACKUP_LOCK.acquire()
            try:
                with self.assertRaisesRegex(ValueError,'already running'):
                    m.compare_current(self.settings,self.device.id,first['version_id'],'tester')
            finally:h.BACKUP_LOCK.release()
            fetch.assert_not_called()
        self.assertFalse(h.BACKUP_LOCK.locked())

    def test_current_compare_api_noop_failure_redaction_and_panos(self):
        first=self.save();app=FastAPI();app.include_router(router(self.settings));client=TestClient(app)
        body={'device':self.device.id,'version_id':first['version_id'],'actor':'tester'}
        with patch.object(h,'current_lab_devices',return_value=[self.device]):
            with patch.object(h,'fetch_config',return_value=BASE):
                result=client.post('/config/compare/current',json=body)
                self.assertEqual(result.status_code,200);self.assertEqual(result.json()['changes'],[])
            for error in [TimeoutError('private-secret'),ValueError('private-secret')]:
                with patch.object(h,'fetch_config',side_effect=error):
                    result=client.post('/config/compare/current',json=body)
                    self.assertIn(result.status_code,[409,502]);self.assertNotIn('private-secret',result.text)
                    self.assertNotIn('private-secret',json.dumps(m.list_audit()))
                    self.assertFalse(h.BACKUP_LOCK.locked())
            self.device.platform='paloalto_panos'
            xml='<config><shared><address><entry name="test"><ip-netmask>192.0.2.1</ip-netmask></entry></address></shared></config>'
            target=self.save(xml)
            with patch.object(h,'fetch_config',return_value=xml.replace('192.0.2.1','192.0.2.2')):
                result=m.compare_current(self.settings,self.device.id,target['version_id'],'tester')
                self.assertTrue(result['changes']);self.assertEqual(result['mode'],'current')
        self.assertEqual(client.get('/config/compare/current').status_code,405)
        self.assertEqual(client.post('/config/compare/current',json={}).status_code,422)

    def test_legacy_migration_uses_only_known_times_and_is_idempotent(self):
        self.save(stamp=100);self.save(stamp=200)
        with h.connect() as db:
            db.execute('DROP TABLE backup_observations')
            db.execute("DELETE FROM config_settings WHERE key='observations_migrated'")
        for _ in range(2):
            rows=m.history(self.device.id)['records']
            self.assertEqual([r['observed_at'] for r in rows],[200,100])
            self.assertTrue(all(r['source']=='legacy' for r in rows))

    def test_schedule_is_persistent_validated_and_updates_cron_job(self):
        plan={'enabled':True,'time':'03:45','timezone':'Asia/Shanghai','devices':[self.device.id]}
        with patch.object(h,'current_lab_devices',return_value=[self.device]):
            saved=m.save_schedule(self.settings,plan,'tester')
        self.assertEqual(m.get_schedule(),plan);self.assertIn('+08:00',saved['next_run_at'])
        scheduler=BackgroundScheduler();scheduler.start(paused=True)
        try:
            m.apply_daily_schedule(scheduler,lambda:None)
            job=scheduler.get_job('config_backup')
            self.assertEqual(job.next_run_time.hour,3);self.assertEqual(job.next_run_time.minute,45)
            with patch.object(h,'current_lab_devices',return_value=[self.device]):
                m.save_schedule(self.settings,dict(plan,enabled=False),'tester')
            m.apply_daily_schedule(scheduler,lambda:None)
            self.assertIsNone(scheduler.get_job('config_backup'))
        finally:scheduler.shutdown()
        for change in [{'time':'25:00'},{'time':'2:00'},{'timezone':'Not/A_Zone'}]:
            with self.assertRaises(ValueError):m.schedule_trigger(dict(plan,**change))
        with patch.object(h,'current_lab_devices',side_effect=ValueError('outside lab')):
            with self.assertRaises(ValueError):m.save_schedule(self.settings,plan,'tester')
        self.assertEqual(len(m.list_audit()['records']),2)

    def test_preview_does_not_apply_and_redacts_sensitive_differences(self):
        first=self.save(BASE.replace('confidential','different-secret'))
        with patch.object(m,'apply_eos_restore') as apply:
            plan=self.preview(first['version_id'],BASE)
            apply.assert_not_called()
        public=json.dumps(plan)
        self.assertNotIn('confidential',public);self.assertNotIn('different-secret',public)
        self.assertIn('<redacted>',public);self.assertTrue(plan['execution_supported'])
        self.assertEqual(m.list_audit()['records'][0]['action'],'restore_preview')

    def test_execution_takes_safety_backup_registers_restore_and_is_single_use(self):
        plan=self.preview()
        result,calls=self.execute(plan)
        self.assertEqual(calls,1);self.assertTrue(result['verified'])
        observations=m.history(self.device.id)['records']
        self.assertEqual(observations[0]['source'],'post_restore')
        self.assertEqual(observations[1]['source'],'pre_restore')
        drift=next(r for r in h.list_drifts()['records'] if r['after_id']==result['restored_version_id'])
        self.assertFalse(drift['unowned']);self.assertEqual(drift['change_ref'],'restore-'+plan['id'])
        with self.assertRaises(ValueError):self.execute(plan)
        self.assertEqual(m.list_audit()['records'][0]['status'],'ok')

    def test_noop_restore_never_applies_configuration(self):
        plan=self.preview(current=BASE)
        result,calls=self.execute(plan,current=BASE)
        self.assertEqual(result['status'],'unchanged');self.assertEqual(calls,0)

    def test_completed_restore_registration_cannot_own_later_changes(self):
        plan=self.preview();self.execute(plan)
        with h.connect() as db:
            end=db.execute('SELECT ends_at FROM changes WHERE id=?',('restore-'+plan['id'],)).fetchone()[0]
        self.save(CHANGED,stamp=end+1);h.detect_drift()
        self.assertTrue(h.list_drifts()['records'][0]['unowned'])

    def test_stale_preview_cannot_overwrite_new_configuration(self):
        plan=self.preview()
        with patch.object(m,'apply_eos_restore') as apply,patch.object(h,'current_lab_devices',return_value=[self.device]),\
                patch.object(h,'fetch_config',return_value=CHANGED.replace('modified','newer')):
            with self.assertRaisesRegex(ValueError,'changed since preview'):m.execute_restore(self.settings,plan['id'],'p1-host')
            apply.assert_not_called()
        self.assertEqual(m.list_audit()['records'][0]['status'],'error')
        self.assertFalse(h.BACKUP_LOCK.locked())

    def test_expired_and_wrong_confirmation_previews_cannot_execute(self):
        plan=self.preview()
        with patch.object(m,'apply_eos_restore') as apply:
            with self.assertRaisesRegex(ValueError,'exact device'):m.execute_restore(self.settings,plan['id'],'wrong')
            with patch.object(h.time,'time',return_value=plan['expires_at']+1):
                with self.assertRaisesRegex(ValueError,'expired'):m.execute_restore(self.settings,plan['id'],'p1-host')
            apply.assert_not_called()

    def test_scope_and_identity_are_rechecked_before_execution(self):
        plan=self.preview()
        with patch.object(h,'current_lab_devices',side_effect=ValueError('outside lab')),patch.object(h,'fetch_config') as fetch:
            with self.assertRaises(ValueError):m.execute_restore(self.settings,plan['id'],'p1-host')
            fetch.assert_not_called()
        self.assertFalse(h.BACKUP_LOCK.locked())
        with patch.object(h,'current_lab_devices',return_value=[self.device]):
            self.device.host='192.0.2.99'
            with self.assertRaisesRegex(ValueError,'identity'):m.preview_restore(self.settings,self.device.id,plan['target']['id'],'tester','CHG')

    def test_nonpilot_preview_is_available_but_execution_is_blocked(self):
        self.device.name='other-lab';plan=self.preview()
        self.assertFalse(plan['execution_supported'])
        with patch.object(h,'current_lab_devices',return_value=[self.device]),patch.object(m,'apply_eos_restore') as apply:
            with self.assertRaisesRegex(ValueError,'pilot'):m.execute_restore(self.settings,plan['id'],'other-lab')
            apply.assert_not_called()

    def test_corrupt_raw_backup_is_rejected_before_device_fetch(self):
        first=self.save();(Path(self.temp.name)/first['raw_file']).write_text(BASE+'malicious')
        with patch.object(h,'current_lab_devices',return_value=[self.device]),patch.object(h,'fetch_config') as fetch:
            with self.assertRaisesRegex(ValueError,'integrity'):m.preview_restore(self.settings,self.device.id,first['version_id'],'tester','CHG')
            fetch.assert_not_called()

    def test_recovery_failure_is_audited_as_requiring_inspection_not_rollback_success(self):
        plan=self.preview()
        def fail(*args):
            args[-1]('timed_commit',True)
            raise TimeoutError('private command')
        with patch.object(h,'current_lab_devices',return_value=[self.device]),patch.object(h,'fetch_config',return_value=CHANGED),\
                patch.object(m,'apply_eos_restore',side_effect=fail):
            with self.assertRaises(TimeoutError):m.execute_restore(self.settings,plan['id'],'p1-host')
        record=m.list_audit()['records'][0]
        self.assertEqual(record['status'],'verification_required')
        self.assertNotIn('private command',json.dumps(record));self.assertIn('safety_version_id',record['details'])
        self.assertFalse(h.BACKUP_LOCK.locked())

    def test_restore_token_protects_execution_and_is_private_persistent(self):
        token=m.restore_token();self.assertTrue(m.authorize_restore(token));self.assertFalse(m.authorize_restore('wrong'))
        self.assertEqual(m.restore_token(),token)
        self.assertEqual((Path(self.temp.name)/'restore-token').stat().st_mode&0o777,0o600)
        app=FastAPI();app.include_router(router(self.settings));client=TestClient(app)
        with patch.object(m,'execute_restore',return_value={'status':'unchanged'}) as execute:
            body={'preview_id':'x','confirm_device':'p1-host'}
            self.assertEqual(client.post('/config/restore/execute',json=body).status_code,403);execute.assert_not_called()
            self.assertEqual(client.post('/config/restore/execute',json=body,headers={'X-Config-Restore-Token':token}).status_code,200)
        self.assertNotIn(token,json.dumps(m.list_audit()))

    def test_api_history_compare_schedule_page_and_audit(self):
        self.save(stamp=100);self.save(CHANGED,200)
        app=FastAPI();callback=MagicMock();app.include_router(router(self.settings,callback));client=TestClient(app)
        self.assertEqual(client.get('/config/ui').status_code,200)
        self.assertEqual(client.get('/config/history',params={'device_id':self.device.id}).json()['total'],2)
        self.assertEqual(client.get('/config/compare',params={'device_id':self.device.id,'before_at':100,'after_at':200}).status_code,200)
        with patch.object(h,'current_lab_devices',return_value=[self.device]):
            response=client.put('/config/schedule',json={**m.DEFAULT_SCHEDULE,'actor':'tester'})
            self.assertEqual(response.status_code,200);callback.assert_called_once()
            self.assertEqual(client.get('/config/devices').json()['devices'][0]['name'],'p1-host')
        self.assertEqual(client.get('/config/audit').json()['total'],1)

    def test_eos_candidate_and_new_connection_verify_before_confirm(self):
        conn=MagicMock();verify=MagicMock();conn.__enter__.return_value=conn;verify.__enter__.return_value=verify
        commands=[]
        def command(c,text,timeout=60):
            commands.append(text)
            if text.startswith('bash sha256sum'):return h.digest(BASE)+'  file'
            if text=='show configuration sessions':
                return '' if 'configure session pending-timer commit' in commands else 'Session with pending commit timer: aiops-'+'a'*16
            if text=='show session-config':return BASE
            if text=='show running-config':return CHANGED if c is conn else BASE
            return ''
        fingerprint=h.digest(json.dumps(h.normalize_config(CHANGED,'arista_eos'),sort_keys=True,ensure_ascii=False))
        with patch.object(m,'eos_connection',side_effect=[conn,verify]),patch.object(m,'eos_command',side_effect=command),\
                patch.object(h,'current_lab_devices',return_value=[self.device]):
            final=m.apply_eos_restore(self.settings,self.device,BASE,h.normalize_config(BASE,'arista_eos'),fingerprint,'a'*32)
        self.assertEqual(final,BASE)
        self.assertLess(commands.index('commit timer 00:05:00'),commands.index('configure session pending-timer commit'))
        self.assertEqual(commands.count('show running-config'),3)
        self.assertTrue(commands[-1].startswith('bash rm -- '))
        self.assertFalse(any('startup-config' in c or 'write memory' in c for c in commands))

    def test_eos_candidate_mismatch_aborts_without_commit(self):
        conn=MagicMock();conn.__enter__.return_value=conn;commands=[]
        def command(c,text,timeout=60):
            commands.append(text)
            if text.startswith('bash sha256sum'):return h.digest(BASE)+'  file'
            return CHANGED if text=='show session-config' else ''
        with patch.object(m,'eos_connection',return_value=conn),patch.object(m,'eos_command',side_effect=command):
            with self.assertRaisesRegex(ValueError,'Candidate'):
                m.apply_eos_restore(self.settings,self.device,BASE,h.normalize_config(BASE,'arista_eos'),'x','a'*32)
        self.assertIn('abort',commands);self.assertFalse(any(c.startswith('commit') for c in commands))

    def test_truncated_transfer_is_rejected_before_candidate_loading(self):
        conn=MagicMock();conn.__enter__.return_value=conn;commands=[]
        def command(c,text,timeout=60):
            commands.append(text)
            return 'invalid-hash file' if text.startswith('bash sha256sum') else ''
        with patch.object(m,'eos_connection',return_value=conn),patch.object(m,'eos_command',side_effect=command):
            with self.assertRaisesRegex(ValueError,'Transferred'):
                m.apply_eos_restore(self.settings,self.device,BASE,h.normalize_config(BASE,'arista_eos'),'x','a'*32)
        self.assertFalse(any(c.startswith(('configure session','commit')) for c in commands))

    def test_failed_post_commit_verification_never_confirms_timer(self):
        conn=MagicMock();verify=MagicMock();conn.__enter__.return_value=conn;verify.__enter__.return_value=verify
        commands=[];progress=MagicMock()
        def command(c,text,timeout=60):
            commands.append(text)
            if text.startswith('bash sha256sum'):return h.digest(BASE)+' file'
            if text=='show session-config':return BASE
            if text=='show running-config':return CHANGED
            return ''
        fp=h.digest(json.dumps(h.normalize_config(CHANGED,'arista_eos'),sort_keys=True,ensure_ascii=False))
        with patch.object(m,'eos_connection',side_effect=[conn,verify]),patch.object(m,'eos_command',side_effect=command),\
                patch.object(h,'current_lab_devices',return_value=[self.device]):
            with self.assertRaisesRegex(ValueError,'did not verify'):
                m.apply_eos_restore(self.settings,self.device,BASE,h.normalize_config(BASE,'arista_eos'),fp,'a'*32,progress)
        self.assertIn('commit timer 00:05:00',commands);self.assertNotIn('configure session pending-timer commit',commands)
        self.assertNotIn('abort',commands);progress.assert_called_with('verify_new_connection',True)
