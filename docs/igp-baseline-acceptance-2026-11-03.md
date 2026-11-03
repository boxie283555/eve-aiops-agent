# lab IGP 基线核查与入库 · 2026-11-03

用户要求检查 IGP 并形成基线写入 Nautobot。只读核查限定为当前 Nautobot Location=lab 的 10 台设备，时间为北京时间 15:04。未对非 lab 设备执行诊断，也未修改网络设备配置。

| 范围 | 配置证据 | 基线 |
|---|---|---|
| p1-ext / p1-host / p1-leaf / p1-svc | 完整运行配置未发现 OSPF、OSPFv3、IS-IS、RIP 配置 | 不适用；无预期邻居 |
| p2-ext / p2-host / p2-leaf / p2-svc | 完整运行配置未发现 OSPF、OSPFv3、IS-IS、RIP 配置 | 不适用；无预期邻居 |
| PA-171 / PA-172 | default 中 OSPF/OSPFv3/RIP 配置不存在；VR-EW 中均明确 enable=no；PAN-OS 不支持 IS-IS | 不适用；无预期邻居 |

EOS 的运行查询没有进程或邻居输出；“未启用”的结论来自完整运行配置，未使用空输出单独判定。PA 通过运行配置 API 获取各虚拟路由器协议开关。

10 台设备的 `aiops_igp_baseline` 已写入并逐台读回验证，版本 1；BGP 基线逐台比较确认完全未变。字段保留观察时间、证据 SHA-256、授权来源及入库时间。说明与入口见 [IGP 基线](igp-baseline.md)。

58 项相关测试通过，含不完整配置、已启用协议、未知开关、不合法基线的保守处理，以及 BGP、健康评分、清单生命周期、监控范围的回归验证。中英文健康详情页面验证通过，无 JavaScript 错误。拓扑布局文件 SHA-256 与部署前一致。

完整采集周期验证通过：10 台 lab 设备采集成功；10 台 OSPF 检查均为 `igp_baseline_not_applicable`，该项 Unassessed 为 0。原 6 台 BGP 基线的 20 条邻居仍全部 Established，4 台 BGP 不适用状态保留。清单仍为 Nautobot 的 18 台资产，其中 10 台启用采集和评分。

部署备份：`/opt/eve-aiops-backups/igp-baseline-20261103-070658`。
导入前字段备份：Agent 持久卷 `/app/data/igp-baseline-before-20261103-070659.json`。
核查证据：Agent 持久卷 `/app/data/igp-baseline-evidence-20261103.jsonl`。

本次基线为 IGP 适用性：排除已确认未启用的 OSPF 评分项，OSPFv3/IS-IS/RIP 暂不新增评分项。未启用 IGP，无邻居可建立预期邻接清单；今后启用时需更新基线并补齐相应协议、VRF、Area、接口及邻居采集。
