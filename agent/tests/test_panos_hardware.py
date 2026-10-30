"""PAN-OS hardware tests use synthetic data only; no device connections."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from prometheus_client import Gauge

from aiops_agent.collector import Collector, DeviceResult
from aiops_agent.inventory import Device
from aiops_agent.parser import DeviceHardwareInfo, parse_device_hardware, parse_panos_hardware
from aiops_agent.snmp import SnmpSample


SYSTEM_INFO = '''admin@lab-fw> show system info
hostname: lab-fw
model: PA-VM
serial: 000000TEST001
sw-version: 11.2.5-h4
app-version: 9000-1234
vm-mode: KVM
family: vm
admin@lab-fw>
'''


def firewall(name='synthetic-pa'):
    return Device(name, 'firewall', 'lab', '192.0.2.10', 'paloalto_panos', 22, True, False)


class PanosHardwareTests(unittest.TestCase):
    def test_explicit_fields_preserve_hotfix_and_serial(self):
        info = parse_panos_hardware(SYSTEM_INFO)
        self.assertEqual((info.model, info.version, info.serial), ('PA-VM', '11.2.5-h4', '000000TEST001'))
        self.assertEqual(info.architecture, 'unknown')  # family / vm-mode is not architecture.
        self.assertEqual(info.hw_version, 'unknown')

    def test_legacy_version_only_does_not_become_model(self):
        self.assertEqual(parse_panos_hardware('sw-version: 11.2.5'), DeviceHardwareInfo(version='11.2.5'))

    def test_whitespace_case_and_explicit_physical_hardware_fields(self):
        info = parse_panos_hardware(' MODEL : PA-440\r\n SW-VERSION:\t11.1.4-h7\r\narchitecture: x86_64\r\nhw-version: Rev A\r\n')
        self.assertEqual((info.model, info.version, info.architecture, info.hw_version), ('PA-440', '11.1.4-h7', 'x86_64', 'Rev A'))

    def test_empty_fields_do_not_consume_following_lines(self):
        self.assertEqual(parse_panos_hardware('model:   \nsw-version:\nserial: TEST'), DeviceHardwareInfo(serial='TEST'))

    def test_no_inferred_identity_from_unrelated_or_failed_output(self):
        for text in ['', 'admin@lab-fw>', 'app-version: 11.2.5\nplatform-family: VM',
                     'COMMAND_FAILED: ReadTimeout\nmodel: PA-VM\nsw-version: 11.2.5',
                     'Invalid syntax.\nmodel: PA-VM']:
            with self.subTest(text=text):
                self.assertEqual(parse_panos_hardware(text), DeviceHardwareInfo())

    def test_synthetic_eos_format_unchanged(self):
        info = parse_device_hardware('Arista vEOS-lab\nSoftware image version: 4.30.0F\nSerial number: TESTEOS\n')
        self.assertEqual((info.model, info.version), ('vEOS-lab', '4.30.0F'))

    def test_collector_routes_full_system_info_to_panos_parser(self):
        collector = Collector(SimpleNamespace(command_timeout_seconds=5), [firewall()])
        collector._device_credentials = Mock(return_value=('test-user', 'test-pass', None))
        collector._collect_snmp_sample = Mock(return_value=SnmpSample())
        connection = Mock()
        connection.send_command_timing.side_effect = lambda cmd, **kw: SYSTEM_INFO if cmd == 'show system info' else ''
        with patch('aiops_agent.collector.ConnectHandler', return_value=connection), patch(
            'aiops_agent.collector.parse_device_hardware', side_effect=AssertionError('Wrong hardware parser')
        ):
            result = collector.collect_device(firewall())
        self.assertTrue(result.ok)
        self.assertEqual(result.hardware.model, 'PA-VM')
        self.assertEqual(result.hardware.version, '11.2.5-h4')
        commands = [call.args[0] for call in connection.send_command_timing.call_args_list]
        self.assertIn('show system info', commands)
        self.assertNotIn('show system info | match sw-version', commands)
        connection.disconnect.assert_called_once()


class PanosHardwarePublishingTests(unittest.TestCase):
    def setUp(self):
        self.names = ('device', 'host', 'model', 'version', 'serial', 'architecture', 'hw_version')
        self.gauge = Gauge('test_panos_hardware_info', 'Test', self.names, registry=None)
        self.collector = Collector(SimpleNamespace(), [])
        self.hardware_patch = patch('aiops_agent.collector.metrics.DEVICE_HARDWARE_INFO', self.gauge)
        self.hardware_patch.start()
        self.addCleanup(self.hardware_patch.stop)

    def labels(self, device, model='PA-VM', version='11.2.5'):
        return dict(zip(self.names, (device, '192.0.2.10', model, version, 'TEST', 'unknown', 'unknown')))

    def samples(self):
        return [s.labels for family in self.gauge.collect() for s in family.samples]

    def test_replaces_all_stale_labels_for_one_pa_only_and_is_idempotent(self):
        self.gauge.labels(**self.labels('synthetic-pa', 'sw-version: 11.2.5', 'unknown')).set(1)
        self.gauge.labels(**self.labels('synthetic-pa', version='10.2.0')).set(1)
        peer = self.labels('synthetic-other')
        self.gauge.labels(**peer).set(1)
        result = DeviceResult(firewall(), True, 1, hardware=DeviceHardwareInfo(model='PA-VM', version='11.2.5', serial='TEST'))
        self.collector._publish_metrics(result)
        self.collector._publish_metrics(result)
        self.assertCountEqual(self.samples(), [self.labels('synthetic-pa'), peer])

    def test_failed_collection_does_not_replace_last_hardware_observation(self):
        previous = self.labels('synthetic-pa')
        self.gauge.labels(**previous).set(1)
        self.collector._publish_metrics(DeviceResult(firewall(), False, 1, error='timeout'))
        self.assertEqual(self.samples(), [previous])


if __name__ == '__main__':
    unittest.main()
