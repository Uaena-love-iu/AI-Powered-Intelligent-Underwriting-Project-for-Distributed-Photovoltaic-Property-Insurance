from app.contracts import RuleExplanation, UnderwritingCase, UnderwritingDecision
from app.contracts.enums import (
    DecisionType,
    DetectionStatus,
    InstallationType,
    MaterialCategory,
    MaterialReviewAction,
    OcrValueStatus,
    ProcessingStatus,
    ProcessingStep,
    ProjectType,
    RiskCategory,
    RiskSeverity,
)
from app.location.reporting import coarse_candidate_comparison, distinct_location_evidence
from app.reports.view import _business_text, build_report_view
from app.rules.explanations import explain_rule

DECISION_LABELS = {
    DecisionType.ACCEPT: "建议承保",
    DecisionType.RECOMMEND_REJECT: "建议拒保",
    DecisionType.SURCHARGE: "建议承保（加费）",
    DecisionType.CONDITIONAL_ACCEPT: "建议附条件承保",
    DecisionType.REQUEST_MORE: "建议补充材料",
    DecisionType.MANUAL_REVIEW: "转人工复核",
}


def _has_active_surcharge(decision: UnderwritingDecision) -> bool:
    return decision.decision is DecisionType.SURCHARGE or (
        decision.decision in {DecisionType.ACCEPT, DecisionType.CONDITIONAL_ACCEPT}
        and decision.pricing_recommendation == "surcharge"
    )


def _decision_label(decision: UnderwritingDecision) -> str:
    label = DECISION_LABELS[decision.decision]
    if (decision.decision in {DecisionType.ACCEPT, DecisionType.CONDITIONAL_ACCEPT}
            and _has_active_surcharge(decision)):
        return f"{label}（加费）"
    return label

PROJECT_TYPE_LABELS = {
    ProjectType.ROOFTOP: "工商业屋顶式",
    ProjectType.CARPORT: "车棚顶式",
    ProjectType.MIXED: "屋顶与车棚混合",
    ProjectType.UNSUPPORTED: "暂不支持",
    ProjectType.UNKNOWN: "未知",
}

INSTALLATION_TYPE_LABELS = {
    InstallationType.COLOR_STEEL_ROOF: "彩钢瓦屋顶",
    InstallationType.FLAT_ROOF: "平屋顶",
    InstallationType.TILE_ROOF: "瓦屋顶",
    InstallationType.CARPORT_ROOF: "车棚屋顶",
    InstallationType.UNKNOWN: "未知",
}

MATERIAL_CATEGORY_LABELS = {
    MaterialCategory.PANORAMA: "项目全景",
    MaterialCategory.ROOF_CONNECTION: "屋顶连接",
    MaterialCategory.PARAPET: "女儿墙或护栏",
    MaterialCategory.WORKSHOP: "厂房内部",
    MaterialCategory.FILING_CERTIFICATE: "备案证明",
    MaterialCategory.PROJECT_DOCUMENT: "项目资料",
    MaterialCategory.GRID_CONNECTION_DOCUMENT: "并网材料",
    MaterialCategory.ELECTRICAL_GROUNDING: "电气接地",
    MaterialCategory.COMPONENT_NAMEPLATE: "组件铭牌",
    MaterialCategory.COMPONENT_SURFACE: "组件表面照片",
    MaterialCategory.INVERTER_NAMEPLATE: "逆变器铭牌",
    MaterialCategory.COMBINER_BOX: "汇流箱",
    MaterialCategory.MONITORING_OPTIONAL: "监控材料",
    MaterialCategory.EQUIPMENT_INVENTORY: "设备清单",
    MaterialCategory.OTHER: "其他材料",
}

MATERIAL_ACTION_LABELS = {
    MaterialReviewAction.PASS: "通过",
    MaterialReviewAction.WARNING: "风险提示",
    MaterialReviewAction.SURCHARGE: "建议加费",
    MaterialReviewAction.RECOMMEND_REJECT: "建议拒保",
    MaterialReviewAction.CONDITIONAL: "建议附加条件",
    MaterialReviewAction.REQUEST_MORE: "补充材料",
    MaterialReviewAction.NOT_APPLICABLE: "不适用",
}

OCR_FIELD_LABELS = {
    "document_title": "文件标题",
    "document_type": "文件类型",
    "project_name": "项目名称",
    "construction_content": "建设内容",
    "project_entity": "项目单位",
    "insured_name": "被保险人",
    "insured_address": "被保险地址",
    "site_address": "项目地址",
    "component_model": "组件型号",
    "inverter_model": "逆变器型号",
    "rated_power_w": "额定功率（W）",
    "serial_number": "序列号",
    "grid_connection_date": "并网日期",
    "grounding_resistance_ohm": "接地电阻（Ω）",
    "watermark_present": "照片拍摄水印", "watermark_date": "照片拍摄日期",
    "watermark_time": "照片拍摄时间", "watermark_longitude": "照片水印经度",
    "watermark_latitude": "照片水印纬度", "watermark_address": "照片水印地址",
    "watermark_coordinate_system": "照片水印坐标系", "shot_date": "照片拍摄日期",
    "inspection_date": "检测日期",
    "inspection_result": "检测结论",
    "monitoring_present": "监控系统状态",
}

OCR_STATUS_LABELS = {
    OcrValueStatus.EXTRACTED: "已提取",
    OcrValueStatus.MISSING: "缺失",
    OcrValueStatus.UNCERTAIN: "不确定",
}

DETECTION_STATUS_LABELS = {
    DetectionStatus.DETECTED: "已识别",
    DetectionStatus.NOT_DETECTED: "未识别到",
    DetectionStatus.UNCERTAIN: "不确定",
    DetectionStatus.NOT_APPLICABLE: "不适用",
}

ENVIRONMENT_RELATION_LABELS = {
    "project_site": "项目现场本身",
    "operational_surroundings": "项目邻近周边（边界待核）",
    "distant_background": "远处背景景物",
    "uncertain": "现场关系不确定",
}

RISK_SEVERITY_LABELS = {
    RiskSeverity.INFO: "信息",
    RiskSeverity.LOW: "低",
    RiskSeverity.MEDIUM: "中",
    RiskSeverity.HIGH: "高",
    RiskSeverity.CRITICAL: "严重",
    RiskSeverity.UNKNOWN: "未知",
}

PROCESSING_STEP_LABELS = {
    ProcessingStep.MATERIAL_PARSE: "材料解析",
    ProcessingStep.INSTALLATION_GROUP: "组图安装类型识别",
    ProcessingStep.AISLE_GROUP: "组图检修通道识别",
    ProcessingStep.GEOMETRY_GROUP: "抗灾参数组图估计",
    ProcessingStep.FILING_RULES: "备案阶段规则",
    ProcessingStep.INSTALLATION_RULES: "安装类型规则",
    ProcessingStep.COMPLETENESS: "正式材料完整性判定",
    ProcessingStep.OCR: "文字识别",
    ProcessingStep.EQUIPMENT_INVENTORY: "设备清单解析",
    ProcessingStep.VISION: "图片风险识别",
    ProcessingStep.COMPONENT_LOOKUP: "组件参数查询",
    ProcessingStep.WEATHER_LOOKUP: "气象数据查询",
    ProcessingStep.CATASTROPHE_ASSESSMENT: "灾害风险评估",
    ProcessingStep.FRONT_LOAD_SCREENING: "风雪风险量化",
    ProcessingStep.MAP_REVIEW: "地图辅助复核",
    ProcessingStep.RULE_ENGINE: "规则判断",
    ProcessingStep.DECISION: "综合决策",
}

PROCESSING_STATUS_LABELS = {
    ProcessingStatus.SUCCESS: "成功",
    ProcessingStatus.PARTIAL: "部分成功",
    ProcessingStatus.FAILED: "失败",
    ProcessingStatus.SKIPPED: "已跳过",
}

PROCESSING_STEP_PURPOSE = {
    ProcessingStep.MATERIAL_PARSE: "检查文件可读性并提取材料基础信息",
    ProcessingStep.INSTALLATION_GROUP: "综合本案现场原图确认一个整单安装类型及组件承载结构",
    ProcessingStep.AISLE_GROUP: "综合相关现场原图判断检修通道并定位阻塞位置",
    ProcessingStep.GEOMETRY_GROUP: "仅在前置初审放行后估计倾角、离地高度和粗糙度候选",
    ProcessingStep.FILING_RULES: "核对备案名称或建设用途的禁投依据",
    ProcessingStep.INSTALLATION_RULES: "根据已确认的光伏阵列安装类型决定继续、待核或提前拒保",
    ProcessingStep.COMPLETENESS: "完成专项检查后核对适用材料清单",
    ProcessingStep.OCR: "从文档或图片提取可核对的文字字段",
    ProcessingStep.EQUIPMENT_INVENTORY: "解析设备清单并识别设备型号和数量",
    ProcessingStep.VISION: "识别图像中的风险观察，不单独决定整单结论",
    ProcessingStep.COMPONENT_LOOKUP: "查询组件型号和抗灾参数来源",
    ProcessingStep.WEATHER_LOOKUP: "查询项目坐标对应的历史气象指标",
    ProcessingStep.CATASTROPHE_ASSESSMENT: "比较组件额定能力与历史灾害指标",
    ProcessingStep.FRONT_LOAD_SCREENING: "按城市基本风雪压和安装参数比较组件正面能力",
    ProcessingStep.MAP_REVIEW: "记录人工查看第三方地图页面后的补充观察，不改变自动结论",
    ProcessingStep.RULE_ENGINE: "把材料事实与确定性规则逐项比对",
    ProcessingStep.DECISION: "按固定优先顺序汇总材料动作和评估结果",
}

RESISTANCE_LABELS = {
    "low": "低",
    "medium": "中",
    "high": "高",
    "unknown": "未知",
}

LOSS_RISK_LABELS = {
    "low": "低",
    "medium": "中",
    "high": "高",
    "critical": "严重",
    "unknown": "未评估",
}


def _display(value: object | None) -> str:
    if value is None or value == "":
        return "未提供"
    return str(value)


def _display_measurement(value: float | None) -> str:
    if value is None:
        return "未提供"
    return f"{value:g}"


def _independent_component_load(value: float | None) -> str:
    return "本次未取得独立字段" if value is None else f"{value:g} Pa"


def _table_cell(value: object | None) -> str:
    return _display(value).replace("|", "\\|").replace("\n", " ")


def _bullet_section(title: str, values: list[str]) -> list[str]:
    lines = [f"### {title}", ""]

    if values:
        lines.extend(f"- {value}" for value in values)
    else:
        lines.append("- 无")

    lines.append("")
    return lines


def _missing_basis_lines(case: UnderwritingCase) -> list[str]:
    """Show required gaps with the check they prevent, without promoting optional checks."""
    decision = case.decision
    if decision is None:
        return []
    lines = ["### 核心依据：缺失材料与受阻核验", ""]
    used: set[str] = set()
    rows: list[tuple[str, str, str, str | None]] = []

    def add(name: str, request: str | None, blocked: str, rule_id: str | None = None) -> None:
        if request and request not in used:
            used.add(request)
            rows.append((name, request, blocked, rule_id))

    for check in case.applicable_checks:
        if not check.required_now or check.applicability != "applicable":
            continue
        outcome = next((item for item in case.check_results
                        if item.area_id == check.area_id and item.key == check.key), None)
        if outcome is None or outcome.status not in {"missing", "unusable"} or not outcome.customer_request:
            continue
        add(check.label, outcome.customer_request,
            f"无法完成「{check.label}」核查：{check.evidence_requirement}。",
            outcome.rule_ids[0] if outcome.rule_ids else None)
        if check.key == "filing":
            used.add("补充电站备案证")

    requests = list(dict.fromkeys(decision.missing_requirements))
    for request in requests:
        if request in used:
            continue
        review = next((item for item in case.material_reviews if request in item.missing_requirements), None)
        rule_id = next((item for item in (review.triggered_rule_ids if review else [])
                        if item == "IMG-WATERMARK-DATE-001"), None)
        if rule_id is None and review and review.triggered_rule_ids:
            rule_id = review.triggered_rule_ids[0]
        if request == "补充清晰水印信息：拍摄日期":
            add("带清晰拍摄日期水印的现场照片", request,
                f"无法核验现场照片的拍摄日期；缺少拟起保日期时，{case.ruleset_snapshot.get('parameters', {}).get('watermark_days', 15)}日时效也无法完成核验。", rule_id)
        elif request == "补充项目准确经纬度":
            add("项目准确经纬度", request, "无法定位项目并查询所在地的历史气象指标。", rule_id)
        elif request == "补充光伏组件完整型号":
            add("光伏组件完整型号", request, "无法匹配组件抗灾参数并完成相应灾害能力比较。", rule_id)
        else:
            add(request, request, "本次结果未记录该缺口对应的具体受阻任务，需核保人员核对。", rule_id)

    if not rows:
        return lines + ["- 本次结果未保存可逐项核对的补材依据，请查看完整核验记录。", ""]
    for index, (name, request, blocked, _rule_id) in enumerate(rows, 1):
        lines.extend([f"#### {index}. {name}", "", f"- 需补充：{request}",
                      f"- 无法完成：{blocked}", ""])
    return lines


def _warning_rule_id(case: UnderwritingCase, warning: str) -> str | None:
    if warning.startswith("拟起保日期未提供"):
        return "IMG-WATERMARK-DATE-001"
    if warning.startswith("部分自动识别服务未完整执行"):
        return "SYS-PROVIDER-FAILURE"
    for review in case.material_reviews:
        if warning not in review.reasons:
            continue
        if (warning.startswith("交叉核对范围提示：")
                and "DOC-COMPARISON-FIELD-UNAVAILABLE-001" in review.triggered_rule_ids):
            return "DOC-COMPARISON-FIELD-UNAVAILABLE-001"
        preferred = (
            "IMG-WATERMARK-001" if "水印" in warning else
            "ENV-BACKGROUND-CONTEXT-001" if "远处背景" in warning else
            "ENV-SITE-RELATION-REVIEW-001" if "邻近周边" in warning else
            "IMG-FINDING-WARNING-001"
        )
        if preferred in review.triggered_rule_ids:
            return preferred
        return review.triggered_rule_ids[0] if len(review.triggered_rule_ids) == 1 else None
    for stage in case.stage_reviews:
        if warning in stage.pending_questions and stage.rule_ids:
            return stage.rule_ids[0]
    return None


def _warning_lines(case: UnderwritingCase) -> list[str]:
    if case.decision is None or not case.decision.warnings:
        return []
    lines: list[str] = []
    grouped: dict[str, list[str]] = {"核对范围提示": [], "风险提示": []}
    for warning in dict.fromkeys(case.decision.warnings):
        title = ("核对范围提示" if _warning_rule_id(case, warning)
                 == "DOC-COMPARISON-FIELD-UNAVAILABLE-001" else "风险提示")
        grouped[title].append(warning)
    for title, warnings in grouped.items():
        if not warnings:
            continue
        lines.extend([f"### {title}", ""])
        for warning in warnings:
            lines.append(f"- {_business_text(warning)}")
        lines.append("")
    return lines


def _finding_rule_id(case: UnderwritingCase, finding_id: str) -> str | None:
    finding = next((item for item in case.findings if item.finding_id == finding_id), None)
    if finding is None:
        return None
    reviews = [item for item in case.material_reviews if finding_id in item.finding_ids]
    for review in reviews:
        ids = review.triggered_rule_ids
        if finding.category.value.endswith("_environment"):
            preferred = {
                "distant_background": "ENV-BACKGROUND-CONTEXT-001",
                "operational_surroundings": "ENV-SITE-RELATION-REVIEW-001",
                "project_site": "ENV-EXCLUDED-001",
            }.get(finding.environment_relation.value)
            if preferred in ids:
                return preferred
        elif "IMG-FINDING-WARNING-001" in ids:
            return "IMG-FINDING-WARNING-001"
        if len(ids) == 1:
            return ids[0]
    return None


def _rule_explanation_lines(
    rule_ids: list[str], explanation_by_id: dict[str, RuleExplanation]
) -> list[str]:
    if not rule_ids:
        return ["- 命中规则：无"]

    lines = []
    for rule_id in rule_ids:
        explanation = explanation_by_id.get(rule_id) or explain_rule(rule_id)
        lines.append(
            f"- **{rule_id}｜{explanation.title}**："
            f"触发条件：{_table_cell(explanation.trigger)}；"
            f"处理作用：{_table_cell(explanation.effect)}"
        )
    return lines


def _decision_priority_explanation(decision: DecisionType) -> str:
    return {
        DecisionType.RECOMMEND_REJECT: "命中建议拒保条件，因此输出建议拒保。",
        DecisionType.REQUEST_MORE: "未先命中建议拒保条件，但存在必需信息或材料缺口，因此输出补充材料。",
        DecisionType.MANUAL_REVIEW: "存在位置冲突或其他需人工核实事项，因此转人工复核；位置冲突可优先于一般补材要求。",
        DecisionType.SURCHARGE: "前序拒保、补充材料和人工复核条件均未触发，规则命中加费建议。",
        DecisionType.CONDITIONAL_ACCEPT: "前序更高优先级条件均未触发，规则命中附条件承保建议。",
        DecisionType.ACCEPT: "前序风险、资料缺口和人工复核条件均未触发，规则输出建议承保。",
    }[decision]


def _decisive_evidence_lines(case: UnderwritingCase) -> list[str]:
    """Keep each decisive explanation beside the original material it describes."""

    names = {item.material_id: item.file_name for item in case.materials}
    scene_names = {
        "building_roof": "建筑屋顶式", "carport_roof": "车棚顶式",
        "ground_mount": "地面支架式", "floating": "水上光伏",
        "other_canopy": "其他棚架式", "building_facade": "建筑立面式",
        "other_known": "其他安装类型", "unknown": "安装类型待核",
    }
    rows: list[tuple[str, str, str]] = []
    for stage in case.stage_reviews:
        if stage.action != "recommend_reject":
            continue
        if stage.stage == "filing":
            fields = [field for field in case.ocr_fields
                      if field.field_id in stage.ocr_field_ids
                      and ("DOC-FILING-NAME-CONFLICT-001" in stage.rule_ids
                           or (field.filing_assessment is not None
                               and field.filing_assessment.status == "confirmed"))]
            for field in fields:
                explanation = (field.filing_assessment.reason
                               if field.filing_assessment else None) or (
                    stage.reasons[0] if stage.reasons else "请核对备案原文"
                )
                original = field.raw_value or field.normalized_value or "未保存原文"
                rows.append((
                    names.get(field.material_id, "原件未保留"),
                    f"{OCR_FIELD_LABELS.get(field.field_name, field.field_name)}：{original}",
                    explanation,
                ))
        elif stage.stage in {"installation_type", "installation_scope"}:
            cited = set(stage.material_ids)
            for area in case.installation_analysis.areas if case.installation_analysis else []:
                if area.status != "confirmed":
                    continue
                for region in area.evidence_regions:
                    if cited and region.material_id not in cited:
                        continue
                    rows.append((
                        names.get(region.material_id, "原件未保留"),
                        scene_names.get(area.scene_type.value, "安装类型现场证据"),
                        region.description,
                    ))
    environment_categories = {
        RiskCategory.AGRICULTURE_ENVIRONMENT, RiskCategory.FOREST_ENVIRONMENT,
        RiskCategory.LIVESTOCK_ENVIRONMENT, RiskCategory.FISHERY_ENVIRONMENT,
        RiskCategory.WATER_ADJACENT_ENVIRONMENT, RiskCategory.TIDAL_FLAT_ENVIRONMENT,
        RiskCategory.MOUNTAIN_ENVIRONMENT, RiskCategory.DESERTIFICATION_ENVIRONMENT,
    }
    def decisive_rule(category: RiskCategory) -> str | None:
        if category in environment_categories:
            return "ENV-EXCLUDED-001"
        return {
            RiskCategory.SEVERE_SHADING: "IMG-SHADING-SEVERE-001",
            RiskCategory.UNPROTECTED_CABLE: "ELEC-UNPROTECTED-CABLE-REJECT-001",
        }.get(category)

    reviewed: dict[str, set[str]] = {}
    for review in case.material_reviews:
        if review.action == MaterialReviewAction.RECOMMEND_REJECT:
            for finding_id in review.finding_ids:
                reviewed.setdefault(finding_id, set()).update(review.triggered_rule_ids)
    for finding in case.findings:
        if (finding.finding_id in reviewed
                and decisive_rule(finding.category) in reviewed[finding.finding_id]
                and finding.detection_status == DetectionStatus.DETECTED):
            rows.append((names.get(finding.material_id, "原件未保留"),
                         finding.label, finding.evidence_text))
    if not rows:
        return []
    lines = ["### 决定性依据（逐项对应原图）", "",
             "> 每条说明仅对应标题中的原图。Markdown 文件不包含图片附件；请在案件材料中打开同名原件。", ""]
    for index, (file_name, title, explanation) in enumerate(rows, start=1):
        lines.extend([
            f"#### 证据 {index}｜{_table_cell(file_name)}", "",
            f"- 判断事项：{_table_cell(title)}",
            f"- 该图证据：{_table_cell(explanation)}",
            "",
        ])
    return lines


def generate_markdown_report(
    case: UnderwritingCase,
    *,
    human_reviews: list[dict[str, str]] | None = None,
) -> str:
    """把结构化核保案件转换为便于业务人员阅读的 Markdown 报告。"""

    project = case.project
    view = build_report_view(case)
    overview_lines = [
        "## 案件概况（留档）",
        "",
        "| 项目 | 内容 |",
        "| --- | --- |",
        f"| 案件编号 | {_table_cell(case.case_id)} |",
        f"| 本轮运行开始 | {_table_cell(case.run_started_at)} |",
        f"| 项目名称 | {_table_cell(project.project_name)} |",
        f"| 被保险人 | {_table_cell(project.insured_name)} |",
        f"| 项目单位 | {_table_cell(project.project_entity)} |",
        f"| 企业所属行业 | {_table_cell(project.industry_name)} |",
        f"| 项目类型 | {PROJECT_TYPE_LABELS[project.project_type]} |",
        f"| 安装类型 | {INSTALLATION_TYPE_LABELS[project.installation_type]} |",
        f"| 项目地址 | {_table_cell(project.site_address)} |",
        f"| 组件型号 | {_table_cell(project.component_model)} |",
        f"| 计划起保日期 | {_table_cell(project.proposed_start_date)} |",
        f"| 被保险地址 | {_table_cell(project.insured_address)} |",
        "",
    ]
    current_actions = [
        item for item in view["actions"]
        if view["decision"]["code"] != "recommend_reject"
        or item["text"] == "核对拒保原件、现场关系与适用规则"
    ]
    deferred_actions = [
        item for item in view["actions"]
        if view["decision"]["code"] == "recommend_reject"
        and item["text"] != "核对拒保原件、现场关系与适用规则"
    ]
    lines = [
        "# 分布式光伏财产险 AI 核保报告",
        "",
        "> 本报告由系统根据当前材料自动生成，最终承保结论需由授权核保人员确认。",
        "> 对于未识别到风险的图片，结论仅限当前图像和模型的识别范围，不代表现场无风险。",
        "",
        (f"案件：{_table_cell(case.case_id)} · {_table_cell(project.project_name)} · "
         f"{_table_cell(project.insured_name)} · {_table_cell(project.site_address)}"),
        "",
        "## 一屏结论",
        "",
        f"- **系统建议：**{view['decision']['label']}（待核保员确认）",
        f"- **当前推进：**{view['decision']['progression']}；{view['decision']['workflow_action']}",
        ("- **下一步：**" + "；".join(_table_cell(item["text"])
                                        for item in current_actions[:2])
         + (f"；另有 {len(current_actions) - 2} 项待核事项"
            if len(current_actions) > 2 else "")
         if current_actions else "- **下一步：**本轮未记录可执行事项，请核对完整记录。"),
        "",
        "### 关键依据",
        "",
    ]
    if view["basis"]:
        for item in view["basis"][:3]:
            cited = [view["references"][ref]["label"] for ref in item["reference_ids"]
                     if ref in view["references"]]
            lines.append(f"- **{_table_cell(item['text'])}**。作用：{_table_cell(item['impact'])}"
                         + (f"；关联：{'、'.join(dict.fromkeys(cited[:3]))}" if cited else
                            "；原件或规则关联待核对"))
    else:
        lines.append("- 本轮未保存可核对的关键依据，请查看完整记录。")
    if view["decision"]["code"] != "request_more":
        lines.extend(["", "### 下一步事项", ""])
        if current_actions:
            for item in current_actions:
                lines.append(f"- **{_table_cell(item['text'])}**；"
                             f"当前影响：{_table_cell(item['blocked_check'])}；"
                             f"处理后：{_table_cell(item['after'])}")
        else:
            lines.append("- 本轮未记录可执行待办，需核保员核对完整记录。")
    component_source = (
        f"{_table_cell(case.component_profile.source_name)}（取得于 "
        f"{case.component_profile.retrieved_at.date().isoformat()}）"
        if case.component_profile else "未取得"
    )
    weather_source = (
        f"{_table_cell(case.weather_profile.source_name)}（取得于 "
        f"{case.weather_profile.retrieved_at.date().isoformat()}）"
        if case.weather_profile else "未取得"
    )
    location_status = (
        "气象查询位置已核对，仍需确认组件参数适用于本项目安装条件"
        if case.location_assessment and case.location_assessment.weather_coordinates_verified
        else "气象查询位置尚未完成核对；核保员先核实项目位置，再确认是否需要系统重新查询"
    )
    if case.pre_catastrophe_outcome and case.pre_catastrophe_outcome != "continue":
        lines.extend(["", "## 抗灾筛查", "",
                      "- 前置审核尚未通过，本轮未执行风、雹、雪抗灾能力筛查。"])
    else:
        lines.extend(["", "## 风、雹、雪初步检查", "",
                      f"- 资料来源：组件参数 {component_source}；当地历史天气 {weather_source}。",
                      f"- 位置与适用性：{location_status}。",
                      "- 单位说明：Pa 为承受的压力，m/s 为风速；不同单位不能直接比较。",
                      "",
                      "| 灾害 | 组件厂家资料 | 当地历史天气 | 目前能否判断 | 还需核对什么 |",
                      "| --- | --- | --- | --- | --- |"])
        for item in view["hazards"]:
            status = "可做初步比较，尚不能下承保结论" if item["status"] == "calculated" else (
                "未检查" if item["status"] == "not_executed" else "无法判断"
            )
            missing = []
            if item["capacity"] is None:
                missing.append("缺组件厂家数据")
            if item["event"] is None:
                missing.append("缺当地历史天气数据")
            if item["status"] == "calculated":
                missing.append("核对项目设计要求、安装方式与厂家试验条件")
            if not missing:
                missing.append(item["reason"])
            capacity = (f"{_display_measurement(item['capacity'])} {item['unit']}"
                        if item["capacity"] is not None else "未取得")
            event_unit = "m/s" if item["hazard"] == "wind" else item["unit"]
            event = (f"{_display_measurement(item['event'])} {event_unit}"
                     if item["event"] is not None else "未取得")
            if item["hazard"] == "wind" and item["demand"] is not None:
                event += f"；按模型换算的风荷载 {_display_measurement(item['demand'])} Pa"
            lines.append(f"| {item['label']} | {capacity} | {event} | {status} | "
                         f"{_table_cell('；'.join(missing))} |")
    lines.extend(["", "## 综合核保意见（详细）",
        "",
    ])

    if case.decision is None:
        lines.extend(
            [
                "- 建议结论：尚未生成",
                "- 当前状态：等待规则判断或人工复核",
                "",
            ]
        )

    else:
        decision = case.decision

        lines.extend(
            [
                f"- 建议结论：**{_decision_label(decision)}**",
                f"- 价格建议：{'建议加费，具体幅度由核保员依据正式费率确认' if _has_active_surcharge(decision) else '未提出加费建议'}",
                f"- 结论生成时间：{decision.generated_at.isoformat()}",
                "",
            ]
        )

        if decision.decision == DecisionType.REQUEST_MORE:
            lines.extend(_missing_basis_lines(case))
        else:
            if decision.reasons:
                lines.extend(_bullet_section("判断理由", [_business_text(text) for text in decision.reasons]))
            if decision.missing_requirements:
                lines.extend(_bullet_section("需要补充的材料", [_business_text(text) for text in decision.missing_requirements]))
        if decision.conditions:
            lines.extend(_bullet_section("附加承保条件", decision.conditions))
        lines.extend(_warning_lines(case))
        lines.extend(_decisive_evidence_lines(case))

    lines.extend(overview_lines)
    lines.extend(["---", "", "> 以下为完整核验记录，供复核与追溯；业务结论及逐图证据见上方。", ""])
    if view["decision"]["code"] == "request_more" and current_actions:
        lines.extend(["### 处理事项完整记录", ""])
        for item in current_actions:
            lines.append(f"- **{_table_cell(item['text'])}**；"
                         f"当前影响：{_table_cell(item['blocked_check'])}；"
                         f"处理后：{_table_cell(item['after'])}")
        lines.append("")
    if deferred_actions:
        lines.extend(["### 拒保依据复核后的其他事项", ""])
        for item in deferred_actions:
            lines.append(f"- **{_table_cell(item['text'])}**；"
                         f"受阻检查：{_table_cell(item['blocked_check'])}")
        lines.append("")

    if case.package_assessment is not None:
        package = case.package_assessment
        lines.extend(
            [
                "### 材料覆盖统计（不按固定数量触发补材）",
                "",
                f"- 图片材料：{package.image_count} 张；全景照：{package.panorama_count} 张",
                (
                    f"- 备案证：{'已提供' if package.has_filing_certificate else '缺失'}；"
                    f"全景视角（仅供复核参考，不影响缺材判断）："
                    f"正面平视{'已标注' if package.has_front_level_panorama else '未标注'}、"
                    f"俯拍{'已标注' if package.has_overhead_panorama else '未标注'}"
                ),
                "- 补材要求由下方适用检查项和实际风险规则决定",
                "",
            ]
        )
    duplicates = [item for item in case.materials if item.duplicate_of_material_id]
    if duplicates:
        lines.extend(["### 重复材料去重记录", ""])
        for item in duplicates:
            lines.append(
                f"- {_table_cell(item.file_name)} 与材料 {item.duplicate_of_material_id} 内容相同；"
                "本轮不重复识别或计数。"
            )
        lines.append("")
    if any(item.category_source or item.additional_categories for item in case.materials):
        lines.extend(["### 材料分类与人工复核记录", "",
                      "| 材料 | 本轮主类别 | 本轮采用的其他用途 | 分类来源 | 说明 |",
                      "| --- | --- | --- | --- | --- |"])
        for item in case.materials:
            lines.append(
                f"| {_table_cell(item.file_name)} | {MATERIAL_CATEGORY_LABELS[item.category]} "
                f"| {_table_cell('、'.join(MATERIAL_CATEGORY_LABELS[category] for category in item.additional_categories) or '无')} "
                f"| {_table_cell(item.category_source)} | {_table_cell(item.category_note)} |"
            )
        lines.append("")

    if case.materials:
        lines.extend([
            "### 材料质量与水印原始状态", "",
            "| 材料 | 可读性 | 预检限制 | 水印 | 拍摄时间 | 水印坐标 |",
            "| --- | --- | --- | --- | --- | --- |",
        ])
        for item in case.materials:
            coordinates = (f"{item.longitude}, {item.latitude}"
                           if item.longitude is not None and item.latitude is not None
                           else None)
            lines.append(
                f"| {_table_cell(item.file_name)} | {_table_cell(item.quality_status.value)} "
                f"| {_table_cell('、'.join(item.quality_issues) or '无')} "
                f"| {_table_cell(item.watermark_status.value)} "
                f"| {_table_cell(item.captured_at)} | {_table_cell(coordinates)} |"
            )
        lines.append("")

    if case.location_assessment is not None:
        location = case.location_assessment
        comparison = coarse_candidate_comparison(location)
        status_labels = {
            "verified": "多来源位置一致",
            "distance_screened": "500 米原始距离初筛通过（未作精确位置核验）",
            "single_source": "仅一个位置来源，待核实",
            "conflict": "位置冲突，暂停自动通过",
            "uncertain": "位置证据不足，待核实",
            "missing": "缺少位置证据",
        }
        lines.extend([
            "### 项目位置核验",
            "",
            f"- 核验结果：{status_labels[location.status]}",
            "- 气象查询位置：" + (
                f"{location.weather_coordinate_source}（{'已交叉核验' if location.weather_coordinates_verified else '未经交叉核验，仅作候选参考'}）"
                if location.weather_coordinate_source else "未取得可用坐标"
            ),
            *[f"- 说明：{note}" for note in location.notes],
            *([f"- {'位置距离说明' if location.status == 'distance_screened' else '位置距离与复核原因'}：{comparison}"] if comparison else []),
            "",
            "| 来源 | 文件 | 地址 | 经度 | 纬度 | 坐标系 | 地图定位精度 |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ])
        for evidence in distinct_location_evidence(location):
            lines.append(
                f"| {_table_cell(evidence.source)} | {_table_cell(evidence.file_name or evidence.material_id)} | "
                f"{_table_cell(evidence.address)} | {_table_cell(evidence.longitude)} | "
                f"{_table_cell(evidence.latitude)} | {_table_cell(evidence.coordinate_system)} | "
                f"{_table_cell(evidence.geocode_level)} |"
            )
        lines.append("")

    material_by_id = {
        material.material_id: material
        for material in case.materials
    }
    if case.stage_reviews:
        stage_labels = {
            "filing": "备案关键事实与规则",
            "installation_type": "组图安装类型与规则动作",
            "installation_scope": "组图安装类型与规则动作",
            "aisle_group": "组图检修通道判断",
            "single_image": "单份材料专项规则",
            "completeness": "正式缺材判定",
            "location": "项目位置核验",
        }
        action_labels = {
            "continue": "继续", "recommend_reject": "建议拒保",
            "manual_review": "人工复核", "request_more": "建议补材",
        }
        lines.extend([
            "### 抗灾评估前的阶段动作与证据",
            "",
            "- 本阶段主意见：" + _display(case.pre_catastrophe_outcome),
            "",
        ])
        for index, stage in enumerate(case.stage_reviews, start=1):
            files = [material_by_id[mid].file_name if mid in material_by_id else mid
                     for mid in stage.material_ids]
            lines.extend([
                f"#### {index}. {stage_labels[stage.stage]}｜{action_labels[stage.action]}",
                "",
                "- 命中规则：" + ("、".join(stage.rule_ids) or "无"),
                "- 关联材料：" + ("、".join(files) or "无"),
                "- OCR 字段编号：" + ("、".join(stage.ocr_field_ids) or "无"),
                "- 视觉发现编号：" + ("、".join(stage.finding_ids) or "无"),
                "- 动作依据：" + ("；".join(_business_text(text) for text in stage.reasons) or "未触发阻断规则"),
                *[f"- 原始证据：{_table_cell(evidence)}" for evidence in stage.evidence_texts],
                *[f"- 待核事项：{_table_cell(_business_text(question))}" for question in stage.pending_questions],
                *[f"- 客户补充要求：{_table_cell(_business_text(request))}" for request in stage.customer_requests],
                "- 后续动作：" + ("结束本轮自动分析" if stage.terminates_run else "按流程继续或汇总"),
                "",
            ])
        if case.installation_analysis is not None:
            single_project_result = (len(case.installation_analysis.areas) == 1
                                     and case.installation_analysis.areas[0].area_id == "project")
            lines.extend(["#### 整单安装类型" if single_project_result
                          else "#### 旧版安装区域识别记录", ""])
            if case.installation_analysis.filing_field_ids:
                lines.append(
                    "- 辅助备案字段：" + "、".join(case.installation_analysis.filing_field_ids)
                )
            for area in case.installation_analysis.areas:
                lines.append(
                    f"- {'' if single_project_result else area.area_id + '｜'}{area.scene_type.value}｜{area.status}｜"
                    f"材料：{_table_cell('、'.join(area.material_ids))}｜"
                    f"承载：{_table_cell(area.supporting_structure)}｜"
                    f"用途：{_table_cell(area.usage)}｜证据：{_table_cell(area.evidence_text)}"
                )
                for region in area.evidence_regions:
                    bbox = (f"({region.bbox.x_min:.2f}, {region.bbox.y_min:.2f})"
                            f"—({region.bbox.x_max:.2f}, {region.bbox.y_max:.2f})"
                            if region.bbox is not None else "未可靠定位")
                    lines.append(
                        f"  - 原图 {region.material_id}｜区域 {bbox}｜"
                        f"{_table_cell(region.description)}"
                    )
            lines.append("")
    if case.applicable_checks:
        result_by_key = {(item.area_id, item.key): item for item in case.check_results}
        status_labels = {
            "valid": "有效", "missing": "缺失", "not_provided": "可选未提供",
            "unusable": "已交但不可用",
            "not_applicable": "有证据支持不适用", "pending": "适用性或识别待核",
            "unassessed": "图片识别未返回（仅提示）",
        }
        lines.extend([
            "### 适用检查清单与正式材料状态", "",
            "| 检查对象 | 检查项 | 本轮适用性 | 材料状态 | 规则 | 证据来源 | 处理要求 |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ])
        for check in case.applicable_checks:
            result = result_by_key.get((check.area_id, check.key))
            source = "；".join(filter(None, [
                "材料：" + "、".join(result.material_ids) if result and result.material_ids else "",
                "视觉：" + "、".join(result.finding_ids) if result and result.finding_ids else "",
                "文字：" + "、".join(result.ocr_field_ids) if result and result.ocr_field_ids else "",
            ]))
            requirement = _business_text(result.customer_request or result.reason if result else check.reason)
            lines.append(
                f"| {_table_cell('整单' if check.area_id == 'project' else check.area_id)} | {_table_cell(check.label)} "
                f"| {_table_cell(check.applicability)}（{'本轮必需' if check.required_now else '记录覆盖'}） "
                f"| {('可选未提供' if not check.required_now and result.status == 'missing' else status_labels.get(result.status, result.status)) if result else '未判定'} "
                f"| {_table_cell('、'.join(result.rule_ids) if result else None)} "
                f"| {_table_cell(source)} | {_table_cell(requirement)} |"
            )
        lines.append("")
    if case.geometry_analysis is not None:
        lines.extend(["### 抗灾阶段组图参数候选", "",
                      "> 下列结果仅为带证据的粗估；当前灾害计算模块尚未把这些值代入工程公式。",
                      ""])
        for area in case.geometry_analysis.areas:
            for label, metric in (
                ("组件倾角（度）", area.tilt_degrees),
                ("组件离地高度（米）", area.height_above_ground_m),
                ("地面粗糙度候选", area.ground_roughness),
            ):
                value = (metric.roughness_candidate or
                         (f"{metric.value_min:g}—{metric.value_max:g}"
                          if metric.value_min is not None and metric.value_max is not None
                          else "未知"))
                lines.append(
                    f"- {'整单' if area.area_id == 'project' else area.area_id}｜{label}：{value}；状态：{metric.status}；"
                    f"材料：{_table_cell('、'.join(metric.material_ids))}；"
                    f"证据：{_table_cell(metric.evidence_text)}"
                )
        lines.append("")
    explanation_by_id = {
        item.rule_id: item
        for item in (case.decision.rule_explanations if case.decision else [])
    }
    lines.extend(
        [
            "## 判断过程与追溯",
            "",
            "- **模型与规则分工：** OCR/视觉模型提供字段提取、风险观察和证据文字；模型置信度是参考分数，尚未经过概率校准。材料动作和整单建议由程序规则汇总，模型不直接生成整单结论。",
            "- **整单汇总顺序：** 明确拒保事实 → 位置冲突人工复核 → 补充材料 → 其他人工复核 → 附条件承保 → 建议承保；价格建议独立判断，仅在建议承保或建议附条件承保时展示。",
            "",
        ]
    )
    if case.decision is None:
        lines.extend(["- 当前尚未生成整单结论。", ""])
    else:
        decision = case.decision
        lines.extend(
            [
                f"- **本案结论：** {_decision_label(decision)}。{_decision_priority_explanation(decision.decision)}",
                "- **参与整单汇总的规则：**",
                *[
                    f"  {line}"
                    for line in _rule_explanation_lines(
                        decision.decisive_rule_ids,
                        explanation_by_id,
                    )
                ],
                "",
            ]
        )

    findings_by_id = {item.finding_id: item for item in case.findings}
    ocr_fields_by_id = {item.field_id: item for item in case.ocr_fields}
    lines.extend(["### 单份材料的证据链", ""])
    if case.material_reviews:
        for review in case.material_reviews:
            material = material_by_id.get(review.material_id)
            material_name = material.file_name if material is not None else review.material_id
            lines.extend(
                [
                    f"#### {_table_cell(material_name)} → {MATERIAL_ACTION_LABELS[review.action]}",
                    "",
                    "- 规则理由：" + ("；".join(_business_text(text) for text in review.reasons) or "未提供文字理由"),
                    "- 命中规则：",
                    *[
                        f"  {line}"
                        for line in _rule_explanation_lines(
                            review.triggered_rule_ids,
                            explanation_by_id,
                        )
                    ],
                    "- 关联视觉证据：",
                ]
            )
            if review.finding_ids:
                for finding_id in review.finding_ids:
                    finding = findings_by_id.get(finding_id)
                    if finding is None:
                        lines.append(f"  - {finding_id}：引用记录未找到。")
                        continue
                    environment_relation = (
                        ENVIRONMENT_RELATION_LABELS.get(
                            finding.environment_relation.value,
                            finding.environment_relation.value,
                        )
                        if finding.category.value.endswith("_environment")
                        else "不适用"
                    )
                    lines.append(
                        f"  - {finding_id}｜{_table_cell(finding.label)}｜"
                        f"{DETECTION_STATUS_LABELS[finding.detection_status]}｜"
                        f"环境关系：{environment_relation}｜"
                        f"证据：{_table_cell(finding.evidence_text)}｜"
                        f"参考置信度：{finding.confidence:.2f}（未校准）"
                    )
            else:
                lines.append("  - 无直接关联的视觉发现。")
            lines.append("- 关联 OCR 字段：")
            if review.ocr_field_ids:
                for field_id in review.ocr_field_ids:
                    field = ocr_fields_by_id.get(field_id)
                    if field is None:
                        lines.append(f"  - {field_id}：引用记录未找到。")
                        continue
                    field_label = OCR_FIELD_LABELS.get(field.field_name, field.field_name)
                    lines.append(
                        f"  - {field_id}｜{field_label}｜原文：{_table_cell(field.raw_value)}｜"
                        f"规范值：{_table_cell(field.normalized_value)}｜"
                        f"来源说明：{_table_cell(field.evidence_text)}｜"
                        f"状态：{OCR_STATUS_LABELS[field.value_status]}｜"
                        f"参考置信度：{field.confidence:.2f}（未校准）"
                    )
            else:
                lines.append("  - 无直接关联的 OCR 字段。")
            lines.append("")
    else:
        lines.extend(["- 当前没有生成逐份材料审核记录。", ""])

    if case.catastrophe_assessment is not None:
        lines.extend(
            [
                "### 自然灾害评估引用的规则",
                "",
                *[
                    f"  {line}"
                    for line in _rule_explanation_lines(
                        case.catastrophe_assessment.triggered_rule_ids,
                        explanation_by_id,
                    )
                ],
                "",
            ]
        )

    lines.extend(["### 自动处理步骤及其用途", ""])
    if case.processing_trace:
        for trace in case.processing_trace:
            step_name = PROCESSING_STEP_LABELS[trace.step]
            purpose = PROCESSING_STEP_PURPOSE[trace.step]
            detail = (
                f"{step_name}：{purpose}；执行方 {_table_cell(trace.provider)}"
                f"；模型 {_table_cell(trace.model)}；状态 {PROCESSING_STATUS_LABELS[trace.status]}"
                f"；耗时 {trace.latency_ms} ms。"
            )
            if trace.error_code or trace.error_message:
                detail += f" 留痕错误：{_table_cell(trace.error_code)} {_table_cell(trace.error_message)}。"
            if trace.status in {ProcessingStatus.PARTIAL, ProcessingStatus.FAILED}:
                detail += " 该步骤未完整执行，因此整单会提示人工复核。"
            lines.append(f"- {detail}")
    else:
        lines.append("- 当前没有自动处理留痕。")
    lines.append("")

    reviewed_finding_ids = {
        finding_id
        for review in case.material_reviews
        for finding_id in review.finding_ids
    }
    report_findings = (
        [item for item in case.findings if item.finding_id in reviewed_finding_ids]
        if case.material_reviews
        else case.findings
    )

    lines.extend(
        [
            "## 3. 材料审核结果",
            "",
            "| 文件 | 材料类别 | 审核动作 | 命中规则 | 人工复核 | 原因 |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )

    if case.material_reviews:
        for review in case.material_reviews:
            material = material_by_id[review.material_id]
            reasons = "；".join(_business_text(text) for text in review.reasons) or "无"
            manual_review = (
                "是" if review.requires_manual_review else "否"
            )

            lines.append(
                f"| {_table_cell(material.file_name)} "
                f"| {MATERIAL_CATEGORY_LABELS[material.category]} "
                f"| {MATERIAL_ACTION_LABELS[review.action]} "
                f"| {_table_cell('、'.join(review.triggered_rule_ids) or '无')} "
                f"| {manual_review} "
                f"| {_table_cell(reasons)} |"
            )
    else:
        lines.append("| 无 | 无 | 无 | 无 | 无 | 无 |")

    lines.extend(
        [
            "",
            "## 4. OCR 字段提取结果",
            "",
            "| 材料 | 字段 | 状态 | 原文 | 标准化结果 | 来源说明 | 识别参考分数（未校准） | 位置 |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )

    if case.ocr_fields:
        for field in case.ocr_fields:
            material = material_by_id[field.material_id]
            location = (
                "未定位"
                if field.bbox is None
                else (
                    f"({field.bbox.x_min:.2f}, {field.bbox.y_min:.2f})-"
                    f"({field.bbox.x_max:.2f}, {field.bbox.y_max:.2f})"
                )
            )
            lines.append(
                f"| {_table_cell(material.file_name)} "
                f"| {_table_cell(OCR_FIELD_LABELS.get(field.field_name, field.field_name))} "
                f"| {OCR_STATUS_LABELS[field.value_status]} "
                f"| {_table_cell(field.raw_value)} "
                f"| {_table_cell(field.normalized_value)} "
                f"| {_table_cell(field.evidence_text)} "
                f"| {field.confidence:.2f} "
                f"| {location} |"
            )
    else:
        lines.append("| 无 | 无 | 无 | 无 | 无 | 无 | 无 | 无 |")

    filing_fields = [field for field in case.ocr_fields if field.filing_assessment]
    if filing_fields:
        assessment_labels = {
            "confirmed": "模型确认命中", "not_found": "模型未发现",
            "uncertain": "模型判断存疑",
        }
        generation_labels = {"distributed": "分布式", "centralized": "集中式"}
        site_labels = {
            "building_roof": "建筑屋顶", "carport_roof": "车棚顶",
            "other": "非屋顶／非车棚",
        }
        prohibition_labels = {
            "centralized": "集中式", "forest_pv": "林业复合利用",
            "fishery_pv": "渔业复合利用", "tidal_flat": "滩涂",
            "under_panel_cultivation": "组件下方种植", "agriculture": "涉农",
            "forestry": "涉林", "livestock": "畜牧", "water": "涉水",
            "mountain": "山地", "desert": "沙漠",
            "unsupported_installation": "非屋顶／非车棚安装",
        }
        lines.extend(["", "### 备案证建设方式与环境判断", ""])
        for field in filing_fields:
            assessment = field.filing_assessment
            location = f"{material_by_id[field.material_id].file_name}"
            if field.page_number:
                location += f" 第 {field.page_number} 页"
            if assessment.evidence_bbox:
                box = assessment.evidence_bbox
                location += (f" ({box.x_min:.2f}, {box.y_min:.2f})"
                             f"-({box.x_max:.2f}, {box.y_max:.2f})")
            lines.append(
                f"- {OCR_FIELD_LABELS.get(field.field_name, field.field_name)}："
                f"{assessment_labels[assessment.status]}；"
                f"备案所述项目类型：{generation_labels.get(assessment.generation_mode, '未明确')}；"
                f"备案所述安装位置：{site_labels.get(assessment.installation_site, '未明确')}；"
                f"禁投类型：{prohibition_labels.get(assessment.prohibition_type, '未发现')}；"
                f"理由：{_table_cell(assessment.reason)}；"
                f"原文片段：{_table_cell(assessment.evidence_quote)}；"
                f"位置：{_table_cell(location)}"
            )

    if case.equipment_inventory:
        lines.extend(
            [
                "",
                "## 设备清单结构化结果",
                "",
                "| 来源 | 工作表/行 | 类别 | 名称 | 品牌 | 型号/规格 | 数量 | 单价 | 合计 |",
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
        for item in case.equipment_inventory:
            source = material_by_id[item.source_material_id]
            specification = item.specification or ""
            if item.normalized_component_model:
                specification += f"（识别型号：{item.normalized_component_model}"
                if item.rated_power_w is not None:
                    specification += f"，{item.rated_power_w:g} W"
                specification += "）"
            lines.append(
                f"| {_table_cell(source.file_name)} "
                f"| {_table_cell(f'{item.worksheet_name}/{item.row_number}')} "
                f"| {_table_cell(item.item_category)} "
                f"| {_table_cell(item.item_name)} "
                f"| {_table_cell(item.manufacturer)} "
                f"| {_table_cell(specification)} "
                f"| {_table_cell(item.quantity)} "
                f"| {_table_cell(item.unit_price)} "
                f"| {_table_cell(item.total_price)} |"
            )
        lines.append("")

    lines.extend(
        [
            "",
            "## 5. 图片风险识别结果",
            "",
            "| 材料 | 风险点 | 状态 | 环境与现场关系 | 严重程度 | 识别参考分数（未校准） | 位置 | 判断依据 | 相关规则 |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )

    if report_findings:
        for finding in report_findings:
            material = material_by_id[finding.material_id]
            environment_relation = (
                ENVIRONMENT_RELATION_LABELS.get(
                    finding.environment_relation.value,
                    finding.environment_relation.value,
                )
                if finding.category.value.endswith("_environment")
                else "不适用"
            )
            location = (
                "未定位"
                if finding.bbox is None
                else (
                    f"({finding.bbox.x_min:.2f}, {finding.bbox.y_min:.2f})-"
                    f"({finding.bbox.x_max:.2f}, {finding.bbox.y_max:.2f})"
                )
            )

            rule_id = _finding_rule_id(case, finding.finding_id)
            rule_text = (
                f"{explain_rule(rule_id).title}（{rule_id}）" if rule_id
                else "未记录可直接对应的规则，供人工核对"
            )
            lines.append(
                f"| {_table_cell(material.file_name)} "
                f"| {_table_cell(finding.label)} "
                f"| {DETECTION_STATUS_LABELS[finding.detection_status]} "
                f"| {environment_relation} "
                f"| {RISK_SEVERITY_LABELS[finding.severity]} "
                f"| {finding.confidence:.2f} "
                f"| {location} "
                f"| {_table_cell(finding.evidence_text)} "
                f"| {_table_cell(rule_text)} |"
            )
    else:
        lines.append("| 无 | 无 | 无 | 无 | 无 | 无 | 无 | 无 | 无 |")

    lines.extend(["", "## 6. 自然灾害抗灾筛查", ""])
    if case.pv_load_screening_assessment is not None:
        lines.append("- 风雪量化采用本次统一模块结果，详见下文风雪风险量化报告。")
    elif case.catastrophe_assessment is None:
        lines.extend(["- 状态：未评估", "- 原因：没有生成组件与气象数据的联合评估", ""])
    elif case.catastrophe_assessment.module == "component-workbench-v1":
        assessment = case.catastrophe_assessment
        result = assessment.module_result
        profile = result.get("profile") or {}
        lines.extend(["### 组件工作台识别结果", "", assessment.explanation, "",
                      "| 参数 | 数值 | 口径 | 状态 |", "| --- | --- | --- | --- |"])
        for item in [*[v for k, v in profile.get("ui_fields", {}).items() if not k.startswith("hail_")], *profile.get("mechanical_fields", {}).values()]:
            value = str(item.get("value")) + " " + item.get("unit", "") if item.get("value") is not None else "未取得"
            lines.append(f"| {_table_cell(item.get('label'))} | {value} | {_table_cell(item.get('mapping_note') or item.get('load_basis'))} | {_table_cell(item.get('status'))} |")
        lines.extend(["", "- 查询状态：" + str(result.get("status", "not_found")), ""])
        for recognition in result.get("recognition", []):
            lines.append("- 型号核验：" + _table_cell(recognition.get("filename")) + "；" + _table_cell(recognition.get("recognition_status")))
    else:
        assessment = case.catastrophe_assessment
        ratio_thresholds = (
            (
                f"- 能力比分级门槛：严重不足 < {assessment.critical_shortfall_ratio:.2f}；"
                f"能力不足 < 1.00；余量有限 < {assessment.adequate_margin_ratio:.2f}；"
                "此处为旧版演示算法阈值，未经结构设计或业务审批，不等同工程判断"
            )
            if assessment.critical_shortfall_ratio is not None
            and assessment.adequate_margin_ratio is not None
            else "- 能力比：未计算；现有厂家试验参数与历史天气指标尚无经验证的同口径比较方法"
        )
        lines.extend(
            [
                f"- 旧版技术筛查的能力标签：{RESISTANCE_LABELS[assessment.resistance_level.value]}",
                f"- 旧版技术筛查的风险标签：{LOSS_RISK_LABELS[assessment.expected_loss_risk.value]}",
                "- 口径说明：上述标签只反映现有技术筛查规则，不代表出险概率、预计赔款或最终承保等级；项目设计与厂家试验口径须人工核实。",
                f"- 评估说明：{_table_cell(assessment.explanation)}",
                "- 逐项因素：" + ("；".join(assessment.factors) or "无"),
                ratio_thresholds,
                "- 规则编号：" + ("、".join(assessment.triggered_rule_ids) or "无"),
                "",
            ]
        )
        if assessment.installation_parameter_reviews:
            hazard_labels = {"wind": "大风", "hail": "冰雹", "snow": "积雪"}
            status_labels = {
                "usable": "可用",
                "not_applicable": "不适用",
                "pending_confirmation": "待确认",
            }
            parameter_labels = {
                "wind_load_pa": "厂家风荷载",
                "front_static_load_pa": "正面最大静载",
                "back_static_load_pa": "背面最大静载",
                "hail_resistance_mm": "冰雹试验直径",
                "hail_impact_velocity_m_s": "冰雹试验速度",
                "snow_load_pa": "厂家雪荷载",
            }
            lines.extend(["### 逐灾种安装参数适用性", ""])
            for review in assessment.installation_parameter_reviews:
                parameters = "、".join(
                    parameter_labels.get(field, field)
                    for field in review.parameter_fields
                )
                requirements = "；".join(review.required_evidence)
                lines.append(
                    f"- {hazard_labels[review.hazard]}："
                    f"{status_labels[review.status.value]}；参数：{_table_cell(parameters)}；"
                    f"原因：{_table_cell(review.explanation)}；"
                    f"需核对：{_table_cell(requirements)}；"
                    f"规则：{review.triggered_rule_id}"
                )
            lines.append("")

    if case.catastrophe_assessment is not None and case.catastrophe_assessment.module != "component-workbench-v1":
        rows = case.catastrophe_assessment.comparisons
        if rows:
            lines.extend(["### 逐项计算明细", "", "| 灾害 | 组件能力 | 历史指标 | 比较需求 | 能力比 | 判断 |", "| --- | --- | --- | --- | --- | --- |"])
            for row in rows:
                label = {"wind":"风灾", "hail":"雹灾", "snow":"雪灾"}[row.hazard]
                state = LOSS_RISK_LABELS[row.risk.value] if row.status == 'calculated' else '数据不足'
                event_unit = 'm/s' if row.hazard == 'wind' else row.unit
                lines.append(f"| {label} | {_display(row.capacity)} {row.unit} | {_display(row.event)} {event_unit} | {_display(row.demand)} {row.unit} | {_display(row.ratio)} | {state} |")
            lines.append("")
            for row in rows:
                label = {"wind":"风灾", "hail":"雹灾", "snow":"雪灾"}[row.hazard]
                lines.append(f"- {label}计算：{_table_cell(row.formula)}；{_table_cell(row.reason) if row.reason else '按保存的输入进行比较'}；规则：{'、'.join(row.rule_ids) or '该项未完成计算'}")
        else:
            lines.append("- 历史记录未保存逐项计算明细；保留原始结论，不用当前规则重新计算。")
        lines.extend(["", "- 筛查结果不代表出险概率、预计赔款或整套电站承载合格。", ""])

    if case.pv_load_screening_assessment is not None:
        from app.catastrophe.front_load_screening.unified import report as wind_snow_report
        lines.extend(["", wind_snow_report(case.pv_load_screening_assessment), ""])

    if case.component_profile is not None:
        component = case.component_profile
        lines.extend(
            [
                "### 组件参数来源",
                "",
                (
                    f"- 型号：{_table_cell(component.component_model)}；厂商：{_table_cell(component.manufacturer)}；"
                    f"目录型号匹配参考分：{component.match_confidence:.2f}；"
                    f"市场版本：{_table_cell(component.market_version)}"
                ),
                (
                    f"- 额定功率：{_display_measurement(component.rated_power_w)} W；"
                    f"正面最大静态载荷：{_display_measurement(component.front_static_load_pa)} Pa；"
                    f"背面最大静态载荷：{_display_measurement(component.back_static_load_pa)} Pa"
                ),
                (
                    f"- 单独列示的风荷载：{_independent_component_load(component.wind_load_pa)}；"
                    f"单独列示的雪荷载：{_independent_component_load(component.snow_load_pa)}"
                ),
                (
                    f"- 型号来源方式：{_table_cell(component.model_derivation_method)}；"
                    f"文件类型：{_table_cell(component.source_document_type)}"
                ),
                f"- 厂家参数说明：{_table_cell(component.source_note)}",
                (
                    f"- 来源：{_table_cell(component.source_name)}；"
                    f"链接：{_table_cell(component.source_url)}；获取时间：{component.retrieved_at.isoformat()}"
                ),
                "- 口径边界：正反面最大静载作为厂家参数记录，不折算为项目设计风压或屋面结构雪荷载；参数有值不等于该灾种自动通过。",
                "",
            ]
        )
        if component.parameter_sources:
            labels = {
                "rated_power_w": "额定功率",
                "hail_resistance_mm": "抗冰雹",
                "hail_impact_velocity_m_s": "冰雹试验冲击速度",
                "front_static_load_pa": "正面最大静态载荷",
                "back_static_load_pa": "背面最大静态载荷",
                "wind_load_pa": "风荷载",
                "snow_load_pa": "雪荷载",
            }
            lines.append("- 参数逐项来源：")
            lines.extend(
                f"  - {labels.get(field, field)}：{_table_cell(source)}"
                for field, source in component.parameter_sources.items() if not field.startswith("hail_")
            )
            lines.append("")
        if component.workbench_run_id:
            lines.append(f"- 组件工作台查询记录：{_table_cell(component.workbench_run_id)}；型号状态：{_table_cell(component.model_match_status)}")
        for field, evidence in component.parameter_evidence.items():
            if field.startswith("hail_"):
                continue
            for record in (evidence.get("records", []) if isinstance(evidence, dict) else evidence):
                lines.append(
                    f"- 字段证据 {field}：状态 {_table_cell(record.get('status'))}；"
                    f"原文 {_table_cell(record.get('quote'))}；页码 {_table_cell(record.get('page'))}；"
                    f"口径 {_table_cell(record.get('load_basis'))}；链接 {_table_cell(record.get('source_url'))}"
                )
        lines.extend(f"- 查询说明：{_table_cell(note)}" for note in component.lookup_notes)

    if case.weather_profile is not None:
        weather = case.weather_profile
        lines.extend(
            [
                "### 气象数据范围",
                "",
                f"- 项目查询坐标（WGS84）：{weather.longitude:.5f}, {weather.latitude:.5f}",
                (
                    f"- 请求期间：{_display(weather.observation_start)} 至 {_display(weather.observation_end)}；"
                    f"上游返回日期：{_display(weather.returned_data_start)} 至 {_display(weather.returned_data_end)}；"
                    f"来源：{_table_cell(weather.source_name)}；链接：{_table_cell(weather.source_url)}"
                ),
                (
                    f"- 阵风有效日数：{_display(weather.wind_valid_day_count)}/"
                    f"{_display(weather.expected_day_count)}，历史最大值："
                    f"{_display_measurement(weather.historical_max_wind_m_s)} {weather.wind_unit or 'm/s'}；"
                    f"日持续风速有效日数：{_display(weather.wind_speed_valid_day_count)}/"
                    f"{_display(weather.expected_day_count)}，历史最大值："
                    f"{_display_measurement(weather.historical_max_daily_wind_speed_m_s)} "
                    f"{weather.wind_speed_unit or 'm/s'}；"
                    f"单日总降水有效日数：{_display(weather.precipitation_valid_day_count)}/"
                    f"{_display(weather.expected_day_count)}，最大值："
                    f"{_display_measurement(weather.historical_max_daily_precipitation_mm)} "
                    f"{weather.precipitation_unit or 'mm'}；"
                    f"单日降雨有效日数：{_display(weather.rain_valid_day_count)}/"
                    f"{_display(weather.expected_day_count)}，最大值："
                    f"{_display_measurement(weather.historical_max_daily_rain_mm)} "
                    f"{weather.rain_unit or 'mm'}；"
                    f"降水时数有效日数：{_display(weather.precipitation_hours_valid_day_count)}/"
                    f"{_display(weather.expected_day_count)}，最长单日有降水时数："
                    f"{_display_measurement(weather.historical_max_daily_precipitation_hours)} "
                    f"{weather.precipitation_hours_unit or 'h'}；"
                    f"降雪有效日数：{_display(weather.snowfall_valid_day_count)}/"
                    f"{_display(weather.expected_day_count)}，历史最大单日降雪："
                    f"{_display_measurement(weather.historical_max_daily_snowfall_cm)} {weather.snowfall_unit or 'cm'}"
                ),
                (
                    f"- 数据集选择：{_table_cell(weather.dataset_selection)}；"
                    f"网格选择：{_table_cell(weather.grid_selection_method)}；"
                    f"时区：{_table_cell(weather.timezone)}；"
                    f"实际气象网格：{_display(weather.grid_longitude)}, {_display(weather.grid_latitude)}；"
                    f"网格距项目点：{_display(weather.grid_distance_km)} km；"
                    f"网格 DEM 高程：{_display(weather.grid_elevation_m)} m"
                ),
                (
                    f"- 项目规范雪荷载：{_display(weather.historical_max_snow_load_pa)} Pa"
                ),
                "- 资料说明：历史再分析和网格点用于气候背景筛查，不等同于项目现场气象站实测或结构设计参数。",
                "",
            ]
        )
        lines.extend(
            f"- 数据质量提示：{_table_cell(note)}"
            for note in weather.data_quality_notes
        )
        if weather.data_quality_notes:
            lines.append("")

    if case.map_reviews:
        lines.extend(
            [
                "",
                "### 地图影像辅助复核（外部参考）",
                "",
                (
                    "> 地图影像是第三方存档影像，不代表实时现场状态。拍摄日期可能未知；"
                    "位置和可见范围必须由人工核实。此信息不会自动改变核保结论。"
                ),
                "",
            ]
        )
        visibility_labels = {
            "visible": "可见",
            "not_visible": "未见",
            "unclear": "不确定",
        }
        address_source_labels = {
            "project_info": "项目基本信息",
            "material_ocr": "材料文字识别/人工核对",
            "human_corrected": "人工更正",
        }
        for review in case.map_reviews:
            address_match_details = (
                "高德接口未返回可信度评分；已由核保员人工确认候选位置"
                if review.geocoding_confidence is None
                or review.address_comprehension is None
                else (
                    f"匹配度 {review.address_comprehension}/100；"
                    f"定位置信分 {review.geocoding_confidence}/100；"
                    f"精确匹配 {'是' if review.precise_match else '否'}"
                )
            )
            if review.match_level:
                address_match_details += f"；解析级别 {_table_cell(review.match_level)}"
            lines.extend(
                [
                    f"#### 复核记录 {_table_cell(review.review_id)}",
                    "",
                    f"- 地图服务：{_table_cell(review.provider)}；地址来源：{address_source_labels[review.address_source]}",
                    f"- 触发复核的模糊/低质量材料：{_table_cell('、'.join(review.triggering_material_ids))}",
                    f"- 查询地址：{_table_cell(review.query_address)}；匹配地址：{_table_cell(review.matched_address)}",
                    f"- 地址匹配依据：{address_match_details}",
                    (
                        f"- 地图坐标（GCJ-02）：{review.longitude_gcj02}, {review.latitude_gcj02}"
                        if review.provider == "amap_maps"
                        else f"- 地图坐标（BD-09）：{review.longitude_bd09}, {review.latitude_bd09}"
                    ),
                    (
                        f"- 天气查询坐标（WGS84 近似）：{review.longitude_wgs84}, {review.latitude_wgs84}"
                        if review.longitude_wgs84 is not None
                        else "- 天气查询坐标：未记录"
                    ),
                    f"- 地图查看入口：{review.provider_map_url}",
                    f"- 复核人：{_table_cell(review.reviewer_name)}；项目位置确认：{'是' if review.site_match_confirmed else '否'}",
                    (
                        f"- 地图可见光伏组件：{visibility_labels[review.photovoltaic_visibility]}；"
                        f"观察到的安装载体：{INSTALLATION_TYPE_LABELS[review.observed_installation_type]}"
                    ),
                    f"- 影像拍摄日期：{_display(review.imagery_capture_date)}；查询时间：{review.queried_at.isoformat()}；记录时间：{review.reviewed_at.isoformat()}",
                    f"- 人工观察：{_table_cell(review.observations)}",
                    "- 对自动核保结论的作用：仅供人工参考；未作为自动规则事实。",
                    "",
                ]
            )
            if review.sentinel_context:
                context = review.sentinel_context
                cloud = (
                    "unknown"
                    if context.tile_cloud_cover_percent is None
                    else f"{context.tile_cloud_cover_percent:.1f}% (scene/tile estimate)"
                )
                lines.extend(
                    [
                        "##### Sentinel-2 surroundings image",
                        "",
                        f"- Source: Copernicus Sentinel-2 L2A; acquisition: {context.acquired_at.isoformat()}",
                        f"- Scene: {_table_cell(context.scene_id)}; cloud cover: {cloud}; masked pixels: {context.masked_pixel_fraction:.1%}",
                        f"- Scale: about {context.pixel_size_m:.1f} m per pixel; radius: {context.radius_m} m; WGS84 bbox: {context.bbox_wgs84}",
                        f"- Cloud note: {_table_cell(context.tile_cloud_cover_note)}",
                        f"- Attribution: {_table_cell(context.attribution)}",
                    ]
                )
                if context.quality_warning:
                    lines.append(f"- Image quality warning: {_table_cell(context.quality_warning)}")
                if context.analysis_warning:
                    lines.append(f"- VLM warning: {_table_cell(context.analysis_warning)}")
                if context.analysis:
                    lines.append(f"- Optional VLM summary (not an underwriting result): {_table_cell(context.analysis.summary)}")
                    lines.extend(
                        f"  - {item.category} / {item.presence} / confidence {item.confidence:.2f}: {_table_cell(item.evidence)}"
                        for item in context.analysis.observations
                    )
                lines.append("")
    else:
        lines.extend([
"", "### 地图影像辅助复核", "", "- 未执行。", ""])

    lines.extend(
        [
            "",
            "## 7. 处理留痕",
            "",
            "| 步骤 | 执行方 | 模型 | 状态 | 耗时（毫秒） |",
            "| --- | --- | --- | --- | --- |",
        ]
    )

    if case.processing_trace:
        for trace in case.processing_trace:
            lines.append(
                f"| {PROCESSING_STEP_LABELS[trace.step]} "
                f"| {_table_cell(trace.provider)} "
                f"| {_table_cell(trace.model)} "
                f"| {PROCESSING_STATUS_LABELS[trace.status]} "
                f"| {trace.latency_ms} |"
            )
    else:
        lines.append("| 无 | 无 | 无 | 无 | 无 |")

    lines.extend(
        [
            "",
            "## 8. 人工复核记录",
            "",
            "| 复核人 | 最终意见 | 复核时间（UTC） | 说明 |",
            "| --- | --- | --- | --- |",
        ]
    )
    review_labels = {
        "accept": "建议承保",
        "recommend_reject": "建议拒保",
        "surcharge": "建议承保（加费）",
        "conditional_accept": "建议附条件承保",
        "request_more": "补充材料",
    }
    if human_reviews:
        for review in human_reviews:
            review_label = review_labels.get(review.get("final_decision", ""), "未指定")
            if (review.get("pricing_recommendation") == "surcharge"
                    and review.get("final_decision") in {"accept", "conditional_accept"}):
                review_label += "（加费）"
            lines.append(
                "| "
                f"{_table_cell(review.get('reviewer_name'))} | "
                f"{review_label} | "
                f"{_table_cell(review.get('reviewed_at'))} | "
                f"{_table_cell(review.get('comment'))} |"
            )
    else:
        lines.append("| 尚无人复核 | — | — | — |")

    if case.ruleset_snapshot:
        snapshot = case.ruleset_snapshot
        statuses = {"not_selected": "未选择", "not_applicable": "场景不适用", "applicability_pending": "适用条件待确认",
                    "dependency_pending": "前置规则待确认", "not_executed": "未执行", "material_missing": "材料不足",
                    "evidence_insufficient": "证据待核", "risk_found": "发现关注事项", "completed": "检查已完成"}
        scope_lines = ["## 本次核保范围与规则版本", "",
            f"方案：{_table_cell(snapshot['scheme_name'])}；方案版本：{snapshot['selection'].get('scheme_version') or '自定义'}；规则目录：{snapshot['catalog_version']}",
            "", "本次结论仅覆盖已执行的所选规则。未选择、场景不适用和材料不足均不代表风险已排除。", "",
            "| 核保规则 | 状态 | 依据与限制 |", "| --- | --- | --- |"]
        for item in case.rule_execution:
            scope_lines.append(f"| {_table_cell(item['name'])} | {statuses.get(item['status'], item['status'])} | {_table_cell(item['reason'])} |")
        scope_lines += ["", f"快照编号：{snapshot['snapshot_id']}",
            f"判断参数：参考分门槛 {snapshot['parameters']['environment_min_confidence']}；照片时效 {snapshot['parameters']['watermark_days']} 天；材料要求 {snapshot['parameters']['material_profile']}", ""]
        lines = lines[:2] + scope_lines + lines[2:]
    manual = case.component_review
    if manual:
        from app.contracts.component_review import PARAMETER_NAMES
        lines += ["", "## 本案组件人工核验与参数补充", "",
                  "使用范围：仅本案。本次确认未写入共享参数库。",
                  "执行状态：" + ("已在本轮组件查询中采用；后续计算以本轮实际执行记录为准。" if manual.get('applied') else "已确认但本轮未执行组件步骤，未据此完成风险评估。"),
                  f"完整型号：{_table_cell(manual['component_model'])}；核验人：{_table_cell(manual['reviewer_name'])}；时间：{manual['confirmed_at']}",
                  f"型号依据：{_table_cell(manual['model_reason'])}", f"补充／冲突处理理由：{_table_cell(manual['reason'])}", "",
                  "| 参数 | 原检索值 | 本案确认值 | 来源口径与适用条件 |", "| --- | --- | --- | --- |"]
        original = manual.get('automatic_profile') or manual.get('original_profile') or {}
        source_lines = []
        for field, p in manual['parameters'].items():
            label, unit, direction = PARAMETER_NAMES[field]
            basis = {'test': '测试载荷', 'design': '设计载荷', 'unspecified': '口径待确认'}[p['load_basis']]
            lines.append(f"| {label} | {_table_cell(original.get(field))} | {p['value']} {p['unit']} → {p['normalized_value']} {unit} | {basis}；{_table_cell(p['applicability'])}；现场适用性{'已确认' if p['installation_confirmed'] else '待核验'} |")
            source_lines += ["", f"### {label}来源证据", "",
                      f"- 来源：{_table_cell(p['document_title'])}；版本：{_table_cell(p['document_version'])}；位置：{_table_cell(p['page'])}",
                      f"- 原文：{_table_cell(p['quote'])}"]
            if p.get('source_url'):
                source_lines.append(f"- 原始来源：{_table_cell(p['source_url'])}")
            if p.get('source_material_id'):
                source_lines.append(f"- 本轮附件：{_table_cell(p.get('source_file_name'))}；文件摘要：{p['source_sha256']}")
        lines += source_lines
        lines += ["", *[f"读取异常：{_table_cell(f['reason'])}" for f in manual.get('ocr_failures', [])]]
        lines += ["", "原始 AI 识别及自动检索证据保留在本轮结构化记录中。正面与背面机械载荷分别保存，不能直接作为独立风／雪设计能力。"]
    reused = case.component_module_result.get("verified_reuse") or (
        case.component_profile.workbench_result.get("verified_reuse") if case.component_profile else None)
    if reused:
        lines += ["", "## 已核验参数复用记录", "", reused["notice"], "",
                  f"记录：{_table_cell(reused['id'])}；版本：{reused['version']}；完整型号：{_table_cell(reused['component_model'])}",
                  f"核验人：{_table_cell(reused['approved_by'])}；核验时间：{reused['approved_at']}",
                  f"当时有效期：{reused['valid_from']} 至 {reused['expires_at']}；本轮采用时间：{reused['used_at']}",
                  f"来源适用条件：{_table_cell(reused['applicability'])}",
                  f"核验依据与复用原因：{_table_cell(reused['reason'])}", "",
                  "| 参数 | 数值 | 口径 | 原文 | 来源 |", "| --- | --- | --- | --- | --- |"]
        from app.storage.verified_components import FIELDS
        for field, item in reused["parameters"].items():
            label, unit = FIELDS[field]
            basis = {"test": "测试载荷", "design": "设计载荷", "unspecified": "口径未明确"}[item["load_basis"]]
            lines.append(f"| {label} | {item['value']} {unit} | {basis} | {_table_cell(item['quote'])} | {_table_cell(item['source_url'])} |")
        lines += ["", "本节是本轮采用时的历史快照。记录到期或撤销后，新查询与重试不再复用；历史报告不自动改写。"]
    strategy = case.strategy_assessment
    if strategy:
        from app.strategies.engine import ACTIONS
        a=strategy;scheme=a['scheme_snapshot']
        labels={**ACTIONS,'accept':'建议通过','conditional_accept':'有条件通过','unknown':'待核','none':'无命中'}
        lines += ["", "## 承保策略处理建议" + ("（演示方案）" if scheme['demo'] else ""), "",
                  f"方案：{_table_cell(scheme['name'])}；业务：{_table_cell(scheme['company'])}；版本：V{scheme['version']}（{scheme['id']}）",
                  f"业务依据：{_table_cell(scheme['basis'])}；发布人：{_table_cell(scheme['approved_by'])}",
                  f"有效期：{scheme['valid_from']} 至 {scheme['expires_at']}；本次采用时间：{scheme.get('selected_at','试算')}",
                  f"季节依据：拟起保日期 {a['facts'].get('start_date') or '未提供'}，不使用操作当天代替。",
                  f"原 AI 建议：{labels.get(a['original_ai_decision'],a['original_ai_decision'])}；策略处理建议：{labels.get(a['effective_suggestion'],a['effective_suggestion'])}",
                  "风险事实与策略关注等级分开保存，所有处理建议须人工审核。", a['pricing']['notice'], *a['reasons'], "",
                  "| 策略 | 匹配状态 | 优先级 | 动作 | 结果来源 | 理由 |", "| --- | --- | --- | --- | --- | --- |"]
        for rule in a['rule_results']:
            state={'matched':'命中','not_matched':'不匹配','insufficient':'依据不足'}[rule['status']]
            lines.append(f"| {_table_cell(rule['name'])} ({rule['rule_id']}) | {state} | {rule['priority']} | {ACTIONS[rule['action']]} | {_table_cell(rule['evidence_ref'])}: {_table_cell(rule['observed_value'])} | {_table_cell(rule['reason'])} |")
        lines += ["", "本节保留采用时的策略快照；方案更新、撤销或到期不改写历史报告。"]
    coverage = case.evidence_coverage
    if coverage:
        lines += ["", "## 材料覆盖与补拍清单", "", coverage['notice'], "",
                  "| 检查项 / 区域 | 覆盖状态 | 部位及依据 | 下一步 |", "| --- | --- | --- | --- |"]
        labels = {'satisfied':'已取得覆盖证据','insufficient':'需补材','review':'内部核验','not_selected':'未选择，未检查','not_applicable':'不适用','not_executed':'未执行'}
        for row in coverage['rows']:
            evidence = '；'.join(t['label']+'：'+t['reason']+'（材料：'+','.join(t['material_ids'])+'）' for t in row['targets']) or row['reason']
            actions = '；'.join(row['customer_requests']+row['review_questions']) or '保留原风险判断'
            lines.append('| '+ ' | '.join(_table_cell(x) for x in [row['name']+' / '+row['area_id'], labels[row['status']], evidence, actions])+' |')
        for d in coverage['duplicates']:
            lines.append(f"- 完全重复文件：{_table_cell(d['material_id'])} → {_table_cell(d['duplicate_of'])}，不增加部位覆盖。")

    if case.verification:
        import json
        from urllib.parse import quote
        v = case.verification
        lines.extend(['', '## v2 证据核验与人工确认', '', f'证据与复核版本：{v["version"]}',
            f'[打开本地案件复核页面](/verification?case={quote(case.case_id)})', '',
            '此导出为版本快照；实际操作请使用工作台复核页面。', v['limitations']])
        for issue in v['issues']:
            lines.extend(['', '### ' + issue['question'], '状态：' + issue['status'],
                '原存疑原因：' + issue['reason'], '证据：' + '、'.join(issue['material_ids']),
                '补材要求：' + issue.get('supplement_request',''),
                '```json', json.dumps({'options':issue['options'],'human_review':issue.get('human_review'),
                'verification':issue['decisions']},ensure_ascii=False,indent=2), '```'])
        lines.extend(['', '### 最终整单决定', '```json',json.dumps(v.get('final'),ensure_ascii=False,indent=2),'```',
            '', '### 首轮与调用审计', '```json',json.dumps({'first_round':v['first_round'],'calls':v['calls'],
            'human_events':v['human_events']},ensure_ascii=False,indent=2),'```'])
    return "\n".join(lines).rstrip() + "\n"
