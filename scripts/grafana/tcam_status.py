"""TCAM presentation: distinguish missing observations and invalid percentages."""
import copy


def inject(dashboard, lang):
    tr = lambda zh, en: zh if lang == 'zh' else en
    panels = dashboard['panels']
    byid = {p['id']: p for p in panels}
    curve, details = byid[25], byid[26]
    status = byid.get(9401)
    selector = 'device=~"$device"'
    members = 'max by(device) (aiops_device_classification_info{device=~"$device",manufacturer=~"$manufacturer",device_type=~"$device_type",hardware_group=~"$hardware_group"} @ end())'
    def source(prefix, label):
        return 'label_replace(' + prefix + 'utilization_percent{' + selector + '}, "collection_source", "' + label + '", "__name__", ".*")'
    raw = '(' + source('aiops_telemetry_asic_tcam_resource_', 'telemetry') + ' or on(device, resource, region) ' + source('aiops_asic_tcam_resource_', 'cli') + ') and on(device) ' + members
    success = 'max by(device) (aiops_collect_success{' + selector + '} == 1)'
    gate = ' and on(device) ' + success + ' and on() (up{job="eve-aiops-agent"} == 1)'
    current = '(' + raw + ')' + gate
    curve['targets'][0]['expr'] = '((' + current + ') >= 0) <= 100'
    curve['fieldConfig']['defaults'].update(min=0, max=100)
    curve['options']['legend']['calcs'] = ['last']
    curve['options']['legend']['sortBy'] = 'Last'
    curve['options']['legend']['sortDesc'] = True
    curve['description'] = tr(
        '仅绘制 0–100% 的有效值；越界值不截断、不视为正常，请看采样状态与资源明细。当前使用周期性 CLI 采集，页面每 30 秒刷新不等于设备每 30 秒采集。不支持或未返回该指标的设备不画零值；历史重启/采集失败保留断点。',
        'Plots valid 0–100% observations only. Outliers are not clamped: see sample status and resource details. Periodic CLI collection is in use; a 30s dashboard refresh is not a 30s device poll. Missing observations are not zero. Restart/collection gaps remain visible.')
    if not status:
        # Insert before the curve; preserve all other panel IDs and ordering.
        y = curve['gridPos']['y']
        for p in panels:
            if p['gridPos']['y'] >= y:
                p['gridPos']['y'] += 7
        status = dict(id=9401, type='table', gridPos=dict(x=0,y=y,w=24,h=7),
                      datasource=copy.deepcopy(curve['datasource']), repeat='hardware_group',
                      repeatDirection='h', maxPerRow=1)
        panels.insert(panels.index(curve), status)
    zero = '(' + members + ') * 0'
    def count(expr):
        return '(count by(device) (' + expr + ') or on(device) (' + zero + ')) and on() (up{job="eve-aiops-agent"} == 1)'
    status['targets'] = [dict(refId=ref,expr=expr,format='table',instant=True,range=False) for ref,expr in [
        ('A', count(current)),
        ('B', count('((' + current + ') < 0) or ((' + current + ') > 100)')),
        ('C', '(max by(device) (timestamp(aiops_telemetry_asic_tcam_resource_utilization_percent{' + selector + '}) or timestamp(aiops_asic_tcam_resource_utilization_percent{' + selector + '})) * 1000) and on(device) (' + members + ')' + gate),
        ('D', 'max by(device) (aiops_collect_success{' + selector + '}) and on(device) (' + members + ') and on() (up{job="eve-aiops-agent"} == 1)'),
    ]]
    labels = {'device':tr('设备','Device'), 'Value #A':tr('资源观测数','Resource observations'),
              'Value #B':tr('异常百分比数','Invalid percentages'),
              'Value #C':tr('Prometheus 最新采样','Latest Prometheus sample'),
              'Value #D':tr('设备采集状态','Device collection')}
    status['title'] = tr('TCAM 采样状态 · $hardware_group','TCAM Sample Status · $hardware_group')
    status['description'] = tr(
        '资源数为 0 表示当前没有 TCAM/ASIC 观测，不代表设备断开或资源占用为零。异常数表示百分比超出 0–100%，仍可在明细中查看原值。采样时间是 Prometheus 抓取时间，不是设备采集时间；周期性 CLI 更新与抓取独立。',
        'Zero resources means no current TCAM/ASIC observations, not device down or zero usage. Invalid percentages fall outside 0–100%; original values remain in details. Sample time is the Prometheus scrape time, not the device observation time; CLI polling runs independently.')
    status['options'] = {'showHeader':True,'cellHeight':'sm','footer':{'show':False}}
    status['transformations'] = [
        {'id':'joinByField','options':{'byField':'device','mode':'outer'}},
        {'id':'filterFieldsByName','options':{'include':{'names':list(labels)}}},
        {'id':'organize','options':{'renameByName':labels,'indexByName':{key:i for i,key in enumerate(labels)}}},
    ]
    status['fieldConfig'] = {'defaults':{'unit':'short','decimals':0,'noValue':tr('无观测','No observation'),'custom':{'cellOptions':{'type':'auto'}}},'overrides':[
        {'matcher':{'id':'byName','options':labels['Value #C']},'properties':[{'id':'unit','value':'dateTimeAsIso'}]},
        {'matcher':{'id':'byName','options':labels['Value #B']},'properties':[
            {'id':'custom.cellOptions','value':{'type':'color-text'}},
            {'id':'thresholds','value':{'mode':'absolute','steps':[{'color':'green','value':None},{'color':'red','value':1}]}}]},
        {'matcher':{'id':'byName','options':labels['Value #D']},'properties':[{'id':'mappings','value':[{'type':'value','options':{'0':{'text':tr('失败','Failed'),'color':'red'},'1':{'text':tr('成功','Successful'),'color':'green'}}}]}]},
    ]}
    # Counts and percentages share this long-form table: one threshold for all
    # of them misleadingly marked any count above 90 as a critical percentage.
    details['fieldConfig']['overrides'] = []
    expr = details['targets'][0]['expr']
    if not expr.endswith(gate):
        details['targets'][0]['expr'] = '(' + expr + ')' + gate
    details['description'] = tr('保留原始数值用于核查；超过 100% 的利用率为异常观测，不能当作实际容量使用率。仅展示最近一次设备采集成功时的指标。',
                               'Original values retained for investigation. Utilization above 100% is an invalid observation, not actual capacity usage. Only shown when the latest device collection succeeded.')
    return dashboard
