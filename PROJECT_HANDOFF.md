# EVE AIOps Agent 项目交付说明

这个项目可以作为通用 AIOps Agent 模板复用到其它 EVE/网络实验环境。新项目只需要复制并修改一个文件：`config/project.yml`。采集代码、Prometheus、Grafana Dashboard、MRTG 页面和 Tools UI 都不需要改。

## 组件

- `agent/`: Python FastAPI 采集 Agent，负责 SSH 登录设备、执行 show 命令、解析状态并输出 Prometheus metrics。
- `config/project.example.yml`: 单配置文件模板。
- `config/project.yml`: 新项目唯一需要修改的配置文件，包含项目地址、路由、账号、SMTP、采集周期和设备清单。该文件不会提交到 Git。
- `config/aiops.yml`: 旧版采集配置，保留用于兼容。
- `inventory/devices.yml`: 旧版设备清单，保留用于兼容。
- `prometheus/prometheus.yml`: Prometheus 抓取配置。
- `grafana/`: Grafana datasource 和 dashboard provisioning。
- `telegraf/`: Telemetry 接收配置。
- `scripts/install_remote.sh`: 从 `config/project.yml` 读取路由和 Grafana 密码，安装并启动服务。

## 新项目只改一个文件

复制模板：

```bash
cp config/project.example.yml config/project.yml
chmod 600 config/project.yml
```

然后只编辑：

```bash
vi config/project.yml
```

常用配置：

```yaml
project:
  name: eve-aiops-agent
  monitor_host: 192.168.20.169
  eve_url: http://192.168.20.185/legacy/

route:
  enabled: true
  destination: 172.16.1.0/24
  gateway: 192.168.20.129

credentials:
  device:
    username: admin
    password: change-me
    secret: ""
  smtp:
    host: ""
    port: 25
    to: []

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

字段说明：

- `project.monitor_host`: 监控主机 IP，用于访问 Agent、Prometheus 和 Grafana。
- `route.destination`: 设备管理网段。
- `route.gateway`: 到设备管理网段的下一跳或 jump host。
- `credentials.device`: 设备 SSH 登录信息。
- `credentials.smtp`: 邮件告警配置。
- `collector`: 采集周期、命令超时、每日快照和对比时间。
- `inventory.devices[].name`: Dashboard 上显示的设备名。
- `inventory.devices[].role`: 设备角色，例如 `spine`、`clf`、`blf`。
- `inventory.devices[].site`: 站点名，例如 `site1`、`site2`。
- `inventory.devices[].host`: 设备管理 IP。

注意：`config/project.yml` 已在 `.gitignore` 中排除，不能提交到 Git。

## 部署

在监控主机上：

```bash
cd /opt/eve-aiops-agent
cp config/project.example.yml config/project.yml
vi config/project.yml
./scripts/install_remote.sh
```

`install_remote.sh` 会做这些事：

- 读取 `route.destination` 和 `route.gateway` 并写入系统路由。
- 安装 Docker 和 Grafana。
- 同步 Grafana datasource 和 dashboard。
- 使用 `grafana.admin_password` 设置 Grafana admin 密码。
- 启动 `agent`、`prometheus`、`telegraf`。

## 默认访问地址

- Agent: `http://<monitor-host>:8080`
- Metrics: `http://<monitor-host>:8080/metrics`
- Tools UI: `http://<monitor-host>:8080/tools`
- MRTG Line View: `http://<monitor-host>:8080/mrtg`
- Prometheus: `http://<monitor-host>:9090`
- Grafana: `http://<monitor-host>:3000`

## 兼容模式

如果没有 `config/project.yml`，Agent 会继续读取旧文件：

- `config/aiops.yml`
- `inventory/devices.yml`
- `.env`

环境变量优先级最高，可以覆盖 `config/project.yml` 里的账号和 SMTP 配置。

## GitHub 发布建议

不要把密码写进 Git remote URL。GitHub 推荐使用 SSH key 或 Personal Access Token。

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

- `config/project.yml`
- `.env`
- 真实设备密码
- SMTP 密码
- 运行时数据目录
- Prometheus/Grafana 本地数据目录

