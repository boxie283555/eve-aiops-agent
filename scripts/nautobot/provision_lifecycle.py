"""Run inside the Agent environment. Adds metadata controls; never changes a device."""
import json
from pathlib import Path
from datetime import datetime,timezone
from aiops_agent.settings import load_settings
from aiops_agent.nautobot import NautobotClient

s=load_settings();c=NautobotClient(s.nautobot_url,s.nautobot_token,30)
fields=c._get_all('extras/custom-fields/?limit=100')
statuses=c._get_all('extras/statuses/?limit=100')
backup=Path('/app/data')/('lifecycle-schema-'+datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')+'.json')
backup.write_text(json.dumps({'custom_fields':fields,'statuses':statuses},ensure_ascii=False));backup.chmod(0o600)
definitions=[
 ('aiops_monitoring_enabled','boolean','AIOps · Enable collection',True,'False pauses metric collection and lab configuration/policy checks; also excludes health scoring.'),
 ('aiops_health_excluded','boolean','AIOps · Exclude from health',False,'Exclude this device from health scoring while continuing collection. Active/Failed devices otherwise remain scored.'),
 ('aiops_exclusion_reason','text','AIOps · Exclusion reason',None,'Reason for maintenance, retirement, collection pause or health exclusion.'),
 ('aiops_exclusion_owner','text','AIOps · Exclusion owner',None,'Operator or change reference. Nautobot change history records the editing account.'),
 ('aiops_exclusion_until','text','AIOps · Health exclusion expires',None,'Optional ISO 8601 time with timezone, e.g. 2026-11-03T18:00:00+08:00. Only expires the health-excluded checkbox; lifecycle status and collection switch remain authoritative.'),
]
existing={r['key']:r for r in fields}
for key,kind,label,default,description in definitions:
 row=existing.get(key)
 if row:
  actual=row['type']['value'] if isinstance(row['type'],dict) else row['type']
  if actual!=kind:raise ValueError('Custom field type mismatch: '+key)
  if 'dcim.device' not in row['content_types']:
   c._request('PATCH','extras/custom-fields/'+row['id']+'/',{'content_types':row['content_types']+['dcim.device']})
 else:
  c._request('POST','extras/custom-fields/',{'key':key,'label':label,'type':kind,'content_types':['dcim.device'],
                                           'default':default,'required':False,'description':description})
for name in ['Maintenance','Retired','Decommissioned']:
 row=next(r for r in statuses if r['name']==name)
 if 'dcim.device' not in row['content_types']:
  c._request('PATCH','extras/statuses/'+row['id']+'/',{'content_types':row['content_types']+['dcim.device']})
print('Lifecycle custom fields and device statuses ready. Schema backup:',backup.name)
