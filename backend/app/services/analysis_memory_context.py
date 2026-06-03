from __future__ import annotations

import json
from typing import Any

from app.core.semantic_grammar import DataFilter
from app.core.structured_logging import emit_structured_log


_PLACEHOLDER_TOKENS = {
    "context_inherited",
    "inherited_context",
    "previous_context",
    "same_as_previous",
    "same_context",
    "parent_context",
    "contextual_filter",
}


def unwrap_prompt_payload(prompt: str | None) -> tuple[str | None, str | None]:
    raw_prompt = str(prompt or "")
    try:
        parsed = json.loads(raw_prompt)
    except Exception:
        return prompt, None

    if not isinstance(parsed, dict):
        return prompt, None

    text = parsed.get("text")
    parent_id = parsed.get("parent_id")
    return (
        str(text).strip() if isinstance(text, str) and text.strip() else prompt,
        str(parent_id).strip() if parent_id else None,
    )


def _safe_json_loads(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _filter_from_payload(payload: Any, allowed_columns: set[str]) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    column = str(payload.get("column") or "").strip()
    if not column or column not in allowed_columns:
        return None
    operator = payload.get("operator") or payload.get("op") or "=="
    value = payload.get("value")
    if value is None or value == "":
        return None
    try:
        validated = DataFilter.model_validate(
            {"column": column, "operator": operator, "value": value}
        )
    except Exception:
        return None
    return validated.model_dump(mode="json")


def _dedupe_filters(filters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for item in filters:
        fingerprint = json.dumps(item, sort_keys=True, ensure_ascii=False, default=str)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        deduped.append(item)
    return deduped


def _extract_filters_from_traceability(
    traceability: dict[str, Any],
    allowed_columns: set[str],
) -> list[dict[str, Any]]:
    filters: list[dict[str, Any]] = []
    for plan in list(traceability.get("plans") or []):
        if not isinstance(plan, dict):
            continue
        for raw_filter in list(plan.get("filters") or []):
            parsed_filter = _filter_from_payload(raw_filter, allowed_columns)
            if parsed_filter:
                filters.append(parsed_filter)
        query_contract = plan.get("query_contract")
        if isinstance(query_contract, dict):
            for raw_filter in list(query_contract.get("filters") or []):
                parsed_filter = _filter_from_payload(raw_filter, allowed_columns)
                if parsed_filter:
                    filters.append(parsed_filter)
    return filters


def _extract_filters_from_chart_options(
    result_payload: dict[str, Any],
    allowed_columns: set[str],
) -> list[dict[str, Any]]:
    filters: list[dict[str, Any]] = []
    for chart_option in list(result_payload.get("chart_options") or []):
        if not isinstance(chart_option, dict):
            continue
        query_contract = chart_option.get("query_contract")
        if not isinstance(query_contract, dict):
            continue
        for raw_filter in list(query_contract.get("filters") or []):
            parsed_filter = _filter_from_payload(raw_filter, allowed_columns)
            if parsed_filter:
                filters.append(parsed_filter)
    return filters


def build_plan_semantic_context(plan: Any, schema_profile: dict[str, Any] | None = None) -> dict[str, Any]:
    intent = getattr(plan, "main_intent", None)
    if not intent:
        return {}

    schema_profile = schema_profile or {}
    aliases = getattr(plan, "column_aliases", {}) or {}

    def _role(column_name: Any) -> str | None:
        column = str(column_name or "").strip()
        if not column:
            return None
        return schema_profile.get(column, {}).get("role")

    def _serialize_filter(filter_obj: Any) -> dict[str, Any] | None:
        column = str(getattr(filter_obj, "column", "") or "").strip()
        if not column:
            return None
        return {
            "column": column,
            "operator": getattr(getattr(filter_obj, "operator", None), "value", None)
            or getattr(filter_obj, "operator", "=="),
            "value": getattr(filter_obj, "value", None),
            "label": aliases.get(column) or column,
            "role": _role(column),
        }

    filters = [
        item
        for item in (_serialize_filter(filter_obj) for filter_obj in list(getattr(intent, "filters", None) or []))
        if item
    ]
    negative_filters = [
        item
        for item in (_serialize_filter(filter_obj) for filter_obj in list(getattr(intent, "negative_filters", None) or []))
        if item
    ]
    metrics = []
    for attr in ("metric", "value_column", "plot_metric", "ranking_metric"):
        value = getattr(intent, attr, None)
        if value and value not in metrics:
            metrics.append(value)
    for value in list(getattr(intent, "metrics", None) or []):
        if value and value not in metrics:
            metrics.append(value)

    dimensions = []
    for attr in ("dimension", "date_column", "split_dimension"):
        value = getattr(intent, attr, None)
        if value and value not in dimensions:
            dimensions.append(value)
    for value in list(getattr(intent, "group_by", None) or []):
        if value and value not in dimensions:
            dimensions.append(value)

    return {
        "intent_type": getattr(intent, "type", None),
        "title": getattr(plan, "title", None),
        "filters": filters,
        "negative_filters": negative_filters,
        "metrics": [str(value) for value in metrics],
        "dimensions": [str(value) for value in dimensions],
        "time_axis": getattr(intent, "date_column", None),
        "split_dimension": getattr(intent, "split_dimension", None),
        "grain": getattr(intent, "grain", None),
        "visual_protocol": getattr(getattr(intent, "visual_protocol", None), "value", None)
        or getattr(intent, "visual_protocol", None),
    }


def build_plan_query_contract(plan: Any, schema_profile: dict[str, Any] | None = None) -> dict[str, Any]:
    context = build_plan_semantic_context(plan, schema_profile)
    if not context:
        return {}
    return {
        key: value
        for key, value in {
            "intent_type": context.get("intent_type"),
            "visual_protocol": context.get("visual_protocol"),
            "metric": (context.get("metrics") or [None])[0],
            "metrics": context.get("metrics") or None,
            "dimension": (context.get("dimensions") or [None])[0],
            "dimensions": context.get("dimensions") or None,
            "time_axis": context.get("time_axis"),
            "split_dimension": context.get("split_dimension"),
            "grain": context.get("grain"),
            "filters": context.get("filters") or None,
            "negative_filters": context.get("negative_filters") or None,
            "title": context.get("title"),
        }.items()
        if value not in (None, [], {})
    }


def build_result_semantic_context(
    *,
    plans: list[Any],
    schema_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    plan_contexts = [
        context
        for context in (build_plan_semantic_context(plan, schema_profile) for plan in plans)
        if context
    ]
    filters = _dedupe_filters([
        item
        for context in plan_contexts
        for item in list(context.get("filters") or [])
        if isinstance(item, dict)
    ])
    return {
        "plan_count": len(plan_contexts),
        "filters": filters,
        "plans": plan_contexts,
    }


def load_parent_analysis_context(
    *,
    service_client: Any,
    parent_task_id: str | None,
    file_id: str,
    columns: list[str],
) -> dict[str, Any] | None:
    if not service_client or not parent_task_id:
        return None

    try:
        response = (
            service_client.table("analysis_tasks")
            .select("id,file_id,prompt,results_json")
            .eq("id", parent_task_id)
            .single()
            .execute()
        )
    except Exception as error:
        emit_structured_log(
            "analysis_parent_context_load_error",
            level="warning",
            parent_task_id=parent_task_id,
            file_id=file_id,
            error=str(error)[:240],
        )
        return None

    row = dict(getattr(response, "data", None) or {})
    if not row:
        return None
    if str(row.get("file_id") or "") != str(file_id):
        emit_structured_log(
            "analysis_parent_context_rejected",
            level="warning",
            parent_task_id=parent_task_id,
            file_id=file_id,
            reason="file_mismatch",
        )
        return None

    result_payload = _safe_json_loads(row.get("results_json"))
    traceability = result_payload.get("traceability") if isinstance(result_payload.get("traceability"), dict) else {}
    semantic_context = (
        traceability.get("semantic_context")
        if isinstance(traceability.get("semantic_context"), dict)
        else {}
    )
    allowed_columns = set(columns or [])
    filters = []
    for raw_filter in list(semantic_context.get("filters") or []):
        parsed_filter = _filter_from_payload(raw_filter, allowed_columns)
        if parsed_filter:
            filters.append(parsed_filter)
    filters.extend(_extract_filters_from_traceability(traceability, allowed_columns))
    filters.extend(_extract_filters_from_chart_options(result_payload, allowed_columns))
    filters = _dedupe_filters(filters)

    actual_parent_prompt, _ = unwrap_prompt_payload(str(row.get("prompt") or ""))
    return {
        "parent_task_id": str(parent_task_id),
        "parent_prompt": actual_parent_prompt or "",
        "filters": filters,
        "semantic_context": semantic_context,
    }


def build_parent_memory_context_text(parent_context: dict[str, Any] | None) -> str:
    if not parent_context:
        return ""
    payload = {
        "parent_task_id": parent_context.get("parent_task_id"),
        "parent_prompt": parent_context.get("parent_prompt"),
        "available_filters": parent_context.get("filters") or [],
        "semantic_context": parent_context.get("semantic_context") or {},
    }
    return (
        "CONTEXTO_ANALITICO_PREVIO_DISPONIBLE_JSON:\n"
        f"{json.dumps(payload, ensure_ascii=False, default=str)}\n"
        "REGLA: usa este contexto solo si el nuevo pedido se refiere al análisis anterior. "
        "Si decides heredarlo, emite filtros concretos con columnas y valores reales; "
        "nunca emitas marcadores abstractos como context_inherited."
    )


def _value_has_placeholder(value: Any) -> bool:
    if isinstance(value, list):
        return any(_value_has_placeholder(item) for item in value)
    normalized = str(value or "").strip().lower()
    return normalized in _PLACEHOLDER_TOKENS


def apply_parent_context_to_placeholder_filters(
    *,
    plans: list[Any],
    parent_context: dict[str, Any] | None,
) -> list[Any]:
    if not plans:
        return plans

    inherited_filters = [
        DataFilter.model_validate(item)
        for item in list((parent_context or {}).get("filters") or [])
        if isinstance(item, dict)
    ]

    for plan in plans:
        intent = getattr(plan, "main_intent", None)
        if not intent:
            continue
        clean_filters = []
        placeholders_found = False
        for filter_obj in list(getattr(intent, "filters", None) or []):
            if _value_has_placeholder(getattr(filter_obj, "value", None)):
                placeholders_found = True
                continue
            clean_filters.append(filter_obj)
        if placeholders_found:
            existing_columns = {str(getattr(item, "column", "") or "") for item in clean_filters}
            for inherited_filter in inherited_filters:
                if inherited_filter.column not in existing_columns:
                    clean_filters.append(inherited_filter)
            intent.filters = clean_filters
            emit_structured_log(
                "analysis_parent_context_placeholder_resolved",
                plan_title=getattr(plan, "title", None),
                inherited_filter_count=len(inherited_filters),
            )
    return plans
