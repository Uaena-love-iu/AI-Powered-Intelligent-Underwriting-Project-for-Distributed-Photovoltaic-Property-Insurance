from datetime import datetime
from typing import Literal, Self

from pydantic import Field, model_validator

from .common import ContractModel
from .enums import (
    DecisionType,
    MaterialReviewAction,
    ProcessingStatus,
    ProcessingStep,
)


class PackageAssessment(ContractModel):
    """材料数量与类别覆盖的审计快照；不作为固定数量补材门槛。"""

    image_count: int = Field(ge=0)
    panorama_count: int = Field(ge=0)
    has_filing_certificate: bool
    has_front_level_panorama: bool
    has_overhead_panorama: bool
    missing_material_categories: list[str] = Field(default_factory=list)
    missing_requirements: list[str]
    requirement_profile: Literal["demo_minimum", "full_intake"] = "demo_minimum"
    minimum_gate_passed: bool | None = None
    document_image_count: int = Field(default=0, ge=0)
    coverage_requirements: list[str] = Field(default_factory=list)


class MaterialReview(ContractModel):
    """规则引擎对单份材料的审核结果。"""

    material_id: str = Field(min_length=1)
    action: MaterialReviewAction
    triggered_rule_ids: list[str]
    finding_ids: list[str]
    ocr_field_ids: list[str]
    reasons: list[str]
    missing_requirements: list[str]
    requires_manual_review: bool


class RuleExplanation(ContractModel):
    """用户可读的规则触发条件和处理作用。"""

    rule_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    trigger: str = Field(min_length=1)
    effect: str = Field(min_length=1)


class UnderwritingDecision(ContractModel):
    """整单综合核保意见。"""

    decision: DecisionType
    decisive_rule_ids: list[str]
    rule_explanations: list[RuleExplanation] = Field(default_factory=list)
    reasons: list[str]
    conditions: list[str]
    warnings: list[str]
    missing_requirements: list[str]
    generated_at: datetime
    pricing_recommendation: Literal["not_proposed", "surcharge"] = "not_proposed"


class OcrTaskExecution(ContractModel):
    material_id: str
    task: Literal["watermark", "business"]
    status: Literal["success", "failed", "skipped"]
    field_ids: list[str]
    reason: str | None = None


class ProcessingTrace(ContractModel):
    """一个核保处理步骤的运行留痕。"""

    step: ProcessingStep
    provider: str = Field(min_length=1)
    model: str | None = None
    started_at: datetime
    finished_at: datetime
    latency_ms: int = Field(ge=0)
    status: ProcessingStatus
    detail: str | None = None
    output_status: Literal["available", "partial", "missing"] | None = None
    task_executions: list[OcrTaskExecution] = Field(default_factory=list)
    failed_material_ids: list[str] = Field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None

    @model_validator(mode="after")
    def validate_time_order(self) -> Self:
        if self.finished_at < self.started_at:
            raise ValueError("finished_at must not be before started_at")
        return self
