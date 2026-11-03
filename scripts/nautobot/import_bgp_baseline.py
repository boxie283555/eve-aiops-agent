"""Import an explicitly approved candidate; run in Agent container with --apply.
Never calls devices. Re-runs accept identical intent but refuse to overwrite revisions.
"""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from aiops_agent.bgp_baseline import FIELD, validate
from aiops_agent.nautobot import NautobotClient
from aiops_agent.settings import load_settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('candidate')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--approved-by', required=True)
    args = parser.parse_args()
    content = Path(args.candidate).read_bytes()
    candidate = json.loads(content)
    devices = candidate['devices']
    if not devices or len({d['device_id'] for d in devices}) != len(devices):
        raise ValueError('Empty or duplicate candidate identities')
    s = load_settings()
    c = NautobotClient(s.nautobot_url, s.nautobot_token, 60)
    stamp = datetime.now(timezone.utc).isoformat()
    before, updates = [], []
    for d in devices:
        row = c._get('dcim/devices/' + d['device_id'] + '/')
        location = c._related_name(row.get('location') or row.get('site'))
        if location != 'lab' or row['name'] != d['device'] or d['site'] != 'lab':
            raise ValueError('Candidate identity/scope changed: ' + d['device'])
        baseline = dict(schema_version=1, device_id=d['device_id'], revision=1,
                        intent=d['candidate_intent'], approval_status='approved',
                        approved_by=args.approved_by, approved_at=stamp,
                        source='User-confirmed lab BGP review 2026-11-03',
                        candidate_sha256=hashlib.sha256(content).hexdigest(),
                        neighbors=[{key:n[key] for key in ('vrf','neighbor','peer_as','afi_safi','required')}
                                   for n in d['expected_neighbors']])
        validate(baseline, d['device_id'])
        old = (row.get('custom_fields') or {}).get(FIELD)
        before.append(dict(id=row['id'],name=row['name'],custom_fields=row.get('custom_fields') or {}))
        if old is not None:
            validate(old, d['device_id'])
            if any(old.get(k) != baseline[k] for k in ('intent','neighbors','candidate_sha256')):
                raise ValueError('Existing baseline differs; explicit revision workflow required: ' + d['device'])
            baseline = old
        updates.append({'id':row['id'],'custom_fields':{FIELD:baseline}})
    fields = c._get_all('extras/custom-fields/?limit=100')
    existing = next((f for f in fields if f['key']==FIELD),None)
    if existing:
        kind=existing['type']['value'] if isinstance(existing['type'],dict) else existing['type']
        if kind != 'json' or 'dcim.device' not in existing['content_types']:
            raise ValueError('Incompatible existing baseline field')
    print(json.dumps({'mode':'apply' if args.apply else 'preview','device_count':len(updates),
                      'required_neighbors':sum(len(d['expected_neighbors']) for d in devices)}),flush=True)
    if not args.apply:
        return
    backup=Path('/app/data')/('bgp-baseline-before-'+datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')+'.json')
    backup.write_text(json.dumps({'devices':before,'custom_field':existing},ensure_ascii=False,indent=2));backup.chmod(0o600)
    if not existing:
        c._request('POST','extras/custom-fields/',{'key':FIELD,'label':'AIOps · Approved BGP baseline',
            'type':'json','content_types':['dcim.device'],'default':None,'required':False,
            'description':'Approved BGP intent and required peers by VRF/AF. Manual revision only; collection never overwrites. Includes approval and candidate hash.'})
    c._request('PATCH','dcim/devices/',updates)
    for update in updates:
        saved=c._get('dcim/devices/'+update['id']+'/')
        assert saved['custom_fields'].get(FIELD)==update['custom_fields'][FIELD], 'Baseline readback mismatch'
    print(json.dumps({'status':'verified','devices':len(updates),'backup':str(backup)}))

if __name__=='__main__':
    main()
