"""Decision-first, evidence-linked presentation data for underwriting reports."""

from __future__ import annotations

import re
from typing import Any

from app.contracts import UnderwritingCase
from app.location.reporting import coarse_candidate_comparison, distinct_location_evidence

DECISION_LABELS = {
    "accept": "建议承保",
    "recommend_reject": "建议拒保",
    "surcharge": "建议承保（加费）",
    "conditional_accept": "建议附条件承保",
    "request_more": "需补充材料",
    "manual_review": "需人工复核",
}
OWNER_LABELS = {
    "customer": "客户补材",
    "underwriter": "核保员核实",
    "system": "系统处理",
    "business": "业务规则确认",
}
HAZARD_LABELS = {"wind": "风灾", "hail": "雹灾", "snow": "雪灾"}
PROCESSING_STEP_LABELS = {
    "aisle_group": "组图检修通道检查",
    "geometry_group": "组件倾角、离地高度和地面粗糙度估计",
    "ocr": "材料文字识别",
    "vision": "现场照片检查",
    "equipment_inventory": "设备清单检查",
    "component_lookup": "组件资料查询",
    "weather_lookup": "当地历史天气查询",
    "catastrophe_assessment": "风雪检查",
    "front_load_screening": "风雪风险量化",
    "map_review": "项目位置核对",
    "rule_engine": "核保规则检查",
    "decision": "核保建议生成",
}
STAGE_LABELS = {
    "aisle_group": "组图检修通道检查",
    "single_image": "逐张照片检查",
    "filing": "备案材料检查",
    "installation_type": "安装类型检查",
    "installation_scope": "安装范围检查",
    "completeness": "材料是否齐全",
    "location": "项目位置检查",
}
FIELD_LABELS = {
    "insured_name": "被保险人名称", "serial_number": "设备序列号",
    "document_title": "文件标题", "document_type": "文件类型",
    "insured_address": "被保险地址",
    "project_name": "项目名称", "construction_content": "建设内容",
    "project_entity": "项目单位", "site_address": "项目地址",
    "component_model": "组件型号", "inverter_model": "逆变器型号",
    "rated_power_w": "额定功率", "grid_connection_date": "并网日期",
    "grounding_resistance_ohm": "接地电阻", "inspection_date": "检测日期",
    "inspection_result": "检测结论", "monitoring_present": "监控配置",
    "watermark_present": "照片拍摄水印", "watermark_date": "照片拍摄日期",
    "watermark_time": "照片拍摄时间", "watermark_longitude": "照片水印经度",
    "watermark_latitude": "照片水印纬度", "watermark_address": "照片水印地址",
    "watermark_coordinate_system": "照片水印坐标系", "shot_date": "照片拍摄日期",
}
CHECK_RISK_CATEGORIES = {
    "filing": set(), "grid": set(), "component": set(),
    "panorama": {"agriculture_environment", "forest_environment", "livestock_environment",
                 "fishery_environment", "water_adjacent_environment", "tidal_flat_environment",
                 "mountain_environment", "desertification_environment", "severe_shading",
                 "minor_shading", "aisle_obstruction"},
    "support": {"roof_connection_abnormal", "aisle_obstruction"},
    "edge": {"missing_parapet_or_guardrail", "drainage_abnormal", "aisle_obstruction"},
    "workshop": {"flammable_material", "hazardous_material", "dangerous_process",
                 "fire_protection_absent"},
    "grounding": {"electrical_grounding_abnormal", "unprotected_cable"},
    "combiner": {"combiner_box_seal_abnormal", "combiner_box_fuse_abnormal",
                 "surge_protector_abnormal"},
    "inverter": {"inverter_abnormal"},
}
DECISION_ACTIONS = {
    "recommend_reject": ("待确认", "可进入拒保依据复核"),
    "request_more": ("等待阻断事项处理", "需先处理阻断项"),
    "manual_review": ("待复核", "暂停自动判断"),
    "surcharge": ("等待加费方案确认", "可进入加费依据复核"),
    "conditional_accept": ("等待承保条件确认", "可进入条件复核"),
    "accept": ("待确认", "可进入承保复核"),
}


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _business_text(value: str) -> str:
    """Keep internal OCR names out of decision and action copy."""
    return re.sub(r"\b[a-z][a-z0-9_]*\b",
                  lambda match: FIELD_LABELS.get(match.group(),
                                                "其他待核字段" if "_" in match.group() else match.group()), value)


def _is_customer_request(text: str) -> bool:
    """Only recognizable material requests become customer-facing instructions."""
    return text.startswith(("补充", "补拍", "重拍")) and not any(
        word in text for word in (
            "系统", "查询失败", "规则待", "接口失败", "加费方案", "费率", "承保条件",
        )
    )


def _missing_start_date_notice(text: str) -> bool:
    return "拟起保日期" in text and any(word in text for word in ("未提供", "未填写", "缺少", "为空"))


def build_report_view(case: UnderwritingCase) -> dict[str, Any]:
    """Derive report display fields without changing the underwriting decision."""

    data = case.model_dump(mode="json")
    decision = data.get("decision") or {}
    decision_code = decision.get("decision")
    pricing_recommendation = decision.get("pricing_recommendation", "not_proposed")
    if decision_code == "surcharge":  # Older saved cases used a standalone decision.
        pricing_recommendation = "surcharge"
    price_applies = pricing_recommendation == "surcharge" and decision_code in {
        "accept", "conditional_accept", "surcharge",
    }
    materials = {item["material_id"]: item for item in data.get("materials", [])}
    findings = {item["finding_id"]: item for item in data.get("findings", [])}
    multiview_shade = []
    if (decision_code == "manual_review" and
            "IMG-SHADING-MULTIVIEW-REVIEW-001" in decision.get("decisive_rule_ids", [])):
        candidates = [item for item in findings.values()
                      if item.get("category") == "minor_shading"
                      and item.get("detection_status") == "detected"
                      and materials.get(item.get("material_id"), {}).get("category") == "panorama"]
        views = {materials[item["material_id"]].get("capture_view") for item in candidates}
        if {"front_level", "overhead"} <= views:
            multiview_shade = [item for item in candidates
                               if materials[item["material_id"]].get("capture_view")
                               in {"front_level", "overhead"}]
    shade_material_ids = _unique([item["material_id"] for item in multiview_shade])
    checks = data.get("applicable_checks", [])
    results = {
        (item["area_id"], item["key"]): item
        for item in data.get("check_results", [])
    }
    traces = data.get("processing_trace", [])
    provider_failed = any(item.get("status") in {"failed", "partial"} for item in traces)
    rule_explanations = {
        item["rule_id"]: item for item in decision.get("rule_explanations", [])
    }
    references: dict[str, dict[str, str]] = {}

    for material_id, item in materials.items():
        references[f"material:{material_id}"] = {
            "kind": "material", "label": item.get("file_name") or material_id,
            "material_id": material_id,
        }
    for finding_id, item in findings.items():
        references[f"finding:{finding_id}"] = {
            "kind": "finding", "label": item.get("label") or finding_id,
            "material_id": item.get("material_id") or "",
        }
    for item in data.get("ocr_fields", []):
        references[f"ocr:{item['field_id']}"] = {
            "kind": "ocr", "label": _business_text(item.get("field_name") or "材料文字"),
            "material_id": item.get("material_id") or "",
        }
    for rule_id, item in rule_explanations.items():
        references[f"rule:{rule_id}"] = {
            "kind": "rule", "label": item.get("title") or rule_id,
        }

    coverage: list[dict[str, Any]] = []
    for check in checks:
        area_id, key = check["area_id"], check["key"]
        check_id = f"check:{area_id}:{key}"
        result = results.get((area_id, key))
        raw_status = result.get("status") if result else None
        related_findings = [
            findings[finding_id]
            for finding_id in (result or {}).get("finding_ids", [])
            if finding_id in findings
        ]
        related_ocr = set((result or {}).get("ocr_field_ids", []))
        decisive_reviews = [
            review for review in data.get("material_reviews", [])
            if review.get("action") in {"surcharge", "recommend_reject", "conditional"}
            and bool(related_ocr.intersection(review.get("ocr_field_ids", [])))
        ]
        relevant_categories = CHECK_RISK_CATEGORIES.get(key)
        detected_risk = any(
            finding.get("detection_status") == "detected"
            and finding.get("category") != "image_quality"
            and (relevant_categories is None or finding.get("category") in relevant_categories)
            for finding in related_findings
        ) or bool(decisive_reviews)
        uncertain = any(
            finding.get("detection_status") == "uncertain"
            and (relevant_categories is None or finding.get("category") in relevant_categories)
            for finding in related_findings
        )
        if check.get("applicability") == "not_applicable" or raw_status == "not_applicable":
            state = "not_applicable"
        elif raw_status is None or raw_status in {"missing", "not_provided"}:
            state = "not_executed"
        elif detected_risk:
            state = "risk"
        elif raw_status == "valid" and not uncertain:
            state = "evidence_ready"
        else:
            state = "undetermined"
        refs = [f"material:{item}" for item in (result or {}).get("material_ids", [])]
        refs += [f"finding:{item}" for item in (result or {}).get("finding_ids", [])]
        refs += [f"ocr:{item}" for item in (result or {}).get("ocr_field_ids", [])]
        coverage.append({
            "id": check_id, "label": check["label"], "area_id": area_id,
            "state": state, "raw_status": raw_status,
            "required_now": check.get("required_now", False),
            "scope": check.get("evidence_requirement") or "",
            "reason": _business_text((result or {}).get("reason") or check.get("reason") or ""),
            "reference_ids": _unique(refs),
        })
        references[check_id] = {"kind": "check", "label": check["label"]}

    actions: list[dict[str, Any]] = []
    action_keys: set[tuple[str, str]] = set()

    def add_action(owner: str, text: str, blocked: str, after: str,
                   refs: list[str] | None = None, priority: int = 2) -> None:
        key = owner, text.strip()
        if not text.strip() or key in action_keys:
            return
        if owner == "customer" and text.strip() == "补充电站备案证" and any(
            item["owner"] == "customer" and "备案" in item["text"]
            for item in actions
        ):
            return
        action_keys.add(key)
        action_id = f"action:{len(actions) + 1}"
        actions.append({
            "id": action_id, "owner": owner, "owner_label": OWNER_LABELS[owner],
            "text": _business_text(text.strip()), "blocked_check": blocked,
            "completion": "核对原件或数据，记录处理结果" if owner != "customer"
            else "提供清晰、完整且与本项目对应的材料或信息",
            "after": after, "priority": priority,
            "reference_ids": _unique(refs or []),
        })
        references[action_id] = {"kind": "action", "label": text.strip()}

    for check in checks:
        result = results.get((check["area_id"], check["key"]))
        if not result or not check.get("required_now"):
            continue
        request = result.get("customer_request")
        if result.get("status") in {"missing", "unusable"} and request:
            add_action(
                "customer", request, check["label"],
                f"重新核验{check['label']}",
                [f"check:{check['area_id']}:{check['key']}"]
                + [f"material:{item}" for item in result.get("material_ids", [])],
                1,
            )

    for stage in data.get("stage_reviews", []):
        for request in stage.get("customer_requests", []):
            if _is_customer_request(request):
                add_action(
                    "customer", request,
                    STAGE_LABELS.get(stage.get("stage"), "对应审核事项"),
                    "重跑对应阶段审核",
                    [f"material:{item}" for item in stage.get("material_ids", [])], 1,
                )
    for request in decision.get("missing_requirements", []):
        if request == "补充电站备案证" and any(
            item["owner"] == "customer" and "备案" in item["text"]
            for item in actions
        ):
            continue
        if any(term in request for term in ("加费方案", "费率", "承保条件")):
            add_action("business", f"确认业务规则：{request}",
                       "业务规则尚未确定", "明确方案后重新核定整单建议", [], 1)
        elif _is_customer_request(request):
            add_action("customer", request, "本轮综合判断", "重跑受影响检查", [], 1)
        else:
            add_action("underwriter", f"确认缺口责任归属：{request}",
                       "本轮综合判断", "明确处理人后重查", [], 2)

    location = data.get("location_assessment") or {}
    if location.get("status") == "conflict":
        legacy_reject = any(
            stage.get("stage") == "location" and stage.get("action") == "recommend_reject"
            for stage in data.get("stage_reviews", [])
        )
        if legacy_reject:
            add_action("underwriter", "核对拒保位置证据并记录最终结论",
                       "位置冲突已触发建议拒保", "记录人工审核结论", [], 1)
        else:
            add_action("underwriter", "核对坐标系、位置原件与项目现场关系",
                       "重大位置冲突，已暂停自动核保", "确认位置后重查气象数据与整单建议", [], 1)
    elif location.get("status") in {"single_source", "uncertain", "missing"}:
        add_action("underwriter", "核对备案地址与现场照片位置是否属于同一项目",
                   "位置尚未交叉核验一致", "确认位置后重查气象数据与整单建议",
                   [f"material:{item['material_id']}" for item in location.get("evidence", [])
                    if item.get("material_id")], 1)
    for trace in traces:
        if trace.get("status") in {"failed", "partial"}:
            step = PROCESSING_STEP_LABELS.get(trace.get("step"), "未完成的自动检查")
            add_action("system", f"重新运行{step}",
                       "相关检查尚未完成", "完成后重新查看受影响的检查与建议", [], 1)
    ocr_pending = [
        reason for reason in (
            list(decision.get("reasons", []))
            + [reason for review in data.get("material_reviews", [])
               for reason in review.get("reasons", [])]
            + [result.get("reason") or "" for result in results.values()]
        ) if any(phrase in reason for phrase in (
            "以下字段未返回可靠识别结果", "自动识别失败或未覆盖检查事项",
        ))
    ]
    if ocr_pending:
        field_names = _unique([
            FIELD_LABELS[token] for reason in ocr_pending
            for token in re.findall(r"\b[a-z][a-z0-9_]*\b", reason)
            if token in FIELD_LABELS
        ])
        add_action("system", "文字识别待重试：" + ("、".join(field_names) if field_names else "未识别的材料文字"),
                   "相关材料文字尚无法可靠核验", "重查受影响检查；仍无法识别时核对原件", [], 1)
    if multiview_shade:
        add_action("underwriter", "核对正面与俯拍遮挡标框，确认实际受影响组件及范围",
                   "两张全景图均标记组件局部遮挡", "核对原图及现场情况，记录人工复核结论",
                   [f"material:{item}" for item in shade_material_ids], 0)
    if any(_missing_start_date_notice(text) for text in decision.get("warnings", [])):
        days = case.ruleset_snapshot.get("parameters", {}).get("watermark_days", 15)
        add_action("underwriter", f"确认拟起保日期并核验照片拍摄时间是否符合 {days} 日要求",
                   "当前缺少拟起保日期，时效暂无法核验", "取得日期后完成时效核验", [], 1)
    for check in checks:
        if (check.get("applicability") == "pending"
                and "尚未核实是否配置" in (check.get("reason") or "")):
            add_action("underwriter", f"确认项目是否配置{check['label']}",
                       check["label"], "明确是否适用后重查该项", [], 1)
    if decision_code == "recommend_reject":
        add_action("underwriter", "核对拒保原件、现场关系与适用规则",
                   "最终拒保确认", "记录人工审核结论", [], 1)
    elif (decision_code == "manual_review" and location.get("status") != "conflict"
          and not actions):
        add_action("underwriter", "核实关键冲突或不确定事实",
                   "自动判断已暂停", "核实后重跑或记录人工结论", [], 1)
    elif decision_code == "conditional_accept":
        add_action("underwriter", "确认承保条件的条款和满足情况",
                   "条件尚未人工确认", "记录最终承保条件", [], 1)
    elif decision_code == "accept":
        add_action("underwriter", "复核关键证据并确认承保",
                   "系统建议待确认", "记录最终核保结论", [], 1)
    if price_applies:
        add_action("business", "确认正式加费依据与适用方案",
                   "加费建议待批准", "由核保员确认最终条件", [], 1)
    if provider_failed and not any(item["owner"] == "system" for item in actions):
        add_action("system", "重试未完成的自动识别服务",
                   "处理轨迹不完整", "刷新受影响检查", [], 1)

    basis: list[dict[str, Any]] = []
    basis_texts: set[str] = set()

    def add_basis(text: str, impact: str, refs: list[str],
                  rule_ids: list[str] | None = None) -> None:
        raw = text
        text = _business_text(text)
        if text in {"部分检查项仍待内部复核", "需人工核实关键问题"}:
            return
        refs = _unique(refs + [f"rule:{item}" for item in (rule_ids or [])])
        if any(phrase in raw for phrase in (
            "以下字段未返回可靠识别结果", "自动识别失败或未覆盖检查事项",
            "可选材料部分字段未可靠识别",
        )):
            names = [FIELD_LABELS[token] for token in re.findall(r"\b[a-z][a-z0-9_]*\b", raw)
                     if token in FIELD_LABELS]
            if not names:
                names = [part.strip() for part in text.partition("：")[2].replace("，", ",").split(",")
                         if part.strip()] or ["部分材料文字"]
            existing = next((item for item in basis if item.get("kind") == "ocr_gap"), None)
            if existing is None:
                basis_id = f"basis:{len(basis) + 1}"
                existing = {"id": basis_id, "kind": "ocr_gap", "fields": [], "text": "",
                            "impact": "系统重试；仍无法识别时由核保员核对原件",
                            "reference_ids": [], "evidence_complete": False}
                basis.append(existing)
                references[basis_id] = {"kind": "basis", "label": ""}
            existing["fields"] = _unique(existing["fields"] + names)
            existing["text"] = "文字识别待核：" + "、".join(existing["fields"])
            existing["reference_ids"] = _unique(existing["reference_ids"] + refs)
            existing["evidence_complete"] = bool(existing["reference_ids"])
            references[existing["id"]]["label"] = existing["text"]
            return
        if not text or text in basis_texts:
            return
        basis_texts.add(text)
        basis_id = f"basis:{len(basis) + 1}"
        basis.append({
            "id": basis_id, "text": text,
            "impact": impact, "reference_ids": refs,
            "evidence_complete": bool(refs),
        })
        references[basis_id] = {"kind": "basis", "label": text}

    review_action = {
        "recommend_reject": "recommend_reject",
        "surcharge": "surcharge",
        "conditional_accept": "conditional",
    }.get(decision_code)
    review_actions = {review_action} if review_action else set()
    if price_applies:
        review_actions.add("surcharge")
    if review_actions:
        for stage in data.get("stage_reviews", []):
            if stage.get("action") == decision_code:
                for text in stage.get("reasons", []):
                    add_basis(text, "决定本轮系统建议",
                              [f"material:{item}" for item in stage.get("material_ids", [])]
                              + [f"ocr:{item}" for item in stage.get("ocr_field_ids", [])],
                              stage.get("rule_ids", []))
        for review in data.get("material_reviews", []):
            if review.get("action") in review_actions:
                for text in review.get("reasons", []):
                    add_basis(text, "决定本轮系统建议",
                              [f"material:{review['material_id']}"]
                              + [f"finding:{item}" for item in review.get("finding_ids", [])],
                              review.get("triggered_rule_ids", []))
    if decision_code == "request_more":
        for action in actions:
            if action["owner"] == "customer":
                add_basis(f"缺少：{action['text']}",
                          f"阻断{action['blocked_check']}",
                          [action["id"], *action["reference_ids"]])
    if decision_code == "manual_review":
        location_refs = (
            [f"material:{item.material_id}"
             for item in distinct_location_evidence(case.location_assessment)
             if item.material_id in materials]
            if case.location_assessment else []
        )
        comparison = coarse_candidate_comparison(case.location_assessment) if case.location_assessment else None
        if comparison and location.get("status") != "distance_screened":
            add_basis(comparison, "需核对项目准确位置和现场照片归属", location_refs)
        for text in location.get("notes", []) if location.get("status") == "conflict" else []:
            add_basis(text, "暂停自动判断", location_refs)
        if multiview_shade:
            add_basis("正面与俯拍照片均标记组件局部遮挡；遮挡范围及实际影响需人工核实",
                      ("两张原图分别有定位框" if all(item.get("bbox") for item in multiview_shade)
                       else "部分原图未取得可靠定位框，需按原图核对"),
                      [f"material:{item}" for item in shade_material_ids]
                      + [f"finding:{item['finding_id']}" for item in multiview_shade],
                      ["IMG-SHADING-MULTIVIEW-REVIEW-001"])
        for stage in data.get("stage_reviews", []):
            if stage.get("action") == "manual_review":
                for text in stage.get("reasons", []) + stage.get("pending_questions", []):
                    if _missing_start_date_notice(text):
                        continue
                    if (multiview_shade and stage.get("stage") == "single_image"
                            and any(item in shade_material_ids for item in stage.get("material_ids", []))
                            and "遮挡" in text):
                        continue
                    add_basis(text, "需人工核实",
                              [f"material:{item}" for item in stage.get("material_ids", [])],
                              stage.get("rule_ids", []))
        decisive_rules = set(decision.get("decisive_rule_ids", []))
        if "CAT-FRONT-LOAD-SCREENING-REVIEW" in decisive_rules:
            front_load = data.get("pv_load_screening_assessment") or {}
            add_basis("风雪风险量化：" + front_load.get("label", "结果待核"),
                      "风雪专项结果需复核", [], ["CAT-FRONT-LOAD-SCREENING-REVIEW"])
        if "SYS-PROVIDER-FAILURE" in decisive_rules:
            failed_steps = _unique([
                PROCESSING_STEP_LABELS.get(item.get("step"), item.get("step", "自动检查"))
                for item in traces if item.get("status") in {"failed", "partial"}
            ])
            add_basis("自动检查未完整执行：" + "、".join(failed_steps or ["待核查步骤"]),
                      "需重试未完成的检查", [], ["SYS-PROVIDER-FAILURE"])
    if decision_code == "accept":
        ready = sum(item["state"] == "evidence_ready" for item in coverage)
        add_basis(f"本轮 {ready} 项有可核材料；系统未在已查范围识别到对应风险",
                  "支持系统建议；仍须核对已查范围和未知项",
                  [item["id"] for item in coverage if item["state"] == "evidence_ready"][:3])
    if not basis:
        fallback_reasons = decision.get("reasons", [])
        if decision_code == "manual_review":
            fallback_reasons = [text for text in fallback_reasons
                                if not text.startswith("拟起保日期未提供")]
        for text in fallback_reasons[:3]:
            add_basis(text, "需核对原始依据", [])

    comparisons = {
        item.get("hazard"): item
        for item in (data.get("catastrophe_assessment") or {}).get("comparisons", [])
    }
    hazards = []
    halted = data.get("pre_catastrophe_outcome") not in (None, "continue")
    for hazard in ("wind", "snow"):
        item = comparisons.get(hazard) or {}
        status = "not_executed" if halted else item.get("status") or "not_executed"
        hazards.append({
            "hazard": hazard, "label": HAZARD_LABELS[hazard], "status": status,
            "capacity": item.get("capacity"), "event": item.get("event"),
            "demand": item.get("demand"), "ratio": item.get("ratio"),
            "unit": item.get("unit") or ("mm" if hazard == "hail" else "Pa"),
            "reason": item.get("reason") or (
                "前置审查未放行，本轮未执行" if halted else "本轮未保存可比较的逐项数据"
            ), "formula": item.get("formula") or "",
            "comparability": "已有初步比较值，仍须核对本项目的设计要求和安装方式"
            if status == "calculated" else "现有资料不足，不能判断",
        })
        references[f"hazard:{hazard}"] = {
            "kind": "hazard", "label": HAZARD_LABELS[hazard],
        }

    workflow_action, progression = DECISION_ACTIONS.get(
        decision_code, ("等待系统生成建议", "尚不能推进")
    )
    if decision_code == "manual_review" and data.get("pre_catastrophe_outcome") == "continue":
        workflow_action, progression = "复核风险提示和待核事项", "已完成可运行分析"
    if decision_code == "request_more" and not any(
        item["owner"] == "customer" for item in actions
    ):
        workflow_action = "等待核保员确定缺口责任归属"
    counts = {
        state: sum(item["state"] == state for item in coverage)
        for state in ("evidence_ready", "risk", "undetermined", "not_executed", "not_applicable")
    }
    owner_first = {
        "recommend_reject": "underwriter", "manual_review": "underwriter",
        "request_more": "customer", "surcharge": "business",
        "conditional_accept": "underwriter", "accept": "underwriter",
    }.get(decision_code)
    ordered_actions = sorted(
        actions,
        key=lambda item: (
            0 if item["owner"] == owner_first else 1,
            item["priority"], int(item["id"].split(":")[1]),
        ),
    )
    return {
        "case_id": data["case_id"],
        "project": {
            "name": data["project"].get("project_name") or "未命名项目",
            "insured_name": data["project"].get("insured_name"),
            "site_address": data["project"].get("site_address"),
            "proposed_start_date": data["project"].get("proposed_start_date"),
        },
        "decision": {
            "code": decision_code,
            "label": (DECISION_LABELS.get(decision_code, "尚未生成建议")
                      + ("（加费）" if price_applies and decision_code in {"accept", "conditional_accept"} else "")),
            "pricing_recommendation": pricing_recommendation,
            "workflow_action": workflow_action, "progression": progression,
            "human_confirmation_required": True,
        },
        "basis": basis,
        "actions": ordered_actions,
        "checks": coverage,
        "coverage_counts": counts,
        "hazards": hazards,
        "references": references,
    }
