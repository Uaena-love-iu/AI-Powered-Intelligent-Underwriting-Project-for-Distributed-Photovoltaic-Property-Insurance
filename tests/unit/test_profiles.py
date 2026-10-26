from datetime import date

import pytest
from app.contracts.enums import ExpectedLossRisk, ResistanceLevel
from app.contracts.profiles import (
    CatastropheAssessment,
    ComponentProfile,
    WeatherProfile,
)
from pydantic import ValidationError


def valid_component_payload() -> dict[str, object]:
    return {
        "component_model": "PV-MODULE-580W",
        "manufacturer": "示例组件厂商",
        "rated_power_w": 580,
        "hail_resistance_mm": 25,
        "wind_load_pa": 2400,
        "snow_load_pa": 5400,
        "source_url": "https://example.com/component.pdf",
        "source_name": "组件规格书",
        "retrieved_at": "2026-09-24T10:00:00+08:00",
        "match_confidence": 0.96,
    }


def valid_weather_payload() -> dict[str, object]:
    return {
        "longitude": 113.25,
        "latitude": 23.12,
        "historical_max_wind_m_s": 38.5,
        "historical_max_hail_mm": 20,
        "historical_max_snow_load_pa": None,
        "observation_start": "2023-01-01",
        "observation_end": "2025-12-31",
        "source_name": "示例气象数据服务",
        "source_url": "https://example.com/weather",
        "retrieved_at": "2026-09-24T10:10:00+08:00",
    }


def valid_assessment_payload() -> dict[str, object]:
    return {
        "resistance_level": "high",
        "expected_loss_risk": "medium",
        "factors": ["组件风荷载能力高于历史风速对应需求"],
        "explanation": "组件能力与当地历史气象数据比较后，风险等级为中等。",
        "triggered_rule_ids": ["CAT-WIND-001"],
        "requires_manual_review": False,
    }


def test_component_profile_accepts_valid_payload() -> None:
    profile = ComponentProfile.model_validate(valid_component_payload())

    assert profile.component_model == "PV-MODULE-580W"
    assert profile.rated_power_w == 580
    assert profile.match_confidence == 0.96


@pytest.mark.parametrize(
    "field_name",
    [
        "rated_power_w",
        "hail_resistance_mm",
        "wind_load_pa",
        "snow_load_pa",
    ],
)
def test_component_profile_rejects_non_positive_physical_values(
    field_name: str,
) -> None:
    payload = valid_component_payload()
    payload[field_name] = 0

    with pytest.raises(ValidationError):
        ComponentProfile.model_validate(payload)


@pytest.mark.parametrize("confidence", [-0.01, 1.01])
def test_component_profile_rejects_invalid_match_confidence(
    confidence: float,
) -> None:
    payload = valid_component_payload()
    payload["match_confidence"] = confidence

    with pytest.raises(ValidationError):
        ComponentProfile.model_validate(payload)


def test_weather_profile_accepts_valid_payload() -> None:
    profile = WeatherProfile.model_validate(valid_weather_payload())

    assert profile.observation_start == date(2023, 1, 1)
    assert profile.observation_end == date(2025, 12, 31)


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("longitude", 180.01),
        ("latitude", 90.01),
    ],
)
def test_weather_profile_rejects_invalid_coordinates(
    field_name: str,
    invalid_value: float,
) -> None:
    payload = valid_weather_payload()
    payload[field_name] = invalid_value

    with pytest.raises(ValidationError):
        WeatherProfile.model_validate(payload)


def test_weather_profile_rejects_reversed_observation_period() -> None:
    payload = valid_weather_payload()
    payload["observation_start"] = "2025-12-31"
    payload["observation_end"] = "2023-01-01"

    with pytest.raises(ValidationError):
        WeatherProfile.model_validate(payload)


def test_catastrophe_assessment_accepts_valid_payload() -> None:
    assessment = CatastropheAssessment.model_validate(valid_assessment_payload())

    assert assessment.resistance_level is ResistanceLevel.HIGH
    assert assessment.expected_loss_risk is ExpectedLossRisk.MEDIUM


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("resistance_level", "critical"),
        ("expected_loss_risk", "extreme"),
    ],
)
def test_catastrophe_assessment_rejects_invalid_levels(
    field_name: str,
    invalid_value: str,
) -> None:
    payload = valid_assessment_payload()
    payload[field_name] = invalid_value

    with pytest.raises(ValidationError):
        CatastropheAssessment.model_validate(payload)