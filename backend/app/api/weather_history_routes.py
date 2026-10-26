"""Location-based historical weather lookup for risk research."""

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal, Self

from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_validator
from starlette.concurrency import run_in_threadpool

from app.api.weather_dependencies import PROJECT_ROOT, build_weather_provider
from app.location.assessment import _gcj_to_wgs84
from app.location.geocoding import AmapGeocoder
from app.providers.common import ProviderError
from app.providers.weather.history import HistoricalWeatherClient

router = APIRouter(prefix="/api/v1/weather", tags=["weather"])


@router.get("/test", include_in_schema=False)
def weather_test_page() -> FileResponse:
    return FileResponse(Path(__file__).resolve().parents[3] / "frontend" / "weather-test.html")


class WeatherHistoryRequest(BaseModel):
    address: str | None = Field(default=None, min_length=6, max_length=300)
    city: str | None = Field(default=None, max_length=100)
    latitude: float | None = Field(default=None, ge=-90, le=90, allow_inf_nan=False)
    longitude: float | None = Field(default=None, ge=-180, le=180, allow_inf_nan=False)
    start_date: date
    end_date: date
    resolution: Literal["hourly", "daily", "yearly"] = "daily"

    @model_validator(mode="after")
    def validate_input(self) -> Self:
        has_address = bool(self.address and self.address.strip())
        has_latitude = self.latitude is not None
        has_longitude = self.longitude is not None
        if has_address == (has_latitude or has_longitude) or has_latitude != has_longitude:
            raise ValueError("Provide either an address or both WGS84 coordinates")
        if self.start_date > self.end_date:
            raise ValueError("start_date must not be after end_date")
        if self.start_date < date(1950, 1, 1) or self.end_date > datetime.now(UTC).date():
            raise ValueError("查询范围须在 1950 年至今天之间；最新几天再分析资料可能尚未更新")
        limit = 2 * 366 if self.resolution == "hourly" else 20 * 366
        if (self.end_date - self.start_date).days + 1 > limit:
            raise ValueError("逐小时最多 2 年，日/年汇总最多 20 年；服务端自动按年分段")
        return self


@router.post("/history")
async def lookup_weather_history(request: WeatherHistoryRequest) -> dict:
    """Return hourly, daily or yearly reanalysis values for an address or WGS84 point."""
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    if request.address:
        geocoder = AmapGeocoder.from_environment()
        if geocoder is None:
            raise HTTPException(
                503,
                "尚未配置高德地址解析密钥 PV_AMAP_WEB_SERVICE_KEY。"
                "请先配置密钥，或改用 WGS84 经纬度查询。",
            )
        if hasattr(geocoder, "lookup_detailed"):
            attempt = await run_in_threadpool(geocoder.lookup_detailed, request.address, city=request.city)
            match = attempt.result
            if match is None or not match.precise:
                candidates = "；".join(f"{item['address']}（{item['level']}）" for item in attempt.candidates)
                status_code = 502 if attempt.status in {"timeout", "request_failed", "provider_rejected", "invalid_response"} else 422
                raise HTTPException(status_code, attempt.reason + (" 候选：" + candidates if candidates else ""))
        else:
            match = await run_in_threadpool(geocoder.lookup, request.address, city=request.city)
            if match is None or not match.precise:
                raise HTTPException(422, "地址解析未得到唯一、精确的结果。请补充省市、道路和门牌号，或改用 WGS84 经纬度查询。")
        longitude, latitude = _gcj_to_wgs84(match.longitude, match.latitude)
        location = {
            "input": "address", "address": match.formatted_address,
            "geocode_level": match.level,
            "original_coordinates": {
                "latitude": match.latitude, "longitude": match.longitude, "system": "GCJ-02"
            },
            "weather_coordinates": {
                "latitude": latitude, "longitude": longitude, "system": "WGS84",
                "conversion": "approximate",
            },
            "verified_site_location": False,
        }
    else:
        latitude, longitude = request.latitude, request.longitude
        location = {
            "input": "coordinates",
            "weather_coordinates": {"latitude": latitude, "longitude": longitude, "system": "WGS84"},
            "verified_site_location": False,
        }

    configured = build_weather_provider()
    client = HistoricalWeatherClient(
        base_url=configured.base_url,
        timeout_seconds=configured.timeout_seconds,
    )
    try:
        result = await run_in_threadpool(
            client.lookup,
            latitude=latitude,
            longitude=longitude,
            start_date=request.start_date,
            end_date=request.end_date,
            resolution=request.resolution,
        )
    except ProviderError as exc:
        raise HTTPException(502, "Historical weather lookup failed") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"location": location, **result}
