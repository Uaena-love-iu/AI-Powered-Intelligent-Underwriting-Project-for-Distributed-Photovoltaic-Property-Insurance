import json

import httpx
from app.contracts import Material, MaterialCategory, OcrField, ProjectInfo
from app.providers import CompatibleOcrProvider, MaterialInput
from app.rules import RuleEngine
from app.settings import VlmSettings


def filing_material() -> Material:
    return Material(
        material_id="filing", category=MaterialCategory.FILING_CERTIFICATE,
        file_name="备案证.png", media_type="image/png", quality_status="usable",
        quality_issues=[], parse_status="success",
    )


def project(name: str = "屋顶示例项目") -> ProjectInfo:
    return ProjectInfo(
        project_name=name, insured_name="示例企业", project_type="unknown",
        installation_type="unknown", site_address=None, longitude=None,
        latitude=None, proposed_start_date=None, component_model=None,
    )


def field(name: str, raw: str, assessment: dict | None) -> OcrField:
    return OcrField(
        field_id=f"filing:{name}", material_id="filing", field_name=name,
        raw_value=raw, normalized_value=raw, value_status="extracted",
        confidence=0.96, provider="test", model="test", page_number=2,
        filing_assessment=assessment,
    )


def clear_name() -> OcrField:
    return field("project_name", "屋顶示例项目", {
        "status": "not_found", "reason": "项目名称完整可读，未见禁投名称",
    })


def test_model_assessment_is_retained_with_quote_and_location() -> None:
    payload = {"fields": [
        {
            "field_name": "document_type", "raw_value": "备案证",
            "normalized_value": "filing_certificate", "value_status": "extracted",
            "confidence": 0.98, "bbox": None, "evidence_text": "备案证",
        },
        {
            "field_name": "project_name", "raw_value": "屋顶示例项目",
            "normalized_value": "屋顶示例项目", "value_status": "extracted",
            "confidence": 0.96, "bbox": None, "evidence_text": "项目名称：屋顶示例项目",
            "filing_assessment": {
                "status": "not_found", "reason": "名称未见禁投词",
            },
        },
        {
            "field_name": "construction_content",
            "raw_value": "光伏板下方开展种植", "normalized_value": "光伏板下方开展种植",
            "value_status": "extracted", "confidence": 0.93, "bbox": None,
            "evidence_text": "建设内容：光伏板下方开展种植",
            "filing_assessment": {
                "status": "confirmed", "prohibition_type": "under_panel_cultivation",
                "generation_mode": "distributed", "installation_site": "other",
                "reason": "明确在组件下方种植", "evidence_quote": "光伏板下方开展种植",
                "evidence_bbox": {
                    "x_min": 0.1, "y_min": 0.2, "x_max": 0.8,
                    "y_max": 0.3, "coordinate_space": "normalized_0_1",
                },
            },
        },
    ]}

    def handler(request: httpx.Request) -> httpx.Response:
        prompt = json.loads(request.content)["messages"][0]["content"]
        assert "filing_assessment" in prompt
        return httpx.Response(200, json={"choices": [{"message": {
            "content": json.dumps(payload, ensure_ascii=False),
        }}]})

    provider = CompatibleOcrProvider(VlmSettings(
        base_url="https://api.example.com/v1", api_key="test-key",
        model="test-model", timeout_seconds=10,
    ), transport=httpx.MockTransport(handler))
    fields = provider.extract(MaterialInput(material=filing_material(), content=b"image"))

    content = next(item for item in fields if item.field_name == "construction_content")
    assert content.page_number == 1
    assert content.filing_assessment.evidence_quote == "光伏板下方开展种植"
    assert content.filing_assessment.generation_mode == "distributed"
    assert content.filing_assessment.installation_site == "other"
    review = RuleEngine().evaluate_filing_stage([filing_material()], fields, project())
    assert review.action == "recommend_reject"
    assert review.terminates_run is True
    assert "第 1 页" in review.evidence_texts[0]
    assert "(0.10, 0.20)-(0.80, 0.30)" in review.evidence_texts[0]


def test_name_model_assessment_rejects_without_construction_content() -> None:
    name = field("project_name", "某某渔光项目", {
        "status": "confirmed", "prohibition_type": "fishery_pv",
        "reason": "项目名称含渔光", "evidence_quote": "渔光",
    })
    review = RuleEngine().evaluate_filing_stage(
        [filing_material()], [name], project("某某渔光项目"),
    )
    assert review.action == "recommend_reject"
    assert review.ocr_field_ids == [name.field_id]


def test_keyword_without_model_assessment_does_not_reject() -> None:
    name = field("project_name", "某某渔光项目", None)
    review = RuleEngine().evaluate_filing_stage(
        [filing_material()], [name], project("某某渔光项目"),
    )
    assert review.action == "manual_review"
    assert review.terminates_run is False


def test_reliable_filing_name_mismatch_recommends_rejection() -> None:
    name = field("project_name", "另一个项目", {
        "status": "not_found", "reason": "未见禁投名称",
    })
    review = RuleEngine().evaluate_filing_stage(
        [filing_material()], [name], project(),
    )

    assert review.action == "recommend_reject"
    assert review.rule_ids == ["DOC-FILING-NAME-CONFLICT-001"]
    assert review.ocr_field_ids == [name.field_id]
    assert review.terminates_run is True


def test_construction_content_needs_matching_non_negated_quote() -> None:
    for raw, quote in [
        ("不涉及光伏板下方种植", "光伏板下方种植"),
        ("仅在屋顶建设光伏电站", "光伏板下方种植"),
        ("厂房周边种植绿化植物", "种植绿化植物"),
    ]:
        content = field("construction_content", raw, {
            "status": "confirmed", "prohibition_type": "under_panel_cultivation",
            "reason": "模型判断为板下种植", "evidence_quote": quote,
        })
        review = RuleEngine().evaluate_filing_stage(
            [filing_material()], [clear_name(), content], project(),
        )
        assert review.action == "manual_review"
        assert review.terminates_run is False


def test_unrelated_field_keyword_cannot_trigger_filing_rejection() -> None:
    content = field("construction_content", "在厂房屋顶建设光伏电站", {
        "status": "not_found", "reason": "建设内容未见禁投用途",
    })
    project_type = field("project_type", "集中式", None)
    review = RuleEngine().evaluate_filing_stage(
        [filing_material()], [clear_name(), content, project_type], project(),
    )
    assert review.action == "continue"


def test_changed_construction_content_needs_version_review() -> None:
    prohibited = field("construction_content", "光伏板下方开展种植", {
        "status": "confirmed", "prohibition_type": "under_panel_cultivation",
        "reason": "明确在组件下方种植", "evidence_quote": "光伏板下方开展种植",
    })
    amended = field("construction_content", "仅在屋顶安装光伏组件", {
        "status": "not_found", "reason": "未见禁投用途",
    }).model_copy(update={"field_id": "filing:construction_content:page3", "page_number": 3})
    review = RuleEngine().evaluate_filing_stage(
        [filing_material()], [clear_name(), prohibited, amended], project(),
    )
    assert review.action == "manual_review"
    assert review.terminates_run is False
    assert any("有效版本" in question for question in review.pending_questions)


def test_filing_prompt_asks_for_semantic_scope_and_farmer_roof_exception() -> None:
    from app.prompts.stages import stage_prompt

    prompt = stage_prompt("filing_assessment")
    assert "不是搜索几个词" in prompt
    assert "农户住宅屋顶" in prompt
    assert "组件下继续种植小麦" in prompt
    assert "车棚顶" in prompt
    assert "generation_mode" in prompt
    assert "installation_site" in prompt


def test_farmer_roof_without_farming_is_not_rejected() -> None:
    content = field("construction_content", "农户住宅屋顶建设分布式光伏，屋面以下仍为住宅", {
        "status": "not_found", "generation_mode": "distributed",
        "installation_site": "building_roof", "reason": "农户身份不代表板下农业用途",
    })
    review = RuleEngine().evaluate_filing_stage(
        [filing_material()], [clear_name(), content], project(),
    )
    assert review.action == "continue"


def test_semantic_filing_findings_do_not_require_old_name_keywords() -> None:
    cases = [
        ("组件架设在耕地上方，组件下继续种植小麦", "under_panel_cultivation"),
        ("塘内继续养鱼，塘上架设光伏组件", "fishery_pv"),
        ("在湖面布设漂浮组件方阵", "water"),
        ("在山坡上采用独立支架铺设组件", "mountain"),
        ("建成大型地面电站，由统一升压站集中送出", "centralized"),
    ]
    for raw, prohibition_type in cases:
        content = field("construction_content", raw, {
            "status": "confirmed", "prohibition_type": prohibition_type,
            "reason": "备案建设方式明确涉及该禁投场景", "evidence_quote": raw,
        })
        review = RuleEngine().evaluate_filing_stage(
            [filing_material()], [clear_name(), content], project(),
        )
        assert review.action == "recommend_reject", raw


def test_farmer_identity_alone_cannot_prove_agricultural_project() -> None:
    content = field("construction_content", "农户住宅屋顶建设光伏", {
        "status": "confirmed", "prohibition_type": "agriculture",
        "reason": "模型误将农户身份当作农业用途", "evidence_quote": "农户住宅屋顶",
    })
    review = RuleEngine().evaluate_filing_stage(
        [filing_material()], [clear_name(), content], project(),
    )
    assert review.action == "manual_review"
    assert review.terminates_run is False
    assert any("农户身份" in question for question in review.pending_questions)


def test_charging_station_location_cannot_prove_pv_installation_type() -> None:
    content = field(
        "construction_content",
        "利用同心县福银高速停车场安装360kW的光、储、充一体化电动车充电站，以及12kW的光伏车棚",
        {
            "status": "confirmed", "prohibition_type": "unsupported_installation",
            "generation_mode": "distributed", "installation_site": "other",
            "reason": "停车场安装充电站，推测为地面安装光伏组件",
            "evidence_quote": "利用同心县福银高速停车场安装360kW的光、储、充一体化电动车充电站",
        },
    )
    engine = RuleEngine()
    normalized = engine.normalize_filing_fields([content])[0]
    assert normalized.filing_assessment.status == "not_found"
    assert normalized.filing_assessment.installation_site == "carport_roof"
    assert "光伏车棚" in normalized.filing_assessment.reason
    assert normalized.filing_assessment.evidence_quote is None
    review = engine.evaluate_filing_stage(
        [filing_material()], [clear_name(), content], project(),
    )
    assert review.action == "continue"
    assert review.terminates_run is False


def test_missing_filing_installation_type_continues_to_photo_check() -> None:
    content = field("construction_content", "建设12kW光伏系统", {
        "status": "uncertain", "generation_mode": "distributed",
        "installation_site": "unknown", "reason": "备案未写明组件安装位置",
    })
    review = RuleEngine().evaluate_filing_stage(
        [filing_material()], [clear_name(), content], project(),
    )
    assert review.action == "continue"


def test_not_found_with_unsupported_other_site_inference_continues() -> None:
    content = field("construction_content", "停车场建设充电站及光伏车棚", {
        "status": "not_found", "generation_mode": "distributed",
        "installation_site": "other", "reason": "误将充电站地点视为组件位置",
    })
    engine = RuleEngine()
    assert engine.normalize_filing_fields([content])[0].filing_assessment.installation_site == "carport_roof"
    review = engine.evaluate_filing_stage(
        [filing_material()], [clear_name(), content], project(),
    )
    assert review.action == "continue"


def test_explicit_carport_is_retained_when_model_marks_site_unknown() -> None:
    content = field("construction_content", "建设12kW光伏车棚", {
        "status": "not_found", "installation_site": "unknown",
        "reason": "模型没有识别出车棚",
    })
    normalized = RuleEngine().normalize_filing_fields([content])[0]
    assert normalized.filing_assessment.installation_site == "carport_roof"


def test_ground_mount_clause_is_not_hidden_by_a_second_carport_clause() -> None:
    raw = "地面支架安装光伏组件，以及建设12kW光伏车棚"
    content = field("construction_content", raw, {
        "status": "confirmed", "prohibition_type": "unsupported_installation",
        "installation_site": "other", "reason": "另有地面支架式阵列",
        "evidence_quote": raw,
    })
    review = RuleEngine().evaluate_filing_stage(
        [filing_material()], [clear_name(), content], project(),
    )
    assert review.action == "recommend_reject"


def test_ground_level_parking_lot_does_not_prove_ground_mounted_pv() -> None:
    content = field("construction_content", "在地面停车场安装光伏组件", {
        "status": "confirmed", "prohibition_type": "unsupported_installation",
        "installation_site": "other", "reason": "地面停车场是地面支架式",
        "evidence_quote": "在地面停车场安装光伏组件",
    })
    review = RuleEngine().evaluate_filing_stage(
        [filing_material()], [clear_name(), content], project(),
    )
    assert review.action == "continue"


def test_explicit_ground_mounted_pv_still_rejects() -> None:
    content = field("construction_content", "在停车场地面独立支架安装光伏组件", {
        "status": "confirmed", "prohibition_type": "unsupported_installation",
        "installation_site": "other", "reason": "明确地面独立支架安装光伏组件",
        "evidence_quote": "在停车场地面独立支架安装光伏组件",
    })
    review = RuleEngine().evaluate_filing_stage(
        [filing_material()], [clear_name(), content], project(),
    )
    assert review.action == "recommend_reject"
    assert review.terminates_run is True
