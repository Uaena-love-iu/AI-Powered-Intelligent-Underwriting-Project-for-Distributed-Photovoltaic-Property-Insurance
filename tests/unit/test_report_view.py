import json
from pathlib import Path

from app.contracts import (
    CheckResult,
    DecisionType,
    MaterialReviewAction,
    ProcessingStatus,
    ProcessingStep,
    UnderwritingCase,
)
from app.main import app
from app.decision import DecisionEngine
from app.reports.view import build_report_view
from fastapi.testclient import TestClient

from tests.integration.test_report_render_api import mock_analyze_payload


def _case() -> UnderwritingCase:
    response = TestClient(app).post("/api/v1/analyze/mock", json=mock_analyze_payload())
    assert response.status_code == 200
    return UnderwritingCase.model_validate(response.json())


def test_saved_cross_view_shading_is_one_basis_with_both_photo_references() -> None:
    saved = (Path(__file__).resolve().parents[2] / "测试素材"
             / "车棚树枝遮挡_稳定复核集_2026-10-16" / "运行结果"
             / "SHADE-REVIEW-B0FD7E0F20B9.json")
    case = UnderwritingCase.model_validate(json.loads(saved.read_text()))
    view = build_report_view(case)
    assert view["decision"]["code"] == "manual_review"
    assert len(view["basis"]) == 1
    basis = view["basis"][0]
    assert "遮挡范围及实际影响需人工核实" in basis["text"]
    assert "IMG-SHADING-MULTIVIEW-REVIEW-001" in basis["reference_ids"][-1]
    panorama_ids = [item.material_id for item in case.materials
                    if item.category.value == "panorama"]
    assert all(f"material:{item}" in basis["reference_ids"] for item in panorama_ids)
    assert all("拟起保日期" not in item["text"] for item in view["basis"])
    assert view["actions"][0]["text"].startswith("核对正面与俯拍遮挡标框")
    assert view["actions"][1]["text"].startswith("确认拟起保日期")
    refreshed_decision = DecisionEngine().decide(case)
    assert refreshed_decision.decision.value == "manual_review"
    assert "IMG-SHADING-MULTIVIEW-REVIEW-001" in refreshed_decision.decisive_rule_ids
    assert "IMG-WATERMARK-DATE-001" not in refreshed_decision.decisive_rule_ids
    assert any("拟起保日期" in item for item in refreshed_decision.warnings)
    assert all("拟起保日期" not in item for item in refreshed_decision.reasons)


def test_view_separates_check_state_from_material_validity_and_owners() -> None:
    case = _case()
    view = build_report_view(case)
    assert view["decision"]["human_confirmation_required"] is True
    assert all(item["state"] in {
        "evidence_ready", "risk", "undetermined", "not_executed", "not_applicable",
    } for item in view["checks"])
    assert sum(view["coverage_counts"].values()) == len(view["checks"])
    assert all(item["owner"] in {
        "customer", "underwriter", "system", "business",
    } for item in view["actions"])
    assert all(ref in view["references"] for item in view["basis"]
               for ref in item["reference_ids"] if not ref.startswith("rule:"))


def test_comparison_field_notice_does_not_count_as_discovered_risk() -> None:
    case = _case()
    check = case.applicable_checks[0]
    material_id = case.materials[0].material_id
    review = case.material_reviews[0].model_copy(update={
        "material_id": material_id,
        "action": MaterialReviewAction.WARNING,
        "triggered_rule_ids": ["DOC-COMPARISON-FIELD-UNAVAILABLE-001"],
        "finding_ids": [],
        "reasons": ["交叉核对范围提示：未取得序列号的可靠识别值"],
        "requires_manual_review": False,
    })
    result = CheckResult(area_id=check.area_id, key=check.key, status="valid",
                         material_ids=[material_id], reason="已有可核材料")
    changed = case.model_copy(update={
        "material_reviews": [review], "applicable_checks": [check],
        "check_results": [result], "findings": [],
    })
    assert build_report_view(changed)["checks"][0]["state"] == "evidence_ready"


def test_failed_ocr_is_inconclusive_and_uses_business_field_name() -> None:
    case = _case()
    check = case.applicable_checks[0]
    material_id = case.materials[0].material_id
    review = case.material_reviews[0].model_copy(update={
        "material_id": material_id,
        "action": MaterialReviewAction.WARNING,
        "triggered_rule_ids": ["SYS-OCR-FIELDS-PENDING-001"],
        "finding_ids": [],
        "ocr_field_ids": [],
        "reasons": ["以下字段未返回可靠识别结果，需内部重试或人工核验原件：insured_name"],
    })
    result = CheckResult(area_id=check.area_id, key=check.key, status="pending",
                         material_ids=[material_id],
                         reason="自动识别失败或未覆盖检查事项，需内部重试或人工核验：insured_name")
    changed = case.model_copy(update={
        "material_reviews": [review], "applicable_checks": [check],
        "check_results": [result], "findings": [], "stage_reviews": [],
        "decision": case.decision.model_copy(update={
            "decision": DecisionType.MANUAL_REVIEW,
            "reasons": [review.reasons[0],
                        "以下字段未返回可靠识别结果，需内部重试或人工核验原件：serial_number",
                        "部分检查项仍待内部复核"],
        }),
    })
    view = build_report_view(changed)
    assert view["checks"][0]["state"] == "undetermined"
    assert "被保险人名称" in view["checks"][0]["reason"]
    assert len(view["basis"]) == 1
    assert "被保险人名称、设备序列号" in view["basis"][0]["text"]
    assert any(item["owner"] == "system"
               and "文字识别待重试：被保险人名称、设备序列号" in item["text"]
               for item in view["actions"])


def test_reject_and_surcharge_put_the_responsible_reviewer_first() -> None:
    case = _case()
    for decision, owner in (
        (DecisionType.RECOMMEND_REJECT, "underwriter"),
        (DecisionType.SURCHARGE, "business"),
        (DecisionType.ACCEPT, "underwriter"),
    ):
        changed = case.model_copy(update={
            "decision": case.decision.model_copy(update={"decision": decision}),
        })
        view = build_report_view(changed)
        assert view["actions"][0]["owner"] == owner
        assert view["decision"]["code"] == decision.value


def test_price_label_is_added_only_to_acceptance_outcomes() -> None:
    case = _case()
    for code, label in (
        (DecisionType.ACCEPT, "建议承保（加费）"),
        (DecisionType.CONDITIONAL_ACCEPT, "建议附条件承保（加费）"),
        (DecisionType.REQUEST_MORE, "需补充材料"),
    ):
        changed = case.model_copy(update={
            "decision": case.decision.model_copy(update={
                "decision": code, "pricing_recommendation": "surcharge",
            }),
        })
        view = build_report_view(changed)
        assert view["decision"]["label"] == label
        assert any(action["owner"] == "business" and "加费" in action["text"]
                   for action in view["actions"]) is (code != DecisionType.REQUEST_MORE)


def test_provider_failure_is_not_a_customer_request() -> None:
    case = _case()
    trace = case.processing_trace[0].model_copy(update={"status": ProcessingStatus.FAILED})
    changed = case.model_copy(update={
        "processing_trace": [trace, *case.processing_trace[1:]],
    })
    actions = build_report_view(changed)["actions"]
    assert any(item["owner"] == "system" and "重新运行" in item["text"]
               for item in actions)
    assert not any(item["owner"] == "customer" and "重新运行" in item["text"]
                   for item in actions)


def test_manual_basis_names_actual_screening_and_failed_step_before_date_warning() -> None:
    case = _case()
    failed = case.processing_trace[0].model_copy(update={
        "step": ProcessingStep.GEOMETRY_GROUP, "status": ProcessingStatus.FAILED,
    })
    changed = case.model_copy(update={
        "location_assessment": None, "stage_reviews": [],
        "processing_trace": [failed],
        "pv_load_screening_assessment": {"status": "PARTIAL", "label": "风雪初筛等级：待评估"},
        "decision": case.decision.model_copy(update={
            "decision": DecisionType.MANUAL_REVIEW,
            "decisive_rule_ids": ["IMG-WATERMARK-DATE-001", "CAT-FRONT-LOAD-SCREENING-REVIEW", "SYS-PROVIDER-FAILURE"],
            "reasons": ["拟起保日期未提供，15日时效暂无法核验", "风雪结果待评估", "自动识别服务未完整执行"],
        }),
    })
    basis = build_report_view(changed)["basis"]
    assert "风雪初筛等级：待评估" in basis[0]["text"]
    assert any("组件倾角、离地高度和地面粗糙度估计" in item["text"] for item in basis)
    assert all("拟起保日期未提供" not in item["text"] for item in basis)


def test_missing_material_is_not_reported_as_an_inconclusive_examination() -> None:
    data = _case().model_dump(mode="json")
    assert data["applicable_checks"]
    check = data["applicable_checks"][0]
    key = (check["area_id"], check["key"])
    result = next(item for item in data["check_results"]
                  if (item["area_id"], item["key"]) == key)
    result["status"] = "missing"
    view = build_report_view(UnderwritingCase.model_validate(data))
    displayed = next(item for item in view["checks"]
                     if item["id"] == f"check:{key[0]}:{key[1]}")
    assert displayed["state"] == "not_executed"


def test_customer_actions_do_not_repeat_filing_or_show_stage_codes() -> None:
    actions = build_report_view(_case())["actions"]
    filing = [item for item in actions
              if item["owner"] == "customer" and "备案" in item["text"]]
    assert len(filing) == 1
    assert all("single_image" not in item["blocked_check"] for item in actions)
