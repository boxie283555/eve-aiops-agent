# Q2 归一化模型 v1（schema_version 1.1）

先读一页版 [模型设计摘要](normalized-model-design-v1.md)。本文保留实现细节和历史验收记录，历史计数不代表当前在线状态。

## 身份与兼容性

- `device.id` 使用 Nautobot UUID；YAML 兼容模式使用名称派生 UUID。跨模式迁移时 UUID 会变，名称布局仍可读取。
- `interface.id` 使用设备 UUID + 规范接口名派生；`vrf.id` 使用设备 UUID + VRF 名派生。同名 VRF 不会跨设备合并。
- `Ethernet1`/`Et1`、`Port-Channel1`/`Po1` 等使用统一名称。`raw_name`、`aliases` 保留原始名称，旧设备名保存在设备 `aliases` 中。
- 设备改名且 Nautobot UUID 不变时，接口/VRF ID 保持不变。接口实际改名会产生新的接口 ID；不猜测两个不同接口是同一个端口。
- 原有 `/api/devices`、`/api/interfaces`、`/state`、`/api/topology`、`/tools/path`、`/api/path` 保留。拓扑仍含 `devices/interfaces/links/endpoints/layout`；新增 `vrfs/routes/schema_version/warnings`。
- 布局读取兼容旧名称键；保存时同时存 UUID 键。升级不要求重新保存布局。旧拓扑快照可读取，但其中没有证据的链路降为候选，等待采集重新确认。

## 接口与端点

接口区分 physical / aggregate / subinterface / virtual；`parent_id`、`aggregate_id`、`members`、`children` 表达层级。PA 的 `ethernet1/1 → ae1 → ae1.10` 和交换机 channel-group 都会归一化。

端点观察带有 `device_id/vrf_id/interface_id/vlan/scope_id`，区分 owned（接口自身）、neighbor（ARP）、learned（FDB）。ARP/FDB 只能在同设备、同 VLAN 下关联，保留全部附件端口。相同 IP/MAC 不会跨设备或 VRF 去重；冲突查询返回 `ambiguous` 和候选列表。

PA 未提供的管理状态保留 null；子接口继承父口链路状态时标记 `state_source=parent_link`。模型层级位于 Agent 归一化快照；不会根据 MAC 相等自动创建物理线缆。用户确认且经 lab 范围证据核实的连接可登记为 Nautobot cable。既有异步接口状态同步继续工作。

## 证据与时效

记录包含来源、数据集、`last_attempt_at`、`last_success_at`、`freshness`；链路还包含 `evidence` 与 `confirmation`。默认 TTL 600 秒，可通过 `AIOPS_TOPOLOGY_TTL_SECONDS` 调整。

- LLDP 指明双方物理端口，或 Nautobot 已登记 Active/Connected 的物理 cable，才属于 confirmed physical。
- 同一物理端口出现多个对端时标记 conflicting，退出已确认物理图。
- IP/MAC 关联只创建 candidate adjacency，不能证明直连线缆。两台 PA 的重复 MAC/IP 不再通过设备名称猜测连接。
- 命令失败时保留旧数据及原成功时间，立即标 stale；成功采集到空表则清除旧记录。重读快照时也检查 TTL。
- 拓扑页面只画 fresh + confirmed + physical；显示隐藏候选数量。候选及证据可从 `/api/topology` 和 `/api/topology/anomalies` 查看。管理接口不纳入数据平面物理路径。
- 页面仍由用户手动刷新；后台每轮采集更新快照，手动刷新保留当前布局位置。

## 路由与统一路径引擎

采集 EOS `show ip route vrf all | json`、PAN-OS `show routing route`、NX-OS `show ip route vrf all`，存储设备、VRF、前缀、下一跳、出接口、协议及可用的 metric/preference。当前覆盖 IPv4 RIB；不声称已采集 IPv6 FIB。

`/tools` 页面和 `/api/path`、`/tools/path` 由同一个处理器调用同一引擎。参数：

| 参数 | 含义 |
|---|---|
| src / dst | IP、MAC、设备名称或 UUID:接口 |
| src_device / dst_device | 设备名称、UUID；默认 all |
| src_vrf / dst_vrf | VRF 名称、UUID；默认 all |
| vrf | 旧参数；未指定两端 VRF 时作为两端筛选值 |
| mode | auto / physical / routed；默认 auto |

- auto 在目的为 IP 时查路由，否则查物理连接。
- physical 仅表示设备间存在已确认连接，不证明某个流量实际走这条路。
- routed 在每跳的设备/VRF 中做最长前缀匹配；支持递归下一跳、聚合成员、子接口 VLAN 匹配与 ECMP 分支。过期的明细路由不会降级到新鲜默认路由。
- 下一跳必须有同一物理链路另一端的接口地址证据；缺少确认链路、端口 down、路由过期、循环或无法证明的 VLAN/overlay 中转均明确返回 incomplete/stale。
- 所有 ECMP 分支放入 `paths`。只有一条分支时同时填充旧 `path` 字段，不默认挑第一条路径。
- 默认 `analysis=routing` 返回配置支持的候选转发路径，已支持部分 EOS traffic-policy；`analysis=flow` 加入流量状态与部分 PA 安全策略/静态 NAT，详见 [PA 流量分析](pa-flow-analysis.md)。不声称验证通用多厂商 PBR、实际 ECMP 哈希、已有会话回程或真实报文交付。

路由明细：`GET /api/routes?device=p1-svc&vrf=default`，可额外用 `prefix` 做精确前缀过滤。页面在 Source Device / Source VRF 下选择条件，点 **Show routes for source device / VRF**。

验收例子：

```text
/api/path?src=p1-svc:Loopback0&dst=1.1.1.174&src_vrf=default
/api/path?src=192.168.111.2&dst=192.168.111.1
/api/path?src=192.168.111.2&dst=192.168.111.1&src_device=PA-171&src_vrf=VR-EW&dst_device=p1-svc&dst_vrf=FUSION
/api/path?src=p1-svc:Ethernet999&dst=p2-svc:Ethernet1
```

第二个查询应提示重复地址；第三个限定两端消除歧义，但 PA 缺少已确认物理线缆时仍必须 incomplete；第四个必须 not_found。

## 测试与升级

后续主动校验、探测和验收仅限当前 Nautobot Site/Location 为 `lab` 的设备，连接前必须检查站点。非 lab 设备继续正常定时采集，只被动查看汇总健康状况，不作为测试目标。离线回归使用 lab 样本或合成数据；时钟提示符解析已改用合成样本。详见根目录 `AGENTS.md`。

Python 3.12，安装 `agent/requirements.txt` 和测试依赖 `agent/requirements-test.txt` 后运行：

```sh
PYTHONPATH=agent python -m unittest discover -s agent/tests -v
```

回归包括真实 PA/EOS 输出、标识/改名/布局、失败证据保留、重复地址、层级、LPM、递归、循环、ECMP、过期链路、页面/API 一致性以及采集错误计数不重复。

部署前备份 Agent 源码、topology.json 和 topology-layout.json，仅重建 agent。EOS 使用按输出空闲结束的读取方式，并兼容带时间戳的尾部提示符；TextFSM 复用已采集输出，避免重复下发命令。新进程逐台完成采集后发布指标，避免等待全批设备结束；启动期间仍会有短暂指标预热，Prometheus 历史数据保留。

## 线上验收记录（2026-10-30）

部署环境：`192.168.20.169`。10:19 CST 快照包含 18 台设备、946 条接口模型记录（含设计/占位记录）、346 条路由、35 条已确认物理链路；39 条候选邻接不画成物理线缆。数量随后续采集变化。

- 18/18 台设备采集成功，接口和路由数据集均成功。
- Prometheus：设备 up 18，接口状态时间序列 854，接收速率时间序列 732，防火墙会话指标覆盖 2 台；Grafana 健康接口 HTTP 200。
- 原有 18 个布局坐标逐项比较一致。
- 真实路径 `p1-svc → p2-svc → p2-leaf` 成功，每跳显示匹配前缀、下一跳、出接口及 LLDP 证据。
- PA 重复地址返回 ambiguous；限定两端设备/VRF 后，未确认的物理连接返回 incomplete；不存在的接口返回 not_found。页面及两个路径 API 返回一致。
- 36 项自动化测试通过；Chrome 验证路径结果、PA 路由明细、重复地址候选表及拓扑，未发现 JavaScript 错误。
- 旧采集基线中的 p2-leaf Loopback6/Vlan610 当前在设备上已不存在，直接执行 show ip interface brief 已确认；没有为保留旧计数伪造接口状态。

部署前备份位于服务器 `/opt/eve-aiops-backups/q2-model-20261030/`。首次验收发现的广播 IP 误识别 MAC 已修正；对应快照及 Nautobot 修复前记录分别保存在 Agent 数据卷 `q2-mac-correction-backup.json`、`q2-nautobot-mac-correction-backup.json`。复查 Nautobot 中该错误 MAC 为 0 条。

## lab PA 物理连接确认（2026-10-30）

用户确认原有 PA 对应关系。仅在 Nautobot 站点已核实为 `lab` 的 p1-svc / p2-svc 上执行定向检查，并结合 PA 正常采集的接口层级，确认：

| PA 物理接口 | 交换机物理接口 | scoped ARP / FDB 证据 |
|---|---|---|
| PA-171 ethernet1/1（ae1 成员） | p1-svc Ethernet3（Port-Channel1 唯一成员） | FUSION / VLAN10：192.168.111.2 → badb.eefb.ad30 → Po1 |
| PA-172 ethernet1/1（ae1 成员） | p2-svc Ethernet3（Port-Channel1 唯一成员） | FUSION / VLAN20：192.168.111.2 → badb.eefb.ad30 → Po1 |

两次 lab 内定向 ping 各 3/3 成功；`show port-channel 1 detailed` 显示 Ethernet3 为唯一 Active / LACP / Rx,Tx 成员。两台 PA 的重复 MAC/IP 依靠站点、设备、VLAN 和接口层级区分。上述两条物理线缆登记为 Nautobot `Connected`，由现有后台拓扑构建读取；MAC 老化不删除人工确认的布线记录。来源为 Nautobot 设计确认，不冒充 LLDP 自动发现。今后改线时需更新 Nautobot cable。

写入前的接口记录、线缆清单和请求保存在 Agent 数据卷 `/app/data/lab-pa-cables-20261030-before.json`。Nautobot cable 的设备 UUID 已修正为先映射设备名再交给快照构建器，避免已登记线缆被忽略；为加载此修复仅更新 Agent，Grafana 无需重启。拓扑页面继续手动刷新，布局保存方式不变。确认物理线缆并不代表已验证防火墙策略或完整的三层转发路径。

Nautobot 关联名称在单次客户端读取内缓存，避免每个接口都请求一次相同状态；读取拓扑设计的单请求超时下限为 30 秒，适应完整接口清单。设备采集超时和正常采集范围保持原配置。39 项 lab/合成离线测试通过。
