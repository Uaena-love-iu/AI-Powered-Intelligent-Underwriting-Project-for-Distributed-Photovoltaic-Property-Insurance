"""Optional, source-linked AI interpretation of an already completed report."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.contracts import UnderwritingCase
from app.prompts.report_summary import SUMMARY_SCENARIOS
from app.prompts.stages import stage_prompt
from app.settings import ProviderConfigurationError, VlmSettings
from app.storage import CaseRepository

from .generator import generate_markdown_report
from .view import build_report_view

PROMPT_VERSION = "report-interpretation-v3"
STREAM_PROMPT_VERSION = "report-stream-v3"
FORBIDDEN_PHRASES = ("出险概率", "赔付概率", "损失率", "精确保费", "已经承保", "自动拒保")


class InterpretationUnavailable(RuntimeError):
    """The optional interpretation failed without affecting the core report."""


class InterpretationPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(pattern="^(current|basis|verify|next)$")
    text: str = Field(min_length=8, max_length=240)
    reference_ids: list[str] = Field(min_length=1, max_length=4)


class InterpretationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_code: str
    points: list[InterpretationPoint] = Field(min_length=4, max_length=4)


def report_fingerprint(case: UnderwritingCase, *, model: str,
                       review_revision: str = "") -> str:
    content = f"{PROMPT_VERSION}\n{model}\n{review_revision}\n{case.model_dump_json()}"
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def validate_interpretation(payload: Any, view: dict[str, Any]) -> dict[str, Any]:
    """Reject uncited, stale or decision-changing model output."""

    parsed = InterpretationPayload.model_validate(payload)
    if parsed.decision_code != view["decision"]["code"]:
        raise ValueError("interpretation decision differs from the rule result")
    if [point.kind for point in parsed.points] != ["current", "basis", "verify", "next"]:
        raise ValueError("interpretation must cover judgment, evidence, checks and next step")
    allowed = set(_model_input(view)["references"])
    for point in parsed.points:
        if not set(point.reference_ids).issubset(allowed):
            raise ValueError("interpretation cites an unknown report reference")
        if any(phrase in point.text for phrase in FORBIDDEN_PHRASES):
            raise ValueError("interpretation contains an unsupported insurance claim")
    return parsed.model_dump(mode="json")


def _model_input(view: dict[str, Any]) -> dict[str, Any]:
    """Send a small, structured report excerpt without source file contents."""

    active_actions = [
        item for item in view["actions"]
        if view["decision"]["code"] != "recommend_reject"
        or item["text"] == "核对拒保原件、现场关系与适用规则"
    ][:12]
    visible_checks = view["checks"][:20]
    relevant = {
        ref for item in [*view["basis"][:8], *active_actions, *visible_checks]
        for ref in [item["id"], *item["reference_ids"]]
    }
    relevant.update(f"hazard:{item['hazard']}" for item in view["hazards"])
    return {
        "decision": view["decision"],
        "basis": view["basis"][:8],
        "actions": active_actions,
        "checks": visible_checks,
        "hazards": view["hazards"],
        "references": {
            ref: meta for ref, meta in view["references"].items() if ref in relevant
        },
    }


def _request_model(view: dict[str, Any], settings: VlmSettings) -> dict[str, Any]:
    system = (
        "你是财产险核保报告的辅助解读员。只能解释输入中已经存在的系统建议、事实、"
        "证据、检查状态和待办，不得重新决定承保结果，不得创造新事实、费率、出险概率或保费。"
        "输出严格 JSON 对象：decision_code 与输入一致；points 恰好四条并按顺序分别为"
        "kind=current、basis、verify、next，每条包含 kind、text、reference_ids。"
        "四条依次回答：当前建议及现在能否推进；最影响判断的具体证据及其限制；"
        "首先要核实的具体事实；核实后的处理路径。每条必须引用输入 references 中的"
        "至少一个 ID。合并同类问题，不重复列出同一事项；每条尽量不超过两句话。"
        "不同材料中的字段不能说成出自同一原件；仅有待核提示时，不能说风险已经证实。"
        "描述图片风险时优先引用对应的 finding 证据，避免只引用材料而丢失标记位置。"
        "如果位置依据有候选点与照片坐标的距离，说明该距离及为何仍需复核；"
        "乡镇等区域级候选点不是电站准确位置，不能把候选距离写成已确认电站距离。"
        "读者是核保业务人员，用材料名称和日常业务语言写，不使用字段名、接口名、"
        "程序模块名或规则编号；如需提到风雪数值，解释它不能直接决定承保。"
        "如果某项未执行或无法判断，必须保留该状态。不要安排核保员去找谁处理，"
        "只写需要确认的事实、材料和重查事项。不要输出 Markdown。"
    )
    body = {
        "model": settings.model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(_model_input(view), ensure_ascii=False)},
        ],
        "temperature": 0,
        "enable_thinking": False,
        "response_format": {"type": "json_object"},
    }
    timeout = min(settings.timeout_seconds, 25.0)
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout, connect=min(timeout, 10.0)),
                          trust_env=settings.trust_env) as client:
            response = client.post(
                settings.base_url + "/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
            response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return json.loads(content)
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
        raise InterpretationUnavailable("AI 辅助解读暂不可用") from exc


def _stream_system_prompt(view: dict[str, Any]) -> str:
    decision_code = view["decision"]["code"]
    scenario = SUMMARY_SCENARIOS.get(decision_code)
    if scenario is None:
        raise InterpretationUnavailable("系统建议类型暂不支持 AI 总结")
    label = scenario["label"]
    if (decision_code in {"accept", "conditional_accept"}
            and view["decision"].get("pricing_recommendation") == "surcharge"):
        label += "（加费）"
    return stage_prompt(
        "report_summary",
        DECISION_LABEL=label,
        SCENARIO_GUIDANCE=stage_prompt(f"report_summary_{decision_code}"),
    )


def validate_stream_summary(summary: str, view: dict[str, Any]) -> str:
    """Check the saved decision and forbidden claims without imposing a layout."""

    scenario = SUMMARY_SCENARIOS.get(view["decision"]["code"])
    if scenario is None:
        raise ValueError("unsupported decision")
    summary = summary.strip()
    if not summary:
        raise ValueError("summary is empty")
    label = scenario["label"]
    if (view["decision"]["code"] in {"accept", "conditional_accept"}
            and view["decision"].get("pricing_recommendation") == "surcharge"):
        label += "（加费）"
    if label not in summary[:200]:
        raise ValueError("summary does not match the saved decision")
    if any(phrase in summary for phrase in FORBIDDEN_PHRASES):
        raise ValueError("summary contains an unsupported insurance claim")
    return summary


def _request_model_stream(view: dict[str, Any], settings: VlmSettings,
                          report_text: str) -> Iterator[str]:
    """Summarize the exported report and yield provider text deltas."""

    system = _stream_system_prompt(view)
    body = {
        "model": settings.model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps({
                "saved_decision": view["decision"],
                "decision_basis": view["basis"][:3],
                "report": report_text,
            }, ensure_ascii=False)},
        ],
        "temperature": 0,
        "enable_thinking": False,
        "stream": True,
    }
    timeout = min(settings.timeout_seconds, 60.0)
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout, connect=min(timeout, 10.0)),
                          trust_env=settings.trust_env) as client, client.stream(
            "POST", settings.base_url + "/chat/completions",
            headers={"Authorization": f"Bearer {settings.api_key}",
                     "Content-Type": "application/json"},
            json=body,
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if raw == "[DONE]":
                    break
                if not raw:
                    continue
                packet = json.loads(raw)
                if not isinstance(packet, dict):
                    continue
                if packet.get("error"):
                    raise InterpretationUnavailable("AI 摘要生成中断，请重试")
                choices = packet.get("choices") or []
                if not choices:
                    continue
                delta = (choices[0].get("delta") or {}).get("content")
                if isinstance(delta, str) and delta:
                    yield delta
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
        raise InterpretationUnavailable("AI 摘要生成中断，请重试") from exc


def stream_interpretation(case: UnderwritingCase, *, review_revision: str = "",
                          force_refresh: bool = False,
                          cache: InterpretationCache | None = None) -> Iterator[str]:
    """Start only on request; stream NDJSON deltas and cache completed summaries."""

    if case.decision is None:
        raise InterpretationUnavailable("尚未形成系统建议")
    cases = CaseRepository()
    if cases.database_path.exists():
        stored = cases.get_case(case.case_id)
        if stored and stored.get("review_history"):
            raise InterpretationUnavailable("本案已有人工审核结论，请以人工复核记录为准")
    try:
        settings = VlmSettings.from_environment().for_stage("summary")
    except (ProviderConfigurationError, OSError, ValueError) as exc:
        raise InterpretationUnavailable("AI 摘要未配置") from exc
    view = build_report_view(case)
    raw = (f"{STREAM_PROMPT_VERSION}\n{settings.model}\n{review_revision}\n"
           f"{_stream_system_prompt(view)}\n{case.model_dump_json()}")
    fingerprint = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    repository = cache or InterpretationCache()
    cached = None if force_refresh else repository.get(fingerprint)

    def event(kind: str, **data: Any) -> str:
        return json.dumps({"type": kind, **data}, ensure_ascii=False) + "\n"

    def generate() -> Iterator[str]:
        if cached and isinstance(cached.get("payload", {}).get("summary"), str):
            yield event("token", text=cached["payload"]["summary"])
            yield event("done", cached=True)
            return
        summary = ""
        try:
            for token in _request_model_stream(view, settings, generate_markdown_report(case)):
                summary += token
                if len(summary) > 1500:
                    raise InterpretationUnavailable("AI 摘要过长，请重试")
                if any(phrase in summary for phrase in FORBIDDEN_PHRASES):
                    raise InterpretationUnavailable("AI 摘要未通过内容校验，请查看原报告")
                yield event("token", text=token)
            try:
                summary = validate_stream_summary(summary, view)
            except ValueError:
                raise InterpretationUnavailable("AI 摘要未通过内容校验，请查看原报告")
            repository.save(fingerprint, case.case_id, settings.model,
                            {"decision_code": view["decision"]["code"],
                             "summary": summary})
            yield event("done", cached=False)
        except InterpretationUnavailable as exc:
            yield event("error", message=str(exc))

    return generate()


class InterpretationCache:
    """Small local cache; stores generated points, not source documents or secrets."""

    def __init__(self, path: str | Path = "outputs/report-interpretations.sqlite3") -> None:
        self.path = Path(path)

    def _open(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS interpretations (
                fingerprint TEXT PRIMARY KEY,
                case_id TEXT NOT NULL,
                model TEXT NOT NULL,
                generated_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            )
        """)
        return connection

    def get(self, fingerprint: str) -> dict[str, Any] | None:
        with closing(self._open()) as connection:
            row = connection.execute(
                "SELECT model, generated_at, payload_json FROM interpretations WHERE fingerprint = ?",
                (fingerprint,),
            ).fetchone()
        if row is None:
            return None
        return {
            "model": row[0], "generated_at": row[1],
            "payload": json.loads(row[2]), "cached": True,
            "fingerprint": fingerprint,
        }

    def save(self, fingerprint: str, case_id: str, model: str,
             payload: dict[str, Any]) -> dict[str, Any]:
        generated_at = datetime.now(UTC).isoformat()
        with closing(self._open()) as connection, connection:
            connection.execute(
                """INSERT OR REPLACE INTO interpretations
                    (fingerprint, case_id, model, generated_at, payload_json)
                    VALUES (?, ?, ?, ?, ?)""",
                (fingerprint, case_id, model, generated_at,
                 json.dumps(payload, ensure_ascii=False)),
            )
        return {
            "model": model, "generated_at": generated_at,
            "payload": payload, "cached": False,
            "fingerprint": fingerprint,
        }


def generate_interpretation(case: UnderwritingCase, *, review_revision: str = "",
                            cache: InterpretationCache | None = None) -> dict[str, Any]:
    """Generate or reuse interpretation; callers must keep report rendering independent."""

    if case.decision is None:
        raise InterpretationUnavailable("尚未形成系统建议")
    cases = CaseRepository()
    if cases.database_path.exists():
        stored = cases.get_case(case.case_id)
        if stored and stored.get("review_history"):
            raise InterpretationUnavailable("本案已有人工审核结论，请以人工复核记录为准")
    try:
        settings = VlmSettings.from_environment()
    except (ProviderConfigurationError, OSError, ValueError) as exc:
        raise InterpretationUnavailable("AI 辅助解读未配置") from exc
    fingerprint = report_fingerprint(case, model=settings.model,
                                     review_revision=review_revision)
    repository = cache or InterpretationCache()
    view = build_report_view(case)
    cached = repository.get(fingerprint)
    if cached is not None:
        return {**cached, "references": _model_input(view)["references"]}
    try:
        payload = validate_interpretation(_request_model(view, settings), view)
    except (ValidationError, ValueError) as exc:
        raise InterpretationUnavailable("AI 辅助解读未通过证据校验") from exc
    saved = repository.save(fingerprint, case.case_id, settings.model, payload)
    return {**saved, "references": _model_input(view)["references"]}
