from datetime import UTC, date, datetime

import httpx
from app.providers.geocoding import AmapGeocodingProvider
from app.providers.weather import OpenMeteoWeatherProvider


def test_confirmed_amap_address_coordinates_are_used_for_open_meteo() -> None:
    address = "广东省广州市天河区珠江新城"

    def geocoding_response(request: httpx.Request) -> httpx.Response:
        assert request.url.params["address"] == address
        return httpx.Response(
            200,
            json={
                "status": "1",
                "geocodes": [
                    {
                        "formatted_address": address,
                        "level": "兴趣点",
                        "location": "113.327455,23.125868",
                    }
                ],
            },
        )

    candidate = AmapGeocodingProvider(
        "test-key",
        transport=httpx.MockTransport(geocoding_response),
    ).lookup(address)[0]

    def weather_response(request: httpx.Request) -> httpx.Response:
        assert request.url.params["longitude"] == str(candidate.longitude)
        assert request.url.params["latitude"] == str(candidate.latitude)
        assert request.url.params["start_date"] == "2025-01-01"
        assert request.url.params["end_date"] == "2025-01-02"
        assert request.url.params["daily"] == (
            "wind_gusts_10m_max,wind_speed_10m_max,precipitation_sum,"
            "rain_sum,precipitation_hours,snowfall_sum"
        )
        return httpx.Response(
            200,
            json={
                "latitude": candidate.latitude,
                "longitude": candidate.longitude,
                "timezone": "UTC",
                "daily_units": {
                    "wind_gusts_10m_max": "m/s",
                    "wind_speed_10m_max": "m/s",
                    "precipitation_sum": "mm",
                    "rain_sum": "mm",
                    "precipitation_hours": "h",
                    "snowfall_sum": "cm",
                },
                "daily": {
                    "time": ["2025-01-01", "2025-01-02"],
                    "wind_gusts_10m_max": [22.0, 29.5],
                    "wind_speed_10m_max": [15.0, 20.0],
                    "precipitation_sum": [2.0, 40.0],
                    "rain_sum": [2.0, 40.0],
                    "precipitation_hours": [1.0, 16.0],
                    "snowfall_sum": [0.0, 0.0],
                },
            },
        )

    profile = OpenMeteoWeatherProvider(
        observation_start=date(2025, 1, 1),
        observation_end=date(2025, 1, 2),
        transport=httpx.MockTransport(weather_response),
        clock=lambda: datetime(2025, 1, 3, tzinfo=UTC),
    ).lookup(longitude=candidate.longitude, latitude=candidate.latitude)

    assert profile.longitude == candidate.longitude
    assert profile.latitude == candidate.latitude
    assert profile.historical_max_wind_m_s == 29.5
    assert profile.historical_max_daily_snowfall_cm == 0.0
    assert profile.historical_max_hail_mm is None
    assert profile.historical_max_snow_load_pa is None
