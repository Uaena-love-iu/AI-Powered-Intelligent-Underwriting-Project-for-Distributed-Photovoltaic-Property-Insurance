"""Look up address candidates using Amap's Web Service geocoding API."""

from dataclasses import dataclass
from math import cos, isfinite, pi, sin, sqrt

import httpx

from app.providers.common import ProviderError

AMAP_GEOCODING_URL = "https://restapi.amap.com/v3/geocode/geo"
_EARTH_RADIUS = 6378245.0
_ECCENTRICITY = 0.00669342162296594323


def _latitude_offset(x: float, y: float) -> float:
    value = -100 + 2 * x + 3 * y + 0.2 * y**2 + 0.1 * x * y
    value += 0.2 * sqrt(abs(x))
    value += (20 * sin(6 * x * pi) + 20 * sin(2 * x * pi)) * 2 / 3
    value += (20 * sin(y * pi) + 40 * sin(y / 3 * pi)) * 2 / 3
    return value + (160 * sin(y / 12 * pi) + 320 * sin(y * pi / 30)) * 2 / 3


def _longitude_offset(x: float, y: float) -> float:
    value = 300 + x + 2 * y + 0.1 * x**2 + 0.1 * x * y
    value += 0.1 * sqrt(abs(x))
    value += (20 * sin(6 * x * pi) + 20 * sin(2 * x * pi)) * 2 / 3
    value += (20 * sin(x * pi) + 40 * sin(x / 3 * pi)) * 2 / 3
    return value + (150 * sin(x / 12 * pi) + 300 * sin(x / 30 * pi)) * 2 / 3


def _wgs84_to_gcj02(longitude: float, latitude: float) -> tuple[float, float]:
    if not 72.004 <= longitude <= 137.8347 or not 0.8293 <= latitude <= 55.8271:
        return longitude, latitude
    x, y = longitude - 105, latitude - 35
    latitude_delta = _latitude_offset(x, y)
    longitude_delta = _longitude_offset(x, y)
    radians = latitude / 180 * pi
    magic = 1 - _ECCENTRICITY * sin(radians) ** 2
    root = sqrt(magic)
    latitude_delta = latitude_delta * 180 / (
        (_EARTH_RADIUS * (1 - _ECCENTRICITY) / (magic * root)) * pi
    )
    longitude_delta = longitude_delta * 180 / ((_EARTH_RADIUS / root) * cos(radians) * pi)
    return longitude + longitude_delta, latitude + latitude_delta


def approximate_wgs84(longitude: float, latitude: float) -> tuple[float, float]:
    """Invert GCJ-02 iteratively; the result is a weather-lookup estimate, not a survey."""
    estimate_longitude, estimate_latitude = longitude, latitude
    for _ in range(4):
        forward_longitude, forward_latitude = _wgs84_to_gcj02(
            estimate_longitude, estimate_latitude
        )
        estimate_longitude += longitude - forward_longitude
        estimate_latitude += latitude - forward_latitude
    return estimate_longitude, estimate_latitude


@dataclass(frozen=True)
class GeocodeCandidate:
    formatted_address: str
    level: str
    longitude: float
    latitude: float
    amap_longitude: float
    amap_latitude: float


class AddressLookupError(ProviderError):
    """Safe diagnostics without upstream response bodies or credential URLs."""
    def __init__(self, code: str, public_message: str) -> None:
        super().__init__(public_message)
        self.code = code
        self.public_message = public_message


class AmapGeocodingProvider:
    """Return address candidates without silently deciding a project's location."""

    def __init__(
        self,
        api_key: str,
        *,
        timeout_seconds: float = 10.0,
        transport: httpx.BaseTransport | None = None,
        trust_env: bool = False,
    ) -> None:
        if not api_key.strip():
            raise ValueError("Amap Web Service key is required")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._api_key = api_key.strip()
        self._timeout_seconds = timeout_seconds
        self._transport = transport
        self._trust_env = trust_env

    def lookup(
        self,
        address: str,
        *,
        city: str | None = None,
    ) -> list[GeocodeCandidate]:
        address = address.strip()
        if not 4 <= len(address) <= 200:
            raise ValueError("address must be between 4 and 200 characters")
        params = {"key": self._api_key, "address": address, "output": "JSON"}
        if city and city.strip():
            params["city"] = city.strip()
        try:
            with httpx.Client(
                timeout=self._timeout_seconds,
                transport=self._transport,
                trust_env=self._trust_env,
            ) as client:
                response = client.get(
                    AMAP_GEOCODING_URL,
                    params=params,
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.TimeoutException as exc:
            raise AddressLookupError("timeout", "高德地址解析请求超时，请稍后重试。") from exc
        except httpx.HTTPError as exc:
            raise AddressLookupError("request_failed", "高德地址解析请求失败，请核对网络后重试。") from exc
        except ValueError as exc:
            raise AddressLookupError("invalid_response", "高德地址解析返回格式异常，请稍后重试。") from exc
        if not isinstance(payload, dict) or str(payload.get("status")) != "1":
            code = str(payload.get("infocode", "")) if isinstance(payload, dict) else ""
            suffix = f"（错误码 {code}）" if len(code) == 5 and code.isascii() and code.isdigit() else ""
            raise AddressLookupError("provider_rejected", f"高德地址解析服务拒绝请求{suffix}，请核对密钥、权限或配额。")
        geocodes = payload.get("geocodes")
        if not isinstance(geocodes, list):
            raise ProviderError("address lookup provider returned invalid candidates")

        candidates: list[GeocodeCandidate] = []
        for item in geocodes[:5]:
            if not isinstance(item, dict):
                continue
            location = item.get("location")
            formatted_address = item.get("formatted_address")
            if not isinstance(location, str) or not isinstance(formatted_address, str):
                continue
            try:
                amap_longitude, amap_latitude = (float(part) for part in location.split(","))
            except ValueError:
                continue
            if not (
                isfinite(amap_longitude)
                and isfinite(amap_latitude)
                and -180 <= amap_longitude <= 180
                and -90 <= amap_latitude <= 90
                and formatted_address.strip()
            ):
                continue
            longitude, latitude = approximate_wgs84(amap_longitude, amap_latitude)
            level = item.get("level")
            candidates.append(
                GeocodeCandidate(
                    formatted_address=formatted_address.strip()[:300],
                    level=level[:60] if isinstance(level, str) else "未知",
                    longitude=round(longitude, 6),
                    latitude=round(latitude, 6),
                    amap_longitude=amap_longitude,
                    amap_latitude=amap_latitude,
                )
            )
        return candidates
