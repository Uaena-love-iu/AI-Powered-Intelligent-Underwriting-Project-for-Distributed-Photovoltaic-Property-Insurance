from types import SimpleNamespace

from app.contracts import MaterialCategory, OcrValueStatus, ProjectInfo
from app.pipeline import UnderwritingPipeline


def project(address=None):
    return ProjectInfo(project_name=None, insured_name=None, project_type='unknown',
                       installation_type='unknown', site_address=address, longitude=None,
                       latitude=None, proposed_start_date=None, component_model=None)


def field(value, **changes):
    data = dict(material_id='filing', field_name='site_address',
                value_status=OcrValueStatus.EXTRACTED, confidence=1.0,
                normalized_value=value, raw_value=value)
    return SimpleNamespace(**(data | changes))


def apply(fields, address=None):
    materials = [SimpleNamespace(material_id='filing', category=MaterialCategory.FILING_CERTIFICATE)]
    return UnderwritingPipeline._apply_filing_site_address(project(address), materials, fields)


def test_filing_site_is_used_even_with_duplicate_extractions():
    assert apply([field('吴忠市同心县豫海镇'), field(' 吴忠市同心县豫海镇 ')]).site_address == '吴忠市同心县豫海镇'


def test_different_filing_addresses_are_not_silently_selected():
    assert apply([field('吴忠市同心县豫海镇'), field('上海市浦东新区')]).site_address is None


def test_existing_submission_is_preserved_for_conflict_review():
    assert apply([field('吴忠市同心县豫海镇')], '上海市浦东新区').site_address == '上海市浦东新区'


def test_unreliable_or_unrelated_address_is_not_used():
    for item in [field('地址', confidence=0.4), field('地址', field_name='insured_address'),
                 field('地址', material_id='photo'), field('  '),
                 field('地址', value_status=OcrValueStatus.UNCERTAIN)]:
        assert apply([item]).site_address is None


def test_explicit_filing_county_matches_existing_station_only():
    from app.catastrophe.front_load_screening.city_lookup import lookup
    result = lookup({'address': '吴忠市同心县豫海镇'})
    assert result['status'] == 'MATCHED'
    assert result['city_source'] == '同心'
    assert result['province'] == '宁夏'
    assert lookup({'address': '吴忠市同心路'})['status'] == 'NOT_FOUND'
    assert lookup({'address': '吴忠市同心县', 'province': '山东'})['status'] == 'NOT_FOUND'
