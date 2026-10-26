"""Compatibility adapter for conservative location-evidence assessment."""

import os
from dataclasses import dataclass
from pathlib import Path

import httpx
from dotenv import load_dotenv

PROJECT_ENV_PATH = Path(__file__).resolve().parents[3] / ".env"
AMAP_GEOCODING_URL = "https://restapi.amap.com/v3/geocode/geo"


@dataclass(frozen=True)
class GeocodeResult:
    longitude: float
    latitude: float
    level: str
    formatted_address: str
    precise: bool


@dataclass(frozen=True)
class GeocodeAttempt:
    status: str
    reason: str
    result: GeocodeResult | None = None
    candidates: tuple[dict, ...] = ()


class AmapGeocoder:
    """Select one Amap candidate for evidence comparison, without proving location."""

    URL = AMAP_GEOCODING_URL
    PRECISE_LEVELS = frozenset({"门牌号", "兴趣点", "楼宇"})

    def __init__(
        self,
        key: str,
        *,
        timeout_seconds: float = 8.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        from app.providers.geocoding import AmapGeocodingProvider

        self.key = key.strip()
        self.timeout_seconds = timeout_seconds
        self._provider = AmapGeocodingProvider(
            self.key,
            timeout_seconds=timeout_seconds,
            transport=transport,
        )

    @classmethod
    def from_environment(cls) -> "AmapGeocoder | None":
        load_dotenv(PROJECT_ENV_PATH, override=False)
        key = os.getenv("PV_AMAP_WEB_SERVICE_KEY", "").strip()
        return cls(key) if key else None

    def lookup(self, address: str, *, city: str | None = None) -> GeocodeResult | None:
        return self.lookup_detailed(address, city=city).result

    def lookup_detailed(self, address: str, *, city: str | None = None) -> GeocodeAttempt:
        from app.providers.common import ProviderError

        try:
            matches = self._provider.lookup(address, city=city)
        except ValueError:
            return GeocodeAttempt("invalid_address", "地址格式无效：请填写 4～200 字的具体地址。")
        except ProviderError as exc:
            return GeocodeAttempt(getattr(exc, "code", "request_failed"),
                                  getattr(exc, "public_message", "高德地址解析请求失败，请稍后重试或核对服务配置。"))
        candidates = tuple({"address": m.formatted_address, "level": m.level,
                            "longitude": m.amap_longitude, "latitude": m.amap_latitude}
                           for m in matches[:5])
        if not matches:
            return GeocodeAttempt("no_match", "高德未返回可用地址结果，请核对省市区、道路和门牌号。")
        if len(matches) != 1:
            return GeocodeAttempt("multiple_candidates", "高德返回多个候选位置（下方最多展示 5 个），无法自动确定电站位置。请补充真实省市区或核对候选位置。", candidates=candidates)
        match = matches[0]
        result = GeocodeResult(match.amap_longitude, match.amap_latitude, match.level,
                               match.formatted_address, match.level in self.PRECISE_LEVELS)
        if not result.precise:
            return GeocodeAttempt("imprecise", f"高德返回唯一候选，但定位级别为“{match.level}”，未达到当前系统采用坐标的精度要求。请补充详细地址或确认实际坐标。", result, candidates)
        return GeocodeAttempt("resolved", "地址已解析为唯一、符合当前精度要求的候选位置；仍需核验是否属于该电站。", result, candidates)
