"""Scene-dependent checklist and evidence-aware material status."""

from app.contracts import (
    ApplicableCheck,
    CheckResult,
    DetectionStatus,
    InstallationAnalysis,
    InstallationSceneType,
    Material,
    MaterialCategory,
    MaterialQualityStatus,
    OcrField,
    OcrValueStatus,
    RiskCategory,
    RiskFinding,
)

from .engine import EXPECTED_CHECKS_BY_MATERIAL
from .quality_evidence import quality_evidence_conflicts

_FINDING_REQUIREMENTS = {
    "panorama": EXPECTED_CHECKS_BY_MATERIAL[MaterialCategory.PANORAMA]
    - {RiskCategory.IMAGE_QUALITY},
    "support": frozenset({RiskCategory.ROOF_CONNECTION_ABNORMAL}),
    "edge": frozenset({RiskCategory.MISSING_PARAPET_OR_GUARDRAIL,
                        RiskCategory.DRAINAGE_ABNORMAL}),
    "workshop": frozenset({RiskCategory.FLAMMABLE_MATERIAL,
                            RiskCategory.HAZARDOUS_MATERIAL}),
    "grounding": frozenset({RiskCategory.ELECTRICAL_GROUNDING_ABNORMAL}),
    "combiner": frozenset({RiskCategory.COMBINER_BOX_SEAL_ABNORMAL,
                            RiskCategory.COMBINER_BOX_FUSE_ABNORMAL,
                            RiskCategory.SURGE_PROTECTOR_ABNORMAL}),
}
_FIELD_REQUIREMENTS = {
    "filing": frozenset({"project_name", "site_address"}),
    "grid": frozenset({"project_name", "project_entity", "site_address",
                        "grid_connection_date"}),
    "component": frozenset({"component_model", "rated_power_w"}),
    "inverter": frozenset({"inverter_model", "rated_power_w"}),
}


def build_applicable_checks(analysis: InstallationAnalysis, *, profile: str,
                            materials: list[Material] | None = None) -> list[ApplicableCheck]:
    full = profile == "full_intake"
    checks: list[ApplicableCheck] = []
    combiner_submitted = any(
        item.category is MaterialCategory.COMBINER_BOX
        or MaterialCategory.COMBINER_BOX in item.additional_categories
        for item in (materials or [])
    )

    def add(area_id: str, key: str, label: str, category: MaterialCategory,
            evidence: str, *, required: bool, applicability: str = "applicable",
            reason: str = "适用核保检查") -> None:
        checks.append(ApplicableCheck(
            area_id=area_id, key=key, label=label, category=category,
            applicability=applicability, required_now=required,
            evidence_requirement=evidence, reason=reason,
        ))

    add("project", "panorama", "现场全景", MaterialCategory.PANORAMA,
        "展示光伏阵列及周边环境，能够判断场地关系和组件遮挡",
        required=True)
    add("project", "filing", "备案证明", MaterialCategory.FILING_CERTIFICATE,
        "清楚显示备案项目名称、地址和建设内容", required=True)
    add("project", "grid", "并网或调度材料", MaterialCategory.GRID_CONNECTION_DOCUMENT,
        "清楚显示项目名称、主体、地点和并网信息", required=False,
        reason="可选材料；提交后用于交叉核验，不作为本轮补材门槛")
    add("project", "grounding", "电气及接地", MaterialCategory.ELECTRICAL_GROUNDING,
        "展示电气设备、线缆保护及接地可见部位", required=full)
    add("project", "component", "组件铭牌", MaterialCategory.COMPONENT_NAMEPLATE,
        "铭牌型号和功率可读；序列号用于有对应来源时的核对", required=False,
        reason="组件资料用于抗灾筛查；缺少时提示筛查范围，不阻断前置拒保检查")
    add("project", "inverter", "逆变器铭牌", MaterialCategory.INVERTER_NAMEPLATE,
        "铭牌型号和功率可读；序列号用于有对应来源时的核对", required=False,
        reason="可选材料；提交后用于设备信息核验，不作为本轮补材门槛")
    add("project", "combiner", "汇流箱", MaterialCategory.COMBINER_BOX,
        "先确认是否配置；如配置，展示箱体、熔断器和防雷器窗口",
        required=full, applicability="applicable" if combiner_submitted else "pending",
        reason="已收到汇流箱材料，检查可见部位" if combiner_submitted
        else "尚未核实是否配置汇流箱")
    for area in analysis.areas:
        if area.scene_type is InstallationSceneType.UNKNOWN or area.status != "confirmed":
            add(area.area_id, "support", "组件承载与连接", MaterialCategory.ROOF_CONNECTION,
                "展示组件与建筑屋面或停车棚结构的连接关系", required=full,
                applicability="pending", reason="安装类型尚未确认")
            continue
        if area.scene_type is InstallationSceneType.BUILDING_ROOF:
            add(area.area_id, "support", "屋顶连接与锚固", MaterialCategory.ROOF_CONNECTION,
                "拍清组件、屋面、连接节点和锚固关系", required=full)
            add(area.area_id, "edge", "屋顶边缘保护与排水", MaterialCategory.PARAPET,
                "展示适用的边缘防护、女儿墙及排水部位", required=full,
                applicability="pending", reason="需先确认该屋顶边缘保护设施的适用性")
            usage = area.usage or ""
            if any(term in usage for term in ("厂房", "车间", "工业生产")):
                applicability, reason = "applicable", "厂房屋顶需检查下方生产或存放空间"
            elif any(term in usage for term in ("住宅", "民居", "办公")):
                applicability, reason = "not_applicable", "现场用途不属于厂房车间"
            else:
                applicability, reason = "pending", "建筑用途尚不能确认是否需车间照片"
            add(area.area_id, "workshop", "屋顶下车间", MaterialCategory.WORKSHOP,
                "展示屋顶下方车间及可燃物、危险品存放情况", required=full,
                applicability=applicability, reason=reason)
        elif area.scene_type is InstallationSceneType.CARPORT_ROOF:
            add(area.area_id, "support", "车棚连接、梁柱与基础", MaterialCategory.ROOF_CONNECTION,
                "展示组件与棚顶支架、梁柱、锚固或基础及下方停车空间", required=full)
            add(area.area_id, "workshop", "厂房车间", MaterialCategory.WORKSHOP,
                "独立停车棚不要求厂房车间照片", required=False,
                applicability="not_applicable", reason="停车棚不是厂房车间")
            add(area.area_id, "edge", "女儿墙", MaterialCategory.PARAPET,
                "独立停车棚不机械要求建筑屋面女儿墙", required=False,
                applicability="not_applicable", reason="停车棚顶通常无建筑女儿墙")
    return checks


def assess_checks(checks: list[ApplicableCheck], materials: list[Material],
                  findings: list[RiskFinding], fields: list[OcrField],
                  analysis: InstallationAnalysis, *, failed_material_ids: set[str], allowed_risks=None) -> list[CheckResult]:
    areas = {area.area_id: area for area in analysis.areas}
    results: list[CheckResult] = []
    for check in checks:
        if check.applicability == "not_applicable":
            results.append(CheckResult(area_id=check.area_id, key=check.key,
                                       status="not_applicable", reason=check.reason))
            continue
        if check.applicability == "pending":
            results.append(CheckResult(area_id=check.area_id, key=check.key,
                                       status="pending", reason=check.reason,
                                       rule_ids=["CHECK-EVIDENCE-PENDING-001"]
                                       if check.required_now else []))
            continue
        candidates = [item for item in materials
                      if (item.category is check.category
                          or check.category in item.additional_categories)
                      and item.duplicate_of_material_id is None]
        if check.area_id != "project":
            related = set(areas[check.area_id].material_ids)
            candidates = [item for item in candidates if item.material_id in related]
        ids = [item.material_id for item in candidates]
        if not candidates:
            results.append(CheckResult(
                area_id=check.area_id, key=check.key,
                status="missing" if check.required_now else "not_provided",
                rule_ids=["CHECK-EVIDENCE-MISSING-001"] if check.required_now else [],
                reason="未收到能对应本检查项的材料" if check.required_now
                else "可选材料未提供，不影响材料完整性结论",
                customer_request=f"补充{check.label}：{check.evidence_requirement}"
                if check.required_now else None,
            ))
            continue
        visibly_unusable_ids = {item.material_id for item in findings
                                if item.category is RiskCategory.IMAGE_QUALITY
                                and not quality_evidence_conflicts(item)
                                and item.detection_status is DetectionStatus.DETECTED}
        valid = [item for item in candidates
                 if item.quality_status is MaterialQualityStatus.USABLE
                 and item.material_id not in visibly_unusable_ids]
        if not valid:
            quality_findings = [item for item in findings
                                if item.material_id in ids
                                and item.category is RiskCategory.IMAGE_QUALITY
                                and item.detection_status is DetectionStatus.DETECTED]
            results.append(CheckResult(
                area_id=check.area_id, key=check.key, status="unusable", material_ids=ids,
                rule_ids=["CHECK-EVIDENCE-UNUSABLE-001"] if check.required_now else [],
                finding_ids=[item.finding_id for item in quality_findings],
                reason="已提交材料但画质不足，关键部位无法可靠核验"
                + ("；" + "；".join(item.evidence_text for item in quality_findings)
                   if quality_findings else ""),
                customer_request=f"重拍{check.label}：{check.evidence_requirement}"
                if check.required_now else None,
            ))
            continue
        usable_ids = {item.material_id for item in valid}
        contradictory = [item for item in findings
                         if item.material_id in usable_ids and quality_evidence_conflicts(item)]
        if contradictory:
            results.append(CheckResult(
                area_id=check.area_id, key=check.key, status="pending", material_ids=ids,
                finding_ids=[item.finding_id for item in contradictory],
                reason="图片质量识别状态与文字证据不一致，需内部重试或人工核验；不据此要求客户补拍",
                customer_request=None,
            ))
            continue
        evidence_findings = [item for item in findings if item.material_id in usable_ids]
        evidence_fields = [item for item in fields if item.material_id in usable_ids]
        failed = bool(usable_ids & failed_material_ids)
        expected_findings = _FINDING_REQUIREMENTS.get(check.key, frozenset())
        if allowed_risks is not None:
            expected_findings = expected_findings & allowed_risks
        expected_fields = _FIELD_REQUIREMENTS.get(check.key, frozenset())
        observed_findings = {
            item.category for item in evidence_findings
            if item.detection_status in {
                DetectionStatus.DETECTED, DetectionStatus.NOT_DETECTED,
                DetectionStatus.NOT_APPLICABLE,
            }
        }
        uncertain_findings = {
            item.category for item in evidence_findings
            if item.detection_status is DetectionStatus.UNCERTAIN
        }
        observed_fields = {
            item.field_name for item in evidence_fields
            if item.value_status is OcrValueStatus.EXTRACTED
            and item.confidence >= 0.65
            and (item.normalized_value or item.raw_value)
        }
        uncovered_findings = expected_findings - observed_findings
        uncovered_fields = expected_fields - observed_fields
        if failed:
            results.append(CheckResult(
                area_id=check.area_id, key=check.key, status="pending", material_ids=ids,
                rule_ids=["CHECK-EVIDENCE-PENDING-001"] if check.required_now else [],
                finding_ids=[item.finding_id for item in evidence_findings],
                ocr_field_ids=[item.field_id for item in evidence_fields],
                reason="该材料有自动识别任务失败，需内部重试或人工核验原件",
            ))
            continue
        explicitly_missing_fields = {field.field_name for field in evidence_fields
                                     if field.field_name in uncovered_fields
                                     and field.value_status is OcrValueStatus.MISSING}
        if explicitly_missing_fields:
            results.append(CheckResult(
                area_id=check.area_id, key=check.key, status="unusable", material_ids=ids,
                rule_ids=["CHECK-EVIDENCE-UNUSABLE-001"] if check.required_now else [],
                finding_ids=[item.finding_id for item in evidence_findings],
                ocr_field_ids=[item.field_id for item in evidence_fields],
                reason="已提交材料，但识别结果明确显示以下关键字段缺失："
                + "、".join(sorted(explicitly_missing_fields)),
                customer_request=f"补充{check.label}，拍清以下字段："
                + "、".join(sorted(explicitly_missing_fields)) if check.required_now else None,
            ))
            continue
        if uncovered_findings and not uncovered_fields and not (
            uncovered_findings & uncertain_findings
        ):
            results.append(CheckResult(
                area_id=check.area_id, key=check.key, status="unassessed",
                material_ids=ids,
                finding_ids=[item.finding_id for item in evidence_findings],
                ocr_field_ids=[item.field_id for item in evidence_fields],
                reason="以下图片检查项未返回识别结果："
                + "、".join(item.value for item in sorted(
                    uncovered_findings, key=lambda value: value.value))
                + "；未返回结果不代表现场没有该类风险。该漏答不触发补材或复核",
            ))
            continue
        if uncovered_findings or uncovered_fields or (
            not expected_findings and not expected_fields
            and not evidence_findings and not evidence_fields
        ):
            gaps = [item.value for item in sorted(uncovered_findings, key=lambda value: value.value)]
            gaps.extend(sorted(uncovered_fields))
            results.append(CheckResult(
                area_id=check.area_id, key=check.key, status="pending", material_ids=ids,
                rule_ids=["CHECK-EVIDENCE-PENDING-001"] if check.required_now else [],
                finding_ids=[item.finding_id for item in evidence_findings],
                ocr_field_ids=[item.field_id for item in evidence_fields],
                reason="自动识别失败或未覆盖检查事项，需内部重试或人工核验"
                + ("：" + "、".join(gaps) if gaps else ""),
            ))
            continue
        results.append(CheckResult(
            area_id=check.area_id, key=check.key, status="valid", material_ids=ids,
            finding_ids=[item.finding_id for item in evidence_findings],
            ocr_field_ids=[item.field_id for item in evidence_fields],
            reason="已有可读材料和关联识别记录；具体风险以逐项结果为准",
        ))
    return results
