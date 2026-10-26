from io import BytesIO
from unittest.mock import patch

from app.main import app
from app.providers.vision.base import VisionAnalysis
from app.settings import VlmSettings
from fastapi.testclient import TestClient
from PIL import Image

client = TestClient(app)


def photo():
    out = BytesIO()
    Image.new('RGB', (640, 640), 'white').save(out, format='PNG')
    return out.getvalue()


def test_lab_preserves_custom_prompt_and_watermark_evidence():
    settings = VlmSettings(base_url='https://example.com', api_key='test-secret', model='test-model')
    seen = []
    def run(provider, material):
        seen.append((provider._system_prompt, material.material.category.value))
        return VisionAnalysis(findings=[], watermark_present=True,
                              watermark_bbox={'x_min': .1, 'y_min': .8, 'x_max': .4,
                                              'y_max': .9, 'coordinate_space': 'normalized_0_1'},
                              watermark_evidence='底部存在日期水印')
    prompt = '只依据可见证据识别风险，并使用约定 JSON 输出结构。不得猜测图外信息。'
    with patch('app.api.vision_lab.VlmSettings.from_environment', return_value=settings), patch(
        'app.api.vision_lab.CompatibleVisionProvider.analyze_with_watermark', run
    ):
        response = client.post('/api/v1/vision/lab/analyze',
                               files={'file': ('test.png', photo(), 'image/png')},
                               data={'category': 'panorama', 'prompt': prompt})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['actual_prompt'].startswith(prompt)
    assert seen == [(result['actual_prompt'], 'panorama')]
    assert result['watermark_bbox']['y_min'] == .8
    assert result['watermark_evidence'] == '底部存在日期水印'
    assert result['model'] == 'test-model'
    assert 'test-secret' not in response.text
    assert result['image_hash'] and 'agriculture_environment' in result['expected_checks']


def test_lab_rejects_document_and_invalid_image_before_model_call():
    with patch('app.api.vision_lab.CompatibleVisionProvider') as provider:
        for category in ['filing_certificate', 'panorama']:
            response = client.post('/api/v1/vision/lab/analyze',
                                   files={'file': ('test.png', b'not-image', 'image/png')},
                                   data={'category': category, 'prompt': 'a' * 40})
            assert response.status_code == 422
        provider.assert_not_called()


def test_prompt_promotion_is_explicit_and_persistent(tmp_path):
    path = tmp_path / 'default.json'
    with patch('app.api.vision_lab.PROMPT_DEFAULT_PATH', path), patch(
        'app.providers.vision.compatible.PROMPT_DEFAULT_PATH', path
    ):
        before = client.get('/api/v1/vision/lab/config').json()['prompt']
        assert not path.exists()
        prompt = before + '\n测试变体'
        assert client.post('/api/v1/vision/lab/default', json={'prompt': prompt}).status_code == 200
        assert client.get('/api/v1/vision/lab/config').json()['prompt'] == prompt


def test_lab_page_and_script_are_served():
    assert '看见风险，也看清依据' in client.get('/vision-lab').text
    assert client.get('/vision-lab.js').status_code == 200


def test_composed_lab_prompt_contains_only_selected_material_instructions():
    from app.contracts import MaterialCategory
    from app.prompts.vision import compose_prompt
    settings = VlmSettings(base_url='https://example.com', api_key='test', model='test-model')
    config = client.get('/api/v1/vision/lab/config').json()
    common = config['prompt'] + '\n公共编辑标记'
    specialty = config['specialized_prompts']['workshop'] + '\n车间编辑标记'
    seen = []
    def run(provider, material):
        seen.append(provider._system_prompt)
        return VisionAnalysis(findings=[])
    with patch('app.api.vision_lab.VlmSettings.from_environment', return_value=settings), patch(
        'app.api.vision_lab.CompatibleVisionProvider.analyze_with_watermark', run
    ):
        response = client.post('/api/v1/vision/lab/analyze',
            files={'file': ('test.png', photo(), 'image/png')},
            data={'category': 'workshop', 'prompt': common, 'specialized_prompt': specialty})
    assert response.status_code == 200
    data = response.json()
    assert data['actual_prompt'] == compose_prompt(common, specialty, MaterialCategory.WORKSHOP)
    assert seen == [data['actual_prompt']]
    assert data['specialized_prompt'] == specialty and data['prompt_mode'] == 'composed'
    assert config['specialized_prompts']['combiner_box'].strip() not in data['actual_prompt']
    assert config['specialized_prompts']['panorama'].strip() not in data['actual_prompt']


def test_formal_provider_uses_promoted_common_and_selected_specialty(tmp_path):
    import json

    import httpx
    from app.contracts import Material, MaterialCategory
    from app.providers import MaterialInput
    from app.providers.vision.compatible import CompatibleVisionProvider
    path = tmp_path / 'default.json'
    common = '公共测试规则：仅根据图片直接可见证据识别，禁止猜测，返回约定结构。'
    with patch('app.api.vision_lab.PROMPT_DEFAULT_PATH', path), patch(
        'app.providers.vision.compatible.PROMPT_DEFAULT_PATH', path
    ):
        result = client.post('/api/v1/vision/lab/default', json={'prompt': common,
            'specialized_prompts': {'panorama': '全景专属测试标记', 'workshop': '车间专属测试标记'},
            'version_name': '测试组合版本'})
        assert result.status_code == 200
        config = client.get('/api/v1/vision/lab/config').json()
        assert config['version_name'] == '测试组合版本'
        assert config['specialized_prompts']['panorama'] == '全景专属测试标记'
        assert 'combiner_box' in config['specialized_prompts']
        captured = []
        def handler(request):
            captured.append(json.loads(request.content)['messages'][0]['content'])
            return httpx.Response(200, json={'choices': [{'message': {'content': '{"findings": []}'}}]})
        provider = CompatibleVisionProvider(
            VlmSettings(base_url='https://example.com', api_key='test', model='test-model'),
            transport=httpx.MockTransport(handler))
        for category in [MaterialCategory.PANORAMA, MaterialCategory.WORKSHOP]:
            provider.analyze_with_watermark(MaterialInput(material=Material(
                material_id=category.value, category=category, file_name='test.png',
                media_type='image/png', quality_status='usable', quality_issues=[],
                parse_status='success'), content=photo()))
        assert all(p.startswith(common) for p in captured)
        assert '全景专属测试标记' in captured[0] and '车间专属测试标记' not in captured[0]
        assert '车间专属测试标记' in captured[1] and '全景专属测试标记' not in captured[1]


def test_lab_timeout_message_does_not_blame_prompt():
    from app.providers import ProviderError
    settings = VlmSettings(base_url='https://example.com', api_key='test-secret', model='test-model')
    with patch('app.api.vision_lab.VlmSettings.from_environment', return_value=settings), patch(
        'app.api.vision_lab.CompatibleVisionProvider.analyze_with_watermark',
        side_effect=ProviderError('vision provider response timed out')
    ):
        response = client.post('/api/v1/vision/lab/analyze',
            files={'file': ('test.png', photo(), 'image/png')},
            data={'category': 'panorama', 'prompt': 'a'*40})
    assert response.status_code == 502
    assert '响应超时' in response.json()['detail']
    assert '无需修改提示词' in response.json()['detail']
    assert 'test-secret' not in response.text


def test_classification_rejects_bad_files_before_calling_model():
    with patch('app.providers.vision.classification.classify_image') as provider:
        r = client.post('/api/v1/vision/lab/classify',
            files={'file': ('bad.png', b'not-image', 'image/png')})
    assert r.status_code == 422
    provider.assert_not_called()


def test_classification_endpoint_returns_suggestions_without_risk_call():
    settings = VlmSettings(base_url='https://example.com', api_key='secret', model='test')
    result = {'category': 'panorama', 'confidence': .9, 'reason': '可见阵列',
              'requires_confirmation': True, 'candidates': []}
    with patch('app.api.vision_lab.VlmSettings.from_environment', return_value=settings), patch(
        'app.providers.vision.classification.classify_image', return_value=result
    ), patch('app.api.vision_lab.CompatibleVisionProvider') as risk:
        r = client.post('/api/v1/vision/lab/classify',
            files={'file': ('sample.png', photo(), 'image/png')})
    assert r.status_code == 200
    assert r.json()['requires_confirmation'] is False
    risk.assert_not_called()


def test_full_photo_measurement_checks_watermark_before_independent_ocr():
    from app.contracts import OcrField
    from app.providers.common import ProviderError
    settings = VlmSettings(base_url='https://example.com', api_key='test-secret', model='test-model')
    calls = []
    def vision(provider, source):
        calls.append('vision')
        return VisionAnalysis(findings=[], watermark_present=True)
    def ocr(provider, source):
        calls.append(source.ocr_task)
        assert source.material.watermark_status.value == 'present'
        if source.ocr_task=='watermark':
            raise ProviderError('watermark service failed')
        return [OcrField(field_id='test', material_id=source.material.material_id,
                         field_name='component_model', raw_value='ABC', normalized_value='ABC',
                         value_status='extracted', confidence=.9, provider='test', model='test')]
    with patch('app.api.vision_lab.VlmSettings.from_environment',return_value=settings), patch(
        'app.api.vision_lab.CompatibleVisionProvider.analyze_with_watermark',vision
    ), patch('app.api.vision_lab.CompatibleOcrProvider.extract',ocr):
        response=client.post('/api/v1/vision/lab/analyze',
                             files={'file':('test.png',photo(),'image/png')},
                             data={'category':'component_nameplate','prompt':'a'*40,'extract_text':'true'})
    assert response.status_code==200, response.text
    assert calls==['vision','watermark','business']
    result=response.json()
    assert result['watermark_present'] is True
    assert result['ocr_fields'][0]['extraction_task']=='business'
    assert [t['status'] for t in result['ocr_task_executions']]==['failed','success']
    assert 'watermark service failed' not in response.text
