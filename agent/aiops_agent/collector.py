from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable

from netmiko import ConnectHandler, NetmikoAuthenticationException, NetmikoTimeoutException

from . import metrics
from .inventory import Device
from .parser import (
    BgpSummaryEntry,
    ArpEntry,
    DeviceHardwareInfo,
    DeviceResourceUsage,
    HardwareComponent,
    LldpNeighbor,
    MacTableEntry,
    RouteSummaryEntry,
    TcamResource,
    parse_bgp_summary,
    parse_device_hardware,
    parse_device_resources,
    parse_ip_arp,
    parse_modules,
    parse_power_supplies,
    parse_ip_interface_brief,
    parse_lldp_neighbors,
    parse_mac_address_table,
    parse_nve_peers,
    NeighborState,
    parse_ospf_neighbors,
    parse_route_summary,
    parse_show_interface,
    parse_tcam_resources,
)
from .settings import Settings
from .snmp import SnmpSample, collect_snmp_sample, merge_interface_counters, merge_resources


TEXTFSM_COMMAND_KEYS = {"version", "inventory", "power", "module", "bgp_summary"}

COMMANDS_BY_PLATFORM = {
    "arista_eos": {
        "hostname": "show hostname",
        "version": "show version",
        "inventory": "show inventory",
        "power": "show environment power",
        "module": "show module",
        "tcam_capacity": "show hardware capacity",
        "tcam_platform": "show platform tcam utilization",
        "tcam_resource": "show platform fap resource utilization",
        "processes": "show processes top once",
        "ip_interface_brief": "show ip interface brief",
        "interface": "show interfaces",
        "mac_address_table": "show mac address-table dynamic",
        "arp": "show ip arp vrf all",
        "lldp_neighbors": "show lldp neighbors",
        "route_summary": "show ip route vrf all summary",
        "bgp_summary": "show bgp summary",
        "ospf_neighbors": "show ip ospf neighbor",
        "nve_peers": "show vxlan vtep",
        "nve_vni": "show vxlan vni",
        "running_config": "show running-config",
    },
    "cisco_nxos": {
        "hostname": "show hostname",
        "version": "show version",
        "inventory": "show inventory",
        "power": "show environment power",
        "module": "show module",
        "tcam_capacity": "show hardware capacity",
        "tcam_platform": "show platform tcam utilization",
        "tcam_resource": "show platform hardware capacity",
        "processes": "show processes top once",
        "ip_interface_brief": "show ip interface brief",
        "interface": "show interface",
        "mac_address_table": "show mac address-table dynamic",
        "arp": "show ip arp vrf all",
        "lldp_neighbors": "show lldp neighbors",
        "route_summary": "show ip route vrf all summary",
        "bgp_summary": "show bgp all summary",
        "ospf_neighbors": "show ip ospf neighbors",
        "nve_peers": "show nve peers",
        "nve_vni": "show nve vni",
        "running_config": "show running-config",
    },
}


@dataclass
class DeviceResult:
    device: Device
    ok: bool
    duration_seconds: float
    outputs: dict[str, str] = field(default_factory=dict)
    structured_outputs: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    bgp: list[BgpSummaryEntry] = field(default_factory=list)
    ospf: list[NeighborState] = field(default_factory=list)
    nve: list[NeighborState] = field(default_factory=list)
    interfaces: list[dict[str, object]] = field(default_factory=list)
    mac_table: list[MacTableEntry] = field(default_factory=list)
    arp_table: list[ArpEntry] = field(default_factory=list)
    lldp_neighbors: list[LldpNeighbor] = field(default_factory=list)
    route_summary: list[RouteSummaryEntry] = field(default_factory=list)
    hardware: DeviceHardwareInfo = field(default_factory=DeviceHardwareInfo)
    hardware_components: list[HardwareComponent] = field(default_factory=list)
    tcam_resources: list[TcamResource] = field(default_factory=list)
    resources: DeviceResourceUsage = field(default_factory=DeviceResourceUsage)


class Collector:
    def __init__(self, settings: Settings, devices: list[Device]):
        self.settings = settings
        self.devices = devices
        self.latest_results: dict[str, DeviceResult] = {}

    def collect_all(self) -> dict[str, DeviceResult]:
        results: dict[str, DeviceResult] = {}
        workers = max(1, self.settings.max_parallel_devices)
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_map = {executor.submit(self.collect_device, device): device for device in self.devices}
            for future in as_completed(future_map):
                result = future.result()
                results[result.device.name] = result
                self._publish_metrics(result)
        self.latest_results = results
        return results

    def collect_device(self, device: Device) -> DeviceResult:
        started = time.monotonic()

        if not self.settings.device_username or not self.settings.device_password:
            return DeviceResult(
                device=device,
                ok=False,
                duration_seconds=time.monotonic() - started,
                error="missing_device_credentials",
            )

        connection = None
        outputs: dict[str, str] = {}
        structured_outputs: dict[str, Any] = {}
        try:
            connection = ConnectHandler(
                device_type=device.platform,
                host=device.host,
                port=device.port,
                username=self.settings.device_username,
                password=self.settings.device_password,
                secret=self.settings.device_secret or "",
                timeout=self.settings.command_timeout_seconds,
                conn_timeout=self.settings.command_timeout_seconds,
                auth_timeout=self.settings.command_timeout_seconds,
                banner_timeout=self.settings.command_timeout_seconds,
            )
            connection.send_command("terminal length 0", expect_string=r"#|>")
            command_set = COMMANDS_BY_PLATFORM.get(device.platform, COMMANDS_BY_PLATFORM["cisco_nxos"])
            for key, command in command_set.items():
                outputs[key] = self._safe_command(connection.send_command, command)
                if key in TEXTFSM_COMMAND_KEYS:
                    structured = self._safe_textfsm_command(connection.send_command, command)
                    if structured is not None:
                        structured_outputs[key] = structured

            interfaces = parse_ip_interface_brief(outputs.get("ip_interface_brief", ""))
            interface_counters = parse_show_interface(outputs.get("interface", ""))
            snmp_sample = self._collect_snmp_sample(device)
            if snmp_sample.interfaces:
                interface_counters = {
                    name: merge_interface_counters(interface_counters.get(name), snmp_counters)
                    for name, snmp_counters in (snmp_sample.interfaces or {}).items()
                } | {
                    name: merge_interface_counters(counters, (snmp_sample.interfaces or {}).get(name))
                    for name, counters in interface_counters.items()
                }
            interface_payload: list[dict[str, object]] = []
            for iface in interfaces:
                counters = interface_counters.get(iface.name)
                interface_payload.append(
                    {
                        "name": iface.name,
                        "admin_up": iface.admin_up,
                        "oper_up": iface.oper_up,
                        "rx_bps": counters.rx_bps if counters else None,
                        "tx_bps": counters.tx_bps if counters else None,
                        "input_errors": counters.input_errors if counters else None,
                        "crc_errors": counters.crc_errors if counters else None,
                        "alignment_errors": counters.alignment_errors if counters else None,
                        "symbol_errors": counters.symbol_errors if counters else None,
                        "input_discards": counters.input_discards if counters else None,
                        "output_errors": counters.output_errors if counters else None,
                        "collisions": counters.collisions if counters else None,
                        "late_collisions": counters.late_collisions if counters else None,
                        "deferred": counters.deferred if counters else None,
                        "output_discards": counters.output_discards if counters else None,
                    }
                )

            return DeviceResult(
                device=device,
                ok=True,
                duration_seconds=time.monotonic() - started,
                outputs=outputs,
                structured_outputs=structured_outputs,
                bgp=parse_bgp_summary(outputs.get("bgp_summary", ""), structured_outputs.get("bgp_summary")),
                ospf=parse_ospf_neighbors(outputs.get("ospf_neighbors", "")),
                nve=parse_nve_peers(outputs.get("nve_peers", "")),
                interfaces=interface_payload,
                mac_table=parse_mac_address_table(outputs.get("mac_address_table", "")),
                arp_table=parse_ip_arp(outputs.get("arp", "")),
                lldp_neighbors=parse_lldp_neighbors(outputs.get("lldp_neighbors", "")),
                route_summary=parse_route_summary(outputs.get("route_summary", "")),
                hardware=parse_device_hardware(
                    outputs.get("version", ""),
                    outputs.get("inventory", ""),
                    structured_outputs.get("version"),
                    structured_outputs.get("inventory"),
                ),
                resources=merge_resources(
                    parse_device_resources(outputs.get("version", ""), outputs.get("processes", "")),
                    snmp_sample.resources,
                ),
                hardware_components=[
                    *parse_power_supplies(outputs.get("power", ""), structured_outputs.get("power")),
                    *parse_modules(
                        outputs.get("module", ""),
                        outputs.get("inventory", ""),
                        structured_outputs.get("module"),
                        structured_outputs.get("inventory"),
                    ),
                ],
                tcam_resources=parse_tcam_resources(
                    {
                        key: value
                        for key, value in outputs.items()
                        if key.startswith("tcam_")
                    }
                ),
            )
        except (NetmikoAuthenticationException, NetmikoTimeoutException, OSError) as exc:
            return DeviceResult(
                device=device,
                ok=False,
                duration_seconds=time.monotonic() - started,
                error=exc.__class__.__name__,
                outputs=outputs,
                structured_outputs=structured_outputs,
            )
        finally:
            if connection:
                connection.disconnect()

    def _safe_command(self, runner: Callable[..., str], command: str) -> str:
        try:
            return runner(command, read_timeout=self.settings.command_timeout_seconds)
        except Exception as exc:  # Network images may reject optional commands.
            return f"COMMAND_FAILED: {exc.__class__.__name__}: {exc}"

    def _safe_textfsm_command(self, runner: Callable[..., Any], command: str) -> Any | None:
        try:
            result = runner(command, read_timeout=self.settings.command_timeout_seconds, use_textfsm=True)
        except Exception:
            return None
        if isinstance(result, str):
            return None
        return result

    def _collect_snmp_sample(self, device: Device):
        if not self.settings.snmp_enabled or not device.use_snmp or not self.settings.snmp_community:
            return SnmpSample()
        try:
            return collect_snmp_sample(
                device.host,
                self.settings.snmp_community,
                port=self.settings.snmp_port,
                timeout=self.settings.snmp_timeout_seconds,
                retries=self.settings.snmp_retries,
            )
        except Exception:
            return SnmpSample()

    def _publish_metrics(self, result: DeviceResult) -> None:
        labels = {
            "device": result.device.name,
            "host": result.device.host,
            "role": result.device.role,
            "site": result.device.site,
        }
        metrics.DEVICE_UP.labels(**labels).set(1 if result.ok else 0)
        metrics.COLLECT_SUCCESS.labels(result.device.name, result.device.host).set(1 if result.ok else 0)
        metrics.COLLECT_DURATION.labels(result.device.name, result.device.host).set(result.duration_seconds)

        if not result.ok:
            metrics.COLLECT_ERRORS.labels(
                result.device.name,
                result.device.host,
                result.error or "unknown",
            ).inc()
            return

        metrics.DEVICE_HARDWARE_INFO.labels(
            result.device.name,
            result.device.host,
            result.hardware.model,
            result.hardware.version,
            result.hardware.serial,
            result.hardware.architecture,
            result.hardware.hw_version,
        ).set(1)

        for component in result.hardware_components:
            status_value = self._hardware_status_value(component.status)
            metrics.HARDWARE_COMPONENT_STATUS.labels(
                result.device.name,
                component.component_type,
                component.name,
                component.model,
                component.serial,
                component.status,
            ).set(status_value)

        for resource in result.tcam_resources:
            tcam_labels = (
                result.device.name,
                resource.resource,
                resource.region,
                resource.source,
            )
            if resource.utilization_percent is not None:
                metrics.ASIC_TCAM_RESOURCE_UTILIZATION_PERCENT.labels(*tcam_labels).set(resource.utilization_percent)
            if resource.used_entries is not None:
                metrics.ASIC_TCAM_RESOURCE_USED_ENTRIES.labels(*tcam_labels).set(resource.used_entries)
            if resource.free_entries is not None:
                metrics.ASIC_TCAM_RESOURCE_FREE_ENTRIES.labels(*tcam_labels).set(resource.free_entries)
            if resource.total_entries is not None:
                metrics.ASIC_TCAM_RESOURCE_TOTAL_ENTRIES.labels(*tcam_labels).set(resource.total_entries)

        if result.resources.uptime_seconds is not None:
            metrics.DEVICE_UPTIME_SECONDS.labels(result.device.name, result.device.host).set(result.resources.uptime_seconds)
        if result.resources.cpu_percent is not None:
            metrics.DEVICE_CPU_UTILIZATION_PERCENT.labels(result.device.name, result.device.host).set(result.resources.cpu_percent)
        if result.resources.memory_percent is not None:
            metrics.DEVICE_MEMORY_UTILIZATION_PERCENT.labels(result.device.name, result.device.host).set(result.resources.memory_percent)
        if result.resources.memory_total_bytes is not None:
            metrics.DEVICE_MEMORY_TOTAL_BYTES.labels(result.device.name, result.device.host).set(result.resources.memory_total_bytes)
        if result.resources.memory_free_bytes is not None:
            metrics.DEVICE_MEMORY_FREE_BYTES.labels(result.device.name, result.device.host).set(result.resources.memory_free_bytes)

        for iface in result.interfaces:
            name = str(iface["name"])
            metrics.INTERFACE_ADMIN_UP.labels(result.device.name, name).set(1 if iface["admin_up"] else 0)
            metrics.INTERFACE_OPER_UP.labels(result.device.name, name).set(1 if iface["oper_up"] else 0)
            if iface["rx_bps"] is not None:
                metrics.INTERFACE_RX_BPS.labels(result.device.name, name).set(float(iface["rx_bps"]))
            if iface["tx_bps"] is not None:
                metrics.INTERFACE_TX_BPS.labels(result.device.name, name).set(float(iface["tx_bps"]))
            self._set_optional_interface_metric(metrics.INTERFACE_INPUT_ERRORS, result.device.name, name, iface["input_errors"])
            self._set_optional_interface_metric(metrics.INTERFACE_CRC_ERRORS, result.device.name, name, iface["crc_errors"])
            self._set_optional_interface_metric(metrics.INTERFACE_ALIGNMENT_ERRORS, result.device.name, name, iface["alignment_errors"])
            self._set_optional_interface_metric(metrics.INTERFACE_SYMBOL_ERRORS, result.device.name, name, iface["symbol_errors"])
            self._set_optional_interface_metric(metrics.INTERFACE_INPUT_DISCARDS, result.device.name, name, iface["input_discards"])
            self._set_optional_interface_metric(metrics.INTERFACE_OUTPUT_ERRORS, result.device.name, name, iface["output_errors"])
            self._set_optional_interface_metric(metrics.INTERFACE_COLLISIONS, result.device.name, name, iface["collisions"])
            self._set_optional_interface_metric(metrics.INTERFACE_LATE_COLLISIONS, result.device.name, name, iface["late_collisions"])
            self._set_optional_interface_metric(metrics.INTERFACE_DEFERRED, result.device.name, name, iface["deferred"])
            self._set_optional_interface_metric(metrics.INTERFACE_OUTPUT_DISCARDS, result.device.name, name, iface["output_discards"])

        for neighbor in result.bgp:
            metrics.BGP_NEIGHBOR_UP.labels(
                result.device.name,
                neighbor.neighbor,
                neighbor.context,
                neighbor.raw_state,
            ).set(1 if neighbor.up else 0)
            metrics.BGP_NEIGHBOR_DOWN.labels(
                result.device.name,
                neighbor.neighbor,
                neighbor.context,
                neighbor.raw_state,
            ).set(0 if neighbor.up else 1)
            route_labels = (
                result.device.name,
                neighbor.neighbor,
                neighbor.vrf,
                neighbor.peer_as,
                neighbor.session_state,
                neighbor.afi_safi,
                neighbor.afi_safi_state,
            )
            if neighbor.routes_received is not None:
                metrics.BGP_ROUTES_RECEIVED.labels(*route_labels).set(neighbor.routes_received)
            if neighbor.routes_accepted is not None:
                metrics.BGP_ROUTES_ACCEPTED.labels(*route_labels).set(neighbor.routes_accepted)
            if neighbor.routes_advertised is not None:
                metrics.BGP_ROUTES_ADVERTISED.labels(*route_labels).set(neighbor.routes_advertised)
            message_labels = (
                result.device.name,
                neighbor.neighbor,
                neighbor.vrf,
                neighbor.peer_as,
                neighbor.session_state,
            )
            if neighbor.messages_received is not None:
                metrics.BGP_MESSAGES_RECEIVED.labels(*message_labels).set(neighbor.messages_received)
            if neighbor.messages_sent is not None:
                metrics.BGP_MESSAGES_SENT.labels(*message_labels).set(neighbor.messages_sent)

        for neighbor in result.ospf:
            metrics.OSPF_NEIGHBOR_UP.labels(
                result.device.name,
                neighbor.neighbor,
                neighbor.raw_state,
            ).set(1 if neighbor.up else 0)

        for peer in result.nve:
            metrics.NVE_PEER_UP.labels(
                result.device.name,
                peer.neighbor,
                peer.raw_state,
            ).set(1 if peer.up else 0)

        for entry in result.route_summary:
            metrics.ROUTE_SUMMARY_ROUTES.labels(
                result.device.name,
                entry.vrf,
                entry.source,
            ).set(entry.routes)

    def _hardware_status_value(self, status: str) -> float:
        normalized = status.lower()
        if normalized in {"fail", "failed", "down", "error", "fault", "not_ok"}:
            return 0
        if normalized in {"unsupported", "not_present", "not_installed", "no_power_supplies", "unknown"}:
            return 0.5
        return 1

    def _set_optional_interface_metric(self, gauge, device: str, interface: str, value: object) -> None:
        if value is not None:
            gauge.labels(device, interface).set(float(value))
