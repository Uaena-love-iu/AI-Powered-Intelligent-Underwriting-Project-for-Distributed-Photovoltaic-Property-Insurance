from datetime import date

import pytest
from app.api.weather_dependencies import build_weather_provider, get_weather_provider
from app.providers.weather import OpenMeteoWeatherProvider
from fastapi import HTTPException


def test_builds_provider_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PV_WEATHER_BASE_URL", "https://weather.example.test/archive")
    monkeypatch.setenv("PV_WEATHER_LOOKBACK_DAYS", "30")
    monkeypatch.setenv("PV_WEATHER_DATA_LAG_DAYS", "5")
    monkeypatch.setenv("PV_WEATHER_TIMEOUT_SECONDS", "12.5")

    provider = build_weather_provider(today=date(2026, 9, 26))

    assert isinstance(provider, OpenMeteoWeatherProvider)
    assert provider.base_url == "https://weather.example.test/archive"
    assert provider.observation_start == date(2026, 8, 22)
    assert provider.observation_end == date(2026, 9, 21)
    assert provider.timeout_seconds == 12.5


def test_invalid_configuration_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PV_WEATHER_LOOKBACK_DAYS", "0")

    with pytest.raises(ValueError, match="positive"):
        build_weather_provider(today=date(2026, 9, 26))


def test_fastapi_dependency_sanitizes_invalid_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PV_WEATHER_TIMEOUT_SECONDS", "invalid")

    with pytest.raises(HTTPException) as exc:
        get_weather_provider()

    assert exc.value.status_code == 503
    assert exc.value.detail == "Weather provider configuration is invalid"
