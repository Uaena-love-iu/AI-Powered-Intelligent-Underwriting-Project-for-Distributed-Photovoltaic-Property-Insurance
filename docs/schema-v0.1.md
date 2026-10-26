# 核保数据契约 Schema v0.1

## 1 目的

本文件定义材料接收、OCR、视觉识别、外部数据、规则判断和综合核保决策之间的统一数据格式。

技术人员一和技术人员二必须按照本契约交换数据。任何字段修改先更新本文件并经双方确认，不允许在各自模块中私自创建含义重复的字段。

## 2 当前业务范围

阶段一只处理：

\- 工商业屋顶式光伏
\- 车棚顶式光伏

以下场景属于拒保候选场景：

\- 农田
\- 林地
\- 畜牧场景
\- 渔业场景
\- 涉水环境
\- 滩涂
\- 山地
\- 沙漠化环境
\- 备案材料包含集中式、林光、渔光、滩涂等禁投关键词

本版本不检查支架锈蚀/变形、防水层损坏和组件背板鼓包；危险工艺等其他扩展类别可按配置启用。

## 3 通用约束

1\. `schema\_version` 固定为 `0.1.0`。
2\. 所有置信度采用 0 到 1 之间的小数。
3\. 未识别成功的字段使用 `null`，不允许编造内容。
4\. `uncertain` 与 `not\_detected` 含义不同。
5\. 低置信度结果必须标记为 `uncertain`。
6\. 图片质量不足或必需 OCR 字段缺失/低置信时进入 `request\_more`；风险存在但现场关系或结论不确定时转 `manual\_review`，不能强行拒保或当作通过。
7\. 每个识别结果必须能够追溯到具体材料。
8\. 图片风险点应尽可能提供 bbox。
9\. bbox 使用 0 到 1 的归一化坐标。
10\. 组件参数和气象数据必须保留来源、单位及获取时间。
11\. 系统不输出具体保费金额。

## 4 顶层对象 UnderwritingCase

| 字段 | 类型 | 必填 | 产生方 | 含义 |
| --- | --- | --- | --- | --- |
| schema\_version | string | 是 | 系统 | 当前固定为 0.1.0 |
| case\_id | string | 是 | 系统 | 单次核保案件唯一编号 |
| project | ProjectInfo | 是 | 材料解析和用户输入 | 项目与被保险标的信息 |
| materials | Material\[] | 是 | 材料接收模块 | 本次提交的全部材料 |
| equipment\_inventory | EquipmentInventoryItem\[] | 否 | XLSX 设备清单解析器 | 设备清单行项目和来源位置；无清单时为空数组 |
| ocr\_fields | OcrField\[] | 是 | OCR Provider | 结构化文字识别结果 |
| findings | RiskFinding\[] | 是 | Vision Provider | 图片风险识别结果 |
| component\_profile | ComponentProfile 或 null | 否 | 组件参数 Provider | 组件型号及抗灾参数 |
| weather\_profile | WeatherProfile 或 null | 否 | 气象 Provider | 项目所在地气象风险 |
| catastrophe\_assessment | CatastropheAssessment 或 null | 否 | 风险量化模块 | 设备能力与气象风险比较结果 |
| location\_assessment | LocationAssessment 或 null | 否 | 位置核验模块 | 项目地址材料与现场水印位置的交叉核验结果、来源及天气查询位置的核实状态 |
| material\_reviews | MaterialReview\[] | 是 | 规则引擎 | 每份材料的审核结论 |
| decision | UnderwritingDecision 或 null | 否 | 决策模块 | 整单综合核保意见 |
| processing\_trace | ProcessingTrace\[] | 是 | 各处理模块 | 模型、耗时和异常留痕 |
| package\_assessment | PackageAssessment 或 null | 否 | 材料门禁 | 照片数量、备案证和可选的全景视角标注 |
| map\_reviews | MapImageryReview\[] | 否 | 核保员人工复核 | 可选外部地图位置观察；不进入自动风险事实 |

位置核验先区分项目级坐标与乡镇等区域级地址。只有项目级坐标与照片水印坐标均标明可识别的坐标系，才统一到 WGS-84 比较：不超过 500 米为位置初核一致（仍需核对项目身份）；超过 500 米且不超过 2 公里转人工复核；超过 2 公里为重大位置冲突，暂停自动核保，核对坐标系、录入和原件后再决定是否拒保。区域级地理编码点不适用这些距离阈值；备案地址与照片已提供时，应说明精度不足，不提示缺少另一类材料。

`PackageAssessment` 会计算图片数量、全景照数量、是否含备案证、可选的正面平视和俯拍视角标注、缺失的必需材料类别，以及需要补充的项目。Demo 门槛包括至少 5 张图片、2 张全景照、1 份备案证，并要求提供屋顶连接处、女儿墙/排水、并网材料、电气接地、组件铭牌、逆变器铭牌和汇流箱材料；屋顶式项目还要求车间照片，监控照片为可选。门槛不满足时整案建议补充材料。

`LocationAssessment.status` 取 `verified`（项目级位置证据一致）、`distance_screened`（备案地址候选点与现场照片坐标按原始数值均在 500 米内，位置项不触发人工复核，但不声称精确核验）、`single_source`（仅一份位置来源）、`uncertain`（无法可靠比较）、`conflict`（明显冲突）或 `missing`（没有位置证据）。`evidence` 保留材料编号、文件名、原始地址及坐标。`weather_coordinates_verified` 区分交叉核验后的位置与仅供参考的天气候选位置；`distance_screened` 时保持 `false`。位置冲突只预警并暂停自动通过；人工核验后可通过案件复核记录最终意见，系统不凭自动距离比较直接拒保。

图片数量只统计 JPEG/PNG；全景数量统计非重复的 JPEG/PNG 全景材料；画质不足另按材料可用性处理，不把已提交的全景误报为缺失。全景视角标注仅供复核参考，不进入缺材门槛。每张全景图建议具备日期和经纬度水印。当前赛题阶段缺少水印仅提示建议补充，不单独阻断；水印日期已识别时，仍须处于拟起保日前 15 日内。若日期或坐标进入校验但无法可靠读取，系统要求补充可核验材料。PDF 备案材料不计入图片数量。

设备清单可作为 `equipment_inventory` 类别上传 XLSX。系统读取工作表行并保留原文件、工作表和行号来源；明确且唯一的光伏组件型号可用于补充案件型号。清单价格只作为原始资料展示，不用于推算保额或保费；清单不能替代铭牌照片和组件抗灾规格来源。

赛题原始高火险行业清单与加费规则尚未提供。若提交车间材料，工作台要求填写企业所属行业；系统明确转人工核验，不会因缺少行业清单而自动视为低风险。

## 5 ProjectInfo 项目信息

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| project\_name | string 或 null | 是 | 项目名称，无法识别时为 null |
| insured\_name | string 或 null | 是 | 被保险人名称 |
| insured\_address | string 或 null | 否 | 被保险人地址，用于与备案材料交叉核验 |
| project\_entity | string 或 null | 否 | 备案证项目单位 |
| industry\_name | string 或 null | 否 | 企业所属行业；正式高火险清单未配置时转人工核验 |
| project\_type | enum | 是 | rooftop、carport、unsupported、unknown |
| installation\_type | enum | 是 | color\_steel\_roof、flat\_roof、tile\_roof、carport\_roof、unknown |
| site\_address | string 或 null | 是 | 被保险项目地址 |
| province | string 或 null | 否 | 省 |
| city | string 或 null | 否 | 市 |
| district | string 或 null | 否 | 区县 |
| longitude | number 或 null | 是 | 项目经度，缺失时触发补材 |
| latitude | number 或 null | 是 | 项目纬度，缺失时触发补材 |
| proposed\_start\_date | date 或 null | 是 | 计划起保日期 |
| component\_model | string 或 null | 是 | 组件型号，缺失时触发补材 |
| submission\_ip | string 或 null | 否 | 材料提交端 IP，仅用于审计 |

## 6 Material 材料对象

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| material\_id | string | 是 | 材料唯一编号 |
| category | enum | 是 | 材料类别 |
| capture\_view | enum | 否 | 全景照视角：front\_level、overhead、other、unknown |
| file\_name | string | 是 | 原始文件名 |
| media\_type | string | 是 | image/jpeg、image/png、application/pdf 等 |
| sha256 | string 或 null | 否 | 文件摘要，用于证据追溯 |
| captured\_at | datetime 或 null | 否 | 水印或元数据中的拍摄时间 |
| watermark\_status | enum | 否 | present、absent、uncertain、not\_checked |
| longitude | number 或 null | 否 | 图片水印中的经度 |
| latitude | number 或 null | 否 | 图片水印中的纬度 |
| quality\_status | enum | 是 | usable、poor、unusable、unknown |
| quality\_confidence | number 或 null | 否 | 图片质量判断置信度 |
| quality\_issues | string\[] | 是 | blur、dark、wrong\_angle、key\_area\_missing 等 |
| parse\_status | enum | 是 | pending、success、partial、failed |

材料类别 `category`：

\- `panorama`
\- `roof\_connection`
\- `parapet`
\- `workshop`
\- `filing\_certificate`
\- `grid\_connection\_document`
\- `electrical\_grounding`
\- `component\_nameplate`
\- `inverter\_nameplate`
\- `combiner\_box`
\- `monitoring\_optional`
\- `other`

## 7 OcrField 文字识别结果

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| field\_id | string | 是 | OCR 字段唯一编号 |
| material\_id | string | 是 | 来源材料编号 |
| field\_name | string | 是 | project\_name、insured\_name、component\_model 等 |
| raw\_value | string 或 null | 是 | OCR 原始文字 |
| normalized\_value | string 或 null | 是 | 清洗后的标准值 |
| value\_status | enum | 是 | extracted、missing、uncertain |
| confidence | number | 是 | OCR 置信度 |
| bbox | Bbox 或 null | 否 | 文字在图片中的位置 |
| provider | string | 是 | OCR 服务名称 |
| model | string | 是 | 使用的模型或版本 |
| evidence\_text | string 或 null | 否 | 支撑结果的原始文字片段 |

首期会从备案/并网材料提取项目名称、项目单位和地址；从组件、逆变器及汇流箱铭牌提取型号和额定参数；从接地检测记录提取接地电阻、检测日期和结论。可选监控材料仅用于提示核保员检查优惠资格，不据此自动计算折扣。

## 8 RiskFinding 图片风险识别结果

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| finding\_id | string | 是 | 风险点唯一编号 |
| material\_id | string | 是 | 来源材料编号 |
| category | enum | 是 | 风险类别 |
| label | string | 是 | 面向报告展示的中文标签 |
| detection\_status | enum | 是 | detected、not\_detected、uncertain、not\_applicable |
| severity | enum | 是 | info、low、medium、high、critical、unknown |
| confidence | number | 是 | 识别置信度 |
| environment\_relation | enum | 否 | 拒保环境与项目关系：project\_site、operational\_surroundings、distant\_background、uncertain；其他风险默认 uncertain |
| bbox | Bbox 或 null | 否 | 风险区域坐标 |
| evidence\_text | string | 是 | 模型识别依据 |
| provider | string | 是 | 视觉服务名称 |
| model | string | 是 | 模型名称或版本 |
| requires\_manual\_review | boolean | 是 | 是否需要人工复核 |

自动拒保门槛只适用于 8 类拒保环境：必须为 `detected`、`environment_relation` 属于 `data/rules/challenge_open_ai_01.json` 配置范围、置信度达到配置门槛且 `requires_manual_review=false`。当前仅启用 `project_site`，默认分数门槛为 0.65；该分数未做业务概率校准。邻近周边或关系不确定转人工核验，远处背景不允许配置为现场拒保事实。旧接口或模型未返回 `environment_relation` 时默认 `uncertain`，不会被当作现场确认。多张全景的检查覆盖按案件合并；仅当所有全景均缺少某项结果时才针对该项要求补充。

阶段一风险类别 `category`：

每张送入视觉识别的图片还必须包含 `image_quality` 检查：清晰可用时为 `not_detected`，质量不足时为 `detected`，无法判断时为 `uncertain`。

\- `agriculture\_environment`
\- `forest\_environment`
\- `livestock\_environment`
\- `fishery\_environment`
\- `water\_adjacent\_environment`
\- `tidal\_flat\_environment`
\- `mountain\_environment`
\- `desertification\_environment`
\- `severe\_shading`
\- `minor\_shading`
\- `aisle\_obstruction`
\- `missing\_parapet\_or\_guardrail`
\- `drainage\_abnormal`
\- `flammable\_material`
\- `hazardous\_material`

风险类别还包括 `image_quality`、`roof_connection_abnormal`、`module_damage`、`inverter_abnormal`、`electrical_grounding_abnormal`、`combiner_box_seal_abnormal`、`combiner_box_fuse_abnormal` 和 `surge_protector_abnormal`。设备外观类别只记录照片可直接观察的缺陷；不能依据普通照片推断电气性能、接地电阻或隐蔽结构合格。接地电阻与检验结论须来自可读的检测记录。

模糊、过暗、关键区域缺失或视角不符时要求补拍，不能把未充分检查的风险判为未发现。

## 9 Bbox 风险位置

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| x\_min | number | 是 | 左边界，0 到 1 |
| y\_min | number | 是 | 上边界，0 到 1 |
| x\_max | number | 是 | 右边界，0 到 1 |
| y\_max | number | 是 | 下边界，0 到 1 |
| coordinate\_space | string | 是 | 固定为 normalized\_0\_1 |

必须满足：

\- `0 <= x\_min < x\_max <= 1`
\- `0 <= y\_min < y\_max <= 1`

## 10 ComponentProfile 组件抗灾参数

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| component\_model | string | 是 | 标准化组件型号 |
| manufacturer | string 或 null | 否 | 厂商 |
| rated\_power\_w | number 或 null | 否 | 标称功率，单位 W |
| front\_static\_load\_pa / back\_static\_load\_pa | number 或 null | 否 | 厂家正面/背面最大静态载荷；保留原方向和口径，不转换成项目设计风压或结构雪荷载 |
| hail\_resistance\_mm | number 或 null | 否 | 厂家冰雹试验直径，单位 mm；不是现场可抵御直径的保证 |
| hail\_impact\_velocity\_m\_s | number 或 null | 否 | 厂家冰雹试验冲击速度，单位 m/s |
| wind\_load\_pa / snow\_load\_pa | number 或 null | 否 | 厂家明确列示的风/雪荷载值，单位 Pa；来源和测试条件需结合 `source_note` 核对，当前不与历史天气指标直接计算能力比 |
| market\_version / model\_derivation\_method | string 或 null | 否 | 市场版本及型号是否逐项列示或经展开 |
| source\_document\_type / source\_note | string 或 null | 否 | 来源文档类型及参数适用说明 |
| parameter\_sources | object | 是 | 数值参数到厂家来源 URL 的逐字段映射 |
| source\_url | string 或 null | 是 | 厂商或公开资料来源 |
| source\_name | string | 是 | 数据来源名称 |
| retrieved\_at | datetime | 是 | 数据获取时间 |
| match\_confidence | number | 是 | 目录型号匹配参考分；不代表参数经人工核实或风险概率 |

## 11 WeatherProfile 气象数据

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| longitude | number | 是 | 查询经度 |
| latitude | number | 是 | 查询纬度 |
| historical\_max\_wind\_m\_s | number 或 null | 否 | 历史最大 10 米日阵风，单位 m/s；Open-Meteo 再分析值，不是项目规范设计风速 |
| historical\_max\_daily\_wind\_speed\_m\_s | number 或 null | 否 | 历史最大 10 米日持续风速，单位 m/s；不是项目规范设计风速 |
| historical\_max\_daily\_precipitation\_mm / historical\_max\_daily\_rain\_mm | number 或 null | 否 | 单日总降水（含降雪）和单日降雨历史最大值，单位 mm；不是洪水或内涝结论 |
| historical\_max\_daily\_precipitation\_hours | number 或 null | 否 | 有降水的单日最长时数，范围 0–24 h |
| historical\_max\_hail\_mm | number 或 null | 否 | 历史最大冰雹直径，单位 mm |
| historical\_max\_snow\_load\_pa | number 或 null | 否 | 历史雪荷载估计，单位 Pa |
| historical\_max\_daily\_snowfall\_cm | number 或 null | 否 | 历史最大单日降雪量，单位 cm；不得换算为结构雪荷载 |
| observation\_start / observation\_end | date 或 null | 否 | 请求的数据起止日期，不代表完整返回覆盖 |
| returned\_data\_start / returned\_data\_end | date 或 null | 否 | 上游实际返回的序列日期范围 |
| expected\_day\_count / returned\_day\_count | integer 或 null | 否 | 请求期间预期日数及实际返回日数 |
| wind\_valid\_day\_count / wind\_speed\_valid\_day\_count / precipitation\_valid\_day\_count / rain\_valid\_day\_count / precipitation\_hours\_valid\_day\_count / snowfall\_valid\_day\_count | integer 或 null | 否 | 每种每日序列的有效值日数，用于揭示缺测 |
| grid\_longitude / grid\_latitude / grid\_distance\_km | number 或 null | 否 | 上游实际采用的气象网格坐标及与项目查询点的距离 |
| grid\_elevation\_m / timezone / dataset\_selection | number/string 或 null | 否 | 网格 DEM 高程、日汇总时区及数据集选择 |
| wind\_unit / wind\_speed\_unit / precipitation\_unit / rain\_unit / precipitation\_hours\_unit / snowfall\_unit | string 或 null | 否 | API 返回并经校验的单位 |
| data\_quality\_notes | string\[] | 是 | 网格元数据、有效日数等数据质量说明 |
| source\_name | string | 是 | 气象数据来源 |
| source\_url | string 或 null | 否 | 来源地址 |
| retrieved\_at | datetime | 是 | 获取时间 |

## 12 CatastropheAssessment 自然灾害量化

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| resistance\_level | enum | 是 | low、medium、high、unknown |
| expected\_loss\_risk | enum | 是 | low、medium、high、critical、unknown |
| factors | string\[] | 是 | 影响结果的因素 |
| explanation | string | 是 | 可解释的比较过程 |
| triggered\_rule\_ids | string\[] | 是 | 命中的规则编号 |
| requires\_manual\_review | boolean | 是 | 是否需要人工判断 |

该对象只输出风险等级和理由，不输出具体保费。风、雹、雪资料口径未验证时，等级和能力比保持 `unknown`/`null` 并要求人工核验；不能把未知解释成通过。

## 13 MaterialReview 逐项审核结果

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| material\_id | string | 是 | 被审核材料编号 |
| action | enum | 是 | pass、warning、surcharge、recommend_reject、conditional、request\_more、not\_applicable |
| triggered\_rule\_ids | string\[] | 是 | 命中的业务规则 |
| finding\_ids | string\[] | 是 | 引用的风险点 |
| ocr\_field\_ids | string\[] | 是 | 引用的 OCR 字段 |
| reasons | string\[] | 是 | 审核原因 |
| missing\_requirements | string\[] | 是 | 需要补充的内容 |
| requires\_manual\_review | boolean | 是 | 是否需要人工复核 |

## 14 UnderwritingDecision 综合核保意见

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| decision | enum | 是 | accept、recommend_reject、surcharge、conditional\_accept、request\_more、manual\_review |
| decisive\_rule\_ids | string\[] | 是 | 决定最终结论的规则 |
| reasons | string\[] | 是 | 综合判断理由 |
| conditions | string\[] | 是 | 附加承保条件 |
| warnings | string\[] | 是 | 风险提示 |
| missing\_requirements | string\[] | 是 | 需要补充的材料 |
| generated\_at | datetime | 是 | 结论生成时间 |

决策优先级暂定：

1\. `recommend_reject`
2\. `request\_more`
3\. `manual\_review`
4\. `surcharge`
5\. `conditional\_accept`
6\. `accept`

当前比赛 Demo 使用上述保守顺序。可选监控材料只触发优惠资格复核提示；未提供正式费率和资格规则时不计算折扣或保费。最终优先级和业务规则仍须由金融成员确认。

## 15 ProcessingTrace 处理留痕

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| step | string | 是 | material\_parse、ocr、vision、component\_lookup、weather\_lookup、catastrophe\_assessment、rule\_engine、decision |
| provider | string | 是 | 执行模块或外部服务 |
| model | string 或 null | 否 | 模型名称和版本 |
| started\_at | datetime | 是 | 开始时间 |
| finished\_at | datetime | 是 | 完成时间 |
| latency\_ms | integer | 是 | 耗时 |
| status | enum | 是 | success、partial、failed |
| error\_code | string 或 null | 否 | 错误编号 |
| error\_message | string 或 null | 否 | 脱敏后的错误信息 |

`ProcessingStep` 另包含 `map_review`，表示核保员在原分析完成后执行了地图辅助复核。

### MapImageryReview 外部地图人工观察

`map_reviews` 默认是空数组。记录字段包括：复核编号、触发复核的低质量材料编号、地图服务、地址来源与查询/匹配地址、地理编码置信分与地址理解度、精确匹配状态、BD-09 坐标、地图官方链接、查询及复核时间、复核人、是否确认该点对应本项目、光伏组件可见性、观察到的安装载体、影像日期（未知时为 `null`）和人工观察说明。

每条记录的 `decision_effect` 固定为 `manual_context_only`。地图观察不修改 `findings`、材料规则动作或系统综合结论。“地图中未见”不代表现场没有组件。高德影像仍只通过高德地图页面人工查看，系统不下载或转发高德影像。

记录可选的 `sentinel_context`，用于保存 Copernicus Sentinel-2 L2A 周边环境影像的审计元数据：数据集/场景编号、获取日期、场景/瓦片云量估计及其口径说明、云/云影/无数据像元遮罩比例、WGS84 查询中心与范围、查询半径、约 10 米输出像元尺度、署名、影像质量提示及可选 VLM 环境描述。原始 PNG 不写入案件数据。地址级坐标属于近似定位；云量估算不是项目地块级实测。VLM 环境类别、置信度和证据只作为人工复核建议，不能代替现场材料或直接改变自动核保结论。

## 16 需要金融成员确认的问题

1\. 低置信度阈值具体是多少？
2\. 缺少经纬度是否必须补材？
3\. 缺少组件型号是否必须补材？
4\. 拍摄日期超过起保前 15 日时，是提示还是补材？
5\. 项目单位与被保险人不一致时，是预警还是拒保？
6\. 项目地址与被保险地址不一致时，是预警还是拒保？
7\. 严重遮挡与轻微遮挡的业务边界是什么？
8\. 车间易燃物和危险品分别触发提示、加费还是拒保？
9\. 自然灾害风险等级如何触发加费或附加条件？
10\. 综合决策优先级是否认可？
11\. 对项目邻近的农田、林地、水体或山地，业务定义的拒保环境边界是什么？
12\. 哪些材料在首期属于强制材料？

### 通过、补件和人工复核的触发条件

- **建议承保**：材料数量和类别门槛已满足；适用检查均有可用结果；必需 OCR 字段及交叉核验完整；未出现确认属于项目现场的禁保环境、明确拒保项或未解决的人工复核事项。水印缺失提示和远处背景景物等非阻断警示可以同时展示。
- **建议补件**：明确缺少必需材料/字段，图像无法解码或关键区域模糊，某项检查在所有全景中都没有结果，或已识别的水印日期不符合时效要求。
- **转人工复核**：高风险环境关系不明或只在邻近周边、模型主动要求复核、OCR 交叉冲突、识别服务失败、业务规则清单缺失，或灾害评估的必要数据不全。
- **建议拒保**：规则定义的硬拒保事实被明确识别且满足相应自动触发条件。对于环境类，必须是项目现场本身、置信度达到当前技术门槛且模型未要求人工复核；邻近环境边界待业务确认。

这些是系统建议结论的程序条件，不替代有权限核保人的最终审批。

## 17 案件历史与人工复核记录

异步分析接口 `POST /api/v1/underwriting/jobs` 返回 `job_id`。客户端通过 `GET /api/v1/underwriting/jobs/{job_id}` 轮询 `queued`、`running`、`completed` 或 `failed` 状态、阶段名称和进度百分比；完成时响应包含标准 `UnderwritingCase`。任务由当前服务进程执行，重启时未完成记录会标记为 `SERVER_RESTARTED`，不会自动恢复。

真实整单分析成功后，结构化 `UnderwritingCase` 会保存到本地 SQLite。历史接口为：

| 方法与路径 | 说明 |
| --- | --- |
| `GET /api/v1/cases` | 分页查询案件摘要，可按机器结论和人工复核状态筛选 |
| `GET /api/v1/cases/{case_id}` | 读取案件结果和全部人工复核事件 |
| `GET /api/v1/cases/{case_id}/report` | 下载包含机器意见及复核历史的 Markdown 报告 |
| `POST /api/v1/cases/{case_id}/review` | 记录复核人填写的最终结论、说明和 UTC 时间 |

人工复核事件追加保留；当前案件记录同时保存最新复核结论。相同案件编号不能重复创建分析记录。上传原文件只在本次请求处理期间保留在内存中，不写入数据库。数据库可能包含 OCR 提取的个人或企业信息。默认 `PV_AUTH_ENABLED=false`；启用后使用 SQLite 本地账号与 8 小时 Bearer 会话，支持 `viewer`、`underwriter`、`admin` 三种角色，首次启动通过 `PV_ADMIN_USERNAME`、`PV_ADMIN_PASSWORD` 引导创建管理员。该认证面向本地演示，不含 MFA、登录限速或生产级密钥管理；对外部署前仍需评估 HTTPS、网络访问控制、数据库加密和删除审计。`PV_CASE_RETENTION_DAYS` 可在数据责任方批准期限后配置自动清理，默认关闭。此清理不能代替正式的数据访问、加密和删除控制。

## 18 离线评测标注格式

每行一个匿名案件 JSON 对象，使用 `scripts/evaluate_cases.py` 与真实预测结果对齐。`suite` 可标记 A/B/C。可选的 `expected_ocr_fields` 用于逐字段 OCR 对照；只有在 `findings_complete: true` 且提供完整 `expected_findings` 时，评测器才会统计风险点误报率。最小示例：

~~~json
{"case_id":"case-a","suite":"B","expected_decision":"recommend_reject","required_high_risk_categories":[{"category":"water_adjacent_environment","material_id":"panorama-1"}],"required_rule_ids":["ENV-EXCLUDED-001"],"findings_complete":true,"expected_findings":[{"category":"water_adjacent_environment","material_id":"panorama-1"}],"expected_ocr_fields":[{"material_id":"filing-1","field_name":"project_name","expected_value":"示例项目"}],"required_findings":[{"category":"water_adjacent_environment","material_id":"panorama-1","bbox":{"x_min":0.1,"y_min":0.2,"x_max":0.4,"y_max":0.6}}]}
~~~

高风险召回只将 `detection_status=detected` 计为检出；`uncertain` 按漏检统计，符合赛题对高风险零漏检的验收底线。标注框使用 0 到 1 归一化坐标，定位命中默认要求 IoU ≥ 0.50。默认评测门槛为综合结论准确率 ≥ 0.90 且高风险场景召回率为 1.00；风险点精确率和 OCR 准确率可由命令行显式设置门槛，避免把尚未确认的指标阈值写成既定业务要求。
