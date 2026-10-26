import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from fastapi import HTTPException, status

from app.providers.weather import WeatherProvider
from app.providers.weather.series import SeriesWeatherProvider

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_WEATHER_BASE_URL = "https://archive-api.open-meteo.com/v1/archive"
DEFAULT_WEATHER_LOOKBACK_DAYS = 3650
DEFAULT_WEATHER_DATA_LAG_DAYS = 7
DEFAULT_WEATHER_TIMEOUT_SECONDS = 30.0


def _positive_int(name: str, default: int) -> int:
    value = os.getenv(name, str(default)).strip()
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if parsed <= 0:
        raise ValueError(f"{name} must be positive")
    return parsed


def _non_negative_int(name: str, default: int) -> int:
    value = os.getenv(name, str(default)).strip()
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if parsed < 0:
        raise ValueError(f"{name} must not be negative")
    return parsed


def _positive_float(name: str, default: float) -> float:
    value = os.getenv(name, str(default)).strip()
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if parsed <= 0:
        raise ValueError(f"{name} must be positive")
    return parsed


def build_weather_provider(*, today: date | None = None) -> WeatherProvider:
    """根据环境变量创建历史气象 Provider。"""

    load_dotenv(dotenv_path=PROJECT_ROOT / ".env", override=False)
    current_date = datetime.now(UTC).date() if today is None else today
    lookback_days = _positive_int(
        "PV_WEATHER_LOOKBACK_DAYS",
        DEFAULT_WEATHER_LOOKBACK_DAYS,
    )
    data_lag_days = _non_negative_int(
        "PV_WEATHER_DATA_LAG_DAYS",
        DEFAULT_WEATHER_DATA_LAG_DAYS,
    )
    timeout_seconds = _positive_float(
        "PV_WEATHER_TIMEOUT_SECONDS",
        DEFAULT_WEATHER_TIMEOUT_SECONDS,
    )
    observation_end = current_date - timedelta(days=data_lag_days)
    observation_start = observation_end - timedelta(days=lookback_days)

    return SeriesWeatherProvider(
        base_url=os.getenv(
            "PV_WEATHER_BASE_URL",
            DEFAULT_WEATHER_BASE_URL,
        ).strip()
        or DEFAULT_WEATHER_BASE_URL,
        observation_start=observation_start,
        observation_end=observation_end,
        timeout_seconds=timeout_seconds,
    )


def get_weather_provider() -> WeatherProvider:
    """为 FastAPI 请求创建历史气象 Provider。"""

    try:
        return build_weather_provider()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Weather provider configuration is invalid",
        ) from exc
