from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from app.api.schemas import AmapAddressCandidate
from app.contracts import (
    DecisionType,
    DetectionStatus,
    MapImageryReview,
    Material,
    MaterialParseStatus,
    MaterialQualityStatus,
    ProcessingStatus,
    ProcessingStep,
    ProjectInfo,
    RiskCategory,
    RiskFinding,
    RiskSeverity,
    UnderwritingCase,
    UnderwritingDecision,
)
from app.main import app
from app.providers.geocoding import GeocodeCandidate
from app.storage import CaseRepository
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def isolate_case_database(monkeypatch: pytest.MonkeyPatch):
    database_path = Path.cwd() / f"map-review-test-{uuid4().hex}.sqlite3"
    monkeypatch.setenv("PV_CASE_DB_PATH", str(database_path))
    monkeypatch.setenv("PV_AUTH_ENABLED", "false")
    yield
    for suffix in ("", "-journal", "-wal", "-shm"):
        Path(f"{database_path}{suffix}").unlink(missing_ok=True)


def blurry_case(*, quality_warning: bool = True) -> UnderwritingCase:
    material = Material(
        material_id="roof-01",
        category="panorama",
        file_name="roof.jpg",
        media_type="image/jpeg",
        quality_status=(
            MaterialQualityStatus.POOR
            if quality_warning
            else MaterialQualityStatus.USABLE
        ),
        quality_issues=(
            ["suspected_blur_or_insufficient_detail"]
            if quality_warning
            else []
        ),
        parse_status=MaterialParseStatus.SUCCESS,
    )
    now = datetime.now(UTC)
    return UnderwritingCase(
        schema_version="0.1.0",
        case_id="map-case-01",
        project=ProjectInfo(
            project_name="示例光伏项目",
            insured_name="示例企业",
            project_type="rooftop",
            installation_type="unknown",
            site_address="广东省示例市示例区示例路 1 号",
            city="示例市",
            longitude=None,
            latitude=None,
            proposed_start_date=None,
            component_model=None,
        ),
        materials=[material],
        ocr_fields=[],
        findings=[],
        material_reviews=[],
        decision=UnderwritingDecision(
            decision=DecisionType.MANUAL_REVIEW,
            decisive_rule_ids=[],
            reasons=["测试结论"],
            conditions=[],
            warnings=[],
            missing_requirements=[],
            generated_at=now,
        ),
        processing_trace=[],
    )


class FakeGeocoder:
    name = "amap-web-service-geocoding"

    def lookup(self, address: str) -> list[GeocodeCandidate]:
        assert address == "广东省示例市示例区示例路 1 号"
        return [
            GeocodeCandidate(
                formatted_address=address,
                level="门址",
                longitude=113.22,
                latitude=23.12,
                amap_longitude=113.225,
                amap_latitude=23.118,
            )
        ]


def candidate_payload() -> dict[str, object]:
    return AmapAddressCandidate(
        formatted_address="广东省示例市示例区示例路 1 号",
        level="门址",
        longitude=113.22,
        latitude=23.12,
        amap_longitude=113.225,
        amap_latitude=23.118,
        map_url="https://uri.amap.com/marker?position=113.225000%2C23.118000",
    ).model_dump()


def _configure_fake_amap(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.api.map_review_routes as routes

    monkeypatch.setattr(routes, "_load_geocoding_provider", lambda: FakeGeocoder())
    monkeypatch.setattr(
        routes,
        "_load_satellite_map_config",
        lambda: routes.AmapSatelliteMapConfig(configured=False),
    )


def test_prepare_requires_blurry_image_and_user_consent(monkeypatch) -> None:
    import app.api.map_review_routes as routes

    monkeypatch.setenv("PV_AUTH_ENABLED", "false")
    monkeypatch.setattr(routes, "_load_case", lambda _: ({}, blurry_case(quality_warning=False)))
    _configure_fake_amap(monkeypatch)

    with TestClient(app) as client:
        no_warning = client.post(
            "/api/v1/cases/map-case-01/map-review/prepare",
            json={"acknowledged_address_transfer": True},
        )
        no_consent = client.post(
            "/api/v1/cases/map-case-01/map-review/prepare",
            json={"acknowledged_address_transfer": False},
        )

    assert no_warning.status_code == 409
    assert no_consent.status_code == 422


def test_uncertain_image_quality_finding_offers_map_review() -> None:
    import app.api.map_review_routes as routes

    finding = RiskFinding(
        finding_id="quality-01",
        material_id="roof-01",
        category=RiskCategory.IMAGE_QUALITY,
        label="图片模糊",
        detection_status=DetectionStatus.UNCERTAIN,
        severity=RiskSeverity.MEDIUM,
        confidence=0.8,
        bbox=None,
        evidence_text="关键屋顶细节无法可靠辨认",
        provider="test-provider",
        model="test-model",
        requires_manual_review=True,
    )
    case = blurry_case(quality_warning=False).model_copy(update={"findings": [finding]})

    assert routes._image_quality_material_ids(case) == ["roof-01"]


def test_prepare_returns_candidates_and_satellite_configuration(monkeypatch) -> None:
    import app.api.map_review_routes as routes

    monkeypatch.setenv("PV_AUTH_ENABLED", "false")
    monkeypatch.setattr(routes, "_load_case", lambda _: ({}, blurry_case()))
    _configure_fake_amap(monkeypatch)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/cases/map-case-01/map-review/prepare",
            json={
                "acknowledged_address_transfer": True,
                "address_source": "material_ocr",
            },
        )

    assert response.status_code == 200
    result = response.json()
    assert result["provider"] == "amap_maps"
    assert result["candidates"][0]["level"] == "门址"
    assert result["candidates"][0]["amap_longitude"] == 113.225
    assert result["candidates"][0]["longitude"] == 113.22
    assert result["candidates"][0]["map_url"].startswith("https://uri.amap.com/marker?")
    assert result["satellite_map"]["configured"] is False
    assert "不是实时现场画面" in result["notice"]


def test_prepare_returns_jsapi_credentials_after_address_consent(monkeypatch) -> None:
    import app.api.map_review_routes as routes

    monkeypatch.setenv("PV_AUTH_ENABLED", "false")
    monkeypatch.setattr(routes, "_load_case", lambda _: ({}, blurry_case()))
    _configure_fake_amap(monkeypatch)
    monkeypatch.setattr(
        routes,
        "_load_satellite_map_config",
        lambda: routes.AmapSatelliteMapConfig(
            configured=True,
            api_key="test-js-api-key",
            security_js_code="test-security-code",
        ),
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/cases/map-case-01/map-review/prepare",
            json={
                "acknowledged_address_transfer": True,
                "address_source": "project_info",
            },
        )

    assert response.status_code == 200
    config = response.json()["satellite_map"]
    assert config == {
        "configured": True,
        "api_key": "test-js-api-key",
        "security_js_code": "test-security-code",
    }


def test_manual_map_observation_is_saved_without_replacing_decision(monkeypatch) -> None:

    case = blurry_case()
    CaseRepository().save_case(case)
    _configure_fake_amap(monkeypatch)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/cases/map-case-01/map-review",
            json={
                "acknowledged_address_transfer": True,
                "address": case.project.site_address,
                "address_source": "material_ocr",
                "selected_candidate": candidate_payload(),
                "reviewer_name": "核保员甲",
                "map_page_opened": True,
                "site_match_confirmed": False,
                "photovoltaic_visibility": "unclear",
                "observed_installation_type": "unknown",
                "imagery_capture_date": None,
                "observations": "卫星图影像日期未知，无法确认是否为项目屋顶。",
            },
        )

    assert response.status_code == 200
    saved_case = UnderwritingCase.model_validate(response.json()["case"])
    assert saved_case.decision == case.decision
    assert len(saved_case.map_reviews) == 1
    review = saved_case.map_reviews[0]
    assert review.provider == "amap_maps"
    assert review.site_match_confirmed is False
    assert review.longitude_gcj02 == 113.225
    assert review.longitude_wgs84 == 113.22
    assert review.geocoding_confidence is None
    assert review.imagery_capture_date is None
    assert review.triggering_material_ids == ["roof-01"]
    assert saved_case.processing_trace[-1].step is ProcessingStep.MAP_REVIEW
    assert saved_case.processing_trace[-1].status is ProcessingStatus.SUCCESS


def test_map_review_rejects_a_candidate_not_returned_by_amap(monkeypatch) -> None:
    case = blurry_case()
    CaseRepository().save_case(case)
    _configure_fake_amap(monkeypatch)
    selection = candidate_payload()
    selection["amap_longitude"] = 100.0

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/cases/map-case-01/map-review",
            json={
                "acknowledged_address_transfer": True,
                "address": case.project.site_address,
                "selected_candidate": selection,
                "reviewer_name": "核保员甲",
                "map_page_opened": True,
                "site_match_confirmed": False,
                "photovoltaic_visibility": "unclear",
                "observed_installation_type": "unknown",
                "observations": "无法确认。",
            },
        )

    assert response.status_code == 409
    assert "selected Amap candidate changed" in response.json()["detail"]


def test_legacy_baidu_map_review_records_still_validate() -> None:
    now = datetime.now(UTC)
    review = MapImageryReview(
        review_id="legacy-map-review",
        provider="baidu_maps",
        address_source="project_info",
        triggering_material_ids=["roof-01"],
        query_address="广东省示例市示例区示例路 1 号",
        matched_address="广东省示例市示例区示例路 1 号",
        geocoding_confidence=88,
        address_comprehension=91,
        precise_match=True,
        match_level="门址",
        longitude_bd09=113.25,
        latitude_bd09=23.12,
        provider_map_url="https://api.map.baidu.com/marker?location=23.12%2C113.25",
        queried_at=now,
        reviewed_at=now,
        reviewer_name="核保员甲",
        site_match_confirmed=True,
        photovoltaic_visibility="visible",
        observations="旧格式复核记录。",
    )

    assert review.provider == "baidu_maps"


def test_sentinel_context_requires_separate_consents_and_saves_metadata(monkeypatch) -> None:
    import app.api.map_review_routes as routes
    from app.contracts import SentinelEnvironmentAnalysis
    from app.providers.sentinel_hub import SentinelImageMetadata

    monkeypatch.setenv("PV_AUTH_ENABLED", "false")
    _configure_fake_amap(monkeypatch)

    class FakeSentinelSettings:
        @classmethod
        def from_environment(cls, env_file):
            return object()

    class FakeSentinelProvider:
        def __init__(self, settings):
            pass

        def fetch(self, longitude, latitude):
            assert longitude == 113.22
            assert latitude == 23.12
            return type(
                "ImageResult",
                (),
                {
                    "content": b"fake-png",
                    "metadata": SentinelImageMetadata(
                        scene_id="sentinel-test-scene",
                        acquired_at=datetime(2026, 9, 10, tzinfo=UTC),
                        tile_cloud_cover_percent=12.5,
                        tile_cloud_cover_note="Tile level estimate",
                        masked_pixel_fraction=0.1,
                        center_longitude=113.22,
                        center_latitude=23.12,
                        bbox_wgs84=(113.2, 23.1, 113.24, 23.14),
                        radius_m=1000,
                        pixel_size_m=10,
                        image_width=200,
                        image_height=200,
                        attribution="Contains modified Copernicus Sentinel data 2026",
                    ),
                },
            )()

    class FakeVlmSettings:
        @classmethod
        def from_environment(cls, env_file):
            return object()

    class FakeSentinelAnalyzer:
        def __init__(self, settings):
            pass

        def analyze(self, png):
            assert png == b"fake-png"
            return SentinelEnvironmentAnalysis.model_validate({
                "summary": "Broad vegetation patterns are visible.",
                "observations": [{
                    "category": "tree_cover",
                    "presence": "possible",
                    "confidence": 0.7,
                    "evidence": "Irregular green patches are visible.",
                }],
            })

    monkeypatch.setattr(routes, "SentinelHubSettings", FakeSentinelSettings)
    monkeypatch.setattr(routes, "SentinelHubProvider", FakeSentinelProvider)
    monkeypatch.setattr(routes, "VlmSettings", FakeVlmSettings)
    monkeypatch.setattr(routes, "SentinelContextAnalyzer", FakeSentinelAnalyzer)
    case = blurry_case()
    CaseRepository().save_case(case)
    payload = {
        "address": case.project.site_address,
        "address_source": "project_info",
        "selected_candidate": candidate_payload(),
        "acknowledged_address_transfer": True,
        "acknowledged_coordinate_transfer": True,
        "analyze_with_vlm": False,
        "acknowledged_vlm_transfer": False,
    }

    with TestClient(app) as client:
        no_consent = client.post(
            "/api/v1/cases/map-case-01/map-review/sentinel-context",
            json={**payload, "acknowledged_coordinate_transfer": False},
        )
        no_vlm_consent = client.post(
            "/api/v1/cases/map-case-01/map-review/sentinel-context",
            json={**payload, "analyze_with_vlm": True},
        )
        response = client.post(
            "/api/v1/cases/map-case-01/map-review/sentinel-context",
            json={**payload, "analyze_with_vlm": True, "acknowledged_vlm_transfer": True},
        )

    assert no_consent.status_code == 422
    assert no_vlm_consent.status_code == 422
    assert response.status_code == 200
    result = response.json()
    assert result["metadata"]["analysis"]["summary"] == "Broad vegetation patterns are visible."
    assert result["image_base64"] == "ZmFrZS1wbmc="
    assert result["metadata"]["scene_id"] == "sentinel-test-scene"
    assert result["metadata"]["pixel_size_m"] == 10

    review_payload = {
        "acknowledged_address_transfer": True,
        "address": case.project.site_address,
        "address_source": "project_info",
        "selected_candidate": candidate_payload(),
        "reviewer_name": "Underwriter",
        "map_page_opened": True,
        "site_match_confirmed": True,
        "photovoltaic_visibility": "unclear",
        "observed_installation_type": "unknown",
        "observations": "Broad green patches are visible; exact parcel use is uncertain.",
        "sentinel_context": result["metadata"],
    }
    with TestClient(app) as client:
        saved = client.post("/api/v1/cases/map-case-01/map-review", json=review_payload)
    assert saved.status_code == 200, saved.text
    saved_case = UnderwritingCase.model_validate(saved.json()["case"])
    assert saved_case.map_reviews[0].sentinel_context.scene_id == "sentinel-test-scene"
    assert saved_case.decision == case.decision
