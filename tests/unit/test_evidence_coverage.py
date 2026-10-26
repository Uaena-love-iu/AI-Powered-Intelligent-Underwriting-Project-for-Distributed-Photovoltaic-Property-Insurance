from copy import deepcopy
import pytest
from app.contracts import UnderwritingCase, Material, RiskFinding, InstallationAnalysis
from app.contracts.findings import EvidenceObservation
from app.contracts.outputs import ProcessingTrace
from app.rules.evidence_coverage import assess
from app.rules.selection import Selection, resolve
from app.decision import DecisionEngine
from app.storage.revisions import compare_cases
from unit.test_rule_selection import project


def material(id_='node', **kwargs):
    return Material(material_id=id_,file_name=id_+'.png',media_type='image/png',category='roof_connection',quality_status='usable',quality_issues=[],parse_status='success',**kwargs)


def finding(id_='node', statuses=None, **kwargs):
    statuses = statuses or {'support_context':'visible','connection_node':'visible','anchorage':'visible'}
    return RiskFinding(finding_id=id_+':connection',material_id=id_,category='roof_connection_abnormal',label='连接外观',detection_status='not_detected',severity='low',confidence=.99,evidence_text='连接外观清楚',provider='test',model='test',requires_manual_review=False,evidence_coverage=[EvidenceObservation(target=k,status=v,evidence_text='已清楚显示连接关系' if v=='visible' else '锚固位置被挡住，无法看清') for k,v in statuses.items()],**kwargs)


def case(materials=None,findings=None,ids=None):
    p=project(project_type='rooftop',installation_type='flat_roof')
    snapshot=resolve(Selection(selected_rule_ids=ids or ['connection']),p)
    ms=[material()] if materials is None else materials
    fs=[finding()] if findings is None else findings
    c=UnderwritingCase(schema_version='0.1.0',case_id='COVERAGE-TEST',project=p,materials=ms,ocr_fields=[],findings=fs,material_reviews=[],processing_trace=[],ruleset_snapshot=snapshot,
        installation_analysis=InstallationAnalysis(areas=[dict(area_id='roof-A',scene_type='building_roof',status='confirmed',material_ids=[m.material_id for m in ms],usage='住宅',evidence_text='可辨认屋面阵列')],provider='test'),
        rule_execution=[dict(id=r['id'],name=r['name'],status='completed' if r['status']=='active' else r['status'],reason='模拟已执行') for r in snapshot['rules']])
    return c


def active(result):return next(r for r in result['rows'] if r['rule_id']=='connection')


def test_coverage_is_per_part_not_photo_count():
    fs=[finding('node',{'connection_node':'visible','support_context':'visible','anchorage':'occluded'}),finding('another',{'connection_node':'visible','support_context':'visible','anchorage':'not_visible'})]
    c=case([material(),material('another'),material('copy',duplicate_of_material_id='node')],fs)
    r=assess(c);row=active(r)
    assert row['status']=='insufficient' and len(row['customer_requests'])==1
    assert '锚固' in row['customer_requests'][0] and 'roof-A' in row['customer_requests'][0]
    assert 'copy' not in row['material_ids'] and r['duplicates']==[{'material_id':'copy','duplicate_of':'node'}]
    assert '不等于故意隐瞒' in r['notice']
    decision=DecisionEngine().decide(c)
    assert decision.decision.value=='request_more' and decision.missing_requirements


def test_different_view_can_complete_missing_part_and_round_diff():
    c=case(findings=[finding(statuses={'support_context':'visible','connection_node':'visible','anchorage':'occluded'})])
    DecisionEngine().decide(c);before=c.model_dump(mode='json')
    c.materials.append(material('anchor'));c.findings.append(finding('anchor',{'anchorage':'visible'}))
    c.installation_analysis.areas[0].material_ids.append('anchor')
    # New execution is recomputed after a rerun, not copied from the old round.
    next(r for r in c.rule_execution if r['id']=='connection').update(status='completed',reason='重新核验完成')
    DecisionEngine().decide(c)
    assert active(c.evidence_coverage)['status']=='satisfied'
    assert not c.evidence_coverage['customer_requests']
    delta=compare_cases(before,c.model_dump(mode='json'))['evidence_coverage_changes']
    assert any(d['before']['status']=='insufficient' and d['after']['status']=='satisfied' for d in delta)
    assert active(before['evidence_coverage'])['status']=='insufficient'


def test_missing_structured_result_requires_internal_review_not_customer_retake():
    f=finding();f.evidence_coverage=[]
    c=case(findings=[f]);r=assess(c)
    assert active(r)['status']=='review' and not r['customer_requests']
    assert '未返回' in r['review_questions'][0]
    assert DecisionEngine().decide(c).decision.value=='manual_review'


def test_invalid_target_does_not_satisfy_real_part():
    f=finding(statuses={'arbitrary':'visible'});r=assess(case(findings=[f]))
    assert all(t['status']=='review' for t in active(r)['targets'])


def test_unselected_rule_has_no_requirements_and_legacy_snapshot_is_not_rewritten():
    c=case(ids=['component']);r=assess(c)
    assert next(row for row in r['rows'] if row['rule_id']=='connection')['status']=='not_selected'
    assert not any('锚固' in q for q in r['customer_requests'])
    c.ruleset_snapshot.pop('evidence_coverage_version');assert assess(c)=={}


def test_missing_category_gets_specific_requests():
    r=assess(case(materials=[],findings=[]))
    assert active(r)['status']=='insufficient' and len(active(r)['customer_requests'])==3


def test_two_roofs_cannot_borrow_one_roofs_evidence():
    c=case();c.installation_analysis.areas.append(c.installation_analysis.areas[0].model_copy(update={'area_id':'roof-B','material_ids':[]}))
    rows=[r for r in assess(c)['rows'] if r['rule_id']=='connection']
    assert [r['status'] for r in rows]==['satisfied','insufficient']


def test_unknown_area_requires_internal_assignment_first():
    c=case();c.installation_analysis.areas[0].status='unknown'
    r=assess(c);assert active(r)['status']=='review' and not r['customer_requests']


def test_contradictory_same_photo_observations_do_not_pass_or_request_retake():
    f=finding();f.evidence_coverage.append(EvidenceObservation(target='anchorage',status='not_visible',evidence_text='同一部位未拍到'))
    r=assess(case(findings=[f]));assert active(r)['status']=='review' and not r['customer_requests']


def test_low_confidence_does_not_become_verified_coverage():
    f=finding();f.confidence=.4
    r=assess(case(findings=[f]));assert active(r)['status']=='review'


def test_provider_failure_does_not_become_customer_gap():
    from datetime import UTC,datetime
    c=case(findings=[finding(statuses={'support_context':'not_visible','connection_node':'not_visible','anchorage':'not_visible'})])
    now=datetime.now(UTC)
    c.processing_trace=[ProcessingTrace(step='vision',provider='test',started_at=now,finished_at=now,latency_ms=0,status='failed',failed_material_ids=['node'])]
    r=assess(c);assert active(r)['status']=='review' and not r['customer_requests']


def test_visible_coverage_does_not_erase_risk_or_create_accept():
    from app.contracts import MaterialReview
    c=case();c.findings[0].detection_status='detected'
    c.material_reviews=[MaterialReview(material_id='node',action='recommend_reject',triggered_rule_ids=['TEST-RISK'],finding_ids=['node:connection'],ocr_field_ids=[],reasons=['已确认可见风险'],missing_requirements=[],requires_manual_review=False)]
    assert DecisionEngine().decide(c).decision.value=='recommend_reject'
    assert active(c.evidence_coverage)['status']=='satisfied'


def test_snapshot_freezes_target_prompts_and_disabled_rules_have_no_targets():
    c=case();prompt=c.ruleset_snapshot['vision_prompts']['roof_connection']
    assert 'connection_node' in prompt and 'anchorage' in prompt
    assert 'box_fuse' not in prompt
    assert c.ruleset_snapshot['evidence_coverage_version']=='1'


def test_provider_parses_coverage_and_keeps_legacy_outputs_readable():
    from app.providers.vision.compatible import VisionResponsePayload
    base=dict(category='roof_connection_abnormal',label='连接',detection_status='not_detected',severity='low',confidence=.9,evidence_text='清晰',requires_manual_review=False)
    assert VisionResponsePayload(findings=[base]).findings[0].evidence_coverage==[]
    base['evidence_coverage']=[dict(target='anchorage',status='occluded',evidence_text='遮挡锚固点')]
    assert VisionResponsePayload(findings=[base]).findings[0].evidence_coverage[0].status=='occluded'


def test_quality_text_conflict_is_internal_review_not_a_retake():
    c=case(findings=[finding(statuses={'support_context':'visible','connection_node':'visible','anchorage':'occluded'})])
    c.findings.append(RiskFinding(finding_id='quality',material_id='node',category='image_quality',label='质量',detection_status='detected',severity='info',confidence=1,evidence_text='图片清晰，光照充足，细节可见，图像质量满足检查要求。',provider='test',model='test',requires_manual_review=False))
    r=assess(c);assert active(r)['status']=='review' and not r['customer_requests']


def test_all_coverage_targets_reference_real_risk_categories():
    from app.contracts import RiskCategory
    from app.rules.evidence_coverage import REQUIREMENTS
    values={r.value for r in RiskCategory}
    assert all(set(t['risks'])<=values for targets in REQUIREMENTS.values() for t in targets)


def test_unassigned_photos_require_internal_mapping_before_retake():
    c=case();c.installation_analysis.areas[0].material_ids=[]
    r=assess(c);assert active(r)['status']=='review' and not r['customer_requests']
    assert any('归属' in q for q in r['review_questions'])
