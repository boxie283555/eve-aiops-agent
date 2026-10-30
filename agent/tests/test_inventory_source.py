import os
import unittest
from unittest.mock import patch

from aiops_agent.inventory import load_inventory, inventory_status
from aiops_agent.nautobot import NautobotError


class InventorySourceTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,{},clear=True);self.env.start();self.addCleanup(self.env.stop)

    def config(self,**changes):
        return {'inventory':{'source':'nautobot'},'nautobot':{'url':'http://example.invalid','token':'synthetic',**changes}}

    def test_source_alias_and_uuid_metadata(self):
        with patch('aiops_agent.inventory.load_project_config',return_value=self.config()),patch('aiops_agent.inventory.NautobotClient') as client:
            client.return_value.devices.return_value=[{'id':'uuid','name':'lab-test','host':'192.0.2.1'}]
            self.assertEqual(load_inventory()[0].id,'uuid')
            self.assertEqual(inventory_status()['effective_source'],'nautobot')
            self.assertFalse(inventory_status()['fallback_to_yaml'])
            self.assertNotIn('token',inventory_status())

    def test_failed_nautobot_does_not_silently_use_yaml(self):
        with patch('aiops_agent.inventory.load_project_config',return_value=self.config()),patch('aiops_agent.inventory.NautobotClient') as client,patch('aiops_agent.inventory._load_yaml_inventory') as yaml:
            client.return_value.devices.side_effect=NautobotError('test')
            with self.assertRaises(NautobotError):load_inventory()
            yaml.assert_not_called()

    def test_missing_credentials_do_not_fall_back(self):
        for missing in ['url','token']:
            config=self.config();config['nautobot'].pop(missing)
            with patch('aiops_agent.inventory.load_project_config',return_value=config),patch('aiops_agent.inventory._load_yaml_inventory') as yaml:
                with self.assertRaises(NautobotError):load_inventory()
                yaml.assert_not_called()

    def test_explicit_legacy_fallback_and_environment_precedence(self):
        with patch('aiops_agent.inventory.load_project_config',return_value=self.config(fallback_to_yaml=True)),patch('aiops_agent.inventory.NautobotClient') as client,patch('aiops_agent.inventory._load_yaml_inventory',return_value={'devices':[]}):
            client.return_value.devices.side_effect=NautobotError('test');load_inventory()
            self.assertEqual(inventory_status()['effective_source'],'yaml_fallback')
        with patch.dict(os.environ,{'AIOPS_INVENTORY_SOURCE':'nautobot'}),patch('aiops_agent.inventory.load_project_config',return_value={'inventory':{'source':'yaml'},'nautobot':{'url':'http://example.invalid','token':'synthetic'}}),patch('aiops_agent.inventory.NautobotClient') as client:
            client.return_value.devices.return_value=[];load_inventory()
            self.assertEqual(inventory_status()['effective_source'],'nautobot')
