#!/usr/bin/env python3
"""Add Q3 status without mixing exercise records with real unattended changes."""
import json
from pathlib import Path


def inject(dashboard, lang='both'):
    def tr(zh,en):return en if lang=='en' else zh+' / '+en if lang=='both' else zh
    panels=dashboard['panels']
    existing=next((p for p in panels if p['id']==9300),None)
    old_end=max((p['gridPos']['y']+p['gridPos']['h'] for p in panels if 9300<=p['id']<=9305),default=0)
    panels[:]=[p for p in panels if not 9300<=p['id']<=9305]
    anchor=existing['gridPos']['y'] if existing else max(p['gridPos']['y']+p['gridPos']['h'] for p in panels if 9001<=p['id']<=9006)
    for p in panels:
        if p['gridPos']['y'] >= (old_end if existing else anchor):
            p['gridPos']['y'] += anchor+11-old_end if existing else 11
    panels.append(dict(id=9300,type='row',title=tr('Q3 · 配置备份与漂移','Q3 · Configuration and drift'),
                       gridPos=dict(x=0,y=anchor,w=24,h=1),collapsed=False,panels=[]))
    rows=[
        ('未处理无主漂移','Open unowned drift','sum(aiops_config_drift_records{device=~"$device",origin="observed",status="open",unowned="true"})',True),
        ('待处理演练漂移','Open exercise drift','sum(aiops_config_drift_records{device=~"$device",origin="exercise",status="open",unowned="true"})',False),
        ('演练漂移历史记录','Exercise drift history','sum(aiops_config_drift_records{device=~"$device",origin="exercise",unowned="true"})',False),
        ('最近备份失败设备','Latest backup failures','sum(1-aiops_config_backup_success{device=~"$device"})',True),
    ]
    explanations=[
        ('真实观察中，没有匹配预登记变更、且尚未登记处理结果的漂移记录数。不是故障设备数量；无主不等于已证明未经审批。',
         'Unresolved drift records from real observations with no matching preregistered change. This counts records, not failed devices. Unowned does not prove a change was unauthorized.'),
        ('模拟演练产生、没有匹配预登记变更、且尚未登记处理结果的漂移记录数。与真实漂移分开统计。',
         'Unresolved simulated drift records with no matching preregistered change. Counted separately from real observations.'),
        ('全部无主演练记录，包含已处理、未处理以及回滚产生的记录。例如三次修改加三次回滚会留下六条记录；不计入真实漂移验收。',
         'All unowned exercise records, including resolved records and rollbacks. Three changes plus three rollbacks produce six records; these do not count toward real drift acceptance.'),
        ('每台设备只看最近一次配置备份尝试；统计失败设备数，不是失败次数。成功但配置未变化也算成功；尚未尝试备份的设备不计入。',
         'Number of devices whose latest configuration backup attempt failed, not the number of failed attempts. An unchanged configuration is a successful backup. Devices never attempted are excluded.'),
    ]
    for i,(zh,en,expr,alarm) in enumerate(rows):
        panels.append(dict(id=9301+i,type='stat',title=tr(zh,en),gridPos=dict(x=6*i,y=anchor+1,w=6,h=4),
            description=tr(*explanations[i]),
            datasource={'type':'prometheus','uid':'Prometheus'},
            targets=[dict(refId='A',expr='('+expr+') and on() (aiops_config_history_available == 1) and on() (up{job="eve-aiops-agent"} == 1)',instant=True,range=False)],
            fieldConfig={'defaults':{'unit':'short','min':0,'decimals':0,'noValue':tr('未备份 / 无数据','No backup / data'),
                'color':{'mode':'thresholds' if alarm else 'fixed','fixedColor':'blue'},
                'thresholds':{'mode':'absolute','steps':[{'color':'green','value':None},{'color':'red','value':1}]}},'overrides':[]},
            options={'reduceOptions':{'calcs':['lastNotNull'],'fields':'','values':False},'textMode':'value','colorMode':'background' if alarm else 'value',
                     'graphMode':'none','justifyMode':'center','orientation':'horizontal'},
            links=[{'title':tr('配置管理与漂移','Configuration management'),'url':'http://192.168.20.169:8080/config/ui'+('?lang=en' if lang=='en' else ''),'targetBlank':True}]))
    panels.append(dict(id=9305,type='text',title=tr('指标说明与统计范围','Metric definitions and scope'),
        gridPos=dict(x=0,y=anchor+5,w=24,h=6),options={'mode':'markdown','content':tr(
            '[打开配置管理：备份、历史比对与恢复 ↗](http://192.168.20.169:8080/config/ui)\n\n**未处理无主漂移**：真实观察中，无匹配预登记变更且未处理的记录数。**待处理演练漂移**：同样条件下的模拟记录。\n\n**演练历史**：包含已处理记录和回滚；例如 3 次修改 + 3 次回滚 = 6 条记录。**备份失败设备**：最近一次备份失败的设备数，配置未变化也算备份成功。\n\n仅覆盖已尝试备份的 lab 设备；支持每日定时备份，可在配置管理中选择时间、时区和设备。无主不等于已证明未经审批；没有数据不等于零。卡片为所选时间终点的状态，历史记录数不是所选时间段内的新增数。',
            '[Open configuration management: backups, comparison & recovery ↗](http://192.168.20.169:8080/config/ui?lang=en)\n\n**Open unowned drift**: unresolved real-observation records without a matching preregistered change. **Open exercise drift**: simulated records under the same conditions.\n\n**Exercise history** includes resolved records and rollbacks: 3 changes + 3 rollbacks = 6 records. **Backup failures** counts devices whose latest attempt failed; unchanged configurations are successful backups.\n\nCovers lab devices with backup attempts; daily backup time, timezone and devices are configurable. Unowned does not prove unauthorized activity. Missing data is not zero. Cards show state at the selected end time; history is not the number of new records within the selected range.')}))
    panels.sort(key=lambda p:(p['gridPos']['y'],p['gridPos']['x']))
    return dashboard


if __name__=='__main__':
    path=Path(__file__).resolve().parents[2]/'grafana/dashboards/aiops-overview.json'
    path.write_text(json.dumps(inject(json.loads(path.read_text())),ensure_ascii=False,indent=2)+'\n')
