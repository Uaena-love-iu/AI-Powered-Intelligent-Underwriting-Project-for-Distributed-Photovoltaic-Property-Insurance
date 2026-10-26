import json
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
from app.api.ocr_routes import get_ocr_provider
from app.api.routes import get_vision_provider
from app.api.component_dependencies import get_underwriting_component_provider
from app.providers import MockOcrProvider, MockVisionProvider
from app.storage import CaseRepository
from app.storage.revisions import RevisionRepository, RevisionConflict, CorrectedComponentProvider


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv('PV_AUTH_ENABLED','false')
    monkeypatch.setenv('PV_AMAP_WEB_SERVICE_KEY','')
    old = dict(app.dependency_overrides)
    app.dependency_overrides[get_ocr_provider] = lambda: MockOcrProvider()
    app.dependency_overrides[get_vision_provider] = lambda: MockVisionProvider()
    app.dependency_overrides[get_underwriting_component_provider] = lambda: None
    with TestClient(app) as value:
        yield value
    app.dependency_overrides.clear()
    app.dependency_overrides.update(old)


def png(color):
    stream=BytesIO()
    Image.new('RGB',(100,80),color).save(stream,format='PNG')
    return stream.getvalue()


def submit(client,id_,parent=None,items=None,model=None,corrections=None):
    items=items or [('m1','white')]
    manifest=dict(case_id=id_,project=dict(project_name=None,insured_name=None,project_type='unknown',installation_type='unknown',site_address=None,longitude=None,latitude=None,proposed_start_date=None,component_model=model),
                  materials=[dict(material_id=m,category='component_nameplate') for m,c in items])
    if parent:
        manifest['revision']=dict(parent_case_id=parent,actor='测试审核员',reason='补充证据并重新核验',corrections=corrections or [])
    response=client.post('/api/v1/underwriting/jobs',data={'manifest':json.dumps(manifest)},files=[('files',(m+'.png',png(c),'image/png')) for m,c in items])
    return response


def done(client,response):
    assert response.status_code==202,response.text
    job=client.get('/api/v1/underwriting/jobs/'+response.json()['job_id']).json()
    assert job['status']=='completed',job
    return job['result']


def review(client,id_):
    return client.post('/api/v1/cases/'+id_+'/review',json=dict(reviewer_name='模拟审核员',comment='仅用于测试的原件核验记录',final_decision='accept'))


def test_replacement_removal_addition_rounds_and_independent_review(client):
    first=done(client,submit(client,'round-one',items=[('m1','white'),('m2','blue')]))
    assert review(client,'round-one').json()['review_effective']
    second=done(client,submit(client,'round-two','round-one',items=[('m1','red'),('m3','green')]))
    assert first['decision']==client.get('/api/v1/cases/round-one').json()['case']['decision']
    old=client.get('/api/v1/cases/round-one').json()
    assert old['final_decision']=='accept' and not old['review_effective']
    assert review(client,'round-one').status_code==409
    current=client.get('/api/v1/cases/round-two').json()
    assert current['final_decision'] is None and not current['review_effective']
    assert review(client,'round-two').status_code==200
    rounds=client.get('/api/v1/cases/round-two/rounds').json()['rounds']
    assert [r['round_number'] for r in rounds]==[1,2]
    assert {(m['material_id'],m['action']) for m in rounds[1]['comparison']['materials']}=={('m1','replaced'),('m2','removed'),('m3','added')}
    assert client.get('/api/v1/cases/round-one/materials/m1/source').content==png('white')
    assert client.get('/api/v1/cases/round-one/materials/m2/source').content==png('blue')
    assert client.get('/api/v1/cases/round-two/materials/m1/source').content==png('red')
    assert '仅为历史记录' in client.get('/api/v1/cases/round-one/report').text
    assert submit(client,'branch','round-one').status_code==409


def test_correction_audit_validation_and_fresh_provider_lookup(client):
    done(client,submit(client,'original',model='OLD-500'))
    assert submit(client,'blank','original',corrections=[dict(field='component_model',material_id='m1',evidence='铭牌原文')]).status_code==409
    assert submit(client,'no-evidence','original',model='NEW-600',corrections=[dict(field='component_model',material_id='m1',evidence=' ')]).status_code==422
    second=done(client,submit(client,'corrected','original',model='NEW-600',corrections=[dict(field='component_model',material_id='m1',evidence='补充规格书第2页')]))
    audit=second['revision']['corrections'][0]
    assert audit['before']=='JAM66D42-580/MB' and audit['after']=='NEW-600'
    assert any(f['field_name']=='component_model' and f['normalized_value']=='JAM66D42-580/MB' for f in second['original_ocr_fields'])
    assert any(f['field_name']=='component_model' and f['normalized_value']=='NEW-600' and f['provider']=='human-review' for f in second['ocr_fields'])
    assert audit['actor']=='测试审核员' and audit['at'] and audit['evidence']=='补充规格书第2页'
    class Catalog:
        def __init__(self):self.calls=[]
        def lookup(self,model):self.calls.append(model);return None
        def lookup_materials(self,*args):raise AssertionError('must not reuse prior nameplate recognition')
    catalog=Catalog()
    from app.contracts import ProjectInfo
    CorrectedComponentProvider(catalog,audit).lookup_materials(ProjectInfo.model_validate(second['project']),[])
    assert catalog.calls==['NEW-600']


def test_snapshot_timestamps_do_not_count_as_rule_changes():
    from app.storage.revisions import compare_cases
    assert not compare_cases({'ruleset_snapshot':{'created_at':'a','snapshot_id':'a','rules':[1]}},
                             {'ruleset_snapshot':{'created_at':'b','snapshot_id':'b','rules':[1]}})['rules_changed']


def test_correcting_one_source_keeps_other_model_evidence(client):
    done(client,submit(client,'multi-original',items=[('m1','white'),('m2','blue')]))
    corrections=[dict(field='component_model',material_id='m1',evidence='仅核对第一份铭牌')]
    second=done(client,submit(client,'multi-corrected','multi-original',items=[('m1','white'),('m2','blue')],model='NEW-600',corrections=corrections))
    fields={f['material_id']:f for f in second['ocr_fields'] if f['field_name']=='component_model'}
    assert fields['m1']['normalized_value']=='NEW-600'
    assert fields['m2']['normalized_value']=='JAM66D42-580/MB'
    assert fields['m2']['provider']!='human-review'
    assert all(f['material_id']=='m1' for f in second['original_ocr_fields'])
    bad=submit(client,'bad-source','multi-corrected',model='OTHER',corrections=[dict(field='component_model',material_id='not-submitted',evidence='无对应原件')])
    assert bad.status_code==409
    assert RevisionRepository().get('bad-source') is None


def test_reservation_blocks_concurrent_round_and_old_review(client):
    done(client,submit(client,'parent'))
    store=RevisionRepository()
    request=store.request('parent')
    context=dict(parent_case_id='parent',actor='测试',reason='修改')
    store.reserve('reserved','pending-job',request,context)
    with pytest.raises(RevisionConflict):store.reserve('concurrent','other-job',request,context)
    assert review(client,'parent').status_code==409
    store.abandon('reserved')
    assert review(client,'parent').status_code==200


def test_material_sources_follow_case_retention(client):
    done(client,submit(client,'retained'))
    from datetime import UTC,datetime,timedelta
    CaseRepository().purge_expired_cases(retention_days=1,now=datetime.now(UTC)+timedelta(days=2))
    assert RevisionRepository().get('retained') is None
    assert client.get('/api/v1/cases/retained/materials/m1/source').status_code==404


def test_restart_unblocks_a_crashed_submission_without_revalidating_old_review(client):
    done(client,submit(client,'restart-parent'))
    store=RevisionRepository()
    request=store.request('restart-parent')
    context=dict(parent_case_id='restart-parent',actor='测试',reason='更正')
    store.reserve('interrupted-round','orphan-job',request,context)
    store.reconcile_orphans()
    assert client.get('/api/v1/underwriting/jobs/orphan-job').json()['status']=='failed'
    assert review(client,'restart-parent').status_code==409
    done(client,submit(client,'after-restart','restart-parent'))
    assert len(store.history('after-restart'))==3
