# 网络发现向导

入口 `/discovery?lang=zh`（英文 `lang=en`）。监控首页侧栏和 OPS Tools 顶部提供链接。

## 五步流程

1. **选择范围**：勾选已有 lab 设备，或输入管理 IPv4、IPv4:端口、IPv4 CIDR。每次最多展开 256 个候选地址，网段最宽 /24；这一阶段只整理清单，不发送探测报文。
2. **审核清单**：按管理 IP＋端口匹配现有设备。重复端点标记冲突，非 lab 端点及其替代端口排除。未知设备需明确选择 lab Location、平台、设备类型、角色及服务器凭据配置，经确认才批量创建 Nautobot 设备。初始状态 Maintenance，`aiops_monitoring_enabled=false`；管理地址存入现有 `management_ip` 自定义字段，Nautobot IPAM 地址对象可另行维护。
3. **识别设备**：最多选 32 台，异步任务并发 2 台。每台连接前重新读取当前 Nautobot 清单，验证 UUID、管理地址、端口和 lab 归属。EOS 按选定 SSH/Telnet、NX-OS 经 SSH 读取身份与 LLDP；EOS 额外核对全 VRF BGP 和 IGP 配置。PA 经现有 PAN-OS API 读取身份与虚拟路由器协议开关。证据不保存密码或完整配置。新设备成功验证后可启用正常采集，保持 Maintenance；已有设备状态不改。
4. **核对连线**：采集和接口同步完成后，手动读取当前拓扑。新鲜、已确认的物理接口连接可经用户确认写成 Nautobot Cable，已有相同线缆幂等跳过；接口占用、冲突、陈旧证据、IP/MAC 候选不自动写线缆。未登记的 LLDP 邻居列为线索，不递归探测。两端都必须属于 lab。原拓扑图布局和手动刷新方式不变。
5. **确认基线**：成功识别后 15 分钟内预览并批准新基线。BGP 按实际观测给出必需邻居候选，状态异常的邻居仍明确显示观测状态供审核；空表只有结合未配置证据才生成不适用预期。IGP 仅能生成已验证未启用的适用性。已有 BGP/IGP 基线一律保留，变更已有预期需到 Nautobot 审核修改。批准绑定本次预览和证据指纹，重跑识别、设备连接身份变化或预览过期后必须重新预览。

正式接管完成后，可在 Nautobot 将核实后的 Maintenance 设备改为 Active 参与健康评分。向导没有自动将设备加入正式评分的步骤。

## 首次部署准备

Nautobot 必须已配置 lab Location、设备类型、角色和受支持平台，并完成 Agent 的连接、生命周期及基线自定义字段初始化。当前受支持驱动为 `arista_eos`、`cisco_nxos`、`paloalto_panos`，Nautobot 平台需设置对应 `network_driver`。未启用的类型可在 Nautobot 中先配置。

凭据配置自动从现有 lab 设备使用的环境变量引用生成。没有已有设备时，可以在 Agent 环境中配置 `AIOPS_DISCOVERY_PROFILES`，值为 JSON 数组，例如：

```json
[{"name":"Lab EOS","platform":"arista_eos","username_env":"LAB_SSH_USER","password_env":"LAB_SSH_PASSWORD","secret_env":"LAB_SSH_SECRET"}]
```

服务器必须已配置引用的环境变量。网页只显示配置名称和引用，不显示或存储密码。新增环境配置需要更新 Agent 容器；一般设备录入仍通过现有清单热加载生效。PA 识别使用原生 HTTPS 管理 API，需管理端可达。

## 任务与审计

任务和审计持久化于 `/app/data/discovery/discovery.sqlite3`，可用 `AIOPS_DISCOVERY_PATH` 调整。后台识别独立于定时采集线程，并用单任务锁避免重复执行。重启后未结束任务标记 interrupted，可在最近任务中重新执行。注册、开始探测、启用采集、线缆入库、基线审核均记录操作人和目标。

主要 API：`GET /discovery/options`、`POST /discovery/jobs`、`GET /discovery/jobs/{id}`，以及任务下的 `register`、`probe`、`enable`、`topology`、`cables`、`baselines/preview`、`baselines/approve`、`audit`。修改操作为 JSON POST，同源浏览器请求；沿用当前内部运维应用的访问边界。

## 范围和限制

当前部署仍受项目 lab 测试范围约束。CIDR 展开不等于扫描整个网段；不使用没有当前 lab 登记的 IP/MAC/LLDP 线索扩展主动访问。向导目前不做自动厂商盲探、跨站点递归扫描或未知凭据尝试。

首次核对、写入和启用采集均为用户明确操作；未知设备登记不会自动开启采集，已有资产不会被重新创建。非 lab 的正常定时采集保持现状。

## 验证记录（2026-11-03）

- 71 项自动化测试通过，覆盖发现流程、BGP/IGP 基线、生命周期、健康评分和监控可见性。
- 模拟浏览器走通候选清单、暂停采集登记、异步识别、启用新设备采集、基线预览/批准及审计；新建 Cable、冲突及幂等行为由模拟 API 测试验证。
- 线上仅对当前 lab 内的 p1-leaf、PA-171 执行只读识别：成功取得 EOS/PAN-OS 身份、EOS LLDP/BGP、协议适用性；两台设备共四份已有 BGP/IGP 基线均显示保留。
- 线上查看到 5 条相关物理连接、1 条未登记邻居线索。没有为验收创建新设备、线缆或覆盖基线，未探测邻居线索。
- 线上中英文、任务恢复、移动布局、浏览器无脚本异常检查通过；原有拓扑布局文件哈希不变。
- 重启后完整周期确认：18 台清单设备、10 台启用采集且全部成功；20 条 BGP 邻居均建立（含 8 条非 default VRF），10 台 IGP 不适用基线保持有效。监控首页、Prometheus 和 Grafana 健康入口均返回成功。


## SSH / Telnet 连接协议

“选择范围”中的 CLI 连接协议作用于新输入地址：SSH 默认 22，Telnet 默认 23，`IP:端口` 可指定隧道或自定义端口。已有设备以当前 Nautobot 的 `use_ssh` 和 `connection_port` 为准，不因下拉框切换而被修改。候选清单和识别表显示实际协议。

运行 `scripts/nautobot/provision_cli_transport.py` 初始化布尔自定义字段 `use_ssh`（默认 true）。新设备登记时保存该字段：true/空值为 SSH，false 为 Telnet。Nautobot 设备编辑页可维护此字段及连接端口，Agent 清单热加载后生效。协议改变会让旧识别证据失效，需要重新识别才能批准基线或启用新设备采集。

发现、定时 CLI 指标采集、CLI 配置读取和 EOS 恢复连接共用协议映射。当前内置 Telnet 驱动支持 Arista EOS 与 PAN-OS；NX-OS 使用 SSH，不会静默回退到其他厂商驱动。PA 的身份发现、配置及策略 API 仍走 HTTPS；Nautobot 自身的 NAPALM 连接不由此字段切换。

不会自动启用设备上的 Telnet 服务。Telnet 以明文传输凭据，适用于可信管理网或已有受保护隧道。未改变既有设备的协议、端口或凭据。

SSH/Telnet 更新验证：101 项相关测试通过，涵盖登记字段、默认与自定义端口、协议改变后证据失效、采集与配置连接选择。浏览器模拟登记验证通过；Netmiko 对本地模拟 EOS Telnet 服务完成真实登录和命令读取。此次没有开启或修改任何设备的 Telnet 服务，实机 Telnet 兼容性尚需用已启用该服务的 lab 设备验证。
