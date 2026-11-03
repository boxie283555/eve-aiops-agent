# lab BGP 基线验收 · 2026-11-03

用户确认了 20 条必需邻居，以及 p1-host、p2-host、PA-171、PA-172 的 BGP 不适用状态。本次只使用 lab 设备的证据，非 lab 的采集命令和设备配置未修改。

Nautobot 已逐台读回校验 10 台设备的 `aiops_bgp_baseline` JSON，版本 1，审批来源为当前会话用户确认。Agent 清单源保持 Nautobot，60 秒热加载加载基线，无 YAML 回退。基线配置入口及规则见 [BGP 基线说明](bgp-baseline.md)。

| 设备 | 必需邻居记录 | 验证结果 |
|---|---:|---|
| p1-ext / p2-ext | 各 2 | Established；Internet、VAS |
| p1-leaf / p2-leaf | 各 2 | Established；default IPv4 与 EVPN |
| p1-svc / p2-svc | 各 6 | Established；default 与 FUSION |
| p1-host / p2-host | 0 | 已确认不适用，BGP 项排除评分 |
| PA-171 / PA-172 | 0 | 已确认不适用，BGP 项排除评分 |

线上验证：10 台 lab 均采集成功；6 台 BGP 基线检查各得 100 分，另外 4 台 BGP 检查明确排除。Prometheus 中 lab BGP Up 记录合计 20，其中非 default VRF 合计 8。监控首页 API 正常，中英文健康详情页面无 JavaScript 错误。拓扑布局文件 SHA-256 与部署前一致。

52 项相关测试通过，覆盖多 VRF 重复 IP、缺失邻居、Down、对端 AS 不符、采集失败/过期、非法基线、未配置基线的兼容行为、生命周期与监控范围。异常情况使用合成数据验证，未对实际设备执行断邻居、关接口等实验。

部署备份：`/opt/eve-aiops-backups/bgp-baseline-20261103-065148`。
Nautobot 导入前字段备份：Agent 持久卷内 `/app/data/bgp-baseline-before-20261103-065232.json`。

限制：本轮为 lab EOS IPv4 / EVPN 的 BGP 基线；PA 的 BGP 运行状态采集、OSPF 基线和 IPv6 邻居基线未在本轮扩展。未启用基线的协议仍采用原有观察评分。
