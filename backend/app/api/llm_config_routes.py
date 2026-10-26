"""Shared live model routing and prompt-template configuration."""

import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config.llm_runtime import read_runtime, update_runtime
from app.prompts.stages import DEFAULT_STAGE_PROMPTS, REQUIRED_PLACEHOLDERS
from app.prompts.simulation import ensure_simulation_rule
from app.settings import MODEL_ENV_BY_STAGE, ProviderConfigurationError, VlmSettings

router = APIRouter()


class RuntimeUpdate(BaseModel):
    models: dict[str, str | None] = Field(default_factory=dict)
    prompts: dict[str, str | None] = Field(default_factory=dict)


@router.get("/api/v1/config/llm")
def get_llm_config() -> dict:
    runtime = read_runtime()
    try:
        settings = VlmSettings.from_environment()
        effective = {stage: settings.for_stage(stage).model for stage in MODEL_ENV_BY_STAGE}
    except ProviderConfigurationError:
        effective = {stage: runtime["models"].get(stage) or os.getenv(env_name) or None
                     for stage, env_name in MODEL_ENV_BY_STAGE.items()}
    return {
        "models": effective,
        "model_overrides": runtime["models"],
        "model_stages": list(MODEL_ENV_BY_STAGE),
        "prompts": {key: ensure_simulation_rule(runtime["prompts"].get(key, default))
                    for key, default in DEFAULT_STAGE_PROMPTS.items()},
        "prompt_overrides": list(runtime["prompts"]),
        "prompt_defaults": {key: ensure_simulation_rule(default)
                            for key, default in DEFAULT_STAGE_PROMPTS.items()},
        "required_placeholders": REQUIRED_PLACEHOLDERS,
    }


@router.patch("/api/v1/config/llm")
def patch_llm_config(update: RuntimeUpdate) -> dict:
    try:
        update_runtime(models=update.models, prompts=update.prompts)
    except (TypeError, ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return get_llm_config()
