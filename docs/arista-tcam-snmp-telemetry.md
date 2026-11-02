# Arista TCAM：SNMP 与 gNMI 采集核查

## 官方定义

`ARISTA-HARDWARE-UTILIZATION-MIB`（2024-04-01 修订）描述转发 ASIC 的硬件资源使用情况。范围包括 TCAM，也可能包括 MAC 哈希表等其他资源；不能把所有条目都当作 TCAM。

根节点为 `1.3.6.1.4.1.30065.3.22`，资源表为 `1.3.6.1.4.1.30065.3.22.1.1`。

| 对象 | OID 列前缀 | 含义 |
| --- | --- | --- |
| aristaHardwareUtilizationInUseEntries | `1.3.6.1.4.1.30065.3.22.1.1.1.4` | 当前使用条目数 |
| aristaHardwareUtilizationFreeEntries | `1.3.6.1.4.1.30065.3.22.1.1.1.5` | 当前空闲条目数 |
| aristaHardwareUtilizationCommittedEntries | `1.3.6.1.4.1.30065.3.22.1.1.1.6` | 为某功能预留的条目数 |
| aristaHardwareUtilizationMaxEntries | `1.3.6.1.4.1.30065.3.22.1.1.1.7` | 单功能不受其他功能共享影响时的最大逻辑条目数 |
| aristaHardwareUtilizationHighWatermark | `1.3.6.1.4.1.30065.3.22.1.1.1.8` | 历史最高使用条目数 |
| aristaHardwareUtilizationHighWatermarkTime | `1.3.6.1.4.1.30065.3.22.1.1.1.9` | 达到高水位的 sysUpTime 时间标记 |
| aristaHardwareUtilizationSharedFeatures | `1.3.6.1.4.1.30065.3.22.1.1.1.10` | 共享同一资源的功能；新版本扩展，不能要求所有设备都返回 |

以上是表列，不能直接加 `.0` 当作标量读取。实例索引由 Resource、Feature、ForwardingElement 三个变长字符串构成；名称列 1–3 为 `not-accessible`，须从实例 OID 的三个长度前缀字符串解码。使用 GETNEXT/GETBULK 遍历再按完整索引关联各列。

虽然 MIB 将占用数定义成 Counter32，语义是当前占用，导出 Prometheus 时应作为 gauge，不能用 `rate()`。共享池、IPv4/IPv6 不同条目宽度及多芯片复制会造成容量口径不同；不要跨功能或芯片直接相加。`used/max` 是相对理论最大容量，OpenConfig 阈值定义使用 `used/(used+free)`；选用后者时要求同一索引下的数据、分母大于零，并明确口径。缺失、空表和超范围值不能伪装成 0%。

## gNMI / OpenConfig 路径

Arista 发布的 EOS 4.37.0F OpenConfig 模型包含：

```text
/components/component/integrated-circuit/utilization/resources/resource/state
```

其中包括 `name`、`used`、`committed`、`free`、`max-limit`、`high-watermark`、`last-high-watermark`。`component` 和 `resource` 分别按名称区分，路径存在于模型中并不保证所有平台返回实例。

采集顺序：Capabilities → 基础 GET（确认认证与协议）→ 对应 ASIC 组件资源 GET / Subscribe ONCE → 实际存在的资源使用 SAMPLE 订阅。不要仅因 Capabilities 包含 openconfig-platform 就宣布 TCAM 已支持。无资源实例时保持缺数据状态。

当前 Telegraf 有 `TELEGRAF_GNMI_TCAM_PATHS` 订阅入口，但它仍需真实有效的路径、设备 gNMI 服务及到标准指标的标签/字段映射；仅设置环境变量不等于已有 TCAM 数据。

## 来源

- [Arista 官方 HARDWARE-UTILIZATION MIB](https://www.arista.com/assets/data/docs/MIBS/ARISTA-HARDWARE-UTILIZATION-MIB.txt)
- [Arista 官方 SMI MIB：enterprises 30065 / aristaMibs 3](https://www.arista.com/assets/data/docs/MIBS/ARISTA-SMI-MIB.txt)
- [Arista EOS 4.37.0F：OpenConfig platform](https://github.com/aristanetworks/yang/blob/master/EOS-4.37.0F/openconfig/public/release/models/platform/openconfig-platform.yang)
- [Arista EOS 4.37.0F：资源利用率模型](https://github.com/aristanetworks/yang/blob/master/EOS-4.37.0F/openconfig/public/release/models/platform/openconfig-platform-common.yang)
- [Arista EOS 4.37.0F：平台不支持项声明](https://github.com/aristanetworks/yang/blob/master/EOS-4.37.0F/release/openconfig/models/not-supported/arista-platform-notsupported-deviations.yang)

## 前期 vEOS 核查（2026-11-02）

用户确认 TCAM 验证对象必须是物理 Arista 交换机；vEOS 不支持物理 TCAM。当前 lab 的 Arista 为 vEOS-lab，因此停止在这些设备上继续尝试 TCAM 采集。

前期仅确认官方 OID 与 YANG 模型定义；后续物理设备实测结果见下文。不能据 vEOS 的 CLI 空表、SNMP 空实例或 gNMI 连接结果推断物理交换机不支持。Telemetry 也不能在 vEOS 上补出不存在的硬件资源。

此前对 lab `p1-host` 的临时服务尝试已撤销，归一化运行配置与测试前完全一致。最近一次记录为 `TCAM-PROTOCOL-PROBE-1793591656`：试验变更已预登记，两条试验漂移均为有主且已关闭；没有处理原有其他漂移。试验过程中 gNMI 显示服务未启用，故该尝试也不构成成功的 Telemetry 数据验证。

此前因物理设备不在默认 lab 测试范围内，未主动测试。用户随后明确要求在物理设备上验证，授权了下文 DUT-7368 的只读测试；这不改变其他设备的默认测试范围。已知认证失败的 7 台物理交换机未重试认证。

物理设备验证顺序：

1. 确认型号、EOS 版本及 `show hardware capacity` 返回的实际资源；确定哪些条目是 TCAM。
2. 用已配置的只读 SNMP 验证 sysUpTime，再遍历上述资源表；基础 OID 成功但资源表为空，才继续检查 MIB 支持、SNMP view、设备功能和资源代理状态。超时不能直接判为“不支持”。
3. 对照同一 resource/feature/forwarding-element 的 CLI 与 SNMP 值。至少采样两个时间点，确认更新、标签及容量口径。
4. 如果该型号/EOS 没有可用 SNMP 资源实例，再验证 gNMI Capabilities、基础路径和 ASIC 资源路径；确认数据后才接 Telegraf SAMPLE 订阅与指标映射。


## 物理设备与 SSH 隧道验证结果（2026-11-02）

**确认可用：通过现有 SSH 隧道登录 DUT-7368，再读取设备本机 SNMP MIB，能够取得真实硬件资源数据。** 本次完成能力验证，持续采集程序仍使用原有实现。

- 当前 Nautobot 设备：DUT-7368，UUID `b6ac4141-c38e-4a7b-adcf-cc937244a334`，Site `public-wan`。
- 物理型号 / EOS：`7368-F` / `4.35.5M`；进入现有 SSH Tunnel 的地址为 `123.58.11.222:3016`。
- 用户明确要求物理设备测试，并确认通信封装在 SSH Tunnel 内；本次仅查询该可登录设备，未修改设备配置。
- 2026-11-02 12:06:02、12:08:20（Asia/Shanghai）两次完成采样。sysUpTime 从 `317937336` 增加到 `317951728` ticks，所列资源值在两次采样间相同。
- used/free/max 三列各返回 **71 个实例**。第二次采样按完整 `(resource, feature, forwarding-element)` 索引与 `show hardware capacity | json` 的 71 行逐项核对，**全部一致**。

| 资源 | Feature | 芯片 | 已用 | 空闲 | 最大容量 | 使用率 |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| IFP | 总览（空字符串） | Switchcard1/0 | 286 | 2786 | 3072 | 9.31% |
| EFP | 总览（空字符串） | Switchcard1/0 | 3 | 509 | 512 | 0.59% |
| VFP | 总览（空字符串） | Switchcard1/0 | 25 | 487 | 512 | 4.88% |
| IFP | Slice-1 | Switchcard1/0 | 107 | 149 | 256 | 41.80% |

使用率按 `used/(used+free)*100` 计算。这里的 71 行包含其他 ASIC 资源，不能全部标记为 TCAM。

通过上述 SSH 会话执行的只读命令：

```text
show snmp mib get 1.3.6.1.2.1.1.3.0
show snmp mib walk 1.3.6.1.4.1.30065.3.22.1.1.1.4
show snmp mib walk 1.3.6.1.4.1.30065.3.22.1.1.1.5
show snmp mib walk 1.3.6.1.4.1.30065.3.22.1.1.1.7
show hardware capacity | json
```

**传输方式要明确区分：** 上述实测是 SSH 传输 CLI 查询、本机读取 SNMP MIB；不是采集服务器直接向公网发送 UDP SNMP。当前 Nautobot 中 8 台物理设备的 SNMP endpoint 都为 `123.58.11.222:161`，直接读取 sysName/sysUpTime 超时，未确认该 endpoint 对应哪个设备；不能据此判断 SSH Tunnel 中的设备 OID 不可用。普通 SSH 端口转发也不能直接替代 UDP 转发。接入时可复用已存在的 SSH 会话读取 MIB，或在设备管理网内部部署 SNMP 采集器。

设备上 gNMI 已运行于 `mgt` VRF 的 TCP 6030，配置了 TLS profile；本次没有读取其资源路径，不能宣称 gNMI TCAM 已验证，也没有为此改变现有服务。SNMP MIB 路径已证明可用，无需为了本次能力验证额外启用 Telemetry。

同时发现已有 CLI 文本解析存在列错位：当前 IFP 的 `344` 是 highWatermark，却被已有解析器当作百分比；同一资源真实 `usedPercent` 为 EOS 显示的整数 `9`，由条目数计算为 `9.31%`。后续接入时应采用明确的 MIB 列或 JSON 字段映射，保留 Feature 和 Chip，避免总览与 Slice 明细被去重覆盖。本次未修改线上解析器或将新数据接入 Grafana。
