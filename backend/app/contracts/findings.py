from typing import Literal

from pydantic import Field

from .common import Bbox, ContractModel
from .enums import (
    DetectionStatus,
    EnvironmentRelation,
    RiskCategory,
    RiskSeverity,
)


class EvidenceObservation(ContractModel):
    """Visible coverage of a required part, independent of risk detection."""
    target: str = Field(min_length=1, max_length=80)
    status: Literal["visible", "not_visible", "occluded", "blurred", "unknown"]
    evidence_text: str = Field(min_length=2, max_length=1500)


class RiskFinding(ContractModel):
    """一项图片风险识别结果。"""

    finding_id: str = Field(min_length=1)
    material_id: str = Field(min_length=1)
    category: RiskCategory
    label: str = Field(min_length=1)
    detection_status: DetectionStatus
    severity: RiskSeverity
    confidence: float = Field(ge=0.0, le=1.0)
    environment_relation: EnvironmentRelation = EnvironmentRelation.UNCERTAIN
    bbox: Bbox | None = None
    evidence_text: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    evidence_coverage: list[EvidenceObservation] = Field(default_factory=list, max_length=20)
    requires_manual_review: bool
    uncertainty_kind: Literal['none', 'visual', 'missing', 'classification', 'conflict'] = 'none'
    area_id: str | None = None
    object_id: str | None = None
