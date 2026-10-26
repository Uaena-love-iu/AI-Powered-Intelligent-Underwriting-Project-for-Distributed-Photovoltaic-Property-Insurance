# 历史气象查询接口

`POST /api/v1/weather/history` 根据详细地址或 WGS84 经纬度查询历史气象。此接口独立于现有核保计算链路；返回的是再分析格点数据，不是电站屋顶实测值。

## 输入

提供 `address`（可附 `city`），或者同时提供 `latitude`、`longitude`，两种方式不能混用。地址查询使用已配置的高德 Web Key；仅接受唯一、精确到门牌号、兴趣点或楼宇的结果。高德 GCJ-02 坐标近似转换为 WGS84 后查询天气，返回原坐标、转换后坐标和定位状态。地址匹配不证明该地址就是实际电站位置。

必须提供 `start_date`、`end_date`；逐小时输出最多 2 年，日/年汇总最多 20 年；服务端自动按日历年分段，并使用 SQLite 缓存。时间范围从 1950 年至今天，ERA5 最近几天可能尚未更新。`resolution` 可为 `hourly`、`daily`（默认）或 `yearly`。

```json
{
  "latitude": 36.24,
  "longitude": 116.06,
  "start_date": "2024-01-01",
  "end_date": "2024-12-31",
  "resolution": "daily"
}
```

## 返回字段

| 类别 | 逐时原始字段 | 日/年汇总 |
| --- | --- | --- |
| 风 | 10 米风速、阵风、风向 | 最大风速、最大阵风、阵风有效小时数 |
| 降雨 | 液态降雨量 | 累计降雨量 |
| 总降水 | 雨雪合计降水量 | 累计总降水量 |
| 降雪 | 降雪量，单位 cm | 累计降雪量 |
| 地面积雪 | 地面积雪深度，单位 m | 最大地面积雪深度 |
| 风荷载辅助量 | 2 米气温、地面气压 | 气温范围、平均地面气压 |
| 冰雹 | 暂无可核实的历史事件与直径来源 | `status=unavailable`，数量和最大直径为 `null` |

风、雨、降雪及辅助量固定查询 Open-Meteo ERA5；地面积雪深度另查 ERA5-Land。响应分别标出两套模型的返回格点、单位、查询时间、预期小时数、实际小时数和有效小时数；缺测保持 `null`，不按零处理。`time_series_complete` 和 `wind_gust_complete` 可用于筛除不完整年份。地面积雪不是屋顶雪荷载，降雪量也不能直接换算为雪荷载。

**阵风口径待核实：** 当前实际接口对 `models=era5` 返回了阵风数值，但 Open-Meteo 官方模型变量表标示 ERA5 不含阵风。正式用于工程超限计算前，应向数据方核实生成方式，并用当地台站或专项资料交叉核对。冰雹天气代码和普通降水不能替代历史冰雹事件或直径。

数据接口说明：[Open-Meteo Historical Weather API](https://open-meteo.com/en/docs/historical-weather-api)。

## 2026-10-11 升级

小时字段包括：风速、阵风、风向、温度、相对湿度、露点、地表/海平面气压、降水、降雨、降雪、云量、短波辐照；另用 ERA5-Land 获取地面积雪深度，共 14 类。

每个字段有单位、数据源和有效小时数。缺字段或单位不匹配时仅保留该字段为未知，其他数据继续返回；部分年份失败会显示缺口。逐年 `comparable_fields` 按完整日历年及逐字段有效小时数标记，`comparable_full_year` 仅在所有字段都完整时为真。部分年份的累计值只是已取得小时的合计。

`annual_history`、`field_coverage`、`field_sources`、`source_segments`、`missing_parameters` 与 `cache` 随响应返回。整单与抗灾调试使用同一查询实现，WeatherProfile 保存逐年记录、温湿度、气压与积雪深度；原始小时数据缓存于 `outputs/provider-cache.sqlite3`，需要时可通过同接口选择 `hourly` 输出。

短波辐照小时平均 W/m² 乘 1 小时累计后除 1000，得到 kWh/m²；不是组件实际发电量。风向使用圆周平均；阵风峰值附时间和对应风向。

官方文档支持表与实际 ERA5 阵风返回存在差异；本次实测及 [Open-Meteo 官方源码](https://github.com/open-meteo/open-meteo/blob/main/Sources/App/Era5/Era5Variables.swift) 显示阵风变量对应 GRIB 49.128。响应标注这一差异，不混称 ERA5-Land 阵风或现场观测。

参考：[Open-Meteo 历史气象文档](https://open-meteo.com/en/docs/historical-weather-api)。
