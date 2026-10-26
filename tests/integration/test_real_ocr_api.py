from collections.abc import Callable, Iterator
from io import BytesIO

import pymupdf
import pytest
from app.api.ocr_routes import get_ocr_provider
from app.contracts import (
    MaterialCategory,
    MaterialQualityStatus,
    OcrField,
    OcrValueStatus,
)
from app.main import app
from app.providers import (
    MaterialInput,
    OcrProvider,
    ProviderError,
)
from fastapi.testclient import TestClient
from PIL import Image

client = TestClient(app)

ProviderOverride = Callable[
    [OcrProvider],
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


class RecordingOcrProvider(OcrProvider):
    def __init__(self) -> None:
        self.received_input: MaterialInput | None = None

    @property
    def name(self) -> str:
        return "recording-ocr-provider"

    @property
    def model_name(self) -> str:
        return "recording-model-v1"

    def extract(
        self,
        material_input: MaterialInput,
    ) -> list[OcrField]:
        self.received_input = material_input

        return [
            OcrField(
                field_id="field-api-001",
                material_id=(
                    material_input.material.material_id
                ),
                field_name="component_model",
                raw_value="PV-MODULE-580W",
                normalized_value="PV-MODULE-580W",
                value_status=(
                    OcrValueStatus.EXTRACTED
                ),
                confidence=0.98,
                bbox=None,
                provider=self.name,
                model=self.model_name,
                evidence_text=(
                    "Model: PV-MODULE-580W"
                ),
            )
        ]


class FailingOcrProvider(OcrProvider):
    @property
    def name(self) -> str:
        return "failing-ocr-provider"

    @property
    def model_name(self) -> str:
        return "failing-model-v1"

    def extract(
        self,
        material_input: MaterialInput,
    ) -> list[OcrField]:
        del material_input

        raise ProviderError(
            "sensitive upstream provider details"
        )


@pytest.fixture
def override_ocr_provider() -> Iterator[
    ProviderOverride
]:
    original_override = (
        app.dependency_overrides.get(
            get_ocr_provider
        )
    )

    def apply_override(
        provider: OcrProvider,
    ) -> None:
        app.dependency_overrides[
            get_ocr_provider
        ] = lambda: provider

    yield apply_override

    if original_override is None:
        app.dependency_overrides.pop(
            get_ocr_provider,
            None,
        )
    else:
        app.dependency_overrides[
            get_ocr_provider
        ] = original_override


def test_extracts_fields_from_uploaded_image(
    override_ocr_provider: ProviderOverride,
) -> None:
    provider = RecordingOcrProvider()
    override_ocr_provider(provider)

    response = client.post(
        "/api/v1/ocr/extract",
        data={
            "material_id": "material-api-001",
            "category": "component_nameplate",
        },
        files={
            "file": (
                "component-nameplate.png",
                png_bytes(),
                "image/png",
            )
        },
    )

    assert response.status_code == 200

    result = response.json()

    assert len(result) == 1
    assert (
        result[0]["field_id"]
        == "field-api-001"
    )
    assert (
        result[0]["material_id"]
        == "material-api-001"
    )
    assert (
        result[0]["field_name"]
        == "component_model"
    )
    assert (
        result[0]["normalized_value"]
        == "PV-MODULE-580W"
    )
    assert (
        result[0]["provider"]
        == "recording-ocr-provider"
    )

    assert provider.received_input is not None

    material = provider.received_input.material

    assert material.material_id == (
        "material-api-001"
    )
    assert (
        material.category
        is MaterialCategory.COMPONENT_NAMEPLATE
    )
    assert material.file_name == (
        "component-nameplate.png"
    )
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
    override_ocr_provider: ProviderOverride,
) -> None:
    provider = RecordingOcrProvider()
    override_ocr_provider(provider)

    response = client.post(
        "/api/v1/ocr/extract",
        data={
            "material_id": "material-broken-001",
            "category": "component_nameplate",
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


def test_accepts_pdf_for_ocr_extraction(
    override_ocr_provider: ProviderOverride,
) -> None:
    provider = RecordingOcrProvider()
    override_ocr_provider(provider)

    content = pdf_bytes()

    response = client.post(
        "/api/v1/ocr/extract",
        data={
            "material_id": "material-pdf-001",
            "category": "filing_certificate",
        },
        files={
            "file": (
                "filing.pdf",
                content,
                "application/pdf",
            )
        },
    )

    assert response.status_code == 200

    result = response.json()

    assert len(result) == 1
    assert (
        result[0]["material_id"]
        == "material-pdf-001"
    )

    assert provider.received_input is not None

    material = provider.received_input.material

    assert (
        material.category
        is MaterialCategory.FILING_CERTIFICATE
    )
    assert material.file_name == "filing.pdf"
    assert (
        material.media_type
        == "application/pdf"
    )
    assert material.sha256 is not None
    assert (
        provider.received_input.content
        == content
    )


def test_sanitizes_provider_failure(
    override_ocr_provider: ProviderOverride,
) -> None:
    override_ocr_provider(
        FailingOcrProvider()
    )

    response = client.post(
        "/api/v1/ocr/extract",
        data={
            "material_id": "material-error-001",
            "category": "component_nameplate",
        },
        files={
            "file": (
                "component-nameplate.png",
                png_bytes(),
                "image/png",
            )
        },
    )

    assert response.status_code == 502
    assert response.json() == {
        "detail": "OCR provider request failed"
    }
    assert (
        "sensitive upstream provider details"
        not in response.text
    )
