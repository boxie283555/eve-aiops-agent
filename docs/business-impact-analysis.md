# 业务流登记与影响分析

业务影响页面为 `/business`，也可从 Network Pulse 侧栏“工作空间 → 业务影响”进入。业务定义和报告存放在 Agent 持久化数据卷中；页面使用现有拓扑、路由和防火墙策略证据，不主动登录设备。

## 登记业务流

填写：

- 业务名称和说明。
- 源 IP、目的 IP。
- 协议：`any`、TCP、UDP、ICMP 或 ICMPv6。
- 可选源/目的端口、PAN-OS application、入口接口和 VRF。

如果只知道源和目的 IP，仍可先登记，但策略命中可能无法唯一确定，报告会指出缺少协议、端口或应用等条件。

## 生成路径与故障报告

选择已登记业务后，生成当前基线报告。报告按路径图显示源端、经过的网络设备/接口、防火墙决策和目的端，并给出易读结论、证据时间及不完整项。

可在当前拓扑快照上离线模拟以下故障：

- lab 设备离线。
- lab 接口中断。
- 从 lab 设备路由中移除指定路由。

模拟仅修改内存中的快照副本，不会写入设备或 Nautobot。报告同时保留故障前基线和故障后路径结果，可从历史报告列表重新打开。

## API

| 方法与路径 | 用途 |
| --- | --- |
| `GET /api/business/flows` | 列出已登记业务 |
| `POST /api/business/flows` | 新建业务定义 |
| `PUT /api/business/flows/{flow_id}` | 编辑未归档业务 |
| `POST /api/business/flows/{flow_id}/archive` | 归档业务 |
| `POST /api/business/flows/{flow_id}/analyze` | 生成当前路径基线报告 |
| `POST /api/business/flows/{flow_id}/scenarios` | 生成故障模拟报告 |
| `GET /api/business/reports` | 列出最近报告；可用 `flow_id` 过滤 |
| `GET /api/business/reports/{report_id}` | 读取一份历史报告 |

业务定义的 JSON 字段与页面一致。例如：

```json
{
  "name": "OA",
  "description": "办公业务流",
  "source_ip": "10.100.101.111",
  "destination_ip": "10.200.123.212",
  "protocol": "any",
  "source_port": null,
  "destination_port": null,
  "application": null,
  "ingress_interface": null,
  "vrf": "all"
}
```

故障场景 JSON 使用 `kind` (`device_down`、`interface_down` 或 `route_removed`) 和对应拓扑对象的 `target_id`。

## 证据边界

- 只分析当前快照中 Nautobot Site 为 `lab` 的范围。
- 报告绑定当时的拓扑和策略证据时间；报告不是实时探测。
- 模拟故障用于解释路径影响，不证明真实报文一定通过或被丢弃。
- PA 策略结论受应用、协议、端口、入口接口等匹配条件和策略备份新鲜度限制。
- 缺少或过期的路由、拓扑、策略或设备证据会使结果为 `incomplete`；不能把缺证当作正常。

Q4 OA 试点记录应由项目运行环境单独保存；此通用文档不包含特定部署的报告 ID、内部设备配置或业务评审链接。
