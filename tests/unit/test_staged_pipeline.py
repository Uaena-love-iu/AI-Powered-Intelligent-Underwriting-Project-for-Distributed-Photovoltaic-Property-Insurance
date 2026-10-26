"""Stage ordering and evidence retention for the pre-catastrophe review."""

import json
from datetime import datetime
from io import BytesIO
from threading import Barrier, Lock

import httpx
from app.api.schemas import RealAnalyzeManifest
from app.api.underwriting_routes import _build_material_inputs
from app.contracts import (
    CaptureView,
    InstallationAnalysis,
    InstallationArea,
    Material,
    MaterialCategory,
    MaterialParseStatus,
    MaterialQualityStatus,
    OcrField,
    ProjectInfo,
    RiskFinding,
    WatermarkStatus,
)
from app.location import GeocodeResult
from app.pipeline import UnderwritingPipeline
from app.providers import CompatibleVisionProvider, MaterialInput, MockOcrProvider, ProviderError
from app.providers.vision.base import VisionProvider
from app.reports import generate_markdown_report
from app.rules import RuleEngine
from app.rules.completeness import assess_checks, build_applicable_checks
from app.rules.config import BusinessRulesConfig
from app.rules.engine import EXPECTED_CHECKS_BY_MATERIAL
from app.settings import VlmSettings
from PIL import Image


def project(**updates):
    payload = {"project_name": "屋顶示例项目", "insured_name": "示例企业",
               "project_type": "unknown", "installation_type": "unknown",
               "site_address": None, "longitude": None, "latitude": None,
               "proposed_start_date": None, "component_model": None}
    payload.update(updates)
    return ProjectInfo(**payload)


def image(identifier, category=MaterialCategory.PANORAMA):
    return MaterialInput(material=Material(
        material_id=identifier, category=category, file_name=identifier + ".jpg",
        media_type="image/jpeg", quality_status=MaterialQualityStatus.USABLE,
        quality_issues=[], parse_status=MaterialParseStatus.SUCCESS,
    ), content=b"photo")


def certificate():
    return MaterialInput(material=Material(
        material_id="filing", category=MaterialCategory.FILING_CERTIFICATE,
        file_name="filing.pdf", media_type="application/pdf",
        quality_status=MaterialQualityStatus.USABLE, quality_issues=[],
        parse_status=MaterialParseStatus.SUCCESS,
    ), content=b"document")


class FilingOcr(MockOcrProvider):
    def extract(self, item):
        if item.material.category is MaterialCategory.FILING_CERTIFICATE:
            return [OcrField(
                field_id="filing:project_name", material_id="filing",
                field_name="project_name", raw_value="渔光互补项目",
                normalized_value="渔光互补项目", value_status="extracted",
                confidence=0.98, provider=self.name, model=self.model_name,
                filing_assessment={
                    "status": "confirmed", "prohibition_type": "fishery_pv",
                    "reason": "项目名称描述渔业与光伏复合利用", "evidence_quote": "渔光",
                },
            )]
        return []


class FixedGroupVision(VisionProvider):
    def __init__(self, scene="building_roof", status="confirmed", reject_image=False):
        self.scene, self.status, self.reject_image = scene, status, reject_image
        self.group_calls = 0
        self.group_material_ids = []
        self.single_calls = []

    @property
    def name(self):
        return "fixed-group"

    @property
    def model_name(self):
        return "fixed-group-v1"

    def analyze_installation(self, materials, *, filing_fields=None):
        self.group_calls += 1
        self.group_material_ids = [item.material.material_id for item in materials]
        return InstallationAnalysis(
            provider=self.name, model=self.model_name,
            areas=[InstallationArea(
                area_id="project", scene_type=self.scene, status=self.status,
                material_ids=[item.material.material_id for item in materials],
                supporting_structure="独立地面支架" if self.scene == "ground_mount" else "建筑屋面",
                evidence_text="照片显示组件由独立地面基础承载" if self.scene == "ground_mount"
                else "照片显示组件固定在建筑屋面",
                evidence_regions=[{"material_id": materials[0].material.material_id,
                                   "bbox": None, "description": "原图可见承载连接关系"}]
                if materials else [],
                pending_question="核对承载结构" if self.status != "confirmed" else None,
            )],
        )

    def analyze(self, item):
        self.single_calls.append(item.material.material_id)
        if self.reject_image and item.material.material_id == "first":
            return [RiskFinding(
                finding_id="first:shade", material_id="first",
                category="severe_shading", label="大面积遮挡",
                detection_status="detected", severity="high", confidence=0.95,
                evidence_text="组件上方连续大面积阴影", provider=self.name,
                model=self.model_name, requires_manual_review=False,
            )]
        return []


class CompleteVision(FixedGroupVision):
    def __init__(self):
        super().__init__()
        self.geometry_calls = 0

    def analyze(self, item):
        self.single_calls.append(item.material.material_id)
        return [RiskFinding(
            finding_id=f"{item.material.material_id}:{category.value}",
            material_id=item.material.material_id, category=category,
            label=category.value, detection_status="not_detected",
            severity="info", confidence=0.99, evidence_text="已检查，未见该风险",
            provider=self.name, model=self.model_name, requires_manual_review=False,
        ) for category in EXPECTED_CHECKS_BY_MATERIAL[item.material.category]]

    def estimate_geometry(self, materials, installation):
        self.geometry_calls += 1
        return super().estimate_geometry(materials, installation)


class CompleteFilingOcr(MockOcrProvider):
    def extract(self, item):
        if item.material.category is not MaterialCategory.FILING_CERTIFICATE:
            return []
        values = {
            "project_name": "屋顶示例项目", "site_address": "示例地址",
            "insured_name": "示例企业", "construction_content": "在厂房屋顶建设光伏电站",
        }
        return [OcrField(
            field_id=f"filing:{name}", material_id="filing", field_name=name,
            raw_value=value, normalized_value=value, value_status="extracted",
            confidence=0.99, provider=self.name, model=self.model_name,
            filing_assessment={"status": "not_found", "reason": "完整原文未见禁投事实"}
            if name in {"project_name", "construction_content"} else None,
        ) for name, value in values.items()]


def test_unsupported_installation_inference_does_not_stop_photo_stage():
    class InferredGroundMountOcr(CompleteFilingOcr):
        def extract(self, item):
            fields = super().extract(item)
            raw = ("利用同心县福银高速停车场安装360kW的光、储、充一体化电动车充电站，"
                   "以及12kW的光伏车棚")
            quote = "利用同心县福银高速停车场安装360kW的光、储、充一体化电动车充电站"
            return [field.model_copy(update={
                "raw_value": raw, "normalized_value": raw,
                "filing_assessment": field.filing_assessment.model_copy(update={
                    "status": "confirmed", "prohibition_type": "unsupported_installation",
                    "installation_site": "other", "evidence_quote": quote,
                    "reason": "误将充电站位置视为光伏组件安装位置",
                }),
            }) if field.field_name == "construction_content" else field for field in fields]

    vision = CompleteVision()
    case = UnderwritingPipeline(
        ocr_provider=InferredGroundMountOcr(), vision_provider=vision,
    ).run(case_id="filing-location-inference", project=project(),
          materials=[certificate(), image("panorama")])

    assert case.stage_reviews[0].action == "continue"
    assert vision.group_calls == 1
    assert case.stage_reviews[1].stage == "installation_type"
    content = next(field for field in case.ocr_fields
                   if field.field_name == "construction_content")
    assert content.filing_assessment.status == "not_found"
    assert content.filing_assessment.installation_site == "carport_roof"


def test_distant_filing_address_and_photo_coordinate_pause_for_manual_review():
    class NingxiaFilingOcr(CompleteFilingOcr):
        def extract(self, item):
            fields = super().extract(item)
            return [field.model_copy(update={
                "raw_value": "吴忠市同心县豫海镇",
                "normalized_value": "吴忠市同心县豫海镇",
            }) if field.field_name == "site_address" else field for field in fields]

    class TownshipGeocoder:
        def lookup(self, address, *, city=None):
            return GeocodeResult(105.912878, 36.980018, "乡镇", address, False)

    source = image("site")
    source = MaterialInput(material=source.material.model_copy(update={
        "watermark_status": WatermarkStatus.PRESENT,
        "longitude": 113.1189, "latitude": 23.0345,
    }), content=source.content)
    vision = CompleteVision()
    case = UnderwritingPipeline(
        ocr_provider=NingxiaFilingOcr(), vision_provider=vision,
        location_geocoder=TownshipGeocoder(),
    ).run(case_id="distant-location", project=project(project_type="rooftop",
        installation_type="flat_roof"), materials=[certificate(), source])
    assert case.location_assessment.address_photo_conflict
    assert case.pre_catastrophe_outcome == "manual_review"
    assert case.decision.decision.value == "manual_review"
    assert "LOC-CONFLICT-001" in case.decision.decisive_rule_ids
    assert case.stage_reviews[-1].action == "manual_review"
    assert case.stage_reviews[-1].stage == "location"
    assert case.catastrophe_assessment is None
    assert vision.geometry_calls == 0


def test_filing_rejection_prevents_group_and_all_later_calls():
    vision = FixedGroupVision()
    case = UnderwritingPipeline(ocr_provider=FilingOcr(), vision_provider=vision).run(
        case_id="filing-stop", project=project(project_name="渔光互补项目"),
        materials=[certificate(), image("first")],
    )
    assert case.decision.decision.value == "recommend_reject"
    assert case.stage_reviews[0].terminates_run
    assert case.stage_reviews[0].ocr_field_ids == ["filing:project_name"]
    assert vision.group_calls == 0 and vision.single_calls == []
    assert case.package_assessment is None and case.catastrophe_assessment is None
    assert "渔光互补项目" in generate_markdown_report(case)


def test_ground_mount_rejection_prevents_single_image_and_disaster_work():
    vision = FixedGroupVision(scene="ground_mount")
    case = UnderwritingPipeline(ocr_provider=MockOcrProvider(), vision_provider=vision).run(
        case_id="ground-stop", project=project(),
        materials=[image("first"), image("second")],
    )
    assert case.decision.decision.value == "recommend_reject"
    assert case.stage_reviews[1].rule_ids == ["INSTALLATION-TYPE-001"]
    assert case.stage_reviews[1].stage == "installation_type"
    assert case.stage_reviews[1].material_ids == ["first", "second"]
    assert vision.single_calls == []
    assert case.package_assessment is None and case.catastrophe_assessment is None
    assert "独立地面基础承载" in generate_markdown_report(case)
    assert "原图可见承载连接关系" in generate_markdown_report(case)


def test_unknown_scene_is_not_rejected_and_independent_checks_continue():
    vision = FixedGroupVision(scene="unknown", status="unknown")
    case = UnderwritingPipeline(ocr_provider=MockOcrProvider(), vision_provider=vision).run(
        case_id="scene-unknown", project=project(), materials=[image("first")],
    )
    assert case.decision.decision.value == "request_more"
    assert vision.single_calls == ["first"]
    assert case.pre_catastrophe_outcome == "request_more"
    assert case.catastrophe_assessment is None


def test_out_of_scope_label_without_support_evidence_is_not_auto_rejected():
    analysis = InstallationAnalysis(provider="test", areas=[InstallationArea(
        area_id="candidate", scene_type="ground_mount", status="confirmed",
        material_ids=["first"], supporting_structure=None,
        evidence_text="模型仅给出地面式类别标签",
    )])
    review = RuleEngine().evaluate_installation_type(analysis)
    assert review.action == "manual_review"
    assert review.terminates_run is False


def test_multiple_installation_entries_require_project_level_review():
    analysis = InstallationAnalysis(provider="test", areas=[
        InstallationArea(
            area_id="roof", scene_type="building_roof", status="confirmed",
            material_ids=["first"], supporting_structure="建筑屋面",
            evidence_text="第一张图显示屋面组件",
        ),
        InstallationArea(
            area_id="ground", scene_type="ground_mount", status="confirmed",
            material_ids=["second"], supporting_structure="地面独立支架",
            evidence_text="第二张图显示地面支架",
        ),
    ])
    review = RuleEngine().evaluate_installation_type(analysis)
    assert review.action == "manual_review"
    assert review.terminates_run is False


def test_confirmed_carport_does_not_require_parking_keyword_in_summary():
    analysis = InstallationAnalysis(provider="test", areas=[InstallationArea(
        area_id="project", scene_type="carport_roof", status="confirmed",
        material_ids=["first"], supporting_structure="棚顶梁柱",
        evidence_text="组件安装在独立棚顶", usage=None,
    )])

    review = RuleEngine().evaluate_installation_type(analysis)

    assert review.action == "continue"


def test_equipment_photos_do_not_enter_installation_type_group():
    vision = FixedGroupVision(scene="building_roof")
    case = UnderwritingPipeline(ocr_provider=MockOcrProvider(), vision_provider=vision).run(
        case_id="equipment-is-context", project=project(), materials=[
            image("roof", MaterialCategory.PANORAMA),
            image("module-label", MaterialCategory.COMPONENT_NAMEPLATE),
            image("inverter-label", MaterialCategory.INVERTER_NAMEPLATE),
        ],
    )
    assert vision.group_material_ids == ["roof"]
    assert case.installation_analysis.areas[0].material_ids == ["roof"]
    assert case.stage_reviews[1].stage == "installation_type"
    assert case.stage_reviews[1].action == "continue"


def test_single_image_rejection_stops_unscheduled_images(monkeypatch):
    monkeypatch.setenv("PV_SINGLE_IMAGE_WORKERS", "2")
    vision = FixedGroupVision(reject_image=True)
    case = UnderwritingPipeline(ocr_provider=MockOcrProvider(), vision_provider=vision).run(
        case_id="image-stop", project=project(),
        materials=[image("first"), image("second"), image("third")],
    )
    assert case.decision.decision.value == "recommend_reject"
    assert set(vision.single_calls) == {"first", "second"}
    assert len([stage for stage in case.stage_reviews if stage.stage == "single_image"]) == 1
    assert case.stage_reviews[-1].finding_ids == ["first:shade"]
    assert case.package_assessment is None
    assert any("并发任务已启动" in (trace.detail or "") for trace in case.processing_trace)
    assert any("third：因已确认拒保而未启动单图识别" == trace.detail
               for trace in case.processing_trace)


def test_single_image_calls_overlap_and_reviews_keep_upload_order(monkeypatch):
    monkeypatch.setenv("PV_SINGLE_IMAGE_WORKERS", "2")
    rendezvous = Barrier(2, timeout=3)
    lock = Lock()

    class ConcurrentVision(FixedGroupVision):
        def __init__(self):
            super().__init__()
            self.active = 0
            self.peak = 0

        def analyze(self, item):
            with lock:
                self.active += 1
                self.peak = max(self.peak, self.active)
            try:
                if item.material.material_id in {"first", "second"}:
                    rendezvous.wait()
                return super().analyze(item)
            finally:
                with lock:
                    self.active -= 1

    vision = ConcurrentVision()
    case = UnderwritingPipeline(ocr_provider=MockOcrProvider(), vision_provider=vision).run(
        case_id="parallel-images", project=project(),
        materials=[image("first"), image("second"), image("third")],
    )
    assert vision.peak == 2
    assert [stage.material_ids for stage in case.stage_reviews
            if stage.stage == "single_image"] == [["first"], ["second"], ["third"]]


def test_carport_checklist_excludes_rooftop_only_materials():
    analysis = InstallationAnalysis(provider="test", areas=[InstallationArea(
        area_id="carport", scene_type="carport_roof", status="confirmed",
        material_ids=["first"], evidence_text="棚顶与停车位可见",
    )])
    checks = build_applicable_checks(analysis, profile="full_intake")
    by_key = {(item.area_id, item.key): item for item in checks}
    assert by_key["carport", "workshop"].applicability == "not_applicable"
    assert by_key["carport", "edge"].applicability == "not_applicable"
    assert "梁柱" in by_key["carport", "support"].evidence_requirement


def test_full_intake_treats_grid_and_inverter_as_optional_evidence():
    analysis = InstallationAnalysis(provider="test", areas=[InstallationArea(
        area_id="project", scene_type="carport_roof", status="confirmed",
        material_ids=["first"], evidence_text="停车棚顶安装组件",
    )])
    checks = build_applicable_checks(analysis, profile="full_intake")
    by_key = {item.key: item for item in checks}
    assert not by_key["grid"].required_now
    assert not by_key["inverter"].required_now
    results = assess_checks(checks, [], [], [], analysis, failed_material_ids=set())
    optional = {item.key: item for item in results if item.key in {"grid", "inverter"}}
    assert {item.status for item in optional.values()} == {"not_provided"}
    assert all(item.customer_request is None and not item.rule_ids
               for item in optional.values())
    engine = RuleEngine(config=BusinessRulesConfig.load().model_copy(
        update={"package_requirement_profile": "full_intake"},
    ))
    package = engine.assess_package([], applicable_checks=checks)
    assert "grid_connection_document" not in package.missing_material_categories
    assert "inverter_nameplate" not in package.missing_material_categories


def test_single_image_generic_roof_does_not_override_group_scene():
    finding = RiskFinding(
        finding_id="connection:type", material_id="connection",
        category="installation_type", label="建筑屋顶式",
        detection_status="detected", severity="info", confidence=0.95,
        evidence_text="组件位于建筑屋面", provider="test", model="test",
        requires_manual_review=False,
    )
    review = RuleEngine().evaluate_material(
        image("connection", MaterialCategory.ROOF_CONNECTION).material,
        findings=[finding], project=project(
            project_type="rooftop", installation_type="color_steel_roof",
        ),
    )
    assert "DOC-CROSS-CHECK-001" not in review.triggered_rule_ids


def test_check_requires_relevant_evidence_not_an_unrelated_finding():
    analysis = InstallationAnalysis(provider="test", areas=[InstallationArea(
        area_id="roof", scene_type="building_roof", status="confirmed",
        material_ids=["connection"], evidence_text="屋面承载关系清楚",
    )])
    checks = build_applicable_checks(analysis, profile="full_intake")
    material = image("connection", MaterialCategory.ROOF_CONNECTION).material
    finding = RiskFinding(
        finding_id="connection:shade", material_id="connection",
        category="minor_shading", label="轻微遮挡", detection_status="detected",
        severity="low", confidence=0.9, evidence_text="边缘有局部阴影",
        provider="test", model="test", requires_manual_review=False,
    )
    results = assess_checks(checks, [material], [finding], [], analysis,
                            failed_material_ids=set())
    support = next(item for item in results if item.area_id == "roof" and item.key == "support")
    assert support.status == "unassessed"
    assert support.rule_ids == []
    assert support.customer_request is None
    assert "roof_connection_abnormal" in support.reason
    assert "不代表现场没有该类风险" in support.reason


def test_confirmed_secondary_use_is_candidate_without_automatic_pass():
    analysis = InstallationAnalysis(provider="test", areas=[InstallationArea(
        area_id="roof", scene_type="building_roof", status="confirmed",
        material_ids=["first"], evidence_text="可见建筑屋面",
    )])
    checks = build_applicable_checks(analysis, profile="full_intake")
    source = image("first")
    material = Material.model_validate({
        **source.material.model_dump(mode="python"),
        "additional_categories": ["roof_connection"],
    })
    results = assess_checks(checks, [material], [], [], analysis,
                            failed_material_ids=set())
    support = next(item for item in results if item.area_id == "roof" and item.key == "support")
    assert support.material_ids == ["first"]
    assert support.status == "unassessed"
    assert support.customer_request is None


def test_explicitly_missing_filing_field_is_unusable_not_model_omission():
    analysis = InstallationAnalysis(provider="test", areas=[])
    checks = build_applicable_checks(analysis, profile="full_intake")
    field = OcrField(
        field_id="filing:site_address", material_id="filing",
        field_name="site_address", raw_value=None, normalized_value=None,
        value_status="missing", confidence=0.99,
        provider="test", model="test", evidence_text="备案页未显示建设地点",
    )
    results = assess_checks(checks, [certificate().material], [], [field], analysis,
                            failed_material_ids=set())
    filing = next(item for item in results if item.key == "filing")
    assert filing.status == "unusable"
    assert filing.rule_ids == ["CHECK-EVIDENCE-UNUSABLE-001"]
    assert filing.ocr_field_ids == ["filing:site_address"]
    assert "site_address" in filing.customer_request


def test_image_quality_finding_makes_check_unusable_with_evidence():
    analysis = InstallationAnalysis(provider="test", areas=[InstallationArea(
        area_id="roof", scene_type="building_roof", status="confirmed",
        material_ids=["connection"], evidence_text="建筑屋面可见",
    )])
    checks = build_applicable_checks(analysis, profile="full_intake")
    finding = RiskFinding(
        finding_id="connection:quality", material_id="connection",
        category="image_quality", label="连接节点模糊",
        detection_status="detected", severity="medium", confidence=0.9,
        evidence_text="锚固螺栓无法辨认", provider="test", model="test",
        requires_manual_review=False,
    )
    results = assess_checks(
        checks, [image("connection", MaterialCategory.ROOF_CONNECTION).material],
        [finding], [], analysis, failed_material_ids=set(),
    )
    support = next(item for item in results if item.area_id == "roof" and item.key == "support")
    assert support.status == "unusable"
    assert support.rule_ids == ["CHECK-EVIDENCE-UNUSABLE-001"]
    assert support.finding_ids == ["connection:quality"]
    assert "锚固螺栓无法辨认" in support.reason


def test_missing_start_date_is_warning_with_original_evidence_in_report():
    vision = FixedGroupVision()
    source = image("first")
    panorama = MaterialInput(material=Material.model_validate({
        **source.material.model_dump(mode="python"),
        "watermark_status": "present", "captured_at": "2026-10-10T09:00:00+08:00",
        "longitude": 113.3, "latitude": 23.1,
    }), content=source.content)
    case = UnderwritingPipeline(ocr_provider=MockOcrProvider(), vision_provider=vision).run(
        case_id="missing-start-date", project=project(), materials=[panorama],
    )
    assert case.project.proposed_start_date is None
    assert any("拟起保日期未提供" in item for item in case.decision.warnings)
    assert not any("拟起保日期" in item for item in case.decision.missing_requirements)
    assert "15日时效暂无法核验" in generate_markdown_report(case)


def test_geometry_runs_only_after_first_review_allows_it():
    vision = CompleteVision()
    panoramas = []
    for index in range(5):
        source = image(f"panorama-{index}")
        panoramas.append(MaterialInput(material=Material.model_validate({
            **source.material.model_dump(mode="python"),
            "capture_view": "front_level" if index == 0 else
            "overhead" if index == 1 else "unknown",
            "watermark_status": "present", "captured_at": "2026-10-10T09:00:00+08:00",
            "longitude": 113.3, "latitude": 23.1,
        }), content=source.content))
    pipeline = UnderwritingPipeline(
        ocr_provider=CompleteFilingOcr(), vision_provider=vision,
    )
    case = pipeline.run(
        case_id="complete-first-review",
        project=project(site_address="示例地址", longitude=113.3, latitude=23.1,
                        component_model="DEMO-MODULE", proposed_start_date="2026-10-12"),
        materials=[certificate(), *panoramas],
    )
    assert case.pre_catastrophe_outcome == "continue"
    assert vision.geometry_calls == 1
    assert case.geometry_analysis is not None
    assert case.stage_reviews[-1].action == "continue"


    # Four panoramas remain sufficient; package size does not block later stages.
    vision.geometry_calls = 0
    incomplete = pipeline.run(
        case_id="incomplete-first-review",
        project=project(site_address="示例地址", longitude=113.3, latitude=23.1,
                        component_model="DEMO-MODULE", proposed_start_date="2026-10-12"),
        materials=[certificate(), *panoramas[:4]],
    )
    assert incomplete.pre_catastrophe_outcome == "continue"
    assert vision.geometry_calls == 1
    assert incomplete.geometry_analysis is not None

    # Missing catastrophe-only parameters narrow the screen without blocking rejection checks.
    vision.geometry_calls = 0
    blocked = pipeline.run(
        case_id="missing-component-before-screening",
        project=project(site_address="示例地址", longitude=113.3, latitude=23.1,
                        proposed_start_date="2026-10-12"),
        materials=[certificate(), *panoramas],
    )
    assert blocked.pre_catastrophe_outcome == "continue"
    assert vision.geometry_calls == 1
    assert blocked.catastrophe_assessment is None
    assert any("组件型号缺失" in warning for warning in blocked.decision.warnings)
    assert not blocked.decision.missing_requirements

    vision.geometry_calls = 0
    no_location = pipeline.run(
        case_id="missing-location-before-screening",
        project=project(component_model="DEMO-MODULE", proposed_start_date="2026-10-12"),
        materials=[certificate(), *panoramas],
    )
    assert no_location.pre_catastrophe_outcome == "continue"
    assert vision.geometry_calls == 1
    assert no_location.catastrophe_assessment is None
    assert no_location.location_assessment.missing_requirements
    assert not no_location.decision.missing_requirements


def test_geometry_retries_one_transient_provider_failure():
    class FlakyGeometry(CompleteVision):
        def estimate_geometry(self, materials, installation):
            self.geometry_calls += 1
            if self.geometry_calls == 1:
                raise ProviderError("temporary model response failure") from httpx.ReadTimeout("test timeout")
            return VisionProvider.estimate_geometry(self, materials, installation)

    vision = FlakyGeometry()
    photos = []
    for index in range(5):
        source = image(f"retry-panorama-{index}")
        photos.append(MaterialInput(material=source.material.model_copy(update={
            "watermark_status": WatermarkStatus.PRESENT,
            "captured_at": datetime.fromisoformat("2026-10-10T09:00:00+08:00"),
            "longitude": 113.3, "latitude": 23.1,
        }), content=source.content))
    case = UnderwritingPipeline(
        ocr_provider=CompleteFilingOcr(), vision_provider=vision,
    ).run(
        case_id="geometry-retry",
        project=project(site_address="示例地址", longitude=113.3, latitude=23.1,
                        component_model="DEMO-MODULE", proposed_start_date="2026-10-12"),
        materials=[certificate(), *photos],
    )
    trace = next(item for item in case.processing_trace if item.step.value == "geometry_group")
    assert vision.geometry_calls == 2
    assert trace.status.value == "success"
    assert "重试成功" in trace.detail
    assert "SYS-PROVIDER-FAILURE" not in case.decision.decisive_rule_ids


def test_risk_warning_continues_later_analysis_but_final_decision_needs_review():
    class WarningVision(CompleteVision):
        def analyze(self, item):
            findings = super().analyze(item)
            return [finding.model_copy(update={
                "detection_status": type(finding.detection_status)("detected"),
                "severity": type(finding.severity)("low"),
                "evidence_text": "组件边缘有少量树影",
            }) if finding.category.value == "minor_shading" else finding
                for finding in findings]

    vision = WarningVision()
    case = UnderwritingPipeline(
        ocr_provider=CompleteFilingOcr(), vision_provider=vision,
    ).run(
        case_id="warning-continues",
        project=project(site_address="示例地址", longitude=113.3, latitude=23.1,
                        component_model="DEMO-MODULE", proposed_start_date="2026-10-12"),
        materials=[certificate(), image("first")],
    )
    assert case.pre_catastrophe_outcome == "continue"
    assert vision.geometry_calls == 1
    assert case.geometry_analysis is not None
    assert case.decision.decision.value == "manual_review"


def test_two_view_shading_review_keeps_wind_snow_quantification():
    class LowShadeVision(CompleteVision):
        def analyze(self, item):
            return [finding.model_copy(update={
                "detection_status": type(finding.detection_status)("detected"),
                "severity": type(finding.severity)("low"),
                "evidence_text": "树叶遮住组件边缘",
            }) if finding.category.value == "minor_shading" else finding
                for finding in super().analyze(item)]

    front = image("front")
    front.material.capture_view = CaptureView.FRONT_LEVEL
    overhead = image("overhead")
    overhead.material.capture_view = CaptureView.OVERHEAD
    case = UnderwritingPipeline(
        ocr_provider=CompleteFilingOcr(), vision_provider=LowShadeVision(),
    ).run(case_id="two-view-shading", project=project(
        site_address="示例地址", longitude=113.3, latitude=23.1,
        component_model="DEMO-MODULE", proposed_start_date="2026-10-12",
    ), materials=[certificate(), front, overhead])
    assert case.decision.decision.value == "manual_review"
    assert "IMG-SHADING-MULTIVIEW-REVIEW-001" in case.decision.decisive_rule_ids
    assert case.pv_load_screening_assessment is not None
    assert case.pre_catastrophe_outcome == "continue"
    assert sum(stage.action == "manual_review" for stage in case.stage_reviews
               if stage.stage == "single_image") == 2


def test_group_provider_sends_both_original_images_and_retains_sources():
    def handler(request):
        body = json.loads(request.content)
        content = body["messages"][0]["content"]
        image_urls = [item["image_url"]["url"] for item in content
                      if item["type"] == "image_url"]
        assert len(image_urls) == 2
        assert all(url.startswith("data:image/jpeg;base64,") for url in image_urls)
        assert any("first" in item["text"] for item in content if item["type"] == "text")
        assert any("不要机械地把所有 bbox 都设为 null" in item["text"]
                   for item in content if item["type"] == "text")
        assert any("备案第1页" in item["text"] and "filing:project_name" in item["text"]
                   for item in content if item["type"] == "text")
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
            "areas": [{"area_id": "project", "scene_type": "ground_mount",
                       "status": "confirmed", "material_ids": ["first", "second"],
                       "supporting_structure": "地面支架", "usage": None,
                       "evidence_text": "两张原图显示独立地面基础", "pending_question": None,
                       "evidence_regions": [{"material_id": "first", "bbox": {
                           "x_min": 0.1, "y_min": 0.2, "x_max": 0.8, "y_max": 0.9,
                       }, "coordinate_space": "normalized_0_1",
                           "description": "组件下方可见地面基础"}]}],
            "notes": [], "filing_field_ids": ["filing:project_name"],
        }, ensure_ascii=False)}}]})

    provider = CompatibleVisionProvider(
        VlmSettings(base_url="https://example.test/v1", api_key="test", model="test-model"),
        transport=httpx.MockTransport(handler),
    )
    result = provider.analyze_installation([image("first"), image("second")],
        filing_fields=[OcrField(
            field_id="filing:project_name", material_id="filing",
            field_name="project_name", raw_value="屋顶示例项目",
            normalized_value="屋顶示例项目", value_status="extracted",
            confidence=0.99, provider="test", model="test",
            evidence_text="备案第1页",
        )])
    assert result.areas[0].material_ids == ["first", "second"]
    assert result.areas[0].evidence_text == "两张原图显示独立地面基础"
    assert result.areas[0].evidence_regions[0].bbox.x_min == 0.1
    assert result.areas[0].evidence_regions[0].bbox.coordinate_space == "normalized_0_1"
    assert result.filing_field_ids == ["filing:project_name"]


def test_group_region_accepts_normalized_bbox_without_repeating_coordinate_space():
    analysis = InstallationAnalysis.model_validate({
        "provider": "test", "areas": [{
            "area_id": "project", "scene_type": "floating", "status": "confirmed",
            "evidence_text": "组件与水面承载关系", "material_ids": ["photo"],
            "evidence_regions": [{
                "material_id": "photo", "description": "水面上的光伏阵列",
                "bbox": {"x_min": 0.1, "y_min": 0.2, "x_max": 0.8, "y_max": 0.9},
            }],
        }],
    })
    assert analysis.areas[0].evidence_regions[0].bbox.coordinate_space == "normalized_0_1"


def test_group_provider_retries_missing_boxes_without_changing_scene_judgment():
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        if len(calls) == 1:
            payload = {"areas": [{"area_id": "project", "scene_type": "floating",
                                  "status": "confirmed", "material_ids": ["first"],
                                  "evidence_text": "组件位于水面",
                                  "evidence_regions": [{"material_id": "first",
                                                        "bbox": None,
                                                        "description": "组件与水面相邻"}]}]}
        else:
            payload = {"regions": [{"evidence_index": 0,
                                    "material_id": "first",
                                    "bbox": {"x_min": .12, "y_min": .2,
                                             "x_max": .82, "y_max": .9}}]}
        return httpx.Response(200, json={"choices": [{"message": {
            "content": json.dumps(payload, ensure_ascii=False),
        }}]})

    provider = CompatibleVisionProvider(
        VlmSettings(base_url="https://example.test/v1", api_key="test", model="test-model"),
        transport=httpx.MockTransport(handler),
    )
    result = provider.analyze_installation([image("first")])
    assert len(calls) == 2
    assert result.areas[0].scene_type == "floating"
    assert result.areas[0].evidence_regions[0].bbox.x_min == .12
    assert result.areas[0].evidence_regions[0].bbox.coordinate_space == "normalized_0_1"


def test_installation_group_assesses_aisle_in_the_same_multiphoto_request():
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        content = body["messages"][0]["content"]
        assert [item["text"] for item in content if item["type"] == "text"
                and item["text"].startswith("图片编号：")] == [
                    "图片编号：overview", "图片编号：roof"]
        payload = {
            "areas": [{"area_id": "project", "scene_type": "building_roof",
                       "status": "confirmed", "material_ids": ["overview", "roof"],
                       "evidence_text": "全景与近景显示建筑屋顶组件",
                       "evidence_regions": [{"material_id": "overview",
                                             "description": "屋顶组件及承载关系",
                                             "bbox": {"x_min": .1, "y_min": .2,
                                                      "x_max": .8, "y_max": .9}}]}],
            "aisle_assessment": {
                "status": "detected", "material_id": "roof",
                "supporting_material_ids": ["overview", "roof"],
                "cross_image_reasoning": "组件排布相同，确认两图拍摄同一通道",
                "confidence": .91, "severity": "medium",
                "evidence_text": "屋顶近景可见线缆占据通道",
                "bbox": {"x_min": .62, "y_min": .42,
                         "x_max": .83, "y_max": .72},
            },
        }
        return httpx.Response(200, json={"choices": [{"message": {
            "content": json.dumps(payload, ensure_ascii=False),
        }}]})

    provider = CompatibleVisionProvider(
        VlmSettings(base_url="https://example.test/v1", api_key="test", model="test-model"),
        transport=httpx.MockTransport(handler),
    )
    result = provider.analyze_installation([
        image("overview"), image("roof", MaterialCategory.ROOF_CONNECTION),
    ])
    assert len(calls) == 1
    assert result.areas[0].scene_type.value == "building_roof"
    assert result.aisle_assessment.material_id == "roof"
    assert result.aisle_assessment.bbox.x_min == .62
    text_parts = [item["text"] for item in calls[0]["messages"][0]["content"]
                  if item["type"] == "text"]
    assert "安装类型" in text_parts[0]
    assert "【组图通道阻塞观察】" in text_parts[1]


def test_group_response_without_passage_does_not_invent_uncertain_assessment():
    def handler(_request):
        payload = {"areas": [{
            "area_id": "project", "scene_type": "building_roof",
            "status": "confirmed", "material_ids": ["overview", "roof"],
            "evidence_text": "全景与近景显示建筑屋顶组件",
        }]}
        return httpx.Response(200, json={"choices": [{"message": {
            "content": json.dumps(payload, ensure_ascii=False),
        }}]})

    provider = CompatibleVisionProvider(
        VlmSettings(base_url="https://example.test/v1", api_key="test", model="test-model"),
        transport=httpx.MockTransport(handler),
    )
    result = provider.analyze_installation([
        image("overview"), image("roof", MaterialCategory.ROOF_CONNECTION),
    ])
    assert result.aisle_assessment is None


def test_clear_group_passage_does_not_create_an_inspection_finding():
    class CombinedVision(FixedGroupVision):
        def analyze_installation(self, materials, *, filing_fields=None):
            result = super().analyze_installation(materials, filing_fields=filing_fields)
            return InstallationAnalysis.model_validate({**result.model_dump(), "aisle_assessment": {
                "status": "not_detected", "material_id": "overview",
                "supporting_material_ids": ["overview", "roof"],
                "cross_image_reasoning": "全景看到通道，屋顶近景只展示节点",
                "confidence": .9, "severity": "info",
                "evidence_text": "所覆盖的通道段清晰且未见阻塞", "bbox": None,
            }})

        def analyze(self, item):
            if item.material.category is not MaterialCategory.ROOF_CONNECTION:
                return []
            return [RiskFinding(
                finding_id="roof:aisle:single", material_id="roof",
                category="aisle_obstruction", label="近景未拍到通道",
                detection_status="uncertain", severity="unknown", confidence=.8,
                evidence_text="屋顶节点近景未显示通道", provider=self.name,
                model=self.model_name, requires_manual_review=True,
            )]

    case = UnderwritingPipeline(
        ocr_provider=MockOcrProvider(), vision_provider=CombinedVision(),
    ).run(case_id="combined-aisle", project=project(), materials=[
        image("overview"), image("roof", MaterialCategory.ROOF_CONNECTION),
    ])
    aisle = [item for item in case.findings if item.category.value == "aisle_obstruction"]
    assert aisle == []
    assert not any(stage.stage == "aisle_group" for stage in case.stage_reviews)
    assert not any(trace.step.value == "aisle_group" for trace in case.processing_trace)


def test_missing_passage_view_does_not_create_a_risk_warning():
    class NoPassageVision(FixedGroupVision):
        def analyze_installation(self, materials, *, filing_fields=None):
            result = super().analyze_installation(materials, filing_fields=filing_fields)
            return InstallationAnalysis.model_validate({**result.model_dump(), "aisle_assessment": {
                "status": "uncertain", "material_id": "roof",
                "supporting_material_ids": ["overview", "roof"],
                "cross_image_reasoning": "照片只展示整体外观与局部支架连接处",
                "confidence": .8, "severity": "unknown",
                "evidence_text": "没有拍到完整通道，无法判断是否通畅", "bbox": None,
            }})

    case = UnderwritingPipeline(
        ocr_provider=MockOcrProvider(), vision_provider=NoPassageVision(),
    ).run(case_id="no-passage", project=project(), materials=[
        image("overview"), image("roof", MaterialCategory.ROOF_CONNECTION),
    ])
    assert not any(item.category.value == "aisle_obstruction" for item in case.findings)
    assert not any("通道" in reason for review in case.material_reviews for reason in review.reasons)


def test_upload_keeps_duplicate_relation_without_counting_second_image():
    buffer = BytesIO()
    Image.new("RGB", (64, 64), "white").save(buffer, format="PNG")
    data = buffer.getvalue()
    manifest = RealAnalyzeManifest(
        case_id="duplicate", project=project(), materials=[
            {"material_id": "first", "category": "panorama", "category_source": "automatic"},
            {"material_id": "second", "category": "panorama", "category_source": "manual"},
        ],
    )
    inputs = _build_material_inputs(
        manifest=manifest,
        uploaded_files=[("first.png", "image/png", data),
                        ("second.png", "image/png", data)],
    )
    assert inputs[1].material.duplicate_of_material_id == "first"
    case = UnderwritingPipeline(
        ocr_provider=MockOcrProvider(), vision_provider=FixedGroupVision(),
    ).run(case_id="duplicate", project=project(), materials=inputs)
    assert case.package_assessment.image_count == 1
    assert case.installation_analysis.areas[0].material_ids == ["first"]


def test_upload_preserves_manually_confirmed_secondary_photo_use():
    buffer = BytesIO()
    Image.new("RGB", (64, 64), "white").save(buffer, format="PNG")
    manifest = RealAnalyzeManifest(
        case_id="secondary-use", project=project(), materials=[{
            "material_id": "first", "category": "panorama",
            "additional_categories": ["roof_connection"],
            "category_source": "manual",
        }],
    )
    inputs = _build_material_inputs(
        manifest=manifest,
        uploaded_files=[("first.png", "image/png", buffer.getvalue())],
    )
    assert inputs[0].material.additional_categories == [MaterialCategory.ROOF_CONNECTION]
