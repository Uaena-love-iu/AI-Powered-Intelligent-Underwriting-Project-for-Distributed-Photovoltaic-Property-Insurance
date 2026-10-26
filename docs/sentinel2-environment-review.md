# Sentinel-2 周边环境影像辅助复核

## 能力边界

系统通过 Copernicus Data Space Sentinel Hub 的 Sentinel-2 L2A 数据集查询所选地址附近的卫星影像。B02、B03、B04 真彩色通道的原生空间分辨率为 10 米。默认获取所选点周边 1 公里半径的影像，输出网格按约 10 米像元设置；该像元大小不等于项目边界或组件级分辨率。

影像用于观察较大范围的地表背景，例如耕作地、树木覆盖、水体、建成区和裸地/山地。地址点来自高德地址级地理编码，属于近似定位，不能保证落在实际投保地块内。系统不得据此确认具体投保地块边界、组件是否安装、养殖活动、产权、结构安全或现场当前状态。AI 描述只在用户单独勾选后才把影像发给已有的视觉大模型，并始终作为非约束性建议；不会写入核保事实，也不会直接改变承保/拒保结论。

## 处理流程

1. 只有案件存在模糊、低分辨率或不可读图片时，界面才提供地图辅助复核。
2. 核保员确认地址解析同意、选中高德返回的地址候选，然后单独勾选同意向 Copernicus 发送其 WGS84 坐标。
3. 服务端获取 OAuth token，搜索最近 180 天的 Sentinel-2 L2A 场景；优先选最新且场景云量低于配置阈值的影像。若没有符合阈值的场景，返回可用影像中云量最低的一景并展示警告；找不到覆盖场景时返回明确错误。
4. 服务端按所选影像日期请求 2 公里见方的真彩色 PNG，屏蔽云、云影、饱和/无效和无数据像元，并计算透明遮罩比例。原始 PNG 只在本次响应中返回，不写入案件数据库。
5. 若另行勾选“大模型辅助描述”，服务端将影像传给现有 `PV_VLM_*` 模型设置。结构化类别和证据随人工复核记录保存；未勾选时不调用视觉模型。
6. 核保员核实位置和影像后，保存人工观察。案件与报告仅保存场景 ID、日期、云量、遮罩比例、查询范围、分辨率、署名及可选模型描述，不保存影像本体。

## 配置

在项目根目录 `.env` 中配置 Copernicus Data Space OAuth 客户端凭证：

```dotenv
PV_SENTINEL_HUB_CLIENT_ID=your-client-id
PV_SENTINEL_HUB_CLIENT_SECRET=your-client-secret
PV_SENTINEL_TIMEOUT_SECONDS=45
PV_SENTINEL_LOOKBACK_DAYS=180
PV_SENTINEL_MAX_CLOUD_COVER_PERCENT=40
PV_SENTINEL_RADIUS_M=1000
PV_SENTINEL_USE_SYSTEM_PROXY=false
PV_VLM_USE_SYSTEM_PROXY=false
```

客户端 ID 和 Secret 只保留在后端环境，不发到浏览器。视觉模型默认直连；如果当前网络必须经系统代理访问模型服务，可将 `PV_VLM_USE_SYSTEM_PROXY` 设为 `true`。若网络要求走系统代理，将 `PV_SENTINEL_USE_SYSTEM_PROXY` 设为 `true`。`GET /ready` 的 `optional_capabilities.sentinel2_configured` 只检查配置项是否齐全，不会登录或试抓影像。首次实际查询仍需确认账户、服务配额、网络和 OAuth 应用有效。

## 接口

`POST /api/v1/cases/{case_id}/map-review/sentinel-context` 需要图片质量异常案件、地址、已返回的高德候选项及三类明确授权：向高德解析地址、向 Copernicus 发送坐标；若请求大模型分析，还需确认把影像交给已配置的 VLM。接口返回 `image_base64`、影像 MIME 类型和 `metadata`。

`POST /api/v1/cases/{case_id}/map-review` 可把返回的 `metadata` 作为 `sentinel_context` 一并提交，保存审计记录和报告内容。若所提交影像中心坐标与所选候选不匹配，接口拒绝保存。

## 影像筛选和限制

- 场景云量是场景/瓦片尺度的估算值，不是项目地块云量。影像透明像元比例同时包括云、云影和无数据像元。
- 默认最多回看 180 天，半径 1 公里，允许配置范围为 250–2,000 米；影像边长最多 400 像素，响应最多 4 MB。
- 遥感影像不是实时影像，季节、云量、山地阴影及混合像元都会影响判断。树木覆盖不能单独推断为林业用地；绿色区域也不能证明是耕作地或养殖场。
- Copernicus 开放数据使用时遵守数据条款。生成的影像或公开材料须保留 `Contains modified Copernicus Sentinel data [year]` 署名，本系统把署名保存在页面和报告中。
- 新接入仅用于 Sentinel-2。既有高德卫星图仍按高德服务条款通过地图界面人工查看，不下载、不截图、不转交视觉模型。

## 复核验证

当前自动化测试用模拟 OAuth、Catalog、Process API 和 VLM 响应验证了地址范围、场景筛选、云影遮罩配置、异常/大响应处理及模型结构化结果。测试不等于真实 Copernicus 服务联通验证；配好 OAuth 凭证后，需选用已知坐标做一次线上 smoke test，再用主办方 A/B/C 样本校验环境描述，重点评估农林用地误判。

## 官方资料

- [Copernicus Sentinel-2 数据集与 10 米波段](https://dataspace.copernicus.eu/data-collections/copernicus-sentinel-missions/sentinel-2)
- [Sentinel Hub OAuth 认证](https://documentation.dataspace.copernicus.eu/APIs/SentinelHub/Overview/Authentication.html)
- [Catalog API](https://documentation.dataspace.copernicus.eu/APIs/SentinelHub/Catalog.html)
- [Process API 新版接口示例](https://documentation.dataspace.copernicus.eu/APIs/SentinelHub/UserGuides/BeginnersGuide.html)
- [配额与免费额度](https://documentation.dataspace.copernicus.eu/Quotas.html)
- [归属署名说明](https://documentation.dataspace.copernicus.eu/FAQ.html)
