"""Unified model -> city pressure -> front load -> grading -> case report."""
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from app.api.component_dependencies import get_component_provider
from app.catastrophe.front_load_screening.city_lookup import DATA, lookup
from app.catastrophe.front_load_screening.unified import (
    VERSION,
    assess_unified,
    capacities,
    model_key,
    report,
)
from app.contracts import ProcessingStatus, ProcessingStep, ProcessingTrace, UnderwritingCase
from app.decision.engine import DecisionEngine
from app.providers.common import ProviderError
from app.reports import generate_markdown_report
from app.storage import CaseRepository

router=APIRouter(prefix='/api/v1/risk/unified',tags=['unified-wind-snow'])
def profile(model):
    try: return get_component_provider().lookup(model)
    except ProviderError as exc: raise HTTPException(502,'组件查询未完成，请稍后重试') from exc

class AssessmentInput(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    project_id:str=''
    model:str=Field(min_length=1,max_length=150)
    address:str=Field(min_length=2,max_length=500)
    screening_input:dict=Field(default_factory=dict)

@router.get('/component')
async def get_component(model:str=Query(min_length=1,max_length=150)):
    c=await run_in_threadpool(profile,model)
    if c is None: raise HTTPException(404,'完整型号未确认，不能借用相近型号参数')
    return {'component':c.model_dump(mode='json'),'capacities':capacities(c)}

@router.get('/catalog')
def catalog():
    return {'version':VERSION,'return_period_years':10,'records':[{'record_id':r['id'],'province':r['province'],'city_source':r['city_source']} for r in DATA['records']]}

@router.post('/location')
def location(site:dict): return lookup(site)

@router.post('/assess')
async def assess(body:AssessmentInput):
    c=await run_in_threadpool(profile,body.model)
    r=assess_unified(case_id=body.project_id,model=body.model,address=body.address,component=c,screening_input=body.screening_input)
    return {'assessment':r,'report_markdown':report(r)}

class MergeInput(AssessmentInput):
    case_id:str=Field(min_length=1,max_length=200)

@router.post('/merge-report')
async def merge_report(body:MergeInput):
    repo=CaseRepository(); stored=await run_in_threadpool(repo.get_case,body.case_id)
    if stored is None: raise HTTPException(404,'原案件不存在，请先运行整单核保')
    case=UnderwritingCase.model_validate(stored['case'])
    address=case.project.site_address or case.project.insured_address or ''
    if model_key(body.model)!=model_key(case.project.component_model) or ''.join(body.address.split())!=''.join(address.split()):
        raise HTTPException(409,'当前型号或地址与原案件不同，请返回原案件更新后重新运行整单')
    c=await run_in_threadpool(profile,body.model)
    new_id=case.case_id+'-FS-'+uuid4().hex[:8]
    r=assess_unified(case_id=new_id,model=body.model,address=address,component=c,screening_input=body.screening_input,
                     province=case.project.province,city=case.project.city,district=case.project.district,
                     location_conflict=bool(case.location_assessment and case.location_assessment.status=='conflict'))
    now=datetime.now(UTC)
    trace=ProcessingTrace(step=ProcessingStep.FRONT_LOAD_SCREENING,provider='unified-city-wind-snow',model=VERSION,
                          started_at=now,finished_at=now,latency_ms=0,status=ProcessingStatus.SUCCESS,
                          output_status='available' if r['data_complete'] else 'partial')
    # Save as a new case version, preserving the original and its human review.
    payload=case.model_dump(mode='python');payload.update(case_id=new_id,component_profile=c,component_module_result=c.workbench_result if c else {},pv_load_screening_assessment=r,catastrophe_assessment=None,
        decision=None,processing_trace=[t for t in case.processing_trace if t.step not in [ProcessingStep.FRONT_LOAD_SCREENING,ProcessingStep.CATASTROPHE_ASSESSMENT,ProcessingStep.DECISION]]+[trace])
    merged=UnderwritingCase.model_validate(payload)
    merged=merged.model_copy(update={'decision':DecisionEngine().decide(merged)})
    if merged.verification:
        from app.verification.store import Store
        from app.verification.engine import apply_pending,report_items
        merged.verification.pop('final',None)
        merged.verification['version']+=1
        merged.verification['parent_case_id']=case.case_id
        merged.verification['rule_missing_requirements']=list(merged.decision.missing_requirements)
        report_items(merged)
        apply_pending(merged)
        Store().archive_sources(new_id,Store().sources(case.case_id))
    await run_in_threadpool(repo.save_case,merged)
    return {'case':merged.model_dump(mode='json'),'parent_case_id':case.case_id,'report_markdown':generate_markdown_report(merged)}
