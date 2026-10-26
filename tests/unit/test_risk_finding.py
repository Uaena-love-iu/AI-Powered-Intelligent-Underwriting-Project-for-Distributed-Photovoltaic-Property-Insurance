import pytest
from app.contracts.enums import DetectionStatus, RiskCategory, RiskSeverity
from app.contracts.findings import RiskFinding
from pydantic import ValidationError


def valid_payload() -> dict[str, object]:
    return {
        "finding_id": "finding-001",
        "material_id": "material-001",
        "category": "water_adjacent_environment",
        "label": "临近水体",
        "detection_status": "detected",
        "severity": "high",
        "confidence": 0.86,
        "bbox": {
            "x_min": 0.52,
            "y_min": 0.18,
            "x_max": 0.96,
            "y_max": 0.84,
            "coordinate_space": "normalized_0_1",
        },
        "evidence_text": "光伏组件右侧发现大面积水体",
        "provider": "mock-vision",
        "model": "mock-vision-v1",
        "requires_manual_review": False,
    }


def test_risk_finding_accepts_valid_payload() -> None:
    finding = RiskFinding.model_validate(valid_payload())

    assert finding.category is RiskCategory.WATER_ADJACENT_ENVIRONMENT
    assert finding.detection_status is DetectionStatus.DETECTED
    assert finding.severity is RiskSeverity.HIGH
    assert finding.bbox is not None
    assert finding.bbox.x_min == 0.52

    serialized = finding.model_dump(mode="json")
    assert serialized["category"] == "water_adjacent_environment"
    assert serialized["detection_status"] == "detected"


@pytest.mark.parametrize("confidence", [-0.01, 1.01])
def test_risk_finding_rejects_invalid_confidence(confidence: float) -> None:
    payload = valid_payload()
    payload["confidence"] = confidence

    with pytest.raises(ValidationError):
        RiskFinding.model_validate(payload)


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("category", "unknown_category"),
        ("detection_status", "maybe"),
        ("severity", "extreme"),
    ],
)
def test_risk_finding_rejects_invalid_enum_values(
    field_name: str,
    invalid_value: str,
) -> None:
    payload = valid_payload()
    payload[field_name] = invalid_value

    with pytest.raises(ValidationError):
        RiskFinding.model_validate(payload)


def test_risk_finding_accepts_missing_bbox() -> None:
    payload = valid_payload()
    payload["bbox"] = None
    payload["detection_status"] = "uncertain"
    payload["requires_manual_review"] = True

    finding = RiskFinding.model_validate(payload)

    assert finding.bbox is None
    assert finding.requires_manual_review is True


def test_risk_finding_rejects_unknown_fields() -> None:
    payload = valid_payload()
    payload["unexpected_field"] = "unexpected"

    with pytest.raises(ValidationError):
        RiskFinding.model_validate(payload)


def test_risk_finding_rejects_missing_required_field() -> None:
    payload = valid_payload()
    del payload["provider"]

    with pytest.raises(ValidationError):
        RiskFinding.model_validate(payload)