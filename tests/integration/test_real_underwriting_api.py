import json
from collections.abc import Callable, Iterator
from io import BytesIO

import pymupdf
import pytest
from app.api.ocr_routes import get_ocr_provider
from app.api.routes import get_vision_provider
from app.api.weather_dependencies import get_weather_provider
from app.contracts import (
    DetectionStatus,
    MaterialCategory,
    OcrField,
    OcrValueStatus,
    ProcessingStatus,
    ProcessingStep,
    RiskCategory,
    RiskFinding,
    RiskSeverity,
    WeatherProfile,
)
from app.main import app
from app.providers import (
    MaterialInput,
    OcrProvider,
    ProviderError,
    VisionProvider,
    WeatherProvider,
)
from app.providers.vision.base import VisionAnalysis
from fastapi.testclient import TestClient
from PIL import Image

client = TestClient(app)

ProviderOverride = Callable[
    [OcrProvider, VisionProvider],
    None,
]


def png_bytes(
    color: str = "white",
) -> bytes:
    buffer = BytesIO()

    Image.new(
        "RGB",
        (80, 60),
        color=color,
    ).save(
        buffer,
        format="PNG",
    )

    return buffer.getvalue()


def pdf_bytes() -> bytes:
    document = pymupdf.open()

    try:
        page = document.new_page(
            width=300,
            height=200,
        )
        page.insert_text(
            (30, 60),
            "Filing certificate test",
        )
        return document.tobytes()
    finally:
        document.close()


def project_payload() -> dict:
    return {
        "project_name": "真实接口测试项目",
        "insured_name": "示例制造企业有限公司",
        "project_entity": "示例新能源有限公司",
        "project_type": "rooftop",
        "installation_type": "color_steel_roof",
        "site_address": "广东省示例市示例区",
        "province": "广东省",
        "city": "示例市",
        "district": "示例区",
        "longitude": 113.25,
        "latitude": 23.12,
        "proposed_start_date": "2026-10-01",
        "component_model": "JAM66D42-580/MB",
    }


def manifest_payload() -> dict:
    return {
        "case_id": "case-real-api-001",
        "project": project_payload(),
        "materials": [
            {
                "material_id": (
                    "material-nameplate-001"
                ),
                "category": (
                    "component_nameplate"
                ),
            },
            {
                "material_id": (
                    "material-panorama-001"
                ),
                "category": "panorama",
            },
        ],
    }


class RecordingOcrProvider(OcrProvider):
    def __init__(self) -> None:
        self.received_inputs: list[
            MaterialInput
        ] = []

    @property
    def name(self) -> str:
        return "recording-ocr-provider"

    @property
    def model_name(self) -> str:
        return "recording-ocr-v1"

    def extract(
        self,
        material_input: MaterialInput,
    ) -> list[OcrField]:
        self.received_inputs.append(
            material_input
        )

        if (
            material_input.material.category
            is not MaterialCategory
            .COMPONENT_NAMEPLATE
        ):
            return []

        return [
            OcrField(
                field_id=(
                    f"{material_input.material.material_id}:"
                    "component_model:1"
                ),
                material_id=(
                    material_input.material.material_id
                ),
                field_name="component_model",
                raw_value="JAM66D42-580/MB",
                normalized_value="JAM66D42-580/MB",
                value_status=(
                    OcrValueStatus.EXTRACTED
                ),
                confidence=0.98,
                bbox=None,
                provider=self.name,
                model=self.model_name,
                evidence_text=(
                    "Model: JAM66D42-580/MB"
                ),
            )
        ]


class RecordingVisionProvider(VisionProvider):
    def __init__(self) -> None:
        self.received_inputs: list[
            MaterialInput
        ] = []

    @property
    def name(self) -> str:
        return "recording-vision-provider"

    @property
    def model_name(self) -> str:
        return "recording-vision-v1"

    def analyze_with_watermark(self, material_input: MaterialInput) -> VisionAnalysis:
        return VisionAnalysis(
            findings=self.analyze(material_input),
            watermark_present=(
                True if material_input.material.category is MaterialCategory.PANORAMA else None
            ),
        )

    def analyze(
        self,
        material_input: MaterialInput,
    ) -> list[RiskFinding]:
        self.received_inputs.append(
            material_input
        )

        if (
            material_input.material.category
            is not MaterialCategory.PANORAMA
        ):
            return []

        return [
            RiskFinding(
                finding_id=(
                    f"{material_input.material.material_id}:"
                    "minor_shading:1"
                ),
                material_id=(
                    material_input.material.material_id
                ),
                category=(
                    RiskCategory.MINOR_SHADING
                ),
                label="轻微遮挡",
                detection_status=(
                    DetectionStatus.DETECTED
                ),
                severity=RiskSeverity.LOW,
                confidence=0.88,
                bbox=None,
                evidence_text=(
                    "组件表面存在小面积阴影"
                ),
                provider=self.name,
                model=self.model_name,
                requires_manual_review=False,
            )
        ]


class FailingOcrProvider(OcrProvider):
    @property
    def name(self) -> str:
        return "failing-ocr-provider"

    @property
    def model_name(self) -> str:
        return "failing-ocr-v1"

    def extract(
        self,
        material_input: MaterialInput,
    ) -> list[OcrField]:
        del material_input

        raise ProviderError(
            "sensitive upstream provider details"
        )


class StaticWeatherProvider(WeatherProvider):
    @property
    def name(self) -> str:
        return "static-weather-provider"

    def lookup(
        self,
        *,
        longitude: float,
        latitude: float,
    ) -> WeatherProfile:
        return WeatherProfile(
            longitude=longitude,
            latitude=latitude,
            historical_max_wind_m_s=30.0,
            historical_max_hail_mm=20.0,
            historical_max_snow_load_pa=3000.0,
            observation_start="2021-01-01",
            observation_end="2025-12-31",
            source_name="integration-test-weather",
            source_url=None,
            retrieved_at="2026-09-26T00:00:00+00:00",
        )


@pytest.fixture
def override_real_providers() -> Iterator[
    ProviderOverride
]:
    original_ocr_override = (
        app.dependency_overrides.get(
            get_ocr_provider
        )
    )
    original_vision_override = (
        app.dependency_overrides.get(
            get_vision_provider
        )
    )
    original_weather_override = (
        app.dependency_overrides.get(
            get_weather_provider
        )
    )

    def apply_override(
        ocr_provider: OcrProvider,
        vision_provider: VisionProvider,
    ) -> None:
        app.dependency_overrides[
            get_ocr_provider
        ] = lambda: ocr_provider

        app.dependency_overrides[
            get_vision_provider
        ] = lambda: vision_provider

        app.dependency_overrides[
            get_weather_provider
        ] = lambda: StaticWeatherProvider()

    yield apply_override

    if original_ocr_override is None:
        app.dependency_overrides.pop(
            get_ocr_provider,
            None,
        )
    else:
        app.dependency_overrides[
            get_ocr_provider
        ] = original_ocr_override

    if original_vision_override is None:
        app.dependency_overrides.pop(
            get_vision_provider,
            None,
        )
    else:
        app.dependency_overrides[
            get_vision_provider
        ] = original_vision_override

    if original_weather_override is None:
        app.dependency_overrides.pop(
            get_weather_provider,
            None,
        )
    else:
        app.dependency_overrides[
            get_weather_provider
        ] = original_weather_override


def test_runs_complete_real_underwriting_pipeline(
    override_real_providers: ProviderOverride,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "PV_CASE_DB_PATH",
        str(tmp_path / "cases.sqlite3"),
    )
    ocr_provider = RecordingOcrProvider()
    vision_provider = RecordingVisionProvider()

    override_real_providers(
        ocr_provider,
        vision_provider,
    )

    response = client.post(
        "/api/v1/underwriting/analyze",
        data={
            "manifest": json.dumps(
                manifest_payload(),
                ensure_ascii=False,
            )
        },
        files=[
            (
                "files",
                (
                    "component-nameplate.png",
                    png_bytes("white"),
                    "image/png",
                ),
            ),
            (
                "files",
                (
                    "site-panorama.png",
                    png_bytes("gray"),
                    "image/png",
                ),
            ),
        ],
    )

    assert response.status_code == 200

    result = response.json()

    assert (
        result["case_id"]
        == "case-real-api-001"
    )
    assert len(result["materials"]) == 2
    assert len(result["ocr_fields"]) == 1
    assert len(result["findings"]) == 1
    assert len(result["material_reviews"]) == 2
    assert any(trace["step"] == "installation_group" for trace in result["processing_trace"])

    assert (
        result["ocr_fields"][0]["field_name"]
        == "component_model"
    )
    assert (
        result["findings"][0]["category"]
        == "minor_shading"
    )
    assert (
        result["decision"]["decision"]
        == "request_more"
    )

    saved_case = client.get(
        "/api/v1/cases/case-real-api-001"
    )
    assert saved_case.status_code == 200
    assert saved_case.json()["case"]["case_id"] == "case-real-api-001"
    assert saved_case.json()["case"]["catastrophe_assessment"] is None
    assert saved_case.json()["review_status"] == "pending"

    review_response = client.post(
        "/api/v1/cases/case-real-api-001/review",
        json={
            "reviewer_name": "测试核保员",
            "final_decision": "conditional_accept",
            "comment": "测试人工复核意见",
        },
    )
    assert review_response.status_code == 200
    assert review_response.json()["review_status"] == "completed"
    assert review_response.json()["review_history"][0]["final_decision"] == "conditional_accept"

    saved_report = client.get(
        "/api/v1/cases/case-real-api-001/report"
    )
    assert saved_report.status_code == 200
    assert "抗灾评估前的阶段动作与证据" in saved_report.text
    assert "人工复核记录" in saved_report.text
    assert "测试核保员" in saved_report.text
    assert "测试人工复核意见" in saved_report.text

    completed_cases = client.get(
        "/api/v1/cases?review_status=completed&decision=request_more"
    )
    assert completed_cases.status_code == 200
    assert [item["case_id"] for item in completed_cases.json()] == [
        "case-real-api-001"
    ]

    assert result["component_profile"] is None

    component_trace = next(
        trace
        for trace in result["processing_trace"]
        if trace["step"]
        == ProcessingStep.COMPONENT_LOOKUP
    )

    assert component_trace["status"] == ProcessingStatus.SKIPPED

    assert result["weather_profile"] is None
    assert result["catastrophe_assessment"] is None

    weather_trace = next(
        trace
        for trace in result["processing_trace"]
        if trace["step"] == ProcessingStep.WEATHER_LOOKUP
    )
    assert weather_trace["status"] == ProcessingStatus.SKIPPED


    assert sorted(x.ocr_task for x in ocr_provider.received_inputs) == ['business', 'watermark', 'watermark']
    assert len(
        vision_provider.received_inputs
    ) == 2

    assert sorted((x.material.file_name, x.ocr_task) for x in ocr_provider.received_inputs) == [
        ('component-nameplate.png', 'business'),
        ('component-nameplate.png', 'watermark'),
        ('site-panorama.png', 'watermark'),
    ]


def test_background_job_reports_progress_and_saves_result(
    override_real_providers: ProviderOverride,
) -> None:
    override_real_providers(
        RecordingOcrProvider(),
        RecordingVisionProvider(),
    )
    payload = manifest_payload()
    payload["case_id"] = "case-job-api-001"

    response = client.post(
        "/api/v1/underwriting/jobs",
        data={"manifest": json.dumps(payload, ensure_ascii=False)},
        files=[
            ("files", ("component-nameplate.png", png_bytes(), "image/png")),
            ("files", ("site-panorama.png", png_bytes("gray"), "image/png")),
        ],
    )

    assert response.status_code == 202
    job_id = response.json()["job_id"]
    job_response = client.get(f"/api/v1/underwriting/jobs/{job_id}")

    assert job_response.status_code == 200
    job = job_response.json()
    assert job["status"] == "completed"
    assert job["progress_percent"] == 100
    assert job["result"]["case_id"] == "case-job-api-001"
    assert client.get("/api/v1/cases/case-job-api-001").status_code == 200


def test_rejects_file_count_mismatch(
    override_real_providers: ProviderOverride,
) -> None:
    ocr_provider = RecordingOcrProvider()
    vision_provider = RecordingVisionProvider()

    override_real_providers(
        ocr_provider,
        vision_provider,
    )

    response = client.post(
        "/api/v1/underwriting/analyze",
        data={
            "manifest": json.dumps(
                manifest_payload(),
                ensure_ascii=False,
            )
        },
        files={
            "files": (
                "component-nameplate.png",
                png_bytes(),
                "image/png",
            )
        },
    )

    assert response.status_code == 422
    assert response.json() == {
        "detail": {
            "message": (
                "Uploaded file count does not "
                "match manifest materials"
            ),
            "expected": 2,
            "received": 1,
        }
    }
    assert ocr_provider.received_inputs == []
    assert vision_provider.received_inputs == []


def test_rejects_invalid_manifest(
    override_real_providers: ProviderOverride,
) -> None:
    ocr_provider = RecordingOcrProvider()
    vision_provider = RecordingVisionProvider()

    override_real_providers(
        ocr_provider,
        vision_provider,
    )

    response = client.post(
        "/api/v1/underwriting/analyze",
        data={
            "manifest": "{not-valid-json"
        },
        files={
            "files": (
                "component-nameplate.png",
                png_bytes(),
                "image/png",
            )
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"][
        "message"
    ] == "Invalid underwriting manifest"
    assert ocr_provider.received_inputs == []
    assert vision_provider.received_inputs == []


def test_records_duplicate_uploaded_files_without_double_counting(
    override_real_providers: ProviderOverride,
) -> None:
    ocr_provider = RecordingOcrProvider()
    vision_provider = RecordingVisionProvider()

    override_real_providers(
        ocr_provider,
        vision_provider,
    )

    duplicate_content = png_bytes()

    response = client.post(
        "/api/v1/underwriting/analyze",
        data={
            "manifest": json.dumps(
                manifest_payload(),
                ensure_ascii=False,
            )
        },
        files=[
            (
                "files",
                (
                    "component-nameplate.png",
                    duplicate_content,
                    "image/png",
                ),
            ),
            (
                "files",
                (
                    "site-panorama.png",
                    duplicate_content,
                    "image/png",
                ),
            ),
        ],
    )

    assert response.status_code == 200
    result = response.json()
    assert result["materials"][1]["duplicate_of_material_id"] == result["materials"][0]["material_id"]
    assert {
        item.material.material_id for item in ocr_provider.received_inputs
    } <= {result["materials"][0]["material_id"]}


def test_accepts_pdf_for_ocr_processing(
    override_real_providers: ProviderOverride,
) -> None:
    ocr_provider = RecordingOcrProvider()
    vision_provider = RecordingVisionProvider()

    override_real_providers(
        ocr_provider,
        vision_provider,
    )

    content = pdf_bytes()

    manifest = {
        "case_id": "case-pdf-001",
        "project": project_payload(),
        "materials": [
            {
                "material_id": (
                    "material-filing-001"
                ),
                "category": (
                    "filing_certificate"
                ),
            }
        ],
    }

    response = client.post(
        "/api/v1/underwriting/analyze",
        data={
            "manifest": json.dumps(
                manifest,
                ensure_ascii=False,
            )
        },
        files={
            "files": (
                "filing.pdf",
                content,
                "application/pdf",
            )
        },
    )

    assert response.status_code == 200

    result = response.json()

    assert len(result["materials"]) == 1
    assert (
        result["materials"][0]["media_type"]
        == "application/pdf"
    )
    assert (
        result["materials"][0]["file_name"]
        == "filing.pdf"
    )

    assert len(
        ocr_provider.received_inputs
    ) == 1
    assert len(vision_provider.received_inputs) == 0

    assert (
        ocr_provider
        .received_inputs[0]
        .material
        .media_type
        == "application/pdf"
    )
    assert (
        ocr_provider
        .received_inputs[0]
        .content
        == content
    )


def test_records_provider_failure_without_leaking_details(
    override_real_providers: ProviderOverride,
) -> None:
    vision_provider = RecordingVisionProvider()

    override_real_providers(
        FailingOcrProvider(),
        vision_provider,
    )

    manifest = {
        "case_id": "case-provider-failure-001",
        "project": project_payload(),
        "materials": [
            {
                "material_id": (
                    "material-panorama-001"
                ),
                "category": "panorama",
            }
        ],
    }

    response = client.post(
        "/api/v1/underwriting/analyze",
        data={
            "manifest": json.dumps(
                manifest,
                ensure_ascii=False,
            )
        },
        files={
            "files": (
                "site-panorama.png",
                png_bytes(),
                "image/png",
            )
        },
    )

    assert response.status_code == 200

    result = response.json()

    ocr_trace = next(
        trace
        for trace in result["processing_trace"]
        if trace["step"] == ProcessingStep.OCR and trace["status"] == ProcessingStatus.FAILED
    )

    assert (
        ocr_trace["status"]
        == ProcessingStatus.FAILED
    )
    assert (
        ocr_trace["error_code"]
        == "OCR_PROVIDER_FAILURE"
    )
    assert (
        "sensitive upstream provider details"
        not in response.text
    )
    assert (
        "SYS-PROVIDER-FAILURE"
        in result["decision"]["decisive_rule_ids"]
    )
