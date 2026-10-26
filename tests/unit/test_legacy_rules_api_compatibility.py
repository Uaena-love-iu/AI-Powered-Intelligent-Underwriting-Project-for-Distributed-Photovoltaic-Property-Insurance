"""Regression coverage for the teammate environment policy after the rule switch."""

import pytest
from app.contracts import (
    DecisionType,
    DetectionStatus,
    EnvironmentRelation,
    Material,
    MaterialCategory,
    MaterialParseStatus,
    MaterialQualityStatus,
    MaterialReviewAction,
    ProjectInfo,
    RiskFinding,
    RiskSeverity,
    UnderwritingCase,
)
from app.decision import DecisionEngine
from app.rules import RuleEngine
from app.rules.engine import REJECT_ENVIRONMENT_CATEGORIES


@pytest.mark.parametrize("category", sorted(REJECT_ENVIRONMENT_CATEGORIES, key=lambda x: x.value))
@pytest.mark.parametrize("relation", list(EnvironmentRelation))
@pytest.mark.parametrize("manual_review,confidence", [(False, 0.95), (True, 0.4)])
def test_teammate_environment_policy_with_api_metadata(
    category, relation, manual_review, confidence,
) -> None:
    material = Material(
        material_id="panorama", category=MaterialCategory.PANORAMA,
        file_name="panorama.jpg", media_type="image/jpeg",
        quality_status=MaterialQualityStatus.USABLE,
        quality_issues=[], parse_status=MaterialParseStatus.SUCCESS,
    )
    finding = RiskFinding(
        finding_id="environment", material_id=material.material_id, category=category,
        label="拒保环境", detection_status=DetectionStatus.DETECTED,
        severity=RiskSeverity.HIGH, confidence=confidence, environment_relation=relation,
        evidence_text="可见环境证据", provider="test", model="test",
        requires_manual_review=manual_review,
    )
    engine = RuleEngine()
    reviews = engine.evaluate_materials([material], findings=[finding])
    should_reject = relation is EnvironmentRelation.PROJECT_SITE and confidence >= 0.65 and not manual_review
    assert (reviews[0].action is MaterialReviewAction.RECOMMEND_REJECT) is should_reject
    assert ("ENV-EXCLUDED-001" in reviews[0].triggered_rule_ids) is should_reject
    case = UnderwritingCase(
        schema_version="0.1.0", case_id="legacy-api-regression",
        project=ProjectInfo(
            project_name="测试项目", insured_name=None, project_type="rooftop",
            installation_type="color_steel_roof", site_address=None,
            longitude=None, latitude=None, proposed_start_date=None, component_model=None,
        ),
        materials=[material], findings=[finding], material_reviews=reviews,
        ocr_fields=[], processing_trace=[],
        package_assessment=engine.assess_package([material]),
    )
    assert case.package_assessment.missing_requirements == []
    # This fixture deliberately omits the proposed start date and independent
    # location evidence. Under the current whole-case policy it cannot be
    # accepted automatically even when the environment rule itself passes.
    expected = (DecisionType.RECOMMEND_REJECT if should_reject else
                DecisionType.REQUEST_MORE if reviews[0].action is MaterialReviewAction.REQUEST_MORE else
                DecisionType.MANUAL_REVIEW)
    assert DecisionEngine().decide(case).decision is expected


@pytest.mark.parametrize("status", [DetectionStatus.UNCERTAIN, DetectionStatus.NOT_DETECTED])
def test_unconfirmed_environment_is_not_rejected(status) -> None:
    material = Material(
        material_id="panorama", category=MaterialCategory.PANORAMA,
        file_name="panorama.jpg", media_type="image/jpeg",
        quality_status=MaterialQualityStatus.USABLE,
        quality_issues=[], parse_status=MaterialParseStatus.SUCCESS,
    )
    finding = RiskFinding(
        finding_id="environment", material_id=material.material_id,
        category=next(iter(REJECT_ENVIRONMENT_CATEGORIES)), label="待核环境",
        detection_status=status, severity=RiskSeverity.UNKNOWN, confidence=0.4,
        evidence_text="证据不足", provider="test", model="test", requires_manual_review=True,
    )
    review = RuleEngine().evaluate_material(material, findings=[finding])
    assert review.action is not MaterialReviewAction.RECOMMEND_REJECT
    assert "ENV-EXCLUDED-001" not in review.triggered_rule_ids
