import json
from datetime import datetime

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.contracts import Material, ProjectInfo, RiskFinding, UnderwritingCase, MaterialCategory, WatermarkStatus
from app.main import app
from app.pipeline import UnderwritingPipeline
from app.providers import MaterialInput, MockOcrProvider
from app.providers.vision.base import VisionProvider
from app.providers.vision.compatible import CompatibleVisionProvider
from app.reports import generate_markdown_report
from app.rules import RuleEngine
from app.rules.selection import Selection, Scope, Policy, Parameters, SaveScheme, resolve, save_scheme, get_scheme, CATALOG
from app.settings import VlmSettings


def project(**updates):
    return ProjectInfo(**(dict(project_name=None, insured_name=None, project_type="unknown",
        installation_type="unknown", site_address=None, longitude=None, latitude=None,
        proposed_start_date=None, component_model=None) | updates))


def photo():
    return MaterialInput(material=Material(material_id="p", category="panorama", file_name="p.png",
        media_type="image/png", quality_status="usable", quality_issues=[], parse_status="success"), content=b"image")


class Vision(VisionProvider):
    name = "test"
    model_name = "test"

    def analyze(self, item):
        return [RiskFinding(finding_id="p:shade", material_id="p", category="severe_shading",
            label="严重遮挡", detection_status="detected", severity="high", confidence=.99,
            evidence_text="连续多排组件被实体物遮住", requires_manual_review=False, provider="test", model="test")]

    def analyze_installation(self, *args, **kwargs):
        raise AssertionError("未选择安装类型，不能调用")

    def estimate_geometry(self, *args, **kwargs):
        raise AssertionError("未选择风雪，不能调用")


class Ocr(MockOcrProvider):
    def extract(self, item):
        raise AssertionError("本次不应调用文字模型")


def run(ids, **kwargs):
    p = project(**kwargs)
    snapshot = resolve(Selection(selected_rule_ids=ids), p)
    return UnderwritingPipeline(ocr_provider=Ocr(), vision_provider=Vision()).run(
        case_id="scope-test", project=p, materials=[photo()], rule_snapshot=snapshot)


def test_same_evidence_rejects_only_when_shading_selected():
    enabled = run(["shading"])
    disabled = run(["surface"])
    assert enabled.decision.decision.value == "recommend_reject"
    assert disabled.decision.decision.value != "recommend_reject"
    assert not disabled.findings
    statuses = {r["id"]: r["status"] for r in disabled.rule_execution}
    assert statuses["shading"] == "not_selected"
    assert statuses["surface"] == "material_missing"
    assert not any("备案" in s for s in disabled.decision.missing_requirements)
    assert disabled.location_assessment is None
    assert disabled.pv_load_screening_assessment is None


def test_dependency_scope_missing_and_not_applicable_are_distinct():
    p = project(province="上海市", installed_capacity_kw=500, insured_type="企业")
    selected = Selection(selected_rule_ids=["wind_snow", "shading"],
        scopes={"location": Scope(regions=["浙江省"]), "shading": Scope(min_capacity_kw=1000)})
    s = resolve(selected, p)
    rows = {r["id"]: r for r in s["rules"]}
    assert rows["component"]["automatic"]
    assert rows["location"]["status"] == "not_applicable"
    assert rows["wind_snow"]["status"] == "dependency_pending"
    assert rows["shading"]["status"] == "not_applicable"
    missing = resolve(selected, project())
    assert next(r for r in missing["rules"] if r["id"] == "location")["status"] == "applicability_pending"


@pytest.mark.parametrize("payload", [
    {"selected_rule_ids": []}, {"selected_rule_ids": ["unknown"]},
    {"selected_rule_ids": ["shading"], "parameters": {"environment_min_confidence": .1}},
    {"selected_rule_ids": ["shading"], "scopes": {"shading": {"min_capacity_kw": 20, "max_capacity_kw": 10}}},
])
def test_invalid_selection_rejected(payload):
    with pytest.raises(ValidationError):
        Selection.model_validate(payload)


def test_saved_revision_and_historical_case_do_not_change(tmp_path, monkeypatch):
    monkeypatch.setenv("PV_RULE_SCHEMES_DB", str(tmp_path / "rules.db"))
    first = save_scheme(SaveScheme(name="甲公司", selection=Selection(selected_rule_ids=["shading"])))
    selection = Selection.model_validate(first["selection"])
    frozen = resolve(selection, project())
    selection.selected_rule_ids = ["surface"]
    second = save_scheme(SaveScheme(name="甲公司", selection=selection))
    assert second["version"] == 2
    assert get_scheme(first["id"], 1)["selection"]["selected_rule_ids"] == ["shading"]
    assert frozen["selection"]["selected_rule_ids"] == ["shading"]
    with pytest.raises(ValueError, match="已被更新"):
        save_scheme(SaveScheme(name="甲公司", selection=selection))
    case = run(["shading"])
    restored = UnderwritingCase.model_validate_json(case.model_dump_json())
    assert restored.ruleset_snapshot == case.ruleset_snapshot
    assert "未选择" in generate_markdown_report(restored)
    assert case.ruleset_snapshot["snapshot_id"] in generate_markdown_report(restored)


def test_model_request_contains_only_selected_checklist():
    requests = []
    def respond(req):
        requests.append(json.loads(req.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"findings": [], "watermark_present": null}'}}]})
    provider = CompatibleVisionProvider(VlmSettings(base_url="https://test.invalid/v1", api_key="test", model="test"), transport=httpx.MockTransport(respond))
    provider.rule_policy = Policy(resolve(Selection(selected_rule_ids=["shading"]), project()))
    provider.analyze(photo())
    prompt = requests[0]["messages"][0]["content"]
    checklist = prompt.split("【本次唯一检查清单】")[1].split("【唯一输出规范】")[0]
    assert "severe_shading" in checklist
    assert "agriculture_environment" not in checklist
    assert "不检查水印" in checklist
    assert len(requests) == 1


def test_scope_validation_api_and_config_persistence(tmp_path, monkeypatch):
    monkeypatch.setenv("PV_RULE_SCHEMES_DB", str(tmp_path / "rules.db"))
    client = TestClient(app)
    assert len(client.get("/api/v1/rule-selection").json()["catalog"]) == len(CATALOG)
    payload = {"selection": {"selected_rule_ids": ["wind_snow"]}, "project": project().model_dump(mode="json")}
    preview = client.post("/api/v1/rule-selection/preview", json=payload)
    assert preview.status_code == 200
    assert {r["id"] for r in preview.json()["rules"] if r["status"] == "active"} == {"wind_snow","component","location","installation"}
    payload["selection"]["selected_rule_ids"] = ["invented"]
    assert client.post("/api/v1/rule-selection/preview", json=payload).status_code == 422
    assert client.get("/rule-selection.js").status_code == 200


def test_no_applicable_rules_cannot_accept():
    s = resolve(Selection(selected_rule_ids=[r["id"] for r in CATALOG],
        scopes={r["id"]: Scope(regions=["浙江省"]) for r in CATALOG}), project(province="上海市"))
    case = UnderwritingPipeline(ocr_provider=Ocr(), vision_provider=Vision()).run(
        case_id="not-applicable", project=project(province="上海市"), materials=[photo()], rule_snapshot=s)
    assert case.decision.decision.value == "manual_review"
    assert all(r["status"] == "not_applicable" for r in case.rule_execution)


def test_watermark_parameter_changes_actual_rule():
    p = project(proposed_start_date="2026-10-20")
    material = photo().material.model_copy(update=dict(captured_at=datetime(2026,10,1), watermark_status=WatermarkStatus.PRESENT, longitude=121., latitude=31.))
    engine = RuleEngine()
    for days, expected in [(15, True), (30, False)]:
        engine.rule_policy = Policy(resolve(Selection(selected_rule_ids=["watermark"], parameters=Parameters(watermark_days=days)), p))
        review = engine.evaluate_material(material, project=p)
        assert any("照片拍摄日期不在" in text for text in review.reasons) == expected


def test_scene_exclusion_preserves_snapshot_and_removes_model_task():
    from app.contracts import InstallationAnalysis, InstallationArea
    from app.rules.completeness import build_applicable_checks
    snapshot = resolve(Selection(selected_rule_ids=["edge", "workshop"]), project())
    original = json.dumps(snapshot, sort_keys=True)
    policy = Policy(snapshot)
    analysis = InstallationAnalysis(provider="test", areas=[InstallationArea(
        area_id="carport", scene_type="carport_roof", status="confirmed",
        evidence_text="可见独立车棚", supporting_structure="棚顶与梁柱", material_ids=["p"])])
    checks = build_applicable_checks(analysis, profile="full_intake", materials=[photo().material])
    filtered = policy.filter_checks(checks)
    assert not policy.enabled("edge")
    assert not policy.enabled("workshop")
    assert not any(c.key in {"edge", "workshop"} for c in filtered)
    prompt = policy.vision_prompt(MaterialCategory.PARAPET)
    assert "missing_parapet_or_guardrail" not in prompt
    assert json.dumps(snapshot, sort_keys=True) == original


def test_configuration_snapshot_discloses_unsaved_changes(tmp_path, monkeypatch):
    monkeypatch.setenv("PV_RULE_SCHEMES_DB", str(tmp_path / "rules.db"))
    s = Selection(scheme_id="capacity", scheme_version=1, selected_rule_ids=["wind_snow"],
                  scopes={r["id"]: Scope() for r in CATALOG})
    assert not resolve(s, project())["modified_from_scheme"]
    s.selected_rule_ids.append("shading")
    assert resolve(s, project())["modified_from_scheme"]


def test_configured_watermark_days_survive_decision_and_export():
    from app.reports.view import build_report_view
    selection = Selection(selected_rule_ids=["watermark"], parameters=Parameters(watermark_days=30))
    case = UnderwritingPipeline(ocr_provider=MockOcrProvider(), vision_provider=Vision()).run(
        case_id="days-30", project=project(), materials=[photo()], rule_snapshot=resolve(selection, project()))
    assert any("30日时效" in text for text in case.decision.warnings)
    assert any("30 日" in row.trigger for row in case.decision.rule_explanations if row.rule_id == "IMG-WATERMARK-DATE-001")
    assert any("30 日" in item["text"] for item in build_report_view(case)["actions"])
    assert "30 日要求" in generate_markdown_report(case)
