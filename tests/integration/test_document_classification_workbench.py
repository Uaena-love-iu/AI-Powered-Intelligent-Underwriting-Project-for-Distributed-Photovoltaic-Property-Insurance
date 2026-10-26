import json
from io import BytesIO
from unittest.mock import patch

import httpx
import pymupdf
from app.contracts import Material, MaterialCategory, OcrField
from app.main import app
from app.material_catalog import MATERIAL_CATALOG
from app.pipeline.core import UnderwritingPipeline
from app.providers import MaterialInput
from app.providers.vision.classification import GUIDE, classify_image
from app.rules.engine import RuleEngine
from app.settings import VlmSettings
from fastapi.testclient import TestClient
from PIL import Image

client=TestClient(app)

def photo():
    out=BytesIO();Image.new('RGB',(600,600),'white').save(out,format='PNG');return out.getvalue()

def pdf_document():
    document=pymupdf.open()
    page=document.new_page()
    page.insert_text((72,72),'Project filing certificate')
    content=document.tobytes()
    document.close()
    return content

def test_pdf_classification_renders_page_and_reaches_document_routing():
    settings=VlmSettings('https://example.com','secret','test')
    def classified(page, actual_settings):
        assert page.startswith(b'\x89PNG')
        assert actual_settings.model == settings.model
        return {'category':'project_document','suggested_category':'project_document',
                'confidence':.91,'reason':'可见备案文件标题','candidates':[],
                'requires_confirmation':True,'model':'test','capture_view':'unknown'}
    with patch('app.api.vision_lab.VlmSettings.from_environment',return_value=settings),patch(
        'app.providers.vision.classification.classify_image',side_effect=classified
    ):
        response=client.post('/api/v1/vision/lab/classify',files={
            'file':('车棚顶.pdf',pdf_document(),'application/pdf')})
    assert response.status_code==200,response.text
    assert response.json()['category']=='project_document'
    assert response.json()['reason'].startswith('第 1 页：')

def field(name,value,confidence=.9,status='extracted',filing_assessment=None):
    return OcrField(field_id=name,material_id='doc',field_name=name,raw_value=value,normalized_value=value,
                    value_status=status,confidence=confidence,provider='test',model='test',
                    filing_assessment=filing_assessment)

def material():
    return Material(material_id='doc',category='project_document',file_name='doc.png',media_type='image/png',
                    quality_status='usable',quality_issues=[],parse_status='success')

def test_shared_catalog_has_one_grouped_document_type_and_all_enum_values():
    config=client.get('/api/v1/vision/lab/config').json()
    assert set(config['categories'])==set(GUIDE)==set(MATERIAL_CATALOG)
    assert 'project_document' in GUIDE and 'equipment_inventory' in GUIDE
    assert 'filing_certificate' not in GUIDE and 'grid_connection_document' not in GUIDE
    assert set(MATERIAL_CATALOG)|{'filing_certificate','grid_connection_document'}=={c.value for c in MaterialCategory}
    assert config['document_specialized_prompts']['project_document']
    assert config['document_specialized_prompts']['equipment_inventory']

def test_classification_accepts_certificate_and_inventory_photos():
    for category in ['project_document','equipment_inventory']:
        def handler(request, expected_category=category):
            prompt=json.loads(request.content)['messages'][0]['content']
            assert '不得因是文件照就归 other' in prompt
            return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps({'category':expected_category,'confidence':.9,'reason':'清晰可读标题','alternatives':[]})}}]})
        with patch('app.providers.vision.classification.httpx.Client',return_value=httpx.Client(transport=httpx.MockTransport(handler))):
            assert classify_image(photo(),VlmSettings('https://example.com','secret','test'))['category']==category

def test_permit_does_not_satisfy_filing_requirement():
    for subtype,expected in [('filing_certificate',True),('grid_connection_permit',False),('dispatch_agreement',False),('unknown',False)]:
        fields=[field('document_type',subtype)]
        result=UnderwritingPipeline._apply_ocr_metadata([MaterialInput(material=material(),content=photo())],fields)
        package=RuleEngine().assess_package([result[0].material])
        assert package.has_filing_certificate is expected
    uncertain=UnderwritingPipeline._apply_ocr_metadata([MaterialInput(material=material(),content=photo())],[field('document_type','filing_certificate',.4)])
    assert uncertain[0].material.category is MaterialCategory.PROJECT_DOCUMENT

def test_document_lab_routes_to_ocr_and_rules_without_risk_provider():
    fields=[field('document_type','filing_certificate'),field('project_name','渔光互补项目',filing_assessment={
        'status':'confirmed','prohibition_type':'fishery_pv','reason':'渔业与光伏复合利用','evidence_quote':'渔光',
    }),field('site_address','示例地址'),field('insured_name','示例企业')]
    settings=VlmSettings('https://example.com','secret','test')
    def extract(provider,input):
        assert '文档提取测试标记' in provider._system_prompt
        assert '专属测试标记' in provider._system_prompt
        return [f.model_copy(update={"material_id":input.material.material_id}) for f in fields]
    with patch('app.api.vision_lab.VlmSettings.from_environment',return_value=settings),patch('app.api.vision_lab.CompatibleOcrProvider.extract',extract),patch('app.api.vision_lab.CompatibleVisionProvider.analyze_with_watermark') as vision:
        response=client.post('/api/v1/vision/lab/document/analyze',files={'file':('doc.png',photo(),'image/png')},data={'category':'project_document','prompt':'文档提取测试标记：只读取清晰可见的原文，不补充、不推断，严格返回约定字段。','specialized_prompt':'专属测试标记：提取项目证照信息'})
    assert response.status_code==200,response.text
    data=response.json();assert data['pipeline']=='document' and data['category']=='project_document'
    assert data['resolved_category']=='filing_certificate'
    assert not data['findings'] and len(data['ocr_fields'])==4
    assert data['material_reviews'][0]['action']=='recommend_reject'
    vision.assert_not_called()
    assert 'secret' not in response.text

def test_document_prompt_promotion_preserves_photo_templates(tmp_path):
    path=tmp_path/'prompts.json'
    with patch('app.api.vision_lab.PROMPT_DEFAULT_PATH',path):
        config=client.get('/api/v1/vision/lab/config').json()
        response=client.post('/api/v1/vision/lab/default',json={'prompt':config['prompt'],'document_prompt':config['document_prompt']+'\n文档修改标记','document_specialized_prompts':{'project_document':'文档专属修改标记'}})
        assert response.status_code==200
        after=client.get('/api/v1/vision/lab/config').json()
    assert '文档修改标记' in after['document_prompt']
    assert after['document_specialized_prompts']['project_document']=='文档专属修改标记'
    assert after['specialized_prompts']['panorama']==config['specialized_prompts']['panorama']
