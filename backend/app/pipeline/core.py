from collections.abc import Callable
from datetime import UTC, datetime
from time import perf_counter_ns

from app.catastrophe import CatastropheAssessmentEngine
from app.catastrophe.front_load_screening.unified import assess_unified
from app.contracts import (
    CatastropheAssessment,
    ComponentProfile,
    EquipmentInventoryItem,
    InstallationType,
    Material,
    MaterialCategory,
    MaterialReview,
    OcrField,
    OcrValueStatus,
    ProcessingStatus,
    ProcessingStep,
    ProcessingTrace,
    ProjectInfo,
    ProjectType,
    RiskFinding,
    UnderwritingCase,
    UnderwritingDecision,
    WatermarkStatus,
    WeatherProfile,
)
from app.decision import DecisionEngine
from app.intake.equipment_inventory import (
    EquipmentInventoryParseError,
    parse_equipment_inventory,
)
from app.location import AmapGeocoder, assess_location
from app.location.coordinates import parse_decimal_coordinate
from app.providers import MaterialInput, OcrProvider, ProviderError, VisionProvider
from app.providers.component import ComponentProvider
from app.providers.ocr.tasks import extract_tasks
from app.providers.routing import SCENE_PHOTO_CATEGORIES
from app.providers.weather import WeatherProvider
from app.rules import RuleEngine


def processing_status(total: int, failures: int) -> ProcessingStatus:
    """根据 Provider 调用失败数量生成处理状态。"""

    if failures == 0:
        return ProcessingStatus.SUCCESS
    if total > 0 and failures == total:
        return ProcessingStatus.FAILED
    return ProcessingStatus.PARTIAL


class UnderwritingPipeline:
    """协调 Provider、灾害评估、规则引擎和决策引擎。"""

    def __init__(
        self,
        ocr_provider: OcrProvider,
        vision_provider: VisionProvider,
        component_provider: ComponentProvider | None = None,
        weather_provider: WeatherProvider | None = None,
        catastrophe_engine: CatastropheAssessmentEngine | None = None,
        rule_engine: RuleEngine | None = None,
        decision_engine: DecisionEngine | None = None,
        location_geocoder: AmapGeocoder | None = None,
        progress_callback: Callable[[str, int], None] | None = None,
    ) -> None:
        self.ocr_provider = ocr_provider
        self.vision_provider = vision_provider
        self.component_provider = component_provider
        self.weather_provider = weather_provider
        self.catastrophe_engine = catastrophe_engine
        self.rule_engine = RuleEngine() if rule_engine is None else rule_engine
        self.decision_engine = DecisionEngine() if decision_engine is None else decision_engine
        self.location_geocoder = location_geocoder
        self.progress_callback = progress_callback

    def run(
        self,
        *,
        case_id: str,
        project: ProjectInfo,
        materials: list[MaterialInput],
        pv_load_screening_input: dict | None = None,
        rule_snapshot: dict | None = None,
    ) -> UnderwritingCase:
        self.pv_load_screening_input = pv_load_screening_input or {}
        self.policy = None
        if rule_snapshot:
            from app.rules.selection import Policy
            from app.rules.config import BusinessRulesConfig
            self.policy = Policy(rule_snapshot)
            self.rule_engine.config = BusinessRulesConfig.model_validate(rule_snapshot["business_config"])
        for provider in (self.ocr_provider, self.vision_provider, self.rule_engine):
            provider.rule_policy = self.policy
        from .staged import run_staged

        return run_staged(self, case_id=case_id, project=project, materials=materials)

    def _run_legacy(
        self,
        *,
        case_id: str,
        project: ProjectInfo,
        materials: list[MaterialInput],
    ) -> UnderwritingCase:
        self._notify_progress("正在识别图片风险与水印", 5)
        findings, vision_trace = self._run_vision(materials)
        self._notify_progress("图片分析完成", 35)
        self._notify_progress("正在分别提取拍摄水印与业务文字", 38)
        ocr_fields, ocr_trace = self._run_ocr(materials)
        self._notify_progress("文字提取完成", 43)
        materials = self._apply_ocr_metadata(materials, ocr_fields)
        project = self._apply_ocr_project_fields(project, ocr_fields)
        self._notify_progress("正在解析设备清单", 44)
        equipment_inventory, equipment_trace = self._run_equipment_inventory(materials)
        project = self._apply_inventory_project_fields(project, equipment_inventory)
        self._notify_progress("设备清单解析完成", 46)
        material_models = [item.material for item in materials]
        project = self._apply_vision_project_fields(project, findings)
        location_assessment, weather_coordinates = assess_location(
            project, material_models, ocr_fields, geocoder=self.location_geocoder
        )
        if weather_coordinates is not None:
            project = project.model_copy(update={
                "longitude": weather_coordinates[0],
                "latitude": weather_coordinates[1],
            })

        component_profile: ComponentProfile | None = None
        weather_profile: WeatherProfile | None = None
        catastrophe_assessment: CatastropheAssessment | None = None
        processing_traces = [vision_trace, ocr_trace]
        if equipment_trace is not None:
            processing_traces.append(equipment_trace)

        if self.component_provider is not None:
            self._notify_progress("正在查询组件参数", 50)
            component_profile, component_trace = self._run_component_lookup(project, materials)
            processing_traces.append(component_trace)
            self._notify_progress("组件参数查询完成", 60)

        if (
            self.weather_provider is not None
            and project.longitude is not None
            and project.latitude is not None
            and location_assessment.status != "conflict"
        ):
            self._notify_progress("正在查询历史气象数据", 65)
            weather_profile, weather_trace = self._run_weather_lookup(project)
            processing_traces.append(weather_trace)
            self._notify_progress("气象数据查询完成", 75)
        elif self.weather_provider is not None:
            now = datetime.now(UTC)
            if location_assessment.status == "conflict":
                reason = "位置证据存在冲突，未选定气象查询坐标。请先核对电站位置。"
            else:
                details = [f"{item.file_name or item.source}：{item.geocode_reason}"
                           for item in location_assessment.evidence if item.geocode_reason]
                reason = "缺少可用的电站经纬度，历史气象查询未发起。" + (
                    " ".join(details) if details else "当前材料未取得可用坐标或可解析的具体地址。")
            processing_traces.append(ProcessingTrace(
                step=ProcessingStep.WEATHER_LOOKUP, provider=self.weather_provider.name,
                model=None, started_at=now, finished_at=now, latency_ms=0,
                status=ProcessingStatus.SKIPPED, detail=reason,
            ))
            self._notify_progress("历史气象查询已跳过：" + reason, 75)

        if self.catastrophe_engine is not None:
            self._notify_progress("正在进行自然灾害风险量化", 80)
            catastrophe_assessment, catastrophe_trace = self._run_catastrophe_assessment(
                component_profile,
                weather_profile,
            )
            processing_traces.append(catastrophe_trace)
            self._notify_progress("自然灾害评估完成", 84)

        package_assessment = self.rule_engine.assess_package(
            material_models,
            project_type=project.project_type,
        )
        self._notify_progress("正在匹配核保规则", 86)
        material_reviews, rule_trace = self._run_rules(
            material_models,
            findings=findings,
            ocr_fields=ocr_fields,
            project=project,
        )
        processing_traces.append(rule_trace)
        self._notify_progress("核保规则匹配完成", 92)

        case_without_decision = UnderwritingCase(
            schema_version="0.1.0",
            case_id=case_id,
            project=project,
            materials=material_models,
            equipment_inventory=equipment_inventory,
            ocr_fields=ocr_fields,
            findings=findings,
            component_profile=component_profile,
            weather_profile=weather_profile,
            catastrophe_assessment=catastrophe_assessment,
            location_assessment=location_assessment,
            package_assessment=package_assessment,
            material_reviews=material_reviews,
            decision=None,
            processing_trace=processing_traces,
        )

        self._notify_progress("正在汇总综合核保意见", 95)
        decision, decision_trace = self._run_decision(case_without_decision)
        final_payload = case_without_decision.model_dump(mode="python")
        final_payload["decision"] = decision
        final_payload["processing_trace"] = [
            *case_without_decision.processing_trace,
            decision_trace,
        ]

        case = UnderwritingCase.model_validate(final_payload)
        self._notify_progress("核保分析完成", 98)
        return case

    @staticmethod
    def _run_front_load_screening(
        *,
        case_id: str,
        project: ProjectInfo,
        component_profile: ComponentProfile | None,
        screening_input: dict,
        location_conflict: bool = False,
    ) -> tuple[dict, ProcessingTrace]:
        started_at = datetime.now(UTC)
        started_ns = perf_counter_ns()
        assessment = assess_unified(
            case_id=case_id, model=project.component_model,
            address=project.site_address or project.insured_address,
            screening_input=screening_input, component=component_profile,
            province=project.province, city=project.city, district=project.district,
            location_conflict=location_conflict,
        )
        status = assessment.get("status")
        output_status = (
            "missing"
            if status == "INSUFFICIENT_DATA"
            else "partial"
            if status == "PARTIAL"
            else "available"
        )
        return assessment, ProcessingTrace(
            step=ProcessingStep.FRONT_LOAD_SCREENING,
            provider="builtin-pv-front-load-screening",
            model=assessment.get("model_version"),
            started_at=started_at,
            finished_at=datetime.now(UTC),
            latency_ms=(perf_counter_ns() - started_ns) // 1_000_000,
            status=ProcessingStatus.SUCCESS,
            output_status=output_status,
        )

    def _notify_progress(self, step: str, progress_percent: int) -> None:
        if self.progress_callback is not None:
            self.progress_callback(step, progress_percent)

    def _run_ocr(
        self,
        materials: list[MaterialInput],
    ) -> tuple[list[OcrField], ProcessingTrace]:
        started_at = datetime.now(UTC)
        started_ns = perf_counter_ns()
        fields: list[OcrField] = []
        failures = 0

        executions=[]
        for material_input in materials:
            output, details = extract_tasks(self.ocr_provider, material_input)
            fields.extend(output);executions.extend(details)
        attempted=[d for d in executions if d['status']!='skipped']
        failures=sum(d['status']=='failed' for d in attempted)

        finished_at = datetime.now(UTC)
        status = processing_status(len(attempted), failures)

        return fields, ProcessingTrace(
            step=ProcessingStep.OCR,
            provider=self.ocr_provider.name,
            model=self.ocr_provider.model_name_for_materials(materials),
            started_at=started_at,
            finished_at=finished_at,
            task_executions=executions,
            failed_material_ids=list(dict.fromkeys(
                detail["material_id"] for detail in executions
                if detail["status"] == "failed"
            )),
            latency_ms=(perf_counter_ns() - started_ns) // 1_000_000,
            status=status,
            error_code=None if failures == 0 else "OCR_PROVIDER_FAILURE",
            error_message=(
                None
                if failures == 0
                else f"{failures} of {len(attempted)} extraction tasks failed"
            ),
        )

    def _run_vision(
        self,
        materials: list[MaterialInput],
    ) -> tuple[list[RiskFinding], ProcessingTrace]:
        started_at = datetime.now(UTC)
        started_ns = perf_counter_ns()
        findings: list[RiskFinding] = []
        failures = 0
        failed_material_ids: list[str] = []

        for material_input in materials:
            try:
                analysis = self.vision_provider.analyze_with_watermark(material_input)
                findings.extend(analysis.findings)
                if material_input.material.category in SCENE_PHOTO_CATEGORIES:
                    if analysis.watermark_present is True:
                        material_input.material.watermark_status = WatermarkStatus.PRESENT
                    elif analysis.watermark_present is False:
                        material_input.material.watermark_status = WatermarkStatus.ABSENT
                    elif material_input.material.watermark_status in {WatermarkStatus.NOT_CHECKED, WatermarkStatus.UNCERTAIN}:
                        material_input.material.watermark_status = WatermarkStatus.UNCERTAIN
            except ProviderError:
                if material_input.material.category in SCENE_PHOTO_CATEGORIES:
                    material_input.material.watermark_status = WatermarkStatus.UNCERTAIN
                failures += 1
                failed_material_ids.append(material_input.material.material_id)

        finished_at = datetime.now(UTC)
        status = processing_status(len(materials), failures)

        return findings, ProcessingTrace(
            step=ProcessingStep.VISION,
            provider=self.vision_provider.name,
            model=self.vision_provider.model_name,
            started_at=started_at,
            finished_at=finished_at,
            latency_ms=(perf_counter_ns() - started_ns) // 1_000_000,
            status=status,
            failed_material_ids=failed_material_ids,
            error_code=None if failures == 0 else "VISION_PROVIDER_FAILURE",
            error_message=(
                None
                if failures == 0
                else f"{failures} of {len(materials)} material calls failed"
            ),
        )

    def _run_component_lookup(
        self,
        project: ProjectInfo,
        materials: list[MaterialInput] | None = None,
    ) -> tuple[ComponentProfile | None, ProcessingTrace]:
        if self.component_provider is None:
            raise RuntimeError("component provider is not configured")

        started_at = datetime.now(UTC)
        started_ns = perf_counter_ns()
        component_profile: ComponentProfile | None = None
        status_value = ProcessingStatus.SUCCESS
        error_code: str | None = None
        error_message: str | None = None

        try:
            if materials is not None and hasattr(self.component_provider, "lookup_materials"):
                component_profile = self.component_provider.lookup_materials(project, materials)
                component_model = None
            else:
                component_model = project.component_model
            if component_model is not None and component_model.strip():
                component_profile = self.component_provider.lookup(component_model)
        except ProviderError:
            status_value = ProcessingStatus.FAILED
            error_code = "COMPONENT_PROVIDER_FAILURE"
            error_message = "component provider lookup failed"

        return component_profile, ProcessingTrace(
            step=ProcessingStep.COMPONENT_LOOKUP,
            provider=self.component_provider.name,
            model=None,
            started_at=started_at,
            finished_at=datetime.now(UTC),
            latency_ms=(perf_counter_ns() - started_ns) // 1_000_000,
            status=status_value,
            error_code=error_code,
            error_message=error_message,
        )

    def _run_weather_lookup(
        self,
        project: ProjectInfo,
    ) -> tuple[WeatherProfile | None, ProcessingTrace]:
        if self.weather_provider is None:
            raise RuntimeError("weather provider is not configured")
        if project.longitude is None or project.latitude is None:
            raise RuntimeError("project coordinates are not configured")

        started_at = datetime.now(UTC)
        started_ns = perf_counter_ns()
        weather_profile: WeatherProfile | None = None
        status_value = ProcessingStatus.SUCCESS
        error_code: str | None = None
        error_message: str | None = None

        try:
            weather_profile = self.weather_provider.lookup(
                longitude=project.longitude,
                latitude=project.latitude,
            )
        except ProviderError:
            status_value = ProcessingStatus.FAILED
            error_code = "WEATHER_PROVIDER_FAILURE"
            error_message = "weather provider lookup failed"

        return weather_profile, ProcessingTrace(
            step=ProcessingStep.WEATHER_LOOKUP,
            provider=self.weather_provider.name,
            model=None,
            started_at=started_at,
            finished_at=datetime.now(UTC),
            latency_ms=(perf_counter_ns() - started_ns) // 1_000_000,
            status=status_value,
            error_code=error_code,
            error_message=error_message,
        )

    def _run_catastrophe_assessment(
        self,
        component_profile: ComponentProfile | None,
        weather_profile: WeatherProfile | None,
    ) -> tuple[CatastropheAssessment, ProcessingTrace]:
        if self.catastrophe_engine is None:
            raise RuntimeError("catastrophe assessment engine is not configured")

        started_at = datetime.now(UTC)
        started_ns = perf_counter_ns()
        assessment = self.catastrophe_engine.assess(
            component=component_profile,
            weather=weather_profile,
            module_result=getattr(self.component_provider, "last_result", None),
        )

        return assessment, ProcessingTrace(
            step=ProcessingStep.CATASTROPHE_ASSESSMENT,
            provider="component-workbench-assessment",
            model=None,
            started_at=started_at,
            finished_at=datetime.now(UTC),
            latency_ms=(perf_counter_ns() - started_ns) // 1_000_000,
            status=ProcessingStatus.SUCCESS,
            error_code=None,
            error_message=None,
        )

    @staticmethod
    def _run_equipment_inventory(
        materials: list[MaterialInput],
    ) -> tuple[list[EquipmentInventoryItem], ProcessingTrace | None]:
        inventory_materials = [
            item
            for item in materials
            if item.material.category is MaterialCategory.EQUIPMENT_INVENTORY
            and item.material.media_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ]
        if not inventory_materials:
            return [], None

        started_at = datetime.now(UTC)
        started_ns = perf_counter_ns()
        items: list[EquipmentInventoryItem] = []
        failures = 0
        for material_input in inventory_materials:
            try:
                items.extend(
                    parse_equipment_inventory(
                        material_id=material_input.material.material_id,
                        content=material_input.content,
                    )
                )
            except EquipmentInventoryParseError:
                failures += 1

        finished_at = datetime.now(UTC)
        status = processing_status(len(inventory_materials), failures)
        trace = ProcessingTrace(
            step=ProcessingStep.EQUIPMENT_INVENTORY,
            provider="openxml-equipment-inventory-parser",
            model="openxml-parser-v1",
            started_at=started_at,
            finished_at=finished_at,
            latency_ms=(perf_counter_ns() - started_ns) // 1_000_000,
            status=status,
            error_code="equipment_inventory_parse_failed" if failures else None,
            error_message=(
                "One or more equipment inventory workbooks could not be parsed"
                if failures
                else None
            ),
        )
        return items, trace

    @staticmethod
    def _apply_inventory_project_fields(
        project: ProjectInfo,
        inventory: list[EquipmentInventoryItem],
    ) -> ProjectInfo:
        """Use a single unambiguous module model from the equipment list as a fallback."""
        if project.component_model:
            return project
        models = {
            item.normalized_component_model
            for item in inventory
            if item.normalized_component_model
        }
        if len(models) != 1:
            return project
        return project.model_copy(update={"component_model": next(iter(models))})

    def _run_rules(
        self,
        materials: list[Material],
        *,
        findings: list[RiskFinding],
        ocr_fields: list[OcrField],
        project: ProjectInfo,
        failed_material_ids: set[str] | None = None,
    ) -> tuple[list[MaterialReview], ProcessingTrace]:
        started_at = datetime.now(UTC)
        started_ns = perf_counter_ns()
        reviews = self.rule_engine.evaluate_materials(
            materials,
            findings=findings,
            ocr_fields=ocr_fields,
            project=project,
            failed_material_ids=failed_material_ids,
        )

        return reviews, ProcessingTrace(
            step=ProcessingStep.RULE_ENGINE,
            provider="builtin-rule-engine",
            model=None,
            started_at=started_at,
            finished_at=datetime.now(UTC),
            latency_ms=(perf_counter_ns() - started_ns) // 1_000_000,
            status=ProcessingStatus.SUCCESS,
            error_code=None,
            error_message=None,
        )

    @staticmethod
    def _apply_ocr_metadata(
        materials: list[MaterialInput],
        ocr_fields: list[OcrField],
    ) -> list[MaterialInput]:
        """把可信水印字段写回材料证据，供日期规则和报告使用。"""
        fields_by_material: dict[str, dict[str, OcrField]] = {}
        for item in ocr_fields:
            fields_by_material.setdefault(item.material_id, {})[item.field_name] = item

        result: list[MaterialInput] = []
        for material_input in materials:
            material = material_input.material
            fields = fields_by_material.get(material.material_id, {})
            updates: dict[str, object] = {}

            subtype = fields.get("document_type")
            if material.category is MaterialCategory.PROJECT_DOCUMENT and subtype is not None and subtype.value_status is OcrValueStatus.EXTRACTED and subtype.confidence >= 0.65:
                value = subtype.normalized_value or subtype.raw_value
                if value == "filing_certificate":
                    updates["category"] = MaterialCategory.FILING_CERTIFICATE
                elif value in {"grid_connection_permit", "dispatch_agreement"}:
                    updates["category"] = MaterialCategory.GRID_CONNECTION_DOCUMENT

            watermark = fields.get("watermark_present")
            if watermark is not None and material.watermark_status in {WatermarkStatus.NOT_CHECKED, WatermarkStatus.UNCERTAIN}:
                value = (watermark.normalized_value or watermark.raw_value or "").strip().casefold()
                if (
                    watermark.value_status is OcrValueStatus.UNCERTAIN
                    or watermark.confidence < 0.65
                ):
                    updates["watermark_status"] = WatermarkStatus.UNCERTAIN
                elif value in {"true", "yes", "present", "有", "是"}:
                    updates["watermark_status"] = WatermarkStatus.PRESENT
                elif value in {"false", "no", "absent", "无", "否"}:
                    updates["watermark_status"] = WatermarkStatus.ABSENT
                else:
                    updates["watermark_status"] = WatermarkStatus.UNCERTAIN

            date_field = fields.get("watermark_date") or fields.get("shot_date")
            if date_field is not None:
                value = date_field.normalized_value or date_field.raw_value
                if (
                    value
                    and date_field.value_status is OcrValueStatus.EXTRACTED
                    and date_field.confidence >= 0.65
                ):
                    try:
                        from datetime import date, datetime, time

                        parsed_date = date.fromisoformat(value[:10])
                        updates["captured_at"] = datetime.combine(parsed_date, time.min)
                    except ValueError:
                        pass

            for field_name, target in (
                ("watermark_longitude", "longitude"),
                ("watermark_latitude", "latitude"),
            ):
                field = fields.get(field_name)
                value = (
                    (field.normalized_value or field.raw_value)
                    if field is not None
                    and field.value_status is OcrValueStatus.EXTRACTED
                    and field.confidence >= 0.65
                    else None
                )
                if value:
                    coordinate = parse_decimal_coordinate(value, axis=target)
                    if coordinate is not None:
                        updates[target] = coordinate

            result.append(
                MaterialInput(
                    material=material.model_copy(update=updates),
                    content=material_input.content,
                )
            )
        return result

    @staticmethod
    def _apply_filing_site_address(project, materials, ocr_fields):
        """Fill the project site from a unique extracted filing-form address.

        Do this after independent location checks, so copied OCR evidence is not
        counted as a second source. Never substitute the insured's office address.
        """
        if project.site_address and project.site_address.strip():
            return project
        filing_ids = {item.material_id for item in materials
                      if item.category is MaterialCategory.FILING_CERTIFICATE}
        addresses = {}
        for field in ocr_fields:
            if (field.material_id not in filing_ids or field.field_name != "site_address"
                    or field.value_status is not OcrValueStatus.EXTRACTED
                    or field.confidence < 0.65):
                continue
            value = (field.normalized_value or field.raw_value or "").strip()
            if value and len(value) <= 500:
                addresses.setdefault("".join(value.split()), value)
        if len(addresses) != 1:
            return project
        return project.model_copy(update={"site_address": next(iter(addresses.values()))})

    @staticmethod
    def _apply_ocr_project_fields(
        project: ProjectInfo,
        ocr_fields: list[OcrField],
    ) -> ProjectInfo:
        """用高置信度铭牌 OCR 补齐未录入的组件型号。"""
        if project.component_model:
            return project
        candidates = [
            item
            for item in ocr_fields
            if item.field_name == "component_model"
            and item.value_status.value == "extracted"
            and item.confidence >= 0.65
            and (item.normalized_value or item.raw_value)
        ]
        if not candidates:
            return project
        selected = max(candidates, key=lambda item: item.confidence)
        return project.model_copy(
            update={"component_model": selected.normalized_value or selected.raw_value}
        )

    @staticmethod
    def _apply_vision_project_fields(
        project: ProjectInfo,
        findings: list[RiskFinding],
    ) -> ProjectInfo:
        """仅在高置信度且结果唯一时，用照片识别补齐安装类型。"""
        if project.installation_type is not InstallationType.UNKNOWN:
            return project

        candidates: set[InstallationType] = set()
        for finding in findings:
            if (
                finding.category.value != "installation_type"
                or finding.detection_status.value != "detected"
                or finding.confidence < 0.75
            ):
                continue
            label = f"{finding.label} {finding.evidence_text}".casefold()
            if any(term in label for term in ("彩钢", "彩钢瓦", "钢瓦", "metal sheet")):
                candidates.add(InstallationType.COLOR_STEEL_ROOF)
            elif any(term in label for term in ("平屋顶", "平屋面", "flat roof")):
                candidates.add(InstallationType.FLAT_ROOF)
            elif any(term in label for term in ("瓦屋顶", "瓦片屋顶", "tile roof")):
                candidates.add(InstallationType.TILE_ROOF)
            elif any(term in label for term in ("车棚", "车棚顶", "carport")):
                candidates.add(InstallationType.CARPORT_ROOF)

        if len(candidates) != 1:
            return project

        installation_type = next(iter(candidates))
        updates: dict[str, object] = {"installation_type": installation_type}
        if (
            project.project_type is ProjectType.UNKNOWN
            and installation_type is InstallationType.CARPORT_ROOF
        ):
            updates["project_type"] = ProjectType.CARPORT
        elif project.project_type is ProjectType.UNKNOWN:
            updates["project_type"] = ProjectType.ROOFTOP
        return project.model_copy(update=updates)

    def _run_decision(
        self,
        case: UnderwritingCase,
    ) -> tuple[UnderwritingDecision, ProcessingTrace]:
        started_at = datetime.now(UTC)
        started_ns = perf_counter_ns()
        decision = self.decision_engine.decide(case)

        return decision, ProcessingTrace(
            step=ProcessingStep.DECISION,
            provider="conservative-decision-engine",
            model=None,
            started_at=started_at,
            finished_at=datetime.now(UTC),
            latency_ms=(perf_counter_ns() - started_ns) // 1_000_000,
            status=ProcessingStatus.SUCCESS,
            error_code=None,
            error_message=None,
        )
