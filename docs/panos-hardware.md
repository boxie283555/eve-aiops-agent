# PAN-OS 软件版本与设备型号

PA 使用完整的 `show system info`，由 `parse_panos_hardware()` 解析明确的
`sw-version`、`model`、`serial`、`architecture`、`hw-version` 字段。
不再复用 EOS 的 `Software image version:` 规则，也不再把输出第一行当作型号。
缺少字段保持 `unknown`，例如 `family: vm` 不会被冒充为 CPU 架构。

发布 PAN-OS 硬件信息指标时，替换同一设备的旧标签组合；修正解析或升级软件后，
不会在当前 `aiops_device_hardware_info` 中同时保留旧版本和新版本而重复计数。
采集失败则保留最后一次硬件观察，并由采集状态指标标示失败。历史 Prometheus 样本保留。

Grafana 原运维视图和双语汇报视图使用相同指标，无需修改面板或重启 Grafana。
PAN-OS 资源解析复用同一轮 `show system info` 的 `uptime` 字段，将
`12 days, 01:02:03` 转为完整秒数，发布到 `aiops_device_uptime_seconds`。
Device Uptime 和双语汇报视图直接使用该指标，无需额外 SSH 命令。
零秒是有效的重启后观察；缺失、失败或格式无效时不伪造为零。

测试使用合成 PAN-OS / EOS 输出，包括空字段、命令失败、热修复版本号、平台分流、
重复发布和旧指标清理，不连接网络设备。线上仅检查 lab PA 的正常采集结果。

```sh
PYTHONPATH=agent python -m unittest discover -s agent/tests -v
```
