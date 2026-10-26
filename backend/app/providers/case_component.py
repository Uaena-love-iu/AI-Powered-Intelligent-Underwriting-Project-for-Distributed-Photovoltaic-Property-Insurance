"""Case-local parameter corrections with source snapshots and fresh calculations."""
from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid4

from app.contracts import ComponentProfile
from app.contracts.component_review import PARAMETER_NAMES
from app.providers.common import ProviderError
from app.storage.cases import CaseRepository
from app.storage.verified_components import model_key
from app.storage.revisions import CorrectedOcrProvider


def prepare_review(review, project, materials, case_id, parent_id=None, actor=None):
    """Validate referenced bytes before creating a job or accepting any manual value."""
    if review is None:
        return None
    if model_key(project.component_model or "") != model_key(review.component_model):
        raise ValueError("本案补充参数型号与项目完整型号不一致，请重新确认")
    by_id = {item.material.material_id: item for item in materials}
    def source(id_, expected):
        item = by_id.get(id_)
        if item is None or sha256(item.content).hexdigest() != expected:
            raise ValueError("核验依据文件已移除或替换，请重新核对型号与参数后确认")
        return item
    identity = source(review.identity_material_id, review.identity_sha256)
    if identity.material.category.value not in ("component_nameplate", "equipment_inventory"):
        raise ValueError("型号确认必须关联组件铭牌或设备清单")
    data = review.model_dump(mode="json")
    for key, parameter in data["parameters"].items():
        parameter["normalized_value"] = parameter["value"] * (1000 if parameter["unit"].startswith("k") else 1)
        if not __import__('math').isfinite(parameter["normalized_value"]):
            raise ValueError("单位换算超出数值范围")
        parameter["normalized_unit"] = PARAMETER_NAMES[key][1]
        if parameter["source_material_id"]:
            item = source(parameter["source_material_id"], parameter["source_sha256"])
            parameter["source_file_name"] = item.material.file_name
    parent = CaseRepository().get_case(parent_id) if parent_id else None
    data.update(review_id="CP-" + uuid4().hex[:16], confirmed_at=datetime.now(UTC).isoformat(),
                reviewer_name=actor or review.reviewer_name, original_case_id=parent_id,
                original_result=deepcopy(parent["case"].get("component_module_result", {})) if parent else {},
                original_profile=deepcopy(parent["case"].get("component_profile")) if parent else None,
                applied=False)
    return data


class CaseComponentProvider:
    """Overlay confirmed fields only; retain automatic output, including conflicts."""
    name = "case-confirmed-component-parameters"

    def __init__(self, delegate, review, case_id):
        self.delegate, self.review, self.case_id = delegate, deepcopy(review), case_id
        self.last_result = {}

    @property
    def evidence_cache_token(self):
        return {"case_id": self.case_id, "review": self.review,
                "delegate": getattr(self.delegate, "evidence_cache_token", None)}

    def lookup_materials(self, project, materials):
        return self.lookup(project.component_model)

    def lookup(self, model):
        if model_key(model or "") != model_key(self.review["component_model"]):
            raise ProviderError("Confirmed component model changed")
        automatic, original = None, {}
        try:
            if self.delegate:
                automatic = self.delegate.lookup(model)
                original = deepcopy(getattr(self.delegate, "last_result", {}))
        except (ProviderError, ValueError, RuntimeError, OSError):
            original = {"status": "source_unavailable", "notes": ["自动查询未完成；仅采用本案已确认、有证据的补充字段。"]}
        exact = bool(automatic and model_key(automatic.component_model) == model_key(model) and automatic.model_match_status == "exact")
        if exact:
            profile = automatic.model_copy(deep=True)
        else:
            profile = ComponentProfile(component_model=model, manufacturer=self.review["manufacturer"],
                model_match_status="exact", source_url=None, source_name="本案人工核验资料",
                retrieved_at=datetime.now(UTC), match_confidence=1)
        before = automatic.model_dump(mode="json") if automatic else None
        audit = {**deepcopy(self.review), "applied": True, "applied_case_id": self.case_id,
                 "automatic_result": original, "automatic_profile": before}
        raw = deepcopy(original.get("profile") or {}) if exact else {}
        raw.update(component_model=model, manufacturer=self.review["manufacturer"], model_match_status="exact")
        raw.setdefault("ui_fields", {})
        raw.setdefault("mechanical_fields", {})
        raw.setdefault("parameter_evidence", {})
        # Remove old directional mappings when their underlying mechanical value changes.
        for field in list(raw["ui_fields"]):
            if field in ("wind_load_pa", "snow_load_pa") and raw["ui_fields"][field].get("value_origin") == "mechanical_mapping":
                raw["ui_fields"].pop(field)
                setattr(profile, field, None)
                raw[field] = None
        for field, parameter in audit["parameters"].items():
            label, unit, direction = PARAMETER_NAMES[field]
            source_url = parameter["source_url"] or (
                f"/api/v1/cases/{self.case_id}/materials/{parameter['source_material_id']}/source")
            evidence = dict(status="found", value=parameter["normalized_value"], unit=unit,
                quote=parameter["quote"], page=parameter["page"], source_url=source_url,
                document_title=parameter["document_title"], document_version=parameter["document_version"],
                load_basis=parameter["load_basis"], load_direction=direction, applicability=parameter["applicability"],
                installation_confirmed=parameter["installation_confirmed"], source_kind="case_manual",
                human_verification={"scope": "case_only", "review_id": audit["review_id"],
                                    "reviewer": audit["reviewer_name"], "confirmed_at": audit["confirmed_at"]})
            setattr(profile, field, parameter["normalized_value"])
            profile.parameter_evidence[field] = {"status": "found", "value": parameter["normalized_value"], "records": [evidence]}
            profile.parameter_sources[field] = source_url
            raw[field] = parameter["normalized_value"]
            raw["parameter_evidence"][field] = [evidence]
            target = raw["ui_fields"] if field == "rated_power_w" else raw["mechanical_fields"]
            target[field] = dict(label=label, value=parameter["normalized_value"], unit=unit, status="found",
                                 load_basis=parameter["load_basis"], value_origin="case_manual")
        profile.manufacturer = self.review["manufacturer"]
        profile.source_name = "本案确认参数（原自动检索结果另行保留）"
        profile.lookup_notes.append("本案补充字段经人工确认，仅在本轮及明确继承的复核轮次中使用；未写入共享参数库。")
        self.last_result = dict(status="case_confirmed", profile=raw, case_parameter_review=audit)
        profile.workbench_result = self.last_result
        return profile


class CaseReviewedOcrProvider(CorrectedOcrProvider):
    """An OCR outage cannot erase explicit case facts; unreviewed fields stay unknown."""
    def __init__(self, delegate, review, project):
        correction = dict(field='component_model',material_id=review['identity_material_id'],evidence=review['model_reason'])
        super().__init__(delegate, [correction], project)
        object.__setattr__(self, 'review', review)
        object.__setattr__(self, 'failures', [])

    def extract(self, item):
        from app.contracts import OcrField
        target = item.material.material_id == self.review['identity_material_id'] and item.ocr_task != 'watermark'
        try:
            fields = self.delegate.extract(item)
        except ProviderError:
            if not target:
                raise
            fields = []
            with self.lock:
                self.failures.append(dict(material_id=item.material.material_id,
                    reason='自动文字读取失败，仅采用经本案人工确认的型号／功率；其他字段仍未知'))
        if not target:
            return fields
        values = {'component_model': (self.review['component_model'], self.review['model_reason'])}
        power = self.review['parameters'].get('rated_power_w')
        if power:
            values['rated_power_w'] = (str(power['normalized_value']), power['quote'])
        result = [f for f in fields if f.field_name not in values]
        for key,(value,evidence) in values.items():
            selected = [f for f in fields if f.field_name == key]
            with self.lock:
                for f in selected:
                    self.originals[f.field_id] = f.model_copy(deep=True)
            result.append(OcrField(field_id=selected[0].field_id if selected else 'human-'+item.material.material_id+'-'+key,
                material_id=item.material.material_id,extraction_task='business',field_name=key,raw_value=value,
                normalized_value=value,value_status='extracted',confidence=1,provider='human-review',model='case-component-review-v1',
                evidence_text='本案人工核验：'+evidence))
        return result
