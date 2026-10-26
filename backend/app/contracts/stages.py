"""Evidence and decisions produced before catastrophe assessment."""

from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from .common import Bbox, ContractModel
from .enums import MaterialCategory, RiskSeverity


class InstallationSceneType(StrEnum):
    BUILDING_ROOF = "building_roof"
    CARPORT_ROOF = "carport_roof"
    GROUND_MOUNT = "ground_mount"
    FLOATING = "floating"
    OTHER_CANOPY = "other_canopy"
    BUILDING_FACADE = "building_facade"
    OTHER_KNOWN = "other_known"
    UNKNOWN = "unknown"


class SceneEvidenceRegion(ContractModel):
    material_id: str = Field(min_length=1)
    bbox: Bbox | None = None
    description: str = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def add_default_coordinate_space(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        # Some vision models put this bbox annotation beside bbox instead of inside it.
        # Accept only the known coordinate system; unknown values remain validation errors.
        if value.get("coordinate_space") == "normalized_0_1":
            value = {key: item for key, item in value.items()
                     if key != "coordinate_space"}
        bbox = value.get("bbox")
        if not isinstance(bbox, dict) or "coordinate_space" in bbox:
            return value
        return {**value, "bbox": {**bbox, "coordinate_space": "normalized_0_1"}}


class InstallationArea(ContractModel):
    area_id: str = Field(min_length=1)
    scene_type: InstallationSceneType
    status: Literal["confirmed", "candidate", "unknown", "conflict"]
    material_ids: list[str] = Field(default_factory=list)
    supporting_structure: str | None = None
    usage: str | None = None
    evidence_text: str = Field(min_length=1)
    evidence_regions: list[SceneEvidenceRegion] = Field(default_factory=list)
    pending_question: str | None = None


class AisleAssessment(ContractModel):
    status: Literal["detected", "not_detected", "uncertain"]
    material_id: str = Field(min_length=1)
    supporting_material_ids: list[str] = Field(min_length=1)
    cross_image_reasoning: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    severity: RiskSeverity
    evidence_text: str = Field(min_length=1)
    bbox: Bbox | None = None

    @model_validator(mode="before")
    @classmethod
    def add_default_coordinate_space(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        bbox = value.get("bbox")
        if not isinstance(bbox, dict) or "coordinate_space" in bbox:
            return value
        return {**value, "bbox": {**bbox, "coordinate_space": "normalized_0_1"}}

    @model_validator(mode="after")
    def validate_location(self):
        if self.status != "detected" and self.bbox is not None:
            raise ValueError("only a detected obstruction may have a bbox")
        return self


class InstallationAnalysis(ContractModel):
    areas: list[InstallationArea] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    filing_field_ids: list[str] = Field(default_factory=list)
    aisle_assessment: AisleAssessment | None = None
    provider: str
    model: str | None = None


class StageReview(ContractModel):
    # Keep the old value readable for reports saved before the installation-type rename.
    stage: Literal["filing", "installation_type", "installation_scope", "aisle_group", "single_image", "completeness", "location"]
    action: Literal["continue", "recommend_reject", "manual_review", "request_more"]
    rule_ids: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    material_ids: list[str] = Field(default_factory=list)
    ocr_field_ids: list[str] = Field(default_factory=list)
    finding_ids: list[str] = Field(default_factory=list)
    evidence_texts: list[str] = Field(default_factory=list)
    pending_questions: list[str] = Field(default_factory=list)
    customer_requests: list[str] = Field(default_factory=list)
    terminates_run: bool = False


class ApplicableCheck(ContractModel):
    area_id: str
    key: str
    label: str
    category: MaterialCategory | None = None
    applicability: Literal["applicable", "not_applicable", "pending"]
    required_now: bool
    evidence_requirement: str
    reason: str


class CheckResult(ContractModel):
    area_id: str
    key: str
    status: Literal["valid", "missing", "not_provided", "unusable", "not_applicable", "pending", "unassessed"]
    rule_ids: list[str] = Field(default_factory=list)
    material_ids: list[str] = Field(default_factory=list)
    finding_ids: list[str] = Field(default_factory=list)
    ocr_field_ids: list[str] = Field(default_factory=list)
    reason: str
    customer_request: str | None = None


class GeometryMetric(ContractModel):
    status: Literal["estimated", "unknown"]
    value_min: float | None = Field(default=None, ge=0)
    value_max: float | None = Field(default=None, ge=0)
    roughness_candidate: Literal["A", "B", "C", "D"] | None = None
    material_ids: list[str] = Field(default_factory=list)
    evidence_text: str

    @model_validator(mode="after")
    def validate_estimate(self):
        if self.status == "unknown":
            if any(value is not None for value in (
                self.value_min, self.value_max, self.roughness_candidate
            )):
                raise ValueError("unknown geometry must not contain a guessed value")
        elif (not self.material_ids or
              not ((self.value_min is not None and self.value_max is not None)
                   or self.roughness_candidate is not None)):
            raise ValueError("estimated geometry needs a value and source image")
        if (self.value_min is not None and self.value_max is not None
                and self.value_max < self.value_min):
            raise ValueError("geometry range must be ordered")
        return self


class GeometryArea(ContractModel):
    area_id: str
    tilt_degrees: GeometryMetric
    height_above_ground_m: GeometryMetric
    ground_roughness: GeometryMetric

    @model_validator(mode="after")
    def validate_metric_types(self):
        for metric in (self.tilt_degrees, self.height_above_ground_m):
            if metric.roughness_candidate is not None:
                raise ValueError("numeric geometry metric cannot contain roughness")
        if (self.tilt_degrees.value_max is not None
                and self.tilt_degrees.value_max > 90):
            raise ValueError("tilt exceeds 90 degrees")
        if (self.ground_roughness.value_min is not None
                or self.ground_roughness.value_max is not None):
            raise ValueError("roughness must use A-D candidate")
        return self


class GeometryAnalysis(ContractModel):
    areas: list[GeometryArea] = Field(default_factory=list)
    provider: str
    model: str | None = None
    notes: list[str] = Field(default_factory=list)
