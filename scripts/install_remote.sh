#!/usr/bin/env bash
set -euo pipefail

cd /opt/eve-aiops-agent

if [ ! -f config/project.yml ] && [ -f config/project.example.yml ]; then
  cp config/project.example.yml config/project.yml
  chmod 600 config/project.yml
fi

if ! python3 -c 'import yaml' >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y python3-yaml
fi

project_value() {
  local key="$1"
  local default_value="${2:-}"
  python3 - "$key" "$default_value" <<'PY'
import sys
from pathlib import Path

import yaml

key = sys.argv[1]
default = sys.argv[2]
path = Path("config/project.yml")
if not path.exists():
    print(default)
    raise SystemExit

data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
value = data
for part in key.split("."):
    if not isinstance(value, dict) or part not in value:
        print(default)
        raise SystemExit
    value = value[part]

if isinstance(value, bool):
    print("true" if value else "false")
elif value is None:
    print(default)
else:
    print(value)
PY
}

route_enabled="$(project_value route.enabled true)"
route_destination="$(project_value route.destination 172.16.1.0/24)"
route_gateway="$(project_value route.gateway 192.168.20.129)"

if [ "$route_enabled" = "true" ]; then
  sudo ip route replace "$route_destination" via "$route_gateway"
fi
sudo tee /etc/systemd/system/eve-aiops-route.service >/dev/null <<'EOF'
[Unit]
Description=Route to EVE device management network
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
EnvironmentFile=/etc/eve-aiops-route.env
ExecStart=/usr/sbin/ip route replace ${ROUTE_DESTINATION} via ${ROUTE_GATEWAY}
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
sudo tee /etc/eve-aiops-route.env >/dev/null <<EOF
ROUTE_DESTINATION=$route_destination
ROUTE_GATEWAY=$route_gateway
EOF
sudo systemctl daemon-reload
if [ "$route_enabled" = "true" ]; then
  sudo systemctl enable --now eve-aiops-route.service
else
  sudo systemctl disable --now eve-aiops-route.service || true
fi

if ! command -v docker >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y ca-certificates curl docker.io docker-compose-v2
  sudo systemctl enable --now docker
fi

if ! groups "$USER" | grep -q '\bdocker\b'; then
  sudo usermod -aG docker "$USER" || true
fi

if [ ! -f .env ]; then
  cp .env.example .env
  chmod 600 .env
fi

if ! command -v grafana-server >/dev/null 2>&1; then
  tmp_deb=/tmp/grafana_11.3.0_amd64.deb
  curl -L --retry 3 --connect-timeout 10 \
    -o "$tmp_deb" \
    https://dl.grafana.com/oss/release/grafana_11.3.0_amd64.deb
  sudo apt-get install -y "$tmp_deb"
fi

sudo rsync -a /opt/eve-aiops-agent/grafana/provisioning/ /etc/grafana/provisioning/
sudo mkdir -p /var/lib/grafana/dashboards
sudo rsync -a /opt/eve-aiops-agent/grafana/dashboards/ /var/lib/grafana/dashboards/
sudo chown -R grafana:grafana /var/lib/grafana /etc/grafana/provisioning
sudo systemctl enable --now grafana-server
grafana_admin_password="${GRAFANA_ADMIN_PASSWORD:-$(project_value grafana.admin_password admin)}"
sudo grafana-cli admin reset-admin-password "$grafana_admin_password"
sudo systemctl restart grafana-server

sudo docker compose up -d --build agent prometheus telegraf
