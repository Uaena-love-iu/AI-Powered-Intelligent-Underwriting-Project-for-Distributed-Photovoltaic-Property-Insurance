import pytest
from app.contracts.case import UnderwritingCase
from app.contracts.enums import DetectionStatus, RiskCategory
from pydantic import ValidationError


def minimal_case_payload() -> dict[str, object]:
    return {
        "schema_version": "0.1.0",
        "case_id": "case-001",
        "project": {
            "project_name": None,
            "insured_name": None,
            "project_type": "unknown",
            "installation_type": "unknown",
            "site_address": None,
            "longitude": None,
            "latitude": None,
            "proposed_start_date": None,
            "component_model": None,
        },
        "materials": [],
        "ocr_fields": [],
        "findings": [],
        "component_profile": None,
        "weather_profile": None,
        "catastrophe_assessment": None,
        "material_reviews": [],
        "decision": None,
        "processing_trace": [],
    }


def material_payload(material_id: str = "material-001") -> dict[str, object]:
    return {
        "material_id": material_id,
        "category": "panorama",
        "file_name": "panorama.jpg",
        "media_type": "image/jpeg",
        "quality_status": "usable",
        "quality_confidence": 0.95,
        "quality_issues": [],
        "parse_status": "success",
    }


def ocr_payload(material_id: str = "material-001") -> dict[str, object]:
    return {
        "field_id": "ocr-001",
        "material_id": material_id,
        "field_name": "component_model",
        "raw_value": "PV-MODULE-580W",
        "normalized_value": "PV-MODULE-580W",
        "value_status": "extracted",
        "confidence": 0.92,
        "provider": "mock-ocr",
        "model": "mock-ocr-v1",
    }


def finding_payload(material_id: str = "material-001") -> dict[str, object]:
    return {
        "finding_id": "finding-001",
        "material_id": material_id,
        "category": "water_adjacent_environment",
        "label": "临近水体",
        "detection_status": "detected",
        "severity": "high",
        "confidence": 0.86,
        "evidence_text": "图片右侧发现水体",
        "provider": "mock-vision",
        "model": "mock-vision-v1",
        "requires_manual_review": False,
    }


def review_payload() -> dict[str, object]:
    return {
        "material_id": "material-001",
        "action": "warning",
        "triggered_rule_ids": ["VIS-WATER-001"],
        "finding_ids": ["finding-001"],
        "ocr_field_ids": ["ocr-001"],
        "reasons": ["发现临近水体"],
        "missing_requirements": [],
        "requires_manual_review": True,
    }


def test_underwriting_case_accepts_minimal_case() -> None:
    case = UnderwritingCase.model_validate(minimal_case_payload())

    assert case.schema_version == "0.1.0"
    assert case.case_id == "case-001"
    assert case.materials == []
    assert case.decision is None


def test_underwriting_case_builds_nested_contracts() -> None:
    payload = minimal_case_payload()
    payload["materials"] = [material_payload()]
    payload["ocr_fields"] = [ocr_payload()]
    payload["findings"] = [finding_payload()]
    payload["material_reviews"] = [review_payload()]

    case = UnderwritingCase.model_validate(payload)

    assert case.ocr_fields[0].field_id == "ocr-001"
    assert case.findings[0].category is RiskCategory.WATER_ADJACENT_ENVIRONMENT
    assert case.findings[0].detection_status is DetectionStatus.DETECTED


def test_underwriting_case_rejects_wrong_schema_version() -> None:
    payload = minimal_case_payload()
    payload["schema_version"] = "0.2.0"

    with pytest.raises(ValidationError):
        UnderwritingCase.model_validate(payload)


def test_underwriting_case_rejects_duplicate_material_ids() -> None:
    payload = minimal_case_payload()
    payload["materials"] = [material_payload(), material_payload()]

    with pytest.raises(ValidationError):
        UnderwritingCase.model_validate(payload)


def test_underwriting_case_rejects_ocr_reference_to_unknown_material() -> None:
    payload = minimal_case_payload()
    payload["ocr_fields"] = [ocr_payload("missing-material")]

    with pytest.raises(ValidationError):
        UnderwritingCase.model_validate(payload)


def test_underwriting_case_rejects_finding_reference_to_unknown_material() -> None:
    payload = minimal_case_payload()
    payload["findings"] = [finding_payload("missing-material")]

    with pytest.raises(ValidationError):
        UnderwritingCase.model_validate(payload)


def test_underwriting_case_rejects_review_reference_to_unknown_results() -> None:
    payload = minimal_case_payload()
    payload["materials"] = [material_payload()]
    payload["material_reviews"] = [review_payload()]

    with pytest.raises(ValidationError):
        UnderwritingCase.model_validate(payload)