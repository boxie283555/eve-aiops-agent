# 硬件面板按厂商和设备类型展示

运维视图 `eve-aiops-overview` 的硬件功能先按功能分区，再在每个功能内按
**厂商 → 交换机 / 防火墙** 展示独立面板。型号继续保留在清单和部件明细中。

覆盖版本分布、在线时间、CPU、内存、设备版本与清单、电源/模块、TCAM 利用率与明细。
在线时间用组内表格呈现完整设备名与时长，避免大量 stat 卡片挤压文字。
顶部筛选按 Manufacturer → Device type → Device 联动；选择 Device=All 时展开当前可选设备，
所以其他运维面板也遵循厂商/类型范围。接口相关面板仍放在最后。

`aiops_device_classification_info` 单独发布资产分类，不改变既有指标的标签。
厂商优先使用 Nautobot Device Type 的 Manufacturer，缺失时使用已知采集平台映射；
交换机/防火墙按资产角色和已知平台识别。未知平台/角色进入 Unknown / Other，不猜测型号。
分类在 Agent 初始化时即发布，认证失败的设备也保留在类别中，不与在线状态混淆。
更新单台分类会替换该设备旧的分类标签。

Grafana 使用隐藏的 hardware_group 变量重复面板，避免把厂商名称写死到面板或设备名单。
每个面板内的版本占比以当前组所选设备为分母。不同组独立计算，不混算。
历史资源曲线用所选时间终点的资产分类过滤；早于此指标首次上线的历史终点没有分类数据。
无 TCAM / 电源等可选指标时保留 No data；未安装的虚拟电源显示 N/A (not installed)。

TCAM 区域另有双语「采样状态」表，展示资源观测数、越界百分比数、Prometheus
最新抓取时间及设备采集状态。抓取时间不是设备采集时间，不能据此证明设备刚完成采集。
当前 CLI 周期性采集与页面 30 秒刷新独立。没有资源观测时不推断占用为零或设备离线。
利用率图只显示 0–100% 的观测，越界值保留在资源明细，并单独计数；不会截断成 100%。
Agent 抓取失败或设备最近采集失败时不展示当前资源值，历史缺口保留。
这属于展示与状态说明修复，未修改底层解析器；部分容量列解析的异常仍需进一步核对。

更新分组配置：

```sh
python scripts/grafana/group_hardware.py
python scripts/grafana/build_overview_languages.py
python scripts/grafana/build_overview_languages.py --check
python scripts/grafana/build_report.py --check
```

双语汇报视图的生成器按变量名读取设备选择器，保持其独立筛选，避免依赖运维专用变量。
修改清单加载与指标需要重启 Agent；Grafana 的面板 JSON 通过文件 provisioner 自动加载。
