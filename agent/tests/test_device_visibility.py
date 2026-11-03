import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from aiops_agent.monitoring import router, EvidenceReader, expressions
from aiops_agent.lifecycle import operational_status, prometheus_inventory
from test_lifecycle import device
from test_monitoring import fixture

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts/grafana'))
from device_visibility import inject, MATCH


class VisibilityTests(unittest.TestCase):
    def test_visibility_does_not_change_collection_policy_and_includes_maintenance(self):
        for status in ['Active','Failed','Maintenance','ACTIVE']:
            self.assertTrue(operational_status(status))
        for status in ['Offline','Planned','Inventory','Retired','Staged','Custom']:
            self.assertFalse(operational_status(status))
        text=prometheus_inventory([device(status='Offline'),device('active',monitoring_enabled=False)]).decode()
        self.assertIn('lifecycle_group="non_operational"',text)
        self.assertIn('lifecycle_group="operational"',text)
        self.assertNotIn('password',text)

    def test_api_scope_options_drift_and_hidden_selection(self):
        records,health,evidence=fixture()
        active={**records[0],'status':'Active'}
        offline={**records[0],'name':'offline','id':'offline','status':'Offline','monitoring_enabled':False,'health_included':False}
        maintenance={**records[0],'name':'maintenance','id':'maintenance','status':'Maintenance','health_included':False}
        app=FastAPI();app.include_router(router(SimpleNamespace(),lambda:health));client=TestClient(app)
        with patch('aiops_agent.monitoring.inventory_records',return_value=[active,offline,maintenance]),patch.object(EvidenceReader,'read',return_value=evidence) as reader,patch('aiops_agent.monitoring.live_status',return_value={'available':False,'total':None,'watch_devices':[],'devices':[]}) as live:
            result=client.get('/api/monitor').json()
            self.assertEqual(result['summary']['total'],2)
            self.assertEqual(result['hidden_devices'],1)
            self.assertEqual({x['name'] for x in result['options']['devices']},{active['name'],'maintenance'})
            self.assertNotIn('offline',reader.call_args.args[-1])
            result=client.get('/api/monitor?include_inactive=true').json()
            self.assertEqual(result['summary']['total'],3)
            self.assertIn('offline',reader.call_args.args[-1])
            result=client.get('/api/monitor?device=offline').json()
            self.assertEqual(result['device'],'all')
            client.get('/api/monitor/drift')
            self.assertNotIn('offline',[r['name'] for r in live.call_args.args[0]])
            client.get('/api/monitor/drift?include_inactive=true')
            self.assertIn('offline',[r['name'] for r in live.call_args.args[0]])

    def test_empty_and_escaped_history_scope(self):
        _,trend=expressions('all','all',())
        self.assertIn('__aiops_no_devices__',trend)
        _,trend=expressions('all','all',('leaf.1','leaf+2'))
        self.assertIn('leaf\\\\.1',trend)
        self.assertIn('leaf\\\\+2',trend)

    def test_grafana_scopes_aggregates_and_is_idempotent(self):
        d={'templating':{'list':[{'name':'device','includeAll':True,'multi':True,'allValue':'.*'}]},'panels':[{'targets':[{'expr':'sum(aiops_device_up{device=~"$device"})'}]}]}
        result=inject(d)
        self.assertEqual(inject(result),result)
        expr=result['panels'][0]['targets'][0]['expr']
        self.assertIn(MATCH,expr)
        self.assertTrue(expr.startswith('sum('))
        var=next(v for v in result['templating']['list'] if v['name']=='device')
        self.assertIsNone(var['allValue'])
        self.assertIn('query_result(',var['definition'])
        self.assertIn('__aiops_no_visible_device__',result['templating']['list'][1]['definition'])
