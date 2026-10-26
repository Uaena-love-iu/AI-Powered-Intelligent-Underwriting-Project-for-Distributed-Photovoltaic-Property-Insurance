import pytest
from app.contracts import Material, RiskFinding, ApplicableCheck, InstallationAnalysis
from app.rules import RuleEngine
from app.rules.completeness import assess_checks


def sample(text, status='detected', quality='usable'):
    material = Material(material_id='ground', category='electrical_grounding', file_name='ground.png', media_type='image/png', quality_status=quality, quality_issues=[], parse_status='success')
    finding = RiskFinding(finding_id='quality', material_id='ground', category='image_quality', label='图片质量', detection_status=status, severity='info', confidence=1, evidence_text=text, provider='fixture', model='fixture', requires_manual_review=False)
    check = ApplicableCheck(area_id='project', key='grounding', label='接地', category='electrical_grounding', applicability='applicable', required_now=True, reason='接地检查', evidence_requirement='接地连接区域')
    review = RuleEngine().evaluate_material(material, findings=[finding])
    result = assess_checks([check], [material], [finding], [], InstallationAnalysis(provider='fixture'), failed_material_ids=set())[0]
    return review, result


@pytest.mark.parametrize('status', ['detected','uncertain'])
def test_clear_image_conflict_goes_to_internal_review(status):
    review, check = sample('图片清晰，光照充足，接地排细节可见。左上角有 SIMULATED / TEST ONLY 标识，但图像本身质量满足检查要求。', status)
    assert review.action.value == 'warning'
    assert review.requires_manual_review
    assert not review.missing_requirements
    assert 'MAT-QUALITY-001' not in review.triggered_rule_ids
    assert check.status == 'pending'
    assert check.customer_request is None


@pytest.mark.parametrize('text', ['图片模糊，无法检查接地线。','图片清晰，但关键接地端子被遮挡。','图片清晰，但未展示关键区域。'])
def test_real_defects_still_require_material(text):
    review, check = sample(text)
    assert review.action.value == 'request_more'
    assert review.missing_requirements
    assert check.status == 'unusable'
    assert check.customer_request


def test_independent_preflight_failure_is_not_cleared():
    review, check = sample('图片清晰，质量满足检查要求。', quality='unusable')
    assert review.action.value == 'request_more'
    assert check.status == 'unusable'
