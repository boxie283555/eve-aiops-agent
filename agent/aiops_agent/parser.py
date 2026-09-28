from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class InterfaceState:
    name: str
    admin_up: bool
    oper_up: bool
    rx_bps: float | None = None
    tx_bps: float | None = None


@dataclass(frozen=True)
class InterfaceCounters:
    rx_bps: float | None = None
    tx_bps: float | None = None
    input_errors: float | None = None
    crc_errors: float | None = None
    alignment_errors: float | None = None
    symbol_errors: float | None = None
    input_discards: float | None = None
    output_errors: float | None = None
    collisions: float | None = None
    late_collisions: float | None = None
    deferred: float | None = None
    output_discards: float | None = None


@dataclass(frozen=True)
class NeighborState:
    neighbor: str
    protocol: str
    context: str
    up: bool
    raw_state: str


@dataclass(frozen=True)
class BgpSummaryEntry:
    neighbor: str
    vrf: str
    peer_as: str
    session_state: str
    afi_safi: str
    afi_safi_state: str
    routes_received: float | None = None
    routes_accepted: float | None = None
    routes_advertised: float | None = None
    messages_received: float | None = None
    messages_sent: float | None = None

    @property
    def up(self) -> bool:
        return self.session_state.lower() in {"estab", "established"}

    @property
    def context(self) -> str:
        return self.vrf

    @property
    def raw_state(self) -> str:
        return self.session_state


@dataclass(frozen=True)
class DeviceHardwareInfo:
    model: str = "unknown"
    version: str = "unknown"
    serial: str = "unknown"
    architecture: str = "unknown"
    hw_version: str = "unknown"


@dataclass(frozen=True)
class HardwareComponent:
    component_type: str
    name: str
    status: str
    model: str = "unknown"
    serial: str = "unknown"


@dataclass(frozen=True)
class DeviceResourceUsage:
    uptime_seconds: float | None = None
    cpu_percent: float | None = None
    memory_percent: float | None = None
    memory_total_bytes: float | None = None
    memory_free_bytes: float | None = None


@dataclass(frozen=True)
class MacTableEntry:
    vlan: str
    mac: str
    entry_type: str
    port: str
    moves: str = ""
    last_move: str = ""


@dataclass(frozen=True)
class ArpEntry:
    vrf: str
    ip: str
    mac: str
    interface: str
    age: str = ""


@dataclass(frozen=True)
class LldpNeighbor:
    local_interface: str
    neighbor: str
    neighbor_interface: str


@dataclass(frozen=True)
class RouteSummaryEntry:
    vrf: str
    source: str
    routes: float


@dataclass(frozen=True)
class TcamResource:
    resource: str
    region: str
    used_entries: float | None = None
    free_entries: float | None = None
    total_entries: float | None = None
    utilization_percent: float | None = None
    source: str = "cli"


def parse_ip_interface_brief(output: str) -> list[InterfaceState]:
    states: list[InterfaceState] = []
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith(("IP Interface", "Interface", "---")):
            continue
        fields = re.split(r"\s+", line)
        if len(fields) < 4:
            continue
        name = fields[0]
        if fields[2].lower() in {"up", "down", "administratively"}:
            status = fields[2].lower()
            proto = fields[3].lower()
        else:
            proto = fields[-1].lower()
            status = " ".join(fields[2:-1]).lower()
        states.append(
            InterfaceState(
                name=name,
                admin_up=("admin down" not in status and "down" not in status),
                oper_up=(proto == "up"),
            )
        )
    return states


def parse_show_interface(output: str) -> dict[str, InterfaceCounters]:
    counters: dict[str, InterfaceCounters] = {}
    current: str | None = None
    current_counters: dict[str, float | None] = {}

    for line in output.splitlines():
        header = re.match(r"^(\S+) is .*, line protocol is", line)
        if header:
            if current:
                counters[current] = InterfaceCounters(**current_counters)
            current = header.group(1)
            current_counters = {}
            continue

        rx_match = re.search(r"input rate\s+([0-9.]+)\s*([kmg]?)(?:bits/sec|bps)", line, re.IGNORECASE)
        if rx_match:
            current_counters["rx_bps"] = _rate_to_bps(rx_match.group(1), rx_match.group(2))

        tx_match = re.search(r"output rate\s+([0-9.]+)\s*([kmg]?)(?:bits/sec|bps)", line, re.IGNORECASE)
        if tx_match:
            current_counters["tx_bps"] = _rate_to_bps(tx_match.group(1), tx_match.group(2))

        input_match = re.search(
            r"(\d+)\s+input errors,\s+(\d+)\s+CRC,\s+(\d+)\s+alignment,\s+(\d+)\s+symbol,\s+(\d+)\s+input discards",
            line,
            re.IGNORECASE,
        )
        if input_match:
            current_counters.update(
                {
                    "input_errors": float(input_match.group(1)),
                    "crc_errors": float(input_match.group(2)),
                    "alignment_errors": float(input_match.group(3)),
                    "symbol_errors": float(input_match.group(4)),
                    "input_discards": float(input_match.group(5)),
                }
            )

        output_errors_match = re.search(r"(\d+)\s+output errors,\s+(\d+)\s+collisions?", line, re.IGNORECASE)
        if output_errors_match:
            current_counters.update(
                {
                    "output_errors": float(output_errors_match.group(1)),
                    "collisions": float(output_errors_match.group(2)),
                }
            )

        output_delay_match = re.search(
            r"(\d+)\s+late collisions?,\s+(\d+)\s+deferred",
            line,
            re.IGNORECASE,
        )
        if output_delay_match:
            current_counters.update(
                {
                    "late_collisions": float(output_delay_match.group(1)),
                    "deferred": float(output_delay_match.group(2)),
                }
            )

        output_discards_match = re.search(r"(\d+)\s+output discards", line, re.IGNORECASE)
        if output_discards_match:
            current_counters["output_discards"] = float(output_discards_match.group(1))

    if current:
        counters[current] = InterfaceCounters(**current_counters)
    return counters


def _rate_to_bps(value: str, unit: str) -> float:
    multiplier = {
        "": 1,
        "k": 1_000,
        "m": 1_000_000,
        "g": 1_000_000_000,
    }
    return float(value) * multiplier.get(unit.lower(), 1)


def parse_bgp_summary(output: str, structured: Any = None) -> list[BgpSummaryEntry]:
    structured_neighbors = _parse_bgp_summary_structured(structured)
    if structured_neighbors:
        return structured_neighbors

    neighbors: list[BgpSummaryEntry] = []
    current_vrf = "default"
    header_style = ""

    for line in output.splitlines():
        vrf_match = re.search(r"BGP summary information for VRF\s+(\S+)", line)
        if vrf_match:
            current_vrf = vrf_match.group(1).rstrip(",")
            continue
        if "NLRI Rcd" in line:
            header_style = "nlri"
            continue
        if "MsgRcvd" in line and "MsgSent" in line:
            header_style = "messages"
            continue

        fields = re.split(r"\s+", line.strip())
        if not fields or not re.match(r"^\d{1,3}(\.\d{1,3}){3}$", fields[0]):
            continue

        if header_style == "nlri" and len(fields) >= 8:
            neighbors.append(
                BgpSummaryEntry(
                    neighbor=fields[0],
                    vrf=current_vrf,
                    peer_as=fields[1],
                    session_state=fields[2],
                    afi_safi=" ".join(fields[3:-4]) or "unknown",
                    afi_safi_state=fields[-4],
                    routes_received=_to_float(fields[-3]),
                    routes_accepted=_to_float(fields[-2]),
                    routes_advertised=_to_float(fields[-1]),
                )
            )
            continue

        if header_style == "messages" and len(fields) >= 11:
            has_version = len(fields) >= 12 and fields[1].isdigit() and fields[2].isdigit()
            peer_as_index = 2 if has_version else 1
            msg_received_index = peer_as_index + 1
            msg_sent_index = peer_as_index + 2
            state_index = msg_sent_index + 4
            routes_index = state_index + 1
            neighbors.append(
                BgpSummaryEntry(
                    neighbor=fields[0],
                    vrf=current_vrf,
                    peer_as=fields[peer_as_index],
                    session_state=fields[state_index] if len(fields) > state_index else fields[-4],
                    afi_safi="unknown",
                    afi_safi_state="unknown",
                    routes_received=_to_float(fields[routes_index]) if len(fields) > routes_index else None,
                    routes_accepted=_to_float(fields[routes_index + 1]) if len(fields) > routes_index + 1 else None,
                    routes_advertised=_to_float(fields[routes_index + 2]) if len(fields) > routes_index + 2 else None,
                    messages_received=_to_float(fields[msg_received_index]),
                    messages_sent=_to_float(fields[msg_sent_index]),
                )
            )
    return neighbors


def _parse_bgp_summary_structured(structured: Any) -> list[BgpSummaryEntry]:
    neighbors: list[BgpSummaryEntry] = []
    for record in _records(structured):
        neighbor = _get(record, "bgp_neigh", "neighbor", "neighbor_ip")
        if not neighbor:
            continue
        state = _get(record, "state", "session_state", "state_pfxrcd") or "unknown"
        pfx_rcd = _get(record, "state_pfxrcd", "pfxrcd", "prefix_received")
        if state.isdigit() and not pfx_rcd:
            pfx_rcd = state
            state = "Established"
        neighbors.append(
            BgpSummaryEntry(
                neighbor=neighbor,
                vrf=_get(record, "vrf") or "default",
                peer_as=_get(record, "neigh_as", "as", "peer_as") or "unknown",
                session_state=state,
                afi_safi=_get(record, "afi_safi") or "unknown",
                afi_safi_state=_get(record, "afi_safi_state") or "unknown",
                routes_received=_to_float(pfx_rcd),
                routes_accepted=_to_float(_get(record, "state_pfxacc", "pfxacc", "prefix_accepted")),
                routes_advertised=_to_float(_get(record, "pfxadv", "prefix_advertised")),
                messages_received=_to_float(_get(record, "msg_rcvd", "messages_received")),
                messages_sent=_to_float(_get(record, "msg_sent", "messages_sent")),
            )
        )
    return neighbors


def _to_float(value: str) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_device_hardware(
    version_output: str,
    inventory_output: str = "",
    version_structured: Any = None,
    inventory_structured: Any = None,
) -> DeviceHardwareInfo:
    version_record = _first_record(version_structured)
    inventory_record = _first_record(inventory_structured)
    if version_record:
        return DeviceHardwareInfo(
            model=_get(version_record, "model") or _inventory_model(inventory_output) or _get(inventory_record, "pid") or "unknown",
            version=_get(version_record, "image", "version") or "unknown",
            serial=_get(version_record, "serial_number", "serial") or _inventory_serial(inventory_output) or _get(inventory_record, "sn") or "unknown",
            architecture=_get(version_record, "architecture") or _match_value(r"Architecture:[^\S\r\n]*(.*)", version_output),
            hw_version=_get(version_record, "hw_version") or _match_value(r"Hardware version:[^\S\r\n]*(.*)", version_output),
        )

    model = _first_nonempty_line(version_output)
    if model.lower().startswith("arista "):
        model = model.split(" ", maxsplit=1)[1]

    inventory_model = _inventory_model(inventory_output)
    if inventory_model != "unknown":
        model = inventory_model

    return DeviceHardwareInfo(
        model=model or "unknown",
        version=_match_value(r"Software image version:\s*(.+)", version_output),
        serial=_first_known(_match_value(r"Serial number:\s*(.+)", version_output), _inventory_serial(inventory_output)),
        architecture=_match_value(r"Architecture:\s*(.+)", version_output),
        hw_version=_match_value(r"Hardware version:[^\S\r\n]*(.*)", version_output),
    )


def parse_power_supplies(output: str, structured: Any = None) -> list[HardwareComponent]:
    structured_components = []
    for record in _records(structured):
        status = _get(record, "power_supply_status", "status")
        if not status:
            continue
        structured_components.append(
            HardwareComponent(
                component_type="power",
                name=_get(record, "power_supply_location", "location", "slot") or "power",
                model=_get(record, "power_supply_pid", "pid", "model") or "unknown",
                serial=_get(record, "serial", "sn") or "unknown",
                status=status.replace(" ", "_").lower(),
            )
        )
    if structured_components:
        return structured_components

    if "no power supplies connected" in output.lower():
        return [HardwareComponent("power", "none", "not_present")]
    if output.startswith("COMMAND_FAILED"):
        return [HardwareComponent("power", "unsupported", "unsupported")]

    components: list[HardwareComponent] = []
    for line in output.splitlines():
        fields = re.split(r"\s+", line.strip())
        if len(fields) < 2 or fields[0].lower() in {"power", "supply", "----", "input", "output"}:
            continue
        if not (fields[0].isdigit() or fields[0].lower().startswith(("ps", "pwr"))):
            continue
        components.append(
            HardwareComponent(
                component_type="power",
                name=fields[0],
                model=fields[1] if len(fields) > 2 else "unknown",
                status=fields[-1],
            )
        )
    return components


def parse_modules(
    output: str,
    inventory_output: str = "",
    module_structured: Any = None,
    inventory_structured: Any = None,
) -> list[HardwareComponent]:
    structured_components = []
    for record in _records(module_structured):
        module = _get(record, "module")
        if not module:
            continue
        structured_components.append(
            HardwareComponent(
                component_type="module",
                name=module,
                model=_get(record, "model") or "unknown",
                serial=_get(record, "serial_num", "serial", "sn") or "unknown",
                status=(_get(record, "status") or "present").lower(),
            )
        )
    if structured_components:
        return structured_components

    if output.startswith("COMMAND_FAILED") or "Unavailable command" in output:
        hardware = parse_device_hardware("", inventory_output, None, inventory_structured)
        if hardware.model != "unknown" or hardware.serial != "unknown":
            return [
                HardwareComponent(
                    component_type="module",
                    name="chassis",
                    model=hardware.model,
                    serial=hardware.serial,
                    status="present",
                )
            ]
        return [HardwareComponent("module", "unsupported", "unsupported")]

    components: list[HardwareComponent] = []
    for line in output.splitlines():
        fields = re.split(r"\s+", line.strip())
        if len(fields) < 2 or fields[0].lower() in {"module", "----", "slot"}:
            continue
        if fields[0].isdigit():
            components.append(
                HardwareComponent(
                    component_type="module",
                    name=fields[0],
                    model=fields[1],
                    status=fields[-1],
                )
            )

    if components:
        return components

    hardware = parse_device_hardware("", inventory_output)
    if hardware.model != "unknown" or hardware.serial != "unknown":
        return [
            HardwareComponent(
                component_type="module",
                name="chassis",
                model=hardware.model,
                serial=hardware.serial,
                status="present",
            )
        ]
    return []


def parse_device_resources(version_output: str, processes_output: str = "") -> DeviceResourceUsage:
    total_kb = _to_float(_match_value(r"Total memory:\s*([0-9.]+)\s*kB", version_output))
    free_kb = _to_float(_match_value(r"Free memory:\s*([0-9.]+)\s*kB", version_output))
    memory_percent = None
    memory_total_bytes = None
    memory_free_bytes = None
    if total_kb and free_kb is not None and total_kb > 0:
        memory_total_bytes = total_kb * 1024
        memory_free_bytes = free_kb * 1024
        memory_percent = max(0.0, min(100.0, ((total_kb - free_kb) / total_kb) * 100))

    return DeviceResourceUsage(
        uptime_seconds=_parse_uptime_seconds(_match_value(r"Uptime:\s*(.+)", version_output)),
        cpu_percent=_parse_cpu_percent(processes_output),
        memory_percent=memory_percent,
        memory_total_bytes=memory_total_bytes,
        memory_free_bytes=memory_free_bytes,
    )


def parse_tcam_resources(outputs: dict[str, str]) -> list[TcamResource]:
    resources: list[TcamResource] = []
    seen: set[tuple[str, str, str]] = set()
    for source, output in outputs.items():
        if not output or output.startswith("COMMAND_FAILED"):
            continue
        for line in output.splitlines():
            resource = _parse_tcam_resource_line(line, source)
            if not resource:
                continue
            key = (resource.source, resource.region, resource.resource)
            if key in seen:
                continue
            seen.add(key)
            resources.append(resource)
    return resources


def _parse_tcam_resource_line(line: str, source: str) -> TcamResource | None:
    raw = line.strip()
    if not raw or set(raw) <= {"-", "=", " "}:
        return None
    lowered = raw.lower()
    if any(marker in lowered for marker in ("command failed", "invalid input", "unavailable command")):
        return None
    if not re.search(r"\d", raw):
        return None
    if lowered.startswith(("resource ", "feature ", "type ", "table ", "bank ", "slice ", "asic ")):
        return None

    keyed = _parse_keyed_tcam_line(raw)
    if keyed:
        resource, used, free, total, percent = keyed
        return _build_tcam_resource(source, resource, "default", used, free, total, percent)

    ratio = re.match(
        r"^(?P<resource>[A-Za-z][A-Za-z0-9/_.:() -]*?)\s+(?P<used>[0-9,]+)\s*/\s*(?P<total>[0-9,]+)(?:\s+\(?\s*(?P<percent>[0-9.]+)\s*%?\)?)?$",
        raw,
    )
    if ratio:
        return _build_tcam_resource(
            source,
            ratio.group("resource"),
            "default",
            _parse_number(ratio.group("used")),
            None,
            _parse_number(ratio.group("total")),
            _parse_number(ratio.group("percent")),
        )

    fields = [field.strip() for field in re.split(r"\s{2,}|\t+", raw) if field.strip()]
    if len(fields) < 3:
        return None
    numbers = [_parse_number(field.rstrip("%")) for field in fields[1:]]
    if sum(value is not None for value in numbers) < 2:
        return None
    resource = fields[0]
    if len(resource) > 80:
        return None

    used = free = total = percent = None
    numeric_values = [value for value in numbers if value is not None]
    if len(numeric_values) >= 3:
        used, free, total = numeric_values[:3]
    elif len(numeric_values) == 2:
        used, total = numeric_values
    if "%" in raw:
        percent = numeric_values[-1]
        if len(numeric_values) >= 3:
            used, total = numeric_values[0], numeric_values[1]
            free = None
    return _build_tcam_resource(source, resource, "default", used, free, total, percent)


def _parse_keyed_tcam_line(line: str) -> tuple[str, float | None, float | None, float | None, float | None] | None:
    used = _keyed_number(line, r"used|in\s+use|allocated")
    free = _keyed_number(line, r"free|available")
    total = _keyed_number(line, r"total|size|limit")
    percent = _keyed_number(line, r"util(?:ization)?|used\s*%|percent")
    if used is None or (free is None and total is None and percent is None):
        return None
    resource = re.split(r"\b(?:used|in\s+use|allocated|free|available|total|size|limit|util(?:ization)?|percent)\b\s*[:=]", line, maxsplit=1, flags=re.IGNORECASE)[0]
    resource = resource.strip(" :-")
    if not resource:
        return None
    return resource, used, free, total, percent


def _keyed_number(line: str, key_pattern: str) -> float | None:
    match = re.search(rf"(?:{key_pattern})\s*[:=]\s*([0-9,]+(?:\.[0-9]+)?%?)", line, re.IGNORECASE)
    if not match:
        return None
    return _parse_number(match.group(1).rstrip("%"))


def _build_tcam_resource(
    source: str,
    resource: str,
    region: str,
    used: float | None,
    free: float | None,
    total: float | None,
    percent: float | None,
) -> TcamResource | None:
    resource = re.sub(r"\s+", " ", resource.strip(" :-"))
    if not resource or resource.lower() in {"used", "free", "total", "utilization"}:
        return None
    if total is None and used is not None and free is not None:
        total = used + free
    if free is None and total is not None and used is not None:
        free = max(total - used, 0)
    if percent is None and total and used is not None and total > 0:
        percent = used / total * 100
    return TcamResource(
        resource=resource,
        region=region,
        used_entries=used,
        free_entries=free,
        total_entries=total,
        utilization_percent=percent,
        source=source,
    )


def _parse_number(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value.replace(",", ""))
    except ValueError:
        return None


def _parse_cpu_percent(output: str) -> float | None:
    match = re.search(r"%Cpu\(s\):.*?([0-9.]+)\s+id", output)
    if match:
        return max(0.0, min(100.0, 100.0 - float(match.group(1))))
    match = re.search(r"%Cpu\(s\):\s*([0-9.]+)\s+us,\s*([0-9.]+)\s+sy", output)
    if match:
        return max(0.0, min(100.0, float(match.group(1)) + float(match.group(2))))
    return None


def _parse_uptime_seconds(value: str) -> float | None:
    if not value or value == "unknown":
        return None
    total = 0
    patterns = {
        "week": 7 * 24 * 3600,
        "day": 24 * 3600,
        "hour": 3600,
        "minute": 60,
        "second": 1,
    }
    for unit, multiplier in patterns.items():
        match = re.search(rf"(\d+)\s+{unit}s?", value, re.IGNORECASE)
        if match:
            total += int(match.group(1)) * multiplier
    compact = re.search(r"(\d+):(\d+)", value)
    if total == 0 and compact:
        total += int(compact.group(1)) * 3600 + int(compact.group(2)) * 60
    return float(total) if total else None


def _first_nonempty_line(output: str) -> str:
    return next((line.strip() for line in output.splitlines() if line.strip()), "unknown")


def _match_value(pattern: str, output: str) -> str:
    match = re.search(pattern, output, re.MULTILINE)
    if not match:
        return "unknown"
    return match.group(1).strip() or "unknown"


def _inventory_model(output: str) -> str:
    for line in output.splitlines():
        fields = re.split(r"\s{2,}", line.strip())
        if len(fields) < 2:
            continue
        model = fields[0].strip()
        if model and model.lower() not in {"model", "type"} and not set(model) <= {"-"}:
            return model
    return "unknown"


def _inventory_serial(output: str) -> str:
    match = re.search(r"^\s*(\S{10,})\s*$", output, re.MULTILINE)
    return match.group(1) if match else "unknown"


def _first_known(*values: str) -> str:
    for value in values:
        if value and value != "unknown":
            return value
    return "unknown"


def _records(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [record for record in value if isinstance(record, dict)]
    if isinstance(value, dict):
        return [value]
    return []


def _first_record(value: Any) -> dict[str, Any]:
    records = _records(value)
    return records[0] if records else {}


def _get(record: dict[str, Any], *keys: str) -> str:
    if not record:
        return ""
    lowered = {str(key).lower(): val for key, val in record.items()}
    for key in keys:
        value = lowered.get(key.lower())
        if value is not None and value != "":
            return str(value).strip()
    return ""


def parse_ospf_neighbors(output: str) -> list[NeighborState]:
    neighbors: list[NeighborState] = []
    for line in output.splitlines():
        fields = re.split(r"\s+", line.strip())
        if len(fields) < 4 or not re.match(r"^\d{1,3}(\.\d{1,3}){3}$", fields[0]):
            continue
        state = next((field for field in fields if "/" in field or field.upper() in {"FULL", "2-WAY", "INIT", "DOWN"}), fields[2])
        neighbors.append(
            NeighborState(
                neighbor=fields[0],
                protocol="ospf",
                context="default",
                up=state.upper().startswith("FULL"),
                raw_state=state,
            )
        )
    return neighbors


def parse_nve_peers(output: str) -> list[NeighborState]:
    peers: list[NeighborState] = []
    for line in output.splitlines():
        fields = re.split(r"\s+", line.strip())
        if len(fields) < 2 or not re.match(r"^\d{1,3}(\.\d{1,3}){3}$", fields[0]):
            continue
        state = " ".join(fields[1:]) or "present"
        peers.append(
            NeighborState(
                neighbor=fields[0],
                protocol="nve",
                context="overlay",
                up=(state.lower() != "down"),
                raw_state=state,
            )
        )
    return peers


def parse_mac_address_table(output: str) -> list[MacTableEntry]:
    entries: list[MacTableEntry] = []
    for line in output.splitlines():
        fields = re.split(r"\s+", line.strip())
        if len(fields) < 4 or not fields[0].isdigit() or not _looks_like_mac(fields[1]):
            continue
        entries.append(
            MacTableEntry(
                vlan=fields[0],
                mac=normalize_mac(fields[1]),
                entry_type=fields[2],
                port=fields[3],
                moves=fields[4] if len(fields) > 4 else "",
                last_move=" ".join(fields[5:]) if len(fields) > 5 else "",
            )
        )
    return entries


def parse_ip_arp(output: str) -> list[ArpEntry]:
    entries: list[ArpEntry] = []
    current_vrf = "default"
    for line in output.splitlines():
        vrf_match = re.match(r"VRF:\s+(\S+)", line.strip())
        if vrf_match:
            current_vrf = vrf_match.group(1)
            continue
        fields = re.split(r"\s+", line.strip(), maxsplit=3)
        if len(fields) < 4 or not re.match(r"^\d{1,3}(\.\d{1,3}){3}$", fields[0]) or not _looks_like_mac(fields[2]):
            continue
        entries.append(
            ArpEntry(
                vrf=current_vrf,
                ip=fields[0],
                age=fields[1],
                mac=normalize_mac(fields[2]),
                interface=fields[3],
            )
        )
    return entries


def parse_lldp_neighbors(output: str) -> list[LldpNeighbor]:
    neighbors: list[LldpNeighbor] = []
    for line in output.splitlines():
        fields = re.split(r"\s+", line.strip())
        if len(fields) < 4 or not fields[0].lower().startswith(("et", "ethernet", "ma", "management")):
            continue
        if fields[0].lower() == "port" or fields[-1].lower() == "ttl":
            continue
        neighbors.append(
            LldpNeighbor(
                local_interface=_expand_interface(fields[0]),
                neighbor=fields[1],
                neighbor_interface=_expand_interface(fields[2]),
            )
        )
    return neighbors


def parse_route_summary(output: str) -> list[RouteSummaryEntry]:
    entries: list[RouteSummaryEntry] = []
    current_vrf = "default"
    for line in output.splitlines():
        stripped = line.strip()
        vrf_match = re.match(r"VRF:\s+(\S+)", stripped)
        if vrf_match:
            current_vrf = vrf_match.group(1)
            continue
        if not stripped or stripped.startswith(("-", "Route Source", "Number of routes")):
            continue
        if ":" in stripped and not stripped.startswith("Total Routes"):
            continue
        match = re.match(r"(.+?)\s+(\d+)\s*$", stripped)
        if not match:
            continue
        source = re.sub(r"\s+", " ", match.group(1).strip())
        if source.lower().startswith(("intra-area", "nssa", "external", "level-")):
            continue
        entries.append(RouteSummaryEntry(vrf=current_vrf, source=source, routes=float(match.group(2))))
    return entries


def normalize_mac(value: str) -> str:
    raw = re.sub(r"[^0-9a-fA-F]", "", value)
    if len(raw) != 12:
        return value.lower()
    raw = raw.lower()
    return f"{raw[0:4]}.{raw[4:8]}.{raw[8:12]}"


def _looks_like_mac(value: str) -> bool:
    return len(re.sub(r"[^0-9a-fA-F]", "", value)) == 12


def _expand_interface(value: str) -> str:
    replacements = {
        "Et": "Ethernet",
        "Eth": "Ethernet",
        "Ma": "Management",
        "Vx": "Vxlan",
    }
    for short, long in replacements.items():
        if value.startswith(short) and not value.startswith(long):
            return value.replace(short, long, 1)
    return value
