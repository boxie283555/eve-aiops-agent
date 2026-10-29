from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any


class NautobotError(RuntimeError):
    """Raised when Nautobot cannot provide the requested inventory."""


def _address(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("address", "")
    return str(value or "").split("/", maxsplit=1)[0]


def _bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


class NautobotClient:
    def __init__(self, url: str, token: str, timeout: float = 5.0) -> None:
        self.base_url = url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _get(self, path: str) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self.base_url}/api/{path.lstrip('/')}",
            headers={"Authorization": f"Token {self.token}", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.load(response)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise NautobotError(f"Nautobot request failed: {exc}") from exc

    def _related_name(self, value: Any) -> str:
        if not isinstance(value, dict):
            return "unknown"
        name = value.get("name") or value.get("slug") or value.get("display")
        if name:
            return str(name)
        url = value.get("url")
        if not url:
            return "unknown"
        try:
            detail = self._get(url.split("/api/", 1)[-1])
            return str(detail.get("name") or detail.get("slug") or detail.get("display") or "unknown")
        except NautobotError:
            return "unknown"

    def devices(self) -> list[dict[str, Any]]:
        payload = self._get("dcim/devices/?limit=0")
        results = payload.get("results", [])
        devices: list[dict[str, Any]] = []
        for item in results:
            custom_fields = item.get("custom_fields") or {}
            primary_ip = _address(
                item.get("primary_ip4")
                or item.get("primary_ip6")
                or custom_fields.get("management_ip")
            )
            if not primary_ip:
                continue
            role = item.get("role") or {}
            site = item.get("site") or item.get("location") or {}
            platform = item.get("platform") or {}
            platform_name = self._related_name(platform).lower().replace(" ", "_")
            if platform_name in {"arista_eos", "arista_eos_"}:
                platform_name = "arista_eos"
            devices.append(
                {
                    "name": item.get("name") or item.get("display") or item.get("id"),
                    "role": self._related_name(role),
                    "site": self._related_name(site),
                    "host": primary_ip,
                    "platform": platform_name or "cisco_nxos",
                    "port": int(custom_fields.get("connection_port") or 22),
                    "use_ssh": _bool(custom_fields.get("use_ssh")),
                    "use_snmp": _bool(custom_fields.get("use_snmp")),
                    "snmp_community_env": custom_fields.get("snmp_community_env") or "AIOPS_SNMP_COMMUNITY",
                    "username_env": custom_fields.get("username_env"),
                    "password_env": custom_fields.get("password_env"),
                    "secret_env": custom_fields.get("secret_env"),
                    "snmp": {
                        "host": custom_fields.get("snmp_host") or primary_ip,
                        "port": int(custom_fields.get("snmp_port") or 161),
                    },
                }
            )
        return sorted(
            devices,
            key=lambda item: (
                str(item.get("site", "")).lower() in {"public-wan", "public_wan"},
                str(item.get("site", "")),
                str(item.get("name", "")),
            ),
        )

    def topology(self) -> dict[str, list[dict[str, Any]]]:
        """Read the Nautobot design graph in the normalized topology shape.

        This is intentionally read-only.  A missing optional endpoint yields a
        NautobotError so callers can retain the last valid snapshot.
        """
        devices = self._get("dcim/devices/?limit=0").get("results", [])
        interfaces = self._get("dcim/interfaces/?limit=0").get("results", [])
        addresses = self._get("ipam/ip-addresses/?limit=0").get("results", [])
        cables = self._get("dcim/cables/?limit=0").get("results", [])
        device_names = {item.get("id"): item.get("name") or item.get("display") for item in devices}
        interface_names = {item.get("id"): (item.get("device", {}).get("id"), item.get("name")) for item in interfaces}
        normalized_devices = []
        for item in devices:
            custom = item.get("custom_fields") or {}
            normalized_devices.append({"name": item.get("name") or item.get("display"),
                "role": self._related_name(item.get("role")), "site": self._related_name(item.get("site") or item.get("location")),
                "host": _address(item.get("primary_ip4") or item.get("primary_ip6") or custom.get("management_ip")),
                "platform": self._related_name(item.get("platform")), "source": "nautobot"})
        normalized_interfaces = []
        for item in interfaces:
            device_id = (item.get("device") or {}).get("id")
            if not device_id:
                continue
            normalized_interfaces.append({"device": device_names.get(device_id), "name": item.get("name"),
                "description": item.get("description") or "", "type": self._related_name(item.get("type")),
                "source": "nautobot", "status": self._related_name(item.get("status"))})
        normalized_endpoints = []
        for item in addresses:
            interface = item.get("assigned_object") or {}
            interface_id = interface.get("id")
            device_id, interface_name = interface_names.get(interface_id, (None, None))
            normalized_endpoints.append({"ip": _address(item.get("address")), "vrf": self._related_name(item.get("vrf")),
                "device": device_names.get(device_id), "interface": interface_name, "source": "nautobot"})
        normalized_links = []
        for cable in cables:
            a, b = cable.get("termination_a") or {}, cable.get("termination_b") or {}
            a_device, a_int = interface_names.get(a.get("id"), (None, None))
            b_device, b_int = interface_names.get(b.get("id"), (None, None))
            if a_device and b_device:
                normalized_links.append({"from": a_device, "to": b_device, "local_interface": a_int,
                    "remote_interface": b_int, "type": "physical", "source": "nautobot",
                    "confidence": 1.0, "status": self._related_name(cable.get("status"))})
        return {"devices": normalized_devices, "interfaces": normalized_interfaces,
                "links": normalized_links, "endpoints": normalized_endpoints}

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(
            f"{self.base_url}/api/{path.lstrip('/')}",
            data=data,
            method=method,
            headers={
                "Authorization": f"Token {self.token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.load(response)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise NautobotError(f"Nautobot request failed: {exc}") from exc

    def sync_interface_states(self, results: list[Any]) -> int:
        """Write the latest Agent state to matching Nautobot interfaces."""
        fields = {
            "aiops_admin_status": "text",
            "aiops_oper_status": "text",
            "aiops_last_collected": "text",
            "aiops_collection_error": "text",
        }
        existing = self._get("extras/custom-fields/?limit=100").get("results", [])
        for key, field_type in fields.items():
            if not any(item.get("key") == key for item in existing):
                self._request(
                    "POST",
                    "extras/custom-fields/",
                    {"label": key.replace("aiops_", "").replace("_", " ").title(), "key": key, "type": field_type, "content_types": ["dcim.interface"]},
                )

        collected_at = datetime.now(timezone.utc).isoformat()
        devices = self._get("dcim/devices/?limit=0").get("results", [])
        device_ids = {item.get("name"): item.get("id") for item in devices}
        interfaces = self._get("dcim/interfaces/?limit=0").get("results", [])
        interface_ids = {
            (item.get("device", {}).get("id"), item.get("name")): item
            for item in interfaces
        }
        updates: list[tuple[str, dict[str, Any]]] = []
        for result in results:
            device_id = device_ids.get(result.device.name)
            if not device_id:
                continue
            for interface in result.interfaces:
                name = str(interface.get("name") or "")
                if not name:
                    continue
                match = interface_ids.get((device_id, name))
                if not match:
                    continue
                current = match.get("custom_fields") or {}
                current.update(
                    {
                        "aiops_admin_status": "up" if interface.get("admin_up") else "down",
                        "aiops_oper_status": "up" if interface.get("oper_up") else "down",
                        "aiops_last_collected": collected_at,
                        "aiops_collection_error": result.error or "",
                    }
                )
                updates.append((f"dcim/interfaces/{match['id']}/", {"custom_fields": current}))

        def update(item: tuple[str, dict[str, Any]]) -> bool:
            self._request("PATCH", item[0], item[1])
            return True

        updated = 0
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(update, item) for item in updates]
            for future in as_completed(futures):
                future.result()
                updated += 1
        return updated

