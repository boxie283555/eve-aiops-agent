# Grafana 汇报视图

汇报视图：[网络运行简报](http://192.168.20.169:3000/d/eve-aiops-report/network-operations-report)

投屏模式：[隐藏 Grafana 导航](http://192.168.20.169:3000/d/eve-aiops-report/network-operations-report?kiosk)

原有运维视图 `eve-aiops-overview` 完整保留。新视图由
`grafana/dashboards/aiops-report.json` 独立配置，使用现有 Prometheus 数据源，
不改变设备采集、Nautobot 同步、拓扑及保存布局。

## 展示内容

- 在线设备、不可达设备、最近采集成功率，以及距上次资产同步成功的时间。
- 设备状态趋势与软件版本横向条形图（Grafana Bar gauge，按设备数量降序）。
  版本名称显示在每条柱上方，数量使用“台”，长名称不与相邻版本重叠。
- CPU / 内存使用率最高的五台设备，以及不可达优先的设备清单。
- 接口统计置于最后；已启用但未连通的接口标为待核查，不当作业务故障。
- 中文说明与通往完整运维视图、物理拓扑的链接。

右上角选择时间段，设备范围支持单选、多选和全部。默认最近 24 小时、
每分钟刷新。资产同步是全局指标，其余指标随设备筛选变化。

## 数据含义与边界

即时卡片反映所选时间终点的最近采集值，趋势覆盖所选时间范围；成功率
是设备最近一次采集的成功比例，不是业务 SLA 或时段可用率。

所有查询均以采集 Agent 的 Prometheus `up == 1` 为条件。
抓取失败时留空，不使用 `or vector(0)` 将缺失伪装为正常。
此条件只能证明指标端点可访问，不能证明内部采集循环仍在推进；
结合资产同步时效和原有运维视图判断。尚无同步成功记录时，时效卡片留空。

同步时效 5 分钟为橙色、15 分钟为红色；资源使用率 80% 为橙色、95% 为红色。
这些是展示参考线，并非新建告警策略。版本 `unknown` / 空值显示为“未识别”。

## 部署

将 JSON 放到服务器 `/opt/eve-aiops-agent/grafana/dashboards/` 与
`/var/lib/grafana/dashboards/`。现有 provisioner 每 30 秒读取，
无需重启 Grafana 或 Agent。现有安装脚本也会复制此目录内的新 dashboard。
回退时仅移除新 JSON，原有 dashboard 不受影响。
