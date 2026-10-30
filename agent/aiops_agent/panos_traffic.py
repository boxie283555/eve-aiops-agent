"""Independent lab PA byte sampling; never wait on a device during a scrape."""
from __future__ import annotations

import copy
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from prometheus_client.core import GaugeMetricFamily

INTERVAL = 60
TTL = 180


def parse_counters(root):
    """Prefer hardware counters for physical/aggregate ports, ifnet for logical ports."""
    result = root.find('result')
    if result is None:
        raise ValueError('Missing counter result')
    rows = {}
    for path, rx, tx, layer in [('./hw/entry', 'port/rx-bytes', 'port/tx-bytes', 'hardware'),
                                ('./ifnet//entry', 'ibytes', 'obytes', 'logical')]:
        for entry in result.findall(path):
            name = entry.findtext('name')
            if not name or name in rows:
                continue
            values = [entry.findtext(rx), entry.findtext(tx)]
            if not all(v is not None and v.strip().isdigit() for v in values):
                continue
            rows[name] = {'rx_bytes': int(values[0]), 'tx_bytes': int(values[1]), 'layer': layer}
    if not rows:
        raise ValueError('No valid interface byte counters')
    return rows


class TrafficStore:
    def __init__(self):
        self.lock = threading.Lock()
        self.records = {}
        self.scope_error = None

    def scope(self, devices):
        with self.lock:
            self.scope_error = None
            allowed = {(d.id, d.host, d.name) for d in devices}
            self.records = {k: r for k, r in self.records.items()
                            if (k, r['host'], r['device']) in allowed}

    def fail(self, device=None, error='CollectionError'):
        with self.lock:
            if device is None:
                self.scope_error = error
                for row in self.records.values():
                    row.update(status='error', error=error, interfaces={})
            else:
                self.records[device.id] = dict(device_id=device.id, device=device.name, host=device.host,
                                              status='error', error=error, interfaces={})

    def sample(self, device, counters, uptime, mono=None, wall=None):
        mono = time.monotonic() if mono is None else mono
        wall = time.time() if wall is None else wall
        with self.lock:
            previous = self.records.get(device.id, {})
            elapsed = mono - previous.get('_mono', mono)
            continuous = (previous.get('host') == device.host and previous.get('device') == device.name
                          and previous.get('status') in {'ok', 'warming_up'} and 0 < elapsed <= TTL
                          and uptime is not None and previous.get('_uptime') is not None
                          and abs((uptime - previous['_uptime']) - elapsed) <= 15)
            rows = {}
            for name, counter in counters.items():
                old = previous.get('interfaces', {}).get(name, {})
                valid = (continuous and old.get('layer') == counter['layer']
                         and all(counter[d+'_bytes'] >= old.get(d+'_bytes', float('inf')) for d in ('rx', 'tx')))
                rows[name] = dict(counter, rx_bps=None, tx_bps=None)
                if valid:
                    for direction in ('rx', 'tx'):
                        rows[name][direction+'_bps'] = 8 * (counter[direction+'_bytes'] - old[direction+'_bytes']) / elapsed
            self.records[device.id] = dict(device_id=device.id, device=device.name, host=device.host,
                status='ok' if any(r['rx_bps'] is not None for r in rows.values()) else 'warming_up',
                sampled_at=wall, interfaces=rows, _mono=mono, _uptime=uptime)

    def snapshot(self, mono=None):
        mono = time.monotonic() if mono is None else mono
        with self.lock:
            rows = copy.deepcopy(list(self.records.values()))
            error = self.scope_error
        for row in rows:
            age = mono - row.pop('_mono', float('-inf'))
            row.pop('_uptime', None)
            if row['status'] in {'ok', 'warming_up'} and not 0 <= age <= TTL:
                row.update(status='stale', interfaces={})
        return {'interval_seconds': INTERVAL, 'ttl_seconds': TTL, 'scope': 'Nautobot site=lab, PAN-OS',
                'scope_error': error, 'devices': rows}


STORE = TrafficStore()


def refresh_traffic(settings):
    # Lazy import keeps metric registration independent of collector initialization.
    from .panos_evidence import lab_firewalls, PanosAPI
    from .parser import _parse_panos_uptime
    try:
        devices = lab_firewalls(settings)
        STORE.scope(devices)
    except Exception as exc:
        STORE.fail(error=type(exc).__name__)
        return

    def sample(device):
        try:
            api = PanosAPI(device, settings)
            system = api.command(['show', 'system', 'info'], {})
            uptime = _parse_panos_uptime('uptime: ' + (system.findtext('.//uptime') or ''))
            if uptime is None:
                raise ValueError('Missing uptime for reset detection')
            counters = parse_counters(api.command(['show', 'counter'], {'interface': 'all'}))
            STORE.sample(device, counters, uptime)
        except Exception as exc:
            STORE.fail(device, type(exc).__name__)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(sample, devices))


def add_rates(metric, direction):
    for device in STORE.snapshot()['devices']:
        if device['status'] != 'ok':
            continue
        for name, row in device['interfaces'].items():
            rate = row[direction+'_bps']
            if rate is not None:
                metric.add_sample(metric.name, {'device': device['device'], 'interface': name}, rate)


def overlay_interfaces(device, interfaces):
    if device.platform != 'paloalto_panos' or device.site != 'lab':
        return interfaces
    record = next((r for r in STORE.snapshot()['devices']
                   if r['device_id'] == device.id and r['host'] == device.host and r['device'] == device.name), {})
    rows = []
    for interface in interfaces:
        counter = record.get('interfaces', {}).get(interface['name'], {})
        rows.append({**interface, 'rx_bps': counter.get('rx_bps'), 'tx_bps': counter.get('tx_bps'),
                     'traffic_status': record.get('status', 'unavailable'),
                     'traffic_sampled_at': record.get('sampled_at'), 'traffic_counter_layer': counter.get('layer')})
    return rows


class TrafficCollector:
    def collect(self):
        devices = STORE.snapshot()['devices']
        for suffix, description, value in [
            ('collect_success', 'Latest PA interface byte poll succeeded and is fresh.', lambda d: d['status'] in {'ok', 'warming_up'}),
            ('rates_ready', 'PA has at least one fresh rate from consecutive samples.', lambda d: d['status'] == 'ok'),
            ('last_sample_timestamp_seconds', 'Last successful PA interface byte sample.', lambda d: d.get('sampled_at', 0)),
        ]:
            metric = GaugeMetricFamily('aiops_pa_interface_'+suffix, description, labels=['device'])
            for device in devices:
                metric.add_metric([device['device']], float(value(device)))
            yield metric
        for direction in ('rx', 'tx'):
            metric = GaugeMetricFamily('aiops_pa_interface_'+direction+'_bps',
                'PA interface '+direction+' bits per second over consecutive byte samples; layers overlap.',
                labels=['device', 'interface', 'layer'])
            for device in devices:
                if device['status'] != 'ok':
                    continue
                for name, row in device['interfaces'].items():
                    if row[direction+'_bps'] is not None:
                        metric.add_metric([device['device'], name, row['layer']], row[direction+'_bps'])
            yield metric
