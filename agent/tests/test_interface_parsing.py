import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from aiops_agent.parser import parse_ip_interface_brief
from aiops_agent.nautobot import NautobotClient


class InterfaceParsingTests(unittest.TestCase):
    def test_timeout_diagnostic_is_not_inventory(self):
        diagnostic = '''COMMAND_FAILED: ReadTimeout:
Pattern not detected: 'switch\\#' in output.
Things you might try to fix this:
1. Explicitly set your pattern using expect_string.
2. Increase the read_timeout to a larger value.
You can also look at the Netmiko session_log for more information.
'''
        self.assertEqual(parse_ip_interface_brief(diagnostic), [])
        self.assertEqual(parse_ip_interface_brief(diagnostic.split('\n', 1)[1]), [])
        self.assertEqual(parse_ip_interface_brief('COMMAND_FAILED: timeout\nEthernet1 10.0.0.1 up up'), [])

    def test_eos_columns_and_admin_down(self):
        rows = parse_ip_interface_brief('''Interface IP Address Status Protocol MTU Owner
Ethernet1 10.0.0.1/31 up up 9214
Ethernet2 unassigned down down 1500
Ethernet3 unassigned admin down down 1500
Ethernet4 unassigned administratively down down
Loopback0 1.1.1.1/32 up up 65535
Management1 172.16.250.175/24 up up 1500
''')
        self.assertEqual([(r.name, r.admin_up, r.oper_up) for r in rows], [
            ('Ethernet1', True, True), ('Ethernet2', True, False),
            ('Ethernet3', False, False), ('Ethernet4', False, False),
            ('Loopback0', True, True), ('Management1', True, True)])

    def test_nxos_and_subinterfaces(self):
        rows = parse_ip_interface_brief('''IP Interface Status for VRF "default"(1)
Interface IP Address Interface Status
Vlan10 192.0.2.1 protocol-up/link-up/admin-up
Eth1/1 192.0.2.1 up up
Ethernet4/1/1.20 10.0.0.2/31 up down
Port-Channel1 unassigned up up
''')
        self.assertEqual([r.name for r in rows], ['Vlan10', 'Eth1/1', 'Ethernet4/1/1.20', 'Port-Channel1'])
        self.assertTrue(rows[0].admin_up)
        self.assertTrue(rows[0].oper_up)

    def test_malformed_rows_are_ignored(self):
        self.assertEqual(parse_ip_interface_brief('''You unassigned up up
Ethernet1 999.0.0.1 up up
Ethernet2 unassigned not a status
1. unassigned up up
Ethernet3 bad-address up up'''), [])

    def test_eos_unpopulated_ports_remain_in_inventory(self):
        rows = parse_ip_interface_brief('''Ethernet2/3/1 unassigned down notpresent 1500
Ethernet4/1/1.20 192.168.111.2/29 up lowerlayerdown 9214''')
        self.assertEqual([r.name for r in rows], ['Ethernet2/3/1', 'Ethernet4/1/1.20'])
        self.assertTrue(all(not r.oper_up for r in rows))

    def test_sync_rejects_invalid_names_even_from_another_parser(self):
        client = NautobotClient('http://unused', 'test')
        fields = ['aiops_admin_status', 'aiops_oper_status', 'aiops_last_collected',
                  'aiops_collection_error', 'aiops_ip_address']
        payloads = {
            'extras/custom-fields/?limit=100': [{'key': k} for k in fields],
            'dcim/devices/?limit=0': [{'name': 'switch', 'id': 'device-id'}],
            'extras/statuses/?limit=100': [{'name': 'Active', 'id': 'active'}],
            'dcim/interfaces/?limit=0': [],
        }
        client._get = lambda path: {'results': payloads[path]}
        client._request = Mock(side_effect=AssertionError('Unexpected write'))
        result = SimpleNamespace(device=SimpleNamespace(name='switch'), interfaces=[
            {'name': n} for n in ['1.', '2.', 'Pattern', 'Things', 'You']])
        self.assertEqual(client.sync_interface_states([result]), 0)
        client._request.assert_not_called()


if __name__ == '__main__':
    unittest.main()
