from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from app.contracts import RiskFinding, InstallationAnalysis


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Option(Strict):
    id: str = Field(min_length=1, max_length=60)
    label: str = Field(min_length=1, max_length=400)
    fact: Literal['present', 'absent', 'unknown']
    classification: Literal['成立', '不成立', '待确认']
    treatment: Literal['acceptable', 'surcharge', 'reject', 'request_more', 'undecided']


class Plan(Strict):
    action: Literal['single', 'group', 'request_more', 'human']
    question: str = Field(min_length=5, max_length=1500)
    reason: str = Field(min_length=2, max_length=1500)
    material_ids: list[str] = Field(default_factory=list, max_length=5)
    missing_evidence: str = Field(default='', max_length=1500)
    evidence_sufficient: bool = False
    options: list[Option] = Field(default_factory=list, max_length=6)


class VerificationResult(Strict):
    outcome: Literal['resolved', 'uncertain', 'request_more', 'classification']
    conclusion: str = Field(min_length=2, max_length=3000)
    same_object: bool = False
    evidence_sufficient: bool = False
    evidence_ids: list[str] = Field(default_factory=list, max_length=5)
    findings: list[RiskFinding] = Field(default_factory=list, max_length=20)
    installation: InstallationAnalysis | None = None
    corrected_finding_ids: list[str] = Field(default_factory=list, max_length=20)
    correction_reason: str = Field(default='', max_length=1500)
    missing_evidence: str = Field(default='', max_length=1500)


class Review(Strict):
    version: int = Field(ge=1)
    option_id: str = Field(min_length=1, max_length=60)
    fact: Literal['present', 'absent', 'unknown']
    classification: Literal['成立', '不成立', '待确认']
    treatment: Literal['acceptable', 'surcharge', 'reject', 'request_more', 'undecided']
    # This describes the subject of disagreement, not a training label.
    disagreements: list[Literal['recognition', 'classification', 'treatment', 'unattributed']] = Field(min_length=1)
    opinion: str = Field(min_length=2, max_length=4000)
    actor: str = Field(min_length=1, max_length=100)
    evidence_ids: list[str] = Field(min_length=1, max_length=40)
    coverage_confirmed: bool = False


class FinalReview(Strict):
    version: int = Field(ge=1)
    decision: Literal['accept', 'conditional_accept', 'recommend_reject', 'request_more']
    opinion: str = Field(min_length=2, max_length=4000)
    actor: str = Field(min_length=1, max_length=100)
    override_reason: str = Field(default='', max_length=2000)
