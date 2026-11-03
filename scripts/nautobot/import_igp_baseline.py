"""Store user-requested verified inactive IGP baselines, never change network devices."""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from aiops_agent.igp_baseline import FIELD, validate, applicability_from_evidence
from aiops_agent.nautobot import NautobotClient
from aiops_agent.settings import load_settings


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('evidence')
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--approved-by',required=True)
    args=parser.parse_args()
    content=Path(args.evidence).read_bytes()
    rows=[json.loads(line) for line in content.splitlines() if line.strip()]
    if not rows or len({r['device_id'] for r in rows}) != len(rows):
        raise ValueError('Missing or duplicate device evidence')
    s=load_settings();c=NautobotClient(s.nautobot_url,s.nautobot_token,60)
    now=datetime.now(timezone.utc)
    before,updates=[],[]
    for row in rows:
        stamp=datetime.fromisoformat(row['checked_at'])
        if not 0 <= (now-stamp).total_seconds() <= 7200:
            raise ValueError('Evidence must be from the last two hours')
        protocols=applicability_from_evidence(row)
        current=c._get('dcim/devices/'+row['device_id']+'/')
        if current['name']!=row['device'] or c._related_name(current.get('location') or current.get('site'))!='lab':
            raise ValueError('Device identity or lab scope changed')
        baseline=dict(schema_version=1,device_id=row['device_id'],revision=1,approval_status='approved',
                      approved_by=args.approved_by,approved_at=now.isoformat(),
                      source='User requested verified IGP baseline import in chat',
                      observed_at=row['checked_at'],evidence_sha256=hashlib.sha256(content).hexdigest(),protocols=protocols)
        validate(baseline,row['device_id'])
        custom=current.get('custom_fields') or {}
        old=custom.get(FIELD)
        if old is not None:
            validate(old,row['device_id'])
            if old['protocols']!=baseline['protocols']:
                raise ValueError('Existing baseline differs; explicit revision required')
            baseline=old
        before.append(dict(id=current['id'],name=current['name'],custom_fields=custom))
        updates.append(dict(id=current['id'],custom_fields={FIELD:baseline}))
    fields=c._get_all('extras/custom-fields/?limit=100')
    existing=next((f for f in fields if f['key']==FIELD),None)
    if existing:
        kind=existing['type']['value'] if isinstance(existing['type'],dict) else existing['type']
        if kind!='json' or 'dcim.device' not in existing['content_types']:raise ValueError('Incompatible field')
    print(json.dumps({'mode':'apply' if args.apply else 'preview','devices':len(updates),'intent':'not_applicable'}),flush=True)
    if not args.apply:return
    backup=Path('/app/data')/('igp-baseline-before-'+now.strftime('%Y%m%d-%H%M%S')+'.json')
    backup.write_text(json.dumps({'devices':before,'field':existing,'evidence':rows},indent=2));backup.chmod(0o600)
    if not existing:
        c._request('POST','extras/custom-fields/',{'key':FIELD,'label':'AIOps · Approved IGP baseline','type':'json',
            'content_types':['dcim.device'],'default':None,'required':False,
            'description':'Verified OSPF/OSPFv3/IS-IS/RIP applicability with evidence and approval metadata. Collection never overwrites.'})
    c._request('PATCH','dcim/devices/',updates)
    for update,original in zip(updates,before):
        saved=c._get('dcim/devices/'+update['id']+'/')['custom_fields']
        assert saved[FIELD]==update['custom_fields'][FIELD]
        assert saved.get('aiops_bgp_baseline')==original['custom_fields'].get('aiops_bgp_baseline'), 'BGP baseline changed'
    print(json.dumps({'status':'verified','devices':len(updates),'backup':str(backup),'bgp_baselines_unchanged':True}))

if __name__=='__main__':main()
