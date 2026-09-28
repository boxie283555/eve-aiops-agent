# EVE AIOps Agent 项目交付说明

这个项目可以作为通用 AIOps Agent 模板复用到其它 EVE/网络实验环境。新项目通常只需要更新设备清单和运行时凭据，不需要改采集代码、Prometheus、Grafana Dashboard 或 Tools UI。

## 组件

- `agent/`: Python FastAPI 采集 Agent，负责 SSH 登录设备、执行 show 命令、解析状态并输出 Prometheus metrics。
- `inventory/devices.yml`: 设备清单。新项目主要改这个文件。
- `config/aiops.yml`: 采集周期、超时时间、快照和告警策略。
- `prometheus/prometheus.yml`: Prometheus 抓取配置。
- `grafana/`: Grafana datasource 和 dashboard provisioning。
- `telegraf/`: Telemetry 接收配置。
- `scripts/install_remote.sh`: 在监控主机上安装路由、Grafana、Docker 服务并启动 Agent。
- `.env.example`: 环境变量模板。真实 `.env` 不要提交到 Git。

## 新项目需要修改的文件

### 1. 设备清单

编辑 `inventory/devices.yml`：

```yaml
defaults:
  platform: arista_eos
  port: 22
  use_ssh: true
  use_snmp: false

devices:
  - name: Site1-Spine
    role: spine
    site: site1
    host: 172.16.1.101
  - name: Site1-CLF
    role: clf
    site: site1
    host: 172.16.1.102
```

字段说明：

- `name`: Dashboard 上显示的设备名。
- `role`: 设备角色，例如 `spine`、`clf`、`blf`。
- `site`: 站点名，例如 `site1`、`site2`。
- `host`: 设备管理 IP。
- `platform`: 默认是 `arista_eos`，如需支持其它平台，需要确认 `collector.py` 中有对应命令集。

### 2. 运行时凭据

复制 `.env.example` 为 `.env`，然后填写真实账号密码：

```bash
cp .env.example .env
chmod 600 .env
vi .env
```

常用变量：

```bash
AIOPS_DEVICE_USERNAME=admin
AIOPS_DEVICE_PASSWORD=change-me
AIOPS_DEVICE_SECRET=

SMTP_HOST=
SMTP_PORT=25
SMTP_FROM=aiops-agent@example.local
SMTP_TO=
```

注意：`.env` 已在 `.gitignore` 中排除，不能提交到 Git。

## 部署

在监控主机上：

```bash
cd /opt/eve-aiops-agent
docker compose up -d --build agent prometheus telegraf
```

如果设备网段需要通过 jump host 访问，添加路由，例如：

```bash
sudo ip route replace 172.16.1.0/24 via 192.168.20.129
```

Grafana 使用本机 systemd 服务时，同步 provisioning：

```bash
sudo rsync -a grafana/provisioning/ /etc/grafana/provisioning/
sudo mkdir -p /var/lib/grafana/dashboards
sudo rsync -a grafana/dashboards/ /var/lib/grafana/dashboards/
sudo chown -R grafana:grafana /var/lib/grafana/dashboards /etc/grafana/provisioning
sudo systemctl restart grafana-server
```

## 默认访问地址

- Agent: `http://<monitor-host>:8080`
- Metrics: `http://<monitor-host>:8080/metrics`
- Tools UI: `http://<monitor-host>:8080/tools`
- MRTG Line View: `http://<monitor-host>:8080/mrtg`
- Prometheus: `http://<monitor-host>:9090`
- Grafana: `http://<monitor-host>:3000`

## 采集周期

默认配置在 `config/aiops.yml`：

```yaml
collector:
  interval_seconds: 60
  command_timeout_seconds: 30
  snapshot_time: "02:00"
  compare_time: "02:20"
  max_parallel_devices: 4
```

- Agent 每 60 秒采集一次设备。
- Prometheus 每 30 秒抓取一次 Agent metrics。
- Grafana Dashboard 默认 30 秒刷新。
- 每天生成设备状态快照并与前一天对比。

## GitHub 发布建议

不要把密码写进 Git remote URL。GitHub 现在推荐使用 SSH key 或 Personal Access Token。

推荐 remote：

```bash
git remote add origin https://github.com/boxie283555/eve-aiops-agent.git
git push -u origin main
```

如果使用 HTTPS，push 时请使用 GitHub Personal Access Token 作为密码。不要使用普通账号密码。

## 交付前检查

```bash
git status --short
git ls-files
```

确认没有以下文件：

- `.env`
- 真实设备密码
- SMTP 密码
- 运行时数据目录
- Prometheus/Grafana 本地数据目录

