from __future__ import annotations

import random
import socket
from dataclasses import dataclass, replace
from typing import Any

from .parser import DeviceResourceUsage, InterfaceCounters

SYS_UPTIME = "1.3.6.1.2.1.1.3.0"
HR_PROCESSOR_LOAD = "1.3.6.1.2.1.25.3.3.1.2"
HR_STORAGE_TYPE = "1.3.6.1.2.1.25.2.3.1.2"
HR_STORAGE_ALLOC_UNITS = "1.3.6.1.2.1.25.2.3.1.4"
HR_STORAGE_SIZE = "1.3.6.1.2.1.25.2.3.1.5"
HR_STORAGE_USED = "1.3.6.1.2.1.25.2.3.1.6"
HR_STORAGE_RAM = "1.3.6.1.2.1.25.2.1.2"

IF_NAME = "1.3.6.1.2.1.31.1.1.1.1"
IF_IN_DISCARDS = "1.3.6.1.2.1.2.2.1.13"
IF_IN_ERRORS = "1.3.6.1.2.1.2.2.1.14"
IF_OUT_DISCARDS = "1.3.6.1.2.1.2.2.1.19"
IF_OUT_ERRORS = "1.3.6.1.2.1.2.2.1.20"


@dataclass(frozen=True)
class SnmpSample:
    resources: DeviceResourceUsage = DeviceResourceUsage()
    interfaces: dict[str, InterfaceCounters] | None = None


class SnmpClient:
    def __init__(self, host: str, community: str, port: int = 161, timeout: float = 3, retries: int = 1):
        self.host = host
        self.community = community
        self.port = port
        self.timeout = timeout
        self.retries = retries

    def get(self, oid: str) -> Any | None:
        response = self._request(0xA0, oid)
        return response[0][1] if response else None

    def walk(self, prefix: str, limit: int = 2048) -> dict[str, Any]:
        values: dict[str, Any] = {}
        current = prefix
        for _ in range(limit):
            response = self._request(0xA1, current)
            if not response:
                break
            oid, value = response[0]
            if not _oid_startswith(oid, prefix):
                break
            values[oid] = value
            current = oid
        return values

    def _request(self, pdu_tag: int, oid: str) -> list[tuple[str, Any]]:
        packet = _build_packet(pdu_tag, self.community, random.randint(1, 2_000_000_000), oid)
        last_error: OSError | None = None
        for _ in range(max(1, self.retries + 1)):
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(self.timeout)
                try:
                    sock.sendto(packet, (self.host, self.port))
                    data, _ = sock.recvfrom(65535)
                    return _parse_response(data)
                except OSError as exc:
                    last_error = exc
        if last_error:
            raise last_error
        return []


def collect_snmp_sample(host: str, community: str, port: int = 161, timeout: float = 3, retries: int = 1) -> SnmpSample:
    client = SnmpClient(host, community, port=port, timeout=timeout, retries=retries)
    resources = _collect_resources(client)
    interfaces = _collect_interfaces(client)
    return SnmpSample(resources=resources, interfaces=interfaces)


def merge_resources(cli_resources: DeviceResourceUsage, snmp_resources: DeviceResourceUsage) -> DeviceResourceUsage:
    return DeviceResourceUsage(
        uptime_seconds=snmp_resources.uptime_seconds if snmp_resources.uptime_seconds is not None else cli_resources.uptime_seconds,
        cpu_percent=snmp_resources.cpu_percent if snmp_resources.cpu_percent is not None else cli_resources.cpu_percent,
        memory_percent=snmp_resources.memory_percent if snmp_resources.memory_percent is not None else cli_resources.memory_percent,
        memory_total_bytes=(
            snmp_resources.memory_total_bytes
            if snmp_resources.memory_total_bytes is not None
            else cli_resources.memory_total_bytes
        ),
        memory_free_bytes=(
            snmp_resources.memory_free_bytes
            if snmp_resources.memory_free_bytes is not None
            else cli_resources.memory_free_bytes
        ),
    )


def merge_interface_counters(cli: InterfaceCounters | None, snmp: InterfaceCounters | None) -> InterfaceCounters | None:
    if cli is None and snmp is None:
        return None
    base = cli or InterfaceCounters()
    if snmp is None:
        return base
    return replace(
        base,
        input_errors=snmp.input_errors if snmp.input_errors is not None else base.input_errors,
        input_discards=snmp.input_discards if snmp.input_discards is not None else base.input_discards,
        output_errors=snmp.output_errors if snmp.output_errors is not None else base.output_errors,
        output_discards=snmp.output_discards if snmp.output_discards is not None else base.output_discards,
    )


def _collect_resources(client: SnmpClient) -> DeviceResourceUsage:
    uptime = _number_or_none(client.get(SYS_UPTIME))
    cpu_loads = [_number_or_none(value) for value in client.walk(HR_PROCESSOR_LOAD).values()]
    cpu_values = [value for value in cpu_loads if value is not None]

    memory_total = None
    memory_free = None
    memory_percent = None
    storage_types = client.walk(HR_STORAGE_TYPE)
    for oid, value in storage_types.items():
        if str(value) != HR_STORAGE_RAM:
            continue
        suffix = oid.removeprefix(HR_STORAGE_TYPE + ".")
        alloc_units = _number_or_none(client.get(f"{HR_STORAGE_ALLOC_UNITS}.{suffix}"))
        size = _number_or_none(client.get(f"{HR_STORAGE_SIZE}.{suffix}"))
        used = _number_or_none(client.get(f"{HR_STORAGE_USED}.{suffix}"))
        if alloc_units is None or size is None or used is None or size <= 0:
            continue
        memory_total = alloc_units * size
        memory_free = alloc_units * max(size - used, 0)
        memory_percent = used / size * 100
        break

    return DeviceResourceUsage(
        uptime_seconds=uptime / 100 if uptime is not None else None,
        cpu_percent=sum(cpu_values) / len(cpu_values) if cpu_values else None,
        memory_percent=memory_percent,
        memory_total_bytes=memory_total,
        memory_free_bytes=memory_free,
    )


def _collect_interfaces(client: SnmpClient) -> dict[str, InterfaceCounters]:
    names = client.walk(IF_NAME)
    interfaces: dict[str, InterfaceCounters] = {}
    for oid, raw_name in names.items():
        suffix = oid.removeprefix(IF_NAME + ".")
        name = _string_value(raw_name)
        if not name:
            continue
        counter = InterfaceCounters(
            input_errors=_number_or_none(client.get(f"{IF_IN_ERRORS}.{suffix}")),
            input_discards=_number_or_none(client.get(f"{IF_IN_DISCARDS}.{suffix}")),
            output_errors=_number_or_none(client.get(f"{IF_OUT_ERRORS}.{suffix}")),
            output_discards=_number_or_none(client.get(f"{IF_OUT_DISCARDS}.{suffix}")),
        )
        for alias in _interface_aliases(name):
            interfaces[alias] = counter
    return interfaces


def _interface_aliases(name: str) -> set[str]:
    aliases = {name}
    if name.startswith("Et") and name[2:].isdigit():
        aliases.add(f"Ethernet{name[2:]}")
    if name.startswith("Ethernet") and name[8:].isdigit():
        aliases.add(f"Et{name[8:]}")
    if name.startswith("Ma") and name[2:].isdigit():
        aliases.add(f"Management{name[2:]}")
    if name.startswith("Management") and name[10:].isdigit():
        aliases.add(f"Ma{name[10:]}")
    return aliases


def _number_or_none(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _string_value(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode(errors="ignore")
    return str(value)


def _build_packet(pdu_tag: int, community: str, request_id: int, oid: str) -> bytes:
    varbind = _sequence(_oid(oid) + _null())
    pdu = _tlv(
        pdu_tag,
        _integer(request_id)
        + _integer(0)
        + _integer(0)
        + _sequence(varbind),
    )
    return _sequence(_integer(1) + _octet_string(community.encode()) + pdu)


def _parse_response(data: bytes) -> list[tuple[str, Any]]:
    tag, payload, _ = _read_tlv(data, 0)
    if tag != 0x30:
        return []
    pos = 0
    _, _, pos = _read_tlv(payload, pos)  # version
    _, _, pos = _read_tlv(payload, pos)  # community
    _, pdu, _ = _read_tlv(payload, pos)
    pos = 0
    _, _, pos = _read_tlv(pdu, pos)  # request id
    _, error_status_raw, pos = _read_tlv(pdu, pos)
    _, _, pos = _read_tlv(pdu, pos)  # error index
    if _decode_int(error_status_raw) != 0:
        return []
    tag, varbinds_payload, _ = _read_tlv(pdu, pos)
    if tag != 0x30:
        return []

    values: list[tuple[str, Any]] = []
    pos = 0
    while pos < len(varbinds_payload):
        tag, varbind_payload, pos = _read_tlv(varbinds_payload, pos)
        if tag != 0x30:
            continue
        inner_pos = 0
        oid_tag, oid_payload, inner_pos = _read_tlv(varbind_payload, inner_pos)
        value_tag, value_payload, _ = _read_tlv(varbind_payload, inner_pos)
        if oid_tag != 0x06 or value_tag in {0x80, 0x81, 0x82}:
            continue
        values.append((_decode_oid(oid_payload), _decode_value(value_tag, value_payload)))
    return values


def _read_tlv(data: bytes, pos: int) -> tuple[int, bytes, int]:
    tag = data[pos]
    pos += 1
    length_byte = data[pos]
    pos += 1
    if length_byte & 0x80:
        length_size = length_byte & 0x7F
        length = int.from_bytes(data[pos : pos + length_size], "big")
        pos += length_size
    else:
        length = length_byte
    return tag, data[pos : pos + length], pos + length


def _decode_value(tag: int, payload: bytes) -> Any:
    if tag in {0x02, 0x41, 0x42, 0x43, 0x46}:
        return int.from_bytes(payload, "big", signed=False)
    if tag == 0x04:
        return payload
    if tag == 0x06:
        return _decode_oid(payload)
    if tag == 0x05:
        return None
    return payload


def _decode_int(payload: bytes) -> int:
    return int.from_bytes(payload, "big", signed=True) if payload else 0


def _decode_oid(payload: bytes) -> str:
    if not payload:
        return ""
    first = payload[0]
    parts = [first // 40, first % 40]
    value = 0
    for byte in payload[1:]:
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            parts.append(value)
            value = 0
    return ".".join(str(part) for part in parts)


def _integer(value: int) -> bytes:
    if value == 0:
        payload = b"\x00"
    else:
        payload = value.to_bytes((value.bit_length() + 7) // 8, "big", signed=False)
        if payload[0] & 0x80:
            payload = b"\x00" + payload
    return _tlv(0x02, payload)


def _octet_string(value: bytes) -> bytes:
    return _tlv(0x04, value)


def _null() -> bytes:
    return _tlv(0x05, b"")


def _oid(value: str) -> bytes:
    parts = [int(part) for part in value.split(".")]
    if len(parts) < 2:
        raise ValueError(f"invalid oid: {value}")
    payload = bytes([parts[0] * 40 + parts[1]])
    for part in parts[2:]:
        payload += _base128(part)
    return _tlv(0x06, payload)


def _sequence(payload: bytes) -> bytes:
    return _tlv(0x30, payload)


def _tlv(tag: int, payload: bytes) -> bytes:
    return bytes([tag]) + _length(len(payload)) + payload


def _length(size: int) -> bytes:
    if size < 0x80:
        return bytes([size])
    raw = size.to_bytes((size.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(raw)]) + raw


def _base128(value: int) -> bytes:
    encoded = [value & 0x7F]
    value >>= 7
    while value:
        encoded.insert(0, 0x80 | (value & 0x7F))
        value >>= 7
    return bytes(encoded)


def _oid_startswith(oid: str, prefix: str) -> bool:
    return oid == prefix or oid.startswith(prefix + ".")
