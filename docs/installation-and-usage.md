# 安装与使用

本文说明 Network AIOps Agent 的部署、配置和主要功能入口。首次部署脚本针对 Debian/Ubuntu 类监控主机，使用 Docker Compose。

## 部署要求

- 一台可访问网络设备管理网和 Nautobot 的 Linux 监控主机。
- Docker Engine 与 Docker Compose v2；`scripts/install_remote.sh` 会在缺失时安装 Docker。
- 设备清单、Nautobot API token，以及按需提供的设备凭据和 Grafana 密码。
- 要启用 Excel 秘密凭据导入，Agent 环境还需要 `AIOPS_CREDENTIAL_VAULT_KEY`。

首次部署前，检查 [config/project.example.yml](../config/project.example.yml) 中的监控主机、路由和设备清单。示例路由默认开启；若监控主机已能直接访问管理网，应先关闭它，避免安装时添加不需要的路由。

## 首次安装

```bash
git clone https://github.com/boxie283555/eve-aiops-agent.git
cd eve-aiops-agent
cp config/project.example.yml config/project.yml
chmod 600 config/project.yml
vi config/project.yml
cp .env.example .env
chmod 600 .env
vi .env
./scripts/install_remote.sh
```

在配置中填写监控主机、设备清单、Grafana 管理员密码、Nautobot 连接参数和采集范围。`.env`、`config/project.yml` 与实际凭据文件不得提交到 Git。

Agent、Prometheus、Telegraf 由 Compose 启动；安装脚本另行安装并配置 Grafana。首次安装后检查容器状态和 Grafana 页面，再开始登记设备。

### Nautobot 清单

若使用 Nautobot 作为 inventory：

1. 部署并初始化 Nautobot，创建 API token、lab Location、设备类型、角色和平台。
2. 配置 Agent 的 Nautobot URL/token，并将 inventory source 设为 `nautobot`。
3. 运行项目提供的自定义字段初始化脚本，例如 `scripts/nautobot/provision_lifecycle.py`、`scripts/nautobot/provision_cli_transport.py` 和 `scripts/nautobot/provision_snmp_fields.py`。
4. 从 `/api/inventory/status` 查看清单来源和最近刷新状态。

Nautobot 中保存设备级设置和凭据引用。不要把登录密码、enable secret 或 SNMP community 写进普通 Nautobot 自定义字段。

## 更新已有部署

先备份配置和数据，再在项目目录执行：

```bash
git pull --ff-only
docker compose up -d --build --no-deps agent
```

只改 `.env` 中的环境变量时，可用 `docker compose up -d --no-deps --force-recreate agent` 让容器重新读取配置，无需构建镜像。其它服务不需要随 Agent 一起重启时，使用 `--no-deps`。

## 设备发现与 Excel 登记

向导入口为 `/discovery?lang=zh`。流程为：选择范围、审核候选设备、登记到 Nautobot、识别设备、核对物理连线、确认协议基线。主动探测只允许当前 Nautobot Location 为 `lab` 的设备；新设备登记后默认处于 Maintenance 且暂停定时采集，用户完成识别后再明确启用。

向导可下载 `.xlsx` 模板。每行对应一台设备，包含管理地址、名称、端口、协议、资产标签、用户名、密码、enable secret、SNMP 开关/community、SNMP 目标地址和端口。模板的完整列名和限制见 [Network discovery](network-discovery.md)。

含秘密的 Excel 上传需要经 SSH 本地转发访问 Agent：

```bash
ssh -N -L 18080:127.0.0.1:8080 user@<monitor-host>
```

然后在本机浏览器打开 `http://127.0.0.1:18080/discovery?lang=zh`，下载模板、填写并上传。旧版 `.xls` 请先另存为 `.xlsx`。普通网络直连的上传会被 Agent 拒绝。

Agent 为每个设备生成独立引用，将用户名/密码、enable secret 和 SNMP community 加密后存入持久化凭据库；Nautobot 只保存引用。新设备的凭据在用户确认登记时写入。定时采集按设备引用解密使用。加密密钥通过 `AIOPS_CREDENTIAL_VAULT_KEY` 提供，需与凭据库一起备份；丢失密钥会导致已保存凭据无法解密。上传表格本身也含明文秘密，应按组织规定安全保管并在导入后清理。

导入仅生成待审核候选，不会因上传文件自动扫描未知地址。需要设备属于 `lab` 并完成登记后，才能启动识别；启用定时采集是单独操作。

## 业务登记与影响分析

Network Pulse 侧栏的“工作空间 → 业务影响”打开 `/business`。登记业务名称、源/目的 IP、协议、端口、应用、入口接口和 VRF，保存后可生成当前路径基线报告；也可模拟 lab 设备离线、接口中断或移除路由，并以图形对比故障前后的路径和结论。

报告保留拓扑和策略证据时间。它是基于已采集状态的离线推演，不等于真实流量探测或应用 SLA。协议/端口等条件不足或证据缺失时，报告会标为不完整。详见 [Business flow impact analysis](business-impact-analysis.md)。

## 主要功能入口

| 功能 | 页面或接口 | 说明 |
| --- | --- | --- |
| Network Pulse | `/monitor` | 采集状态、设备健康评分、资源和流量趋势 |
| 设备发现 | `/discovery?lang=zh` | lab 设备登记、Excel 导入、设备识别、线缆核对和基线审核 |
| 业务影响 | `/business` | 业务登记、正常路径图、离线故障推演和历史报告 |
| 运维工具 | `/tools` | 路径查询、设备信息和流量分析 |
| 配置与漂移 | `/config/ui` | 配置备份、版本差异、漂移证据和回滚工作流 |
| 防火墙策略 | `/firewall` | 策略、NAT、命中和路径关联分析 |
| 健康评分 API | `/api/health/score` | 结构化评分及证据覆盖 |
| Prometheus metrics | `/metrics` | Agent 指标，供 Prometheus/Grafana 抓取 |

默认端口和更多来源说明见 [README](../README.md) 与 [DATA_SOURCES.md](../DATA_SOURCES.md)。
