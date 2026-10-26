"""Conservative, auditable comparison of independent site-location evidence."""

from dataclasses import dataclass
from math import asin, cos, pi, radians, sin, sqrt
from typing import Literal, Protocol

from pydantic import Field

from app.contracts.common import ContractModel
from app.contracts.enums import MaterialCategory, OcrValueStatus, WatermarkStatus
from app.contracts.inputs import Material, OcrField, ProjectInfo

from .geocoding import GeocodeResult

LocationStatus = Literal["verified", "distance_screened", "single_source", "conflict", "uncertain", "missing"]


class LocationCandidate(ContractModel):
    address: str
    level: str
    longitude: float
    latitude: float


class LocationEvidence(ContractModel):
    source: str
    material_id: str | None = None
    file_name: str | None = None
    address: str | None = None
    longitude: float | None = None
    latitude: float | None = None
    geocode_level: str | None = None
    coordinate_system: str | None = None
    geocode_status: str | None = None
    geocode_reason: str | None = None
    geocode_candidates: list[LocationCandidate] = Field(default_factory=list)


class LocationAssessment(ContractModel):
    status: LocationStatus
    address_photo_conflict: bool = False
    evidence: list[LocationEvidence] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    missing_requirements: list[str] = Field(default_factory=list)
    weather_coordinate_source: str | None = None
    weather_coordinates_verified: bool = False


class Geocoder(Protocol):
    def lookup(self, address: str, *, city: str | None = None) -> GeocodeResult | None: ...


@dataclass
class _Source:
    source: str
    material_id: str | None
    file_name: str | None = None
    is_photo: bool = False
    address: str | None = None
    longitude: float | None = None
    latitude: float | None = None
    geocode_level: str | None = None
    coordinate_system: str | None = None
    geocoded: bool = False
    geocode_status: str | None = None
    geocode_reason: str | None = None
    geocode_candidates: tuple[dict, ...] = ()
    coarse_longitude: float | None = None
    coarse_latitude: float | None = None

    def as_evidence(self) -> LocationEvidence:
        return LocationEvidence(
            source=self.source,
            material_id=self.material_id,
            file_name=self.file_name,
            address=self.address,
            longitude=self.longitude,
            latitude=self.latitude,
            geocode_level=self.geocode_level,
            geocode_status=self.geocode_status,
            geocode_reason=self.geocode_reason,
            geocode_candidates=[LocationCandidate(**item) for item in self.geocode_candidates],
            coordinate_system=self.coordinate_system or (
                "GCJ-02" if self.geocoded else
                "未标明" if self.longitude is not None else None
            ),
        )


def _normalized_address(value: str) -> str:
    return "".join(char for char in value.casefold() if char.isalnum())


def _is_detailed_address(value: str) -> bool:
    normalized = _normalized_address(value)
    return len(normalized) >= 6 and any(
        marker in normalized for marker in ("号", "园区", "厂", "大厦", "楼")
    )


def _gcj_to_wgs84(longitude: float, latitude: float) -> tuple[float, float]:
    """Approximate mainland GCJ-02 to WGS-84 for weather-grid lookup."""
    if not (72.004 <= longitude <= 137.8347 and 0.8293 <= latitude <= 55.8271):
        return longitude, latitude
    x, y = longitude - 105.0, latitude - 35.0
    dlat = (-100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y
            + 0.2 * sqrt(abs(x)) + (20 * sin(6 * x * pi) + 20 * sin(2 * x * pi)) * 2 / 3
            + (20 * sin(y * pi) + 40 * sin(y / 3 * pi)) * 2 / 3
            + (160 * sin(y / 12 * pi) + 320 * sin(y * pi / 30)) * 2 / 3)
    dlon = (300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y
            + 0.1 * sqrt(abs(x)) + (20 * sin(6 * x * pi) + 20 * sin(2 * x * pi)) * 2 / 3
            + (20 * sin(x * pi) + 40 * sin(x / 3 * pi)) * 2 / 3
            + (150 * sin(x / 12 * pi) + 300 * sin(x / 30 * pi)) * 2 / 3)
    rad_lat = latitude / 180 * pi
    magic = 1 - 0.00669342162296594323 * sin(rad_lat) ** 2
    sqrt_magic = sqrt(magic)
    dlat = dlat * 180 / ((6378245.0 * (1 - 0.00669342162296594323))
                          / (magic * sqrt_magic) * pi)
    dlon = dlon * 180 / (6378245.0 / sqrt_magic * cos(rad_lat) * pi)
    return longitude - dlon, latitude - dlat


def _distance_km(left: _Source, right: _Source) -> float:
    lat1, lon1, lat2, lon2 = map(
        radians, (left.latitude, left.longitude, right.latitude, right.longitude)
    )
    delta_lat, delta_lon = lat2 - lat1, lon2 - lon1
    value = sin(delta_lat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(delta_lon / 2) ** 2
    return 12742 * asin(min(1.0, sqrt(value)))


def _raw_distance_within_500m(sources: list[_Source]) -> bool:
    """Apply the agreed screening rule without treating raw coordinates as verified."""
    addresses = [item for item in sources if not item.is_photo and item.address]
    photos = [item for item in sources if item.is_photo]
    if not addresses or not photos or any(
        item.longitude is None or item.latitude is None for item in photos
    ):
        return False
    if any(_distance_km(left, right) > 0.5
           for index, left in enumerate(photos) for right in photos[index + 1:]):
        return False
    for address in addresses:
        if len(address.geocode_candidates) > 1:
            return False
        longitude = address.longitude if address.longitude is not None else address.coarse_longitude
        latitude = address.latitude if address.latitude is not None else address.coarse_latitude
        if longitude is None or latitude is None:
            return False
        candidate = _Source(source="地址候选点", material_id=address.material_id,
                            longitude=longitude, latitude=latitude)
        if any(_distance_km(candidate, photo) > 0.5 for photo in photos):
            return False
    return True


def _coordinate_system(value: str | None) -> str | None:
    normalized = (value or "").upper().replace("-", "").replace("_", "").replace(" ", "")
    if "GCJ02" in normalized or "高德" in (value or "") or "火星" in (value or ""):
        return "GCJ-02"
    if "WGS84" in normalized or "GPS" in normalized:
        return "WGS-84"
    return None


def _comparison_point(source: _Source) -> _Source | None:
    if source.longitude is None or source.latitude is None:
        return None
    system = "GCJ-02" if source.geocoded else source.coordinate_system
    if system == "GCJ-02":
        longitude, latitude = _gcj_to_wgs84(source.longitude, source.latitude)
    elif system == "WGS-84":
        longitude, latitude = source.longitude, source.latitude
    else:
        return None
    return _Source(source=source.source, material_id=source.material_id,
                   longitude=longitude, latitude=latitude)


def assess_location(
    project: ProjectInfo,
    materials: list[Material],
    ocr_fields: list[OcrField],
    *,
    geocoder: Geocoder | None = None,
) -> tuple[LocationAssessment, tuple[float, float] | None]:
    """Return a location verdict and optional weather candidate (longitude, latitude).

    The distance bands are evidence-screening heuristics, not insurance loss
    thresholds. Small offsets may reflect a different coordinate datum.
    """
    sources: dict[str, _Source] = {}
    if project.site_address or (project.longitude is not None and project.latitude is not None):
        sources["submission"] = _Source(
            source="投保标的",
            material_id=None,
            address=project.site_address,
            longitude=project.longitude,
            latitude=project.latitude,
        )
    watermark_systems = {
        field.material_id: _coordinate_system(field.normalized_value or field.raw_value)
        for field in ocr_fields
        if field.field_name == "watermark_coordinate_system"
        and field.value_status is OcrValueStatus.EXTRACTED
        and field.confidence >= 0.65
    }
    materials_by_id = {item.material_id: item for item in materials}
    categories = {item.material_id: item.category for item in materials}
    file_names = {item.material_id: item.file_name for item in materials}
    for material in materials:
        if (material.watermark_status is WatermarkStatus.PRESENT
            and material.longitude is not None and material.latitude is not None):
            sources[material.material_id] = _Source(
                source="现场照片水印",
                material_id=material.material_id,
                file_name=material.file_name,
                is_photo=True,
                longitude=material.longitude,
                latitude=material.latitude,
                coordinate_system=watermark_systems.get(material.material_id),
            )

    for field in ocr_fields:
        if (
            field.value_status is not OcrValueStatus.EXTRACTED
            or field.confidence < 0.65
        ):
            continue
        address = (field.normalized_value or field.raw_value or "").strip()
        if not address:
            continue
        category = categories.get(field.material_id)
        official = category in {
            MaterialCategory.PROJECT_DOCUMENT, MaterialCategory.FILING_CERTIFICATE,
            MaterialCategory.GRID_CONNECTION_DOCUMENT,
        }
        photo = category not in {None, MaterialCategory.PROJECT_DOCUMENT, MaterialCategory.FILING_CERTIFICATE,
                                 MaterialCategory.GRID_CONNECTION_DOCUMENT,
                                 MaterialCategory.EQUIPMENT_INVENTORY}
        if photo and materials_by_id[field.material_id].watermark_status is not WatermarkStatus.PRESENT:
            continue
        if not ((official and field.field_name == "site_address") or
                (photo and field.field_name == "watermark_address")):
            continue
        source = sources.setdefault(
            field.material_id,
            _Source(
                source="备案/并网材料" if official else "现场照片水印",
                material_id=field.material_id,
                file_name=file_names[field.material_id],
                is_photo=photo,
            ),
        )
        source.address = address

    if not sources:
        return LocationAssessment(
            status="missing",
            missing_requirements=["补充项目地址及带位置水印的现场照片"],
        ), None

    cache: dict[str, GeocodeResult | None] = {}
    attempts = {}
    internal_conflicts: list[str] = []
    for source in sources.values():
        if not source.address:
            continue
        if geocoder is None:
            source.geocode_status = "not_configured"
            source.geocode_reason = "未配置高德地址解析服务，无法将地址转换为坐标。"
            continue
        key = _normalized_address(source.address)
        if key not in cache:
            if hasattr(geocoder, "lookup_detailed"):
                attempts[key] = geocoder.lookup_detailed(source.address, city=project.city)
                cache[key] = attempts[key].result
            else:
                cache[key] = geocoder.lookup(source.address, city=project.city)
        if key in attempts:
            attempt = attempts[key]
            source.geocode_status = attempt.status
            source.geocode_reason = attempt.reason
            source.geocode_candidates = attempt.candidates
        else:
            source.geocode_status = "resolved" if cache[key] and cache[key].precise else "unresolved"
            source.geocode_reason = "地址已取得候选坐标。" if source.geocode_status == "resolved" else "地址解析未取得可用的精确坐标；该服务未提供详细原因。"
        match = cache[key]
        if match is not None:
            source.geocode_level = match.level
            if match.precise:
                if source.longitude is not None and source.latitude is not None:
                    geocoded_point = _Source(source="地图定位", material_id=source.material_id,
                                             longitude=match.longitude, latitude=match.latitude)
                    if _distance_km(source, geocoded_point) > 5.0:
                        internal_conflicts.append(
                            f"{source.source}（{source.file_name or '投保信息'}）内部地址与坐标明显不符，需核对原件"
                        )
                else:
                    source.longitude = match.longitude
                    source.latitude = match.latitude
                    source.geocoded = True
                    source.coordinate_system = "GCJ-02"
            else:
                source.coarse_longitude = match.longitude
                source.coarse_latitude = match.latitude

    comparable = 0
    consistent = 0
    cross_origin_agreement = False
    conflicts: list[str] = internal_conflicts
    address_photo_conflict = False
    review_distances: list[str] = []
    unknown_coordinate_system = False
    values = list(sources.values())
    for index, left in enumerate(values):
        for right in values[index + 1:]:
            if (left.address and right.address
                and _is_detailed_address(left.address)
                and _normalized_address(left.address) == _normalized_address(right.address)):
                comparable += 1
                consistent += 1
                cross_origin_agreement = cross_origin_agreement or left.is_photo != right.is_photo
            if (
                left.longitude is not None and left.latitude is not None
                and right.longitude is not None and right.latitude is not None
            ):
                left_point, right_point = _comparison_point(left), _comparison_point(right)
                if left_point is None or right_point is None:
                    unknown_coordinate_system = True
                    # Even an unknown datum cannot explain a cross-country mismatch.
                    if _distance_km(left, right) <= 500.0:
                        continue
                    distance = _distance_km(left, right)
                else:
                    distance = _distance_km(left_point, right_point)
                comparable += 1
                if distance <= 0.5:
                    consistent += 1
                    cross_origin_agreement = cross_origin_agreement or left.is_photo != right.is_photo
                elif distance <= 2.0:
                    review_distances.append(
                        f"{left.source}（{left.file_name or '投保信息'}）与"
                        f"{right.source}（{right.file_name or '投保信息'}）相距约 {distance:.2f} 公里，"
                        "处于 500 米至 2 公里人工复核范围"
                    )
                else:
                    conflicts.append(
                        f"{left.source}（{left.file_name or '投保信息'}）与"
                        f"{right.source}（{right.file_name or '投保信息'}）经纬度相距约 {distance:.2f} 公里，"
                        "存在重大位置冲突；先核对坐标系、录入和原件"
                    )
                    if left.is_photo != right.is_photo and (left.address or right.address):
                        address_photo_conflict = True

    # A unique township/county geocode is too coarse for a small distance
    # comparison. A separation above 500 km is nevertheless far beyond its
    # precision and cannot be explained by GCJ-02/WGS-84 conversion.
    for address_source in values:
        if (address_source.is_photo or not address_source.address
            or address_source.coarse_longitude is None
            or address_source.coarse_latitude is None
            or address_source.geocode_level not in {"乡镇", "街道", "区县"}):
            continue
        approximate = _Source(source="地址定位候选", material_id=address_source.material_id,
                              longitude=address_source.coarse_longitude,
                              latitude=address_source.coarse_latitude)
        for photo in values:
            if not photo.is_photo or photo.longitude is None or photo.latitude is None:
                continue
            distance = _distance_km(approximate, photo)
            if distance > 500.0:
                conflicts.append(
                    f"{address_source.source}（{address_source.file_name or '投保信息'}）"
                    f"的地址经地图服务转换为{address_source.geocode_level}级候选经纬度后，与"
                    f"现场照片（{photo.file_name or '未命名'}）水印经纬度相距约 {distance:.1f} 公里，"
                    "项目地址与现场明显不对应"
                )
                address_photo_conflict = True
                break

    has_coarse_address_and_photo = any(
        item.address and not item.is_photo and item.geocode_level in {"乡镇", "街道", "区县"}
        for item in values
    ) and any(item.is_photo and item.longitude is not None for item in values)

    if conflicts:
        status: LocationStatus = "conflict"
        notes = conflicts
        missing = (["核对项目地址与现场坐标原始证据；如识别或材料归属有误，申请人工改判"]
                   if address_photo_conflict else ["核对位置冲突的原始材料"])
    elif len(sources) == 1:
        status = "single_source"
        notes = ["仅有一份独立位置来源，无法交叉核验是否为同一电站"]
        missing = ["补充另一份独立的位置材料以核验电站位置"]
    elif (not review_distances
          and not (consistent and comparable == consistent and cross_origin_agreement)
          and _raw_distance_within_500m(values)):
        status = "distance_screened"
        notes = ["备案地址候选点与全部现场照片水印坐标按原始数值估算均在 500 米内；按位置距离初筛口径不触发人工复核。坐标系或项目级位置未核实，不作为精确位置证明"]
        missing = []
    elif consistent:
        status = "verified" if comparable == consistent and cross_origin_agreement else "uncertain"
        if status == "verified":
            notes = ["项目地址材料与现场照片的位置一致"]
            missing = []
        elif not cross_origin_agreement:
            notes = [
                "备案地址与现场照片坐标均已提供；地址仅到区域级别，照片之间的位置一致，但尚未确认属于同一电站"
                if has_coarse_address_and_photo else
                "同类材料位置一致，但尚未完成项目地址与现场照片的交叉核验"
            ]
            missing = ["补充可定位到具体项目的地址或坐标，并核对现场照片"]
        else:
            notes = ["部分位置证据一致，仍有未解释的差异", *review_distances]
            missing = ["人工核对位置证据差异"]
    else:
        status = "uncertain"
        needs_address_coordinates = any(item.address and item.longitude is None for item in values) and any(
            item.longitude is not None for item in values
        )
        if has_coarse_address_and_photo:
            nearby_region = any(
                _distance_km(
                    _Source(source="区域候选点", material_id=item.material_id,
                            longitude=item.coarse_longitude, latitude=item.coarse_latitude), photo
                ) <= 2.0
                for item in values
                if item.address and not item.is_photo
                and item.coarse_longitude is not None and item.coarse_latitude is not None
                and item.geocode_level in {"乡镇", "街道", "区县"}
                for photo in values
                if photo.is_photo and photo.longitude is not None and photo.latitude is not None
            )
            notes = [
                "备案地址与现场照片坐标均已提供；区域候选点与照片坐标接近，仅可作为区域线索，不能确认属于同一电站"
                if nearby_region else
                "备案地址与现场照片坐标均已提供；地址仅到乡镇等区域级别，不能用区域候选点确认属于同一电站"
            ]
            missing = ["补充项目级准确地址或项目坐标，或由核保员核对备案与现场关系"]
        elif review_distances:
            notes = review_distances
            missing = ["人工复核项目位置，并核对准确地址或项目坐标"]
        else:
            notes = [
                "地址与坐标需要地图定位服务；当前未配置，无法自动比较"
                if needs_address_coordinates and geocoder is None
                else "多份材料的位置无法可靠比对，可能缺少精确地址或地图定位结果"
            ]
            missing = ["补充精确位置材料或人工核对地址与坐标"]
        if unknown_coordinate_system:
            notes.append("现场或投保坐标未标明坐标系，不能按 500 米及 2 公里阈值自动判定")

    candidate: _Source | None = None
    if status != "conflict":
        # Prefer an actual photo coordinate to an approximate geocoded address.
        candidate = next(
            (item for item in values if item.material_id and not item.geocoded
             and item.longitude is not None and item.latitude is not None), None
        )
        if candidate is None:
            candidate = next(
                (item for item in values if not item.geocoded
                 and item.longitude is not None and item.latitude is not None), None
            )
        if candidate is None:
            candidate = next(
                (item for item in values if item.longitude is not None and item.latitude is not None), None
            )
    coordinates = None
    if candidate is not None:
        coordinates = (
            _gcj_to_wgs84(candidate.longitude, candidate.latitude)
            if candidate.geocoded or candidate.coordinate_system == "GCJ-02"
            else (candidate.longitude, candidate.latitude)
        )
    assessment = LocationAssessment(
        status=status,
        address_photo_conflict=address_photo_conflict,
        evidence=[item.as_evidence() for item in values],
        notes=notes,
        missing_requirements=missing,
        weather_coordinate_source=(
            f"{candidate.source}（{candidate.file_name or '投保信息'}）" if candidate else None
        ),
        weather_coordinates_verified=status == "verified" and candidate is not None,
    )
    return assessment, coordinates
