# 电源与模块序列号

Power and Module Status 使用 `aiops_hardware_component_status`。
电源状态来自 `show environment power`，模块状态来自 `show module`；
当状态命令不提供 SN 时，复用同一轮的 `show inventory` 按 **部件类型＋槽位** 补齐。
不新增定时采集命令，也不改变 Nautobot 清单来源。

- EOS：按 System information / power supplies / fan / ports / transceiver 段落区分部件。
- NX-OS：按 NAME、PID、SN 记录区分 chassis、module/slot、power supply。
- 部件直接返回的 SN 优先；型号不符、槽位冲突、多个不同 SN 不猜测，保留 unknown。
- 风扇、光模块的槽位即使与电源相同，也不能用于补电源 SN。
- 虚拟 EOS 明确报告无电源时，model/serial 为 not_applicable，不冒用机箱 SN。
- 只有清单、没有可用状态时，模块/电源健康状态为 unknown。
- 备用模块解析忽略 MAC/HW/SW 表；电源解析识别状态列，不把电流或 uptime 当作状态。

成功发布部件观察时，清理同设备、同部件类型的旧标签组合；失败的整轮采集保留上次观察。
历史 Prometheus 数据不删除，面板即时查询显示当前观察。重复发布不会累加重复行。
PAN-OS 使用独立的 `show system info` 硬件解析，不把虚拟防火墙伪装成有实体电源的设备。

自动测试全部使用合成输出。此次用户明确扩大排查范围，允许对 lab 和非 lab 全部设备
执行硬件只读检查；该次授权不改变日后测试默认限于 lab 的仓库规则。

```sh
PYTHONPATH=agent python -m unittest discover -s agent/tests -v
```
