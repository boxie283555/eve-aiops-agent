from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
from typing import Any

import yaml

from .settings import load_project_config
from .nautobot import NautobotClient, NautobotError

_last_load_status: dict[str, Any] = {}


def inventory_status() -> dict[str, Any]:
    """Metadata only: never expose inventory credentials or response payloads."""
    return dict(_last_load_status)


@dataclass(frozen=True)
class SnmpTarget:
    host: str | None = None
    port: int | None = None


@dataclass(frozen=True)
class Device:
    name: str
    role: str
    site: str
    host: str
    platform: str
    port: int
    use_ssh: bool
    use_snmp: bool
    snmp_community_env: str | None = None
    username_env: str | None = None
    password_env: str | None = None
    secret_env: str | None = None
    snmp: SnmpTarget = SnmpTarget()
    id: str | None = None
    manufacturer: str = ""
    status: str = "Active"
    monitoring_enabled: bool = True
    health_excluded: bool = False
    exclusion_reason: str = ""
    exclusion_owner: str = ""
    exclusion_until: str | None = None
    bgp_baseline: dict | None = None
    igp_baseline: dict | None = None


def device_classification(device: Device) -> tuple[str, str]:
    """Inventory identity remains available even when device collection fails."""
    platform = getattr(device, "platform", "").lower()
    manufacturer = getattr(device, "manufacturer", "").strip()
    if not manufacturer or manufacturer.lower() == "unknown":
        manufacturer = {"arista_eos": "Arista", "paloalto_panos": "Palo Alto Networks",
                        "cisco_nxos": "Cisco"}.get(platform, "Unknown")
    aliases = {"arista": "Arista", "arista networks": "Arista", "palo alto": "Palo Alto Networks",
               "paloalto": "Palo Alto Networks", "palo alto networks": "Palo Alto Networks", "cisco": "Cisco"}
    manufacturer = aliases.get(manufacturer.lower(), manufacturer)
    role = getattr(device, "role", "").lower()
    if role in {"firewall", "fw"} or platform == "paloalto_panos":
        device_type = "Firewall"
    elif role in {"switch", "leaf", "spine", "border-leaf", "access-switch", "core-switch"} or platform in {"arista_eos", "cisco_nxos"}:
        device_type = "Switch"
    else:
        device_type = "Other"
    return manufacturer, device_type


def load_inventory(path: str = "/app/inventory/devices.yml", *, allow_fallback=True) -> list[Device]:
    global _last_load_status
    project_config = load_project_config()
    nautobot = project_config.get("nautobot", {})
    nautobot_url = os.getenv("NAUTOBOT_URL") or nautobot.get("url")
    nautobot_token = os.getenv("NAUTOBOT_TOKEN") or nautobot.get("token")
    inventory_source = (os.getenv("AIOPS_INVENTORY_SOURCE")
                        or project_config.get("inventory", {}).get("source")
                        or nautobot.get("inventory_source", "yaml"))
    if inventory_source not in {"yaml", "nautobot"}:
        raise ValueError("Unknown inventory source")
    effective_source = inventory_source
    fallback = nautobot.get("fallback_to_yaml", False) is True
    raw: dict[str, Any]
    if inventory_source == "nautobot":
        if not nautobot_url or not nautobot_token:
            raise NautobotError("Nautobot inventory requires URL and token; YAML was not loaded")
        try:
            # Inventory reads may overlap asynchronous bulk interface sync. Give
            # Nautobot the same 30s minimum used by that sync; hot reload runs
            # independently and cannot block metric collection.
            timeout = max(30.0, float(os.getenv("NAUTOBOT_TIMEOUT_SECONDS") or nautobot.get("timeout_seconds", 5.0)))
            raw = {"devices": NautobotClient(nautobot_url, nautobot_token, timeout).devices()}
        except NautobotError:
            if not fallback or not allow_fallback:
                raise
            raw = _load_yaml_inventory(path, project_config)
            effective_source = "yaml_fallback"
    elif project_config.get("inventory"):
        raw = project_config["inventory"]
    else:
        raw = _load_yaml_inventory(path, project_config)

    defaults = raw.get("defaults", {})
    devices: list[Device] = []
    for item in raw.get("devices", []):
        merged = defaults | item
        devices.append(
            Device(
                name=str(merged["name"]),
                id=merged.get("id"),
                manufacturer=str(merged.get("manufacturer") or ""),
                status=str(merged.get("status") or "Active"),
                monitoring_enabled=merged.get("monitoring_enabled",True),
                health_excluded=merged.get("health_excluded",False),
                exclusion_reason=str(merged.get("exclusion_reason") or ""),
                exclusion_owner=str(merged.get("exclusion_owner") or ""),
                exclusion_until=merged.get("exclusion_until") or None,
                bgp_baseline=merged.get("bgp_baseline"),
                igp_baseline=merged.get("igp_baseline"),
                role=str(merged.get("role", "unknown")),
                site=str(merged.get("site", "unknown")),
                host=str(merged["host"]),
                platform=str(merged.get("platform", "cisco_nxos")),
                port=int(merged.get("port") or (22 if merged.get("use_ssh", True) else 23)),
                use_ssh=bool(merged.get("use_ssh", True)),
                use_snmp=bool(merged.get("use_snmp", False)),
                snmp_community_env=merged.get("snmp_community_env"),
                username_env=merged.get("username_env"),
                password_env=merged.get("password_env"),
                secret_env=merged.get("secret_env"),
                snmp=SnmpTarget(
                    host=(merged.get("snmp") or {}).get("host"),
                    port=(merged.get("snmp") or {}).get("port"),
                ),
            )
        )
    _last_load_status = {"configured_source": inventory_source,
                         "effective_source": effective_source,
                         "fallback_to_yaml": fallback,
                         "loaded_at": datetime.now(timezone.utc).isoformat(),
                         "device_count": len(devices),
                         "devices_with_id": sum(bool(d.id) for d in devices)}
    return devices


def _load_yaml_inventory(path: str, project_config: dict[str, Any]) -> dict[str, Any]:
    if project_config.get("inventory"):
        return project_config["inventory"]
    inventory_path = Path(path)
    if not inventory_path.exists():
        inventory_path = Path("inventory/devices.yml")
    with open(inventory_path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}
