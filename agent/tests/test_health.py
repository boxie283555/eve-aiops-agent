import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from aiops_agent.collector import DeviceResult
from aiops_agent.inventory import Device
from aiops_agent.parser import DeviceResourceUsage, HardwareComponent, NeighborState
from aiops_agent.health import network_health, utilization_score, prometheus_health

NOW = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)


def fixture(name='lab-switch', site='lab'):
    device = Device(name, 'switch', site, '192.0.2.1', 'arista_eos', 22, True, False, id=name)
    row = DeviceResult(device, True, 1, collected_at=NOW.isoformat(),
                       dataset_status=dict.fromkeys(['interfaces', 'version', 'processes', 'bgp_summary',
                                                    'ospf_neighbors', 'nve_peers', 'power', 'module'], True),
                       resources=DeviceResourceUsage(cpu_percent=30, memory_percent=50),
                       interfaces=[{'name': 'Ethernet1', 'admin_up': True, 'oper_up': True},
                                   {'name': 'Ethernet99', 'admin_up': True, 'oper_up': False}],
                       hardware_components=[HardwareComponent('power', '1', 'ok'), HardwareComponent('module', '1', 'ok')])
    snapshot = SimpleNamespace(observed_at=NOW.isoformat(), links=[{'type':'physical','confirmation':'confirmed',
                                  'from':name,'local_interface':'Et1','to':'other','remote_interface':'Et2'}])
    return device, row, snapshot


def score(device, row, snapshot, **kw):
    return network_health([device], {device.name:row} if row else {}, snapshot, now=NOW, **kw)


class HealthTests(unittest.TestCase):
    def test_healthy_and_idle_ports(self):
        d,r,s=fixture();p=score(d,r,s)
        self.assertEqual(p['score'],100)
        self.assertEqual(p['coverage_percent'],100)
        self.assertEqual(p['devices'][0]['state'],'assessed')

    def test_confirmed_down_port_and_shutdown(self):
        d,r,s=fixture();r.interfaces[0]['oper_up']=False
        p=score(d,r,s)
        self.assertLess(p['score'],100)
        self.assertEqual(p['coverage_percent'],100)
        r.interfaces[0]['admin_up']=False
        self.assertEqual(score(d,r,s)['score'],100)

    def test_failed_dataset_is_not_healthy_even_with_retained_values(self):
        d,r,s=fixture();r.dataset_status['interfaces']=False
        p=score(d,r,s)
        self.assertLess(p['score'],100)
        self.assertEqual(p['deduction_points'],0)
        self.assertGreater(p['unassessed_points'],0)

    def test_expired_results_and_empty_inventory(self):
        d,r,s=fixture();r.collected_at=(NOW-timedelta(seconds=601)).isoformat()
        p=score(d,r,s)
        self.assertIsNone(p['score']);self.assertEqual(p['coverage_percent'],0)
        self.assertIsNone(network_health([],{},s,now=NOW)['score'])
        r.collected_at=(NOW+timedelta(seconds=1)).isoformat()
        self.assertIsNone(score(d,r,s)['score'])

    def test_missing_device_stays_in_denominator(self):
        a,r,s=fixture();b,_,_=fixture('missing')
        p=network_health([a,b],{a.name:r},s,now=NOW)
        self.assertEqual(p['score'],50)
        self.assertEqual(p['coverage_percent'],50)
        self.assertEqual(len(p['devices']),2)

    def test_collection_failure_is_not_an_offline_verdict(self):
        d,r,s=fixture();r.ok=False
        p=score(d,r,s)
        self.assertEqual(p['score'],0)
        self.assertGreater(p['deduction_points'],0)
        self.assertEqual(p['devices'][0]['checks'][0]['reason'],'collection_failed')

    def test_resource_thresholds_and_invalid_numbers(self):
        self.assertEqual([utilization_score(v) for v in [0,70,80,90,95,100]],[100,100,80,50,20,0])
        for v in [-1,101,float('nan'),float('inf'),None]:self.assertIsNone(utilization_score(v))
        d,r,s=fixture();r.resources=DeviceResourceUsage(cpu_percent=95,memory_percent=50)
        self.assertLess(score(d,r,s)['score'],100)

    def test_protocols_empty_failed_and_ospf_two_way(self):
        d,r,s=fixture();r.ospf=[NeighborState('192.0.2.2','ospf','default',False,'2WAY/DROTHER')]
        self.assertEqual(score(d,r,s)['score'],100)
        r.bgp=[NeighborState('192.0.2.3','bgp','default',False,'Idle')]*2
        p=score(d,r,s);self.assertGreater(p['deduction_points'],0)
        bgp=next(c for c in p['devices'][0]['checks'] if c['key']=='bgp')
        self.assertEqual(len(bgp['details']),1)
        r.bgp=[];r.dataset_status['bgp_summary']=False
        p=score(d,r,s);self.assertGreater(p['unassessed_points'],0)

    def test_hardware_presence_is_not_operational_health(self):
        d,r,s=fixture();r.hardware_components[0]=HardwareComponent('power','1','present')
        p=score(d,r,s);self.assertEqual(p['deduction_points'],0);self.assertGreater(p['unassessed_points'],0)
        r.hardware_components[0]=HardwareComponent('power','1','failed')
        p=score(d,r,s);self.assertGreater(p['deduction_points'],0)

    def test_unknown_wiring_and_site_filter(self):
        d,r,s=fixture();s.links=[]
        self.assertGreater(score(d,r,s)['unassessed_points'],0)
        self.assertEqual(score(d,r,s,site='missing')['device_count'],0)
        s.observed_at=(NOW-timedelta(seconds=601)).isoformat()
        self.assertGreater(score(d,r,s)['unassessed_points'],0)

    def test_accounting_and_prometheus(self):
        d,r,s=fixture();r.interfaces[0]['oper_up']=False;r.dataset_status['module']=False
        p=score(d,r,s)
        self.assertAlmostEqual(p['score']+p['deduction_points']+p['unassessed_points'],100,places=0)
        text=prometheus_health(p).decode()
        self.assertIn('aiops_health_score{device="lab-switch",site="lab"}',text)
        self.assertIn('aiops_health_coverage_percent',text)

    def test_api_metrics_and_page_share_passive_model(self):
        from fastapi.testclient import TestClient
        with patch('aiops_agent.inventory.load_inventory',return_value=[]):
            from aiops_agent.main import app
        d,r,s=fixture();client=TestClient(app)
        r.collected_at=datetime.now(timezone.utc).isoformat();s.observed_at=r.collected_at
        with patch('aiops_agent.main.collector.devices',[d]),patch('aiops_agent.main.collector.latest_results',{d.name:r}),patch('aiops_agent.main.load_snapshot',return_value=s):
            p=client.get('/api/health/score?site=lab').json()
            self.assertEqual(p['score'],100)
            self.assertIn('aiops_health_score{device="lab-switch",site="lab"} 100.0',client.get('/metrics').text)
            self.assertIn('/api/health/score',client.get('/health').text)
            self.assertEqual(client.get('/api/inventory/status').json()['device_count'],1)
