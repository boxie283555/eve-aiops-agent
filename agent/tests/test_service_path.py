"""Regression tests: lab captures plus explicitly synthetic forwarding variants."""
import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from test_normalized_model import result, network
from aiops_agent.routing import eos_interfaces, parse_routes
from aiops_agent.forwarding import eos_forwarding, evaluate_policy, operational_fieldsets
from aiops_agent.topology import build_snapshot, find_path

FIXTURE = Path(__file__).parent / 'fixtures/q2-lab-service-path.json'
SRC, DST = '10.100.101.111', '10.200.123.212'


def lab_results(populate=False):
    fixture = json.loads(FIXTURE.read_text())
    results = {}
    for name, data in fixture['devices'].items():
        rows = copy.deepcopy(data['interfaces'])
        meta = eos_interfaces('', '', data.get('config', ''))
        for row in rows:
            row.update({k: v for k, v in meta.get(row['name'], {}).items() if v is not None})
        forwarding = eos_forwarding(data['config'], data['fieldsets']) if 'config' in data else None
        if populate and forwarding:
            # Synthetic repair of empty BGP sets, never change live device state.
            for name_, prefixes in {'bgpDC1': [SRC+'/32', '10.200.123.112/32'],
                                    'bgpDC2': [DST+'/32', '10.100.101.211/32']}.items():
                if name_ in forwarding['fieldsets']:
                    forwarding['fieldsets'][name_].update(prefixes=prefixes, available=True)
        routes = parse_routes(data['route_output'], 'arista_eos') if 'route_output' in data else data['routes']
        results[name] = result(name, rows, routes, platform=data['platform'], forwarding=forwarding)
        results[name].device.site = 'lab'
    return results, {'links': fixture['links']}


def lab(populate=False):
    return build_snapshot(*lab_results(populate))


def sequence(path):
    names = []
    for hop in path:
        for name in ([hop['from'], hop['to']] if hop['type'] == 'physical' else [hop.get('device')]):
            if name and (not names or names[-1] != name):
                names.append(name)
    return names


class LabServicePathTests(unittest.TestCase):
    def test_observed_empty_sets_report_both_direction_drop(self):
        snapshot = lab()
        for src, dst, pod, pa in [(SRC, DST, 'p1', 'PA-171'), (DST, SRC, 'p2', 'PA-172')]:
            with self.subTest(src=src):
                r = find_path(snapshot, src, dst, site='lab')
                self.assertEqual(r['status'], 'blocked', r['warnings'])
                self.assertEqual(sequence(r['path']), [pod+'-host', pod+'-leaf', pod+'-svc', pa, pod+'-svc'])
                self.assertIn('FW-unclassified-DC', r['paths'][0]['reason'])
                self.assertEqual(set(r['path'][-1]['empty_fieldsets']), {'bgpDC1', 'bgpDC2'})
                self.assertTrue(any(h['type'] == 'vxlan_encap' for h in r['path']))

    def test_synthetic_populated_sets_complete_both_directions_with_two_firewalls(self):
        snapshot = lab(populate=True)
        expected = ['p1-host', 'p1-leaf', 'p1-svc', 'PA-171', 'p1-svc', 'p2-svc', 'PA-172', 'p2-svc', 'p2-leaf', 'p2-host']
        for src, dst, nodes in [(SRC, DST, expected), (DST, SRC, list(reversed(expected)))]:
            r = find_path(snapshot, src, dst, site='lab')
            self.assertEqual(r['status'], 'ok', r['warnings'])
            self.assertEqual(sequence(r['path']), nodes)
            self.assertEqual(len([h for h in r['path'] if h['type'] == 'vxlan_encap']), 3)
            self.assertTrue(all(h['local_interface'] and h['remote_interface'] for h in r['path'] if h['type'] == 'physical'))

    def test_missing_runtime_sets_are_unknown_not_empty(self):
        snapshot = lab()
        svc = next(d for d in snapshot.devices if d['name'] == 'p1-svc')
        svc['forwarding']['fieldsets']['bgpDC1']['available'] = False
        r = find_path(snapshot, SRC, DST, site='lab')
        # bgpDC2 being known empty safely rules out the remote rule, but the
        # local destination rule still has an unknown predicate.
        self.assertEqual(r['status'], 'incomplete')
        self.assertIn('bgpDC1', r['warnings'][-1])

    def test_vlan_membership_is_required(self):
        snapshot = lab()
        for row in snapshot.interfaces:
            if row['device'] == 'p1-host' and row['name'] == 'Ethernet1':
                row['allowed_vlans'] = '123'
        r = find_path(snapshot, SRC, DST, site='lab')
        self.assertEqual(r['status'], 'incomplete')
        self.assertEqual(sequence(r['path']), ['p1-host'])

    def test_underlay_and_vni_evidence_required(self):
        for missing in ['route', 'vni', 'link']:
            snapshot = lab()
            if missing == 'route':
                snapshot.routes = [r for r in snapshot.routes if not (r['device'] == 'p1-leaf' and r['vrf'] == 'default')]
            elif missing == 'vni':
                for i in snapshot.interfaces:
                    if i['device'] == 'p1-svc': i.pop('vrf_vnis', None)
            else:
                snapshot.links = [e for e in snapshot.links if {e['from'], e['to']} != {'p1-leaf', 'p1-svc'}]
            r = find_path(snapshot, SRC, DST, site='lab')
            self.assertEqual(r['status'], 'incomplete')
            self.assertRegex(r['warnings'][-1], 'VXLAN|VTEP')

    def test_stale_route_cannot_be_hidden_by_overlay(self):
        snapshot = lab()
        for route in snapshot.routes:
            if route['device'] == 'p1-leaf' and route['vrf'] == 'default': route['freshness'] = 'stale'
        self.assertEqual(find_path(snapshot, SRC, DST, site='lab')['status'], 'incomplete')

    def test_site_filters_duplicate_owners_and_transit(self):
        results, design = lab_results()
        duplicate = copy.deepcopy(results['p1-host'])
        duplicate.device.id = 'synthetic-other-site'
        duplicate.device.name = 'synthetic-duplicate'
        duplicate.device.site = 'synthetic-other'
        results[duplicate.device.name] = duplicate
        snapshot = build_snapshot(results, design)
        self.assertEqual(find_path(snapshot, SRC, DST)['status'], 'ambiguous')
        self.assertEqual(find_path(snapshot, SRC, DST, site='lab')['status'], 'blocked')
        for d in snapshot.devices:
            if d['name'] == 'p1-svc': d['site'] = 'synthetic-other'
        self.assertEqual(find_path(snapshot, SRC, DST, site='lab')['status'], 'incomplete')

    def test_physical_mode_stays_physical(self):
        r = find_path(lab(), SRC, DST, site='lab', mode='physical')
        self.assertEqual(r['status'], 'ok')
        self.assertNotIn('PA-171', sequence(r['path']))
        self.assertNotIn('traffic_policy', {h['type'] for h in r['path']})

    def test_route_ids_distinguish_overlay_ecmp(self):
        results, design = lab_results()
        route = next(r for r in results['p1-leaf'].routes if r.get('vtep'))
        results['p1-leaf'].routes.append({**route, 'vtep': '192.0.2.99'})
        snapshot = build_snapshot(results, design)
        self.assertEqual(len(snapshot.routes), len({r['id'] for r in snapshot.routes}))

    def test_optional_lab_policy_timeout_preserves_collection_and_nonlab_commands(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from test_device_classification import device
        from aiops_agent.collector import Collector
        fixture = json.loads(FIXTURE.read_text())['devices']['p1-svc']
        command = 'show traffic-policy field-set ipv4 prefix'
        def output(cmd, **kwargs):
            if cmd == command: raise TimeoutError('synthetic timeout')
            return {'show running-config': fixture['config'],
                    'show ip route vrf all | json': fixture['route_output'],
                    'show interfaces': 'Ethernet1 is up, line protocol is up'}.get(cmd, '')
        settings = SimpleNamespace(command_timeout_seconds=1, snmp_enabled=False,
            device_username='test', device_password='test', device_secret=None)
        for site in ['lab', 'synthetic-other']:
            conn = Mock()
            conn.send_command_timing.side_effect = output
            with patch('aiops_agent.collector.ConnectHandler', return_value=conn):
                collected = Collector(settings, []).collect_device(device(site=site))
            self.assertTrue(collected.ok)
            self.assertTrue(collected.dataset_status['routes'])
            self.assertTrue(collected.dataset_status['interfaces'])
            self.assertFalse(collected.forwarding['fieldsets']['bgpDC1']['available'])
            commands = [call.args[0] for call in conn.send_command_timing.call_args_list]
            self.assertEqual(command in commands, site == 'lab')


class PolicyParserTests(unittest.TestCase):
    def test_truncated_or_exception_fieldsets_are_unavailable(self):
        header = 'Traffic policy IPv4 prefix field set: X\nPrefix count: 1\nException prefix count: 0\n'
        self.assertEqual(operational_fieldsets(header), {})
        self.assertEqual(operational_fieldsets(header+'192.0.2.1/32\n'), {'X': ['192.0.2.1/32']})
        self.assertEqual(operational_fieldsets(header.replace('Exception prefix count: 0', 'Exception prefix count: 1')+'192.0.2.1/32\n'), {})

    def test_unsupported_predicate_does_not_become_pass(self):
        fixture = json.loads(FIXTURE.read_text())['devices']['p1-leaf']
        evidence = eos_forwarding(fixture['config'], fixture['fieldsets'])
        rule = next(r for r in evidence['policies']['pSDN-OWEN'] if r['name'] == 'DC-interzone-to-FW')
        rule['unsupported'] = ['destination port 443']
        chosen, error = evaluate_policy(evidence, 'FUSION', SRC, DST, '101', False)
        self.assertIsNone(chosen)
        self.assertIn('destination port', error)
