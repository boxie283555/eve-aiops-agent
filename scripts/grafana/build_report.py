#!/usr/bin/env python3
"""Generate the Chinese and English reports from one definition (stdlib only)."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DS = {'type': 'prometheus', 'uid': 'Prometheus'}
GATE = ' and on() (up{job="eve-aiops-agent"} == 1)'
SELECT = 'device=~"$device"'

def raw(name, extra=''):
    return name + '{' + SELECT + (',' + extra if extra else '') + '}'

def metric(name, extra=''):
    # Optional observations retained by the exporter after a failed poll are not current facts.
    return '(' + raw(name, extra) + ' and on(device) (' + raw('aiops_collect_success') + ' == 1))'

def capacity(suffix, valid=True):
    expr = '(' + metric('aiops_telemetry_asic_tcam_resource_' + suffix) + ' or on(device,resource,region) ' + metric('aiops_asic_tcam_resource_' + suffix) + ')'
    return '(' + expr + ' >= 0) <= 100' if valid and suffix == 'utilization_percent' else expr

def count_devices(expr):
    return 'count(count by(device) (' + expr + '))'

def build(lang):
    def tr(zh, en): return zh if lang == 'zh' else en
    panels = []
    y = 0
    def target(expr, ref='A', legend='', instant=True, fmt='time_series'):
        return dict(datasource=DS, expr='(' + expr + ')' + GATE, refId=ref, legendFormat=legend,
                    instant=instant, range=not instant, format=fmt)
    def panel(title, kind, x, y, w, h, description='', targets=None):
        p = dict(id=len(panels)+1, title=title, type=kind, gridPos=dict(x=x,y=y,w=w,h=h),
                 description=description, fieldConfig={'defaults': {'noValue':tr('暂无数据','No data'),'mappings':[]},'overrides':[]}, options={})
        if targets is not None: p.update(datasource=DS,targets=targets)
        panels.append(p)
        return p
    def text(title, content, y, h=3):
        p=panel(title,'text',0,y,24,h);p['options']={'mode':'markdown','content':content};return p
    def row(title, y):
        p=panel(title,'row',0,y,24,1);p.update(collapsed=False,panels=[]);return p
    def thresholds(warn=80, bad=95):
        return {'mode':'absolute','steps':[{'value':None,'color':'green'},{'value':warn,'color':'orange'},{'value':bad,'color':'red'}]}
    def stat(title, expr, x, y, w=6, unit='short', desc='', attention=False):
        p=panel(title,'stat',x,y,w,4,desc,[target(expr)])
        p['fieldConfig']['defaults'].update(unit=unit,decimals=0,color={'mode':'fixed','fixedColor':'blue'})
        if attention:p['fieldConfig']['defaults'].update(color={'mode':'thresholds'},thresholds=thresholds(1,1))
        p['options']={'reduceOptions':{'calcs':['lastNotNull'],'fields':'','values':False},'orientation':'horizontal','textMode':'value','colorMode':'value','graphMode':'none','justifyMode':'center','wideLayout':True}
        return p
    def bars(title, expr, legend, x, y, w=12, h=9, unit='short', desc='', percent=False):
        p=panel(title,'bargauge',x,y,w,h,desc,[target(expr,legend=legend)])
        defaults=p['fieldConfig']['defaults'];defaults.update(unit=unit,decimals=1 if percent else 0,min=0,displayName=legend,color={'mode':'fixed','fixedColor':'blue'})
        if percent:defaults.update(max=100,color={'mode':'thresholds'},thresholds=thresholds())
        p['options']={'orientation':'horizontal','displayMode':'basic','showUnfilled':False,'valueMode':'color','namePlacement':'top','reduceOptions':{'calcs':['lastNotNull'],'fields':'','values':False},'sizing':'auto','minVizHeight':16,'minVizWidth':8,'text':{'titleSize':13,'valueSize':18}}
        return p
    def trend(title, queries, x, y, w=12, h=8, unit='short', desc=''):
        p=panel(title,'timeseries',x,y,w,h,desc,[target(q,chr(65+i),name,False) for i,(name,q) in enumerate(queries)])
        p['fieldConfig']['defaults'].pop('noValue')
        p['fieldConfig']['defaults'].update(unit=unit,min=0,color={'mode':'palette-classic'},custom={'drawStyle':'line','lineInterpolation':'linear','lineWidth':2,'fillOpacity':10,'spanNulls':False,'showPoints':'never','axisPlacement':'auto','axisBorderShow':False,'scaleDistribution':{'type':'linear'},'stacking':{'mode':'none','group':'A'},'hideFrom':{'legend':False,'tooltip':False,'viz':False}})
        p['options']={'legend':{'displayMode':'list','placement':'bottom','showLegend':True},'tooltip':{'mode':'multi','sort':'desc'}}
        return p
    def table(title, queries, key, fields, x,y,w=24,h=9,desc=''):
        p=panel(title,'table',x,y,w,h,desc,[target(q,chr(65+i),fmt='table') for i,q in enumerate(queries)])
        transforms=[{'id':'filterFieldsByName','options':{'exclude':{'names':['Time']}}}]
        if len(queries)>1:transforms.append({'id':'joinByField','options':{'byField':key,'mode':'outerTabular'}})
        transforms.extend([{'id':'filterFieldsByName','options':{'include':{'names':list(fields)}}},{'id':'organize','options':{'renameByName':fields,'indexByName':{n:i for i,n in enumerate(fields)}}}])
        p['transformations']=transforms
        p['options']={'showHeader':True,'cellHeight':'sm','footer':{'show':False}}
        p['fieldConfig']['defaults']['custom']={'minWidth':75}
        return p
    def field(p,name,props):
        p['fieldConfig']['overrides'].append({'matcher':{'id':'byName','options':name},'properties':[{'id':k,'value':v} for k,v in props.items()]})
    def label(expr,key,value):
        return 'label_replace(('+expr+'), '+json.dumps(key)+', '+json.dumps(value,ensure_ascii=False)+', "", "")'
    inventory=raw('aiops_device_up')
    missing='(0 * count('+inventory+') - 1)'
    def available_or_unknown(q):return '('+q+') or on() '+missing
    def coverage(q):return '('+count_devices(q)+') or on() (0 * count('+inventory+'))'

    text(tr('网络运行简报','Network operations report'),tr(
        '### 先看结论，再看依据\n顶部 **语言 / Language** 可切换中英文。摘要随设备范围和时间更新；卡片为所选时间终点的最近采集结果，趋势为所选时段。设备可达性不是业务 SLA。',
        '### Findings first. Evidence below.\nUse **语言 / Language** at the top to switch languages. The summary follows the device and time selections. Cards show the latest observations at the selected end time; charts cover the selected period. Reachability is not a business SLA.'),0,3)
    stat(tr('在线设备','Reachable devices'),'sum('+inventory+')',0,3)
    stat(tr('不可达设备','Unreachable devices'),'count('+inventory+') - sum('+inventory+')',6,3,attention=True)
    p=stat(tr('最近采集成功率','Latest collection success'),'100 * sum('+raw('aiops_collect_success')+') / count('+raw('aiops_collect_success')+')',12,3,unit='percent')
    p['fieldConfig']['defaults'].update(color={'mode':'thresholds'},thresholds={'mode':'absolute','steps':[{'value':None,'color':'red'},{'value':90,'color':'orange'},{'value':100,'color':'green'}]})
    p=stat(tr('资产同步距今 · 全局','Inventory sync age · global'),'time() - (aiops_nautobot_sync_last_success_timestamp_seconds > 0)',18,3,unit='s',desc=tr('全局指标，不随设备筛选变化。5 分钟提醒、15 分钟标红。','Global, independent of device selection. Review after 5 minutes; red after 15.'))
    p['fieldConfig']['defaults'].update(color={'mode':'thresholds'},thresholds=thresholds(300,900))

    # Summaries count distinct endpoints/components, not changing status-label series.
    bgp='min by(device,neighbor,vrf) ('+metric('aiops_bgp_neighbor_up')+')'
    ospf='min by(device,neighbor) ('+metric('aiops_ospf_neighbor_up')+')'
    nve='min by(device,peer) ('+metric('aiops_nve_peer_up')+')'
    hw=metric('aiops_hardware_component_status')
    hw_bad=metric('aiops_hardware_component_status','status=~"(?i:fail|failed|down|error|fault|not_ok|offline|power_loss)"')
    known='(?i:ok|present|up|online|active|normal|operational|good|fail|failed|down|error|fault|not_ok|offline|power_loss|unsupported|not_present|not_installed|no_power_supplies|unknown)'
    hw_unknown=metric('aiops_hardware_component_status','status!~"'+known+'"')
    cpu=metric('aiops_device_cpu_utilization_percent');memory=metric('aiops_device_memory_utilization_percent')
    interfaces=metric('aiops_interface_oper_up')
    interface_review='sum(('+metric('aiops_interface_admin_up')+' == bool 1) * on(device,interface) ('+interfaces+' == bool 0))'
    summaries=[
        (tr('设备可达性','Device reachability'),'count('+inventory+')','sum(1 - '+inventory+')',tr('单位：设备；不可达需核查连接','Devices; review unreachable endpoints')),
        (tr('设备采集','Device collection'),'count('+raw('aiops_collect_success')+')','sum(1 - '+raw('aiops_collect_success')+')',tr('单位：设备；失败会影响以下指标覆盖','Devices; failures reduce coverage below')),
        ('CPU','count('+cpu+')','sum('+cpu+' >= bool 80)',tr('单位：设备；使用率 ≥ 80%','Devices with usage ≥ 80%')),
        (tr('内存','Memory'),'count('+memory+')','sum('+memory+' >= bool 80)',tr('单位：设备；使用率 ≥ 80%','Devices with usage ≥ 80%')),
        ('BGP','count('+bgp+')','sum(1 - ('+bgp+'))',tr('单位：邻居；未建立需核查','Neighbor observations; review non-established states')),
        ('OSPF','count('+ospf+')','sum(1 - ('+ospf+'))',tr('单位：邻居；无数据不表示正常','Neighbor observations; no data is not healthy')),
        ('VXLAN','count('+nve+')','sum(1 - ('+nve+'))',tr('单位：对端观察；不是物理链路数','Peer observations, not physical link counts')),
        (tr('硬件部件','Hardware components'),'count(count by(device,component_type,name) ('+hw+'))','count(count by(device,component_type,name) ('+hw_bad+')) or on() (0 * count('+hw+'))',tr('单位：部件；明确异常状态，需人工确认','Components with explicit adverse labels; confirm manually')),
        (tr('接口','Interfaces'),'count('+interfaces+')',interface_review,tr('单位：接口；启用未连通，可能为空闲口','Admin-up / oper-down; may include unused ports')),
    ]
    qs=[]
    for col in (1,2):
        qs.append(' or '.join((label(label(available_or_unknown(item[col]),'topic',item[0]),'meaning',item[3]) if col == 1 else label(available_or_unknown(item[col]),'topic',item[0])) for item in summaries))
    fields={'topic':tr('领域','Area'),'meaning':tr('口径与结论','Interpretation'),'Value #A':tr('观测数量','Observed'),'Value #B':tr('待核查数量','Review')}
    p=table(tr('运行总结 · 根据采集数据自动汇总','Executive summary · calculated from observations'),qs,'topic',fields,0,7,h=10,
            desc=tr('各行单位不同，不可相加。暂无数据表示本次无可用指标，不表示该功能未配置或正常。','Rows have different units and must not be added. No data does not prove a feature is unconfigured or healthy.'))
    for n in [fields['Value #A'],fields['Value #B']]:field(p,n,{'custom.width':125,'decimals':0,'mappings':[{'type':'value','options':{'-1':{'text':tr('暂无数据','No data'),'color':'gray'}}}]})
    field(p,fields['topic'],{'custom.width':185})
    field(p,fields['Value #B'],{'color':{'mode':'thresholds'},'thresholds':thresholds(1,1),'custom.cellOptions':{'type':'color-text'}})
    trend(tr('设备状态趋势','Device reachability trend'),[(tr('在线','Reachable'),'sum('+inventory+')'),(tr('不可达','Unreachable'),'sum(1 - '+inventory+')')],0,17)
    bars(tr('软件版本分布 · Software Version Share','Software Version Share'),
         'sort_desc(count by(version)(label_replace('+metric('aiops_device_hardware_info')+', "version", "'+tr('未识别','Unknown')+'", "version", "unknown|^$")))',
         '${__field.labels.version}',12,17,h=8,unit=tr('suffix: 台','suffix: devices'))

    row(tr('01 · 采集覆盖与数据可信度','01 · Coverage and data confidence'),25)
    groups=[('CPU',cpu),(tr('内存','Memory'),memory),(tr('硬件资产 / 版本','Hardware / version'),metric('aiops_device_hardware_info')),(tr('运行时长','Uptime'),metric('aiops_device_uptime_seconds')),
            (tr('硬件部件','Components'),hw),('ASIC / TCAM',capacity('utilization_percent')),('BGP',bgp),('OSPF',ospf),('VXLAN',nve),
            (tr('路由规模','Route counts'),metric('aiops_route_summary_routes','source="Total Routes"')),(tr('防火墙','Firewalls'),metric('aiops_firewall_sessions_active')),
            (tr('接口状态','Interface state'),interfaces),(tr('接口流量','Interface traffic'),metric('aiops_interface_rx_bps')),(tr('接口错误','Interface errors'),metric('aiops_interface_input_errors'))]
    q=' or '.join(label(coverage(m),'category',name) for name,m in groups)
    p=table(tr('各类指标覆盖设备数','Reporting devices by metric family'),[q],'category',{'category':tr('指标类别','Metric family'),'Value':tr('提供数据的设备数','Reporting devices')},0,26,12,11,desc=tr('0 表示没有当前可用序列，不等于采集失败；有些能力仅适用于部分设备。最近采集失败的设备不计入覆盖。','Zero means no currently usable series, not necessarily a collection failure. Some features apply only to certain devices. Failed latest polls are excluded.'))
    text_content=tr('### 如何读这份报告\n- **摘要不是故障定论**：按明确口径筛出待核查对象。\n- **缺失不补零**：无可用数据标为暂无数据；覆盖表中的 0 表示未收到该类指标。\n- **时效**：采集端点可访问不代表内部采集一直在更新；结合全局同步时效判断。\n- **硬件**：不认识的状态标签只计为数据质量问题；已识别异常也需确认。\n- **路由 / 流量**：按设备、VRF 或接口呈现，不把重复观察相加成全网业务总量。\n- **历史**：趋势只代表已有样本，不推导业务 SLA。',
                    '### How to read this report\n- **Review is not an incident verdict:** counts use explicit rules.\n- **Missing is not zero:** unavailable observations stay unknown; coverage zero means no usable series.\n- **Freshness:** a reachable exporter does not prove collection is advancing; check global sync age.\n- **Hardware:** unrecognized labels are data-quality findings; adverse labels need confirmation.\n- **Routes / traffic:** per device, VRF or interface; repeated observations are not global business totals.\n- **History:** trends describe observed samples, not a business SLA.')
    p=panel(tr('总结口径','Summary methodology'),'text',12,26,12,7);p['options']={'mode':'markdown','content':text_content}
    stat(tr('硬件状态待归类 · 部件','Unclassified hardware labels · components'),'count(count by(device,component_type,name) ('+hw_unknown+')) or on() (0 * count('+hw+'))',12,33,w=4,attention=True)
    route_allowed='Total Routes|connected|static \\(persistent\\)|static \\(non-persistent\\)|VXLAN Control Service|static nexthop-group|ospf|ospfv3|bgp|isis|rip|internal|attached|aggregate|dynamic policy|gribi'
    # JSON encode the RE2 regex when putting it in a PromQL string.
    bad_routes=metric('aiops_route_summary_routes','source!~'+json.dumps(route_allowed))
    stat(tr('路由来源待归类 · 记录','Unclassified route sources · records'),'count('+bad_routes+') or on() (0 * count('+metric('aiops_route_summary_routes')+'))',16,33,w=4,attention=True)
    stat(tr('容量百分比无效 · 记录','Invalid capacity percentages · records'),'sum(('+capacity('utilization_percent',False)+' < bool 0) + ('+capacity('utilization_percent',False)+' > bool 100))',20,33,w=4,attention=True)
    stat(tr('最近同步结果 · 全局','Latest sync result · global'),'aiops_nautobot_sync_success',0,37)
    p=panels[-1];p['fieldConfig']['defaults']['mappings']=[{'type':'value','options':{'0':{'text':tr('未成功','Unsuccessful'),'color':'orange'},'1':{'text':tr('成功','Successful'),'color':'green'}}}]
    stat(tr('最近同步接口 · 全局','Interfaces in last sync · global'),'aiops_nautobot_sync_updated_interfaces',6,37)
    stat(tr('同步耗时 · 全局','Sync duration · global'),'aiops_nautobot_sync_duration_seconds',12,37,unit='s')
    stat(tr('最慢设备采集','Slowest device collection'),'max('+raw('aiops_collect_duration_seconds')+')',18,37,unit='s')

    row(tr('02 · 设备资产与资源','02 · Assets and capacity'),41)
    bars(tr('CPU 使用率 · 最高 5 台','CPU usage · top 5'),'topk(5,'+cpu+')','${__field.labels.device}',0,42,w=8,h=9,unit='percent',percent=True)
    bars(tr('内存使用率 · 最高 5 台','Memory usage · top 5'),'topk(5,'+memory+')','${__field.labels.device}',8,42,w=8,h=9,unit='percent',percent=True)
    bars(tr('硬件容量使用率 · 最高 5 项','ASIC / TCAM usage · top 5'),'topk(5,'+capacity('utilization_percent')+')','${__field.labels.device} · ${__field.labels.resource} · ${__field.labels.region}',16,42,w=8,h=9,unit='percent',percent=True)
    p=table(tr('设备清单 · 不可达优先','Device inventory · unreachable first'),['min by(device)('+inventory+')','min by(device)('+raw('aiops_collect_success')+')','max by(device)('+metric('aiops_device_uptime_seconds')+')'],'device',{'device':tr('设备','Device'),'Value #A':tr('可达状态','Reachability'),'Value #B':tr('采集状态','Collection'),'Value #C':tr('运行时长','Uptime')},0,51,12,10)
    inventory_series=[('reachability','min by(device)('+inventory+')'),('collection','min by(device)('+raw('aiops_collect_success')+')'),('uptime','max by(device)('+metric('aiops_device_uptime_seconds')+')')]
    p['targets']=[target(' or '.join(label(q,'measure',name) for name,q in inventory_series),fmt='table')]
    p['transformations']=[{'id':'groupingToMatrix','options':{'columnField':'measure','rowField':'device','valueField':'Value'}},{'id':'organize','options':{'renameByName':{'device\\measure':tr('设备','Device'),'reachability':tr('可达状态','Reachability'),'collection':tr('采集状态','Collection'),'uptime':tr('运行时长','Uptime')},'indexByName':{'device\\measure':0,'reachability':1,'collection':2,'uptime':3}}}]
    p['options']['sortBy']=[{'displayName':tr('可达状态','Reachability'),'desc':False},{'displayName':tr('采集状态','Collection'),'desc':False}]
    for name,zero,one in [(tr('可达状态','Reachability'),tr('不可达','Unreachable'),tr('在线','Reachable')),(tr('采集状态','Collection'),tr('失败','Failed'),tr('成功','Successful'))]:
        field(p,name,{'unit':'short','decimals':0,'mappings':[{'type':'value','options':{'0':{'text':zero,'color':'red'},'1':{'text':one,'color':'green'}}}],'custom.cellOptions':{'type':'color-text'}})
    field(p,tr('运行时长','Uptime'),{'unit':'s','decimals':0})
    table(tr('设备型号分布','Device model distribution'),['sort_desc(count by(model)(label_replace('+metric('aiops_device_hardware_info')+', "model", "'+tr('未识别','Unknown')+'", "model", "(?i:sw-version.*|unknown|^$)")))'],'model',{'model':tr('型号','Model'),'Value':tr('设备数','Devices')},12,51,12,10)
    # Aggregate only: do not present parser artifacts as known healthy components.
    table(tr('硬件部件 · 已识别状态汇总','Hardware · recognized status summary'),['count by(component_type,status) ('+metric('aiops_hardware_component_status','status=~"'+known+'"')+')'],'status',{'component_type':tr('部件类型','Component type'),'status':tr('采集状态标签','Observed status label'),'Value':tr('观察记录','Observations')},0,61,12,9,desc=tr('原始状态保留用于核查；同一部件可能保留多个历史标签，不作为物理部件总数。','Raw status labels are retained for review. A component may retain multiple historical labels; this is not a physical inventory count.'))
    table(tr('硬件容量 · 已用 / 可用 / 总量','ASIC / TCAM · used / free / total'),[label(capacity(s),'measure',tr(z,e)) for s,z,e in [('used_entries','已用','Used'),('free_entries','可用','Free'),('total_entries','总量','Total')]],'resource',{'device':tr('设备','Device'),'resource':tr('资源','Resource'),'region':tr('区域','Region'),'measure':tr('口径','Measure'),'Value':tr('条目数','Entries')},12,61,12,9)
    # Concatenate capacity measures, avoiding joins across non-unique resource names.
    cap=panels[-1];cap['targets']=[target(' or '.join(label(capacity(s),'measure',tr(z,e)) for s,z,e in [('used_entries','已用','Used'),('free_entries','可用','Free'),('total_entries','总量','Total')]),fmt='table')];cap['transformations']=[t for t in cap['transformations'] if t['id']!='joinByField']

    row(tr('03 · 路由与网络邻居','03 · Routing and network peers'),70)
    trend(tr('BGP 邻居状态','BGP neighbor state'),[(tr('已建立','Established'),'sum('+bgp+')'),(tr('未建立','Not established'),'sum(1 - ('+bgp+'))')],0,71)
    trend(tr('VXLAN 对端状态','VXLAN peer state'),[(tr('在线','Up'),'sum('+nve+')'),(tr('未连通','Down'),'sum(1 - ('+nve+'))')],12,71,desc=tr('同一设备和对端去重，不表示物理拓扑连接。','Deduplicated per device and peer; these are not physical topology links.'))
    bars(tr('路由规模 · 最高 8 个设备 / VRF','Route scale · top 8 device / VRF tables'),'topk(8,'+metric('aiops_route_summary_routes','source="Total Routes"')+')','${__field.labels.device} · ${__field.labels.vrf}',0,79,h=10,desc=tr('仅使用明确的 Total Routes 行，不将分类与总计重复相加。','Uses explicit Total Routes observations; categories are not added to totals.'))
    route_queries=[label(metric('aiops_bgp_routes_'+suffix),'direction',tr(zh,en)) for suffix,zh,en in [('received','收到','Received'),('accepted','接受','Accepted'),('advertised','发送','Advertised')]]
    table(tr('BGP 路由交换汇总','BGP route exchange summary'),['sum by(direction,afi_safi) ('+' or '.join(route_queries)+')'],'direction',{'afi_safi':tr('地址族','Address family'),'direction':tr('方向','Direction'),'Value':tr('路由 / NLRI 观察合计','Observed routes / NLRI')},12,79,12,10,desc=tr('同一路由可能出现在多个邻居或地址族；不代表全网唯一路由数量。','Routes may appear at multiple peers or address families; this is not a global unique-route count.'))
    trend(tr('OSPF 邻居状态','OSPF neighbor state'),[(tr('正常','Up'),'sum('+ospf+')'),(tr('待核查','Review'),'sum(1 - ('+ospf+'))')],0,89)
    trend(tr('BGP 消息计数 · 原始累计值','BGP message counters · cumulative'),[(tr('收到','Received'),'sum('+metric('aiops_bgp_messages_received')+')'),(tr('发出','Sent'),'sum('+metric('aiops_bgp_messages_sent')+')')],12,89,desc=tr('设备原始累计计数，可能在重启时归零；无数据表示当前未采集。','Raw cumulative device counters may reset on restart; no data means currently unavailable.'))

    row(tr('04 · 防火墙运行情况','04 · Firewall operations'),97)
    stat(tr('提供指标的防火墙','Reporting firewalls'),count_devices(metric('aiops_firewall_sessions_active')),0,98)
    stat(tr('活跃会话 · 合计观察','Active sessions · observed sum'),'sum('+metric('aiops_firewall_sessions_active')+')',6,98)
    stat(tr('最高会话建立速率','Highest session setup rate'),'max('+metric('aiops_firewall_sessions_per_second')+')',12,98,unit='ops')
    stat(tr('最高单机吞吐','Highest single-device throughput'),'max('+metric('aiops_firewall_throughput_bps')+')',18,98,unit='bps')
    trend(tr('防火墙活跃会话','Firewall active sessions'),[('{{device}}',metric('aiops_firewall_sessions_active'))],0,102)
    trend(tr('防火墙吞吐','Firewall throughput'),[('{{device}}',metric('aiops_firewall_throughput_bps'))],12,102,unit='bps')
    trend(tr('每秒新建会话','New sessions per second'),[('{{device}}',metric('aiops_firewall_sessions_per_second'))],0,110)
    p=panel(tr('防火墙总结说明','Firewall interpretation'),'text',12,110,12,8)
    p['options']={'mode':'markdown','content':tr('### 当前采集覆盖\n活跃会话、会话建立速率、吞吐，以及前面资源区的 CPU / 内存。\n\n会话总数是各设备观察之和；HA 或串联路径可能重复观察同一业务。吞吐按设备展示，避免相加导致重复计算。\n\n这些指标用于容量和负载汇报，**不代表安全事件、策略命中率或威胁检测结论**。','### Current collection coverage\nActive sessions, session setup rate, throughput, plus CPU and memory in the resource section above.\n\nSession totals add device observations; HA or serial paths may observe the same business flow. Throughput is per device to avoid double counting.\n\nThese metrics describe load and capacity. They do **not** establish security incidents, policy hit rates, or threat-detection outcomes.')}

    row(tr('05 · 接口状态、流量与质量','05 · Interface state, traffic and quality'),118)
    stat(tr('已监测接口','Observed interfaces'),'count('+interfaces+')',0,119,w=8)
    stat(tr('已连通接口','Operational interfaces'),'sum('+interfaces+')',8,119,w=8)
    stat(tr('已启用但未连通 · 待核查','Admin-up / oper-down · review'),interface_review,16,119,w=8)
    bars(tr('接收流量 · 最高 8 个接口','Receive traffic · top 8 interfaces'),'topk(8,'+metric('aiops_interface_rx_bps')+')','${__field.labels.device} · ${__field.labels.interface}',0,123,h=10,unit='bps')
    bars(tr('发送流量 · 最高 8 个接口','Transmit traffic · top 8 interfaces'),'topk(8,'+metric('aiops_interface_tx_bps')+')','${__field.labels.device} · ${__field.labels.interface}',12,123,h=10,unit='bps')
    trend(tr('最高单接口流量趋势','Highest individual interface traffic'),[(tr('接收峰值接口','Maximum RX interface'),'max('+metric('aiops_interface_rx_bps')+')'),(tr('发送峰值接口','Maximum TX interface'),'max('+metric('aiops_interface_tx_bps')+')')],0,133,desc=tr('每个采样点取最大值，设备/接口可能变化；不是全网总吞吐。','Maximum at each sample; the interface may change. Not total network throughput.'),unit='bps')
    trend(tr('未连通但已启用的接口','Admin-up / oper-down interfaces'),[(tr('待核查接口','Interfaces to review'),interface_review)],12,133,desc=tr('可能为空闲或未接线端口；不要直接等同于业务中断。','May include unused or unplugged ports; do not equate directly with business outages.'))
    counters=[('input_errors','输入错误','Input errors'),('crc_errors','CRC 错误','CRC errors'),('alignment_errors','对齐错误','Alignment errors'),('symbol_errors','符号错误','Symbol errors'),('input_discards','输入丢弃','Input discards'),('output_errors','输出错误','Output errors'),('collisions','碰撞','Collisions'),('late_collisions','晚碰撞','Late collisions'),('deferred','延迟发送','Deferred transmissions'),('output_discards','输出丢弃','Output discards')]
    p=table(tr('接口质量总结 · 有非零累计值的接口','Interface quality · interfaces with nonzero counters'),[' or '.join(label(available_or_unknown('sum('+metric('aiops_interface_'+suffix)+' > bool 0)'),'counter',tr(zh,en)) for suffix,zh,en in counters)],'counter',{'counter':tr('计数器类别','Counter'),'Value':tr('接口数量','Interfaces')},0,141,12,10,desc=tr('累计值可能来自历史，不是所选时段新增错误，不将不同计数器相加。','Cumulative values may be historical, not new errors in the selected period. Counter families must not be added.'))
    field(p,tr('接口数量','Interfaces'),{'mappings':[{'type':'value','options':{'-1':{'text':tr('暂无数据','No data'),'color':'gray'}}}]})
    all_counters=' or '.join(label(metric('aiops_interface_'+suffix),'counter',tr(zh,en)) for suffix,zh,en in counters)
    table(tr('接口质量明细 · 最高 20 个累计值','Interface quality · top 20 cumulative counters'),['topk(20, '+all_counters+')'],'interface',{'device':tr('设备','Device'),'interface':tr('接口','Interface'),'counter':tr('计数器','Counter'),'Value':tr('累计值','Cumulative')},12,141,12,10)
    text(tr('汇报边界与进一步排查','Report limits and drill-down'),tr('**数据缺失不等于正常；未识别状态不做健康结论。** CPU / 内存 / 硬件容量 80% 提醒、95% 标红，仅为展示参考线。接口、路由和邻居由多台设备重复观察，不能相加推导业务流量、唯一链路或 SLA。需要配置、拓扑、路径或单接口细节时，使用顶部完整运维视图和物理拓扑入口。','**Missing data is not healthy; unrecognized states do not establish health.** CPU / memory / hardware capacity thresholds of 80% and 95% are presentation guides. Interfaces, routes and peers may be observed at multiple devices; they do not add up to unique links, business traffic or an SLA. Use the operations and physical-topology links for detailed investigation.'),151,4)
    # Additional asset detail belongs before routing, leaving interface panels last.
    for existing in panels:
        if existing['gridPos']['y'] >= 70:existing['gridPos']['y'] += 10
    p=table(tr('内存容量 · 总量 / 可用','Memory capacity · total / free'),['max by(device)('+metric('aiops_device_memory_total_bytes')+')','max by(device)('+metric('aiops_device_memory_free_bytes')+')'],'device',{'device':tr('设备','Device'),'Value #A':tr('总量','Total'),'Value #B':tr('可用','Free')},0,70,12,10)
    for name in [tr('总量','Total'),tr('可用','Free')]:field(p,name,{'unit':'bytes','decimals':1})
    allowed_sources=raw('aiops_route_summary_routes','source=~'+json.dumps(route_allowed)+',source!="Total Routes"')
    table(tr('路由分类 · 仅已识别来源','Routes by recognized source'),['sum by(source) ('+allowed_sources+' and on(device) ('+raw('aiops_collect_success')+' == 1))'],'source',{'source':tr('路由来源','Route source'),'Value':tr('设备表项观察之和','Observed entries across devices')},12,70,12,10,desc=tr('不同设备可能包含相同前缀；分类之和不是全网唯一路由数。无法识别的来源已排除并计入数据质量提示。','Devices may contain the same prefix; category sums are not global unique routes. Unrecognized sources are excluded and counted as data-quality findings.'))
    for existing in panels:
        if existing['id'] == 6:existing['gridPos']['h'] += 1
        if existing['gridPos']['y'] >= 17:existing['gridPos']['y'] += 1
        if existing['id'] == 1:existing['gridPos']['h'] += 1
        else:existing['gridPos']['y'] += 1
    panels.sort(key=lambda p:(p['gridPos']['y'],p['gridPos']['x']))
    variables=json.loads((ROOT/'grafana/dashboards/aiops-overview.json').read_text())['templating']['list']
    variable=next(v for v in variables if v['name']=='device')
    # Reports have an independent device selector, without operations-only dependencies.
    variable['definition']=variable['query']['query']='label_values(aiops_device_up, device)'
    variable['allValue']='.*'
    variable['label']=tr('设备范围','Device scope')
    return dict(annotations={'list':[]},editable=True,fiscalYearStartMonth=0,graphTooltip=1,id=None,
                links=[{'title':'语言 / Language','type':'dashboards','tags':['aiops-report-language'],'asDropdown':True,'includeVars':True,'keepTime':True,'targetBlank':False},
                       {'title':tr('完整运维视图 ↗','Operations dashboard ↗'),'type':'link','url':'/d/eve-aiops-overview/ai-networking-ops','includeVars':True,'keepTime':True,'targetBlank':True,'icon':'dashboard','tags':[]},
                       {'title':tr('物理拓扑 ↗','Physical topology ↗'),'type':'link','url':'http://192.168.20.169:8080/topology','targetBlank':True,'icon':'external link','tags':[]}],
                panels=panels,refresh='1m',schemaVersion=40,tags=['aiops','report','aiops-report-language'],templating={'list':[variable]},time={'from':'now-24h','to':'now'},timepicker={},timezone='browser',title=tr('中文 · 网络运行简报','English · Network Operations Report'),uid='eve-aiops-report'+('' if lang=='zh' else '-en'),version=2,weekStart='')

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--check',action='store_true');args=parser.parse_args()
    for lang,filename in [('zh','aiops-report.json'),('en','aiops-report-en.json')]:
        path=ROOT/'grafana/dashboards'/filename
        text=json.dumps(build(lang),ensure_ascii=False,indent=2)+'\n'
        if args.check:
            if not path.exists() or path.read_text()!=text:raise SystemExit(f'Generated file differs: {path}')
        else:path.write_text(text)
        print(f'{lang}: {len(build(lang)["panels"])} panels; {path.name}')

if __name__=='__main__':main()
