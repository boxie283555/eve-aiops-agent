"""Versioned, evidence-backed topology; legacy API names remain available."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from ipaddress import ip_interface

from .model import stable_id, interface_name, interface_kind, mac_address


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def ttl_seconds() -> int:
    return int(os.getenv("AIOPS_TOPOLOGY_TTL_SECONDS", "600"))


def fresh(row: dict, now: datetime | None = None) -> bool:
    if row.get("freshness") in {"stale", "unknown"}:
        return False
    try:
        age = (
            (now or datetime.now(timezone.utc))
            - datetime.fromisoformat(row["last_success_at"])
        ).total_seconds()
        return 0 <= age <= ttl_seconds()
    except (KeyError, TypeError, ValueError):
        return False


@dataclass
class TopologySnapshot:
    observed_at: str
    devices: list[dict[str, Any]] = field(default_factory=list)
    interfaces: list[dict[str, Any]] = field(default_factory=list)
    links: list[dict[str, Any]] = field(default_factory=list)
    endpoints: list[dict[str, Any]] = field(default_factory=list)
    schema_version: str = "1.1"
    vrfs: list[dict[str, Any]] = field(default_factory=list)
    routes: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def build_snapshot(
    results: dict[str, Any],
    design: dict | None = None,
    previous: TopologySnapshot | None = None,
) -> TopologySnapshot:
    now = utcnow()
    snapshot = TopologySnapshot(observed_at=now)
    design = design or {}
    inventory = {d["name"]: d for d in design.get("devices", [])}
    prior_devices = {d["id"]: d for d in previous.devices} if previous else {}
    device_rows = {}
    for name in sorted(set(results) | set(inventory)):
        result = results.get(name)
        spec = inventory.get(name, {})
        device = result.device if result else None
        did = getattr(device, "id", None) or spec.get("id") or stable_id("device", name)
        prior = prior_devices.get(did, {})
        observed = getattr(result, "collected_at", now) if result else now
        aliases = set(prior.get("aliases", [])) | {name}
        if prior.get("name"):
            aliases.add(prior["name"])
        if result:
            raw = result.outputs.get("hostname", "")
            import re

            match = re.search(r"(?:Hostname|hostname):\s*(\S+)", raw)
            if match:
                aliases.add(match[1])
            elif (
                raw.strip()
                and len(raw.split()) == 1
                and not raw.startswith("COMMAND_FAILED")
            ):
                aliases.add(raw.strip())
        row = {
            "id": did,
            "name": name,
            "aliases": sorted(aliases),
            "host": getattr(device, "host", spec.get("host")),
            "role": getattr(device, "role", spec.get("role")),
            "site": getattr(device, "site", spec.get("site")),
            "platform": getattr(device, "platform", spec.get("platform")),
            "up": bool(result and result.ok),
            "error": result.error if result else "not_collected",
            "last_attempt_at": observed,
            "last_success_at": (
                observed if result and result.ok else prior.get("last_success_at")
            ),
            "freshness": "fresh" if result and result.ok else "stale",
            "datasets": getattr(result, "dataset_status", {}) if result else {},
        }
        device_rows[name] = row
        snapshot.devices.append(row)
        for dataset in ("interfaces", "routes"):
            if not row["datasets"].get(dataset, False):
                snapshot.warnings.append(
                    f"{name}: {dataset} unavailable; historical records are stale when retained."
                )
    by_id = {d["id"]: d for d in snapshot.devices}
    aliases = {}
    for d in snapshot.devices:
        for alias in d["aliases"] + [d["id"]]:
            aliases.setdefault(alias.lower(), set()).add(d["name"])

    def resolve_name(value):
        matches = aliases.get(str(value).lower(), set())
        return next(iter(matches)) if len(matches) == 1 else None

    interfaces = {}
    vrfs = {}

    def vrf_id(device, name):
        did = device_rows[device]["id"]
        name = str(name or "unknown")
        vid = stable_id("vrf", did, name)
        vrfs[vid] = {"id": vid, "device_id": did, "device": device, "name": name}
        return vid

    def stamp(device, dataset, source=None):
        d = device_rows[device]
        return {
            "observed_at": d["last_attempt_at"],
            "last_attempt_at": d["last_attempt_at"],
            "last_success_at": d["last_attempt_at"],
            "freshness": "fresh",
            "source": source or dataset,
            "dataset": dataset,
            "ttl_seconds": ttl_seconds(),
        }

    def interface(device, name, values=None):
        name = interface_name(name)
        did = device_rows[device]["id"]
        iid = stable_id("interface", did, name)
        if iid not in interfaces:
            interfaces[iid] = {
                "id": iid,
                "device_id": did,
                "device": device,
                "name": name,
                "raw_name": name,
                "aliases": [name],
                "kind": interface_kind(name),
                "admin_up": None,
                "oper_up": None,
                "freshness": "unknown",
                "vrf": "unknown",
                "vrf_id": vrf_id(device, "unknown"),
            }
        row = interfaces[iid]
        if values:
            row.update(values)
            row["name"] = name
            row["id"] = iid
            row["device_id"] = did
            row["device"] = device
            row["aliases"] = sorted(
                set(row.get("aliases", []) + [name, str(values.get("raw_name", name))])
            )
            row["vrf_id"] = vrf_id(device, row.get("vrf", "unknown"))
        return row

    for name, result in results.items():
        if not result.ok:
            continue
        for raw in (
            result.interfaces if result.dataset_status.get("interfaces", False) else []
        ):
            interface(
                name,
                raw["name"],
                {**raw, "raw_name": raw["name"], **stamp(name, "interfaces", "cli")},
            )
        for route in (
            getattr(result, "routes", [])
            if result.dataset_status.get("routes", False)
            else []
        ):
            vid = vrf_id(name, route["vrf"])
            did = device_rows[name]["id"]
            iface = (
                interface(name, route["interface"]) if route.get("interface") else None
            )
            # A routing interface has a device-local VRF, not a global VRF name.
            if iface and iface.get("vrf") == "unknown":
                iface["vrf"] = route["vrf"]
                iface["vrf_id"] = vid
            snapshot.routes.append(
                {
                    **route,
                    "id": stable_id(
                        "route",
                        did,
                        vid,
                        route["prefix"],
                        route.get("next_hop"),
                        route.get("interface"),
                    ),
                    "device": name,
                    "device_id": did,
                    "vrf_id": vid,
                    "interface_id": iface["id"] if iface else None,
                    **stamp(name, "routes", "rib"),
                }
            )
    for row in design.get("interfaces", []):
        if row.get("device") not in device_rows:
            continue
        item = interface(row["device"], row["name"])
        item["nautobot_id"] = row.get("nautobot_id")
        item.setdefault("description", row.get("description", ""))
    for item in list(interfaces.values()):
        for key in ("parent", "aggregate"):
            target = item.get(key)
            if target:
                parent = interface(item["device"], target)
                item[key + "_id"] = parent["id"]
                parent.setdefault(
                    "members" if key == "aggregate" else "children", []
                ).append(item["id"])

    def endpoint(device, raw, dataset):
        did = device_rows[device]["id"]
        raw = {**raw}
        raw["mac"] = mac_address(raw.get("mac"))
        raw_interface = raw.get("interface", "")
        names = [interface_name(v.strip()) for v in raw_interface.split(",")]
        names = [v for v in names if v and v.lower() != "not learned"]
        iface = interface(device, names[0]) if names else None
        if iface and not raw.get("vrf"):
            raw["vrf"] = iface.get("vrf", "unknown")
        vid = vrf_id(device, raw.get("vrf", "unknown"))
        vlan = raw.get("vlan") or (iface or {}).get("vlan")
        if not vlan and names and names[0].startswith("Vlan"):
            vlan = names[0][4:]
        e = {
            **raw,
            "device": device,
            "device_id": did,
            "vrf_id": vid,
            "vlan": str(vlan) if vlan else None,
            "interface": names[0] if names else "",
            "raw_interface": raw_interface,
            "interface_id": iface["id"] if iface else None,
            "scope_id": stable_id("scope", did, vid, vlan),
            "attachments": [
                interface(device, n)["id"]
                for n in names[1:]
                if interface_kind(n) in {"physical", "aggregate"}
            ],
            **stamp(device, dataset, raw.get("source", dataset)),
        }
        e["id"] = stable_id(
            "endpoint-observation",
            did,
            vid,
            e.get("ip"),
            e.get("mac"),
            e["interface_id"],
            vlan,
            dataset,
        )
        snapshot.endpoints.append(e)

    for name, result in results.items():
        if not result.ok:
            continue
        for i in list(interfaces.values()):
            if i["device"] != name or not fresh(i):
                continue
            if i.get("mac") and not i.get("addresses"):
                endpoint(
                    name,
                    {
                        "mac": i["mac"],
                        "interface": i["name"],
                        "vrf": i.get("vrf"),
                        "vlan": i.get("vlan"),
                        "kind": "owned",
                        "source": "interface",
                    },
                    "interfaces",
                )
            for addr in i.get("addresses", []):
                endpoint(
                    name,
                    {
                        "ip": str(ip_interface(addr).ip),
                        "prefix": addr,
                        "mac": i.get("mac"),
                        "interface": i["name"],
                        "vrf": i.get("vrf"),
                        "vlan": i.get("vlan"),
                        "kind": "owned",
                        "source": "interface",
                    },
                    "interfaces",
                )
        for entry in (
            result.arp_table if result.dataset_status.get("arp", False) else []
        ):
            row = asdict(entry)
            if result.device.platform == "paloalto_panos":
                row["vrf"] = interface(name, entry.interface).get("vrf", "unknown")
            endpoint(name, {**row, "kind": "neighbor", "source": "arp"}, "arp")
        for entry in (
            result.mac_table
            if result.dataset_status.get("mac_address_table", False)
            else []
        ):
            endpoint(
                name,
                {
                    "mac": entry.mac,
                    "vlan": entry.vlan,
                    "interface": entry.port,
                    "kind": "learned",
                    "source": "mac",
                },
                "mac_address_table",
            )
    # Correlate ARP and FDB only within the same device and VLAN; preserve all attachments.
    for e in snapshot.endpoints:
        if e["kind"] != "neighbor" or not e.get("vlan"):
            continue
        for m in snapshot.endpoints:
            if (
                m["kind"] == "learned"
                and m["device_id"] == e["device_id"]
                and m["mac"] == e["mac"]
                and m["vlan"] == e["vlan"]
            ):
                if interface_kind(m["interface"]) in {"physical", "aggregate"}:
                    e["attachments"].append(m["interface_id"])
        e["attachments"] = sorted(set(e["attachments"]))
    links = {}

    def link(a, b, ai, bi, source, dataset, confirmation="confirmed", evidence=None):
        if a not in device_rows or b not in device_rows or a == b:
            return
        ia = interface(a, ai)
        ib = interface(b, bi)
        physical = ia["kind"] == ib["kind"] == "physical" and not any(
            i["name"].startswith("Management") for i in (ia, ib)
        )
        if not physical:
            confirmation = "candidate"
        kind = "physical" if physical and confirmation == "confirmed" else "adjacency"
        lid = stable_id("link", *sorted([ia["id"], ib["id"]]), kind)
        ev = {
            "source": source,
            "observed_by": device_rows[a]["id"],
            **stamp(a, dataset, source),
            **(evidence or {}),
        }
        if lid in links:
            links[lid]["evidence"].append(ev)
            return
        links[lid] = {
            "id": lid,
            "from": a,
            "to": b,
            "from_id": device_rows[a]["id"],
            "to_id": device_rows[b]["id"],
            "local_interface": ia["name"],
            "remote_interface": ib["name"],
            "local_interface_id": ia["id"],
            "remote_interface_id": ib["id"],
            "type": kind,
            "confirmation": confirmation,
            "source": source,
            "evidence": [ev],
            "confidence": 0.98 if confirmation == "confirmed" else 0.5,
            **stamp(a, dataset, source),
        }

    for name, result in results.items():
        if not result.ok:
            continue
        for n in (
            result.lldp_neighbors
            if result.dataset_status.get("lldp_neighbors", False)
            else []
        ):
            remote = resolve_name(n.neighbor)
            if remote:
                link(
                    name,
                    remote,
                    n.local_interface,
                    n.neighbor_interface,
                    "lldp",
                    "lldp_neighbors",
                )
        # MAC equality is evidence of adjacency, never proof of a cable.
        if result.device.platform == "paloalto_panos":
            for i in list(interfaces.values()):
                if (
                    i["device"] != name
                    or i["kind"] != "physical"
                    or not mac_address(i.get("mac"))
                ):
                    continue
                parents = {i["id"], i.get("aggregate_id")}
                vlans = {
                    r.get("vlan")
                    for r in interfaces.values()
                    if r.get("parent_id") in parents and r.get("vlan")
                }
                for e in snapshot.endpoints:
                    if (
                        e["device"] == name
                        or e.get("mac") != mac_address(i["mac"])
                        or e["kind"] not in {"neighbor", "learned"}
                    ):
                        continue
                    if vlans and e.get("vlan") and e["vlan"] not in vlans:
                        continue
                    link(
                        name,
                        e["device"],
                        i["name"],
                        e["interface"],
                        "ip_mac_inferred",
                        "interfaces",
                        "candidate",
                        {
                            "mac": e["mac"],
                            "ip": e.get("ip"),
                            "vlan": e.get("vlan"),
                            "observation_id": e["id"],
                        },
                    )
    for row in design.get("links", []):
        link(
            row["from"],
            row["to"],
            row["local_interface"],
            row["remote_interface"],
            "nautobot",
            "design",
            (
                "confirmed"
                if str(row.get("status", "")).lower() in {"connected", "active"}
                else "candidate"
            ),
        )
    # Multiple remote ports claimed for one physical port are conflicting
    # evidence (for example a shared segment), not several verified cables.
    claims = {}
    for row in links.values():
        if row["confirmation"] != "confirmed":
            continue
        a, b = row["local_interface_id"], row["remote_interface_id"]
        claims.setdefault(a, set()).add(b)
        claims.setdefault(b, set()).add(a)
    for row in links.values():
        if row["confirmation"] == "confirmed" and any(
            len(claims.get(row[key], set())) > 1
            for key in ("local_interface_id", "remote_interface_id")
        ):
            row.update(confirmation="conflicting", type="adjacency")
            snapshot.warnings.append(
                f"Conflicting port claims: {row['from']}:{row['local_interface']} ↔ {row['to']}:{row['remote_interface']}"
            )
    snapshot.links = list(links.values())
    snapshot.interfaces = list(interfaces.values())
    # Retain failed datasets with their ORIGINAL success time, never renew old evidence.
    datasets = {
        "interfaces": "interfaces",
        "routes": "routes",
        "arp": "arp",
        "mac_address_table": "mac_address_table",
        "lldp_neighbors": "lldp_neighbors",
    }
    if previous:
        for attr in ("interfaces", "routes", "endpoints", "links"):
            current = getattr(snapshot, attr)
            existing = {r["id"]: r for r in current}
            for old in getattr(previous, attr):
                did = old.get("device_id") or old.get("from_id")
                d = by_id.get(did)
                if not d:
                    continue
                result = results.get(d["name"])
                dataset = old.get("dataset", "interfaces")
                key = datasets.get(dataset, dataset)
                failed = (
                    not result
                    or not result.ok
                    or not result.dataset_status.get(key, False)
                )
                if not failed:
                    continue
                if (
                    old.get("id") in existing
                    and existing[old["id"]].get("freshness") != "unknown"
                ):
                    continue
                row = {
                    **old,
                    "last_attempt_at": d["last_attempt_at"],
                    "freshness": "stale",
                    "collection_error": d.get("error") or "dataset_failed",
                }
                if "device" in row:
                    row["device"] = d["name"]
                if "from" in row:
                    row["from"] = d["name"]
                if old.get("id") in existing:
                    existing[old["id"]].update(row)
                else:
                    current.append(row)
                    existing[row["id"]] = row
    # Preserve VRF records referenced by retained historical observations.
    if previous:
        for v in previous.vrfs:
            if v["device_id"] in by_id:
                vrfs.setdefault(v["id"], {**v, "device": by_id[v["device_id"]]["name"]})
    # Names are presentation fields; references remain UUID-based across renames.
    for collection in (
        snapshot.interfaces,
        snapshot.routes,
        snapshot.endpoints,
        snapshot.links,
    ):
        for row in collection:
            if row.get("device_id") in by_id:
                row["device"] = by_id[row["device_id"]]["name"]
            for key in ("from", "to"):
                if row.get(key + "_id") in by_id:
                    row[key] = by_id[row[key + "_id"]]["name"]
    snapshot.vrfs = list(vrfs.values())
    return refresh_freshness(snapshot)


def refresh_freshness(snapshot: TopologySnapshot) -> TopologySnapshot:
    now = datetime.now(timezone.utc)
    for rows in (
        snapshot.devices,
        snapshot.interfaces,
        snapshot.routes,
        snapshot.endpoints,
        snapshot.links,
    ):
        for row in rows:
            if row.get("freshness") != "unknown":
                row["freshness"] = "fresh" if fresh(row, now) else "stale"
    return snapshot


def atomic_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def save_snapshot(snapshot: TopologySnapshot, path: Path | None = None) -> None:
    atomic_json(
        path or Path(os.getenv("AIOPS_TOPOLOGY_PATH", "/app/data/topology.json")),
        asdict(snapshot),
    )


def load_snapshot(path: Path | None = None) -> TopologySnapshot | None:
    try:
        raw = json.loads(
            (
                path
                or Path(os.getenv("AIOPS_TOPOLOGY_PATH", "/app/data/topology.json"))
            ).read_text()
        )
        if "schema_version" not in raw:
            # Legacy snapshots remain readable but cannot prove fresh forwarding paths.
            for d in raw.get("devices", []):
                d["id"] = stable_id("device", d["name"])
                d["aliases"] = [d["name"]]
                d["freshness"] = "stale"
            ids = {d["name"]: d["id"] for d in raw.get("devices", [])}
            for attr in ("interfaces", "endpoints", "links"):
                for row in raw.get(attr, []):
                    did = ids.get(row.get("device") or row.get("from"))
                    row["device_id"] = did
                    row["freshness"] = "stale"
                    row["id"] = stable_id(
                        "legacy", attr, json.dumps(row, sort_keys=True)
                    )
                    if attr == "links":
                        row["confirmation"] = "candidate"
                        row["type"] = "adjacency"
            raw["schema_version"] = "1.1"
        snapshot = TopologySnapshot(
            **{f.name: raw[f.name] for f in fields(TopologySnapshot) if f.name in raw}
        )
        return refresh_freshness(snapshot)
    except (OSError, ValueError, TypeError, KeyError):
        return None


def load_layout(path: Path | None = None) -> dict[str, dict[str, float]]:
    try:
        raw = json.loads(
            (
                path
                or Path(
                    os.getenv(
                        "AIOPS_TOPOLOGY_LAYOUT_PATH", "/app/data/topology-layout.json"
                    )
                )
            ).read_text()
        )
        layout = {
            str(k): {"x": float(v["x"]), "y": float(v["y"])} for k, v in raw.items()
        }
        snapshot = load_snapshot()
        if snapshot:
            for d in snapshot.devices:
                for key in [d["id"], d["name"], *d.get("aliases", [])]:
                    if key in layout:
                        layout[d["name"]] = layout[key]
                        break
        return layout
    except (OSError, ValueError, TypeError, KeyError):
        return {}


def save_layout(layout: dict[str, dict[str, float]], path: Path | None = None) -> None:
    target = path or Path(
        os.getenv("AIOPS_TOPOLOGY_LAYOUT_PATH", "/app/data/topology-layout.json")
    )
    snapshot = load_snapshot()
    normalized = {**layout}
    if snapshot:
        for d in snapshot.devices:
            if d["name"] in layout:
                normalized[d["id"]] = layout[d["name"]]
    atomic_json(target, normalized)


def find_path(snapshot, src, dst, vrf="all", **kwargs):
    from .path_query import find_path as query

    return query(snapshot, src, dst, vrf=vrf, **kwargs)
