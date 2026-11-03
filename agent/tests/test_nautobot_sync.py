import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from aiops_agent.nautobot import NautobotClient, NautobotError, NautobotSyncError


FIELDS = [
    "aiops_admin_status",
    "aiops_oper_status",
    "aiops_last_collected",
    "aiops_collection_error",
    "aiops_ip_address",
]
OBSERVED = "2026-01-01T01:02:03+00:00"


def result(name="lab-switch", count=1, ok=True):
    return SimpleNamespace(
        device=SimpleNamespace(name=name),
        ok=ok,
        dataset_status={"interfaces": ok},
        collected_at=OBSERVED,
        error=None if ok else "timeout",
        interfaces=[
            {
                "name": f"Ethernet{i}",
                "oper_up": True,
                "admin_up": True,
                "ip": "192.0.2.1",
                "mac": "5000.0000.0001",
            }
            for i in range(1, count + 1)
        ],
    )


class SyncTests(unittest.TestCase):
    def client(self, results):
        client = NautobotClient("http://unused", "test")
        payloads = {
            "extras/custom-fields/?limit=100": [{"key": k} for k in FIELDS],
            "dcim/devices/?limit=100": [
                {"name": r.device.name, "id": r.device.name} for r in results
            ],
            "extras/statuses/?limit=100": [{"name": "Active", "id": "active"}],
        }
        for r in results:
            payloads[f"dcim/interfaces/?device_id={r.device.name}&limit=100"] = [
                {
                    "id": r.device.name + "-" + i["name"],
                    "name": i["name"],
                    "custom_fields": {
                        "aiops_last_collected": "old",
                        "operator_note": "keep",
                    },
                }
                for i in r.interfaces
            ]
        client._get = Mock(side_effect=lambda path: {"results": payloads[path]})
        client._request = Mock(return_value=[])
        return client

    def test_bounded_bulk_updates_use_actual_collection_time(self):
        r = result(count=101)
        client = self.client([r])
        self.assertEqual(client.sync_interface_states([r]), 101)
        calls = client._request.call_args_list
        self.assertEqual([len(c.args[2]) for c in calls], [50, 50, 1])
        self.assertTrue(all(c.args[:2] == ("PATCH", "dcim/interfaces/") for c in calls))
        for call in calls:
            for row in call.args[2]:
                self.assertEqual(row["custom_fields"]["aiops_last_collected"], OBSERVED)
                self.assertNotIn("operator_note", row["custom_fields"])
                self.assertEqual(row["mac_address"], "50:00:00:00:00:01")

    def test_failed_collection_preserves_success_time_and_state(self):
        r = result(ok=False)
        client = self.client([r])
        client.sync_interface_states([r])
        row = client._request.call_args.args[2][0]
        self.assertEqual(row["custom_fields"], {"aiops_collection_error": "timeout"})
        self.assertNotIn("mac_address", row)

    def test_one_device_failure_does_not_prevent_other_updates(self):
        a, b = result("lab-a"), result("lab-b")
        client = self.client([a, b])

        def write(method, path, rows):
            if rows[0]["id"].startswith("lab-a"):
                raise NautobotError("timeout")
            return rows

        client._request.side_effect = write
        with self.assertRaises(NautobotSyncError) as caught:
            client.sync_interface_states([a, b])
        self.assertEqual(caught.exception.updated, 1)
        self.assertEqual(caught.exception.failed_devices, ["lab-a"])

    def test_idempotent_retry_after_timeout(self):
        r = result()
        client = self.client([r])
        client._request.side_effect = [NautobotError("timeout"), []]
        self.assertEqual(client.sync_interface_states([r]), 1)
        self.assertEqual(
            client._request.call_args_list[0], client._request.call_args_list[1]
        )

    def test_paginated_reads_include_every_page(self):
        client = NautobotClient("http://unused", "test")
        client._get = Mock(
            side_effect=[
                {
                    "results": [{"id": "one"}],
                    "next": "http://unused/api/dcim/interfaces/?limit=100&offset=100",
                },
                {"results": [{"id": "two"}], "next": None},
            ]
        )
        self.assertEqual(
            [r["id"] for r in client._get_all("dcim/interfaces/?limit=100")],
            ["one", "two"],
        )
        self.assertEqual(
            client._get.call_args.args[0], "dcim/interfaces/?limit=100&offset=100"
        )

    def test_primary_ip_reference_and_lab_platform_driver(self):
        client = NautobotClient("http://unused", "test")
        payloads = {
            "dcim/devices/?limit=100": {
                "results": [
                    {
                        "id": "lab-device",
                        "name": "lab-switch",
                        "primary_ip4": {
                            "id": "ip",
                            "url": "http://unused/api/ipam/ip-addresses/ip/",
                        },
                        "custom_fields": {"management_ip": "192.0.2.10"},
                        "location": {"name": "lab"},
                        "platform": {
                            "id": "eos-lab",
                            "url": "http://unused/api/dcim/platforms/eos-lab/",
                        },
                    }
                ]
            },
            "dcim/platforms/eos-lab/": {
                "name": "Arista EOS (lab NAPALM)",
                "network_driver": "arista_eos",
            },
        }
        client._get = Mock(side_effect=lambda path: payloads[path])
        devices = client.devices()
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]["host"], "192.0.2.10")
        self.assertEqual(devices[0]["platform"], "arista_eos")

    def test_background_failure_is_visible_and_does_not_raise(self):
        with patch("aiops_agent.inventory.load_inventory", return_value=[]):
            from aiops_agent import main
        with patch.object(
            main.NautobotClient,
            "sync_interface_states",
            side_effect=NautobotSyncError(7, ["lab-a"]),
        ):
            asyncio.run(main._sync_interfaces_background())
        self.assertEqual(main.interface_sync_status["status"], "error")
        self.assertEqual(main.interface_sync_status["updated_interfaces"], 7)
        self.assertEqual(main.interface_sync_status["failed_devices"], ["lab-a"])
