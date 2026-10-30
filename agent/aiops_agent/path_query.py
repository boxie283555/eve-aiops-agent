"""One conservative path engine for both the tools page and public API."""

from __future__ import annotations

from collections import deque
from ipaddress import ip_address, ip_network, ip_interface

from .model import interface_name, mac_address
from .forwarding import vlan_allowed, evaluate_policy
from .topology import fresh
from .flow import FlowState
from .panos_policy import inspect_firewall


def find_path(
    snapshot,
    src,
    dst,
    vrf="all",
    src_device="all",
    dst_device="all",
    src_vrf="all",
    dst_vrf="all",
    mode="auto",
    site="all",
    analysis="routing",
    protocol="any",
    source_port=None,
    destination_port=None,
    application=None,
    ingress_interface=None,
):
    devices = {d["id"]: d for d in snapshot.devices if site == "all" or d.get("site") == site}
    interfaces = {i["id"]: i for i in snapshot.interfaces if i["device_id"] in devices}
    by_name = {
        (i["device_id"], interface_name(i["name"])): i for i in interfaces.values()
    }

    def named(value):
        return [
            d
            for d in devices.values()
            if value.lower()
            in {str(v).lower() for v in [d["name"], d["id"], *d.get("aliases", [])]}
        ]

    def resolve(query, device_filter, vrf_filter):
        q = query.strip()
        allowed = (
            {d["id"] for d in named(device_filter)}
            if device_filter != "all"
            else set(devices)
        )

        def permitted(e):
            return e.get("device_id") in allowed and (
                vrf_filter == "all" or vrf_filter in {e.get("vrf"), e.get("vrf_id")}
            )

        mac = mac_address(q)
        try:
            ip = str(ip_address(q))
        except ValueError:
            ip = None
        if not ip and not mac and ":" in q:
            device, iface = q.rsplit(":", 1)
            matches = named(device)
            rows = []
            for d in matches:
                i = by_name.get((d["id"], interface_name(iface)))
                if i:
                    e = {
                        **i,
                        "interface": i["name"],
                        "interface_id": i["id"],
                        "source": "explicit",
                        "kind": "owned",
                        "query": q,
                    }
                    if permitted(e):
                        rows.append(e)
            return rows
        rows = [
            e
            for e in snapshot.endpoints
            if permitted(e)
            and (
                (ip and e.get("ip") == ip) or (mac and mac_address(e.get("mac")) == mac)
            )
        ]
        owned = [e for e in rows if e.get("kind") == "owned"]
        if owned:
            rows = owned
        # Merge repeated observations only at exactly the same scoped attachment.
        grouped = {}
        for row in rows:
            key = (
                row.get("device_id"),
                row.get("vrf_id"),
                row.get("interface_id"),
                row.get("vlan"),
                row.get("mac") if ip else row.get("ip"),
            )
            if key not in grouped:
                grouped[key] = {**row, "query": q, "observations": []}
            grouped[key]["observations"].append(row["id"])
        return list(grouped.values())

    source = resolve(src, src_device, src_vrf if src_vrf != "all" else vrf)
    destination = resolve(dst, dst_device, dst_vrf if dst_vrf != "all" else vrf)
    base = {
        "status": "incomplete",
        "source": source[0] if len(source) == 1 else {"query": src},
        "destination": destination[0] if len(destination) == 1 else {"query": dst},
        "source_candidates": source,
        "destination_candidates": destination,
        "path": [],
        "paths": [],
        "observed_at": snapshot.observed_at,
        "warnings": [],
        "schema_version": snapshot.schema_version,
        "site": site,
        "analysis": analysis,
        "evidence_level": "configuration",
        "flow_model_version": "1.0",
        "connection": "new",

    }

    def finish(status, warning=None):
        base["status"] = status
        for steps in [base["path"], *[b["path"] for b in base["paths"]]]:
            for hop, item in enumerate(steps, 1):
                item["hop"] = hop
                item.setdefault(
                    "role",
                    "underlay" if item.get("type") == "physical" else item.get("type"),
                )
        for name in ("source", "destination"):
            endpoint = base[name]
            ids = set(endpoint.get("observations", []))
            endpoint["arp_matches"] = [
                e
                for e in snapshot.endpoints
                if e["id"] in ids and e.get("kind") == "neighbor"
            ]
            endpoint["mac_matches"] = [
                e
                for e in snapshot.endpoints
                if e["id"] in ids and e.get("kind") == "learned"
            ]
        if warning:
            base["warnings"].append(warning)
        base["evidence"] = {
            "notes": base["warnings"],
            "lldp_path": [s for s in base["path"] if s.get("type") == "physical"],
        }
        return base

    if analysis not in {"routing", "flow"}:
        return finish("invalid", "Unknown analysis mode")
    if analysis == "flow" and mode == "physical":
        return finish("invalid", "Flow analysis requires routed or auto mode")
    if mode not in {"auto", "physical", "routed"}:
        return finish("invalid", "Unknown path mode")
    if len(source) > 1 or len(destination) > 1:
        return finish(
            "ambiguous",
            "Multiple scoped locations match; select a device and VRF. No location was chosen automatically.",
        )
    if not source:
        return finish(
            "not_found",
            "Source device/interface or endpoint was not found in inventory.",
        )
    try:
        destination_ip = str(ip_address(dst))
    except ValueError:
        destination_ip = destination[0].get("ip") if destination else None
    if not destination and not destination_ip:
        return finish(
            "not_found",
            "Destination device/interface or endpoint was not found in inventory.",
        )
    if not destination and dst_device != "all":
        return finish("not_found", "Destination is not known on the selected device.")
    for e in source + destination:
        d = devices.get(e.get("device_id"), {})
        if not fresh(e) or not d.get("up") or not fresh(d):
            return finish(
                "stale",
                "An endpoint or its collecting device has missing, failed or expired observations.",
            )
        i = interfaces.get(e.get("interface_id"), {})
        if i.get("oper_up") is False:
            return finish("incomplete", "An endpoint interface is down.")
    source = source[0]
    target = destination[0] if destination else None
    adjacency = {}
    for edge in snapshot.links:
        if (
            edge.get("type") != "physical"
            or edge.get("confirmation") != "confirmed"
            or not fresh(edge)
        ):
            continue
        if any(
            not devices.get(edge[k], {}).get("up")
            or not fresh(devices.get(edge[k], {}))
            for k in ("from_id", "to_id")
        ):
            continue
        if any(
            interfaces.get(edge[k], {}).get("oper_up") is False
            or not fresh(interfaces.get(edge[k], {}))
            for k in ("local_interface_id", "remote_interface_id")
        ):
            continue
        adjacency.setdefault(edge["from_id"], []).append(edge)
        reverse = {
            **edge,
            "from": edge["to"],
            "to": edge["from"],
            "from_id": edge["to_id"],
            "to_id": edge["from_id"],
            "local_interface": edge["remote_interface"],
            "remote_interface": edge["local_interface"],
            "local_interface_id": edge["remote_interface_id"],
            "remote_interface_id": edge["local_interface_id"],
        }
        adjacency.setdefault(reverse["from_id"], []).append(reverse)

    def physical_members(iid, seen=None):
        seen = set() if seen is None else seen
        if iid in seen:
            return set()
        seen.add(iid)
        i = interfaces.get(iid, {})
        if not i or not fresh(i) or i.get("oper_up") is False:
            return set()
        if i.get("kind") == "physical":
            return {iid}
        if i.get("name", "").startswith("Vlan") and i.get("vlan"):
            out = set()
            for port in interfaces.values():
                if port["device_id"] == i["device_id"] and vlan_allowed(port, i["vlan"]):
                    out.update(physical_members(port["id"], seen.copy()))
            return out
        if i.get("parent_id"):
            return physical_members(i["parent_id"], seen)
        out = set()
        for member in i.get("members", []):
            out.update(physical_members(member, seen))
        return out

    def physical_path(start, end):
        queue = deque([(start, [])])
        visited = {start}
        while queue:
            node, path = queue.popleft()
            if node == end:
                return path
            for edge in sorted(adjacency.get(node, []), key=lambda e: e["id"]):
                if edge["to_id"] not in visited:
                    visited.add(edge["to_id"])
                    queue.append((edge["to_id"], path + [edge]))
        return None

    def step(row, kind):
        return {
            **row,
            "type": kind,
            "label": row.get("label") or kind.replace("_", " "),
        }

    use_routing = mode == "routed" or (mode == "auto" and destination_ip is not None)
    base["mode"] = "routed" if use_routing else "physical"
    if not use_routing:
        if target is None:
            return finish(
                "not_found", "Physical mode requires a known destination attachment."
            )
        path = physical_path(source["device_id"], target["device_id"])
        if path is None:
            return finish(
                "incomplete",
                "No fresh confirmed physical path; candidate or overlay edges are excluded.",
            )
        base["path"] = [
            step(
                {"device": source["device"], "interface": source["interface"]},
                "source_attachment",
            ),
            *[step(e, "physical") for e in path],
            step(
                {"device": target["device"], "interface": target["interface"]},
                "destination_attachment",
            ),
        ]
        base["paths"] = [{"status": "ok", "path": base["path"]}]
        return finish(
            "ok",
            "This is physical connectivity, not proof of the selected traffic forwarding path.",
        )
    if not destination_ip:
        return finish("incomplete", "A destination IP is required for route lookup.")
    original_destination_ip = destination_ip
    try:
        initial_flow = FlowState(source.get("ip") or str(ip_address(src)), destination_ip,
                                protocol, source_port, destination_port, application) if analysis == "flow" else None
    except ValueError as exc:
        return finish("invalid", str(exc))
    if initial_flow:
        base["flow"] = initial_flow.data()
        base["warnings"].append("New-connection configuration prediction. Existing-session return traffic, threat profiles, ASIC installation and packet delivery are not verified. Application, when supplied, is an assumption.")
    addr = ip_address(destination_ip)
    start_vrf = source.get("vrf_id")
    if not start_vrf or source.get("vrf") in {"unknown", None}:
        return finish(
            "incomplete",
            "Source VRF is unknown; collect interface VRF membership or select a scoped endpoint.",
        )
    if analysis == "routing":
        base["warnings"].append(
            "Configured forwarding candidates using RIB, VLAN, VXLAN and supported EOS traffic policies; firewall security/NAT, ASIC installation, packet delivery and actual ECMP hashing are not verified."
        )

    def lookup(did, vid, address, seen=None):
        seen = set() if seen is None else seen
        key = (did, vid, str(address))
        if key in seen:
            return [], "Recursive next-hop loop"
        seen = seen | {key}
        routes = []
        for r in snapshot.routes:
            if r.get("device_id") != did or r.get("vrf_id") != vid:
                continue
            net = ip_network(r["prefix"])
            if address.version == net.version and address in net:
                routes.append(r)
        if not routes:
            return [], "No matching route in this device/VRF"
        # Select the actual most-specific prefix BEFORE testing freshness; never fall back to a less-specific fresh route.
        longest = max(ip_network(r["prefix"]).prefixlen for r in routes)
        routes = [r for r in routes if ip_network(r["prefix"]).prefixlen == longest]
        if any(not fresh(r) for r in routes):
            return [], "Most-specific route evidence is stale"
        out = []
        for r in routes:
            if (
                r.get("interface_id")
                or r.get("protocol") == "local"
                or r.get("action") in {"drop", "discard", "reject"}
            ):
                out.append(r)
                continue
            if not r.get("next_hop"):
                return [], "Route has no resolved egress or next hop"
            try:
                recursive = ip_address(r["next_hop"])
            except ValueError:
                return [], "Unsupported route next hop"
            hops, error = lookup(did, vid, recursive, seen)
            if error:
                return [], error
            for h in hops:
                out.append(
                    {
                        **r,
                        "interface": h["interface"],
                        "interface_id": h["interface_id"],
                        "next_hop": h.get("next_hop") or r["next_hop"],
                        "recursive_routes": [h["id"], *h.get("recursive_routes", [])],
                        "vtep": h.get("vtep"), "vni": h.get("vni"),
                    }
                )
        return out, None

    def adjacent(did, route, next_ip):
        outgoing = physical_members(route.get("interface_id"))
        egress = interfaces.get(route.get("interface_id"), {})
        neighbors = []
        for edge in adjacency.get(did, []):
            if edge["local_interface_id"] not in outgoing:
                continue
            for e in snapshot.endpoints:
                if (e.get("kind") != "owned" or e.get("device_id") != edge["to_id"]
                        or e.get("ip") != next_ip or not fresh(e)):
                    continue
                if edge["remote_interface_id"] not in physical_members(e.get("interface_id")):
                    continue
                if egress.get("vlan") and e.get("vlan") and str(egress["vlan"]) != str(e["vlan"]):
                    continue
                neighbors.append((edge, e))
        return neighbors

    def tunnel_paths(did, route):
        """Walk the VTEP's default-VRF RIB, never an arbitrary topology shortcut."""
        local = interfaces.get(route.get("interface_id"), {})
        if str(local.get("vrf_vnis", {}).get(route["vrf"])) != str(route.get("vni")):
            return [], "VXLAN egress VRF/VNI mapping is missing"
        owners = []
        for vx in interfaces.values():
            if not vx.get("vxlan_source") or not fresh(vx) or vx.get("oper_up") is False:
                continue
            src_iface = by_name.get((vx["device_id"], vx["vxlan_source"]), {})
            if not fresh(src_iface) or src_iface.get("oper_up") is False:
                continue
            if not any(str(ip_interface(a).ip) == route["vtep"] for a in src_iface.get("addresses", [])):
                continue
            for vrf_name, vni in vx.get("vrf_vnis", {}).items():
                if str(vni) == str(route.get("vni")):
                    vids = [v["id"] for v in snapshot.vrfs if v["device_id"] == vx["device_id"] and v["name"] == vrf_name]
                    owners.extend((vx, vid) for vid in vids)
        if len(owners) != 1:
            return [], "Remote VTEP/VNI has missing or ambiguous ownership"
        remote, remote_vid = owners[0]
        todo = deque([(did, [], set())])
        complete = []
        attempts = 0
        while todo and attempts < 64:
            node, hops, visited = todo.popleft()
            attempts += 1
            if node == remote["device_id"]:
                complete.append((remote, remote_vid, hops))
                continue
            if node in visited:
                return [], "VXLAN underlay routing loop"
            vids = [v["id"] for v in snapshot.vrfs if v["device_id"] == node and v["name"] == "default"]
            if len(vids) != 1:
                return [], "VXLAN underlay VRF is missing"
            rib, error = lookup(node, vids[0], ip_address(route["vtep"]))
            if error:
                return [], "VXLAN underlay: " + error
            for r in rib:
                if r.get("vtep") or r.get("action") in {"drop", "discard", "reject"}:
                    return [], "Unsupported or discarded VXLAN underlay route"
                neighbors = adjacent(node, r, r.get("next_hop") or route["vtep"])
                if not neighbors:
                    return [], "VXLAN underlay next-hop physical adjacency is unconfirmed"
                if len({(e["device_id"], e["vrf_id"], e["interface_id"]) for _, e in neighbors}) > 1:
                    return [], "VXLAN underlay next-hop ownership is ambiguous"
                for edge, e in neighbors:
                    if e.get("vrf") != "default":
                        return [], "VXLAN underlay next-hop VRF mismatch"
                    todo.append((e["device_id"], hops + [step({**edge, "role": "underlay",
                        "route_id": r["id"], "prefix": r["prefix"], "next_hop": r.get("next_hop"),
                        "vtep": route["vtep"], "vni": route["vni"]}, "physical")], visited | {node}))
        if todo:
            return [], "VXLAN underlay expansion limit reached"
        return complete, None

    # Every ECMP branch is kept, with bounded expansion and explicit truncation.
    initial_context = (None, None, False)
    if ingress_interface:
        ingress = by_name.get((source["device_id"], interface_name(ingress_interface)))
        if not ingress or ingress.get("vrf_id") != start_vrf or not fresh(ingress) or ingress.get("oper_up") is False:
            return finish("invalid", "Ingress interface must be fresh, up and in the source device/VRF")
        initial_context = (ingress["id"], ingress.get("vlan"), False)
    elif source.get("kind") != "owned":
        initial_context = (source.get("interface_id"), source.get("vlan"), False)
    base["origin"] = "ingress" if initial_context[0] else "device"
    queue = deque([(source["device_id"], start_vrf, [], set(), initial_context, initial_flow)])
    paths = []
    expansions = 0
    while queue and expansions < 64:
        did, vid, path, seen, context, packet = queue.popleft()
        destination_ip = packet.destination_ip if packet else original_destination_ip
        addr = ip_address(destination_ip)
        state = (did, vid, *context, packet)
        expansions += 1
        if state in seen or len(path) > 96:
            paths.append(
                {
                    "status": "incomplete",
                    "path": path,
                    "reason": "Routing loop or hop limit",
                }
            )
            continue
        if not fresh(devices.get(did, {})) or not devices.get(did, {}).get("up"):
            paths.append(
                {
                    "status": "stale",
                    "path": path,
                    "reason": "Next-hop device state is stale",
                }
            )
            continue
        vrf_name = next((v["name"] for v in snapshot.vrfs if v["id"] == vid), "unknown")
        if packet:
            path = path + [step({"device": devices[did]["name"], "device_id": did,
                "vrf": vrf_name, "interface": interfaces.get(context[0], {}).get("name"),
                "flow_before": packet.data(), "evidence_level": "configuration"}, "flow_state")]
        if packet and devices[did].get("platform") == "paloalto_panos":
            if not context[0]:
                paths.append({"status":"incomplete", "path":path, "reason":"PA flow evaluation requires an ingress logical interface"})
                continue
            def pa_routes(address):
                rows, error = lookup(did, vid, ip_address(address))
                if error:
                    raise ValueError(error)
                if any(r.get("action") in {"drop","discard","reject"} for r in rows):
                    raise ValueError("PA egress route discards traffic")
                if any(not fresh(interfaces.get(r.get("interface_id"), {})) or interfaces.get(r.get("interface_id"), {}).get("oper_up") is False for r in rows):
                    raise ValueError("PA egress interface is down or stale")
                return [(r["interface"], r["id"]) for r in rows]
            evidence = devices[did].get("pa_policy")
            if evidence and not fresh(evidence):
                evidence = None
            decision = inspect_firewall(evidence, packet, interfaces.get(context[0], {}).get("name", ""), pa_routes)
            path = path + [step({**decision, "device":devices[did]["name"], "device_id":did,
                "vrf":vrf_name, "interface":interfaces.get(context[0],{}).get("name"),
                "verification_flow":packet.data()}, "firewall_policy")]
            if decision["status"] != "allow":
                paths.append({"status": "blocked" if decision["status"]=="blocked" else "incomplete",
                    "path":path,"reason":decision["reason"]})
                continue
            before = packet
            packet = FlowState(**decision["flow_after"])
            if packet != before:
                path = path + [step({"device":devices[did]["name"],"device_id":did,
                    "nat_rule":decision.get("nat_rule"),"flow_before":before.data(),"flow_after":packet.data(),
                    "evidence_level":"configuration"}, "nat_translation")]
            destination_ip = packet.destination_ip
            addr = ip_address(destination_ip)
        policy, error = evaluate_policy(devices[did].get("forwarding"), vrf_name,
            packet.source_ip if packet else source.get("ip"), destination_ip, context[1], context[2]) if context[0] else (None, None)
        if error:
            paths.append({"status": "incomplete", "path": path, "reason": error})
            continue
        lookup_addr = addr
        lookup_vid = vid
        if policy:
            path = path + [step({**policy, "device": devices[did]["name"], "device_id": did,
                "vrf": vrf_name, "observed_at": devices[did].get("last_success_at"),
                "label": policy["policy"] + "/" + policy["name"]}, "traffic_policy")]
            if policy["action"] == "drop":
                paths.append({"status": "blocked", "path": path,
                    "reason": f"Configured traffic-policy {devices[did]['name']} {policy['policy']}/{policy['name']} drops this flow"})
                continue
            if policy["action"] == "redirect":
                try:
                    lookup_addr = ip_address(policy["next_hop"])
                except ValueError:
                    paths.append({"status": "incomplete", "path": path, "reason": "Invalid policy redirect address"})
                    continue
                lookup_vid = next((v["id"] for v in snapshot.vrfs if v["device_id"] == did and v["name"] == policy["vrf"]), None)
        routes, error = lookup(did, lookup_vid, lookup_addr)
        if error:
            paths.append({"status": "incomplete", "path": path, "reason": error})
            continue
        for route in routes:
            rs = step(
                {
                    "device": devices[did]["name"],
                    "device_id": did,
                    "vrf": route["vrf"],
                    "vrf_id": lookup_vid,
                    "lookup_ip": str(lookup_addr),
                    "vtep": route.get("vtep"), "vni": route.get("vni"),
                    "prefix": route["prefix"],
                    "next_hop": route.get("next_hop"),
                    "interface": route["interface"],
                    "route_id": route["id"],
                    "protocol": route["protocol"],
                    "observed_at": route["last_success_at"],
                    "recursive_routes": route.get("recursive_routes", []),
                },
                "route_lookup",
            )
            branch = path + [rs]
            egress = interfaces.get(route.get("interface_id"), {})
            if route.get("action") in {"drop", "discard", "reject"}:
                paths.append(
                    {"status": "blocked", "path": branch, "reason": "Discard route"}
                )
                continue
            if egress.get("oper_up") is False or (
                route.get("interface_id") and not fresh(egress)
            ):
                paths.append(
                    {
                        "status": "incomplete",
                        "path": branch,
                        "reason": "Egress interface is down or stale",
                    }
                )
                continue
            owned = [
                e
                for e in snapshot.endpoints
                if e.get("kind") == "owned"
                and e.get("device_id") == did
                and e.get("vrf_id") == vid
                and e.get("ip") == destination_ip
                and fresh(e)
            ]
            if owned and (
                not target
                or destination_ip != original_destination_ip
                or (
                    target["device_id"] == did
                    and target.get("vrf_id") == vid
                    and any(
                        e.get("interface_id") == target.get("interface_id")
                        for e in owned
                    )
                )
            ):
                paths.append(
                    {
                        "status": "ok",
                        "path": branch,
                        "reason": "Destination IP belongs to this device",
                    }
                )
                continue
            if route.get("vtep"):
                tunnels, error = tunnel_paths(did, route)
                if error:
                    paths.append({"status": "incomplete", "path": branch, "reason": error})
                for remote, remote_vid, underlay in tunnels:
                    encap = step({"device": devices[did]["name"], "interface": route["interface"],
                        "vtep": route["vtep"], "vni": route["vni"], "vrf": route["vrf"]}, "vxlan_encap")
                    decap = step({"device": remote["device"], "interface": remote["name"],
                        "vtep": route["vtep"], "vni": route["vni"]}, "vxlan_decap")
                    queue.append((remote["device_id"], remote_vid, branch + [encap, *underlay, decap],
                        seen | {state}, (remote["id"], None, True), packet))
                continue
            next_ip = route.get("next_hop") or str(lookup_addr)
            try:
                ip_address(next_ip)
            except ValueError:
                paths.append(
                    {
                        "status": "incomplete",
                        "path": branch,
                        "reason": "Unsupported next-hop address",
                    }
                )
                continue
            outgoing = physical_members(route.get("interface_id"))
            neighbors = adjacent(did, route, next_ip)
            if neighbors:
                if (
                    len(
                        {
                            (e["device_id"], e["vrf_id"], e["interface_id"])
                            for _, e in neighbors
                        }
                    )
                    > 1
                ):
                    paths.append(
                        {
                            "status": "ambiguous",
                            "path": branch,
                            "reason": "Next-hop IP has multiple adjacent owners",
                        }
                    )
                    continue
                for edge, e in neighbors:
                    queue.append(
                        (
                            e["device_id"],
                            e["vrf_id"],
                            branch + [step(edge, "physical")],
                            seen | {state},
                            (e["interface_id"], e.get("vlan"), False),
                            packet,
                        )
                    )
                continue
            # A directly connected endpoint must have fresh, scoped ARP and an explicit physical attachment.
            learned = [
                e
                for e in snapshot.endpoints
                if e.get("kind") == "neighbor"
                and e.get("device_id") == did
                and e.get("vrf_id") == vid
                and e.get("ip") == next_ip
                and fresh(e)
                and e.get("interface_id") == route.get("interface_id")
            ]
            if (
                not route.get("next_hop")
                and learned
                and not (target and target.get("kind") == "owned" and destination_ip == original_destination_ip)
            ):
                attachments = (
                    set().union(*(set(e.get("attachments", [])) for e in learned))
                    or outgoing
                )
                physical = (
                    set().union(*(physical_members(i) for i in attachments))
                    if attachments
                    else set()
                )
                if (
                    len(physical) == 1
                    and len({e.get("mac") for e in learned}) == 1
                    and all(
                        fresh(interfaces[i])
                        and interfaces[i].get("oper_up") is not False
                        for i in physical
                    )
                ):
                    iid = next(iter(physical))
                    endpoint = step(
                        {
                            "device": devices[did]["name"],
                            "interface": interfaces[iid]["name"],
                            "ip": destination_ip,
                            "evidence": [e["id"] for e in learned],
                        },
                        "arp_attachment",
                    )
                    paths.append(
                        {
                            "status": "ok",
                            "path": branch + [endpoint],
                            "reason": "Fresh directly connected ARP attachment",
                        }
                    )
                    continue
            paths.append(
                {
                    "status": "incomplete",
                    "path": branch,
                    "reason": "Next-hop physical adjacency is unconfirmed or requires an unresolved VLAN/overlay transit",
                }
            )
    if queue:
        paths.append(
            {
                "status": "incomplete",
                "path": [],
                "reason": "ECMP expansion limit reached",
            }
        )
    if initial_flow:
        for branch in paths:
            states = [h for h in branch["path"] if h.get("flow_after") or h.get("flow_before")]
            branch["final_flow"] = initial_flow.data()
            if states:
                last = states[-1]
                branch["final_flow"] = last["flow_before"] if last.get("status") == "blocked" else last.get("flow_after") or last["flow_before"]
            branch["evidence_level"] = "configuration"
    base["paths"] = paths
    # A single path field remains for old callers; multiple branches are never silently reduced to the first.
    if len(paths) == 1:
        base["path"] = paths[0]["path"]
    for branch in paths:
        if branch.get("status") != "ok":
            base["warnings"].append(branch["reason"])
    status = (
        "ok"
        if paths and all(p["status"] == "ok" for p in paths)
        else (
            "blocked" if paths and all(p["status"] == "blocked" for p in paths) else
            "ambiguous"
            if any(p["status"] == "ambiguous" for p in paths)
            else "incomplete"
        )
    )
    return finish(status)
