"""Apply lifecycle display scope to operations/report dashboards, idempotently."""
import copy

METRIC = 'aiops_device_inventory_info'
SCOPE = 'lifecycle_group="operational"'
MATCH = 'device=~"${visible_devices:regex}"'


def inject(dashboard, lang='zh'):
    dashboard = copy.deepcopy(dashboard)
    variables = dashboard['templating']['list']
    variables = [v for v in variables if v['name'] not in {'device_visibility','visible_devices'}]
    datasource = {'type':'prometheus','uid':'Prometheus'}
    mode = dict(name='device_visibility',label='显示非运行设备' if lang=='zh' else 'Show non-operational devices',
                type='custom',query=('否 : 0, 是 : 1' if lang=='zh' else 'No : 0, Yes : 1'),
                current={'text':'否' if lang=='zh' else 'No','value':'0','selected':True},
                options=[{'text':'否' if lang=='zh' else 'No','value':'0','selected':True},
                         {'text':'是' if lang=='zh' else 'Yes','value':'1','selected':False}],
                hide=0,multi=False,includeAll=False,skipUrlSync=False)

    def query(variable, expression, label):
        variable.update(type='query',datasource=datasource,definition='query_result('+expression+')',
                        query={'query':'query_result('+expression+')','refId':'Visibility-'+variable['name']},
                        regex='/'+label+'="([^\\"]+)"/',refresh=2,options=[],allValue=None)

    def inventory(labels):
        filters=','.join(labels)
        active=METRIC+'{'+','.join([SCOPE]+labels)+'}'
        # Historical access also works before the lifecycle metric was introduced.
        archive='aiops_device_classification_info{'+filters+'}'
        return '('+active+' or ('+archive+' and on() (vector($device_visibility) == 1)))'

    visible = dict(name='visible_devices',label='Visible devices',hide=2,multi=True,includeAll=True,
                   current={'text':'All','value':'$__all','selected':True},skipUrlSync=True,sort=1)
    # Keep the All interpolation restrictive even when no devices match.
    query(visible, 'count by(device) ('+inventory([])+') or '
          'label_replace(vector(0),"device","__aiops_no_visible_device__","","")','device')
    names = {v['name'] for v in variables}
    for v in variables:
        name=v['name']
        if name not in {'manufacturer','device_type','device','hardware_group'}:continue
        labels=[]
        if name in {'device_type','device','hardware_group'} and 'manufacturer' in names:
            labels.append('manufacturer=~"$manufacturer"')
        if name in {'device','hardware_group'} and 'device_type' in names:
            labels.append('device_type=~"$device_type"')
        if name=='hardware_group':labels.append('device=~"$device"')
        query(v,'count by('+name+') ('+inventory(labels)+')',name)
    dashboard['templating']['list']=[mode,visible]+variables

    def panels(rows):
        for panel in rows:
            for target in panel.get('targets',[]):
                expression=target.get('expr','')
                if MATCH not in expression:
                    target['expr']=expression.replace('device=~"$device"','device=~"$device",'+MATCH) if expression else expression
            panels(panel.get('panels',[]))
    panels(dashboard.get('panels',[]))
    # Language and professional/report links carry the selected display scope.
    for link in dashboard.get('links',[]):
        if link.get('type')=='link':link['includeVars']=True
    return dashboard
