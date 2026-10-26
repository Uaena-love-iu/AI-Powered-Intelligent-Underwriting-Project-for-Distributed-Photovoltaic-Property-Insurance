import json

import httpx
from app.contracts import (
    Material,
    MaterialCategory,
    MaterialParseStatus,
    MaterialQualityStatus,
)
from app.providers import (
    CompatibleOcrProvider,
    MaterialInput,
)
from app.settings import VlmSettings


def test_discards_pixel_bbox_without_losing_ocr_field() -> None:
    def handler(
        _request: httpx.Request,
    ) -> httpx.Response:
        response_content = {
            "fields": [
                {
                    "field_name": "filing_number",
                    "raw_value": "PV-2026-0001",
                    "normalized_value": "PV-2026-0001",
                    "value_status": "extracted",
                    "confidence": 0.98,
                    "bbox": {
                        "x_min": 120,
                        "y_min": 240,
                        "x_max": 720,
                        "y_max": 310,
                        "coordinate_space": "pixel",
                    },
                    "evidence_text": (
                        "Filing Number: PV-2026-0001"
                    ),
                }
            ]
        }

        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                response_content
                            )
                        }
                    }
                ]
            },
        )

    settings = VlmSettings(
        base_url="https://api.example.com/v1",
        api_key="test-api-key",
        model="test-model",
        timeout_seconds=10,
    )

    provider = CompatibleOcrProvider(
        settings=settings,
        transport=httpx.MockTransport(handler),
    )

    material = Material(
        material_id="material-pixel-bbox-001",
        category=(
            MaterialCategory.FILING_CERTIFICATE
        ),
        file_name="filing-page.png",
        media_type="image/png",
        quality_status=(
            MaterialQualityStatus.USABLE
        ),
        quality_confidence=0.95,
        quality_issues=[],
        parse_status=(
            MaterialParseStatus.SUCCESS
        ),
    )

    fields = provider.extract(
        MaterialInput(
            material=material,
            content=b"image-bytes",
        )
    )

    assert len(fields) == 1

    field = fields[0]

    assert field.field_name == "filing_number"
    assert field.raw_value == "PV-2026-0001"
    assert field.normalized_value == "PV-2026-0001"
    assert field.confidence == 0.98
    assert field.bbox is None
    assert field.evidence_text == (
        "Filing Number: PV-2026-0001"
    )
