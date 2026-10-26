"""Bounded issue-driven loop. A negative observation never cancels another image.

Raw observations are immutable; effective findings are derived by explicit,
evidence-scoped resolutions. Human treatment preferences never edit findings.
"""
from copy import deepcopy
from datetime import UTC, datetime
import hashlib
import json
import os
from uuid import uuid4

from app.contracts import RiskFinding, DecisionType, DetectionStatus
from app.rules import RuleEngine
from app.rules.selection import Policy, CATALOG
from app.rules.config import BusinessRulesConfig
from app.rules.completeness import assess_checks, build_applicable_checks
from app.decision import DecisionEngine
from .contracts import Plan, VerificationResult


def now():
    return datetime.now(UTC).isoformat()


def enabled():
    return os.getenv('PV_VERIFICATION_ENABLED', 'true').lower() not in {'0', 'false'}


def key(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:24]


def rules_for(category):
    return [r['id'] for r in CATALOG if category in r['risks']]


def area_for(case, mid):
    assignments = case.verification.get('area_assignments', {})
    if mid in assignments:
        return assignments[mid]
    areas = [a.area_id for a in case.installation_analysis.areas if mid in a.material_ids] if case.installation_analysis else []
    return areas[0] if len(areas) == 1 else None


def new_state(case):
    from .model import INSTRUCTIONS
    return dict(version=1, schema='verification-v2', issues=[], calls=[], human_events=[],
        harness_prompt=INSTRUCTIONS,
        area_assignments={}, first_round=dict(findings=[f.model_dump(mode='json') for f in case.findings],
        installation=case.installation_analysis.model_dump(mode='json') if case.installation_analysis else None,
        decision=case.decision.model_dump(mode='json') if case.decision else None),
        original_findings=[f.model_dump(mode='json') for f in case.findings],
        limitations='无首轮疑点的自信错误不会因本循环自动被发现；人工操作不作为训练真值。',
        created_at=now())


def add_issue(case, *, source, category, material_ids, finding_ids, question, reason,
              kind='visual', area=None, target=None, request=''):
    state = case.verification
    id_ = 'issue-' + key([source, category, sorted(finding_ids), area, target])
    if any(i['id'] == id_ for i in state['issues']):
        return
    state['issues'].append(dict(id=id_, source=source, risk_category=category,
        rule_ids=rules_for(category) if category else [], material_ids=list(dict.fromkeys(material_ids)),
        finding_ids=finding_ids, area_id=area, target_id=target, question=question, reason=reason,
        kind=kind, status='open', required=True, options=[], attempts=0, decisions=[],
        supplement_request=request, existing_conclusions=[f for f in state['original_findings'] if f['finding_id'] in finding_ids],
        created_at=now(), updated_at=now()))


def discover(case):
    state = case.verification
    policy = Policy(case.ruleset_snapshot) if case.ruleset_snapshot else None
    for f in case.findings:
        if policy and f.category not in policy.risks:
            continue
        if f.detection_status is DetectionStatus.UNCERTAIN or f.requires_manual_review or f.uncertainty_kind != 'none':
            kind = f.uncertainty_kind if f.uncertainty_kind != 'none' else 'visual'
            add_issue(case, source='finding', category=f.category.value, material_ids=[f.material_id],
                finding_ids=[f.finding_id], area=area_for(case, f.material_id), kind=kind,
                question=f'依据当前规则，{f.label}是否成立？', reason=f.evidence_text)
    # Only explicit same-object identifiers are used for automatic conflict creation.
    buckets = {}
    for f in case.findings:
        area = area_for(case, f.material_id)
        if f.object_id and area:
            buckets.setdefault((area, f.object_id, f.category.value), []).append(f)
    for (area, obj, category), fs in buckets.items():
        if {'detected', 'not_detected'} <= {f.detection_status.value for f in fs}:
            add_issue(case, source='conflict:' + obj, category=category, area=area,
                finding_ids=[f.finding_id for f in fs], material_ids=[f.material_id for f in fs],
                kind='conflict', question=f'{area} 的 {obj} 在相应时点是否存在该风险？',
                reason='同一对象存在相反观察，需核验位置、时间和可见范围；不采用多数投票。')
    installation = case.installation_analysis
    if installation:
        for area in installation.areas:
            if area.status != 'confirmed' and (not policy or policy.enabled('installation')):
                add_issue(case,source='installation:'+area.area_id,category='installation_type',
                    material_ids=area.material_ids,finding_ids=[],area=area.area_id,
                    question='该区域组件安装载体属于哪一种现有安装类型？',reason=area.pending_question or area.evidence_text)
        aisle = installation.aisle_assessment
        if aisle and aisle.status == 'uncertain' and (not policy or policy.enabled('aisle')):
            add_issue(case, source='aisle_group', category='aisle_obstruction',
                material_ids=aisle.supporting_material_ids, finding_ids=[], area=area_for(case, aisle.material_id),
                question='这些图片中的检修通道是否存在实际可见阻塞？', reason=aisle.evidence_text)
    for row in case.evidence_coverage.get('rows', []):
        for target in row.get('targets', []):
            if target['status'] not in {'insufficient', 'review'}:
                continue
            rule = next((r for r in CATALOG if r['id'] == row.get('rule_id')), None)
            category = (rule or {}).get('risks', [None])[0] if (rule or {}).get('risks') else None
            add_issue(case, source='coverage:' + str(row.get('rule_id')), category=category,
                material_ids=target.get('material_ids', []) or row.get('material_ids', []),
                finding_ids=target.get('finding_ids', []), area=row.get('area_id'), target=target['target_id'],
                question=target['label'] + '是否已有足够证据支持判断？', reason=target['reason'],
                kind='missing' if target['status'] == 'insufficient' else 'visual', request=target.get('customer_request') or '')


def candidates(case, issue, inputs):
    output = []
    for item in inputs:
        m = item.material
        if not m.media_type.startswith('image/') or m.duplicate_of_material_id or m.parse_status.value != 'success':
            continue
        area = area_for(case, m.material_id)
        if issue['area_id'] not in {None, 'project'} and area and area != issue['area_id']:
            continue
        output.append(dict(material_id=m.material_id, category=m.category.value,
            captured_at=str(m.captured_at) if m.captured_at else None,
            view=m.capture_view.value, area_id=area, quality=m.quality_status.value,
            observations=[f for f in case.verification['original_findings'] if f['material_id'] == m.material_id]))
    return output


def context_for(case, issue, inputs):
    return dict(issue=issue, candidates=candidates(case, issue, inputs),
        harness_prompt=case.verification.get('harness_prompt'),
        rules=case.ruleset_snapshot or {'catalog': [r for r in CATALOG if r['id'] in issue['rule_ids']]},
        recent_calls=[c for c in case.verification['calls'] if c['issue_id'] == issue['id']][-6:],
        limits={'remaining_image_calls': max(0, 2-issue['attempts'])})


def human(issue, reason):
    issue.update(status='human_review', stop_reason=reason, updated_at=now())


def options_for(issue, options):
    # Labels are not executable. Fixed semantic enums and server review validation own actions.
    import re
    from app.rules.engine import REJECT_ENVIRONMENT_CATEGORIES
    prohibited={c.value for c in REJECT_ENVIRONMENT_CATEGORIES}
    issue['options'] = [o.model_dump(mode='json') for o in options if o.id != 'other'
        and not re.search(r'\d+(?:\.\d+)?\s*(?:%|％|元|万元)',o.label)
        and not (o.fact=='absent' and o.classification=='成立')
        and not (issue.get('risk_category') in prohibited and o.classification=='成立' and o.treatment in {'acceptable','surcharge'})]
    if len({o['id'] for o in issue['options']}) != len(issue['options']):
        raise ValueError('重复选项编号')


def run_loop(case, provider, inputs, checkpoint=lambda _: None, issue_ids=None):
    state = case.verification
    max_calls = max(1, min(int(os.getenv('PV_VERIFICATION_MAX_CALLS', '12')), 40))
    for issue in state['issues']:
        if issue['status'] != 'open' or (issue_ids is not None and issue['id'] not in issue_ids):
            continue
        while issue['status'] == 'open':
            used = sum(c.get('version') == state['version'] for c in state['calls'])
            if issue['attempts'] >= 2 or used >= max_calls:
                human(issue, '达到本轮查证预算，问题未解决，请人工核验。')
                break
            ctx = context_for(case, issue, inputs)
            call = dict(id=uuid4().hex, issue_id=issue['id'], version=state['version'],
                        provider=provider.name, model=provider.model_name,
                        tool='plan', started_at=now(), status='running', input=deepcopy(ctx))
            state['calls'].append(call)
            checkpoint(case)
            try:
                plan = Plan.model_validate(provider.plan_verification(ctx))
                call.update(status='success', output=plan.model_dump(mode='json'), finished_at=now())
                options_for(issue, plan.options)
                issue['question'] = plan.question
                issue['question_generated'] = True
                # Business classification issues cannot be routed back to repeated photography.
                if issue['kind'] in {'classification', 'business'} or plan.action == 'human':
                    human(issue, plan.reason)
                    if issue['risk_category'] == 'water_adjacent_environment':
                        issue['question'] = '按本案现有核保规则，结合所列图片和已知事实，本案是否属于涉水风险？'
                    break
                if plan.action == 'request_more':
                    if plan.evidence_sufficient:
                        human(issue, '证据已足，转业务口径复核；不要求重复补拍。')
                    elif len(plan.missing_evidence.strip()) < 10:
                        human(issue, '模型未提供具体补材对象、视角和目的。')
                    else:
                        issue.update(status='awaiting_materials', supplement_request=plan.missing_evidence)
                    break
                allowed = {c['material_id'] for c in ctx['candidates']}
                ids = plan.material_ids
                if len(set(ids)) != len(ids) or not set(ids) <= allowed or not ids:
                    raise ValueError('图片引用非法')
                if (plan.action == 'single' and len(ids) != 1) or (plan.action == 'group' and len(ids) < 2):
                    raise ValueError('图片数量不符合工具约束')
                if issue['material_ids'] and not set(ids) & set(issue['material_ids']):
                    raise ValueError('未包含存疑原图，不能用无关阴性证据替代')
                signature = key([issue['id'], sorted(ids), [(m.material.material_id, hashlib.sha256(m.content).hexdigest()) for m in inputs if m.material.material_id in ids]])
                if any(c.get('signature') == signature and c['status'] in {'success','running'} for c in state['calls']):
                    human(issue, '相同证据已核验，缺少新的有效证据，停止重复调用。')
                    break
                if sum(c.get('version') == state['version'] for c in state['calls']) >= max_calls:
                    human(issue, '本轮调用预算用尽，未解决。')
                    break
                image_call = dict(id=uuid4().hex, issue_id=issue['id'], version=state['version'], tool=plan.action,
                    provider=provider.name,model=provider.installation_model_name if plan.action=='group' else provider.model_name,
                    signature=signature, material_ids=ids, question=plan.question, started_at=now(), status='running')
                state['calls'].append(image_call)
                issue['attempts'] += 1
                checkpoint(case)
                selected = [m for m in inputs if m.material.material_id in ids]
                result = VerificationResult.model_validate(provider.verify_images(selected, {**ctx, 'task': plan.model_dump(mode='json')}))
                if not set(result.evidence_ids) <= set(ids) or not result.evidence_ids:
                    raise ValueError('核验结果缺少有效引用')
                if any(f.material_id not in ids or f.category.value != issue['risk_category'] for f in result.findings):
                    raise ValueError('核验发现超出本次问题范围')
                image_call.update(status='success', output=result.model_dump(mode='json'), finished_at=now())
                issue['decisions'].append(result.model_dump(mode='json'))
                if result.outcome == 'classification':
                    issue['kind'] = 'classification'
                    human(issue, result.conclusion)
                    issue['question'] = f'按本案现有核保口径，{issue["question"]} 请确认风险分类及独立的承保意见。'
                elif result.outcome == 'request_more':
                    if result.evidence_sufficient or len(result.missing_evidence.strip()) < 10:
                        human(issue, '证据已足或补材要求不具体，需内部判断。')
                    else:
                        issue.update(status='awaiting_materials', supplement_request=result.missing_evidence)
                elif result.outcome == 'resolved':
                    areas = {area_for(case, mid) for mid in result.evidence_ids}
                    dates = {m.material.captured_at.date() for m in selected if m.material.captured_at}
                    if len(dates)>1:
                        human(issue, '证据来自不同拍摄日期，不能将不同时间状态相互抵消；请核保员确认适用时点。')
                        break
                    if not result.same_object or not result.evidence_sufficient or (len(ids)>1 and (None in areas or len(areas)>1)):
                        human(issue, '尚不能确认图片对应同一区域或证据不足，不能自动消除疑点。')
                        break
                    originals = [f for f in state['original_findings'] if f['finding_id'] in issue['finding_ids']]
                    positive = {f['finding_id'] for f in originals if f['detection_status'] == 'detected'}
                    absent = not any(f.detection_status.value == 'detected' for f in result.findings)
                    if absent and positive and (not positive <= set(result.corrected_finding_ids) or not result.correction_reason.strip()):
                        human(issue, '缺少针对原明确风险的反证说明，保留原发现。')
                        break
                    installation_ok = issue['risk_category']=='installation_type' and result.installation is not None and bool(result.installation.areas)
                    if installation_ok and any(a.status!='confirmed' or not set(a.material_ids)<=set(ids) or not a.material_ids for a in result.installation.areas):
                        raise ValueError('安装核验结果引用或状态无效')
                    if (not result.findings and not installation_ok) or any(f.detection_status.value == 'uncertain' or f.requires_manual_review or f.uncertainty_kind != 'none' for f in result.findings):
                        human(issue, '返回结果仍不确定，不能作为已解决事实。')
                        break
                    if issue['target_id'] and not any(o.target == issue['target_id'] and o.status == 'visible' for f in result.findings for o in f.evidence_coverage):
                        human(issue, '未取得所需部位的清楚可见证据。')
                        break
                    issue.update(status='resolved', resolution=result.model_dump(mode='json'))
            except Exception as exc:
                from app.pipeline.recovery import JobStopped, JobDeadline
                if isinstance(exc, (JobStopped, JobDeadline)):
                    checkpoint(case)
                    raise
                for c in state['calls']:
                    if c['issue_id'] == issue['id'] and c['status'] == 'running':
                        c.update(status='failed', error=type(exc).__name__, finished_at=now())
                human(issue, '模型调用、证据或结构校验未完成；保留未解决状态，不据此要求客户补拍。')
            finally:
                issue['updated_at'] = now()
                checkpoint(case)
    return case


def effective_findings(case):
    state = case.verification
    findings = {f['finding_id']: RiskFinding.model_validate(f) for f in state['original_findings']}
    for issue in state['issues']:
        if issue['status'] == 'resolved' and issue.get('resolution'):
            for fid in issue['finding_ids']:
                findings.pop(fid, None)
            for index, raw in enumerate(issue['resolution']['findings']):
                f = RiskFinding.model_validate(raw)
                f.finding_id = f'{issue["id"]}:verified:{index}'
                findings[f.finding_id] = f
        review = issue.get('human_review')
        if not review or issue['status'] != 'confirmed' or not issue['risk_category']:
            continue
        target_ids=list(issue['finding_ids'])
        if not target_ids and issue['risk_category']!='installation_type':
            f=RiskFinding(finding_id=issue['id']+':human',material_id=review['evidence_ids'][0],category=issue['risk_category'],
                label=issue['question'],detection_status='uncertain',severity='unknown',confidence=1,
                evidence_text='人工证据核验：'+review['opinion'],provider='human-review',model='not-model-training-truth',requires_manual_review=False)
            findings[f.finding_id]=f
            target_ids=[f.finding_id]
        for fid in target_ids:
            f = findings.get(fid)
            if f is None:
                continue
            # Preserve source facts separately; this projection is solely the adjudicated risk classification.
            f = f.model_copy(deep=True)
            f.detection_status = DetectionStatus.DETECTED if review['classification'] == '成立' else DetectionStatus.NOT_DETECTED
            f.requires_manual_review = False
            f.uncertainty_kind = 'none'
            f.evidence_text += '；人工分类确认：' + review['opinion']
            findings[fid] = f
        if issue.get('target_id') and review.get('coverage_confirmed'):
            from app.contracts.findings import EvidenceObservation
            for f in findings.values():
                if f.material_id in review['evidence_ids'] and f.category.value==issue['risk_category']:
                    f.evidence_coverage=[o for o in f.evidence_coverage if o.target!=issue['target_id']]+[
                        EvidenceObservation(target=issue['target_id'],status='visible',evidence_text='人工覆盖确认：'+review['opinion'])]
    return list(findings.values())


def recalculate(case):
    engine = RuleEngine()
    policy = Policy(case.ruleset_snapshot) if case.ruleset_snapshot else None
    if policy:
        engine.config = BusinessRulesConfig.model_validate(case.ruleset_snapshot['business_config'])
    engine.rule_policy = policy
    if case.verification:
        case.findings = effective_findings(case)
        for issue in case.verification['issues']:
            installation = (issue.get('resolution') or {}).get('installation')
            if issue['status']=='resolved' and installation and case.installation_analysis:
                from app.contracts import InstallationAnalysis
                verified = InstallationAnalysis.model_validate(installation)
                other = [a for a in case.installation_analysis.areas if a.area_id != issue['area_id']]
                case.installation_analysis.areas = other + [a.model_copy(update={'area_id':issue['area_id']}) for a in verified.areas]
            if issue['risk_category']=='aisle_obstruction' and case.installation_analysis and issue['status'] in {'resolved','confirmed'}:
                from app.contracts.stages import AisleAssessment
                fs=[f for f in case.findings if f.category.value=='aisle_obstruction' and (f.finding_id in issue['finding_ids'] or f.finding_id.startswith(issue['id']+':'))]
                if fs:
                    f=next((f for f in fs if f.detection_status.value=='detected'),fs[0])
                    refs=(issue.get('human_review') or {}).get('evidence_ids') or (issue.get('resolution') or {}).get('evidence_ids') or [f.material_id]
                    case.installation_analysis.aisle_assessment=AisleAssessment(status=f.detection_status.value,material_id=f.material_id,
                        supporting_material_ids=refs,cross_image_reasoning=f.evidence_text,confidence=f.confidence,severity=f.severity,
                        evidence_text=f.evidence_text,bbox=f.bbox if f.detection_status.value=='detected' else None)
    materials = [m for m in case.materials if not m.duplicate_of_material_id and m.material_id not in case.component_source_material_ids]
    case.material_reviews = [engine.evaluate_material(m, findings=[f for f in case.findings if f.material_id == m.material_id],
        ocr_fields=[f for f in case.ocr_fields if f.material_id == m.material_id], project=case.project) for m in materials]
    # Human confirmation establishes the existing rule predicate; it is not a new policy.
    from app.rules.engine import REJECT_ENVIRONMENT_CATEGORIES
    from app.contracts import MaterialReviewAction
    confirmed = {fid for i in case.verification.get('issues',[]) if i['status']=='confirmed'
        and (i.get('human_review') or {}).get('classification')=='成立' for fid in (i['finding_ids'] or [i['id']+':human'])}
    for review in case.material_reviews:
        fs = [f for f in case.findings if f.finding_id in confirmed and f.material_id==review.material_id
              and f.category in REJECT_ENVIRONMENT_CATEGORIES and (not policy or f.category in policy.risks)]
        if fs:
            review.action=MaterialReviewAction.RECOMMEND_REJECT
            review.triggered_rule_ids=list(dict.fromkeys([*review.triggered_rule_ids,engine.ENVIRONMENT_REJECT_RULE_ID]))
            review.reasons.append('核保员确认本案符合现有禁投环境分类；沿用该规则的拒保建议，人工可接受意见不豁免规则。')
    if case.installation_analysis:
        checks = build_applicable_checks(case.installation_analysis, profile=engine.config.package_requirement_profile, materials=materials)
        case.applicable_checks = policy.filter_checks(checks) if policy else checks
        case.check_results = assess_checks(case.applicable_checks, materials, case.findings, case.ocr_fields,
            case.installation_analysis, failed_material_ids={mid for t in case.processing_trace for mid in t.failed_material_ids},
            allowed_risks=policy.risks if policy else None)
    case.package_assessment = engine.assess_package(materials, project_type=case.project.project_type, applicable_checks=case.applicable_checks)
    from app.pipeline.staged import _stage_from_material
    case.stage_reviews = [s for s in case.stage_reviews if s.stage not in {'single_image', 'completeness'}]
    if case.installation_analysis and (not policy or policy.enabled('installation')):
        case.stage_reviews = [s for s in case.stage_reviews if s.stage not in {'installation_type','installation_scope'}]
        case.stage_reviews.append(engine.evaluate_installation_type(case.installation_analysis))
    case.stage_reviews.extend(_stage_from_material(r, case.findings, case.ocr_fields) for r in case.material_reviews)
    if policy:
        case.rule_execution = policy.executions(case)
    # A prior early stop is historical, not a permanent current-case decision.
    case.pre_catastrophe_outcome = 'continue'
    case.decision = DecisionEngine().decide(case)
    case.verification['rule_missing_requirements'] = list(case.decision.missing_requirements)
    reconcile_evidence_issues(case)
    apply_pending(case)
    if case.strategy_assessment:
        from app.strategies.engine import evaluate
        case.strategy_assessment = evaluate(case, case.strategy_assessment.get('scheme_snapshot'))
    return case


def reconcile_evidence_issues(case):
    """Close only objectively satisfied evidence requirements, never business disagreements."""
    for issue in case.verification['issues']:
        if issue['status'] in {'resolved','confirmed'}:
            continue
        satisfied = False
        if issue['source'].startswith('requirement:'):
            satisfied = issue['supplement_request'] not in case.verification.get('rule_missing_requirements', [])
        elif issue['source'].startswith('coverage:'):
            rule_id = issue['source'].split(':',1)[1]
            satisfied = any(row['rule_id']==rule_id and row['area_id']==issue['area_id']
                and any(t['target_id']==issue['target_id'] and t['status']=='satisfied' for t in row.get('targets',[]))
                for row in case.evidence_coverage.get('rows',[]))
        if satisfied:
            issue.update(status='resolved',stop_reason='更新后的证据及现有规则已满足本项材料要求。',updated_at=now())


def apply_pending(case):
    if not case.verification or not case.decision:
        return
    pending = [i for i in case.verification['issues'] if i['status'] not in {'resolved', 'confirmed'}]
    requests = [i['supplement_request'] for i in pending if i['status'] == 'awaiting_materials']
    treatments = [i['human_review']['treatment'] for i in case.verification['issues'] if i.get('human_review') and i['status'] == 'confirmed']
    if case.decision.decision != DecisionType.RECOMMEND_REJECT:
        if requests or 'request_more' in treatments:
            case.decision.decision = DecisionType.REQUEST_MORE
        elif 'reject' in treatments:
            case.decision.decision = DecisionType.RECOMMEND_REJECT
            case.decision.reasons.append('核保人员逐项提出拒保处理意见；风险事实与人工意见分别留档。')
        elif pending:
            case.decision.decision = DecisionType.MANUAL_REVIEW
    if 'surcharge' in treatments:
        case.decision.pricing_recommendation = 'surcharge'
        case.decision.warnings.append('人工提出加费建议；未配置定价规则，不计算金额或比例。')
    case.decision.missing_requirements = list(dict.fromkeys([*case.decision.missing_requirements, *requests]))
    case.decision.reasons.extend(i['question'] for i in pending if i['question'] not in case.decision.reasons)


def report_items(case):
    """Capture non-visual blockers too, without feeding them into the image loop."""
    if not case.verification or not case.decision:
        return
    for request in case.verification.get('rule_missing_requirements', case.decision.missing_requirements):
        if any(i['supplement_request'] == request for i in case.verification['issues']):
            continue
        source = 'requirement:' + key(request)
        if any(i['source'] == source for i in case.verification['issues']):
            continue
        add_issue(case, source=source, category=None, material_ids=[], finding_ids=[],
            question=request, reason='现有核保规则要求的材料或信息不足。', kind='missing', request=request)
        case.verification['issues'][-1]['status'] = 'awaiting_materials'
    if case.decision.decision in {DecisionType.MANUAL_REVIEW, DecisionType.CONDITIONAL_ACCEPT}:
        for reason in case.decision.reasons:
            if any(reason == i['question'] or reason == i['reason'] for i in case.verification['issues']):
                continue
            # Single-image issues already have their own precise evidence cards.
            if any(f.evidence_text in reason for f in case.findings):
                continue
            source = 'business:' + key(reason)
            if any(i['source'] == source for i in case.verification['issues']):
                continue
            add_issue(case, source=source, category=None, material_ids=[m.material_id for m in case.materials], finding_ids=[],
                question='请按现有核保口径确认：' + reason, reason=reason, kind='business')
            human(case.verification['issues'][-1], '现有规则要求人工业务确认。')


def initial(case, provider, inputs):
    from .store import Store
    store = Store()
    if store.repo.get_case(case.case_id):
        from app.storage.cases import CaseRepositoryConflict
        raise CaseRepositoryConflict(case.case_id)
    fingerprint = key([[m.material.material_id, hashlib.sha256(m.content).hexdigest()] for m in inputs] + [case.ruleset_snapshot])
    previous = store.load_checkpoint(case.case_id, fingerprint)
    case.verification = deepcopy(previous.verification) if previous else new_state(case)
    store.archive_sources(case.case_id, inputs)
    recalculate(case)
    if not case.verification['first_round']['decision']:
        case.verification['first_round']['decision'] = case.decision.model_dump(mode='json')
    discover(case)
    run_loop(case, provider, inputs, checkpoint=lambda c: store.checkpoint(c, fingerprint))
    recalculate(case)
    return case


def prepare_questions(case, provider, inputs):
    """Generate case-specific review wording within the same overall budget."""
    state=case.verification
    for issue in state['issues']:
        if issue['status']!='human_review' or issue.get('question_generated'):
            continue
        if sum(c.get('version')==state['version'] for c in state['calls'])>=int(os.getenv('PV_VERIFICATION_MAX_CALLS','12')):
            issue['question_fallback']='调用预算已用尽，使用原问题与其他意见入口。'
            continue
        context=context_for(case,issue,inputs)
        context['instruction']='仅生成本案人工复核问题及选项，不再调用图片识别。'
        call=dict(id=uuid4().hex,issue_id=issue['id'],version=state['version'],tool='review_question',
                  started_at=now(),status='running',input=deepcopy(context),provider=provider.name,model=provider.model_name)
        state['calls'].append(call)
        try:
            plan=Plan.model_validate(provider.plan_verification(context))
            issue['question']=plan.question
            options_for(issue,plan.options)
            issue['question_generated']=True
            call.update(status='success',output=plan.model_dump(mode='json'))
        except Exception as exc:
            call.update(status='failed',error=type(exc).__name__)
            issue['question_fallback']='模型问题生成未完成，保留原问题，核保员可填写其他意见。'
        call['finished_at']=now()
