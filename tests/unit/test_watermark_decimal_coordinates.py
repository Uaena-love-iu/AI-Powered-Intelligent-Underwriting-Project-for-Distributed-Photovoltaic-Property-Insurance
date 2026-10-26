import pytest
from app.contracts import Material, OcrField
from app.location.coordinates import parse_decimal_coordinate
from app.pipeline.core import UnderwritingPipeline
from app.providers import MaterialInput


@pytest.mark.parametrize('value,axis,expected', [
    ('113.118900 E', 'longitude', 113.1189),
    ('23.034500 N', 'latitude', 23.0345),
    ('西经 113.1189°', 'longitude', -113.1189),
    ('南纬23.0345度', 'latitude', -23.0345),
    ('-23.0345', 'latitude', -23.0345),
    ('113°7\'8" E', 'longitude', None),
    ('181 E', 'longitude', None),
    ('91 N', 'latitude', None),
    ('113 N', 'longitude', None),
    ('-113 E', 'longitude', None),
    ('E 113 W', 'longitude', None),
    ('nan', 'latitude', None),
])
def test_parse_coordinates(value, axis, expected):
    assert parse_decimal_coordinate(value, axis=axis) == expected


def test_pipeline_uses_directional_ocr_coordinates():
    material = Material(material_id='front', category='panorama',
                        file_name='test.png', media_type='image/png',
                        quality_status='usable', quality_issues=[], parse_status='success')
    fields = [OcrField(field_id=key, material_id='front', field_name=key,
                       raw_value=value, normalized_value=value,
                       value_status='extracted', confidence=0.99,
                       provider='test', model='test')
              for key, value in [('watermark_longitude','113.118900 E'),
                                 ('watermark_latitude','23.034500 N')]]
    updated = UnderwritingPipeline._apply_ocr_metadata(
        [MaterialInput(material=material, content=b'test')], fields)[0].material
    assert updated.longitude == 113.1189
    assert updated.latitude == 23.0345
