"""Standalone result contract, independent of the team's workbench framework."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

MECHANICAL_MAPPING = {
    "wind_load_pa": "back_static_load_pa",
    "snow_load_pa": "front_static_load_pa",
}
PARAMETER_KEYS = ("wind_load_pa", "snow_load_pa")


def numeric_missing_fields(values, evidence=None, *, exact=True):
    """Count available measurements separately from unverified hazard/design semantics."""
    evidence = evidence or {}
    missing = []
    for key in PARAMETER_KEYS:
        conflict = any(r.get("status") == "conflict" for r in evidence.get(key, []))
        mechanical = MECHANICAL_MAPPING.get(key)
        mechanical_conflict = any(r.get("status") == "conflict" for r in evidence.get(mechanical, []))
        direct = values.get(key)
        mapped = values.get(mechanical) if mechanical else None
        if not exact or conflict or not (
            direct is not None and direct > 0
            or mapped is not None and mapped > 0 and not mechanical_conflict
        ):
            missing.append(key)
    return missing


def load_basis(records):
    # API maxLoadFrontPa/maxLoadRearPa alone do not establish test vs design.
    verified = [r for r in records if r.get("source_kind") != "api" or r.get("load_basis_verified")]
    bases = {r.get("load_basis") for r in verified if r.get("load_basis") not in (None, "unspecified")}
    return next(iter(bases)) if len(bases) == 1 else "unspecified"


class ProviderError(RuntimeError):
    pass


class ComponentProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)
    component_model: str
    manufacturer: str | None = None
    rated_power_w: float | None = None
    hail_resistance_mm: float | None = None
    wind_load_pa: float | None = None
    snow_load_pa: float | None = None
    front_static_load_pa: float | None = None
    back_static_load_pa: float | None = None
    hail_impact_velocity_m_s: float | None = None
    market_version: str | None = None
    source_document_type: str | None = None
    source_url: str | None = None
    source_name: str
    retrieved_at: datetime
    match_confidence: float
    parameter_sources: dict[str, str] = Field(default_factory=dict)
    lookup_notes: list[str] = Field(default_factory=list)
    model_match_status: str = "exact"
    parameter_evidence: dict[str, list[dict[str, object]]] = Field(default_factory=dict)

    def workbench_payload(self) -> dict:
        data = self.model_dump(mode="json")
        for key in ("hail_resistance_mm", "hail_impact_velocity_m_s"):
            data.pop(key, None)
            data["parameter_evidence"].pop(key, None)
            data["parameter_sources"].pop(key, None)
        fields = {}
        for key, label in [
            ("wind_load_pa", "风荷载"),
            ("snow_load_pa", "雪荷载"),
        ]:
            records = self.parameter_evidence.get(key, [])
            valid = [x for x in records if x.get("status") == "found"]
            value = getattr(self, key)
            reference_key = MECHANICAL_MAPPING.get(key)
            reference = getattr(self, reference_key) if reference_key else None
            mechanical_records = self.parameter_evidence.get(reference_key, [])
            mechanical_valid = [r for r in mechanical_records if r.get("status") == "found"]
            mapped = (
                self.model_match_status == "exact" and value is None and reference is not None and reference > 0
                and not any(r.get("status") == "conflict" for r in records + mechanical_records)
            )
            display_value = reference if mapped else value
            display_records = [dict(r, derived_from=reference_key, mapping_kind="direction_based") for r in mechanical_valid] if mapped else records
            display_valid = mechanical_valid if mapped else valid
            status = (
                "conflict"
                if any(x.get("status") == "conflict" for x in records) or value is None and any(x.get("status") == "conflict" for x in mechanical_records)
                else "found"
                if value is not None
                else "mechanical_mapped"
                if mapped
                else "observed_not_found"
                if any(x.get("status") == "observed_not_found" for x in records)
                else "unknown"
            )
            fields[key] = {
                "label": label,
                "value": display_value if self.model_match_status == "exact" and status != "conflict" else None,
                "direct_value": value,
                "value_origin": "mechanical_mapping" if mapped else "direct_reported" if value is not None else None,
                "unit": "mm" if key == "hail_resistance_mm" else "Pa",
                "status": status,
                "load_basis": load_basis(display_valid) if display_valid else None,
                "reference_value": reference,
                "reference_field": reference_key,
                "reference_label": (
                    "背面机械载荷（抗风参考）"
                    if key == "wind_load_pa"
                    else "正面机械载荷（抗雪参考）"
                    if key == "snow_load_pa"
                    else None
                ),
                "evidence": display_records,
                "mapping_kind": "direction_based" if mapped else None,
                "mapping_note": (
                    "来自背面机械载荷，映射描述抗风吸力；未取得该型号直接风荷载字段。"
                    if key == "wind_load_pa" else
                    "来自正面机械载荷，映射描述抗雪压；正面也承受风正压，未取得直接雪荷载字段。"
                ) if mapped else None,
                "can_use_as_design_capacity": not mapped and bool(valid)
                and all(
                    x.get("load_basis") == "design" and not x.get("requires_manual_review")
                    for x in valid
                )
                and key != "hail_resistance_mm",
            }
        data["ui_fields"] = fields
        # Mechanical measurements are first-class data, even when their hazard
        # correspondence or design basis is not established.
        mechanical = {}
        for key,label,direction in [('front_static_load_pa','正面机械载荷','front'),('back_static_load_pa','背面机械载荷','back')]:
            records=self.parameter_evidence.get(key,[])
            valid=[r for r in records if r.get('status')=='found']
            value=getattr(self,key)
            mechanical[key]={'label':label,'value':value,'unit':'Pa','load_direction':direction,
                'status':'conflict' if any(r.get('status')=='conflict' for r in records) else 'found' if value is not None else 'unknown',
                'load_basis':load_basis(valid) if valid else None,
                'requires_manual_review':any(r.get('requires_manual_review') for r in valid),
                'evidence':records,'can_use_as_design_capacity':False}
        data['mechanical_fields']=mechanical
        data['parameter_tasks'] = {
            'missing_values': numeric_missing_fields(data, self.parameter_evidence, exact=self.model_match_status == 'exact'),
            'verification_targets': [key for key, field in fields.items() if field['status'] == 'mechanical_mapped'],
        }
        data['presentation_version'] = 'wind-snow-v2'
        return data
