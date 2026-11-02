import asyncio
import copy
import time
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from aiops_agent.monitoring import summarize, expressions, EvidenceReader, router


def sample(metric,value,device='lab-switch',**labels):
    return {'metric':{'__name__':metric,**({'device':device} if device else {}),**labels},'value':[1000,str(value)]}


def fixture():
    records=[dict(id='device-1',name='lab-switch',host='192.0.2.10',site='lab',manufacturer='Arista',device_type='Switch',
                  collected_at=datetime.fromtimestamp(1000,timezone.utc).isoformat(),collection_ok=True,reason=None)]
    health={'devices':[dict(device='lab-switch',score=80,coverage_percent=90,deduction_points=10,unassessed_points=10)]}
    evidence=dict(start=900,end=1020,step=30,errors=[],current=[sample('aiops_device_up',1),sample('aiops_collect_success',1)],
                  global_=[])
    evidence['global']=[sample('up',1,None),sample('aiops_config_history_available',1,None)]
    evidence['trend']=[]
    return records,health,evidence


class SummaryTests(unittest.TestCase):
    def test_missing_is_not_zero_but_observed_zero_is_preserved(self):
        r,h,e=fixture();a=summarize(r,h,e,now=1030)
        self.assertIsNone(a['summary']['drift']);self.assertIsNone(a['summary']['backup_failures'])
        self.assertIsNone(a['devices'][0]['cpu']);self.assertEqual(a['summary']['score'],80)
        e['current'] += [sample('aiops_device_cpu_utilization_percent',0),sample('aiops_config_backup_success',1),
                         sample('aiops_config_drift_records',0,origin='observed',status='open',unowned='true')]
        a=summarize(r,h,e,now=1030)
        self.assertEqual(a['devices'][0]['cpu'],0);self.assertEqual(a['summary']['drift'],0)
        self.assertEqual(a['summary']['backup_failures'],0)

    def test_source_failure_and_stale_collection_do_not_show_current_health(self):
        r,h,e=fixture();e['current'].append(sample('aiops_device_cpu_utilization_percent',20))
        e['global'][0]=sample('up',0,None)
        a=summarize(r,h,e,now=1030)
        self.assertIsNone(a['summary']['online']);self.assertIsNone(a['summary']['score'])
        self.assertIsNone(a['devices'][0]['cpu']);self.assertEqual(a['devices'][0]['state'],'unknown')
        e['global'][0]=sample('up',1,None)
        a=summarize(r,h,e,now=1800)
        self.assertEqual(a['devices'][0]['state'],'unknown');self.assertIsNone(a['devices'][0]['cpu'])

    def test_failed_collection_not_labeled_offline_and_drift_has_device(self):
        r,h,e=fixture();r[0].update(collection_ok=False,reason='authentication');e['current'][0]=sample('aiops_device_up',0)
        e['current'] += [sample('aiops_config_backup_success',0),sample('aiops_config_backup_failure_info',1,reason='timeout'),
                         sample('aiops_config_drift_records',3,origin='observed',status='open',unowned='true'),
                         sample('aiops_config_drift_records',9,origin='exercise',status='open',unowned='true')]
        a=summarize(r,h,e,now=1030)
        self.assertEqual(a['summary']['failed'],1);self.assertEqual(a['summary']['drift'],3)
        self.assertEqual({x['kind'] for x in a['issues']},{'collection','backup','drift'})
        self.assertTrue(all(x['device']=='lab-switch' for x in a['issues']))
        e['global'][1]=sample('aiops_config_history_available',0,None)
        a=summarize(r,h,e,now=1030);self.assertIsNone(a['summary']['drift']);self.assertIsNone(a['summary']['backup_failures'])

    def test_pa_rates_override_generic_and_invalid_tcam_is_not_clamped(self):
        r,h,e=fixture();e['current'] += [sample('aiops_interface_rx_bps',100,interface='eth1'),
            sample('aiops_pa_interface_rx_bps',50,interface='eth1'),sample('aiops_interface_oper_up',0,interface='eth1'),
            sample('aiops_interface_admin_up',1,interface='eth1'),sample('aiops_asic_tcam_resource_utilization_percent',200,resource='x',region='y')]
        a=summarize(r,h,e,now=1030)
        self.assertEqual(a['interfaces'][0]['rx_bps'],50);self.assertEqual(len(a['interfaces']),1)
        self.assertEqual(a['summary']['interface_review'],1);self.assertIsNone(a['devices'][0]['tcam'])
        self.assertEqual(a['devices'][0]['invalid_capacity'],1)

    def test_chart_gaps_nonfinite_samples_and_all_unknown_health(self):
        r,h,e=fixture();e['trend']=[{'metric':{'series':'rx'},'values':[[900,'0'],[960,'NaN'],[1020,'10']]}]
        h['devices'][0].update(coverage_percent=0,score=0)
        a=summarize(r,h,e,now=1030)
        self.assertEqual(a['trend']['series']['rx'],[0,None,None,None,10])
        self.assertIsNone(a['summary']['score'])

    def test_scope_is_exact_and_quoted(self):
        current,trend=expressions('lab','a"} or up{')
        self.assertIn('device="a\\"} or up{"',current)
        self.assertIn('site="lab"',current);self.assertNotIn('device=~',current)
        self.assertIn('or on(device,interface)',trend);self.assertIn('up{job="eve-aiops-agent"} == 1',trend)

    def test_retained_optional_gauge_cannot_override_failed_dataset(self):
        r,h,e=fixture()
        r[0]['valid_metrics']=['aiops_device_up','aiops_collect_success']
        e['current'].append(sample('aiops_device_cpu_utilization_percent',10))
        a=summarize(r,h,e,now=1030)
        self.assertEqual(a['devices'][0]['state'],'ok')
        self.assertIsNone(a['devices'][0]['cpu'])
        self.assertNotIn('valid_metrics',a['devices'][0])


class ReaderTests(unittest.IsolatedAsyncioTestCase):
    async def test_bounded_ranges_cache_and_failure_not_cached(self):
        reader=EvidenceReader()
        with patch('aiops_agent.monitoring.prom_request',return_value=[]) as prom:
            first=await reader.read('lab','all','30d');second=await reader.read('lab','all','30d')
            self.assertEqual(prom.call_count,3);self.assertLessEqual((first['end']-first['start'])/first['step'],240)
            second['current'].append('modified');self.assertEqual((await reader.read('lab','all','30d'))['current'],[])
        reader=EvidenceReader()
        with patch('aiops_agent.monitoring.prom_request',side_effect=TimeoutError('private details')) as prom:
            r=await reader.read('lab','all','1h');await reader.read('lab','all','1h')
            self.assertEqual(prom.call_count,6);self.assertEqual(set(r['errors']),{'current','global','trend'})
            self.assertNotIn('private',str(r))


class ApiTests(unittest.TestCase):
    def test_scope_validation_and_page_are_read_only(self):
        r,h,e=fixture();app=FastAPI();app.include_router(router(SimpleNamespace(),lambda:h))
        client=TestClient(app)
        with patch('aiops_agent.monitoring.inventory_records',return_value=r),patch.object(EvidenceReader,'read',return_value=e) as read:
            self.assertEqual(client.get('/api/monitor?site=missing').status_code,404)
            self.assertEqual(client.get('/api/monitor?site=lab&device=missing').status_code,404)
            self.assertEqual(client.get('/api/monitor?period=1y').status_code,422)
            read.assert_not_called()
            self.assertEqual(client.post('/api/monitor').status_code,405)
            response=client.get('/api/monitor?site=lab');self.assertEqual(response.status_code,200)
            self.assertEqual(response.json()['options']['devices'][0]['name'],'lab-switch')
            page=client.get('/monitor').text;self.assertIn('Network Pulse',page);self.assertNotIn('<iframe',page)
