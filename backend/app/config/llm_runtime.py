"""Persistent, server-side model and stage-prompt overrides."""

import json
import os
import re
from pathlib import Path
from threading import Lock
from uuid import uuid4

from app.settings import MODEL_ENV_BY_STAGE

PROMPT_KEYS = frozenset({
    "classification", "installation", "installation_localization", "aisle_group",
    "geometry", "watermark_guidance", "filing_assessment",
    "report_summary", "report_summary_recommend_reject", "report_summary_request_more",
    "report_summary_manual_review", "report_summary_surcharge",
    "report_summary_conditional_accept", "report_summary_accept",
})
_MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}$")
_LOCK = Lock()


def config_path() -> Path:
    override = os.getenv("PV_LLM_RUNTIME_CONFIG_PATH")
    return Path(override).expanduser() if override else (
        Path(__file__).resolve().parents[2] / "data" / "llm_runtime_config.json"
    )


def read_runtime() -> dict[str, dict[str, str]]:
    path = config_path()
    if not path.is_file():
        return {"models": {}, "prompts": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError("LLM runtime configuration must be an object")
    models = data.get("models", {})
    prompts = data.get("prompts", {})
    if not isinstance(models, dict) or not isinstance(prompts, dict):
        raise TypeError("LLM runtime configuration sections must be objects")
    return {"models": models, "prompts": prompts}


def validate_update(models: dict[str, str | None] | None,
                    prompts: dict[str, str | None] | None) -> None:
    for stage, model in (models or {}).items():
        if stage not in MODEL_ENV_BY_STAGE:
            raise ValueError(f"Unknown model stage: {stage}")
        if model is not None and (not isinstance(model, str) or not _MODEL_NAME.fullmatch(model)):
            raise ValueError(f"Invalid model name for {stage}")
    for key, prompt in (prompts or {}).items():
        if key not in PROMPT_KEYS:
            raise ValueError(f"Unknown prompt: {key}")
        if prompt is not None and (not isinstance(prompt, str) or not 30 <= len(prompt) <= 30000):
            raise ValueError(f"Prompt {key} must contain 30–30000 characters")
        if prompt is not None:
            from app.prompts.stages import REQUIRED_PLACEHOLDERS
            if any(marker not in prompt for marker in REQUIRED_PLACEHOLDERS.get(key, ())):
                raise ValueError(f"Prompt {key} is missing a required dynamic placeholder")


def update_runtime(*, models: dict[str, str | None] | None = None,
                   prompts: dict[str, str | None] | None = None) -> dict[str, dict[str, str]]:
    validate_update(models, prompts)
    with _LOCK:
        data = read_runtime()
        for section, changes in (("models", models), ("prompts", prompts)):
            for key, value in (changes or {}).items():
                if value is None:
                    data[section].pop(key, None)
                else:
                    data[section][key] = value
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
        return data
