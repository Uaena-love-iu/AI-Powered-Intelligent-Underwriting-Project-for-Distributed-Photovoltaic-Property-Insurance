import re
from pathlib import Path

import pytest
from app.api.case_routes import _include_legacy_rule_explanations
from app.rules.explanations import explain_rule


def test_every_literal_rule_id_has_a_user_facing_explanation() -> None:
    app_root = Path(__file__).resolve().parents[2] / "backend" / "app"
    rule_pattern = re.compile(
        r"['\"]((?:PKG|ENV|DOC|MAT|IMG|ELEC|DISC|INDUSTRY|REQ|SYS|CAT)-[A-Z0-9-]+)['\"]"
    )
    rule_ids = {
        rule_id
        for source in app_root.rglob("*.py")
        for rule_id in rule_pattern.findall(source.read_text(encoding="utf-8"))
    }

    missing = [
        rule_id
        for rule_id in sorted(rule_ids)
        if explain_rule(rule_id).title == "规则说明待补充"
    ]

    assert not missing, f"Rule IDs without explanations: {missing}"


@pytest.mark.parametrize(
    ("rule_id", "expected_title"),
    [
        ("CAT-WIND-CRITICAL", "风灾历史筛查判为严重不足"),
        ("CAT-HAIL-CAPACITY-SHORTFALL", "雹灾历史筛查判为能力不足"),
        ("CAT-SNOW-LIMITED-MARGIN", "雪灾历史筛查判为余量有限"),
        ("CAT-WIND-CAPACITY-ADEQUATE", "风灾历史简化筛查结果"),
        ("CAT-WIND-COMPARABILITY-UNVERIFIED", "风灾数据口径未验证"),
        ("CAT-HAIL-COMPARABILITY-UNVERIFIED", "雹灾数据口径未验证"),
        ("CAT-SNOW-COMPARABILITY-UNVERIFIED", "雪灾数据口径未验证"),
    ],
)
def test_generated_catastrophe_rule_ids_are_explained(
    rule_id: str,
    expected_title: str,
) -> None:
    explanation = explain_rule(rule_id)

    assert explanation.title == expected_title
    assert explanation.trigger
    assert explanation.effect


def test_unknown_rule_id_is_marked_instead_of_silently_hidden() -> None:
    explanation = explain_rule("NEW-UNREGISTERED-RULE")

    assert explanation.title == "规则说明待补充"
    assert "未登记" in explanation.trigger


def test_legacy_saved_cases_get_rule_explanations_when_returned() -> None:
    record: dict[str, object] = {
        "case": {
            "decision": {
                "decisive_rule_ids": ["REQ-PROJECT-COORDINATES"],
            }
        }
    }

    enriched = _include_legacy_rule_explanations(record)
    saved_case = enriched["case"]
    assert isinstance(saved_case, dict)
    saved_decision = saved_case["decision"]
    assert isinstance(saved_decision, dict)
    explanations = saved_decision["rule_explanations"]
    assert isinstance(explanations, list)
    assert explanations[0]["rule_id"] == "REQ-PROJECT-COORDINATES"
    assert explanations[0]["title"] == "项目坐标缺失"
