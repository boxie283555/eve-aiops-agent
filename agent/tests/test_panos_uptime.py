"""Synthetic PAN-OS uptime samples; no network connections."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from prometheus_client import Gauge
from aiops_agent.collector import Collector
from aiops_agent.inventory import Device
from aiops_agent.parser import parse_panos_resources
from aiops_agent.snmp import SnmpSample


class PanosUptimeTests(unittest.TestCase):
    def test_days_clock_and_seconds_are_all_counted(self):
        for value, expected in [('12 days, 01:02:03',1036800+3723),('1 day, 23:59:59',172799),('0 days, 00:00:00',0),('00:00:37',37),('123 days, 12:34:56',123*86400+45296)]:
            with self.subTest(value=value):
                self.assertEqual(parse_panos_resources('', 'uptime: '+value).uptime_seconds, expected)

    def test_case_crlf_and_other_fields_cannot_be_confused_with_uptime(self):
        info='time: Fri Oct 30 13:00:00 2026\r\n UPTIME : 2 days, 03:04:05 \r\nsw-version: 11.2.5\r\n'
        self.assertEqual(parse_panos_resources('',info).uptime_seconds,183845)

    def test_missing_invalid_and_failed_output_do_not_become_zero_or_clock_time(self):
        for value in ['', 'time: 12:00:00', 'uptime: \ntime: 12:00:00', 'uptime: unknown', 'uptime: -1 days, 01:02:03', 'uptime: 0 days, 00:60:00', 'uptime: 2 days, 01:02', 'COMMAND_FAILED: timeout\nuptime: 2 days, 01:02:03', 'uptime: 2 days, 01:02:03 extra']:
            with self.subTest(value=value):
                self.assertIsNone(parse_panos_resources('',value).uptime_seconds)

    def test_existing_cpu_memory_parsing_and_one_argument_call_preserved(self):
        output='cpu utilization: 25.5\nmemory usage: 60.0'
        before=parse_panos_resources(output)
        after=parse_panos_resources(output,'uptime: 1 days, 00:00:01')
        self.assertEqual((after.cpu_percent,after.memory_percent),(before.cpu_percent,before.memory_percent))
        self.assertEqual((after.cpu_percent,after.memory_percent,after.uptime_seconds),(25.5,60,86401))
        self.assertIsNone(before.uptime_seconds)

    def test_collector_reuses_system_info_and_publishes_rebooted_zero(self):
        device=Device('synthetic-pa-uptime','firewall','lab','192.0.2.50','paloalto_panos',22,True,False)
        collector=Collector(SimpleNamespace(command_timeout_seconds=5),[device])
        collector._device_credentials=Mock(return_value=('test','test',None))
        collector._collect_snmp_sample=Mock(return_value=SnmpSample())
        connection=Mock()
        gauge=Gauge('test_panos_uptime_seconds','Test',['device','host'],registry=None)
        gauge.labels(device.name,device.host).set(999999)
        with patch('aiops_agent.collector.ConnectHandler',return_value=connection), patch('aiops_agent.collector.metrics.DEVICE_UPTIME_SECONDS',gauge):
            for uptime,expected in [('3 days, 02:03:04',266584),('0 days, 00:00:00',0)]:
                connection.send_command_timing.side_effect=lambda command,**kwargs: 'model: PA-VM\nuptime: '+uptime if command=='show system info' else ''
                result=collector.collect_device(device)
                self.assertTrue(result.ok)
                self.assertEqual(result.resources.uptime_seconds,expected)
                collector._publish_metrics(result)
                self.assertEqual(next(iter(next(iter(gauge.collect())).samples)).value,expected)
            commands=[call.args[0] for call in connection.send_command_timing.call_args_list]
            self.assertEqual(commands.count('show system info'),2)
            self.assertFalse(any('uptime' in command for command in commands))


if __name__ == '__main__':
    unittest.main()
