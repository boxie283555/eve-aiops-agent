import unittest
from dataclasses import replace
from unittest.mock import patch
from netmiko import NetmikoTimeoutException
from aiops_agent.inventory import Device
from aiops_agent.settings import load_settings
from aiops_agent.transport import cli_driver
from aiops_agent.collector import Collector
from aiops_agent.config_history import fetch_config
from aiops_agent.config_management import eos_connection
from aiops_agent.nautobot import NautobotClient


class CliTransportTests(unittest.TestCase):
    def setUp(self):
        self.d = Device(name='synthetic', role='switch', site='lab', host='192.0.2.1',
                        platform='arista_eos', port=2323, use_ssh=False, use_snmp=False)
        self.settings = replace(load_settings(), device_username='test', device_password='test', snmp_enabled=False)

    def test_driver_selection_and_ssh_default(self):
        self.assertEqual(cli_driver(self.d), 'arista_eos_telnet')
        self.assertEqual(cli_driver(replace(self.d, use_ssh=True)), 'arista_eos')
        self.assertEqual(cli_driver(replace(self.d, platform='paloalto_panos')), 'paloalto_panos_telnet')
        with self.assertRaises(ValueError):cli_driver(replace(self.d, platform='cisco_nxos'))

    def test_collector_uses_telnet_driver_and_custom_port(self):
        with patch('aiops_agent.collector.ConnectHandler', side_effect=NetmikoTimeoutException('synthetic timeout')) as connect:
            result = Collector(self.settings, []).collect_device(self.d)
        self.assertFalse(result.ok)
        self.assertEqual(connect.call_args.kwargs['device_type'], 'arista_eos_telnet')
        self.assertEqual(connect.call_args.kwargs['port'], 2323)

    def test_unsupported_transport_fails_one_device_without_connecting(self):
        with patch('aiops_agent.collector.ConnectHandler') as connect:
            result = Collector(self.settings, []).collect_device(replace(self.d, platform='cisco_nxos'))
        self.assertFalse(result.ok)
        connect.assert_not_called()

    def test_config_reads_and_recovery_connection_use_telnet(self):
        with patch('netmiko.ConnectHandler') as connect:
            fetch_config(self.d, self.settings)
            self.assertEqual(connect.call_args.kwargs['device_type'], 'arista_eos_telnet')
            eos_connection(self.d, self.settings)
            self.assertEqual(connect.call_args.kwargs['device_type'], 'arista_eos_telnet')
            self.assertEqual(connect.call_args.kwargs['port'], 2323)

    def test_nautobot_protocol_and_default_ports(self):
        client = NautobotClient('http://example.invalid', 'test')
        row = {'id':'synthetic', 'name':'synthetic', 'platform':{'name':'arista_eos'},
               'location':{'name':'lab'}, 'custom_fields':{'management_ip':'192.0.2.1'}}
        with patch.object(client, '_get_all', return_value=[row]):
            for value,port in [(None,22),(True,22),(False,23),('false',23)]:
                row['custom_fields']['use_ssh'] = value
                actual = client.devices()[0]
                self.assertEqual(actual['port'],port)
                self.assertEqual(actual['use_ssh'],port==22)
            row['custom_fields']['connection_port']=2323
            self.assertEqual(client.devices()[0]['port'],2323)
