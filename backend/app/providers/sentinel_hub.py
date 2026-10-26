"""Small, bounded Sentinel-2 L2A context-image client for Copernicus Data Space."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from io import BytesIO
from math import cos, radians
from typing import Any

import httpx
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from app.settings import SentinelHubSettings

from .common import ProviderError

TOKEN_URL = (
    "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/"
    "protocol/openid-connect/token"
)
CATALOG_URL = "https://sh.dataspace.copernicus.eu/catalog/v1/search"
PROCESS_URL = "https://sh.dataspace.copernicus.eu/process/v1"
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
ATTRIBUTION_TEMPLATE = "Contains modified Copernicus Sentinel data {year}"

EVALSCRIPT = """//VERSION=3
function setup() {
  return {
    input: [{ bands: ["B02", "B03", "B04", "SCL", "dataMask"] }],
    output: { bands: 4, sampleType: "UINT8" }
  };
}
function evaluatePixel(sample) {
  const scl = sample.SCL;
  const masked = sample.dataMask === 0 || [0, 1, 3, 8, 9, 10].includes(scl);
  if (masked) return [0, 0, 0, 0];
  return [
    Math.min(255, sample.B04 * 637.5),
    Math.min(255, sample.B03 * 637.5),
    Math.min(255, sample.B02 * 637.5),
    255
  ];
}
"""


class SentinelImageError(ProviderError):
    """Raised when Copernicus cannot return a valid bounded image."""


class SentinelImageMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = "copernicus_sentinel_hub"
    collection: str = "sentinel-2-l2a"
    scene_id: str = Field(min_length=1, max_length=300)
    acquired_at: datetime
    tile_cloud_cover_percent: float | None = Field(default=None, ge=0, le=100)
    tile_cloud_cover_note: str
    masked_pixel_fraction: float = Field(ge=0, le=1)
    center_longitude: float = Field(ge=-180, le=180)
    center_latitude: float = Field(ge=-90, le=90)
    bbox_wgs84: tuple[float, float, float, float]
    radius_m: int = Field(ge=250, le=2000)
    pixel_size_m: float = Field(gt=0, le=20)
    image_width: int = Field(ge=1, le=400)
    image_height: int = Field(ge=1, le=400)
    attribution: str = Field(min_length=1)
    quality_warning: str | None = None


@dataclass(frozen=True, slots=True)
class SentinelImage:
    metadata: SentinelImageMetadata
    content: bytes


class SentinelHubProvider:
    """Fetch a recent, cloud-screened true-colour image around a confirmed point."""

    name = "copernicus-sentinel-2-l2a"

    def __init__(
        self,
        settings: SentinelHubSettings,
        *,
        transport: httpx.BaseTransport | None = None,
        now: datetime | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport
        self._now = now or datetime.now(UTC)
        if self._now.tzinfo is None:
            self._now = self._now.replace(tzinfo=UTC)

    def fetch(self, longitude: float, latitude: float) -> SentinelImage:
        if not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
            raise ValueError("coordinates are outside WGS84 bounds")
        bbox = self._bbox(longitude, latitude, self._settings.radius_m)
        token = self._get_access_token()
        item, cloud_warning = self._find_scene(token, bbox)
        content, masked_fraction = self._request_image(token, bbox, item)
        properties = item["properties"]
        acquired_at = self._parse_datetime(str(properties["datetime"]))
        cloud = properties.get("eo:cloud_cover")
        cloud_value = float(cloud) if cloud is not None else None
        width = max(64, min(400, round(self._settings.radius_m * 2 / 10)))
        metadata = SentinelImageMetadata(
            scene_id=str(item.get("id") or "unknown"),
            acquired_at=acquired_at,
            tile_cloud_cover_percent=cloud_value,
            tile_cloud_cover_note=(
                "Catalog cloud cover is an approximate scene/tile statistic, "
                "not a parcel-level cloud measurement. Transparent pixels are "
                "masked clouds, shadows, or no-data."
            ),
            masked_pixel_fraction=masked_fraction,
            center_longitude=longitude,
            center_latitude=latitude,
            bbox_wgs84=bbox,
            radius_m=self._settings.radius_m,
            pixel_size_m=(2 * self._settings.radius_m / width),
            image_width=width,
            image_height=width,
            attribution=ATTRIBUTION_TEMPLATE.format(year=acquired_at.year),
            quality_warning=cloud_warning,
        )
        return SentinelImage(metadata=metadata, content=content)

    @staticmethod
    def _bbox(longitude: float, latitude: float, radius_m: int) -> tuple[float, float, float, float]:
        lat_delta = radius_m / 110_574
        lon_delta = radius_m / max(111_320 * abs(cos(radians(latitude))), 1)
        return (
            max(-180.0, longitude - lon_delta),
            max(-90.0, latitude - lat_delta),
            min(180.0, longitude + lon_delta),
            min(90.0, latitude + lat_delta),
        )

    def _get_access_token(self) -> str:
        try:
            with httpx.Client(
                timeout=self._settings.timeout_seconds,
                transport=self._transport,
                trust_env=self._settings.use_system_proxy,
            ) as client:
                response = client.post(
                    TOKEN_URL,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self._settings.client_id,
                        "client_secret": self._settings.client_secret,
                    },
                )
                response.raise_for_status()
                token = response.json().get("access_token")
                if not isinstance(token, str) or not token:
                    raise SentinelImageError("Copernicus returned no access token")
                return token
        except SentinelImageError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise SentinelImageError("Copernicus authentication failed") from exc

    def _find_scene(
        self,
        token: str,
        bbox: tuple[float, float, float, float],
    ) -> tuple[dict[str, Any], str | None]:
        end = self._now.astimezone(UTC)
        start = end - timedelta(days=self._settings.lookback_days)
        payload = {
            # Cloud screening is applied locally; CDSE Catalog rejects STAC query syntax.
            "collections": ["sentinel-2-l2a"],
            "bbox": list(bbox),
            "datetime": f"{start.isoformat()}/{end.isoformat()}",
            "limit": 100,
        }
        try:
            with httpx.Client(
                timeout=self._settings.timeout_seconds,
                transport=self._transport,
                trust_env=self._settings.use_system_proxy,
            ) as client:
                response = client.post(
                    CATALOG_URL,
                    headers={"Authorization": f"Bearer {token}"},
                    json=payload,
                )
                response.raise_for_status()
                features = response.json().get("features", [])
        except (httpx.HTTPError, ValueError, AttributeError) as exc:
            raise SentinelImageError("Copernicus Catalog search failed") from exc
        valid = []
        for item in features:
            if not isinstance(item, dict) or not isinstance(item.get("properties"), dict):
                continue
            try:
                self._parse_datetime(str(item["properties"].get("datetime", "")))
            except (TypeError, ValueError):
                continue
            valid.append(item)
        if not valid:
            raise SentinelImageError(
                "No Sentinel-2 L2A scene covers the selected point in the configured date window"
            )
        valid.sort(
            key=lambda item: self._parse_datetime(str(item["properties"]["datetime"])),
            reverse=True,
        )
        acceptable = [
            item for item in valid
            if self._cloud_cover(item) is not None
            and self._cloud_cover(item) <= self._settings.max_cloud_cover_percent
        ]
        if acceptable:
            return acceptable[0], None
        least_cloudy = min(
            valid,
            key=lambda item: (
                self._cloud_cover(item) if self._cloud_cover(item) is not None else 100.0,
                -self._parse_datetime(str(item["properties"]["datetime"])).timestamp(),
            ),
        )
        return least_cloudy, (
            "No scene in the date window met the configured cloud-cover threshold; "
            "the least-cloudy available scene is shown."
        )

    def _request_image(
        self,
        token: str,
        bbox: tuple[float, float, float, float],
        item: dict[str, Any],
    ) -> tuple[bytes, float]:
        acquired_at = self._parse_datetime(str(item["properties"]["datetime"]))
        day_start = datetime.combine(acquired_at.date(), datetime.min.time(), UTC)
        day_end = day_start + timedelta(days=1) - timedelta(seconds=1)
        width = max(64, min(400, round(self._settings.radius_m * 2 / 10)))
        payload = {
            "input": {
                "bounds": {
                    "bbox": list(bbox),
                    "properties": {
                        "crs": "http://www.opengis.net/def/crs/OGC/1.3/CRS84"
                    },
                },
                "data": [
                    {
                        "type": "sentinel-2-l2a",
                        "dataFilter": {
                            "timeRange": {
                                "from": day_start.isoformat().replace("+00:00", "Z"),
                                "to": day_end.isoformat().replace("+00:00", "Z"),
                            },
                            "maxCloudCoverage": 100,
                            "mosaickingOrder": "mostRecent",
                        },
                    }
                ],
            },
            "output": {
                "width": width,
                "height": width,
                "responses": [
                    {"identifier": "default", "format": {"type": "image/png"}}
                ],
            },
            "evalscript": EVALSCRIPT,
        }
        try:
            with httpx.Client(
                timeout=self._settings.timeout_seconds,
                transport=self._transport,
                trust_env=self._settings.use_system_proxy,
            ) as client:
                response = client.post(
                    PROCESS_URL,
                    headers={"Authorization": f"Bearer {token}"},
                    json=payload,
                )
                response.raise_for_status()
                if len(response.content) > MAX_RESPONSE_BYTES:
                    raise SentinelImageError("Copernicus image exceeded the 4 MB limit")
                if not response.headers.get("content-type", "").startswith("image/png"):
                    raise SentinelImageError("Copernicus returned a non-PNG image")
                content = response.content
                with Image.open(BytesIO(content)) as image:
                    if image.format != "PNG" or image.size != (width, width):
                        raise SentinelImageError("Copernicus image dimensions were invalid")
                    alpha = image.convert("RGBA").getchannel("A")
                    histogram = alpha.histogram()
                    masked_fraction = sum(histogram[:255]) / (width * width)
        except SentinelImageError:
            raise
        except (httpx.HTTPError, ValueError, OSError) as exc:
            raise SentinelImageError("Copernicus Process API image request failed") from exc
        return content, masked_fraction

    @staticmethod
    def _parse_datetime(value: str) -> datetime:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    @staticmethod
    def _cloud_cover(item: dict[str, Any]) -> float | None:
        value = item.get("properties", {}).get("eo:cloud_cover")
        try:
            cloud_cover = float(value)
        except (TypeError, ValueError):
            return None
        return cloud_cover if 0 <= cloud_cover <= 100 else None
