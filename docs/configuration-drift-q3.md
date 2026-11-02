# Q3：配置备份、结构化 diff 与漂移检测 v1

当前部署范围：Nautobot 当前 Site 恰为 `lab` 的 EOS、NX-OS、PAN-OS 设备。每次主动备份先校验设备 UUID、名称、地址和 Site；非 lab 的原常规指标采集保持运行，不新增主动测试连接。

## 持久化和采集

默认目录 `/app/data/config_history/`（Docker `agent-data` 持久卷），可由 `AIOPS_CONFIG_HISTORY_PATH` 指定绝对路径。目录权限 0700，原始配置、规范化模型和数据库文件 0600。原始配置可能包含设备密钥，只允许服务器受控访问，API 不提供原文下载。

- `POST /config/backup`：无请求体时备份当前 lab 设备；`{"devices":["p1-ext"]}` 选择名称或 UUID。成功后自动检测漂移；重复配置更新成功观察时间 `last_observed_at`，不生成重复版本。
- `GET /config/backups`：版本元数据，可按 `device_id` 筛选；`created_at` 是版本首次保存时间，`last_observed_at` 是该版本最近一次成功观察时间。
- `POST /config/drift/detect`：可单独补做检测，按版本幂等处理。
- `GET /config/drift?unowned=true`：保留全部无主记录，包括已处理与演练。支持 `status=open|resolved`、`origin=observed|exercise`、`limit`。
- `POST /config/drift/{id}/resolve`：`{"actor":"operator","reason":"已核实并恢复"}`。处理结果不可改写原始归属、版本和差异。

独立后台任务默认每日 **02:00 Asia/Shanghai** 备份并检测。在 `/config/ui` 可修改时间、IANA 时区、设备范围或暂停计划；设置持久化到 SQLite，保存后立即重排 Cron 任务，重启后仍生效。空设备列表表示届时所有当前 lab 设备；显式设备列表仍在每次执行前重新检查 Site。旧的 `AIOPS_CONFIG_BACKUP_INTERVAL` 不再控制备份频率。最多两个并发设备，同一进程备份/恢复不重入，手动冲突返回 409。任务不等待或调用全设备指标采集。进程暂停造成一小时以内的计划延迟可合并执行一次；停机跨过计划时间后重启不补跑过去日期，可手动补备份。

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

运维、中英文汇报视图顶部健康评分后有「配置备份与漂移」区：

- 未处理无主漂移：`aiops_config_drift_records{origin="observed",status="open",unowned="true"}`，大于零标红。
- 待处理演练漂移：独立显示，不并入真实告警。
- 演练漂移历史：包含变更和回滚，处理后仍保留。
- 最近备份失败设备：基于 `aiops_config_backup_success`。
- 未处理无主漂移设备明细：设备名、未处理记录数、最近检测时间和无主说明。只统计真实观察，排除演练和已处理记录。
- 最近备份失败设备明细：设备名、最近失败尝试时间和错误类别。下一次备份成功（包括配置未变化）后自动移除；失败历史仍保留在配置管理中。

新增 `aiops_config_drift_last_detected_timestamp_seconds{device,origin}` 为当前未处理无主记录的最近检测时间；
`aiops_config_backup_failure_info{device,reason}=1` 仅在最近尝试失败时发布。错误类别来自异常类型白名单，
不发布原始异常正文、凭据或配置内容。认证、超时、连接、API、响应校验、存储权限/数据库异常分别展示，
未识别类型明确标为未分类。时间来自持久记录，不是 Prometheus 抓取时间。
明细与卡片使用相同设备筛选和所选时间终点。备份失败的设备链接进入备份历史（`/config/ui?device=设备名`）。
无主漂移的设备链接进入变化详情（`/config/drift/ui?device=设备名&lang=zh`），默认筛选真实、未处理、无主记录，
自动展开最新一条；可切换其他记录、已处理记录与演练。支持名称或 UUID 的精确筛选；没有匹配记录时明确显示空状态。
详情页显示当前处理状态，不把 Grafana 历史终点的状态当作当前事实。

`GET /config/drift` 增加可选 `device` 和 `offset`，保持旧调用兼容。
`GET /config/drift/{id}` 返回该记录的脱敏变化及绑定版本元数据；`?full=true` 校验模型指纹后提供完整规范化配置对照。
比对只使用 `before_id/after_id`，前采样时间取后版本创建时冻结的 `baseline_observed_at`，不被后来未变化备份推进。
历史证据不足时注明采用版本首次保存时间。缺失或损坏的版本明确报错，不改用最新版本。
前后规范化配置保留命令顺序，新增/删除行以绿/红色标示；纯顺序变化仍需看前后列的原有顺序。

页面支持填写处理人、说明及可选变更/事件单引用，再手动标记已处理。
`POST /config/drift/{id}/resolve` 的可选 `reference` 进入处理说明与 `drift_resolve` 审计，
与状态更新在同一事务完成；重复处理不覆盖原记录。不改变 `unowned/change_ref`，事后关联不冒充预审批。
浏览、完整比对、登记处理均只访问持久化证据，不连接设备，不触发配置恢复。

`aiops_config_history_available` 标识持久库可读；库缺失/失败时不显示虚假的正常零值。未执行过备份的设备不在覆盖范围内，应同时查看备份 API 和失败卡片。

## 验证范围与后续

当前具备备份、结构化差异、持续检测、预登记关联及处理记录闭环。测试包括重启持久化基础、幂等、三类变化、秘密脱敏、规则顺序、损坏模型、失败基线保护、范围校验和指标来源隔离。真实目标仍需观察并核实至少三次现场发生、无匹配变更单的漂移。后续可对接 ITSM 身份/审批验证、备份加密与保留策略、非 lab 经授权推广和更完整的多厂商语义归一化。现有 API 沿用项目内网部署方式，尚未新增统一身份认证。


## 配置管理页面与 API

Grafana 四个中英文视图的配置备份与漂移区均链接到 `http://192.168.20.169:8080/config/ui`，英文使用 `?lang=en`。页面支持设备多选立即备份、每日计划、备份文件与日期、单设备历史分页/时间筛选、按时间或版本比对、恢复预览和操作审计；没有自动刷新，操作后更新相关记录。

页面采用四个独立页签，同一时间只显示一个模块：**01 备份与计划**（蓝色）、**02 历史与比对**（青色）、**03 配置恢复**（琥珀色）、**04 操作审计**（紫色）。顶部固定导航同时使用编号、文字与选中标记，支持键盘方向键及 Home/End；窄屏导航排列为两列，表格在各自区域内滚动。URL 的 `#backup`、`#history`、`#restore`、`#audit` 可直接打开对应模块。

设备与操作人输入在适用模块显示，切换页签保留已选设备、版本与比对结果。历史版本的“选作恢复目标”跳转至 03；“查看脱敏配置”跳转至 01 文件预览；备份文件行的“版本”跳转至 02。恢复页明确展示“选择设备与版本 → 读取当前配置并预览 → 确认恢复并核查结果”三个步骤，切换页签或选择版本本身不会执行恢复。

备份文件表显示文件名、大小、文件是否存在、版本创建时间、最近成功备份时间及最近尝试结果。配置未变化时共用文件和版本，只更新观察时间；最近尝试失败仍保留上次成功文件。文件预览直接显示备份文件脱敏后的连续文本，不按配置块分类。交换机配置保留文件顺序、缩进、注释和分隔行；PAN-OS XML 保留元素顺序并缩进显示。密码、community、密钥及其嵌套内容以 `[REDACTED]` 隐藏。原始 `.txt` / `.xml` 仍保留在服务器用于受控恢复。

- `GET /config/devices`：当前 lab 清单及恢复试点能力。
- `GET/PUT /config/schedule`：`enabled/time/timezone/devices`；写入另需填写 `actor`。时间格式 `HH:MM`。
- `GET /config/history?device_id=UUID&start=秒&end=秒&limit=50&offset=0`：逐次成功/失败备份记录；相同内容不重复保存配置文件。
- `GET /config/compare?device_id=UUID&before_at=秒&after_at=秒`：分别选择指定时间当时或之前最近一次成功观察，返回实际采样时间和脱敏结构化差异。无历史覆盖、时间倒置或设备地址/平台基线变化会拒绝比较。相同版本比较为空差异。
- `GET /config/compare/versions?device_id=UUID&before_id=版本A&after_id=版本B`：直接比较固定版本 A → B，返回版本 ID、创建时间及脱敏差异。允许反向或同版本比较；拒绝跨设备或管理地址/平台基线不同的版本。时间比较与版本比较共用差异计算逻辑。
- `GET /config/backups?device_id=UUID&limit=100&offset=0`：文件元信息、版本及最近成功观察时间；`total` 是版本总数。
- `GET /config/backup-status`：每台已尝试备份设备的最近结果及最新成功文件；页面只展示当前 lab 设备。
- `GET /config/backups/版本ID`：校验模型及原始文件完整性后返回 `content` 脱敏文本，不返回原始秘密或服务器路径。
- `GET /config/audit?limit=50&offset=0`：备份、计划、预览、恢复审计。操作人是用户声明，不是身份认证结果。记录含来源、目标/安全版本、变更引用、结果或脱敏错误类型。

迁移只补入已有版本创建时间和已有最后成功观察时间，不制造旧周期的备份记录。新备份的观察记录与版本在同一事务提交，失败记录不会成为时间比对基线。

历史行 A/B 在按时间模式选取该次观察时间；按版本模式选取该行的配置版本。版本文件表的 A/B 则自动切换到按版本模式。`Select as recovery target / 选作恢复目标` 仅选择恢复目标，不连接设备或执行恢复；后续仍需预览与明确确认执行。

## 恢复试点

预览面向当前 lab；**执行默认仅开放 lab 的 `p1-host` / Arista EOS**。`AIOPS_CONFIG_RESTORE_DEVICES` 可由服务器管理员设置名称或 UUID 白名单，但其他厂商恢复尚未实现。恢复作用于 running-config，不写 startup-config。

1. 页面选择设备与历史版本，填写操作人和变更引用，点击预览。`POST /config/restore/preview` 接收 `device/version_id/actor/change_ref`，读取当前配置并自动备份，返回脱敏差异和有效期 10 分钟的预览 ID。
2. 输入完整设备名和恢复操作密钥，调用 `POST /config/restore/execute`，请求为 `preview_id/confirm_device`，密钥通过 `X-Config-Restore-Token` 请求头传入。首次启动自动生成私有 `config_history/restore-token`（0600）；服务器管理员可通过 `sudo docker exec eve-aiops-agent cat /app/data/config_history/restore-token` 获取，不能放在 URL 或工单中。页面不存储密钥。普通备份、比对沿用现有内网访问方式；此共享密钥不替代完整身份/权限系统。
3. 执行前重新核实 Nautobot Site、UUID、地址、平台、白名单、目标原文及模型 SHA256、预览有效期及当前配置指纹。预览一次性消费；不符合条件需重新预览。
4. 自动登记 `restore-预览ID` 操作引用并生成恢复前安全备份，防止将本次已登记恢复误判为无主；外部变更编号同时写入审计，仍不表示已接入审批系统。恢复成功或失败后关闭该操作的登记窗口，避免后续无关变更被归到本次恢复。
5. EOS 配置文件暂存为权限受限的 flash 文件，在独立配置会话内 `rollback flash:...`，检查完整候选配置，并再次确认运行配置没有并发变化。使用 `commit timer 00:05:00`，重新建立管理连接验证完整配置、核对待确认会话归属后，再执行 `configure session pending-timer commit` 确认，最后备份与检测漂移。
6. 提交前失败会中止本操作的候选会话。提交后的异常记录为 `verification_required`，由设备确认倒计时保护；审计不会在未观察到回退时声称回退成功。应核查运行配置及 EOS 会话状态后再操作。进程中断留下的 `running` 审计也需要人工核查。

当前验证范围以 lab 试点为准，不能据此宣称 PA / NX-OS 恢复或全网配置恢复已经验收。


## 备份与当前运行配置即时比对

在「02 历史与比对」选择「备份与当前运行配置」，填写操作人，选择已有备份版本
（默认最新），点击「与当前运行配置比对」。版本列表也可选择某个版本作为即时比对
基线；选择本身不连接设备。结果方向固定为 **备份 → 当前运行配置**，展示版本创建
时间、该版本最近成功观察时间、当前读取开始/完成时间及脱敏结构化差异，支持中英文。

`POST /config/compare/current` 接收 `device`、`version_id`、`actor`。读取前重新确认
Nautobot 当前 lab 范围，校验备份 UUID、管理地址、平台及文件/模型完整性；复用现有
EOS/NX-OS CLI 或 PAN-OS 运行配置读取。与备份/恢复共用操作锁，避免配置任务重入。
不调用恢复预览，不生成配置文件、备份版本、备份观察或漂移记录，不推进检测基线，
不更新最近备份成功/失败状态，也不执行设备配置。只保存 `compare_current` 操作审计
（版本、设备、读取时间、差异块数量或错误类别）；当前配置及差异内容不写入审计。
即时比对结果不计入 Open unowned drift，刷新页面后需重新读取。若需持久证据，使用
正常备份流程。读取失败清空旧结果，已有两时间/两版本比较及恢复流程保持可用。
