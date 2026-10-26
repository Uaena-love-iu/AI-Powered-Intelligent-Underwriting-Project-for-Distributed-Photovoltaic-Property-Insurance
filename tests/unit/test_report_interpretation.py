import json as jsonlib

import pytest
from app.contracts import UnderwritingCase
from app.main import app
from app.reports import interpretation
from app.reports.interpretation import (
    InterpretationCache,
    report_fingerprint,
    validate_interpretation,
)
from app.reports.view import build_report_view
from app.settings import VlmSettings
from fastapi.testclient import TestClient

from tests.integration.test_report_render_api import mock_analyze_payload


def _case() -> UnderwritingCase:
    response = TestClient(app).post("/api/v1/analyze/mock", json=mock_analyze_payload())
    assert response.status_code == 200
    return UnderwritingCase.model_validate(response.json())


def test_interpretation_requires_current_decision_and_existing_reference() -> None:
    view = build_report_view(_case())
    ref = view["actions"][0]["id"]
    payload = {
        "decision_code": view["decision"]["code"],
        "points": [
            {"kind": "current", "text": "本轮系统建议仍需人工核对后确认。", "reference_ids": [ref]},
            {"kind": "basis", "text": "关键依据来自当前报告列出的材料。", "reference_ids": [ref]},
            {"kind": "verify", "text": "请先核实当前报告列出的关键事项。", "reference_ids": [ref]},
            {"kind": "next", "text": "核实后重新查看受影响的检查结论。", "reference_ids": [ref]},
        ],
    }
    assert [point["kind"] for point in validate_interpretation(payload, view)["points"]] == [
        "current", "basis", "verify", "next",
    ]
    with pytest.raises(ValueError, match="decision differs"):
        validate_interpretation({**payload, "decision_code": "accept"}, view)
    with pytest.raises(ValueError, match="unknown report reference"):
        validate_interpretation({**payload, "points": [{
            **point, "reference_ids": ["material:invented"] if index == 0
            else point["reference_ids"],
        } for index, point in enumerate(payload["points"])]}, view)
    with pytest.raises(ValueError, match="must cover"):
        validate_interpretation({**payload, "points": [
            {**payload["points"][0], "kind": "verify"}, *payload["points"][1:],
        ]}, view)


def test_cache_fingerprint_invalidates_when_review_changes(tmp_path) -> None:
    case = _case()
    first = report_fingerprint(case, model="test-model")
    second = report_fingerprint(case, model="test-model", review_revision="review-2")
    assert first != second
    cache = InterpretationCache(tmp_path / "interpretations.sqlite3")
    assert cache.get(first) is None
    saved = cache.save(first, case.case_id, "test-model", {
        "decision_code": case.decision.decision.value, "points": [],
    })
    assert saved["cached"] is False
    assert cache.get(first)["cached"] is True
    assert cache.get(second) is None


def test_provider_stream_yields_actual_text_deltas(monkeypatch) -> None:
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def raise_for_status(self):
            return None

        def iter_lines(self):
            return iter([
                'data: {"choices":[{"delta":{"content":"本轮"}}]}',
                'data: {"choices":[{"delta":{"content":"待核实"}}]}',
                "data: [DONE]",
            ])

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def stream(self, _method, _url, *, json, **_kwargs):
            assert json["stream"] is True
            payload = jsonlib.loads(json["messages"][1]["content"])
            assert payload["saved_decision"]["code"] == build_report_view(_case())["decision"]["code"]
            assert "完整报告正文" in payload["report"]
            return FakeResponse()

    monkeypatch.setattr(interpretation.httpx, "Client", FakeClient)
    settings = VlmSettings(base_url="https://example.test", api_key="test-only", model="test-model")
    assert list(interpretation._request_model_stream(
        build_report_view(_case()), settings, "完整报告正文",
    )) == [
        "本轮", "待核实",
    ]


@pytest.mark.parametrize(("decision_code", "required_guidance"), [
    ("recommend_reject", "拒保依据"),
    ("request_more", "系统数据缺口"),
    ("manual_review", "相互冲突的证据"),
    ("surcharge", "正式加费方案"),
    ("conditional_accept", "条件目前已满足"),
    ("accept", "实际完成的检查范围"),
])
def test_stream_prompt_and_flexible_summary_follow_saved_decision(
    decision_code, required_guidance,
) -> None:
    view = build_report_view(_case())
    view["decision"]["code"] = decision_code
    scenario = interpretation.SUMMARY_SCENARIOS[decision_code]
    prompt = interpretation._stream_system_prompt(view)
    assert scenario["label"] in prompt
    assert required_guidance in prompt
    assert "严格只输出三行" not in prompt
    summary = f"本案系统建议{scenario['label']}，仍待核保确认。以当前报告所列材料为依据，先核对原件，再复查受影响结论。"
    assert interpretation.validate_stream_summary(summary, view) == summary
    paragraphs = f"本案系统建议{scenario['label']}，仍待核保确认。\n\n关键依据来自当前报告。\n\n核对原件后复查受影响结论。"
    assert interpretation.validate_stream_summary(paragraphs, view) == paragraphs
    with pytest.raises(ValueError, match="saved decision"):
        interpretation.validate_stream_summary(summary.replace(scenario["label"], "其他建议"), view)
    with pytest.raises(ValueError, match="summary is empty"):
        interpretation.validate_stream_summary("  \n  ", view)


def test_stream_summary_keeps_the_independent_price_suggestion() -> None:
    view = build_report_view(_case())
    view["decision"].update({"code": "conditional_accept", "pricing_recommendation": "surcharge"})
    label = "建议附条件承保（加费）"
    assert label in interpretation._stream_system_prompt(view)
    assert interpretation.validate_stream_summary(f"本案系统{label}，仍待核保确认。", view)
    with pytest.raises(ValueError, match="saved decision"):
        interpretation.validate_stream_summary("本案系统建议附条件承保，仍待核保确认。", view)
