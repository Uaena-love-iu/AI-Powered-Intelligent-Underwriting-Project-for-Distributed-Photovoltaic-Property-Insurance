"""Amap geocoding and human-reviewed satellite-map context for blurry images."""

import base64
import os
from datetime import UTC, datetime
from math import isclose
from time import perf_counter_ns
from typing import Annotated
from urllib.parse import urlencode
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException, Path, status
from starlette.concurrency import run_in_threadpool

from app.contracts import (
    DetectionStatus,
    MapImageryReview,
    MaterialQualityStatus,
    ProcessingStatus,
    ProcessingStep,
    ProcessingTrace,
    RiskCategory,
    SentinelContextRecord,
    UnderwritingCase,
)
from app.providers.common import ProviderError
from app.providers.geocoding import AmapGeocodingProvider, GeocodeCandidate
from app.providers.sentinel_hub import SentinelHubProvider
from app.providers.vision.sentinel_context import SentinelContextAnalyzer
from app.settings import (
    AmapJsApiSettings,
    ProviderConfigurationError,
    SentinelHubSettings,
    VlmSettings,
)
from app.storage import CaseRepository

from .component_dependencies import PROJECT_ROOT
from .schemas import (
    AmapAddressCandidate,
    AmapSatelliteMapConfig,
    MapReviewPreparationRequest,
    MapReviewPreparationResponse,
    MapReviewSubmission,
    SentinelContextRequest,
    SentinelContextResponse,
)

router = APIRouter(prefix="/api/v1/cases", tags=["map review"])

MAP_REVIEW_NOTICE = (
    "高德卫星影像可能存在更新时滞，拍摄日期也可能不可见，因此不是实时现场画面。"
    "请先确认地址和地图位置，再记录可直接观察到的内容。地图只作人工补充参考，"
    "不替代原始现场照片，不会发送给视觉模型，也不会自动改变核保结论。"
)


def _image_quality_material_ids(case: UnderwritingCase) -> list[str]:
    uncertain_quality_ids = {
        finding.material_id
        for finding in case.findings
        if finding.category is RiskCategory.IMAGE_QUALITY
        and finding.detection_status
        in {DetectionStatus.DETECTED, DetectionStatus.UNCERTAIN}
    }
    poor_quality_ids = {
        material.material_id
        for material in case.materials
        if material.media_type in {"image/jpeg", "image/png"}
        and (
            material.quality_status
            in {MaterialQualityStatus.POOR, MaterialQualityStatus.UNUSABLE}
            or bool(material.quality_issues)
        )
    }
    known_material_ids = {item.material_id for item in case.materials}
    return sorted((uncertain_quality_ids | poor_quality_ids) & known_material_ids)


def _load_geocoding_provider() -> AmapGeocodingProvider:
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    key = os.getenv("PV_AMAP_WEB_SERVICE_KEY", "").strip()
    if not key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Map review geocoding is not configured; set PV_AMAP_WEB_SERVICE_KEY.",
        )
    try:
        timeout_seconds = float(os.getenv("PV_MAP_TIMEOUT_SECONDS", "10").strip())
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Map review timeout configuration is invalid.",
        ) from exc
    if timeout_seconds <= 0:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Map review timeout configuration is invalid.",
        )
    use_system_proxy = os.getenv("PV_AMAP_USE_SYSTEM_PROXY", "false").strip().lower() in {
        "1", "true", "yes", "on"
    }
    return AmapGeocodingProvider(
        key,
        timeout_seconds=timeout_seconds,
        trust_env=use_system_proxy,
    )


def _load_satellite_map_config() -> AmapSatelliteMapConfig:
    try:
        settings = AmapJsApiSettings.from_environment(
            env_file=PROJECT_ROOT / ".env"
        )
    except ProviderConfigurationError:
        return AmapSatelliteMapConfig(configured=False)
    return AmapSatelliteMapConfig(
        configured=True,
        api_key=settings.api_key,
        security_js_code=settings.security_js_code,
    )


def _load_case(case_id: str) -> tuple[dict[str, object], UnderwritingCase]:
    record = CaseRepository().get_case(case_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Case not found",
        )
    case_payload = record.get("case")
    if not isinstance(case_payload, dict):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Stored case is invalid",
        )
    return record, UnderwritingCase.model_validate(case_payload)


def _resolve_address(
    *,
    case: UnderwritingCase,
    requested_address: str | None,
) -> str:
    address = (requested_address or case.project.site_address or "").strip()
    if not address:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Provide a project address or a legible address from the materials.",
        )
    if len(address.encode("utf-8")) > 128:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Map provider addresses must be at most 128 UTF-8 bytes.",
        )
    return address


def _amap_uri(candidate: GeocodeCandidate) -> str:
    query = urlencode(
        {
            "position": f"{candidate.amap_longitude:.6f},{candidate.amap_latitude:.6f}",
            "name": candidate.formatted_address,
            "src": "pv-underwriting-ai",
            "coordinate": "gaode",
            "callnative": "0",
        }
    )
    return f"https://uri.amap.com/marker?{query}"


def _candidate_response(candidate: GeocodeCandidate) -> AmapAddressCandidate:
    return AmapAddressCandidate(
        formatted_address=candidate.formatted_address,
        level=candidate.level or "未知",
        longitude=candidate.longitude,
        latitude=candidate.latitude,
        amap_longitude=candidate.amap_longitude,
        amap_latitude=candidate.amap_latitude,
        map_url=_amap_uri(candidate),
    )


async def _get_candidates(address: str) -> tuple[AmapGeocodingProvider, list[GeocodeCandidate]]:
    provider = _load_geocoding_provider()
    try:
        candidates = await run_in_threadpool(provider.lookup, address)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="The address is too short or too long for geocoding.",
        ) from exc
    except ProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Amap could not resolve the address. Check the address, key permissions, and quota.",
        ) from exc
    if not candidates:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Amap returned no usable address candidates. Add district and street details or correct the OCR text.",
        )
    return provider, candidates


def _match_selected_candidate(
    candidates: list[GeocodeCandidate],
    selection: AmapAddressCandidate,
) -> GeocodeCandidate:
    for candidate in candidates:
        if (
            candidate.formatted_address == selection.formatted_address
            and isclose(candidate.amap_longitude, selection.amap_longitude, abs_tol=1e-6)
            and isclose(candidate.amap_latitude, selection.amap_latitude, abs_tol=1e-6)
        ):
            return candidate
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="The selected Amap candidate changed. Resolve the address again before saving the review.",
    )


@router.post(
    "/{case_id}/map-review/prepare",
    response_model=MapReviewPreparationResponse,
)
async def prepare_map_review(
    case_id: Annotated[str, Path(min_length=1)],
    request: MapReviewPreparationRequest,
) -> MapReviewPreparationResponse:
    """Resolve a blurry-image address and return manual Amap satellite-review options."""

    _, case = await run_in_threadpool(_load_case, case_id)
    material_ids = _image_quality_material_ids(case)
    if not material_ids:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Map review is offered only when an uploaded image has a quality warning.",
        )

    address = _resolve_address(case=case, requested_address=request.address)
    _, candidates = await _get_candidates(address)
    return MapReviewPreparationResponse(
        query_address=address,
        candidates=[_candidate_response(candidate) for candidate in candidates],
        satellite_map=_load_satellite_map_config(),
        triggering_material_ids=material_ids,
        notice=MAP_REVIEW_NOTICE,
    )


@router.post(
    "/{case_id}/map-review/sentinel-context",
    response_model=SentinelContextResponse,
)
async def fetch_sentinel_context(
    case_id: Annotated[str, Path(min_length=1)],
    request: SentinelContextRequest,
) -> SentinelContextResponse:
    """Fetch Copernicus context imagery; optional VLM output remains advisory."""

    _, case = await run_in_threadpool(_load_case, case_id)
    if not _image_quality_material_ids(case):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Sentinel context is offered only when an uploaded image has a quality warning.",
        )
    address = _resolve_address(case=case, requested_address=request.address)
    _, candidates = await _get_candidates(address)
    selected = _match_selected_candidate(candidates, request.selected_candidate)
    try:
        settings = SentinelHubSettings.from_environment(env_file=PROJECT_ROOT / ".env")
    except ProviderConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Sentinel-2 is not configured; set the Copernicus Sentinel Hub client ID and secret.",
        ) from exc
    try:
        image = await run_in_threadpool(
            SentinelHubProvider(settings).fetch,
            selected.longitude,
            selected.latitude,
        )
    except ProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Copernicus Sentinel-2 imagery could not be retrieved for this location.",
        ) from exc

    metadata = SentinelContextRecord.model_validate(image.metadata.model_dump())
    if request.analyze_with_vlm:
        try:
            vlm_settings = VlmSettings.from_environment(env_file=PROJECT_ROOT / ".env")
        except ProviderConfigurationError:
            metadata = metadata.model_copy(
                update={"analysis_warning": "VLM configuration is missing; the image is available without AI analysis."}
            )
        else:
            try:
                analysis = await run_in_threadpool(
                    SentinelContextAnalyzer(vlm_settings).analyze,
                    image.content,
                )
            except ProviderError:
                metadata = metadata.model_copy(
                    update={"analysis_warning": "VLM analysis failed; the image is available for human review."}
                )
            else:
                metadata = metadata.model_copy(update={"analysis": analysis})

    return SentinelContextResponse(
        metadata=metadata,
        image_base64=base64.b64encode(image.content).decode("ascii"),
    )


@router.post("/{case_id}/map-review")
async def save_map_review(
    case_id: Annotated[str, Path(min_length=1)],
    submission: MapReviewSubmission,
) -> dict[str, object]:
    """Append the underwriter's Amap satellite observation without changing the decision."""

    _, case = await run_in_threadpool(_load_case, case_id)
    material_ids = _image_quality_material_ids(case)
    if not material_ids:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This case no longer has an image-quality warning for map review.",
        )

    address = _resolve_address(case=case, requested_address=submission.address)
    started_at = datetime.now(UTC)
    started_ns = perf_counter_ns()
    _, candidates = await _get_candidates(address)
    selected = _match_selected_candidate(candidates, submission.selected_candidate)
    finished_at = datetime.now(UTC)
    map_url = _amap_uri(selected)

    review = MapImageryReview(
        review_id=uuid4().hex,
        provider="amap_maps",
        address_source=submission.address_source,
        triggering_material_ids=material_ids,
        query_address=address,
        matched_address=selected.formatted_address,
        geocoding_confidence=None,
        address_comprehension=None,
        precise_match=None,
        match_level=selected.level,
        longitude_gcj02=selected.amap_longitude,
        latitude_gcj02=selected.amap_latitude,
        longitude_wgs84=selected.longitude,
        latitude_wgs84=selected.latitude,
        provider_map_url=map_url,
        queried_at=started_at,
        reviewed_at=finished_at,
        reviewer_name=submission.reviewer_name,
        site_match_confirmed=submission.site_match_confirmed,
        photovoltaic_visibility=submission.photovoltaic_visibility,
        observed_installation_type=submission.observed_installation_type,
        imagery_capture_date=submission.imagery_capture_date,
        observations=submission.observations,
        sentinel_context=submission.sentinel_context,
    )
    trace = ProcessingTrace(
        step=ProcessingStep.MAP_REVIEW,
        provider=(
            "amap-web-service-geocoding+copernicus-sentinel-2"
            if submission.sentinel_context
            else "amap-web-service-geocoding"
        ),
        model=(
            os.getenv("PV_VLM_MODEL", "").strip() or None
            if submission.sentinel_context and submission.sentinel_context.analysis
            else None
        ),
        started_at=started_at,
        finished_at=finished_at,
        latency_ms=(perf_counter_ns() - started_ns) // 1_000_000,
        status=ProcessingStatus.SUCCESS,
    )
    saved = await run_in_threadpool(
        CaseRepository().append_map_review,
        case_id=case_id,
        review=review,
        trace=trace,
    )
    if saved is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Case not found",
        )
    return saved
