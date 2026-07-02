from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


TRACEABILITY_SCHEMA_VERSION = "1.0"
INTERPRETATION_ENGINE_VERSION = "semantic_translator_v1"
ANALYSIS_PIPELINE_VERSION = "ibis_titanium_v1"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _compact_text(value: Any, *, max_len: int = 240) -> str:
    text = str(value or "").replace("\x00", "").replace("\n", " ").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def _humanize_column(column_name: str | None, aliases: dict[str, str]) -> str | None:
    if not column_name:
        return None
    alias = aliases.get(column_name)
    if alias:
        return alias
    text = str(column_name).replace("_", " ").strip()
    return text[:1].upper() + text[1:] if text else None


def _serialize_filter(filter_obj: Any, aliases: dict[str, str]) -> dict[str, Any]:
    column_name = str(getattr(filter_obj, "column", "") or "").strip()
    operator = str(getattr(filter_obj, "operator", "") or "").strip()
    value = getattr(filter_obj, "value", None)
    if isinstance(value, list):
        serialized_value: Any = [str(item) for item in value]
        display_value = ", ".join(serialized_value)
    else:
        serialized_value = value
        display_value = str(value)

    display_column = _humanize_column(column_name, aliases) or column_name
    return {
        "column": column_name,
        "label": display_column,
        "operator": operator,
        "value": serialized_value,
        "display": f"{display_column} {operator} {display_value}".strip(),
    }


def _extract_plan_metrics(intent: Any) -> list[str]:
    metrics: list[str] = []
    for attr in ("metric", "value_column"):
        value = getattr(intent, attr, None)
        if value and value not in metrics:
            metrics.append(str(value))
    for item in list(getattr(intent, "metrics", None) or []):
        if item and item not in metrics:
            metrics.append(str(item))
    return metrics


def _extract_plan_dimensions(intent: Any) -> list[str]:
    dimensions: list[str] = []
    for attr in ("dimension", "date_column"):
        value = getattr(intent, attr, None)
        if value and value not in dimensions:
            dimensions.append(str(value))
    for item in list(getattr(intent, "group_by", None) or []):
        if item and item not in dimensions:
            dimensions.append(str(item))
    return dimensions


def build_traceability_plan_entry(
    *,
    plan: Any,
    schema_profile: dict[str, Any] | None = None,
    query_contract: dict[str, Any] | None = None,
    execution: dict[str, Any] | None = None,
) -> dict[str, Any]:
    schema_profile = schema_profile or {}
    aliases = getattr(plan, "column_aliases", {}) or {}
    intent = getattr(plan, "main_intent", None)

    if not intent:
        return {
            "title": getattr(plan, "title", "Analisis"),
            "intent_type": "unknown",
            "visual_protocol": None,
            "aggregation": None,
            "metrics": [],
            "dimensions": [],
            "filters": [],
            "query_contract": query_contract or {},
            "execution": execution or {},
        }

    metrics = _extract_plan_metrics(intent)
    dimensions = _extract_plan_dimensions(intent)
    filters = [
        _serialize_filter(filter_obj, aliases)
        for filter_obj in list(getattr(intent, "filters", None) or [])
    ]

    metric_roles = {
        metric: schema_profile.get(metric, {}).get("role")
        for metric in metrics
        if metric in schema_profile
    }
    dimension_roles = {
        dimension: schema_profile.get(dimension, {}).get("role")
        for dimension in dimensions
        if dimension in schema_profile
    }

    visual_protocol = getattr(getattr(intent, "visual_protocol", None), "value", None) or getattr(intent, "visual_protocol", None)

    return {
        "title": getattr(plan, "title", "Analisis"),
        "intent_type": str(getattr(intent, "type", "") or "").strip() or "unknown",
        "visual_protocol": visual_protocol,
        "aggregation": getattr(intent, "aggregation", None),
        "metric_unit": getattr(intent, "metric_unit", None),
        "metric_polarity": getattr(plan, "metric_polarity", "neutral"),
        "rationale": _compact_text(getattr(intent, "rationale", "")),
        "metrics": metrics,
        "metric_labels": [_humanize_column(metric, aliases) or metric for metric in metrics],
        "metric_roles": metric_roles,
        "dimensions": dimensions,
        "dimension_labels": [_humanize_column(dimension, aliases) or dimension for dimension in dimensions],
        "dimension_roles": dimension_roles,
        "filters": filters,
        "limit": getattr(intent, "limit", None),
        "barmode": getattr(intent, "barmode", None),
        "query_contract": query_contract or {},
        "execution": execution or {},
    }


def _summarize_schema_profile(schema_profile: dict[str, Any]) -> dict[str, Any]:
    role_counts: dict[str, int] = {}
    type_counts: dict[str, int] = {}
    for info in schema_profile.values():
        if not isinstance(info, dict):
            continue
        role = str(info.get("role") or "").strip()
        dtype = str(info.get("type") or "").strip()
        if role:
            role_counts[role] = role_counts.get(role, 0) + 1
        if dtype:
            type_counts[dtype] = type_counts.get(dtype, 0) + 1
    return {
        "column_count": len(schema_profile),
        "roles": role_counts,
        "types": type_counts,
    }


def _serialize_document_sources(snippets: list[Any]) -> dict[str, Any]:
    document_map: dict[str, dict[str, Any]] = {}
    for snippet in snippets or []:
        document_id = str(getattr(snippet, "document_id", "") or "").strip()
        if not document_id:
            continue
        current = document_map.setdefault(
            document_id,
            {
                "document_id": document_id,
                "title": str(getattr(snippet, "document_title", "") or "").strip(),
                "file_name": str(getattr(snippet, "document_file_name", "") or "").strip(),
                "source_kind": str(getattr(snippet, "source_kind", "") or "").strip(),
                "chunk_indexes": [],
            },
        )
        chunk_index = getattr(snippet, "chunk_index", None)
        if isinstance(chunk_index, int):
            current["chunk_indexes"].append(chunk_index)

    sources = []
    for item in document_map.values():
        item["chunk_indexes"] = sorted(set(item["chunk_indexes"]))[:10]
        item["chunk_count"] = len(item["chunk_indexes"])
        sources.append(item)

    return {
        "context_injected": bool(sources),
        "source_count": len(sources),
        "sources": sources,
    }


def build_traceability_payload(
    *,
    task_id: str,
    file_id: str,
    user_id: str | None,
    raw_prompt: str,
    actual_prompt: str,
    parent_task_id: str | None,
    memory_decision: str,
    format_override: dict[str, Any] | None,
    schema_profile: dict[str, Any] | None,
    currency_meta: dict[str, Any] | None,
    institutional_snippets: list[Any] | None,
    plan_entries: list[dict[str, Any]],
    final_struct: dict[str, Any],
    semantic_context: dict[str, Any] | None = None,
    status: str,
    error_message: str | None = None,
) -> dict[str, Any]:
    format_override = format_override or {}
    schema_profile = schema_profile or {}
    institutional_snippets = institutional_snippets or []
    semantic_context = semantic_context or {}

    outputs = {
        "analysis_present": bool(str(final_struct.get("analysis") or "").strip()),
        "metric_count": len(final_struct.get("metrics") or {}),
        "chart_count": len(final_struct.get("chart_options") or []),
        "table_row_count": int(final_struct.get("arrow_row_count") or len(final_struct.get("data") or [])),
        "recommendation_count": len(final_struct.get("recommendations") or []),
        "explainability_count": len(final_struct.get("explainability") or []),
        "snapshot_row_count": int(final_struct.get("snapshot_row_count") or 0),
    }

    return {
        "schema_version": TRACEABILITY_SCHEMA_VERSION,
        "generated_at": _now_iso(),
        "pipeline": {
            "analysis_engine": ANALYSIS_PIPELINE_VERSION,
            "interpretation_engine": INTERPRETATION_ENGINE_VERSION,
            "status": status,
            "error": _compact_text(error_message, max_len=320) if error_message else None,
        },
        "task": {
            "task_id": task_id,
            "file_id": file_id,
            "user_id": user_id,
        },
        "request": {
            "raw_prompt": _compact_text(raw_prompt, max_len=320),
            "interpreted_prompt": _compact_text(actual_prompt, max_len=320),
            "parent_task_id": parent_task_id,
            "memory_decision": memory_decision,
            "format_override": {
                "enabled": bool(format_override.get("enabled")),
                "renderer": format_override.get("renderer"),
                "reason": _compact_text(format_override.get("reason"), max_len=180),
            },
        },
        "data_profile": {
            "schema": _summarize_schema_profile(schema_profile),
            "currency": currency_meta or {},
        },
        "documents": _serialize_document_sources(institutional_snippets),
        "semantic_context": semantic_context,
        "plans": plan_entries,
        "outputs": outputs,
    }


def summarize_history_item(*, task_row: dict[str, Any], result_payload: dict[str, Any] | None) -> dict[str, Any]:
    result_payload = result_payload or {}
    traceability = result_payload.get("traceability") if isinstance(result_payload.get("traceability"), dict) else {}
    request = traceability.get("request") if isinstance(traceability.get("request"), dict) else {}
    outputs = traceability.get("outputs") if isinstance(traceability.get("outputs"), dict) else {}
    documents = traceability.get("documents") if isinstance(traceability.get("documents"), dict) else {}
    plans = traceability.get("plans") if isinstance(traceability.get("plans"), list) else []

    intent_types = _dedupe_preserve_order([
        str(plan.get("intent_type") or "").strip()
        for plan in plans
        if isinstance(plan, dict)
    ])
    filter_scope = _dedupe_preserve_order([
        str(filter_item.get("display") or "").strip()
        for plan in plans
        if isinstance(plan, dict)
        for filter_item in list(plan.get("filters") or [])
        if isinstance(filter_item, dict)
    ])

    return {
        "task_id": str(task_row.get("id") or ""),
        "file_id": task_row.get("file_id"),
        "status": str(task_row.get("status") or ""),
        "created_at": task_row.get("created_at"),
        "prompt_preview": _compact_text(request.get("interpreted_prompt") or request.get("raw_prompt") or task_row.get("prompt"), max_len=140),
        "plan_count": len(plans),
        "intent_types": intent_types,
        "filter_scope": filter_scope[:8],
        "source_count": int(documents.get("source_count") or 0),
        "chart_count": int(outputs.get("chart_count") or 0),
        "metric_count": int(outputs.get("metric_count") or 0),
        "recommendation_count": int(outputs.get("recommendation_count") or 0),
        "format_override": (request.get("format_override") or {}).get("renderer") if isinstance(request.get("format_override"), dict) else None,
        "traceability_available": bool(traceability),
    }
