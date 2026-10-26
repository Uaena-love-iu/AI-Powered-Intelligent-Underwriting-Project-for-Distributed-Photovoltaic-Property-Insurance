import pytest
from app.contracts.enums import (
    DecisionType,
    MaterialReviewAction,
    ProcessingStatus,
    ProcessingStep,
)
from app.contracts.outputs import (
    MaterialReview,
    ProcessingTrace,
    UnderwritingDecision,
)
from pydantic import ValidationError


def valid_review_payload() -> dict[str, object]:
    return {
        "material_id": "material-001",
        "action": "warning",
        "triggered_rule_ids": ["MAT-QUALITY-001"],
        "finding_ids": ["finding-001"],
        "ocr_field_ids": [],
        "reasons": ["现场照片存在需要关注的风险点"],
        "missing_requirements": [],
        "requires_manual_review": True,
    }


def valid_decision_payload() -> dict[str, object]:
    return {
        "decision": "manual_review",
        "decisive_rule_ids": ["MAT-QUALITY-001"],
        "reasons": ["关键风险点需要人工确认"],
        "conditions": [],
        "warnings": ["请复核现场照片"],
        "missing_requirements": [],
        "generated_at": "2026-09-24T11:00:00+08:00",
    }


def valid_trace_payload() -> dict[str, object]:
    return {
        "step": "vision",
        "provider": "mock-vision",
        "model": "mock-vision-v1",
        "started_at": "2026-09-24T10:00:00+08:00",
        "finished_at": "2026-09-24T10:00:01+08:00",
        "latency_ms": 1000,
        "status": "success",
        "error_code": None,
        "error_message": None,
    }


def test_material_review_accepts_valid_payload() -> None:
    review = MaterialReview.model_validate(valid_review_payload())

    assert review.action is MaterialReviewAction.WARNING
    assert review.requires_manual_review is True


def test_material_review_rejects_invalid_action() -> None:
    payload = valid_review_payload()
    payload["action"] = "maybe"

    with pytest.raises(ValidationError):
        MaterialReview.model_validate(payload)


def test_underwriting_decision_accepts_valid_payload() -> None:
    decision = UnderwritingDecision.model_validate(valid_decision_payload())

    assert decision.decision is DecisionType.MANUAL_REVIEW
    assert decision.warnings == ["请复核现场照片"]


def test_underwriting_decision_rejects_invalid_type() -> None:
    payload = valid_decision_payload()
    payload["decision"] = "approve"

    with pytest.raises(ValidationError):
        UnderwritingDecision.model_validate(payload)


def test_processing_trace_accepts_valid_payload() -> None:
    trace = ProcessingTrace.model_validate(valid_trace_payload())

    assert trace.step is ProcessingStep.VISION
    assert trace.status is ProcessingStatus.SUCCESS
    assert trace.latency_ms == 1000


def test_processing_trace_rejects_negative_latency() -> None:
    payload = valid_trace_payload()
    payload["latency_ms"] = -1

    with pytest.raises(ValidationError):
        ProcessingTrace.model_validate(payload)


def test_processing_trace_rejects_reversed_time_order() -> None:
    payload = valid_trace_payload()
    payload["started_at"] = "2026-09-24T10:00:02+08:00"
    payload["finished_at"] = "2026-09-24T10:00:01+08:00"

    with pytest.raises(ValidationError):
        ProcessingTrace.model_validate(payload)


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("step", "unknown_step"),
        ("status", "pending"),
    ],
)
def test_processing_trace_rejects_invalid_enum_values(
    field_name: str,
    invalid_value: str,
) -> None:
    payload = valid_trace_payload()
    payload[field_name] = invalid_value

    with pytest.raises(ValidationError):
        ProcessingTrace.model_validate(payload)