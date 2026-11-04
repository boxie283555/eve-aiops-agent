"""Provision the Nautobot device custom fields used by Agent SNMP collection."""
from aiops_agent.settings import load_settings
from aiops_agent.nautobot import NautobotClient


settings = load_settings()
client = NautobotClient(settings.nautobot_url, settings.nautobot_token, 30)
existing = {field["key"]: field for field in client._get_all("extras/custom-fields/?limit=100")}

definitions = {
    "use_snmp": {
        "label": "AIOps · Enable SNMP collection",
        "type": "text",
        "description": "Set to true to collect this device using SNMP; requires the Agent SNMP master switch.",
    },
    "snmp_community_env": {
        "label": "AIOps · SNMP community environment variable",
        "type": "text",
        "description": "Environment variable name containing the SNMPv2c community; never store the community itself here.",
    },
    "snmp_host": {
        "label": "AIOps · SNMP target host",
        "type": "text",
        "description": "Optional SNMP target address; defaults to the device management address.",
    },
    "snmp_port": {
        "label": "AIOps · SNMP target port",
        "type": "text",
        "description": "Optional SNMP target port; defaults to the Agent SNMP port (161).",
    },
}

for key, definition in definitions.items():
    current = existing.get(key)
    if current:
        kind = current.get("type")
        kind = kind.get("value") if isinstance(kind, dict) else kind
        if "dcim.device" not in current.get("content_types", []):
            raise ValueError(f"Existing {key} field is not attached to dcim.device")
        if key == "use_snmp":
            if kind not in {"text", "boolean"}:
                raise ValueError("Existing use_snmp field must be text or boolean")
        elif kind != "text":
            raise ValueError(f"Existing {key} field must be text")
        print(f"retained {key}")
        continue
    client._request("POST", "extras/custom-fields/", {
        "key": key,
        "label": definition["label"],
        "type": definition["type"],
        "content_types": ["dcim.device"],
        "required": False,
        "description": definition["description"],
    })
    print(f"created {key}")
