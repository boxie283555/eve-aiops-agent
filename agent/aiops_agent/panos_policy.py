"""Conservative PAN-OS running-config evaluation for new IPv4 connections.

Three-valued matches preserve first-match ordering. Unknown security matches
may establish an allow action only if every reachable outcome allows; the exact
rule remains unknown. Security uses original addresses/ports and the post-DNAT
destination zone. NAT uncertainty is never bypassed or assigned guessed values.
"""
from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from ipaddress import ip_address, ip_network

from .model import interface_name


def members(node, path, default=None):
    child = node.find(path)
    if child is None:
        return list(default or [])
    return [m.text or '' for m in child.findall('member')]


def parse_config(xml):
    root = ET.fromstring(xml)
    config = root if root.tag == 'config' else root.find('.//config')
    if config is None:
        raise ValueError('Running configuration is missing')
    shared = config.find('shared')
    model = {'status': 'ok', 'source': 'panos_running_config', 'vsyses': {},
             'config_digest': hashlib.sha256(ET.tostring(config)).hexdigest(), 'limitations': []}
    if any((e.text or '').strip() for e in config.findall('.//panorama-server') + config.findall('.//panorama-server-2')):
        model['limitations'].append('Panorama effective policy is not available in this local configuration')
    if config.find('.//pre-rulebase') is not None or config.find('.//post-rulebase') is not None:
        model['limitations'].append('Panorama pre/post rule ordering is not supported')
    entries = config.findall('./devices/entry/vsys/entry')
    if not entries:
        raise ValueError('No VSYS policy context')
    for vs in entries:
        v = {'zones': {}, 'addresses': {}, 'address_groups': {}, 'services': {}, 'service_groups': {}, 'security': [], 'nat': [], 'pbf': False}
        for zone in vs.findall('./zone/entry'):
            for m in zone.findall('./network/layer3/member'):
                name = interface_name(m.text or '')
                if name in v['zones']:
                    raise ValueError('Ambiguous zone membership')
                v['zones'][name] = zone.get('name')
        for scope in [shared, vs]:
            if scope is None:
                continue
            for obj in scope.findall('./address/entry'):
                v['addresses'][obj.get('name')] = {c.tag: c.text for c in obj if c.tag in {'ip-netmask', 'ip-range', 'fqdn'}}
            for obj in scope.findall('./address-group/entry'):
                v['address_groups'][obj.get('name')] = members(obj, 'static') if obj.find('static') is not None else None
            for obj in scope.findall('./service/entry'):
                protocols = list(obj.find('protocol')) if obj.find('protocol') is not None else []
                v['services'][obj.get('name')] = [{'protocol': p.tag, 'destination': p.findtext('port'), 'source': p.findtext('source-port', 'any')} for p in protocols]
            for obj in scope.findall('./service-group/entry'):
                v['service_groups'][obj.get('name')] = members(obj, 'members')
        for kind in ['security', 'nat']:
            for rule in vs.findall('./rulebase/'+kind+'/rules/entry'):
                if rule.findtext('disabled') == 'yes':
                    continue
                row = {'name': rule.get('name'), 'uuid': rule.get('uuid'), 'kind': kind,
                       'from': members(rule, 'from'), 'to': members(rule, 'to'),
                       'source': members(rule, 'source'), 'destination': members(rule, 'destination'),
                       'negate_source': rule.findtext('negate-source') == 'yes',
                       'negate_destination': rule.findtext('negate-destination') == 'yes',
                       'unsupported': []}
                harmless = {'description','tag','log-start','log-end','log-setting','profile-setting','option','group-tag','rule-type','disabled','action','from','to','source','destination','negate-source','negate-destination','service','application','source-user','category','source-hip','destination-hip','hip-profiles','source-translation','destination-translation','to-interface','nat-type'}
                for c in rule:
                    if c.tag not in harmless:
                        row['unsupported'].append(c.tag)
                if kind == 'security':
                    row.update(action=rule.findtext('action','unknown'), application=members(rule,'application'), service=members(rule,'service'), rule_type=rule.findtext('rule-type','universal'))
                    for field in ['source-user','category','source-hip','destination-hip','hip-profiles']:
                        if rule.find(field) is not None and members(rule,field) != ['any']:
                            row['unsupported'].append(field)
                else:
                    row.update(service=[rule.findtext('service','any')], to_interface=interface_name(rule.findtext('to-interface','any')), nat_type=rule.findtext('nat-type','ipv4'))
                    for field in ['source-translation','destination-translation']:
                        child=rule.find(field)
                        row[field.replace('-','_')] = ET.tostring(child,encoding='unicode') if child is not None else None
                v[kind].append(row)
        # Default rule overrides are evaluated after explicit rules.
        v['defaults'] = {r.get('name'): r.findtext('action','unknown') for r in vs.findall('./rulebase/default-security-rules/rules/entry')}
        for rule in vs.findall('./rulebase/default-security-rules/rules/entry'):
            if any(c.tag not in {'action','log-start','log-end','log-setting','profile-setting','description','tag'} for c in rule):
                model['limitations'].append('Unsupported default-security rule condition')
        v['pbf'] = any(r.findtext('disabled') != 'yes' for r in vs.findall('./rulebase/pbf/rules/entry'))
        model['vsyses'][vs.get('name','vsys1')] = v
    return model


def any_match(values):
    values = list(values)
    return True if True in values else None if None in values else False


def all_match(values):
    values = list(values)
    return False if False in values else None if None in values else True


def address_match(value, address, v, seen=frozenset()):
    if value == 'any':
        return True
    if value in seen or not address:
        return None
    seen = seen | {value}
    if value in v['address_groups']:
        items = v['address_groups'][value]
        return None if items is None else any_match(address_match(i,address,v,seen) for i in items)
    if value in v['addresses']:
        obj = v['addresses'][value]
        if 'ip-netmask' in obj:
            value = obj['ip-netmask']
        elif 'ip-range' in obj:
            value = obj['ip-range']
        else:
            return None
    try:
        ip = ip_address(address)
        if '-' in value:
            low, high = map(ip_address,value.split('-',1))
            return low.version == ip.version == high.version and low <= ip <= high
        return ip in ip_network(value,strict=False)
    except (ValueError, TypeError):
        return None


def port_match(expression, port):
    if expression in {'any', None, ''}:
        return True if expression == 'any' else None
    if port is None:
        return None
    try:
        return any(int(low) <= port <= int(high) for low,high in (token.split('-',1) if '-' in token else (token,token) for token in expression.split(',')))
    except ValueError:
        return None


def service_match(name, flow, v, seen=frozenset()):
    if name == 'any':
        return True
    if name == 'application-default' or name in seen:
        return None
    seen = seen | {name}
    if name in v['service_groups']:
        return any_match(service_match(n,flow,v,seen) for n in v['service_groups'][name])
    services = v['services'].get(name)
    if services is None:
        services = {'service-http':[{'protocol':'tcp','destination':'80','source':'any'}], 'service-https':[{'protocol':'tcp','destination':'443','source':'any'}]}.get(name)
    if not services:
        return None
    return any_match(all_match([None if flow.protocol=='any' else flow.protocol==s['protocol'], port_match(s['destination'],flow.destination_port),port_match(s['source'],flow.source_port)]) for s in services)


def match_rule(rule, flow, v, from_zone, to_zone, egress):
    values = []
    for key, zone in [('from',from_zone),('to',to_zone)]:
        values.append(True if 'any' in rule[key] else None if zone is None else zone in rule[key])
    for key, addr in [('source',flow.source_ip),('destination',flow.destination_ip)]:
        match = any_match(address_match(n,addr,v) for n in rule[key]) if rule[key] else None
        values.append(not match if rule['negate_'+key] and match is not None else match)
    values.append(any_match(service_match(n,flow,v) for n in rule['service']) if rule['service'] else None)
    if rule['kind'] == 'security':
        apps=rule['application']
        values.append(True if 'any' in apps else None if not flow.application else flow.application in apps or None)
        rt=rule['rule_type']
        values.append(True if rt=='universal' else None if rt not in {'intrazone','interzone'} or from_zone is None or to_zone is None else (from_zone==to_zone)==(rt=='intrazone'))
    else:
        values.append(rule['nat_type']=='ipv4')
        if rule['to_interface'] != 'any':
            values.append(None if egress is None else rule['to_interface']==interface_name(egress))
    if rule['unsupported']:
        values.append(None)
    return all_match(values)


def select_rule(rules, flow, v, from_zone, to_zone, egress):
    for rule in rules:
        match = match_rule(rule,flow,v,from_zone,to_zone,egress)
        if match is None:
            return None, 'Rule '+rule['name']+' requires unavailable match evidence (ports, App-ID/application-default, objects or unsupported conditions)'
        if match:
            return rule, None
    return None, None


def possible_security_outcomes(v, flow, from_zone, to_zone, egress):
    """Conservative first-match possibilities, ending at a definite match/default.

    An unknown predicate may match or miss. Its rule stays a possible outcome;
    later rules are reachable only through its miss branch. This never selects
    a later allow as the exact rule or reasons about unknown NAT translations.
    """
    outcomes = []
    for rule in v['security']:
        match = match_rule(rule, flow, v, from_zone, to_zone, egress)
        if match is False:
            continue
        outcomes.append({key: rule[key] for key in ['name', 'action', 'source', 'destination', 'application', 'service']} |
                        {'match': 'definite_if_reached' if match else 'possible'})
        if match is True:
            return outcomes
    name = 'intrazone-default' if from_zone == to_zone else 'interzone-default'
    outcomes.append({'name': name, 'action': v['defaults'].get(name, 'allow' if from_zone == to_zone else 'deny'),
                     'source': ['any'], 'destination': ['any'], 'application': ['any'], 'service': ['any'],
                     'match': 'default_if_reached'})
    return outcomes


def translated_address(value, original, rule, v):
    """A single address or equal-size static prefix mapping; pools stay unknown."""
    if value in v['addresses']:
        value=v['addresses'][value].get('ip-netmask')
    try:
        network=ip_network(value,strict=False)
        if network.num_addresses == 1:
            return str(network.network_address)
        sources=[]
        for name in rule['source']:
            if name in v['addresses']:name=v['addresses'][name].get('ip-netmask')
            sources.append(ip_network(name,strict=False))
        matching=[n for n in sources if ip_address(original) in n and n.num_addresses==network.num_addresses]
        if len(matching)==1:
            return str(ip_address(int(network.network_address)+int(ip_address(original))-int(matching[0].network_address)))
    except (ValueError,TypeError):
        pass
    return None


def apply_nat(rule, flow, v):
    if rule is None:
        return flow, None
    values={}
    if rule['destination_translation']:
        node=ET.fromstring(rule['destination_translation'])
        if any(c.tag not in {'translated-address','translated-port'} for c in node):
            return None,'Unsupported destination NAT mode'
        address=node.findtext('translated-address')
        # Destination pools/FQDN need runtime selection. Only a single address is deterministic.
        if address in v['addresses']:address=v['addresses'][address].get('ip-netmask')
        try:
            net=ip_network(address,strict=False)
            if net.num_addresses!=1:raise ValueError()
            values['destination_ip']=str(net.network_address)
            port=node.findtext('translated-port')
            if port:values['destination_port']=int(port)
        except (ValueError,TypeError):
            return None,'Destination NAT needs runtime address/port selection'
    if rule['source_translation']:
        node=ET.fromstring(rule['source_translation'])
        static=node.find('static-ip')
        if static is None:
            return None,'Dynamic source NAT/PAT requires an observed session translation'
        if static.findtext('bi-directional')=='yes':
            return None,'Bidirectional static NAT requires implicit reverse-rule evaluation'
        address=translated_address(static.findtext('translated-address'),flow.source_ip,rule,v)
        if not address:
            return None,'Static source NAT mapping is unresolved'
        values['source_ip']=address
    try:
        return flow.translated(**values),None
    except ValueError:
        return None,'NAT translation is incompatible with the specified flow'


def inspect_firewall(model, flow, ingress, route_lookup):
    """route_lookup(address) returns [(egress_interface, route_evidence)] or raises ValueError."""
    result={'status':'incomplete','flow_before':flow.data(),'evidence_level':'configuration',
            'assumptions':['New connection; session return processing is not inferred'], 'steps':[]}
    def fail(reason):
        result['reason']=reason
        return result
    if not model or model.get('status')!='ok':
        return fail('PA running-policy evidence is unavailable')
    if model.get('limitations'):
        return fail('; '.join(model['limitations']))
    contexts=[(name,v) for name,v in model['vsyses'].items() if interface_name(ingress) in v['zones']]
    if len(contexts)!=1:
        return fail('PA ingress logical interface has missing or ambiguous VSYS/zone ownership')
    vsys,v=contexts[0];fz=v['zones'][interface_name(ingress)]
    result.update(vsys=vsys,from_zone=fz,observed_at=model.get('last_success_at'),config_digest=model.get('config_digest'))
    if ip_address(flow.source_ip).version!=4:
        return fail('PA IPv6 policy evaluation is not implemented')
    if v['pbf']:
        return fail('Enabled PA PBF rules require native forwarding verification')
    # Bidirectional NAT creates additional implicit match behavior; do not silently ignore it.
    if any('bi-directional>yes<' in (r.get('source_translation') or '') for r in v['nat']):
        return fail('Bidirectional NAT implicit rules require native matching')
    def egress_context(address):
        routes=route_lookup(address)
        pairs={(interface_name(i),v['zones'].get(interface_name(i))) for i,_ in routes}
        if len(pairs)!=1 or next(iter(pairs))[1] is None:
            raise ValueError('PA route has ambiguous or missing egress zone/interface')
        return (*next(iter(pairs)),[r for _,r in routes])
    try:
        original_egress,original_zone,original_routes=egress_context(flow.destination_ip)
        nat,error=select_rule(v['nat'],flow,v,fz,original_zone,original_egress)
        if error:return fail('NAT: '+error)
        result['nat_rule']=nat['name'] if nat else None
        translated,error=apply_nat(nat,flow,v)
        if error:return fail(error)
        final_egress,final_zone,final_routes=egress_context(translated.destination_ip)
        result.update(to_zone=final_zone,original_to_zone=original_zone,egress=final_egress,
                      nat_flow=translated.data(),route_evidence=original_routes+final_routes)
        security,error=select_rule(v['security'],flow,v,fz,final_zone,final_egress)
        if error:
            outcomes = possible_security_outcomes(v, flow, fz, final_zone, final_egress)
            result['security_candidates'] = outcomes[:100]
            result['security_candidates_truncated'] = len(outcomes) > 100
            if outcomes and all(row['action'] == 'allow' for row in outcomes):
                result.update(status='allow', action='allow', security_rule=None,
                              policy_resolution='action_only', flow_after=translated.data(),
                              reason='All possible first-match security outcomes allow; exact rule is unresolved')
                return result
            return fail('Security: '+error)
        if security:
            action=security['action'];rule=security['name']
        else:
            rule='intrazone-default' if fz==final_zone else 'interzone-default'
            action=v['defaults'].get(rule,'allow' if fz==final_zone else 'deny')
        result.update(security_rule=rule,action=action,flow_after=translated.data(),policy_resolution='exact_rule')
        if action in {'deny','drop','reset-client','reset-server','reset-both'}:
            result.update(status='blocked',reason='PA security rule '+rule+' '+action)
        elif action=='allow':
            result.update(status='allow',reason='Configured PA security rule '+rule+' allows this new flow')
        else:
            return fail('Unsupported security action '+action)
        return result
    except ValueError as exc:
        return fail(str(exc))
