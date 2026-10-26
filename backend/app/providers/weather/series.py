"""Use the same enriched historical query for risk lab and case pipeline."""

from datetime import datetime
from math import asin, cos, radians, sin, sqrt

from app.contracts import WeatherProfile

from .history import HistoricalWeatherClient, _maximum, _minimum
from .open_meteo import OPEN_METEO_SOURCE_URL, OpenMeteoWeatherProvider


class SeriesWeatherProvider(OpenMeteoWeatherProvider):
    def lookup(self, *, longitude: float, latitude: float) -> WeatherProfile:
        result = HistoricalWeatherClient(base_url=self.base_url, timeout_seconds=self.timeout_seconds,
                                         transport=self.transport).lookup(
            longitude=longitude, latitude=latitude, start_date=self.observation_start,
            end_date=self.observation_end, resolution="daily")
        days, years = result["data"], result["annual_history"]
        def maximum(field): return _maximum([d.get(field) for d in days])
        def valid_days(field): return sum(d.get(field) is not None for d in days)
        def weighted_mean(field, raw_field):
            weights = [(y.get(field), y["valid_hours"].get(raw_field, 0)) for y in years]
            count = sum(n for value, n in weights if value is not None)
            return sum(value * n for value, n in weights if value is not None) / count if count else None
        source = result["sources"]["wind_rain_snowfall"]
        glat, glon = source.get("grid_latitude"), source.get("grid_longitude")
        distance = None
        if glat is not None and glon is not None:
            a = sin(radians(glat - latitude)/2)**2 + cos(radians(latitude))*cos(radians(glat))*sin(radians(glon-longitude)/2)**2
            distance = 2*6371.0088*asin(sqrt(min(1, a)))
        notes = ["固定 ERA5 小时资料按 Asia/Shanghai 汇总；ERA5-Land 地面积雪使用独立网格。",
                 "再分析网格不能代表电站现场观测；不推算冰雹直径或屋面雪荷载。",
                 "逐年记录标注完整日历年与有效小时数；不完整年份不能直接充当完整年度极值样本。",
                 *result["quality"]["notes"]]
        if not result["quality"]["time_series_complete"]:
            notes.append(f"时间序列未完整：{result['quality']['hours']}/{result['quality']['expected_hours']} 小时")
        for field, coverage in result["field_coverage"].items():
            if not coverage["complete"]:
                notes.append(f"{field} 有效 {coverage['valid_hours']}/{coverage['expected_hours']} 小时")
        return WeatherProfile(
            longitude=longitude, latitude=latitude, historical_max_wind_m_s=maximum("wind_gusts_10m_max"),
            historical_max_daily_wind_speed_m_s=maximum("wind_speed_10m_max"),
            historical_max_daily_precipitation_mm=maximum("precipitation_sum"),
            historical_max_daily_rain_mm=maximum("rain_sum"),
            historical_max_daily_precipitation_hours=maximum("precipitation_hours"),
            historical_max_daily_snowfall_cm=maximum("snowfall_sum"),
            historical_min_temperature_c=_minimum([d.get("temperature_2m_min") for d in days]),
            historical_max_temperature_c=maximum("temperature_2m_max"),
            historical_max_ground_snow_depth_m=maximum("ground_snow_depth_max"),
            relative_humidity_mean_percent=weighted_mean("relative_humidity_2m_mean", "relative_humidity_2m"),
            surface_pressure_mean_hpa=weighted_mean("surface_pressure_mean", "surface_pressure"),
            observation_start=self.observation_start, observation_end=self.observation_end,
            returned_data_start=min((d["period"] for d in days), default=None),
            returned_data_end=max((d["period"] for d in days), default=None),
            expected_day_count=(self.observation_end-self.observation_start).days+1, returned_day_count=len(days),
            wind_valid_day_count=valid_days("wind_gusts_10m_max"), wind_speed_valid_day_count=valid_days("wind_speed_10m_max"),
            precipitation_valid_day_count=valid_days("precipitation_sum"), rain_valid_day_count=valid_days("rain_sum"),
            precipitation_hours_valid_day_count=valid_days("precipitation_hours"), snowfall_valid_day_count=valid_days("snowfall_sum"),
            grid_longitude=glon, grid_latitude=glat, grid_elevation_m=source.get("grid_elevation_m"),
            grid_distance_km=distance, timezone=result["timezone"], dataset_selection="ERA5 + ERA5-Land ground snow depth",
            grid_selection_method="land", wind_unit="m/s", wind_speed_unit="m/s", precipitation_unit="mm",
            rain_unit="mm", precipitation_hours_unit="h", snowfall_unit="cm", data_quality_notes=notes,
            annual_history=years, basic_parameter_coverage=result["field_coverage"], field_sources=result["field_sources"],
            missing_parameters=result["missing_parameters"], cache_summary=result["cache"],
            source_name="Open-Meteo Historical Weather API (ERA5 / ERA5-Land)", source_url=OPEN_METEO_SOURCE_URL,
            retrieved_at=datetime.fromisoformat(result["retrieved_at"]),
        )
