"""Compatibility entry points for the replacement workbench."""
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse
from starlette.concurrency import run_in_threadpool

from app.component_workbench_runtime import runtime

router = APIRouter(prefix="/api/v1/components", tags=["components"])

@router.get("/test", include_in_schema=False)
def component_test_page():
    return RedirectResponse("/component-model-workbench/", status_code=307)

@router.get("/lookup")
async def lookup_component_parameters(model: str = Query(min_length=3, max_length=100)):
    try:
        return await run_in_threadpool(runtime().bench.run, model)
    except (ValueError, RuntimeError, OSError) as exc:
        raise HTTPException(502, "组件工作台查询失败，请检查配置或稍后重试") from exc

@router.get("/sources")
@router.post("/corrections")
def retired():
    raise HTTPException(410, "旧组件接口已替换，请在组件工作台查询来源并记录核验反馈")
