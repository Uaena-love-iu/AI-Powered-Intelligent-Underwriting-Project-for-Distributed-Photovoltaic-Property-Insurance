from collections import Counter
from datetime import datetime
from typing import Literal, Self

from pydantic import Field, model_validator

from app.location.assessment import LocationAssessment

from .common import ContractModel
from .equipment import EquipmentInventoryItem
from .findings import RiskFinding
from .inputs import Material, OcrField, ProjectInfo
from .map_review import MapImageryReview
from .outputs import (
    MaterialReview,
    PackageAssessment,
    ProcessingTrace,
    UnderwritingDecision,
)
from .profiles import CatastropheAssessment, ComponentProfile, WeatherProfile
from .stages import (
    ApplicableCheck,
    CheckResult,
    GeometryAnalysis,
    InstallationAnalysis,
    StageReview,
)


def find_duplicate_ids(values: list[str]) -> list[str]:
    """返回列表中重复出现的ID。"""

    return sorted(value for value, count in Counter(values).items() if count > 1)


class UnderwritingCase(ContractModel):
    """一次分布式光伏财产险智能核保案件。"""

    schema_version: Literal["0.1.0"]
    case_id: str = Field(min_length=1)
    run_started_at: datetime | None = None
    ruleset_snapshot: dict = Field(default_factory=dict)
    revision: dict = Field(default_factory=dict)
    original_ocr_fields: list[OcrField] = Field(default_factory=list)
    evidence_coverage: dict = Field(default_factory=dict)
    verification: dict = Field(default_factory=dict)
    strategy_assessment: dict = Field(default_factory=dict)
    component_review: dict = Field(default_factory=dict)
    component_source_material_ids: list[str] = Field(default_factory=list)
    rule_execution: list[dict] = Field(default_factory=list)
    project: ProjectInfo
    materials: list[Material]
    equipment_inventory: list[EquipmentInventoryItem] = Field(default_factory=list)
    ocr_fields: list[OcrField]
    findings: list[RiskFinding]
    component_profile: ComponentProfile | None = None
    weather_profile: WeatherProfile | None = None
    catastrophe_assessment: CatastropheAssessment | None = None
    pv_load_screening_assessment: dict | None = None
    component_module_result: dict = Field(default_factory=dict)
    location_assessment: LocationAssessment | None = None
    package_assessment: PackageAssessment | None = None
    map_reviews: list[MapImageryReview] = Field(default_factory=list)
    material_reviews: list[MaterialReview]
    installation_analysis: InstallationAnalysis | None = None
    applicable_checks: list[ApplicableCheck] = Field(default_factory=list)
    check_results: list[CheckResult] = Field(default_factory=list)
    geometry_analysis: GeometryAnalysis | None = None
    stage_reviews: list[StageReview] = Field(default_factory=list)
    pre_catastrophe_outcome: Literal[
        "recommend_reject", "manual_review", "request_more", "continue"
    ] | None = None
    decision: UnderwritingDecision | None = None
    processing_trace: list[ProcessingTrace]

    @model_validator(mode="after")
    def validate_ids_and_references(self) -> Self:
        material_ids = [material.material_id for material in self.materials]
        ocr_field_ids = [field.field_id for field in self.ocr_fields]
        finding_ids = [finding.finding_id for finding in self.findings]
        map_review_ids = [review.review_id for review in self.map_reviews]

        duplicate_material_ids = find_duplicate_ids(material_ids)
        duplicate_ocr_field_ids = find_duplicate_ids(ocr_field_ids)
        duplicate_finding_ids = find_duplicate_ids(finding_ids)
        duplicate_map_review_ids = find_duplicate_ids(map_review_ids)

        if duplicate_material_ids:
            raise ValueError(f"duplicate material IDs: {duplicate_material_ids}")
        if duplicate_ocr_field_ids:
            raise ValueError(f"duplicate OCR field IDs: {duplicate_ocr_field_ids}")
        if duplicate_finding_ids:
            raise ValueError(f"duplicate finding IDs: {duplicate_finding_ids}")
        if duplicate_map_review_ids:
            raise ValueError(
                f"duplicate map review IDs: {duplicate_map_review_ids}"
            )

        material_id_set = set(material_ids)
        for material in self.materials:
            if material.duplicate_of_material_id is not None and (
                material.duplicate_of_material_id not in material_id_set
                or material.duplicate_of_material_id == material.material_id
            ):
                raise ValueError("duplicate material references an invalid original")
        ocr_field_id_set = set(ocr_field_ids)
        finding_id_set = set(finding_ids)

        for item in self.equipment_inventory:
            if item.source_material_id not in material_id_set:
                raise ValueError(
                    "equipment inventory item references unknown material "
                    f"{item.source_material_id}"
                )

        for field in self.ocr_fields:
            if field.material_id not in material_id_set:
                raise ValueError(
                    f"OCR field {field.field_id} references unknown material "
                    f"{field.material_id}"
                )

        for finding in self.findings:
            if finding.material_id not in material_id_set:
                raise ValueError(
                    f"finding {finding.finding_id} references unknown material "
                    f"{finding.material_id}"
                )

        for review in self.material_reviews:
            if review.material_id not in material_id_set:
                raise ValueError(
                    f"material review references unknown material {review.material_id}"
                )

            unknown_finding_ids = sorted(set(review.finding_ids) - finding_id_set)
            if unknown_finding_ids:
                raise ValueError(
                    f"material review references unknown findings: {unknown_finding_ids}"
                )

            unknown_ocr_field_ids = sorted(
                set(review.ocr_field_ids) - ocr_field_id_set
            )
            if unknown_ocr_field_ids:
                raise ValueError(
                    "material review references unknown OCR fields: "
                    f"{unknown_ocr_field_ids}"
                )

        if self.installation_analysis is not None:
            if set(self.installation_analysis.filing_field_ids) - ocr_field_id_set:
                raise ValueError("installation analysis references unknown filing fields")
            for area in self.installation_analysis.areas:
                unknown = set(area.material_ids) - material_id_set
                region_unknown = {region.material_id for region in area.evidence_regions}
                region_unknown -= set(area.material_ids)
                if unknown or region_unknown:
                    raise ValueError(
                        "installation area references unknown materials: "
                        f"{sorted(unknown | region_unknown)}"
                    )
        for stage in self.stage_reviews:
            unknown_materials = set(stage.material_ids) - material_id_set
            unknown_fields = set(stage.ocr_field_ids) - ocr_field_id_set
            unknown_findings = set(stage.finding_ids) - finding_id_set
            if unknown_materials or unknown_fields or unknown_findings:
                raise ValueError("stage review contains an unknown evidence reference")
        for result in self.check_results:
            if (set(result.material_ids) - material_id_set
                    or set(result.finding_ids) - finding_id_set
                    or set(result.ocr_field_ids) - ocr_field_id_set):
                raise ValueError("check result contains an unknown evidence reference")
        if self.geometry_analysis is not None:
            for area in self.geometry_analysis.areas:
                for metric in (area.tilt_degrees, area.height_above_ground_m,
                               area.ground_roughness):
                    if set(metric.material_ids) - material_id_set:
                        raise ValueError("geometry estimate references unknown materials")

        return self
