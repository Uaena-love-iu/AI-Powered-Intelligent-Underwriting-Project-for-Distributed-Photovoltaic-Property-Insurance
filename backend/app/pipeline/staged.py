"""Ordered first review with evidence-backed early exits."""

import os
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from time import perf_counter_ns

from app.contracts import (
    DetectionStatus,
    InstallationAnalysis,
    InstallationSceneType,
    InstallationType,
    MaterialCategory,
    MaterialParseStatus,
    MaterialQualityStatus,
    MaterialReviewAction,
    ProcessingStatus,
    ProcessingStep,
    ProcessingTrace,
    ProjectType,
    RiskCategory,
    RiskFinding,
    StageReview,
    UnderwritingCase,
)
from app.location import assess_location
from app.providers import MaterialInput, ProviderError
from app.rules.completeness import assess_checks, build_applicable_checks


def _trace(step: ProcessingStep, provider: str, status: ProcessingStatus,
           detail: str | None = None, *, started: datetime | None = None,
           start_ns: int | None = None, model: str | None = None) -> ProcessingTrace:
    now = datetime.now(UTC)
    return ProcessingTrace(
        step=step, provider=provider, model=model, started_at=started or now,
        finished_at=now, latency_ms=(perf_counter_ns() - start_ns) // 1_000_000
        if start_ns is not None else 0, status=status, detail=detail,
    )


def _installation_project(project, analysis: InstallationAnalysis):
    if len(analysis.areas) != 1 or analysis.areas[0].status != "confirmed":
        return project
    scene_type = analysis.areas[0].scene_type
    if scene_type is InstallationSceneType.BUILDING_ROOF:
        kind = ProjectType.ROOFTOP
    elif scene_type is InstallationSceneType.CARPORT_ROOF:
        kind = ProjectType.CARPORT
    else:
        return project
    updates = {"project_type": kind}
    # The scene result owns project type; a single-image finding cannot overwrite it.
    if kind is ProjectType.CARPORT:
        updates["installation_type"] = InstallationType.CARPORT_ROOF
    elif project.installation_type.value == "carport_roof":
        updates["installation_type"] = InstallationType.UNKNOWN
    return project.model_copy(update=updates)


_INSTALLATION_SCENE_CATEGORIES = {
    MaterialCategory.PANORAMA,
    MaterialCategory.ROOF_CONNECTION,
    MaterialCategory.PARAPET,
    MaterialCategory.WORKSHOP,
    MaterialCategory.COMPONENT_SURFACE,
    MaterialCategory.OTHER,
}
_EQUIPMENT_AND_DOCUMENT_CATEGORIES = {
    MaterialCategory.COMPONENT_NAMEPLATE,
    MaterialCategory.INVERTER_NAMEPLATE,
    MaterialCategory.COMBINER_BOX,
    MaterialCategory.EQUIPMENT_INVENTORY,
    MaterialCategory.FILING_CERTIFICATE,
    MaterialCategory.PROJECT_DOCUMENT,
    MaterialCategory.GRID_CONNECTION_DOCUMENT,
}


def _installation_scene_input(item: MaterialInput) -> bool:
    """Use site photos, not equipment labels or documents, to identify PV arrays."""
    material = item.material
    if material.media_type not in {"image/jpeg", "image/png"}:
        return False
    if material.category in _EQUIPMENT_AND_DOCUMENT_CATEGORIES:
        return bool(set(material.additional_categories)
                    & (_INSTALLATION_SCENE_CATEGORIES - {MaterialCategory.OTHER}))
    return bool({material.category, *material.additional_categories}
                & _INSTALLATION_SCENE_CATEGORIES)


def _stage_from_material(review, findings, fields) -> StageReview:
    finding_by_id = {item.finding_id: item for item in findings}
    field_by_id = {item.field_id: item for item in fields}
    rejected = review.action is MaterialReviewAction.RECOMMEND_REJECT
    if rejected:
        action = "recommend_reject"
    elif review.action is MaterialReviewAction.REQUEST_MORE:
        action = "request_more"
    elif review.requires_manual_review:
        action = "manual_review"
    else:
        action = "continue"
    return StageReview(
        stage="single_image", action=action,
        rule_ids=review.triggered_rule_ids, reasons=review.reasons,
        material_ids=[review.material_id], finding_ids=review.finding_ids,
        ocr_field_ids=review.ocr_field_ids,
        evidence_texts=[finding_by_id[item].evidence_text for item in review.finding_ids
                        if item in finding_by_id] +
                       [f"{field_by_id[item].evidence_text + '；' if field_by_id[item].evidence_text else ''}"
                        f"原文：{field_by_id[item].raw_value or field_by_id[item].normalized_value or ''}"
                        for item in review.ocr_field_ids if item in field_by_id],
        customer_requests=review.missing_requirements,
        terminates_run=rejected,
    )


def _single_image_workers() -> int:
    """Keep external model concurrency bounded and locally configurable."""
    raw = os.getenv("PV_SINGLE_IMAGE_WORKERS", "4")
    try:
        workers = int(raw)
    except ValueError as exc:
        raise ValueError("PV_SINGLE_IMAGE_WORKERS must be an integer from 1 to 8") from exc
    if not 1 <= workers <= 8:
        raise ValueError("PV_SINGLE_IMAGE_WORKERS must be an integer from 1 to 8")
    return workers


def _inspect_single_material(pipeline, source: MaterialInput, filing_ids: set[str]):
    """Run dependent calls for one material without mutating shared case state."""
    item = replace(source, material=source.material.model_copy(deep=True))
    detected, extracted, item_traces = [], [], []
    if item.material.media_type in {"image/jpeg", "image/png"} and item.material.category not in {
        MaterialCategory.FILING_CERTIFICATE, MaterialCategory.PROJECT_DOCUMENT,
        MaterialCategory.GRID_CONNECTION_DOCUMENT,
    }:
        detected, trace = pipeline._run_vision([item])
        # Only the grouped photos may support a visible obstruction finding;
        # a saved single-image prompt cannot turn a missing view into a risk.
        policy = getattr(pipeline, "policy", None)
        detected = [finding for finding in detected
                    if finding.category is not RiskCategory.AISLE_OBSTRUCTION
                    and (policy is None or finding.category in policy.risks)]
        item_traces.append(trace)
    if item.material.material_id not in filing_ids:
        extracted, trace = pipeline._run_ocr([item])
        item_traces.append(trace)
    return item, detected, extracted, item_traces


def run_staged(pipeline, *, case_id, project, materials: list[MaterialInput]) -> UnderwritingCase:
    """Perform only the work justified by the preceding stage's result."""
    policy = getattr(pipeline, "policy", None)
    enabled = lambda key: policy is None or policy.enabled(key)
    run_started_at = datetime.now(UTC)
    traces: list[ProcessingTrace] = []
    stages: list[StageReview] = []
    findings = []
    ocr_fields = []
    material_reviews = []
    installation = None
    geometry = None
    applicable_checks = []
    check_results = []
    package = None
    equipment = []
    component = weather = location = None
    front_load = None
    outcome = "continue"
    from app.verification.engine import enabled as verification_enabled
    verification = {}

    def finish() -> UnderwritingCase:
        case = UnderwritingCase(
            schema_version="0.1.0", case_id=case_id, run_started_at=run_started_at,
            project=project,
            materials=[item.material for item in materials], equipment_inventory=equipment,
            ocr_fields=ocr_fields, findings=findings, component_profile=component,
            weather_profile=weather, catastrophe_assessment=None,
            pv_load_screening_assessment=front_load,
            component_module_result=getattr(pipeline.component_provider, "last_result", {}),
            location_assessment=location, package_assessment=package,
            material_reviews=material_reviews, installation_analysis=installation,
            applicable_checks=applicable_checks, check_results=check_results,
            geometry_analysis=geometry,
            stage_reviews=stages, pre_catastrophe_outcome=outcome,
            decision=None, processing_trace=traces,
            verification=verification,
        )
        if policy:
            case.ruleset_snapshot = policy.snapshot
            case.rule_execution = policy.executions(case)
        pipeline._notify_progress("正在汇总综合核保意见", 95)
        decision, decision_trace = pipeline._run_decision(case)
        payload = case.model_dump(mode="python")
        payload["decision"] = decision
        payload["processing_trace"] = [*traces, decision_trace]
        pipeline._notify_progress("核保分析完成", 98)
        result = UnderwritingCase.model_validate(payload)
        if verification_enabled():
            from app.verification.engine import initial, apply_pending, report_items
            if not result.verification:
                result = initial(result, pipeline.vision_provider, materials)
            result.verification['rule_missing_requirements'] = list(result.decision.missing_requirements)
            apply_pending(result)
            report_items(result)
            from app.verification.engine import prepare_questions
            prepare_questions(result, pipeline.vision_provider, materials)
        return result

    def stop_after_rejection(reason: str,
                             remaining: list[MaterialInput] | None = None) -> UnderwritingCase:
        nonlocal outcome
        outcome = "recommend_reject"
        for item in remaining or []:
            traces.append(_trace(ProcessingStep.VISION, "pipeline", ProcessingStatus.SKIPPED,
                                 f"{item.material.material_id}：因已确认拒保而未启动单图识别"))
            traces.append(_trace(ProcessingStep.OCR, "pipeline", ProcessingStatus.SKIPPED,
                                 f"{item.material.material_id}：因已确认拒保而未启动后续文字提取"))
        for step in (
            ProcessingStep.INSTALLATION_GROUP, ProcessingStep.VISION,
            ProcessingStep.COMPLETENESS, ProcessingStep.GEOMETRY_GROUP,
            ProcessingStep.COMPONENT_LOOKUP,
            ProcessingStep.WEATHER_LOOKUP, ProcessingStep.CATASTROPHE_ASSESSMENT,
        ):
            if not any(trace.step is step for trace in traces):
                traces.append(_trace(step, "pipeline", ProcessingStatus.SKIPPED,
                                     f"因已确认拒保而跳过：{reason}"))
        return finish()

    pipeline._notify_progress("正在核验材料可读性", 2)
    usable = [item for item in materials
              if item.material.parse_status is MaterialParseStatus.SUCCESS
              and item.material.quality_status is not MaterialQualityStatus.UNUSABLE
              and item.material.duplicate_of_material_id is None
              and bool(item.content)]
    duplicate_count = sum(item.material.duplicate_of_material_id is not None
                          for item in materials)
    traces.append(_trace(ProcessingStep.MATERIAL_PARSE, "file-preflight",
                         ProcessingStatus.SUCCESS if len(usable) + duplicate_count == len(materials)
                         else ProcessingStatus.PARTIAL,
                         f"可分析 {len(usable)}/{len(materials)} 份材料；重复 {duplicate_count} 份不重复计数"))
    if not usable:
        stages.append(StageReview(stage="completeness", action="request_more",
                                  rule_ids=["DOC-NO-USABLE-MATERIAL-001"],
                                  reasons=["没有可分析的材料"],
                                  customer_requests=["修复损坏或不可读的上传材料"]))
        outcome = "request_more"
        return finish()

    pipeline._notify_progress("正在提取备案关键事实", 8)
    filing_inputs = [item for item in usable if item.material.category in
                     {MaterialCategory.FILING_CERTIFICATE, MaterialCategory.PROJECT_DOCUMENT}]
    filing_ids = {item.material.material_id for item in filing_inputs}
    if filing_inputs and (enabled("filing") or enabled("location")):
        filing_fields, filing_trace = pipeline._run_ocr(filing_inputs)
        filing_fields = pipeline.rule_engine.normalize_filing_fields(filing_fields)
        ocr_fields.extend(filing_fields)
        traces.append(filing_trace)
        materials = pipeline._apply_ocr_metadata(materials, filing_fields)
        usable_ids = {item.material.material_id for item in usable}
        usable = [item for item in materials if item.material.material_id in usable_ids]
    else:
        traces.append(_trace(ProcessingStep.OCR, pipeline.ocr_provider.name,
                             ProcessingStatus.SKIPPED, "没有备案类材料"))
    filing_review = pipeline.rule_engine.evaluate_filing_stage(
        [item.material for item in materials], ocr_fields, project,
    ) if enabled("filing") else StageReview(stage="filing", action="continue", reasons=["本次未执行备案准入规则"])
    stages.append(filing_review)
    traces.append(_trace(ProcessingStep.FILING_RULES, "builtin-rule-engine",
                         ProcessingStatus.SUCCESS, ",".join(filing_review.rule_ids) or "未命中禁投"))
    if filing_review.terminates_run and not verification_enabled():
        return stop_after_rejection("备案禁投规则", usable)

    pipeline._notify_progress("正在综合现场图片确认安装类型", 20)
    group_inputs = [item for item in usable if _installation_scene_input(item)]
    started, start_ns = datetime.now(UTC), perf_counter_ns()
    if enabled("installation"):
        try:
            installation = pipeline.vision_provider.analyze_installation(
                group_inputs, filing_fields=[field for field in ocr_fields
                                             if field.material_id in filing_ids],
            )
            group_status = ProcessingStatus.SUCCESS if group_inputs else ProcessingStatus.SKIPPED
        except ProviderError:
            from app.contracts import InstallationArea
            installation = InstallationAnalysis(
                areas=[InstallationArea(
                    area_id="project", scene_type=InstallationSceneType.UNKNOWN,
                    status="unknown", material_ids=[item.material.material_id for item in group_inputs],
                    evidence_text="组图识别服务失败，尚不能确认安装载体",
                    pending_question="内部重试组图识别或人工核验原图",
                )], provider=pipeline.vision_provider.name,
                model=pipeline.vision_provider.installation_model_name,
            )
            group_status = ProcessingStatus.FAILED
    else:
        installation = InstallationAnalysis(areas=[], provider="rule-selection")
        group_status = ProcessingStatus.SKIPPED
    traces.append(_trace(ProcessingStep.INSTALLATION_GROUP,
                         pipeline.vision_provider.name, group_status,
                         "依据原图综合判断承载结构", started=started, start_ns=start_ns,
                         model=pipeline.vision_provider.installation_model_name))
    type_review = (pipeline.rule_engine.evaluate_installation_type(installation) if enabled("installation")
                   else StageReview(stage="installation_type", action="continue", reasons=["本次未执行安装类型准入规则"]))
    stages.append(type_review)
    traces.append(_trace(ProcessingStep.INSTALLATION_RULES, "builtin-rule-engine",
                         ProcessingStatus.SUCCESS,
                         ",".join(type_review.rule_ids) or "安装类型已确认，继续专项检查"))
    if type_review.terminates_run and not verification_enabled():
        return stop_after_rejection("安装类型不是屋顶式或车棚顶式", usable)
    if type_review.action == "continue":
        project = _installation_project(project, installation)

    aisle_finding = None
    aisle = installation.aisle_assessment
    # Missing views and uncertain group guesses are not an aisle inspection failure.
    # Only a confirmed, visible obstruction becomes a reportable finding.
    if enabled("aisle") and aisle is not None and aisle.status == "detected":
        aisle_finding = RiskFinding(
            finding_id=f"{aisle.material_id}:aisle_obstruction:group",
            material_id=aisle.material_id, category=RiskCategory.AISLE_OBSTRUCTION,
            label="通道阻塞", detection_status=DetectionStatus.DETECTED,
            severity=aisle.severity,
            confidence=aisle.confidence,
            bbox=aisle.bbox,
            evidence_text="组图依据（" + "、".join(aisle.supporting_material_ids)
                          + "）：" + aisle.cross_image_reasoning
                          + "；结论：" + aisle.evidence_text,
            provider=installation.provider,
            model=installation.model or pipeline.vision_provider.installation_model_name,
            requires_manual_review=aisle.bbox is None,
        )
        findings.append(aisle_finding)

    applicable_checks = build_applicable_checks(
        installation, profile=pipeline.rule_engine.config.package_requirement_profile,
        materials=[item.material for item in materials],
    )

    if policy:
        applicable_checks = policy.filter_checks(applicable_checks)

    workers = min(_single_image_workers(), len(usable))
    pipeline._notify_progress("正在逐份识别并匹配材料规则", 35)
    material_positions = {item.material.material_id: index
                          for index, item in enumerate(materials)}
    pending: deque[tuple[int, Future]] = deque()
    next_to_submit = 0
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="single-image") as executor:
        def fill_window() -> None:
            nonlocal next_to_submit
            while len(pending) < workers and next_to_submit < len(usable):
                index = next_to_submit
                pending.append((index, executor.submit(
                    _inspect_single_material, pipeline, usable[index], filing_ids,
                )))
                next_to_submit += 1

        fill_window()
        for index in range(len(usable)):
            submitted_index, future = pending.popleft()
            assert submitted_index == index
            item, detected, extracted, item_traces = future.result()
            item_failed = any(trace.status in {ProcessingStatus.FAILED, ProcessingStatus.PARTIAL}
                              for trace in item_traces)
            current_fields = [field for field in ocr_fields
                              if field.material_id == item.material.material_id]
            current_fields.extend(extracted)
            findings.extend(detected)
            ocr_fields.extend(extracted)
            traces.extend(item_traces)
            if item.material.material_id not in filing_ids:
                item = pipeline._apply_ocr_metadata([item], extracted)[0]
            materials[material_positions[item.material.material_id]] = item
            review = pipeline.rule_engine.evaluate_material(
                item.material, findings=detected, ocr_fields=current_fields,
                project=project, analysis_failed=item_failed,
            )
            material_reviews.append(review)
            stage = _stage_from_material(review, detected, current_fields)
            stages.append(stage)
            pipeline._notify_progress(f"逐份材料检查 {index + 1}/{len(usable)}", 36 +
                                      int(38 * (index + 1) / len(usable)))
            if stage.terminates_run and not verification_enabled():
                for _, remaining_future in pending:
                    _, _, _, unused_traces = remaining_future.result()
                    traces.extend(trace.model_copy(update={
                        "detail": "并发任务已启动；前序材料拒保，识别结果未纳入结论",
                    }) for trace in unused_traces)
                return stop_after_rejection("单份材料命中明确拒保规则",
                                            usable[next_to_submit:])
            fill_window()

    if verification_enabled():
        from app.verification.engine import initial
        provisional = UnderwritingCase(schema_version='0.1.0', case_id=case_id, project=project,
            materials=[m.material for m in materials], ocr_fields=ocr_fields, findings=findings,
            material_reviews=material_reviews, stage_reviews=stages, processing_trace=traces,
            installation_analysis=installation, ruleset_snapshot=policy.snapshot if policy else {})
        pipeline._notify_progress('正在围绕案件存疑点定向核验', 75)
        provisional = initial(provisional, pipeline.vision_provider, materials)
        findings = provisional.findings
        verification = provisional.verification
        verification['screening_input'] = getattr(pipeline,'pv_load_screening_input',{})
        installation = provisional.installation_analysis
        project = _installation_project(project, installation)
        applicable_checks = build_applicable_checks(installation, profile=pipeline.rule_engine.config.package_requirement_profile, materials=[m.material for m in materials])
        if policy:
            applicable_checks = policy.filter_checks(applicable_checks)
        aisle_finding = next((f for f in findings if f.category is RiskCategory.AISLE_OBSTRUCTION and f.detection_status is DetectionStatus.DETECTED),None)
        type_review = pipeline.rule_engine.evaluate_installation_type(installation) if enabled('installation') else type_review
        stages = [s for s in stages if s.stage not in {'installation_type','installation_scope'}] + [type_review]
    pipeline._notify_progress("正在正式判定材料完整性", 76)
    material_models = [item.material for item in materials]
    review_material_models = [item for item in material_models
                              if item.duplicate_of_material_id is None]
    failed_material_ids = {
        material_id for trace in traces for material_id in trace.failed_material_ids
    }
    material_reviews, rule_trace = pipeline._run_rules(
        review_material_models, findings=findings, ocr_fields=ocr_fields, project=project,
        failed_material_ids=failed_material_ids,
    )
    material_reviews = pipeline.rule_engine.corroborate_panorama_shading(
        review_material_models, findings, material_reviews,
    )
    final_review_by_id = {review.material_id: review for review in material_reviews}
    stages = [
        _stage_from_material(final_review_by_id[stage.material_ids[0]], findings, ocr_fields)
        if stage.stage == "single_image" and len(stage.material_ids) == 1
        and stage.material_ids[0] in final_review_by_id else stage
        for stage in stages
    ]
    traces.append(rule_trace)
    package = pipeline.rule_engine.assess_package(material_models,
                                                  project_type=project.project_type,
                                                  applicable_checks=applicable_checks)
    check_results = assess_checks(
        applicable_checks, material_models, findings, ocr_fields, installation,
        failed_material_ids=failed_material_ids,
        allowed_risks=policy.risks if policy else None,
    )
    if filing_review.terminates_run or type_review.terminates_run:
        return stop_after_rejection('前置业务准入规则命中，首轮图片与核验记录已保留')
    if any(review.action is MaterialReviewAction.RECOMMEND_REJECT for review in material_reviews):
        rejected = [review for review in material_reviews
                    if review.action is MaterialReviewAction.RECOMMEND_REJECT]
        for review in rejected:
            stages.append(_stage_from_material(review, findings, ocr_fields))
        return stop_after_rejection("完整逐项规则复核命中拒保")
    equipment, equipment_trace = pipeline._run_equipment_inventory(usable) if enabled("component") else ([], None)
    if equipment_trace is not None:
        traces.append(equipment_trace)
    if not hasattr(pipeline.component_provider, "lookup_materials"):
        project = pipeline._apply_inventory_project_fields(project, equipment)
        project = pipeline._apply_ocr_project_fields(project, ocr_fields)
    location, weather_coordinates = assess_location(
        project, review_material_models, ocr_fields, geocoder=pipeline.location_geocoder,
    ) if enabled("location") else (None, None)
    if location and location.address_photo_conflict:
        stages.append(StageReview(
            stage="location", action="manual_review",
            rule_ids=["LOC-CONFLICT-001"], reasons=location.notes,
            material_ids=[item.material_id for item in location.evidence if item.material_id],
            terminates_run=True,
        ))
        outcome = "manual_review"
        for step in (ProcessingStep.COMPLETENESS, ProcessingStep.GEOMETRY_GROUP,
                     ProcessingStep.COMPONENT_LOOKUP, ProcessingStep.WEATHER_LOOKUP,
                     ProcessingStep.CATASTROPHE_ASSESSMENT):
            if not any(trace.step is step for trace in traces):
                traces.append(_trace(step, "pipeline", ProcessingStatus.SKIPPED,
                                     "位置证据存在重大冲突，暂停自动核保并等待人工核对"))
        return finish()
    project = pipeline._apply_filing_site_address(project, review_material_models, ocr_fields)
    if weather_coordinates is not None:
        project = project.model_copy(update={"longitude": weather_coordinates[0],
                                     "latitude": weather_coordinates[1]})
    concrete_missing = [item.customer_request for item in check_results if item.customer_request]
    material_requests = [request for review in material_reviews
                         for request in review.missing_requirements]
    customer_requests = list(dict.fromkeys([
        *concrete_missing, *material_requests,
    ]))
    if customer_requests:
        outcome = "request_more"
    elif (type_review.action == "manual_review" or filing_review.action == "manual_review"
          or aisle_finding is not None and aisle_finding.requires_manual_review):
        outcome = "manual_review"
    else:
        outcome = "continue"
    stages.append(StageReview(
        stage="completeness", action=outcome,
        rule_ids=list(dict.fromkeys(
            rule_id for result in check_results for rule_id in result.rule_ids
        )),
        reasons=["本轮存在需要补充的客户材料或信息"] if customer_requests
        else ["部分检查项仍待内部复核"] if outcome == "manual_review" else [],
        material_ids=[item.material.material_id for item in materials],
        customer_requests=customer_requests,
        pending_questions=[result.reason for result in check_results
                           if result.status == "pending" and result.customer_request is None],
    ))
    traces.append(_trace(ProcessingStep.COMPLETENESS, "builtin-rule-engine",
                         ProcessingStatus.SUCCESS, outcome))
    if outcome != "continue":
        for step in (ProcessingStep.GEOMETRY_GROUP, ProcessingStep.COMPONENT_LOOKUP,
                     ProcessingStep.WEATHER_LOOKUP,
                     ProcessingStep.CATASTROPHE_ASSESSMENT):
            traces.append(_trace(step, "pipeline", ProcessingStatus.SKIPPED,
                                 f"前置阶段结论为 {outcome}，本轮未启动抗灾测算"))
        return finish()

    if not enabled("wind_snow"):
        if enabled("component") and pipeline.component_provider is not None:
            component, trace = pipeline._run_component_lookup(project, usable)
            traces.append(trace)
        for step in (ProcessingStep.GEOMETRY_GROUP, ProcessingStep.CATASTROPHE_ASSESSMENT):
            traces.append(_trace(step, "rule-selection", ProcessingStatus.SKIPPED, "本次未启用风雪承载风险检查"))
        if not enabled("component"):
            traces.append(_trace(ProcessingStep.COMPONENT_LOOKUP, "rule-selection", ProcessingStatus.SKIPPED, "本次未启用组件参数核验"))
        return finish()

    pipeline._notify_progress("正在并行估计组图参数与核验组件能力", 82)
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="capacity") as capacity_pool:
        component_future = (capacity_pool.submit(pipeline._run_component_lookup, project, usable)
                            if pipeline.component_provider is not None else None)
        started, start_ns = datetime.now(UTC), perf_counter_ns()
        geometry_error = None
        for attempt in range(1 if getattr(pipeline.vision_provider, "manages_retries", False) else 2):
            try:
                geometry = pipeline.vision_provider.estimate_geometry(group_inputs, installation)
                geometry_error = None
                break
            except ProviderError as exc:
                geometry_error = exc
                from app.pipeline.recovery import classify_error
                if not classify_error(exc)["auto_retry"]:
                    break
        if geometry_error is None:
            geometry_incomplete = any(
                metric.status == "unknown"
                for area in geometry.areas for metric in (
                    area.tilt_degrees, area.height_above_ground_m, area.ground_roughness)
            )
            geometry_status = ProcessingStatus.SUCCESS
        else:
            from app.providers.vision.base import VisionProvider
            geometry = VisionProvider.estimate_geometry(
                pipeline.vision_provider, group_inputs, installation,
            )
            geometry_incomplete = True
            geometry_status = ProcessingStatus.FAILED
        if geometry_error is not None:
            geometry_detail = "调用未能完成，部分字段未知；请查看任务异常或人工评估"
        elif attempt:
            geometry_detail = "首次调用失败后重试成功；" + (
                "部分字段未知" if geometry_incomplete else "各字段已返回粗估"
            )
        else:
            geometry_detail = "部分字段未知，需后续人工评估" if geometry_incomplete else "各字段已返回粗估"
        traces.append(_trace(ProcessingStep.GEOMETRY_GROUP, pipeline.vision_provider.name,
                             geometry_status,
                             "整单光伏组件倾角、离地高度和地面粗糙度候选；" + geometry_detail,
                             started=started, start_ns=start_ns,
                             model=pipeline.vision_provider.geometry_model_name).model_copy(update={
                                 "error_code": "GEOMETRY_PROVIDER_FAILURE" if geometry_error else None,
                                 "error_message": type(geometry_error.__cause__ or geometry_error).__name__
                                 if geometry_error else None,
                             }))

        if pipeline.component_provider is not None:
            pipeline._notify_progress("正在识别组件型号并查询参数来源（联网检索可能需要数分钟）", 83)
            component, trace = component_future.result()
            if component is not None and not project.component_model:
                project = project.model_copy(update={"component_model": component.component_model})
            traces.append(trace)
    traces.append(_trace(ProcessingStep.WEATHER_LOOKUP, "pipeline", ProcessingStatus.SKIPPED,
                         "整单采用全国城市基本风压、雪压数据库，不调用历史气象服务"))
    from copy import deepcopy
    screening_input = deepcopy(getattr(pipeline, "pv_load_screening_input", {}))
    if "ai_geometry" not in screening_input and geometry and len(geometry.areas) == 1:
        area = geometry.areas[0]
        auto_geometry = {}
        for field, metric in (("height_m", area.height_above_ground_m), ("tilt_deg", area.tilt_degrees)):
            if metric.status != "unknown" and metric.value_min is not None and metric.value_max is not None:
                auto_geometry[field] = {"min": metric.value_min, "max": metric.value_max,
                                        "source": metric.evidence_text + "；材料：" + ",".join(metric.material_ids)}
        metric = area.ground_roughness
        if metric.status != "unknown" and metric.roughness_candidate:
            auto_geometry["terrain"] = {"candidates": [metric.roughness_candidate],
                                         "source": metric.evidence_text + "；材料：" + ",".join(metric.material_ids)}
        if auto_geometry:
            screening_input["ai_geometry"] = auto_geometry
    pipeline._notify_progress("正在按城市基本风雪压进行统一风险量化", 86)
    front_load, trace = pipeline._run_front_load_screening(
        case_id=case_id, project=project, component_profile=component,
        screening_input=screening_input,
        location_conflict=bool(location and location.status == "conflict"),
    )
    traces.append(trace)
    return finish()
