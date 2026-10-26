from datetime import UTC, datetime

import pytest
from app.api import unified_risk
from app.catastrophe.front_load_screening.unified import assess_unified, capacities, grade, report
from app.contracts import ComponentProfile, ProjectInfo, UnderwritingCase
from app.main import app
from app.pipeline import UnderwritingPipeline
from app.providers import MockOcrProvider, MockVisionProvider
from app.reports import generate_markdown_report
from app.storage import CaseRepository
from fastapi.testclient import TestClient


def component(value=5400,model='TEST-5400'):
    return ComponentProfile(component_model=model,front_static_load_pa=value,back_static_load_pa=2400,wind_load_pa=2400,
        source_name='fixture manufacturer',source_url='https://example.com/spec.pdf',retrieved_at=datetime.now(UTC),match_confidence=1,model_match_status='exact',
        parameter_evidence={'front_static_load_pa':[{'status':'found','value':value,'source_url':'https://example.com/spec.pdf','quote':'front load','load_direction':'front'}]})
def params():
    return {'ai_geometry':{'height_m':{'min':10,'max':10,'source':'现场确认'},'tilt_deg':{'min':15,'max':15,'source':'现场确认'},'terrain':{'candidates':['B'],'source':'周边照片'}}}
def assess(c=None,address='北京市朝阳区',**kwargs):
    return assess_unified(case_id='test',model='TEST-5400',address=address,component=c or component(),screening_input=params(),**kwargs)

def test_beijing_full_chain_and_direction():
    r=assess();assert r['wind']['load_pa']['max']==637.5;assert r['snow']['load_pa']['max']==250
    assert r['wind']['capacity_pa']==5400 # not the back wind suction value 2400
    assert r['risk_level']=='low' and r['data_complete'];assert r['can_auto_approve'] is False
    assert r['requires_manual_review'] is False
    assert r['location_match']['return_period_years']==10

def test_different_snow_capacity_retained():
    c=component();c.snow_load_pa=6000
    r=assess(c);assert r['wind']['capacity_pa']==5400;assert r['snow']['capacity_pa']==6000

def test_no_front_value_no_back_substitution():
    c=component();c.front_static_load_pa=None
    r=assess(c);assert r['wind']['risk_level']=='unknown';assert 'load_pa' not in r['wind']

def test_conflicting_front_evidence_blocks():
    c=component();c.parameter_evidence['front_static_load_pa'].append({'status':'conflict'})
    assert capacities(c)['wind']['value_pa'] is None

def test_model_mismatch_blocks():
    r=assess(component(model='OTHER'));assert r['status']=='INSUFFICIENT_DATA';assert '型号' in r['integration_issue']

def test_location_conflict_blocks(): assert assess(location_conflict=True)['status']=='INSUFFICIENT_DATA'

def test_null_snow_keeps_critical_wind():
    r=assess(component(value=100),address='广东省广州市')
    assert r['status']=='PARTIAL' and r['risk_level']=='critical'
    assert r['requires_manual_review'] is True
    assert r['snow']['risk_level']=='unknown' and 'load_pa' not in r['snow']

@pytest.mark.parametrize('ratio,expected',[(.8,'low'),(.81,'medium'),(1,'medium'),(1.01,'high'),(1/.75,'high'),(1.34,'critical')])
def test_grade_boundaries(ratio,expected): assert grade(ratio,1.25,.75)==expected

def test_geometry_interval_crosses_grade():
    p=params();p['ai_geometry']['tilt_deg'].update(min=10,max=20)
    r=assess_unified(case_id='x',model='TEST-5400',address='北京市',component=component(700),screening_input=p)
    assert r['wind']['uncertainty_crosses_grade'];assert r['wind']['risk_level']=='high'

def test_unknown_height_and_missing_geometry():
    p=params();p['ai_geometry']['height_m'].update(min=8,max=8)
    r=assess_unified(case_id='x',model='TEST-5400',address='北京市',component=component(),screening_input=p)
    assert r['wind']['risk_level']=='unknown';assert r['snow']['load_pa']['max']==250

def test_report_has_units_source_grade_not_probability():
    text=report(assess());assert '637.5' in text and '5400.0' in text and '10年' in text and '阈值待业务确认' in text

def test_api_and_merge_versions(tmp_path,monkeypatch):
    monkeypatch.setenv('PV_AUTH_ENABLED','false');monkeypatch.setenv('PV_CASE_DB_PATH',str(tmp_path/'cases.sqlite3'))
    monkeypatch.setattr(unified_risk,'profile',lambda model:component())
    client=TestClient(app);body={'model':'TEST-5400','address':'北京市朝阳区','screening_input':params()}
    r=client.post('/api/v1/risk/unified/assess',json=body);assert r.status_code==200,r.text
    assert r.json()['assessment']['wind']['load_pa']['max']==637.5
    case=UnderwritingCase(schema_version='0.1.0',case_id='ORIGINAL',project=ProjectInfo(component_model='TEST-5400',site_address='北京市朝阳区',project_name='统一风雪测试',insured_name='测试企业',project_type='rooftop',installation_type='color_steel_roof',longitude=None,latitude=None,proposed_start_date=None),materials=[],ocr_fields=[],findings=[],material_reviews=[],processing_trace=[])
    repo=CaseRepository();repo.save_case(case)
    repo.submit_review(case_id='ORIGINAL',reviewer_name='业务测试',final_decision='manual_review',comment='保留原复核')
    r=client.post('/api/v1/risk/unified/merge-report',json={**body,'case_id':'ORIGINAL'})
    assert r.status_code==200,r.text
    data=r.json();assert data['case']['case_id']!='ORIGINAL';assert data['parent_case_id']=='ORIGINAL'
    assert '风雪风险量化' in data['report_markdown'] and '637.5' in data['report_markdown']
    old=repo.get_case('ORIGINAL');assert old['review_status']=='completed' and len(old['review_history'])==1
    assert repo.get_case(data['case']['case_id'])['case']['pv_load_screening_assessment']['model_version'].startswith('city-wind-snow')
    bad=client.post('/api/v1/risk/unified/merge-report',json={**body,'address':'上海市','case_id':'ORIGINAL'})
    assert bad.status_code==409

def test_pipeline_uses_same_engine_and_report():
    class Provider:
        name='fixture'
        def lookup(self,model):return component()
    pipeline=UnderwritingPipeline(ocr_provider=MockOcrProvider(),vision_provider=MockVisionProvider(),component_provider=Provider())
    project=ProjectInfo(component_model='TEST-5400',site_address='北京市朝阳区',project_name='统一风雪测试',insured_name='测试企业',project_type='rooftop',installation_type='color_steel_roof',longitude=None,latitude=None,proposed_start_date=None)
    result, trace=pipeline._run_front_load_screening(case_id='pipeline-test',project=project,component_profile=component(),screening_input=params())
    assert result['wind']['load_pa']['max']==637.5
    assert trace.step.value=='front_load_screening'
    case=UnderwritingCase(schema_version='0.1.0',case_id='pipeline-test',project=project,materials=[],ocr_fields=[],findings=[],material_reviews=[],processing_trace=[trace],pv_load_screening_assessment=result)
    text=generate_markdown_report(case);assert '风雪风险量化报告' in text and '637.5' in text


def test_legacy_page_redirect():
    r=TestClient(app).get('/pv-load-screening',follow_redirects=False)
    assert r.status_code==307 and r.headers['location']=='/risk-lab'


def test_actual_staged_pipeline_hands_component_to_wind_snow_and_report():
    from unit.test_staged_pipeline import (
        CompleteFilingOcr,
        CompleteVision,
        certificate,
        image,
        project,
    )
    class Provider:
        name='fixture-workbench'
        def lookup(self, model): return component(model=model)
    class ForbiddenWeather:
        name='must-not-call'
        def lookup(self, **kwargs):
            raise AssertionError('整单不得调用历史气象接口')
    pipeline=UnderwritingPipeline(ocr_provider=CompleteFilingOcr(),vision_provider=CompleteVision(),component_provider=Provider(),weather_provider=ForbiddenWeather())
    case=pipeline.run(case_id='staged-integration',project=project(site_address='北京市朝阳区',component_model='TEST-5400'),materials=[certificate(),image('roof')],pv_load_screening_input=params())
    assert case.pre_catastrophe_outcome=='continue'
    assert case.catastrophe_assessment is None
    assert case.weather_profile is None
    weather_trace = next(t for t in case.processing_trace if t.step.value == 'weather_lookup')
    assert weather_trace.status.value == 'skipped'
    assert '全国城市' in weather_trace.detail
    assert case.pv_load_screening_assessment['wind']['load_pa']['max']==637.5
    assert '637.5' in generate_markdown_report(case)
    assert any(t.step.value=='front_load_screening' for t in case.processing_trace)


def test_delivery_golden_example_is_unchanged():
    import json
    from pathlib import Path
    root=Path(__file__).resolve().parents[2]/'examples/wind-snow'
    payload=json.loads((root/'01_assess_request.json').read_text('utf-8'))
    c=ComponentProfile.model_validate(json.loads((root/'02_component_fixture.json').read_text('utf-8')))
    expected=json.loads((root/'04_expected_assessment.json').read_text('utf-8'))
    actual=assess_unified(case_id=payload['project_id'],model=payload['model'],address=payload['address'],component=c,screening_input=payload['screening_input'])
    for key in ['wind','snow','status','risk_level','thresholds','location_match','component_capacities']:
        assert actual[key]==expected[key], key


def test_hail_does_not_affect_wind_snow_rating_or_completeness():
    absent = assess(component())
    old = component()
    old.hail_resistance_mm = 1
    old.parameter_evidence['hail_resistance_mm'] = [{'status': 'conflict'}]
    retained = assess(old)
    assert absent['data_complete'] and retained['data_complete']
    for key in ('risk_level', 'status', 'wind', 'snow', 'actions'):
        assert absent[key] == retained[key]
    assert set(retained['component_capacities']) == {'wind', 'snow'}
