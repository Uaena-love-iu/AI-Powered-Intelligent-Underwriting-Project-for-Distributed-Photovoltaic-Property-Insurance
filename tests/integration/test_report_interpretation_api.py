import json

from app.config.llm_runtime import update_runtime
from app.main import app
from app.prompts.stages import DEFAULT_STAGE_PROMPTS
from app.reports import interpretation
from app.settings import VlmSettings
from fastapi.testclient import TestClient

from tests.integration.test_report_render_api import mock_analyze_payload


def test_interpretation_is_separate_cached_and_source_linked(monkeypatch, tmp_path) -> None:
    client = TestClient(app)
    case = client.post("/api/v1/analyze/mock", json=mock_analyze_payload()).json()
    settings = VlmSettings(base_url="https://example.test", api_key="test-only", model="test-model")
    monkeypatch.setattr(VlmSettings, "from_environment", classmethod(lambda cls: settings))
    interpretation_cache = interpretation.InterpretationCache(tmp_path / "cache.sqlite3")
    monkeypatch.setattr(interpretation, "InterpretationCache", lambda: interpretation_cache)
    calls = []

    def fake_model(view, _settings):
        calls.append(view["case_id"])
        return {
            "decision_code": view["decision"]["code"],
            "points": [
                {"kind": "current", "text": "当前系统建议需结合原件继续核对。",
                 "reference_ids": [view["actions"][0]["id"]]},
                {"kind": "basis", "text": "报告列出的原件是当前判断依据。",
                 "reference_ids": [view["actions"][0]["id"]]},
                {"kind": "verify", "text": "请核实报告中的首项待办，并记录原件核对结果。",
                 "reference_ids": [view["actions"][0]["id"]]},
                {"kind": "next", "text": "核实后重查受影响的核保检查结论。",
                 "reference_ids": [view["actions"][0]["id"]]},
            ],
        }

    monkeypatch.setattr(interpretation, "_request_model", fake_model)
    report = client.post("/api/v1/reports/render", json=case)
    assert report.status_code == 200
    first = client.post("/api/v1/reports/interpret", json={"case": case})
    second = client.post("/api/v1/reports/interpret", json={"case": case})
    assert first.status_code == second.status_code == 200
    assert first.json()["cached"] is False
    assert second.json()["cached"] is True
    assert len(calls) == 1
    reference = first.json()["payload"]["points"][0]["reference_ids"][0]
    assert reference in first.json()["references"]


def test_invalid_model_reference_does_not_replace_the_report(monkeypatch, tmp_path) -> None:
    client = TestClient(app)
    case = client.post("/api/v1/analyze/mock", json=mock_analyze_payload()).json()
    settings = VlmSettings(base_url="https://example.test", api_key="test-only", model="test-model")
    monkeypatch.setattr(VlmSettings, "from_environment", classmethod(lambda cls: settings))
    cache = interpretation.InterpretationCache(tmp_path / "cache.sqlite3")
    monkeypatch.setattr(interpretation, "InterpretationCache", lambda: cache)
    monkeypatch.setattr(interpretation, "_request_model", lambda view, _: {
        "decision_code": view["decision"]["code"],
        "points": [
            {"kind": kind, "text": "这是一条没有有效证据引用的模型输出。",
             "reference_ids": ["material:not-real"]}
            for kind in ("current", "basis", "verify", "next")
        ],
    })
    response = client.post("/api/v1/reports/interpret", json={"case": case})
    assert response.status_code == 503
    assert client.post("/api/v1/reports/render", json=case).status_code == 200


def test_clicked_summary_streams_provider_deltas_and_reuses_completed_cache(
    monkeypatch, tmp_path,
) -> None:
    monkeypatch.setenv("PV_LLM_RUNTIME_CONFIG_PATH", str(tmp_path / "runtime.json"))
    client = TestClient(app)
    case = client.post("/api/v1/analyze/mock", json=mock_analyze_payload()).json()
    settings = VlmSettings(base_url="https://example.test", api_key="test-only", model="test-model")
    monkeypatch.setattr(VlmSettings, "from_environment", classmethod(lambda cls: settings))
    cache = interpretation.InterpretationCache(tmp_path / "stream-cache.sqlite3")
    monkeypatch.setattr(interpretation, "InterpretationCache", lambda: cache)
    calls = []

    def fake_stream(view, _settings, report_text):
        calls.append(view["decision"]["code"])
        assert "## " in report_text
        assert "## 8. 人工复核记录" in report_text
        assert len(report_text) > 1000
        scenario = interpretation.SUMMARY_SCENARIOS[view["decision"]["code"]]
        yield f"本案系统建议{scenario['label']}，本轮仍需核对原件。"
        yield f"报告列有第{len(calls)}次核对的待核证据；先核对原件，再重查受影响结论。"

    monkeypatch.setattr(interpretation, "_request_model_stream", fake_stream)
    first = client.post("/api/v1/reports/interpret/stream", json={"case": case})
    second = client.post("/api/v1/reports/interpret/stream", json={"case": case})
    assert first.status_code == second.status_code == 200
    events = [json.loads(line) for line in first.text.splitlines()]
    assert [event["type"] for event in events] == ["token", "token", "done"]
    assert "".join(event["text"] for event in events if event["type"] == "token").startswith("本案系统建议")
    assert events[-1]["cached"] is False
    assert json.loads(second.text.splitlines()[-1])["cached"] is True
    assert len(calls) == 1
    refreshed = client.post("/api/v1/reports/interpret/stream", json={
        "case": case, "force_refresh": True,
    })
    after_refresh = client.post("/api/v1/reports/interpret/stream", json={"case": case})
    assert refreshed.status_code == after_refresh.status_code == 200
    assert json.loads(refreshed.text.splitlines()[-1])["cached"] is False
    assert json.loads(after_refresh.text.splitlines()[-1])["cached"] is True
    assert "第2次核对" in refreshed.text
    assert "第2次核对" in after_refresh.text
    assert len(calls) == 2
    prompt_key = "report_summary_" + case["decision"]["decision"]
    update_runtime(prompts={prompt_key: DEFAULT_STAGE_PROMPTS[prompt_key] + "请写出具体受阻检查。"})
    after_prompt_change = client.post("/api/v1/reports/interpret/stream", json={"case": case})
    assert json.loads(after_prompt_change.text.splitlines()[-1])["cached"] is False
    assert "第3次核对" in after_prompt_change.text
    assert len(calls) == 3


def test_stream_rejects_unsupported_claim_without_caching(monkeypatch, tmp_path) -> None:
    client = TestClient(app)
    case = client.post("/api/v1/analyze/mock", json=mock_analyze_payload()).json()
    settings = VlmSettings(base_url="https://example.test", api_key="test-only", model="test-model")
    monkeypatch.setattr(VlmSettings, "from_environment", classmethod(lambda cls: settings))
    cache = interpretation.InterpretationCache(tmp_path / "rejected-cache.sqlite3")
    monkeypatch.setattr(interpretation, "InterpretationCache", lambda: cache)
    monkeypatch.setattr(interpretation, "_request_model_stream", lambda _view, _settings, _report: iter([
        "已经承保",
    ]))
    response = client.post("/api/v1/reports/interpret/stream", json={"case": case})
    events = [json.loads(line) for line in response.text.splitlines()]
    assert [event["type"] for event in events] == ["error"]
    assert "未通过内容校验" in events[-1]["message"]


def test_existing_human_review_invalidates_ai_interpretation(monkeypatch, tmp_path) -> None:
    client = TestClient(app)
    case = client.post("/api/v1/analyze/mock", json=mock_analyze_payload()).json()

    class ReviewedCases:
        database_path = tmp_path / "reviewed.sqlite3"

        def get_case(self, case_id):
            return {"review_history": [{"case_id": case_id}]}

    ReviewedCases.database_path.touch()
    monkeypatch.setattr(interpretation, "CaseRepository", ReviewedCases)
    response = client.post("/api/v1/reports/interpret", json={"case": case})
    assert response.status_code == 503
    assert "人工审核结论" in response.json()["detail"]
