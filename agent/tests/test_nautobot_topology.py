import unittest
from unittest.mock import Mock

from aiops_agent.nautobot import NautobotClient
from aiops_agent.topology import build_snapshot


class NautobotCableTests(unittest.TestCase):
    def test_cable_device_ids_resolve_to_snapshot_names(self):
        client = NautobotClient("http://unused", "test")
        payloads = {
            "dcim/devices/?limit=0": [
                {"id": "device-a", "name": "lab-pa"},
                {"id": "device-b", "name": "lab-switch"},
            ],
            "dcim/interfaces/?limit=0": [
                {"id": "interface-a", "device": {"id": "device-a"}, "name": "ethernet1/1"},
                {"id": "interface-b", "device": {"id": "device-b"}, "name": "Ethernet3"},
            ],
            "ipam/ip-addresses/?limit=0": [],
            "dcim/cables/?limit=0": [{
                "id": "cable-a", "termination_a": {"id": "interface-a"},
                "termination_b": {"id": "interface-b"}, "status": {"name": "Connected"},
            }],
        }
        client._get = Mock(side_effect=lambda path: {"results": payloads[path]})
        snapshot = build_snapshot({}, client.topology())
        self.assertEqual(len(snapshot.links), 1)
        cable = snapshot.links[0]
        self.assertEqual((cable["from"], cable["to"]), ("lab-pa", "lab-switch"))
        self.assertEqual((cable["local_interface"], cable["remote_interface"]), ("Ethernet1/1", "Ethernet3"))
        self.assertEqual((cable["type"], cable["confirmation"]), ("physical", "confirmed"))
        self.assertEqual((cable["from_id"], cable["to_id"]), ("device-a", "device-b"))
