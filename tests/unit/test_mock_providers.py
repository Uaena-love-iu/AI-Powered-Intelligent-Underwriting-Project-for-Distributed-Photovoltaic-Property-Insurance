from app.contracts import (
    DetectionStatus,
    Material,
    MaterialCategory,
    MaterialParseStatus,
    MaterialQualityStatus,
    OcrValueStatus,
    RiskCategory,
)
from app.providers import MaterialInput, MockOcrProvider, MockVisionProvider


def material_input(category: MaterialCategory) -> MaterialInput:
    material = Material(
        material_id="material-001",
        category=category,
        file_name="example.jpg",
        media_type="image/jpeg",
        quality_status=MaterialQualityStatus.USABLE,
        quality_confidence=0.95,
        quality_issues=[],
        parse_status=MaterialParseStatus.SUCCESS,
    )
    return MaterialInput(material=material, content=b"mock-image-content")


def test_mock_ocr_returns_component_model_for_nameplate() -> None:
    provider = MockOcrProvider()

    fields = provider.extract(material_input(MaterialCategory.COMPONENT_NAMEPLATE))

    assert len(fields) == 1
    assert fields[0].field_name == "component_model"
    assert fields[0].value_status is OcrValueStatus.EXTRACTED
    assert fields[0].provider == provider.name
    assert fields[0].model == provider.model_name


def test_mock_ocr_returns_no_fields_for_other_materials() -> None:
    provider = MockOcrProvider()

    fields = provider.extract(material_input(MaterialCategory.PANORAMA))

    assert fields == []


def test_mock_vision_returns_water_risk_for_panorama() -> None:
    provider = MockVisionProvider()

    findings = provider.analyze(material_input(MaterialCategory.PANORAMA))

    assert len(findings) == 1
    assert findings[0].category is RiskCategory.WATER_ADJACENT_ENVIRONMENT
    assert findings[0].detection_status is DetectionStatus.DETECTED
    assert findings[0].bbox is not None
    assert findings[0].provider == provider.name
    assert findings[0].model == provider.model_name


def test_mock_vision_returns_no_findings_for_other_materials() -> None:
    provider = MockVisionProvider()

    findings = provider.analyze(
        material_input(MaterialCategory.COMPONENT_NAMEPLATE)
    )

    assert findings == []