"""Verified IGP applicability; no inference from empty/failed neighbor tables."""
from datetime import datetime

FIELD = 'aiops_igp_baseline'
PROTOCOLS = ('ospf', 'ospfv3', 'isis', 'rip')


def validate(value, device_id):
    if not isinstance(value, dict) or value.get('schema_version') != 1:
        raise ValueError('Invalid IGP baseline schema')
    if value.get('device_id') != device_id or value.get('approval_status') != 'approved':
        raise ValueError('IGP baseline identity or approval mismatch')
    if not value.get('approved_by') or type(value.get('revision')) is not int or value['revision'] < 1:
        raise ValueError('Missing IGP approval metadata')
    stamp = datetime.fromisoformat(value['approved_at'].replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('Approval timestamp requires timezone')
    protocols = value.get('protocols')
    if not isinstance(protocols, dict) or set(protocols) != set(PROTOCOLS):
        raise ValueError('Incomplete IGP protocol applicability')
    for protocol in protocols.values():
        if not isinstance(protocol, dict) or protocol.get('intent') not in ('not_applicable','enabled','unspecified'):
            raise ValueError('Invalid IGP intent')
        if not isinstance(protocol.get('neighbors'), list):
            raise ValueError('Invalid IGP neighbors')
        if protocol['intent'] == 'not_applicable' and (protocol['neighbors'] or not protocol.get('reason')):
            raise ValueError('Inapplicable IGP needs evidence reason and no neighbors')
    return value


def evaluate_ospf(device):
    raw = getattr(device, 'igp_baseline', None)
    if raw is None:
        return None
    try:
        baseline = validate(raw, device.id)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return dict(reason='igp_baseline_invalid')
    ospf = baseline['protocols']['ospf']
    if ospf['intent'] == 'not_applicable':
        return dict(reason='igp_baseline_not_applicable', excluded=True)
    # Active IGP requires VRF/area/interface-aware collection before judging
    # missing adjacencies. Legacy NeighborState lacks that evidence.
    return dict(reason='igp_baseline_evidence_required')


def applicability_from_evidence(row):
    """Fail closed: this import supports verified inactive IGP only."""
    if row.get('site') != 'lab' or row.get('error'):
        raise ValueError('IGP observation missing or outside lab')
    if row['platform'] == 'arista_eos':
        if row.get('config_valid') is not True or row.get('igp_config_lines') != []:
            raise ValueError('IGP configuration present or incomplete; requires peer review')
        return {p:dict(intent='not_applicable', neighbors=[], reason='no_igp_in_complete_running_config') for p in PROTOCOLS}
    if row['platform'] == 'paloalto_panos':
        routers = row.get('virtual_routers')
        if not isinstance(routers, list) or not routers:
            raise ValueError('Missing virtual routers')
        result = {'isis':dict(intent='not_applicable',neighbors=[],reason='not_supported_by_panos')}
        for p in ('ospf','ospfv3','rip'):
            contexts = []
            for vr in routers:
                detail = vr['protocols'][p]
                if detail.get('present') is not False and detail.get('enabled') != 'no':
                    raise ValueError('IGP enabled or ambiguous; requires peer review')
                contexts.append({'virtual_router':vr['virtual_router'],'configured':detail['present'],'enabled':False})
            result[p] = dict(intent='not_applicable',neighbors=[],reason='disabled_or_absent_in_running_virtual_routers',contexts=contexts)
        return result
    raise ValueError('Unsupported IGP evidence platform')
