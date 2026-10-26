import httpx
import pytest
from app.api.geocoding_routes import get_geocoding_provider
from app.main import app
from app.providers.common import ProviderError
from app.providers.geocoding import AmapGeocodingProvider, GeocodeCandidate
from fastapi.testclient import TestClient


def test_amap_geocoding_returns_reviewable_wgs84_candidate() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.params["key"] == "test-key"
        assert request.url.params["address"] == "广东省广州市天河区珠江新城"
        return httpx.Response(
            200,
            json={
                "status": "1",
                "geocodes": [
                    {
                        "formatted_address": "广东省广州市天河区珠江新城",
                        "level": "兴趣点",
                        "location": "113.327455,23.125868",
                    }
                ],
            },
        )

    provider = AmapGeocodingProvider(
        "test-key",
        transport=httpx.MockTransport(respond),
    )

    candidate = provider.lookup("广东省广州市天河区珠江新城")[0]

    assert candidate.formatted_address == "广东省广州市天河区珠江新城"
    assert candidate.level == "兴趣点"
    assert candidate.amap_longitude == 113.327455
    assert candidate.amap_latitude == 23.125868
    assert candidate.longitude == pytest.approx(113.322, abs=0.002)
    assert candidate.latitude == pytest.approx(23.128, abs=0.002)


def test_amap_geocoding_ignores_invalid_candidates() -> None:
    def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "1",
                "geocodes": [
                    {"formatted_address": "invalid", "location": "not-a-coordinate"},
                    {"formatted_address": "", "location": "113.2,23.1"},
                ],
            },
        )

    provider = AmapGeocodingProvider(
        "test-key",
        transport=httpx.MockTransport(respond),
    )

    assert provider.lookup("广东省广州市天河区") == []


def test_amap_geocoding_hides_upstream_error_details() -> None:
    secret = "do-not-expose"

    def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text=secret)

    provider = AmapGeocodingProvider(
        "test-key",
        transport=httpx.MockTransport(respond),
    )

    with pytest.raises(ProviderError) as caught:
        provider.lookup("广东省广州市天河区")

    assert secret not in str(caught.value)


def test_address_endpoint_requires_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PV_AMAP_WEB_SERVICE_KEY", "")

    response = TestClient(app).get(
        "/api/v1/locations/resolve",
        params={"address": "广东省广州市天河区珠江新城"},
    )

    assert response.status_code == 503


def test_address_endpoint_returns_candidates_after_explicit_lookup() -> None:
    class StubProvider:
        def lookup(self, address: str) -> list[GeocodeCandidate]:
            assert address == "广东省广州市天河区珠江新城"
            return [
                GeocodeCandidate(
                    formatted_address=address,
                    level="兴趣点",
                    longitude=113.322,
                    latitude=23.128,
                    amap_longitude=113.327,
                    amap_latitude=23.126,
                )
            ]

    original = app.dependency_overrides.get(get_geocoding_provider)
    app.dependency_overrides[get_geocoding_provider] = StubProvider
    try:
        response = TestClient(app).get(
            "/api/v1/locations/resolve",
            params={"address": "广东省广州市天河区珠江新城"},
        )
    finally:
        app.dependency_overrides.pop(get_geocoding_provider, None)
        if original is not None:
            app.dependency_overrides[get_geocoding_provider] = original

    assert response.status_code == 200
    payload = response.json()
    assert payload["candidates"][0]["longitude"] == 113.322
    assert payload["coordinate_system"] == "WGS84 approximate"
    assert "核对" in payload["note"]
