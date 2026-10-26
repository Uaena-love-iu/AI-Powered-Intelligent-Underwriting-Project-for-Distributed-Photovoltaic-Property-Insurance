"""Historical weather, segmented by calendar year with auditable coverage."""
from calendar import isleap
from collections import defaultdict
from datetime import UTC, date, datetime
from math import atan2, cos, degrees, isfinite, radians, sin
from pathlib import Path
from time import sleep

import httpx

from app.providers.cache import DEFAULT_CACHE_PATH, ProviderCache
from app.providers.common import ProviderError

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
MAIN_FIELDS = (
    "wind_speed_10m", "wind_gusts_10m", "wind_direction_10m", "temperature_2m",
    "relative_humidity_2m", "dew_point_2m", "surface_pressure", "pressure_msl",
    "precipitation", "rain", "snowfall", "cloud_cover", "shortwave_radiation",
)
UNITS = {
    "wind_speed_10m": "m/s", "wind_gusts_10m": "m/s", "wind_direction_10m": "°",
    "temperature_2m": "°C", "relative_humidity_2m": "%", "dew_point_2m": "°C",
    "surface_pressure": "hPa", "pressure_msl": "hPa", "precipitation": "mm", "rain": "mm",
    "snowfall": "cm", "cloud_cover": "%", "shortwave_radiation": "W/m²", "snow_depth": "m",
}
ALL_FIELDS = MAIN_FIELDS + ("snow_depth",)

def _number(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value) else None

def _valid(field, value):
    n = _number(value)
    if n is None: return None
    if field in {"temperature_2m", "dew_point_2m"}: return n if -100 <= n <= 80 else None
    if n < 0: return None
    if field in {"cloud_cover", "relative_humidity_2m"} and n > 100: return None
    if field == "wind_direction_10m" and n > 360: return None
    if field in {"surface_pressure", "pressure_msl"} and n <= 0: return None
    return n

def _maximum(values): return max((v for v in values if v is not None), default=None)
def _minimum(values): return min((v for v in values if v is not None), default=None)
def _sum(values):
    valid = [v for v in values if v is not None]
    return round(sum(valid), 3) if valid else None

def _mean(values):
    valid = [v for v in values if v is not None]
    return round(sum(valid) / len(valid), 3) if valid else None

class HistoricalWeatherClient:
    """Fixed ERA5 background plus separate ERA5-Land ground snow depth.

    Failed chunks/fields remain explicit gaps, never imputed as zero. Cached
    public responses survive dependency recreation and service restarts.
    """
    def __init__(self, *, base_url=ARCHIVE_URL, timeout_seconds=30.0,
                 transport: httpx.BaseTransport | None = None, cache_path: Path | str | None = None):
        self.base_url, self.timeout_seconds, self.transport = base_url, timeout_seconds, transport
        self.cache = ProviderCache(cache_path or DEFAULT_CACHE_PATH) if cache_path is not None or transport is None else None

    def _fetch(self, latitude, longitude, start_date, end_date, *, model, fields):
        params = {"latitude": latitude, "longitude": longitude, "start_date": start_date.isoformat(),
                  "end_date": end_date.isoformat(), "models": model, "hourly": ",".join(fields),
                  "timezone": "Asia/Shanghai", "wind_speed_unit": "ms", "temperature_unit": "celsius",
                  "precipitation_unit": "mm", "cell_selection": "land"}
        key = ProviderCache.key("weather-hourly-v2", {"url": self.base_url, **params})
        if self.cache:
            hit, payload = self.cache.get(key)
            if hit and isinstance(payload, dict):
                payload["_cache_hit"] = True
                return payload
        try:
            with httpx.Client(timeout=self.timeout_seconds, transport=self.transport) as client:
                for attempt in range(3):
                    try:
                        response = client.get(self.base_url, params=params)
                        if response.status_code in {429, 502, 503, 504} and attempt < 2:
                            sleep(0.5 * (attempt + 1)); continue
                        response.raise_for_status()
                        if len(response.content) > 20_000_000:
                            raise ProviderError("historical weather response exceeded size limit")
                        payload = response.json()
                        break
                    except (httpx.ConnectError, httpx.TimeoutException):
                        if attempt == 2: raise
                        sleep(0.5 * (attempt + 1))
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError("historical weather request failed") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("hourly"), dict):
            raise ProviderError("historical weather response is invalid")
        series, units = payload["hourly"], payload.get("hourly_units", {})
        times = series.get("time")
        if not isinstance(times, list) or not times or len(times) > 8784:
            raise ProviderError("historical weather response has invalid time series")
        try:
            parsed = [datetime.fromisoformat(t) for t in times]
            if len(set(times)) != len(times) or any(t.tzinfo is not None or t.minute or t.second or t.microsecond or not start_date <= t.date() <= end_date for t in parsed):
                raise ValueError("times outside requested local-hour window")
        except (TypeError, ValueError) as exc:
            raise ProviderError("historical weather response has invalid or duplicate times") from exc
        issues = {}
        if not isinstance(units, dict): units = {}
        for field in fields:
            values = series.get(field)
            if not isinstance(values, list) or len(values) != len(times):
                issues[field] = "上游未返回该字段或序列长度不一致"
            elif units.get(field) != UNITS[field]:
                issues[field] = f"上游单位 {units.get(field)!r} 与要求 {UNITS[field]} 不一致"
            series[field] = [None] * len(times) if field in issues else [_valid(field, v) for v in values]
        payload.update(_issues=issues, _retrieved_at=datetime.now(UTC).isoformat())
        if self.cache:
            complete = len(times) == ((end_date - start_date).days + 1) * 24 and all(all(v is not None for v in series[f]) for f in fields)
            self.cache.put(key, payload, 30 * 86400 if complete else 300)
        payload["_cache_hit"] = False
        return payload

    def lookup(self, *, latitude, longitude, start_date, end_date, resolution="daily"):
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180 or start_date > end_date:
            raise ValueError("Invalid weather coordinates or period")
        span = (end_date - start_date).days + 1
        if start_date < date(1950, 1, 1) or end_date > datetime.now(UTC).date() or span > 20 * 366:
            raise ValueError("Weather history supports past periods since 1950, up to 20 years")
        if resolution not in {"hourly", "daily", "yearly"} or resolution == "hourly" and span > 2 * 366:
            raise ValueError("Hourly output supports at most 2 years")
        hours, segments, issues, cache_hits, fetch_count = [], [], [], 0, 0
        latest_main, latest_snow = None, None
        for year in range(start_date.year, end_date.year + 1):
            begin, end = max(start_date, date(year, 1, 1)), min(end_date, date(year, 12, 31))
            try:
                main = self._fetch(latitude, longitude, begin, end, model="era5", fields=MAIN_FIELDS)
            except ProviderError:
                segments.append({"year": year, "start_date": begin.isoformat(), "end_date": end.isoformat(), "status": "failed", "reason": "ERA5 查询失败，保留该年份缺口"})
                issues.append(f"{year} ERA5 查询失败；该段未计入任何最大值或合计")
                continue
            fetch_count += 1
            cache_hits += int(main.get("_cache_hit", False))
            latest_main = main
            snow = None
            try:
                snow = self._fetch(latitude, longitude, begin, end, model="era5_land", fields=("snow_depth",))
                fetch_count += 1
                cache_hits += int(snow.get("_cache_hit", False))
                latest_snow = snow
            except ProviderError:
                issues.append(f"{year} ERA5-Land 地面积雪深度查询失败；其他气象字段已保留")
            for label, payload in (("ERA5", main), ("ERA5-Land", snow)):
                if payload:
                    issues.extend(f"{year} {label} {field}: {reason}" for field, reason in payload.get("_issues", {}).items())
            snow_by_time = dict(zip(snow["hourly"]["time"], snow["hourly"]["snow_depth"])) if snow else {}
            for index, timestamp in enumerate(main["hourly"]["time"]):
                row = {field: main["hourly"][field][index] for field in MAIN_FIELDS}
                row.update(time=timestamp, date=timestamp[:10], snow_depth=snow_by_time.get(timestamp))
                hours.append(row)
            segments.append({"year": year, "start_date": begin.isoformat(), "end_date": end.isoformat(),
                             "status": "available", "era5_grid": self._grid(main), "era5_land_grid": self._grid(snow), "retrieved_at": main.get("_retrieved_at")})
        if latest_main is None: raise ProviderError("All historical weather chunks failed")
        hours.sort(key=lambda r: r["time"])
        by_day, by_year = defaultdict(list), defaultdict(list)
        for row in hours:
            by_day[row["date"]].append(row); by_year[row["date"][:4]].append(row)
        days = [self._aggregate(day, rows, expected_hours=24) for day, rows in sorted(by_day.items())]
        years = []
        for year in range(start_date.year, end_date.year + 1):
            begin, end = max(start_date, date(year, 1, 1)), min(end_date, date(year, 12, 31))
            record = self._aggregate(str(year), by_year[str(year)], expected_hours=((end - begin).days + 1) * 24)
            record.update(start_date=begin.isoformat(), end_date=end.isoformat(), full_calendar_year=begin == date(year, 1, 1) and end == date(year, 12, 31), calendar_year_hours=8784 if isleap(year) else 8760)
            record["comparable_fields"] = {field: record["full_calendar_year"] and record["valid_hours"][field] == record["calendar_year_hours"] for field in ALL_FIELDS}
            record["comparable_full_year"] = all(record["comparable_fields"].values())
            years.append(record)
        expected = span * 24
        coverage = {f: {"valid_hours": sum(r[f] is not None for r in hours), "expected_hours": expected, "unit": UNITS[f], "status": "available" if any(r[f] is not None for r in hours) else "unavailable"} for f in ALL_FIELDS}
        for value in coverage.values(): value["complete"] = value["valid_hours"] == expected
        field_sources = {f: {"provider": "Open-Meteo Historical Weather API", "requested_model": "era5_land" if f == "snow_depth" else "era5", "url": self.base_url, "unit": UNITS[f]} for f in ALL_FIELDS}
        field_sources["wind_gusts_10m"].update(provenance_note="API ERA5 实测返回阵风；官方源码将其映射到 GRIB 49.128。文档支持表与实测不一致，按来源标注使用。", implementation_reference="https://github.com/open-meteo/open-meteo/blob/main/Sources/App/Era5/Era5Variables.swift")
        return {
            "requested_coordinates": {"latitude": latitude, "longitude": longitude, "system": "WGS84"},
            "period": {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
            "timezone": "Asia/Shanghai", "resolution": resolution, "retrieved_at": datetime.now(UTC).isoformat(),
            "sources": {"wind_rain_snowfall": {"provider": "Open-Meteo Historical Weather API", "model": "era5", "url": self.base_url, **self._grid(latest_main)}, "ground_snow_depth": {"provider": "Open-Meteo Historical Weather API", "model": "era5_land", "url": self.base_url, **self._grid(latest_snow), "status": coverage["snow_depth"]["status"], "note": "Ground snow depth is not rooftop snow load"}},
            "source_segments": segments, "field_sources": field_sources,
            "units": {"time": "iso8601", **UNITS}, "field_coverage": coverage,
            "quality": {"hours": len(hours), "expected_hours": expected, "unique_hours": len({r["time"] for r in hours}), "time_series_complete": len(hours) == expected, "wind_gust_non_null_hours": coverage["wind_gusts_10m"]["valid_hours"], "wind_gust_complete": coverage["wind_gusts_10m"]["complete"], "ground_snow_depth_non_null_hours": coverage["snow_depth"]["valid_hours"], "notes": issues, "note": "Reanalysis grid values are not site measurements; partial sums/maxima must be interpreted with valid-hour counts"},
            "cache": {"hit_segments": cache_hits, "successful_segments": fetch_count, "backend": "local SQLite" if self.cache else "disabled"},
            "missing_parameters": {**{f: "全部缺测或单位/字段校验失败，请查看 quality.notes" for f, v in coverage.items() if v["valid_hours"] == 0}, "historical_max_hail_mm": "尚无可信冰雹事件及直径数据源", "historical_max_snow_load_pa": "地面积雪深度、降雪量不能直接转换为屋面雪荷载"},
            "hail": {"status": "unavailable", "event_count": None, "max_diameter_mm": None, "note": "No verified historical hail-event or diameter provider is configured; missing is not zero"},
            "annual_history": years, "data": hours if resolution == "hourly" else days if resolution == "daily" else years,
        }

    @staticmethod
    def _grid(payload):
        latitude = _number(payload.get("latitude")) if payload else None
        longitude = _number(payload.get("longitude")) if payload else None
        if latitude is None or longitude is None or not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            latitude, longitude = None, None
        return {"grid_latitude": latitude, "grid_longitude": longitude, "grid_elevation_m": _number(payload.get("elevation")) if payload else None}

    @staticmethod
    def _aggregate(period, rows, *, expected_hours=None):
        def values(field): return [r.get(field) for r in rows]
        valid = {f: sum(v is not None for v in values(f)) for f in ALL_FIELDS}
        directions = [r["wind_direction_10m"] for r in rows if r.get("wind_direction_10m") is not None]
        x, y = sum(cos(radians(d)) for d in directions), sum(sin(radians(d)) for d in directions)
        direction = round(degrees(atan2(y, x)) % 360, 2) if directions and x*x+y*y > 1e-12 else None
        peak = max((r for r in rows if r.get("wind_gusts_10m") is not None), key=lambda r: r["wind_gusts_10m"], default=None)
        return {
            "period": period, "hours": len(rows), "expected_hours": expected_hours,
            "time_series_complete": expected_hours is not None and len(rows) == expected_hours,
            "valid_hours": valid, "wind_speed_10m_max": _maximum(values("wind_speed_10m")),
            "wind_gusts_10m_max": _maximum(values("wind_gusts_10m")), "wind_gusts_10m_valid_hours": valid["wind_gusts_10m"],
            "wind_gust_peak_time": peak["time"] if peak else None,
            "wind_direction_at_gust_peak": peak.get("wind_direction_10m") if peak else None,
            "wind_direction_10m_circular_mean": direction,
            "temperature_2m_min": _minimum(values("temperature_2m")), "temperature_2m_max": _maximum(values("temperature_2m")),
            "relative_humidity_2m_mean": _mean(values("relative_humidity_2m")), "dew_point_2m_min": _minimum(values("dew_point_2m")),
            "surface_pressure_mean": _mean(values("surface_pressure")), "pressure_msl_mean": _mean(values("pressure_msl")),
            "precipitation_sum": _sum(values("precipitation")), "rain_sum": _sum(values("rain")), "snowfall_sum": _sum(values("snowfall")),
            "precipitation_hours": sum(v > 0 for v in values("precipitation") if v is not None) if valid["precipitation"] else None,
            "ground_snow_depth_max": _maximum(values("snow_depth")), "cloud_cover_mean": _mean(values("cloud_cover")),
            "shortwave_radiation_max": _maximum(values("shortwave_radiation")),
            "shortwave_radiation_sum_kwh_m2": round(sum(v for v in values("shortwave_radiation") if v is not None) / 1000, 4) if valid["shortwave_radiation"] else None,
        }
