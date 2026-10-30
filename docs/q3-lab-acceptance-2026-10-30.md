# Q3 lab 演练记录 · 2026-10-30

本记录验证备份、结构化差异、漂移检测和处理闭环；不作为“三次真实无主漂移”的业务验收。

演练编号：`q3-lab-20261030-165455-29eefd`。目标：Nautobot 当前 Site 为 `lab` 的 `p1-ext`。

| 演练 | 漂移 ID | 分类 | 回滚 / BGP |
| --- | --- | --- | --- |
| interface_description | `731e6e0b86e24c9bb679d06783580594` | interface | 配置恢复、邻居状态及会话起始时间未变 |
| unbound_acl | `61ddf97bfa724861babffc5397c405bf` | acl_policy | 配置恢复、邻居状态及会话起始时间未变 |
| bgp_neighbor_description | `6897e681362643dea7d74bc38340a378` | bgp | 配置恢复、邻居状态及会话起始时间未变 |

- 首次 `POST /config/backup`：10 台 lab 设备成功（8 台 EOS、2 台 PA），首次基线没有误报漂移。
- 三次修改与三次回滚均产生可追溯记录，共 6 条 `unowned=true, origin=exercise`，验证回滚后已登记处理。
- 16 个配置版本、32 个原文/模型文件均存在，文件权限 0600、目录 0700，SQLite 完整性检查为 `ok`。
- 当前真实观察的未处理无主漂移为 0；演练待处理为 0；演练历史为 6。
- 运维、中英文汇报视图均已增加 Q3 状态卡；真实与演练统计分开。
- 自动备份与检测间隔为 15 分钟，仅覆盖当前 lab；156 项自动化测试通过。
- 拓扑布局 SHA256 保持 `611e6eacfe30f5388f03d7566ea523a07c548b7af3e3c5db865f92f36800dd56`。

完整执行报告在服务器 Agent 持久卷：`/app/data/config_history/exercises/q3-lab-20261030-165455-29eefd.json`。

容器强制重建后再次验证：16 个版本和 6 条已处理演练记录全部保留；再次 POST 备份，10 台均为 unchanged，未新增重复漂移。三套 Grafana 视图的查询与浏览器渲染验证通过，显示依次为 0 / 0 / 6 / 0。
