#!/usr/bin/env python3
"""Import the configured inventory and latest Agent interfaces into Nautobot.

The sync is intentionally idempotent. It stores the management address in the
``management_ip`` device custom field because Nautobot 2.x requires an IP to be
assigned to an interface before it can be selected as primary.
"""

from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import yaml


class Api:
    def __init__(self, base: str, token: str) -> None:
        self.base = base.rstrip("/") + "/api"
        self.headers = {"Authorization": f"Token {token}", "Content-Type": "application/json"}

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        request = urllib.request.Request(
            self.base + path,
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers=self.headers,
        )
        with urllib.request.urlopen(request) as response:
            return json.load(response)

    def find(self, endpoint: str, **filters: str) -> dict[str, Any] | None:
        query = urllib.parse.urlencode(filters)
        results = self.request("GET", f"/{endpoint}/?{query}").get("results", [])
        return results[0] if results else None

    def upsert(self, endpoint: str, filters: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
        return self.find(endpoint, **filters) or self.request("POST", f"/{endpoint}/", body)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--config", default="config/project.yml")
    parser.add_argument("--agent-state", default="http://127.0.0.1:8080/state")
    args = parser.parse_args()

    api = Api(args.url, args.token)
    config = yaml.safe_load(Path(args.config).read_text())
    inventory = config["inventory"]
    active = next(x for x in api.request("GET", "/extras/statuses/?limit=100")["results"] if x["name"] == "Active")
    custom_fields = api.request("GET", "/extras/custom-fields/?limit=100").get("results", [])
    if not any(item.get("key") == "management_ip" for item in custom_fields):
        api.request("POST", "/extras/custom-fields/", {"label": "Management IP", "key": "management_ip", "type": "text", "content_types": ["dcim.device"]})
    manufacturer = api.upsert("dcim/manufacturers", {"name": "Arista"}, {"name": "Arista", "slug": "arista"})
    role_cache: dict[str, dict[str, Any]] = {}
    site_cache: dict[str, dict[str, Any]] = {}
    location_type = api.upsert("dcim/location-types", {"name": "Site"}, {"name": "Site", "nestable": True})
    api.request("PATCH", f"/dcim/location-types/{location_type['id']}/", {"content_types": ["dcim.device"]})
    platform = api.upsert("dcim/platforms", {"name": "Arista EOS"}, {"name": "Arista EOS", "slug": "arista-eos", "manufacturer": manufacturer["id"]})
    device_type = api.upsert("dcim/device-types", {"model": "Arista Switch"}, {"model": "Arista Switch", "slug": "arista-switch", "manufacturer": manufacturer["id"]})
    try:
        state = json.load(urllib.request.urlopen(args.agent_state))
    except Exception:
        state = {"devices": []}
    state_by_name = {item["device"]: item for item in state.get("devices", [])}

    for item in inventory["devices"]:
        site = site_cache.setdefault(item["site"], api.upsert("dcim/locations", {"name": item["site"]}, {"name": item["site"], "location_type": location_type["id"], "status": active["id"]}))
        role = role_cache.setdefault(item["role"], api.upsert("extras/roles", {"name": item["role"]}, {"name": item["role"], "slug": item["role"], "content_types": ["dcim.device"]}))
        device = api.upsert("dcim/devices", {"name": item["name"]}, {"name": item["name"], "device_type": device_type["id"], "role": role["id"], "location": site["id"], "status": active["id"], "platform": platform["id"]})
        api.request("PATCH", f"/dcim/devices/{device['id']}/", {"custom_fields": {"management_ip": item["host"]}})
        for interface in state_by_name.get(item["name"], {}).get("interfaces", []):
            if interface.get("name"):
                api.upsert("dcim/interfaces", {"device": device["id"], "name": interface["name"]}, {"device": device["id"], "name": interface["name"], "type": "other", "status": active["id"], "description": interface.get("description", "")})
        print(f"synced {item['name']}")


if __name__ == "__main__":
    main()
