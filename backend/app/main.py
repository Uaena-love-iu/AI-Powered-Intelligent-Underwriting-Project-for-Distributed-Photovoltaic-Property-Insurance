import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from app.api import (
    auth_router,
    case_router,
    component_router,
    geocoding_router,
    map_review_router,
    ocr_router,
    report_router,
    router,
    underwriting_router,
)
from app.api.component_agent_routes import router as component_agent_router
from app.api.llm_config_routes import router as llm_config_router
from app.api.risk_lab import router as risk_lab_router
from app.api.vision_lab import router as vision_lab_router
from app.api.weather_history_routes import router as weather_history_router
from app.security import AuthRepository, auth_enabled
from app.security.middleware import RoleAccessMiddleware
from app.storage import AnalysisJobRepository, CaseRepository


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    from app.verification.store import Store
    await run_in_threadpool(Store().recover_interrupted)
    await run_in_threadpool(AnalysisJobRepository().fail_incomplete_jobs)
    from app.storage.revisions import RevisionRepository
    await run_in_threadpool(RevisionRepository().reconcile_orphans)
    from app.pipeline.recovery import RecoveryStore
    await run_in_threadpool(RecoveryStore().purge_expired)
    retention_days = int(os.getenv("PV_CASE_RETENTION_DAYS", "0"))
    if retention_days < 0:
        raise ValueError("PV_CASE_RETENTION_DAYS must be zero or greater")
    if retention_days:
        await run_in_threadpool(
            CaseRepository().purge_expired_cases,
            retention_days=retention_days,
        )
    if auth_enabled():
        await run_in_threadpool(AuthRepository().ensure_initial_admin)
    yield

app = FastAPI(
    title="分布式光伏财产险 AI 智能核保",
    description="分布式光伏投保材料解析、风险识别和核保决策接口。",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(RoleAccessMiddleware)

from app.api.verification_routes import router as verification_router
app.include_router(verification_router)

@app.get('/verification', include_in_schema=False)
def verification_page():
    return FileResponse(Path(__file__).resolve().parents[2] / 'frontend' / 'verification.html')

@app.get('/verification.js', include_in_schema=False)
def verification_script():
    return FileResponse(Path(__file__).resolve().parents[2] / 'frontend' / 'verification.js', media_type='application/javascript')

from app.api.rule_selection_routes import router as rule_selection_router
app.include_router(rule_selection_router)

@app.get("/rule-selection.js", include_in_schema=False)
def rule_selection_js():
    return FileResponse(Path(__file__).resolve().parents[2] / "frontend" / "rule-selection.js", media_type="application/javascript")


@app.get("/task-recovery.js", include_in_schema=False)
def task_recovery_js():
    return FileResponse(Path(__file__).resolve().parents[2] / "frontend" / "task-recovery.js", media_type="application/javascript")


@app.get('/case-review.js', include_in_schema=False)
def case_review_script():
    return FileResponse(Path(__file__).resolve().parents[2] / 'frontend' / 'case-review.js', media_type='application/javascript')


@app.get('/component-review.js', include_in_schema=False)
def component_review_script():
    return FileResponse(Path(__file__).resolve().parents[2] / 'frontend' / 'component-review.js', media_type='application/javascript')

app.include_router(router)
app.include_router(vision_lab_router)
app.include_router(risk_lab_router)
app.include_router(weather_history_router)
app.include_router(auth_router)
app.include_router(ocr_router)
app.include_router(case_router)
app.include_router(component_router)
from app.api.verified_component_routes import router as verified_component_router
app.include_router(verified_component_router)
app.include_router(component_agent_router)
app.include_router(llm_config_router)
app.include_router(geocoding_router)
app.include_router(map_review_router)
app.include_router(underwriting_router)
app.include_router(report_router)

from app.api.component_workbench_routes import install as install_component_workbench

install_component_workbench(app)
from app.api.unified_risk import router as unified_risk_router

app.include_router(unified_risk_router)


@app.get("/", include_in_schema=False)
def evaluation_home() -> FileResponse:
    """在首页提供当前材料评测台。"""
    page = Path(__file__).resolve().parents[2] / "frontend" / "evaluation.html"
    return FileResponse(page, media_type="text/html; charset=utf-8")


@app.get("/evaluation", include_in_schema=False)
def evaluation_workspace() -> FileResponse:
    """提供不依赖投保字段的材料评测工作台。"""
    page = Path(__file__).resolve().parents[2] / "frontend" / "evaluation.html"
    return FileResponse(page, media_type="text/html; charset=utf-8")


@app.get("/llm-config", include_in_schema=False)
def llm_config_page() -> FileResponse:
    return FileResponse(
        Path(__file__).resolve().parents[2] / "frontend" / "llm-config.html",
        media_type="text/html; charset=utf-8",
    )


@app.get("/verified-components", include_in_schema=False)
def verified_components_page():
    return FileResponse(Path(__file__).resolve().parents[2] / "frontend" / "verified-components.html")


@app.get("/verified-components.js", include_in_schema=False)
def verified_components_script():
    return FileResponse(Path(__file__).resolve().parents[2] / "frontend" / "verified-components.js", media_type="application/javascript")


@app.get("/llm-config.js", include_in_schema=False)
def llm_config_script() -> FileResponse:
    return FileResponse(
        Path(__file__).resolve().parents[2] / "frontend" / "llm-config.js",
        media_type="application/javascript",
    )


@app.get("/material-files.js", include_in_schema=False)
def material_files_script() -> FileResponse:
    return FileResponse(Path(__file__).resolve().parents[2] / "frontend" / "material-files.js", media_type="application/javascript")


@app.get("/workbench.js", include_in_schema=False)
def workbench_script() -> FileResponse:
    return FileResponse(Path(__file__).resolve().parents[2] / "frontend" / "workbench.js", media_type="application/javascript")


@app.get("/workbench.css", include_in_schema=False)
def workbench_style() -> FileResponse:
    return FileResponse(Path(__file__).resolve().parents[2] / "frontend" / "workbench.css", media_type="text/css")


@app.get("/evaluation/report.js", include_in_schema=False)
def evaluation_report_script() -> FileResponse:
    return FileResponse(Path(__file__).resolve().parents[2] / "frontend" / "underwriting-report.js", media_type="application/javascript")


@app.get("/evaluation/report.css", include_in_schema=False)
def evaluation_report_style() -> FileResponse:
    return FileResponse(Path(__file__).resolve().parents[2] / "frontend" / "underwriting-report.css", media_type="text/css")


@app.get("/evaluation/progress.js", include_in_schema=False)
def evaluation_progress_script() -> FileResponse:
    return FileResponse(Path(__file__).resolve().parents[2] / "frontend" / "analysis-progress.js", media_type="application/javascript")


@app.get("/evaluation/progress.css", include_in_schema=False)
def evaluation_progress_style() -> FileResponse:
    return FileResponse(Path(__file__).resolve().parents[2] / "frontend" / "analysis-progress.css", media_type="text/css")


@app.get("/evaluation/report-preview", include_in_schema=False)
def evaluation_report_preview() -> FileResponse:
    return FileResponse(Path(__file__).resolve().parents[2] / "frontend" / "report-preview.html", media_type="text/html; charset=utf-8")


@app.get("/evaluation/replay", include_in_schema=False)
def evaluation_replay() -> RedirectResponse:
    """兼容先前的案例复盘链接。"""
    return RedirectResponse("/evaluation?view=replay", status_code=302)


def _set_file_array_binary(
    openapi_schema: dict[str, Any],
    path: str,
) -> None:
    request_schema = openapi_schema[
        "paths"
    ][
        path
    ][
        "post"
    ][
        "requestBody"
    ][
        "content"
    ][
        "multipart/form-data"
    ][
        "schema"
    ]

    if "$ref" in request_schema:
        component_name = request_schema[
            "$ref"
        ].rsplit(
            "/",
            maxsplit=1,
        )[-1]

        properties = openapi_schema[
            "components"
        ][
            "schemas"
        ][
            component_name
        ][
            "properties"
        ]
    else:
        properties = request_schema[
            "properties"
        ]

    properties[
        "files"
    ][
        "items"
    ][
        "format"
    ] = "binary"


def custom_openapi() -> dict[str, Any]:
    """Add binary hints required by Swagger UI for file arrays."""

    if app.openapi_schema is not None:
        return app.openapi_schema

    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        openapi_version=app.openapi_version,
        summary=app.summary,
        description=app.description,
        routes=app.routes,
    )

    _set_file_array_binary(
        openapi_schema,
        "/api/v1/files/inspect",
    )
    _set_file_array_binary(
        openapi_schema,
        "/api/v1/underwriting/analyze",
    )

    app.openapi_schema = openapi_schema
    return openapi_schema


app.openapi = custom_openapi


from app.api.strategy_routes import router as strategy_router
app.include_router(strategy_router)
@app.get('/strategies',include_in_schema=False)
def strategy_page():
    return FileResponse(Path(__file__).resolve().parents[2]/'frontend'/'strategies.html',media_type='text/html; charset=utf-8')
@app.get('/strategies.js',include_in_schema=False)
def strategy_script():
    return FileResponse(Path(__file__).resolve().parents[2]/'frontend'/'strategies.js',media_type='application/javascript')
@app.get('/strategy-selection.js',include_in_schema=False)
def strategy_selection_script():
    return FileResponse(Path(__file__).resolve().parents[2]/'frontend'/'strategy-selection.js',media_type='application/javascript')
