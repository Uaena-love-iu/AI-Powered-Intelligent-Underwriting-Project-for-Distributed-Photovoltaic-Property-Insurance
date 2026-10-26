import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.api.verified_component_routes import Approval
from app.providers.component_workbench import ComponentWorkbenchProvider
from app.storage.verified_components import VerifiedComponentRepository, VerifiedComponentConflict


def body():
    instant = datetime.now(UTC)
    return dict(component_model="TEST-600/AB", manufacturer="测试厂商", document_title="测试规格书 V1",
        applicability="正面测试载荷；夹具位置须按规格书核实，尚未确认实际项目安装适用性。",
        reason="已逐项核对完整型号及原文，仅供自动化测试", reviewer_name="测试核验员",
        valid_from=(instant-timedelta(days=1)).isoformat(), expires_at=(instant+timedelta(days=1)).isoformat(),
        exact_model_verified=True, parameters={"front_static_load_pa": dict(value=5400,
            source_url="https://example.com/test.pdf", quote="第 2 页：正面测试载荷 5400 Pa", load_basis="test"),
            "back_static_load_pa": dict(value=2400, source_url="https://example.com/test.pdf",
            quote="第 2 页：背面测试载荷 2400 Pa", load_basis="test")})


class Bench:
    rules_index = reader = None
    def __init__(self):
        self.calls = 0
    def run(self, *args, **kwargs):
        self.calls += 1
        return {"status": "not_found", "profile": None}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("PV_AUTH_ENABLED", "false")
    with TestClient(app) as c:
        yield c


def test_create_exact_reuse_and_source_scope(client):
    saved = client.post('/api/v1/components/verified', json=body())
    assert saved.status_code == 201, saved.text
    record = saved.json()
    assert record['scope'] == 'exact_model_source_parameters' and record['version'] == 1
    provider = ComponentWorkbenchProvider(Bench())
    profile = provider.lookup('test-600/ab')
    assert provider.workbench.calls == 0
    assert profile.front_static_load_pa == 5400 and profile.back_static_load_pa == 2400
    assert profile.wind_load_pa is None and profile.snow_load_pa is None
    assert profile.workbench_result['verified_reuse']['id'] == record['id']
    assert profile.parameter_evidence['front_static_load_pa']['records'][0]['load_basis'] == 'test'
    for different in ['TEST600/AB', 'TEST-600/AC', 'TEST-605/AB', 'TEST-600', 'TEST -600/AB']:
        assert provider.lookup(different) is None
    assert provider.workbench.calls == 5
    assert client.get('/verified-components').status_code == 200
    assert client.get('/verified-components.js').status_code == 200


@pytest.mark.parametrize('change',[
    {'exact_model_verified':False}, {'reviewer_name':'  '}, {'parameters':{}},
    {'component_model':'TEST-*'}, {'valid_from':'2026-10-21T10:00:00'}, {'scope':'all_rules'},
    {'parameters':{'wind_load_pa':dict(value=2400,source_url='https://example.com',quote='test')}},
    {'parameters':{'front_static_load_pa':dict(value=-1,source_url='https://example.com',quote='test')}},
    {'parameters':{'front_static_load_pa':dict(value=5400,source_url='http://example.com',quote='test')}},
    {'parameters':{'front_static_load_pa':dict(value=5400,source_url='https://user:pass@example.com',quote='test')}},
    {'parameters':{'front_static_load_pa':dict(value=5400,source_url='https://example.com',quote=' ')}},
])
def test_reject_unreviewed_or_unscoped_payload(client, change):
    value = body(); value.update(change)
    assert client.post('/api/v1/components/verified', json=value).status_code == 422
    assert client.get('/api/v1/components/verified').json()['records'] == []


def test_expiry_schedule_revocation_and_versions(client,monkeypatch):
    from app.storage import verified_components as module
    value=body(); saved=client.post('/api/v1/components/verified',json=value).json()
    repo=VerifiedComponentRepository(); provider=ComponentWorkbenchProvider(Bench()); token=provider.evidence_cache_token
    assert client.post('/api/v1/components/verified',json=value).status_code==409
    monkeypatch.setattr(module,'now',lambda: datetime.fromisoformat(value['expires_at']))
    assert repo.list()[0]['status']=='expired' and provider.lookup(value['component_model']) is None
    assert provider.evidence_cache_token!=token
    monkeypatch.setattr(module,'now',lambda: datetime.fromisoformat(value['valid_from'])-timedelta(seconds=1))
    assert repo.list()[0]['status']=='scheduled' and repo.lookup(value['component_model']) is None
    monkeypatch.setattr(module,'now',lambda: datetime.now(UTC))
    revoked=client.post('/api/v1/components/verified/'+saved['id']+'/revoke',json=dict(reviewer_name='复核员',reason='来源版本已更新'))
    assert revoked.status_code==200 and revoked.json()['status']=='revoked'
    assert repo.lookup(value['component_model']) is None
    assert client.post('/api/v1/components/verified/'+saved['id']+'/revoke',json=dict(reviewer_name='another',reason='重复')).status_code==409
    value['parameters']['front_static_load_pa']['value']=5500
    new=client.post('/api/v1/components/verified',json=value).json()
    assert new['version']==2 and new['id']!=saved['id']
    old=repo.list()[1]; assert old['parameters']['front_static_load_pa']['value']==5400 and old['revoked_by']=='复核员'
    assert repo.lookup(value['component_model']).front_static_load_pa==5500


def test_concurrent_approval_no_ambiguous_overlap():
    repo=VerifiedComponentRepository(); data=Approval.model_validate(body())
    def add(_):
        try: return repo.create(data,'reviewer')
        except VerifiedComponentConflict: return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(add,range(2)))
    assert sum(x is not None for x in results)==1


def test_recovery_cache_invalidated_on_revoke_expire_and_add(tmp_path,monkeypatch):
    from unit.test_job_recovery import execution
    from app.storage import verified_components as module
    store,run=execution(tmp_path); provider=ComponentWorkbenchProvider(Bench())
    assert run.call(provider,'lookup',('TEST-600/AB',),{}) is None
    repo=VerifiedComponentRepository(); value=body(); record=repo.create(Approval.model_validate(value),'reviewer')
    first=run.call(provider,'lookup',('TEST-600/AB',),{})
    assert first.front_static_load_pa==5400
    assert run.call(provider,'lookup',('TEST-600/AB',),{}).workbench_result==first.workbench_result
    monkeypatch.setattr(module,'now',lambda: datetime.fromisoformat(value['expires_at']))
    assert run.call(provider,'lookup',('TEST-600/AB',),{}) is None
    monkeypatch.setattr(module,'now',lambda: datetime.now(UTC))
    repo.revoke(record['id'],'reviewer','测试撤销')
    assert run.call(provider,'lookup',('TEST-600/AB',),{}) is None
    assert first.workbench_result['verified_reuse']['status']=='active' # immutable historical snapshot


def test_case_cache_and_punctuation_cannot_resurrect_or_expand_approval(monkeypatch):
    from app.storage.cases import CaseRepository
    from unit.test_job_recovery import material
    import recognition
    repo=VerifiedComponentRepository(); saved=repo.create(Approval.model_validate(body()),'reviewer')
    profile=repo.lookup('TEST-600/AB')
    cached=dict(case_id='old',profile=profile.model_dump(mode='json'),recognition=dict(candidates=[dict(lookup_model='TEST-600/AB',auto_query_eligible=True)]))
    case_repo=CaseRepository();monkeypatch.setattr(case_repo,'find_verified_component_for_file',lambda _:copy.deepcopy(cached))
    provider=ComponentWorkbenchProvider(Bench(),case_repo)
    assert provider.lookup_materials(SimpleNamespace(component_model='TEST-600/AB'),[material()]).front_static_load_pa==5400
    repo.revoke(saved['id'],'reviewer','撤销参数')
    assert provider.lookup_materials(SimpleNamespace(component_model='TEST-600/AB'),[material()]) is None
    assert provider.workbench.calls==1
    # Recognition's looser compact_key must not expand an approval's model scope.
    cached['recognition']['candidates'][0]['lookup_model']='TEST600/AB'
    assert provider.lookup_materials(SimpleNamespace(component_model='TEST-600/AB'),[material()]) is None
    assert provider.last_result['status']=='identity_conflict'


def test_reuse_does_not_override_decision_and_report_keeps_evidence():
    from unit.test_report_generator import make_case
    from app.decision.engine import DecisionEngine
    from app.reports import generate_markdown_report
    repo=VerifiedComponentRepository();record=repo.create(Approval.model_validate(body()),'reviewer')
    original=make_case(); profile=repo.lookup('TEST-600/AB')
    updated=original.model_copy(update={'component_profile':profile,'component_module_result':profile.workbench_result})
    # Compare the same risk/material case; the evidence record grants no exemption.
    assert DecisionEngine().decide(updated).decision == DecisionEngine().decide(original).decision
    report=generate_markdown_report(updated)
    assert record['id'] in report and '核验人：reviewer' in report and '安装条件' in report
    repo.revoke(record['id'],'reviewer','撤销原记录')
    assert generate_markdown_report(updated)==report


def test_roles_and_auditor_are_bound_to_login(client,monkeypatch):
    from app.security import AuthRepository
    repo=AuthRepository()
    repo.create_user(username='read-user',password='long-test-password-123',role='viewer')
    repo.create_user(username='review-user',password='long-test-password-123',role='underwriter')
    monkeypatch.setenv('PV_AUTH_ENABLED','true')
    def headers(user):
        token=client.post('/api/v1/auth/login',json=dict(username=user,password='long-test-password-123')).json()['access_token']
        return {'Authorization':'Bearer '+token}
    viewer,reviewer=headers('read-user'),headers('review-user')
    assert client.post('/api/v1/components/verified',json=body(),headers=viewer).status_code==403
    saved=client.post('/api/v1/components/verified',json=body(),headers=reviewer)
    assert saved.status_code==201,saved.text
    assert saved.json()['approved_by']=='review-user'
    url='/api/v1/components/verified/'+saved.json()['id']+'/revoke'
    assert client.post(url,json=dict(reviewer_name='forged',reason='test revoke'),headers=viewer).status_code==403
    assert client.post(url,json=dict(reviewer_name='forged',reason='test revoke'),headers=reviewer).json()['revoked_by']=='review-user'
