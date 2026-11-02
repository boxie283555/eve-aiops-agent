"""Q3 configuration lifecycle API. Raw backups stay private on disk."""
from __future__ import annotations

import asyncio
import sqlite3
from typing import Literal
from fastapi import APIRouter, HTTPException, Query, Header
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from . import config_history as history
from . import config_management as management


class BackupRequest(BaseModel):
    devices: list[str] = Field(default_factory=list, max_length=100)
    exercise_id: str | None = Field(default=None, min_length=1, max_length=100)
    phase: Literal['observation','baseline','change','rollback'] = 'observation'
    actor: str = Field(default='operator',min_length=1,max_length=100)


class DailySchedule(BaseModel):
    enabled: bool = True
    time: str = Field(pattern=r'^\d{2}:\d{2}$')
    timezone: str = Field(min_length=1,max_length=100)
    devices: list[str] = Field(default_factory=list,max_length=100)
    actor: str = Field(min_length=1,max_length=100)


class RestorePreview(BaseModel):
    device: str
    version_id: str
    actor: str = Field(min_length=1,max_length=100)
    change_ref: str = Field(min_length=1,max_length=100)
    exercise_id: str | None = Field(default=None,min_length=1,max_length=100)


class RestoreExecution(BaseModel):
    preview_id: str = Field(min_length=1,max_length=100)
    confirm_device: str = Field(min_length=1,max_length=100)


class Resolution(BaseModel):
    actor: str = Field(min_length=1,max_length=100)
    reason: str = Field(min_length=1,max_length=1000)
    reference: str | None = Field(default=None,max_length=100)


class PlannedChange(BaseModel):
    id: str = Field(min_length=1,max_length=100)
    device: str
    owner: str = Field(min_length=1,max_length=100)
    starts_at: float
    ends_at: float
    contexts: list[str] = Field(min_length=1,max_length=100)


def router(settings, schedule_changed=None):
    routes=APIRouter(prefix='/config',tags=['Configuration history'])

    @routes.get('/ui',response_class=HTMLResponse)
    def page():
        from .config_view import CONFIG_PAGE
        return CONFIG_PAGE

    @routes.get('/devices')
    async def devices():
        try:rows=await asyncio.to_thread(history.current_lab_devices,settings)
        except ValueError as exc:raise HTTPException(400,str(exc)) from None
        return {'scope':'lab','devices':[{'id':d.id,'name':d.name,'host':d.host,'platform':d.platform,
                 'site':d.site,'restore_supported':management.restore_supported(d)} for d in rows]}

    @routes.get('/schedule')
    def schedule():return management.schedule_info()

    @routes.put('/schedule')
    async def set_schedule(request: DailySchedule):
        value=request.model_dump(exclude={'actor'})
        try:
            result=await asyncio.to_thread(management.save_schedule,settings,value,request.actor)
            if schedule_changed:schedule_changed()
            return result
        except ValueError as exc:raise HTTPException(400,str(exc)) from None

    @routes.get('/history')
    def observations(device_id: str, start: float | None = None, end: float | None = None,
                     limit: int = Query(100,ge=1,le=1000), offset: int = Query(0,ge=0)):
        try:return management.history(device_id,start,end,limit,offset)
        except ValueError as exc:raise HTTPException(400,str(exc)) from None

    @routes.get('/compare')
    def compare(device_id: str, before_at: float, after_at: float):
        try:return management.compare(device_id,before_at,after_at)
        except ValueError as exc:raise HTTPException(400,str(exc)) from None

    @routes.get('/audit')
    def audit(device_id: str | None = None, limit: int = Query(100,ge=1,le=1000), offset: int = Query(0,ge=0)):
        return management.list_audit(device_id,limit,offset)

    @routes.post('/restore/preview')
    async def preview(request: RestorePreview):
        try:return await asyncio.to_thread(management.preview_restore,settings,request.device,request.version_id,
                                           request.actor,request.change_ref,request.exercise_id)
        except ValueError as exc:raise HTTPException(409,str(exc)) from None
        except Exception:raise HTTPException(502,'Restore preview failed; inspect operation audit') from None

    @routes.post('/restore/execute')
    async def execute(request: RestoreExecution, x_config_restore_token: str | None = Header(default=None)):
        if not management.authorize_restore(x_config_restore_token):
            management.audit('restore_execute','unauthenticated',status='denied',preview_id=request.preview_id)
            raise HTTPException(403,'Recovery operator token required')
        try:return await asyncio.to_thread(management.execute_restore,settings,request.preview_id,request.confirm_device)
        except ValueError as exc:raise HTTPException(409,str(exc)) from None
        except Exception:raise HTTPException(502,'Recovery requires inspection; check operation audit and EOS timer') from None

    @routes.post('/backup')
    async def backup(request: BackupRequest | None = None):
        request=request or BackupRequest()
        if request.phase in {'change','rollback'} and not request.exercise_id:
            raise HTTPException(400,'Exercise change/rollback requires an exercise_id')
        try:
            result=await asyncio.to_thread(management.tracked_backup,settings,request.devices,request.exercise_id,request.phase,request.actor)
        except ValueError as exc:
            raise HTTPException(400,str(exc)) from None
        if result['status']=='busy':raise HTTPException(409,'Configuration backup already running')
        return result

    @routes.get('/backups')
    def backups(device_id: str | None = None, limit: int = Query(default=100,ge=1,le=1000)):
        with history.connect() as db:
            sql='''SELECT v.id,device_id,device,platform,created_at,fingerprint,exercise_id,phase,
                COALESCE(o.last_observed_at,v.created_at) AS last_observed_at
                FROM versions v LEFT JOIN version_observations o ON o.version_id=v.id'''
            args=[]
            if device_id:sql+=' WHERE device_id=?';args.append(device_id)
            return {'versions':[dict(r) for r in db.execute(sql+' ORDER BY seq DESC LIMIT ?',args+[limit])]}

    @routes.post('/drift/detect')
    async def detect():
        return await asyncio.to_thread(history.detect_drift)

    @routes.get('/drift')
    def drift(unowned: bool | None = None, status: Literal['open','resolved'] | None = None,
              origin: Literal['observed','exercise'] | None = None, limit: int = Query(default=100,ge=1,le=1000),
              device: str | None = None, offset: int = Query(default=0,ge=0)):
        return history.list_drifts(unowned,status,origin,limit,device,offset)

    @routes.get('/drift/ui',response_class=HTMLResponse)
    def drift_page():
        from .drift_view import DRIFT_PAGE
        return DRIFT_PAGE

    @routes.get('/drift/{drift_id}')
    def drift_details(drift_id: str, full: bool = False):
        try:return history.drift_detail(drift_id,full)
        except KeyError:raise HTTPException(404,'Drift not found') from None
        except (ValueError,OSError):raise HTTPException(409,'Drift backup evidence unavailable or failed integrity validation') from None

    @routes.post('/drift/{drift_id}/resolve')
    def resolve(drift_id: str, request: Resolution):
        try:return history.resolve_drift(drift_id,request.actor,request.reason,request.reference)
        except KeyError:raise HTTPException(404,'Drift not found') from None
        except ValueError as exc:raise HTTPException(400,str(exc)) from None

    @routes.post('/changes')
    async def change(request: PlannedChange):
        try:
            devices=await asyncio.to_thread(history.current_lab_devices,settings,[request.device])
            return history.register_change(request.id,devices[0].id,request.owner,request.starts_at,request.ends_at,request.contexts)
        except ValueError as exc:raise HTTPException(400,str(exc)) from None
        except sqlite3.IntegrityError:raise HTTPException(409,'Change reference already registered') from None

    return routes
