import copy
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from aiops_agent.flow import FlowState
from aiops_agent.panos_policy import parse_config, inspect_firewall
from aiops_agent.panos_evidence import attach_policy, refresh_policy, read_cache, verify_flow
from aiops_agent.topology import build_snapshot, find_path
from aiops_agent.parser import LldpNeighbor
from test_normalized_model import result, interface, route
from test_service_path import lab, SRC, DST

FIXTURE = Path(__file__).parent/'fixtures/lab-pa-policy.xml'


def rule(name='allow', destination='any', port='any', action='allow', to='untrust', extra=''):
    return f'''<entry name="{name}"><from><member>trust</member></from><to><member>{to}</member></to>
    <source><member>any</member></source><destination><member>{destination}</member></destination>
    <application><member>any</member></application><service><member>{port}</member></service>
    <action>{action}</action>{extra}</entry>'''


def nat(translation, destination='any', service='any'):
    return f'''<entry name="translate"><from><member>trust</member></from><to><member>untrust</member></to>
    <source><member>any</member></source><destination><member>{destination}</member></destination>
    <service>{service}</service>{translation}</entry>'''


def config(security='', nat_rules='', extra=''):
    return parse_config(f'''<config><devices><entry name="local"><vsys><entry name="vsys1">
    <zone><entry name="trust"><network><layer3><member>ethernet1/1</member></layer3></network></entry>
    <entry name="untrust"><network><layer3><member>ethernet1/2</member></layer3></network></entry>
    <entry name="dmz"><network><layer3><member>ethernet1/3</member></layer3></network></entry></zone>
    <service><entry name="web8443"><protocol><tcp><port>8443</port></tcp></protocol></entry></service>
    <rulebase><security><rules>{security}</rules></security><nat><rules>{nat_rules}</rules></nat></rulebase>
    {extra}</entry></vsys></entry></devices></config>''')


def inspect(model, flow=None, routing=None):
    return inspect_firewall(model,flow or FlowState('10.1.0.10','203.0.113.10','tcp',40000,8443),
                            'Ethernet1/1',routing or (lambda ip:[('Ethernet1/2','test-route')]))


def stamp(model):
    return {**model,'last_success_at':datetime.now(timezone.utc).isoformat(),'freshness':'fresh'}


class PolicyEvaluationTests(unittest.TestCase):
    def test_real_lab_exact_rule_and_all_possible_rules_allow(self):
        model=parse_config(FIXTURE.read_text())
        r=inspect_firewall(model,FlowState(SRC,DST,'tcp',40000,50001),'ae1.10',lambda ip:[('ae1.10','lab-route')])
        self.assertEqual(r['status'],'allow',r)
        self.assertEqual(r['security_rule'],'vlan101-vlan123-1')
        self.assertIsNone(r['nat_rule'])
        r=inspect_firewall(model,FlowState(SRC,DST,'tcp',40000,443),'ae1.10',lambda ip:[('ae1.10','lab-route')])
        self.assertEqual(r['status'],'allow')
        self.assertEqual(r['policy_resolution'],'action_only')
        self.assertIsNone(r['security_rule'])
        self.assertEqual([c['name'] for c in r['security_candidates']],['intrazone-SDN-default','intrazone-default'])
        r=inspect_firewall(model,FlowState(SRC,DST),'ae1.10',lambda ip:[('ae1.10','lab-route')])
        self.assertEqual(r['status'],'allow')
        self.assertEqual(len(r['security_candidates']),3)

    def test_first_match_deny_and_disabled_rule(self):
        self.assertEqual(inspect(config(rule(action='deny')+rule()))['status'],'blocked')
        self.assertEqual(inspect(config(rule(action='deny',extra='<disabled>yes</disabled>')+rule()))['status'],'allow')

    def test_unknown_earlier_rule_does_not_fall_through(self):
        r=inspect(config(rule('unknown',port='missing-service',action='deny')+rule()))
        self.assertEqual(r['status'],'incomplete')
        self.assertIn('unknown',r['reason'])

    def test_unknown_allow_and_definite_allow_establish_action_not_rule(self):
        r=inspect(config(rule('unknown',port='missing-service')+rule('catchall')))
        self.assertEqual(r['status'],'allow')
        self.assertIsNone(r['security_rule'])
        self.assertEqual([c['name'] for c in r['security_candidates']],['unknown','catchall'])
        # A deny after a definite catch-all is unreachable.
        r=inspect(config(rule('unknown',port='missing-service')+rule('catchall')+rule('unreachable-deny',action='deny')))
        self.assertEqual(r['status'],'allow')

    def test_unknown_allow_cannot_ignore_possible_deny_or_default_deny(self):
        for tail in ['',rule('deny',action='deny')]:
            r=inspect(config(rule('unknown',port='missing-service')+tail))
            self.assertEqual(r['status'],'incomplete')
            self.assertTrue(any(c['action']=='deny' for c in r['security_candidates']))
        model=parse_config(FIXTURE.read_text())
        model['vsyses']['vsys1']['defaults']['intrazone-default']='deny'
        r=inspect_firewall(model,FlowState(SRC,DST),'ae1.10',lambda ip:[('ae1.10','lab-route')])
        self.assertEqual(r['status'],'incomplete')

    def test_unknown_nat_cannot_be_bypassed_by_allow_security(self):
        model=config(rule(),nat('<destination-translation><translated-address>10.2.0.10</translated-address></destination-translation>',service='missing'))
        self.assertEqual(inspect(model)['status'],'incomplete')

    def test_unknown_predicate_with_definite_miss_can_skip(self):
        model=config(rule('irrelevant',destination='192.0.2.2',port='missing')+rule())
        self.assertEqual(inspect(model)['status'],'allow')

    def test_dnat_security_uses_original_address_and_port_but_new_zone(self):
        model=config(rule(destination='203.0.113.10',port='web8443',to='dmz'),
            nat('<destination-translation><translated-address>10.2.0.10</translated-address><translated-port>443</translated-port></destination-translation>',destination='203.0.113.10'))
        routing=lambda ip:[('Ethernet1/3' if ip=='10.2.0.10' else 'Ethernet1/2','route:'+ip)]
        r=inspect(model,routing=routing)
        self.assertEqual(r['status'],'allow',r)
        self.assertEqual((r['original_to_zone'],r['to_zone']),('untrust','dmz'))
        self.assertEqual((r['flow_after']['destination_ip'],r['flow_after']['destination_port']),('10.2.0.10',443))
        self.assertEqual(r['flow_before']['destination_port'],8443)

    def test_static_snat_and_combined_nat_do_not_mutate_input(self):
        flow=FlowState('10.1.0.10','203.0.113.10','tcp',40000,8443)
        translation='<source-translation><static-ip><translated-address>198.51.100.10</translated-address><bi-directional>no</bi-directional></static-ip></source-translation>'
        translation+='<destination-translation><translated-address>10.2.0.10</translated-address></destination-translation>'
        r=inspect(config(rule(),nat(translation)),flow)
        self.assertEqual(r['status'],'allow',r)
        self.assertEqual(r['flow_after']['source_ip'],'198.51.100.10')
        self.assertEqual(r['flow_after']['destination_ip'],'10.2.0.10')
        self.assertEqual(flow.source_ip,'10.1.0.10')

    def test_dynamic_pat_requires_session_allocation(self):
        model=config(rule(),nat('<source-translation><dynamic-ip-and-port><translated-address><member>198.51.100.10</member></translated-address></dynamic-ip-and-port></source-translation>'))
        r=inspect(model)
        self.assertEqual(r['status'],'incomplete')
        self.assertIn('session',r['reason'])

    def test_rule_negation_groups_and_source_port(self):
        extra='<address><entry name="net"><ip-netmask>203.0.113.0/24</ip-netmask></entry></address><address-group><entry name="group"><static><member>net</member></static></entry></address-group>'
        model=config(rule(destination='group',extra='<negate-destination>yes</negate-destination>')+rule(action='deny'),extra=extra)
        self.assertEqual(inspect(model)['status'],'blocked')
        model=config(rule(port='sport'),extra='<service><entry name="sport"><protocol><tcp><port>8443</port><source-port>40000-40010</source-port></tcp></protocol></entry></service>')
        self.assertEqual(inspect(model)['status'],'allow')
        self.assertEqual(inspect(model,FlowState('10.1.0.10','203.0.113.10','tcp',None,8443))['status'],'incomplete')

    def test_defaults_pbf_external_policy_and_ipv6_are_conservative(self):
        self.assertEqual(inspect(config())['status'],'blocked')
        model=config(rule());model['vsyses']['vsys1']['pbf']=True
        self.assertEqual(inspect(model)['status'],'incomplete')
        model=config(rule());model['limitations']=['Panorama policy missing']
        self.assertEqual(inspect(model)['status'],'incomplete')
        self.assertEqual(inspect(config(rule()),FlowState('2001:db8::1','2001:db8::2','tcp',1,443))['status'],'incomplete')

    def test_unknown_application_source_identity_and_schedule(self):
        for extra in ['<schedule>work-hours</schedule>','<source-user><member>alice</member></source-user>']:
            self.assertEqual(inspect(config(rule(extra=extra)))['status'],'incomplete')
        model=config(rule());model['vsyses']['vsys1']['security'][0]['application']=['ssl']
        self.assertEqual(inspect(model)['status'],'incomplete')


class FlowPathTests(unittest.TestCase):
    def test_lab_two_firewalls_evaluate_current_tuple(self):
        snapshot=lab(populate=True)
        for d in snapshot.devices:
            if d['name'].startswith('PA-'):
                text=FIXTURE.read_text().replace('ae1.10','ae1.20') if d['name']=='PA-172' else FIXTURE.read_text()
                d['pa_policy']=stamp(parse_config(text))
        r=find_path(snapshot,SRC,DST,site='lab',analysis='flow',protocol='tcp',source_port=40000,destination_port=50001)
        self.assertEqual(r['status'],'ok',r['warnings'])
        checks=[s for s in r['path'] if s['type']=='firewall_policy']
        self.assertEqual([s['device'] for s in checks],['PA-171','PA-172'])
        self.assertTrue(all(s['security_rule']=='vlan101-vlan123-1' for s in checks))
        self.assertEqual(r['paths'][0]['final_flow']['destination_ip'],DST)
        self.assertEqual(find_path(snapshot,SRC,DST,site='lab',analysis='flow',protocol='tcp',destination_port=443)['status'],'ok')
        broad=find_path(snapshot,SRC,DST,site='lab',analysis='flow')
        self.assertEqual(broad['status'],'ok',broad['warnings'])
        self.assertEqual(broad['flow']['protocol'],'any')
        self.assertIsNone(broad['flow']['destination_port'])
        checks=[s for s in broad['path'] if s['type']=='firewall_policy']
        self.assertEqual([s['policy_resolution'] for s in checks],['action_only','action_only'])
        self.assertTrue(all(s['security_rule'] is None for s in checks))
        # Independent reverse new connection is NOT an established session reply.
        reverse=find_path(snapshot,DST,SRC,site='lab',analysis='flow',protocol='tcp',source_port=50001,destination_port=40000)
        self.assertEqual(reverse['connection'],'new')
        self.assertEqual(reverse['flow']['source_ip'],DST)
        self.assertFalse(any(s.get('security_rule')=='vlan101-vlan123-1' for s in reverse['path']))

    def test_stale_peer_is_explained_without_advancing_path(self):
        snapshot=lab(populate=True)
        for i in snapshot.interfaces:
            if i['device']=='p1-leaf':i['freshness']='stale'
        r=find_path(snapshot,SRC,DST,site='lab')
        self.assertEqual(r['status'],'incomplete')
        self.assertTrue(any('p1-leaf' in w and 'stale' in w for w in r['warnings']),r['warnings'])
        self.assertFalse(any(s.get('type')=='physical' for s in r['path']))

    def test_dnat_routes_to_translated_endpoint_and_keeps_original_query(self):
        a=result('a',[interface('Lo0','10.1.0.10/32'),interface('Eth1','192.0.2.0/31')],
                 [route('203.0.113.10/32','192.0.2.1')],[LldpNeighbor('Eth1','pa','Eth1/1')])
        pa=result('pa',[interface('Eth1/1','192.0.2.1/31'),interface('Eth1/2','192.0.2.2/31')],
                  [route('203.0.113.10/32','192.0.2.3',iface='Eth1/2'),route('10.2.0.10/32','192.0.2.3',iface='Eth1/2')],
                  [LldpNeighbor('Eth1/2','b','Eth1')],platform='paloalto_panos')
        b=result('b',[interface('Eth1','192.0.2.3/31'),interface('Lo0','10.2.0.10/32')],[route('10.2.0.10/32',iface='Lo0',protocol='connected')])
        snapshot=build_snapshot({'a':a,'pa':pa,'b':b})
        model=config(rule(destination='203.0.113.10',port='web8443'),nat('<destination-translation><translated-address>10.2.0.10</translated-address><translated-port>443</translated-port></destination-translation>'))
        next(d for d in snapshot.devices if d['name']=='pa')['pa_policy']=stamp(model)
        r=find_path(snapshot,'10.1.0.10','203.0.113.10',analysis='flow',protocol='tcp',source_port=40000,destination_port=8443)
        self.assertEqual(r['status'],'ok',r['warnings'])
        self.assertEqual(r['destination']['query'],'203.0.113.10')
        self.assertEqual(r['paths'][0]['final_flow']['destination_ip'],'10.2.0.10')
        self.assertEqual(r['paths'][0]['final_flow']['destination_port'],443)
        self.assertTrue(any(h['type']=='nat_translation' for h in r['path']))
        model['vsyses']['vsys1']['security'][0]['action']='deny'
        next(d for d in snapshot.devices if d['name']=='pa')['pa_policy']=stamp(model)
        r=find_path(snapshot,'10.1.0.10','203.0.113.10',analysis='flow',protocol='tcp',source_port=40000,destination_port=8443)
        self.assertEqual(r['status'],'blocked')
        self.assertEqual(r['paths'][0]['final_flow']['destination_ip'],'203.0.113.10')

    def test_invalid_tuple_and_missing_policy_never_report_success(self):
        snapshot=lab(populate=True)
        self.assertEqual(find_path(snapshot,SRC,DST,analysis='flow',protocol='icmp',source_port=1)['status'],'invalid')
        self.assertEqual(find_path(snapshot,SRC,DST,analysis='flow',protocol='tcp',destination_port=50001)['status'],'incomplete')
        self.assertEqual(find_path(snapshot,SRC,DST,analysis='flow',mode='physical')['status'],'invalid')


class PolicyCacheTests(unittest.TestCase):
    def test_failed_refresh_retains_stale_evidence_and_does_not_touch_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ',{'AIOPS_PANOS_POLICY_PATH':tmp+'/policy.json'}):
            device=SimpleNamespace(id='fw',name='fw',host='192.0.2.5',site='lab')
            Path(tmp+'/policy.json').write_text(json.dumps({'devices':{'fw':{'status':'ok','host':device.host,'last_success_at':datetime.now(timezone.utc).isoformat()}}}))
            with patch('aiops_agent.panos_evidence.lab_firewalls',return_value=[device]),patch('aiops_agent.panos_evidence.PanosAPI',side_effect=TimeoutError):
                refresh_policy(SimpleNamespace())
            record=read_cache()['devices']['fw']
            self.assertEqual(record['status'],'error')
            self.assertEqual(record['freshness'],'stale')
            snapshot=build_snapshot({'fw':result('fw',platform='paloalto_panos')});snapshot.devices[0].update(id='fw',site='lab',host=device.host)
            self.assertEqual(attach_policy(snapshot).devices[0]['pa_policy']['status'],'unavailable')
            self.assertNotIn('pa_policy',snapshot.devices[0])

    def test_native_verify_rejects_out_of_scope_without_device_access(self):
        with patch('aiops_agent.panos_evidence.lab_firewalls',return_value=[]),patch('aiops_agent.panos_evidence.PanosAPI') as api:
            r=verify_flow(SimpleNamespace(),{'device_id':'not-lab'})
            self.assertEqual(r['status'],'unavailable')
            api.assert_not_called()
