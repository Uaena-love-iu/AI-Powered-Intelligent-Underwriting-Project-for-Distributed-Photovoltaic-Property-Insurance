"""Legacy risk entry now opens the delivered component module."""
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from app.api.component_dependencies import get_underwriting_component_provider

router = APIRouter()

@router.get("/risk-lab", include_in_schema=False)
def page():
    from pathlib import Path
    return FileResponse(Path(__file__).resolve().parents[3] / "frontend/risk-lab.html")

@router.get("/risk-lab.js", include_in_schema=False)
def script():
    from pathlib import Path
    return FileResponse(Path(__file__).resolve().parents[3] / "frontend/risk-lab.js", media_type="application/javascript")

@router.get("/pv-load-screening", include_in_schema=False)
def old_page():
    return RedirectResponse("/risk-lab", status_code=307)

@router.get("/api/v1/risk/lab/config")
def config():
    return {"module": "component-workbench-v1", "entry": "/component-model-workbench/",
            "legacy_assessment_retired": True}

@router.get("/api/v1/risk/lab/component")
async def component(model: str):
    profile = await run_in_threadpool(get_underwriting_component_provider().lookup, model)
    if profile is None:
        raise HTTPException(404, "未找到该完整型号的可用参数")
    return profile

@router.post("/api/v1/risk/lab/assess")
def retired_assessment():
    raise HTTPException(410, "旧抗灾计算已停用，请使用组件抗灾识别模块")
