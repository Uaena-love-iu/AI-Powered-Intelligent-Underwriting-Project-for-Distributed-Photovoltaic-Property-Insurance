"""Old agent entry redirects to the workbench's integrated search agent."""
from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

router = APIRouter()

@router.get("/component-agent", include_in_schema=False)
def agent_page():
    return RedirectResponse("/component-model-workbench/", status_code=307)

@router.api_route("/api/v1/components/agent/{path:path}", methods=["GET", "POST"], include_in_schema=False)
def retired(path: str):
    raise HTTPException(410, "原组件 Agent 已替换，请使用组件工作台的查询、任务与反馈接口")
