"""Versioned rule selection. Snapshots are resolved by the server at submission."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contracts import RiskCategory
from .config import BusinessRulesConfig
from .engine import EXPECTED_CHECKS_BY_MATERIAL, ADVANCED_CHECKS_BY_MATERIAL, RISK_CATEGORY_LABELS

ROOT = Path(__file__).resolve().parents[3]
CATALOG_VERSION = "v1.1"


def rule(key, name, purpose, materials, risks=(), dependencies=(), checks=()):
    return dict(id=key, name=name, purpose=purpose, materials=list(materials),
                risks=list(risks), dependencies=list(dependencies), checks=list(checks),
                version=CATALOG_VERSION)


CATALOG = [
    rule("filing", "备案禁投与主体核对", "核验备案建设内容、项目名称及投保主体，保留原文证据。", ["filing_certificate", "project_document", "grid_connection_document"], checks=["filing", "grid"]),
    rule("installation", "安装类型准入", "确认组件承载结构；当前规则支持建筑屋顶或车棚顶。", ["panorama", "roof_connection"], ["installation_type"], checks=["panorama"]),
    rule("environment", "现场禁投环境", "分别核验农、林、牧、渔、涉水、滩涂、山地、沙化环境与项目场地关系；背景景物不能直接拒保。", ["panorama", "roof_connection"], [c.value for c in RiskCategory if c.value.endswith("_environment")], checks=["panorama"]),
    rule("shading", "组件遮挡", "识别遮挡位置和程度；严重遮挡与轻微遮挡分别判断。", ["panorama"], ["severe_shading", "minor_shading"], checks=["panorama"]),
    rule("connection", "连接与锚固", "识别屋面或车棚连接节点可见异常，不推断结构承载力。", ["roof_connection"], ["roof_connection_abnormal"], ["installation"], ["support"]),
    rule("edge", "边缘防护与排水", "按安装场景判断女儿墙、护栏及排水可见异常。", ["parapet"], ["missing_parapet_or_guardrail", "drainage_abnormal"], ["installation"], ["edge"]),
    rule("workshop", "车间火灾风险", "识别易燃物、危险品及已启用的工艺、消防扩展项；未拍到不等于不存在。", ["workshop"], ["flammable_material", "hazardous_material", "dangerous_process", "cleanroom", "fire_protection_absent"], ["installation"], ["workshop"]),
    rule("grounding", "电气接地与线缆", "核验接地外观、检测记录以及已启用的裸露线缆检查。", ["electrical_grounding"], ["electrical_grounding_abnormal", "unprotected_cable"], checks=["grounding"]),
    rule("combiner", "汇流箱与防雷器", "核验箱体密封、熔断器、防雷器及铭牌参数。", ["combiner_box"], ["combiner_box_seal_abnormal", "combiner_box_fuse_abnormal", "surge_protector_abnormal"], checks=["combiner"]),
    rule("surface", "组件表面损伤", "核验组件破损与已启用的变色检查。", ["component_surface"], ["module_damage", "module_discoloration"]),
    rule("inverter", "逆变器外观与铭牌", "核验逆变器可见异常和型号、功率。", ["inverter_nameplate"], ["inverter_abnormal"], checks=["inverter"]),
    rule("aisle", "检修通道阻塞", "结合组图核验真实可见阻塞；无通道视角应保留证据不足状态。", ["panorama", "roof_connection", "parapet"], ["aisle_obstruction"], ["installation"]),
    rule("watermark", "照片水印与时效", "核验拍摄日期、坐标和相对拟起保日的时效；缺失时给出提示。", ["panorama", "roof_connection"]),
    rule("location", "备案与现场位置一致性", "交叉核验备案地址和现场拍摄位置；冲突转人工。", ["filing_certificate", "project_document", "panorama"]),
    rule("component", "组件型号与承载参数", "查询组件型号及参数来源；无可信来源时不能宣称能力满足。", ["component_nameplate", "equipment_inventory"], checks=["component"]),
    rule("wind_snow", "风雪承载风险", "结合确认位置、安装几何和组件参数进行城市风雪风险筛查。", ["panorama", "component_nameplate", "equipment_inventory"], dependencies=["installation", "location", "component"]),
]
from .evidence_coverage import VERSION as COVERAGE_VERSION, requirements_for, guidance_for, prompt_for
for spec in CATALOG:
    spec['evidence_targets'] = requirements_for(spec)
    spec['evidence_guidance'] = guidance_for(spec)
BY_ID = {r["id"]: r for r in CATALOG}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Scope(Strict):
    regions: list[str] = Field(default_factory=list, max_length=50)
    insured_types: list[str] = Field(default_factory=list, max_length=20)
    min_capacity_kw: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    max_capacity_kw: float | None = Field(default=None, ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_range(self):
        if self.min_capacity_kw is not None and self.max_capacity_kw is not None and self.min_capacity_kw > self.max_capacity_kw:
            raise ValueError("装机规模下限不能大于上限")
        self.regions = list(dict.fromkeys(x.strip() for x in self.regions if x.strip()))
        self.insured_types = list(dict.fromkeys(x.strip() for x in self.insured_types if x.strip()))
        return self


class Parameters(Strict):
    environment_min_confidence: float = Field(default=.65, ge=.5, le=1, allow_inf_nan=False)
    watermark_days: int = Field(default=15, ge=1, le=90)
    material_profile: str = Field(default="demo_minimum", pattern="^(demo_minimum|full_intake)$")


class Selection(Strict):
    scheme_id: str | None = Field(default=None, max_length=80)
    scheme_version: int | None = Field(default=None, ge=1)
    selected_rule_ids: list[str] = Field(min_length=1, max_length=50)
    scopes: dict[str, Scope] = Field(default_factory=dict)
    parameters: Parameters = Field(default_factory=Parameters)

    @model_validator(mode="after")
    def validate_ids(self):
        unknown = (set(self.selected_rule_ids) | set(self.scopes)) - BY_ID.keys()
        if unknown:
            raise ValueError("未知核保规则：" + "、".join(sorted(unknown)))
        if (self.scheme_id is None) != (self.scheme_version is None):
            raise ValueError("方案编号与版本必须一起提供")
        self.selected_rule_ids = list(dict.fromkeys(self.selected_rule_ids))
        return self


class SaveScheme(Strict):
    name: str = Field(min_length=1, max_length=80)
    selection: Selection


def builtins():
    return [dict(id=key, version=1, name=name, builtin=True,
                 selection=Selection(selected_rule_ids=ids).model_dump())
            for key, name, ids in [
                ("complete", "比赛规则全量检查", list(BY_ID)),
                ("site", "现场风险检查", ["installation", "environment", "shading", "connection", "edge", "workshop", "grounding", "combiner", "surface", "aisle", "watermark"]),
                ("capacity", "组件与风雪专项", ["wind_snow"]),
            ]]


def database():
    path = Path(os.getenv("PV_RULE_SCHEMES_DB", str(ROOT / "outputs" / "rule_schemes.sqlite3")))
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.execute("CREATE TABLE IF NOT EXISTS schemes (id TEXT, version INTEGER, payload TEXT, PRIMARY KEY(id,version))")
    return conn


def list_schemes():
    with database() as conn:
        rows = conn.execute("SELECT payload FROM schemes ORDER BY rowid DESC").fetchall()
    return builtins() + [json.loads(row[0]) for row in rows]


def get_scheme(key, version):
    found = next((s for s in list_schemes() if s["id"] == key and s["version"] == version), None)
    if found is None:
        raise ValueError("规则方案或版本不存在，请重新选择")
    return found


def save_scheme(request):
    selection = request.selection
    base = get_scheme(selection.scheme_id, selection.scheme_version) if selection.scheme_id else None
    key = base["id"] if base and not base.get("builtin") else uuid4().hex
    with database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        latest = conn.execute("SELECT MAX(version) FROM schemes WHERE id=?", (key,)).fetchone()[0] or 0
        if latest and latest != selection.scheme_version:
            raise ValueError("方案已被更新，请重新加载后保存；旧版本保持不变")
        payload = dict(id=key, version=latest + 1, name=request.name.strip(), builtin=False,
                       selection=selection.model_dump(), created_at=datetime.now(UTC).isoformat())
        if not payload["name"]:
            raise ValueError("方案名称不能为空")
        payload["selection"].update(scheme_id=key, scheme_version=latest + 1)
        conn.execute("INSERT INTO schemes VALUES (?,?,?)", (key, latest + 1, json.dumps(payload, ensure_ascii=False)))
    return payload


def closure(ids):
    result = set(ids)
    while True:
        extended = result | {dep for key in result for dep in BY_ID[key]["dependencies"]}
        if extended == result:
            return result
        result = extended


def scope_status(scope, project):
    tests = []
    if scope.regions:
        # Explicit administrative fields, never guess region from a substring in an address.
        regions = [x for x in (project.province, project.city, project.district) if x]
        tests.append(None if not regions else bool(set(regions) & set(scope.regions)))
    if scope.insured_types:
        tests.append(None if not project.insured_type else project.insured_type in scope.insured_types)
    if scope.min_capacity_kw is not None or scope.max_capacity_kw is not None:
        cap = project.installed_capacity_kw
        tests.append(None if cap is None else
                     (scope.min_capacity_kw is None or cap >= scope.min_capacity_kw) and
                     (scope.max_capacity_kw is None or cap <= scope.max_capacity_kw))
    if False in tests:
        return "not_applicable", "项目资料不符合本规则设置的适用条件"
    if None in tests:
        return "applicability_pending", "缺少地区、装机规模或投保人类型，不能确定适用性"
    return "active", "符合适用条件"


def resolve(selection, project):
    selection = selection or Selection(selected_rule_ids=list(BY_ID))
    source = get_scheme(selection.scheme_id, selection.scheme_version) if selection.scheme_id else None
    expanded = closure(selection.selected_rule_ids)
    rows = []
    for spec in CATALOG:
        key = spec["id"]
        state, reason = scope_status(selection.scopes.get(key, Scope()), project) if key in expanded else ("not_selected", "本次未纳入核保范围")
        rows.append(dict(**spec, status=state, reason=reason, automatic=key in expanded and key not in selection.selected_rule_ids))
    states = {row["id"]: row for row in rows}
    for _ in CATALOG:
        for row in rows:
            if row["status"] == "active" and any(states[d]["status"] != "active" for d in row["dependencies"]):
                row.update(status="dependency_pending", reason="前置规则不适用或尚未确认，不能执行本项")
    config = BusinessRulesConfig.load().model_copy(update={
        "environment_auto_reject_min_confidence": selection.parameters.environment_min_confidence,
        "package_requirement_profile": selection.parameters.material_profile,
    })
    snapshot = dict(catalog_version=CATALOG_VERSION, created_at=datetime.now(UTC).isoformat(),
                    scheme_name=source["name"] if source else "本次自定义规则",
                    selection=selection.model_dump(), rules=rows,
                    business_config=config.model_dump(mode="json"),
                    parameters=selection.parameters.model_dump(),
                    context={k: getattr(project, k) for k in ["province", "city", "district", "installed_capacity_kw", "insured_type"]})
    source_selection = source["selection"] if source else None
    def configuration(value):
        data = {k: v for k, v in value.items() if k not in {"scheme_id", "scheme_version"}}
        data["scopes"] = {k: v for k, v in data.get("scopes", {}).items() if v != Scope().model_dump()}
        data["selected_rule_ids"] = sorted(data["selected_rule_ids"])
        return data
    snapshot["modified_from_scheme"] = bool(source_selection and configuration(selection.model_dump()) != configuration(source_selection))
    # Freeze the actual prompts used in this run, rather than only a mutable file path.
    from app.prompts.stages import stage_prompt
    from app.contracts import MaterialCategory
    policy = Policy(snapshot)
    snapshot['evidence_coverage_version'] = COVERAGE_VERSION
    snapshot['evidence_expected_risks'] = {c.value: sorted(r.value for r in policy.expected(c)) for c in MaterialCategory}
    snapshot["vision_prompts"] = {c.value: policy.make_vision_prompt(c) for c in MaterialCategory}
    quality_policy = Policy(dict(snapshot, rules=[
        dict(r, status=r["status"] if r["id"] in {"watermark", "location"} else "not_selected") for r in rows
    ]))
    snapshot["quality_only_prompts"] = {c.value: quality_policy.make_vision_prompt(c) for c in MaterialCategory}
    snapshot["stage_prompts"] = {key: stage_prompt(key) for key in ("installation", "installation_localization", "aisle_group", "geometry", "watermark_guidance", "filing_assessment")}
    from app.providers.ocr.compatible import SYSTEM_PROMPT, CATEGORY_FIELD_GUIDANCE, DEFAULT_FIELD_GUIDANCE
    from app.prompts.documents import material_prompt
    from app.material_catalog import DOCUMENT_CATEGORIES
    snapshot["ocr_prompts"] = {c.value: material_prompt(c, SYSTEM_PROMPT) if c in DOCUMENT_CATEGORIES else SYSTEM_PROMPT for c in MaterialCategory}
    snapshot["ocr_guidance"] = {c.value: CATEGORY_FIELD_GUIDANCE.get(c, DEFAULT_FIELD_GUIDANCE) for c in MaterialCategory}
    snapshot["shading_focus_prompt"] = (ROOT / "backend/app/prompts/vision/shading_focus.txt").read_text(encoding="utf-8")
    snapshot["snapshot_id"] = hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return snapshot


class Policy:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.active = {r["id"] for r in snapshot["rules"] if r["status"] == "active"}
        self.scene_exclusions = {}
        self.risks = {RiskCategory(c) for key in self.active for c in BY_ID[key]["risks"]} | {RiskCategory.IMAGE_QUALITY}

    def enabled(self, key):
        return key in self.active

    def expected(self, category):
        expected = EXPECTED_CHECKS_BY_MATERIAL.get(category, frozenset())
        if self.snapshot["business_config"]["advanced_checks_enabled"]:
            expected |= ADVANCED_CHECKS_BY_MATERIAL.get(category, frozenset())
        return expected & self.risks

    def business_ocr(self, category):
        key = category.value
        return any(key in BY_ID[r]["materials"] for r in self.active if r in {
            "filing", "location", "component", "inverter", "grounding", "combiner"
        })

    def make_vision_prompt(self, category):
        checks = set(self.expected(category))
        if category.value in {"panorama", "roof_connection"} and self.enabled("environment"):
            checks |= {RiskCategory(c) for c in BY_ID["environment"]["risks"]}
        checks.add(RiskCategory.IMAGE_QUALITY)
        labels = "\n".join(f"- {c.value}：{RISK_CATEGORY_LABELS.get(c, c.value)}" for c in sorted(checks, key=lambda c: c.value))
        base = (ROOT / "backend" / "app" / "prompts" / "vision" / "common.txt").read_text(encoding="utf-8")
        # Retain evidence safeguards, remove unconditional task instructions.
        base = base.split("【水印】")[0]
        base = base.replace("还可报告其他允许类别中直接可见的发现。", "不得执行或输出清单以外的业务检查。")
        base = base.replace("8类环境检查必须独立填写", "如清单含环境检查，其结果须填写")
        output = (ROOT / "backend" / "app" / "prompts" / "vision" / "output.txt").read_text(encoding="utf-8").split("当前材料为 panorama")[0]
        watermark = ("核验拍摄水印，watermark_present 返回 true、false 或 null。" if self.enabled("watermark") or self.enabled("location")
                     else "本次未启用水印核验，不检查水印，watermark_present 固定 null。")
        purposes = "\n".join(BY_ID[key]["purpose"] for key in sorted(self.active)
                             if set(BY_ID[key]["risks"]) & {c.value for c in checks})
        folder = ROOT / "backend/app/prompts/vision"
        specialized = []
        if category.value == "panorama":
            environment, shading = (folder / "panorama.txt").read_text(encoding="utf-8").split("【组件遮挡：", 1)
            if self.enabled("environment"):
                specialized.append(environment)
            if self.enabled("shading"):
                specialized.append("【组件遮挡：" + shading.replace("独立判断拍摄水印。", ""))
        elif category.value == "roof_connection":
            if self.enabled("connection"):
                specialized.append("检查可见连接件松脱、锈蚀、缺失。不能从外观证明紧固力或抗风能力合格。")
            if self.enabled("environment"):
                specialized.append("直接看到禁投环境时返回对应环境类别及项目关系；近景未展示周边不能输出环境 not_detected。")
        elif checks - {RiskCategory.IMAGE_QUALITY}:
            file = folder / (category.value + ".txt")
            if file.is_file():
                specialized.append(file.read_text(encoding="utf-8"))
        return base + "\n【本次唯一检查清单】\n" + labels + "\n" + purposes + "\n" + "\n".join(specialized) + "\n" + watermark + "\n仅执行上述清单内的类别，专项说明不扩大检查范围。\n" + output + prompt_for(checks, self.snapshot["rules"])

    def vision_prompt(self, category):
        if self.scene_exclusions and not (self.expected(category) - {RiskCategory.IMAGE_QUALITY}):
            return self.snapshot["quality_only_prompts"][category.value]
        return self.snapshot["vision_prompts"][category.value]

    def filter_checks(self, checks):
        for key in ("edge", "workshop"):
            related = [c for c in checks if c.key in BY_ID[key]["checks"]]
            if key in self.active and related and all(c.applicability == "not_applicable" for c in related):
                self.scene_exclusions[key] = "；".join(dict.fromkeys(c.reason for c in related))
                self.active.remove(key)
        self.risks = {RiskCategory(c) for key in self.active for c in BY_ID[key]["risks"]} | {RiskCategory.IMAGE_QUALITY}
        keys = {c for key in self.active for c in BY_ID[key]["checks"]}
        return [c for c in checks if c.key in keys]

    def executions(self, case):
        rows = []
        for item in self.snapshot["rules"]:
            row = {k: item[k] for k in ("id", "name", "version", "status", "reason", "automatic")}
            if row["status"] != "active":
                rows.append(row)
                continue
            key = row["id"]
            if key in self.scene_exclusions:
                row.update(status="not_applicable", reason=self.scene_exclusions[key])
                rows.append(row)
                continue
            relevant = [f for f in case.findings if f.category.value in item["risks"]]
            checks = [c for c in case.check_results if c.key in item["checks"]]
            row["finding_ids"] = [f.finding_id for f in relevant]
            row["material_ids"] = list(dict.fromkeys([f.material_id for f in relevant] + [m for c in checks for m in c.material_ids]))
            row.update(status="not_executed", reason="前置阶段中止或该规则尚未形成结果")
            if relevant:
                if any(f.detection_status.value == "detected" and f.category.value != "installation_type" for f in relevant):
                    row.update(status="risk_found", reason="识别到需关注的可见事实，处理动作以规则结论为准")
                elif any(f.detection_status.value == "uncertain" or f.requires_manual_review for f in relevant):
                    row.update(status="evidence_insufficient", reason="已执行，但可见证据尚不能支持确定判断")
                else:
                    row.update(status="completed", reason="已完成现有证据检查；仅对本次可见范围负责")
                expected = set(item["risks"]) & {c.value for material in case.materials for c in self.expected(material.category)}
                covered = {f.category.value for f in relevant}
                if expected - covered and row["status"] == "completed":
                    row.update(status="evidence_insufficient", reason="所选规则有检查项未返回结果，不能视为全部完成")
            elif not any(m.category.value in item["materials"] or set(c.value for c in m.additional_categories) & set(item["materials"]) for m in case.materials):
                row.update(status="material_missing", reason="未收到本项所需材料，无法完成检查")
            if checks and all(c.status == "not_applicable" for c in checks):
                row.update(status="not_applicable", reason="经现场场景识别确认不适用")
            elif checks and any(c.status in {"missing", "not_provided", "unusable"} for c in checks) and not relevant:
                row.update(status="material_missing", reason="缺少可用的检查材料；不能视为未发现风险")
            elif checks and any(c.status in {"pending", "unassessed"} for c in checks) and row["status"] != "risk_found":
                row.update(status="evidence_insufficient", reason="所选检查仍有未确定或未返回的证据")
            elif checks and all(c.status == "valid" for c in checks) and not item["risks"]:
                row.update(status="completed", reason="已取得并核验所需材料字段")
            if key in {"filing", "installation"}:
                stages = [s for s in case.stage_reviews if s.stage == ("filing" if key == "filing" else "installation_type")]
                if stages:
                    stage = stages[-1]
                    row.update(status={"continue": "completed", "recommend_reject": "risk_found", "manual_review": "evidence_insufficient", "request_more": "material_missing"}.get(stage.action, "evidence_insufficient"), reason="；".join(stage.reasons) or "本项阶段检查完成")
            if key == "location" and case.location_assessment:
                loc = case.location_assessment
                row.update(status="completed" if loc.status in {"verified", "distance_screened"} else "evidence_insufficient",
                           reason="位置核验：" + loc.status)
            if key == "component" and case.component_profile:
                component = case.component_profile
                has_load = any([component.front_static_load_pa, component.back_static_load_pa, component.wind_load_pa, component.snow_load_pa])
                row.update(status="completed" if has_load and component.parameter_evidence else "evidence_insufficient",
                           reason="已返回有来源的承载参数；适用安装条件见组件专项" if has_load and component.parameter_evidence else "已返回型号资料，但承载参数或来源证据不足")
            if key == "aisle" and case.installation_analysis and case.installation_analysis.aisle_assessment:
                a = case.installation_analysis.aisle_assessment
                row.update(status={"detected": "risk_found", "not_detected": "completed", "uncertain": "evidence_insufficient"}[a.status], reason=a.evidence_text)
            elif key == "aisle" and case.installation_analysis:
                row.update(status="evidence_insufficient", reason="组图未返回可辨认通道的检查结果，需核对相关视角")
            if key == "wind_snow" and case.pv_load_screening_assessment:
                a = case.pv_load_screening_assessment
                row.update(status="evidence_insufficient" if a.get("requires_manual_review", True) else "completed", reason=a.get("label", a.get("status", "风雪测算已返回")))
            if key == "watermark":
                scene = [m for m in case.materials if m.category.value in item["materials"]]
                if scene:
                    sufficient = all(m.captured_at and m.longitude is not None and m.latitude is not None for m in scene) and case.project.proposed_start_date is not None
                    row.update(status="completed" if sufficient else "evidence_insufficient",
                               reason="水印与日期已核验，具体提示见材料结论" if sufficient else "拍摄日期、位置水印或拟起保日期不足")
            rows.append(row)
        return rows
