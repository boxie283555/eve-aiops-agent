# Data Source Mapping

This document explains which data is collected through CLI, SNMP, and telemetry.

## Summary

Recommended architecture:

- CLI over SSH: configuration snapshots and state that is easiest or safest to read from show commands.
- SNMP: standard platform health and counters where available, especially CPU, memory, uptime, and interface counters.
- Telemetry: high-frequency streaming data, especially interface traffic and protocol state such as BGP when the platform supports it.

Current implementation status:

- CLI over SSH: active primary source for the `aiops_*` metrics.
- SNMP: preferred in the configuration policy for CPU, memory, uptime, and interface counters, but the SNMP poller still needs to be implemented.
- Telemetry: Telegraf receiver is available for Cisco MDT/gRPC and Prometheus scrapes it separately. Mapping telemetry into the main `aiops_*` dashboard still depends on the device telemetry subscription and metric names.

The single project config records the desired source policy:

```yaml
collection_sources:
  default_fallback: cli
  preferences:
    cpu_memory: snmp
    uptime: snmp
    interface_traffic: telemetry
    interface_errors_discards: snmp
    bgp: telemetry
    config_snapshot: cli
```

If the preferred source is unavailable, keep CLI as the fallback so the dashboard still has data.

## Recommended Source By Data Type

| Data | Preferred Source | Fallback | Notes |
|---|---|---|---|
| Device reachability | CLI/SSH | ICMP or SNMP later | Current `aiops_device_up` is based on collector success. |
| Hostname / identity | CLI | SNMP sysName later | CLI gives consistent naming with inventory. |
| Running config snapshot | CLI | None | Must stay CLI/API; SNMP/telemetry are not suitable. |
| Software version / model / serial | CLI | SNMP ENTITY-MIB later | CLI/TextFSM is reliable for current lab. |
| Power / module / hardware state | CLI | SNMP ENTITY-SENSOR-MIB later | Keep CLI until platform SNMP OIDs are validated. |
| CPU | SNMP | CLI | Use SNMP when device MIB support is available; current fallback is `show processes top once`. |
| Memory | SNMP | CLI | Use SNMP when memory OIDs are validated; current fallback is CLI parsing. |
| Uptime | SNMP | CLI | SNMP `sysUpTime` or HOST-RESOURCES-MIB is preferred. |
| Interface admin/oper state | CLI or SNMP | CLI | CLI names are cleaner for dashboard labels; SNMP if ifName mapping is implemented. |
| Interface traffic | Telemetry | SNMP or CLI | Telemetry is best for high-frequency traffic; CLI is lowest preference. |
| Interface errors/discards | SNMP | CLI | SNMP IF-MIB counters are efficient; current fallback is `show interfaces`. |
| BGP state/routes | Telemetry | CLI | Use telemetry where supported; current fallback parses `show bgp summary`. |
| OSPF state | CLI | Telemetry later | Keep CLI until telemetry model is defined. |
| VXLAN/VTEP state | CLI | Telemetry later | Keep CLI until telemetry model is defined. |
| Route summary | CLI | Telemetry later | CLI output is compact and easy to parse. |
| MAC / ARP / LLDP tools | CLI | SNMP later | CLI gives exact operational tables for lookup/path tools. |

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
| CPU and memory fallback | `show processes top once` |
| Interface state | `show ip interface brief` |
| Interface traffic/errors/discards fallback | `show interfaces` |
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

SNMP is the recommended source for data that is cheap and standardized enough to poll periodically:

- CPU utilization.
- Memory utilization.
- Device uptime.
- Interface traffic counters when telemetry is not available.
- Interface errors and discards.

```yaml
snmp:
  enabled: false
  version: "2c"
  port: 161
  community_env: AIOPS_SNMP_COMMUNITY

inventory:
  defaults:
    use_snmp: true
    snmp_community_env: AIOPS_SNMP_COMMUNITY
```

Implementation note: the current Python collector has the config fields but does not yet poll SNMP. The next implementation step is to add an SNMP collector that exports the same metric names used by the dashboard, for example `aiops_device_cpu_utilization_percent`, `aiops_device_memory_utilization_percent`, and interface counter metrics.

## Telemetry

Telemetry is handled by Telegraf, not by the Python CLI collector.

For Arista EOS in this lab, use gNMI from Telegraf to the devices. The devices listen on TCP `6030` after this running configuration is applied:

```text
management api gnmi
   transport grpc default
      vrf MGMT
      authentication username priority metadata x509-spiffe x509-common-name
      no accounting requests
      no authorization requests
```

The config above is runtime configuration unless it is explicitly saved on the device.

Telegraf gNMI is enabled through `.env`:

```bash
TELEGRAF_ENABLE_GNMI=true
TELEGRAF_GNMI_ADDRESSES=172.16.1.101:6030,172.16.1.102:6030
```

Current Arista gNMI subscriptions:

| Data | gNMI Path |
|---|---|
| Interface counters / traffic / errors / discards | `/interfaces/interface/state/counters` |
| BGP neighbor state and message counters | `/network-instances/network-instance/protocols/protocol/bgp/neighbors/neighbor/state` |

Current Cisco MDT input remains available:

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

Recommended telemetry use:

- Interface traffic rates.
- Interface counter streams where available.
- BGP neighbor state and route counters where available.
- High-frequency state changes that should not wait for CLI polling.

CLI remains the fallback for BGP until telemetry subscriptions and field names are normalized into dashboard queries.

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
