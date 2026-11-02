"""Firewall workspace API; device access is always constrained by Nautobot identity."""
from __future__ import annotations

import csv
import io
import json
from typing import Literal
from fastapi import APIRouter, HTTPException, Query, Header
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field
from . import firewall_policy as policy
from . import config_management as management
from . import firewall_execution as execution


class Flow(BaseModel):
    source_ip: str = Field(max_length=64)
    destination_ip: str = Field(max_length=64)
    protocol: Literal['any','tcp','udp','icmp','icmpv6'] = 'any'
    source_port: int | None = Field(default=None,ge=0,le=65535)
    destination_port: int | None = Field(default=None,ge=0,le=65535)
    application: str | None = Field(default=None,max_length=128)
    ingress_interface: str | None = Field(default=None,max_length=128)
    vrf: str = Field(default='all',max_length=128)


class Preview(BaseModel):
    device_id: str = Field(max_length=64)
    version_id: str = Field(max_length=64)
    rule_id: str = Field(max_length=64)
    operation: Literal['disable','enable','action','source','destination','service','move_before','move_after']
    values: list[str] = Field(default_factory=list,max_length=100)
    actor: str = Field(min_length=1,max_length=100)
    reason: str = Field(default='',max_length=1000)
    flow: Flow | None = None


class Execute(BaseModel):
    review_id: str = Field(max_length=64)
    confirm_device: str = Field(max_length=255)
    confirm_full_commit: bool = False


def router(settings):
    routes=APIRouter(tags=['Firewall policy'])

    def run(fn,*args):
        if not policy.SLOTS.acquire(blocking=False):raise HTTPException(429,'Two firewall analysis requests are running; retry shortly')
        try:return fn(*args)
        except ValueError as exc:raise HTTPException(409,str(exc)) from None
        except PermissionError as exc:raise HTTPException(403,str(exc)) from None
        except OSError:raise HTTPException(503,'Policy evidence or analysis service unavailable; existing backups are retained') from None
        finally:policy.SLOTS.release()

    @routes.get('/firewall',response_class=HTMLResponse)
    @routes.get('/firewall/',response_class=HTMLResponse)
    def page():
        from .firewall_view import FIREWALL_PAGE
        return FIREWALL_PAGE

    @routes.get('/api/firewall/devices')
    def devices():return run(policy.device_list,settings)

    @routes.get('/api/firewall/versions')
    def versions(device_id: str):
        run(policy.target,settings,device_id)
        return management.list_backups(device_id,1000)

    @routes.get('/api/firewall/inventory')
    def inventory(device_id: str,version_id: str | None=None):return run(policy.inventory,settings,device_id,version_id)

    @routes.post('/api/firewall/preview')
    def preview(request: Preview):return run(policy.preview,settings,request.model_dump())

    @routes.get('/api/firewall/reviews')
    def reviews(device_id: str):
        run(policy.target,settings,device_id)
        return {'records':policy.reviews(device_id)}

    @routes.get('/api/firewall/reviews/{review_id}')
    def review(review_id: str):return run(policy.get_review,settings,review_id)

    @routes.get('/firewall/reviews/{review_id}',response_class=HTMLResponse)
    def report_page(review_id: str,lang: Literal['zh','en']='zh'):
        from .firewall_report import render
        return render(run(policy.get_review,settings,review_id),lang)

    @routes.get('/api/firewall/reviews/{review_id}/report')
    def report(review_id: str):
        result=run(policy.get_review,settings,review_id)
        return Response(json.dumps(result,ensure_ascii=False,indent=2),media_type='application/json',
                        headers={'Content-Disposition':'attachment; filename="firewall-review.json"'})

    @routes.get('/api/firewall/execution-capability')
    def capability(device_id: str):
        run(policy.target,settings,device_id)
        return {'enabled':execution.enabled(device_id),'mode':'explicit_operator_token_and_device_allowlist',
                'requires':['fresh_preview','unchanged_running','empty_candidate','device_locks','explicit_full_commit_confirmation'],
                'traffic_verification':'separate'}

    @routes.post('/api/firewall/execute',status_code=202)
    def execute(request: Execute,x_firewall_operator_token: str | None=Header(default=None)):
        if not request.confirm_full_commit:raise HTTPException(400,'Explicit full commit confirmation required')
        try:return run(execution.start,settings,request.review_id,request.confirm_device,x_firewall_operator_token)
        except PermissionError as exc:raise HTTPException(403,str(exc)) from None

    @routes.get('/api/firewall/executions/{job_id}')
    def job(job_id: str):
        result=run(execution.state,job_id)
        run(policy.target,settings,result['device_id'])
        return result

    @routes.get('/api/firewall/export')
    def export(device_id: str,version_id: str | None=None,kind: Literal['policies','nat_rules','objects','findings']='policies'):
        result=run(policy.inventory,settings,device_id,version_id)
        fields={'policies':['id','context','position','rule_name','source_addresses','destination_addresses','services','applications','action','enabled'],
                'nat_rules':['id','context','position','rule_name','original_sources','original_destinations','original_services','translated_sources','translated_destinations','translated_services','enabled'],
                'objects':['id','context','name','type','value'],
                'findings':['id','context','severity','category','policy_ids','detail','requires_review']}[kind]
        out=io.StringIO();writer=csv.writer(out);writer.writerow(['device_id','device','snapshot_id',*fields])
        def safe(value):
            text=json.dumps(value,ensure_ascii=False) if isinstance(value,(dict,list)) else str(value if value is not None else '')
            return "'"+text if text.lstrip().startswith(('=','+','-','@')) else text
        for row in result[kind]:writer.writerow([safe(result[k]) for k in ['device_id','device','snapshot_id']]+[safe(row.get(f)) for f in fields])
        return Response('\ufeff'+out.getvalue(),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="firewall-'+kind+'.csv"'})

    return routes
