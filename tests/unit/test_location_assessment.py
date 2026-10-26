"""Location evidence must distinguish a clear site mismatch from uncertainty."""

from datetime import UTC, date, datetime

import httpx
from app.contracts import (
    Material,
    MaterialCategory,
    MaterialParseStatus,
    MaterialQualityStatus,
    OcrField,
    ProjectInfo,
    WatermarkStatus,
    WeatherProfile,
)
from app.location import AmapGeocoder, GeocodeResult, assess_location
from app.providers import MaterialInput, VisionProvider
from app.providers.weather import WeatherProvider


def project(*, address: str | None = None) -> ProjectInfo:
    return ProjectInfo(
        project_name=None,
        insured_name=None,
        project_type="rooftop",
        installation_type="flat_roof",
        site_address=address,
        longitude=None,
        latitude=None,
        proposed_start_date=None,
        component_model=None,
    )


def material(material_id: str, category: MaterialCategory, *,
             longitude: float | None = None, latitude: float | None = None) -> Material:
    return Material(
        material_id=material_id,
        category=category,
        file_name=material_id + ".jpg",
        media_type="image/jpeg",
        quality_status=MaterialQualityStatus.USABLE,
        quality_issues=[],
        parse_status=MaterialParseStatus.SUCCESS,
        watermark_status="present" if longitude is not None or category is MaterialCategory.PANORAMA else "not_checked",
        longitude=longitude,
        latitude=latitude,
    )


def address_field(material_id: str, address: str, *, field_name: str = "site_address") -> OcrField:
    return OcrField(
        field_id=material_id + ":address",
        material_id=material_id,
        field_name=field_name,
        raw_value=address,
        normalized_value=address,
        value_status="extracted",
        confidence=0.98,
        provider="test",
        model="test",
    )


def coordinate_system_field(material_id: str, system: str = "GCJ-02") -> OcrField:
    return address_field(material_id, system, field_name="watermark_coordinate_system")


class PreciseGeocoder:
    def lookup(self, address: str, *, city: str | None = None) -> GeocodeResult | None:
        del city
        if "甲" in address:
            return GeocodeResult(116.397, 39.908, "门牌号", address, True)
        return GeocodeResult(121.474, 31.230, "门牌号", address, True)


class CoarseGeocoder:
    def lookup(self, address: str, *, city: str | None = None) -> GeocodeResult | None:
        del city
        return GeocodeResult(116.397, 39.908, "区县", address, False)


class NingxiaTownshipGeocoder:
    def lookup(self, address: str, *, city: str | None = None) -> GeocodeResult | None:
        del city
        return GeocodeResult(105.912878, 36.980018, "乡镇", address, False)


def test_one_photo_coordinate_is_candidate_but_not_verified() -> None:
    result, coordinates = assess_location(
        project(), [material("photo", MaterialCategory.PANORAMA,
                             longitude=116.397, latitude=39.908)], []
    )
    assert result.status == "single_source"
    assert coordinates == (116.397, 39.908)
    assert not result.weather_coordinates_verified
    assert result.missing_requirements


def test_unconfirmed_photo_coordinates_are_not_location_evidence() -> None:
    photo = material("photo", MaterialCategory.PANORAMA,
                     longitude=116.397, latitude=39.908)
    photo = photo.model_copy(update={"watermark_status": WatermarkStatus.NOT_CHECKED})
    result, coordinates = assess_location(project(), [photo], [])
    assert result.status == "missing"
    assert coordinates is None


def test_two_photo_coordinates_match_or_conflict() -> None:
    photo = material("photo-1", MaterialCategory.PANORAMA,
                     longitude=116.397, latitude=39.908)
    nearby = material("photo-2", MaterialCategory.PANORAMA,
                      longitude=116.398, latitude=39.909)
    distant = material("photo-3", MaterialCategory.PANORAMA,
                       longitude=121.474, latitude=31.230)
    matched, coordinate = assess_location(project(), [photo, nearby], [])
    assert matched.status == "uncertain"  # Two photos alone cannot verify the insured site.
    assert not matched.weather_coordinates_verified
    assert coordinate == (116.397, 39.908)

    conflict, coordinate = assess_location(project(), [photo, distant], [])
    assert conflict.status == "conflict"
    assert coordinate is None
    assert "photo-1" in conflict.notes[0]
    assert "photo-3" in conflict.notes[0]


def test_unlabelled_submitted_and_photo_coordinates_do_not_auto_verify() -> None:
    submitted = project()
    submitted = submitted.model_copy(update={"longitude": 116.397, "latitude": 39.908})
    photo = material("photo", MaterialCategory.PANORAMA,
                     longitude=116.398, latitude=39.909)
    result, _ = assess_location(submitted, [photo], [])
    assert result.status == "uncertain"
    assert not result.weather_coordinates_verified
    assert any("坐标系" in note for note in result.notes)


def test_address_and_photo_coordinate_need_precise_geocoding() -> None:
    filing = material("filing", MaterialCategory.FILING_CERTIFICATE)
    photo = material("photo", MaterialCategory.PANORAMA,
                     longitude=116.397, latitude=39.908)
    fields = [address_field("filing", "北京市甲路1号")]
    without_map, _ = assess_location(project(), [filing, photo], fields)
    assert without_map.status == "uncertain"
    assert "未配置" in without_map.notes[0]

    precise, _ = assess_location(project(), [filing, photo],
                                 [*fields, coordinate_system_field("photo")],
                                 geocoder=PreciseGeocoder())
    assert precise.status == "verified"

    coarse, _ = assess_location(project(), [filing, photo], fields,
                                geocoder=CoarseGeocoder())
    assert coarse.status == "distance_screened"
    assert not coarse.weather_coordinates_verified


def test_township_address_and_distant_photo_require_location_review() -> None:
    filing = material("filing", MaterialCategory.FILING_CERTIFICATE)
    photo = material("photo", MaterialCategory.PANORAMA,
                     longitude=113.1189, latitude=23.0345)
    result, coordinates = assess_location(
        project(), [filing, photo],
        [address_field("filing", "吴忠市同心县豫海镇")],
        geocoder=NingxiaTownshipGeocoder(),
    )
    assert result.status == "conflict"
    assert result.address_photo_conflict
    assert coordinates is None
    assert "项目地址与现场明显不对应" in result.notes[0]


def test_precise_address_photo_coordinate_conflict_requires_review() -> None:
    filing = material("filing", MaterialCategory.FILING_CERTIFICATE)
    photo = material("photo", MaterialCategory.PANORAMA,
                     longitude=121.474, latitude=31.230)
    result, coordinates = assess_location(
        project(), [filing, photo], [address_field("filing", "北京市甲路1号"),
                                    coordinate_system_field("photo")],
        geocoder=PreciseGeocoder(),
    )
    assert result.status == "conflict"
    assert result.address_photo_conflict
    assert coordinates is None
    assert "核对坐标系" in result.notes[0]


def test_project_level_distance_bands_require_known_coordinate_system() -> None:
    filing = material("filing", MaterialCategory.FILING_CERTIFICATE)
    fields = [address_field("filing", "北京市甲路1号")]
    for offset, expected in [(0.004, "verified"), (0.012, "uncertain"),
                             (0.03, "conflict")]:
        photo = material("photo", MaterialCategory.PANORAMA,
                         longitude=116.397 + offset, latitude=39.908)
        result, _ = assess_location(
            project(), [filing, photo], [*fields, coordinate_system_field("photo")],
            geocoder=PreciseGeocoder(),
        )
        assert result.status == expected
        if expected == "uncertain":
            assert any("人工复核" in note for note in result.notes)
        if expected == "conflict":
            assert result.address_photo_conflict


def test_township_address_and_watermarked_photos_are_present_but_not_project_verified() -> None:
    filing = material("filing", MaterialCategory.FILING_CERTIFICATE)
    photos = [material(f"photo-{index}", MaterialCategory.PANORAMA,
                       longitude=105.91146, latitude=36.97829) for index in range(3)]
    result, _ = assess_location(
        project(), [filing, *photos],
        [address_field("filing", "吴忠市同心县豫海镇")],
        geocoder=NingxiaTownshipGeocoder(),
    )
    assert result.status == "distance_screened"
    assert any("不触发人工复核" in note for note in result.notes)
    assert not any("补充另一类" in item for item in result.missing_requirements)
    assert not result.weather_coordinates_verified

    labelled, _ = assess_location(
        project(), [filing, *photos],
        [address_field("filing", "吴忠市同心县豫海镇"),
         *[coordinate_system_field(photo.material_id) for photo in photos]],
        geocoder=NingxiaTownshipGeocoder(),
    )
    assert labelled.status == "distance_screened"
    assert any("不触发人工复核" in note for note in labelled.notes)
    assert not any("补充另一类" in item for item in labelled.missing_requirements)


def test_raw_distance_screening_stops_at_500m_and_requires_consistent_photos() -> None:
    filing = material("filing", MaterialCategory.FILING_CERTIFICATE)
    near = material("near", MaterialCategory.PANORAMA,
                    longitude=105.91146, latitude=36.97829)
    far = material("far", MaterialCategory.PANORAMA,
                   longitude=105.924, latitude=36.980018)
    fields = [address_field("filing", "吴忠市同心县豫海镇")]
    result, _ = assess_location(project(), [filing, far], fields,
                                geocoder=NingxiaTownshipGeocoder())
    assert result.status == "uncertain"
    mixed, _ = assess_location(project(), [filing, near, far], fields,
                               geocoder=NingxiaTownshipGeocoder())
    assert mixed.status == "uncertain"


def test_matching_township_text_does_not_verify_project_location() -> None:
    filing = material("filing", MaterialCategory.FILING_CERTIFICATE)
    photo = material("photo", MaterialCategory.PANORAMA)
    result, _ = assess_location(project(), [filing, photo], [
        address_field("filing", "吴忠市同心县豫海镇"),
        address_field("photo", "吴忠市同心县豫海镇", field_name="watermark_address"),
    ])
    assert result.status == "uncertain"


def test_one_address_can_offer_a_weather_candidate_without_being_verified() -> None:
    filing = material("filing", MaterialCategory.FILING_CERTIFICATE)
    result, coordinates = assess_location(
        project(), [filing], [address_field("filing", "北京市甲路1号")],
        geocoder=PreciseGeocoder(),
    )
    assert result.status == "single_source"
    assert coordinates is not None
    assert not result.weather_coordinates_verified
    assert result.evidence[0].geocode_level == "门牌号"


def test_matching_textual_photo_address_and_filing_address() -> None:
    filing = material("filing", MaterialCategory.FILING_CERTIFICATE)
    photo = material("photo", MaterialCategory.PANORAMA)
    result, coordinates = assess_location(
        project(), [filing, photo], [
            address_field("filing", "北京市甲路1号"),
            address_field("photo", "北京市甲路1号", field_name="watermark_address"),
        ],
    )
    assert result.status == "verified"
    assert coordinates is None  # An address alone is not a latitude/longitude.


def test_amap_project_env_fallback_and_environment_priority(monkeypatch, tmp_path) -> None:
    project_env = tmp_path / ".env"
    project_env.write_text(
        "PV_AMAP_WEB_SERVICE_KEY=project-env-test-key\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("app.location.geocoding.PROJECT_ENV_PATH", project_env)
    monkeypatch.delenv("PV_AMAP_WEB_SERVICE_KEY", raising=False)
    assert AmapGeocoder.from_environment().key == "project-env-test-key"
    monkeypatch.setenv("PV_AMAP_WEB_SERVICE_KEY", "project-test-key")
    assert AmapGeocoder.from_environment().key == "project-test-key"


def test_amap_geocoder_requires_one_precise_result() -> None:
    responses = [
        {"status": "1", "geocodes": [{"location": "116.397,39.908", "level": "门牌号",
                                       "formatted_address": "北京市甲路1号"}]},
        {"status": "1", "geocodes": [{"location": "116.397,39.908", "level": "区县",
                                       "formatted_address": "北京市"}]},
        {"status": "1", "geocodes": [{"location": "116.397,39.908", "level": "门牌号"},
                                      {"location": "121.474,31.230", "level": "门牌号"}]},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith(AmapGeocoder.URL)
        assert request.url.params["address"] == "北京市甲路1号"
        return httpx.Response(200, request=request, json=responses.pop(0))

    geocoder = AmapGeocoder(
        "test-key",
        transport=httpx.MockTransport(handler),
    )
    assert geocoder.lookup("北京市甲路1号").precise
    assert not geocoder.lookup("北京市甲路1号").precise
    assert geocoder.lookup("北京市甲路1号") is None


class NoFindings(VisionProvider):
    @property
    def name(self) -> str:
        return "no-findings"

    @property
    def model_name(self) -> str:
        return "test"

    def analyze(self, material_input: MaterialInput) -> list:
        del material_input
        return []


class RecordingWeather(WeatherProvider):
    def __init__(self) -> None:
        self.calls: list[tuple[float, float]] = []

    @property
    def name(self) -> str:
        return "recording-weather"

    def lookup(self, *, longitude: float, latitude: float) -> WeatherProfile:
        self.calls.append((longitude, latitude))
        return WeatherProfile(
            longitude=longitude,
            latitude=latitude,
            historical_max_wind_m_s=20,
            historical_max_hail_mm=None,
            historical_max_snow_load_pa=None,
            observation_start=date(2020, 1, 1),
            observation_end=date(2025, 1, 1),
            source_name="test",
            source_url=None,
            retrieved_at=datetime.now(UTC),
        )


def test_single_location_source_remains_unverified() -> None:
    photo = material("photo", MaterialCategory.PANORAMA,
                     longitude=116.397, latitude=39.908)
    assessment, coordinates = assess_location(project(), [photo], [])
    assert coordinates == (116.397, 39.908)
    assert assessment.status == "single_source"
    assert not assessment.weather_coordinates_verified


def test_location_conflict_does_not_select_weather_coordinate() -> None:
    photos = [
        material("photo-1", MaterialCategory.PANORAMA, longitude=116.397, latitude=39.908),
        material("photo-2", MaterialCategory.PANORAMA, longitude=121.474, latitude=31.230),
    ]
    assessment, coordinates = assess_location(project(), photos, [])
    assert coordinates is None
    assert assessment.status == "conflict"


def test_geocoding_diagnostics_keep_candidate_addresses() -> None:
    def handler(request):
        assert request.url.params['city'] == '北京'
        return httpx.Response(200, json={'status': '1', 'geocodes': [
            {'formatted_address': '北京市甲路1号', 'location': '116.397,39.908', 'level': '门牌号'},
            {'formatted_address': '北京市乙路1号', 'location': '116.398,39.909', 'level': '门牌号'},
        ]})
    geocoder = AmapGeocoder('test-key', transport=httpx.MockTransport(handler))
    info = project(address='甲路1号').model_copy(update={'city': '北京'})
    assessment, coordinates = assess_location(info, [], [], geocoder=geocoder)
    assert coordinates is None
    evidence = assessment.evidence[0]
    assert evidence.geocode_status == 'multiple_candidates'
    assert [c.address for c in evidence.geocode_candidates] == ['北京市甲路1号', '北京市乙路1号']
    # Diagnostics must survive case persistence/export without guessing a location.
    restored = type(assessment).model_validate_json(assessment.model_dump_json())
    assert restored.evidence[0].geocode_candidates == evidence.geocode_candidates


def test_geocoding_diagnostics_distinguish_empty_coarse_timeout_and_configuration() -> None:
    for payload, expected in [
        ({'status': '1', 'geocodes': []}, 'no_match'),
        ({'status': '1', 'geocodes': [{'formatted_address': '北京市', 'location': '116.397,39.908', 'level': '市'}]}, 'imprecise'),
        ({'status': '0', 'infocode': '10001', 'info': 'sensitive-key-value'}, 'provider_rejected'),
    ]:
        geocoder = AmapGeocoder('test-key', transport=httpx.MockTransport(lambda _, payload=payload: httpx.Response(200, json=payload)))
        result, coords = assess_location(project(address='北京市甲路1号'), [], [], geocoder=geocoder)
        assert coords is None
        assert result.evidence[0].geocode_status == expected
        assert 'sensitive-key-value' not in result.evidence[0].geocode_reason
    def timeout(request):
        raise httpx.ReadTimeout('sensitive-url-and-key', request=request)
    result, coords = assess_location(project(address='北京市甲路1号'), [], [],
        geocoder=AmapGeocoder('test-key', transport=httpx.MockTransport(timeout)))
    assert result.evidence[0].geocode_status == 'timeout'
    assert '超时' in result.evidence[0].geocode_reason
    assert 'sensitive' not in result.evidence[0].geocode_reason
    result, coords = assess_location(project(address='北京市甲路1号'), [], [], geocoder=None)
    assert result.evidence[0].geocode_status == 'not_configured'
    assert coords is None
