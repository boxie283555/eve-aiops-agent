"""Printable, escaped review evidence. No device actions or executable config."""
from html import escape
from datetime import datetime, timezone


def render(review,lang='zh'):
    en=lang=='en'
    t=lambda zh,eng:eng if en else zh
    e=lambda value:escape(str(value if value is not None else '—'))
    def stamp(value):return datetime.fromtimestamp(value,timezone.utc).isoformat() if value else '—'
    rows=[(t('设备','Device'),review['device']),('Nautobot UUID',review['device_id']),
          (t('操作人（声明）','Declared operator'),review['actor']),(t('目的 / 工单引用','Reason / ticket reference'),review['reason']),
          (t('生成时间 UTC','Created UTC'),stamp(review['created_at'])),(t('配置备份版本','Backup version'),review['version']['id']),
          (t('最近成功观察 UTC','Last observation UTC'),stamp(review['version']['last_observed_at'])),
          (t('目标规则','Target rule'),review['context']+' / '+review['kind']+' / '+review['rule_name']),
          (t('操作','Operation'),review['operation']), (t('新值','New values'),', '.join(review['values']))]
    table='<table>'+''.join('<tr><th>'+e(k)+'</th><td>'+e(v)+'</td></tr>' for k,v in rows)+'</table>'
    risk='<p>'+t('风险发现数量：','Risk findings: ')+str(review['before_summary']['findings'])+' → '+str(review['after_summary']['findings'])+'</p>'
    for key,label in [('introduced',t('新增风险','Introduced findings')),('resolved',t('消除风险','Resolved findings'))]:
        risk+='<h3>'+label+'</h3><ul>'+''.join('<li>'+e(f['severity']+' · '+f['category']+' · '+f['detail'])+'</li>' for f in review[key])+'</ul>'
    flow=review.get('flow_impact');paths=''
    if flow:
        paths+='<h2>'+t('指定流量路径比较','Specified flow comparison')+'</h2><p>'+e(flow.get('flow',{}))+'</p>'
        if not flow.get('before'):paths+='<p>'+e(flow.get('reason'))+'</p>'
        for key,label in [('before',t('变更前','Before')),('after',t('变更后','After'))]:
            data=flow.get(key)
            if not data:continue
            paths+='<h3>'+label+' · '+e(data.get('status'))+'</h3>'
            for branch in data.get('paths') or [data]:
                names=[]
                for step in branch.get('path',[]):
                    for name in ([step.get('from'),step.get('to')] if step.get('type')=='physical' else [step.get('device')]):
                        if name and (not names or names[-1]!=name):names.append(name)
                paths+='<p>'+e(' → '.join(names) or branch.get('reason') or data.get('reason') or 'See JSON evidence')+'</p>'
        paths+='<p>'+t('配置推演使用所选防火墙备份和当前拓扑，不能证明实际转发；仅覆盖指定流量。','Prediction combines the selected firewall backup and current topology; it does not prove packet delivery and covers only the supplied flow.')+'</p>'
    return '<!doctype html><html lang="'+('en' if en else 'zh')+'"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Firewall review</title><style>body{font:15px/1.6 system-ui;color:#172338;max-width:1080px;margin:32px auto;padding:0 24px}h1{color:#15466c}h2{border-bottom:2px solid #d2dfea;padding-bottom:8px;margin-top:30px}table{width:100%;border-collapse:collapse}th,td{border:1px solid #c8d5e1;padding:10px;text-align:left;overflow-wrap:anywhere}th{width:220px;background:#eff4fa}pre{white-space:pre-wrap;overflow-wrap:anywhere;padding:18px;background:#f0f4f8;font-size:12px}.notice{padding:16px;border-left:4px solid #ad7429;background:#fff5e5}a{color:#21649a}@media print{body{margin:0;max-width:none}.links{display:none}tr,li{break-inside:avoid}}</style><body><p class="links"><a href="/firewall/?lang='+('en' if en else 'zh')+'">'+t('返回策略中心','Back to policy center')+'</a> · <a href="/api/firewall/reviews/'+e(review['id'])+'/report">JSON</a></p><h1>'+t('防火墙变更影响评审','Firewall change impact review')+'</h1><p class="notice">'+t('状态：已预览。此报告不代表已审批、已执行或业务验证通过。','Status: previewed. This report does not establish approval, execution or successful business validation.')+'</p>'+table+'<h2>'+t('风险变化','Risk changes')+'</h2>'+risk+paths+'<h2>'+t('配置差异（脱敏）','Configuration diff (redacted)')+'</h2><pre>'+e(review['diff'])+'</pre><h2>'+t('分析限制','Analysis limitations')+'</h2><ul>'+''.join('<li>'+e(x)+'</li>' for x in review['limitations'])+'</ul></body></html>'
