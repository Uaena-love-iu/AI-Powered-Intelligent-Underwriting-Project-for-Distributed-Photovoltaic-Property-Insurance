import copy
import json
from hashlib import sha256

import pytest
from fastapi.testclient import TestClient

from app.api.component_dependencies import get_underwriting_component_provider
from app.api.ocr_routes import get_ocr_provider
from app.api.routes import get_vision_provider
from app.contracts.component_review import CaseComponentReview
from app.main import app
from app.providers import MockOcrProvider, MockVisionProvider, ProviderError
from app.providers.case_component import CaseComponentProvider, prepare_review
from app.storage import CaseRepository
from app.storage.verified_components import VerifiedComponentRepository
from integration.test_revision_api import done
from integration.test_unified_wind_snow import component, params


def png(color):
    from io import BytesIO
    from PIL import Image
    out=BytesIO();Image.new('RGB',(1200,900),color).save(out,format='PNG');return out.getvalue()


class CompleteOcr(MockOcrProvider):
    def extract(self,item):
        from app.contracts import OcrField
        fields=super().extract(item)
        for key,value in [('rated_power_w','600'),('serial_number','TEST-ONLY-001')]:
            fields.append(OcrField(field_id=item.material.material_id+':'+key,material_id=item.material.material_id,
                field_name=key,raw_value=value,normalized_value=value,value_status='extracted',confidence=.99,
                provider=self.name,model=self.model_name,evidence_text='模拟清晰铭牌字段'))
        return fields


class ClearVision(MockVisionProvider):
    def analyze(self,item):
        from app.contracts import RiskFinding
        return [RiskFinding(finding_id=item.material.material_id+':quality',material_id=item.material.material_id,
            category='image_quality',label='图像清晰',detection_status='not_detected',severity='low',confidence=.99,
            provider=self.name,model=self.model_name,evidence_text='模拟清晰照片',requires_manual_review=False)]


def payload(value=5.4):
    return dict(scope='case_only',component_model='TEST-5400',manufacturer='测试组件厂商',
        identity_material_id='plate',identity_sha256=sha256(png('white')).hexdigest(),
        model_reason='铭牌完整型号及后缀经人工核对',reviewer_name='模拟核验员',reason='根据厂家规格书补充并解决原参数冲突',confirmed=True,
        parameters={'front_static_load_pa':dict(value=value,unit='kPa',direction='front',load_basis='test',
            document_title='厂家规格书',document_version='2026-10版',applicability='四点夹具安装，位置须按原文核对',
            quote='正面测试载荷 5.4 kPa',page='第2页',source_material_id='spec',
            source_sha256=sha256(png('blue')).hexdigest(),installation_confirmed=False)})


class Automatic:
    name='test-component'
    def __init__(self,fail=False):
        self.fail,self.calls,self.last_result=fail,0,{}
    def lookup(self,model):
        self.calls+=1
        if self.fail:raise ProviderError('synthetic unavailable')
        profile=component(2000,model)
        records=[dict(status='conflict',value=2000,source_url='https://example.com/old.pdf',quote='old version'),
                 dict(status='conflict',value=2400,source_url='https://example.com/other.pdf',quote='other version')]
        profile.front_static_load_pa=None
        profile.parameter_evidence['front_static_load_pa']=records
        self.last_result=dict(status='conflict',profile=dict(component_model=model,model_match_status='exact',
            front_static_load_pa=None,parameter_evidence={'front_static_load_pa':records},ui_fields={},mechanical_fields={}))
        profile.workbench_result=self.last_result
        return profile


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv('PV_AUTH_ENABLED','false');monkeypatch.setenv('PV_AMAP_WEB_SERVICE_KEY','')
    old=dict(app.dependency_overrides)
    app.dependency_overrides[get_ocr_provider]=lambda:CompleteOcr()
    app.dependency_overrides[get_vision_provider]=lambda:ClearVision()
    app.dependency_overrides[get_underwriting_component_provider]=lambda:Automatic()
    with TestClient(app) as c:yield c
    app.dependency_overrides.clear();app.dependency_overrides.update(old)


def submit(client,id_,review=None,parent=None,source_content=None,rules=None,model='TEST-5400'):
    manifest=dict(case_id=id_,project=dict(project_name=None,insured_name=None,project_type='unknown',installation_type='unknown',
        site_address=None,longitude=None,latitude=None,proposed_start_date=None,component_model=model),
        rule_selection=dict(selected_rule_ids=rules or ['component']),component_review=review,
        materials=[dict(material_id='plate',category='component_nameplate'),dict(material_id='spec',category='other',source_only=True)])
    if parent:manifest['revision']=dict(parent_case_id=parent,actor='模拟核验员',reason='核对新版本后修正参数')
    return client.post('/api/v1/underwriting/jobs',data={'manifest':json.dumps(manifest)},
        files=[('files',('nameplate.png',png('white'),'image/png')),('files',('厂家规格书.png',source_content or png('blue'),'image/png'))])


def test_case_only_evidence_originals_and_new_round_calculation(client):
    first=done(client,submit(client,'manual-first',payload()))
    assert first['component_review']['applied'],first['processing_trace']
    p=first['component_profile'];assert p['front_static_load_pa']==5400
    assert p['workbench_result']['case_parameter_review']['automatic_result']['status']=='conflict'
    assert p['parameter_evidence']['front_static_load_pa']['records'][0]['status']=='found'
    assert first['original_ocr_fields'][0]['normalized_value']=='JAM66D42-580/MB'
    assert next(f for f in first['ocr_fields'] if f['field_name']=='component_model')['normalized_value']=='TEST-5400'
    assert first['component_source_material_ids']==['spec']
    assert all(f['material_id']!='spec' for f in first['ocr_fields'])
    assert all(r['material_id']!='spec' for r in first['material_reviews'])
    assert client.get('/api/v1/cases/manual-first/materials/spec/source').content==png('blue')
    assert VerifiedComponentRepository().list()==[]
    assert CaseRepository().find_verified_component_for_file(sha256(png('white')).hexdigest()) is None
    changed=payload(.5);changed['parameters']['front_static_load_pa']['quote']='新版本正面测试载荷 0.5 kPa'
    second=done(client,submit(client,'manual-second',changed,'manual-first'))
    assert second['component_profile']['front_static_load_pa']==500
    assert second['component_review']['original_profile']['front_static_load_pa']==5400
    assert client.get('/api/v1/cases/manual-first').json()['case']['component_profile']['front_static_load_pa']==5400
    rounds=client.get('/api/v1/cases/manual-second/rounds').json()['rounds']
    assert rounds[-1]['comparison']['component_parameters']==[{'field':'front_static_load_pa','before':5400.,'after':500.}]
    from app.contracts import ComponentProfile
    from app.catastrophe.front_load_screening.unified import assess_unified
    def calculate(data):
        return assess_unified(case_id=data['case_id'],model='TEST-5400',address='北京市朝阳区',
                              component=ComponentProfile.model_validate(data['component_profile']),screening_input=params())
    old,new=calculate(first),calculate(second)
    assert old['wind']['capacity_pa']==5400 and new['wind']['capacity_pa']==500
    assert new['wind']['comparison_ratio']['max']>old['wind']['comparison_ratio']['max']
    assert old['requires_manual_review'] and new['requires_manual_review']
    text=client.get('/api/v1/cases/manual-second/report').text
    assert '仅本案' in text and '0.5 kPa → 500.0 Pa' in text and '2026-10版' in text
    assert '已在本轮组件查询中采用' in text


@pytest.mark.parametrize('change',[
    {'confirmed':False},{'scope':'shared'},{'reviewer_name':' '},{'component_model':'TEST-*'},
])
def test_incomplete_confirmation_never_starts_job(client,change):
    data=payload();data.update(change)
    assert submit(client,'invalid',data).status_code==422
    assert CaseRepository().get_case('invalid') is None


@pytest.mark.parametrize('change',[
    {'value':0},{'value':True},{'unit':'W'},{'direction':'back'},
    {'quote':' '},{'document_version':''},{'applicability':''},
    {'source_material_id':None,'source_sha256':None},
    {'source_material_id':'missing'}, {'source_sha256':'0'*64},
    {'source_url':'http://example.com/spec.pdf'},
])
def test_invalid_parameter_units_or_evidence_rejected(client,change):
    data=payload();data['parameters']['front_static_load_pa'].update(change)
    assert submit(client,'invalid-parameter',data).status_code==422


def test_changed_files_and_model_require_new_confirmation(client):
    assert submit(client,'replaced',payload(),source_content=png('red')).status_code==422
    assert submit(client,'model-mismatch',payload(),model='TEST-5400B').status_code==422
    data=payload();data['identity_sha256']='0'*64
    assert submit(client,'identity-replaced',data).status_code==422


def test_unselected_component_keeps_manual_values_unapplied(client):
    case=done(client,submit(client,'not-selected',payload(),rules=['surface']))
    assert not case['component_review']['applied'] and case['component_profile'] is None
    assert '本轮未执行组件步骤' in client.get('/api/v1/cases/not-selected/report').text
    assert VerifiedComponentRepository().list()==[]


def test_failed_automatic_lookup_can_use_confirmed_evidence_without_masking_failure(client):
    app.dependency_overrides[get_underwriting_component_provider]=lambda:Automatic(fail=True)
    case=done(client,submit(client,'failed-auto',payload()))
    assert case['component_profile']['front_static_load_pa']==5400
    assert case['component_review']['automatic_result']['status']=='source_unavailable'
    assert case['component_profile']['wind_load_pa'] is None and case['component_profile']['snow_load_pa'] is None


def test_case_parameters_never_leak_to_next_case(client):
    first=done(client,submit(client,'private',payload()))
    other=done(client,submit(client,'other-case',model='JAM66D42-580/MB'))
    assert other['component_profile']['front_static_load_pa'] is None
    assert other['component_review']=={}
    assert VerifiedComponentRepository().list()==[]


def test_different_back_direction_cannot_replace_front_comparison(client):
    data=payload();p=data['parameters'].pop('front_static_load_pa');p.update(direction='back',value=20)
    data['parameters']['back_static_load_pa']=p
    case=done(client,submit(client,'back-only',data))
    from app.catastrophe.front_load_screening.unified import capacities
    from app.contracts import ComponentProfile
    assert case['component_profile']['back_static_load_pa']==20000
    assert capacities(ComponentProfile.model_validate(case['component_profile']))['wind']['value_pa'] is None


def test_failed_ocr_keeps_only_explicitly_confirmed_fields(client):
    class FailedOcr(MockOcrProvider):
        def extract(self,item):raise ProviderError('synthetic OCR outage')
    app.dependency_overrides[get_ocr_provider]=lambda:FailedOcr()
    data=payload();power=copy.deepcopy(data['parameters']['front_static_load_pa'])
    power.update(value=.6,unit='kW',direction='not_applicable',load_basis='unspecified',quote='额定功率 600 W')
    data['parameters']['rated_power_w']=power
    case=done(client,submit(client,'ocr-outage',data))
    fields={f['field_name']:f for f in case['ocr_fields']}
    assert fields['component_model']['normalized_value']=='TEST-5400'
    assert fields['rated_power_w']['normalized_value']=='600.0'
    assert 'serial_number' not in fields
    assert case['original_ocr_fields']==[] and case['component_review']['ocr_failures']
    assert VerifiedComponentRepository().list()==[]


def test_unconfirmed_source_attachments_cannot_replace_site_evidence(client):
    data=payload()
    response=submit(client,'site-missing',data,rules=['installation','component'])
    case=done(client,response)
    assert not case['component_review']['applied']
    assert case['component_profile'] is None
    assert case['decision']['decision']!='accept'


def test_mismatched_automatic_model_is_preserved_but_not_merged(client):
    class Mismatched(Automatic):
        def lookup(self, model):
            profile=super().lookup(model+'B')
            self.last_result['profile']['rated_power_w']=999
            profile.rated_power_w=999
            return profile
    app.dependency_overrides[get_underwriting_component_provider]=lambda:Mismatched()
    case=done(client,submit(client,'mismatch-source',payload()))
    assert case['component_profile']['front_static_load_pa']==5400
    assert case['component_profile']['rated_power_w'] is None
    assert case['component_module_result']['profile'].get('rated_power_w') is None
    assert case['component_review']['automatic_profile']['component_model']=='TEST-5400B'


def test_old_mechanical_direction_mappings_do_not_survive_new_manual_values(client):
    class Mapped(Automatic):
        def lookup(self, model):
            profile=super().lookup(model)
            profile.wind_load_pa=2000
            self.last_result['profile']['ui_fields']['wind_load_pa']={'value':2000,'value_origin':'mechanical_mapping'}
            return profile
    app.dependency_overrides[get_underwriting_component_provider]=lambda:Mapped()
    case=done(client,submit(client,'old-mapping',payload()))
    assert case['component_profile']['front_static_load_pa']==5400
    assert case['component_profile']['wind_load_pa'] is None
    assert 'wind_load_pa' not in case['component_module_result']['profile']['ui_fields']
    assert case['component_review']['automatic_profile']['wind_load_pa']==2000
