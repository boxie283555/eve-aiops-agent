# 运维视图的防火墙指标

原有 [AI Networking OPS](http://192.168.20.169:3000/d/eve-aiops-overview/ai-networking-ops)
在 **Device Health** 下方新增 **Firewall / 防火墙** 分组，接口仍位于末尾。

- Active Sessions、Session Setup Rate、Throughput：各台防火墙的最新值。
- 三项对应趋势，以及 CPU / 内存趋势。
- Firewall Collection Status：各台防火墙的可达状态与最近采集结果。

按现有 `Device` 下拉筛选，选择 All 查看所有提供防火墙指标的设备，或选择 PA-171 / PA-172。
`Interface` 筛选不作用于这些设备级指标。数据来自原有 Agent / Prometheus，未增加采集任务。

查询根据已有防火墙专用指标识别设备，不依赖设备名称必须以 PA 开头。
最新业务指标仅采用最近采集成功且 Agent 指标端点可访问的设备；抓取或设备采集失败时留空，
状态表仍显示最近采集结果，避免旧会话值被当作当前数据。端点可访问本身不能证明内部采集
一直在推进，也无法保证设备的每一项可选指标都在最近一轮更新。

0 表示采集器上报的零值，不等于缺失；暂无指标显示 No data。选择不提供防火墙指标的设备时，
此分组无数据是预期行为。会话与吞吐按设备分别展示，不相加推断唯一业务流量。
PAN-OS CPU / 内存反映管理平面，不代表数据平面资源。

只更新 `grafana/dashboards/aiops-overview.json` 及运行目录对应 JSON；Grafana 的
provisioner 约 30 秒自动加载，不需重启 Grafana、Nautobot 或 Agent。
