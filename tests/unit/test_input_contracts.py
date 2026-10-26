from datetime import date

import pytest
from app.contracts.enums import (
    MaterialCategory,
    MaterialParseStatus,
    MaterialQualityStatus,
    OcrValueStatus,
    ProjectType,
)
from app.contracts.inputs import Material, OcrField, ProjectInfo
from pydantic import ValidationError


def valid_project_payload() -> dict[str, object]:
    return {
        "project_name": "示例屋顶光伏项目",
        "insured_name": "示例企业有限公司",
        "project_entity": "示例能源有限公司",
        "project_type": "rooftop",
        "installation_type": "color_steel_roof",
        "site_address": "广东省示例市示例区",
        "province": "广东省",
        "city": "示例市",
        "district": "示例区",
        "longitude": 113.25,
        "latitude": 23.12,
        "proposed_start_date": "2026-10-01",
        "component_model": "PV-MODULE-580W",
        "submission_ip": "192.0.2.10",
    }


def valid_material_payload() -> dict[str, object]:
    return {
        "material_id": "material-001",
        "category": "panorama",
        "file_name": "panorama-001.jpg",
        "media_type": "image/jpeg",
        "sha256": "a" * 64,
        "captured_at": "2026-09-20T10:30:00+08:00",
        "longitude": 113.25,
        "latitude": 23.12,
        "quality_status": "usable",
        "quality_confidence": 0.94,
        "quality_issues": [],
        "parse_status": "success",
    }


def valid_ocr_payload() -> dict[str, object]:
    return {
        "field_id": "ocr-field-001",
        "material_id": "material-001",
        "field_name": "component_model",
        "raw_value": "PV-MODULE-580W",
        "normalized_value": "PV-MODULE-580W",
        "value_status": "extracted",
        "confidence": 0.92,
        "bbox": None,
        "provider": "mock-ocr",
        "model": "mock-ocr-v1",
        "evidence_text": "组件型号：PV-MODULE-580W",
    }


def test_project_info_accepts_valid_payload() -> None:
    project = ProjectInfo.model_validate(valid_project_payload())

    assert project.project_type is ProjectType.ROOFTOP
    assert project.proposed_start_date == date(2026, 10, 1)
    assert str(project.submission_ip) == "192.0.2.10"


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("longitude", 180.01),
        ("latitude", -90.01),
    ],
)
def test_project_info_rejects_invalid_coordinates(
    field_name: str,
    invalid_value: float,
) -> None:
    payload = valid_project_payload()
    payload[field_name] = invalid_value

    with pytest.raises(ValidationError):
        ProjectInfo.model_validate(payload)


def test_project_info_requires_nullable_core_fields() -> None:
    payload = valid_project_payload()
    del payload["longitude"]

    with pytest.raises(ValidationError):
        ProjectInfo.model_validate(payload)


def test_material_accepts_valid_payload() -> None:
    material = Material.model_validate(valid_material_payload())

    assert material.category is MaterialCategory.PANORAMA
    assert material.quality_status is MaterialQualityStatus.USABLE
    assert material.parse_status is MaterialParseStatus.SUCCESS


@pytest.mark.parametrize("confidence", [-0.01, 1.01])
def test_material_rejects_invalid_quality_confidence(confidence: float) -> None:
    payload = valid_material_payload()
    payload["quality_confidence"] = confidence

    with pytest.raises(ValidationError):
        Material.model_validate(payload)


def test_material_rejects_invalid_sha256() -> None:
    payload = valid_material_payload()
    payload["sha256"] = "not-a-valid-sha256"

    with pytest.raises(ValidationError):
        Material.model_validate(payload)


def test_ocr_field_accepts_valid_payload() -> None:
    field = OcrField.model_validate(valid_ocr_payload())

    assert field.value_status is OcrValueStatus.EXTRACTED
    assert field.confidence == 0.92


@pytest.mark.parametrize("confidence", [-0.01, 1.01])
def test_ocr_field_rejects_invalid_confidence(confidence: float) -> None:
    payload = valid_ocr_payload()
    payload["confidence"] = confidence

    with pytest.raises(ValidationError):
        OcrField.model_validate(payload)


def test_ocr_field_allows_missing_value_without_inventing_content() -> None:
    payload = valid_ocr_payload()
    payload["raw_value"] = None
    payload["normalized_value"] = None
    payload["value_status"] = "missing"
    payload["confidence"] = 0.0

    field = OcrField.model_validate(payload)

    assert field.value_status is OcrValueStatus.MISSING
    assert field.raw_value is None
    assert field.normalized_value is None