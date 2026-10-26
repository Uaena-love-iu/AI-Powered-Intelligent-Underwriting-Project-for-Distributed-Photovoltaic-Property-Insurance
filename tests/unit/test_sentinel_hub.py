from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from app.contracts import SentinelEnvironmentAnalysis
from app.providers.common import ProviderError
from app.providers.sentinel_hub import (
    CATALOG_URL,
    MAX_RESPONSE_BYTES,
    PROCESS_URL,
    TOKEN_URL,
    SentinelHubProvider,
)
from app.providers.vision.sentinel_context import SentinelContextAnalyzer
from app.settings import SentinelHubSettings, VlmSettings
from PIL import Image


def sentinel_settings(**updates) -> SentinelHubSettings:
    values = {
        "client_id": "test-client",
        "client_secret": "test-secret",
        "timeout_seconds": 2,
        "lookback_days": 180,
        "max_cloud_cover_percent": 40,
        "radius_m": 1000,
    }
    values.update(updates)
    return SentinelHubSettings(**values)


def test_settings_load_credentials_and_reject_out_of_range_values(monkeypatch) -> None:
    env = Path.cwd() / f".sentinel-test-{uuid4().hex}.env"
    try:
        env.write_text(
            "PV_SENTINEL_HUB_CLIENT_ID=id\n"
            "PV_SENTINEL_HUB_CLIENT_SECRET=secret\n"
            "PV_SENTINEL_RADIUS_M=1500\n",
            encoding="utf-8",
        )
        monkeypatch.delenv("PV_SENTINEL_HUB_CLIENT_ID", raising=False)
        monkeypatch.delenv("PV_SENTINEL_HUB_CLIENT_SECRET", raising=False)
        settings = SentinelHubSettings.from_environment(env)
        assert settings.client_id == "id"
        assert settings.client_secret == "secret"
        assert settings.radius_m == 1500
        assert "secret" not in repr(settings)

        monkeypatch.setenv("PV_SENTINEL_RADIUS_M", "9000")
        with pytest.raises(ValueError, match="PV_SENTINEL_RADIUS_M"):
            SentinelHubSettings.from_environment(env)
    finally:
        env.unlink(missing_ok=True)


def test_provider_queries_catalog_masks_clouds_and_returns_attributed_png() -> None:
    requests: list[httpx.Request] = []
    buffer = BytesIO()
    Image.new("RGBA", (200, 200), (30, 80, 110, 255)).save(buffer, format="PNG")
    png = buffer.getvalue()
    items = [
        {
            "id": "new-cloudy",
            "properties": {"datetime": "2026-09-15T10:00:00Z", "eo:cloud_cover": 80},
        },
        {
            "id": "recent-clear",
            "properties": {"datetime": "2026-09-10T10:00:00Z", "eo:cloud_cover": 15.5},
        },
        {
            "id": "older-clearer",
            "properties": {"datetime": "2026-08-01T10:00:00Z", "eo:cloud_cover": 5},
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url == httpx.URL(TOKEN_URL):
            return httpx.Response(200, json={"access_token": "test-token", "expires_in": 300})
        if request.url == httpx.URL(CATALOG_URL):
            assert request.headers["Authorization"] == "Bearer test-token"
            return httpx.Response(200, json={"features": items})
        if request.url == httpx.URL(PROCESS_URL):
            assert request.headers["Authorization"] == "Bearer test-token"
            return httpx.Response(200, content=png, headers={"content-type": "image/png"})
        raise AssertionError(request.url)

    provider = SentinelHubProvider(
        sentinel_settings(),
        transport=httpx.MockTransport(handler),
        now=datetime(2026, 10, 1, tzinfo=UTC),
    )
    result = provider.fetch(113.2, 23.1)

    assert result.metadata.scene_id == "recent-clear"
    assert result.metadata.tile_cloud_cover_percent == 15.5
    assert result.metadata.acquired_at.date().isoformat() == "2026-09-10"
    assert result.metadata.pixel_size_m == 10
    assert result.metadata.bbox_wgs84[0] < 113.2 < result.metadata.bbox_wgs84[2]
    assert result.metadata.attribution == "Contains modified Copernicus Sentinel data 2026"
    catalog_payload = __import__("json").loads(requests[1].content)
    assert catalog_payload["collections"] == ["sentinel-2-l2a"]
    assert 'query' not in catalog_payload
    process_payload = __import__("json").loads(requests[2].content)
    assert process_payload["output"]["width"] == 200
    assert "[0, 1, 3, 8, 9, 10]" in process_payload["evalscript"]
    assert len(result.content) == len(png)


def test_provider_uses_least_cloudy_scene_with_explicit_warning_when_all_exceed_threshold() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL(TOKEN_URL):
            return httpx.Response(200, json={"access_token": "token"})
        if request.url == httpx.URL(CATALOG_URL):
            return httpx.Response(
                200,
                json={
                    "features": [
                        {"id": "bad", "properties": {"datetime": "2026-09-01T00:00:00Z", "eo:cloud_cover": 90}},
                        {"id": "best", "properties": {"datetime": "2026-08-01T00:00:00Z", "eo:cloud_cover": 55}},
                    ]
                },
            )
        image = BytesIO()
        Image.new("RGBA", (200, 200), (40, 80, 90, 255)).save(image, format="PNG")
        return httpx.Response(200, content=image.getvalue(), headers={"content-type": "image/png"})

    result = SentinelHubProvider(
        sentinel_settings(),
        transport=httpx.MockTransport(handler),
        now=datetime(2026, 10, 1, tzinfo=UTC),
    ).fetch(113.2, 23.1)
    assert result.metadata.scene_id == "best"
    assert result.metadata.quality_warning is not None


def test_provider_reports_no_coverage() -> None:
    def empty_catalog(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL(TOKEN_URL):
            return httpx.Response(200, json={"access_token": "token"})
        return httpx.Response(200, json={"features": []})

    provider = SentinelHubProvider(
        sentinel_settings(),
        transport=httpx.MockTransport(empty_catalog),
        now=datetime(2026, 10, 1, tzinfo=UTC),
    )
    with pytest.raises(ProviderError, match="No Sentinel-2 L2A scene"):
        provider.fetch(113.2, 23.1)


def test_provider_rejects_oversized_image() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL(TOKEN_URL):
            return httpx.Response(200, json={"access_token": "token"})
        if request.url == httpx.URL(CATALOG_URL):
            return httpx.Response(
                200,
                json={
                    "features": [
                        {"id": "scene", "properties": {"datetime": "2026-09-01T00:00:00Z", "eo:cloud_cover": 10}}
                    ]
                },
            )
        return httpx.Response(
            200,
            content=b"x" * (MAX_RESPONSE_BYTES + 1),
            headers={"content-type": "image/png"},
        )

    provider = SentinelHubProvider(
        sentinel_settings(),
        transport=httpx.MockTransport(handler),
        now=datetime(2026, 10, 1, tzinfo=UTC),
    )
    with pytest.raises(ProviderError, match="exceeded the 4 MB limit"):
        provider.fetch(113.2, 23.1)


def test_sentinel_vlm_analyzer_enforces_structured_output_and_scoped_prompt() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(__import__("json").loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"summary":"Patchy tree cover and built-up areas are visible.",'
                                '"observations":[{"category":"tree_cover","presence":"possible",'
                                '"confidence":0.7,"evidence":"Dark green patches form irregular clusters."}]}'
                            )
                        }
                    }
                ]
            },
        )

    analyzer = SentinelContextAnalyzer(
        VlmSettings(
            base_url="https://vlm.example/v1",
            api_key="test-key",
            model="test-model",
            timeout_seconds=2,
        ),
        transport=httpx.MockTransport(handler),
    )
    result = analyzer.analyze(b"fake-png")
    assert isinstance(result, SentinelEnvironmentAnalysis)
    assert result.observations[0].category == "tree_cover"
    assert "do not claim parcel" in captured["messages"][0]["content"]

def test_sentinel_uncertain_presence_remains_possible() -> None:
    analysis = SentinelEnvironmentAnalysis.model_validate(
        {
            "summary": "Tree cover cannot be resolved at this scale.",
            "observations": [
                {
                    "category": "tree_cover",
                    "presence": "uncertain",
                    "confidence": 0.4,
                    "evidence": "Mixed pixels and shadows obscure the feature.",
                }
            ],
        }
    )
    assert analysis.observations[0].presence == "possible"
