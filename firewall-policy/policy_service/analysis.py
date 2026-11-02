"""Version-bound policy inventory; comparisons never cross device/rulebase scope."""
from __future__ import annotations

import copy
import hashlib
import ipaddress
import json
import xml.etree.ElementTree as ET
from collections import Counter

from .upstream.models import Vendor
from .upstream.parser import parse_config
from .upstream.analyzer import analyze_policies

VERSION = '1.0'


def identity(*parts):
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()[:32]


def xml_root(text):
    if '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():
        raise ValueError('DTD/entity declarations are not supported')
    root = ET.fromstring(text)
    if root.tag != 'config':
        raise ValueError('Expected PAN-OS config XML')
    return root


def members(node, path):
    return [x.text or '' for x in node.findall(path + '/member')]


def address_intervals(value, objects, seen=frozenset()):
    if value in seen:
        return None
    if value in {'any','any4','any6','all'}:
        return ([(4, 0, 2**32-1)] if value=='any4' else [(6, 0, 2**128-1)] if value=='any6'
                else [(4, 0, 2**32-1), (6, 0, 2**128-1)])
    node = objects.get(value)
    if node is not None:
        if node.tag == 'address':
            value = node.findtext('ip-netmask') or node.findtext('ip-range')
            if not value:return None
        else:
            names = members(node, 'static')
            if not names:return None
            result = [address_intervals(n, objects, seen | {value}) for n in names]
            return None if any(x is None for x in result) else [i for row in result for i in row]
    try:
        if '-' in value:
            lo, hi = [ipaddress.ip_address(x.strip()) for x in value.split('-',1)]
            if lo.version != hi.version or int(lo)>int(hi):return None
            return [(lo.version, int(lo), int(hi))]
        net = ipaddress.ip_network(value,strict=False)
        return [(net.version, int(net.network_address), int(net.broadcast_address))]
    except ValueError:return None


def ports(value):
    try:
        rows=[]
        for token in (value or '0-65535').split(','):
            parts=token.strip().split('-');lo=int(parts[0]);hi=int(parts[-1])
            if not 0<=lo<=hi<=65535:return None
            rows.append((lo,hi))
        return rows
    except ValueError:return None


def service_intervals(value, objects, seen=frozenset()):
    if value in seen:return None
    if value=='any':return [('*',0,65535,0,65535)]
    node=objects.get(value)
    if node is None:return None  # Includes application-default; never treat as service-any.
    if node.tag=='service-group':
        values=[service_intervals(n,objects,seen|{value}) for n in members(node,'members')]
        return None if not values or any(v is None for v in values) else [i for row in values for i in row]
    result=[]
    for proto in node.findall('./protocol/*'):
        if proto.tag not in {'tcp','udp'}:return None
        if not proto.findtext('port'):return None
        dest=ports(proto.findtext('port'));source=ports(proto.findtext('source-port'))
        if dest is None or source is None:return None
        result += [(proto.tag,*s,*d) for s in source for d in dest]
    return result or None


def covers_intervals(left,right):
    if left is None or right is None:return False
    for version,low,high in right:
        cursor=low
        for _,a,b in sorted(x for x in left if x[0]==version):
            if a>cursor:break
            if b>=cursor:cursor=b+1
            if cursor>high:break
        if cursor<=high:return False
    return True


def union(values, resolver, objects):
    result=[resolver(v,objects) for v in values]
    return None if not result or any(r is None for r in result) else [x for row in result for x in row]


def scope_covers(left,right):
    return bool(left and right) and ('any' in left or set(right)<=set(left))


def covers(a,b,addresses,services):
    # Optional matching conditions are never silently discarded.
    if a.get('unsupported') or b.get('unsupported'):return False
    for field in ['source_zones','destination_zones','applications','users']:
        if not scope_covers(a[field],b[field]):return False
    for field in ['source_addresses','destination_addresses']:
        if not covers_intervals(union(a[field],address_intervals,addresses),union(b[field],address_intervals,addresses)):return False
    left=union(a['services'],service_intervals,services);right=union(b['services'],service_intervals,services)
    if left is None or right is None:return False
    return all(any((p=='*' or p==q) and sl<=tl<=th<=sh and dl<=ul<=uh<=dh
                   for p,sl,sh,dl,dh in left) for q,tl,th,ul,uh in right)


def scoped_objects(root, vsys):
    addresses={};services={};rows=[]
    for scope in [root.find('shared'),vsys]:
        if scope is None:continue
        for kind in ['address','address-group','service','service-group']:
            for obj in scope.findall(kind+'/entry'):
                item=copy.deepcopy(obj);item.tag=kind
                target=addresses if kind.startswith('address') else services
                target[obj.get('name')]=item
    for kindmap in [addresses,services]:
        for name,obj in kindmap.items():
            rows.append({'name':name,'type':obj.tag,'value':ET.tostring(obj,encoding='unicode')})
    return addresses,services,rows


def finding(category,severity,rule_ids,detail,context,device_id):
    return {'id':identity(device_id,context,category,severity,*rule_ids),'category':category,'severity':severity,
            'policy_ids':rule_ids,'detail':detail,'context':context,'device_id':device_id,
            'evidence':'configuration_analysis','requires_review':True}


def analyze(device_id,device_name,snapshot_id,config_xml):
    root=xml_root(config_xml)
    result={'schema_version':VERSION,'device_id':device_id,'device':device_name,'snapshot_id':snapshot_id,
            'policies':[],'nat_rules':[],'objects':[],'findings':[],'contexts':[],
            'limitations':['Risk findings are configuration analysis, not proof of traffic delivery or business impact.']}
    if root.find('.//pre-rulebase') is not None or root.find('.//post-rulebase') is not None:
        result['limitations'].append('Panorama pre/post policy is not evaluated as effective policy.')
    for vs in root.findall('./devices/entry/vsys/entry'):
        context=vs.get('name')
        if not context or context in result['contexts']:raise ValueError('Missing or ambiguous VSYS context')
        result['contexts'].append(context)
        isolated=ET.Element('config');shared=root.find('shared')
        if shared is not None:isolated.append(copy.deepcopy(shared))
        parent=ET.SubElement(ET.SubElement(ET.SubElement(isolated,'devices'),'entry'),'vsys')
        parent.append(copy.deepcopy(vs))
        parsed=parse_config(Vendor.palo_alto,device_name,ET.tostring(isolated,encoding='unicode'))
        addresses,services,objects=scoped_objects(root,vs)
        for o in objects:
            o.update(id=identity(device_id,context,o['type'],o['name']),context=context,device_id=device_id)
        result['objects']+=objects
        scoped=[]
        for kind,collection,target in [('security',parsed.policies,'policies'),('nat',parsed.nat_rules,'nat_rules')]:
            entries=vs.findall('./rulebase/'+kind+'/rules/entry')
            if len(collection)!=len(entries):raise ValueError('Parsed rule count does not match configuration')
            seen=set()
            for order,(policy,entry) in enumerate(zip(collection,entries),1):
                if policy.rule_name in seen:raise ValueError('Duplicate rule name within policy context')
                seen.add(policy.rule_name)
                key=identity(device_id,context,kind,entry.get('uuid') or policy.rule_name)
                p=policy.model_dump(mode='json');p.update(id=key,context=context,device_id=device_id,
                    rulebase=kind,position=order,native_id=entry.get('uuid'),identity_basis='uuid' if entry.get('uuid') else 'scoped_name',
                    snapshot_id=snapshot_id,raw=ET.tostring(entry,encoding='unicode'))
                # Owner/expiration remain explicitly unknown when not present in the source.
                safe={'from','to','source','destination','service','application','source-user','action','description','disabled','tag','log-start','log-end','log-setting','profile-setting','rule-type','group-tag'}
                p['unsupported']=[c.tag for c in entry if c.tag not in safe] if kind=='security' else []
                if entry.findtext('rule-type','universal')!='universal':p['unsupported'].append('rule-type')
                if kind=='security':
                    p['users']=p['users'] or ['any']
                    for field in ['source_addresses','destination_addresses']:
                        if union(p[field],address_intervals,addresses) is None:p['unsupported'].append('unresolved_'+field)
                    if union(p['services'],service_intervals,services) is None:p['unsupported'].append('unresolved_services')
                    scoped.append(p)
                    for f in analyze_policies([policy.model_copy(update={'id':key})]):
                        data=f.model_dump();data.update(id=identity(device_id,context,f.category,f.title,f.severity,key),context=context,
                            device_id=device_id,evidence='heuristic',requires_review=True)
                        result['findings'].append(data)
                result[target].append(p)
        enabled=[p for p in scoped if p['enabled']]
        if len(enabled)>450:
            result['limitations'].append(context+': pairwise checks limited to 450 rules; no completeness claim.')
        else:
            for i,a in enumerate(enabled):
                for b in enabled[i+1:]:
                    if covers(a,b,addresses,services):
                        equal=covers(b,a,addresses,services);same=a['action']==b['action']
                        cat='duplicate' if equal and same else 'shadowed'
                        result['findings'].append(finding(cat,'medium' if same else 'high',[a['id'],b['id']],
                            a['rule_name']+' → '+b['rule_name'],context,device_id))
        if any(p.get('unsupported') for p in scoped):
            result['limitations'].append(context+': unresolved objects/application-default or additional match conditions excluded from definitive coverage checks.')
    if not result['contexts']:raise ValueError('No supported local VSYS configuration found')
    for n in result['nat_rules']:
        if n['enabled'] and ('any' in n['original_sources'] or 'any' in n['original_destinations']):
            result['findings'].append(finding('broad-nat','medium',[n['id']],n['rule_name'],n['context'],device_id))
    result['summary']={'policies':len(result['policies']),'nat_rules':len(result['nat_rules']),
        'objects':len(result['objects']),'findings':len(result['findings']),
        'severity':dict(Counter(f['severity'] for f in result['findings']))}
    return result
