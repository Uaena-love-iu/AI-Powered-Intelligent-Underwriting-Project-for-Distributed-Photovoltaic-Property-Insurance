"""Refresh affected shared facts using the existing providers/calculator unchanged."""
from copy import deepcopy
from app.pipeline import UnderwritingPipeline
from app.rules.selection import Policy
from app.rules.config import BusinessRulesConfig
from app.location import assess_location, AmapGeocoder
from .engine import add_issue, human, now


def refresh(case, inputs, categories, vision, ocr, affected_ids=None):
    policy=Policy(case.ruleset_snapshot) if case.ruleset_snapshot else None
    enabled=lambda rule: policy is None or policy.enabled(rule)
    scene=bool(categories & {'panorama','roof_connection','parapet','workshop','project_document','filing_certificate'})
    identity=bool(categories & {'component_nameplate','equipment_inventory'})
    location=bool(categories & {'panorama','roof_connection','filing_certificate','project_document'})
    if not (scene or identity or location):
        return
    p=UnderwritingPipeline(vision_provider=vision,ocr_provider=ocr)
    p.policy=policy
    if policy:p.rule_engine.config=BusinessRulesConfig.model_validate(case.ruleset_snapshot['business_config'])
    p.rule_engine.rule_policy=policy
    from .engine import area_for
    affected_areas={area_for(case,mid) for mid in (affected_ids or [])}
    scoped=bool(affected_areas) and None not in affected_areas and 'project' not in affected_areas
    prior_geometry=case.geometry_analysis
    call=dict(id='dependencies-'+str(case.verification['version']),issue_id='shared-facts',version=case.verification['version'],
              tool='refresh_dependencies',material_ids=[m.material.material_id for m in inputs],started_at=now(),status='running')
    case.verification['calls'].append(call)
    try:
        if identity:
            case.component_review={}
            case.component_profile=None
            case.component_module_result={}
        if scene:
            case.geometry_analysis=None
        if scene or identity or location:
            case.pv_load_screening_assessment=None
        case.project=p._apply_ocr_project_fields(case.project,case.ocr_fields)
        case.project=p._apply_filing_site_address(case.project,case.materials,case.ocr_fields)
        if categories & {'filing_certificate','project_document'}:
            filing=p.rule_engine.evaluate_filing_stage(case.materials,case.ocr_fields,case.project)
            case.stage_reviews=[s for s in case.stage_reviews if s.stage!='filing']+[filing]
        if location and enabled('location'):
            case.location_assessment,coords=assess_location(case.project,case.materials,case.ocr_fields,geocoder=AmapGeocoder.from_environment())
            if coords:case.project=case.project.model_copy(update={'longitude':coords[0],'latitude':coords[1]})
        if scene and enabled('installation'):
            from app.pipeline.staged import _installation_scene_input, _installation_project
            relevant=[m for m in inputs if _installation_scene_input(m)]
            if scoped:
                relevant=[m for m in relevant if area_for(case,m.material.material_id) in affected_areas]
            old_areas=case.installation_analysis.areas if case.installation_analysis else []
            updated=vision.analyze_installation(relevant,filing_fields=case.ocr_fields)
            if scoped and len(affected_areas)==1 and len(updated.areas)==1:
                updated.areas[0].area_id=next(iter(affected_areas))
            if scoped:
                updated.areas=[a for a in old_areas if a.area_id not in affected_areas]+updated.areas
            case.installation_analysis=updated
            case.project=_installation_project(case.project,case.installation_analysis)
            call['installation']=case.installation_analysis.model_dump(mode='json')
        from .engine import recalculate
        recalculate(case)
        prerequisites=not case.decision.missing_requirements and all(s.action=='continue' for s in case.stage_reviews if s.stage in {'filing','installation_type','location'})
        if identity and enabled('component'):
            from app.api.component_dependencies import get_underwriting_component_provider
            p.component_provider=get_underwriting_component_provider()
            profile,trace=p._run_component_lookup(case.project,inputs)
            case.component_profile=profile
            case.component_module_result=getattr(p.component_provider,'last_result',{})
            case.processing_trace.append(trace)
        if enabled('wind_snow') and prerequisites:
            from app.pipeline.staged import _installation_scene_input
            if case.geometry_analysis is None and case.installation_analysis:
                geo_inputs=[m for m in inputs if _installation_scene_input(m) and (not scoped or area_for(case,m.material.material_id) in affected_areas)]
                geo_installation=case.installation_analysis.model_copy(deep=True)
                if scoped:geo_installation.areas=[a for a in geo_installation.areas if a.area_id in affected_areas]
                updated_geometry=vision.estimate_geometry(geo_inputs,geo_installation)
                if scoped and prior_geometry:
                    updated_geometry.areas=[a for a in prior_geometry.areas if a.area_id not in affected_areas]+updated_geometry.areas
                case.geometry_analysis=updated_geometry
            data=deepcopy(case.verification.get('screening_input',{}))
            geometry=case.geometry_analysis
            if geometry and len(geometry.areas)==1 and 'ai_geometry' not in data:
                area=geometry.areas[0];auto={}
                for name,metric in [('height_m',area.height_above_ground_m),('tilt_deg',area.tilt_degrees)]:
                    if metric.status!='unknown' and metric.value_min is not None and metric.value_max is not None:
                        auto[name]={'min':metric.value_min,'max':metric.value_max,'source':metric.evidence_text+'；材料：'+','.join(metric.material_ids)}
                metric=area.ground_roughness
                if metric.status!='unknown' and metric.roughness_candidate:
                    auto['terrain']={'candidates':[metric.roughness_candidate],'source':metric.evidence_text+'；材料：'+','.join(metric.material_ids)}
                if auto:data['ai_geometry']=auto
            assessment,trace=p._run_front_load_screening(case_id=case.case_id,project=case.project,component_profile=case.component_profile,
                screening_input=data,location_conflict=bool(case.location_assessment and case.location_assessment.status=='conflict'))
            case.pv_load_screening_assessment=assessment
            case.processing_trace.append(trace)
        elif enabled('wind_snow'):
            add_issue(case,source='dependency-block:'+str(case.verification['version']),category=None,material_ids=[m.material.material_id for m in inputs],finding_ids=[],
                question='共享事实或前置材料尚未确认，风雪专项结果已失效，请先完成相应核验。',reason='不沿用失效的旧计算值；仍使用原抗灾计算方法。',kind='business')
            human(case.verification['issues'][-1],'待前置核验完成后更新专项计算')
            case.verification['issues'][-1]['system_blocker']=True
            case.verification['issues'][-1]['dependency_categories']=sorted(categories)
            case.verification['issues'][-1]['affected_ids']=affected_ids
            case.verification['issues'][-1]['recovery_count']=max((i.get('recovery_count',0) for i in case.verification['issues'] if i.get('system_blocker')),default=0)
        call.update(status='success',finished_at=now())
    except Exception as exc:
        call.update(status='failed',error=type(exc).__name__,finished_at=now())
        add_issue(case,source='dependency-failure:'+str(case.verification['version']),category=None,material_ids=[m.material.material_id for m in inputs],finding_ids=[],
            question='共享事实更新未完成，请恢复相关识别或专项计算后再提交整单结论。',reason='本次依赖服务未完成，不将其视为客户材料不足。',kind='business')
        human(case.verification['issues'][-1],'共享依赖调用失败，结果未完成')
        case.verification['issues'][-1]['system_blocker']=True
        case.verification['issues'][-1]['dependency_categories']=sorted(categories)
        case.verification['issues'][-1]['affected_ids']=affected_ids
        case.verification['issues'][-1]['recovery_count']=max((i.get('recovery_count',0) for i in case.verification['issues'] if i.get('system_blocker')),default=0)
