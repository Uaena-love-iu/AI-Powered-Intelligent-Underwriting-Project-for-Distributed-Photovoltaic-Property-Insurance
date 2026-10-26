from app.contracts import (
    CaptureView,
    DecisionType,
    DetectionStatus,
    EnvironmentRelation,
    Material,
    MaterialCategory,
    MaterialParseStatus,
    MaterialQualityStatus,
    MaterialReview,
    MaterialReviewAction,
    OcrField,
    OcrValueStatus,
    PackageAssessment,
    ProjectInfo,
    ProjectType,
    RiskCategory,
    RiskFinding,
    RiskSeverity,
    UnderwritingCase,
    WatermarkStatus,
)
from app.decision import DecisionEngine
from app.rules import RuleEngine
from app.rules.engine import EXPECTED_CHECKS_BY_MATERIAL


def make_material(
    material_id: str,
    category: MaterialCategory,
    *,
    view: CaptureView = CaptureView.UNKNOWN,
) -> Material:
    return Material(
        material_id=material_id,
        category=category,
        capture_view=view,
        file_name=f"{material_id}.jpg",
        media_type="image/jpeg",
        quality_status=MaterialQualityStatus.USABLE,
        quality_issues=[],
        parse_status=MaterialParseStatus.SUCCESS,
    )


def make_project(**updates: object) -> ProjectInfo:
    payload: dict[str, object] = {
        "project_name": "样例屋顶项目",
        "insured_name": "样例企业",
        "project_type": "rooftop",
        "installation_type": "color_steel_roof",
        "site_address": "广东省广州市天河区",
        "longitude": 113.3,
        "latitude": 23.1,
        "proposed_start_date": "2026-10-01",
        "component_model": "PV-580W-X",
    }
    payload.update(updates)
    return ProjectInfo(**payload)


def make_finding(
    material_id: str,
    category: RiskCategory,
    status: DetectionStatus,
    *,
    environment_relation: EnvironmentRelation | None = None,
    confidence: float = 0.95,
    requires_manual_review: bool = False,
) -> RiskFinding:
    if environment_relation is None:
        environment_relation = (
            EnvironmentRelation.PROJECT_SITE
            if category in EXPECTED_CHECKS_BY_MATERIAL[MaterialCategory.PANORAMA]
            and category.value.endswith("_environment")
            and status is DetectionStatus.DETECTED
            else EnvironmentRelation.UNCERTAIN
        )
    return RiskFinding(
        finding_id=f"{material_id}:{category.value}",
        material_id=material_id,
        category=category,
        label=category.value,
        detection_status=status,
        severity=RiskSeverity.HIGH,
        confidence=confidence,
        environment_relation=environment_relation,
        bbox=None,
        evidence_text="测试证据",
        provider="test-vision",
        model="test-model",
        requires_manual_review=requires_manual_review,
    )


def test_package_counts_do_not_invent_required_categories() -> None:
    materials = [
        make_material("front", MaterialCategory.PANORAMA, view=CaptureView.FRONT_LEVEL),
        make_material("top", MaterialCategory.PANORAMA, view=CaptureView.OVERHEAD),
        make_material("roof", MaterialCategory.ROOF_CONNECTION),
        make_material("workshop", MaterialCategory.WORKSHOP),
        Material(
            material_id="certificate",
            category=MaterialCategory.FILING_CERTIFICATE,
            file_name="certificate.pdf",
            media_type="application/pdf",
            quality_status=MaterialQualityStatus.USABLE,
            quality_issues=[],
            parse_status=MaterialParseStatus.SUCCESS,
        ),
    ]

    assessment = RuleEngine().assess_package(materials)

    assert assessment.image_count == 4
    assert assessment.panorama_count == 2
    assert assessment.has_filing_certificate is True
    assert assessment.has_front_level_panorama is True
    assert assessment.has_overhead_panorama is True
    assert assessment.missing_material_categories == []
    assert assessment.missing_requirements == []
    assert "并网许可或调度协议" not in assessment.coverage_requirements
    assert "逆变器铭牌" not in assessment.coverage_requirements
    assert not any("必需材料类别" in item for item in assessment.missing_requirements)
    assert assessment.minimum_gate_passed is None


def test_carport_package_does_not_invent_rooftop_workshop_requirement() -> None:
    assessment = RuleEngine().assess_package([], project_type=ProjectType.CARPORT)

    assert "workshop" not in assessment.missing_material_categories


def test_optional_grid_and_inverter_materials_never_request_replacement_for_gaps() -> None:
    engine = RuleEngine()
    for category in (MaterialCategory.GRID_CONNECTION_DOCUMENT,
                     MaterialCategory.INVERTER_NAMEPLATE):
        material = make_material(category.value, category)
        fields = [OcrField(
            field_id=f"{material.material_id}:missing", material_id=material.material_id,
            field_name="project_name" if category is MaterialCategory.GRID_CONNECTION_DOCUMENT
            else "inverter_model", raw_value=None, normalized_value=None,
            value_status=OcrValueStatus.MISSING, confidence=0.99,
            provider="test", model="test",
        )]
        findings = [make_finding(material.material_id, check, DetectionStatus.NOT_DETECTED)
                    for check in EXPECTED_CHECKS_BY_MATERIAL.get(category, frozenset())]
        review = engine.evaluate_material(material, findings=findings, ocr_fields=fields)
        assert review.action is MaterialReviewAction.WARNING
        assert review.missing_requirements == []
        assert "DOC-OPTIONAL-EVIDENCE-001" in review.triggered_rule_ids

        poor = engine.evaluate_material(material.model_copy(update={
            "quality_status": MaterialQualityStatus.POOR,
        }), findings=findings, ocr_fields=fields)
        assert poor.action is MaterialReviewAction.WARNING
        assert poor.missing_requirements == []

    required = make_material("component", MaterialCategory.COMPONENT_NAMEPLATE)
    review = engine.evaluate_material(required, ocr_fields=[OcrField(
        field_id="component:missing", material_id="component", field_name="component_model",
        raw_value=None, normalized_value=None, value_status=OcrValueStatus.MISSING,
        confidence=0.99, provider="test", model="test",
    )])
    assert review.action is MaterialReviewAction.REQUEST_MORE


def test_workshop_industry_does_not_route_case_without_risk() -> None:
    material = make_material("workshop", MaterialCategory.WORKSHOP)
    engine = RuleEngine()
    findings = [
        make_finding(material.material_id, category, DetectionStatus.NOT_DETECTED)
        for category in (
            engine._expected_checks_for_material(MaterialCategory.WORKSHOP) or frozenset()
        )
    ]

    missing_industry = engine.evaluate_materials(
        [material], findings=findings, project=make_project()
    )[0]
    provided_industry = engine.evaluate_materials(
        [material], findings=findings, project=make_project(industry_name="电子制造")
    )[0]

    assert missing_industry.action is MaterialReviewAction.PASS
    assert missing_industry.requires_manual_review is False
    assert missing_industry.missing_requirements == []
    assert provided_industry.action is MaterialReviewAction.PASS
    assert provided_industry.requires_manual_review is False


def test_pdf_tagged_as_panorama_cannot_satisfy_photo_or_view_gates() -> None:
    materials = [
        Material(
            material_id="panorama-pdf",
            category=MaterialCategory.PANORAMA,
            capture_view=CaptureView.FRONT_LEVEL,
            file_name="panorama.pdf",
            media_type="application/pdf",
            quality_status=MaterialQualityStatus.USABLE,
            quality_issues=[],
            parse_status=MaterialParseStatus.SUCCESS,
        )
    ]

    assessment = RuleEngine().assess_package(materials)

    assert assessment.image_count == 0
    assert assessment.panorama_count == 0
    assert assessment.has_front_level_panorama is False
    assert assessment.has_overhead_panorama is False


def test_two_usable_panoramas_do_not_require_view_labels() -> None:
    materials = [
        make_material("panorama-a", MaterialCategory.PANORAMA),
        make_material("panorama-b", MaterialCategory.PANORAMA),
        make_material("roof", MaterialCategory.ROOF_CONNECTION),
        make_material("grounding", MaterialCategory.ELECTRICAL_GROUNDING),
        make_material("component", MaterialCategory.COMPONENT_NAMEPLATE),
        Material(
            material_id="certificate", category=MaterialCategory.FILING_CERTIFICATE,
            file_name="certificate.pdf", media_type="application/pdf",
            quality_status=MaterialQualityStatus.USABLE, quality_issues=[],
            parse_status=MaterialParseStatus.SUCCESS,
        ),
    ]

    assessment = RuleEngine().assess_package(materials)

    assert assessment.panorama_count == 2
    assert not assessment.has_front_level_panorama
    assert not assessment.has_overhead_panorama
    assert assessment.minimum_gate_passed is None
    assert assessment.missing_requirements == []


def test_one_panorama_has_no_fixed_package_count_request() -> None:
    assessment = RuleEngine().assess_package([
        make_material("panorama-only", MaterialCategory.PANORAMA),
    ])

    assert assessment.image_count == 1
    assert assessment.panorama_count == 1
    assert assessment.minimum_gate_passed is None
    assert assessment.missing_requirements == []


def test_submitted_unusable_panorama_is_not_reported_as_missing() -> None:
    materials = [
        make_material("panorama-a", MaterialCategory.PANORAMA).model_copy(
            update={"quality_status": MaterialQualityStatus.UNUSABLE}),
        make_material("panorama-b", MaterialCategory.PANORAMA),
        make_material("roof", MaterialCategory.ROOF_CONNECTION),
        make_material("grounding", MaterialCategory.ELECTRICAL_GROUNDING),
        make_material("component", MaterialCategory.COMPONENT_NAMEPLATE),
        make_material("workshop", MaterialCategory.WORKSHOP),
        Material(
            material_id="certificate", category=MaterialCategory.FILING_CERTIFICATE,
            file_name="certificate.pdf", media_type="application/pdf",
            quality_status=MaterialQualityStatus.USABLE, quality_issues=[],
            parse_status=MaterialParseStatus.SUCCESS,
        ),
    ]

    assessment = RuleEngine().assess_package(materials)

    assert assessment.panorama_count == 2
    assert not any("全景" in requirement for requirement in assessment.missing_requirements)


def test_explicit_excluded_environment_recommends_rejection() -> None:
    material = make_material("panorama", MaterialCategory.PANORAMA)
    findings = [
        make_finding(material.material_id, category, DetectionStatus.NOT_DETECTED)
        for category in EXPECTED_CHECKS_BY_MATERIAL[MaterialCategory.PANORAMA]
    ]
    findings.append(
        make_finding(
            material.material_id,
            RiskCategory.AGRICULTURE_ENVIRONMENT,
            DetectionStatus.DETECTED,
        )
    )

    review = RuleEngine().evaluate_material(
        material,
        findings=findings,
        project=make_project(),
    )

    assert review.action is MaterialReviewAction.RECOMMEND_REJECT
    assert "ENV-EXCLUDED-001" in review.triggered_rule_ids


def test_uncovered_environment_check_is_advisory_only() -> None:
    material = make_material("panorama", MaterialCategory.PANORAMA)
    findings = [
        make_finding(
            material.material_id,
            RiskCategory.AGRICULTURE_ENVIRONMENT,
            DetectionStatus.NOT_DETECTED,
        )
    ]

    review = RuleEngine().evaluate_materials([material], findings=findings)[0]

    assert review.action is MaterialReviewAction.WARNING
    assert "ENV-COVERAGE-001" in review.triggered_rule_ids
    assert review.missing_requirements == []
    assert review.requires_manual_review is False
    assert any("不代表现场没有该类风险" in reason for reason in review.reasons)


def test_panorama_without_watermark_is_advisory_when_checks_are_covered() -> None:
    material = make_material("panorama", MaterialCategory.PANORAMA)
    findings = [
        make_finding(material.material_id, category, DetectionStatus.NOT_DETECTED)
        for category in EXPECTED_CHECKS_BY_MATERIAL[MaterialCategory.PANORAMA]
    ]
    review = RuleEngine().evaluate_material(material, findings=findings)

    assert review.action is MaterialReviewAction.WARNING
    assert "IMG-WATERMARK-001" in review.triggered_rule_ids
    assert review.missing_requirements == []
    assert review.requires_manual_review is False


def test_watermarked_panorama_outside_fifteen_day_window_is_advisory() -> None:
    material_data = make_material("panorama", MaterialCategory.PANORAMA).model_dump(
        mode="python"
    )
    material_data.update(
        {
            "watermark_status": WatermarkStatus.PRESENT,
            "captured_at": "2026-09-10T10:00:00+08:00",
            "longitude": 113.3,
            "latitude": 23.1,
        }
    )
    material = Material.model_validate(material_data)

    review = RuleEngine().evaluate_material(material, project=make_project())

    assert review.action is MaterialReviewAction.WARNING
    assert "IMG-WATERMARK-DATE-001" in review.triggered_rule_ids
    assert review.missing_requirements == []
    assert review.requires_manual_review is False


def test_watermarked_panorama_with_unreadable_fields_is_advisory() -> None:
    material = make_material("panorama", MaterialCategory.PANORAMA).model_copy(
        update={"watermark_status": WatermarkStatus.PRESENT}
    )
    review = RuleEngine().evaluate_material(material, project=make_project())

    assert review.action is MaterialReviewAction.WARNING
    assert review.missing_requirements == []
    assert review.requires_manual_review is False


def test_minor_shading_only_stays_advisory_when_extent_is_clearly_low() -> None:
    material = make_material("panorama", MaterialCategory.PANORAMA)
    base = [make_finding(material.material_id, category, DetectionStatus.NOT_DETECTED)
            for category in EXPECTED_CHECKS_BY_MATERIAL[MaterialCategory.PANORAMA]
            if category is not RiskCategory.MINOR_SHADING]
    clear = make_finding(material.material_id, RiskCategory.MINOR_SHADING,
                         DetectionStatus.DETECTED, confidence=0.95).model_copy(
                             update={"severity": RiskSeverity.LOW})
    moderate = clear.model_copy(update={"severity": RiskSeverity.MEDIUM})
    unclear_extent = clear.model_copy(update={"severity": RiskSeverity.UNKNOWN})
    uncertain = clear.model_copy(update={"confidence": 0.40})

    clear_review = RuleEngine().evaluate_material(material, findings=[*base, clear])
    moderate_review = RuleEngine().evaluate_material(material, findings=[*base, moderate])
    unclear_review = RuleEngine().evaluate_material(material, findings=[*base, unclear_extent])
    uncertain_review = RuleEngine().evaluate_material(material, findings=[*base, uncertain])

    assert clear_review.action is MaterialReviewAction.WARNING
    assert clear_review.requires_manual_review is False
    assert any("风险提示" in reason for reason in clear_review.reasons)
    assert moderate_review.requires_manual_review is True
    assert any("遮挡程度达到中等" in reason for reason in moderate_review.reasons)
    assert unclear_review.requires_manual_review is True
    assert uncertain_review.requires_manual_review is True


def test_two_panorama_views_of_detected_shading_require_extent_review() -> None:
    engine = RuleEngine()
    front = make_material("front", MaterialCategory.PANORAMA, view=CaptureView.FRONT_LEVEL)
    overhead = make_material("overhead", MaterialCategory.PANORAMA, view=CaptureView.OVERHEAD)
    findings = [make_finding(mid, RiskCategory.MINOR_SHADING, DetectionStatus.DETECTED)
                .model_copy(update={"severity": RiskSeverity.LOW})
                for mid in ("front", "overhead")]
    reviews = [engine.evaluate_material(material, findings=[finding])
               for material, finding in zip((front, overhead), findings)]
    assert all(not review.requires_manual_review for review in reviews)

    confirmed = engine.corroborate_panorama_shading([front, overhead], findings, reviews)
    assert all(review.requires_manual_review for review in confirmed)
    assert all("IMG-SHADING-MULTIVIEW-REVIEW-001" in review.triggered_rule_ids
               for review in confirmed)
    assert not engine.corroborate_panorama_shading([front], findings[:1], reviews[:1])[0].requires_manual_review


def test_grounding_photo_omission_does_not_require_review() -> None:
    material = make_material("grounding", MaterialCategory.ELECTRICAL_GROUNDING)

    review = RuleEngine().evaluate_material(material)

    assert review.action is MaterialReviewAction.WARNING
    assert "ENV-COVERAGE-001" in review.triggered_rule_ids
    assert "DOC-REQUIRED-FIELDS-001" not in review.triggered_rule_ids
    assert review.requires_manual_review is False
    assert review.missing_requirements == []


def test_poor_image_quality_requests_replacement_even_when_other_checks_are_clear() -> None:
    material = make_material("panorama", MaterialCategory.PANORAMA)
    findings = [
        make_finding(material.material_id, category, DetectionStatus.NOT_DETECTED)
        for category in EXPECTED_CHECKS_BY_MATERIAL[MaterialCategory.PANORAMA]
        if category is not RiskCategory.IMAGE_QUALITY
    ]
    findings.append(
        make_finding(material.material_id, RiskCategory.IMAGE_QUALITY, DetectionStatus.DETECTED)
    )

    review = RuleEngine().evaluate_material(material, findings=findings)

    assert review.action is MaterialReviewAction.REQUEST_MORE
    assert "MAT-QUALITY-001" in review.triggered_rule_ids
    assert any("补拍" in item for item in review.missing_requirements)


def test_monitoring_material_does_not_trigger_discount_review() -> None:
    material = make_material("monitoring", MaterialCategory.MONITORING_OPTIONAL)
    field = OcrField(
        field_id="monitoring:present",
        material_id="monitoring",
        field_name="monitoring_present",
        raw_value="有",
        normalized_value="true",
        value_status=OcrValueStatus.EXTRACTED,
        confidence=0.95,
        provider="test-ocr",
        model="test-model",
    )

    review = RuleEngine().evaluate_material(
        material,
        findings=[
            make_finding(
                material.material_id,
                RiskCategory.IMAGE_QUALITY,
                DetectionStatus.NOT_DETECTED,
            ),
            make_finding(
                material.material_id,
                RiskCategory.MONITORING_EFFECTIVE_COVERAGE,
                DetectionStatus.DETECTED,
            ),
        ],
        ocr_fields=[field],
    )

    assert review.action is MaterialReviewAction.PASS
    assert review.requires_manual_review is False
    assert "DISC-MONITORING-REVIEW-001" not in review.triggered_rule_ids


def test_filing_model_assessment_recommends_rejection() -> None:
    material = Material(
        material_id="certificate",
        category=MaterialCategory.FILING_CERTIFICATE,
        file_name="certificate.pdf",
        media_type="application/pdf",
        quality_status=MaterialQualityStatus.USABLE,
        quality_issues=[],
        parse_status=MaterialParseStatus.SUCCESS,
    )
    fields = [
        OcrField(
            field_id=f"certificate:{name}",
            material_id="certificate",
            field_name=name,
            raw_value=value,
            normalized_value=value,
            value_status=OcrValueStatus.EXTRACTED,
            confidence=0.95,
            provider="test-ocr",
            model="test-model",
            filing_assessment=({
                "status": "confirmed", "prohibition_type": "fishery_pv",
                "reason": "项目名称描述渔业与光伏复合利用", "evidence_quote": "渔光",
            } if name == "project_name" else None),
        )
        for name, value in (
            ("project_name", "某某渔光项目"),
            ("site_address", "广东省广州市天河区"),
            ("insured_name", "样例企业"),
        )
    ]

    review = RuleEngine().evaluate_materials(
        [material],
        ocr_fields=fields,
        project=make_project(project_name="某某渔光项目"),
    )[0]

    assert review.action is MaterialReviewAction.RECOMMEND_REJECT
    assert "DOC-FILING-EXCLUDED-001" in review.triggered_rule_ids


def test_filing_and_grid_documents_are_cross_checked() -> None:
    materials = [
        Material(
            material_id="filing",
            category=MaterialCategory.FILING_CERTIFICATE,
            file_name="filing.pdf",
            media_type="application/pdf",
            quality_status=MaterialQualityStatus.USABLE,
            quality_issues=[],
            parse_status=MaterialParseStatus.SUCCESS,
        ),
        Material(
            material_id="grid",
            category=MaterialCategory.GRID_CONNECTION_DOCUMENT,
            file_name="grid.pdf",
            media_type="application/pdf",
            quality_status=MaterialQualityStatus.USABLE,
            quality_issues=[],
            parse_status=MaterialParseStatus.SUCCESS,
        ),
    ]
    fields = []
    values = {
        "filing": {
            "project_name": "光伏项目甲",
            "project_entity": "示例企业",
            "site_address": "广东省广州市天河区",
            "insured_name": "示例企业",
        },
        "grid": {
            "project_name": "光伏项目乙",
            "project_entity": "示例企业",
            "site_address": "广东省广州市天河区",
            "grid_connection_date": "2026-01-01",
        },
    }
    for material_id, material_fields in values.items():
        for field_name, value in material_fields.items():
            fields.append(
                OcrField(
                    field_id=f"{material_id}:{field_name}",
                    material_id=material_id,
                    field_name=field_name,
                    raw_value=value,
                    normalized_value=value,
                    value_status=OcrValueStatus.EXTRACTED,
                    confidence=0.95,
                    provider="test-ocr",
                    model="test-model",
                )
            )

    reviews = RuleEngine().evaluate_materials(
        materials,
        ocr_fields=fields,
        project=make_project(project_name="光伏项目甲"),
    )

    assert all("DOC-CROSS-CHECK-001" in review.triggered_rule_ids for review in reviews)
    assert all(review.requires_manual_review for review in reviews)


def test_explicit_rejection_ignores_historical_package_gate_requests() -> None:
    material = make_material("panorama", MaterialCategory.PANORAMA)
    review = MaterialReview(
        material_id=material.material_id,
        action=MaterialReviewAction.RECOMMEND_REJECT,
        triggered_rule_ids=["ENV-EXCLUDED-001"],
        finding_ids=[],
        ocr_field_ids=[],
        reasons=["发现明确拒保环境"],
        missing_requirements=[],
        requires_manual_review=False,
    )
    case = UnderwritingCase(
        schema_version="0.1.0",
        case_id="case-reject-and-missing",
        project=make_project(longitude=None),
        materials=[material],
        ocr_fields=[],
        findings=[],
        material_reviews=[review],
        package_assessment=PackageAssessment(
            image_count=1,
            panorama_count=1,
            has_filing_certificate=False,
            has_front_level_panorama=False,
            has_overhead_panorama=False,
            missing_requirements=["补充备案证"],
        ),
        processing_trace=[],
    )

    decision = DecisionEngine().decide(case)

    assert decision.decision is DecisionType.RECOMMEND_REJECT
    assert "补充备案证" not in decision.missing_requirements
    assert "REQ-PROJECT-COORDINATES" not in decision.decisive_rule_ids
    assert any("经纬度缺失" in warning for warning in decision.warnings)


def test_complementary_panorama_views_share_environment_and_risk_coverage() -> None:
    first = make_material("front", MaterialCategory.PANORAMA, view=CaptureView.FRONT_LEVEL)
    second = make_material("overhead", MaterialCategory.PANORAMA, view=CaptureView.OVERHEAD)
    expected = EXPECTED_CHECKS_BY_MATERIAL[MaterialCategory.PANORAMA]
    first_findings = [
        make_finding(first.material_id, category, DetectionStatus.NOT_DETECTED)
        for category in expected
        if category is not RiskCategory.FOREST_ENVIRONMENT
    ]
    second_findings = [
        make_finding(second.material_id, category, DetectionStatus.NOT_DETECTED)
        for category in expected
        if category is not RiskCategory.AGRICULTURE_ENVIRONMENT
    ]

    reviews = RuleEngine().evaluate_materials(
        [first, second], findings=[*first_findings, *second_findings]
    )

    assert all(review.action is MaterialReviewAction.WARNING for review in reviews)
    assert all("ENV-COVERAGE-001" not in review.triggered_rule_ids for review in reviews)
    assert all(not review.missing_requirements for review in reviews)



def test_environment_visible_only_in_distant_background_does_not_reject() -> None:
    material = make_material("panorama", MaterialCategory.PANORAMA)
    environment_categories = {
        category
        for category in EXPECTED_CHECKS_BY_MATERIAL[MaterialCategory.PANORAMA]
        if category.value.endswith("_environment")
    }
    findings = [
        make_finding(material.material_id, category, DetectionStatus.NOT_DETECTED)
        for category in EXPECTED_CHECKS_BY_MATERIAL[MaterialCategory.PANORAMA]
        if category not in environment_categories
    ]
    findings.extend(
        make_finding(
            material.material_id,
            category,
            DetectionStatus.DETECTED,
            environment_relation=EnvironmentRelation.DISTANT_BACKGROUND,
        )
        for category in sorted(environment_categories, key=lambda item: item.value)
    )

    review = RuleEngine().evaluate_material(material, findings=findings)

    assert review.action is MaterialReviewAction.WARNING
    assert "ENV-EXCLUDED-001" not in review.triggered_rule_ids
    assert "ENV-BACKGROUND-CONTEXT-001" in review.triggered_rule_ids
    assert review.requires_manual_review is False



def test_nearby_environment_routes_to_manual_review_until_boundary_is_defined() -> None:
    material = make_material("panorama", MaterialCategory.PANORAMA)
    findings = [
        make_finding(material.material_id, category, DetectionStatus.NOT_DETECTED)
        for category in EXPECTED_CHECKS_BY_MATERIAL[MaterialCategory.PANORAMA]
    ]
    findings.append(
        make_finding(
            material.material_id,
            RiskCategory.AGRICULTURE_ENVIRONMENT,
            DetectionStatus.DETECTED,
            environment_relation=EnvironmentRelation.OPERATIONAL_SURROUNDINGS,
        )
    )

    review = RuleEngine().evaluate_material(material, findings=findings)

    assert review.action is MaterialReviewAction.WARNING
    assert review.requires_manual_review is True
    assert "ENV-EXCLUDED-001" not in review.triggered_rule_ids
    assert "ENV-SITE-RELATION-REVIEW-001" in review.triggered_rule_ids
    assert any("边界尚待业务确认" in reason for reason in review.reasons)



def test_uncertain_site_relation_routes_to_manual_review_without_auto_rejection() -> None:
    material = make_material("panorama", MaterialCategory.PANORAMA)
    findings = [
        make_finding(material.material_id, category, DetectionStatus.NOT_DETECTED)
        for category in EXPECTED_CHECKS_BY_MATERIAL[MaterialCategory.PANORAMA]
    ]
    findings.append(
        make_finding(
            material.material_id,
            RiskCategory.AGRICULTURE_ENVIRONMENT,
            DetectionStatus.DETECTED,
            environment_relation=EnvironmentRelation.UNCERTAIN,
        )
    )

    review = RuleEngine().evaluate_material(material, findings=findings)

    assert review.action is MaterialReviewAction.WARNING
    assert review.requires_manual_review is True
    assert "ENV-EXCLUDED-001" not in review.triggered_rule_ids
    assert "ENV-SITE-RELATION-REVIEW-001" in review.triggered_rule_ids
    assert review.missing_requirements == []



def test_model_manual_review_flag_suppresses_environment_auto_rejection() -> None:
    material = make_material("panorama", MaterialCategory.PANORAMA)
    findings = [
        make_finding(material.material_id, category, DetectionStatus.NOT_DETECTED)
        for category in EXPECTED_CHECKS_BY_MATERIAL[MaterialCategory.PANORAMA]
    ]
    findings.append(
        make_finding(
            material.material_id,
            RiskCategory.AGRICULTURE_ENVIRONMENT,
            DetectionStatus.DETECTED,
            environment_relation=EnvironmentRelation.PROJECT_SITE,
            requires_manual_review=True,
        )
    )

    review = RuleEngine().evaluate_material(material, findings=findings)

    assert review.action is MaterialReviewAction.WARNING
    assert review.requires_manual_review is True
    assert "ENV-EXCLUDED-001" not in review.triggered_rule_ids



def test_low_confidence_site_environment_is_not_an_automatic_rejection() -> None:
    material = make_material("panorama", MaterialCategory.PANORAMA)
    findings = [
        make_finding(material.material_id, category, DetectionStatus.NOT_DETECTED)
        for category in EXPECTED_CHECKS_BY_MATERIAL[MaterialCategory.PANORAMA]
    ]
    findings.append(
        make_finding(
            material.material_id,
            RiskCategory.AGRICULTURE_ENVIRONMENT,
            DetectionStatus.DETECTED,
            environment_relation=EnvironmentRelation.PROJECT_SITE,
            confidence=0.64,
        )
    )

    review = RuleEngine().evaluate_material(material, findings=findings)

    assert review.action is MaterialReviewAction.WARNING
    assert review.requires_manual_review is True
    assert "ENV-EXCLUDED-001" not in review.triggered_rule_ids
    assert any("0.64" in reason and "0.65" in reason for reason in review.reasons)



def test_legacy_finding_without_environment_relation_is_not_auto_rejected() -> None:
    material = make_material("panorama", MaterialCategory.PANORAMA)
    finding_payload = make_finding(
        material.material_id,
        RiskCategory.FOREST_ENVIRONMENT,
        DetectionStatus.DETECTED,
    ).model_dump(mode="python")
    finding_payload.pop("environment_relation")
    legacy_finding = RiskFinding.model_validate(finding_payload)
    findings = [
        make_finding(material.material_id, category, DetectionStatus.NOT_DETECTED)
        for category in EXPECTED_CHECKS_BY_MATERIAL[MaterialCategory.PANORAMA]
    ]
    findings.append(legacy_finding)

    review = RuleEngine().evaluate_material(material, findings=findings)

    assert legacy_finding.environment_relation is EnvironmentRelation.UNCERTAIN
    assert review.action is MaterialReviewAction.WARNING
    assert review.requires_manual_review is True
    assert "ENV-EXCLUDED-001" not in review.triggered_rule_ids
