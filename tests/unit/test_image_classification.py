import json
from io import BytesIO
from unittest.mock import patch

import httpx
import pytest
from app.providers.common import ProviderError
from app.providers.vision.classification import classify_image
from app.settings import VlmSettings
from PIL import Image


def photo():
    out = BytesIO()
    Image.new('RGB', (640, 480), 'white').save(out, format='PNG')
    return out.getvalue()


def test_high_score_still_needs_confirmation_and_keeps_multiple_uses():
    def handler(request):
        body = json.loads(request.content)
        assert '不依据文件名' in body['messages'][0]['content']
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps({
            'category': 'panorama', 'confidence': .99, 'reason': '可见阵列布局',
            'alternatives': [{'category': 'component_surface', 'confidence': .75,
                              'reason': '部分组件表面清晰可见'}]})}}]})
    client = httpx.Client(transport=httpx.MockTransport(handler))
    with patch('app.providers.vision.classification.httpx.Client', return_value=client):
        result = classify_image(photo(), VlmSettings('https://example.com', 'secret', 'test'))
    assert result['requires_confirmation'] is True
    assert [c['category'] for c in result['candidates']] == ['panorama', 'component_surface']
    assert 'secret' not in json.dumps(result)


def test_invalid_category_is_rejected_instead_of_routing_checks():
    def handler(request):
        return httpx.Response(200, json={'choices': [{'message': {'content':
            '{"category":"invented","confidence":0.99,"reason":"test","alternatives":[]}'}}]})
    with (
        patch(
            'app.providers.vision.classification.httpx.Client',
            return_value=httpx.Client(transport=httpx.MockTransport(handler)),
        ),
        pytest.raises(ProviderError, match='分类失败'),
    ):
        classify_image(photo(), VlmSettings('https://example.com', 'secret', 'test'))


def test_connection_failure_explains_network_permission_without_leaking_details():
    def handler(request):
        raise httpx.ConnectError('WinError 10013 private-secret', request=request)

    with (
        patch('app.providers.vision.classification.httpx.Client',
              return_value=httpx.Client(transport=httpx.MockTransport(handler))),
        pytest.raises(ProviderError, match='联网权限') as error,
    ):
        classify_image(photo(), VlmSettings('https://example.com', 'private-secret', 'test'))
    assert 'private-secret' not in str(error.value)
    assert '输出格式无效' not in str(error.value)


def test_free_quota_error_explains_required_account_action_without_leaking_key():
    def handler(request):
        return httpx.Response(403, json={'error': {
            'code': 'AllocationQuota.FreeTierOnly',
            'message': 'Free quota exhausted: private-secret',
        }})

    with (
        patch('app.providers.vision.classification.httpx.Client',
              return_value=httpx.Client(transport=httpx.MockTransport(handler))),
        pytest.raises(ProviderError, match='免费调用额度已用尽') as error,
    ):
        classify_image(photo(), VlmSettings('https://example.com', 'private-secret', 'test'))
    assert 'private-secret' not in str(error.value)
    assert '手动确认材料类别' in str(error.value)
