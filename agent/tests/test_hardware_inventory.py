"""Synthetic hardware outputs only; no device connections or historical captures."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch, Mock

from prometheus_client import Gauge
from aiops_agent.collector import Collector, DeviceResult
from aiops_agent.inventory import Device
from aiops_agent.parser import HardwareComponent, parse_modules, parse_power_supplies
from aiops_agent.snmp import SnmpSample

EOS = '''System information
  Model                    Description
  ------------------------ --------------------
  DCS-TEST                 Synthetic switch
  HW Version  Serial Number                    Mfg Date   Epoch
  ----------- -------------------------------- ---------- -----
  01.00       CHASSIS-TEST                     2025-01-01  01
System has 2 power supplies
  Slot  Model              Serial Number
  ----  -----------------  -------------------
  1     PWR-TEST-AC        PSU-TEST-1
  2     PWR-TEST-AC        PSU-TEST-2
System has 1 fan modules
  Module  Model            Serial Number
  1       FAN-TEST         FAN-TEST-1
System has 4 ports
  Type      Count
  Switched  4
System has 1 transceivers
  Port  Manufacturer  Model     Serial Number
  1     TEST          OPTIC-1   OPTIC-SN-1
'''
POWER = '''Power Supply Model Capacity Current Current Power Status Uptime
1 PWR-TEST-AC 500W 1.2A 8.0A 96W Ok 27 days, 12:13:14
2 PWR-TEST-AC 500W 0.0A 0.0A 0W Power Loss 00:00:00
'''
NX = '''NAME: "Chassis", DESCR: "Synthetic chassis"
PID: N9K-TEST, VID: V01, SN: CHASSIS-NX
NAME: "Slot 1", DESCR: "Ethernet module"
PID: N9K-MOD-TEST, VID: V01, SN: MODULE-NX-1
NAME: "Power Supply 1", DESCR: "AC power supply"
PID: N9K-PWR-TEST, VID: V01, SN: PSU-NX-1
NAME: "Fan 1", DESCR: "Fan module"
PID: N9K-FAN-TEST, VID: V01, SN: FAN-NX-1
'''


def device(name='synthetic-hardware'):
    return Device(name, 'switch', 'lab', '192.0.2.10', 'arista_eos', 22, True, False)


class InventoryTests(unittest.TestCase):
    def test_eos_power_slots_are_not_fan_or_transceiver_slots(self):
        parts = parse_power_supplies(POWER, inventory_output=EOS)
        self.assertEqual([(p.name, p.serial, p.status) for p in parts],
                         [('1', 'PSU-TEST-1', 'ok'), ('2', 'PSU-TEST-2', 'power_loss')])

    def test_structured_power_is_enriched_and_direct_serial_preserved(self):
        parts = parse_power_supplies('', [
            {'POWER_SUPPLY_LOCATION': 'PS1', 'POWER_SUPPLY_PID': 'PWR-TEST-AC', 'STATUS': 'Ok'},
            {'POWER_SUPPLY_LOCATION': '2', 'PID': 'PWR-TEST-AC', 'STATUS': 'Ok', 'SN': 'DIRECT-2'},
        ], EOS)
        self.assertEqual([p.serial for p in parts], ['PSU-TEST-1', 'DIRECT-2'])

    def test_nxos_module_and_psu_inventory_slots_are_separate(self):
        modules = parse_modules('', NX, [{'MODULE': '1', 'MODEL': 'N9K-MOD-TEST', 'STATUS': 'active *'}])
        power = parse_power_supplies('1 N9K-PWR-TEST 50W 500W Ok', inventory_output=NX)
        self.assertEqual((modules[0].serial, modules[0].status), ('MODULE-NX-1', 'active'))
        self.assertEqual(power[0].serial, 'PSU-NX-1')

    def test_eos_module_sections_do_not_create_mac_or_uptime_components(self):
        output = '''Module  Ports  Card Type                Model         Serial No.
1       48     Synthetic Ethernet card  MOD-TEST-1    MODULE-TEST-1
2       48     Synthetic Ethernet card  MOD-TEST-2    MODULE-TEST-2
Module  MAC addresses                  Hw  Sw
1       0000.0000.0001-0000.0000.0048   1.0  1.0
2       0000.0000.0049-0000.0000.0096   1.0  1.0
Module  Status  Uptime
1       Ok      3 days, 01:02:03
2       Failed  00:00:00
'''
        parts = parse_modules(output)
        self.assertEqual([(p.name,p.serial,p.status) for p in parts],
                         [('1','MODULE-TEST-1','ok'),('2','MODULE-TEST-2','failed')])

    def test_nxos_raw_module_status(self):
        output = 'Mod Ports Module-Type Model Status\n1 48 Synthetic Ethernet N9K-MOD-TEST active *\nMod Sw Hw\n1 1.0 2.0\n'
        parts = parse_modules(output, NX)
        self.assertEqual(len(parts), 1)
        self.assertEqual((parts[0].model,parts[0].serial), ('N9K-MOD-TEST','MODULE-NX-1'))

    def test_virtual_chassis_has_serial_and_absent_psu_has_no_fabricated_serial(self):
        inventory = EOS.split('System has')[0].replace('01.00       ', '            ')
        modules = parse_modules('% Unavailable command (not supported on this hardware platform)', inventory)
        self.assertEqual((modules[0].name,modules[0].serial), ('chassis','CHASSIS-TEST'))
        power = parse_power_supplies('% There seem to be no power supplies connected.', [{'STATUS':'no power supplies'}], EOS)
        self.assertEqual((power[0].name,power[0].serial,power[0].status), ('none','not_applicable','not_present'))

    def test_conflicting_inventory_and_mismatched_models_remain_unknown(self):
        duplicate = EOS + '\nSystem has 1 power supplies\n1 PWR-TEST-AC CONFLICT-SN\n'
        self.assertEqual(parse_power_supplies(POWER, inventory_output=duplicate)[0].serial, 'unknown')
        self.assertEqual(parse_power_supplies(POWER.replace('PWR-TEST-AC','OTHER-MODEL'), inventory_output=EOS)[0].serial, 'unknown')

    def test_bare_structured_slot_cannot_supply_an_unrelated_serial(self):
        power = parse_power_supplies(POWER, inventory_structured=[{'PORT':'1','PID':'SFP-TEST','SN':'OPTIC-SN'}])
        self.assertEqual(power[0].serial, 'unknown')
        modules = parse_modules('', '', [{'MODULE':'1','MODEL':'N9K-MOD-TEST'}],
                                [{'NAME':'Slot 1','PID':'N9K-MOD-TEST','SN':'MODULE-NX-1'}])
        self.assertEqual(modules[0].serial, 'MODULE-NX-1')

    def test_blank_serial_does_not_consume_next_inventory_record(self):
        inventory = NX.replace('SN: PSU-NX-1', 'SN: ')
        power = parse_power_supplies('1 N9K-PWR-TEST 50W 500W Ok', inventory_output=inventory)
        self.assertEqual(power[0].serial, 'unknown')

    def test_inventory_identity_does_not_claim_healthy_when_status_command_failed(self):
        parts = parse_power_supplies('COMMAND_FAILED: timeout', inventory_output=EOS)
        self.assertEqual([p.serial for p in parts], ['PSU-TEST-1','PSU-TEST-2'])
        self.assertTrue(all(p.status == 'unknown' for p in parts))

    def test_split_power_tables_use_model_and_status_from_same_slot(self):
        output = '''Power Input A Input B
Supply Model Voltage Current Voltage Current
1 PWR-TEST-AC 0.00V 0.00A 220V 4A
2 PWR-TEST-AC 0.00V 0.00A 220V 5A
Power Output
Supply Capacity Current Power Status Uptime
1 3000W 90A 1100W Ok 21:30:13
2 3000W 90A 1100W Ok 21:30:11
'''
        parts = parse_power_supplies(output, inventory_output=EOS)
        self.assertEqual([(p.model,p.serial) for p in parts], [('PWR-TEST-AC','PSU-TEST-1'),('PWR-TEST-AC','PSU-TEST-2')])

    def test_modular_inventory_and_explicit_status_override_broken_textfsm(self):
        inventory = '''System has 3 card slots
Module Model HW Version Serial Number Mfg Date Epoch
Supervisor1 SUP-TEST 11.00 SUP-SN 2025-01-01 01.00
Linecard2 LC-TEST 11.00 LC-SN 2025-01-01 01.00
Fabric1 FAB-TEST 11.00 FAB-SN 2025-01-01 01.00
System has 1 power supply slots
1 Not Inserted
'''
        output = '''Module MAC addresses (count) Hw Sw
1 0000.0000.0001 1.0 1.0
Module Status Uptime Power off reason
1 Active 33 days, 20:50:41 N/A
2 Ok 33 days, 20:50:41 N/A
Fabric1 Ok 33 days, 20:50:41 N/A
'''
        parts = parse_modules(output,inventory,[{'MODULE':slot,'STATUS':'MAC'} for slot in ['1','2','Fabric1']])
        self.assertEqual([(p.serial,p.status) for p in parts],[('SUP-SN','active'),('LC-SN','ok'),('FAB-SN','ok')])
        self.assertEqual(parse_power_supplies('',inventory_output=inventory), [])

    def test_offline_psu_without_model_or_serial_remains_unknown(self):
        parts = parse_power_supplies('1 n/a n/a n/a n/a Unknown Offline', inventory_output='System has 1 power supply slots\n1 Unknown Unknown\n')
        self.assertEqual((parts[0].model,parts[0].serial,parts[0].status), ('unknown','unknown','offline'))

    def test_collector_passes_inventory_to_both_component_parsers(self):
        collector = Collector(SimpleNamespace(command_timeout_seconds=5), [device()])
        collector._device_credentials = Mock(return_value=('synthetic','synthetic',None))
        collector._collect_snmp_sample = Mock(return_value=SnmpSample())
        connection = Mock()
        connection.send_command_timing.side_effect = lambda cmd, **kw: {
            'show inventory': EOS, 'show environment power': POWER,
            'show module': '% Unavailable command',
        }.get(cmd, '')
        with patch('aiops_agent.collector.ConnectHandler', return_value=connection):
            result = collector.collect_device(device())
        self.assertTrue(result.ok)
        self.assertEqual({c.serial for c in result.hardware_components}, {'PSU-TEST-1','PSU-TEST-2','CHASSIS-TEST'})


class PublishingTests(unittest.TestCase):
    def setUp(self):
        self.gauge = Gauge('test_component_status', 'Test', ['device','component_type','name','model','serial','status'], registry=None)
        self.patch = patch('aiops_agent.collector.metrics.HARDWARE_COMPONENT_STATUS', self.gauge)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.collector = Collector(SimpleNamespace(), [])

    def samples(self):
        return [s for f in self.gauge.collect() for s in f.samples]

    def test_retire_old_labels_for_observed_device_only(self):
        self.gauge.labels(device().name, 'power', '1', 'PWR', 'unknown', '3_days').set(1)
        self.gauge.labels('synthetic-peer', 'power', '1', 'PWR', 'OTHER', 'ok').set(1)
        result = DeviceResult(device(), True, 1, hardware_components=[HardwareComponent('power','1','ok','PWR','PSU-SN')])
        self.collector._publish_metrics(result)
        self.collector._publish_metrics(result)
        self.assertEqual(len(self.samples()),2)
        self.assertEqual({s.labels['serial'] for s in self.samples()}, {'OTHER','PSU-SN'})

    def test_failed_collection_preserves_last_observation(self):
        self.gauge.labels(device().name, 'power', '1', 'PWR', 'PSU-SN', 'ok').set(1)
        self.collector._publish_metrics(DeviceResult(device(), False, 1, error='timeout'))
        self.assertEqual(len(self.samples()),1)

    def test_status_values(self):
        for status,value in [('ok',1),('power_loss',0),('offline',0),('powered_dn',0),('not_present',0.5),('unknown',0.5),('12:00:00',0.5)]:
            self.assertEqual(self.collector._hardware_status_value(status),value)


if __name__ == '__main__':
    unittest.main()
