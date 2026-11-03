# IGP 基线

入口：Nautobot → Devices → 设备详情 / Edit → Custom Fields → **AIOps · Approved IGP baseline**（`aiops_igp_baseline`）。字段包含 OSPF、OSPFv3、IS-IS、RIP 的适用性、证据来源、观察时间、版本、入库授权人和时间。

2026-11-03 对当前 Location=lab 的 10 台设备做只读核查：8 台 EOS 的完整运行配置中没有 IGP 配置；运行查询没有进程/邻居输出。两台 PA 的 default 虚拟路由器没有 OSPF/OSPFv3/RIP 配置，VR-EW 中三者均为 enable=no。PAN-OS 不支持 IS-IS。故保存为 IGP 不适用基线，不生成虚假的邻居记录。

只有完整配置或明确 enable=no 才作为未启用的证据；空邻居表、命令失败或解析失败都不用于自动确认不适用。发现已启用的 IGP 时，导入脚本拒绝此次“不适用”导入，需要另外核对 VRF、进程、Area、接口、邻居及允许的邻接状态。

当前评分有 OSPF 项：已确认不适用的设备排除该项并重新分配权重，不再贡献 OSPF 的 Unassessed。IS-IS、RIP、OSPFv3 目前没有独立评分项，保存其适用性不表示已经具备这些协议的持续邻居监控。若将 OSPF 基线改为 enabled 或 unspecified，系统保守显示需要完整邻接证据，不会直接认定健康；完整 IGP 邻接基线比对尚待启用协议时补齐。

Agent 通过现有 60 秒清单热加载读取此字段；定时采集不覆盖基线，BGP 基线独立保留。没有 IGP 基线的设备沿用原有观察评分，无效基线显示缺证。今后启用 IGP 时需要人工审核并更新版本，不能沿用“未启用”的历史预期。

`import_igp_baseline.py` 默认预览；用户要求入库后使用 `--apply --approved-by ...`。只接受最近两小时且当前仍处于 lab 的已核查证据；入库前备份所有原自定义字段，批量写入后逐台读回，并检查 BGP 基线未被修改。不会更改网络设备配置。
