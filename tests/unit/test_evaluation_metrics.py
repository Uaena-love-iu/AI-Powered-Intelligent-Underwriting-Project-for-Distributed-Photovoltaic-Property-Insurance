import pytest

from scripts.evaluate_cases import evaluate_cases


def test_evaluator_scores_decisions_risk_rules_and_locations() -> None:
    ground_truth = [
        {
            "case_id": "case-a",
            "expected_decision": "recommend_reject",
            "required_high_risk_categories": [
                {"category": "water_adjacent_environment", "material_id": "panorama-1"}
            ],
            "required_rule_ids": ["ENV-REJECT-001"],
            "required_findings": [
                {
                    "category": "water_adjacent_environment",
                    "material_id": "panorama-1",
                    "bbox": {"x_min": 0.1, "y_min": 0.2, "x_max": 0.4, "y_max": 0.6},
                }
            ],
        }
    ]
    predictions = [
        {
            "case_id": "case-a",
            "decision": {"decision": "recommend_reject", "decisive_rule_ids": []},
            "material_reviews": [{"triggered_rule_ids": ["ENV-REJECT-001"]}],
            "findings": [
                {
                    "category": "water_adjacent_environment",
                    "material_id": "panorama-1",
                    "detection_status": "detected",
                    "bbox": {"x_min": 0.12, "y_min": 0.2, "x_max": 0.4, "y_max": 0.6},
                }
            ],
        }
    ]

    metrics = evaluate_cases(ground_truth, predictions)

    assert metrics["decision_accuracy"] == 1.0
    assert metrics["high_risk_recall"] == 1.0
    assert metrics["rule_recall"] == 1.0
    assert metrics["bbox_recall_at_iou_threshold"] == 1.0
    assert metrics["passed"] is True


def test_evaluator_fails_when_high_risk_scene_is_uncertain_or_missing() -> None:
    ground_truth = [
        {
            "case_id": "case-b",
            "expected_decision": "recommend_reject",
            "required_high_risk_categories": ["agriculture_environment"],
        }
    ]
    predictions = [
        {
            "case_id": "case-b",
            "decision": {"decision": "accept"},
            "findings": [
                {
                    "category": "agriculture_environment",
                    "detection_status": "uncertain",
                }
            ],
        }
    ]

    metrics = evaluate_cases(ground_truth, predictions)

    assert metrics["decision_accuracy"] == 0.0
    assert metrics["high_risk_recall"] == 0.0
    assert metrics["passed"] is False


def test_evaluator_reports_false_positives_ocr_accuracy_and_suite_metrics() -> None:
    truth = [
        {
            "case_id": "case-b",
            "suite": "B",
            "expected_decision": "recommend_reject",
            "required_high_risk_categories": ["water_adjacent_environment"],
            "findings_complete": True,
            "expected_findings": [
                {"category": "water_adjacent_environment", "material_id": "panorama-1"}
            ],
            "expected_ocr_fields": [
                {
                    "material_id": "filing-1",
                    "field_name": "project_name",
                    "expected_value": "示例项目",
                }
            ],
        }
    ]
    predictions = [
        {
            "case_id": "case-b",
            "decision": {"decision": "recommend_reject"},
            "findings": [
                {
                    "category": "water_adjacent_environment",
                    "material_id": "panorama-1",
                    "detection_status": "detected",
                },
                {
                    "category": "minor_shading",
                    "material_id": "panorama-1",
                    "detection_status": "detected",
                },
            ],
            "ocr_fields": [
                {
                    "material_id": "filing-1",
                    "field_name": "project_name",
                    "normalized_value": " 示例 项目 ",
                    "value_status": "extracted",
                }
            ],
            "processing_trace": [{"latency_ms": 100}, {"latency_ms": 50}],
        }
    ]

    metrics = evaluate_cases(truth, predictions, minimum_finding_precision=0.75)

    assert metrics["finding_precision"] == 0.5
    assert metrics["finding_recall"] == 1.0
    assert metrics["finding_false_positives"] == 1
    assert metrics["ocr_fields_evaluated"] == 1
    assert metrics["ocr_field_accuracy"] == 1.0
    assert metrics["suite_results"]["B"]["cases"] == 1
    assert metrics["trace_latency_ms_max"] == 150
    assert metrics["passed"] is False


def test_evaluator_requires_full_finding_annotations_to_be_explicit() -> None:
    with pytest.raises(ValueError, match="findings_complete"):
        evaluate_cases(
            [{"case_id": "case-empty", "findings_complete": True}],
            [{"case_id": "case-empty"}],
        )
