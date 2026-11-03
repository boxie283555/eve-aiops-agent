"""Approved BGP intent and conservative all-VRF evidence handling."""
import ipaddress
import re
from datetime import datetime
from .parser import BgpSummaryEntry

FIELD = 'aiops_bgp_baseline'


def afi(value):
    return str(value).lower().replace(' ', '_').replace('-', '_')


def validate(value, device_id):
    if not isinstance(value, dict) or value.get('schema_version') != 1:
        raise ValueError('Invalid baseline schema')
    if value.get('device_id') != device_id or value.get('approval_status') != 'approved':
        raise ValueError('Baseline identity or approval mismatch')
    if not value.get('approved_by') or not isinstance(value.get('revision'), int) or value['revision'] < 1:
        raise ValueError('Missing baseline approval metadata')
    if datetime.fromisoformat(value['approved_at'].replace('Z', '+00:00')).tzinfo is None:
        raise ValueError('Approval timestamp requires timezone')
    if value.get('intent') not in ('enabled', 'not_applicable'):
        raise ValueError('Invalid protocol intent')
    peers = value.get('neighbors')
    if not isinstance(peers, list) or len(peers) > 10000:
        raise ValueError('Invalid peer list')
    if value['intent'] == 'not_applicable' and peers:
        raise ValueError('Inapplicable baseline cannot have peers')
    seen = set()
    for peer in peers:
        ipaddress.ip_address(peer['neighbor'])
        if not isinstance(peer['vrf'], str) or not peer['vrf'] or peer['afi_safi'] not in ('ipv4_unicast', 'l2vpn_evpn'):
            raise ValueError('Invalid peer scope')
        if not isinstance(peer.get('required'), bool) or not 1 <= int(peer['peer_as']) <= 4294967295:
            raise ValueError('Invalid peer requirement')
        key = (peer['vrf'], peer['neighbor'], peer['afi_safi'])
        if key in seen:
            raise ValueError('Duplicate expected peer')
        seen.add(key)
    return value


def parse_eos_ipv4_all_vrfs(output):
    """Reject partial/unrecognized tables instead of calling absent peers faulty."""
    if output.strip() == '% BGP inactive':
        return [], True
    peers, vrf, headers = [], None, 0
    if any(token in output.lower() for token in ('invalid input', 'command_failed', 'error:', '--more--')):
        return [], False
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        match = re.fullmatch(r'BGP summary information for VRF (\S+)', line)
        if match:
            # Each VRF section must have a table header, even if empty.
            if vrf is not None and not headers:
                return [], False
            vrf, headers = match[1], 0
            continue
        if line.startswith(('Router identifier ', 'Neighbor Status Codes:')):
            continue
        if 'Neighbor' in line and 'MsgRcvd' in line and 'MsgSent' in line and 'State' in line:
            headers += 1
            continue
        # EOS may prepend a description, or an 'm' maintenance marker.
        match = re.search(r'\b((?:\d{1,3}\.){3}\d{1,3})\s+4\s+(\d+)\s+(\d+)\s+(\d+)\s+\d+\s+\d+\s+(\S+)\s+(.+?)\s+(\d+)\s+(\d+)\s+(\d+)$', line)
        if not match or vrf is None or not headers:
            return [], False
        address, asn, received, sent, uptime, state, rcv, acc, adv = match.groups()
        peers.append(BgpSummaryEntry(address, vrf, asn, state.strip(), 'IPv4 Unicast',
                                    'Negotiated' if state.lower() in ('estab','established') else 'unknown',
                                    float(rcv), float(acc), float(adv), float(received), float(sent)))
    return peers, vrf is not None and bool(headers)


def evaluate(device, result, usable):
    """None means legacy behavior; otherwise kwargs for the BGP health check."""
    raw = getattr(device, 'bgp_baseline', None)
    if raw is None:
        return None
    try:
        baseline = validate(raw, device.id)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return dict(reason='baseline_invalid')
    if baseline['intent'] == 'not_applicable':
        return dict(reason='baseline_not_applicable', excluded=True)
    required = [p for p in baseline['neighbors'] if p['required']]
    if not required:
        return dict(reason='baseline_incomplete')
    if not usable or not result.dataset_status.get('bgp_baseline_complete'):
        return dict(reason='baseline_evidence_missing')
    observed = {}
    for p in result.bgp:
        observed.setdefault((p.context, p.neighbor, afi(p.afi_safi)), []).append(p)
    values, details = [], []
    for expected in required:
        key = (expected['vrf'], expected['neighbor'], expected['afi_safi'])
        matches = observed.get(key, [])
        reason = 'baseline_neighbor_healthy'
        if not matches:
            value, reason = 0, 'baseline_neighbor_missing'
        elif any(str(p.peer_as) != str(expected['peer_as']) for p in matches):
            value, reason = 0, 'baseline_as_mismatch'
        elif not all(p.up for p in matches):
            value, reason = 0, 'neighbor_down'
        else:
            value = 100
        values.append(value)
        details.append(dict(neighbor=expected['neighbor'], context=expected['vrf'],
                            afi_safi=expected['afi_safi'], peer_as=expected['peer_as'], reason=reason))
    return dict(values=values, reason='approved_bgp_baseline', details=details)
