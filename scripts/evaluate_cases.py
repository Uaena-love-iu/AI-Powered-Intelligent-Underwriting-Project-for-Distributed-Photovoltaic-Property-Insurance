"""Evaluate saved underwriting outputs against case-level annotations."""

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from statistics import median
from typing import Any


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise TypeError(f"{path}:{line_number}: each row must be a JSON object")
        rows.append(value)
    return rows


def _case_payload(row: dict[str, Any]) -> dict[str, Any]:
    for key in ("case", "result"):
        nested = row.get(key)
        if isinstance(nested, dict):
            return nested
    return row


def _indexed(rows: list[dict[str, Any]], label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        case = _case_payload(row)
        case_id = case.get("case_id")
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError(f"{label} row is missing a non-empty case_id")
        if case_id in result:
            raise ValueError(f"{label} contains duplicate case_id: {case_id}")
        result[case_id] = case
    return result


def _detected_findings(case: dict[str, Any]) -> list[dict[str, Any]]:
    findings = case.get("findings", [])
    if not isinstance(findings, list):
        return []
    return [
        finding
        for finding in findings
        if isinstance(finding, dict)
        and finding.get("detection_status") == "detected"
    ]


def _rule_ids(case: dict[str, Any]) -> set[str]:
    result: set[str] = set()
    decision = case.get("decision")
    if isinstance(decision, dict):
        result.update(item for item in decision.get("decisive_rule_ids", []) if isinstance(item, str))
    for review in case.get("material_reviews", []):
        if isinstance(review, dict):
            result.update(item for item in review.get("triggered_rule_ids", []) if isinstance(item, str))
    return result


def _bbox_iou(expected: dict[str, Any], predicted: dict[str, Any]) -> float:
    left = max(float(expected["x_min"]), float(predicted["x_min"]))
    top = max(float(expected["y_min"]), float(predicted["y_min"]))
    right = min(float(expected["x_max"]), float(predicted["x_max"]))
    bottom = min(float(expected["y_max"]), float(predicted["y_max"]))
    intersection = max(0.0, right - left) * max(0.0, bottom - top)
    expected_area = (float(expected["x_max"]) - float(expected["x_min"])) * (
        float(expected["y_max"]) - float(expected["y_min"])
    )
    predicted_area = (float(predicted["x_max"]) - float(predicted["x_min"])) * (
        float(predicted["y_max"]) - float(predicted["y_min"])
    )
    union = expected_area + predicted_area - intersection
    return intersection / union if union > 0 else 0.0


def _expected_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [
        {"category": item} if isinstance(item, str) else item
        for item in value
        if isinstance(item, (str, dict))
        and (isinstance(item, str) or item.get("category") or item.get("field_name"))
    ]


def _normalized_value(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", "", value).casefold()


def evaluate_cases(
    ground_truth: list[dict[str, Any]],
    predictions: list[dict[str, Any]],
    *,
    minimum_decision_accuracy: float = 0.90,
    minimum_high_risk_recall: float = 1.0,
    minimum_bbox_iou: float = 0.50,
    minimum_finding_precision: float | None = None,
    minimum_ocr_accuracy: float | None = None,
) -> dict[str, Any]:
    for name, value in (
        ("minimum_finding_precision", minimum_finding_precision),
        ("minimum_ocr_accuracy", minimum_ocr_accuracy),
    ):
        if value is not None and not 0.0 <= value <= 1.0:
            raise ValueError(f"{name} must be between 0 and 1")
    expected_by_id = _indexed(ground_truth, "ground_truth")
    predicted_by_id = _indexed(predictions, "predictions")
    prediction_coverage = len(expected_by_id.keys() & predicted_by_id.keys())
    decision_total = 0
    decision_correct = 0
    critical_total = 0
    critical_detected = 0
    rule_total = 0
    rule_detected = 0
    bbox_total = 0
    bbox_localized = 0
    finding_annotation_cases = 0
    finding_true_positive = 0
    finding_false_positive = 0
    finding_false_negative = 0
    ocr_total = 0
    ocr_correct = 0
    suite_counts: dict[str, dict[str, int]] = {}
    trace_latency_by_case: list[int] = []
    misses: list[dict[str, str]] = []

    for case_id, expected in expected_by_id.items():
        actual = predicted_by_id.get(case_id)
        if actual is None:
            misses.append({"case_id": case_id, "item": "case_missing"})

        suite = expected.get("suite")
        suite_metrics = None
        if isinstance(suite, str) and suite.strip():
            suite_metrics = suite_counts.setdefault(
                suite.strip(),
                {
                    "cases": 0,
                    "decision_total": 0,
                    "decision_correct": 0,
                    "high_risk_total": 0,
                    "high_risk_detected": 0,
                },
            )
            suite_metrics["cases"] += 1

        expected_decision = expected.get("expected_decision")
        if expected_decision is not None:
            decision_total += 1
            actual_decision = actual.get("decision") if actual else None
            if isinstance(actual_decision, dict):
                actual_decision = actual_decision.get("decision")
            if suite_metrics is not None:
                suite_metrics["decision_total"] += 1
            if actual_decision == expected_decision:
                decision_correct += 1
                if suite_metrics is not None:
                    suite_metrics["decision_correct"] += 1
            else:
                misses.append({"case_id": case_id, "item": f"decision:{expected_decision}"})

        findings = _detected_findings(actual) if actual else []
        for required in _expected_items(expected.get("required_high_risk_categories")):
            critical_total += 1
            if suite_metrics is not None:
                suite_metrics["high_risk_total"] += 1
            found = any(
                finding.get("category") == required["category"]
                and (
                    required.get("material_id") is None
                    or finding.get("material_id") == required["material_id"]
                )
                for finding in findings
            )
            if found:
                critical_detected += 1
                if suite_metrics is not None:
                    suite_metrics["high_risk_detected"] += 1
            else:
                misses.append(
                    {"case_id": case_id, "item": f"high_risk:{required['category']}"}
                )

        if expected.get("findings_complete") is True:
            expected_findings = expected.get("expected_findings")
            if not isinstance(expected_findings, list):
                raise ValueError(
                    f"ground_truth case {case_id} sets findings_complete but has no expected_findings list"
                )
            finding_annotation_cases += 1
            expected_counts = Counter(
                (item["category"], item.get("material_id"))
                for item in _expected_items(expected_findings)
            )
            predicted_counts = Counter(
                (item.get("category"), item.get("material_id")) for item in findings
            )
            true_positive = sum((expected_counts & predicted_counts).values())
            false_positive = sum((predicted_counts - expected_counts).values())
            false_negative = sum((expected_counts - predicted_counts).values())
            finding_true_positive += true_positive
            finding_false_positive += false_positive
            finding_false_negative += false_negative
            misses.extend(
                {"case_id": case_id, "item": "finding_false_positive"}
                for _ in range(false_positive)
            )
            for item, count in (expected_counts - predicted_counts).items():
                misses.extend(
                    {"case_id": case_id, "item": f"finding_missed:{item[0]}"}
                    for _ in range(count)
                )

        actual_ocr_fields = actual.get("ocr_fields", []) if actual else []
        if not isinstance(actual_ocr_fields, list):
            actual_ocr_fields = []
        for required in _expected_items(expected.get("expected_ocr_fields")):
            field_name = required.get("field_name")
            expected_value = required.get("expected_value")
            if not isinstance(field_name, str) or not isinstance(expected_value, str):
                raise TypeError(
                    f"ground_truth case {case_id} OCR annotations require field_name and expected_value"
                )
            ocr_total += 1
            found_field = any(
                isinstance(field, dict)
                and field.get("field_name") == field_name
                and (
                    required.get("material_id") is None
                    or field.get("material_id") == required["material_id"]
                )
                and field.get("value_status") == "extracted"
                and _normalized_value(field.get("normalized_value") or field.get("raw_value"))
                == _normalized_value(expected_value)
                for field in actual_ocr_fields
            )
            if found_field:
                ocr_correct += 1
            else:
                misses.append({"case_id": case_id, "item": f"ocr:{field_name}"})

        actual_rules = _rule_ids(actual) if actual else set()
        required_rules = expected.get("required_rule_ids", [])
        if isinstance(required_rules, list):
            for rule_id in required_rules:
                if not isinstance(rule_id, str):
                    continue
                rule_total += 1
                if rule_id in actual_rules:
                    rule_detected += 1
                else:
                    misses.append({"case_id": case_id, "item": f"rule:{rule_id}"})

        for required in _expected_items(expected.get("required_findings")):
            expected_bbox = required.get("bbox")
            if not isinstance(expected_bbox, dict):
                continue
            bbox_total += 1
            candidates = [
                finding
                for finding in findings
                if finding.get("category") == required["category"]
                and finding.get("material_id") == required.get("material_id")
                and isinstance(finding.get("bbox"), dict)
            ]
            best_iou = max(
                (_bbox_iou(expected_bbox, finding["bbox"]) for finding in candidates),
                default=0.0,
            )
            if best_iou >= minimum_bbox_iou:
                bbox_localized += 1
            else:
                misses.append(
                    {
                        "case_id": case_id,
                        "item": f"bbox:{required['category']}:iou={best_iou:.3f}",
                    }
                )

        traces = actual.get("processing_trace", []) if actual else []
        if isinstance(traces, list):
            latencies = [
                trace.get("latency_ms")
                for trace in traces
                if isinstance(trace, dict)
                and isinstance(trace.get("latency_ms"), int)
                and trace["latency_ms"] >= 0
            ]
            if latencies:
                trace_latency_by_case.append(sum(latencies))

    decision_accuracy = decision_correct / decision_total if decision_total else None
    high_risk_recall = critical_detected / critical_total if critical_total else None
    rule_recall = rule_detected / rule_total if rule_total else None
    bbox_recall = bbox_localized / bbox_total if bbox_total else None
    finding_precision = (
        finding_true_positive / (finding_true_positive + finding_false_positive)
        if finding_true_positive + finding_false_positive
        else None
    )
    finding_recall = (
        finding_true_positive / (finding_true_positive + finding_false_negative)
        if finding_true_positive + finding_false_negative
        else None
    )
    ocr_accuracy = ocr_correct / ocr_total if ocr_total else None
    suite_results = {
        name: {
            "cases": counts["cases"],
            "decision_accuracy": (
                counts["decision_correct"] / counts["decision_total"]
                if counts["decision_total"]
                else None
            ),
            "high_risk_recall": (
                counts["high_risk_detected"] / counts["high_risk_total"]
                if counts["high_risk_total"]
                else None
            ),
        }
        for name, counts in sorted(suite_counts.items())
    }
    passed = (
        prediction_coverage == len(expected_by_id)
        and decision_accuracy is not None
        and decision_accuracy >= minimum_decision_accuracy
        and high_risk_recall is not None
        and high_risk_recall >= minimum_high_risk_recall
        and (
            minimum_finding_precision is None
            or (finding_precision is not None and finding_precision >= minimum_finding_precision)
        )
        and (minimum_ocr_accuracy is None or (ocr_accuracy is not None and ocr_accuracy >= minimum_ocr_accuracy))
    )

    return {
        "cases_expected": len(expected_by_id),
        "cases_predicted": prediction_coverage,
        "decision_accuracy": decision_accuracy,
        "high_risk_recall": high_risk_recall,
        "high_risk_scenes_expected": critical_total,
        "high_risk_scenes_detected": critical_detected,
        "finding_annotation_cases": finding_annotation_cases,
        "finding_precision": finding_precision,
        "finding_recall": finding_recall,
        "finding_false_positives": finding_false_positive,
        "finding_misses": finding_false_negative,
        "ocr_fields_evaluated": ocr_total,
        "ocr_field_accuracy": ocr_accuracy,
        "rule_recall": rule_recall,
        "bbox_recall_at_iou_threshold": bbox_recall,
        "bbox_iou_threshold": minimum_bbox_iou,
        "suite_results": suite_results,
        "trace_latency_ms_median": median(trace_latency_by_case)
        if trace_latency_by_case
        else None,
        "trace_latency_ms_max": max(trace_latency_by_case) if trace_latency_by_case else None,
        "misses": misses,
        "passed": passed,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--truth", required=True, type=Path, help="Ground-truth JSONL file")
    parser.add_argument("--predictions", required=True, type=Path, help="Predicted case JSONL file")
    parser.add_argument("--output", type=Path, help="Optional path for the JSON metrics report")
    parser.add_argument("--minimum-decision-accuracy", type=float, default=0.90)
    parser.add_argument("--minimum-high-risk-recall", type=float, default=1.0)
    parser.add_argument("--minimum-finding-precision", type=float)
    parser.add_argument("--minimum-ocr-accuracy", type=float)
    arguments = parser.parse_args()

    try:
        metrics = evaluate_cases(
            read_jsonl(arguments.truth),
            read_jsonl(arguments.predictions),
            minimum_decision_accuracy=arguments.minimum_decision_accuracy,
            minimum_high_risk_recall=arguments.minimum_high_risk_recall,
            minimum_finding_precision=arguments.minimum_finding_precision,
            minimum_ocr_accuracy=arguments.minimum_ocr_accuracy,
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Evaluation failed: {exc}", file=sys.stderr)
        return 2

    output = json.dumps(metrics, ensure_ascii=False, indent=2)
    print(output)
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(output + "\n", encoding="utf-8")
    return 0 if metrics["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
