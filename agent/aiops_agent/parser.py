from __future__ import annotations

import re
from ipaddress import IPv4Interface
from dataclasses import dataclass, replace
from typing import Any

from .hardware_inventory import identity, known, parse_inventory, slot_id


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
    peer_hostname: str = "unknown"


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


def is_interface_name(value: str) -> bool:
    """Recognize numbered EOS/NX-OS/PAN-OS interface names, not CLI prose."""
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z-]*\d+(?:[/.:]\d+)*", value))


def parse_ip_interface_brief(output: str) -> list[InterfaceState]:
    if output.lstrip().startswith("COMMAND_FAILED"):
        return []
    states: list[InterfaceState] = []
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith(("IP Interface", "Interface", "---")):
            continue
        fields = re.split(r"\s+", line)
        if len(fields) < 3:
            continue
        name = fields[0]
        if not is_interface_name(name):
            continue
        if fields[1].lower() != "unassigned":
            try:
                IPv4Interface(fields[1])
            except ValueError:
                continue
        nxos_state = re.fullmatch(
            r"protocol-(up|down)/link-(up|down)/admin-(up|down)",
            fields[2], re.IGNORECASE,
        )
        if nxos_state:
            protocol, link, admin = (value.lower() for value in nxos_state.groups())
            states.append(InterfaceState(name=name, admin_up=admin == "up",
                                         oper_up=protocol == link == admin == "up"))
            continue
        state = re.match(
            r"^(up|down|admin(?:istratively)?\s+down)\s+"
            r"(up|down|notpresent|lowerlayerdown|dormant|testing|unknown)(?:\s|$)",
            " ".join(fields[2:]), re.IGNORECASE,
        )
        if not state:
            continue
        status, proto = (value.lower() for value in state.groups())
        states.append(
            InterfaceState(
                name=name,
                admin_up=not status.startswith("admin"),
                oper_up=(proto == "up"),
            )
        )
    return states


def parse_ip_interface_addresses(output: str) -> dict[str, str]:
    addresses: dict[str, str] = {}
    for line in output.splitlines():
        fields = re.split(r"\s+", line.strip())
        if len(fields) < 2 or fields[0].lower() in {"interface", "ip"}:
            continue
        if not re.match(r"^\d{1,3}(\.\d{1,3}){3}(?:/\d+)?$", fields[1]):
            continue
        addresses[fields[0]] = fields[1].split("/", maxsplit=1)[0]
    return addresses


def parse_panos_interfaces(output: str) -> list[dict[str, object]]:
    """Parse PAN-OS ``show interface all`` summary into normalized interfaces."""
    rows: list[dict[str, object]] = []
    in_hw = False
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("name") and "mac address" in stripped.lower():
            in_hw = True
            continue
        if in_hw and stripped.startswith("---"):
            continue
        if in_hw:
            fields = re.split(r"\s+", stripped)
            if len(fields) >= 4 and re.match(r"^(ethernet|ae)\S+$", fields[0], re.I) and "/" in fields[2]:
                state = fields[2].lower()
                is_up = state.endswith("/up") or state == "up"
                rows.append({"name": fields[0], "oper_up": is_up, "admin_up": "down" not in state,
                             "mac": normalize_mac(fields[3]), "description": ""})
                continue
        # logical interface rows contain an address in the final column
        fields = re.split(r"\s+", stripped)
        if len(fields) >= 2 and re.match(r"^(ethernet|ae)\S+$", fields[0], re.I):
            address = next((f.split("/")[0] for f in fields[1:] if re.match(r"^\d{1,3}(?:\.\d{1,3}){3}/\d+$", f)), None)
            rows.append({"name": fields[0], "oper_up": True, "admin_up": True, "ip": address, "description": ""})
    # de-duplicate summary/logical records while retaining addresses
    merged: dict[str, dict[str, object]] = {}
    for row in rows:
        merged.setdefault(str(row["name"]), {}).update(row)
    return list(merged.values())


def parse_panos_arp(output: str) -> list[ArpEntry]:
    entries: list[ArpEntry] = []
    for line in output.splitlines():
        fields = re.split(r"\s+", line.strip())
        if len(fields) >= 4 and re.match(r"^\d{1,3}(?:\.\d{1,3}){3}$", fields[1]) and _looks_like_mac(fields[2]):
            entries.append(ArpEntry(vrf="default", ip=fields[1], age=fields[-1], mac=normalize_mac(fields[2]), interface=fields[0]))
    return entries


def parse_panos_sessions(output: str) -> dict[str, float]:
    values: dict[str, float] = {}
    patterns = {
        "sessions_active": r"(?:allocated sessions|active sessions|sessions active)\s*:\s*([\d,]+)",
        "sessions_per_second": r"(?:new connection establish rate|session setup rate|sessions/sec)\s*:\s*([\d.]+)",
        "throughput_bps": r"(?:throughput|bytes/sec)\s*:\s*([\d.]+)\s*([KMG]?)bps",
    }
    text = output.lower()
    for key, pattern in patterns.items():
        match = re.search(pattern, text, re.I)
        if match:
            value = float(match.group(1).replace(",", ""))
            if key == "throughput_bps":
                value *= {"k": 1e3, "m": 1e6, "g": 1e9}.get(match.group(2).lower(), 1)
            values[key] = value
    return values


def _parse_panos_uptime(system_info: str) -> float | None:
    """PAN-OS system uptime is a duration, e.g. '12 days, 01:02:03'."""
    if _hardware_command_failed(system_info):
        return None
    field = re.search(r"^\s*uptime[^\S\r\n]*:[^\S\r\n]*([^\r\n]*)", system_info, re.I | re.M)
    if not field:
        return None
    duration = re.fullmatch(r"\s*(?:(\d+)\s+days?\s*,\s*)?(\d+):([0-5]\d):([0-5]\d)\s*", field.group(1), re.I)
    if not duration:
        return None
    days, hours, minutes, seconds = (int(value or 0) for value in duration.groups())
    return float(days * 86400 + hours * 3600 + minutes * 60 + seconds)


def parse_panos_resources(output: str, system_info: str = "") -> DeviceResourceUsage:
    text = output.lower()
    cpu = _to_float(_match_value(r"(?:cpu utilization|cpu usage|mp\s+cpu)\s*[:=]\s*([0-9.]+)", text))
    if cpu is None:
        match = re.search(r"%cpu\(s\):\s*([0-9.]+)\s+us,\s*([0-9.]+)\s+sy", text)
        if match:
            cpu = float(match.group(1)) + float(match.group(2))
    mem = _to_float(_match_value(r"(?:memory utilization|memory usage)\s*[:=]\s*([0-9.]+)", text))
    if mem is None:
        match = re.search(r"mem\s*:\s*([0-9.]+)\s+total,\s*([0-9.]+)\s+free,\s*([0-9.]+)\s+used", text)
        if match and float(match.group(1)) > 0:
            mem = float(match.group(3)) / float(match.group(1)) * 100
    return DeviceResourceUsage(uptime_seconds=_parse_panos_uptime(system_info), cpu_percent=cpu, memory_percent=mem)


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


def parse_panos_hardware(system_info: str) -> DeviceHardwareInfo:
    """Read explicit PAN-OS system-info fields, never infer a model from a line."""
    if re.search(
        r"COMMAND_FAILED|Invalid (?:command|syntax|input)|Unknown command|Syntax error",
        system_info,
        re.IGNORECASE,
    ):
        return DeviceHardwareInfo()

    fields = {}
    for line in system_info.splitlines():
        match = re.fullmatch(r"\s*([A-Za-z][A-Za-z0-9-]*)\s*:\s*(.*?)\s*", line)
        if match:
            fields[match.group(1).lower()] = match.group(2) or "unknown"
    return DeviceHardwareInfo(
        model=fields.get("model", "unknown"),
        version=fields.get("sw-version", "unknown"),
        serial=fields.get("serial", "unknown"),
        architecture=fields.get("architecture", "unknown"),
        hw_version=fields.get("hw-version", "unknown"),
    )


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


def _component_status(value: str) -> str:
    return re.sub(r"[ -]+", "_", value.strip().lower()).rstrip("_*") or "unknown"


def _hardware_command_failed(output: str) -> bool:
    return bool(re.search(r"COMMAND_FAILED|Unavailable command|Incomplete command|Invalid (?:command|input|syntax)|Unknown command|% Error", output, re.I))


def _enrich_components(components, parts):
    enriched = []
    for component in components:
        kind = "chassis" if component.name == "chassis" else component.component_type
        model, serial = identity(parts, kind, component.name, component.model)
        enriched.append(replace(component,
            model=component.model if known(component.model) else model,
            serial=component.serial if known(component.serial) else serial))
    return enriched


def parse_power_supplies(output: str, structured: Any = None,
                         inventory_output: str = "", inventory_structured: Any = None) -> list[HardwareComponent]:
    parts = parse_inventory(inventory_output, inventory_structured)
    if "no power supplies connected" in output.lower() or any(
        "no power supplies" in _get(r, "status", "power_supply_status").lower() for r in _records(structured)
    ):
        return [HardwareComponent("power", "none", "not_present", "not_applicable", "not_applicable")]
    components = []
    for record in _records(structured):
        status = _get(record, "power_supply_status", "status")
        if status:
            components.append(HardwareComponent(
                "power", slot_id(_get(record, "power_supply_location", "location", "slot") or "power"),
                _component_status(status),
                _get(record, "power_supply_pid", "pid", "model") or "unknown",
                _get(record, "serial", "sn") or "unknown"))
    if not components and not _hardware_command_failed(output):
        models = {}
        for line in output.splitlines():
            fields = line.split()
            if len(fields) < 2 or not re.fullmatch(r"(?:PSU?|PWR)?[0-9]+(?:/[0-9]+)*", fields[0], re.I):
                continue
            name = slot_id(fields[0])
            if known(fields[1]) and not re.fullmatch(r"[0-9.]+[WAV]|Not|Offline", fields[1], re.I):
                models[name] = fields[1]
            # Status precedes uptime on EOS, and may precede wattage on NX-OS.
            match = re.search(r"\b(Not Present|Not Installed|Not Ok|Power Loss|Powered[ -]dn|Ok|Failed|Fail|Fault|Offline|Down|Absent)\b", line, re.I)
            if not match:
                continue
            absent = _component_status(match.group()) in {"not_present", "not_installed", "absent"}
            components.append(HardwareComponent("power", name, _component_status(match.group()),
                                                "unknown" if absent else models.get(name, "unknown")))
    if not components:
        for part in parts:
            if part.kind == "power" and not any(c.name == part.slot for c in components):
                components.append(HardwareComponent("power", part.slot, "unknown"))
    if not components and _hardware_command_failed(output):
        return [HardwareComponent("power", "unsupported", "unsupported")]
    return _enrich_components(components, parts)


def parse_modules(output: str, inventory_output: str = "", module_structured: Any = None,
                  inventory_structured: Any = None) -> list[HardwareComponent]:
    parts = parse_inventory(inventory_output, inventory_structured)
    components = []
    for record in _records(module_structured):
        module = _get(record, "module")
        if module:
            components.append(HardwareComponent("module", slot_id(module),
                _component_status(_get(record, "status") or "unknown"),
                _get(record, "model") or "unknown", _get(record, "serial_num", "serial", "sn") or "unknown"))
    if not components and not _hardware_command_failed(output):
        section = None
        statuses = {}
        for line in output.splitlines():
            text = line.strip()
            if re.match(r"(?:Mod|Module|Slot)\s", text, re.I):
                section = ("identity" if re.search(r"Model", text, re.I) else
                           "status" if re.search(r"Status", text, re.I) else None)
                serial_column = bool(re.search(r"Serial", text, re.I))
                continue
            fields = text.split()
            if not fields or not re.fullmatch(r"(?:Fabric|Switchcard|Supervisor|Linecard)?[0-9]+(?:/[0-9]+)*", fields[0], re.I):
                continue
            if section == "identity" and len(fields) >= 4:
                if serial_column:
                    components.append(HardwareComponent("module", slot_id(fields[0]), "unknown", fields[-2], fields[-1]))
                else:
                    match = re.search(r"\s+(ok|active(?:\s+\*)?|ha-standby|powered-dn|powered-up|offline|failed|standby)\s*$", text, re.I)
                    if match:
                        model = text[:match.start()].split()[-1]
                        components.append(HardwareComponent("module", fields[0], _component_status(match.group()), model))
            elif section == "status" and len(fields) >= 2:
                statuses[slot_id(fields[0])] = _component_status(fields[1])
        components = [replace(c, status=statuses.get(c.name, c.status)) for c in components]
    # Some TextFSM versions treat the MAC table heading as a Fillup status.
    # The explicit status section is authoritative, also for structured rows.
    statuses = {}
    in_status = False
    for line in output.splitlines():
        text = line.strip()
        if re.match(r"(?:Mod|Module|Slot)\s", text, re.I):
            in_status = bool(re.match(r"(?:Mod|Module|Slot)\s+Status\b", text, re.I))
            continue
        fields = text.split()
        if in_status and len(fields) >= 2 and re.fullmatch(r"(?:Fabric|Switchcard|Supervisor|Linecard)?[0-9]+(?:/[0-9]+)*", fields[0], re.I):
            statuses[slot_id(fields[0])] = _component_status(fields[1])
    components = [replace(c, status=statuses.get(c.name, "unknown" if c.status == "mac" else c.status)) for c in components]
    if not components:
        for part in parts:
            if part.kind in {"module", "chassis"} and not any(c.name == part.slot for c in components):
                components.append(HardwareComponent("module", part.slot, "present" if part.kind == "chassis" else "unknown"))
    if not components and _hardware_command_failed(output):
        return [HardwareComponent("module", "unsupported", "unsupported")]
    return _enrich_components(components, parts)


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
