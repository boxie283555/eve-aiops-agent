from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .settings import load_project_config


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


def load_inventory(path: str = "/app/inventory/devices.yml") -> list[Device]:
    project_config = load_project_config()
    if project_config.get("inventory"):
        raw = project_config["inventory"]
    else:
        inventory_path = Path(path)
        if not inventory_path.exists():
            inventory_path = Path("inventory/devices.yml")
        with open(inventory_path, "r", encoding="utf-8") as handle:
            raw: dict[str, Any] = yaml.safe_load(handle) or {}

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
            )
        )
    return devices
