#!/usr/bin/env bash
set -euo pipefail

cd /opt/eve-aiops-agent

sudo ip route replace 172.16.1.0/24 via 192.168.20.129
sudo tee /etc/systemd/system/eve-aiops-route.service >/dev/null <<'EOF'
[Unit]
Description=Route to EVE device management network
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/sbin/ip route replace 172.16.1.0/24 via 192.168.20.129
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now eve-aiops-route.service

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
sudo grafana-cli admin reset-admin-password "${GRAFANA_ADMIN_PASSWORD:-admin}"
sudo systemctl restart grafana-server

sudo docker compose up -d --build agent prometheus telegraf
