# PAN-OS 接口流量

目前覆盖当前 Nautobot Site 为 `lab` 的 PAN-OS 设备（PA-171、PA-172）。每轮先核对 Nautobot 身份、管理地址与 Site；不扩展非 lab 主动诊断范围，现有全设备常规采集保持运行。

独立异步任务每 60 秒执行一次，最多两个并发设备，单个 HTTP 请求 15 秒超时、响应上限 16 MiB；任务不重入。复用设备凭据，通过 PAN-OS XML API 读取 `show system info` 和 `show counter interface all`。无需更改 PA 配置。此任务独立于常规 CLI、策略采集、Nautobot 接口同步和 Prometheus 请求。

- 物理/聚合口使用 `hw/entry/port/rx-bytes,tx-bytes`，标记 `layer=hardware`。
- 逻辑子接口使用 `ifnet/ifnet/entry/ibytes,obytes`，标记 `layer=logical`。
- 同名接口优先采用硬件计数，防止 ifnet 的 ae1 计数覆盖硬件 ae1。
- `bps = 8 × 字节增量 / 单调时钟采样间隔`，保留整数计数精度。首次需两个样本；重启（uptime 连续性）、计数回退、来源层变化、身份/地址变化、失败后的恢复或超过 180 秒的间隔均重新建立基线。
- 采集失败立即撤去速率；样本超过 180 秒未刷新，在导出时撤去速率。未知值不补零；真实零增量输出零。接口/设备从成功采样/有效范围移除后，不保留旧序列。
- 接口 RX/TX 按防火墙自身方向，物理口、聚合口和子接口可能观察同一流量，**不能相加作为总吞吐**。

## 指标和 API

`aiops_interface_rx_bps` / `aiops_interface_tx_bps` 保留原标签 `device,interface`，现在包含独立采样的 lab PA 速率。非 PA 的发布逻辑保持原样；PA 的常规 CLI 不重复发布同名速率。

专用指标 `aiops_pa_interface_rx_bps` / `aiops_pa_interface_tx_bps` 增加 `layer` 标签。`aiops_pa_interface_collect_success`、`aiops_pa_interface_rates_ready` 和 `aiops_pa_interface_last_sample_timestamp_seconds` 提供采集状态、速率就绪状态和采样时间。采集成功但未获得第二个样本时，success=1、ready=0。

`GET /api/pa/interface-traffic` 提供当前状态、样本时间、字节计数及速率；`GET /state` 对 lab PA 接口附加独立流量观察，不修改原常规采集结果。

Grafana 运维视图末尾的 **Palo Alto · 防火墙接口流量 / Firewall Interface Traffic** 展示 RX 和 TX，支持原有设备、接口筛选。中英文汇报视图末尾提供同类面板。独立流量面板不依赖常规 CLI 的整体成功状态，以免无关命令失败遮住有效流量数据。

## 验证

测试采用 lab PA 的真实 bulk counter XML 和合成增量，覆盖硬件/逻辑层选择、首次采样、速率和零流量、计数清零、重启、过期、范围/接口移除、失败恢复、大整数精度、通用指标兼容、API 状态覆盖及范围校验失败时不连接设备。
