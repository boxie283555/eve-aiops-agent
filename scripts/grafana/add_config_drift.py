#!/usr/bin/env python3
"""Add Q3 status without mixing exercise records with real unattended changes."""
import json
from pathlib import Path


def inject(dashboard, lang='both'):
    def tr(zh,en):return en if lang=='en' else zh+' / '+en if lang=='both' else zh
    panels=dashboard['panels']
    if any(p['id']==9300 for p in panels):return dashboard
    anchor=max(p['gridPos']['y']+p['gridPos']['h'] for p in panels if 9001<=p['id']<=9006)
    for p in panels:
        if p['gridPos']['y']>=anchor:p['gridPos']['y']+=5
    panels.append(dict(id=9300,type='row',title=tr('Q3 · 配置备份与漂移','Q3 · Configuration and drift'),
                       gridPos=dict(x=0,y=anchor,w=24,h=1),collapsed=False,panels=[]))
    rows=[
        ('未处理无主漂移','Open unowned drift','sum(aiops_config_drift_records{device=~"$device",origin="observed",status="open",unowned="true"})',True),
        ('待处理演练漂移','Open exercise drift','sum(aiops_config_drift_records{device=~"$device",origin="exercise",status="open",unowned="true"})',False),
        ('演练漂移历史记录','Exercise drift history','sum(aiops_config_drift_records{device=~"$device",origin="exercise",unowned="true"})',False),
        ('最近备份失败设备','Latest backup failures','sum(1-aiops_config_backup_success{device=~"$device"})',True),
    ]
    for i,(zh,en,expr,alarm) in enumerate(rows):
        panels.append(dict(id=9301+i,type='stat',title=tr(zh,en),gridPos=dict(x=6*i,y=anchor+1,w=6,h=4),
            description=tr('只覆盖当前已执行配置备份的 lab 设备。未处理为尚未登记处理结果；无主为观察窗口内没有匹配的预登记变更。演练单独统计，不能计入真实漂移验收。',
                           'Covers lab devices with backup attempts. Open means unresolved; unowned means no matching preregistered change for the observation window. Exercises never count as real drift acceptance.'),
            datasource={'type':'prometheus','uid':'Prometheus'},
            targets=[dict(refId='A',expr='('+expr+') and on() (aiops_config_history_available == 1) and on() (up{job="eve-aiops-agent"} == 1)',instant=True,range=False)],
            fieldConfig={'defaults':{'unit':'short','min':0,'decimals':0,'noValue':tr('未备份 / 无数据','No backup / data'),
                'color':{'mode':'thresholds' if alarm else 'fixed','fixedColor':'blue'},
                'thresholds':{'mode':'absolute','steps':[{'color':'green','value':None},{'color':'red','value':1}]}},'overrides':[]},
            options={'reduceOptions':{'calcs':['lastNotNull'],'fields':'','values':False},'textMode':'value','colorMode':'background' if alarm else 'value',
                     'graphMode':'none','justifyMode':'center','orientation':'horizontal'},
            links=[{'title':tr('查看漂移记录','View drift records'),'url':'http://192.168.20.169:8080/config/drift?unowned=true','targetBlank':True}]))
    panels.sort(key=lambda p:(p['gridPos']['y'],p['gridPos']['x']))
    return dashboard


if __name__=='__main__':
    path=Path(__file__).resolve().parents[2]/'grafana/dashboards/aiops-overview.json'
    path.write_text(json.dumps(inject(json.loads(path.read_text())),ensure_ascii=False,indent=2)+'\n')
