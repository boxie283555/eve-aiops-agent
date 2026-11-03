# Nautobot NAPALM 与接口同步

## 使用范围

NAPALM 只为当前 Site/Location 为 `lab` 的设备启用。lab 专用平台使用
`network_driver=arista_eos` 或 `paloalto_panos`，供 Agent 继续选择正确采集器；
`napalm_driver` 单独指定 Nautobot 的实时查询驱动。原共享平台不修改，非 lab
设备不加入 NAPALM 主动验证。现有定时采集及异步同步仍覆盖原有设备。

lab 设备具有 Primary IPv4、管理接口 IP 关联及 Secrets Group。新增的
`lab-management` Namespace 使用 /32 管理主机记录供连接定位，不表示设备实际
管理子网掩码，更不会修改设备配置。凭据存放在服务器 `nautobot/.env.napalm`
中，由环境变量 Secret provider 读取；数据库只保存变量名，Git 中不包含密码。

## 部署

服务器现有目录为 `/opt/eve-aiops-agent/nautobot`。新环境先将根目录的
`docker-compose.nautobot.yml` 复制为该目录的 `docker-compose.yml`，并配置 `.env`。
准备权限为 600 的 `.env.napalm`，提供四个变量：

- `NAPALM_LAB_EOS_USERNAME` / `NAPALM_LAB_EOS_PASSWORD`
- `NAPALM_LAB_PANOS_USERNAME` / `NAPALM_LAB_PANOS_PASSWORD`

随后运行：

```sh
cd /opt/eve-aiops-agent/nautobot
docker compose --env-file .env build nautobot
docker compose --env-file .env up -d --no-deps nautobot nautobot-worker
docker exec -i eve-aiops-nautobot nautobot-server shell \
  < ../scripts/nautobot/enable_lab_napalm.py
```

`docker-compose.override.yml` 会自动加载自定义镜像和凭据环境。
初始化脚本只修改 lab 设备，拒绝修改被非 lab 设备使用的平台，在数据库事务中
创建管理地址、平台和凭据关联；不连接网络设备。原设备关联保存在
`/opt/nautobot/media/lab-napalm-before.json`。

EOS 使用 SSH，不需要额外开启 eAPI。PAN-OS 使用已有 XML API；lab 自签证书
使用 `ssl_verify=false`。NAPALM 5.0.0 / napalm-panos 0.7.0 固定在镜像中。
lab 兼容驱动补充 vEOS-lab 型号识别与 PAN-OS 环境查询：CPU、内存来自设备
实际输出，PA 返回管理平面资源；虚拟设备不存在的电源、风扇、温度返回空字典。
解析不到数据会明确报错，不伪造零使用率或健康硬件。

设备页 Status、LLDP Neighbors、Configuration 可实时查询。NAPALM 读取不会
替换 Agent 的定时采集，也不会自动将配置写回设备。Configuration 是运行配置
读取，不代表已完成 Q3 的备份、diff 和漂移检测。

## 接口同步

- 在独立后台任务中执行，不等待写回完成才开始下一轮采集。
- 按设备分页读取接口，每页 100 条；每次批量 PATCH 最多 50 条。
- 最多两个设备并发同步，单请求超时至少 30 秒；幂等 PATCH 失败后重试一次。
- 单设备失败不会阻止其他设备同步；API 和日志保留部分成功数量及失败设备。
- Nautobot 重启等短暂故障后，下一轮正常采集会自动重新同步；无需触发设备测试。
- 接口名称按规范名匹配，避免 PA 大小写差异产生重复记录。
- `aiops_last_collected` 使用实际设备采集时间；失败时只写错误，不更新最后成功
  时间，也不把未知状态写成 down。只提交采集器拥有的自定义字段。

同步状态：`GET http://192.168.20.169:8080/api/nautobot/sync`。
`status=ok` 表示该轮接口写回成功；采集数据是否新鲜仍需查看各接口时间及错误字段。
状态 API 的时间为 Unix 秒，接口自定义字段时间为 ISO 8601。

Prometheus 指标：

- `aiops_nautobot_sync_success`
- `aiops_nautobot_sync_last_success_timestamp_seconds`
- `aiops_nautobot_sync_duration_seconds`
- `aiops_nautobot_sync_updated_interfaces`

可在 Nautobot 的 Interfaces → 接口详情 → Custom Fields 查看接口状态、IP、采集时间及错误。

## 回退与验证

部署前数据库及配置备份位于
`/opt/eve-aiops-backups/napalm-sync-20261030/`；数据库备份仅用于灾难恢复，正常回退
应按 `lab-napalm-before.json` 恢复指定 lab 设备关联，避免覆盖其他最新数据。
删除或禁用新平台关联前应先恢复设备原平台、Primary IP、Secrets Group。

离线测试只使用 lab 样本及合成数据。线上验收仅对 lab 设备发起 NAPALM 请求，
其他设备只查看正常采集的汇总健康状态。

## 批量编辑 Pending 与后台任务

批量编辑由 Celery worker 异步执行。部署须同时运行 `nautobot` 网页服务和
`nautobot-worker` 后台服务，并使用相同数据库、Redis、镜像及 media 卷。
worker 启动跳过数据库初始化和超级用户重置，默认并发 2，自动重启并带 ping 健康检查。

若 Job 一直 Pending，先检查 worker 是否运行和队列是否有消费者；不要反复提交
相同的编辑。补齐 worker 后，原来已排队的任务会继续执行，无需重新提交。
在 Nautobot 的 Job Results 中确认 Success，再检查设备实际状态；Agent 会在下一轮
清单热加载后应用。worker 不需要为本功能连接任何网络设备。

2026-11-03 修复时，线上缺少 worker，队列中有 1 个批量编辑任务和 18 个自定义字段
初始化任务；修复保留原队列，不删除或伪造任务状态。部署前备份数据库、队列及 Compose 文件。

验收结果：原任务 `e48793ed-fe99-4205-80ce-f5d136fa710f` 于 2026-11-03
11:41:20 CST 成功完成，原队列清空，worker 健康检查通过。用户选中的 8 台设备
变为 Offline，Agent 热加载后显示 8 台暂停采集/排除评分、10 台继续采集/评分；
无需重启 Agent 或重新提交编辑。此任务同时关闭了 Enable collection 并开启了
Exclude from health，因此恢复设备时需同时检查这两个自定义字段。
