"""Normalized topology v1 and path resolution.

The topology is deliberately stored as a small JSON snapshot.  This keeps the
collector independent from a database while giving the API a durable view
across agent restarts; the schema can later be moved to PostgreSQL unchanged.
"""
from __future__ import annotations

import json
import os
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class TopologySnapshot:
    observed_at: str
    devices: list[dict[str, Any]] = field(default_factory=list)
    interfaces: list[dict[str, Any]] = field(default_factory=list)
    links: list[dict[str, Any]] = field(default_factory=list)
    endpoints: list[dict[str, Any]] = field(default_factory=list)


def build_snapshot(results: dict[str, Any], design: dict[str, list[dict[str, Any]]] | None = None) -> TopologySnapshot:
    now = datetime.now(timezone.utc).isoformat()
    snapshot = TopologySnapshot(observed_at=now)
    known = set(results)
    for name, result in results.items():
        device = result.device
        snapshot.devices.append({"name": name, "role": device.role, "site": device.site,
                                 "host": device.host, "platform": device.platform,
                                 "up": bool(result.ok), "error": result.error})
        for interface in result.interfaces:
            row = {"device": name, "name": str(interface.get("name") or ""),
                   "admin_up": interface.get("admin_up"), "oper_up": interface.get("oper_up"),
                   "description": interface.get("description", ""), "speed_bps": interface.get("speed_bps"),
                   "observed_at": now}
            if row["name"]:
                snapshot.interfaces.append(row)
        for neighbor in result.lldp_neighbors:
            if neighbor.neighbor not in known:
                continue
            edge = {"from": name, "to": neighbor.neighbor,
                    "local_interface": neighbor.local_interface,
                    "remote_interface": neighbor.neighbor_interface,
                    "type": "physical", "source": "lldp", "confidence": 0.98,
                    "observed_at": now}
            snapshot.links.append(edge)
        for entry in result.arp_table:
            snapshot.endpoints.append({"ip": entry.ip, "mac": entry.mac, "vrf": entry.vrf,
                                        "device": name, "interface": entry.interface,
                                        "source": "arp", "observed_at": now})
        for entry in result.mac_table:
            snapshot.endpoints.append({"mac": entry.mac, "vlan": entry.vlan,
                                        "device": name, "interface": entry.port,
                                        "source": "mac", "observed_at": now})
    if design:
        # Design data supplies inventory and cabling even when a device is not
        # currently reachable; runtime fields remain authoritative when both exist.
        runtime_devices = {row["name"]: row for row in snapshot.devices}
        for row in design.get("devices", []):
            existing = runtime_devices.get(row.get("name"))
            if existing:
                existing.update({k: v for k, v in row.items() if k not in {"up", "error"}})
            elif row.get("name"):
                snapshot.devices.append({**row, "up": False, "error": "not_collected"})
        existing_interfaces = {(row.get("device"), row.get("name")) for row in snapshot.interfaces}
        snapshot.interfaces.extend(row for row in design.get("interfaces", [])
                                   if (row.get("device"), row.get("name")) not in existing_interfaces)
        known_links = {(row.get("from"), row.get("to"), row.get("local_interface"), row.get("remote_interface"))
                       for row in snapshot.links}
        snapshot.links.extend(row for row in design.get("links", [])
                              if (row.get("from"), row.get("to"), row.get("local_interface"), row.get("remote_interface")) not in known_links)
        known_endpoints = {(row.get("ip"), row.get("mac"), row.get("device"), row.get("interface")) for row in snapshot.endpoints}
        snapshot.endpoints.extend(row for row in design.get("endpoints", [])
                                  if (row.get("ip"), row.get("mac"), row.get("device"), row.get("interface")) not in known_endpoints)
    return snapshot


def save_snapshot(snapshot: TopologySnapshot, path: Path | None = None) -> None:
    target = path or Path(os.getenv("AIOPS_TOPOLOGY_PATH", "/app/data/topology.json"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(asdict(snapshot), ensure_ascii=False, indent=2), encoding="utf-8")


def load_snapshot(path: Path | None = None) -> TopologySnapshot | None:
    target = path or Path(os.getenv("AIOPS_TOPOLOGY_PATH", "/app/data/topology.json"))
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
        return TopologySnapshot(**raw)
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return None


def find_path(snapshot: TopologySnapshot, src: str, dst: str, vrf: str = "all") -> dict[str, Any]:
    def resolve(query: str) -> list[dict[str, Any]]:
        q = query.lower()
        rows = [e for e in snapshot.endpoints if any(str(e.get(k, "")).lower() == q for k in ("ip", "mac"))]
        if not rows and ":" in query:
            device, interface = query.split(":", 1)
            rows = [{"device": device, "interface": interface, "source": "explicit"}]
        if vrf != "all":
            rows = [e for e in rows if str(e.get("vrf", "")).lower() == vrf.lower()]
        return rows

    source, destination = resolve(src), resolve(dst)
    source_row, destination_row = (source[0] if source else {}), (destination[0] if destination else {})
    start, end = source_row.get("device"), destination_row.get("device")
    graph: dict[str, list[dict[str, Any]]] = {}
    for edge in snapshot.links:
        graph.setdefault(edge["from"], []).append(edge)
        graph.setdefault(edge["to"], []).append({**edge, "from": edge["to"], "to": edge["from"],
                                                   "local_interface": edge.get("remote_interface"),
                                                   "remote_interface": edge.get("local_interface")})
    edges: list[dict[str, Any]] = []
    if start and end and start != end:
        queue = deque([(start, [])]); seen = {start}
        while queue:
            node, path = queue.popleft()
            for edge in graph.get(node, []):
                if edge["to"] in seen: continue
                next_path = [*path, edge]
                if edge["to"] == end:
                    edges = next_path; queue.clear(); break
                seen.add(edge["to"]); queue.append((edge["to"], next_path))
            if edges: break
    steps: list[dict[str, Any]] = []
    if source_row: steps.append({"hop": 1, "device": start, "interface": source_row.get("interface"), "role": "source_attachment", "evidence": source_row.get("source")})
    for index, edge in enumerate(edges, len(steps) + 1):
        steps.append({"hop": index, **edge, "role": "underlay"})
    if destination_row: steps.append({"hop": len(steps) + 1, "device": end, "interface": destination_row.get("interface"), "role": "destination_attachment", "evidence": destination_row.get("source")})
    return {"status": "ok" if source_row and destination_row and (start == end or edges) else "incomplete",
            "source": source_row, "destination": destination_row, "path": steps,
            "observed_at": snapshot.observed_at,
            "warnings": ([] if edges or start == end else ["no complete physical path in current topology"])}
