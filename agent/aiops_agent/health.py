"""Deterministic, passive health scoring; missing evidence never earns points."""
from __future__ import annotations

import math
from datetime import datetime, timezone

from .collector import COMMANDS_BY_PLATFORM
from .model import interface_name
from .lifecycle import policy
from .bgp_baseline import evaluate as evaluate_bgp_baseline
from .igp_baseline import evaluate_ospf

VERSION = "1.2"
WEIGHTS = {"collection": 40, "interfaces": 20, "cpu": 10, "memory": 10,
           "bgp": 5, "ospf": 3, "overlay": 2, "power": 5, "module": 5}
GOOD_HARDWARE = {"ok", "up", "online", "active", "normal", "operational", "good"}
BAD_HARDWARE = {"fail", "failed", "down", "error", "fault", "not_ok", "offline", "power_loss"}


def recent(stamp, now, ttl):
    try:
        age = (now - datetime.fromisoformat(stamp.replace("Z", "+00:00"))).total_seconds()
        return 0 <= age <= ttl
    except (ValueError, TypeError, AttributeError):
        return False


def utilization_score(value):
    if not isinstance(value, (float, int)) or not math.isfinite(value) or not 0 <= value <= 100:
        return None
    points = [(0, 100), (70, 100), (80, 80), (90, 50), (95, 20), (100, 0)]
    for (a, sa), (b, sb) in zip(points, points[1:]):
        if value <= b:
            return sa + (sb - sa) * (value - a) / (b - a)
    return 0


def check(key, values=None, reason="missing", details=None, excluded=False):
    values = list(values or [])
    valid = [v for v in values if v is not None]
    return {"key": key, "weight": WEIGHTS[key], "excluded": excluded,
            "score": sum(valid) / len(values) if values else 0,
            "coverage": len(valid) / len(values) if values else 0,
            "reason": reason, "details": details or []}


def device_health(device, result, snapshot, now, ttl):
    checks = []
    current = bool(result and recent(result.collected_at, now, ttl))
    usable = current and result.ok
    checks.append(check("collection", [100 if result.ok else 0] if current else [],
                        "collected" if usable else "collection_failed" if current else "stale_or_missing"))
    commands = COMMANDS_BY_PLATFORM.get(device.platform, {})
    datasets = result.dataset_status if usable else {}
    # Confirmed cabling identifies expected ports. Idle/unconnected ports are not scored.
    expected = set()
    snapshot_current = snapshot is not None and recent(snapshot.observed_at, now, ttl)
    if snapshot_current:
        for link in snapshot.links:
            if link.get("type") != "physical" or link.get("confirmation") != "confirmed":
                continue
            for name, port in [(link.get("from"), link.get("local_interface")),
                               (link.get("to"), link.get("remote_interface"))]:
                if name == device.name:
                    expected.add(interface_name(port))
    if expected and datasets.get("interfaces"):
        interfaces = {interface_name(i["name"]): i for i in result.interfaces}
        values, details = [], []
        for name in sorted(expected):
            row = interfaces.get(name, {})
            if row.get("admin_up") is False:
                details.append({"interface": name, "reason": "administratively_disabled"})
                continue
            oper = row.get("oper_up")
            # PA does not always expose admin state, but explicit oper state is evidence.
            value = 100 if oper is True else 0 if oper is False else None
            values.append(value)
            if value != 100:
                details.append({"interface": name, "reason": "oper_down" if value == 0 else "unknown"})
        checks.append(check("interfaces", values, "confirmed_ports", details, excluded=not values))
    else:
        checks.append(check("interfaces", reason="no_confirmed_ports" if not expected else "dataset_failed"))
    resource_keys = ["resources"] if device.platform == "paloalto_panos" else ["version", "processes"]
    for key, attr in [("cpu", "cpu_percent"), ("memory", "memory_percent")]:
        value = getattr(result.resources, attr, None) if usable and any(datasets.get(k) for k in resource_keys) else None
        score = utilization_score(value)
        checks.append(check(key, [score], "utilization" if score is not None else "missing",
                            [{"percent": value}] if score is not None else []))
    for key, attr, dataset in [("bgp", "bgp", "bgp_summary"), ("ospf", "ospf", "ospf_neighbors"),
                               ("overlay", "nve", "nve_peers")]:
        baseline_check = evaluate_bgp_baseline(device, result, usable) if key == "bgp" else None
        if key == "ospf":
            baseline_check = evaluate_ospf(device)
        if baseline_check is not None:
            checks.append(check(key, **baseline_check))
        elif dataset not in commands:
            checks.append(check(key, reason="not_collected_for_platform", excluded=True))
        elif not datasets.get(dataset):
            checks.append(check(key, reason="dataset_failed"))
        else:
            # AFI/SAFI rows must not double-count the same BGP session.
            peers = {}
            for peer in getattr(result, attr):
                identity = (peer.neighbor, peer.context)
                healthy = peer.up or (key == "ospf" and peer.raw_state.lower().startswith(("2way", "2-way")))
                peers[identity] = min(peers.get(identity, 100), 100 if healthy else 0)
            checks.append(check(key, peers.values(), "observed_neighbors" if peers else "no_observed_neighbors",
                                [{"neighbor": n, "context": c, "reason": "neighbor_down"}
                                 for (n, c), s in peers.items() if s == 0], excluded=not peers))
    for key in ["power", "module"]:
        if key not in commands:
            checks.append(check(key, reason="not_collected_for_platform", excluded=True))
        elif not datasets.get(key):
            checks.append(check(key, reason="dataset_failed"))
        else:
            values, details = [], []
            parts = [c for c in result.hardware_components if c.component_type == key]
            for part in parts:
                state = part.status.lower()
                if part.name == "none" and part.model == "not_applicable":
                    continue
                score = 100 if state in GOOD_HARDWARE else 0 if state in BAD_HARDWARE else None
                values.append(score)
                if score != 100:
                    details.append({"component": part.name, "status": part.status})
            checks.append(check(key, values, "component_state", details,
                                excluded=bool(parts) and not values))
    total = sum(c["weight"] for c in checks if not c["excluded"])
    earned = sum(c["weight"] * c["score"] / 100 for c in checks if not c["excluded"])
    known = sum(c["weight"] * c["coverage"] for c in checks if not c["excluded"])
    for c in checks:
        c["deduction_points"] = round(c["weight"] * (c["coverage"] - c["score"] / 100) * 100 / total, 2) if not c["excluded"] else 0
        c["unassessed_points"] = round(c["weight"] * (1 - c["coverage"]) * 100 / total, 2) if not c["excluded"] else 0
    score = 100 * earned / total
    coverage = 100 * known / total
    return {"device": device.name, "device_id": device.id, "site": device.site,
            "score": round(score, 1), "coverage_percent": round(coverage, 1),
            "observed_score": round(100 * earned / known, 1) if known else None,
            "state": "unknown" if not known else "partial" if coverage < 100 else "assessed",
            "deduction_points": round(100 * (known - earned) / total, 1),
            "unassessed_points": round(100 - coverage, 1),
            "collected_at": result.collected_at if result else None, "checks": checks}


def network_health(devices, results, snapshot, site="all", now=None, ttl=600):
    now = now or datetime.now(timezone.utc)
    selected = [d for d in devices if site == "all" or d.site == site]
    included = [d for d in selected if policy(d,now)['health_included']]
    excluded = [dict(device=d.name,id=d.id,site=d.site,**policy(d,now)) for d in selected if not policy(d,now)['health_included']]
    rows = [device_health(d, results.get(d.name), snapshot, now, ttl) for d in included]
    mean = lambda key: round(sum(r[key] for r in rows) / len(rows), 1) if rows else None
    coverage = mean("coverage_percent")
    score = mean("score")
    return {"model_version": VERSION, "observed_at": now.isoformat(), "site": site,
            "score": score if coverage else None, "coverage_percent": coverage,
            "deduction_points": mean("deduction_points"), "unassessed_points": mean("unassessed_points"),
            "state": "unknown" if not coverage else "partial" if coverage < 100 else "assessed",
            "device_count": len(rows), "devices": rows,"inventory_count":len(selected),
            "excluded_count":len(excluded),"excluded_devices":excluded,
            "method": "Equal-device mean of weighted verified health points. Missing evidence earns no points; this is not an SLA.",
            "limits": ["Current observations, not sustained incident or packet-delivery proof",
                       "No latency/loss, bandwidth saturation, error-counter deltas, threat-policy or application probes in v1",
                       "Approved BGP baselines detect missing peers; protocols without a baseline retain observation-only behavior",
                       "Collection failure is not proof of physical unreachability"]}


def prometheus_health(payload):
    # A request-local registry avoids stale labels and races between scrapes.
    from prometheus_client import CollectorRegistry, Gauge, generate_latest
    registry = CollectorRegistry()
    fields = {"score": "score", "coverage_percent": "coverage_percent",
              "deduction_points": "deduction_points", "unassessed_points": "unassessed_points"}
    for suffix, key in fields.items():
        gauge = Gauge("aiops_health_" + suffix, "Health v1 " + suffix + "; read together with coverage.",
                      ["device", "site"], registry=registry)
        for row in payload["devices"]:
            gauge.labels(row["device"], row["site"]).set(row[key])
    return generate_latest(registry)
