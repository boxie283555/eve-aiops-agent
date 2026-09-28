# EVE AIOps Agent

This project deploys a lightweight monitoring agent for the EVE VXLAN lab.

It provides:

- Device configuration snapshots.
- Interface state and traffic metrics.
- BGP, OSPF, NVE underlay/overlay state checks.
- Prometheus metrics for Grafana dashboards.
- A simple MRTG-style interface page at `/mrtg`.
- Daily comparison against the previous snapshot.
- Optional SMTP email notification for important changes.
- Optional Cisco MDT telemetry ingestion through Telegraf.

## Target Hosts

- EVE: `http://192.168.20.185/legacy/`
- Monitoring host: `192.168.20.169`
- Lab management network: `172.16.1.0/24`
- Management gateway / jump host: `192.168.20.129` (`172.16.1.254` on the lab side)

The monitoring host reaches the device management subnet through
`192.168.20.129`.

## Quick Start On 192.168.20.169

```bash
cd /opt/eve-aiops-agent
cp .env.example .env
chmod 600 .env
vi .env
sudo ip route replace 172.16.1.0/24 via 192.168.20.129
docker compose up -d --build agent prometheus telegraf
```

URLs:

- Agent: `http://192.168.20.169:8080`
- MRTG-style interface view: `http://192.168.20.169:8080/mrtg`
- Prometheus: `http://192.168.20.169:9090`
- Grafana: `http://192.168.20.169:3000` runs as a native systemd service.
- Telegraf Cisco MDT Prometheus endpoint: `http://192.168.20.169:9273/metrics`

Default Grafana credentials for the native install are `admin` / `admin`.

## Credentials

Do not place device or SMTP credentials in git. Use `.env`:

```bash
AIOPS_DEVICE_USERNAME=admin
AIOPS_DEVICE_PASSWORD=change-me
SMTP_HOST=
SMTP_PORT=25
SMTP_FROM=aiops-agent@example.local
SMTP_TO=
```

## Live Collection Requirements

The agent needs this route on `192.168.20.169`:

```bash
sudo ip route replace 172.16.1.0/24 via 192.168.20.129
```

Once routing is fixed, verify:

```bash
ping 172.16.1.101
nc -vz 172.16.1.101 22
```

## Telemetry

Telegraf listens on TCP `57000` for Cisco MDT dial-out telemetry and exposes
converted metrics on `9273` for Prometheus. NX-OS devices still need telemetry
dial-out configuration before those panels populate.

## Verified Lab Inventory

The currently reachable devices are:

| Device | IP | Platform |
|---|---:|---|
| Site1-Spine | `172.16.1.101` | Arista EOS |
| Site1-CLF | `172.16.1.102` | Arista EOS |
| Site1-BLF | `172.16.1.103` | Arista EOS |
| Site2-Spine | `172.16.1.105` | Arista EOS |
| Site2-CLF | `172.16.1.106` | Arista EOS |
| Site2-BLF | `172.16.1.107` | Arista EOS |

Each device has an explicit management VRF route:

```text
ip route vrf MGMT 192.168.20.0/24 172.16.1.254
```
