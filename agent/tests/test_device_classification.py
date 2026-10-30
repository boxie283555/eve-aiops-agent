"""Synthetic inventory classification and publishing; no device connections."""
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch
from prometheus_client import Gauge
from aiops_agent.inventory import Device, device_classification, load_inventory
from aiops_agent.nautobot import NautobotClient, NautobotError
from aiops_agent.collector import Collector, DeviceResult


def device(**changes):
    return replace(Device('synthetic-switch','switch','lab','192.0.2.10','arista_eos',22,True,False),**changes)


class ClassificationTests(unittest.TestCase):
    def test_manufacturer_aliases_platform_fallback_and_functional_type(self):
        cases=[(device(),('Arista','Switch')),
               (device(platform='paloalto_panos',role='firewall',manufacturer='Palo Alto'),('Palo Alto Networks','Firewall')),
               (device(platform='cisco_nxos',manufacturer='Cisco'),('Cisco','Switch')),
               (device(platform='other_os',manufacturer='Example Vendor',role='firewall'),('Example Vendor','Firewall')),
               (device(platform='other_os',manufacturer='',role='router'),('Unknown','Other'))]
        for d,expected in cases:
            with self.subTest(expected=expected):self.assertEqual(device_classification(d),expected)

    def test_inventory_propagates_manufacturer(self):
        with patch('aiops_agent.inventory.load_project_config',return_value={'inventory':{'devices':[{'name':'synthetic','host':'192.0.2.1','manufacturer':'Arista Networks','platform':'arista_eos'}]}}),patch.dict('os.environ',{'AIOPS_INVENTORY_SOURCE':'yaml'}):
            self.assertEqual(load_inventory()[0].manufacturer,'Arista Networks')

    def test_nautobot_device_type_manufacturer_is_resolved(self):
        client=NautobotClient('http://unused','test')
        row={'name':'synthetic','primary_ip4':{'address':'192.0.2.1/32'},'platform':{'name':'arista_eos'},'device_type':{'url':'http://unused/api/dcim/device-types/synthetic/'}}
        payloads={'dcim/devices/?limit=0':{'results':[row]},'dcim/device-types/synthetic/':{'model':'SYNTHETIC-MODEL','manufacturer':{'name':'Arista Networks'}}}
        client._get=Mock(side_effect=lambda path:payloads[path])
        self.assertEqual(client.devices()[0]['manufacturer'],'Arista Networks')

    def test_optional_manufacturer_lookup_failure_does_not_drop_device(self):
        client=NautobotClient('http://unused','test')
        row={'name':'synthetic','primary_ip4':{'address':'192.0.2.1/32'},'platform':{'name':'arista_eos'},'device_type':{'url':'http://unused/api/dcim/device-types/synthetic/'}}
        client._get=Mock(side_effect=[{'results':[row]},NautobotError('unavailable')])
        records=client.devices()
        self.assertEqual(len(records),1)
        self.assertEqual(records[0]['manufacturer'],'unknown')
        self.assertEqual(records[0]['platform'],'arista_eos')

    def test_classification_exists_before_collection_and_survives_failed_auth(self):
        gauge=Gauge('test_classification','Test',['device','manufacturer','device_type','hardware_group'],registry=None)
        with patch('aiops_agent.collector.metrics.DEVICE_CLASSIFICATION_INFO',gauge):
            collector=Collector(SimpleNamespace(),[device()])
            self.assertEqual(list(gauge.collect())[0].samples[0].labels['hardware_group'],'Arista / Switch')
            collector._publish_metrics(DeviceResult(device(),False,1,error='authentication'))
            samples=list(gauge.collect())[0].samples
            self.assertEqual(len(samples),1)
            self.assertEqual(samples[0].labels['device_type'],'Switch')

    def test_changed_classification_replaces_old_labels_without_affecting_peers(self):
        gauge=Gauge('test_classification_changes','Test',['device','manufacturer','device_type','hardware_group'],registry=None)
        with patch('aiops_agent.collector.metrics.DEVICE_CLASSIFICATION_INFO',gauge):
            collector=Collector(SimpleNamespace(),[device(),device(name='synthetic-peer')])
            updated=device(platform='paloalto_panos',role='firewall')
            collector._publish_classification(updated)
            collector._publish_classification(updated)
            samples=list(gauge.collect())[0].samples
            self.assertEqual(len(samples),2)
            self.assertEqual({s.labels['device']:s.labels['hardware_group'] for s in samples},{'synthetic-switch':'Palo Alto Networks / Firewall','synthetic-peer':'Arista / Switch'})


if __name__=='__main__':unittest.main()
