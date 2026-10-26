import re
from typing import ClassVar
from .quality_evidence import quality_evidence_conflicts

from app.contracts import (
    ApplicableCheck,
    CaptureView,
    DetectionStatus,
    EnvironmentRelation,
    InstallationAnalysis,
    InstallationSceneType,
    InstallationType,
    Material,
    MaterialCategory,
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
    StageReview,
    WatermarkStatus,
)

from .config import BusinessRulesConfig

REJECT_ENVIRONMENT_CATEGORIES = frozenset(
    {
        RiskCategory.AGRICULTURE_ENVIRONMENT,
        RiskCategory.FOREST_ENVIRONMENT,
        RiskCategory.LIVESTOCK_ENVIRONMENT,
        RiskCategory.FISHERY_ENVIRONMENT,
        RiskCategory.WATER_ADJACENT_ENVIRONMENT,
        RiskCategory.TIDAL_FLAT_ENVIRONMENT,
        RiskCategory.MOUNTAIN_ENVIRONMENT,
        RiskCategory.DESERTIFICATION_ENVIRONMENT,
    }
)

CRITICAL_ENVIRONMENT_CHECKS = REJECT_ENVIRONMENT_CATEGORIES
SUPPLEMENT_REQUIRED_CHECKS = frozenset({RiskCategory.IMAGE_QUALITY})
EXPECTED_CHECKS_BY_MATERIAL = {
    MaterialCategory.PANORAMA: CRITICAL_ENVIRONMENT_CHECKS
    | frozenset(
        {
            RiskCategory.IMAGE_QUALITY,
            RiskCategory.SEVERE_SHADING,
            RiskCategory.MINOR_SHADING,
        }
    ),
    MaterialCategory.ROOF_CONNECTION: frozenset(
        {
            RiskCategory.INSTALLATION_TYPE,
            RiskCategory.ROOF_CONNECTION_ABNORMAL,
        }
        | SUPPLEMENT_REQUIRED_CHECKS
    ),
    MaterialCategory.PARAPET: frozenset(
        {RiskCategory.MISSING_PARAPET_OR_GUARDRAIL, RiskCategory.DRAINAGE_ABNORMAL}
    ) | SUPPLEMENT_REQUIRED_CHECKS,
    MaterialCategory.WORKSHOP: frozenset(
        {RiskCategory.FLAMMABLE_MATERIAL, RiskCategory.HAZARDOUS_MATERIAL}
    ) | SUPPLEMENT_REQUIRED_CHECKS,
    MaterialCategory.COMBINER_BOX: frozenset(
        {
            RiskCategory.COMBINER_BOX_SEAL_ABNORMAL,
            RiskCategory.COMBINER_BOX_FUSE_ABNORMAL,
            RiskCategory.SURGE_PROTECTOR_ABNORMAL,
        }
    ) | SUPPLEMENT_REQUIRED_CHECKS,
    MaterialCategory.ELECTRICAL_GROUNDING: frozenset(
        {
            RiskCategory.ELECTRICAL_GROUNDING_ABNORMAL,
        }
    ) | SUPPLEMENT_REQUIRED_CHECKS,
    MaterialCategory.COMPONENT_NAMEPLATE: SUPPLEMENT_REQUIRED_CHECKS,
    MaterialCategory.INVERTER_NAMEPLATE: frozenset(
        {RiskCategory.INVERTER_ABNORMAL}
    ) | SUPPLEMENT_REQUIRED_CHECKS,
    MaterialCategory.COMPONENT_SURFACE: frozenset(
        {
            RiskCategory.MODULE_DAMAGE,
        }
    ) | SUPPLEMENT_REQUIRED_CHECKS,
    MaterialCategory.MONITORING_OPTIONAL: frozenset(
        {RiskCategory.MONITORING_EFFECTIVE_COVERAGE}
    ) | SUPPLEMENT_REQUIRED_CHECKS,
    MaterialCategory.OTHER: SUPPLEMENT_REQUIRED_CHECKS,
}
ADVANCED_CHECKS_BY_MATERIAL = {
    MaterialCategory.WORKSHOP: frozenset(
        {
            RiskCategory.DANGEROUS_PROCESS,
            RiskCategory.CLEANROOM,
            RiskCategory.FIRE_PROTECTION_ABSENT,
        }
    ),
    MaterialCategory.ELECTRICAL_GROUNDING: frozenset(
        {RiskCategory.UNPROTECTED_CABLE}
    ),
    MaterialCategory.COMPONENT_SURFACE: frozenset(
        {RiskCategory.MODULE_DISCOLORATION}
    ),
}
FILING_NEGATIONS = ("不涉及", "不包含", "不属于", "不开展", "不进行", "不是", "不含", "非", "无", "不")
OCR_MIN_CONFIDENCE = 0.65
RISK_CATEGORY_LABELS = {
    RiskCategory.AGRICULTURE_ENVIRONMENT: "农业环境",
    RiskCategory.FOREST_ENVIRONMENT: "林地环境",
    RiskCategory.LIVESTOCK_ENVIRONMENT: "畜牧环境",
    RiskCategory.FISHERY_ENVIRONMENT: "渔业环境",
    RiskCategory.WATER_ADJACENT_ENVIRONMENT: "涉水环境",
    RiskCategory.TIDAL_FLAT_ENVIRONMENT: "滩涂环境",
    RiskCategory.MOUNTAIN_ENVIRONMENT: "山地环境",
    RiskCategory.DESERTIFICATION_ENVIRONMENT: "沙化环境",
    RiskCategory.IMAGE_QUALITY: "图片清晰度",
    RiskCategory.SEVERE_SHADING: "严重遮挡",
    RiskCategory.MINOR_SHADING: "轻微遮挡",
    RiskCategory.AISLE_OBSTRUCTION: "检修通道阻塞",
    RiskCategory.INSTALLATION_TYPE: "安装类型",
    RiskCategory.ROOF_CONNECTION_ABNORMAL: "屋顶连接异常",
    RiskCategory.MISSING_PARAPET_OR_GUARDRAIL: "女儿墙或护栏缺失",
    RiskCategory.DRAINAGE_ABNORMAL: "排水异常",
    RiskCategory.FLAMMABLE_MATERIAL: "易燃物",
    RiskCategory.HAZARDOUS_MATERIAL: "危险品",
    RiskCategory.MODULE_DAMAGE: "组件可见破损",
    RiskCategory.INVERTER_ABNORMAL: "逆变器可见异常",
    RiskCategory.ELECTRICAL_GROUNDING_ABNORMAL: "接地外观异常",
    RiskCategory.COMBINER_BOX_SEAL_ABNORMAL: "汇流箱密封异常",
    RiskCategory.COMBINER_BOX_FUSE_ABNORMAL: "汇流箱熔断器异常",
    RiskCategory.SURGE_PROTECTOR_ABNORMAL: "浪涌保护器异常",
    RiskCategory.MONITORING_EFFECTIVE_COVERAGE: "监控有效覆盖",
    RiskCategory.UNPROTECTED_CABLE: "线缆裸露且无保护",
    RiskCategory.MODULE_DISCOLORATION: "组件变色",
    RiskCategory.DANGEROUS_PROCESS: "危险工艺",
    RiskCategory.CLEANROOM: "洁净车间",
    RiskCategory.FIRE_PROTECTION_ABSENT: "消防设施缺失或异常",
}
ENVIRONMENT_RELATION_DESCRIPTIONS = {
    EnvironmentRelation.PROJECT_SITE: "项目现场本身",
    EnvironmentRelation.OPERATIONAL_SURROUNDINGS: "项目邻近周边",
    EnvironmentRelation.DISTANT_BACKGROUND: "远处背景景物",
    EnvironmentRelation.UNCERTAIN: "现场关系不确定",
}


def _normalized_text(value: str | None) -> str:
    if value is None:
        return ""
    return "".join(value.casefold().split()).replace("，", ",")


def _filing_quote_in_raw(quote: str, raw: str) -> bool:
    return bool(quote and _normalized_text(quote) in _normalized_text(raw))


def _filing_quote_negated(quote: str, raw: str) -> bool:
    start = _normalized_text(raw).find(_normalized_text(quote))
    if start < 0:
        return False
    prefix = _normalized_text(raw)[max(0, start - 8):start]
    return any(term in prefix for term in FILING_NEGATIONS)


def _filing_quote_supports_under_panel_use(quote: str) -> bool:
    """Prevent a generic mention of plants from proving cultivation beneath PV panels."""
    panel_relation = ("板下", "组件下", "阵列下", "支架下", "光伏板下", "光伏组件下")
    agricultural_use = ("种植", "栽培", "耕作", "种粮", "种地", "农作", "农业生产")
    return any(term in quote for term in panel_relation) and any(
        term in quote for term in agricultural_use
    )


def _filing_quote_supports_unsupported_installation(quote: str) -> bool:
    """A charging station's location does not establish where PV modules sit."""
    pv_subject = ("光伏组件", "光伏板", "光伏阵列", "光伏方阵", "光伏电池板", "光伏电站", "光伏系统", "组件", "阵列")
    ground_mount = ("地面支架", "地面独立支架", "独立地面支架", "地面铺设", "地面架设",
                    "地面光伏方阵", "地面光伏电站", "地面安装光伏", "地面安装组件")
    roof_site = ("车棚", "棚顶", "屋顶", "屋面", "楼顶")
    for segment in re.split(r"[，,。；;]|以及|并且", quote):
        explicit_non_roof = (any(term in segment for term in ground_mount)
                             or ("山坡" in segment and "独立支架" in segment)
                             or ("水面" in segment and any(term in segment for term in ("浮体", "漂浮")))
                             or any(term in segment for term in ("建筑立面", "外墙")))
        if (any(term in segment for term in pv_subject)
                and explicit_non_roof
                and not any(term in segment for term in roof_site)):
            return True
    return False


def _filing_explicit_carport_site(raw: str) -> bool:
    """Recognize an actual PV carport mention, not a parking-lot location."""
    carport_terms = ("光伏车棚", "车棚顶光伏", "车棚顶部光伏", "车棚顶安装光伏",
                     "车棚顶部铺设组件", "车棚顶铺设组件")
    return any(term in raw and not _filing_quote_negated(term, raw)
               for term in carport_terms)


def _installation_only_filing_uncertainty(reason: str) -> bool:
    installation_terms = ("安装类型", "安装方式", "安装位置", "安装载体", "组件位置", "组件安装", "是否屋顶", "是否车棚")
    other_risk_terms = ("农业", "林业", "畜牧", "渔业", "水体", "滩涂", "山地", "沙漠", "集中式", "禁投")
    return (any(term in reason for term in installation_terms)
            and not any(term in reason for term in other_risk_terms))


def _normalize_filing_field(field: OcrField) -> OcrField:
    """Remove unsupported model conclusions before rules and reports consume them."""
    assessment = field.filing_assessment
    if assessment is None or field.field_name not in {"project_name", "construction_content"}:
        return field
    unsupported_installation = (
        assessment.status == "confirmed"
        and assessment.prohibition_type == "unsupported_installation"
        and not _filing_quote_supports_unsupported_installation(assessment.evidence_quote or "")
    )
    installation_unknown = (
        assessment.status == "uncertain"
        and assessment.generation_mode != "centralized"
        and _installation_only_filing_uncertainty(assessment.reason)
    )
    unsupported_other_site = (
        assessment.status == "not_found"
        and assessment.installation_site == "other"
        and not _filing_quote_supports_unsupported_installation(field.raw_value or "")
    )
    explicit_carport = (field.field_name == "construction_content"
                        and _filing_explicit_carport_site(field.raw_value or "")
                        and not _filing_quote_supports_unsupported_installation(field.raw_value or "")
                        and assessment.status != "confirmed")
    if not (unsupported_installation or installation_unknown
            or unsupported_other_site or explicit_carport):
        return field
    carport_site = (field.field_name == "construction_content"
                    and _filing_explicit_carport_site(field.raw_value or "")
                    and not _filing_quote_supports_unsupported_installation(field.raw_value or ""))
    reason = ("备案建设内容记载光伏车棚；现场组件承载关系仍由照片核验"
              if carport_site else
              "备案原文未明确说明光伏组件采用禁投安装方式；安装类型交由现场照片核验"
              if unsupported_installation or unsupported_other_site else
              "备案原文未写明光伏组件安装类型；安装类型交由现场照片核验")
    clean = assessment.model_copy(update={
        "status": "not_found", "prohibition_type": None,
        "generation_mode": "unknown" if unsupported_installation else assessment.generation_mode,
        "installation_site": "carport_roof" if carport_site else "unknown", "reason": reason,
        "evidence_quote": None, "evidence_bbox": None,
    })
    return field.model_copy(update={"filing_assessment": clean})


def _filing_field_evidence(field: OcrField) -> str:
    location = f"材料 {field.material_id}"
    if field.page_number:
        location += f" 第 {field.page_number} 页"
    assessment = field.filing_assessment
    if assessment and assessment.evidence_bbox:
        box = assessment.evidence_bbox
        location += (f" 位置 ({box.x_min:.2f}, {box.y_min:.2f})"
                     f"-({box.x_max:.2f}, {box.y_max:.2f})")
    quote = assessment.evidence_quote if assessment else None
    reason = assessment.reason if assessment else None
    return (f"{location}；字段 {field.field_name}；"
            f"原文：{field.raw_value or field.normalized_value or '未提取'}"
            + (f"；违规片段：{quote}" if quote else "")
            + (f"；判断理由：{reason}" if reason else ""))


def _installation_evidence(areas) -> list[str]:
    evidence: list[str] = []
    for area in areas:
        evidence.append(area.evidence_text)
        for region in area.evidence_regions:
            position = (
                f"，定位 ({region.bbox.x_min:.2f}, {region.bbox.y_min:.2f})"
                f"—({region.bbox.x_max:.2f}, {region.bbox.y_max:.2f})"
                if region.bbox is not None else ""
            )
            evidence.append(f"图片 {region.material_id}：{region.description}{position}")
    return evidence


class RuleEngine:
    """将可追溯的 OCR 和视觉事实映射为首期核保动作。

    规则编号和动作是比赛首期的保守建议；费率和承保授权仍由业务确认。
    """

    MATERIAL_QUALITY_RULE_ID = "MAT-QUALITY-001"
    ENVIRONMENT_REJECT_RULE_ID = "ENV-EXCLUDED-001"
    ENVIRONMENT_COVERAGE_RULE_ID = "ENV-COVERAGE-001"
    ENVIRONMENT_RELATION_REVIEW_RULE_ID = "ENV-SITE-RELATION-REVIEW-001"
    ENVIRONMENT_BACKGROUND_RULE_ID = "ENV-BACKGROUND-CONTEXT-001"
    FILING_PROHIBITED_RULE_ID = "DOC-FILING-EXCLUDED-001"
    FILING_NAME_CONFLICT_RULE_ID = "DOC-FILING-NAME-CONFLICT-001"
    CROSS_CHECK_RULE_ID = "DOC-CROSS-CHECK-001"
    WATERMARK_RULE_ID = "IMG-WATERMARK-001"
    WATERMARK_DATE_RULE_ID = "IMG-WATERMARK-DATE-001"
    FINDING_WARNING_RULE_ID = "IMG-FINDING-WARNING-001"
    SEVERE_SHADING_RULE_ID = "IMG-SHADING-SEVERE-001"
    INSTALLATION_TYPE_RULE_ID = "INSTALLATION-TYPE-001"
    INSTALLATION_UNCERTAIN_RULE_ID = "INSTALLATION-TYPE-REVIEW-001"

    _ACTION_PRIORITY: ClassVar[dict[MaterialReviewAction, int]] = {
        MaterialReviewAction.PASS: 0,
        MaterialReviewAction.NOT_APPLICABLE: 0,
        MaterialReviewAction.WARNING: 1,
        MaterialReviewAction.CONDITIONAL: 2,
        MaterialReviewAction.SURCHARGE: 3,
        MaterialReviewAction.REQUEST_MORE: 4,
        MaterialReviewAction.RECOMMEND_REJECT: 5,
    }

    def __init__(self, config: BusinessRulesConfig | None = None) -> None:
        self.config = config or BusinessRulesConfig.load()

    def normalize_filing_fields(self, fields: list[OcrField]) -> list[OcrField]:
        return [_normalize_filing_field(field) for field in fields]

    def corroborate_panorama_shading(
        self, materials: list[Material], findings: list[RiskFinding],
        reviews: list[MaterialReview],
    ) -> list[MaterialReview]:
        """Two distinct views of the same visible shading require an extent review."""
        panoramas = {item.material_id: item for item in materials
                     if item.category is MaterialCategory.PANORAMA}
        detected = {item.material_id: item for item in findings
                    if item.material_id in panoramas
                    and item.category is RiskCategory.MINOR_SHADING
                    and item.detection_status is DetectionStatus.DETECTED}
        views = {panoramas[mid].capture_view for mid in detected}
        if not {CaptureView.FRONT_LEVEL, CaptureView.OVERHEAD}.issubset(views):
            return reviews
        reason = ("正面与俯拍均标记组件表面的局部遮挡；具体遮挡范围及影响"
                  "仍需人工核实")
        result = []
        for review in reviews:
            if (review.material_id not in detected
                    or review.action is MaterialReviewAction.RECOMMEND_REJECT):
                result.append(review)
                continue
            result.append(review.model_copy(update={
                "triggered_rule_ids": list(dict.fromkeys([
                    *review.triggered_rule_ids, "IMG-SHADING-MULTIVIEW-REVIEW-001",
                ])),
                "reasons": list(dict.fromkeys([*review.reasons, reason])),
                "requires_manual_review": True,
            }))
        return result

    def evaluate_filing_stage(
        self, materials: list[Material], fields: list[OcrField], project: ProjectInfo,
    ) -> StageReview:
        """Validate the model's filing findings against source text before stopping."""
        fields = self.normalize_filing_fields(fields)
        filing_ids = {item.material_id for item in materials
                      if item.category is MaterialCategory.FILING_CERTIFICATE}
        for field in fields:
            if field.field_name == "document_type" and field.value_status is OcrValueStatus.EXTRACTED and (
                field.normalized_value or field.raw_value or ""
            ).strip() == "filing_certificate":
                filing_ids.add(field.material_id)
        relevant = [field for field in fields if field.material_id in filing_ids
                    and field.field_name in {"project_name", "construction_content"}]
        usable = [field for field in relevant
                  if field.value_status is OcrValueStatus.EXTRACTED
                  and field.confidence >= OCR_MIN_CONFIDENCE
                  and field.raw_value]
        names = {_normalized_text(field.raw_value)
                 for field in usable if field.field_name == "project_name"}
        contents = {_normalized_text(field.raw_value)
                    for field in usable if field.field_name == "construction_content"}
        questions: list[str] = []
        if len(names) > 1 or (
            project.project_name and names
            and _normalized_text(project.project_name) not in names
        ):
            conflicting_names = [field for field in usable
                                 if field.field_name == "project_name"]
            return StageReview(
                stage="filing", action="recommend_reject",
                rule_ids=[self.FILING_NAME_CONFLICT_RULE_ID],
                reasons=["备案项目名称互相冲突或与申报项目名称不一致，建议拒保"],
                material_ids=list(dict.fromkeys(field.material_id
                                                for field in conflicting_names)),
                ocr_field_ids=[field.field_id for field in conflicting_names],
                evidence_texts=[_filing_field_evidence(field)
                                for field in conflicting_names],
                terminates_run=True,
            )
        if len(contents) > 1:
            questions.append("备案建设内容存在多份不同表述，需核验有效版本和变更记录")
        matches: list[OcrField] = []
        for field in usable:
            raw = field.raw_value or ""
            assessment = field.filing_assessment
            if assessment is None:
                questions.append(f"{field.field_name}缺少模型禁投判断，需复核")
                continue
            if assessment.status == "uncertain":
                questions.append(f"{field.field_name}判断存疑：{assessment.reason}")
                continue
            if assessment.status == "not_found":
                if assessment.generation_mode == "centralized" or assessment.installation_site == "other":
                    questions.append(f"{field.field_name}的建设方式与未发现禁投事实的判断不一致")
                continue
            quote = assessment.evidence_quote or ""
            if not _filing_quote_in_raw(quote, raw):
                questions.append(f"{field.field_name}的违规片段与提取原文不一致")
                continue
            if _filing_quote_negated(quote, raw):
                questions.append(f"{field.field_name}的违规片段附近含否定表述")
                continue
            if (assessment.prohibition_type == "under_panel_cultivation"
                    and not _filing_quote_supports_under_panel_use(quote)):
                questions.append("板下种植判断的原文片段未同时说明组件下方与农业用途")
                continue
            if (assessment.prohibition_type == "agriculture" and "农户" in quote
                    and not any(term in quote for term in (
                        "耕地", "农田", "种植", "栽培", "耕作", "农作", "农业", "农光", "大棚",
                    ))):
                questions.append("农户身份不足以证明项目涉农，需核对实际建设用途")
                continue
            matches.append(field)
        if filing_ids and not any(f.field_name == "project_name" for f in usable):
            questions.append("备案项目名称缺失或不可读")
        if (filing_ids and not matches
                and not any(f.field_name == "construction_content" for f in usable)):
            questions.append("备案建设内容缺失或不可读")
        name_matches = [field for field in matches if field.field_name == "project_name"]
        name_questions = any("项目名称" in question for question in questions)
        accepted_matches = (name_matches if name_matches and not name_questions
                            else matches if not questions else [])
        if accepted_matches:
            return StageReview(
                stage="filing", action="recommend_reject",
                rule_ids=[self.FILING_PROHIBITED_RULE_ID],
                reasons=["备案项目名称或建设内容明确命中已确认禁投条件"],
                material_ids=list(dict.fromkeys(field.material_id for field in accepted_matches)),
                ocr_field_ids=[field.field_id for field in accepted_matches],
                evidence_texts=[_filing_field_evidence(field) for field in accepted_matches],
                terminates_run=True,
            )
        if questions:
            return StageReview(
                stage="filing", action="manual_review",
                rule_ids=[self.CROSS_CHECK_RULE_ID], reasons=["备案关键事实尚不能确认禁投"],
                material_ids=list(filing_ids),
                ocr_field_ids=[field.field_id for field in relevant],
                evidence_texts=[_filing_field_evidence(field) for field in relevant],
                pending_questions=list(dict.fromkeys(questions)),
            )
        return StageReview(stage="filing", action="continue", material_ids=list(filing_ids),
                           ocr_field_ids=[field.field_id for field in usable],
                           evidence_texts=[_filing_field_evidence(field) for field in usable])

    def evaluate_installation_type(self, analysis: InstallationAnalysis) -> StageReview:
        if len(analysis.areas) != 1:
            return StageReview(
                stage="installation_type", action="manual_review",
                rule_ids=[self.INSTALLATION_UNCERTAIN_RULE_ID],
                reasons=["整单安装类型未形成唯一可靠结果"],
                material_ids=list(dict.fromkeys(
                    mid for item in analysis.areas for mid in item.material_ids
                )),
                ocr_field_ids=analysis.filing_field_ids,
                pending_questions=["复核本案光伏阵列的整单安装类型"],
            )
        excluded = {InstallationSceneType.GROUND_MOUNT, InstallationSceneType.FLOATING,
                    InstallationSceneType.OTHER_CANOPY, InstallationSceneType.BUILDING_FACADE,
                    InstallationSceneType.OTHER_KNOWN}
        result = analysis.areas[0]
        if (result.status == "confirmed" and result.scene_type in excluded
                and result.material_ids and result.supporting_structure):
            return StageReview(
                stage="installation_type", action="recommend_reject",
                rule_ids=[self.INSTALLATION_TYPE_RULE_ID],
                reasons=["本案光伏安装类型已确认不是建筑屋顶式或车棚顶式"],
                material_ids=result.material_ids,
                ocr_field_ids=analysis.filing_field_ids,
                evidence_texts=_installation_evidence([result]), terminates_run=True,
            )
        if (result.status != "confirmed" or result.scene_type is InstallationSceneType.UNKNOWN
                or not result.material_ids or not result.supporting_structure):
            return StageReview(
                stage="installation_type", action="manual_review",
                rule_ids=[self.INSTALLATION_UNCERTAIN_RULE_ID],
                reasons=["本案光伏安装类型尚不能确认"],
                material_ids=result.material_ids,
                ocr_field_ids=analysis.filing_field_ids,
                evidence_texts=_installation_evidence([result]),
                pending_questions=[result.pending_question or "核验组件承载结构、用途及原图证据"],
            )
        return StageReview(
            stage="installation_type", action="continue",
            material_ids=result.material_ids,
            ocr_field_ids=analysis.filing_field_ids,
            evidence_texts=_installation_evidence([result]),
        )

    def _expected_checks_for_material(
        self,
        category: MaterialCategory,
    ) -> frozenset[RiskCategory] | None:
        if category is MaterialCategory.MONITORING_OPTIONAL:
            return None
        expected = EXPECTED_CHECKS_BY_MATERIAL.get(category)
        if expected is None:
            return None
        if self.config.advanced_checks_enabled:
            expected |= ADVANCED_CHECKS_BY_MATERIAL.get(category, frozenset())
        policy = getattr(self, "rule_policy", None)
        return expected & policy.risks if policy and expected is not None else expected

    def enabled(self, key):
        policy = getattr(self, "rule_policy", None)
        return policy is None or policy.enabled(key)

    def assess_package(
        self,
        materials: list[Material],
        *,
        project_type: ProjectType | None = None,
        applicable_checks: list[ApplicableCheck] | None = None,
    ) -> PackageAssessment:
        document_categories = {
            MaterialCategory.FILING_CERTIFICATE,
            MaterialCategory.GRID_CONNECTION_DOCUMENT,
            MaterialCategory.PROJECT_DOCUMENT,
            MaterialCategory.EQUIPMENT_INVENTORY,
        }
        images = [
            item
            for item in materials
            if item.media_type in {"image/jpeg", "image/png"}
            and (item.category not in document_categories
                 or any(category not in document_categories
                        for category in item.additional_categories))
            and item.duplicate_of_material_id is None
            and item.quality_status is not MaterialQualityStatus.UNUSABLE
        ]
        panoramas = [
            item
            for item in materials
            if (item.category is MaterialCategory.PANORAMA
                or MaterialCategory.PANORAMA in item.additional_categories)
            and item.media_type in {"image/jpeg", "image/png"}
            and item.duplicate_of_material_id is None
        ]
        has_filing_certificate = any(
            item.category is MaterialCategory.FILING_CERTIFICATE
            and item.duplicate_of_material_id is None
            and item.quality_status is not MaterialQualityStatus.UNUSABLE for item in materials
        )
        has_front = any(item.capture_view is CaptureView.FRONT_LEVEL for item in panoramas)
        has_overhead = any(item.capture_view is CaptureView.OVERHEAD for item in panoramas)
        supplied_categories = {category for item in materials
                               if item.duplicate_of_material_id is None
                               and item.quality_status is not MaterialQualityStatus.UNUSABLE
                               for category in (item.category, *item.additional_categories)}
        required_categories = tuple(dict.fromkeys(
            check.category for check in (applicable_checks or [])
            if check.required_now and check.applicability == "applicable"
            and check.category
        ))
        missing_categories = [
            category.value
            for category in required_categories
            if category not in supplied_categories
        ]
        # Counts and category coverage are audit facts. Customer requests come
        # from applicable checks, never from a fixed package-size threshold.
        coverage_requirements = [
            self._material_category_label(item) for item in missing_categories
        ]

        return PackageAssessment(
            image_count=len(images),
            panorama_count=len(panoramas),
            has_filing_certificate=has_filing_certificate,
            has_front_level_panorama=has_front,
            has_overhead_panorama=has_overhead,
            missing_material_categories=missing_categories,
            missing_requirements=[],
            requirement_profile=self.config.package_requirement_profile,
            minimum_gate_passed=None,
            document_image_count=sum(
                item.media_type in {"image/jpeg", "image/png"}
                and item.category in document_categories
                and not any(category not in document_categories
                            for category in item.additional_categories)
                and item.duplicate_of_material_id is None for item in materials
            ),
            coverage_requirements=coverage_requirements,
        )

    def evaluate_materials(
        self,
        materials: list[Material],
        *,
        findings: list[RiskFinding] | None = None,
        ocr_fields: list[OcrField] | None = None,
        project: ProjectInfo | None = None,
        failed_material_ids: set[str] | None = None,
    ) -> list[MaterialReview]:
        failed_material_ids = failed_material_ids or set()
        findings = findings or []
        ocr_fields = ocr_fields or []
        by_material_findings: dict[str, list[RiskFinding]] = {}
        by_material_fields: dict[str, list[OcrField]] = {}
        for finding in findings:
            by_material_findings.setdefault(finding.material_id, []).append(finding)
        for field in ocr_fields:
            by_material_fields.setdefault(field.material_id, []).append(field)

        reviews: list[MaterialReview] = []
        for material in materials:
            reviews.append(
                self.evaluate_material(
                    material,
                    findings=by_material_findings.get(material.material_id, []),
                    ocr_fields=by_material_fields.get(material.material_id, []),
                    project=project,
                    analysis_failed=material.material_id in failed_material_ids,
                )
            )

        reviews = self._apply_panorama_coverage(
            reviews, materials, findings, failed_material_ids=failed_material_ids,
        )

        if project is not None and self.enabled("filing"):
            reviews = self._apply_filing_cross_checks(
                reviews,
                materials,
                ocr_fields,
                project,
            )
        return reviews

    def evaluate_material(
        self,
        material: Material,
        *,
        findings: list[RiskFinding] | None = None,
        ocr_fields: list[OcrField] | None = None,
        project: ProjectInfo | None = None,
        analysis_failed: bool = False,
    ) -> MaterialReview:
        findings = findings or []
        policy = getattr(self, "rule_policy", None)
        if policy:
            findings = [f for f in findings if f.category in policy.risks]
        ocr_fields = ocr_fields or []
        rule_ids: list[str] = []
        reasons: list[str] = []
        missing: list[str] = []
        finding_ids: list[str] = []
        ocr_field_ids = [item.field_id for item in ocr_fields]
        actions: list[MaterialReviewAction] = []
        manual_review = False
        optional_material = material.category in {
            MaterialCategory.GRID_CONNECTION_DOCUMENT,
            MaterialCategory.INVERTER_NAMEPLATE,
            MaterialCategory.MONITORING_OPTIONAL,
        }
        if analysis_failed:
            rule_ids.append("SYS-PROVIDER-FAILURE")
            actions.append(MaterialReviewAction.WARNING)
            reasons.append("自动识别未完成；先内部重试或人工核验，不能据此认定客户缺材")
            manual_review = True
        relevant_checks = self._expected_checks_for_material(material.category) or frozenset()
        relevant_categories = relevant_checks | {RiskCategory.IMAGE_QUALITY}
        if material.category in {
            MaterialCategory.PANORAMA,
            MaterialCategory.ROOF_CONNECTION,
        }:
            # The challenge requires excluded surroundings to be assessed from
            # site panoramas and whenever they are directly visible at the roof
            # connection. Other material types must not inherit these findings.
            relevant_categories |= CRITICAL_ENVIRONMENT_CHECKS
        if material.category in {MaterialCategory.PANORAMA,
                                 MaterialCategory.ROOF_CONNECTION,
                                 MaterialCategory.PARAPET}:
            # The aisle result is produced once from related photos, then
            # attached to the photo containing the decisive visual evidence.
            relevant_categories |= {RiskCategory.AISLE_OBSTRUCTION}

        if material.quality_status is not MaterialQualityStatus.USABLE:
            rule_ids.append("DOC-OPTIONAL-EVIDENCE-001" if optional_material
                            else self.MATERIAL_QUALITY_RULE_ID)
            actions.append(MaterialReviewAction.WARNING if optional_material
                           else MaterialReviewAction.REQUEST_MORE)
            reasons.append(self._quality_reason(material.quality_status))
            if material.quality_issues:
                reasons.append(
                    "文件预检质量标记：" + "、".join(material.quality_issues)
                )
            if optional_material:
                reasons.append("此材料为可选参考，画质问题不触发客户补材")
            else:
                missing.append("补充清晰、完整且包含关键区域的材料")

        for finding in findings:
            if finding.category not in relevant_categories:
                continue
            if quality_evidence_conflicts(finding):
                finding_ids.append(finding.finding_id)
                rule_ids.append(self.FINDING_WARNING_RULE_ID)
                actions.append(MaterialReviewAction.WARNING)
                reasons.append("图片质量识别状态与文字证据不一致：文字说明图片清晰可用；"
                               "请内部重试或人工核验，不据此要求客户补拍。原始证据："
                               + finding.evidence_text)
                manual_review = True
                continue
            if finding.detection_status is DetectionStatus.NOT_APPLICABLE:
                continue
            if finding.detection_status is DetectionStatus.NOT_DETECTED:
                if finding.requires_manual_review:
                    finding_ids.append(finding.finding_id)
                    rule_ids.append(self.FINDING_WARNING_RULE_ID)
                    actions.append(MaterialReviewAction.WARNING)
                    reasons.append(f"模型标记该检查需人工复核：{finding.label}；{finding.evidence_text}")
                    manual_review = True
                continue

            finding_ids.append(finding.finding_id)
            if finding.detection_status is DetectionStatus.UNCERTAIN:
                if finding.category in SUPPLEMENT_REQUIRED_CHECKS:
                    rule_ids.append("DOC-OPTIONAL-EVIDENCE-001" if optional_material
                                    else self.MATERIAL_QUALITY_RULE_ID)
                    actions.append(MaterialReviewAction.WARNING if optional_material
                                   else MaterialReviewAction.REQUEST_MORE)
                    reasons.append(f"无法确认图片质量是否足以支持判断：{finding.evidence_text}")
                    if not optional_material:
                        missing.append("补拍光线充足、清晰且完整展示检查区域的照片")
                        manual_review = True
                elif finding.category in CRITICAL_ENVIRONMENT_CHECKS:
                    actions.append(MaterialReviewAction.WARNING)
                    if finding.environment_relation is EnvironmentRelation.DISTANT_BACKGROUND:
                        rule_ids.append(self.ENVIRONMENT_BACKGROUND_RULE_ID)
                        reasons.append(
                            f"环境识别状态不确定，但其位置关系被标记为远处背景；"
                            f"不作为项目现场拒保事实：{finding.label}；{finding.evidence_text}"
                        )
                    else:
                        rule_ids.append(self.ENVIRONMENT_RELATION_REVIEW_RULE_ID)
                        reasons.append(
                            f"拒保环境或其与项目现场的关系尚不确定，暂不自动拒保；"
                            f"由核保人员结合原图确认：{finding.label}；{finding.evidence_text}"
                        )
                        manual_review = True
                else:
                    rule_ids.append(self.FINDING_WARNING_RULE_ID)
                    actions.append(MaterialReviewAction.WARNING)
                    reasons.append(f"图片风险判断不确定：{finding.label}；{finding.evidence_text}")
                    manual_review = True
                continue

            if finding.category in REJECT_ENVIRONMENT_CATEGORIES:
                if finding.environment_relation is EnvironmentRelation.DISTANT_BACKGROUND:
                    rule_ids.append(self.ENVIRONMENT_BACKGROUND_RULE_ID)
                    actions.append(MaterialReviewAction.WARNING)
                    reasons.append(
                        f"识别到的{finding.label}属于远处背景景物，不作为项目现场拒保事实；"
                        f"证据：{finding.evidence_text}"
                    )
                    continue

                auto_reject_eligible = (
                    finding.environment_relation
                    in self.config.environment_auto_reject_relations
                    and finding.confidence
                    >= self.config.environment_auto_reject_min_confidence
                    and not finding.requires_manual_review
                )
                if not auto_reject_eligible:
                    rule_ids.append(self.ENVIRONMENT_RELATION_REVIEW_RULE_ID)
                    actions.append(MaterialReviewAction.WARNING)
                    manual_review = True
                    relation_reasons = []
                    if finding.requires_manual_review:
                        relation_reasons.append("模型明确要求人工复核")
                    if (
                        finding.environment_relation
                        not in self.config.environment_auto_reject_relations
                    ):
                        if finding.environment_relation is EnvironmentRelation.OPERATIONAL_SURROUNDINGS:
                            relation_reasons.append(
                                "识别对象位于项目邻近周边，适用边界尚待业务确认"
                            )
                        else:
                            relation_reasons.append(
                                "无法确认该环境是否属于项目现场"
                            )
                    if (
                        finding.confidence
                        < self.config.environment_auto_reject_min_confidence
                    ):
                        relation_reasons.append(
                            f"模型参考置信度{finding.confidence:.2f}低于当前配置门槛"
                            f"{self.config.environment_auto_reject_min_confidence:.2f}"
                        )
                    reasons.append(
                        f"{'；'.join(relation_reasons)}，保留风险证据并转人工核验，不自动拒保："
                        f"{finding.label}；{finding.evidence_text}"
                    )
                    continue

                rule_ids.append(self.ENVIRONMENT_REJECT_RULE_ID)
                actions.append(MaterialReviewAction.RECOMMEND_REJECT)
                relation_description = ENVIRONMENT_RELATION_DESCRIPTIONS[
                    finding.environment_relation
                ]
                reasons.append(
                    f"高风险环境被识别为{relation_description}，模型参考置信度"
                    f"{finding.confidence:.2f}达到当前配置门槛"
                    f"{self.config.environment_auto_reject_min_confidence:.2f}，且未要求人工复核；"
                    f"触发拒保环境规则："
                    f"{finding.label}；{finding.evidence_text}"
                )
                continue

            if finding.category is RiskCategory.IMAGE_QUALITY:
                rule_ids.append("DOC-OPTIONAL-EVIDENCE-001" if optional_material
                                else self.MATERIAL_QUALITY_RULE_ID)
                actions.append(MaterialReviewAction.WARNING if optional_material
                               else MaterialReviewAction.REQUEST_MORE)
                reasons.append(f"图片质量不足，无法可靠检查：{finding.evidence_text}")
                if not optional_material:
                    missing.append("补拍光线充足、清晰且完整展示检查区域的照片")
                continue

            if finding.category is RiskCategory.SEVERE_SHADING:
                if (finding.requires_manual_review or finding.confidence
                        < self.config.environment_auto_reject_min_confidence):
                    rule_ids.append(self.FINDING_WARNING_RULE_ID)
                    actions.append(MaterialReviewAction.WARNING)
                    reasons.append(
                        f"严重遮挡判断不确定，保留图片证据并复核："
                        f"{finding.label}；{finding.evidence_text}"
                    )
                    manual_review = True
                    continue
                rule_ids.append(self.SEVERE_SHADING_RULE_ID)
                actions.append(MaterialReviewAction.RECOMMEND_REJECT)
                reasons.append(f"发现大面积遮挡：{finding.label}；{finding.evidence_text}")
                continue

            if finding.category is RiskCategory.UNPROTECTED_CABLE:
                if (finding.requires_manual_review or finding.confidence
                        < self.config.environment_auto_reject_min_confidence):
                    rule_ids.append(self.FINDING_WARNING_RULE_ID)
                    actions.append(MaterialReviewAction.WARNING)
                    reasons.append(
                        f"裸露电缆判断不确定，保留图片证据并复核："
                        f"{finding.label}；{finding.evidence_text}"
                    )
                    manual_review = True
                    continue
                rule_ids.append("ELEC-UNPROTECTED-CABLE-REJECT-001")
                actions.append(MaterialReviewAction.RECOMMEND_REJECT)
                reasons.append(
                    f"发现裸露且无保护的电缆，按赛题中期规则建议拒保："
                    f"{finding.label}；{finding.evidence_text}"
                )
                continue

            if finding.category is RiskCategory.MONITORING_EFFECTIVE_COVERAGE:
                continue

            if finding.category is RiskCategory.INSTALLATION_TYPE:
                if (project is not None
                    and project.project_type is not ProjectType.MIXED
                    and project.installation_type.value != "unknown"
                    and not self._installation_matches(
                    f"{finding.label} {finding.evidence_text}", project
                )):
                    rule_ids.append(self.CROSS_CHECK_RULE_ID)
                    actions.append(MaterialReviewAction.WARNING)
                    reasons.append(
                        f"照片识别的安装类型“{finding.label}”与录入类型"
                        f"“{project.installation_type.value}”不一致，需核对"
                    )
                    manual_review = True
                continue

            if finding.category in {
                RiskCategory.MINOR_SHADING,
                RiskCategory.AISLE_OBSTRUCTION,
                RiskCategory.MISSING_PARAPET_OR_GUARDRAIL,
                RiskCategory.DRAINAGE_ABNORMAL,
                RiskCategory.FLAMMABLE_MATERIAL,
                RiskCategory.HAZARDOUS_MATERIAL,
                RiskCategory.COMBINER_BOX_SEAL_ABNORMAL,
                RiskCategory.COMBINER_BOX_FUSE_ABNORMAL,
                RiskCategory.SURGE_PROTECTOR_ABNORMAL,
                RiskCategory.ROOF_CONNECTION_ABNORMAL,
                RiskCategory.MODULE_DAMAGE,
                RiskCategory.INVERTER_ABNORMAL,
                RiskCategory.ELECTRICAL_GROUNDING_ABNORMAL,
                RiskCategory.MODULE_DISCOLORATION,
                RiskCategory.DANGEROUS_PROCESS,
                RiskCategory.CLEANROOM,
                RiskCategory.FIRE_PROTECTION_ABSENT,
            }:
                rule_ids.append(self.FINDING_WARNING_RULE_ID)
                actions.append(MaterialReviewAction.WARNING)
                moderate_shading = (
                    finding.category is RiskCategory.MINOR_SHADING
                    and finding.severity in {
                        RiskSeverity.MEDIUM, RiskSeverity.HIGH,
                        RiskSeverity.CRITICAL, RiskSeverity.UNKNOWN,
                    }
                )
                if moderate_shading:
                    reasons.append(
                        f"组件遮挡程度达到中等及以上或无法确定，需人工复核："
                        f"{finding.label}；{finding.evidence_text}"
                    )
                    manual_review = True
                elif (finding.requires_manual_review or finding.confidence
                        < self.config.environment_auto_reject_min_confidence):
                    reasons.append(f"图片风险判断不确定，需复核：{finding.label}；{finding.evidence_text}")
                    manual_review = True
                else:
                    reasons.append(f"图片风险提示：{finding.label}；{finding.evidence_text}")

        if (
            material.category is not MaterialCategory.PANORAMA
            and self._expected_checks_for_material(material.category) is not None
            and material.media_type in {"image/jpeg", "image/png"}
            and not analysis_failed
        ):
            covered = {
                item.category
                for item in findings
                if item.detection_status is not DetectionStatus.NOT_APPLICABLE
            }
            expected = self._expected_checks_for_material(material.category)
            uncovered = (expected or frozenset()) - covered
            if uncovered:
                rule_ids.append(self.ENVIRONMENT_COVERAGE_RULE_ID)
                actions.append(MaterialReviewAction.WARNING)
                names = "、".join(
                    sorted(RISK_CATEGORY_LABELS.get(item, item.value) for item in uncovered)
                )
                reasons.append(
                    f"系统未返回以下图片检查项的识别结果：{names}；"
                    "这不触发补材或复核；未返回结果不代表现场没有该类风险"
                )

        if analysis_failed or (policy and not policy.business_ocr(material.category)):
            pass
        elif material.category in {MaterialCategory.FILING_CERTIFICATE, MaterialCategory.PROJECT_DOCUMENT}:
            manual_review = self._require_ocr_fields(
                ocr_fields,
                (("project_name", "site_address", "document_type") if material.category is MaterialCategory.PROJECT_DOCUMENT else ("project_name", "site_address")),
                rule_ids,
                reasons,
                missing,
                actions,
            ) or manual_review
            self._note_comparison_fields(
                ocr_fields, ("insured_name",), rule_ids, reasons, actions,
            )
        elif material.category is MaterialCategory.EQUIPMENT_INVENTORY and material.media_type in {"image/jpeg", "image/png", "application/pdf"}:
            manual_review = self._require_ocr_fields(ocr_fields, ("row_1_equipment_name",), rule_ids, reasons, missing, actions) or manual_review
        elif material.category is MaterialCategory.COMPONENT_NAMEPLATE:
            manual_review = self._require_ocr_fields(
                ocr_fields,
                ("component_model", "rated_power_w"),
                rule_ids,
                reasons,
                missing,
                actions,
            ) or manual_review
            self._note_comparison_fields(
                ocr_fields, ("serial_number",), rule_ids, reasons, actions,
            )
        elif material.category is MaterialCategory.INVERTER_NAMEPLATE:
            manual_review = self._require_ocr_fields(
                ocr_fields,
                ("inverter_model", "rated_power_w"),
                rule_ids,
                reasons,
                missing,
                actions, optional=True,
            ) or manual_review
            self._note_comparison_fields(
                ocr_fields, ("serial_number",), rule_ids, reasons, actions,
            )
        elif material.category is MaterialCategory.GRID_CONNECTION_DOCUMENT:
            manual_review = self._require_ocr_fields(
                ocr_fields,
                ("project_name", "project_entity", "site_address", "grid_connection_date"),
                rule_ids,
                reasons,
                missing,
                actions, optional=True,
            ) or manual_review
        elif (
            material.category is MaterialCategory.ELECTRICAL_GROUNDING
            and material.media_type == "application/pdf"
        ):
            manual_review = self._require_ocr_fields(
                ocr_fields,
                ("grounding_resistance_ohm", "inspection_date", "inspection_result"),
                rule_ids,
                reasons,
                missing,
                actions,
            ) or manual_review
        elif material.category is MaterialCategory.COMBINER_BOX:
            manual_review = self._require_ocr_fields(
                ocr_fields,
                ("combiner_box_model", "rated_voltage_v", "rated_current_a"),
                rule_ids,
                reasons,
                missing,
                actions,
            ) or manual_review
            self._note_comparison_fields(
                ocr_fields, ("serial_number",), rule_ids, reasons, actions,
            )

        if (
            self.enabled("watermark")
            and material.category in {MaterialCategory.PANORAMA, MaterialCategory.ROOF_CONNECTION}
            and material.media_type in {"image/jpeg", "image/png"}
            and not analysis_failed
        ):
            if project is None or project.proposed_start_date is None:
                rule_ids.append(self.WATERMARK_DATE_RULE_ID)
                actions.append(MaterialReviewAction.WARNING)
                reasons.append(f"拟起保日期未提供，{policy.snapshot['parameters']['watermark_days'] if policy else 15}日时效暂无法核验；日期保持空值，列为风险提示")
            if material.watermark_status is not WatermarkStatus.PRESENT:
                rule_ids.append(self.WATERMARK_RULE_ID)
                actions.append(MaterialReviewAction.WARNING)
                reasons.append("未能确认照片含有日期及经纬度水印，建议补充带水印的现场照片")
            else:
                missing_watermark_fields = []
                if material.captured_at is None:
                    missing_watermark_fields.append("拍摄日期")
                if material.longitude is None or material.latitude is None:
                    missing_watermark_fields.append("经纬度")
                if missing_watermark_fields:
                    rule_ids.append(self.WATERMARK_DATE_RULE_ID)
                    actions.append(MaterialReviewAction.WARNING)
                    reasons.append("水印已识别，但以下字段暂无法完整读取，仅作提示："
                                   + "、".join(missing_watermark_fields))
                elif project is not None and project.proposed_start_date is not None and not (
                    0
                    <= (project.proposed_start_date - material.captured_at.date()).days
                    <= (policy.snapshot["parameters"]["watermark_days"] if policy else 15)
                ):
                    rule_ids.append(self.WATERMARK_DATE_RULE_ID)
                    actions.append(MaterialReviewAction.WARNING)
                    reasons.append(f"照片拍摄日期不在拟起保日前{policy.snapshot['parameters']['watermark_days'] if policy else 15}日内，仅作提示")

        if not actions:
            actions.append(MaterialReviewAction.PASS)
            reasons.append("材料检查未发现需要自动升级处理的事项")

        action = max(actions, key=self._ACTION_PRIORITY.__getitem__)
        return MaterialReview(
            material_id=material.material_id,
            action=action,
            triggered_rule_ids=list(dict.fromkeys(rule_ids)),
            finding_ids=list(dict.fromkeys(finding_ids)),
            ocr_field_ids=ocr_field_ids,
            reasons=list(dict.fromkeys(reasons)),
            missing_requirements=list(dict.fromkeys(missing)),
            requires_manual_review=manual_review,
        )

    def _apply_panorama_coverage(
        self,
        reviews: list[MaterialReview],
        materials: list[Material],
        findings: list[RiskFinding],
        failed_material_ids: set[str] | None = None,
    ) -> list[MaterialReview]:
        """Aggregate checks across complementary panorama views in one case."""
        panorama_ids = {
            item.material_id
            for item in materials
            if item.category is MaterialCategory.PANORAMA
            and item.media_type in {"image/jpeg", "image/png"}
        }
        if not panorama_ids:
            return reviews

        expected = self._expected_checks_for_material(MaterialCategory.PANORAMA) or frozenset()
        covered = {
            item.category
            for item in findings
            if item.material_id in panorama_ids
            and item.detection_status is not DetectionStatus.NOT_APPLICABLE
        }
        uncovered = expected - covered
        if not uncovered:
            return reviews

        target_index = next(
            index
            for index, material in enumerate(materials)
            if material.material_id in panorama_ids
        )
        review = reviews[target_index]
        names = "、".join(
            sorted(RISK_CATEGORY_LABELS.get(item, item.value) for item in uncovered)
        )
        reasons = [
            *review.reasons,
            (f"合并检查{len(panorama_ids)}张全景后，以下图片检查项未返回识别结果：{names}；"
             "这不触发补材或复核；未返回结果不代表现场没有该类风险"),
        ]
        if failed_material_ids and panorama_ids & failed_material_ids:
            reviews[target_index] = review.model_copy(update={
                "action": max([review.action, MaterialReviewAction.WARNING],
                              key=self._ACTION_PRIORITY.__getitem__),
                "triggered_rule_ids": list(dict.fromkeys(
                    [*review.triggered_rule_ids, "SYS-PROVIDER-FAILURE"])),
                "reasons": list(dict.fromkeys([
                    *review.reasons, "全景识别服务未完成；缺少的检查状态先由内部重试或人工核验",
                ])),
                "requires_manual_review": True,
            })
            return reviews
        reviews[target_index] = review.model_copy(
            update={
                "action": max(
                    [review.action, MaterialReviewAction.WARNING],
                    key=self._ACTION_PRIORITY.__getitem__,
                ),
                "triggered_rule_ids": list(
                    dict.fromkeys(
                        [*review.triggered_rule_ids, self.ENVIRONMENT_COVERAGE_RULE_ID]
                    )
                ),
                "reasons": list(dict.fromkeys(reasons)),
                "requires_manual_review": review.requires_manual_review,
            }
        )
        return reviews

    def _apply_filing_cross_checks(
        self,
        reviews: list[MaterialReview],
        materials: list[Material],
        ocr_fields: list[OcrField],
        project: ProjectInfo,
    ) -> list[MaterialReview]:
        material_categories = {item.material_id: item.category for item in materials}
        filing_fields = [
            item
            for item in ocr_fields
            if item.field_name
            in {
                "project_name",
                "project_entity",
                "insured_name",
                "insured_address",
                "site_address",
                "project_type",
                "component_model",
            }
        ]
        usable = [
            item
            for item in filing_fields
            if item.value_status is OcrValueStatus.EXTRACTED
            and item.confidence >= OCR_MIN_CONFIDENCE
        ]
        updates: dict[str, list[object]] = {}
        filing_stage = self.evaluate_filing_stage(materials, ocr_fields, project)
        if filing_stage.action == "recommend_reject":
            by_id = {field.field_id: field for field in ocr_fields}
            filing_rule_id = filing_stage.rule_ids[0]
            for field_id in filing_stage.ocr_field_ids:
                field = by_id[field_id]
                self._add_review_update(
                    updates, field.material_id, MaterialReviewAction.RECOMMEND_REJECT,
                    filing_rule_id,
                    f"{filing_stage.reasons[0]}；原文证据：{field.raw_value or field.normalized_value}",
                    field_id,
                )

        expected_fields = {
            "project_name": project.project_name,
            "project_entity": project.project_entity,
            "insured_name": project.insured_name,
            "insured_address": project.insured_address,
            "site_address": project.site_address,
            "component_model": project.component_model,
        }
        actual_by_name = {
            name: [item for item in usable if item.field_name == name]
            for name in expected_fields
        }
        for name, expected in expected_fields.items():
            actual = actual_by_name[name]
            if expected and actual and not any(
                _normalized_text(expected) == _normalized_text(item.normalized_value or item.raw_value)
                for item in actual
            ):
                for item in actual:
                    self._add_review_update(
                        updates,
                        item.material_id,
                        MaterialReviewAction.WARNING,
                        self.CROSS_CHECK_RULE_ID,
                        f"备案证{self._field_label(name)}与投保信息不一致，请核对",
                        item.field_id,
                    )

        official_document_ids = {
            material_id
            for material_id, category in material_categories.items()
            if category
            in {
                MaterialCategory.PROJECT_DOCUMENT,
                MaterialCategory.FILING_CERTIFICATE,
                MaterialCategory.GRID_CONNECTION_DOCUMENT,
            }
        }

        # The challenge explicitly asks for the project entity/insured and
        # project/insured-address consistency checks. Compare OCR evidence from
        # the same official document when both fields are present.
        related_field_pairs = (
            ("project_entity", "insured_name", "项目单位与被保险人信息不一致"),
            ("site_address", "insured_address", "项目地址与被保险地址不一致"),
        )
        for material_id in official_document_ids:
            fields_by_name = {
                name: [
                    item
                    for item in usable
                    if item.material_id == material_id and item.field_name == name
                ]
                for pair in related_field_pairs
                for name in pair[:2]
            }
            for left_name, right_name, reason in related_field_pairs:
                left_fields = fields_by_name[left_name]
                right_fields = fields_by_name[right_name]
                left_values = {
                    _normalized_text(item.normalized_value or item.raw_value)
                    for item in left_fields
                    if item.normalized_value or item.raw_value
                }
                right_values = {
                    _normalized_text(item.normalized_value or item.raw_value)
                    for item in right_fields
                    if item.normalized_value or item.raw_value
                }
                if left_values and right_values and left_values.isdisjoint(right_values):
                    for item in [*left_fields, *right_fields]:
                        self._add_review_update(
                            updates,
                            item.material_id,
                            MaterialReviewAction.WARNING,
                            self.CROSS_CHECK_RULE_ID,
                            f"{reason}，需核验原件及主体关系",
                            item.field_id,
                        )

        cross_document_fields = {
            "project_name",
            "project_entity",
            "insured_name",
            "site_address",
            "insured_address",
        }
        for name in cross_document_fields:
            values = [
                item
                for item in usable
                if item.field_name == name
                and item.material_id in official_document_ids
                and (item.normalized_value or item.raw_value)
            ]
            distinct_values = {
                _normalized_text(item.normalized_value or item.raw_value)
                for item in values
            }
            if len(distinct_values) > 1:
                for item in values:
                    self._add_review_update(
                        updates,
                        item.material_id,
                        MaterialReviewAction.WARNING,
                        self.CROSS_CHECK_RULE_ID,
                        f"備案證與並網材料中的{self._field_label(name)}不一致，需人工核對",
                        item.field_id,
                    )

        expected_component_model = project.component_model
        for item in actual_by_name.get("component_model", []):
            observed = item.normalized_value or item.raw_value
            if (
                expected_component_model
                and observed
                and _normalized_text(expected_component_model) != _normalized_text(observed)
            ):
                self._add_review_update(
                    updates,
                    item.material_id,
                    MaterialReviewAction.WARNING,
                    self.CROSS_CHECK_RULE_ID,
                    "组件铭牌型号与案件录入型号不一致，需核对实物型号和组件目录匹配结果",
                    item.field_id,
                )

        return [self._merge_update(review, updates[review.material_id]) if review.material_id in updates else review for review in reviews]

    @staticmethod
    def _add_review_update(
        updates: dict[str, list[object]],
        material_id: str,
        action: MaterialReviewAction,
        rule_id: str,
        reason: str,
        field_id: str,
    ) -> None:
        values = updates.setdefault(material_id, [[], [], [], [], []])
        values[0].append(action)
        values[1].append(rule_id)
        values[2].append(reason)
        values[3].append(field_id)

    def _merge_update(self, review: MaterialReview, values: list[object]) -> MaterialReview:
        actions, rule_ids, reasons, field_ids, _ = values
        all_actions = [review.action, *actions]
        return review.model_copy(
            update={
                "action": max(all_actions, key=self._ACTION_PRIORITY.__getitem__),
                "triggered_rule_ids": list(dict.fromkeys([*review.triggered_rule_ids, *rule_ids])),
                "reasons": list(dict.fromkeys([*review.reasons, *reasons])),
                "ocr_field_ids": list(dict.fromkeys([*review.ocr_field_ids, *field_ids])),
                "requires_manual_review": review.requires_manual_review or any(
                    action is MaterialReviewAction.WARNING for action in actions
                ),
            }
        )

    @staticmethod
    def _installation_matches(label: str, project: ProjectInfo) -> bool:
        observed = _normalized_text(label)
        carport_terms = ("车棚", "停车棚", "carport")
        roof_subtypes = {
            "color_steel_roof": ("彩钢", "钢瓦"),
            "flat_roof": ("平屋顶", "平屋面"),
            "tile_roof": ("瓦屋顶", "瓦片屋顶"),
        }
        if project.installation_type is InstallationType.CARPORT_ROOF:
            return any(term in observed for term in carport_terms)
        if any(term in observed for term in carport_terms):
            return False
        expected = roof_subtypes.get(project.installation_type.value, ())
        if any(term in observed for term in expected):
            return True
        if any(term in observed for terms in roof_subtypes.values() for term in terms):
            return False
        # A generic roof observation does not contradict a more specific roof subtype.
        return any(term in observed for term in ("屋顶", "屋面", "建筑顶部", "roof"))

    @staticmethod
    def _quality_reason(status: MaterialQualityStatus) -> str:
        return {
            MaterialQualityStatus.POOR: "材料质量较差，无法保证自动识别结果可靠",
            MaterialQualityStatus.UNUSABLE: "材料质量不可用，无法进行可靠识别",
            MaterialQualityStatus.UNKNOWN: "材料质量状态未知，无法确认是否满足识别要求",
            MaterialQualityStatus.USABLE: "材料质量满足自动处理要求",
        }[status]

    @staticmethod
    def _require_ocr_fields(
        ocr_fields: list[OcrField],
        required: tuple[str, ...],
        rule_ids: list[str],
        reasons: list[str],
        missing: list[str],
        actions: list[MaterialReviewAction],
        *,
        optional: bool = False,
    ) -> bool:
        reliable_names = {
            item.field_name
            for item in ocr_fields
            if item.value_status is OcrValueStatus.EXTRACTED
            and item.confidence >= OCR_MIN_CONFIDENCE
            and (item.normalized_value or item.raw_value)
        }
        unresolved = [name for name in required if name not in reliable_names]
        explicit_missing = [name for name in unresolved if any(
            item.field_name == name and item.value_status is OcrValueStatus.MISSING
            for item in ocr_fields
        )]
        pending = [name for name in unresolved if name not in explicit_missing]
        if optional:
            if unresolved:
                rule_ids.append("DOC-OPTIONAL-EVIDENCE-001")
                actions.append(MaterialReviewAction.WARNING)
                reasons.append("可选材料部分字段未可靠识别，不作为客户补材要求："
                               + ", ".join(unresolved))
            return False
        if explicit_missing:
            rule_ids.append("DOC-REQUIRED-FIELDS-001")
            actions.append(MaterialReviewAction.REQUEST_MORE)
            reasons.append("材料识别结果明确显示关键字段缺失")
            missing.append(
                f"补充可清晰识别的字段：{', '.join(explicit_missing)}"
            )
        if pending:
            rule_ids.append("SYS-OCR-FIELDS-PENDING-001")
            actions.append(MaterialReviewAction.WARNING)
            reasons.append("以下字段未返回可靠识别结果，需内部重试或人工核验原件：" + ", ".join(pending))
        return bool(pending)

    @staticmethod
    def _note_comparison_fields(
        ocr_fields: list[OcrField],
        names: tuple[str, ...],
        rule_ids: list[str],
        reasons: list[str],
        actions: list[MaterialReviewAction],
    ) -> None:
        unavailable = [
            name for name in names
            if not any(
                item.field_name == name
                and item.value_status is OcrValueStatus.EXTRACTED
                and item.confidence >= OCR_MIN_CONFIDENCE
                and (item.normalized_value or item.raw_value)
                for item in ocr_fields
            )
        ]
        if unavailable:
            rule_ids.append("DOC-COMPARISON-FIELD-UNAVAILABLE-001")
            actions.append(MaterialReviewAction.WARNING)
            labels = {"insured_name": "被保险人", "serial_number": "序列号"}
            reasons.append(
                "交叉核对范围提示：未取得"
                + "、".join(labels.get(name, name) for name in unavailable)
                + "的可靠识别值，暂不能核对该项；不据此认定材料缺失或存在风险"
            )

    @staticmethod
    def _field_label(name: str) -> str:
        return {
            "project_name": "项目名称",
            "project_entity": "项目单位",
            "insured_name": "被保险人",
            "insured_address": "被保险地址",
            "component_model": "组件型号",
            "site_address": "项目地址",
        }.get(name, name)

    @staticmethod
    def _material_category_label(category: str) -> str:
        return {
            "panorama": "电站全景照",
            "roof_connection": "屋顶连接处照片",
            "parapet": "女儿墙及排水照片",
            "workshop": "车间内部照片",
            "filing_certificate": "电站备案证",
            "grid_connection_document": "并网许可或调度协议",
            "electrical_grounding": "电气系统与防雷接地照片/记录",
            "component_nameplate": "组件铭牌",
            "inverter_nameplate": "逆变器铭牌",
            "combiner_box": "汇流箱照片",
        }.get(category, category)
