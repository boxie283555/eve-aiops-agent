"""Network discovery wizard API; no implicit recursive scans."""
from __future__ import annotations
from urllib.parse import urlsplit
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from .discovery import Discovery, get, history, database, audit
from .discovery_view import PAGE


class Plan(BaseModel):
    targets: str = Field(default='',max_length=16384)
    device_ids: list[str] = Field(default_factory=list,max_length=256)
    actor: str = Field(min_length=1,max_length=100)

class Selection(BaseModel):
    ids: list[str] = Field(min_length=1,max_length=32)
    actor: str = Field(min_length=1,max_length=100)
    review_id: str | None = Field(default=None,max_length=64)

class Entry(BaseModel):
    id: str = Field(max_length=64)
    name: str = Field(min_length=1,max_length=64)

class Registration(BaseModel):
    entries: list[Entry] = Field(min_length=1,max_length=32)
    profile_id: str = Field(max_length=64)
    location: str = Field(max_length=64)
    platform: str = Field(max_length=64)
    role: str = Field(max_length=64)
    device_type: str = Field(max_length=64)
    actor: str = Field(min_length=1,max_length=100)


def router(settings,collector):
    routes=APIRouter(prefix='/discovery',tags=['Network discovery'])
    service=Discovery(settings,collector)
    def failure(fn,args,exc):
        if fn.__name__ in {'register','probe','enable','baselines','cables'} and args:
            try:audit(args[0],'system',fn.__name__+'_failed',{'error':type(exc).__name__})
            except Exception:pass
    def run(fn,*args):
        try:return fn(*args)
        except ValueError as exc:
            failure(fn,args,exc);raise HTTPException(409,str(exc)) from None
        except Exception as exc:
            failure(fn,args,exc);raise HTTPException(502,'Discovery operation failed; check Nautobot availability and job audit') from None
    def origin(request):
        if request.headers.get('origin') and urlsplit(request.headers['origin']).netloc!=request.url.netloc:
            raise HTTPException(403,'Same-origin operation required')
    @routes.get('',response_class=HTMLResponse)
    def page():return PAGE
    @routes.get('/options')
    def options():return run(service.options)
    @routes.get('/jobs')
    def jobs():return history()
    @routes.post('/jobs')
    def plan(body:Plan,request:Request):
        origin(request);return run(service.plan,body.targets,body.device_ids,body.actor)
    @routes.get('/jobs/{job_id}')
    def job(job_id:str):return run(get,job_id)
    @routes.get('/jobs/{job_id}/audit')
    def job_audit(job_id:str):
        with database() as db:return [dict(r) for r in db.execute('SELECT at,actor,action,details FROM audit WHERE job_id=? ORDER BY id',(job_id,))]
    @routes.post('/jobs/{job_id}/register')
    def register(job_id:str,body:Registration,request:Request):
        origin(request);return run(service.register,job_id,[e.model_dump() for e in body.entries],body.profile_id,body.location,body.platform,body.role,body.device_type,body.actor)
    @routes.post('/jobs/{job_id}/probe')
    def probe(job_id:str,body:Selection,request:Request):
        origin(request);return run(service.probe,job_id,body.ids,body.actor)
    @routes.post('/jobs/{job_id}/enable')
    def enable(job_id:str,body:Selection,request:Request):
        origin(request);return run(service.enable,job_id,body.ids,body.actor)
    @routes.get('/jobs/{job_id}/topology')
    def topology(job_id:str):return run(service.topology,job_id)
    @routes.post('/jobs/{job_id}/cables')
    def cables(job_id:str,body:Selection,request:Request):
        origin(request);return run(service.cables,job_id,body.ids,body.actor)
    @routes.post('/jobs/{job_id}/baselines/preview')
    def preview(job_id:str,body:Selection,request:Request):
        origin(request);return run(service.baselines,job_id,body.ids,body.actor,False)
    @routes.post('/jobs/{job_id}/baselines/approve')
    def approve(job_id:str,body:Selection,request:Request):
        origin(request);return run(service.baselines,job_id,body.ids,body.actor,True,body.review_id)
    return routes
