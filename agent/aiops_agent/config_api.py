"""Q3 configuration lifecycle API. Raw backups stay private on disk."""
from __future__ import annotations

import asyncio
import sqlite3
from typing import Literal
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from . import config_history as history


class BackupRequest(BaseModel):
    devices: list[str] = Field(default_factory=list, max_length=100)
    exercise_id: str | None = Field(default=None, min_length=1, max_length=100)
    phase: Literal['observation','baseline','change','rollback'] = 'observation'


class Resolution(BaseModel):
    actor: str = Field(min_length=1,max_length=100)
    reason: str = Field(min_length=1,max_length=1000)


class PlannedChange(BaseModel):
    id: str = Field(min_length=1,max_length=100)
    device: str
    owner: str = Field(min_length=1,max_length=100)
    starts_at: float
    ends_at: float
    contexts: list[str] = Field(min_length=1,max_length=100)


def router(settings):
    routes=APIRouter(prefix='/config',tags=['Configuration history'])

    @routes.post('/backup')
    async def backup(request: BackupRequest | None = None):
        request=request or BackupRequest()
        if request.phase in {'change','rollback'} and not request.exercise_id:
            raise HTTPException(400,'Exercise change/rollback requires an exercise_id')
        try:
            result=await asyncio.to_thread(history.backup,settings,request.devices,request.exercise_id,request.phase)
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
              origin: Literal['observed','exercise'] | None = None, limit: int = Query(default=100,ge=1,le=1000)):
        return history.list_drifts(unowned,status,origin,limit)

    @routes.post('/drift/{drift_id}/resolve')
    def resolve(drift_id: str, request: Resolution):
        try:return history.resolve_drift(drift_id,request.actor,request.reason)
        except KeyError:raise HTTPException(404,'Drift not found') from None

    @routes.post('/changes')
    async def change(request: PlannedChange):
        try:
            devices=await asyncio.to_thread(history.current_lab_devices,settings,[request.device])
            return history.register_change(request.id,devices[0].id,request.owner,request.starts_at,request.ends_at,request.contexts)
        except ValueError as exc:raise HTTPException(400,str(exc)) from None
        except sqlite3.IntegrityError:raise HTTPException(409,'Change reference already registered') from None

    return routes
