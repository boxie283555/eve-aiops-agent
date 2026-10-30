"""Stable identities and interface vocabulary for normalized topology v1."""

from __future__ import annotations

import re
from uuid import NAMESPACE_URL, uuid5


def stable_id(kind: str, *parts: object) -> str:
    import json

    return str(uuid5(NAMESPACE_URL, "eve-aiops/" + kind + "/" + json.dumps(parts)))


def interface_name(value: str) -> str:
    value = str(value or "").strip()
    match = re.fullmatch(r"([A-Za-z-]+)(\d+(?:[/.:]\d+)*)", value)
    if not match:
        return value
    prefix, number = match.groups()
    prefix = {
        "et": "Ethernet",
        "eth": "Ethernet",
        "ethernet": "Ethernet",
        "po": "Port-Channel",
        "port-channel": "Port-Channel",
        "portchannel": "Port-Channel",
        "ma": "Management",
        "mgmt": "Management",
        "management": "Management",
        "lo": "Loopback",
        "loopback": "Loopback",
        "vl": "Vlan",
        "vlan": "Vlan",
        "vx": "Vxlan",
        "vxlan": "Vxlan",
        "nve": "Nve",
        "ae": "ae",
        "tunnel": "Tunnel",
    }.get(prefix.lower(), prefix)
    return prefix + number


def interface_kind(name: str) -> str:
    name = interface_name(name)
    if "." in name:
        return "subinterface"
    if name.startswith(("Port-Channel", "ae")):
        return "aggregate"
    if name.startswith(("Ethernet", "Management")):
        return "physical"
    return "virtual"


def mac_address(value: str) -> str | None:
    value = str(value or "").strip()
    if not re.fullmatch(
        r"(?:[0-9a-fA-F]{12}|(?:[0-9a-fA-F]{4}\.){2}[0-9a-fA-F]{4}|(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}|(?:[0-9a-fA-F]{2}-){5}[0-9a-fA-F]{2})",
        value,
    ):
        return None
    digits = re.sub(r"[.:-]", "", value).lower()
    if not re.fullmatch("[0-9a-f]{12}", digits):
        return None
    return ":".join(digits[i : i + 2] for i in range(0, 12, 2))
