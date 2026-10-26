from datetime import UTC, datetime

from app.catastrophe import CatastropheAssessmentEngine
from app.contracts import (
    ComponentProfile,
    DetectionStatus,
    EnvironmentRelation,
    EquipmentInventoryItem,
    MapImageryReview,
    OcrField,
    OcrValueStatus,
    RiskCategory,
    RiskFinding,
    RiskSeverity,
    SentinelContextRecord,
    SentinelEnvironmentAnalysis,
    SentinelEnvironmentObservation,
    UnderwritingCase,
    WeatherProfile,
)
from app.reports import generate_markdown_report


def make_case(*, include_decision: bool = True) -> UnderwritingCase:
    decision = None

    if include_decision:
        decision = {
            "decision": "manual_review",
            "decisive_rule_ids": ["RULE-001"],
            "reasons": ["业务核保规则尚未全部确认"],
            "conditions": [],
            "warnings": ["图片风险点需要人工复核"],
            "missing_requirements": ["补充屋顶连接照片"],
            "generated_at": "2026-09-25T10:00:00+08:00",
        }

    return UnderwritingCase.model_validate(
        {
            "schema_version": "0.1.0",
            "case_id": "case-report-001",
            "project": {
                "project_name": "示例光伏项目",
                "insured_name": "示例制造企业有限公司",
                "project_entity": "示例新能源有限公司",
                "project_type": "rooftop",
                "installation_type": "color_steel_roof",
                "site_address": "广东省示例市示例区",
                "province": "广东省",
                "city": "示例市",
                "district": "示例区",
                "longitude": 113.25,
                "latitude": 23.12,
                "proposed_start_date": "2026-10-01",
                "component_model": "PV-MODULE-580W",
                "submission_ip": None,
            },
            "materials": [
                {
                    "material_id": "material-panorama-001",
                    "category": "panorama",
                    "file_name": "site|panorama.jpg",
                    "media_type": "image/jpeg",
                    "sha256": None,
                    "captured_at": None,
                    "longitude": None,
                    "latitude": None,
                    "quality_status": "usable",
                    "quality_confidence": 0.95,
                    "quality_issues": [],
                    "parse_status": "success",
                }
            ],
            "ocr_fields": [],
            "findings": [
                {
                    "finding_id": "finding-001",
                    "material_id": "material-panorama-001",
                    "category": "severe_shading",
                    "label": "严重遮挡",
                    "detection_status": "uncertain",
                    "severity": "high",
                    "confidence": 0.72,
                    "bbox": None,
                    "evidence_text": "组件附近存在疑似大面积阴影",
                    "provider": "mock-vision",
                    "model": "mock-v1",
                    "requires_manual_review": True,
                }
            ],
            "component_profile": None,
            "weather_profile": None,
            "catastrophe_assessment": None,
            "material_reviews": [
                {
                    "material_id": "material-panorama-001",
                    "action": "warning",
                    "triggered_rule_ids": ["RULE-001"],
                    "finding_ids": ["finding-001"],
                    "ocr_field_ids": [],
                    "reasons": ["发现需要复核的图片风险"],
                    "missing_requirements": [],
                    "requires_manual_review": True,
                }
            ],
            "decision": decision,
            "processing_trace": [
                {
                    "step": "vision",
                    "provider": "mock-vision",
                    "model": "mock-v1",
                    "started_at": "2026-09-25T09:59:59+08:00",
                    "finished_at": "2026-09-25T10:00:00+08:00",
                    "latency_ms": 1000,
                    "status": "success",
                    "error_code": None,
                    "error_message": None,
                }
            ],
        }
    )


def test_comparison_field_notice_has_its_own_non_risk_section() -> None:
    case = make_case()
    notice = "交叉核对范围提示：未取得序列号的可靠识别值，暂不能核对该项"
    case.material_reviews = [case.material_reviews[0].model_copy(update={
        "triggered_rule_ids": ["DOC-COMPARISON-FIELD-UNAVAILABLE-001"],
        "reasons": [notice],
        "requires_manual_review": False,
    })]
    case.decision = case.decision.model_copy(update={"warnings": [notice]})
    report = generate_markdown_report(case)
    assert "### 核对范围提示" in report
    assert "### 风险提示" not in report


def test_generates_readable_markdown_report() -> None:
    report = generate_markdown_report(make_case())

    assert report.startswith("# 分布式光伏财产险 AI 核保报告")
    assert "**转人工复核**" in report
    assert "补充屋顶连接照片" in report
    assert "严重遮挡" in report
    assert "0.72" in report
    assert "site\\|panorama.jpg" in report
    assert "图片风险识别" in report
    assert "**当前待办：**客户补材" not in report
    assert "### 下一步事项" in report


def test_request_more_report_leads_with_required_missing_material_and_blocked_check() -> None:
    payload = make_case().model_dump(mode="json")
    payload["decision"]["decision"] = "request_more"
    payload["decision"]["missing_requirements"] = ["补充电站备案证", "补充清晰水印信息：拍摄日期"]
    payload["decision"]["decisive_rule_ids"] = ["CHECK-EVIDENCE-MISSING-001"]
    payload["decision"]["warnings"] = ["水印识别不完整"]
    payload["package_assessment"] = {
        "image_count": 4, "panorama_count": 2, "has_filing_certificate": False,
        "has_front_level_panorama": True, "has_overhead_panorama": True,
        "missing_requirements": [],
    }
    payload["applicable_checks"] = [
        {"area_id": "documents", "key": "filing", "label": "备案证明",
         "applicability": "applicable", "required_now": True,
         "evidence_requirement": "核对项目名称与建设内容", "reason": "必需"},
        {"area_id": "equipment", "key": "component", "label": "组件铭牌",
         "applicability": "applicable", "required_now": False,
         "evidence_requirement": "核对型号", "reason": "可选"},
    ]
    payload["check_results"] = [
        {"area_id": "documents", "key": "filing", "status": "missing",
         "customer_request": "补充备案证明：清楚显示项目名称与建设内容",
         "rule_ids": ["CHECK-EVIDENCE-MISSING-001"], "reason": "缺失"},
        {"area_id": "equipment", "key": "component", "status": "missing",
         "customer_request": "补充组件铭牌", "reason": "缺失"},
    ]
    payload["material_reviews"][0]["reasons"] = ["水印识别不完整"]
    payload["material_reviews"][0]["missing_requirements"] = ["补充清晰水印信息：拍摄日期"]
    payload["material_reviews"][0]["triggered_rule_ids"] = ["IMG-WATERMARK-DATE-001"]
    report = generate_markdown_report(UnderwritingCase.model_validate(payload))
    basis = report.split("### 核心依据：缺失材料与受阻核验", 1)[1].split("### 风险提示", 1)[0]
    assert basis.count("#### ") == 2
    assert "组件铭牌" not in basis
    assert "补充电站备案证" not in basis  # merged into the required filing check
    assert "无法完成「备案证明」核查" in basis
    assert "无法核验现场照片的拍摄日期" in basis
    assert "相关规则：" not in basis
    assert report.index("核心依据：缺失材料与受阻核验") < report.index("风险提示")
    assert "### 建议关联规则" not in report


def test_decisive_installation_evidence_is_paired_with_each_original_photo() -> None:
    payload = make_case().model_dump(mode="json")
    payload["decision"]["decision"] = "recommend_reject"
    payload["decision"]["decisive_rule_ids"] = ["INSTALLATION-TYPE-001"]
    payload["materials"][0]["file_name"] = "俯拍.jpg"
    second = {**payload["materials"][0], "material_id": "photo-2", "file_name": "近景.jpg"}
    payload["materials"].append(second)
    payload["stage_reviews"] = [{
        "stage": "installation_type", "action": "recommend_reject",
        "rule_ids": ["INSTALLATION-TYPE-001"],
        "material_ids": ["material-panorama-001", "photo-2"],
        "reasons": ["确认不是屋顶式或车棚顶式"],
        "terminates_run": True,
    }]
    payload["installation_analysis"] = {
        "provider": "test", "areas": [{
            "area_id": "project", "scene_type": "floating", "status": "confirmed",
            "material_ids": ["material-panorama-001", "photo-2"],
            "evidence_text": "两张图片确认水上光伏",
            "evidence_regions": [
                {"material_id": "material-panorama-001", "description": "阵列位于水面"},
                {"material_id": "photo-2", "description": "支柱立于水中"},
            ],
        }],
    }
    report = generate_markdown_report(UnderwritingCase.model_validate(payload))

    first = report.index("#### 证据 1｜俯拍.jpg")
    second = report.index("#### 证据 2｜近景.jpg")
    assert "阵列位于水面" in report[first:second]
    assert "支柱立于水中" not in report[first:second]
    assert "支柱立于水中" in report[second:]
    assert "Markdown 文件不包含图片附件" in report


def test_report_explains_the_end_to_end_decision_path_and_linked_evidence() -> None:
    report = generate_markdown_report(make_case())

    assert "判断过程与追溯" in report
    assert "整单汇总顺序" in report
    assert "模型不直接生成整单结论" in report
    assert "finding-001" in report
    assert "关联视觉证据" in report
    assert "规则说明待补充" in report


def test_report_shows_environment_relation_separately_from_detection() -> None:
    case = make_case()
    finding = RiskFinding(
        finding_id="finding-forest",
        material_id="material-panorama-001",
        category=RiskCategory.FOREST_ENVIRONMENT,
        label="林地环境",
        detection_status=DetectionStatus.DETECTED,
        severity=RiskSeverity.HIGH,
        confidence=0.88,
        environment_relation=EnvironmentRelation.PROJECT_SITE,
        evidence_text="项目设施所在用地为林地",
        provider="test-vision",
        model="test-model",
        requires_manual_review=False,
    )
    case.findings.append(finding)
    case.material_reviews[0].finding_ids.append(finding.finding_id)

    report = generate_markdown_report(case)

    assert "环境与现场关系" in report
    assert "项目现场本身" in report
    assert "项目设施所在用地为林地" in report


def test_report_includes_extracted_ocr_fields() -> None:
    case = make_case()
    field = OcrField(
        field_id="material-panorama-001:project_name",
        material_id="material-panorama-001",
        field_name="project_name",
        raw_value="示例光伏项目",
        normalized_value="示例光伏项目",
        value_status=OcrValueStatus.EXTRACTED,
        confidence=0.98,
        provider="mock-ocr",
        model="mock-v1",
    )
    report = generate_markdown_report(
        case.model_copy(update={"ocr_fields": [field]})
    )

    assert "OCR 字段提取结果" in report
    assert "示例光伏项目" in report
    assert "0.98" in report


def test_report_includes_structured_equipment_inventory() -> None:
    case = make_case().model_copy(
        update={
            "equipment_inventory": [
                EquipmentInventoryItem(
                    source_material_id="material-panorama-001",
                    worksheet_name="设备清单",
                    row_number=2,
                    item_category="组件",
                    item_name="光伏板",
                    manufacturer="晶澳",
                    specification="JAM72D42-630W",
                    quantity="8525",
                    normalized_component_model="JAM72D42-630W",
                    rated_power_w=630,
                )
            ]
        }
    )

    report = generate_markdown_report(case)

    assert "设备清单结构化结果" in report
    assert "JAM72D42-630W" in report
    assert "630 W" in report
    assert "8525" in report


def test_report_shows_component_evidence_and_weather_data_coverage() -> None:
    case = make_case()
    component = ComponentProfile(
        component_model="LR5-54HTB-440M",
        manufacturer="LONGi",
        rated_power_w=440,
        front_static_load_pa=5400,
        back_static_load_pa=2400,
        hail_resistance_mm=25,
        hail_impact_velocity_m_s=23,
        market_version="中国",
        model_derivation_method="官网逐项列示",
        source_document_type="官网产品页",
        source_note="安装条件需另核",
        source_url="https://www.longi.com/cn/products/modules/example/",
        source_name="厂家参数目录，第 208 行",
        retrieved_at="2026-10-01T00:00:00+08:00",
        match_confidence=0.9,
        parameter_sources={
            "front_static_load_pa": "https://www.longi.com/cn/products/modules/example/",
            "back_static_load_pa": "https://www.longi.com/cn/products/modules/example/",
        },
        lookup_notes=["正反面最大静载是组件试验参数，不是项目设计风压"],
    )
    weather = WeatherProfile(
        longitude=113.25,
        latitude=23.12,
        historical_max_wind_m_s=30,
        historical_max_daily_snowfall_cm=12.5,
        observation_start="2025-01-01",
        observation_end="2025-01-31",
        returned_data_start="2025-01-01",
        returned_data_end="2025-01-31",
        expected_day_count=31,
        returned_day_count=31,
        wind_valid_day_count=30,
        snowfall_valid_day_count=31,
        grid_longitude=113.3,
        grid_latitude=23.1,
        grid_distance_km=6.1,
        grid_elevation_m=18,
        timezone="UTC",
        dataset_selection="best_match",
        grid_selection_method="land",
        wind_unit="m/s",
        snowfall_unit="cm",
        data_quality_notes=["阵风序列缺少 1 天有效值"],
        source_name="Open-Meteo Historical Weather API",
        source_url="https://open-meteo.com/en/docs/historical-weather-api",
        retrieved_at="2026-10-01T00:00:00+00:00",
    )
    assessment = CatastropheAssessmentEngine().assess(component=component, weather=weather)
    report = generate_markdown_report(
        case.model_copy(
            update={
                "component_profile": component,
                "weather_profile": weather,
                "catastrophe_assessment": assessment,
            }
        )
    )

    assert "正面最大静态载荷：5400 Pa" in report
    assert "背面最大静态载荷：2400 Pa" in report
    assert "冰雹试验：25 mm" not in report
    assert "参数逐项来源" in report
    assert "不折算为项目设计风压或屋面结构雪荷载" in report
    assert "阵风有效日数：30/31" in report
    assert "网格距项目点：6.1 km" in report
    assert "阵风序列缺少 1 天有效值" in report
    assert "组件工作台识别结果" in report


def test_report_explains_amap_coordinates_and_no_geocode_confidence_score() -> None:
    now = datetime(2026, 10, 7, tzinfo=UTC)
    review = MapImageryReview(
        review_id="map-review-amap-01",
        provider="amap_maps",
        address_source="material_ocr",
        triggering_material_ids=["material-panorama-001"],
        query_address="广东省示例市示例区",
        matched_address="广东省示例市示例区",
        match_level="区县",
        longitude_gcj02=113.255,
        latitude_gcj02=23.126,
        longitude_wgs84=113.25,
        latitude_wgs84=23.12,
        provider_map_url="https://uri.amap.com/marker?position=113.255%2C23.126",
        queried_at=now,
        reviewed_at=now,
        reviewer_name="核保员甲",
        site_match_confirmed=True,
        photovoltaic_visibility="visible",
        observed_installation_type="color_steel_roof",
        observations="可见屋面有光伏组件，但影像日期未知。",
        sentinel_context=SentinelContextRecord(
            scene_id="S2A-test-scene",
            acquired_at=now,
            tile_cloud_cover_percent=10.0,
            tile_cloud_cover_note="Tile-level cloud estimate, not parcel cloud cover.",
            masked_pixel_fraction=0.08,
            center_longitude=113.25,
            center_latitude=23.12,
            bbox_wgs84=(113.23, 23.10, 113.27, 23.14),
            radius_m=1000,
            pixel_size_m=10,
            image_width=200,
            image_height=200,
            attribution="Contains modified Copernicus Sentinel data 2026",
            analysis=SentinelEnvironmentAnalysis(
                summary="Possible tree cover appears around the selected point.",
                observations=[
                    SentinelEnvironmentObservation(
                        category="tree_cover",
                        presence="possible",
                        confidence=0.7,
                        evidence="Irregular dark-green patches surround built-up shapes.",
                    )
                ],
            ),
        ),
    )
    report = generate_markdown_report(
        make_case().model_copy(update={"map_reviews": [review]})
    )

    assert "高德接口未返回可信度评分" in report
    assert "地图坐标（GCJ-02）：113.255, 23.126" in report
    assert "天气查询坐标（WGS84 近似）：113.25, 23.12" in report
    assert "不会自动改写" in report or "未作为自动规则事实" in report



    assert "Sentinel-2 surroundings image" in report
    assert "S2A-test-scene" in report
    assert "Contains modified Copernicus Sentinel data 2026" in report
    assert "not an underwriting result" in report
def test_renders_catastrophe_assessment_trace_label() -> None:
    case = make_case()
    trace_payload = case.processing_trace[0].model_dump(mode="python")
    trace_payload["step"] = "catastrophe_assessment"
    trace = type(case.processing_trace[0]).model_validate(trace_payload)
    report = generate_markdown_report(
        case.model_copy(update={"processing_trace": [trace]})
    )

    assert "灾害风险评估" in report


def test_report_explains_installation_applicability_review_items() -> None:
    assessment = CatastropheAssessmentEngine().assess(component=None, weather=None)
    case = make_case().model_copy(update={"catastrophe_assessment": assessment})

    report = generate_markdown_report(case)

    assert "组件工作台识别结果" in report
    assert "查询状态：not_found" in report
    assert "CAT-INSTALL-SNOW-APPLICABILITY-PENDING" not in report


def test_report_hides_findings_outside_the_reviewed_material_scope() -> None:
    case = make_case()
    irrelevant = case.findings[0].model_copy(
        update={
            "finding_id": "finding-out-of-scope",
            "category": RiskCategory.FLAMMABLE_MATERIAL,
            "label": "out-of-scope combustible material",
            "detection_status": DetectionStatus.DETECTED,
            "evidence_text": "This finding is outside the material review scope.",
        }
    )

    report = generate_markdown_report(
        case.model_copy(update={"findings": [*case.findings, irrelevant]})
    )

    assert "outside the material review scope" not in report
    assert "0.72" in report


def test_handles_case_without_decision_or_results() -> None:
    case = make_case(include_decision=False).model_copy(
        update={
            "findings": [],
            "material_reviews": [],
            "processing_trace": [],
        }
    )

    report = generate_markdown_report(case)

    assert "建议结论：尚未生成" in report
    assert "等待规则判断或人工复核" in report
    assert report.count("| 无 |") >= 3
def test_report_supports_every_material_category_in_saved_cases() -> None:
    from app.contracts.enums import MaterialCategory
    from app.reports.generator import MATERIAL_CATEGORY_LABELS

    assert set(MaterialCategory) == set(MATERIAL_CATEGORY_LABELS)


def test_component_report_separates_successful_mechanical_lookup_from_missing_direct_fields() -> None:
    component = ComponentProfile(
        component_model="JAM72S20-450/MR", manufacturer="JA Solar",
        front_static_load_pa=5400, back_static_load_pa=2400,
        source_name="厂家规格书", source_url="https://example.com/spec.pdf",
        retrieved_at=datetime.now(UTC), match_confidence=1,
    )
    report = generate_markdown_report(make_case().model_copy(update={
        "component_profile": component,
    }))
    assert "正面最大静态载荷：5400 Pa" in report
    assert "背面最大静态载荷：2400 Pa" in report
    assert "单独列示的风荷载：本次未取得独立字段" in report
    assert "未提供 Pa" not in report
