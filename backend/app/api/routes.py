import os
import sqlite3
from typing import Annotated

from dotenv import load_dotenv
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
    status,
)

from app.contracts import (
    Material,
    MaterialCategory,
    MaterialParseStatus,
    MaterialQualityStatus,
    RiskFinding,
    UnderwritingCase,
)
from app.intake import (
    MAX_FILE_SIZE_BYTES,
    MAX_FILES_PER_REQUEST,
    FileInspectionResult,
    inspect_file,
    read_upload_limited,
)
from app.location import AmapGeocoder
from app.pipeline import UnderwritingPipeline
from app.providers import (
    CompatibleVisionProvider,
    MaterialInput,
    MockOcrProvider,
    MockVisionProvider,
    ProviderError,
    VisionProvider,
)
from app.providers.routing import (
    RoutedVisionProvider,
)
from app.reports import generate_markdown_report
from app.settings import (
    AmapJsApiSettings,
    ProviderConfigurationError,
    SentinelHubSettings,
    VlmSettings,
)
from app.storage import CaseRepository

from .component_dependencies import PROJECT_ROOT, get_component_provider
from .schemas import MockAnalyzeRequest
from .weather_dependencies import build_weather_provider

router = APIRouter()

VISION_MEDIA_TYPES = {
    "image/jpeg",
    "image/png",
}

_mock_pipeline = UnderwritingPipeline(
    ocr_provider=MockOcrProvider(),
    vision_provider=MockVisionProvider(),
)


class MarkdownResponse(Response):
    media_type = "text/markdown"


def get_vision_provider() -> VisionProvider:
    """根据本地环境配置创建真实视觉模型 Provider。"""

    try:
        settings = VlmSettings.from_environment()
    except ProviderConfigurationError as exc:
        raise HTTPException(
            status_code=(
                status.HTTP_503_SERVICE_UNAVAILABLE
            ),
            detail="Vision provider is not configured",
        ) from exc

    return RoutedVisionProvider(
        delegate=CompatibleVisionProvider(settings=settings.for_stage("vision")),
        installation_delegate=CompatibleVisionProvider(
            settings=settings.for_stage("installation"),
        ),
        geometry_delegate=CompatibleVisionProvider(
            settings=settings.for_stage("geometry"),
        ),
    )


def _run_mock_analysis(
    request: MockAnalyzeRequest,
) -> UnderwritingCase:
    material_inputs = [
        MaterialInput(
            material=material,
            content=b"mock-file-content",
        )
        for material in request.materials
    ]

    return _mock_pipeline.run(
        case_id=request.case_id,
        project=request.project,
        materials=material_inputs,
    )


@router.get(
    "/health",
    tags=["system"],
)
def health_check() -> dict[str, str]:
    """检查后端服务是否正常运行。"""

    return {"status": "ok"}


@router.get("/ready", tags=["system"])
def readiness_check() -> dict[str, object]:
    """Report local configuration readiness without revealing credentials."""

    load_dotenv(PROJECT_ROOT / ".env", override=False)
    solar_stack_partner_api_configured = bool(
        os.getenv("PV_SOLAR_STACK_API_KEY", "").strip()
    )

    checks = {
        "ocr_model_configured": False,
        "vision_model_configured": False,
        "component_catalog_available": False,
        "weather_provider_configured": False,
        "case_database_available": False,
    }
    online_catalog_configured = False
    optional_capabilities = {
        "map_review_configured": False,
        "amap_satellite_configured": False,
        "sentinel2_configured": False,
    }
    model_routing: dict[str, str] = {}

    try:
        vlm_settings = VlmSettings.from_environment(env_file=PROJECT_ROOT / ".env")
        model_routing = {
            stage: vlm_settings.for_stage(stage).model
            for stage in (
                "classification", "filing", "ocr", "installation",
                "vision", "geometry",
            )
        }
        checks["ocr_model_configured"] = True
        checks["vision_model_configured"] = True
    except ProviderConfigurationError:
        pass

    try:
        component_provider = get_component_provider()
        checks["component_catalog_available"] = True
        online_catalog_configured = bool(component_provider.workbench.key)
    except HTTPException:
        pass

    try:
        build_weather_provider()
        checks["weather_provider_configured"] = True
    except ValueError:
        pass

    optional_capabilities["map_review_configured"] = bool(
        os.getenv("PV_AMAP_WEB_SERVICE_KEY", "").strip()
    )
    try:
        AmapJsApiSettings.from_environment(env_file=PROJECT_ROOT / ".env")
        optional_capabilities["amap_satellite_configured"] = True
    except ProviderConfigurationError:
        pass
    try:
        SentinelHubSettings.from_environment(env_file=PROJECT_ROOT / ".env")
        optional_capabilities["sentinel2_configured"] = True
    except ProviderConfigurationError:
        pass

    try:
        CaseRepository().list_cases(limit=1)
        checks["case_database_available"] = True
    except (OSError, sqlite3.Error):
        pass

    return {
        "status": "ready" if all(checks.values()) else "degraded",
        "checks": checks,
        "component_online_catalog_configured": online_catalog_configured,
        "component_online_connectivity_checked": False,
        "solar_stack_partner_api_configured": solar_stack_partner_api_configured,
        "solar_stack_partner_api_connectivity_checked": False,
        "optional_capabilities": optional_capabilities,
        "model_routing": model_routing,
        "weather_connectivity_checked": False,
        "address_geocoder_configured": AmapGeocoder.from_environment() is not None,
    }


@router.post(
    "/api/v1/files/inspect",
    response_model=list[FileInspectionResult],
    tags=["materials"],
)
async def inspect_uploaded_files(
    files: Annotated[
        list[UploadFile],
        File(
            description=(
                "需要检查的 JPEG、PNG 或 PDF 文件"
            )
        ),
    ],
) -> list[FileInspectionResult]:
    """检查上传文件的格式、可读性、大小和重复情况。"""

    if len(files) > MAX_FILES_PER_REQUEST:
        raise HTTPException(
            status_code=(
                status.HTTP_413_CONTENT_TOO_LARGE
            ),
            detail=(
                f"A maximum of "
                f"{MAX_FILES_PER_REQUEST} "
                "files is allowed"
            ),
        )

    seen_sha256: set[str] = set()
    results: list[FileInspectionResult] = []

    for uploaded_file in files:
        try:
            content = await read_upload_limited(uploaded_file)
        finally:
            await uploaded_file.close()

        results.append(
            inspect_file(
                file_name=uploaded_file.filename,
                declared_media_type=(
                    uploaded_file.content_type
                ),
                content=content,
                seen_sha256=seen_sha256,
            )
        )

    return results


@router.post(
    "/api/v1/vision/analyze",
    response_model=list[RiskFinding],
    tags=["vision"],
)
async def analyze_uploaded_image(
    vision_provider: Annotated[
        VisionProvider,
        Depends(get_vision_provider),
    ],
    file: Annotated[
        UploadFile,
        File(
            description=(
                "需要进行风险识别的 JPEG 或 PNG 图片"
            )
        ),
    ],
    material_id: Annotated[
        str,
        Form(
            min_length=1,
            description="材料唯一编号",
        ),
    ] = "material-upload-001",
    category: Annotated[
        MaterialCategory,
        Form(
            description="材料类别",
        ),
    ] = MaterialCategory.PANORAMA,
) -> list[RiskFinding]:
    """检查上传图片并调用真实视觉模型识别风险。"""

    try:
        content = await read_upload_limited(file)
    finally:
        await file.close()

    inspection = inspect_file(
        file_name=file.filename,
        declared_media_type=file.content_type,
        content=content,
    )

    if inspection.status != "accepted":
        if "file_too_large" in inspection.issues:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"Maximum file size is {MAX_FILE_SIZE_BYTES} bytes",
            )
        raise HTTPException(
            status_code=(
                status.HTTP_422_UNPROCESSABLE_CONTENT
            ),
            detail={
                "message": "Uploaded file was rejected",
                "issues": inspection.issues,
            },
        )

    if (
        inspection.media_type
        not in VISION_MEDIA_TYPES
    ):
        raise HTTPException(
            status_code=(
                status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
            ),
            detail=(
                "Vision analysis supports only "
                "JPEG and PNG images"
            ),
        )

    if (
        inspection.media_type is None
        or inspection.sha256 is None
    ):
        raise HTTPException(
            status_code=(
                status.HTTP_422_UNPROCESSABLE_CONTENT
            ),
            detail=(
                "Uploaded image could not be inspected"
            ),
        )

    material = Material(
        material_id=material_id,
        category=category,
        file_name=inspection.file_name,
        media_type=inspection.media_type,
        sha256=inspection.sha256,
        quality_status=(
            MaterialQualityStatus.UNKNOWN
        ),
        quality_confidence=None,
        quality_issues=inspection.issues,
        parse_status=(
            MaterialParseStatus.SUCCESS
        ),
    )

    material_input = MaterialInput(
        material=material,
        content=content,
    )

    try:
        return vision_provider.analyze(
            material_input
        )
    except ProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Vision provider request failed",
        ) from exc


@router.post(
    "/api/v1/analyze/mock",
    response_model=UnderwritingCase,
    tags=["underwriting"],
)
def analyze_mock(
    request: MockAnalyzeRequest,
) -> UnderwritingCase:
    """使用 Mock Provider 执行一次完整核保流程。"""

    return _run_mock_analysis(request)


@router.post(
    "/api/v1/reports/mock",
    response_class=MarkdownResponse,
    tags=["reports"],
)
def download_mock_report(
    request: MockAnalyzeRequest,
) -> MarkdownResponse:
    """执行 Mock 核保流程并返回可下载的 Markdown 报告。"""

    case = _run_mock_analysis(request)
    report = generate_markdown_report(case)

    return MarkdownResponse(
        content=report,
        headers={
            "Content-Disposition": (
                "attachment; "
                'filename="underwriting-report.md"'
            )
        },
    )
