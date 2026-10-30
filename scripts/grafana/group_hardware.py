#!/usr/bin/env python3
"""Apply per-function manufacturer/type repeats to the operations dashboard."""
import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / 'grafana/dashboards/aiops-overview.json'
CLASSIFICATION = 'aiops_device_classification_info'
FILTER = 'manufacturer=~"$manufacturer",device_type=~"$device_type"'
# Use classification at the range endpoint for historical resource curves.
MEMBERS = 'max by(device) (' + CLASSIFICATION + '{' + FILTER + ',hardware_group=~"$hardware_group"} @ end())'
FUNCTIONS = [
    (18, 104, 'Software Version Share', 9, 12),
    (19, 200, 'Device Uptime', 9, 12),
    (20, 201, 'CPU Utilization', 8, 12),
    (21, 202, 'Memory Utilization', 8, 12),
    (16, 203, 'Device Version and Inventory', 8, 24),
    (17, 204, 'Power and Module Status', 9, 24),
    (25, 106, 'TCAM Utilization', 8, 12),
    (26, 205, 'TCAM Resource Details', 9, 24),
]


def apply(dashboard):
    variables = {v['name']: v for v in dashboard['templating']['list']}
    template = copy.deepcopy(variables['device'])
    def variable(name, label, query, hide=0):
        v = copy.deepcopy(template)
        v.update(name=name, label=label, definition=query, hide=hide, current={'selected':True,'text':'All','value':'$__all'}, options=[])
        v['allValue'] = '.*'
        v['query'] = {'query':query, 'refId':'PrometheusVariableQueryEditor-'+name}
        if name == 'hardware_group':
            v['allValue'] = None  # All must enumerate real values for Grafana repeats.
            v['skipUrlSync'] = True
        return v
    device = variables['device']
    # Enumerate filtered options: a literal .* would ignore parent filters in
    # existing non-hardware panels that only reference the device selector.
    device['allValue'] = None
    device_query = 'label_values('+CLASSIFICATION+'{'+FILTER+'}, device)'
    device['definition'] = device['query']['query'] = device_query
    dashboard['templating']['list'] = [
        variable('manufacturer','厂商 / Manufacturer','label_values('+CLASSIFICATION+', manufacturer)'),
        variable('device_type','设备类型 / Device type','label_values('+CLASSIFICATION+'{manufacturer=~"$manufacturer"}, device_type)'),
        device, variables['interface'],
        variable('hardware_group','Hardware group','label_values('+CLASSIFICATION+'{'+FILTER+',device=~"$device"}, hardware_group)',2),
    ]
    panels = {p['id']:p for p in dashboard['panels']}
    managed = {i for pid,rid,_,_,_ in FUNCTIONS for i in [pid,rid]}
    remaining = [p for p in dashboard['panels'] if p['id'] not in managed]
    original_y = min(p['gridPos']['y'] for p in remaining)
    result=[]
    y=0
    for pid,rid,title,height,width in FUNCTIONS:
        p=panels[pid]
        if p.get('repeat') != 'hardware_group':
            for target in p['targets']:
                if pid == 18:
                    raw='aiops_device_hardware_info{device=~"$device"}'
                    target['expr']=target['expr'].replace(raw,'('+raw+' and on(device) '+MEMBERS+')')
                else:
                    target['expr']='('+target['expr']+') and on(device) '+MEMBERS
        result.append(dict(id=rid,type='row',title=title,collapsed=False,panels=[],gridPos=dict(x=0,y=y,w=24,h=1)))
        y+=1
        p.update(title=title+' · $hardware_group',repeat='hardware_group',repeatDirection='h',maxPerRow=24//width)
        p['gridPos']=dict(x=0,y=y,w=width,h=height)
        p['description']=(
            'Grouped by manufacturer → device type (Switch / Firewall); device names and models remain visible. '
            'Manufacturer, Device type and Device filters narrow the groups. '
            'No data means this device group has no observations for this metric, not zero or healthy. '
            'Historical curves use inventory classification at the selected range endpoint.'
        )
        if pid == 19:
            # Many switches make stat-card labels unreadably small. A table
            # keeps complete names and durations visible within each group.
            p['type']='table'
            for target in p['targets']:
                target.update(format='table',instant=True,range=False)
            p['options']={'showHeader':True,'cellHeight':'sm','sortBy':[{'displayName':'Device','desc':False}],'footer':{'show':False}}
            p['fieldConfig']={'defaults':{'unit':'none','custom':{'cellOptions':{'type':'auto'}},'mappings':[]},'overrides':[{'matcher':{'id':'byName','options':'Uptime'},'properties':[{'id':'unit','value':'s'},{'id':'custom.width','value':160}]}]}
            p['transformations']=[{'id':'filterFieldsByName','options':{'include':{'names':['device','Value']}}},{'id':'organize','options':{'indexByName':{'device':0,'Value':1},'renameByName':{'device':'Device','Value':'Uptime'}}}]
        if pid == 16:
            p['transformations'][0]['options']['renameByName']['version']='Software Version'
        if pid == 17:
            for name in ['Model','Serial']:
                if not any(o['matcher'].get('options')==name for o in p['fieldConfig']['overrides']):
                    p['fieldConfig']['overrides'].append({'matcher':{'id':'byName','options':name},'properties':[{'id':'mappings','value':[{'type':'value','options':{'not_applicable':{'text':'N/A (not installed)'}}}]}]})
        p['fieldConfig']['defaults']['noValue']='No data'
        result.append(p)
        y+=height
    for p in remaining:
        p['gridPos']['y'] += y-original_y
        result.append(p)
    dashboard['panels']=result
    dashboard['tags']=list(dict.fromkeys([*dashboard.get('tags',[]),'hardware-classification']))
    return dashboard


if __name__ == '__main__':
    PATH.write_text(json.dumps(apply(json.loads(PATH.read_text())),ensure_ascii=False,indent=2)+'\n')
    print('Updated operations dashboard: eight hardware functions grouped by manufacturer / device type.')
