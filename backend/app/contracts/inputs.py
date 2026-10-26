from datetime import date, datetime
from typing import Literal, Self

from pydantic import Field, IPvAnyAddress, field_validator, model_validator

from .common import Bbox, ContractModel
from .enums import (
    CaptureView,
    InstallationType,
    MaterialCategory,
    MaterialParseStatus,
    MaterialQualityStatus,
    OcrValueStatus,
    ProjectType,
    WatermarkStatus,
)


class ProjectInfo(ContractModel):
    """光伏项目和被保险标的信息。"""

    project_name: str | None
    insured_name: str | None
    insured_address: str | None = None
    project_entity: str | None = None
    industry_name: str | None = None
    fire_surcharge_confirmed: bool | None = None
    project_type: ProjectType
    installation_type: InstallationType
    site_address: str | None
    province: str | None = None
    city: str | None = None
    district: str | None = None
    longitude: float | None = Field(ge=-180.0, le=180.0)
    latitude: float | None = Field(ge=-90.0, le=90.0)
    proposed_start_date: date | None
    component_model: str | None
    submission_ip: IPvAnyAddress | None = None
    installed_capacity_kw: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    insured_type: str | None = Field(default=None, max_length=80)


class Material(ContractModel):
    """一次投保提交中的单份材料。"""

    material_id: str = Field(min_length=1)
    category: MaterialCategory
    additional_categories: list[MaterialCategory] = Field(default_factory=list)
    category_source: str | None = None
    category_note: str | None = None
    capture_view: CaptureView = CaptureView.UNKNOWN
    file_name: str = Field(min_length=1)
    media_type: str = Field(min_length=1)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")
    duplicate_of_material_id: str | None = None
    captured_at: datetime | None = None
    watermark_status: WatermarkStatus = WatermarkStatus.NOT_CHECKED
    longitude: float | None = Field(default=None, ge=-180.0, le=180.0)
    latitude: float | None = Field(default=None, ge=-90.0, le=90.0)
    quality_status: MaterialQualityStatus
    quality_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    quality_issues: list[str]
    parse_status: MaterialParseStatus


class FilingProhibitionAssessment(ContractModel):
    """模型对备案证某一原文字段的禁投判断；最终动作由规则引擎决定。"""

    status: Literal["confirmed", "not_found", "uncertain"]
    prohibition_type: Literal[
        "centralized", "forest_pv", "fishery_pv", "tidal_flat",
        "under_panel_cultivation", "agriculture", "forestry", "livestock",
        "water", "mountain", "desert", "unsupported_installation",
    ] | None = None
    generation_mode: Literal["distributed", "centralized", "unknown"] = "unknown"
    installation_site: Literal["building_roof", "carport_roof", "other", "unknown"] = "unknown"
    reason: str = Field(min_length=1)
    evidence_quote: str | None = None
    evidence_bbox: Bbox | None = None

    @field_validator("evidence_bbox", mode="before")
    @classmethod
    def discard_unreliable_bbox(cls, value: object) -> Bbox | None:
        if not isinstance(value, dict):
            return None
        if value.get("coordinate_space", "normalized_0_1") != "normalized_0_1":
            return None
        try:
            return Bbox.model_validate({**value, "coordinate_space": "normalized_0_1"})
        except ValueError:
            return None

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        if self.status == "confirmed" and (
            self.prohibition_type is None or not self.evidence_quote
        ):
            raise ValueError("confirmed filing prohibition requires type and quote")
        if self.status != "confirmed" and self.prohibition_type is not None:
            raise ValueError("unconfirmed filing prohibition cannot have a type")
        return self


class OcrField(ContractModel):
    """从一份材料中提取的一项文字字段。"""

    field_id: str = Field(min_length=1)
    material_id: str = Field(min_length=1)
    extraction_task: Literal["watermark", "business"] | None = None
    field_name: str = Field(min_length=1)
    raw_value: str | None
    normalized_value: str | None
    value_status: OcrValueStatus
    confidence: float = Field(ge=0.0, le=1.0)
    bbox: Bbox | None = None
    page_number: int | None = Field(default=None, ge=1)
    filing_assessment: FilingProhibitionAssessment | None = None
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    evidence_text: str | None = None
