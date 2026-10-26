import json

import httpx
import pytest
from app.contracts import (
    Material,
    MaterialCategory,
    MaterialParseStatus,
    MaterialQualityStatus,
    OcrValueStatus,
)
from app.providers import (
    CompatibleOcrProvider,
    MaterialInput,
    ProviderError,
)
from app.settings import VlmSettings


def make_material_input(
    *,
    media_type: str = "image/png",
    content: bytes = b"image-bytes",
) -> MaterialInput:
    material = Material(
        material_id="material-nameplate-001",
        category=MaterialCategory.COMPONENT_NAMEPLATE,
        file_name="component-nameplate.png",
        media_type=media_type,
        quality_status=MaterialQualityStatus.USABLE,
        quality_confidence=0.95,
        quality_issues=[],
        parse_status=MaterialParseStatus.SUCCESS,
    )

    return MaterialInput(
        material=material,
        content=content,
    )


def make_settings() -> VlmSettings:
    return VlmSettings(
        base_url="https://api.example.com/v1",
        api_key="secret-test-key",
        model="example-multimodal-model",
        timeout_seconds=10,
    )


def successful_response_content() -> str:
    return """```json
{
  "fields": [
    {
      "field_name": "component_model",
      "raw_value": "PV-MODULE-580W",
      "normalized_value": "PV-MODULE-580W",
      "value_status": "extracted",
      "confidence": 0.96,
      "bbox": {
        "x_min": 0.1,
        "y_min": 0.2,
        "x_max": 0.7,
        "y_max": 0.3
      },
      "evidence_text": "型号：PV-MODULE-580W"
    },
    {
      "field_name": "manufacturer",
      "raw_value": "示例光伏有限公司",
      "normalized_value": "示例光伏有限公司",
      "value_status": "extracted",
      "confidence": 0.91,
      "bbox": null,
      "evidence_text": "制造商：示例光伏有限公司"
    },
    {
      "field_name": "rated_power_w",
      "raw_value": "580 W",
      "normalized_value": "580",
      "value_status": "extracted",
      "confidence": 0.4,
      "bbox": null,
      "evidence_text": "Maximum Power: 580 W"
    },
    {
      "field_name": "component_model",
      "raw_value": "LOW-CONFIDENCE-DUPLICATE",
      "normalized_value": "LOW-CONFIDENCE-DUPLICATE",
      "value_status": "uncertain",
      "confidence": 0.2,
      "bbox": null,
      "evidence_text": "疑似重复型号"
    },
    {
      "field_name": "serial_number",
      "raw_value": null,
      "normalized_value": null,
      "value_status": "missing",
      "confidence": 0.0,
      "bbox": null,
      "evidence_text": null
    }
  ]
}
```"""


def test_calls_compatible_api_and_converts_fields() -> None:
    def handler(
        request: httpx.Request,
    ) -> httpx.Response:
        assert str(request.url) == (
            "https://api.example.com/v1/"
            "chat/completions"
        )

        assert request.headers["Authorization"] == (
            "Bearer secret-test-key"
        )

        request_body = json.loads(request.content)

        assert request_body["model"] == (
            "example-multimodal-model"
        )
        assert request_body["temperature"] == 0
        assert request_body["enable_thinking"] is False
        assert request_body["response_format"] == {
            "type": "json_object"
        }

        user_content = request_body[
            "messages"
        ][1]["content"]

        assert (
            "component_nameplate"
            in user_content[0]["text"]
        )
        assert (
            "component_model"
            in user_content[0]["text"]
        )

        image_url = user_content[
            1
        ]["image_url"]["url"]

        assert image_url == (
            "data:image/png;base64,"
            "aW1hZ2UtYnl0ZXM="
        )

        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                successful_response_content()
                            ),
                        }
                    }
                ]
            },
        )

    provider = CompatibleOcrProvider(
        make_settings(),
        transport=httpx.MockTransport(handler),
    )

    fields = provider.extract(
        make_material_input()
    )

    assert len(fields) == 3

    assert fields[0].field_id == (
        "material-nameplate-001:"
        "component_model:1"
    )
    assert fields[0].field_name == (
        "component_model"
    )
    assert fields[0].raw_value == (
        "PV-MODULE-580W"
    )
    assert fields[0].normalized_value == (
        "PV-MODULE-580W"
    )
    assert (
        fields[0].value_status
        is OcrValueStatus.EXTRACTED
    )
    assert fields[0].bbox is not None
    assert (
        fields[0].bbox.coordinate_space
        == "normalized_0_1"
    )
    assert fields[0].provider == (
        "openai-compatible-ocr"
    )
    assert fields[0].model == (
        "example-multimodal-model"
    )

    assert fields[1].field_name == (
        "manufacturer"
    )

    assert fields[2].field_name == (
        "rated_power_w"
    )
    assert (
        fields[2].value_status
        is OcrValueStatus.UNCERTAIN
    )


def test_coerces_json_boolean_and_number_values_to_text() -> None:
    response_content = json.dumps(
        {
            "fields": [
                {
                    "field_name": "watermark_present",
                    "raw_value": True,
                    "normalized_value": True,
                    "value_status": "extracted",
                    "confidence": 0.98,
                    "evidence_text": True,
                },
                {
                    "field_name": "watermark_latitude",
                    "raw_value": 23.0123,
                    "normalized_value": 23.0123,
                    "value_status": "extracted",
                    "confidence": 0.91,
                },
            ]
        }
    )

    def handler(
        _request: httpx.Request,
    ) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": response_content,
                        }
                    }
                ]
            },
        )

    provider = CompatibleOcrProvider(
        make_settings(),
        transport=httpx.MockTransport(handler),
    )

    fields = provider.extract(make_material_input())

    assert fields[0].raw_value == "true"
    assert fields[0].normalized_value == "true"
    assert fields[0].evidence_text == "true"
    assert fields[1].raw_value == "23.0123"
    assert fields[1].normalized_value == "23.0123"


def test_skips_non_image_material_without_calling_api() -> None:
    def handler(
        _request: httpx.Request,
    ) -> httpx.Response:
        pytest.fail(
            "API must not be called "
            "for non-image material"
        )

    provider = CompatibleOcrProvider(
        make_settings(),
        transport=httpx.MockTransport(handler),
    )

    fields = provider.extract(
        make_material_input(
            media_type="application/pdf",
            content=b"%PDF-test",
        )
    )

    assert fields == []


def test_rejects_empty_image() -> None:
    provider = CompatibleOcrProvider(
        make_settings()
    )

    with pytest.raises(
        ProviderError,
        match="received an empty image",
    ):
        provider.extract(
            make_material_input(content=b"")
        )


def test_sanitizes_http_error() -> None:
    def handler(
        _request: httpx.Request,
    ) -> httpx.Response:
        return httpx.Response(
            401,
            text="sensitive upstream error",
        )

    provider = CompatibleOcrProvider(
        make_settings(),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(
        ProviderError,
        match="returned HTTP 401",
    ) as exc_info:
        provider.extract(
            make_material_input()
        )

    assert (
        "sensitive upstream error"
        not in str(exc_info.value)
    )
    assert (
        "secret-test-key"
        not in str(exc_info.value)
    )


def test_rejects_invalid_model_response() -> None:
    def handler(
        _request: httpx.Request,
    ) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "not-json",
                        }
                    }
                ]
            },
        )

    provider = CompatibleOcrProvider(
        make_settings(),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(
        ProviderError,
        match="returned an invalid response",
    ):
        provider.extract(
            make_material_input()
        )


def test_reports_safe_validation_location() -> None:
    invalid_status = "invented-secret-status"

    def handler(
        _request: httpx.Request,
    ) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "fields": [
                                        {
                                            "field_name": (
                                                "component_model"
                                            ),
                                            "raw_value": (
                                                "SECRET-VALUE"
                                            ),
                                            "normalized_value": (
                                                "SECRET-VALUE"
                                            ),
                                            "value_status": (
                                                invalid_status
                                            ),
                                            "confidence": 0.9,
                                            "bbox": None,
                                            "evidence_text": (
                                                "SECRET-EVIDENCE"
                                            ),
                                        }
                                    ]
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            },
        )

    provider = CompatibleOcrProvider(
        make_settings(),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(
        ProviderError,
        match=r"fields\.0\.value_status:enum",
    ) as exc_info:
        provider.extract(
            make_material_input()
        )

    error_message = str(exc_info.value)

    assert invalid_status not in error_message
    assert "SECRET-VALUE" not in error_message
    assert "SECRET-EVIDENCE" not in error_message


def test_rejects_extracted_field_without_value() -> None:
    def handler(
        _request: httpx.Request,
    ) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "fields": [
                                        {
                                            "field_name": (
                                                "component_model"
                                            ),
                                            "raw_value": None,
                                            "normalized_value": None,
                                            "value_status": (
                                                "extracted"
                                            ),
                                            "confidence": 0.9,
                                            "bbox": None,
                                            "evidence_text": None,
                                        }
                                    ]
                                }
                            )
                        }
                    }
                ]
            },
        )

    provider = CompatibleOcrProvider(
        make_settings(),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(
        ProviderError,
        match=r"fields\.0:value_error",
    ):
        provider.extract(
            make_material_input()
        )


def test_rejects_invalid_confidence_threshold() -> None:
    with pytest.raises(
        ValueError,
        match=(
            "confidence_threshold must be "
            "between zero and one"
        ),
    ):
        CompatibleOcrProvider(
            make_settings(),
            confidence_threshold=1.1,
        )
