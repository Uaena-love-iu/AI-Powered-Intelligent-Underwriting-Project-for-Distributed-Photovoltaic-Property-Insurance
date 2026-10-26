import json
from io import BytesIO

import httpx
import pytest
from PIL import Image
from app.contracts import (
    DetectionStatus,
    EnvironmentRelation,
    Material,
    MaterialCategory,
    MaterialParseStatus,
    MaterialQualityStatus,
)
from app.providers import (
    CompatibleVisionProvider,
    MaterialInput,
    ProviderError,
)
from app.rules import RuleEngine
from app.settings import VlmSettings


def make_material_input(
    *,
    media_type: str = "image/png",
    content: bytes = b"image-bytes",
) -> MaterialInput:
    material = Material(
        material_id="material-panorama-001",
        category=MaterialCategory.PANORAMA,
        file_name="panorama.png",
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
        model="example-vision-model",
        timeout_seconds=10,
    )


def successful_response_content() -> str:
    return """```json
{
  "findings": [
    {
      "category": "water_adjacent_environment",
      "label": "临近水体",
      "detection_status": "detected",
      "severity": "high",
      "confidence": 0.86,
      "environment_relation": "project_site",
      "bbox": {
        "x_min": 0.5,
        "y_min": 0.1,
        "x_max": 0.9,
        "y_max": 0.8,
        "coordinate_space": "normalized_0_1"
      },
      "evidence_text": "图片右侧存在大面积水体",
      "requires_manual_review": false
    },
    {
      "category": "severe_shading",
      "label": "疑似严重遮挡",
      "detection_status": "detected",
      "severity": "medium",
      "confidence": 0.4,
      "bbox": null,
      "evidence_text": "组件区域存在疑似阴影",
      "requires_manual_review": false
    },
    {
      "category": "hazardous_material",
      "label": "危险品",
      "detection_status": "not_detected",
      "severity": "info",
      "confidence": 0.9,
      "bbox": null,
      "evidence_text": "未发现危险品",
      "requires_manual_review": false
    }
  ]
}
```"""


def test_calls_compatible_api_and_converts_findings() -> None:
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
            "example-vision-model"
        )
        system_prompt = request_body["messages"][0]["content"]
        assert "environment_relation" in system_prompt
        assert "distant_background" in system_prompt
        assert request_body["enable_thinking"] is False
        assert request_body["response_format"] == {
            "type": "json_object"
        }
        prompt_text = request_body["messages"][1]["content"][0]["text"]
        assert "核保动作由后续规则处理" in prompt_text

        image_url = request_body[
            "messages"
        ][1]["content"][1]["image_url"]["url"]

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

    provider = CompatibleVisionProvider(
        make_settings(),
        transport=httpx.MockTransport(handler),
    )

    findings = provider.analyze(
        make_material_input()
    )

    assert len(findings) == 3

    assert findings[0].finding_id == (
        "material-panorama-001:"
        "water_adjacent_environment:1"
    )

    assert findings[0].provider == (
        "openai-compatible-vision"
    )

    assert findings[0].model == (
        "example-vision-model"
    )

    assert findings[0].bbox is not None
    assert findings[0].environment_relation is EnvironmentRelation.PROJECT_SITE

    assert (
        findings[1].detection_status
        is DetectionStatus.UNCERTAIN
    )

    assert (
        findings[1].requires_manual_review
        is True
    )
    assert findings[2].detection_status is DetectionStatus.NOT_DETECTED


def test_model_receives_evidence_instructions_without_rejection_policy() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        request_body = json.loads(request.content)
        prompt_text = request_body["messages"][1]["content"][0]["text"]
        assert "核保动作由后续规则处理" in prompt_text
        assert "自动拒保环境配置" not in prompt_text
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"findings": []}',
                        }
                    }
                ]
            },
        )

    provider = CompatibleVisionProvider(
        make_settings(),
        transport=httpx.MockTransport(handler),
    )

    assert provider.analyze(make_material_input()) == []


@pytest.mark.parametrize(
    ("evidence", "expected_status"),
    [
        (
            "图片左上角包含明显的‘SIMULATED / TEST ONLY’文字标识，表明该图片为模拟图或测试素材，"
            "非真实现场拍摄照片。虽然画质清晰，但作为核保证据的有效性存疑。",
            DetectionStatus.NOT_DETECTED,
        ),
        (
            "左上角存在明显的‘SIMULATED / TEST ONLY’文字标识，表明该图片可能为模拟图或测试素材，"
            "非真实现场拍摄照片。",
            DetectionStatus.NOT_DETECTED,
        ),
        (
            "左上角有 SIMULATED / TEST ONLY 标识；接地端子区域模糊，无法看清连接。",
            DetectionStatus.DETECTED,
        ),
    ],
)
def test_simulation_label_alone_does_not_create_image_quality_risk(
    evidence: str, expected_status: DetectionStatus,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        prompt = json.loads(request.content)["messages"][0]["content"]
        assert "【模拟图片证据边界】" in prompt
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
            "findings": [{
                "category": "image_quality", "label": "图片质量",
                "detection_status": "detected", "severity": "medium", "confidence": 0.9,
                "bbox": None, "evidence_text": evidence, "requires_manual_review": True,
            }],
        }, ensure_ascii=False)}}]})

    provider = CompatibleVisionProvider(make_settings(), transport=httpx.MockTransport(handler))
    finding = provider.analyze(make_material_input())[0]
    assert finding.detection_status is expected_status
    if expected_status is DetectionStatus.NOT_DETECTED:
        assert not finding.requires_manual_review
        assert "有效性存疑" not in finding.evidence_text
        review = RuleEngine().evaluate_material(make_material_input().material, findings=[finding])
        assert review.action.value != "request_more"
        assert "MAT-QUALITY-001" not in review.triggered_rule_ids
    else:
        assert "模糊" in finding.evidence_text


def test_defaults_bbox_coordinate_space() -> None:
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
                                    "findings": [
                                        {
                                            "category": (
                                                "minor_shading"
                                            ),
                                            "label": "轻微遮挡",
                                            "detection_status": (
                                                "detected"
                                            ),
                                            "severity": "low",
                                            "confidence": 0.8,
                                            "bbox": {
                                                "x_min": 0.1,
                                                "y_min": 0.2,
                                                "x_max": 0.4,
                                                "y_max": 0.6,
                                            },
                                            "evidence_text": (
                                                "组件表面存在线状阴影"
                                            ),
                                            "requires_manual_review": (
                                                False
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

    provider = CompatibleVisionProvider(
        make_settings(),
        transport=httpx.MockTransport(handler),
    )

    findings = provider.analyze(
        make_material_input()
    )

    assert len(findings) == 1
    assert findings[0].bbox is not None
    assert (
        findings[0].bbox.coordinate_space
        == "normalized_0_1"
    )


def test_panorama_edge_recheck_maps_confirmed_crop_box_to_original() -> None:
    source = BytesIO()
    Image.new("RGB", (800, 500), "white").save(source, format="PNG")
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        if len(calls) == 1:
            finding = {
                "category": "minor_shading", "label": "遮挡检查",
                "detection_status": "not_detected", "severity": "info",
                "confidence": 0.9, "bbox": None,
                "evidence_text": "整图未看见遮挡", "requires_manual_review": False,
            }
        else:
            finding = {
                "category": "minor_shading", "label": "组件局部遮挡",
                "detection_status": "detected", "severity": "medium",
                "confidence": 0.9,
                "bbox": {"x_min": 0.2, "y_min": 0.3,
                         "x_max": 0.4, "y_max": 0.5},
                "evidence_text": "左侧组件有枝叶覆盖",
                "requires_manual_review": True,
            }
        return httpx.Response(200, json={"choices": [{"message": {
            "content": json.dumps({"findings": [finding]}, ensure_ascii=False),
        }}]})

    provider = CompatibleVisionProvider(
        make_settings(), transport=httpx.MockTransport(handler),
    )
    result = provider.analyze(make_material_input(content=source.getvalue()))
    assert len(calls) == 2
    assert "局部图像核验员" in calls[1]["messages"][0]["content"]
    assert result[0].detection_status is DetectionStatus.DETECTED
    assert result[0].requires_manual_review
    assert result[0].bbox is not None
    assert result[0].bbox.x_min == pytest.approx(0.11)
    assert result[0].bbox.y_min == pytest.approx(0.318)
    assert result[0].bbox.x_max == pytest.approx(0.22)
    assert result[0].bbox.y_max == pytest.approx(0.45)


def test_panorama_edge_recheck_does_not_invent_unconfirmed_shading() -> None:
    source = BytesIO()
    Image.new("RGB", (800, 500), "white").save(source, format="PNG")
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        finding = {
            "category": "minor_shading", "label": "遮挡检查",
            "detection_status": "not_detected", "severity": "info",
            "confidence": 0.9, "bbox": None,
            "evidence_text": "组件表面没有可见遮挡",
            "requires_manual_review": False,
        }
        return httpx.Response(200, json={"choices": [{"message": {
            "content": json.dumps({"findings": [finding]}, ensure_ascii=False),
        }}]})

    provider = CompatibleVisionProvider(
        make_settings(), transport=httpx.MockTransport(handler),
    )
    result = provider.analyze(make_material_input(content=source.getvalue()))
    assert calls == 3
    assert result[0].detection_status is DetectionStatus.NOT_DETECTED
    assert result[0].bbox is None


def test_skips_non_image_material_without_calling_api() -> None:
    def handler(
        _request: httpx.Request,
    ) -> httpx.Response:
        pytest.fail(
            "API must not be called "
            "for non-image material"
        )

    provider = CompatibleVisionProvider(
        make_settings(),
        transport=httpx.MockTransport(handler),
    )

    findings = provider.analyze(
        make_material_input(
            media_type="application/pdf",
            content=b"pdf-bytes",
        )
    )

    assert findings == []


def test_rejects_empty_image() -> None:
    provider = CompatibleVisionProvider(
        make_settings()
    )

    with pytest.raises(
        ProviderError,
        match="received an empty image",
    ):
        provider.analyze(
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

    provider = CompatibleVisionProvider(
        make_settings(),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(
        ProviderError,
        match="returned HTTP 401",
    ) as exc_info:
        provider.analyze(
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
                            "content": "not-json"
                        }
                    }
                ]
            },
        )

    provider = CompatibleVisionProvider(
        make_settings(),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(
        ProviderError,
        match="returned an invalid response",
    ):
        provider.analyze(
            make_material_input()
        )


def test_reports_safe_validation_location() -> None:
    invalid_category = "invented-secret-category"

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
                                    "findings": [
                                        {
                                            "category": (
                                                invalid_category
                                            ),
                                            "label": "错误类别",
                                            "detection_status": (
                                                "detected"
                                            ),
                                            "severity": "low",
                                            "confidence": 0.8,
                                            "bbox": None,
                                            "evidence_text": (
                                                "测试错误类别"
                                            ),
                                            "requires_manual_review": (
                                                False
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

    provider = CompatibleVisionProvider(
        make_settings(),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(
        ProviderError,
        match=r"findings\.0\.category:enum",
    ) as exc_info:
        provider.analyze(
            make_material_input()
        )

    assert invalid_category not in str(exc_info.value)


def test_rejects_invalid_confidence_threshold() -> None:
    with pytest.raises(
        ValueError,
        match=(
            "confidence_threshold must be "
            "between zero and one"
        ),
    ):
        CompatibleVisionProvider(
            make_settings(),
            confidence_threshold=1.1,
        )


def test_retries_malformed_output_once_with_validation_feedback():
    requests = []
    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        content = 'not-json' if len(requests) == 1 else '{"findings": [], "watermark_present": false}'
        return httpx.Response(200, json={'choices': [{'message': {'content': content}}]})
    provider = CompatibleVisionProvider(make_settings(), transport=httpx.MockTransport(handler))
    result = provider.analyze_with_watermark(make_material_input())
    assert result.watermark_present is False
    assert len(requests) == 2
    assert '上次输出未通过格式校验' in requests[1]['messages'][-1]['content']
    assert requests[0]['messages'][1] == requests[1]['messages'][1]


def test_does_not_retry_authentication_failure():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(401, json={'error': 'secret upstream body'})
    provider = CompatibleVisionProvider(make_settings(), transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderError, match='HTTP 401'):
        provider.analyze(make_material_input())
    assert len(calls) == 1


def test_retired_findings_are_ignored_without_breaking_saved_case_categories():
    from app.contracts import RiskCategory
    from app.prompts.vision import output_contract

    assert RiskCategory("support_corrosion") is RiskCategory.SUPPORT_CORROSION
    assert "support_corrosion" not in output_contract()
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
            "findings": [
                {"category": "support_corrosion"},
                {"category": "module_damage", "label": "组件破损",
                 "detection_status": "detected", "severity": "medium",
                 "confidence": 0.9, "bbox": None, "evidence_text": "可见破损",
                 "requires_manual_review": False},
            ],
        })}}]})

    provider = CompatibleVisionProvider(make_settings(), transport=httpx.MockTransport(handler))
    findings = provider.analyze(make_material_input())
    assert [item.category for item in findings] == [RiskCategory.MODULE_DAMAGE]
    assert len(calls) == 1
