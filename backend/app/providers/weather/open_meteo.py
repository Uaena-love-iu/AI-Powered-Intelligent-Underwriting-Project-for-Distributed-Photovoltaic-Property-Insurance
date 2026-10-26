from collections.abc import Callable
from datetime import UTC, date, datetime
from math import asin, cos, isfinite, radians, sin, sqrt
from typing import Self

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.contracts import WeatherProfile

from ..common import ProviderError
from .base import WeatherProvider

OPEN_METEO_SOURCE_URL = "https://open-meteo.com/en/docs/historical-weather-api"


class _DailyWeather(BaseModel):
    model_config = ConfigDict(extra="ignore")

    time: list[date]
    wind_gusts_10m_max: list[float | None]
    wind_speed_10m_max: list[float | None]
    precipitation_sum: list[float | None]
    rain_sum: list[float | None]
    precipitation_hours: list[float | None]
    snowfall_sum: list[float | None]

    @model_validator(mode="after")
    def validate_series_lengths(self) -> Self:
        for field_name in (
            "wind_gusts_10m_max",
            "wind_speed_10m_max",
            "precipitation_sum",
            "rain_sum",
            "precipitation_hours",
            "snowfall_sum",
        ):
            if len(self.time) != len(getattr(self, field_name)):
                raise ValueError(f"daily {field_name} series length does not match")
        if len(set(self.time)) != len(self.time):
            raise ValueError("daily weather dates must be unique")
        return self


class _OpenMeteoResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    latitude: float | None = Field(default=None, ge=-90.0, le=90.0)
    longitude: float | None = Field(default=None, ge=-180.0, le=180.0)
    elevation: float | None = None
    timezone: str | None = None
    daily_units: dict[str, str]
    daily: _DailyWeather


class OpenMeteoWeatherProvider(WeatherProvider):
    """通过 Open-Meteo 查询风、降水和降雪的历史气候背景资料。"""

    def __init__(
        self,
        *,
        observation_start: date,
        observation_end: date,
        base_url: str = "https://archive-api.open-meteo.com/v1/archive",
        timeout_seconds: float = 30.0,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if observation_start > observation_end:
            raise ValueError("observation_start must not be after observation_end")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if not base_url.startswith(("https://", "http://")):
            raise ValueError("base_url must be an HTTP URL")

        self.observation_start = observation_start
        self.observation_end = observation_end
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.transport = transport
        self._clock = clock or (lambda: datetime.now(UTC))

    @property
    def name(self) -> str:
        return "open-meteo-historical-weather"

    def lookup(
        self,
        *,
        longitude: float,
        latitude: float,
    ) -> WeatherProfile:
        if not -180.0 <= longitude <= 180.0:
            raise ValueError("longitude is outside the valid range")
        if not -90.0 <= latitude <= 90.0:
            raise ValueError("latitude is outside the valid range")

        try:
            with httpx.Client(
                timeout=self.timeout_seconds,
                transport=self.transport,
            ) as client:
                response = client.get(
                    self.base_url,
                    params={
                        "latitude": latitude,
                        "longitude": longitude,
                        "start_date": self.observation_start.isoformat(),
                        "end_date": self.observation_end.isoformat(),
                        "daily": (
                            "wind_gusts_10m_max,wind_speed_10m_max,"
                            "precipitation_sum,rain_sum,precipitation_hours,snowfall_sum"
                        ),
                        "timezone": "UTC",
                        "wind_speed_unit": "ms",
                        "cell_selection": "land",
                    },
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderError("weather provider request failed") from exc

        try:
            payload = _OpenMeteoResponse.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise ProviderError("weather provider returned an invalid response") from exc

        wind_unit = payload.daily_units.get("wind_gusts_10m_max")
        wind_speed_unit = payload.daily_units.get("wind_speed_10m_max")
        precipitation_unit = payload.daily_units.get("precipitation_sum")
        rain_unit = payload.daily_units.get("rain_sum")
        precipitation_hours_unit = payload.daily_units.get("precipitation_hours")
        snowfall_unit = payload.daily_units.get("snowfall_sum")
        if (
            wind_unit not in {"m/s", "ms-1"}
            or wind_speed_unit not in {"m/s", "ms-1"}
            or precipitation_unit != "mm"
            or rain_unit != "mm"
            or precipitation_hours_unit not in {"h", "hour"}
            or snowfall_unit != "cm"
        ):
            raise ProviderError("weather provider returned unsupported units")
        if (payload.latitude is None) != (payload.longitude is None):
            raise ProviderError("weather provider returned incomplete grid coordinates")
        if payload.timezone is not None and payload.timezone.upper() not in {"UTC", "GMT"}:
            raise ProviderError("weather provider returned an unexpected timezone")

        requested_days = (self.observation_end - self.observation_start).days + 1
        if any(
            day < self.observation_start or day > self.observation_end
            for day in payload.daily.time
        ):
            raise ProviderError("weather provider returned dates outside the requested period")

        wind_values = [
            value
            for value in payload.daily.wind_gusts_10m_max
            if value is not None and isfinite(value) and value >= 0.0
        ]
        wind_speed_values = [
            value
            for value in payload.daily.wind_speed_10m_max
            if value is not None and isfinite(value) and value >= 0.0
        ]
        precipitation_values = [
            value
            for value in payload.daily.precipitation_sum
            if value is not None and isfinite(value) and value >= 0.0
        ]
        rain_values = [
            value
            for value in payload.daily.rain_sum
            if value is not None and isfinite(value) and value >= 0.0
        ]
        precipitation_hours_values = [
            value
            for value in payload.daily.precipitation_hours
            if value is not None and isfinite(value) and 0.0 <= value <= 24.0
        ]
        snowfall_values = [
            value
            for value in payload.daily.snowfall_sum
            if value is not None and isfinite(value) and value >= 0.0
        ]
        missing_grid_metadata = payload.latitude is None or payload.longitude is None
        lookup_notes: list[str] = []
        if missing_grid_metadata:
            lookup_notes.append("上游未返回实际气象网格经纬度，无法核对网格点与项目点距离")
        if payload.elevation is None:
            lookup_notes.append("上游未返回网格高程元数据")
        if payload.timezone is None:
            lookup_notes.append("上游未回报时区名称；日汇总按请求的 UTC 时区处理")
        if len(wind_values) < requested_days:
            lookup_notes.append(
                f"阵风有效日数 {len(wind_values)}/{requested_days}，期间存在缺测或未返回日期"
            )
        if len(snowfall_values) < requested_days:
            lookup_notes.append(
                f"降雪有效日数 {len(snowfall_values)}/{requested_days}，期间存在缺测或未返回日期"
            )
        for label, values in (
            ("日最大持续风速", wind_speed_values),
            ("日降水量", precipitation_values),
            ("日降雨量", rain_values),
            ("降水小时数", precipitation_hours_values),
        ):
            if len(values) < requested_days:
                lookup_notes.append(
                    f"{label}有效日数 {len(values)}/{requested_days}，期间存在缺测或未返回日期"
                )
        returned_dates = payload.daily.time
        grid_distance_km = None
        if payload.latitude is not None and payload.longitude is not None:
            latitude_delta = radians(payload.latitude - latitude)
            longitude_delta = radians(payload.longitude - longitude)
            haversine = (
                sin(latitude_delta / 2) ** 2
                + cos(radians(latitude))
                * cos(radians(payload.latitude))
                * sin(longitude_delta / 2) ** 2
            )
            grid_distance_km = 2 * 6371.0088 * asin(sqrt(min(1.0, haversine)))

        return WeatherProfile(
            longitude=longitude,
            latitude=latitude,
            historical_max_wind_m_s=max(wind_values, default=None),
            historical_max_daily_wind_speed_m_s=max(
                wind_speed_values, default=None
            ),
            historical_max_daily_precipitation_mm=max(
                precipitation_values, default=None
            ),
            historical_max_daily_rain_mm=max(rain_values, default=None),
            historical_max_daily_precipitation_hours=max(
                precipitation_hours_values, default=None
            ),
            historical_max_hail_mm=None,
            historical_max_snow_load_pa=None,
            historical_max_daily_snowfall_cm=max(snowfall_values, default=None),
            observation_start=self.observation_start,
            observation_end=self.observation_end,
            returned_data_start=min(returned_dates, default=None),
            returned_data_end=max(returned_dates, default=None),
            expected_day_count=requested_days,
            returned_day_count=len(returned_dates),
            wind_valid_day_count=len(wind_values),
            wind_speed_valid_day_count=len(wind_speed_values),
            precipitation_valid_day_count=len(precipitation_values),
            rain_valid_day_count=len(rain_values),
            precipitation_hours_valid_day_count=len(precipitation_hours_values),
            snowfall_valid_day_count=len(snowfall_values),
            grid_longitude=payload.longitude,
            grid_latitude=payload.latitude,
            grid_distance_km=grid_distance_km,
            grid_elevation_m=payload.elevation,
            timezone=payload.timezone or "UTC (requested)",
            dataset_selection="best_match (Open-Meteo API default)",
            grid_selection_method="land",
            wind_unit=wind_unit,
            wind_speed_unit=wind_speed_unit,
            precipitation_unit=precipitation_unit,
            rain_unit=rain_unit,
            precipitation_hours_unit=precipitation_hours_unit,
            snowfall_unit=snowfall_unit,
            source_name="Open-Meteo Historical Weather API (Best Match reanalysis)",
            source_url=OPEN_METEO_SOURCE_URL,
            retrieved_at=self._clock(),
            data_quality_notes=lookup_notes,
        )
