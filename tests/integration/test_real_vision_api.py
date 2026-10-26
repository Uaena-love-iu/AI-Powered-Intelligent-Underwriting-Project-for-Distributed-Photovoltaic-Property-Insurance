from collections.abc import Callable, Iterator
from io import BytesIO

import pymupdf
import pytest
from app.api.routes import get_vision_provider
from app.contracts import (
    MaterialCategory,
    MaterialQualityStatus,
    RiskCategory,
    RiskFinding,
    RiskSeverity,
)
from app.main import app
from app.providers import (
    MaterialInput,
    ProviderError,
    VisionProvider,
)
from fastapi.testclient import TestClient
from PIL import Image

client = TestClient(app)

ProviderOverride = Callable[
    [VisionProvider],
    None,
]


def png_bytes() -> bytes:
    buffer = BytesIO()

    Image.new(
        "RGB",
        (80, 60),
        color="white",
    ).save(
        buffer,
        format="PNG",
    )

    return buffer.getvalue()


def pdf_bytes() -> bytes:
    document = pymupdf.open()

    try:
        document.new_page(
            width=100,
            height=100,
        )
        return document.tobytes()
    finally:
        document.close()


class RecordingVisionProvider(VisionProvider):
    def __init__(self) -> None:
        self.received_input: MaterialInput | None = None

    @property
    def name(self) -> str:
        return "recording-vision-provider"

    @property
    def model_name(self) -> str:
        return "recording-model-v1"

    def analyze(
        self,
        material_input: MaterialInput,
    ) -> list[RiskFinding]:
        self.received_input = material_input

        return [
            RiskFinding(
                finding_id="finding-api-001",
                material_id=(
                    material_input.material.material_id
                ),
                category=RiskCategory.MINOR_SHADING,
                label="轻微遮挡",
                detection_status="detected",
                severity=RiskSeverity.LOW,
                confidence=0.88,
                bbox=None,
                evidence_text="组件表面存在小面积阴影",
                provider=self.name,
                model=self.model_name,
                requires_manual_review=False,
            )
        ]


class FailingVisionProvider(VisionProvider):
    @property
    def name(self) -> str:
        return "failing-vision-provider"

    @property
    def model_name(self) -> str:
        return "failing-model-v1"

    def analyze(
        self,
        material_input: MaterialInput,
    ) -> list[RiskFinding]:
        del material_input

        raise ProviderError(
            "sensitive upstream provider details"
        )


@pytest.fixture
def override_vision_provider() -> Iterator[
    ProviderOverride
]:
    original_override = (
        app.dependency_overrides.get(
            get_vision_provider
        )
    )

    def apply_override(
        provider: VisionProvider,
    ) -> None:
        app.dependency_overrides[
            get_vision_provider
        ] = lambda: provider

    yield apply_override

    if original_override is None:
        app.dependency_overrides.pop(
            get_vision_provider,
            None,
        )
    else:
        app.dependency_overrides[
            get_vision_provider
        ] = original_override


def test_analyzes_uploaded_image(
    override_vision_provider: ProviderOverride,
) -> None:
    provider = RecordingVisionProvider()
    override_vision_provider(provider)

    response = client.post(
        "/api/v1/vision/analyze",
        data={
            "material_id": "material-api-001",
            "category": "panorama",
        },
        files={
            "file": (
                "panorama.png",
                png_bytes(),
                "image/png",
            )
        },
    )

    assert response.status_code == 200

    result = response.json()

    assert len(result) == 1
    assert (
        result[0]["finding_id"]
        == "finding-api-001"
    )
    assert (
        result[0]["material_id"]
        == "material-api-001"
    )
    assert (
        result[0]["category"]
        == "minor_shading"
    )
    assert (
        result[0]["provider"]
        == "recording-vision-provider"
    )

    assert provider.received_input is not None

    material = provider.received_input.material

    assert material.material_id == "material-api-001"
    assert material.category is MaterialCategory.PANORAMA
    assert material.file_name == "panorama.png"
    assert material.media_type == "image/png"
    assert material.sha256 is not None
    assert (
        material.quality_status
        is MaterialQualityStatus.UNKNOWN
    )
    assert (
        provider.received_input.content
        == png_bytes()
    )


def test_rejects_corrupt_image_before_provider_call(
    override_vision_provider: ProviderOverride,
) -> None:
    provider = RecordingVisionProvider()
    override_vision_provider(provider)

    response = client.post(
        "/api/v1/vision/analyze",
        data={
            "material_id": "material-broken-001",
            "category": "panorama",
        },
        files={
            "file": (
                "broken.png",
                b"not-a-real-image",
                "image/png",
            )
        },
    )

    assert response.status_code == 422
    assert response.json() == {
        "detail": {
            "message": "Uploaded file was rejected",
            "issues": [
                "unsupported_or_corrupt_file"
            ],
        }
    }
    assert provider.received_input is None


def test_rejects_pdf_for_vision_analysis(
    override_vision_provider: ProviderOverride,
) -> None:
    provider = RecordingVisionProvider()
    override_vision_provider(provider)

    response = client.post(
        "/api/v1/vision/analyze",
        data={
            "material_id": "material-pdf-001",
            "category": "filing_certificate",
        },
        files={
            "file": (
                "filing.pdf",
                pdf_bytes(),
                "application/pdf",
            )
        },
    )

    assert response.status_code == 415
    assert response.json() == {
        "detail": (
            "Vision analysis supports only "
            "JPEG and PNG images"
        )
    }
    assert provider.received_input is None


def test_sanitizes_provider_failure(
    override_vision_provider: ProviderOverride,
) -> None:
    override_vision_provider(
        FailingVisionProvider()
    )

    response = client.post(
        "/api/v1/vision/analyze",
        data={
            "material_id": "material-error-001",
            "category": "panorama",
        },
        files={
            "file": (
                "panorama.png",
                png_bytes(),
                "image/png",
            )
        },
    )

    assert response.status_code == 502
    assert response.json() == {
        "detail": "Vision provider request failed"
    }
    assert (
        "sensitive upstream provider details"
        not in response.text
    )
