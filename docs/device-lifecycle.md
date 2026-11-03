# 设备生命周期与清单热加载

Nautobot 是设备清单和管理状态的真相源；设备实际是否可达仍由采集结果判断。意外掉线不会自动改成 Offline，也不会自动退出健康评分。

## 在哪里配置

打开 Nautobot（当前部署 `http://192.168.20.169:8082`）→ Devices → 选择设备 → Edit → Status。也可在设备列表批量编辑状态。

| Status | 采集 | 健康评分 |
| --- | --- | --- |
| Active / Failed | 继续 | 参与 |
| Maintenance | 继续 | 排除 |
| Offline | 停止 | 排除 |
| Decommissioning / Decommissioned / Retired | 停止 | 排除 |
| Inventory / Planned / Staged | 停止 | 排除 |
| 未识别的自定义状态 | 继续 | 参与 |

状态名称使用以上英文值，大小写不敏感。映射由 Agent 的 `aiops_agent/lifecycle.py` 统一实现，目前不是 Nautobot 中可编辑的规则表。

同一设备编辑页的 Custom Fields 提供以下覆盖控制：

| 字段标签（键） | 用途 |
| --- | --- |
| AIOps · Enable collection (`aiops_monitoring_enabled`) | 默认开启；关闭后停止采集且不参与评分。开启不能覆盖 Offline 等停止状态。 |
| AIOps · Exclude from health (`aiops_health_excluded`) | 默认关闭；开启后排除评分，仍可采集。 |
| AIOps · Exclusion reason (`aiops_exclusion_reason`) | 说明维护、停采或评分排除的原因。 |
| AIOps · Exclusion owner (`aiops_exclusion_owner`) | 登记人或变更单号；实际编辑账号由 Nautobot 变更历史记录。 |
| AIOps · Health exclusion expires (`aiops_exclusion_until`) | 可选，带时区的 ISO8601，例如 `2026-12-01T18:00:00+08:00`。只控制人工评分排除的有效期；不会恢复生命周期状态或采集开关。 |

人工排除到期后按当前状态恢复评分，字段原值保留以供追溯。期限格式无效时人工排除不生效，监控页面显示提示；Maintenance 等生命周期规则仍然生效。

## 何时生效

- Agent 默认每 60 秒获取完整 Nautobot 清单；通过环境变量 `AIOPS_INVENTORY_REFRESH_INTERVAL` 调整，最小 30 秒。修改该环境变量需重建 Agent 容器，日常修改设备无需重启。
- 新增设备进入下一次正常采集周期；管理地址、端口、平台、凭据引用或名称变化会清除旧实时结果，等待新采集。凭据仍引用现有环境变量，不在状态 API 暴露。
- 停采/删除后，后续采集不再启动。已经建立的会话可以完成，但过期结果不会重新发布。Prometheus 历史曲线、配置备份与漂移历史保留；实时指标会在下一次抓取后消失。
- 清单读取失败、分页不完整或身份重复时保留最后有效清单，并显示刷新失败。运行中不回退 YAML。成功返回的空清单视为确实移除了全部设备。
- 评分直接使用已应用清单；监控页面按其刷新周期更新。拓扑数据随正常采集周期更新，拓扑页面仍然手动刷新，保存的布局不变。
- 暂停状态同样约束现有 lab 配置备份、漂移检查和 PA 自动任务；原有非 lab 正常采集范围不变。

查看 `/api/inventory/status` 可确认来源、最后成功时间、刷新错误、清单代次及新增/删除/修改设备。`/api/devices` 和监控首页设备表会显示生命周期、是否采集、是否计分以及排除说明。健康总分的分母仅包含参与评分的设备；全部排除时显示无评分，不显示 100 分。

## 初始化与验证

在 Agent 环境执行 `scripts/nautobot/provision_lifecycle.py` 可幂等创建字段并使 Maintenance、Retired、Decommissioned 可用于设备。脚本先备份字段/状态定义，不修改任何设备的实际状态。

自动化测试使用合成设备覆盖状态矩阵、评分分母、热加载失败保留、增删改及并发旧结果抑制。线上验收只选择当前 Nautobot Site 恰好为 `lab` 的设备，临时 Maintenance 后恢复原状态；不修改设备运行配置。

## 监控展示范围

监控首页及中英文 Grafana 运维/汇报看板默认只显示 Active、Failed、Maintenance。
这是一项展示规则，独立于采集开关和评分排除；Maintenance 仍可见但不计分。
自定义未知状态默认隐藏，原有采集策略不因此改变。

监控首页勾选“显示非运行设备”，或在 Grafana 将同名选项设为“是”，可查看其他
状态设备及已有历史。设备选项、当前统计、漂移提示和趋势使用一致范围。
暂停设备显示“配置历史”链接；没有新采集样本时仍显示暂停/无数据，不伪造实时指标。
监控首页 URL/API 参数为 `include_inactive=true`，Grafana 变量为 `device_visibility=1`。

Grafana 使用 `aiops_device_inventory_info` 清单样本筛选，不依赖设备采集成功与否。
Failed 不会因采集失败而从设备选项消失；隐藏筛选在设备级查询聚合之前应用。
Grafana 清单选项随页面加载/时间范围刷新；历史固定时间范围按该结束时间的清单样本
解析。查阅早于此指标启用时间的历史时，可开启“显示非运行设备”，通过原有分类指标
获取历史设备选项。默认“到现在”的视图使用最新清单。

设备状态、Nautobot 资产、Prometheus 历史、配置备份、漂移记录和拓扑布局都不因展示
开关而修改。设备退出默认展示范围时，监控首页会将该设备的旧筛选自动恢复为全部可见设备。
