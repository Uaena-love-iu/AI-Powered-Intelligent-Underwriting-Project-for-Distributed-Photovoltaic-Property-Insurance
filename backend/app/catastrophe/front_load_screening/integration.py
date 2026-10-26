"""Connect the front-load screen to a complete underwriting case."""

from copy import deepcopy
from typing import Any

from app.contracts import ComponentProfile

from .engine import evaluate


def assess_case_front_load(
    *,
    case_id: str,
    project_model: str | None,
    screening_input: dict[str, Any] | None,
    component: ComponentProfile | None,
) -> dict[str, Any]:
    """Use exact-model workbench evidence where available, then run the screen.

    Wind, snow, and installation geometry remain explicit source-backed inputs;
    they are never inferred from unlike weather or static-load measurements.
    """

    payload = deepcopy(screening_input) if isinstance(screening_input, dict) else {}
    supplied = payload.get("manufacturer")
    if supplied is not None and not isinstance(supplied, dict):
        supplied = None

    project_model_key = _model_key(project_model)
    supplied_model = _model_key(supplied.get("model")) if supplied else None
    workbench_model = _model_key(component.component_model) if component else None

    mismatch = None
    if supplied and project_model_key and supplied_model != project_model_key:
        mismatch = "风雪筛查厂家参数型号与案件组件完整型号不一致，已阻止计算。"
    elif supplied and workbench_model and supplied_model != workbench_model:
        mismatch = "风雪筛查厂家参数型号与组件工作台查询结果不一致，已阻止计算。"

    workbench_reference = _workbench_front_load_reference(component)
    if mismatch:
        payload.pop("manufacturer", None)
    elif workbench_reference is not None:
        wb_model, capacity, source = workbench_reference
        if supplied and (
            _model_key(supplied.get("model")) != _model_key(wb_model)
            or not _same_number(supplied.get("front_load_pa"), capacity)
        ):
            mismatch = "提交的厂家正面荷载与组件工作台的精确型号证据不一致，已阻止计算。"
            payload.pop("manufacturer", None)
        else:
            payload["manufacturer"] = {
                "brand": component.manufacturer,
                "model": component.component_model,
                "front_load_pa": capacity,
                "source": source,
            }
    elif supplied and project_model_key and supplied_model == project_model_key:
        # Manual source-backed values remain usable when the workbench has no
        # field for this exact model. The case report preserves the assertion.
        payload["manufacturer"] = supplied
    else:
        payload.pop("manufacturer", None)

    payload["project_id"] = case_id
    result = evaluate(payload)
    if mismatch:
        result["integration_issue"] = mismatch
        result["actions"].insert(0, mismatch)
    elif workbench_reference is not None:
        result["manufacturer_evidence_source"] = "component-model-workbench"
        records = component.parameter_evidence.get("front_static_load_pa", [])
        result["manufacturer_load_basis"] = ", ".join(
            dict.fromkeys(
                str(record["load_basis"])
                for record in records
                if record.get("status") == "found" and record.get("load_basis")
            )
        ) or "unspecified"
        result["manufacturer_parameter_requires_manual_review"] = any(
            record.get("requires_manual_review") for record in records
        )
        if result["manufacturer_parameter_requires_manual_review"]:
            result["actions"].insert(
                0,
                "组件工作台标记正面载荷参数需人工核验；数值比较不代表安装条件或工程承载能力已确认。",
            )
    return result


def _workbench_front_load_reference(
    component: ComponentProfile | None,
) -> tuple[str, float, str] | None:
    if (
        component is None
        or component.model_match_status != "exact"
        or component.front_static_load_pa is None
    ):
        return None

    records = component.parameter_evidence.get("front_static_load_pa", [])
    usable = [
        record
        for record in records
        if record.get("status") == "found"
        and isinstance(record.get("source_url"), str)
        and record["source_url"].strip()
    ]
    if not usable or any(record.get("status") == "conflict" for record in records):
        return None

    source = "；".join(
        str(value)
        for value in dict.fromkeys(
            part
            for record in usable
            for part in (
                record.get("document_title"),
                record.get("source_url"),
                f"第{record['page']}页" if record.get("page") else None,
                record.get("quote"),
                record.get("load_basis"),
            )
            if isinstance(part, str) and part.strip()
        )
    )
    if not source:
        return None
    return component.component_model, component.front_static_load_pa, source


def _model_key(value: object) -> str | None:
    return "".join(str(value).split()).casefold() if value else None


def _same_number(value: object, expected: float) -> bool:
    if isinstance(value, bool):
        return False
    try:
        return float(value) == expected
    except (TypeError, ValueError):
        return False
