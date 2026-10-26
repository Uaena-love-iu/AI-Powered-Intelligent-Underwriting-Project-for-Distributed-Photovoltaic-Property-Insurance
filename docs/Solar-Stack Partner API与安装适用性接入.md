# Solar-Stack Partner API 与安装适用性接入

## 已接入内容

后端可通过只读 Solar-Stack Partner API 查询组件。配置 `PV_SOLAR_STACK_API_KEY` 后，组件查询顺序为：本地审核目录 → Solar-Stack 精确型号查询 → 可选的已审核在线目录。API 密钥只在服务端使用，不能放进浏览器。

实现使用官方 `GET /v1/panels?search=...&market=GLOBAL&perPage=100&page=1` 搜索，再用列表返回的 opaque ID 调用 `GET /v1/panels/{id}`。仅接受规范化后完整型号相同、GLOBAL 市场下唯一的一条结果；近似型号、同名多条记录、未知版本均不自动择一。成功结果会在当前后端进程内缓存 24 小时，减少 Partner API 配额消耗。

映射到项目字段：

| Solar-Stack 字段 | 项目字段 | 边界 |
| --- | --- | --- |
| `pmax` | `rated_power_w` | 额定功率 |
| `series.hailDiameterMm` | `hail_resistance_mm` | 厂家冰雹试验直径 |
| `series.hailSpeedMs` | `hail_impact_velocity_m_s` | 厂家冰雹试验速度 |
| `series.maxLoadFrontPa` | `front_static_load_pa` | 组件正面最大静态载荷 |
| `series.maxLoadRearPa` | `back_static_load_pa` | 组件背面最大静态载荷 |

API 未提供与项目设计风压、屋面结构雪荷载同义的可普遍使用字段，因此不会把正反面静态载荷映射为 `wind_load_pa` 或 `snow_load_pa`。空值表示目录尚未收录，不表示设备没有该能力。返回多个规格书版本时，报告先链接到 Solar-Stack 型号页并要求核对版本；不会假定第一份 PDF 就适用于该项目。

项目报告保留 Solar-Stack 来源名称及型号页链接作为来源说明。其规格来自第三方目录对厂家公开资料的转录，仍需核对精确铭牌、厂家资料版本和实际安装配置。Solar-Stack 的 Partner API 是只读且有账户配额；产品中展示其数据时须保留 Solar-Stack 链接与来源署名，并遵守其[使用条款](https://www.solar-stack.com/en/terms)。

## 安装适用性与等级边界

新增的逐灾种安装参数复核输出为 `pending_confirmation`，包含需补证清单及规则编号：

- 大风：型号/手册版本/配置编号，固定方式、固定边、固定点、夹持位置、导轨方向和支撑条件；普通照片不能代替隐蔽连接验收资料。
- 冰雹：精确型号对应的规格书或检测报告、版本、试验直径、冲击速度、试验方法，以及能与厂家试验同口径的历史事件资料。
- 积雪：安装受力方向及厂家配置、屋面/支架承载资料，以及与厂家能力同单位同定义的设计或历史雪荷载。

在逐项证据和逐年同口径历史序列未到位前，系统不生成方案中的 A/B/C、R1/R2/R3 候选等级，也不把组件参数、气象背景或 AI 图像描述解释为整站结构安全或真实出险概率。

## 当前资料缺口

本次收到的《光伏风险等级评估方案_安装方式完善版.docx》包含安装方式核验、风险筛查流程、阈值公式和验收要求，但没有高火险行业名称、加费比例或拦截行业清单。因此业务规则配置仍为空；行业字段缺失或清单未配置时系统继续要求补充或人工核验，不会默认放行，也不会编造行业名单和费率。

继续完成对应业务规则需要：

1. 官方高火险行业清单，含行业分类口径、匹配词或代码、适用阶段、动作（加费/拦截）及生效版本；加费项还需费率、基数和取整规则。
2. 型号对应的厂家安装手册/配置页，能确认版本、载荷性质、方向及每个配置的固定条件。
3. 带坐标和年份的历史冰雹事件数据；项目设计风压、屋面结构设计雪荷载或与组件能力同口径的逐年系列。不同单位和定义之间不作工程换算。
4. 由业务负责人确认候选总等级汇总门槛和各核保动作的适用阶段。

## 官方接口参考

- [Solar-Stack 数据 API 与 Partner API 说明](https://www.solar-stack.com/en/api)
- [Solar-Stack Partner API 参考文档](https://partner-api.solar-stack.com/docs)
- [Solar-Stack Partner API 使用条款](https://www.solar-stack.com/en/terms)
