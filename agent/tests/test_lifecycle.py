import threading
import unittest
from dataclasses import replace
from datetime import datetime,timezone
from types import SimpleNamespace
from unittest.mock import patch,Mock

from aiops_agent.inventory import Device
from aiops_agent.lifecycle import policy,can_collect
from aiops_agent.collector import Collector,DeviceResult
from aiops_agent.inventory_runtime import InventoryRuntime
from aiops_agent import metrics
from aiops_agent.health import network_health,prometheus_health
from aiops_agent.monitoring import summarize
from aiops_agent.nautobot import NautobotClient,NautobotError
from test_monitoring import fixture as monitor_fixture

NOW=datetime(2026,11,3,8,tzinfo=timezone.utc)

def device(name='lifecycle-lab',**values):
    return Device(name,'switch','lab','192.0.2.1','arista_eos',22,True,False,id=name,**values)


class LifecycleTests(unittest.TestCase):
    def test_status_policy_and_expiring_independent_controls(self):
        for status in ['Active','Failed','unknown']:
            self.assertTrue(policy(device(status=status),NOW)['health_included'])
            self.assertTrue(can_collect(device(status=status)))
        for status in ['Offline','Retired','Decommissioning','Decommissioned','Inventory','Planned','Staged']:
            self.assertFalse(policy(device(status=status),NOW)['health_included'])
            self.assertFalse(can_collect(device(status=status)))
        d=device(status='Maintenance');self.assertTrue(can_collect(d));self.assertFalse(policy(d,NOW)['health_included'])
        d=device(health_excluded=True,exclusion_until='2026-11-03T09:00:00+00:00')
        self.assertFalse(policy(d,NOW)['health_included']);self.assertTrue(can_collect(d))
        expired=replace(d,exclusion_until='2026-11-03T07:00:00+00:00')
        self.assertTrue(policy(expired,NOW)['health_included'])
        self.assertFalse(policy(replace(expired,monitoring_enabled=False),NOW)['health_included'])
        for bad in ['bad','2026-11-03T09:00:00']:
            p=policy(replace(d,exclusion_until=bad),NOW);self.assertTrue(p['health_included']);self.assertTrue(p['expiry_error'])
        self.assertFalse(can_collect(replace(device(),host='')))
        self.assertTrue(policy(replace(device(),host=''),NOW)['health_included'])

    def test_excluded_devices_are_outside_health_denominator_and_metrics(self):
        a=device('life-healthy');b=device('life-maintenance',status='Maintenance')
        ra=DeviceResult(a,True,1,collected_at=NOW.isoformat());rb=DeviceResult(b,False,1,collected_at=NOW.isoformat())
        baseline=network_health([a],{a.name:ra},None,now=NOW)
        actual=network_health([a,b],{a.name:ra,b.name:rb},None,now=NOW)
        self.assertEqual(actual['score'],baseline['score']);self.assertEqual(actual['device_count'],1)
        self.assertEqual(actual['excluded_count'],1);self.assertNotIn(b.name,prometheus_health(actual).decode())
        excluded=network_health([b],{b.name:rb},None,now=NOW)
        self.assertIsNone(excluded['score']);self.assertEqual(excluded['excluded_count'],1)
        active_failed=network_health([a,replace(b,status='Failed')],{a.name:ra,b.name:rb},None,now=NOW)
        self.assertLess(active_failed['score'],baseline['score'])

    def test_monitor_score_agrees_with_exclusion_and_paused_not_failed(self):
        records,health,evidence=monitor_fixture()
        records.append({**records[0],'id':'retired','name':'retired','monitoring_enabled':False,'health_included':False,'status':'Retired'})
        actual=summarize(records,health,evidence,now=1030)
        self.assertEqual(actual['summary']['score'],80)
        self.assertEqual(actual['summary']['health_excluded'],1)
        self.assertEqual(actual['summary']['monitored'],1)
        self.assertEqual(next(r for r in actual['devices'] if r['id']=='retired')['state'],'paused')
        self.assertFalse(any(r['kind']=='collection' and r['id']=='retired' for r in actual['issues']))


class ReloadTests(unittest.TestCase):
    def setUp(self):
        self.a=device('reload-lab-a');self.b=device('reload-lab-b')
        self.collector=Collector(SimpleNamespace(max_parallel_devices=1),[self.a,self.b])
        self.runtime=InventoryRuntime(self.collector,{'configured_source':'nautobot','effective_source':'nautobot','loaded_at':'2026-11-03T08:00:00+00:00'})
        self.addCleanup(lambda:[metrics.remove_device(n) for n in ['reload-lab-a','reload-lab-b','renamed','new-lab']])

    def refresh(self,devices):
        with patch('aiops_agent.inventory_runtime.load_inventory',return_value=devices):return self.runtime.refresh()

    def test_add_remove_rename_and_connection_changes_purge_old_results_metrics(self):
        self.collector.latest_results={self.a.name:DeviceResult(self.a,True,1),self.b.name:DeviceResult(self.b,True,1)}
        metrics.DEVICE_UP.labels(self.a.name,self.a.host,self.a.role,self.a.site).set(1)
        renamed=replace(self.a,name='renamed');new=device('new-lab')
        result=self.refresh([renamed,new]);self.assertEqual(result['device_count'],2)
        self.assertEqual(set(result['last_change']['removed']),{self.b.name})
        self.assertEqual(set(result['last_change']['added']),{'new-lab'})
        self.assertEqual(self.collector.latest_results,{})
        self.assertFalse(any(s.labels.get('device')==self.a.name for f in metrics.DEVICE_UP.collect() for s in f.samples))
        self.collector.latest_results={'renamed':DeviceResult(renamed,True,1)}
        self.refresh([replace(renamed,host='192.0.2.9'),new]);self.assertEqual(self.collector.latest_results,{})

    def test_maintenance_preserves_collection_but_offline_clears_it(self):
        self.collector.latest_results={self.a.name:DeviceResult(self.a,True,1)}
        self.refresh([replace(self.a,status='Maintenance'),self.b])
        self.assertIn(self.a.name,self.collector.latest_results)
        self.assertEqual(self.collector.latest_results[self.a.name].device.status,'Maintenance')
        self.refresh([replace(self.a,status='Offline'),self.b])
        self.assertNotIn(self.a.name,self.collector.latest_results)

    def test_failed_or_invalid_refresh_retains_valid_snapshot_and_sanitizes_error(self):
        before=self.collector.snapshot()
        with patch('aiops_agent.inventory_runtime.load_inventory',side_effect=RuntimeError('private-secret')) as load:
            result=self.runtime.refresh();load.assert_called_once_with(allow_fallback=False)
        self.assertEqual(result['status'],'error');self.assertNotIn('private-secret',str(result))
        self.assertEqual(self.collector.snapshot(),before)
        result=self.refresh([self.a,self.a]);self.assertEqual(result['status'],'error')
        self.assertEqual(self.collector.snapshot(),before)
        self.assertEqual(self.refresh([self.a,self.b])['status'],'ok')
        self.assertEqual(self.refresh([])['device_count'],0)

    def test_inflight_retired_and_queued_removed_devices_cannot_reappear(self):
        entered=threading.Event();release=threading.Event();calls=[]
        def collect(d):
            calls.append(d.name);entered.set();release.wait(2);return DeviceResult(d,True,1)
        self.collector.collect_device=collect;self.collector._publish_metrics=Mock()
        thread=threading.Thread(target=self.collector.collect_all);thread.start()
        try:
            self.assertTrue(entered.wait(1))
            self.refresh([replace(self.a,status='Retired')])
        finally:release.set();thread.join(3)
        self.assertFalse(thread.is_alive());self.assertEqual(calls,[self.a.name])
        self.assertEqual(self.collector.latest_results,{})
        self.collector._publish_metrics.assert_not_called()


class NautobotLifecycleTests(unittest.TestCase):
    def test_pagination_and_controls_without_ip_and_incomplete_response(self):
        client=NautobotClient('http://test','synthetic')
        first={'id':'one','name':'one','status':{'name':'Maintenance'},'custom_fields':{'aiops_health_excluded':True,'aiops_exclusion_reason':'change'},'primary_ip4':{'address':'192.0.2.1/24'}}
        second={'id':'two','name':'two','status':{'name':'Inventory'}}
        client._get=Mock(side_effect=[{'results':[first],'count':2,'next':'http://test/api/dcim/devices/?limit=100&offset=1'}, {'results':[second],'count':2,'next':None}])
        rows=client.devices();self.assertEqual(len(rows),2);self.assertEqual(rows[0]['status'],'Maintenance')
        self.assertTrue(rows[0]['health_excluded']);self.assertEqual(rows[1]['host'],'')
        for bad in [{'results':[first],'count':2},{'count':0},{'results':[first,first],'count':2}]:
            client._get=Mock(return_value=bad)
            with self.assertRaises(NautobotError):client.devices()
