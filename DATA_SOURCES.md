# Data Source Mapping

This document explains which data is collected through CLI, SNMP, and telemetry.

## Summary

Current implementation:

- CLI over SSH: primary and active data source.
- SNMP: inventory fields are reserved, but no active SNMP polling is implemented yet.
- Telemetry: optional Telegraf receiver for Cisco MDT/gRPC, exposed to Prometheus separately.

## CLI Over SSH

The Python Agent uses Netmiko to connect to each device and run show commands. This is the source for the `aiops_*` metrics used by the main Grafana dashboard.

Arista EOS commands:

| Data | Command |
|---|---|
| Hostname | `show hostname` |
| Software / hardware version | `show version` |
| Inventory / serial / model | `show inventory` |
| Power status | `show environment power` |
| Module status | `show module` |
| CPU and memory | `show processes top once` |
| Interface state | `show ip interface brief` |
| Interface traffic/errors/discards | `show interfaces` |
| MAC lookup | `show mac address-table dynamic` |
| ARP lookup | `show ip arp vrf all` |
| LLDP topology | `show lldp neighbors` |
| Route summary | `show ip route vrf all summary` |
| BGP state and NLRI counters | `show bgp summary` |
| OSPF neighbors | `show ip ospf neighbor` |
| VXLAN peers | `show vxlan vtep` |
| VXLAN VNI | `show vxlan vni` |
| Config snapshot | `show running-config` |

Cisco NX-OS commands:

| Data | Command |
|---|---|
| Hostname | `show hostname` |
| Software / hardware version | `show version` |
| Inventory / serial / model | `show inventory` |
| Power status | `show environment power` |
| Module status | `show module` |
| CPU and memory | `show processes top once` |
| Interface state | `show ip interface brief` |
| Interface traffic/errors/discards | `show interface` |
| MAC lookup | `show mac address-table dynamic` |
| ARP lookup | `show ip arp vrf all` |
| LLDP topology | `show lldp neighbors` |
| Route summary | `show ip route vrf all summary` |
| BGP state | `show bgp all summary` |
| OSPF neighbors | `show ip ospf neighbors` |
| NVE peers | `show nve peers` |
| NVE VNI | `show nve vni` |
| Config snapshot | `show running-config` |

## SNMP

SNMP is not actively used by the current collector.

The inventory schema keeps these fields for future expansion:

```yaml
use_snmp: false
snmp_community_env: AIOPS_SNMP_COMMUNITY
```

At the moment, setting `use_snmp: true` does not start SNMP polling. If SNMP is required later, add a dedicated SNMP collector and map the required OIDs to Prometheus metrics.

## Telemetry

Telemetry is handled by Telegraf, not by the Python CLI collector.

Current Telegraf input:

```toml
[[inputs.cisco_telemetry_mdt]]
  transport = "grpc"
  service_address = ":57000"
```

Telegraf exposes telemetry metrics here:

```text
http://<monitor-host>:9273/metrics
```

Prometheus scrapes that endpoint with job name:

```yaml
job_name: cisco-mdt-telegraf
```

Telemetry metrics are separate from the Agent's `aiops_*` CLI metrics. Devices must be configured to dial out telemetry to the monitoring host on TCP `57000`.

## Reachability Model

For real projects, the monitoring host can reach devices in either mode:

- Direct reachability: monitoring host can ping and SSH to device management IPs. Keep `route.enabled: false`.
- Routed through a jump host or gateway: set `route.enabled: true`, then configure `route.destination` and `route.gateway`.

Example direct mode:

```yaml
route:
  enabled: false
  destination: ""
  gateway: ""
```

Example routed mode:

```yaml
route:
  enabled: true
  destination: 172.16.1.0/24
  gateway: 192.168.20.129
```

