import unittest
from dataclasses import replace
from unittest.mock import patch, MagicMock
from aiops_agent.bgp_baseline import validate, parse_eos_ipv4_all_vrfs, evaluate
from aiops_agent.collector import Collector
from aiops_agent.settings import load_settings
from test_health import fixture,score,NOW

TABLE='''BGP summary information for VRF blue
Router identifier 192.0.2.1, local AS number 65001
Neighbor Status Codes: m - Under maintenance
Description Neighbor V AS MsgRcvd MsgSent InQ OutQ Up/Down State PfxRcd PfxAcc PfxAdv
peer blue 192.0.2.2 4 65002 5 6 0 0 1d Estab 3 3 3
BGP summary information for VRF red
Router identifier 192.0.2.1, local AS number 65001
Neighbor V AS MsgRcvd MsgSent InQ OutQ Up/Down State PfxRcd PfxAcc PfxAdv
192.0.2.2 4 65003 5 6 0 0 1d Active 0 0 0
'''
EMPTY='''BGP summary information for VRF default
Router identifier 192.0.2.1, local AS number 65001
Neighbor V AS MsgRcvd MsgSent InQ OutQ Up/Down State PfxRcd PfxAcc PfxAdv
'''

def baseline(d):
 return dict(schema_version=1,device_id=d.id,revision=1,approval_status='approved',approved_by='test',approved_at=NOW.isoformat(),intent='enabled',neighbors=[dict(vrf='blue',neighbor='192.0.2.2',peer_as=65002,afi_safi='ipv4_unicast',required=True)])

class BaselineTests(unittest.TestCase):
 def setup_case(self):
  d,r,s=fixture();d=replace(d,bgp_baseline=baseline(d));r.bgp=parse_eos_ipv4_all_vrfs(TABLE)[0];r.dataset_status['bgp_baseline_complete']=True
  return d,r,s
 def check(self,d,r,s):return next(c for c in score(d,r,s)['devices'][0]['checks'] if c['key']=='bgp')
 def test_same_ip_in_multiple_vrfs_and_descriptions(self):
  peers,ok=parse_eos_ipv4_all_vrfs(TABLE);self.assertTrue(ok);self.assertEqual([p.vrf for p in peers],['blue','red']);self.assertTrue(peers[0].up);self.assertFalse(peers[1].up)
 def test_complete_empty_vs_failed_or_truncated(self):
  self.assertEqual(parse_eos_ipv4_all_vrfs(EMPTY),([],True));self.assertEqual(parse_eos_ipv4_all_vrfs('% BGP inactive'),([],True))
  for text in ['', '% Invalid input', TABLE+'broken row',TABLE+'BGP summary information for VRF truncated',TABLE+'COMMAND_FAILED: Timeout']:
   self.assertFalse(parse_eos_ipv4_all_vrfs(text)[1])
 def test_required_peer_disappears_empty_table_is_fault(self):
  d,r,s=self.setup_case();self.assertEqual(self.check(d,r,s)['score'],100)
  r.bgp=[];c=self.check(d,r,s);self.assertEqual(c['coverage'],1);self.assertGreater(c['deduction_points'],0);self.assertEqual(c['details'][0]['reason'],'baseline_neighbor_missing')
 def test_wrong_vrf_or_af_does_not_mask_missing_peer(self):
  d,r,s=self.setup_case();r.bgp=r.bgp[1:];self.assertEqual(self.check(d,r,s)['score'],0)
  r.bgp=[replace(r.bgp[0],vrf='blue',afi_safi='L2VPN EVPN')];self.assertEqual(self.check(d,r,s)['score'],0)
 def test_down_or_as_mismatch(self):
  for delta,reason in [({'session_state':'Idle'},'neighbor_down'),({'peer_as':'65009'},'baseline_as_mismatch')]:
   d,r,s=self.setup_case();r.bgp=[replace(r.bgp[0],**delta)];self.assertEqual(self.check(d,r,s)['details'][0]['reason'],reason)
 def test_failure_and_staleness_are_missing_evidence(self):
  d,r,s=self.setup_case();r.dataset_status['bgp_baseline_complete']=False;c=self.check(d,r,s);self.assertEqual(c['deduction_points'],0);self.assertGreater(c['unassessed_points'],0)
  self.assertEqual(evaluate(d,r,False)['reason'],'baseline_evidence_missing')
 def test_not_applicable_and_invalid_baselines(self):
  d,r,s=self.setup_case();b={**d.bgp_baseline,'intent':'not_applicable','neighbors':[]};d=replace(d,bgp_baseline=b);self.assertTrue(self.check(d,r,s)['excluded'])
  for delta in [{'device_id':'wrong'},{'approved_by':''},{'approval_status':'pending'},{'approved_at':'bad'}]:
   c=self.check(replace(d,bgp_baseline={**b,**delta}),r,s);self.assertEqual(c['reason'],'baseline_invalid');self.assertGreater(c['unassessed_points'],0)
 def test_no_baseline_preserves_legacy_behavior(self):
  d,r,s=fixture();self.assertIsNone(evaluate(d,r,True));self.assertTrue(self.check(d,r,s)['excluded'])
 def test_enabled_without_required_peers_is_unassessed(self):
  d,r,s=self.setup_case();d=replace(d,bgp_baseline={**d.bgp_baseline,'neighbors':[]});self.assertEqual(self.check(d,r,s)['reason'],'baseline_incomplete')
 def test_collector_integration_and_nonlab_command_preservation(self):
  settings=replace(load_settings(),device_username='synthetic',device_password='synthetic',snmp_enabled=False)
  for site in ['lab','synthetic-other']:
   d,_,_=fixture(site=site);conn=MagicMock()
   def output(cmd,**kwargs):return TABLE if cmd=='show ip bgp summary vrf all' else EMPTY if cmd=='show bgp evpn summary' else ''
   conn.send_command_timing.side_effect=output
   with patch('aiops_agent.collector.ConnectHandler',return_value=conn):r=Collector(settings,[]).collect_device(d)
   self.assertTrue(r.ok,r.error)
   cmds=[c.args[0] for c in conn.send_command_timing.call_args_list]
   if site=='lab':self.assertTrue(r.dataset_status['bgp_baseline_complete']);self.assertEqual(len(r.bgp),2)
   else:self.assertNotIn('show ip bgp summary vrf all',cmds);self.assertIn('show bgp summary',cmds)
