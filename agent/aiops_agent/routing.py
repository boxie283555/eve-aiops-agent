"""Parse route entries and interface relationships without inferring missing state."""

from __future__ import annotations

import json
import re
from ipaddress import ip_interface, ip_network
from typing import Any

from .model import interface_name, interface_kind, mac_address


def command_ok(output: str) -> bool:
    return bool(output.strip()) and not re.search(
        r"COMMAND_FAILED|Invalid (?:command|syntax|input)|Unknown command|% Error|Syntax error|Incomplete command",
        output,
        re.I,
    )


def eos_interfaces(brief: str, detail: str, config: str) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}

    def row(name):
        name = interface_name(name)
        return rows.setdefault(
            name,
            {
                "name": name,
                "canonical_name": name,
                "kind": interface_kind(name),
                "admin_up": None,
                "oper_up": None,
            },
        )

    if command_ok(brief):
        for line in brief.splitlines():
            fields = line.split()
            if len(fields) < 2:
                continue
            try:
                address = str(ip_interface(fields[1]))
            except ValueError:
                continue
            row(fields[0])["addresses"] = [address]
    if command_ok(detail):
        current = None
        for line in detail.splitlines():
            m = re.match(r"^(\S+) is .*line protocol is", line)
            if m:
                current = row(m[1])
                state = line.split(" is ", 1)[1].split(",", 1)[0].strip().lower()
                current["admin_up"] = "admin" not in state
                current["oper_up"] = state == "up" and bool(
                    re.search(r"line protocol is up", line)
                )
                current["state_source"] = "interface_detail"
            if current is None:
                continue
            m = re.search(r"address is ([a-fA-F0-9.:-]+)", line)
            if m and mac_address(m[1]):
                current["mac"] = mac_address(m[1])
    if command_ok(config):
        current = None
        for line in config.splitlines():
            m = re.match(r"^interface (\S+)\s*$", line)
            if m:
                current = row(m[1])
                current["vrf"] = "default"
                continue
            if line and not line[0].isspace():
                current = None
            if current is None:
                continue
            m = re.match(
                r"\s+(?:vrf(?: forwarding| member)?|ip vrf forwarding) (\S+)", line
            )
            if m:
                current["vrf"] = m[1]
            m = re.match(r"\s+channel-group (\d+)", line)
            if m:
                current["aggregate"] = "Port-Channel" + m[1]
            m = re.match(r"\s+encapsulation dot1[qQ] (\d+)", line)
            if m:
                current["vlan"] = m[1]
            m = re.match(r"\s+description (.+)", line)
            if m:
                current["description"] = m[1]
            m = re.match(r"\s+ip address (\S+)", line)
            if m:
                try:
                    address = str(ip_interface(m[1]))
                except ValueError:
                    continue
                addresses = current.setdefault("addresses", [])
                if address not in addresses:
                    addresses.append(address)
    for name, item in rows.items():
        if "." in name:
            item["parent"] = name.rsplit(".", 1)[0]
    return rows


def panos_interfaces(output: str) -> list[dict[str, Any]]:
    if not command_ok(output):
        return []
    rows: dict[str, dict[str, Any]] = {}
    section = None
    aggregate = None
    for line in output.splitlines():
        s = line.strip()
        fields = s.split()
        if "configured hardware interfaces:" in s:
            section = "hardware"
            continue
        if "configured logical interfaces:" in s:
            section = "logical"
            aggregate = None
            continue
        m = re.fullmatch(r"(ae\d+) members:", s)
        if m:
            aggregate = m[1]
            section = "members"
            continue
        if section == "members":
            for name in fields:
                if re.fullmatch(r"ethernet\d+/\d+", name):
                    rows.setdefault(name, {"name": name})["aggregate"] = aggregate
            continue
        if not fields or not re.fullmatch(
            r"(?:ethernet\d+/\d+|ae\d+)(?:\.\d+)?", fields[0]
        ):
            continue
        name = fields[0]
        row = rows.setdefault(name, {"name": name})
        if section == "hardware" and len(fields) >= 4 and mac_address(fields[3]):
            state = fields[2].rsplit("/", 1)[-1].lower()
            row.update(mac=mac_address(fields[3]), oper_up=state == "up", admin_up=None)
            try:
                row["speed_bps"] = int(fields[2].split("/")[0]) * 1000000
            except ValueError:
                pass
        if section == "logical":
            for f in fields:
                if f.startswith("vr:"):
                    row["vrf"] = f[3:]
                try:
                    address = str(ip_interface(f)) if "/" in f else None
                except ValueError:
                    address = None
                if address:
                    row.setdefault("addresses", []).append(address)
            # Tag is the column immediately before the address.
            if len(fields) >= 3 and fields[-2].isdigit():
                row["vlan"] = fields[-2]
    for name, row in rows.items():
        row.update(canonical_name=interface_name(name), kind=interface_kind(name))
        row.setdefault("admin_up", None)
        row.setdefault("oper_up", None)
        if "." in name:
            row["parent"] = name.rsplit(".", 1)[0]
            # Inherited link state is not independent administrative state.
            row["oper_up"] = rows.get(row["parent"], {}).get("oper_up")
            row["state_source"] = "parent_link"
        row["ip"] = row.get("addresses", [""])[0].split("/")[0] or None
    return list(rows.values())


def route_row(vrf, prefix, next_hop=None, interface=None, protocol="unknown", **extra):
    try:
        prefix = str(ip_network(prefix, strict=False))
    except ValueError:
        return None
    return {
        "vrf": vrf,
        "prefix": prefix,
        "next_hop": next_hop or None,
        "interface": interface_name(interface or ""),
        "protocol": protocol,
        **extra,
    }


def eos_route_table(output: str) -> dict | None:
    """Accept an EOS JSON object followed only by its optional CLI prompt.

    Prompts containing a clock change during a command, so Netmiko cannot
    always strip the prompt captured at session initialization.
    """
    try:
        raw = output.lstrip()
        data, end = json.JSONDecoder().raw_decode(raw)
        suffix = raw[end:].strip()
        if suffix and not re.fullmatch(r"[\w.():@/+-]+[>#]", suffix):
            return None
        if not isinstance(data, dict) or not isinstance(data.get("vrfs"), dict):
            return None
        if any(
            not isinstance(table, dict) or not isinstance(table.get("routes", {}), dict)
            for table in data["vrfs"].values()
        ):
            return None
        return data
    except (ValueError, TypeError):
        return None


def parse_routes(output: str, platform: str) -> list[dict[str, Any]]:
    if not command_ok(output):
        return []
    rows = []
    if platform == "arista_eos":
        data = eos_route_table(output)
        if data is None:
            return []
        for vrf, table in data.get("vrfs", {}).items():
            for prefix, route in table.get("routes", {}).items():
                for hop in route.get("vias") or [{}]:
                    item = route_row(
                        vrf,
                        prefix,
                        hop.get("nexthopAddr"),
                        hop.get("interface"),
                        route.get("routeType", "unknown"),
                        preference=route.get("preference"),
                        metric=route.get("metric"),
                        action=route.get("routeAction", "forward"),
                        selected=True,
                    )
                    if item:
                        rows.append(item)
    elif platform == "paloalto_panos":
        vrf = None
        for line in output.splitlines():
            m = re.search(r"VIRTUAL ROUTER:\s*(\S+)", line, re.I)
            if m:
                vrf = m[1]
                continue
            f = line.split()
            if vrf is None or len(f) < 5:
                continue
            try:
                ip_network(f[0], strict=False)
            except ValueError:
                continue
            flags = "".join(
                v for v in f[3:] if re.fullmatch(r"[A-Z?~][A-Za-z0-9?~]*", v)
            )
            if "A" not in flags:
                continue  # only active RIB entries
            iface = next(
                (
                    v
                    for v in f[4:]
                    if re.fullmatch(
                        r"(?:ethernet\d+/\d+|ae\d+|tunnel\.\d+|loopback\.\d+)(?:\.\d+)?",
                        v,
                    )
                ),
                "",
            )
            protocol = next(
                (
                    p
                    for flag, p in [
                        ("C", "connected"),
                        ("H", "local"),
                        ("S", "static"),
                        ("B", "bgp"),
                        ("O", "ospf"),
                        ("R", "rip"),
                    ]
                    if flag in flags
                ),
                "unknown",
            )
            item = route_row(
                vrf,
                f[0],
                (
                    None
                    if protocol in {"connected", "local"} or f[1] == "0.0.0.0"
                    else f[1]
                ),
                iface,
                protocol,
                metric=f[2],
                selected=True,
            )
            if item:
                rows.append(item)
    elif platform == "cisco_nxos":
        vrf = "default"
        prefix = None
        for line in output.splitlines():
            m = re.search(r'IP Route Table for VRF "([^"]+)"', line)
            if m:
                vrf = m[1]
                prefix = None
                continue
            m = re.match(r"(\S+/\d+), ubest/mbest:", line.strip())
            if m:
                prefix = m[1]
                continue
            m = re.match(r"\s*\*via\s+([^,]+),\s*(.*)", line)
            if not m or not prefix:
                continue
            parts = [p.strip() for p in m[2].split(",")]
            iface = next(
                (
                    v
                    for v in parts
                    if re.fullmatch(
                        r"(?:Eth(?:ernet)?|Vlan|Lo(?:opback)?|Po(?:rt-channel)?|mgmt)\d+(?:[/.:]\d+)*",
                        v,
                        re.I,
                    )
                ),
                "",
            )
            proto = parts[-1].split("-")[0]
            item = route_row(
                vrf,
                prefix,
                (
                    m[1]
                    if re.match(r"^\d+\.", m[1]) and proto not in {"direct", "local"}
                    else None
                ),
                iface,
                proto,
                selected=True,
            )
            if item:
                rows.append(item)
    return rows
