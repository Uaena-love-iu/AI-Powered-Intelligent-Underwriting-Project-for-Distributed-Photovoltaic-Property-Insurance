"""The configuration page and the underwriting providers use the same saved values."""

from app.config.llm_runtime import config_path, read_runtime
from app.main import app
from app.prompts.stages import DEFAULT_STAGE_PROMPTS, stage_prompt
from app.reports.interpretation import _stream_system_prompt
from app.settings import VlmSettings
from fastapi.testclient import TestClient


def test_default_runtime_config_reads_delivered_models(monkeypatch):
    monkeypatch.delenv("PV_LLM_RUNTIME_CONFIG_PATH", raising=False)
    assert config_path().name == "llm_runtime_config.json"
    assert config_path().parent.name == "data"
    assert config_path().parent.parent.name == "backend"
    assert read_runtime()["models"]["classification"] == "qwen3.8-flash"


def test_runtime_model_and_prompt_are_live_and_shared(tmp_path, monkeypatch):
    monkeypatch.setenv("PV_LLM_RUNTIME_CONFIG_PATH", str(tmp_path / "runtime.json"))
    monkeypatch.setenv("PV_VLM_BASE_URL", "https://example.test")
    monkeypatch.setenv("PV_VLM_API_KEY", "test-only-key")
    monkeypatch.setenv("PV_VLM_MODEL", "baseline-model")
    monkeypatch.setenv("PV_VLM_INSTALLATION_MODEL", "baseline-model")
    client = TestClient(app)

    page = client.get("/llm-config")
    assert page.status_code == 200
    assert "模型与提示词配置" in page.text
    assert client.get("/llm-config.js").status_code == 200
    before = client.get("/api/v1/config/llm").json()
    assert before["models"]["installation"] == "baseline-model"
    assert before["prompts"]["installation"] == DEFAULT_STAGE_PROMPTS["installation"]
    assert "test-only-key" not in str(before)

    changed = DEFAULT_STAGE_PROMPTS["installation"] + "\n只返回可见现场证据。"
    response = client.patch("/api/v1/config/llm", json={
        "models": {"installation": "qwen3.8-flash"},
        "prompts": {"installation": changed},
    })
    assert response.status_code == 200, response.text
    assert response.json()["models"]["installation"] == "qwen3.8-flash"
    assert VlmSettings.from_environment().for_stage("installation").model == "qwen3.8-flash"
    assert stage_prompt("installation", FILING_CONTEXT="[]").endswith("只返回可见现场证据。")
    assert "{{FILING_CONTEXT}}" not in stage_prompt("installation", FILING_CONTEXT="[]")

    reset = client.patch("/api/v1/config/llm", json={
        "models": {"installation": None}, "prompts": {"installation": None},
    })
    assert reset.status_code == 200
    assert reset.json()["models"]["installation"] == "baseline-model"
    assert reset.json()["prompts"]["installation"] == DEFAULT_STAGE_PROMPTS["installation"]


def test_dynamic_placeholders_and_unknown_stages_are_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("PV_LLM_RUNTIME_CONFIG_PATH", str(tmp_path / "runtime.json"))
    client = TestClient(app)
    missing = client.patch("/api/v1/config/llm", json={
        "prompts": {"classification": "请识别图片并输出 JSON，依据图片内容判断材料类别，并返回清晰的解释。"},
    })
    assert missing.status_code == 422
    unknown = client.patch("/api/v1/config/llm", json={"models": {"not_a_stage": "model-a"}})
    assert unknown.status_code == 422
    assert not (tmp_path / "runtime.json").exists()


def test_report_summary_prompts_and_model_are_editable(tmp_path, monkeypatch):
    monkeypatch.setenv("PV_LLM_RUNTIME_CONFIG_PATH", str(tmp_path / "runtime.json"))
    monkeypatch.setenv("PV_VLM_BASE_URL", "https://example.test")
    monkeypatch.setenv("PV_VLM_API_KEY", "test-only-key")
    monkeypatch.setenv("PV_VLM_MODEL", "baseline-model")
    client = TestClient(app)
    before = client.get("/api/v1/config/llm").json()
    assert before["models"]["summary"] == "baseline-model"
    assert before["prompts"]["report_summary"] == DEFAULT_STAGE_PROMPTS["report_summary"]
    assert before["prompts"]["report_summary_request_more"] == DEFAULT_STAGE_PROMPTS["report_summary_request_more"]
    assert "report_summary" in client.get("/llm-config.js").text

    custom_guidance = DEFAULT_STAGE_PROMPTS["report_summary_request_more"] + "请点明受阻检查。"
    changed = client.patch("/api/v1/config/llm", json={
        "models": {"summary": "summary-model"},
        "prompts": {"report_summary_request_more": custom_guidance},
    })
    assert changed.status_code == 200, changed.text
    assert VlmSettings.from_environment().for_stage("summary").model == "summary-model"
    assert "请点明受阻检查。" in _stream_system_prompt({"decision": {"code": "request_more"}})

    invalid = client.patch("/api/v1/config/llm", json={
        "prompts": {"report_summary": "只写三行，不使用任何动态字段。"},
    })
    assert invalid.status_code == 422
    reset = client.patch("/api/v1/config/llm", json={
        "models": {"summary": None},
        "prompts": {"report_summary_request_more": None},
    })
    assert reset.status_code == 200
    assert reset.json()["prompts"]["report_summary_request_more"] == DEFAULT_STAGE_PROMPTS["report_summary_request_more"]
