from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from app.providers import BaiduMapGeocoder, ProviderError
from app.settings import BaiduMapSettings


def make_provider(handler) -> BaiduMapGeocoder:
    return BaiduMapGeocoder(
        BaiduMapSettings(api_key="test-map-key"),
        transport=httpx.MockTransport(handler),
    )


def geocode_payload(
    *,
    confidence: int = 88,
    comprehension: int = 91,
    precise: int = 1,
) -> dict[str, object]:
    return {
        "status": 0,
        "result": {
            "formatted_address": "广东省示例市示例区示例路 1 号",
            "location": {"lng": 113.25, "lat": 23.12},
            "confidence": confidence,
            "comprehension": comprehension,
            "precise": precise,
            "level": "门址",
        },
    }


def test_geocode_requests_bd09_coordinates_and_returns_match_quality() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        query = request.url.params
        assert query["address"] == "广东省示例市示例区示例路 1 号"
        assert query["ret_coordtype"] == "bd09ll"
        assert query["city"] == "示例市"
        assert query["ak"] == "test-map-key"
        return httpx.Response(200, json=geocode_payload())

    result = make_provider(handler).geocode(
        "广东省示例市示例区示例路 1 号",
        city="示例市",
    )

    assert result.longitude == 113.25
    assert result.latitude == 23.12
    assert result.confidence == 88
    assert result.comprehension == 91
    assert result.precise is True


def test_map_url_uses_provider_owned_marker_page_and_does_not_expose_key() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=geocode_payload())

    provider = make_provider(handler)
    result = provider.geocode("广东省示例市示例区示例路 1 号")
    map_url = provider.build_map_url(geocode=result)
    parsed = urlparse(map_url)
    query = parse_qs(parsed.query)

    assert parsed.scheme == "https"
    assert parsed.netloc == "api.map.baidu.com"
    assert query["location"] == ["23.12,113.25"]
    assert query["coord_type"] == ["bd09ll"]
    assert "test-map-key" not in map_url


def test_geocoder_rejects_oversized_address_before_network_call() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        pytest.fail("oversized address must not be sent")

    with pytest.raises(ProviderError, match="provider limit"):
        make_provider(handler).geocode("中" * 50)


def test_geocoder_sanitizes_provider_failures() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": 1, "message": "invalid key"})

    with pytest.raises(ProviderError, match="could not match") as error:
        make_provider(handler).geocode("示例地址")
    assert "test-map-key" not in str(error.value)
