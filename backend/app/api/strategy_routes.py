from fastapi import APIRouter, HTTPException, Request
from pydantic import Field
from app.contracts.common import ContractModel
from app.strategies.engine import Repository, Scheme, ACTIONS, SOURCES, VALUES, evaluate
from app.api.verified_component_routes import actor
from app.storage.cases import CaseRepository
router=APIRouter(prefix='/api/v1/strategies',tags=['underwriting-strategies'])
class Revoke(ContractModel):
    reviewer_name:str=Field(min_length=1,max_length=100)
    reason:str=Field(min_length=2,max_length=1000)
class Preview(ContractModel):
    case_id:str
    record_id:str
@router.get('')
def records():return dict(records=Repository().list(),actions=ACTIONS,sources=SOURCES,values=VALUES)
@router.post('',status_code=201)
def create(body:Scheme,request:Request):
    try:return Repository().create(body,actor(request,body.reviewer_name))
    except ValueError as e:raise HTTPException(409,str(e)) from e
@router.post('/{id}/revoke')
def revoke(id:str,body:Revoke,request:Request):
    try:return Repository().revoke(id,actor(request,body.reviewer_name),body.reason)
    except ValueError as e:raise HTTPException(409,str(e)) from e
@router.post('/preview')
def preview(body:Preview):
    case=CaseRepository().get_case(body.case_id)
    if not case:raise HTTPException(404,'案件不存在')
    try:scheme=Repository().get(body.record_id)
    except ValueError as e:raise HTTPException(404,str(e)) from e
    return dict(preview_only=True,notice='试算不会修改原报告或人工审核结果；新方案实际采用请提交新轮核保。',result=evaluate(case['case'],scheme))
