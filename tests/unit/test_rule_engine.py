import pytest
from app.contracts import (
    Material,
    MaterialCategory,
    MaterialParseStatus,
    MaterialQualityStatus,
    MaterialReviewAction,
)
from app.rules import RuleEngine


def material_with_quality(
    quality_status: MaterialQualityStatus,
) -> Material:
    return Material(
        material_id=f"material-{quality_status.value}",
        category=MaterialCategory.OTHER,
        file_name="example.jpg",
        media_type="application/pdf",
        quality_status=quality_status,
        quality_confidence=None,
        quality_issues=[],
        parse_status=MaterialParseStatus.SUCCESS,
    )


def test_rule_engine_passes_usable_material() -> None:
    engine = RuleEngine()

    review = engine.evaluate_material(
        material_with_quality(MaterialQualityStatus.USABLE)
    )

    assert review.action is MaterialReviewAction.PASS
    assert review.triggered_rule_ids == []
    assert review.missing_requirements == []


@pytest.mark.parametrize(
    "quality_status",
    [
        MaterialQualityStatus.POOR,
        MaterialQualityStatus.UNUSABLE,
        MaterialQualityStatus.UNKNOWN,
    ],
)
def test_rule_engine_requests_more_for_unreliable_material(
    quality_status: MaterialQualityStatus,
) -> None:
    engine = RuleEngine()

    review = engine.evaluate_material(material_with_quality(quality_status))

    assert review.action is MaterialReviewAction.REQUEST_MORE
    assert review.triggered_rule_ids == ["MAT-QUALITY-001"]
    assert review.missing_requirements
    assert review.requires_manual_review is False


def test_rule_engine_returns_one_review_for_each_material() -> None:
    engine = RuleEngine()
    materials = [
        material_with_quality(MaterialQualityStatus.USABLE),
        material_with_quality(MaterialQualityStatus.POOR),
    ]

    reviews = engine.evaluate_materials(materials)

    assert len(reviews) == 2
    assert reviews[0].material_id == "material-usable"
    assert reviews[1].material_id == "material-poor"
