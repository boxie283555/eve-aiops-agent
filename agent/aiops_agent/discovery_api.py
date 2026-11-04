"""Network discovery wizard API; no implicit recursive scans."""
from __future__ import annotations
from urllib.parse import urlsplit
from typing import Literal
from fastapi import APIRouter, HTTPException, Request, UploadFile, File
from fastapi.responses import HTMLResponse, StreamingResponse
from io import BytesIO
import ipaddress
import socket
import struct
from pydantic import BaseModel, Field
from .discovery import Discovery, get, history, database, audit, save
from .discovery_view import PAGE
from .nautobot import NautobotError


class Plan(BaseModel):
    transport: Literal['ssh','telnet'] = 'ssh'
    targets: str = Field(default='',max_length=16384)
    device_ids: list[str] = Field(default_factory=list,max_length=256)
    actor: str = Field(min_length=1,max_length=100)
    imports: list[dict[str,object]] = Field(default_factory=list,max_length=256)

class Selection(BaseModel):
    ids: list[str] = Field(min_length=1,max_length=32)
    actor: str = Field(min_length=1,max_length=100)
    review_id: str | None = Field(default=None,max_length=64)

class Entry(BaseModel):
    id: str = Field(max_length=64)
    name: str = Field(min_length=1,max_length=64)
    serial: str | None = Field(default=None,max_length=100)
    asset_tag: str | None = Field(default=None,max_length=100)
    comments: str | None = Field(default=None,max_length=2000)
    custom_fields: dict[str,object] = Field(default_factory=dict,max_length=50)
    credentials: dict[str,str] = Field(default_factory=dict,max_length=4)

class ConnectionEntry(BaseModel):
    id: str = Field(min_length=1,max_length=64)
    transport: Literal['ssh','telnet']
    port: int = Field(ge=1,le=65535)
    expected_transport: Literal['ssh','telnet']
    expected_port: int = Field(ge=1,le=65535)

class Connections(BaseModel):
    entries: list[ConnectionEntry] = Field(min_length=1,max_length=32)
    actor: str = Field(min_length=1,max_length=100)

class Registration(BaseModel):
    entries: list[Entry] = Field(min_length=1,max_length=32)
    profile_id: str = Field(max_length=64)
    location: str = Field(max_length=64)
    platform: str = Field(max_length=64)
    role: str = Field(max_length=64)
    device_type: str = Field(max_length=64)
    actor: str = Field(min_length=1,max_length=100)


def is_local_upload_request(request:Request)->bool:
    """Allow loopback and the Docker host gateway used by SSH local forwarding."""
    if request.client is None:return False
    host=request.client.host
    if host in {'127.0.0.1','::1'}:return True
    try:
        for row in open('/proc/net/route',encoding='ascii').read().splitlines()[1:]:
            fields=row.split()
            if len(fields)>3 and fields[1]=='00000000' and int(fields[3],16)&2:
                gateway=socket.inet_ntoa(struct.pack('<L',int(fields[2],16)))
                if host==gateway:return True
    except (OSError,ValueError,struct.error):
        return False
    return False


def router(settings,collector):
    routes=APIRouter(prefix='/discovery',tags=['Network discovery'])
    service=Discovery(settings,collector)
    def failure(fn,args,exc):
        if fn.__name__ in {'register','connections','probe','enable','baselines','cables'} and args:
            try:audit(args[0],'system',fn.__name__+'_failed',{'error':str(exc)[:800] if isinstance(exc,NautobotError) else type(exc).__name__})
            except Exception:pass
            if fn.__name__=='register' and isinstance(exc,NautobotError) and len(args)>1:
                try:
                    job=get(args[0]);candidate_ids={entry['id'] for entry in args[1]}
                    for candidate in job['candidates']:
                        if candidate['id'] in candidate_ids:
                            candidate['registration_error']=str(exc)[:800]
                    save(job)
                except Exception:pass
    def run(fn,*args):
        try:return fn(*args)
        except ValueError as exc:
            failure(fn,args,exc);raise HTTPException(409,str(exc)) from None
        except NautobotError as exc:
            failure(fn,args,exc);raise HTTPException(502,str(exc)) from None
        except Exception as exc:
            failure(fn,args,exc);raise HTTPException(502,'Discovery operation failed; check Nautobot availability and job audit') from None
    def origin(request):
        if request.headers.get('origin') and urlsplit(request.headers['origin']).netloc!=request.url.netloc:
            raise HTTPException(403,'Same-origin operation required')
    @routes.get('',response_class=HTMLResponse)
    def page():return PAGE
    @routes.get('/options')
    def options():return run(service.options)
    @routes.get('/import-template')
    def import_template():
        from openpyxl import Workbook
        workbook=Workbook();sheet=workbook.active;sheet.title='Devices'
        sheet.append(['management_ip','name','port','transport','serial','asset_tag','comments','username','password','enable_secret','use_snmp','snmp_community','snmp_host','snmp_port'])
        sheet.append(['192.0.2.10','lab-switch-01',22,'ssh','','','','','','',False,'','',''])
        sheet.freeze_panes='A2';sheet.auto_filter.ref=sheet.dimensions
        for col,width in {'A':20,'B':24,'C':12,'D':14,'E':22,'F':22,'G':32,'H':20,'I':24,'J':24,'K':14,'L':26,'M':22,'N':14}.items():sheet.column_dimensions[col].width=width
        output=BytesIO();workbook.save(output);output.seek(0)
        return StreamingResponse(output,media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            headers={'Content-Disposition':'attachment; filename="nautobot-device-import-template.xlsx"'})
    @routes.post('/import-xlsx')
    async def import_xlsx(request:Request,file:UploadFile=File(...)):
        if not is_local_upload_request(request):
            raise HTTPException(403,'含凭据的 Excel 仅允许通过 SSH 本地转发上传')
        if not (file.filename or '').lower().endswith('.xlsx'):raise HTTPException(415,'仅支持 .xlsx 文件；请将旧版 .xls 另存为 .xlsx')
        content=await file.read(5*1024*1024+1)
        if len(content)>5*1024*1024:raise HTTPException(413,'文件不能超过 5 MB')
        try:
            from openpyxl import load_workbook
            workbook=load_workbook(BytesIO(content),read_only=True,data_only=True)
            sheet=workbook.active;rows=sheet.iter_rows(values_only=True);headers=[str(v).strip() if v is not None else '' for v in next(rows)]
            allowed={'management_ip','name','port','transport','serial','asset_tag','comments','username','password','enable_secret','use_snmp','snmp_community','snmp_host','snmp_port'}
            if 'management_ip' not in headers or any(h not in allowed for h in headers if h):raise ValueError('表头不支持；请下载模板并使用模板字段名')
            named=[h for h in headers if h]
            if len(named)!=len(set(named)):raise ValueError('表头不能重复')
            result=[]
            for number,values in enumerate(rows,start=2):
                if not any(v is not None and str(v).strip() for v in values):continue
                if len(result)>=256:raise ValueError('每个文件最多导入 256 台设备')
                row={headers[i]:values[i] for i in range(min(len(headers),len(values))) if headers[i]}
                host=str(row.get('management_ip') or '').strip()
                try:row['management_ip']=str(ipaddress.ip_address(host))
                except ValueError:raise ValueError(f'第 {number} 行 management_ip 无效') from None
                if row.get('name') is not None:row['name']=str(row['name']).strip()
                transport=str(row.get('transport') or 'ssh').strip().lower()
                if transport not in {'ssh','telnet'}:raise ValueError(f'第 {number} 行 transport 只能是 ssh 或 telnet')
                row['transport']=transport
                try:row['port']=int(row.get('port') or (23 if transport=='telnet' else 22))
                except (ValueError,TypeError):raise ValueError(f'第 {number} 行 port 必须是整数') from None
                if not 1<=row['port']<=65535:raise ValueError(f'第 {number} 行 port 超出范围')
                if 'use_snmp' in row:
                    value=row['use_snmp']
                    if isinstance(value,bool):pass
                    elif str(value).strip().lower() in {'true','yes','1','是'}:row['use_snmp']=True
                    elif str(value).strip().lower() in {'false','no','0','否',''}:row['use_snmp']=False
                    else:raise ValueError(f'第 {number} 行 use_snmp 应为 true/false')
                for key in ('serial','asset_tag','comments','snmp_community_env','snmp_host'):
                    if row.get(key) is not None:row[key]=str(row[key]).strip()
                for key in ('username','password','enable_secret','snmp_community'):
                    if row.get(key) is not None:row[key]=str(row[key])
                if bool(row.get('username')) != bool(row.get('password')):
                    raise ValueError(f'第 {number} 行 username 与 password 必须同时填写')
                if row.get('use_snmp') is True and not row.get('snmp_community'):
                    raise ValueError(f'第 {number} 行启用 SNMP 时必须填写 snmp_community')
                if row.get('snmp_port') not in (None,''):
                    try:row['snmp_port']=int(row['snmp_port'])
                    except (ValueError,TypeError):raise ValueError(f'第 {number} 行 snmp_port 必须是整数') from None
                    if not 1<=row['snmp_port']<=65535:raise ValueError(f'第 {number} 行 snmp_port 超出范围')
                result.append(row)
            workbook.close()
            if not result:raise ValueError('工作表没有可导入的设备行')
            keys=[(r['management_ip'],r['port']) for r in result]
            if len(keys)!=len(set(keys)):raise ValueError('文件中存在重复的 management_ip + port')
            return {'rows':result,'count':len(result)}
        except HTTPException:raise
        except Exception as exc:raise HTTPException(400,str(exc) if isinstance(exc,ValueError) else '无法读取 Excel 文件') from None
    @routes.get('/jobs')
    def jobs():return history()
    @routes.post('/jobs')
    def plan(body:Plan,request:Request):
        origin(request);return run(service.plan,body.targets,body.device_ids,body.actor,body.transport,body.imports)
    @routes.get('/jobs/{job_id}')
    def job(job_id:str):return run(get,job_id)
    @routes.get('/jobs/{job_id}/audit')
    def job_audit(job_id:str):
        with database() as db:return [dict(r) for r in db.execute('SELECT at,actor,action,details FROM audit WHERE job_id=? ORDER BY id',(job_id,))]
    @routes.post('/jobs/{job_id}/connections')
    def connections(job_id:str,body:Connections,request:Request):
        origin(request);return run(service.connections,job_id,[e.model_dump() for e in body.entries],body.actor)
    @routes.post('/jobs/{job_id}/register')
    def register(job_id:str,body:Registration,request:Request):
        origin(request)
        if any(e.credentials for e in body.entries) and not is_local_upload_request(request):
            raise HTTPException(403,'含凭据的设备登记仅允许通过 SSH 本地转发提交')
        return run(service.register,job_id,[e.model_dump() for e in body.entries],body.profile_id,body.location,body.platform,body.role,body.device_type,body.actor)
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
