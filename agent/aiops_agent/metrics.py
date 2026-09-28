from __future__ import annotations

from prometheus_client import Gauge, Counter


DEVICE_UP = Gauge(
    "aiops_device_up",
    "Device reachability from the AIOps collector.",
    ["device", "host", "role", "site"],
)

COLLECT_SUCCESS = Gauge(
    "aiops_collect_success",
    "Last collection success for the device.",
    ["device", "host"],
)

COLLECT_DURATION = Gauge(
    "aiops_collect_duration_seconds",
    "Collection duration per device.",
    ["device", "host"],
)

COLLECT_ERRORS = Counter(
    "aiops_collect_errors_total",
    "Total collection errors.",
    ["device", "host", "error_type"],
)

INTERFACE_ADMIN_UP = Gauge(
    "aiops_interface_admin_up",
    "Interface administrative state.",
    ["device", "interface"],
)

INTERFACE_OPER_UP = Gauge(
    "aiops_interface_oper_up",
    "Interface operational state.",
    ["device", "interface"],
)

INTERFACE_RX_BPS = Gauge(
    "aiops_interface_rx_bps",
    "Interface receive traffic in bits per second.",
    ["device", "interface"],
)

INTERFACE_TX_BPS = Gauge(
    "aiops_interface_tx_bps",
    "Interface transmit traffic in bits per second.",
    ["device", "interface"],
)

INTERFACE_INPUT_ERRORS = Gauge(
    "aiops_interface_input_errors",
    "Interface input errors.",
    ["device", "interface"],
)

INTERFACE_CRC_ERRORS = Gauge(
    "aiops_interface_crc_errors",
    "Interface CRC errors.",
    ["device", "interface"],
)

INTERFACE_ALIGNMENT_ERRORS = Gauge(
    "aiops_interface_alignment_errors",
    "Interface alignment errors.",
    ["device", "interface"],
)

INTERFACE_SYMBOL_ERRORS = Gauge(
    "aiops_interface_symbol_errors",
    "Interface symbol errors.",
    ["device", "interface"],
)

INTERFACE_INPUT_DISCARDS = Gauge(
    "aiops_interface_input_discards",
    "Interface input discards.",
    ["device", "interface"],
)

INTERFACE_OUTPUT_ERRORS = Gauge(
    "aiops_interface_output_errors",
    "Interface output errors.",
    ["device", "interface"],
)

INTERFACE_COLLISIONS = Gauge(
    "aiops_interface_collisions",
    "Interface collisions.",
    ["device", "interface"],
)

INTERFACE_LATE_COLLISIONS = Gauge(
    "aiops_interface_late_collisions",
    "Interface late collisions.",
    ["device", "interface"],
)

INTERFACE_DEFERRED = Gauge(
    "aiops_interface_deferred",
    "Interface deferred transmissions.",
    ["device", "interface"],
)

INTERFACE_OUTPUT_DISCARDS = Gauge(
    "aiops_interface_output_discards",
    "Interface output discards.",
    ["device", "interface"],
)

BGP_NEIGHBOR_UP = Gauge(
    "aiops_bgp_neighbor_up",
    "BGP neighbor state.",
    ["device", "neighbor", "vrf", "state"],
)

BGP_NEIGHBOR_DOWN = Gauge(
    "aiops_bgp_neighbor_down",
    "BGP neighbor down flag.",
    ["device", "neighbor", "vrf", "state"],
)

BGP_ROUTES_RECEIVED = Gauge(
    "aiops_bgp_routes_received",
    "BGP routes or NLRI received from a neighbor.",
    ["device", "neighbor", "vrf", "peer_as", "session_state", "afi_safi", "afi_safi_state"],
)

BGP_ROUTES_ACCEPTED = Gauge(
    "aiops_bgp_routes_accepted",
    "BGP routes or NLRI accepted from a neighbor.",
    ["device", "neighbor", "vrf", "peer_as", "session_state", "afi_safi", "afi_safi_state"],
)

BGP_ROUTES_ADVERTISED = Gauge(
    "aiops_bgp_routes_advertised",
    "BGP routes or NLRI advertised to a neighbor.",
    ["device", "neighbor", "vrf", "peer_as", "session_state", "afi_safi", "afi_safi_state"],
)

BGP_MESSAGES_RECEIVED = Gauge(
    "aiops_bgp_messages_received",
    "BGP messages received from a neighbor.",
    ["device", "neighbor", "vrf", "peer_as", "session_state"],
)

BGP_MESSAGES_SENT = Gauge(
    "aiops_bgp_messages_sent",
    "BGP messages sent to a neighbor.",
    ["device", "neighbor", "vrf", "peer_as", "session_state"],
)

OSPF_NEIGHBOR_UP = Gauge(
    "aiops_ospf_neighbor_up",
    "OSPF neighbor state.",
    ["device", "neighbor", "state"],
)

NVE_PEER_UP = Gauge(
    "aiops_nve_peer_up",
    "NVE overlay peer state.",
    ["device", "peer", "peer_hostname", "state"],
)

DEVICE_HARDWARE_INFO = Gauge(
    "aiops_device_hardware_info",
    "Device hardware and software inventory information.",
    ["device", "host", "model", "version", "serial", "architecture", "hw_version"],
)

HARDWARE_COMPONENT_STATUS = Gauge(
    "aiops_hardware_component_status",
    "Device hardware component status.",
    ["device", "component_type", "name", "model", "serial", "status"],
)

ASIC_TCAM_RESOURCE_UTILIZATION_PERCENT = Gauge(
    "aiops_asic_tcam_resource_utilization_percent",
    "ASIC or TCAM resource utilization percentage from CLI fallback collection.",
    ["device", "resource", "region", "collection_source"],
)

ASIC_TCAM_RESOURCE_USED_ENTRIES = Gauge(
    "aiops_asic_tcam_resource_used_entries",
    "ASIC or TCAM resource used entries from CLI fallback collection.",
    ["device", "resource", "region", "collection_source"],
)

ASIC_TCAM_RESOURCE_FREE_ENTRIES = Gauge(
    "aiops_asic_tcam_resource_free_entries",
    "ASIC or TCAM resource free entries from CLI fallback collection.",
    ["device", "resource", "region", "collection_source"],
)

ASIC_TCAM_RESOURCE_TOTAL_ENTRIES = Gauge(
    "aiops_asic_tcam_resource_total_entries",
    "ASIC or TCAM resource total entries from CLI fallback collection.",
    ["device", "resource", "region", "collection_source"],
)

DEVICE_UPTIME_SECONDS = Gauge(
    "aiops_device_uptime_seconds",
    "Device uptime in seconds.",
    ["device", "host"],
)

DEVICE_CPU_UTILIZATION_PERCENT = Gauge(
    "aiops_device_cpu_utilization_percent",
    "Device CPU utilization percentage.",
    ["device", "host"],
)

DEVICE_MEMORY_UTILIZATION_PERCENT = Gauge(
    "aiops_device_memory_utilization_percent",
    "Device memory utilization percentage.",
    ["device", "host"],
)

DEVICE_MEMORY_TOTAL_BYTES = Gauge(
    "aiops_device_memory_total_bytes",
    "Device total memory in bytes.",
    ["device", "host"],
)

DEVICE_MEMORY_FREE_BYTES = Gauge(
    "aiops_device_memory_free_bytes",
    "Device free memory in bytes.",
    ["device", "host"],
)

ROUTE_SUMMARY_ROUTES = Gauge(
    "aiops_route_summary_routes",
    "Route count by VRF and route source.",
    ["device", "vrf", "source"],
)
