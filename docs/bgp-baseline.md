# BGP 基线与健康评分

Nautobot → Devices → 设备详情 / Edit → Custom Fields → **AIOps · Approved BGP baseline**（`aiops_bgp_baseline`）。JSON 存储设备 UUID、版本、协议预期、必需邻居、确认人、确认时间和候选清单 SHA-256。Agent 按现有 60 秒清单热加载读取；定时采集和接口同步不会自动更新基线。

- `intent=enabled`：按设备 UUID、VRF、邻居 IP、地址族、对端 AS 比较必需邻居；成功完整采集后邻居缺失、未建立或对端 AS 不符会扣 BGP 分。
- `intent=not_applicable`：用户明确确认该设备不运行 BGP，排除该项并重新分配权重。
- 缺少字段（null）：沿用旧观察评分，空邻居表不证明协议不适用。
- 无效基线、无必需邻居的 enabled 基线、采集失败或过期：计 Unassessed，不把证据缺失当作邻居故障。
- 邻居路由条数、运行时间只作观察证据，不固定为健康阈值；新增的非基线邻居不自动变成必需邻居。

lab 的 EOS 定时采集使用 `show ip bgp summary vrf all` 和 `show bgp evpn summary`，保留 IPv4 所有 VRF 与 EVPN。两个表都成功完整解析后，`dataset_status.bgp_baseline_complete=true` 才允许判断基线邻居缺失。任一表失败或格式不认识时，该 BGP 评分保守标记缺证，不误报故障。其他站点现有采集命令不变。启用基线的范围目前为 lab EOS；PA 本次仅确认 BGP 不适用，尚未实现 PA 启用 BGP 时的完整状态采集。

健康详情 `/health?lang=zh&site=lab` 展示基线检查原因、VRF、地址族及每个必需邻居状态。监控首页和 Prometheus 使用相同评分函数。OSPF 尚未接入预期邻居基线。

首次导入使用 `scripts/nautobot/import_bgp_baseline.py`：默认预览，明确人工确认后才使用 `--apply --approved-by ...`。脚本再次核对当前 lab 范围，先备份原字段，再批量写入并逐台读回校验。相同候选重跑不会更改确认时间；已有基线内容不同时拒绝覆盖。修改基线须人工审核，在 Nautobot 中更新内容、递增 `revision` 并填写新确认信息，Nautobot 对象变更历史记录修改。导入脚本不执行网络设备命令。
