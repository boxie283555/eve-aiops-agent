import copy
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from aiops_agent.model import stable_id, interface_name
from aiops_agent.parser import ArpEntry, LldpNeighbor, MacTableEntry
from aiops_agent.routing import panos_interfaces, eos_interfaces, parse_routes
from aiops_agent.topology import (
    build_snapshot,
    find_path,
    load_layout,
    load_snapshot,
    save_layout,
    save_snapshot,
)

FIXTURES = Path(__file__).parent / "fixtures"


def interface(name, address=None, vrf="default", **kw):
    return dict(
        name=name,
        vrf=vrf,
        addresses=[address] if address else [],
        admin_up=True,
        oper_up=True,
        **kw
    )


def result(
    name,
    interfaces=(),
    routes=(),
    links=(),
    arp=(),
    mac=(),
    platform="arista_eos",
    **kw
):
    return SimpleNamespace(
        device=SimpleNamespace(
            id=stable_id("test-device", name),
            name=name,
            host="192.0.2.1",
            platform=platform,
            role="leaf",
            site="test",
        ),
        ok=True,
        error=None,
        interfaces=list(interfaces),
        routes=list(routes),
        outputs={},
        collected_at=datetime.now(timezone.utc).isoformat(),
        dataset_status=dict.fromkeys(
            ["interfaces", "routes", "arp", "mac_address_table", "lldp_neighbors"], True
        ),
        lldp_neighbors=list(links),
        arp_table=list(arp),
        mac_table=list(mac),
        **kw
    )


def route(prefix, nh=None, iface="Ethernet1", vrf="default", protocol="static", **kw):
    return dict(
        prefix=prefix, next_hop=nh, interface=iface, vrf=vrf, protocol=protocol, **kw
    )


def network():
    a = result(
        "a",
        [interface("Ethernet1", "10.0.0.0/31"), interface("Loopback0", "192.0.2.1/32")],
        [route("192.0.2.2/32", "10.0.0.1")],
        [LldpNeighbor("Et1", "b", "Et1")],
    )
    b = result(
        "b",
        [interface("Ethernet1", "10.0.0.1/31"), interface("Loopback0", "192.0.2.2/32")],
        [route("192.0.2.2/32", iface="Loopback0", protocol="connected")],
    )
    return {"a": a, "b": b}


class ParserTests(unittest.TestCase):
    def test_pa_real_hierarchy_and_route_flags(self):
        fixture = json.loads((FIXTURES / "q2-PA-171.json").read_text())
        rows = {i["name"]: i for i in panos_interfaces(fixture["show interface all"])}
        self.assertEqual(set(rows), {"ethernet1/1", "ae1", "ae1.10"})
        self.assertEqual(rows["ethernet1/1"]["aggregate"], "ae1")
        self.assertEqual(rows["ae1.10"]["parent"], "ae1")
        self.assertEqual(rows["ae1.10"]["vrf"], "VR-EW")
        self.assertEqual(rows["ae1.10"]["vlan"], "10")
        self.assertIsNone(rows["ae1.10"]["admin_up"])
        self.assertTrue(rows["ae1.10"]["oper_up"])
        routes = parse_routes(fixture["show routing route"], "paloalto_panos")
        self.assertEqual(
            [r["protocol"] for r in routes], ["static", "connected", "local"]
        )
        self.assertEqual(routes[0]["next_hop"], "192.168.111.1")
        self.assertIsNone(routes[-1]["next_hop"])

    def test_eos_real_routes_and_ecmp(self):
        fixture = json.loads((FIXTURES / "q2-p1-svc.json").read_text())
        rows = parse_routes(fixture["show ip route vrf all | json"], "arista_eos")
        self.assertIn("FUSION", {r["vrf"] for r in rows})
        fixture = {
            "vrfs": {
                "default": {
                    "routes": {
                        "0.0.0.0/0": {
                            "routeType": "eBGP",
                            "vias": [
                                {"nexthopAddr": "10.0.0.1", "interface": "Et1"},
                                {"nexthopAddr": "10.0.0.3", "interface": "Et2"},
                            ],
                        }
                    }
                }
            }
        }
        rows = parse_routes(json.dumps(fixture), "arista_eos")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["interface"], "Ethernet2")

    def test_eos_clock_prompt_after_real_json(self):
        from aiops_agent.routing import eos_route_table

        for filename, count in [
            ("q2-raw-DUT-720XP-48TXH-2C-S.txt", 10),
            ("q2-raw-DUT-7050SPX4-48D8.txt", 25),
        ]:
            output = (FIXTURES / filename).read_text()
            self.assertIsNotNone(eos_route_table(output))
            rows = parse_routes(output, "arista_eos")
            self.assertEqual(len({(r["vrf"], r["prefix"]) for r in rows}), count)
        self.assertIsNone(eos_route_table('{"vrfs": {}} unexpected trailing output'))
        self.assertIsNotNone(eos_route_table('{"vrfs": {"default": {"routes": {}}}}'))

    def test_eos_l2_members_and_subinterface(self):
        rows = eos_interfaces(
            "",
            "Ethernet1 is up, line protocol is up (connected)\n  Hardware is Ethernet, address is 5000.0000.0001\nEthernet2 is administratively down, line protocol is down",
            "interface Ethernet1\n   channel-group 1 mode active\n!\ninterface Port-Channel1\n!\ninterface Port-Channel1.10\n   vrf FUSION\n   encapsulation dot1q 10\n   ip address 10.0.0.1/31\n!",
        )
        self.assertTrue(rows["Ethernet1"]["oper_up"])
        self.assertFalse(rows["Ethernet2"]["admin_up"])
        self.assertEqual(rows["Ethernet1"]["aggregate"], "Port-Channel1")
        self.assertEqual(rows["Port-Channel1.10"]["vrf"], "FUSION")
        self.assertEqual(rows["Port-Channel1.10"]["parent"], "Port-Channel1")

    def test_broadcast_ip_never_overwrites_mac(self):
        from aiops_agent.model import mac_address

        self.assertIsNone(mac_address("255.255.255.255"))
        rows = eos_interfaces(
            "",
            "Ethernet1 is up, line protocol is up\n  Hardware is Ethernet, address is 5000.0000.0001\n  Broadcast address is 255.255.255.255\nLoopback0 is up, line protocol is up\n  Broadcast address is 255.255.255.255",
            "",
        )
        self.assertEqual(rows["Ethernet1"]["mac"], "50:00:00:00:00:01")
        self.assertNotIn("mac", rows["Loopback0"])

    def test_diagnostics_never_form_inventory(self):
        self.assertEqual(panos_interfaces("COMMAND_FAILED: timeout"), [])
        self.assertEqual(parse_routes("% Invalid command", "arista_eos"), [])

    def test_nxos(self):
        rows = parse_routes(
            'IP Route Table for VRF "TENANT"\n10.0.0.0/24, ubest/mbest: 1/0\n *via 192.0.2.1, Eth1/1, [20/0], 00:03:00, bgp-65000, external',
            "cisco_nxos",
        )
        self.assertEqual(rows[0]["vrf"], "TENANT")
        self.assertEqual(rows[0]["interface"], "Ethernet1/1")


class ModelTests(unittest.TestCase):
    def test_rename_keeps_identity_and_layout(self):
        results = network()
        before = build_snapshot(results)
        results["new-a"] = results.pop("a")
        results["new-a"].device.name = "new-a"
        after = build_snapshot(results, previous=before)
        self.assertEqual(
            before.devices[0]["id"],
            next(d["id"] for d in after.devices if d["name"] == "new-a"),
        )
        self.assertEqual(
            {i["id"] for i in before.interfaces}, {i["id"] for i in after.interfaces}
        )
        self.assertEqual(find_path(after, "a:Et1", "b:Et1")["status"], "ok")
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {
                "AIOPS_TOPOLOGY_PATH": tmp + "/topology.json",
                "AIOPS_TOPOLOGY_LAYOUT_PATH": tmp + "/layout.json",
            },
        ):
            save_snapshot(before)
            save_layout({"a": {"x": 123, "y": 456}})
            save_snapshot(after)
            self.assertEqual(load_layout()["new-a"], {"x": 123, "y": 456})

    def test_pa_duplicates_and_no_inferred_cable(self):
        results = {}
        for name in ("PA-171", "PA-172"):
            fixture = json.loads((FIXTURES / ("q2-" + name + ".json")).read_text())
            results[name] = result(
                name,
                panos_interfaces(fixture["show interface all"]),
                parse_routes(fixture["show routing route"], "paloalto_panos"),
                platform="paloalto_panos",
            )
        results["switch"] = result(
            "switch",
            [interface("Ethernet1"), interface("Vlan10", "192.168.111.1/29")],
            arp=[ArpEntry("default", "192.168.111.2", "ba:db:ee:fb:ad:30", "Vlan10")],
            mac=[MacTableEntry("10", "ba:db:ee:fb:ad:30", "dynamic", "Et1")],
        )
        snapshot = build_snapshot(results)
        self.assertTrue(snapshot.links)
        self.assertTrue(
            all(
                l["type"] == "adjacency" and l["confirmation"] == "candidate"
                for l in snapshot.links
            )
        )
        self.assertEqual(
            find_path(snapshot, "192.168.111.2", "192.168.111.1")["status"], "ambiguous"
        )
        scoped = find_path(
            snapshot,
            "192.168.111.2",
            "192.168.111.1",
            src_device="PA-171",
            src_vrf="VR-EW",
        )
        self.assertNotEqual(scoped["status"], "ambiguous")
        sub = next(
            i
            for i in snapshot.interfaces
            if i["device"] == "PA-171" and i["name"] == "ae1.10"
        )
        agg = next(i for i in snapshot.interfaces if i["id"] == sub["parent_id"])
        self.assertEqual(len(agg["members"]), 1)
        self.assertNotEqual(
            next(
                v["id"]
                for v in snapshot.vrfs
                if v["device"] == "PA-171" and v["name"] == "VR-EW"
            ),
            next(
                v["id"]
                for v in snapshot.vrfs
                if v["device"] == "PA-172" and v["name"] == "VR-EW"
            ),
        )

    def test_conflicting_lldp_claims_are_not_cables(self):
        results = network()
        results["c"] = result("c", [interface("Ethernet1", "10.0.0.5/31")])
        results["a"].lldp_neighbors.append(LldpNeighbor("Et1", "c", "Et1"))
        snapshot = build_snapshot(results)
        self.assertTrue(
            all(link["confirmation"] == "conflicting" for link in snapshot.links)
        )
        self.assertEqual(find_path(snapshot, "a:Et1", "b:Et1")["status"], "incomplete")

    def test_failed_data_retained_not_renewed(self):
        results = network()
        before = build_snapshot(results)
        results["a"].ok = False
        results["a"].error = "timeout"
        after = build_snapshot(results, previous=before)
        old = next(r for r in before.routes if r["device"] == "a")
        new = next(r for r in after.routes if r["device"] == "a")
        self.assertEqual(old["last_success_at"], new["last_success_at"])
        self.assertEqual(new["freshness"], "stale")
        self.assertEqual(find_path(after, "a:Et1", "192.0.2.2")["status"], "stale")

    def test_failed_optional_route_retained(self):
        results = network()
        before = build_snapshot(results)
        results["a"].routes = []
        results["a"].dataset_status["routes"] = False
        after = build_snapshot(results, previous=before)
        self.assertTrue(next(d for d in after.devices if d["name"] == "a")["up"])
        self.assertEqual(
            next(r for r in after.routes if r["device"] == "a")["freshness"], "stale"
        )
        self.assertEqual(find_path(after, "a:Et1", "192.0.2.2")["status"], "incomplete")

    def test_old_layout_and_snapshot_readable_but_not_confirmed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "old.json"
            path.write_text(
                json.dumps(
                    {
                        "observed_at": "old",
                        "devices": [{"name": "a"}],
                        "links": [{"from": "a", "to": "b", "type": "physical"}],
                    }
                )
            )
            snapshot = load_snapshot(path)
            self.assertEqual(snapshot.links[0]["confirmation"], "candidate")
            self.assertEqual(snapshot.devices[0]["freshness"], "stale")
            path.write_text(json.dumps({"a": {"x": 1, "y": 2}}))
            self.assertEqual(load_layout(path)["a"], {"x": 1, "y": 2})

    def test_conflicting_arp_is_ambiguous(self):
        r = result(
            "a",
            [interface("Ethernet1", "10.0.0.1/24")],
            arp=[
                ArpEntry("default", "10.0.0.3", "00:00:00:00:00:01", "Ethernet1"),
                ArpEntry("default", "10.0.0.3", "00:00:00:00:00:02", "Ethernet1"),
            ],
        )
        self.assertEqual(
            find_path(build_snapshot({"a": r}), "10.0.0.3", "a:Et1")["status"],
            "ambiguous",
        )


class PathTests(unittest.TestCase):
    def test_routed_and_physical_modes(self):
        snapshot = build_snapshot(network())
        routed = find_path(snapshot, "a:Lo0", "192.0.2.2")
        self.assertEqual(routed["status"], "ok", routed)
        self.assertEqual(
            [s["prefix"] for s in routed["path"] if s["type"] == "route_lookup"],
            ["192.0.2.2/32"] * 2,
        )
        self.assertEqual(find_path(snapshot, "a:Et1", "b:Et1")["mode"], "physical")

    def test_missing_interface_and_unresolved_physical_destination(self):
        s = build_snapshot(network())
        self.assertEqual(find_path(s, "a:Et999", "b:Et1")["status"], "not_found")
        self.assertEqual(
            find_path(s, "a:Et1", "203.0.113.1", mode="physical")["status"], "not_found"
        )

    def test_expired_link_and_down_port(self):
        s = build_snapshot(network())
        s.links[0]["last_success_at"] = (
            datetime.now(timezone.utc) - timedelta(hours=1)
        ).isoformat()
        self.assertEqual(find_path(s, "a:Et1", "b:Et1")["status"], "incomplete")
        s = build_snapshot(network())
        next(
            i for i in s.interfaces if i["device"] == "b" and i["name"] == "Ethernet1"
        )["oper_up"] = False
        self.assertEqual(find_path(s, "a:Lo0", "192.0.2.2")["status"], "incomplete")

    def test_stale_specific_never_falls_back(self):
        results = network()
        results["a"].routes.append(route("0.0.0.0/0", "10.0.0.1"))
        s = build_snapshot(results)
        next(r for r in s.routes if r["device"] == "a" and r["prefix"].endswith("/32"))[
            "freshness"
        ] = "stale"
        answer = find_path(s, "a:Lo0", "192.0.2.2")
        self.assertEqual(answer["status"], "incomplete")
        self.assertIn("Most-specific route evidence is stale", answer["warnings"])

    def test_recursive_route(self):
        results = network()
        results["a"].routes = [
            route("192.0.2.2/32", "10.0.0.1", iface=""),
            route("10.0.0.0/31", iface="Et1", protocol="connected"),
        ]
        answer = find_path(build_snapshot(results), "a:Lo0", "192.0.2.2")
        self.assertEqual(answer["status"], "ok", answer)
        self.assertEqual(len(answer["path"][0]["recursive_routes"]), 1)

    def test_recursive_loop(self):
        results = network()
        results["a"].routes = [route("0.0.0.0/0", "10.0.0.1", iface="")]
        answer = find_path(build_snapshot(results), "a:Lo0", "192.0.2.2")
        self.assertEqual(answer["status"], "incomplete")
        self.assertIn("Recursive next-hop loop", answer["warnings"])

    def test_forwarding_loop(self):
        results = network()
        results["a"].routes = [route("0.0.0.0/0", "10.0.0.1")]
        results["b"].routes = [route("0.0.0.0/0", "10.0.0.0")]
        answer = find_path(build_snapshot(results), "a:Lo0", "203.0.113.1")
        self.assertEqual(answer["status"], "incomplete")
        self.assertIn("Routing loop or hop limit", answer["warnings"])

    def test_ecmp_all_branches_and_no_first_path_lie(self):
        results = network()
        results["a"].interfaces.append(interface("Ethernet2", "10.0.0.2/31"))
        results["a"].routes.append(route("192.0.2.2/32", "10.0.0.3", iface="Et2"))
        answer = find_path(build_snapshot(results), "a:Lo0", "192.0.2.2")
        self.assertEqual(len(answer["paths"]), 2)
        self.assertEqual(answer["path"], [])
        self.assertEqual({p["status"] for p in answer["paths"]}, {"ok", "incomplete"})

    def test_discard_route(self):
        results = network()
        results["a"].routes = [route("0.0.0.0/0", iface="", action="discard")]
        answer = find_path(build_snapshot(results), "a:Lo0", "192.0.2.2")
        self.assertEqual(answer["paths"][0]["status"], "blocked")

    def test_vrf_is_device_scoped(self):
        results = network()
        results["a"].routes[0]["vrf"] = "OTHER"
        self.assertEqual(
            find_path(build_snapshot(results), "a:Lo0", "192.0.2.2")["status"],
            "incomplete",
        )

    def test_aggregate_subinterfaces_use_confirmed_member(self):
        results = network()
        for name, ip in [("a", "10.0.0.0/31"), ("b", "10.0.0.1/31")]:
            results[name].interfaces = [
                interface("Ethernet1", aggregate="Port-Channel1"),
                interface("Port-Channel1"),
                interface("Port-Channel1.10", ip, parent="Port-Channel1", vlan="10"),
                interface(
                    "Loopback0", "192.0.2." + ("1" if name == "a" else "2") + "/32"
                ),
            ]
        results["a"].routes[0]["interface"] = "Po1.10"
        self.assertEqual(
            find_path(build_snapshot(results), "a:Lo0", "192.0.2.2")["status"], "ok"
        )


if __name__ == "__main__":
    unittest.main()
