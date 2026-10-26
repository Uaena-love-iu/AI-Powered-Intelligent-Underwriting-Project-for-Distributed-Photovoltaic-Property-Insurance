"""A-F plus guardrails: deterministic model doubles, no external model charges."""
from copy import deepcopy
from io import BytesIO
import json
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from app.contracts import Material, ProjectInfo, RiskFinding, UnderwritingCase, InstallationAnalysis, DecisionType
from app.providers import MaterialInput, MockOcrProvider
from app.providers.vision.base import VisionProvider
from app.pipeline import UnderwritingPipeline
from app.rules.selection import resolve, Selection
from app.verification.engine import new_state, discover, run_loop, effective_findings, recalculate
from app.verification.service import review_item, finalize, supplement
from app.verification.contracts import Review, FinalReview
from app.verification.store import Store, Conflict


def project():
    return ProjectInfo(project_name='核验样例',insured_name='测试企业',project_type='rooftop',installation_type='flat_roof',
        site_address=None,longitude=None,latitude=None,proposed_start_date=None,component_model=None)


def image(mid):
    b=BytesIO(); Image.new('RGB',(256,256),(sum(map(ord,mid))%256,180,210)).save(b,'PNG')
    return MaterialInput(Material(material_id=mid,category='component_surface',file_name=mid+'.png',media_type='image/png',
        quality_status='usable',quality_issues=[],parse_status='success'), b.getvalue())


def finding(mid='a',status='uncertain',category='module_damage',**kw):
    return RiskFinding(**(dict(finding_id=mid+':'+category,material_id=mid,category=category,label='组件表面损伤',
        detection_status=status,severity='high',confidence=.97,evidence_text='同一组件表面局部无法看清',
        requires_manual_review=status=='uncertain',provider='test',model='test')|kw))


def case(fs=None):
    inputs=[image('a'),image('b'),image('unrelated')]
    snapshot=resolve(Selection(selected_rule_ids=['surface']),project())
    c=UnderwritingCase(schema_version='0.1.0',case_id='v2-test',project=project(),materials=[m.material for m in inputs],
        findings=fs if fs is not None else [finding()],ocr_fields=[],material_reviews=[],processing_trace=[],ruleset_snapshot=snapshot,
        installation_analysis=InstallationAnalysis(provider='test',areas=[dict(area_id='roof-a',scene_type='building_roof',status='confirmed',material_ids=['a','b'],evidence_text='同一屋面'),dict(area_id='roof-b',scene_type='building_roof',status='confirmed',material_ids=['unrelated'],evidence_text='另一屋面')]))
    c.verification=new_state(c)
    discover(c)
    return c,inputs


class Model(VisionProvider):
    name='test';model_name='test'
    def __init__(self,mode='resolved'):self.mode=mode;self.used=[];self.singles=[]
    def analyze(self,item):
        self.singles.append(item.material.material_id)
        return [finding(item.material.material_id,'not_detected',evidence_coverage=[{'target':'module_surface','status':'visible','evidence_text':'表面完整可见'}])]
    def analyze_installation(self,materials,**kwargs):
        return InstallationAnalysis(provider='test',areas=[])
    def plan_verification(self,ctx):
        if self.mode=='missing':return dict(action='request_more',question='请补充同一组件表面另一角度照片？',reason='当前视角遮挡关键部位',missing_evidence='请拍摄同一组件表面正面及侧面近景，包含四角和参照物，以确认破损。')
        if self.mode=='classification':return dict(action='human',question='按现有核保口径，本案是否属于涉水风险？',reason='画面充分，适用业务分类仍需核保员确认',evidence_sufficient=True,options=[dict(id='risk-accepted',label='风险分类成立，建议可接受',fact='present',classification='成立',treatment='acceptable')])
        ids=[x['material_id'] for x in ctx['candidates'] if x['material_id']!='unrelated']
        return dict(action='group',question='请确认同一组件是否存在破损？',reason='对照互补角度',material_ids=ids[:2])
    def verify_images(self,materials,ctx):
        self.used.append([m.material.material_id for m in materials])
        if self.mode=='fail':raise RuntimeError('simulated')
        return dict(outcome='uncertain' if self.mode=='uncertain' else 'resolved',conclusion='同一组件完整可见，原疑点来自反光',
            same_object=True,evidence_sufficient=True,evidence_ids=[m.material.material_id for m in materials],findings=[finding(status='not_detected').model_dump(mode='json')])


def test_A_targeted_group_resolves_without_unrelated_images():
    c,inputs=case();model=Model();run_loop(c,model,inputs)
    assert c.verification['issues'][0]['status']=='resolved'
    assert model.used==[['a','b']]
    assert c.verification['first_round']['findings'][0]['detection_status']=='uncertain'
    assert effective_findings(c)[0].detection_status.value=='not_detected'


def test_B_missing_pauses_then_only_new_photo_is_analyzed():
    c,inputs=case();run_loop(c,Model('missing'),inputs);issue=c.verification['issues'][0]
    assert issue['status']=='awaiting_materials' and '正面' in issue['supplement_request']
    model=Model();new=image('new')
    result=supplement(c,inputs,[new],[issue['id']],{'new':'roof-a'},[],model,MockOcrProvider(),lambda c:None)
    assert model.singles==['new']
    assert {'a','b','new','unrelated'} <= {m.material_id for m in result.materials}
    assert result.verification['first_round']['findings'][0]['detection_status']=='uncertain'
    assert result.verification['version']==2
    assert issue['status']=='resolved'


def test_C_sufficient_evidence_classification_stops_images():
    c,inputs=case([finding(category='water_adjacent_environment',uncertainty_kind='classification')])
    # Enable the existing environmental rules, no new water definition.
    c.ruleset_snapshot=resolve(Selection(selected_rule_ids=['environment']),project());discover(c)
    model=Model('classification');run_loop(c,model,inputs)
    assert c.verification['issues'][0]['status']=='human_review'
    assert '涉水' in c.verification['issues'][0]['question']
    assert model.used==[] and not c.verification['issues'][0]['supplement_request']


@pytest.mark.parametrize('other',[False,True])
def test_D_human_classification_and_treatment_are_separate(other):
    c,inputs=case();run_loop(c,Model('classification'),inputs);recalculate(c)
    Store().repo.save_case(c);Store().archive_sources(c.case_id,inputs)
    i=c.verification['issues'][0]
    body=Review(version=1,option_id='other' if other else 'risk-accepted',fact='present',classification='成立',
        treatment='acceptable',disagreements=['treatment'],opinion='现场证据支持风险存在，承保处理仍依现有规则',actor='复核员',evidence_ids=['a'])
    result=review_item(c.case_id,i['id'],body,'复核员','local_demo')
    assert result.findings[0].detection_status.value=='detected'
    assert result.verification['human_events'][0]['training_truth'] is False
    assert result.verification['human_events'][0]['disagreements']==['treatment']
    assert result.verification['version']==2
    assert Store().history(c.case_id)[0]['event']=='item_review'


def test_E_unresolved_item_blocks_accept_on_server():
    c,_=case();recalculate(c);Store().repo.save_case(c)
    body=FinalReview(version=1,decision='accept',opinion='希望通过',actor='核保员',override_reason='测试')
    with pytest.raises(Conflict,match='未|仍有'):finalize(c.case_id,body,'核保员','local_demo')


def test_F_negative_on_other_photo_never_cancels_positive():
    c,inputs=case([finding('a','detected'),finding('b','not_detected')]);model=Model();run_loop(c,model,inputs)
    assert not c.verification['issues']
    assert next(f for f in effective_findings(c) if f.material_id=='a').detection_status.value=='detected'
    assert model.used==[]


def test_positive_cannot_be_cleared_without_specific_correction():
    c,inputs=case([finding('a','detected',requires_manual_review=True)]);run_loop(c,Model(),inputs)
    assert c.verification['issues'][0]['status']=='human_review'
    assert effective_findings(c)[0].detection_status.value=='detected'


@pytest.mark.parametrize('mode',['fail','uncertain'])
def test_failures_and_repeated_calls_stay_unresolved(mode):
    c,inputs=case();model=Model(mode);run_loop(c,model,inputs)
    assert c.verification['issues'][0]['status']=='human_review'
    assert len(model.used)==1


def test_cross_area_selection_is_rejected():
    class Wrong(Model):
        def plan_verification(self,ctx):return dict(action='group',question='确认是否破损？',reason='比较图片',material_ids=['a','unrelated'])
    c,inputs=case();m=Wrong();run_loop(c,m,inputs)
    assert not m.used and c.verification['issues'][0]['status']=='human_review'


def test_stale_review_version_is_rejected():
    c,_=case();recalculate(c);Store().repo.save_case(c)
    with Store().db() as con:
        with pytest.raises(Conflict):Store().current(con,c.case_id,99)


def test_full_pipeline_preserves_initial_single_and_group(monkeypatch):
    monkeypatch.setenv('PV_VERIFICATION_ENABLED','true')
    c,inputs=case();m=Model();m.analyze=lambda item:[finding(item.material.material_id)]
    result=UnderwritingPipeline(vision_provider=m,ocr_provider=MockOcrProvider()).run(case_id='whole',project=project(),materials=inputs,rule_snapshot=c.ruleset_snapshot)
    assert result.verification['first_round']['findings']
    assert result.verification['calls']
    assert Store().sources('whole')


def test_legacy_review_endpoint_cannot_bypass_v2():
    from app.main import app
    c,_=case();recalculate(c);Store().repo.save_case(c)
    with TestClient(app) as client:
        r=client.post('/api/v1/cases/'+c.case_id+'/review',json={'reviewer_name':'someone','final_decision':'accept','comment':'直接通过'})
    assert r.status_code==409


def test_report_contains_version_questions_and_audit():
    from app.reports import generate_markdown_report
    c,inputs=case();run_loop(c,Model('classification'),inputs);recalculate(c)
    report=generate_markdown_report(c)
    assert '证据与复核版本：1' in report and 'risk-accepted' in report and '首轮与调用审计' in report


def test_supplement_api_accepts_new_file_only_and_archives_old_bytes():
    from app.main import app
    from app.api.routes import get_vision_provider
    from app.api.ocr_routes import get_ocr_provider
    c,inputs=case();run_loop(c,Model('missing'),inputs);recalculate(c)
    store=Store();store.repo.save_case(c);store.archive_sources(c.case_id,inputs)
    model=Model();app.dependency_overrides[get_vision_provider]=lambda:model
    app.dependency_overrides[get_ocr_provider]=lambda:MockOcrProvider()
    spec=dict(version=1,actor='审核员',reason='补拍相同组件',issue_ids=[c.verification['issues'][0]['id']],area_map={'new':'roof-a'},materials=[dict(material_id='new',category='component_surface')])
    try:
        with TestClient(app) as client:
            response=client.post(f'/api/v2/cases/{c.case_id}/verification/supplements',data={'manifest':json.dumps(spec)},files=[('files',('new.png',image('new').content,'image/png'))])
            assert response.status_code==202,response.text
            job=client.get(f'/api/v2/cases/{c.case_id}/verification/jobs/'+response.json()['job_id']).json()
            assert job['status']=='completed',job
            latest=client.get(f'/api/v2/cases/{c.case_id}/verification').json()['case']
            assert latest['verification']['version']==2
            assert client.get(f'/api/v2/cases/{c.case_id}/verification/materials/a').content==inputs[0].content
            assert len(latest['materials'])==4
            assert model.singles==['new']
    finally:app.dependency_overrides.clear()


def test_other_requires_explicit_fields_and_cannot_forge_candidate():
    c,inputs=case();run_loop(c,Model('classification'),inputs);recalculate(c);Store().repo.save_case(c)
    i=c.verification['issues'][0]
    body=Review(version=1,option_id='risk-accepted',fact='absent',classification='不成立',treatment='acceptable',disagreements=['recognition'],opinion='人为修改候选',actor='u',evidence_ids=['a'])
    with pytest.raises(Conflict,match='选项'):review_item(c.case_id,i['id'],body,'u','local_demo')


def test_final_decision_saved_only_after_valid_completion_and_idempotent():
    c,_=case([]);recalculate(c)
    # A fully completed assessment fixture, independent of the picture-coverage scenarios.
    c.decision=c.decision.model_copy(update={'decision':DecisionType.ACCEPT,'missing_requirements':[]})
    c.verification['issues']=[]
    Store().repo.save_case(c)
    body=FinalReview(version=1,decision='accept',opinion='证据充分，现有规则允许',actor='核保员')
    finalize(c.case_id,body,'核保员','local_demo');finalize(c.case_id,body,'核保员','local_demo')
    saved=Store().repo.get_case(c.case_id)
    assert saved['final_decision']=='accept' and len(saved['review_history'])==1
    assert saved['case']['verification']['final']['identity_mode']=='local_demo'


def test_role_gate_on_v2_read_and_write(monkeypatch):
    from app.main import app
    from app.security.auth import AuthRepository,AuthUser
    monkeypatch.setenv('PV_AUTH_ENABLED','true')
    monkeypatch.setattr(AuthRepository,'ensure_initial_admin',lambda s:None)
    monkeypatch.setattr(AuthRepository,'resolve_session',lambda s,t:AuthUser(username='viewer',role='viewer'))
    c,_=case();recalculate(c);Store().repo.save_case(c)
    with TestClient(app) as client:
        assert client.get('/api/v2/cases/v2-test/verification').status_code==401
        assert client.get('/api/v2/cases/v2-test/verification',headers={'Authorization':'Bearer token'}).status_code==200
        assert client.post('/api/v2/cases/v2-test/verification/final',headers={'Authorization':'Bearer token'},json={}).status_code==403


def test_authenticated_actor_cannot_be_forged(monkeypatch):
    from app.main import app
    from app.security.auth import AuthRepository,AuthUser
    monkeypatch.setenv('PV_AUTH_ENABLED','true')
    monkeypatch.setattr(AuthRepository,'ensure_initial_admin',lambda s:None)
    monkeypatch.setattr(AuthRepository,'resolve_session',lambda s,t:AuthUser(username='real-user',role='underwriter'))
    c,inputs=case();run_loop(c,Model('classification'),inputs);recalculate(c);Store().repo.save_case(c)
    body=dict(version=1,option_id='other',fact='present',classification='成立',treatment='acceptable',disagreements=['treatment'],opinion='承保意见与风险事实分离',actor='fake-user',evidence_ids=['a'])
    with TestClient(app) as client:
        r=client.post(f'/api/v2/cases/v2-test/verification/items/{c.verification["issues"][0]["id"]}/review',headers={'Authorization':'Bearer token'},json=body)
    assert r.status_code==200,r.text
    assert r.json()['verification']['human_events'][-1]['actor']=='real-user'


def test_selected_rule_scope_and_no_issue_no_image_loop():
    c,inputs=case([finding(status='not_detected')]);m=Model();run_loop(c,m,inputs)
    assert not m.used and not c.verification['calls']


def test_model_output_and_generated_price_options_are_validated():
    from app.verification.engine import options_for
    from app.verification.contracts import Option
    issue={};options_for(issue,[Option(id='bad',label='直接加费20%',fact='present',classification='成立',treatment='surcharge')])
    assert issue['options']==[]


def test_material_replacement_keeps_original_archive_and_invalidates_review():
    c,inputs=case();run_loop(c,Model('classification'),inputs);recalculate(c)
    store=Store();store.repo.save_case(c);store.archive_sources(c.case_id,inputs)
    i=c.verification['issues'][0]
    b=Review(version=1,option_id='other',fact='present',classification='成立',treatment='acceptable',disagreements=['classification'],opinion='确认本次分类',actor='u',evidence_ids=['a'])
    c=review_item(c.case_id,i['id'],b,'u','local_demo')
    m=Model('missing');new=image('replacement')
    supplement(c,inputs,[new],[i['id']],{'replacement':'roof-a'},['a'],m,MockOcrProvider(),lambda _:None)
    issue=next(x for x in c.verification['issues'] if x['id']==i['id'])
    assert 'human_review' not in issue and issue['status']!='confirmed'
    assert 'a' not in {m.material_id for m in c.materials}
    assert any(m.material.material_id=='a' for m in store.sources(c.case_id))
    assert any(e.get('event')=='confirmation_invalidated' for e in c.verification['human_events'])


def test_failed_verification_can_resume_without_uploading_photos():
    from app.main import app
    from app.api.routes import get_vision_provider
    from app.api.ocr_routes import get_ocr_provider
    c,inputs=case();run_loop(c,Model('fail'),inputs);recalculate(c)
    store=Store();store.repo.save_case(c);store.archive_sources(c.case_id,inputs)
    m=Model();app.dependency_overrides[get_vision_provider]=lambda:m
    app.dependency_overrides[get_ocr_provider]=lambda:MockOcrProvider()
    try:
        with TestClient(app) as client:
            r=client.post('/api/v2/cases/v2-test/verification/resume',json=dict(version=1,actor='核保员',reason='服务恢复后重试',issue_ids=[c.verification['issues'][0]['id']]))
            assert r.status_code==202,r.text
            assert client.get('/api/v2/cases/v2-test/verification/jobs/'+r.json()['job_id']).json()['status']=='completed'
        latest=store.repo.get_case(c.case_id)['case']
        assert latest['verification']['issues'][0]['status']=='resolved'
        assert len(latest['materials'])==3 and m.singles==[]
        assert m.used==[['a','b']]
        assert latest['verification']['version']==2
    finally:app.dependency_overrides.clear()


def test_duplicate_old_image_does_not_supply_new_evidence():
    from dataclasses import replace
    c,inputs=case();run_loop(c,Model('missing'),inputs)
    duplicate=replace(inputs[0],material=inputs[0].material.model_copy(update={'material_id':'copy'}))
    with pytest.raises(Conflict,match='重复'):
        supplement(c,inputs,[duplicate],[c.verification['issues'][0]['id']],{'copy':'roof-a'},[],Model(),MockOcrProvider(),lambda _:None)
    assert c.verification['version']==1


def test_project_level_issue_can_use_known_roof_evidence():
    from app.verification.engine import candidates
    c,inputs=case();i=c.verification['issues'][0];i['area_id']='project'
    assert {x['material_id'] for x in candidates(c,i,inputs)}=={'a','b','unrelated'}


def test_initial_pipeline_calls_original_group_and_single_roles(monkeypatch):
    from dataclasses import replace
    from app.contracts import MaterialCategory
    monkeypatch.setenv('PV_VERIFICATION_ENABLED','true')
    class Group(Model):
        def __init__(self):super().__init__();self.groups=[]
        def analyze_installation(self,materials,**kwargs):
            self.groups.append([m.material.material_id for m in materials])
            return InstallationAnalysis(provider='test',areas=[dict(area_id='roof-a',scene_type='building_roof',status='confirmed',material_ids=[m.material.material_id for m in materials],evidence_text='屋面承载明确')])
    m=Group();items=[image('a'),image('b')]
    panorama=replace(image('scene'),material=image('scene').material.model_copy(update={'category':MaterialCategory.PANORAMA}))
    snap=resolve(Selection(selected_rule_ids=['surface','installation']),project())
    result=UnderwritingPipeline(vision_provider=m,ocr_provider=MockOcrProvider()).run(case_id='first-pass',project=project(),materials=[panorama,*items],rule_snapshot=snap)
    assert m.groups==[['scene','a','b']]
    assert {'a','b'} <= set(m.singles)
    assert result.verification['first_round']['installation']['areas'][0]['area_id']=='roof-a'
    assert {f['material_id'] for f in result.verification['first_round']['findings']} >= {'a','b'}


def test_confirmed_classification_updates_real_advice_and_final():
    c,inputs=case([finding(evidence_coverage=[dict(target='module_surface',status='visible',evidence_text='表面全部清晰可见，存在分类分歧')])])
    c.installation_analysis.areas=c.installation_analysis.areas[:1]
    c.materials=c.materials[:2];inputs=inputs[:2]
    run_loop(c,Model('classification'),inputs);recalculate(c);Store().repo.save_case(c)
    body=Review(version=1,option_id='other',fact='absent',classification='不成立',treatment='acceptable',disagreements=['recognition'],opinion='清楚可见的反光，并无表面破损',actor='核保员',evidence_ids=['a'])
    result=review_item(c.case_id,c.verification['issues'][0]['id'],body,'核保员','local_demo')
    assert result.findings[0].detection_status.value=='not_detected'
    assert not result.decision.missing_requirements
    assert result.decision.decision==DecisionType.MANUAL_REVIEW  # Existing selected-scope policy.
    final=FinalReview(version=2,decision='accept',opinion='逐项确认已完成',actor='核保员',override_reason='核保员确认专项检查范围并结合完整承保范围作出决定')
    finalize(c.case_id,final,'核保员','local_demo')
    assert Store().repo.get_case(c.case_id)['final_decision']=='accept'


def test_http_transport_uses_real_pixels_and_validates_structured_result():
    import httpx
    from app.providers import CompatibleVisionProvider
    from app.settings import VlmSettings
    from app.verification.model import verify
    requests=[]
    def handler(request):
        body=json.loads(request.content);requests.append(body)
        output=dict(outcome='classification',conclusion='事实已充分，分类应按现有业务口径复核',same_object=True,evidence_sufficient=True,evidence_ids=['a'])
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(output)}}]})
    settings=VlmSettings(base_url='https://model.test/v1',api_key='test-only',model='test')
    provider=CompatibleVisionProvider(settings,transport=httpx.MockTransport(handler))
    result=verify(provider,[image('a')],{'issue':{'question':'是否破损'}})
    assert result['outcome']=='classification'
    assert any(x['type']=='image_url' and x['image_url']['url'].startswith('data:image/png;base64,') for x in requests[0]['messages'][1]['content'])


def test_aisle_resolution_updates_existing_group_and_rule_state():
    from app.contracts.stages import AisleAssessment
    c,inputs=case([])
    c.ruleset_snapshot=resolve(Selection(selected_rule_ids=['aisle']),project())
    c.installation_analysis.aisle_assessment=AisleAssessment(status='uncertain',material_id='a',supporting_material_ids=['a','b'],
        cross_image_reasoning='近景似乎有遮挡，需要对照全景',confidence=.5,severity='unknown',evidence_text='通道是否实际阻塞尚不明确')
    discover(c)
    class AisleModel(Model):
        def verify_images(self,materials,ctx):
            result=super().verify_images(materials,ctx)
            result['findings']=[finding(status='not_detected',category='aisle_obstruction').model_dump(mode='json')]
            return result
    run_loop(c,AisleModel(),inputs);recalculate(c)
    assert c.installation_analysis.aisle_assessment.status=='not_detected'
    assert next(r for r in c.rule_execution if r['id']=='aisle')['status']=='completed'


def test_service_failure_supplement_job_is_recoverable_from_checkpoint():
    from app.main import app
    from app.api.routes import get_vision_provider
    from app.api.ocr_routes import get_ocr_provider
    class Once(Model):
        def __init__(self):super().__init__();self.failed=False
        def analyze(self,item):
            if not self.failed:
                self.failed=True
                raise RuntimeError('transient model unavailable')
            return super().analyze(item)
    c,inputs=case();run_loop(c,Model('missing'),inputs);recalculate(c)
    store=Store();store.repo.save_case(c);store.archive_sources(c.case_id,inputs)
    m=Once();app.dependency_overrides[get_vision_provider]=lambda:m
    app.dependency_overrides[get_ocr_provider]=lambda:MockOcrProvider()
    spec=dict(version=1,actor='核保员',reason='补拍同一组件',issue_ids=[c.verification['issues'][0]['id']],area_map={'retry-new':'roof-a'},materials=[dict(material_id='retry-new',category='component_surface')])
    try:
        with TestClient(app) as client:
            r=client.post('/api/v2/cases/v2-test/verification/supplements',data={'manifest':json.dumps(spec)},files=[('files',('new.png',image('retry-new').content,'image/png'))])
            job='/api/v2/cases/v2-test/verification/jobs/'+r.json()['job_id']
            assert client.get(job).json()['status']=='failed'
            assert client.get('/api/v2/cases/v2-test/verification').json()['case']['verification']['version']==1
            assert client.post(job+'/retry').status_code==202
            assert client.get(job).json()['status']=='completed'
        latest=store.repo.get_case(c.case_id)['case']
        assert latest['verification']['version']==2
        assert len([x for x in latest['materials'] if x['material_id']=='retry-new'])==1
        assert len([f for f in latest['verification']['original_findings'] if f['material_id']=='retry-new'])==1
    finally:app.dependency_overrides.clear()


def test_human_coverage_confirmation_is_separate_and_traceable():
    c,inputs=case([finding(status='not_detected',evidence_coverage=[dict(target='module_surface',status='unknown',evidence_text='未返回部位覆盖状态')])])
    c.materials=c.materials[:2];c.installation_analysis.areas=c.installation_analysis.areas[:1]
    recalculate(c);discover(c);run_loop(c,Model('classification'),inputs)
    target=next(i for i in c.verification['issues'] if i['target_id']=='module_surface')
    Store().repo.save_case(c)
    b=Review(version=1,option_id='other',fact='absent',classification='不成立',treatment='acceptable',disagreements=['recognition'],opinion='人工核对图片，组件表面全貌清晰可见且无破损',actor='u',evidence_ids=['a'])
    with pytest.raises(Conflict,match='清楚可见'):review_item(c.case_id,target['id'],b,'u','local_demo')
    b.coverage_confirmed=True
    updated=review_item(c.case_id,target['id'],b,'u','local_demo')
    rows=[r for r in updated.evidence_coverage['rows'] if r['rule_id']=='surface']
    assert rows[0]['targets'][0]['status']=='satisfied'
    assert updated.verification['first_round']['findings'][0]['evidence_coverage'][0]['status']=='unknown'


def test_different_final_cannot_overwrite_with_stale_version():
    c,_=case([]);recalculate(c)
    c.decision=c.decision.model_copy(update={'decision':DecisionType.ACCEPT,'missing_requirements':[]})
    c.verification['issues']=[];Store().repo.save_case(c)
    b=FinalReview(version=1,decision='accept',opinion='证据完整',actor='u')
    finalize(c.case_id,b,'u','local_demo')
    b.decision='recommend_reject';b.override_reason='另一个浏览器中的旧决定'
    with pytest.raises(Conflict,match='版本'):finalize(c.case_id,b,'u','local_demo')


def test_shared_scene_refresh_does_not_reanalyze_unaffected_roof():
    from dataclasses import replace
    from app.contracts import MaterialCategory
    from app.verification.dependencies import refresh
    c,inputs=case([]);c.ruleset_snapshot=resolve(Selection(selected_rule_ids=['installation']),project())
    inputs=[replace(m,material=m.material.model_copy(update={'category':MaterialCategory.PANORAMA})) for m in inputs]
    c.materials=[m.material for m in inputs]
    class Scene(Model):
        def __init__(self):super().__init__();self.groups=[]
        def analyze_installation(self,materials,**kw):
            self.groups.append([m.material.material_id for m in materials])
            return InstallationAnalysis(provider='test',areas=[dict(area_id='model-roof',scene_type='building_roof',status='confirmed',material_ids=[m.material.material_id for m in materials],evidence_text='受影响屋面清晰')])
    m=Scene();refresh(c,inputs,{'panorama'},m,MockOcrProvider(),['a'])
    assert m.groups==[['a','b']]
    assert {a.area_id for a in c.installation_analysis.areas}=={'roof-a','roof-b'}


def test_candidate_options_do_not_grant_existing_reject_rule_exemptions():
    from app.verification.engine import options_for
    from app.verification.contracts import Option
    issue=dict(risk_category='water_adjacent_environment')
    options_for(issue,[Option(id='waiver',label='风险成立但直接通过',fact='present',classification='成立',treatment='acceptable')])
    assert issue['options']==[]


def test_targeted_group_uses_configured_group_model():
    import httpx
    from app.providers import CompatibleVisionProvider
    from app.providers.routing import RoutedVisionProvider
    from app.settings import VlmSettings
    seen=[]
    def handler(request):
        seen.append(json.loads(request.content)['model'])
        result=dict(outcome='classification',conclusion='仍需核保口径确认',evidence_ids=['a','b'],evidence_sufficient=True)
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(result)}}]})
    def compatible(name):return CompatibleVisionProvider(VlmSettings(base_url='https://model.test/v1',api_key='test-only',model=name),transport=httpx.MockTransport(handler))
    provider=RoutedVisionProvider(compatible('single-model'),installation_delegate=compatible('group-model'))
    provider.verify_images([image('a'),image('b')],{'task':{'action':'group'}})
    assert seen==['group-model']
