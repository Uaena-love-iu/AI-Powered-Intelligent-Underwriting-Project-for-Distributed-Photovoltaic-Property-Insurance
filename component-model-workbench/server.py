import hashlib, json, threading, uuid
from pathlib import Path
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from concurrent.futures import ThreadPoolExecutor
from config import ROOT, ai_reader, solar_key
from workflow import Workbench
from recognition import identify, compact_key
from urllib.parse import urlparse

app=FastAPI(title='组件型号与抗灾参数工作台',version='1.0')
bench=Workbench(ROOT,reader=ai_reader(),key=solar_key())
pool=ThreadPoolExecutor(max_workers=2)
jobs={};lock=threading.Lock()

@app.middleware('http')
async def same_origin(request,call_next):
    origin=request.headers.get('origin')
    if request.method not in ('GET','HEAD','OPTIONS') and origin:
        allowed={request.url.scheme+'://'+request.headers.get('host','')}
        if origin not in allowed:return JSONResponse({'detail':'只接受工作台自身页面的请求'},status_code=403)
    return await call_next(request)

class Lookup(BaseModel):
    model:str=Field(min_length=3,max_length=100)
    manufacturer:str=Field(default='',max_length=100)
    strategy:str='auto'
    use_agent:bool=True
    material_id:str|None=None

class Feedback(BaseModel):
    verdict:str
    note:str=Field(min_length=3,max_length=2000)
    fields:list[str]=[]

@app.get('/')
def home():return FileResponse(ROOT/'web/index.html')
app.mount('/static',StaticFiles(directory=ROOT/'web'),name='static')

@app.get('/health')
def health():return {'status':'ok','service':'component-model-workbench'}
@app.get('/api/status')
def status():return {**bench.status(),'ocr_verification':'visual-crosscheck-v2'}
@app.get('/api/rules')
def rules(q:str='',brand:str=''):return bench.rules(q[:100],brand[:100])

@app.post('/api/identify')
async def recognize(file:UploadFile=File(...)):
    content=await file.read(10_000_001);await file.close()
    if len(content)>10_000_000:raise HTTPException(413,'文件超过10MB')
    if not content:raise HTTPException(422,'文件为空')
    rid=uuid.uuid4().hex;jobs[rid]={'id':rid,'kind':'identify','state':'running','steps':[]}
    def worker():
        try:
            result=identify(content,Path(file.filename or '上传材料').name,bench.rules_index,bench.reader)
            result['material_id']=rid;result['sha256']=hashlib.sha256(content).hexdigest()
            folder=ROOT/'private/materials';folder.mkdir(exist_ok=True)
            suffix='.pdf' if content.lstrip().startswith(b'%PDF-') else '.img'
            (folder/(rid+suffix)).write_bytes(content)
            (folder/(rid+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            jobs[rid].update(state='done',result=result)
        except Exception as e:jobs[rid].update(state='failed',error=str(e) if isinstance(e,(ValueError,RuntimeError)) else '识别服务暂时不可用')
    pool.submit(worker);return {'job_id':rid}

@app.post('/api/lookup')
def lookup(req:Lookup):
    if req.strategy not in ('auto','local','agent'):raise HTTPException(422,'不支持的查询模式')
    material=None
    if req.material_id:
        if not re_id(req.material_id):raise HTTPException(422,'材料ID无效')
        path=ROOT/'private/materials'/(req.material_id+'.json')
        if not path.exists():raise HTTPException(422,'材料记录不存在')
        parsed=json.loads(path.read_text('utf-8'))
        if parsed.get('recognition_version')=='visual-crosscheck-v2' and not any(compact_key(req.model)==compact_key(c.get('lookup_model') or c['raw_model']) and c.get('auto_query_eligible') for c in parsed['candidates']):
            raise HTTPException(422,'该型号未通过这份图片的自动交叉校验；保留证据缺口，不以猜测型号查询参数')
        material={k:parsed[k] for k in ('material_id','filename','sha256','candidates')}
    rid=uuid.uuid4().hex;jobs[rid]={'id':rid,'kind':'lookup','state':'running','steps':[]}
    def emit(item):jobs[rid]['steps'].append(item)
    def worker():
        try:jobs[rid].update(state='done',result=bench.run(req.model,req.manufacturer,req.strategy,req.use_agent,emit,rid,material))
        except Exception as e:jobs[rid].update(state='failed',error=str(e) if isinstance(e,(ValueError,RuntimeError)) else '查询服务暂时不可用，请查看服务端日志')
    pool.submit(worker);return {'job_id':rid}

def re_id(value):return len(value)==32 and all(x in '0123456789abcdef' for x in value)
@app.get('/api/jobs/{rid}')
def job(rid:str):
    if rid not in jobs:raise HTTPException(404,'任务不存在')
    return jobs[rid]
@app.get('/api/history')
def history():return {'runs':bench.store.history()}
@app.get('/api/history/{rid}')
def previous(rid:str):
    result=bench.store.get(rid)
    if not result:raise HTTPException(404,'记录不存在')
    return result
@app.get('/api/history/{rid}/export')
def export(rid:str):
    result=bench.store.get(rid)
    if not result:raise HTTPException(404,'记录不存在')
    return JSONResponse(result,headers={'Content-Disposition':f'attachment; filename="component-result-{rid[:8]}.json"'})
@app.post('/api/history/{rid}/feedback')
def feedback(rid:str,req:Feedback):
    if req.verdict not in ('verified','rejected','needs_review'):raise HTTPException(422,'反馈类型无效')
    try:return bench.store.feedback(rid,req.verdict,req.note,req.fields)
    except ValueError as e:raise HTTPException(422,str(e)) from None
