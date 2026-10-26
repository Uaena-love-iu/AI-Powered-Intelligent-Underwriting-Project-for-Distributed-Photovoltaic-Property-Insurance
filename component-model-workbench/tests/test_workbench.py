import copy, json, shutil, socket
from pathlib import Path
from datetime import datetime, UTC
import pytest
import httpx
from pv_component_lookup.contracts import ComponentProfile, ProviderError
from workflow import Workbench, merge_profiles, canonical_brand
from recognition import text_candidates
from agent import PublicFetcher, DashscopeSearch, scope_proof, ComponentSearchAgent
from pv_component_lookup.datasheet import DatasheetExtraction, extract_page
from pv_component_lookup.naming import model_key

ROOT=Path(__file__).resolve().parents[1]
@pytest.fixture
def bench(tmp_path):
    shutil.copytree(ROOT/'data',tmp_path/'data')
    return Workbench(tmp_path)
def profile(value=2400,status='exact'):
    return ComponentProfile(component_model='JAM66D42-605/MB',manufacturer='JA Solar',source_name='fixture',retrieved_at=datetime.now(UTC),match_confidence=1,model_match_status=status,wind_load_pa=value if status=='exact' else None,parameter_evidence={'wind_load_pa':[{'status':'found','value':value,'source_url':'https://www.jasolar.com/example.pdf','load_basis':'test'}]} if status=='exact' else {})

def test_rule_counts_complete(bench):
    assert bench.status()['rule_count']==2066
    assert bench.status()['manufacturer_count']==150
    assert bench.status()['local_rows']==365

def test_unknown_power_keeps_variant_and_missing(bench):
    result=bench.run('JAM66D42-715/MB',strategy='local')
    assert result['profile'] is None
    assert result['identity']['rules'][0]['input_power_w']==715
    assert '/MB' in result['identity']['rules'][0]['template']

def test_local_static_loads_are_reference(bench):
    result=bench.run('LR5-54HTB-440M',strategy='local')
    assert result['profile']['wind_load_pa'] is None
    assert result['profile']['ui_fields']['wind_load_pa']['reference_value']==2400
    assert not result['profile']['ui_fields']['wind_load_pa']['can_use_as_design_capacity']
    assert result['profile']['mechanical_fields']['front_static_load_pa']['value']==5400
    assert result['profile']['mechanical_fields']['front_static_load_pa']['status']=='found'

def test_range_expanded_local_is_only_candidate(bench):
    row=next(x for x in bench.catalog['records'] if not x['auto_match_eligible'])
    result=bench.run(row['component_model'],strategy='local')
    assert result['local_candidates']
    assert result['profile'] is None

def test_canonical_brand_real_solar_names(bench):
    assert canonical_brand('晶科 JinkoSolar',bench.brands)=='JinkoSolar'
    assert canonical_brand('东方日升 Risen',bench.brands)=='Risen'

def test_sources_conflict_never_take_larger():
    p=merge_profiles(profile(2400),profile(4000))
    assert p.wind_load_pa is None
    assert all(x['status']=='conflict' for x in p.parameter_evidence['wind_load_pa'])
    assert merge_profiles(p,profile(2400)).wind_load_pa is None

def test_series_profile_never_supplies_parameters():
    p=merge_profiles(None,profile(status='series_only'))
    assert p.wind_load_pa is None

def test_api_error_triggers_real_agent_contract(bench):
    class Bad:
        def lookup(self,model):raise ProviderError('offline')
    class Agent:
        def run(self,*args,**kwargs):return {'status':'not_found','profile':None,'sources':[],'reason':'finite search finished'}
    bench.provider_factory=lambda index:Bad();bench.agent_factory=lambda:Agent()
    r=bench.run('JAM66D42-715/MB')
    assert any(x['stage']=='api' and x['state']=='unavailable' for x in r['steps'])
    assert r['agent']['status']=='not_found'
    assert r['errors']

def test_agent_source_unavailable_is_not_reported_as_product_missing(bench):
    class Agent:
        def run(self,*args,**kwargs):return {'status':'source_unavailable','profile':None,'sources':[],'reason':'official fetch failed'}
    bench.agent_factory=lambda:Agent()
    r=bench.run('JAM66D42-715/MB',strategy='agent')
    assert r['status']=='source_unavailable' and r['profile'] is None

def test_agent_promoted_only_on_exact_identity(bench):
    class API:
        def lookup(self,model):return profile(status='series_only')
    class Agent:
        def run(self,*a,**kw):return {'status':'evidence_found','profile':profile(2400),'sources':[]}
    bench.provider_factory=lambda index:API();bench.agent_factory=lambda:Agent()
    r=bench.run('JAM66D42-605/MB')
    assert r['profile']['model_match_status']=='exact'
    assert r['profile']['wind_load_pa']==2400

def test_review_cannot_approve_unknown_identity(bench):
    r=bench.run('JAM66D42-715/MB',strategy='local')
    with pytest.raises(ValueError):bench.store.feedback(r['id'],'verified','checked original',['wind_load_pa'])

def test_manual_review_selected_fields_only(bench):
    r=bench.run('LR5-54HTB-440M',strategy='local')
    bench.store.feedback(r['id'],'verified','核对厂家参数与来源',['back_static_load_pa'])
    saved=bench.store.cached('LR5-54HTB-440M')
    assert saved['back_static_load_pa']==r['profile']['back_static_load_pa']
    assert saved['front_static_load_pa'] is None
    assert bench.run('LR5-54HTB-440M',strategy='local')['profile']['source_name']=='人工核验本地缓存'

def test_old_agent_personal_evidence_cannot_be_approved(bench):
    r=bench.run('LR5-54HTB-440M',strategy='local')
    for e in r['profile']['parameter_evidence']['back_static_load_pa']:
        e.update(source_kind='agent_webpage',source_url='https://www.zhihu.com/question/test')
    bench.store.save(r)
    with pytest.raises(ValueError,match='个人或未核验来源'):
        bench.store.feedback(r['id'],'verified','测试不能放行个人来源',['back_static_load_pa'])

def test_ocr_power_not_model_and_suffix_preserved(bench):
    candidates=text_candidates('Model: JAM66D42-605/MB Power 605Wp Serial: 123456789',bench.rules_index)
    assert [c['raw_model'] for c in candidates]==['JAM66D42-605/MB']

def test_ocr_punctuation_spacing_normalizes_without_replacing_characters(bench):
    raw='JAM66D42 -605 / MB'
    assert model_key(raw)=='jam66d42-605/mb'
    r=bench.run(raw,strategy='local')
    assert r['input']['raw_model']==raw
    assert r['identity']['rule_count']==1
    assert model_key('JAM66D42-6O5/MB')!='jam66d42-605/mb'

@pytest.mark.parametrize('url',['http://www.jasolar.com/x.pdf','https://127.0.0.1/x','https://user:pass@www.jasolar.com/x','https://example.com:8840/x'])
def test_agent_blocks_private_or_nonhttps(url):
    fetch=PublicFetcher(resolver=lambda *a,**kw:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))])
    with pytest.raises(ProviderError):fetch.validate(url)

def test_agent_rejects_dimension_as_power(bench):
    fs=bench.rules_index.families('JAM66D42-715/MB')
    assert scope_proof('JAM66D42 MB Dimensions 715 mm. Maximum Power 580 585 590 595 600 605 W', 'JAM66D42-715/MB',fs) is None

def test_search_does_not_trust_llm_links_without_search_info():
    class Reader:base_url='https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1';model='test';_key='fixture'
    body={'output':{'choices':[{'message':{'content':[{'text':'https://invented.example/spec.pdf'}]}}]}}
    search=DashscopeSearch(Reader(),transport=httpx.MockTransport(lambda r:httpx.Response(200,text='data: '+json.dumps(body)+'\n\n')))
    with pytest.raises(ProviderError):search.search('find module')

def test_actual_search_source_is_retained():
    class Reader:base_url='https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1';model='test';_key='fixture'
    body={'output':{'search_info':{'search_results':[{'url':'https://www.jasolar.com/file.pdf','title':'source'}]}},'usage':{'plugins':{'search':{'count':1}}}}
    search=DashscopeSearch(Reader(),transport=httpx.MockTransport(lambda r:httpx.Response(200,text='data: '+json.dumps(body)+'\n\n')))
    assert search.search('find module')[0]['source_kind']=='search_info'
    assert search.last_usage['plugins']['search']['count']==1

def test_explicit_front_snow_back_wind_direct_parameters():
    r=DatasheetExtraction()
    extract_page('Test Load Pressure: 5400Pa Max from the front side (snow) and 2400Pa from the rear (wind).',1,'https://example.com/manual.pdf',r)
    assert r.values()['snow_load_pa']==5400
    assert r.values()['wind_load_pa']==2400
    assert r.evidence['snow_load_pa'][0]['load_direction']=='front'
    assert r.evidence['wind_load_pa'][0]['load_direction']=='back'
    assert r.evidence['snow_load_pa'][0]['load_basis']=='test'
    assert r.evidence['snow_load_pa'][0]['hazard_mapping']=='manufacturer_explicit'

def test_direction_only_is_not_automatically_wind_snow():
    r=DatasheetExtraction()
    extract_page('Mechanical load front/rear: 5400/2400 Pa',1,'https://example.com/data.pdf',r)
    assert r.values()['front_static_load_pa']==5400
    assert 'snow_load_pa' not in r.values()
    assert 'wind_load_pa' not in r.values()

def test_explicit_mapping_retains_disagreeing_installation_values():
    r=DatasheetExtraction()
    extract_page('Front mechanical load (snow): 5400 Pa\nFront mechanical load (snow): 2400 Pa',1,'https://example.com/manual.pdf',r)
    assert 'snow_load_pa' not in r.values()
    assert all(x['status']=='conflict' for x in r.evidence['snow_load_pa'])
