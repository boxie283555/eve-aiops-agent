# Grafana 语言切换与配置漂移指标

运维视图和汇报视图顶部均有 **语言 / Language** 下拉入口，支持中文和英文；切换时保留时间范围及各视图支持的筛选变量。运维视图翻译标题、字段、状态文字和说明；指标查询、设备名称、接口名称及资产分类原始值不改写。

- 中文运维：`/d/eve-aiops-overview/ai-networking-ops`（原 UID 保留）
- 英文运维：`/d/eve-aiops-overview-en/ai-networking-ops`
- 中文汇报：`/d/eve-aiops-report/report`
- 英文汇报：`/d/eve-aiops-report-en/report`

Q3 卡片含悬浮说明，下面的“指标说明与统计范围”展示共同口径：

| 卡片 | 含义 | 单位 |
| --- | --- | --- |
| 未处理无主漂移 | 真实观察产生、没有匹配预登记变更、尚未登记处理结果的记录 | 条 |
| 待处理演练漂移 | 模拟演练产生、没有匹配预登记变更、尚未登记处理结果的记录 | 条 |
| 演练漂移历史记录 | 全部无主演练记录，包含未处理、已处理和回滚记录 | 条 |
| 最近备份失败设备 | 每台设备最近一次备份尝试失败；不是累计失败次数 | 台 |

三次演练修改加三次回滚共六条历史记录；关闭记录不会删除历史。无主表示没有匹配的预登记变更，不直接证明现场变更未经批准。配置未变化的成功备份仍算成功。没有尝试备份的设备不纳入备份失败数。

卡片值对应所选时间终点的状态；历史记录数是当时保留的累计记录，不是所选时间窗口内的新增次数。当前覆盖已尝试备份的 lab 设备，默认间隔 15 分钟。采集或存储不可用时显示缺数据，不用零代替未知。

## 原始 Prometheus 指标

- `aiops_config_drift_records{device,origin,status,unowned}`：持久化漂移记录数。`origin=observed|exercise` 区分真实观察和演练，`status=open|resolved` 区分未处理和已处理，`unowned=true|false` 表示是否未关联预登记变更。这是 Gauge，不是持续递增的事件 Counter。
- `aiops_config_backup_success{device}`：最近一次备份尝试成功为 1、失败为 0。失败设备卡片汇总 `1-success`。
- `aiops_config_backup_last_attempt_timestamp_seconds{device}`：最近一次备份尝试的 Unix 时间戳，不是备份耗时，也不是最近成功时间。
- `aiops_config_history_available`：持久化历史数据库当前可读为 1，否则 0；用于阻止数据库不可用时展示虚假的正常零值。

## 维护

保持原运维 JSON 作为面板配置来源，英文 JSON 自动生成。修改面板后运行：

```sh
python3 scripts/grafana/build_overview_languages.py
python3 scripts/grafana/build_report.py
python3 scripts/grafana/build_overview_languages.py --check
python3 scripts/grafana/build_report.py --check
```

Q3 卡片及说明由 `scripts/grafana/add_config_drift.py` 共用生成。部署四份 JSON 到 Grafana provisioning 目录即可热更新，不需要重启 Agent。Grafana 自身的侧边栏、个人设置等外壳菜单语言由用户的 Grafana 个人偏好控制；这里的切换针对项目仪表盘内容。
