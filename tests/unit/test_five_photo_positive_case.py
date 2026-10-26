"""Regression evidence only: synthetic facts are not a real VLM accuracy test."""
from datetime import UTC, datetime

from app.catastrophe import CatastropheAssessmentEngine
from app.contracts import (
    ComponentProfile,
    EnvironmentRelation,
    Material,
    OcrField,
    ProjectInfo,
    RiskFinding,
    UnderwritingCase,
    WeatherProfile,
)
from app.decision import DecisionEngine
from app.location import assess_location
from app.rules import RuleEngine
from app.rules.config import BusinessRulesConfig


def positive_case(*, profile: str = "demo_minimum") -> UnderwritingCase:
    rules = BusinessRulesConfig.load().model_copy(
        update={"package_requirement_profile": profile}
    )
    engine = RuleEngine(rules)
    project = ProjectInfo(
        project_name="SIM-合规车棚测试项目", insured_name="模拟测试企业",
        insured_address="佛山演示工业园1号（虚构地址）",
        project_entity="模拟测试企业", project_type="carport",
        installation_type="carport_roof",
        site_address="佛山演示工业园1号（虚构地址）",
        longitude=113.1189, latitude=23.0345,
        proposed_start_date="2026-10-06", component_model="JAM72D42-630/LB",
    )
    categories = [
        ("front", "panorama", "front_level"),
        ("top", "panorama", "overhead"),
        ("roof", "roof_connection", "unknown"),
        ("ground", "electrical_grounding", "unknown"),
        ("module", "component_nameplate", "unknown"),
        ("filing", "filing_certificate", "unknown"),
    ]
    materials = [Material(
        material_id=ident, category=category, capture_view=view,
        file_name=f"{ident}_SIMULATED.png", media_type="image/png",
        quality_status="usable", quality_issues=[], parse_status="success",
        watermark_status="present" if ident != "filing" else "not_checked",
        captured_at="2026-10-05T10:00:00+08:00" if ident != "filing" else None,
        longitude=113.1189 if ident != "filing" else None,
        latitude=23.0345 if ident != "filing" else None,
    ) for ident, category, view in categories]
    findings = [RiskFinding(
        finding_id=f"{material.material_id}:{category.value}",
        material_id=material.material_id, category=category,
        label=category.value, detection_status="not_detected", severity="info",
        confidence=0.95, bbox=None,
        evidence_text="模拟回归事实：检查区域清晰，未观察到相应风险。",
        provider="SIMULATED-TEST", model="deterministic-fixture",
        requires_manual_review=False,
    ) for material in materials
        for category in (engine._expected_checks_for_material(material.category) or ())]
    values = {
        "filing": {
            "project_name": project.project_name,
            "site_address": project.site_address,
            "insured_name": project.insured_name,
            "project_entity": project.project_entity,
        },
        "module": {
            "component_model": project.component_model,
            "rated_power_w": "630", "serial_number": "SIM202610050001",
        },
    }
    ocr_fields = [OcrField(
        field_id=f"{ident}:{name}", material_id=ident, field_name=name,
        raw_value=value, normalized_value=value, value_status="extracted",
        confidence=0.99, provider="SIMULATED-TEST", model="deterministic-fixture",
    ) for ident, fields in values.items() for name, value in fields.items()]
    component = ComponentProfile(
        component_model=project.component_model, manufacturer="模拟测试",
        rated_power_w=630, wind_load_pa=2400, hail_resistance_mm=25,
        snow_load_pa=5400, source_url="https://example.invalid/simulated-component",
        source_name="SIMULATED-TEST：参数仅供规则回归",
        retrieved_at=datetime(2026, 10, 5, tzinfo=UTC), match_confidence=1.0,
    )
    weather = WeatherProfile(
        longitude=project.longitude, latitude=project.latitude,
        historical_max_wind_m_s=20, historical_max_hail_mm=10,
        historical_max_snow_load_pa=1000,
        observation_start="2023-01-01", observation_end="2025-12-31",
        source_url="https://example.invalid/simulated-weather",
        source_name="SIMULATED-TEST：气象值仅供规则回归",
        retrieved_at=datetime(2026, 10, 5, tzinfo=UTC),
    )
    location, _ = assess_location(project, materials, ocr_fields)
    return UnderwritingCase(
        schema_version="0.1.0", case_id="SIM-FIVE-PHOTO-POSITIVE-20261005",
        project=project, materials=materials, ocr_fields=ocr_fields, findings=findings,
        component_profile=component, weather_profile=weather,
        catastrophe_assessment=CatastropheAssessmentEngine(rules).assess(
            component=component, weather=weather,
        ),
        location_assessment=location,
        package_assessment=engine.assess_package(materials, project_type=project.project_type),
        material_reviews=engine.evaluate_materials(
            materials, findings=findings, ocr_fields=ocr_fields, project=project,
        ),
        processing_trace=[],
    )


def test_five_scene_photos_are_counted_without_blocking_on_engineering_gaps() -> None:
    case = positive_case()
    assert case.package_assessment.image_count == 5
    assert case.package_assessment.document_image_count == 1
    assert case.package_assessment.minimum_gate_passed is None
    assert case.package_assessment.coverage_requirements == []
    assert case.package_assessment.missing_requirements == []
    assert DecisionEngine().decide(case).decision.value == "manual_review"


def test_full_intake_package_stats_do_not_create_fixed_category_requests() -> None:
    case = positive_case(profile="full_intake")
    assert case.package_assessment.minimum_gate_passed is None
    assert case.package_assessment.coverage_requirements == []
    assert case.package_assessment.missing_requirements == []
    assert DecisionEngine().decide(case).decision.value == "manual_review"


def test_certificate_photo_is_counted_separately_from_scene_photos() -> None:
    case = positive_case()
    materials = [item for item in case.materials if item.material_id != "ground"]
    assessment = RuleEngine().assess_package(materials, project_type=case.project.project_type)
    assert assessment.image_count == 4
    assert assessment.minimum_gate_passed is None
    assert assessment.missing_requirements == []


def test_real_rejection_and_uncertainty_remain_actionable_without_a_count_gate() -> None:
    for status, expected in [("detected", "recommend_reject"), ("uncertain", "manual_review")]:
        case = positive_case()
        findings = [item.model_copy(update={
            "detection_status": type(item.detection_status)(status),
            "requires_manual_review": status == "uncertain",
            "environment_relation": EnvironmentRelation.PROJECT_SITE,
        }) if item.material_id == "front" and item.category.value == "water_adjacent_environment"
            else item for item in case.findings]
        case.material_reviews = RuleEngine().evaluate_materials(
            case.materials, findings=findings, ocr_fields=case.ocr_fields, project=case.project,
        )
        assert DecisionEngine().decide(case).decision.value == expected


def test_missing_weather_hail_and_snow_limit_screening_without_blocking() -> None:
    case = positive_case()
    weather = case.weather_profile.model_copy(update={
        "historical_max_hail_mm": None, "historical_max_snow_load_pa": None,
    })
    case.catastrophe_assessment = CatastropheAssessmentEngine().assess(
        component=case.component_profile, weather=weather,
    )
    decision = DecisionEngine().decide(case)
    assert decision.decision.value == "manual_review"
    assert case.catastrophe_assessment.module == "component-workbench-v1"
    assert case.catastrophe_assessment.comparisons == []
