"""Validated inventory hot reload, independent of device collection."""
import os
import threading
from datetime import datetime, timezone

from .inventory import load_inventory
from .lifecycle import can_collect, policy


def interval_seconds():return max(30,int(os.getenv('AIOPS_INVENTORY_REFRESH_INTERVAL','60')))


class InventoryRuntime:
    def __init__(self,collector,metadata):
        self.collector=collector
        self.lock=threading.Lock()
        self.status={**metadata,'status':'ok','generation':1,'last_attempt_at':metadata.get('loaded_at'),
                     'last_success_at':metadata.get('loaded_at'),'last_error':None,
                     'refresh_interval_seconds':interval_seconds(),'last_change':{'added':[],'removed':[],'updated':[]}}

    def snapshot(self):
        devices,_=self.collector.snapshot()
        return {**self.status,'device_count':len(devices),'devices_with_id':sum(bool(d.id) for d in devices),
                'collection_enabled_count':sum(can_collect(d) for d in devices),
                'health_included_count':sum(policy(d)['health_included'] for d in devices),
                'refresh_policy':'Nautobot hot reload; in-flight sessions may finish but obsolete results are discarded'}

    def refresh(self):
        if self.status.get('configured_source')!='nautobot':return self.snapshot()
        if not self.lock.acquire(blocking=False):return self.snapshot()
        stamp=lambda:datetime.now(timezone.utc).isoformat()
        attempted=stamp()
        try:
            devices=load_inventory(allow_fallback=False)
            ids=[d.id for d in devices];names=[d.name for d in devices]
            if any(not i for i in ids) or len(set(ids))!=len(ids) or len(set(names))!=len(names):
                raise ValueError('Invalid inventory identity')
            before,_=self.collector.snapshot()
            old={d.id:d for d in before};new={d.id:d for d in devices}
            changes={'added':[new[i].name for i in new.keys()-old.keys()],
                     'removed':[old[i].name for i in old.keys()-new.keys()],
                     'updated':[new[i].name for i in new.keys()&old.keys() if new[i]!=old[i]]}
            self.collector.reconcile(devices)
            # Purge removed/stopped PA samples immediately; no device I/O here.
            from .panos_traffic import STORE
            STORE.scope([d for d in devices if can_collect(d)])
            self.status={**self.status,'status':'ok','last_attempt_at':attempted,'last_success_at':stamp(),
                         'loaded_at':stamp(),'last_error':None,'effective_source':'nautobot',
                         'generation':self.status['generation']+int(any(changes.values())),
                         'last_change':changes if any(changes.values()) else self.status['last_change']}
        except Exception as exc:
            self.status={**self.status,'status':'error','last_attempt_at':attempted,'last_error':type(exc).__name__}
        finally:self.lock.release()
        return self.snapshot()
