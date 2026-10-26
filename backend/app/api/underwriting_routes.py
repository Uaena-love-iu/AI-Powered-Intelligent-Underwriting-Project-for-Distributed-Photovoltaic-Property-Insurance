import logging
from threading import BoundedSemaphore
from typing import Annotated
from uuid import uuid4

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from app.catastrophe import CatastropheAssessmentEngine
from app.contracts import (
    Material,
    MaterialParseStatus,
    MaterialQualityStatus,
    ProjectInfo,
    UnderwritingCase,
)
from app.intake import (
    MAX_FILE_SIZE_BYTES,
    MAX_FILES_PER_REQUEST,
    MAX_TOTAL_UPLOAD_SIZE_BYTES,
    inspect_file,
    read_upload_limited,
)
from app.location import AmapGeocoder
from app.pipeline import UnderwritingPipeline
from app.pipeline.recovery import (
    RecoveryStore, JobExecution, RecoverableProvider, JobStopped, JobDeadline,
    classify_error, unpack,
)
import json
from app.providers import (
    MaterialInput,
    OcrProvider,
    VisionProvider,
)
from app.providers.component import (
    ComponentProvider,
)
from app.providers.routing import VISION_MATERIAL_CATEGORIES
from app.storage import (
    AnalysisJobRepository,
    CaseRepository,
    CaseRepositoryConflict,
)

from .component_dependencies import (
    get_underwriting_component_provider,
)
from .ocr_routes import get_ocr_provider
from .routes import get_vision_provider
from .schemas import RealAnalyzeManifest
from app.storage.revisions import RevisionRepository, RevisionConflict, CorrectedComponentProvider, CorrectedOcrProvider
from app.providers.case_component import CaseComponentProvider, CaseReviewedOcrProvider, prepare_review

router = APIRouter()
_JOB_SLOTS = BoundedSemaphore(value=2)
logger = logging.getLogger(__name__)

REAL_PIPELINE_MEDIA_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def _validation_error_summary(
    error: ValidationError,
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []

    for item in error.errors(
        include_url=False,
        include_context=False,
        include_input=False,
    )[:10]:
        location = ".".join(
            str(part)
            for part in item["loc"]
        )

        issues.append(
            {
                "location": (
                    location or "manifest"
                ),
                "type": item["type"],
            }
        )

    return issues


def _parse_manifest(
    manifest: str,
) -> RealAnalyzeManifest:
    try:
        return (
            RealAnalyzeManifest
            .model_validate_json(manifest)
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=(
                status
                .HTTP_422_UNPROCESSABLE_CONTENT
            ),
            detail={
                "message": (
                    "Invalid underwriting manifest"
                ),
                "issues": (
                    _validation_error_summary(exc)
                ),
            },
        ) from exc


async def _read_uploaded_files(
    files: list[UploadFile],
) -> list[
    tuple[str | None, str | None, bytes]
]:
    payloads: list[
        tuple[str | None, str | None, bytes]
    ] = []
    total_bytes = 0

    for uploaded_file in files:
        try:
            content = await read_upload_limited(uploaded_file)
        finally:
            await uploaded_file.close()

        if len(content) > MAX_FILE_SIZE_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail={
                    "message": "Uploaded file exceeds the per-file size limit",
                    "file_name": uploaded_file.filename,
                    "maximum_bytes": MAX_FILE_SIZE_BYTES,
                },
            )

        total_bytes += len(content)
        if total_bytes > MAX_TOTAL_UPLOAD_SIZE_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail={
                    "message": "Total uploaded files exceed the request size limit",
                    "maximum_bytes": MAX_TOTAL_UPLOAD_SIZE_BYTES,
                },
            )

        payloads.append(
            (
                uploaded_file.filename,
                uploaded_file.content_type,
                content,
            )
        )

    return payloads


def _build_material_inputs(
    *,
    manifest: RealAnalyzeManifest,
    uploaded_files: list[
        tuple[str | None, str | None, bytes]
    ],
) -> list[MaterialInput]:
    seen_sha256: set[str] = set()
    original_by_sha256: dict[str, str] = {}
    material_inputs: list[
        MaterialInput
    ] = []

    for index, (
        material_manifest,
        uploaded_file,
    ) in enumerate(
        zip(
            manifest.materials,
            uploaded_files,
            strict=True,
        ),
        start=1,
    ):
        (
            file_name,
            declared_media_type,
            content,
        ) = uploaded_file

        inspection = inspect_file(
            file_name=file_name,
            declared_media_type=(
                declared_media_type
            ),
            content=content,
            seen_sha256=seen_sha256,
        )

        if inspection.status not in {"accepted", "duplicate"}:
            raise HTTPException(
                status_code=(
                    status
                    .HTTP_422_UNPROCESSABLE_CONTENT
                ),
                detail={
                    "message": (
                        "Uploaded file was rejected"
                    ),
                    "file_index": index,
                    "file_name": (
                        inspection.file_name
                    ),
                    "issues": inspection.issues,
                },
            )

        if (
            inspection.media_type
            not in REAL_PIPELINE_MEDIA_TYPES
        ):
            raise HTTPException(
                status_code=(
                    status
                    .HTTP_415_UNSUPPORTED_MEDIA_TYPE
                ),
                detail={
                    "message": (
                        "Real underwriting currently "
                        "supports only JPEG, PNG and "
                        "PDF files"
                    ),
                    "file_index": index,
                    "file_name": (
                        inspection.file_name
                    ),
                },
            )

        if (
            inspection.media_type is None
            or inspection.sha256 is None
        ):
            raise HTTPException(
                status_code=(
                    status
                    .HTTP_422_UNPROCESSABLE_CONTENT
                ),
                detail={
                    "message": (
                        "Uploaded material could not "
                        "be inspected"
                    ),
                    "file_index": index,
                    "file_name": (
                        inspection.file_name
                    ),
                },
            )

        if material_manifest.additional_categories and (
            inspection.media_type not in {"image/jpeg", "image/png"}
            or any(category not in VISION_MATERIAL_CATEGORIES
                   for category in material_manifest.additional_categories)
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"message": "Additional uses require an image and a photo category",
                        "file_index": index},
            )

        material = Material(
            material_id=(
                material_manifest.material_id
            ),
            category=(
                material_manifest.category
            ),
            additional_categories=[category for category in
                                   material_manifest.additional_categories
                                   if category != material_manifest.category],
            category_source=material_manifest.category_source,
            category_note=material_manifest.category_note,
            capture_view=material_manifest.capture_view,
            file_name=inspection.file_name,
            media_type=inspection.media_type,
            sha256=inspection.sha256,
            duplicate_of_material_id=(
                original_by_sha256.get(inspection.sha256)
                if inspection.status == "duplicate" else None
            ),
            quality_status=(
                inspection.quality_status
                or MaterialQualityStatus.USABLE
            ),
            quality_confidence=None,
            quality_issues=inspection.quality_issues,
            parse_status=(
                MaterialParseStatus.SUCCESS
            ),
        )

        material_inputs.append(
            MaterialInput(
                material=material,
                content=content,
            )
        )
        if inspection.status == "accepted":
            original_by_sha256[inspection.sha256] = material.material_id

    return material_inputs


async def _prepare_uploaded_case(
    *,
    manifest: str,
    files: list[UploadFile],
) -> tuple[RealAnalyzeManifest, list[MaterialInput]]:
    parsed_manifest = _parse_manifest(manifest)
    if len(files) > MAX_FILES_PER_REQUEST:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"A maximum of {MAX_FILES_PER_REQUEST} files is allowed",
        )
    if len(files) != len(parsed_manifest.materials):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "message": "Uploaded file count does not match manifest materials",
                "expected": len(parsed_manifest.materials),
                "received": len(files),
            },
        )
    uploaded_files = await _read_uploaded_files(files)
    material_inputs = _build_material_inputs(
        manifest=parsed_manifest,
        uploaded_files=uploaded_files,
    )
    return parsed_manifest, material_inputs


def _resolve_selection(manifest):
    from app.rules.selection import resolve
    try:
        return resolve(manifest.rule_selection, manifest.project)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _run_analysis_job(
    *,
    job_id: str,
    case_id: str,
    project: ProjectInfo,
    materials: list[MaterialInput],
    ocr_provider: OcrProvider,
    vision_provider: VisionProvider,
    component_provider: ComponentProvider,
    pv_load_screening_input: dict | None = None,
    rule_snapshot: dict | None = None,
    revision_context: dict | None = None,
    component_review: dict | None = None,
    component_source_ids: list[str] | None = None,
    strategy_snapshot: dict | None = None,
) -> None:
    jobs = AnalysisJobRepository()
    recovery = RecoveryStore()
    execution = None
    def report_progress(step: str, progress_percent: int) -> None:
        if execution:
            execution.check()
        jobs.update_job(
            job_id,
            status="running",
            progress_percent=progress_percent,
            current_step=step,
        )

    try:
        execution = JobExecution(job_id, recovery) if recovery.get(job_id) else None
        jobs.update_job(
            job_id,
            status="running",
            progress_percent=1,
            current_step="开始核保分析",
        )
        geocoder = AmapGeocoder.from_environment()
        if execution and geocoder:
            geocoder._provider = RecoverableProvider(geocoder._provider, execution)
        correction = next((c for c in (revision_context or {}).get('corrections', []) if c['field'] == 'component_model'), None)
        corrections = (revision_context or {}).get('corrections', [])
        if component_review:
            correction = dict(field='component_model', material_id=component_review['identity_material_id'], evidence=component_review['model_reason'])
            corrections = [correction]
        if correction and component_provider and not component_review:
            component_provider = CorrectedComponentProvider(component_provider, correction)
        effective_ocr = RecoverableProvider(ocr_provider, execution) if execution else ocr_provider
        if component_review:
            effective_ocr = CaseReviewedOcrProvider(effective_ocr, component_review, project)
        elif correction:
            effective_ocr = CorrectedOcrProvider(effective_ocr, corrections, project)
        if component_review:
            component_provider = CaseComponentProvider(component_provider, component_review, case_id)
        pipeline = UnderwritingPipeline(
            ocr_provider=effective_ocr,
            vision_provider=RecoverableProvider(vision_provider, execution) if execution else vision_provider,
            component_provider=RecoverableProvider(component_provider, execution) if execution and component_provider else component_provider,
            catastrophe_engine=CatastropheAssessmentEngine(),
            location_geocoder=geocoder,
            progress_callback=report_progress,
        )
        case = pipeline.run(
            case_id=case_id,
            project=project,
            materials=[m for m in materials if m.material.material_id not in (component_source_ids or [])],
            pv_load_screening_input=pv_load_screening_input,
            rule_snapshot=rule_snapshot,
        )
        case.revision = RevisionRepository().get(case_id) or {}
        case.component_source_material_ids = component_source_ids or []
        case.materials.extend(m.material for m in materials if m.material.material_id in case.component_source_material_ids)
        case.component_review = case.component_module_result.get('case_parameter_review') or component_review or {}
        if case.component_review:
            case.component_review = {**case.component_review, 'submitted_case_id': case_id}
            case.component_review['ocr_failures'] = getattr(effective_ocr, 'failures', [])
        if isinstance(effective_ocr, CorrectedOcrProvider):
            case.original_ocr_fields = list(effective_ocr.originals.values())
        from app.strategies.engine import evaluate
        case.strategy_assessment = evaluate(case, strategy_snapshot)
        if case.verification:
            from app.verification.store import Store
            Store().archive_sources(case.case_id, materials)
        CaseRepository().save_case(case)
        jobs.update_job(
            job_id,
            status="completed",
            progress_percent=100,
            current_step="核保分析完成",
            result=case,
        )
        details = recovery.describe(job_id)
        if details and details["issues"]:
            recovery.event(job_id, "service_attention", "部分步骤仍有异常，请处理失败步骤；AI 结果仅供复核参考")
        elif case.decision:
            value = case.decision.decision.value
            category = "needs_materials" if value == "request_more" else "needs_review"
            recovery.event(job_id, category, "需要补充材料或信息" if category == "needs_materials" else "AI 分析已完成，等待业务人员核验；尚未作出人工审核决定")
    except (JobStopped, JobDeadline) as exc:
        stopped = isinstance(exc, JobStopped)
        previous = jobs.get_job(job_id) or {}
        jobs.update_job(job_id, status="cancelled" if stopped else "failed",
                        progress_percent=previous.get("progress_percent", 0),
                        current_step="已停止，成功步骤已保留" if stopped else "任务执行超时，成功步骤已保留",
                        error_code="USER_STOPPED" if stopped else "SERVICE_TIMEOUT")
        recovery.event(job_id, "stopped" if stopped else "service_attention",
                       "任务已停止，可恢复未完成步骤" if stopped else "任务超时，可恢复未完成步骤")
    except CaseRepositoryConflict:
        jobs.update_job(
            job_id,
            status="failed",
            progress_percent=100,
            current_step="案件编号已使用",
            error_code="CASE_ID_EXISTS",
        )
    except Exception as exc:  # noqa: BLE001 - keep the background task status terminal
        logger.error(
            "underwriting analysis job failed: job_id=%s error_type=%s",
            job_id,
            type(exc).__name__,
        )
        issue = classify_error(exc)
        previous = jobs.get_job(job_id) or {}
        recovery.step(job_id, "pipeline", dict(key="pipeline", name=previous.get("current_step", "任务执行"),
                      status="failed", material_ids=[], attempts=1, **issue)) if execution else None
        jobs.update_job(
            job_id,
            status="failed",
            progress_percent=previous.get("progress_percent", 0),
            current_step=issue["message"],
            error_code=issue["code"],
        )
        recovery.event(job_id, "service_attention", issue["message"])
    finally:
        _JOB_SLOTS.release()


@router.post(
    "/api/v1/underwriting/analyze",
    response_model=UnderwritingCase,
    tags=["underwriting"],
)
async def analyze_uploaded_case(
    ocr_provider: Annotated[
        OcrProvider,
        Depends(get_ocr_provider),
    ],
    vision_provider: Annotated[
        VisionProvider,
        Depends(get_vision_provider),
    ],
    component_provider: Annotated[
        ComponentProvider,
        Depends(get_underwriting_component_provider),
    ],
    files: Annotated[
        list[UploadFile],
        File(
            description=(
                "投保材料文件，顺序必须与 "
                "manifest.materials 保持一致"
            )
        ),
    ],
    manifest: Annotated[
        str,
        Form(
            description=(
                "案件、项目信息和材料清单组成的 "
                "JSON 字符串"
            )
        ),
    ],
) -> UnderwritingCase:
    """使用真实模型和组件目录执行完整核保流水线。"""

    parsed_manifest, material_inputs = await _prepare_uploaded_case(
        manifest=manifest,
        files=files,
    )
    if parsed_manifest.strategy_selection or parsed_manifest.revision or parsed_manifest.component_review or any(m.source_only for m in parsed_manifest.materials):
        raise HTTPException(422, '补材复核请使用异步核保任务接口。')

    pipeline = UnderwritingPipeline(
        ocr_provider=ocr_provider,
        vision_provider=vision_provider,
        component_provider=(
            component_provider
        ),
        catastrophe_engine=(
            CatastropheAssessmentEngine()
        ),
    )

    case = await run_in_threadpool(
        pipeline.run,
        case_id=parsed_manifest.case_id,
        project=parsed_manifest.project,
        materials=material_inputs,
        pv_load_screening_input=parsed_manifest.pv_load_screening_input,
        rule_snapshot=_resolve_selection(parsed_manifest),
    )
    try:
        await run_in_threadpool(CaseRepository().save_case, case)
    except CaseRepositoryConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This case ID already has a saved analysis. "
                "Use a new case ID to create another run."
            ),
        ) from exc
    return case


@router.post(
    "/api/v1/underwriting/jobs",
    status_code=status.HTTP_202_ACCEPTED,
    tags=["underwriting"],
)
async def submit_underwriting_job(
    http_request: Request,
    background_tasks: BackgroundTasks,
    ocr_provider: Annotated[OcrProvider, Depends(get_ocr_provider)],
    vision_provider: Annotated[VisionProvider, Depends(get_vision_provider)],
    component_provider: Annotated[ComponentProvider, Depends(get_underwriting_component_provider)],
    files: Annotated[list[UploadFile], File()],
    manifest: Annotated[str, Form()],
) -> dict[str, str]:
    """Queue a local analysis and return a job identifier for progress polling."""

    if not _JOB_SLOTS.acquire(blocking=False):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The local analysis queue is at capacity; retry shortly.",
        )

    job_id = uuid4().hex
    reserved_case_id = None
    try:
        parsed_manifest, material_inputs = await _prepare_uploaded_case(
            manifest=manifest,
            files=files,
        )
        rule_snapshot = _resolve_selection(parsed_manifest)
        from app.strategies.engine import snapshot
        try:
            strategy_snapshot = snapshot(parsed_manifest.strategy_selection)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        revision_context = parsed_manifest.revision.model_dump() if parsed_manifest.revision else None
        user = getattr(http_request.state, 'auth_user', None)
        try:
            component_review = prepare_review(parsed_manifest.component_review, parsed_manifest.project, material_inputs,
                parsed_manifest.case_id, (revision_context or {}).get('parent_case_id'), user.username if user else None)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        component_source_ids = [m.material_id for m in parsed_manifest.materials if m.source_only]
        request = dict(project=parsed_manifest.project, materials=material_inputs,
            pv_load_screening_input=parsed_manifest.pv_load_screening_input, rule_snapshot=rule_snapshot,
            revision_context=revision_context, component_review=component_review, component_source_ids=component_source_ids, strategy_snapshot=strategy_snapshot)
        await run_in_threadpool(RevisionRepository().reserve, parsed_manifest.case_id, job_id, request, revision_context)
        reserved_case_id = parsed_manifest.case_id
        RecoveryStore().create(job_id, request)
        await run_in_threadpool(
            AnalysisJobRepository().create_job,
            job_id,
            parsed_manifest.case_id,
        )
        background_tasks.add_task(
            _run_analysis_job,
            job_id=job_id,
            case_id=parsed_manifest.case_id,
            project=parsed_manifest.project,
            materials=material_inputs,
            pv_load_screening_input=parsed_manifest.pv_load_screening_input,
            rule_snapshot=rule_snapshot,
            revision_context=revision_context,
            component_review=component_review,
            component_source_ids=component_source_ids,
            strategy_snapshot=strategy_snapshot,
            ocr_provider=ocr_provider,
            vision_provider=vision_provider,
            component_provider=component_provider,
        )
    except RevisionConflict as exc:
        _JOB_SLOTS.release()
        raise HTTPException(409, str(exc)) from exc
    except Exception:
        if reserved_case_id:
            RevisionRepository().abandon(reserved_case_id)
        _JOB_SLOTS.release()
        raise

    return {"job_id": job_id, "status": "queued"}


@router.get("/api/v1/underwriting/jobs/{job_id}")
async def get_underwriting_job(job_id: str) -> dict[str, object]:
    """Return the current status, progress and completed case result, if ready."""

    job = await run_in_threadpool(AnalysisJobRepository().get_job, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    job["recovery"] = await run_in_threadpool(RecoveryStore().describe, job_id)
    details = job["recovery"]
    job["can_retry"] = bool(details and details["attempt"] < 3 and
        (job["status"] in ("failed", "cancelled") or job["status"] == "completed" and details["issues"]))
    return job


@router.post("/api/v1/underwriting/jobs/{job_id}/stop")
async def stop_underwriting_job(job_id: str):
    job = await get_underwriting_job(job_id)
    if job["status"] in ("queued", "running"):
        await run_in_threadpool(RecoveryStore().cancel, job_id)
    return {"job_id": job_id, "message": "已请求停止；正在进行的服务调用返回后停止后续步骤，成功结果保留"}


@router.post("/api/v1/underwriting/jobs/{job_id}/retry", status_code=202)
async def retry_underwriting_job(
    job_id: str, background_tasks: BackgroundTasks,
    ocr_provider: Annotated[OcrProvider, Depends(get_ocr_provider)],
    vision_provider: Annotated[VisionProvider, Depends(get_vision_provider)],
    component_provider: Annotated[ComponentProvider, Depends(get_underwriting_component_provider)],
):
    job = await get_underwriting_job(job_id)
    details = job.get("recovery")
    if details and details["child_id"]:
        return {"job_id": details["child_id"], "status": "existing", "reused_request": True}
    if not job["can_retry"]:
        raise HTTPException(409, "任务尚在运行、没有服务异常、缺少恢复数据或已到重试上限。补材后请新建一轮分析。")
    if not _JOB_SLOTS.acquire(blocking=False):
        raise HTTPException(503, "当前任务较多，请稍后重试")
    child_id, created = None, False
    reserved_case_id = None
    try:
        recovery = RecoveryStore()
        child_id, created = await run_in_threadpool(recovery.claim_retry, job_id, uuid4().hex)
        if not created:
            _JOB_SLOTS.release()
            return {"job_id": child_id, "status": "existing", "reused_request": True}
        saved = await run_in_threadpool(recovery.get, child_id)
        request = unpack(json.loads(saved["request_json"]))
        case_id = "recovery-" + child_id
        parent = job['case_id'] if CaseRepository().get_case(job['case_id']) else (RevisionRepository().get(job['case_id']) or {}).get('parent_id')
        context = {**(request.get('revision_context') or {}), 'parent_case_id': parent,
                   'actor': '系统恢复', 'reason': '服务异常重试；原材料和人工修正依据保持不变',
                   'reuse_corrections': True} if parent else None
        await run_in_threadpool(RevisionRepository().reserve, case_id, child_id, request, context)
        reserved_case_id = case_id
        await run_in_threadpool(AnalysisJobRepository().create_job, child_id, case_id)
        background_tasks.add_task(_run_analysis_job, job_id=child_id, case_id=case_id,
            ocr_provider=ocr_provider, vision_provider=vision_provider, component_provider=component_provider, **request)
    except ValueError as exc:
        if reserved_case_id:
            RevisionRepository().abandon(reserved_case_id)
        if created:
            recovery.abandon_claim(job_id, child_id)
        _JOB_SLOTS.release()
        raise HTTPException(409, str(exc)) from exc
    except Exception:
        if created:
            recovery.abandon_claim(job_id, child_id)
        if reserved_case_id:
            RevisionRepository().abandon(reserved_case_id)
        _JOB_SLOTS.release()
        raise
    return {"job_id": child_id, "status": "queued", "parent_job_id": job_id}


@router.get("/api/v1/underwriting/jobs/{job_id}/events")
async def underwriting_job_events(job_id: str):
    """Sanitised, deduplicated events for future enterprise notification adapters."""
    job = await get_underwriting_job(job_id)
    return {"job_id": job_id, "events": (job.get("recovery") or {}).get("events", [])}


@router.post("/api/v1/underwriting/jobs/{job_id}/manual-attention")
async def request_manual_attention(job_id: str):
    job = await get_underwriting_job(job_id)
    if job["status"] not in ("failed", "cancelled", "completed"):
        raise HTTPException(409, "请先停止任务或等待执行结束")
    await run_in_threadpool(RecoveryStore().event, job_id, "manual_attention", "已记录转人工核验请求，等待复核人员查看原材料；未改变 AI 建议或人工审核结果")
    return {"job_id": job_id, "status": "manual_attention_recorded"}
