from datetime import UTC, datetime

from app.contracts import (
    ApplicableCheck,
    CatastropheAssessment,
    CheckResult,
    DecisionType,
    Material,
    MaterialParseStatus,
    MaterialQualityStatus,
    MaterialReview,
    MaterialReviewAction,
    ProcessingStatus,
    ProcessingStep,
    ProcessingTrace,
    ProjectInfo,
    StageReview,
    UnderwritingCase,
)
from app.decision import DecisionEngine
from app.location import LocationAssessment


def project_info(
    *,
    longitude: float | None = 113.25,
    latitude: float | None = 23.12,
    component_model: str | None = "PV-MODULE-580W",
) -> ProjectInfo:
    return ProjectInfo(
        project_name="示例光伏项目",
        insured_name="示例企业",
        project_type="rooftop",
        installation_type="color_steel_roof",
        site_address="广东省示例市",
        longitude=longitude,
        latitude=latitude,
        proposed_start_date="2026-10-01",
        component_model=component_model,
    )


def material() -> Material:
    return Material(
        material_id="material-001",
        category="panorama",
        file_name="example.jpg",
        media_type="image/jpeg",
        quality_status=MaterialQualityStatus.POOR,
        quality_confidence=0.4,
        quality_issues=["blur"],
        parse_status=MaterialParseStatus.SUCCESS,
    )


def processing_trace(status: ProcessingStatus) -> ProcessingTrace:
    timestamp = datetime.now(UTC)
    return ProcessingTrace(
        step=ProcessingStep.OCR,
        provider="test-provider",
        model="test-model",
        started_at=timestamp,
        finished_at=timestamp,
        latency_ms=0,
        status=status,
        error_code=None,
        error_message=None,
    )


def underwriting_case(
    *,
    project: ProjectInfo | None = None,
    materials: list[Material] | None = None,
    reviews: list[MaterialReview] | None = None,
    traces: list[ProcessingTrace] | None = None,
) -> UnderwritingCase:
    return UnderwritingCase(
        schema_version="0.1.0",
        case_id="case-001",
        project=project or project_info(),
        materials=materials or [],
        ocr_fields=[],
        findings=[],
        component_profile=None,
        weather_profile=None,
        catastrophe_assessment=CatastropheAssessment(
            resistance_level="high", expected_loss_risk="low", factors=[],
            explanation="合成测试：所有灾害检查已完成", triggered_rule_ids=[],
            requires_manual_review=False,
        ),
        location_assessment=LocationAssessment(status="verified"),
        material_reviews=reviews or [],
        decision=None,
        processing_trace=traces or [],
    )


def test_no_materials_requests_materials() -> None:
    decision = DecisionEngine().decide(underwriting_case())

    assert decision.decision is DecisionType.REQUEST_MORE
    assert decision.missing_requirements == ["补充可审核的投保材料"]


def test_complete_low_wind_snow_screening_is_informational_only() -> None:
    low = {"status": "NOT_EXCEEDED", "risk_level": "low", "data_complete": True,
           "label": "风雪初筛等级：低", "requires_manual_review": False, "actions": []}
    case = underwriting_case(materials=[material()]).model_copy(update={
        "pv_load_screening_assessment": low,
    })
    decision = DecisionEngine().decide(case)
    assert decision.decision is DecisionType.ACCEPT
    assert "CAT-FRONT-LOAD-SCREENING-REVIEW" not in decision.decisive_rule_ids
    assert any("风雪初筛等级：低" in item for item in decision.warnings)

    without_start_date = DecisionEngine().decide(case.model_copy(update={
        "project": case.project.model_copy(update={"proposed_start_date": None}),
    }))
    assert without_start_date.decision is DecisionType.ACCEPT
    assert "IMG-WATERMARK-DATE-001" not in without_start_date.decisive_rule_ids

    partial = {**low, "status": "PARTIAL", "risk_level": "unknown",
               "label": "风雪初筛等级：待评估", "requires_manual_review": True}
    reviewed = DecisionEngine().decide(case.model_copy(update={
        "pv_load_screening_assessment": partial,
    }))
    assert reviewed.decision is DecisionType.MANUAL_REVIEW
    assert "CAT-FRONT-LOAD-SCREENING-REVIEW" in reviewed.decisive_rule_ids


def test_required_missing_material_takes_priority_over_manual_review() -> None:
    case = underwriting_case(materials=[material()]).model_copy(update={
        "pre_catastrophe_outcome": "manual_review",
        "stage_reviews": [StageReview(
            stage="completeness", action="manual_review",
            rule_ids=["CHECK-EVIDENCE-MISSING-001"],
            material_ids=["material-001"],
            reasons=["安装区域需人工核验"],
            customer_requests=["补拍车棚梁柱与基础连接"],
        )],
    })
    decision = DecisionEngine().decide(case)
    assert decision.decision is DecisionType.REQUEST_MORE
    assert "补拍车棚梁柱与基础连接" in decision.missing_requirements


def test_missing_start_date_always_stays_empty_and_warns_without_material_request() -> None:
    project = project_info().model_copy(update={"proposed_start_date": None})
    case = underwriting_case(project=project, materials=[material()])
    decision = DecisionEngine().decide(case)
    assert case.project.proposed_start_date is None
    assert any("拟起保日期未提供" in warning for warning in decision.warnings)
    assert not any("拟起保日期" in request for request in decision.missing_requirements)
    assert decision.decision is DecisionType.ACCEPT


def test_missing_coordinates_warns_without_blocking() -> None:
    case = underwriting_case(
        project=project_info(longitude=None, latitude=None), materials=[material()]
    )

    decision = DecisionEngine().decide(case)

    assert decision.decision is DecisionType.ACCEPT
    assert decision.missing_requirements == []
    assert any("经纬度缺失" in warning for warning in decision.warnings)
    assert "REQ-PROJECT-COORDINATES" not in decision.decisive_rule_ids
    explanation = next(
        item
        for item in decision.rule_explanations
        if item.rule_id == "REQ-PROJECT-COORDINATES"
    )
    assert explanation.title == "项目坐标缺失"
    assert "历史气象数据" in explanation.trigger
    assert "补充准确经纬度" in explanation.effect


def test_missing_component_model_warns_without_blocking() -> None:
    case = underwriting_case(
        project=project_info(component_model=None), materials=[material()]
    )

    decision = DecisionEngine().decide(case)

    assert decision.decision is DecisionType.ACCEPT
    assert decision.missing_requirements == []
    assert any("组件型号缺失" in warning for warning in decision.warnings)
    assert "REQ-COMPONENT-MODEL" not in decision.decisive_rule_ids


def test_optional_material_absence_is_a_nonblocking_risk_notice() -> None:
    case = underwriting_case(materials=[material()]).model_copy(update={
        "applicable_checks": [ApplicableCheck(
            area_id="project", key="grid", label="并网或调度材料",
            category="grid_connection_document", applicability="applicable",
            required_now=False, evidence_requirement="核对并网信息", reason="可选材料",
        )],
        "check_results": [CheckResult(
            area_id="project", key="grid", status="not_provided",
            reason="可选材料未提供",
        )],
    })

    decision = DecisionEngine().decide(case)

    assert decision.decision is DecisionType.ACCEPT
    assert not decision.missing_requirements
    assert any("并网或调度材料未提供" in warning for warning in decision.warnings)


def test_decision_engine_uses_material_review() -> None:
    review = MaterialReview(
        material_id="material-001",
        action=MaterialReviewAction.REQUEST_MORE,
        triggered_rule_ids=["MAT-QUALITY-001"],
        finding_ids=[],
        ocr_field_ids=[],
        reasons=["材料质量较差"],
        missing_requirements=["补充清晰材料"],
        requires_manual_review=False,
    )
    case = underwriting_case(
        materials=[material()],
        reviews=[review],
    )

    decision = DecisionEngine().decide(case)

    assert decision.decision is DecisionType.REQUEST_MORE
    assert "MAT-QUALITY-001" in decision.decisive_rule_ids
    assert "补充清晰材料" in decision.missing_requirements


def test_non_blocking_warning_does_not_require_review_after_analysis() -> None:
    clean_material = material().model_copy(
        update={
            "quality_status": MaterialQualityStatus.USABLE,
            "quality_confidence": 0.98,
            "quality_issues": [],
        }
    )
    review = MaterialReview(
        material_id=clean_material.material_id,
        action=MaterialReviewAction.WARNING,
        triggered_rule_ids=["IMG-WATERMARK-001"],
        finding_ids=[],
        ocr_field_ids=[],
        reasons=["当前阶段水印缺失仅作补充建议"],
        missing_requirements=[],
        requires_manual_review=False,
    )
    case = underwriting_case(materials=[clean_material], reviews=[review]).model_copy(update={
        "location_assessment": LocationAssessment(status="verified"),
    })

    decision = DecisionEngine().decide(case)

    assert decision.decision is DecisionType.ACCEPT
    assert "IMG-WATERMARK-001" in decision.decisive_rule_ids
    assert decision.missing_requirements == []


def test_unresolved_location_requires_review_but_500m_screening_does_not() -> None:
    case = underwriting_case(materials=[material()])
    for status in ("single_source", "uncertain", "missing", "conflict"):
        decision = DecisionEngine().decide(case.model_copy(update={
            "location_assessment": LocationAssessment(status=status),
        }))
        assert decision.decision is DecisionType.MANUAL_REVIEW
    without_location = DecisionEngine().decide(case.model_copy(update={
        "location_assessment": None,
    }))
    assert without_location.decision is DecisionType.MANUAL_REVIEW
    screened = DecisionEngine().decide(case.model_copy(update={
        "location_assessment": LocationAssessment(
            status="distance_screened",
            notes=["原始距离估算在 500 米内，位置项不触发人工复核"],
            weather_coordinates_verified=False,
        ),
    }))
    assert screened.decision is DecisionType.ACCEPT
    assert "LOC-UNCERTAIN-001" not in screened.decisive_rule_ids
    assert screened.warnings


def test_address_photo_conflict_requires_manual_review_even_with_other_gaps() -> None:
    case = underwriting_case(materials=[material()]).model_copy(update={
        "location_assessment": LocationAssessment(
            status="conflict", address_photo_conflict=True,
            notes=["备案地址与现场坐标明显不对应"],
        ),
        "check_results": [CheckResult(
            area_id="project", key="support", status="missing",
            reason="连接照片缺失", customer_request="补充连接照片",
        )],
    })
    decision = DecisionEngine().decide(case)
    assert decision.decision is DecisionType.MANUAL_REVIEW
    assert "LOC-CONFLICT-001" in decision.decisive_rule_ids


def test_optional_pending_check_is_a_scope_notice_without_customer_request() -> None:
    case = underwriting_case(materials=[material()]).model_copy(update={
        "location_assessment": LocationAssessment(status="verified"),
        "check_results": [CheckResult(
            area_id="project", key="combiner", status="pending",
            reason="尚未核实是否配置汇流箱",
        )],
    })
    decision = DecisionEngine().decide(case)
    assert decision.decision is DecisionType.ACCEPT
    assert decision.missing_requirements == []


def test_pricing_suggestion_combines_with_acceptance_outcome() -> None:
    clean_material = material().model_copy(update={
        "quality_status": MaterialQualityStatus.USABLE,
        "quality_confidence": 0.98,
        "quality_issues": [],
    })

    def review(action: MaterialReviewAction) -> MaterialReview:
        return MaterialReview(
            material_id=clean_material.material_id,
            action=action,
            triggered_rule_ids=[], finding_ids=[], ocr_field_ids=[],
            reasons=["已识别的风险需要调整承保安排"],
            missing_requirements=[], requires_manual_review=False,
        )

    base = underwriting_case(materials=[clean_material],
                             reviews=[review(MaterialReviewAction.SURCHARGE)])
    priced_accept = DecisionEngine().decide(base)
    assert priced_accept.decision is DecisionType.ACCEPT
    assert priced_accept.pricing_recommendation == "surcharge"

    priced_conditional = DecisionEngine().decide(base.model_copy(update={
        "material_reviews": [review(MaterialReviewAction.SURCHARGE),
                             review(MaterialReviewAction.CONDITIONAL)],
    }))
    assert priced_conditional.decision is DecisionType.CONDITIONAL_ACCEPT
    assert priced_conditional.pricing_recommendation == "surcharge"

    blocked = DecisionEngine().decide(base.model_copy(update={
        "material_reviews": [review(MaterialReviewAction.SURCHARGE),
                             review(MaterialReviewAction.REQUEST_MORE)],
    }))
    assert blocked.decision is DecisionType.REQUEST_MORE
    assert blocked.pricing_recommendation == "not_proposed"


def test_decision_engine_sends_provider_failure_to_manual_review() -> None:
    case = underwriting_case(
        materials=[material()],
        traces=[processing_trace(ProcessingStatus.FAILED)]
    )

    decision = DecisionEngine().decide(case)

    assert decision.decision is DecisionType.MANUAL_REVIEW
    assert "SYS-PROVIDER-FAILURE" in decision.decisive_rule_ids
    assert decision.warnings


def test_request_more_takes_priority_over_provider_failure() -> None:
    case = underwriting_case(
        materials=[material()],
        reviews=[MaterialReview(
            material_id="material-001", action=MaterialReviewAction.REQUEST_MORE,
            triggered_rule_ids=["MAT-QUALITY-001"], finding_ids=[], ocr_field_ids=[],
            reasons=["必需照片不可读"], missing_requirements=["重拍清晰现场照片"],
            requires_manual_review=False,
        )],
        traces=[processing_trace(ProcessingStatus.FAILED)],
    )

    decision = DecisionEngine().decide(case)

    assert decision.decision is DecisionType.REQUEST_MORE
    assert "重拍清晰现场照片" in decision.missing_requirements
    assert decision.warnings
