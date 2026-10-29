from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any

import yaml

from .settings import load_project_config
from .nautobot import NautobotClient, NautobotError


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


def load_inventory(path: str = "/app/inventory/devices.yml") -> list[Device]:
    project_config = load_project_config()
    nautobot = project_config.get("nautobot", {})
    nautobot_url = os.getenv("NAUTOBOT_URL") or nautobot.get("url")
    nautobot_token = os.getenv("NAUTOBOT_TOKEN") or nautobot.get("token")
    inventory_source = os.getenv("AIOPS_INVENTORY_SOURCE") or nautobot.get("inventory_source", "yaml")
    raw: dict[str, Any]
    if inventory_source == "nautobot" and nautobot_url and nautobot_token:
        try:
            raw = {"devices": NautobotClient(nautobot_url, nautobot_token).devices()}
        except NautobotError:
            if nautobot.get("fallback_to_yaml", True) is False:
                raise
            raw = _load_yaml_inventory(path, project_config)
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
                role=str(merged.get("role", "unknown")),
                site=str(merged.get("site", "unknown")),
                host=str(merged["host"]),
                platform=str(merged.get("platform", "cisco_nxos")),
                port=int(merged.get("port", 22)),
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
    return devices


def _load_yaml_inventory(path: str, project_config: dict[str, Any]) -> dict[str, Any]:
    if project_config.get("inventory"):
        return project_config["inventory"]
    inventory_path = Path(path)
    if not inventory_path.exists():
        inventory_path = Path("inventory/devices.yml")
    with open(inventory_path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}
