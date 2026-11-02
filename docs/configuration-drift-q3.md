# Q3：配置备份、结构化 diff 与漂移检测 v1

当前部署范围：Nautobot 当前 Site 恰为 `lab` 的 EOS、NX-OS、PAN-OS 设备。每次主动备份先校验设备 UUID、名称、地址和 Site；非 lab 的原常规指标采集保持运行，不新增主动测试连接。

## 持久化和采集

默认目录 `/app/data/config_history/`（Docker `agent-data` 持久卷），可由 `AIOPS_CONFIG_HISTORY_PATH` 指定绝对路径。目录权限 0700，原始配置、规范化模型和数据库文件 0600。原始配置可能包含设备密钥，只允许服务器受控访问，API 不提供原文下载。

- `POST /config/backup`：无请求体时备份当前 lab 设备；`{"devices":["p1-ext"]}` 选择名称或 UUID。成功后自动检测漂移；重复配置更新成功观察时间 `last_observed_at`，不生成重复版本。
- `GET /config/backups`：版本元数据，可按 `device_id` 筛选；`created_at` 是版本首次保存时间，`last_observed_at` 是该版本最近一次成功观察时间。
- `POST /config/drift/detect`：可单独补做检测，按版本幂等处理。
- `GET /config/drift?unowned=true`：保留全部无主记录，包括已处理与演练。支持 `status=open|resolved`、`origin=observed|exercise`、`limit`。
- `POST /config/drift/{id}/resolve`：`{"actor":"operator","reason":"已核实并恢复"}`。处理结果不可改写原始归属、版本和差异。

独立后台任务默认每 900 秒备份并检测；`AIOPS_CONFIG_BACKUP_INTERVAL` 可调，最低 300 秒。最多两个并发设备，同一进程备份不重入，手动冲突返回 409。任务不等待或调用全设备指标采集，不发送邮件。重启后首个定时任务等待一个周期；可以立即 POST 验证。

原文与规范化 JSON 按 UUID/版本 ID 保存，SQLite 保存索引、检测进度、归属和处理记录。备份文件先原子写入，之后提交索引；中断可能留孤立文件，但不会把未写完文件设为基线。检测在事务内完成并校验模型 SHA256；失败不覆盖成功基线。设备管理地址或平台改变时建立新基线，不将不同设备的配置比较为漂移。

## 结构化 diff 与归属

CLI 按缩进保留父子层级，在接口、ACL、BGP 等配置块内保留命令顺序；仅忽略展示标题与注释。PAN-OS 使用运行配置 XML，保留节点路径、命名对象与顺序。输出 `context/category/operation/before/after`，不是运行计数器的文本差异。密码、secret、community 等敏感字段在私有模型中用指纹比较，公共 diff 仅显示 `<redacted>`。

`unowned=true` 的准确含义是：**观察窗口内没有匹配的预登记变更引用**，不是证明现场操作者未经批准。当前没有对接外部 ITSM 审批系统。

预登记接口 `POST /config/changes` 使用 `id/device/owner/starts_at/ends_at/contexts`；时间为 Unix 秒，窗口最长 24 小时，contexts 必须精确列出配置块，例如 `interface Loopback0` 或 `router bgp 65102`。登记必须早于比较基线的成功观察，时间窗口完整覆盖前后两次采样，并包含所有变更块，才关联 `change_ref`；事后登记不会追认历史漂移。这仍是操作员声明的变更登记，不代表外部审批系统签名验证。

已有设备的操作顺序：

1. `POST /config/changes` 登记变更编号、设备、责任人、计划窗口及配置块。
2. 窗口开始后、实际修改前，对该设备执行 `POST /config/backup`，确认 `saved` 或 `unchanged`，以及返回的 `last_observed_at` 落在窗口内。已有版本即使内容未变，也会记录这次成功观察。
3. 执行已登记的配置修改，在窗口结束前再次成功备份。全部变更块匹配时，漂移记录关联 `change_ref` 并显示 `unowned=false`。

新版本保存时，将前一个版本的最近成功观察时间固定为本次差异的窗口起点；终点是新版本的 `created_at`。延迟检测、后续无变化备份、失败备份或事后登记均不能扩大或改写该窗口。观察元数据与版本索引在同一事务中持久化。升级自动新增观察元数据表；旧版本缺少观察证据时仅使用原版本创建时间，不根据最近尝试时间猜测历史成功观察，也不重算已检测漂移的归属。

## 演练与真实验收

`exercise_id` 将备份及随后生成的漂移标记为 `origin=exercise`，`phase=baseline/change/rollback` 区分阶段。真实定时观察默认为 `origin=observed`。演练在检测器中故意不登记变更引用，因此得到无主记录，但**不能算作 Q3 的 3 次真实无主漂移**。

`scripts/config/lab_drift_exercise.py` 默认仅展示计划，`--execute --device p1-ext` 执行当前 lab EOS 的三项可回滚实验：Loopback0 描述、未绑定测试 ACL、已有 BGP 邻居描述。每项独立修改、备份、检测、回滚、再次备份，核对完整规范化配置恢复及 BGP 会话状态。既不绑定 ACL，也不修改 BGP remote-as 或重置邻居。三次变更和三次回滚共六条审计记录；回滚验证后登记处理结果，保留历史。演练报告写在 `config_history/exercises/`。

## Grafana

运维、中英文汇报视图顶部健康评分后新增 Q3 区：

- 未处理无主漂移：`aiops_config_drift_records{origin="observed",status="open",unowned="true"}`，大于零标红。
- 待处理演练漂移：独立显示，不并入真实告警。
- 演练漂移历史：包含变更和回滚，处理后仍保留。
- 最近备份失败设备：基于 `aiops_config_backup_success`。

`aiops_config_history_available` 标识持久库可读；库缺失/失败时不显示虚假的正常零值。未执行过备份的设备不在覆盖范围内，应同时查看备份 API 和失败卡片。

## 验证范围与后续

当前具备备份、结构化差异、持续检测、预登记关联及处理记录闭环。测试包括重启持久化基础、幂等、三类变化、秘密脱敏、规则顺序、损坏模型、失败基线保护、范围校验和指标来源隔离。真实目标仍需观察并核实至少三次现场发生、无匹配变更单的漂移。后续可对接 ITSM 身份/审批验证、备份加密与保留策略、非 lab 经授权推广和更完整的多厂商语义归一化。现有 API 沿用项目内网部署方式，尚未新增统一身份认证。
