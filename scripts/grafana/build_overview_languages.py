#!/usr/bin/env python3
"""Localize the operations dashboard without changing queries, IDs or field keys.

The existing overview remains the canonical dashboard and Chinese entry point.
English is generated from it. Translation is idempotent in either direction.
"""
import argparse
import copy
import json
from pathlib import Path
from add_config_drift import inject

ROOT=Path(__file__).resolve().parents[2]
TITLES='''9001|网络健康评估分 · 100|Network health points · 100
9002|评分证据覆盖率|Scoring evidence coverage
9003|已观察问题扣分|Observed health deductions
9004|缺证暂未获得分数|Unassessed health points
9005|设备健康评估分|Device health points
9006|健康评分明细与范围|Health scoring details and scope
104|软件版本分布|Software Version Share
18|软件版本分布 · $hardware_group|Software Version Share · $hardware_group
200|设备在线时长|Device Uptime
19|设备在线时长 · $hardware_group|Device Uptime · $hardware_group
201|CPU 利用率|CPU Utilization
20|CPU 利用率 · $hardware_group|CPU Utilization · $hardware_group
202|内存利用率|Memory Utilization
21|内存利用率 · $hardware_group|Memory Utilization · $hardware_group
203|设备版本与资产信息|Device Version and Inventory
16|设备版本与资产信息 · $hardware_group|Device Version and Inventory · $hardware_group
204|电源与模块状态|Power and Module Status
17|电源与模块状态 · $hardware_group|Power and Module Status · $hardware_group
106|TCAM 利用率|TCAM Utilization
25|TCAM 利用率 · $hardware_group|TCAM Utilization · $hardware_group
205|TCAM 资源明细|TCAM Resource Details
26|TCAM 资源明细 · $hardware_group|TCAM Resource Details · $hardware_group
100|设备健康状态|Device Health
1|不可达设备数|Device Down
2|采集失败设备数|Collection Failures
107|不可达设备明细|Devices Down
110|防火墙|Firewall
27|防火墙活跃会话|Firewall Active Sessions
28|防火墙会话建立速率|Firewall Session Setup Rate
29|防火墙吞吐|Firewall Throughput
30|防火墙活跃会话趋势|Firewall Active Sessions Trend
31|防火墙会话建立速率趋势|Firewall Session Setup Rate Trend
32|防火墙吞吐趋势|Firewall Throughput Trend
33|防火墙 CPU 利用率|Firewall CPU Utilization
34|防火墙内存利用率|Firewall Memory Utilization
35|防火墙采集状态|Firewall Collection Status
102|OSPF 底层网络|OSPF Underlay
5|底层 OSPF|Underlay OSPF
11|底层 OSPF 邻居|Underlay OSPF Neighbors
22|按 VRF 与来源统计路由|IP Route Summary by VRF and Source
23|IP 路由规模趋势|IP Route Summary Trend
105|VXLAN 覆盖网络|Overlay VXLAN
6|VXLAN 覆盖网络|Overlay VXLAN
12|VXLAN 对端|Overlay VXLAN Peers
103|BGP|BGP
4|BGP 异常|BGP Problems
13|BGP 邻居|BGP Neighbors
14|BGP 汇总字段|BGP Summary Fields
15|BGP 路由 / NLRI 接收与发送|BGP Routes/NLRI Received vs Advertised
109|拓扑与路径查询|Topology and Path Query
108|拓扑与 A 到 B 路径查询|Topology and A to B Path Query
101|接口视图|Interface View
3|接口异常|Interface Problems
7|接口流量|Interface Traffic
8|接口运行状态|Interface Operational State
24|接口错误与丢弃 - $device|Interface Errors and Discards - $device
9200|Palo Alto · 防火墙接口流量|Palo Alto · Firewall Interface Traffic
9201|Palo Alto · 接口接收流量|Palo Alto · Interface RX
9202|Palo Alto · 接口发送流量|Palo Alto · Interface TX'''
FIELDS='''设备|Device
设备数|Devices
管理地址|Host
角色|Role
站点|Site
软件版本|Software version
占比|Share
在线时长|Uptime
架构|Architecture
硬件版本|HW Version
型号|Model
序列号|Serial
健康状态|Health
类型|Type
名称|Name
状态|Status
数值|Value
来源|Source
指标|Metric
区域|Region
资源|Resource
防火墙|Firewall
可达性|Reachability
采集|Collection
在线|Up
邻居|Neighbor
邻居状态|State
路由数|Routes
路由来源|Route Source
对端 IP|Peer IP
对端设备名|Peer Hostname
BGP 状态|BGP State
地址族状态|AFI/SAFI State
会话状态|Session State
运行状态|Oper
接口|Interface
输入错误|Input Errors
对齐错误|Alignment
符号错误|Symbol
输入丢弃|Input Discards
输出错误|Output Errors
碰撞|Collisions
晚碰撞|Late Collisions
延迟发送|Deferred
输出丢弃|Output Discards
健康分|Health points
覆盖率|Coverage
暂无数据|No data
不适用（未安装）|N/A (not installed)
失败|Failed
可达|Reachable
成功|Successful
不可达|Unreachable
严重|critical
警告|warning
正常|ok
已连通|up
未连通|down
BGP 未建立|BGP down
OSPF 完全邻接|OSPF full
VXLAN 对端数|VXLAN peers
管理启用 / 运行未连通|admin up / oper down
采集失败|collect failed
设备不可达|device down'''


def build(source,lang):
    d=copy.deepcopy(source)
    pairs=[line.split('|') for line in FIELDS.splitlines()]
    aliases={text:pair[0 if lang=='zh' else 1] for pair in pairs for text in pair}
    aliases['Software Version']=aliases['Software version']
    # Do not translate PromQL, raw field names, variable values or inventory labels.
    def text(value):return aliases.get(value,value)
    def ui_walk(value):
        if isinstance(value,dict):
            for key,item in value.items():
                if key in {'text','displayName','noValue','legendFormat'} and isinstance(item,str):value[key]=text(item)
                elif isinstance(item,(dict,list)):ui_walk(item)
        elif isinstance(value,list):
            for item in value:ui_walk(item)
    titles={int(k):(zh,en) for k,zh,en in (line.split('|') for line in TITLES.splitlines())}
    def panel(p):
        if p['id'] in titles:p['title']=titles[p['id']][lang=='en']
        ui_walk(p)
        for t in p.get('transformations',[]):
            if t['id']=='organize':
                t['options']['renameByName']={k:text(v) for k,v in t['options'].get('renameByName',{}).items()}
        for override in p.get('fieldConfig',{}).get('overrides',[]):
            matcher=override.get('matcher',{})
            if matcher.get('id')=='byName':matcher['options']=text(matcher['options'])
            elif matcher.get('id')=='byRegexp' and p['id']==24:
                matcher['options']='/('+'|'.join(text(x) for x in ['Input Errors','CRC','Alignment','Symbol','Input Discards','Output Errors','Collisions','Late Collisions','Deferred','Output Discards'])+')/'
        for child in p.get('panels',[]):panel(child)
    for p in d['panels']:panel(p)
    choose=lambda zh,en:zh if lang=='zh' else en
    byid={p['id']:p for p in d['panels']}
    health=choose('规则评分，不是 SLA。未知项暂不计分，请同时看证据覆盖率和缺证分数；点击“健康评分明细”查看原因。',
                  'Rule-based score, not an SLA. Missing evidence earns no points. Read evidence coverage and unassessed points together; open Health details for the reasons.')
    for i in range(9001,9006):byid[i]['description']=health
    byid[9006]['options']['content']=choose(
        '**100 为最高。** 采集 40%、已确认链路 20%、CPU/内存 20%、协议 10%、硬件 10%。缺失或过期数据暂不计分；缺证不等于故障。排除项重新分配权重，全网按设备等权平均。\n\n[打开健康评分明细 ↗](http://192.168.20.169:8080/health)\n\n当前评分不覆盖时延/丢包、业务探测和实际转发。',
        '**100 is the maximum.** Collection 40%, confirmed links 20%, CPU/memory 20%, peers 10%, hardware 10%. Missing or stale evidence earns no points; it is not a fault. Excluded weights are redistributed; devices have equal weight.\n\n[Open health details ↗](http://192.168.20.169:8080/health?lang=en)\n\nExcludes latency/loss, application probes and packet delivery.')
    hardware=choose('按厂商 → 设备类型（交换机 / 防火墙）分组，明细保留设备名与型号。可用厂商、设备类型、设备筛选缩小范围。没有数据表示本组缺少该指标观察，不表示零或健康。历史曲线采用所选时间终点的资产分类。',
                    'Grouped by manufacturer → device type (Switch / Firewall); device names and models remain visible. Use manufacturer, device type and device filters. Missing data is not zero or healthy. Historical curves use inventory classification at the selected end time.')
    for i in [18,19,20,21,16,17,25,26]:byid[i]['description']=hardware
    for i in [27,28,29]:byid[i]['description']=choose('按防火墙展示最近成功采集的值。零表示设备上报零，缺少采集则留空。支持设备筛选，不受接口筛选影响。',
        'Latest successful observation per firewall. Reported zero is a value; missing collection stays empty. Respects device selection, not interface selection.')
    for i in [30,31,32]:byid[i]['description']=choose('逐台防火墙展示，采集失败时留空。支持设备筛选。同一流量可能被多台设备观察，不跨设备累加吞吐。',
        'Per-firewall observations with gaps on failed collection. Respects device selection. Do not sum throughput across devices that may observe the same traffic.')
    for i in [33,34]:byid[i]['description']=choose('按防火墙展示，采集失败时留空。PAN-OS 的 CPU / 内存反映管理平面，不是数据平面利用率。',
        'Per-firewall observations with gaps on failure. PAN-OS CPU and memory reflect the management plane, not dataplane utilization.')
    byid[35]['description']=choose('即使防火墙指标暂不可用，仍展示可达性与采集状态。没有数据不等于健康。',
        'Shows reachability and collection even when firewall metrics are unavailable. Missing data is not healthy.')
    byid[108]['description']=choose('Q2 归一化拓扑与路径查询','Q2 normalized topology and path lookup')
    byid[108]['options']['content']=choose(
        '**Q2 拓扑工具**\n\n- [交互式物理拓扑](http://192.168.20.169:8080/topology)\n- [A 到 B 路径查询](http://192.168.20.169:8080/tools)\n- [拓扑 JSON API](http://192.168.20.169:8080/api/topology)\n- [拓扑异常](http://192.168.20.169:8080/api/topology/anomalies)',
        '**Q2 topology tools**\n\n- [Interactive physical topology](http://192.168.20.169:8080/topology)\n- [A to B path query](http://192.168.20.169:8080/tools)\n- [Topology JSON API](http://192.168.20.169:8080/api/topology)\n- [Topology anomalies](http://192.168.20.169:8080/api/topology/anomalies)')
    for i in [9201,9202]:byid[i]['description']=choose('每 60 秒采样字节计数，按相邻样本计算 bit/s。首次需两个样本；失败或超过 180 秒无数据时留空。hardware 为物理/聚合口，logical 为子接口；同一流量可能重复出现，不应相加。当前覆盖 Nautobot lab 中的 PA。',
        'Samples byte counters every 60 seconds and calculates bit/s from consecutive samples. Requires two initial samples; failed or expired (180 seconds) observations are absent. Hardware means physical/aggregate ports; logical means subinterfaces. Layers overlap and must not be added. Covers lab PA devices.')
    labels={'manufacturer':('厂商','Manufacturer'),'device_type':('设备类型','Device type'),'device':('设备','Device'),
            'interface':('接口','Interface'),'hardware_group':('硬件分组','Hardware group')}
    for v in d['templating']['list']:v['label']=labels[v['name']][lang=='en']
    d['title']=choose('中文 · 网络运维总览','English · Network Operations Overview')
    d['uid']='eve-aiops-overview'+('-en' if lang=='en' else '')
    d['id']=None
    d['tags']=list(dict.fromkeys(d.get('tags',[])+['aiops-ops-language']))
    d['links']=[{'title':'语言 / Language','type':'dashboards','tags':['aiops-ops-language'],'asDropdown':True,
                 'includeVars':True,'keepTime':True,'targetBlank':False},
                {'title':choose('接口流量明细 ↗','Interface traffic details ↗'),'type':'link','url':'http://192.168.20.169:8080/mrtg','targetBlank':True},
                {'title':choose('网络运行简报 ↗','Operations report ↗'),'type':'link','url':'/d/eve-aiops-report'+('-en' if lang=='en' else '')+'/report',
                 'includeVars':True,'keepTime':True,'targetBlank':False}]
    return inject(d,lang)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--check',action='store_true');args=parser.parse_args()
    path=ROOT/'grafana/dashboards/aiops-overview.json';source=json.loads(path.read_text())
    for lang,name in [('zh','aiops-overview.json'),('en','aiops-overview-en.json')]:
        output=ROOT/'grafana/dashboards'/name
        content=json.dumps(build(source,lang),ensure_ascii=False,indent=2)+'\n'
        if args.check:
            if not output.exists() or output.read_text()!=content:raise SystemExit('Generated file differs: '+name)
        else:output.write_text(content)
        print(name)


if __name__=='__main__':main()
