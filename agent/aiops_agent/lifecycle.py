"""Administrative lifecycle policy, distinct from observed device reachability."""
from datetime import datetime, timezone

STOPPED = {'offline','decommissioning','decommissioned','retired','inventory','planned','staged'}


def policy(device, now=None):
    now = now or datetime.now(timezone.utc)
    status = str(getattr(device,'status','Active') or 'Active')
    state = status.strip().lower()
    monitoring = getattr(device,'monitoring_enabled',True) and state not in STOPPED
    excluded = bool(getattr(device,'health_excluded',False))
    until = getattr(device,'exclusion_until',None)
    expiry_error = False
    if excluded and until:
        try:
            deadline = datetime.fromisoformat(until.replace('Z','+00:00'))
            if deadline.tzinfo is None:raise ValueError('Timezone required')
            excluded = deadline > now
        except (TypeError,ValueError):
            # Invalid expiry cannot silently remove a device from scoring forever.
            excluded = False
            expiry_error = True
    included = monitoring and state != 'maintenance' and not excluded
    reason = ('monitoring_disabled' if not getattr(device,'monitoring_enabled',True) else
              'lifecycle_'+state if state in STOPPED or state=='maintenance' else
              'manual_exclusion' if excluded else None)
    return dict(status=status,monitoring_enabled=bool(monitoring),health_included=bool(included),
                exclusion_code=reason,exclusion_reason=getattr(device,'exclusion_reason','') or '',
                exclusion_owner=getattr(device,'exclusion_owner','') or '',exclusion_until=until,
                expiry_error=expiry_error)


def can_collect(device):
    return bool(getattr(device,'host',None)) and policy(device)['monitoring_enabled']


VISIBLE_STATUSES = {'active', 'failed', 'maintenance'}


def operational_status(status):
    """Display scope only; never changes collection or scoring policy."""
    return str(status or '').strip().lower() in VISIBLE_STATUSES


def prometheus_inventory(devices):
    from prometheus_client import CollectorRegistry, Gauge, generate_latest
    from .inventory import device_classification
    registry = CollectorRegistry()
    metric = Gauge('aiops_device_inventory_info', 'Current authoritative inventory and display lifecycle.',
                   ['device','site','status','lifecycle_group','manufacturer','device_type','hardware_group'],registry=registry)
    for device in devices:
        manufacturer,kind = device_classification(device)
        metric.labels(device.name,device.site,device.status,
                      'operational' if operational_status(device.status) else 'non_operational',
                      manufacturer,kind,manufacturer+' / '+kind).set(1)
    return generate_latest(registry)
