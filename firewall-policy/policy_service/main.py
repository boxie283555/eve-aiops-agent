from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from .analysis import analyze

app=FastAPI(title='Internal firewall policy analysis',docs_url=None,redoc_url=None)

class Evidence(BaseModel):
    device_id: str = Field(min_length=1,max_length=64)
    device_name: str = Field(min_length=1,max_length=255)
    snapshot_id: str = Field(min_length=1,max_length=128)
    config_xml: str = Field(min_length=1,max_length=4000000)

@app.get('/health')
def health():return {'status':'ok','mode':'offline-analysis','schema_version':'1.0'}

@app.post('/analyze')
def analysis(body: Evidence):
    try:return analyze(body.device_id,body.device_name,body.snapshot_id,body.config_xml)
    except (ValueError,KeyError,IndexError) as exc:
        raise HTTPException(422,'Policy evidence could not be analyzed: '+type(exc).__name__) from None
