from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.responses import StreamingResponse

from app.contracts import UnderwritingCase
from app.reports import generate_markdown_report
from app.reports.interpretation import (
    InterpretationUnavailable,
    generate_interpretation,
    stream_interpretation,
)
from app.reports.view import build_report_view

router = APIRouter()


class InterpretationRequest(BaseModel):
    case: UnderwritingCase
    review_revision: str = Field(default="", max_length=128)
    force_refresh: bool = False


class MarkdownResponse(Response):
    media_type = "text/markdown"


@router.post(
    "/api/v1/reports/render",
    response_class=MarkdownResponse,
    tags=["reports"],
)
def render_underwriting_report(
    case: UnderwritingCase,
) -> MarkdownResponse:
    """把完整核保结果转换为可下载的 Markdown 报告。"""

    report = generate_markdown_report(
        case
    )

    return MarkdownResponse(
        content=report,
        headers={
            "Content-Disposition": (
                "attachment; "
                'filename="underwriting-report.md"'
            )
        },
    )


@router.post("/api/v1/reports/view", tags=["reports"])
def render_report_view(case: UnderwritingCase) -> dict:
    """Return the same decision-first fields used by report export and AI."""

    return build_report_view(case)


@router.post("/api/v1/reports/interpret", tags=["reports"])
async def interpret_underwriting_report(request: InterpretationRequest) -> dict:
    """Optional model explanation; the primary report never waits for this endpoint."""

    try:
        return await run_in_threadpool(
            generate_interpretation,
            request.case,
            review_revision=request.review_revision,
        )
    except InterpretationUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/api/v1/reports/interpret/stream", tags=["reports"])
def stream_underwriting_summary(request: InterpretationRequest) -> StreamingResponse:
    """Generate a report summary only after the underwriter clicks the AI button."""

    try:
        chunks = stream_interpretation(
            request.case, review_revision=request.review_revision,
            force_refresh=request.force_refresh,
        )
    except InterpretationUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return StreamingResponse(
        chunks, media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
