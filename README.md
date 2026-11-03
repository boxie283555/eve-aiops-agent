# Network AIOps Agent

This project deploys a lightweight monitoring agent for EVE and network lab environments.

It provides:

- Device configuration snapshots.
- Interface state, traffic, errors, and discards.
- BGP, OSPF, VXLAN, underlay, and overlay state checks.
- Device hardware, software version, uptime, CPU, and memory metrics.
- ASIC/TCAM resource view with telemetry-first dashboard queries and CLI fallback metrics.
- Prometheus metrics for Grafana dashboards.
- A simple MRTG-style line-chart page at `/mrtg`, using telemetry-first interface traffic with 10-second refresh.
- MAC/IP lookup and end-to-end path tools at `/tools`.
- Daily comparison against the previous snapshot.
- Optional SMTP email notification for important changes.
- Optional telemetry ingestion through Telegraf.

## Single Project Config

For a new project, copy and edit only one file:

```bash
cp config/project.example.yml config/project.yml
chmod 600 config/project.yml
vi config/project.yml
```

`config/project.yml` contains:

- Monitor host address and optional lab URL.
- Optional device management route and gateway.
- Service ports.
- Grafana admin password.
- Device SSH credentials.
- SMTP settings.
- Collector schedule.
- Device inventory.

`config/project.yml` is ignored by Git so real passwords are not committed.

The older `.env`, `config/aiops.yml`, and `inventory/devices.yml` files are still supported for backward compatibility. Environment variables override values from `config/project.yml`.

### Flow analysis and PA policy/NAT

The tools page supports an explicit Flow analysis mode with protocol/ports,
per-hop packet state, PA security-rule decisions and deterministic NAT
translation. PA policy collection runs separately from metrics collection;
node details offer read-only native rule and session verification for lab
firewalls. See [scope, API parameters and limitations](docs/pa-flow-analysis.md).

### Nautobot inventory (Q1)

The collector can use Nautobot as its device source of truth. Configure `NAUTOBOT_URL`, `NAUTOBOT_TOKEN`, and set `inventory.source: nautobot`, legacy `nautobot.inventory_source: nautobot`, or `AIOPS_INVENTORY_SOURCE=nautobot` (highest precedence). Nautobot devices must have a name, location, role, platform, and either a primary management IP or the `management_ip` custom field. The adapter reads connection settings from device custom fields and falls back to the global Agent credentials. Interface status, collection time, and collection errors are synchronized back to Nautobot asynchronously after each collection cycle.

`GET /api/inventory/status` reports the active source, inventory generation, refresh status and last successful load. Nautobot changes are hot-loaded every 60 seconds (configurable via `AIOPS_INVENTORY_REFRESH_INTERVAL`, minimum 30), without restarting Agent. Failed or incomplete refreshes retain the last valid inventory and never switch to YAML. Startup fallback remains explicitly controlled by `nautobot.fallback_to_yaml` (default `false`); missing Nautobot URL/token is a configuration error. See [device lifecycle and hot reload](docs/device-lifecycle.md) for status-to-collection/health rules and Nautobot controls.

See the short [normalized model v1 design](docs/normalized-model-design-v1.md) for the distinction between `DeviceResult`, the versioned topology model, and flow state. The passive [network health score](docs/network-health-score.md) is available at `/health` and `/api/health/score`; Grafana operations and bilingual reports show scores alongside evidence coverage and deductions.

The Agent builds a versioned, evidence-backed topology snapshot after every collection cycle at `/app/data/topology.json`. Schema 1.1 adds stable device/interface/VRF IDs, interface hierarchy, scoped endpoint observations and RIB routes. `/tools/path` and `/api/path` share the same path engine; ambiguous or stale evidence is reported explicitly. The graph only displays confirmed physical links. See [the normalized model and path query guide](docs/normalized-model-v1.md) for API compatibility, saved layout migration, tests and query examples.

The optional Nautobot stack is defined in `docker-compose.nautobot.yml`. On the monitor host, create a separate `nautobot/.env` with database, secret-key, superuser, and API-token values, then start it with:

```bash
cd /opt/eve-aiops-agent/nautobot
docker compose --env-file .env up -d
```

The current deployment exposes Nautobot on port `8082`; the existing firewall policy UI uses port `8081`. After importing and validating the inventory, set `AIOPS_INVENTORY_SOURCE=nautobot` in the Agent environment and rebuild the Agent container.

Lab-only NAPALM setup and bounded asynchronous interface synchronization are documented in
[Nautobot NAPALM and sync](docs/nautobot-napalm-sync.md). Sync health is available at
`/api/nautobot/sync` and through `aiops_nautobot_sync_*` Prometheus metrics.

## Quick Start

On the monitoring host:

```bash
cd /opt/eve-aiops-agent
cp config/project.example.yml config/project.yml
chmod 600 config/project.yml
vi config/project.yml
./scripts/install_remote.sh
```

The install script reads `config/project.yml`, optionally installs a route, provisions Grafana, and starts the Docker services.

## Default URLs

Replace `<monitor-host>` with the `project.monitor_host` value from `config/project.yml`.

- Agent: `http://<monitor-host>:8080`
- Metrics: `http://<monitor-host>:8080/metrics`
- Tools UI: `http://<monitor-host>:8080/tools`
- MRTG line view: `http://<monitor-host>:8080/mrtg`
- Prometheus: `http://<monitor-host>:9090`
- Grafana: `http://<monitor-host>:3000`
- Telegraf metrics endpoint: `http://<monitor-host>:9273/metrics`

## Example Project Config

```yaml
project:
  name: network-aiops-agent
  monitor_host: 192.168.20.169
  lab_url: ""

route:
  enabled: false
  destination: ""
  gateway: ""

credentials:
  device:
    username: admin
    password: change-me
    secret: ""

inventory:
  defaults:
    platform: arista_eos
    port: 22
    use_ssh: true
  devices:
    - name: Site1-Spine
      role: spine
      site: site1
      host: 172.16.1.101
```

## Collection Timing

Default values in `config/project.example.yml`:

```yaml
collector:
  interval_seconds: 60
  command_timeout_seconds: 30
  snapshot_time: "02:00"
  compare_time: "02:20"
  max_parallel_devices: 4
```

- Agent collects every 60 seconds.
- Prometheus scrapes every 30 seconds.
- Grafana dashboards refresh every 30 seconds.
- Daily snapshots are compared with the previous day.

## Collection Sources

See [DATA_SOURCES.md](DATA_SOURCES.md) for the full mapping.

- CLI over SSH: active primary source for `aiops_*` metrics, including interface state/traffic/errors, BGP, OSPF, VXLAN, hardware, CPU, memory, route summary, MAC/ARP/LLDP, and config snapshots.
- SNMP: active preferred source for CPU, memory, uptime, and interface errors/discards.
- Telemetry: optional Telegraf gNMI for Arista EOS and Cisco MDT/gRPC receiver. Prometheus scrapes Telegraf on `9273`. ASIC/TCAM telemetry can be enabled with validated platform paths through `TELEGRAF_GNMI_TCAM_PATHS`.
- MRTG traffic view: reads Arista gNMI interface octet counters from Telegraf first, then falls back to Agent interface rates if telemetry is unavailable.

## Reachability

If the monitoring host can directly ping and SSH to devices, keep route injection disabled:

```yaml
route:
  enabled: false
```

If devices are reachable through a gateway or jump host, enable the route:

```yaml
route:
  enabled: true
  destination: 172.16.1.0/24
  gateway: 192.168.20.129
```

## Git Safety

Before publishing changes:

```bash
git status --short
git ls-files
```

Confirm these are not tracked:

- `config/project.yml`
- `.env`
- Real device passwords
- SMTP passwords
- Runtime data directories
