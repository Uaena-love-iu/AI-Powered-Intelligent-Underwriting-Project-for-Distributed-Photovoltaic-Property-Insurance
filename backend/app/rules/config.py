"""Versioned business-rule configuration for the competition demo."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.contracts import EnvironmentRelation

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_RULES_PATH = PROJECT_ROOT / "data" / "rules" / "challenge_open_ai_01.json"


class BusinessRulesConfig(BaseModel):
    """Configurable inputs extracted from the challenge brief.

    The official underwriting workbook was supplied. The fire-industry list and
    stage-two approval were not; an empty list is unknown, never evidence that
    an industry is safe.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    ruleset_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    source_note: str = Field(min_length=1)
    high_fire_risk_industries: tuple[str, ...] = ()
    environment_auto_reject_relations: tuple[EnvironmentRelation, ...] = (
        EnvironmentRelation.PROJECT_SITE,
    )
    environment_auto_reject_min_confidence: float = Field(default=0.65, ge=0.0, le=1.0)
    advanced_checks_enabled: bool = False
    package_requirement_profile: Literal["demo_minimum", "full_intake"] = "demo_minimum"
    # Delivery-defined numerical screening thresholds; business approval remains pending.
    catastrophe_adequate_margin_ratio: float = Field(default=1.25, gt=1.0)
    catastrophe_critical_shortfall_ratio: float = Field(default=0.75, gt=0.0, lt=1.0)

    @field_validator("high_fire_risk_industries")
    @classmethod
    def normalize_industries(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(dict.fromkeys(value.strip() for value in values if value.strip()))
        return normalized

    @field_validator("environment_auto_reject_relations")
    @classmethod
    def validate_environment_auto_reject_relations(
        cls,
        values: tuple[EnvironmentRelation, ...],
    ) -> tuple[EnvironmentRelation, ...]:
        allowed = {
            EnvironmentRelation.PROJECT_SITE,
            EnvironmentRelation.OPERATIONAL_SURROUNDINGS,
        }
        if any(value not in allowed for value in values):
            raise ValueError(
                "distant background and uncertain environment relations cannot trigger automatic rejection"
            )
        return tuple(dict.fromkeys(values))

    @model_validator(mode="after")
    def validate_catastrophe_thresholds(self) -> Self:
        if self.catastrophe_critical_shortfall_ratio >= self.catastrophe_adequate_margin_ratio:
            raise ValueError("catastrophe thresholds are inconsistent")
        return self

    @classmethod
    def load(cls, path: str | Path | None = None) -> Self:
        configured_path = path or os.getenv("PV_BUSINESS_RULES_PATH")
        rules_path = Path(configured_path).expanduser() if configured_path else DEFAULT_RULES_PATH
        if not rules_path.is_absolute():
            rules_path = PROJECT_ROOT / rules_path
        try:
            payload = json.loads(rules_path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("business rules configuration is unavailable or invalid") from exc
        return cls.model_validate(payload)
