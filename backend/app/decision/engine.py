from datetime import UTC, datetime
from typing import TypeVar

from app.contracts import (
    DecisionType,
    MaterialReviewAction,
    ProcessingStatus,
    UnderwritingCase,
    UnderwritingDecision,
)
from app.rules.explanations import explain_rules

Item = TypeVar("Item")


def unique_items(values: list[Item]) -> list[Item]:
    """保持原顺序并移除重复值。"""
    return list(dict.fromkeys(values))


def missing_start_date_notice(text: str) -> bool:
    return "拟起保日期" in text and any(word in text for word in ("未提供", "未填写", "缺少", "为空"))


class DecisionEngine:
    """按拒保优先的固定顺序汇总案件事实，不输出精算保费。"""

    MISSING_COORDINATES_RULE_ID = "REQ-PROJECT-COORDINATES"
    MISSING_COMPONENT_MODEL_RULE_ID = "REQ-COMPONENT-MODEL"
    PROVIDER_FAILURE_RULE_ID = "SYS-PROVIDER-FAILURE"
    LOCATION_CONFLICT_RULE_ID = "LOC-CONFLICT-001"
    LOCATION_SINGLE_SOURCE_RULE_ID = "LOC-SINGLE-SOURCE-001"
    LOCATION_UNCERTAIN_RULE_ID = "LOC-UNCERTAIN-001"

    def decide(self, case: UnderwritingCase) -> UnderwritingDecision:
        active = {r["id"] for r in case.ruleset_snapshot.get("rules", []) if r["status"] == "active"}
        enabled = lambda key: not case.ruleset_snapshot or key in active
        rule_ids: list[str] = []
        informational_rule_ids: list[str] = []
        reasons: list[str] = []
        missing_requirements: list[str] = []
        warnings: list[str] = []
        conditions: list[str] = []
        has_reject = False
        has_surcharge = False
        has_conditional = False
        has_manual_review = False
        has_request_more = False
        has_location_conflict = False
        has_stage_manual = case.pre_catastrophe_outcome == "manual_review"
        early_stage_outcome = case.pre_catastrophe_outcome in {
            "recommend_reject", "manual_review", "request_more",
        }

        from app.rules.evidence_coverage import assess
        coverage = assess(case)
        case.evidence_coverage = coverage
        if coverage:
            missing_requirements.extend(coverage['customer_requests'])
            warnings.extend(coverage['review_questions'])
            if coverage['customer_requests']:
                has_request_more = True
                rule_ids.append('EVIDENCE-COVERAGE-MISSING-001')
                reasons.append('所选规则的关键部位证据不足，详见逐部位补材要求。')
            if coverage['review_questions']:
                has_manual_review = True
                rule_ids.append('EVIDENCE-COVERAGE-REVIEW-001')
                reasons.append('部分证据覆盖或识别结果待内部核验，不能视为检查通过。')
            for row in case.rule_execution:
                related = [r for r in coverage['rows'] if r['rule_id'] == row['id']]
                if row['status'] not in {'risk_found', 'not_selected', 'not_applicable', 'material_missing'}:
                    if any(r['status'] == 'insufficient' for r in related):
                        row.update(status='evidence_insufficient', reason='关键部位证据不足，详见材料覆盖与补拍清单。')
                    elif any(r['status'] == 'review' for r in related):
                        row.update(status='evidence_insufficient', reason='逐部位覆盖待内部核验，不能视为检查通过。')

        if enabled("watermark") and case.project.proposed_start_date is None:
            informational_rule_ids.append("IMG-WATERMARK-DATE-001")
            warnings.append(f"拟起保日期未提供，{case.ruleset_snapshot.get('parameters', {}).get('watermark_days', 15)}日时效暂无法核验；日期保持空值，列为风险提示")

        for stage in case.stage_reviews:
            rule_ids.extend(item for item in stage.rule_ids if item != "IMG-WATERMARK-DATE-001")
            if "IMG-WATERMARK-DATE-001" in stage.rule_ids:
                informational_rule_ids.append("IMG-WATERMARK-DATE-001")
            missing_requirements.extend(stage.customer_requests)
            if stage.customer_requests:
                has_request_more = True
            if stage.action == "recommend_reject":
                has_reject = True
                reasons.extend(item for item in stage.reasons if not missing_start_date_notice(item))
            elif stage.action == "manual_review":
                has_manual_review = True
                reasons.extend(item for item in stage.reasons if not missing_start_date_notice(item))
                warnings.extend(stage.pending_questions)
            elif stage.action == "request_more":
                has_request_more = True

        if (enabled("installation") and case.pre_catastrophe_outcome != "recommend_reject"
                and case.project.project_type.value in {"unsupported", "unknown"}):
            has_manual_review = True
            reasons.append("无法确认本案光伏阵列属于屋顶式或车棚顶式，需人工核实安装类型")

        if not case.materials and case.package_assessment is None:
            has_request_more = True
            reasons.append("当前案件没有可审核的投保材料")
            missing_requirements.append("补充可审核的投保材料")

        if enabled("location") and not early_stage_outcome and (case.project.longitude is None or case.project.latitude is None):
            informational_rule_ids.append(self.MISSING_COORDINATES_RULE_ID)
            warnings.append("项目经纬度缺失，现场位置仍需核实；城市风雪压按地址或确认的站点匹配")

        location = case.location_assessment
        if not enabled("location"):
            pass
        elif location is None:
            has_manual_review = True
            reasons.append("项目位置尚未完成交叉核验，需确认备案与现场属于同一标的")
        else:
            if location.status == "conflict":
                has_location_conflict = True
                rule_ids.append(self.LOCATION_CONFLICT_RULE_ID)
                has_manual_review = True
                reasons.append("材料位置明显冲突，已暂停自动核保；须核对坐标系、录入和原件，无法解释时再决定是否拒保")
                warnings.extend(location.notes)
            elif location.status in {"single_source", "missing", "uncertain"}:
                has_manual_review = True
                rule_ids.append(self.LOCATION_SINGLE_SOURCE_RULE_ID
                                if location.status == "single_source" else self.LOCATION_UNCERTAIN_RULE_ID)
                reasons.append("项目位置未与备案和现场证据核对一致，需复核后再确定承保建议")
                warnings.extend(location.notes)
                warnings.extend(location.missing_requirements)
            elif location.status == "distance_screened":
                warnings.extend(location.notes)

        if enabled("component") and not early_stage_outcome and not case.project.component_model:
            informational_rule_ids.append(self.MISSING_COMPONENT_MODEL_RULE_ID)
            warnings.append("组件型号缺失，无法核对厂家抗灾参数；不阻断已取得材料的拒保风险检查")

        optional_gaps = []
        check_results = {(item.area_id, item.key): item for item in case.check_results}
        for check in case.applicable_checks:
            if check.required_now or check.applicability == "not_applicable":
                continue
            if check.key == "component" and not case.project.component_model:
                continue  # The model gap already explains this same screening limitation.
            result = check_results.get((check.area_id, check.key))
            if result is None:
                continue
            if result.status == "not_provided":
                optional_gaps.append(f"{check.label}未提供")
            elif result.status == "unusable":
                optional_gaps.append(f"{check.label}不可核")
        if optional_gaps:
            warnings.append(
                "、".join(unique_items(optional_gaps))
                + "；对应检查本轮无法完成，已取得材料的拒保风险检查继续进行"
            )

        for review in case.material_reviews:
            warnings.extend(reason for reason in review.reasons
                            if "拟起保日期未提供" in reason or "15日时效暂无法核验" in reason)
            if review.action is MaterialReviewAction.RECOMMEND_REJECT:
                has_reject = True
                rule_ids.extend(review.triggered_rule_ids)
                reasons.extend(review.reasons)
            elif review.action is MaterialReviewAction.REQUEST_MORE:
                has_request_more = True
                rule_ids.extend(review.triggered_rule_ids)
                reasons.extend(review.reasons)
                missing_requirements.extend(review.missing_requirements)
            elif review.action is MaterialReviewAction.SURCHARGE:
                has_surcharge = True
                rule_ids.extend(review.triggered_rule_ids)
                reasons.extend(review.reasons)
            elif review.action is MaterialReviewAction.CONDITIONAL:
                has_conditional = True
                rule_ids.extend(review.triggered_rule_ids)
                reasons.extend(review.reasons)
                conditions.extend(review.missing_requirements)
            elif review.action is MaterialReviewAction.WARNING:
                comparison_rule = "DOC-COMPARISON-FIELD-UNAVAILABLE-001"
                date_rule = "IMG-WATERMARK-DATE-001"
                rule_ids.extend(item for item in review.triggered_rule_ids
                                if item not in {comparison_rule, date_rule})
                informational_rule_ids.extend(item for item in (comparison_rule, date_rule)
                                              if item in review.triggered_rule_ids)
                reasons.extend(item for item in review.reasons
                               if not item.startswith("交叉核对范围提示：")
                               and not missing_start_date_notice(item))
                warnings.extend(review.reasons)
            if review.requires_manual_review:
                has_manual_review = True

        assessment = case.catastrophe_assessment
        if case.geometry_analysis is not None:
            for area in case.geometry_analysis.areas:
                for label, metric in (
                    ("组件倾角", area.tilt_degrees),
                    ("组件离地高度", area.height_above_ground_m),
                    ("地面粗糙度", area.ground_roughness),
                ):
                    if metric.status == "unknown":
                        warnings.append(f"{area.area_id}的{label}资料不足，抗灾筛查范围受限：{metric.evidence_text}")
        if assessment is not None:
            rule_ids.extend(assessment.triggered_rule_ids)
            reasons.append(assessment.explanation)
            warnings.extend(assessment.factors)
            has_manual_review = has_manual_review or assessment.requires_manual_review
            if (assessment.resistance_level.value == "unknown"
                    or assessment.expected_loss_risk.value == "unknown"):
                has_manual_review = True
                reasons.append("风雪能力或出险风险等级尚未确定，需复核后再确定承保建议")
        elif enabled("wind_snow") and case.pre_catastrophe_outcome == "continue" and case.pv_load_screening_assessment is None:
            has_manual_review = True
            reasons.append("抗灾评估尚未形成结果，需复核后再确定承保建议")

        front_load = case.pv_load_screening_assessment
        if front_load is not None:
            status = front_load.get("status", "INSUFFICIENT_DATA")
            screening_text = (
                "风雪风险量化结果："
                f"{front_load.get('label', status)}；该专项数值比较不能单独形成承保结论。"
            )
            warnings.extend(front_load.get("actions", []))
            if front_load.get("requires_manual_review", True):
                rule_ids.append("CAT-FRONT-LOAD-SCREENING-REVIEW")
                reasons.append(screening_text)
                has_manual_review = True
            else:
                warnings.append(screening_text)
            # Missing upstream/component data are internal review gaps, not proof
            # that the customer failed to provide required materials.

        provider_failed = any(
            trace.status in {ProcessingStatus.PARTIAL, ProcessingStatus.FAILED}
            for trace in case.processing_trace
        )
        if provider_failed:
            warnings.append("部分自动识别服务未完整执行，需要人工检查处理留痕")
            has_manual_review = True
            rule_ids.append(self.PROVIDER_FAILURE_RULE_ID)
            reasons.append("自动识别服务未完整执行")

        if case.ruleset_snapshot:
            if not active:
                has_manual_review = True
                reasons.append("本次没有可执行的业务规则，无法形成承保建议")
            incomplete = [r for r in case.rule_execution if r["status"] in {
                "applicability_pending", "dependency_pending", "not_executed", "evidence_insufficient", "material_missing"
            }]
            if incomplete:
                has_manual_review = True
                reasons.append("所选规则尚未全部完成：" + "、".join(r["name"] for r in incomplete))
            omitted = [r for r in case.rule_execution if r["status"] == "not_selected"]
            warnings.append("本次结论仅覆盖规则快照中的已执行检查；未选择、不适用和未完成检查均不代表风险已排除。")
            if omitted:
                has_manual_review = True
                reasons.append("本次为专项核验，需核保人员结合完整承保范围确认最终意见")
        # 明确的禁保事实必须保留在整案结论中，不能被其他材料缺失覆盖。
        if has_reject:
            decision = DecisionType.RECOMMEND_REJECT
        elif has_location_conflict:
            decision = DecisionType.MANUAL_REVIEW
        elif has_request_more:
            decision = DecisionType.REQUEST_MORE
        elif has_stage_manual or has_manual_review:
            decision = DecisionType.MANUAL_REVIEW
        elif has_conditional:
            decision = DecisionType.CONDITIONAL_ACCEPT
        else:
            decision = DecisionType.ACCEPT
            if not reasons:
                reasons.append("已完成的检查未发现拒保或需人工核实的事实；未完成检查和风险提示仍须核对")

        explanations = explain_rules(unique_items([*rule_ids, *informational_rule_ids]))
        if case.ruleset_snapshot:
            days = case.ruleset_snapshot["parameters"]["watermark_days"]
            explanations = [item.model_copy(update={"trigger": item.trigger.replace("15 日", f"{days} 日")})
                            if item.rule_id == "IMG-WATERMARK-DATE-001" else item for item in explanations]
        return UnderwritingDecision(
            decision=decision,
            pricing_recommendation=(
                "surcharge" if has_surcharge and decision in {
                    DecisionType.ACCEPT, DecisionType.CONDITIONAL_ACCEPT,
                } else "not_proposed"
            ),
            decisive_rule_ids=unique_items(rule_ids),
            rule_explanations=explanations,
            reasons=unique_items(reasons),
            conditions=unique_items(conditions),
            warnings=unique_items(warnings),
            missing_requirements=unique_items(missing_requirements),
            generated_at=datetime.now(UTC),
        )
