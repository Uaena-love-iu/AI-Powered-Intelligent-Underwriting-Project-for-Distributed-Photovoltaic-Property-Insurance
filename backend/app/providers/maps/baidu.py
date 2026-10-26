from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from app.settings import BaiduMapSettings

from ..common import ProviderError

GEOCODING_URL = "https://api.map.baidu.com/geocoding/v3/"
MAP_MARKER_URL = "https://api.map.baidu.com/marker"


@dataclass(frozen=True, slots=True)
class BaiduGeocodeResult:
    """百度地图地址匹配结果；坐标使用 BD-09 经纬度。"""

    formatted_address: str
    longitude: float
    latitude: float
    confidence: int
    comprehension: int
    precise: bool
    level: str | None


class BaiduMapGeocoder:
    """只调用官方地址解析接口并生成官方地图打开链接。

    地图影像由用户在百度地图页面直接查看。本 Provider 不下载、缓存、
    转发地图影像，也不把影像发送给视觉模型。
    """

    def __init__(
        self,
        settings: BaiduMapSettings,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport

    @property
    def name(self) -> str:
        return "baidu-map-geocoding"

    def geocode(self, address: str, *, city: str | None = None) -> BaiduGeocodeResult:
        normalized_address = address.strip()
        if not normalized_address:
            raise ProviderError("map review address is empty")
        if len(normalized_address.encode("utf-8")) > 128:
            raise ProviderError("map review address exceeds the provider limit")

        params = {
            "address": normalized_address,
            "output": "json",
            "ak": self._settings.api_key,
            "ret_coordtype": "bd09ll",
        }
        if city and city.strip():
            params["city"] = city.strip()

        try:
            with httpx.Client(
                timeout=httpx.Timeout(
                    self._settings.timeout_seconds,
                    connect=min(self._settings.timeout_seconds, 5.0),
                ),
                transport=self._transport,
            ) as client:
                response = client.get(GEOCODING_URL, params=params)
                response.raise_for_status()
                payload: Any = response.json()
        except httpx.TimeoutException as exc:
            raise ProviderError("map geocoding request timed out") from exc
        except httpx.HTTPStatusError as exc:
            raise ProviderError(
                f"map geocoding provider returned HTTP {exc.response.status_code}"
            ) from exc
        except (httpx.RequestError, ValueError) as exc:
            raise ProviderError("map geocoding request failed") from exc

        if not isinstance(payload, dict) or payload.get("status") != 0:
            raise ProviderError("map geocoding provider could not match the address")

        result = payload.get("result")
        location = result.get("location") if isinstance(result, dict) else None
        if not isinstance(result, dict) or not isinstance(location, dict):
            raise ProviderError("map geocoding provider returned an invalid result")

        try:
            longitude = float(location["lng"])
            latitude = float(location["lat"])
            confidence = int(result["confidence"])
            comprehension = int(result["comprehension"])
            precise = result["precise"] == 1
            formatted_address = str(result["formatted_address"]).strip()
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderError("map geocoding provider returned incomplete fields") from exc

        if (
            not formatted_address
            or not -180 <= longitude <= 180
            or not -90 <= latitude <= 90
            or not 0 <= confidence <= 100
            or not 0 <= comprehension <= 100
        ):
            raise ProviderError("map geocoding provider returned invalid fields")

        level = result.get("level")
        return BaiduGeocodeResult(
            formatted_address=formatted_address,
            longitude=longitude,
            latitude=latitude,
            confidence=confidence,
            comprehension=comprehension,
            precise=precise,
            level=str(level) if level else None,
        )

    @staticmethod
    def build_map_url(
        *,
        geocode: BaiduGeocodeResult,
        title: str = "光伏项目地址参考点",
    ) -> str:
        """返回百度官方地图标点页；该坐标只用于人工核对。"""

        query = urlencode(
            {
                # The official marker URI expects latitude,longitude.
                "location": f"{geocode.latitude},{geocode.longitude}",
                "title": title,
                "content": geocode.formatted_address,
                "output": "html",
                "coord_type": "bd09ll",
                "zoom": "18",
                "src": "webapp.pvunderwriting.map_review",
            }
        )
        return f"{MAP_MARKER_URL}?{query}"
