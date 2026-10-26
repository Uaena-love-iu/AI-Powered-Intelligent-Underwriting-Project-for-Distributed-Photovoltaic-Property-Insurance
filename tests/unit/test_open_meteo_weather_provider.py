from datetime import UTC, date, datetime

import httpx
import pytest
from app.providers import ProviderError
from app.providers.weather import OpenMeteoWeatherProvider

DAILY_UNITS = {
    "wind_gusts_10m_max": "m/s",
    "wind_speed_10m_max": "m/s",
    "precipitation_sum": "mm",
    "rain_sum": "mm",
    "precipitation_hours": "h",
    "snowfall_sum": "cm",
}


def provider(
    handler,
) -> OpenMeteoWeatherProvider:
    return OpenMeteoWeatherProvider(
        observation_start=date(2021, 1, 1),
        observation_end=date(2025, 12, 31),
        timeout_seconds=5,
        transport=httpx.MockTransport(handler),
        clock=lambda: datetime(2026, 9, 26, 4, 0, tzinfo=UTC),
    )


def test_returns_maximum_historical_wind_and_request_parameters() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["latitude"] == "23.12"
        assert request.url.params["longitude"] == "113.25"
        assert request.url.params["start_date"] == "2021-01-01"
        assert request.url.params["end_date"] == "2025-12-31"
        assert request.url.params["daily"] == (
            "wind_gusts_10m_max,wind_speed_10m_max,precipitation_sum,"
            "rain_sum,precipitation_hours,snowfall_sum"
        )
        assert request.url.params["timezone"] == "UTC"
        assert request.url.params["wind_speed_unit"] == "ms"
        assert request.url.params["cell_selection"] == "land"
        return httpx.Response(
            200,
            json={
                "latitude": 23.125,
                "longitude": 113.25,
                "elevation": 18.0,
                "timezone": "UTC",
                "daily_units": DAILY_UNITS,
                "daily": {
                    "time": ["2025-01-01", "2025-01-02", "2025-01-03"],
                    "wind_gusts_10m_max": [18.5, None, 31.2],
                    "wind_speed_10m_max": [10.0, None, 20.0],
                    "precipitation_sum": [5.0, 80.0, 10.0],
                    "rain_sum": [5.0, 80.0, 0.0],
                    "precipitation_hours": [2.0, 18.0, 0.0],
                    "snowfall_sum": [2.0, 12.5, 0.0],
                }
            },
        )

    result = provider(handler).lookup(longitude=113.25, latitude=23.12)

    assert result.historical_max_wind_m_s == 31.2
    assert result.historical_max_daily_wind_speed_m_s == 20.0
    assert result.historical_max_daily_precipitation_mm == 80.0
    assert result.historical_max_daily_rain_mm == 80.0
    assert result.historical_max_daily_precipitation_hours == 18.0
    assert result.historical_max_hail_mm is None
    assert result.historical_max_snow_load_pa is None
    assert result.historical_max_daily_snowfall_cm == 12.5
    assert result.observation_start == date(2021, 1, 1)
    assert result.observation_end == date(2025, 12, 31)
    assert result.returned_data_start == date(2025, 1, 1)
    assert result.returned_data_end == date(2025, 1, 3)
    assert result.expected_day_count == 1826
    assert result.returned_day_count == 3
    assert result.wind_valid_day_count == 2
    assert result.wind_speed_valid_day_count == 2
    assert result.precipitation_valid_day_count == 3
    assert result.rain_valid_day_count == 3
    assert result.precipitation_hours_valid_day_count == 3
    assert result.snowfall_valid_day_count == 3
    assert result.grid_latitude == 23.125
    assert result.grid_longitude == 113.25
    assert 0.5 < result.grid_distance_km < 0.6
    assert result.grid_elevation_m == 18.0
    assert result.timezone == "UTC"
    assert result.grid_selection_method == "land"
    assert "Best Match" in result.source_name
    assert any("阵风有效日数 2/1826" in note for note in result.data_quality_notes)
    assert result.retrieved_at == datetime(2026, 9, 26, 4, 0, tzinfo=UTC)


def test_all_null_wind_values_are_reported_as_missing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json={
                "latitude": 23.12,
                "longitude": 113.25,
                "daily_units": DAILY_UNITS,
                "daily": {
                    "time": ["2025-01-01", "2025-01-02"],
                    "wind_gusts_10m_max": [None, None],
                    "wind_speed_10m_max": [None, None],
                    "precipitation_sum": [None, None],
                    "rain_sum": [None, None],
                    "precipitation_hours": [None, None],
                    "snowfall_sum": [None, None],
                }
            },
        )

    result = provider(handler).lookup(longitude=113.25, latitude=23.12)

    assert result.historical_max_wind_m_s is None
    assert result.wind_valid_day_count == 0
    assert result.snowfall_valid_day_count == 0


@pytest.mark.parametrize("status_code", [400, 429, 500])
def test_http_errors_are_sanitized(status_code: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            status_code,
            json={"reason": "sensitive upstream details"},
        )

    with pytest.raises(ProviderError, match="weather provider request failed") as exc:
        provider(handler).lookup(longitude=113.25, latitude=23.12)

    assert "sensitive upstream details" not in str(exc.value)


def test_invalid_series_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json={
                "daily_units": {
                    "wind_gusts_10m_max": "m/s",
                    "snowfall_sum": "cm",
                },
                "daily": {
                    "time": ["2025-01-01"],
                    "wind_gusts_10m_max": [20.0, 30.0],
                }
            },
        )

    with pytest.raises(ProviderError, match="invalid response"):
        provider(handler).lookup(longitude=113.25, latitude=23.12)


def test_invalid_snowfall_series_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json={
                "daily_units": {
                    "wind_gusts_10m_max": "m/s",
                    "snowfall_sum": "cm",
                },
                "daily": {
                    "time": ["2025-01-01", "2025-01-02"],
                    "wind_gusts_10m_max": [20.0, 30.0],
                    "snowfall_sum": [1.0],
                }
            },
        )

    with pytest.raises(ProviderError, match="invalid response"):
        provider(handler).lookup(longitude=113.25, latitude=23.12)


def test_rejects_unrequested_or_ambiguous_units() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json={
                "daily_units": {**DAILY_UNITS, "wind_gusts_10m_max": "km/h"},
                "daily": {
                    "time": ["2025-01-01"],
                    "wind_gusts_10m_max": [20.0],
                    "wind_speed_10m_max": [20.0],
                    "precipitation_sum": [0.0],
                    "rain_sum": [0.0],
                    "precipitation_hours": [0.0],
                    "snowfall_sum": [0.0],
                },
            },
        )

    with pytest.raises(ProviderError, match="unsupported units"):
        provider(handler).lookup(longitude=113.25, latitude=23.12)


def test_rejects_dates_outside_requested_period() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json={
                "daily_units": DAILY_UNITS,
                "daily": {
                    "time": ["2026-01-01"],
                    "wind_gusts_10m_max": [20.0],
                    "wind_speed_10m_max": [20.0],
                    "precipitation_sum": [0.0],
                    "rain_sum": [0.0],
                    "precipitation_hours": [0.0],
                    "snowfall_sum": [0.0],
                },
            },
        )

    with pytest.raises(ProviderError, match="outside the requested period"):
        provider(handler).lookup(longitude=113.25, latitude=23.12)


def test_rejects_invalid_configuration() -> None:
    with pytest.raises(ValueError, match="observation_start"):
        OpenMeteoWeatherProvider(
            observation_start=date(2025, 1, 2),
            observation_end=date(2025, 1, 1),
        )

    with pytest.raises(ValueError, match="timeout_seconds"):
        OpenMeteoWeatherProvider(
            observation_start=date(2025, 1, 1),
            observation_end=date(2025, 1, 2),
            timeout_seconds=0,
        )
