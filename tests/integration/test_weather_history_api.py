from datetime import date

import httpx
from app.main import app
from app.providers.weather.history import HistoricalWeatherClient
from fastapi.testclient import TestClient


def test_history_client_keeps_missing_values_and_source_grids_separate() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["timezone"] == "Asia/Shanghai"
        assert request.url.params["wind_speed_unit"] == "ms"
        if request.url.params["models"] == "era5_land":
            return httpx.Response(200, json={
                "latitude": 36.2, "longitude": 116.1,
                "hourly_units": {"time": "iso8601", "snow_depth": "m"},
                "hourly": {"time": ["2024-01-01T00:00", "2024-01-01T01:00"],
                           "snow_depth": [0.0, None]},
            })
        assert request.url.params["models"] == "era5"
        return httpx.Response(200, json={
            "latitude": 36.25, "longitude": 116.0,
            "hourly_units": {
                "time": "iso8601", "wind_speed_10m": "m/s", "wind_gusts_10m": "m/s",
                "wind_direction_10m": "°", "temperature_2m": "°C",
                "surface_pressure": "hPa", "precipitation": "mm", "rain": "mm",
                "snowfall": "cm",
            },
            "hourly": {
                "time": ["2024-01-01T00:00", "2024-01-01T01:00"],
                "wind_speed_10m": [2.0, 4.0], "wind_gusts_10m": [None, 9.0],
                "wind_direction_10m": [90, 100], "temperature_2m": [-1.0, 0.0],
                "surface_pressure": [1000.0, 1002.0], "precipitation": [0.0, 2.0],
                "rain": [0.0, None], "snowfall": [0.0, 1.0],
            },
        })

    client = HistoricalWeatherClient(transport=httpx.MockTransport(handler))
    result = client.lookup(
        latitude=36.24, longitude=116.06,
        start_date=date(2024, 1, 1), end_date=date(2024, 1, 1),
    )

    day = result["data"][0]
    assert day["wind_gusts_10m_max"] == 9.0
    assert day["wind_gusts_10m_valid_hours"] == 1
    assert day["precipitation_sum"] == 2.0
    assert day["rain_sum"] == 0.0
    assert day["snowfall_sum"] == 1.0
    assert day["ground_snow_depth_max"] == 0.0
    assert result["sources"]["ground_snow_depth"]["grid_longitude"] == 116.1
    assert result["hail"]["max_diameter_mm"] is None
    assert result["hail"]["status"] == "unavailable"


def test_history_endpoint_validates_location_and_date_range(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PV_AMAP_WEB_SERVICE_KEY", "")
    monkeypatch.setattr(
        "app.location.geocoding.PROJECT_ENV_PATH",
        tmp_path / "missing.env",
    )
    client = TestClient(app)
    base = {"start_date": "2024-01-01", "end_date": "2024-01-02"}
    assert client.post("/api/v1/weather/history", json=base).status_code == 422
    assert client.post("/api/v1/weather/history", json={
        **base, "latitude": 36.24,
    }).status_code == 422
    assert client.post("/api/v1/weather/history", json={
        **base, "address": "山东省泰安市某工业园区1号",
    }).status_code == 503
    assert client.post("/api/v1/weather/history", json={
        **base, "latitude": 36.24, "longitude": 116.06,
        "start_date": "2000-01-01", "end_date": "2025-12-31",
    }).status_code == 422


def test_history_endpoint_returns_address_conversion_and_weather(monkeypatch) -> None:
    from app.api import weather_history_routes as routes
    from app.location.geocoding import GeocodeResult

    class FakeGeocoder:
        def lookup(self, address, *, city=None):
            assert address == "山东省泰安市某工业园区1号"
            return GeocodeResult(116.06, 36.24, "门牌号", address, True)

    class FakeWeatherClient:
        def __init__(self, **kwargs):
            pass

        def lookup(self, **kwargs):
            assert kwargs["resolution"] == "yearly"
            return {"data": [{"period": "2024", "wind_gusts_10m_max": 20.0}],
                    "hail": {"status": "unavailable", "max_diameter_mm": None}}

    monkeypatch.setattr(routes.AmapGeocoder, "from_environment", lambda: FakeGeocoder())
    monkeypatch.setattr(routes, "HistoricalWeatherClient", FakeWeatherClient)
    response = TestClient(app).post("/api/v1/weather/history", json={
        "address": "山东省泰安市某工业园区1号",
        "start_date": "2024-01-01", "end_date": "2024-12-31", "resolution": "yearly",
    })
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["location"]["original_coordinates"]["system"] == "GCJ-02"
    assert result["location"]["weather_coordinates"]["system"] == "WGS84"
    assert result["location"]["verified_site_location"] is False
    assert result["data"][0]["wind_gusts_10m_max"] == 20.0


def test_history_api_explains_multiple_candidates_without_fetching_weather(monkeypatch):
    from app.api import weather_history_routes as routes
    from app.location.geocoding import AmapGeocoder
    def response(_):
        return httpx.Response(200, json={'status': '1', 'geocodes': [
            {'formatted_address': '北京市甲路1号', 'location': '116.397,39.908', 'level': '门牌号'},
            {'formatted_address': '北京市乙路1号', 'location': '116.398,39.909', 'level': '门牌号'},
        ]})
    geocoder = AmapGeocoder('test-key', transport=httpx.MockTransport(response))
    monkeypatch.setattr(routes.AmapGeocoder, 'from_environment', lambda: geocoder)
    def must_not_fetch(**kwargs):
        raise AssertionError('Ambiguous address must not initiate weather lookup')
    monkeypatch.setattr(routes, 'HistoricalWeatherClient', must_not_fetch)
    result = TestClient(app).post('/api/v1/weather/history', json={
        'address': '北京市甲路1号', 'start_date': '2024-01-01', 'end_date': '2024-01-02'})
    assert result.status_code == 422
    assert '多个候选' in result.json()['detail']
    assert '北京市乙路1号' in result.json()['detail']
