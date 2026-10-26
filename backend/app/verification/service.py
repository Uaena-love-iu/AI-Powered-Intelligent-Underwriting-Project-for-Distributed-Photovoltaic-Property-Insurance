"""Human review and incremental evidence revisions; no invented underwriting rules."""
import json
import hashlib
from copy import deepcopy
from uuid import uuid4
from app.contracts import UnderwritingCase, RiskFinding, DecisionType
from .store import Store, Conflict
from .engine import (now, key, discover, run_loop, recalculate, report_items, human, apply_pending)


def actor(request, name):
    from app.security import auth_enabled
    if auth_enabled():
        user = getattr(request.state, 'auth_user', None)
        if user is None or user.role not in {'underwriter', 'admin'}:
            raise Conflict('需要核保人员或管理员权限。')
        return user.username, 'authenticated'
    if not name.strip():
        raise Conflict('请填写操作人')
    return name.strip(), 'local_demo'


def review_item(case_id, issue_id, body, name, identity_mode, store=None):
    store = store or Store()
    with store.db() as con:
        con.execute('BEGIN IMMEDIATE')
        case = store.current(con, case_id, body.version)
        issue = next((i for i in case.verification['issues'] if i['id'] == issue_id), None)
        if issue is None or issue['status'] not in {'human_review', 'confirmed'}:
            raise Conflict('该项不能直接人工关闭；待补材事项须提交证据后重新核验。')
        if issue.get('system_blocker'):
            raise Conflict('该项需要恢复依赖服务或计算，不能用人工点击代替执行。')
        if issue.get('target_id') and not body.coverage_confirmed:
            raise Conflict('请先确认所需部位在引用证据中清楚可见；无法确认时应补材或保持待处理。')
        if not set(body.evidence_ids) <= {m.material_id for m in case.materials}:
            raise Conflict('证据引用不存在')
        if issue['material_ids'] and not set(body.evidence_ids) & set(issue['material_ids']):
            raise Conflict('请选择与该项问题关联的证据')
        from .engine import area_for
        if issue.get('target_id') and issue['area_id'] not in {None,'project'} and any(area_for(case,mid)!=issue['area_id'] for mid in body.evidence_ids):
            raise Conflict('部位覆盖的确认只能引用本区域证据')
        if len(body.opinion.strip()) < 2:
            raise Conflict('请填写明确的核验依据')
        if body.option_id != 'other':
            option = next((o for o in issue['options'] if o['id'] == body.option_id), None)
            if option is None or any(getattr(body,k) != option[k] for k in ('fact','classification','treatment')):
                raise Conflict('选项与结构化确认结果不一致，请刷新或选择其他。')
        if body.fact == 'absent' and body.classification == '成立':
            raise Conflict('事实不存在与风险分类成立矛盾，请检查填写。')
        event = {**body.model_dump(mode='json'), 'actor':name, 'identity_mode':identity_mode,
                 'at':now(), 'issue_id':issue_id, 'training_truth':False}
        issue['human_review'] = event
        case.verification['human_events'].append(event)
        if body.classification == '待确认' or body.fact == 'unknown' or body.treatment == 'undecided':
            human(issue, '人工尚未确认事实、分类或处理意见，保持待处理。')
        elif body.treatment == 'request_more':
            issue.update(status='awaiting_materials', supplement_request=body.opinion)
        else:
            issue['status'] = 'confirmed'
        case.verification.pop('final', None)
        case.verification['version'] += 1
        recalculate(case)
        store.write(con, case, 'item_review', name, event)
    return case


def finalize(case_id, body, name, identity_mode, store=None):
    store = store or Store()
    with store.db() as con:
        con.execute('BEGIN IMMEDIATE')
        row=con.execute('SELECT case_json FROM cases WHERE case_id=?',(case_id,)).fetchone()
        prior=UnderwritingCase.model_validate_json(row[0]) if row else None
        previous=(prior.verification.get('final') if prior else None)
        if previous and previous['actor']==name and all(previous.get(k)==v for k,v in body.model_dump(mode='json').items() if k!='actor'):
            # A retried identical submission is harmless, but an older different decision is stale.
            return prior
        case = store.current(con, case_id, body.version)
        pending = [i for i in case.verification['issues'] if i['required'] and i['status'] not in {'resolved','confirmed'}]
        # No configured policy authorizes an override of mandatory reject or missing evidence.
        if body.decision in {'accept','conditional_accept'}:
            if pending or case.decision.missing_requirements or case.decision.decision == DecisionType.REQUEST_MORE:
                raise Conflict('仍有必需复核项或待补材料，不能提交通过或有条件通过。')
            if case.decision.decision == DecisionType.RECOMMEND_REJECT:
                raise Conflict('现有规则建议拒保，尚无规则例外授权配置；不能直接改为通过。')
            action = (case.strategy_assessment or {}).get('effective_suggestion')
            if action in {'request_more','recommend_reject'}:
                raise Conflict('承保策略存在阻断要求，不能直接通过。')
        if body.decision != case.decision.decision.value and not body.override_reason.strip():
            raise Conflict('与规则建议不同的决定必须记录原因；原因不替代权限。')
        final = {**body.model_dump(mode='json'), 'actor':name, 'identity_mode':identity_mode, 'at':now(),
                 'rule_advice':case.decision.model_dump(mode='json')}
        existing = case.verification.get('final')
        if existing and all(existing.get(k) == final[k] for k in body.model_dump() if k != 'actor') and existing['actor']==name:
            return case
        case.verification['final'] = final
        case.verification['version'] += 1
        store.write(con, case, 'final_decision', name, final)
        con.execute("UPDATE cases SET review_status='completed', final_decision=?, reviewer_name=?, review_comment=?, reviewed_at=? WHERE case_id=?",
            (body.decision,name,body.opinion,final['at'],case_id))
        con.execute('INSERT INTO review_events(case_id,reviewer_name,final_decision,pricing_recommendation,comment,reviewed_at) VALUES (?,?,?,?,?,?)',
            (case_id,name,body.decision,case.decision.pricing_recommendation,body.opinion,final['at']))
    return case


def supplement(case, inputs, additions, issue_ids, area_map, remove_ids, provider, ocr_provider, checkpoint):
    """Analyze new bytes only. Existing first-pass calls are never replayed here."""
    state = case.verification
    active_ids = {m.material_id for m in case.materials}
    marker = key([[m.material.material_id for m in additions],issue_ids,remove_ids])
    resuming = state.get('active_supplement') == marker
    if not resuming and active_ids & {m.material.material_id for m in additions}:
        raise Conflict('新材料须使用新的唯一编号')
    if not resuming and not set(remove_ids) <= active_ids:
        raise Conflict('移除的材料不属于当前版本')
    requested = [i for i in state['issues'] if i['id'] in issue_ids]
    if len(requested) != len(set(issue_ids)) or not requested:
        raise Conflict('请选择有效的存疑点')
    if not resuming:
        previous_hashes = {hashlib.sha256(m.content).hexdigest():m.material.material_id for m in inputs}
        for addition in additions:
            digest = hashlib.sha256(addition.content).hexdigest()
            if digest in previous_hashes:
                raise Conflict('补充材料与已有图片内容重复，不能作为新的视角或证据。')
            previous_hashes[digest] = addition.material.material_id
        state['version'] += 1
        state['active_supplement'] = marker
    state.pop('final', None)
    changed_categories = {m.material.category.value for m in additions}
    changed_categories |= {m.category.value for m in case.materials if m.material_id in remove_ids}
    impacted = set(issue_ids)
    from app.rules.selection import CATALOG
    related_risks = {r for rule in CATALOG if set(rule['materials']) & changed_categories for r in rule['risks']}
    affected_areas = set(area_map.values())
    from .engine import area_for
    affected_areas.update(area_for(case,mid) for mid in remove_ids)
    for issue in state['issues']:
        same_area = None in affected_areas or issue['area_id'] in {None,'project'} or issue['area_id'] in affected_areas
        if set(issue['material_ids']) & set(remove_ids) or (same_area and issue['risk_category'] in related_risks):
            impacted.add(issue['id'])
    for issue in state['issues']:
        if issue['id'] in impacted and not resuming:
            if issue.get('human_review'):
                state['human_events'].append({'event':'confirmation_invalidated','issue_id':issue['id'],'at':now(),'review':issue.pop('human_review')})
            if issue.get('resolution'):
                issue.setdefault('previous_resolutions', []).append(issue.pop('resolution'))
            issue.update(status='open',attempts=0,options=[])
            issue['material_ids'] = [mid for mid in issue['material_ids'] if mid not in remove_ids]
    case.materials = [m for m in case.materials if m.material_id not in remove_ids]
    inputs = [m for m in inputs if m.material.material_id not in remove_ids]
    state['retired_findings'] = state.get('retired_findings', []) + [f for f in state['original_findings'] if f['material_id'] in remove_ids]
    state['original_findings'] = [f for f in state['original_findings'] if f['material_id'] not in remove_ids]
    case.ocr_fields = [f for f in case.ocr_fields if f.material_id not in remove_ids]
    from app.pipeline import UnderwritingPipeline
    from app.rules.selection import Policy
    policy = Policy(case.ruleset_snapshot) if case.ruleset_snapshot else None
    provider.rule_policy = policy
    ocr_provider.rule_policy = policy
    extractor=UnderwritingPipeline(vision_provider=provider,ocr_provider=ocr_provider)
    extractor.policy=policy
    new_ids = []
    for addition in additions:
        mid = addition.material.material_id
        new_ids.append(mid)
        if any(c['tool']=='supplement_single' and c.get('material_ids')==[mid] and c['status']=='success' for c in state['calls']):
            continue
        area = area_map.get(mid)
        known_areas = {a.area_id for a in case.installation_analysis.areas} if case.installation_analysis else set()
        if area and area not in known_areas:
            raise Conflict('区域编号不存在，请先确认区域对应关系')
        state['area_assignments'][mid] = area
        if mid not in {m.material_id for m in case.materials}:
            case.materials.append(addition.material)
        if mid not in {m.material.material_id for m in inputs}:
            inputs.append(addition)
        if case.installation_analysis and area:
            for a in case.installation_analysis.areas:
                if a.area_id == area:
                    if mid not in a.material_ids:
                        a.material_ids.append(mid)
        call = dict(id=uuid4().hex,issue_id=issue_ids[0],version=state['version'],tool='supplement_single',material_ids=[mid],started_at=now(),status='running')
        state['calls'].append(call)
        checkpoint(case)
        try:
            state['original_findings']=[f for f in state['original_findings'] if f['material_id']!=mid]
            case.ocr_fields=[f for f in case.ocr_fields if f.material_id!=mid]
            if addition.material.media_type.startswith('image/'):
                result = provider.analyze_with_watermark(addition)
                fs = [f for f in result.findings if not policy or f.category in policy.risks]
                state['original_findings'].extend(f.model_dump(mode='json') for f in fs)
                call['output'] = result.model_dump(mode='json')
                fields,trace=extractor._run_ocr([addition])
                case.ocr_fields.extend(fields)
                case.processing_trace.append(trace)
                updated=extractor._apply_ocr_metadata([addition],fields)[0]
                case.materials=[updated.material if m.material_id==mid else m for m in case.materials]
                inputs=[updated if m.material.material_id==mid else m for m in inputs]
                call['ocr_output']=[f.model_dump(mode='json') for f in fields]
            else:
                fields = ocr_provider.extract(addition)
                case.ocr_fields.extend(fields)
                call['output'] = [f.model_dump(mode='json') for f in fields]
            call.update(status='success',finished_at=now())
        except Exception as exc:
            call.update(status='failed',error=type(exc).__name__,finished_at=now())
            for issue in requested:
                human(issue,'新材料识别调用失败，保留原件与未解决问题；可恢复本次任务。')
            checkpoint(case)
            raise Conflict('新材料识别未完成，原件与检查点已保存；请恢复本次任务。') from exc
        checkpoint(case)
    for issue in requested:
        issue['material_ids'] = list(dict.fromkeys([*issue['material_ids'], *new_ids]))
    case.findings = [RiskFinding.model_validate(f) for f in state['original_findings']]
    recalculate(case)
    before = {i['id'] for i in state['issues']}
    discover(case)
    impacted |= {i['id'] for i in state['issues']} - before
    run_loop(case, provider, inputs, checkpoint, impacted)
    recalculate(case)
    # Structured legacy material requirements are closed only when the rules stop requiring them.
    requirements = set(state.get('rule_missing_requirements', []))
    for issue in state['issues']:
        if issue['source'].startswith('requirement:') and issue['supplement_request'] not in requirements:
            issue.update(status='resolved',stop_reason='补材后现有规则不再要求该材料。')
    from .dependencies import refresh
    refresh(case,inputs,changed_categories,provider,ocr_provider,new_ids)
    recalculate(case)
    discover(case)
    # New shared-fact uncertainty is pending, not silently considered cleared.
    for issue in state['issues']:
        if issue['status']=='open':
            human(issue,'共享事实更新后出现新的待核问题，请结合证据确认。')
    report_items(case)
    from .engine import prepare_questions
    prepare_questions(case,provider,inputs)
    apply_pending(case)
    state.pop('active_supplement',None)
    return case


def resume(case, inputs, issue_ids, provider, ocr_provider, checkpoint):
    """Explicit, capped recovery of service failures; no re-photograph request."""
    state=case.verification
    marker=key(['resume',issue_ids])
    continuing=state.get('active_resume')==marker
    selected=[i for i in state['issues'] if i['id'] in issue_ids]
    if not selected or len(selected)!=len(set(issue_ids)):
        raise Conflict('请选择有效的未完成事项')
    if not continuing:
        for issue in selected:
            failed=any(c['issue_id']==issue['id'] and c['status'] in {'failed','running'} for c in state['calls'])
            if issue['status']!='human_review' or (not failed and not issue.get('system_blocker')):
                raise Conflict('仅服务失败或依赖未完成事项可恢复；业务分类争议应由人员确认。')
            if issue.get('recovery_count',0)>=3:
                raise Conflict('该项恢复次数已达上限，请排查服务后人工处理。')
        state['version']+=1
        state['active_resume']=marker
        state.pop('final',None)
        for issue in selected:
            issue['recovery_count']=issue.get('recovery_count',0)+1
            issue['attempts']=0
            issue.pop('question_generated',None)
            issue['status']='resolved' if issue.get('system_blocker') else 'open'
        for call in state['calls']:
            if call['status']=='running':call.update(status='failed',error='Interrupted',finished_at=now())
        checkpoint(case)
    if any(i.get('system_blocker') for i in selected):
        from .dependencies import refresh
        categories={c for i in selected for c in i.get('dependency_categories',[])}
        affected=list({mid for i in selected for mid in (i.get('affected_ids') or [])})
        refresh(case,inputs,categories,provider,ocr_provider,affected)
    run_loop(case,provider,inputs,checkpoint,set(issue_ids))
    recalculate(case)
    report_items(case)
    from .engine import prepare_questions
    prepare_questions(case,provider,inputs)
    apply_pending(case)
    state.pop('active_resume',None)
    return case
