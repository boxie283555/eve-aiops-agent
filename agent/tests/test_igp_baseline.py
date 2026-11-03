import unittest
from dataclasses import replace
from aiops_agent.igp_baseline import PROTOCOLS, applicability_from_evidence, evaluate_ospf, validate
from test_health import fixture,score,NOW


def eos():return dict(site='lab',platform='arista_eos',config_valid=True,igp_config_lines=[])
def pa():return dict(site='lab',platform='paloalto_panos',virtual_routers=[dict(virtual_router='test',protocols={p:dict(present=True,enabled='no') for p in ('ospf','ospfv3','rip')})])
def baseline(d):return dict(schema_version=1,device_id=d.id,revision=1,approval_status='approved',approved_by='synthetic',approved_at=NOW.isoformat(),protocols=applicability_from_evidence(eos()))

class IGPBaselineTests(unittest.TestCase):
 def test_complete_eos_config_establishes_inapplicability(self):
  p=applicability_from_evidence(eos());self.assertEqual(set(p),set(PROTOCOLS));self.assertTrue(all(v['intent']=='not_applicable' for v in p.values()))
 def test_empty_runtime_alone_is_insufficient(self):
  for delta in ({'config_valid':False},{'igp_config_lines':['router ospf 10']},{'error':'Timeout'},{'site':'unknown'}):
   with self.assertRaises(ValueError):applicability_from_evidence({**eos(),**delta})
 def test_panos_disabled_and_absent_protocols(self):
  row=pa();row['virtual_routers'][0]['protocols']['ospf']={'present':False,'enabled':None}
  result=applicability_from_evidence(row);self.assertEqual(result['ospf']['contexts'][0]['enabled'],False);self.assertEqual(result['isis']['reason'],'not_supported_by_panos')
 def test_panos_active_ambiguous_or_missing_evidence_rejected(self):
  for enabled in ('yes',None,''):
   row=pa();row['virtual_routers'][0]['protocols']['ospf']['enabled']=enabled
   with self.assertRaises(ValueError):applicability_from_evidence(row)
  with self.assertRaises(ValueError):applicability_from_evidence({**pa(),'virtual_routers':[]})
 def test_unconfigured_ospf_no_longer_counts_as_missing_evidence(self):
  d,r,s=fixture();r.dataset_status['ospf_neighbors']=False
  before=score(d,r,s);d=replace(d,igp_baseline=baseline(d));after=score(d,r,s)
  self.assertGreater(before['unassessed_points'],after['unassessed_points'])
  check=next(c for c in after['devices'][0]['checks'] if c['key']=='ospf');self.assertTrue(check['excluded']);self.assertEqual(check['unassessed_points'],0)
 def test_invalid_baseline_does_not_exclude(self):
  d,r,s=fixture()
  for delta in ({'device_id':'wrong'},{'approval_status':'pending'},{'protocols':{}},{'approved_at':'bad'}):
   d1=replace(d,igp_baseline={**baseline(d),**delta});self.assertEqual(evaluate_ospf(d1),dict(reason='igp_baseline_invalid'))
 def test_enabled_or_unspecified_requires_complete_evidence(self):
  d,r,s=fixture()
  for intent in ('enabled','unspecified'):
   b=baseline(d);b['protocols']['ospf']['intent']=intent
   self.assertEqual(evaluate_ospf(replace(d,igp_baseline=b)),dict(reason='igp_baseline_evidence_required'))
 def test_no_baseline_keeps_legacy_behavior(self):
  d,r,s=fixture();self.assertIsNone(evaluate_ospf(d))
 def test_not_applicable_cannot_hide_required_neighbors(self):
  d,r,s=fixture();b=baseline(d);b['protocols']['ospf']['neighbors']=[{'neighbor':'192.0.2.1'}]
  with self.assertRaises(ValueError):validate(b,d.id)
