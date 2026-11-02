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

## 本次核查结论（2026-11-02）

用户确认 TCAM 验证对象必须是物理 Arista 交换机；vEOS 不支持物理 TCAM。当前 lab 的 Arista 为 vEOS-lab，因此停止在这些设备上继续尝试 TCAM 采集。

本次确认的是官方 OID 与 YANG 模型定义，**尚未在物理交换机上验证返回值**。不能据 vEOS 的 CLI 空表、SNMP 空实例或 gNMI 连接结果推断物理交换机不支持。Telemetry 也不能在 vEOS 上补出不存在的硬件资源。

此前对 lab `p1-host` 的临时服务尝试已撤销，归一化运行配置与测试前完全一致。最近一次记录为 `TCAM-PROTOCOL-PROBE-1793591656`：试验变更已预登记，两条试验漂移均为有主且已关闭；没有处理原有其他漂移。试验过程中 gNMI 显示服务未启用，故该尝试也不构成成功的 Telemetry 数据验证。

已知认证失败的 7 台物理交换机遵照用户要求暂不诊断。继续实测需要一台可访问、获准测试的物理 Arista，并先确认当前 Nautobot Site 为 `lab`；本次未改动生产采集源、Grafana 查询或其他设备配置。

物理设备验证顺序：

1. 确认型号、EOS 版本及 `show hardware capacity` 返回的实际资源；确定哪些条目是 TCAM。
2. 用已配置的只读 SNMP 验证 sysUpTime，再遍历上述资源表；基础 OID 成功但资源表为空，才继续检查 MIB 支持、SNMP view、设备功能和资源代理状态。超时不能直接判为“不支持”。
3. 对照同一 resource/feature/forwarding-element 的 CLI 与 SNMP 值。至少采样两个时间点，确认更新、标签及容量口径。
4. 如果该型号/EOS 没有可用 SNMP 资源实例，再验证 gNMI Capabilities、基础路径和 ASIC 资源路径；确认数据后才接 Telegraf SAMPLE 订阅与指标映射。
