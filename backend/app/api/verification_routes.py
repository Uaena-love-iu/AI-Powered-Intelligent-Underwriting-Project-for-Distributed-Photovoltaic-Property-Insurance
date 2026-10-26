import json
from uuid import uuid4
from typing import Annotated
from fastapi import APIRouter, Request, HTTPException, Depends, BackgroundTasks, UploadFile, File, Form, Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from app.storage import CaseRepository
from app.contracts import UnderwritingCase
from app.verification.contracts import Review, FinalReview
from app.verification.store import Store, Conflict
from app.verification.service import actor, review_item, finalize, supplement, resume
from app.verification.engine import now, run_loop, recalculate, report_items
from app.providers import VisionProvider, OcrProvider
from .routes import get_vision_provider
from .ocr_routes import get_ocr_provider

router = APIRouter(prefix='/api/v2/cases', tags=['verification'])


@router.get('/{case_id}/verification')
async def get_state(case_id: str):
    record = await run_in_threadpool(CaseRepository().get_case, case_id)
    if not record:
        raise HTTPException(404,'案件不存在')
    return record


@router.get('/{case_id}/verification/history')
async def history(case_id: str):
    return await run_in_threadpool(Store().history, case_id)


@router.get('/{case_id}/verification/materials/{material_id}')
async def source(case_id: str, material_id: str):
    sources = await run_in_threadpool(Store().sources,case_id)
    item = next((m for m in sources if m.material.material_id==material_id),None)
    if not item:
        raise HTTPException(404,'原件未归档')
    media=item.material.media_type
    if media not in {'image/jpeg','image/png','image/webp','image/gif','application/pdf'}:
        media='application/octet-stream'
    return Response(item.content,media_type=media,headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Content-Security-Policy':'sandbox'})


@router.post('/{case_id}/verification/items/{issue_id}/review')
async def item_review(case_id: str,issue_id: str,body: Review,request: Request):
    try:
        name, mode = actor(request,body.actor)
        case = await run_in_threadpool(review_item,case_id,issue_id,body,name,mode)
        return case.model_dump(mode='json')
    except ValueError as exc:
        raise HTTPException(409,str(exc)) from exc


@router.post('/{case_id}/verification/final')
async def final_review(case_id: str,body: FinalReview,request: Request):
    try:
        name, mode = actor(request,body.actor)
        return (await run_in_threadpool(finalize,case_id,body,name,mode)).model_dump(mode='json')
    except ValueError as exc:
        raise HTTPException(409,str(exc)) from exc


class SupplementSpec(BaseModel):
    version: int = Field(ge=1)
    actor: str = Field(min_length=1,max_length=100)
    reason: str = Field(min_length=2,max_length=2000)
    issue_ids: list[str] = Field(min_length=1,max_length=100)
    area_map: dict[str,str|None] = Field(default_factory=dict)
    remove_ids: list[str] = Field(default_factory=list,max_length=100)
    materials: list[dict] = Field(min_length=1,max_length=40)


class ResumeSpec(BaseModel):
    version: int = Field(ge=1)
    actor: str = Field(min_length=1,max_length=100)
    reason: str = Field(min_length=2,max_length=2000)
    issue_ids: list[str] = Field(min_length=1,max_length=100)


def run_supplement_job(job_id, provider, ocr):
    store = Store()
    try:
        with store.db() as con:
            row = con.execute('SELECT * FROM verification_jobs WHERE id=?',(job_id,)).fetchone()
            payload = json.loads(row['payload'])
            con.execute("UPDATE verification_jobs SET status='running',error=NULL WHERE id=?",(job_id,))
        record = store.repo.get_case(row['case_id'])
        case = UnderwritingCase.model_validate(record['case'])
        recovering = payload.get('kind')=='resume'
        spec = (ResumeSpec if recovering else SupplementSpec).model_validate(payload['spec'])
        all_sources = store.sources(case.case_id)
        new_ids = set(payload.get('new_ids',[]))
        additions = [m for m in all_sources if m.material.material_id in new_ids]
        old = [m for m in all_sources if m.material.material_id in {x.material_id for x in case.materials}]
        checkpoint = store.load_checkpoint('supplement:'+job_id)
        def save(c):
            cp = c.model_copy(deep=True)
            cp.case_id = 'supplement:'+job_id
            store.checkpoint(cp)
        if checkpoint:
            checkpoint.case_id = case.case_id
            case = checkpoint
        from dataclasses import replace
        metadata={m.material_id:m for m in case.materials}
        old=[replace(m,material=metadata[m.material.material_id]) for m in all_sources if m.material.material_id in metadata]
        if case.verification.get('completed_job') != job_id:
            if recovering:
                case=resume(case,old,spec.issue_ids,provider,ocr,save)
            else:
                case = supplement(case,old,additions,spec.issue_ids,spec.area_map,spec.remove_ids,provider,ocr,save)
            case.verification['completed_job']=job_id
            save(case)
        with store.db() as con:
            con.execute('BEGIN IMMEDIATE')
            store.current(con,case.case_id,spec.version,allow_busy=True)
            store.write(con,case,'resume' if recovering else 'supplement',payload['actor'],payload)
            con.execute("UPDATE verification_jobs SET status='completed' WHERE id=?",(job_id,))
    except Exception as exc:
        with store.db() as con:
            con.execute("UPDATE verification_jobs SET status='failed',error=? WHERE id=?",(str(exc) if isinstance(exc,ValueError) else '执行中断，原件及检查点已保存，请恢复任务。',job_id))


@router.post('/{case_id}/verification/supplements',status_code=202)
async def submit_supplement(case_id: str, request: Request, background: BackgroundTasks,
    manifest: Annotated[str,Form()], files: Annotated[list[UploadFile],File()],
    provider: Annotated[VisionProvider,Depends(get_vision_provider)],
    ocr: Annotated[OcrProvider,Depends(get_ocr_provider)]):
    try:
        spec = SupplementSpec.model_validate_json(manifest)
        name, mode = actor(request,spec.actor)
        store = Store()
        record = store.repo.get_case(case_id)
        if not record:
            raise Conflict('案件不存在')
        from .underwriting_routes import _prepare_uploaded_case
        upload = json.dumps({'case_id':case_id,'project':record['case']['project'],'materials':spec.materials},ensure_ascii=False)
        _, additions = await _prepare_uploaded_case(manifest=upload,files=files)
        job_id = uuid4().hex
        with store.db() as con:
            con.execute('BEGIN IMMEDIATE')
            case = store.current(con,case_id,spec.version)
            if {m.material.material_id for m in additions} & {m.material_id for m in case.materials}:
                raise Conflict('新材料编号与原材料重复')
            if not set(spec.issue_ids) <= {i['id'] for i in case.verification['issues']}:
                raise Conflict('存疑点编号不存在')
            if not set(spec.remove_ids) <= {m.material_id for m in case.materials}:
                raise Conflict('移除材料不存在')
            # Source bytes and job are committed together, so a job cannot lose its files.
            store.archive_sources(case_id,additions,connection=con)
            payload = dict(spec=spec.model_dump(mode='json'),actor=name,identity_mode=mode,new_ids=[m.material.material_id for m in additions])
            con.execute('INSERT INTO verification_jobs VALUES (?,?,?,?,?,?)',(job_id,case_id,'queued',json.dumps(payload,ensure_ascii=False),None,now()))
        background.add_task(run_supplement_job,job_id,provider,ocr)
        return {'job_id':job_id,'case_id':case_id,'status':'queued'}
    except ValueError as exc:
        raise HTTPException(409,str(exc)) from exc


@router.post('/{case_id}/verification/resume',status_code=202)
async def submit_resume(case_id: str,body: ResumeSpec,request: Request,background: BackgroundTasks,
    provider: Annotated[VisionProvider,Depends(get_vision_provider)],ocr: Annotated[OcrProvider,Depends(get_ocr_provider)]):
    try:
        name,mode=actor(request,body.actor)
        store=Store(); job_id=uuid4().hex
        with store.db() as con:
            con.execute('BEGIN IMMEDIATE')
            store.current(con,case_id,body.version)
            payload=dict(kind='resume',spec=body.model_dump(mode='json'),actor=name,identity_mode=mode)
            con.execute('INSERT INTO verification_jobs VALUES (?,?,?,?,?,?)',(job_id,case_id,'queued',json.dumps(payload,ensure_ascii=False),None,now()))
        background.add_task(run_supplement_job,job_id,provider,ocr)
        return dict(job_id=job_id,status='queued')
    except ValueError as exc:
        raise HTTPException(409,str(exc)) from exc


@router.get('/{case_id}/verification/jobs/{job_id}')
async def job_status(case_id: str,job_id: str):
    job = await run_in_threadpool(Store().job,job_id)
    if not job or job['case_id'] != case_id:
        raise HTTPException(404,'任务不存在')
    return job


@router.post('/{case_id}/verification/jobs/{job_id}/retry',status_code=202)
async def retry(case_id: str,job_id: str,background: BackgroundTasks,
    provider: Annotated[VisionProvider,Depends(get_vision_provider)],ocr: Annotated[OcrProvider,Depends(get_ocr_provider)]):
    store=Store()
    with store.db() as con:
        con.execute('BEGIN IMMEDIATE')
        row=con.execute('SELECT * FROM verification_jobs WHERE id=? AND case_id=?',(job_id,case_id)).fetchone()
        if not row:
            raise HTTPException(404,'任务不存在')
        if row['status'] != 'failed':
            return {'job_id':job_id,'status':row['status']}
        payload=json.loads(row['payload'])
        try:
            store.current(con,case_id,payload['spec']['version'])
        except ValueError as exc:
            raise HTTPException(409,str(exc)) from exc
        attempts=payload.get('retries',0)
        if attempts>=3:
            raise HTTPException(409,'恢复次数已达上限，请人工处理')
        payload['retries']=attempts+1
        con.execute("UPDATE verification_jobs SET status='queued',payload=? WHERE id=?",(json.dumps(payload,ensure_ascii=False),job_id))
    background.add_task(run_supplement_job,job_id,provider,ocr)
    return {'job_id':job_id,'status':'queued'}
