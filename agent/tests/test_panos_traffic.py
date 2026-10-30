import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from prometheus_client import CollectorRegistry, generate_latest
from aiops_agent.panos_traffic import TrafficStore, TrafficCollector, parse_counters, overlay_interfaces, refresh_traffic
from aiops_agent.metrics import InterfaceRateGauge


class TrafficTests(unittest.TestCase):
    def setUp(self):
        self.device = SimpleNamespace(id='lab-pa-id', name='PA-lab', host='192.0.2.1', site='lab', platform='paloalto_panos')
        self.store = TrafficStore()
        self.root = ET.parse(Path(__file__).parent/'fixtures/pa-lab-interface-counters.xml').getroot()
        self.counters = parse_counters(self.root)

    def sample(self, counters=None, uptime=1000, mono=100):
        self.store.sample(self.device, counters or self.counters, uptime, mono, wall=1700000000+mono)

    def increased(self, delta=600):
        return {k: dict(v, rx_bytes=v['rx_bytes']+delta, tx_bytes=v['tx_bytes']+delta*2)
                for k, v in self.counters.items()}

    def test_real_lab_schema_preserves_hardware_aggregate_and_logical_subinterface(self):
        self.assertEqual(self.counters['ethernet1/1']['rx_bytes'],545476184)
        self.assertEqual(self.counters['ae1']['rx_bytes'],545476184)
        self.assertEqual(self.counters['ae1']['layer'],'hardware')
        self.assertEqual(self.counters['ae1.10']['rx_bytes'],442820825)
        self.assertEqual(self.counters['ae1.10']['layer'],'logical')
        with self.assertRaises(ValueError):parse_counters(ET.fromstring('<response><result/></response>'))

    def test_warmup_delta_and_real_zero(self):
        self.sample()
        self.assertEqual(self.store.snapshot(100)['devices'][0]['status'],'warming_up')
        self.sample(self.increased(),1060,160)
        row=self.store.snapshot(160)['devices'][0]['interfaces']['ae1']
        self.assertEqual((row['rx_bps'],row['tx_bps']),(80,160))
        self.sample(self.increased(),1120,220)
        self.assertEqual(self.store.snapshot(220)['devices'][0]['interfaces']['ae1']['rx_bps'],0)

    def test_reset_reboot_gap_identity_and_counter_layer_rebaseline(self):
        for change in ('reset','reboot','gap','host','layer'):
            with self.subTest(change=change):
                self.store=TrafficStore();self.sample()
                counters=self.increased();uptime=1060;mono=160
                if change=='reset':counters={k:dict(v,rx_bytes=1,tx_bytes=2) for k,v in counters.items()}
                if change=='reboot':uptime=50
                if change=='gap':mono=400;uptime=1300
                if change=='host':self.device.host='192.0.2.2'
                if change=='layer':counters={k:dict(v,layer='other') for k,v in counters.items()}
                self.sample(counters,uptime,mono)
                self.assertEqual(self.store.snapshot(mono)['devices'][0]['status'],'warming_up')
                self.device.host='192.0.2.1'

    def test_integer_precision(self):
        large={k:dict(v,rx_bytes=2**60,tx_bytes=2**60) for k,v in self.counters.items()}
        self.sample(large)
        self.sample({k:dict(v,rx_bytes=v['rx_bytes']+1,tx_bytes=v['tx_bytes']+1) for k,v in large.items()},1060,160)
        self.assertAlmostEqual(self.store.snapshot(160)['devices'][0]['interfaces']['ae1']['rx_bps'],8/60)

    def test_failure_stale_scope_and_interface_removal(self):
        self.sample();self.sample(self.increased(),1060,160)
        self.assertEqual(self.store.snapshot(341)['devices'][0]['interfaces'],{})
        self.store.fail(self.device,'TimeoutError')
        self.assertEqual(self.store.snapshot(160)['devices'][0]['interfaces'],{})
        self.sample(self.increased(),1120,220)
        self.assertEqual(self.store.snapshot(220)['devices'][0]['status'],'warming_up')
        self.sample({'ae1':self.increased()['ae1']},1180,280)
        self.assertEqual(set(self.store.snapshot(280)['devices'][0]['interfaces']),{'ae1'})
        self.store.fail(error='InventoryError')
        self.assertEqual(self.store.snapshot(280)['devices'][0]['interfaces'],{})
        self.store.scope([])
        self.assertEqual(self.store.snapshot(280)['devices'],[])

    def test_generic_and_pa_metrics_no_duplicates_and_expire_at_scrape(self):
        self.sample();self.sample(self.increased(),1060,160)
        with patch('aiops_agent.panos_traffic.STORE',self.store),patch('aiops_agent.panos_traffic.time.monotonic',return_value=160):
            registry=CollectorRegistry()
            gauge=InterfaceRateGauge('aiops_interface_rx_bps','RX',['device','interface'],registry=registry)
            gauge.labels('synthetic-switch','Ethernet1').set(123)
            registry.register(TrafficCollector())
            output=generate_latest(registry).decode()
            self.assertEqual(output.count('aiops_interface_rx_bps{device="PA-lab",interface="ae1"}'),1)
            self.assertIn('aiops_pa_interface_rx_bps{device="PA-lab",interface="ae1",layer="hardware"} 80.0',output)
            self.assertIn('123.0',output)
            original=[{'name':'ae1','rx_bps':None}]
            self.assertEqual(overlay_interfaces(self.device,original)[0]['rx_bps'],80)
            self.assertIsNone(original[0]['rx_bps'])
            with patch('aiops_agent.panos_traffic.time.monotonic',return_value=341):
                self.assertNotIn('aiops_interface_rx_bps{device="PA-lab"',generate_latest(registry).decode())
                self.assertIsNone(overlay_interfaces(self.device,original)[0]['rx_bps'])

    def test_scope_failure_never_opens_pa_connection(self):
        with patch('aiops_agent.panos_evidence.lab_firewalls',side_effect=ValueError),patch('aiops_agent.panos_evidence.PanosAPI') as api,patch('aiops_agent.panos_traffic.STORE',self.store):
            refresh_traffic(SimpleNamespace())
            api.assert_not_called()
            self.assertEqual(self.store.scope_error,'ValueError')

    def test_bulk_poll_uses_uptime_and_all_interfaces(self):
        with patch('aiops_agent.panos_evidence.lab_firewalls',return_value=[self.device]),patch('aiops_agent.panos_evidence.PanosAPI') as api,patch('aiops_agent.panos_traffic.STORE',self.store):
            api.return_value.command.side_effect=[ET.fromstring('<response><result><system><uptime>1 days, 00:00:00</uptime></system></result></response>'),self.root]
            refresh_traffic(SimpleNamespace())
            self.assertEqual(api.return_value.command.call_args_list[0].args,(['show','system','info'],{}))
            self.assertEqual(api.return_value.command.call_args_list[1].args,(['show','counter'],{'interface':'all'}))
            self.assertEqual(self.store.snapshot()['devices'][0]['status'],'warming_up')
