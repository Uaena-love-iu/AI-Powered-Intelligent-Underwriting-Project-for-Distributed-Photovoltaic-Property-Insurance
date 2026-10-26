import copy
import json
from datetime import UTC, datetime, timedelta
import pytest
from pydantic import ValidationError
from app.strategies.engine import Scheme, Rule, Repository, Selection, snapshot, evaluate
from app.storage.cases import CaseRepository
from app.main import app
from integration.test_case_component_review import client, png
from integration.test_revision_api import done


def scheme(rules=None):
    now=datetime.now(UTC)
    return dict(name='模拟冬季策略',company='模拟保险业务',basis='仅用于功能验收，没有正式定价效力',reviewer_name='测试发布人',
        valid_from=(now-timedelta(days=1)).isoformat(),expires_at=(now+timedelta(days=10)).isoformat(),demo=True,confirmed=True,
        rules=rules or [dict(id='wind-high',name='冬季风灾关注',conditions={'provinces':['山东省'],'start_months':[12,1,2]},source='wind',values=['high','critical'],action='surcharge',priority=100,attention_level='high',reason='模拟业务方案，风灾高风险转加费审核')])


def case(**changes):
    value=dict(project=dict(province='山东省',city='聊城市',proposed_start_date='2027-01-15',installed_capacity_kw=200),
        component_profile=dict(component_model='TEST-600/AB',model_match_status='exact'),decision={'decision':'accept','missing_requirements':[]},
        pv_load_screening_assessment={'wind':{'risk_level':'high'},'snow':{'risk_level':'low'},'requires_manual_review':False})
    value.update(changes);return value


def approved(body=None):
    record=Repository().create(Scheme.model_validate(body or scheme()),'bound-actor')
    return snapshot(Selection(record_id=record['id']))


def test_conditions_use_business_start_month_not_current_day_and_keep_facts():
    plan=approved();data=case();original=copy.deepcopy(data);result=evaluate(data,plan)
    assert result['matched_ids']==['wind-high'] and result['effective_suggestion']=='surcharge'
    assert result['pricing']['rate'] is None and result['pricing']['mode']=='suggestion_only'
    assert data==original
    data['project']['proposed_start_date']='2027-07-01';assert not evaluate(data,plan)['matched_ids']
    data['project']['proposed_start_date']=None;missing=evaluate(data,plan)
    assert missing['effective_suggestion']=='manual_review' and missing['rule_results'][0]['status']=='insufficient'


def test_exact_region_model_and_capacity_are_conjunctions():
    body=scheme();body['rules'][0]['conditions'].update(cities=['聊城市'],models=['TEST-600/AB'],min_capacity_kw=100,max_capacity_kw=300)
    plan=approved(body);data=case();assert evaluate(data,plan)['matched_ids']
    for key,value in [('city','济南市'),('installed_capacity_kw',301)]:
        altered=copy.deepcopy(data);altered['project'][key]=value;assert not evaluate(altered,plan)['matched_ids']
    data['component_profile']['component_model']='TEST-600/AC';assert not evaluate(data,plan)['matched_ids']
    data['component_profile']['model_match_status']='series_only';assert evaluate(data,plan)['rule_results'][0]['status']=='insufficient'


@pytest.mark.parametrize('baseline,expected',[('request_more','request_more'),('recommend_reject','recommend_reject'),('manual_review','manual_review')])
def test_strategy_never_erases_original_blockers(baseline,expected):
    result=evaluate(case(decision={'decision':baseline}),approved())
    assert result['effective_suggestion']==expected and result['original_ai_decision']==baseline


def test_tied_actions_conflict_and_priorities_are_deterministic():
    body=scheme();r=copy.deepcopy(body['rules'][0]);r.update(id='wind-reject',action='recommend_reject');body['rules'].append(r)
    data=case();result=evaluate(data,approved(body));assert result['conflict'] and result['strategy_action']=='manual_review'
    body['rules'][1]['priority']=101;result=evaluate(data,approved(body));assert not result['conflict'] and result['selected_ids']==['wind-reject']


def test_missing_risk_is_unknown_not_low_or_not_applicable():
    data=case(pv_load_screening_assessment=None);r=evaluate(data,approved())
    assert r['facts']['wind']=='unknown' and r['effective_suggestion']=='manual_review'
    body=scheme();body['rules'][0].update(values=['unknown'],action='request_more');assert evaluate(data,approved(body))['strategy_action']=='request_more'


@pytest.mark.parametrize('alter',[{'action':'accept'},{'source':'wind','values':['accept']},{'values':['unknown'],'action':'surcharge'},{'rate':.15}])
def test_invalid_or_invented_pricing_configuration_rejected(alter):
    body=scheme();body['rules'][0].update(alter)
    with pytest.raises(ValidationError):Scheme.model_validate(body)


def test_no_selected_scheme_does_not_add_results():assert evaluate(case(),{})=={}


def test_versions_expiry_revocation_and_snapshots_are_immutable():
    old=approved();body=scheme();body['base_id']=old['id'];body['rules'][0]['action']='manual_review'
    new=Repository().create(Scheme.model_validate(body),'second-actor');assert new['version']==2 and new['family']==old['family']
    assert Repository().get(old['id'])['rules'][0]['action']=='surcharge'
    with pytest.raises(ValueError):Repository().create(Scheme.model_validate(body),'third-actor')
    Repository().revoke(old['id'],'admin','停止新任务使用')
    with pytest.raises(ValueError):snapshot(Selection(record_id=old['id']))
    assert evaluate(case(),old)['strategy_action']=='surcharge'
    body=scheme();body['valid_from']=(datetime.now(UTC)-timedelta(days=3)).isoformat();body['expires_at']=(datetime.now(UTC)-timedelta(days=1)).isoformat()
    expired=Repository().create(Scheme.model_validate(body),'admin')
    with pytest.raises(ValueError):snapshot(Selection(record_id=expired['id']))


def submit(client,id_,record_id=None,parent=None):
    project=dict(project_name=None,insured_name=None,project_type='unknown',installation_type='unknown',site_address=None,longitude=None,latitude=None,proposed_start_date='2027-01-15',component_model='JAM66D42-580/MB',province='山东省')
    body=dict(case_id=id_,project=project,rule_selection={'selected_rule_ids':['component']},materials=[{'material_id':'plate','category':'component_nameplate'}])
    if record_id:body['strategy_selection']={'record_id':record_id}
    if parent:body['revision']={'parent_case_id':parent,'actor':'核保测试人','reason':'切换承保策略重新核保'}
    return client.post('/api/v1/underwriting/jobs',data={'manifest':json.dumps(body)},files=[('files',('plate.png',png('white'),'image/png'))])


def test_api_snapshot_report_preview_and_new_round(client):
    body=scheme();body['rules'][0].update(source='decision',values=['manual_review'],action='request_more')
    record=client.post('/api/v1/strategies',json=body);assert record.status_code==201,record.text
    record=record.json();first=done(client,submit(client,'strategy-first',record['id']))
    assert first['decision']['decision']=='manual_review'
    assert first['strategy_assessment']['effective_suggestion']=='request_more'
    assert first['strategy_assessment']['scheme_snapshot']['id']==record['id']
    report=client.get('/api/v1/cases/strategy-first/report').text
    assert '承保策略处理建议' in report and '未配置费率' in report and '拟起保日期 2027-01-15' in report
    before=client.get('/api/v1/cases/strategy-first').json()
    preview=client.post('/api/v1/strategies/preview',json={'record_id':record['id'],'case_id':'strategy-first'})
    assert preview.status_code==200 and preview.json()['preview_only']
    assert client.get('/api/v1/cases/strategy-first').json()==before
    second=done(client,submit(client,'strategy-second',parent='strategy-first'))
    assert second['strategy_assessment']=={}
    comparison=client.get('/api/v1/cases/strategy-second/rounds').json()['rounds'][-1]['comparison']
    assert comparison['strategy_before']['scheme_snapshot']['id']==record['id'] and comparison['strategy_after']=={}
    client.post('/api/v1/strategies/'+record['id']+'/revoke',json={'reviewer_name':'管理员','reason':'验收结束'})
    assert submit(client,'strategy-revoked',record['id']).status_code==422
    assert CaseRepository().get_case('strategy-revoked') is None
    assert client.get('/api/v1/cases/strategy-first').json()['case']['strategy_assessment']==first['strategy_assessment']


def test_preview_requires_existing_case(client):
    record=client.post('/api/v1/strategies',json=scheme()).json()
    assert client.post('/api/v1/strategies/preview',json={'record_id':record['id'],'case_id':'missing'}).status_code==404


def test_strategy_write_permissions():
    from app.security.middleware import RoleAccessMiddleware as M
    assert M._required_role('POST','/api/v1/strategies')=='admin'
    assert M._required_role('POST','/api/v1/strategies/record/revoke')=='admin'
    assert M._required_role('POST','/api/v1/strategies/preview')=='underwriter'
    assert M._required_role('GET','/api/v1/strategies')=='viewer'
