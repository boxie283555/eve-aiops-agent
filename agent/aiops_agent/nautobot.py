from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from .parser import is_interface_name
from .model import interface_name, mac_address


class NautobotError(RuntimeError):
    """Raised when Nautobot cannot provide the requested inventory."""


class NautobotSyncError(NautobotError):
    def __init__(self, updated: int, failed_devices: list[str]):
        self.updated = updated
        self.failed_devices = failed_devices
        super().__init__(f"Interface synchronization failed for {len(failed_devices)} device(s)")


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
        # A client lives for one inventory/sync/graph read. Reuse related names
        # within that read instead of fetching one status per interface.
        self._related_names: dict[str, str] = {}
        self._related_objects: dict[str, dict[str, Any]] = {}

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

    def _get_all(self, path: str) -> list[dict[str, Any]]:
        """Follow bounded pages without making a large limit=0 response."""
        rows = []
        seen = set()
        while path:
            if path in seen:
                raise NautobotError("Repeated Nautobot pagination cursor")
            seen.add(path)
            page = self._get(path)
            rows.extend(page.get("results", []))
            next_url = page.get("next")
            if not next_url:
                break
            parsed = urllib.parse.urlsplit(next_url)
            if parsed.netloc and parsed.netloc != urllib.parse.urlsplit(self.base_url).netloc:
                raise NautobotError("Unexpected Nautobot pagination host")
            path = parsed.path.split("/api/", 1)[-1] + ("?" + parsed.query if parsed.query else "")
        return rows

    def _related_object(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        url = value.get("url")
        if not url or value.get("name"):
            return value
        if url not in self._related_objects:
            self._related_objects[url] = self._get(url.split("/api/", 1)[-1])
        return self._related_objects[url]

    def _related_name(self, value: Any) -> str:
        if not isinstance(value, dict):
            return "unknown"
        name = value.get("name") or value.get("slug") or value.get("display")
        if name:
            return str(name)
        url = value.get("url")
        if not url:
            return "unknown"
        if url in self._related_names:
            return self._related_names[url]
        try:
            detail = self._related_object(value)
            name = str(detail.get("name") or detail.get("slug") or detail.get("display") or "unknown")
            if name != "unknown":
                self._related_names[url] = name
            return name
        except NautobotError:
            return "unknown"

    def devices(self) -> list[dict[str, Any]]:
        payload = self._get("dcim/devices/?limit=0")
        results = payload.get("results", [])
        devices: list[dict[str, Any]] = []
        for item in results:
            custom_fields = item.get("custom_fields") or {}
            primary = item.get("primary_ip4") or item.get("primary_ip6")
            primary_ip = _address(primary) or _address(custom_fields.get("management_ip"))
            if not primary_ip and primary:
                primary_ip = _address(self._related_object(primary))
            if not primary_ip:
                continue
            role = item.get("role") or {}
            site = item.get("site") or item.get("location") or {}
            platform = self._related_object(item.get("platform") or {})
            platform_name = str(platform.get("network_driver") or self._related_name(platform)).lower().replace(" ", "_")
            if platform_name in {"arista_eos", "arista_eos_"}:
                platform_name = "arista_eos"
            if platform_name in {"palo_alto_pan-os", "palo_alto_panos", "pan-os"}:
                platform_name = "paloalto_panos"
            # Classification is optional metadata: a failed related lookup must
            # not prevent the primary inventory from reaching the collector.
            manufacturer = "unknown"
            try:
                device_type = self._related_object(item.get("device_type") or {})
                manufacturer = self._related_name(device_type.get("manufacturer") or platform.get("manufacturer"))
            except NautobotError:
                pass
            devices.append(
                {
                    "name": item.get("name") or item.get("display") or item.get("id"),
                    "id": item.get("id"),
                    "role": self._related_name(role),
                    "site": self._related_name(site),
                    "host": primary_ip,
                    "platform": platform_name or "cisco_nxos",
                    "manufacturer": manufacturer,
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
            normalized_devices.append({"id": item["id"], "name": item.get("name") or item.get("display"),
                "role": self._related_name(item.get("role")), "site": self._related_name(item.get("site") or item.get("location")),
                "host": _address(item.get("primary_ip4") or item.get("primary_ip6")) or _address(custom.get("management_ip")),
                "platform": self._related_name(item.get("platform")), "source": "nautobot"})
        normalized_interfaces = []
        for item in interfaces:
            device_id = (item.get("device") or {}).get("id")
            if not device_id:
                continue
            normalized_interfaces.append({"nautobot_id": item["id"], "device": device_names.get(device_id), "name": item.get("name"),
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
            if a_device in device_names and b_device in device_names:
                normalized_links.append({"from": device_names[a_device], "to": device_names[b_device], "local_interface": a_int,
                    "remote_interface": b_int, "type": "physical", "source": "nautobot",
                    "confidence": 1.0, "status": self._related_name(cable.get("status"))})
        return {"devices": normalized_devices, "interfaces": normalized_interfaces,
                "links": normalized_links, "endpoints": normalized_endpoints}

    def _request(self, method: str, path: str, payload: Any = None) -> Any:
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
        """Synchronize in bounded batches; one failed device cannot stop others."""
        fields = ["aiops_admin_status", "aiops_oper_status", "aiops_last_collected",
                  "aiops_collection_error", "aiops_ip_address"]
        existing = self._get_all("extras/custom-fields/?limit=100")
        for key in fields:
            if not any(item.get("key") == key for item in existing):
                self._request("POST", "extras/custom-fields/", {
                    "label": key.replace("aiops_", "").replace("_", " ").title(),
                    "key": key, "type": "text", "content_types": ["dcim.interface"],
                })
        devices = self._get_all("dcim/devices/?limit=100")
        device_ids = {item.get("name"): item["id"] for item in devices}
        statuses = self._get_all("extras/statuses/?limit=100")
        active_status = next((item["id"] for item in statuses if item.get("name") == "Active"), None)

        def sync_device(result: Any) -> tuple[int, bool]:
            device_id = device_ids.get(result.device.name)
            if not device_id:
                return 0, True
            rows = self._get_all("dcim/interfaces/?" + urllib.parse.urlencode({
                "device_id": device_id, "limit": 100,
            }))
            by_name = {interface_name(row["name"]): row for row in rows}
            successful = bool(result.ok and result.dataset_status.get("interfaces", False))
            observed_at = result.collected_at
            updates = []
            failed = False
            # Failed collection must not renew last-success timestamps or invent down states.
            if not successful:
                updates = [{"id": row["id"], "custom_fields": {
                    "aiops_collection_error": result.error or "interfaces dataset unavailable",
                }} for row in rows]
            else:
                for iface in result.interfaces:
                    name = str(iface.get("name") or "")
                    if not is_interface_name(name):
                        continue
                    match = by_name.get(interface_name(name))
                    mac = mac_address(iface.get("mac"))
                    if not match:
                        payload = {"device": device_id, "name": name, "type": "other"}
                        if active_status:
                            payload["status"] = active_status
                        if iface.get("description"):
                            payload["description"] = iface["description"]
                        if mac:
                            payload["mac_address"] = mac
                        try:
                            match = self._request("POST", "dcim/interfaces/", payload)
                            by_name[interface_name(name)] = match
                        except NautobotError:
                            failed = True
                            continue
                    # Only patch fields owned by the collector, preserving operator metadata.
                    custom = {
                        "aiops_admin_status": "unknown" if iface.get("admin_up") is None else "up" if iface["admin_up"] else "down",
                        "aiops_oper_status": "unknown" if iface.get("oper_up") is None else "up" if iface["oper_up"] else "down",
                        "aiops_last_collected": observed_at,
                        "aiops_collection_error": "",
                        "aiops_ip_address": str(iface.get("ip") or ""),
                    }
                    payload = {"id": match["id"], "custom_fields": custom}
                    if mac:
                        payload["mac_address"] = mac
                    updates.append(payload)
            updated = 0
            for start in range(0, len(updates), 50):
                batch = updates[start:start + 50]
                # PATCH is idempotent: retry once after an ambiguous timeout.
                for attempt in range(2):
                    try:
                        self._request("PATCH", "dcim/interfaces/", batch)
                        updated += len(batch)
                        break
                    except NautobotError:
                        if attempt == 1:
                            failed = True
            return updated, failed

        updated = 0
        failed_devices = []
        # This entire operation runs in a background thread, separate from collection.
        # Two concurrent devices bound Nautobot load; each request contains at most 50 rows.
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = {executor.submit(sync_device, result): result.device.name for result in results}
            for future in as_completed(futures):
                name = futures[future]
                try:
                    count, failed = future.result()
                    updated += count
                    if failed:
                        failed_devices.append(name)
                except Exception:
                    failed_devices.append(name)
        if failed_devices:
            raise NautobotSyncError(updated, sorted(failed_devices))
        return updated
