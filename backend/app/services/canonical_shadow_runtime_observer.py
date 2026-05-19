from __future__ import annotations

import json
import re
from time import perf_counter
from typing import Any

from app.core.config import settings
from app.core.structured_logging import emit_structured_log
from app.core.supabase_client import get_supabase_service_client
from app.services.enterprise_telemetry import track_shadow_runtime_observed
from app.services.canonical_analytical_contract_adapter import get_selected_candidate_dataframe
from app.services.canonical_shadow_query_runner import (
    CanonicalShadowQueryExecution,
    run_canonical_shadow_query_for_uploaded_file,
    summarize_canonical_shadow_query_execution,
)


_TABULAR_EXTENSIONS = {"csv", "xlsx", "xls"}


def is_canonical_shadow_traffic_mirror_enabled() -> bool:
    return settings.CANONICAL_SHADOW_TRAFFIC_MIRROR_ENABLED


def _safe_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _safe_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _normalize_text(value: Any) -> str:
    return str(value or "").replace("\x00", "").strip()


def _coerce_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _coerce_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _extension(file_name: str) -> str:
    normalized = _normalize_text(file_name).lower()
    if "." not in normalized:
        return ""
    return normalized.rsplit(".", 1)[-1]


def _is_tabular_file_name(file_name: str) -> bool:
    return _extension(file_name) in _TABULAR_EXTENSIONS


def _canonical_visual_type(value: Any) -> str | None:
    normalized = _normalize_text(value).lower()
    if not normalized:
        return None
    aliases = {
        "bar": "bar_chart",
        "line": "line_chart",
        "pie": "pie_chart",
        "donut": "donut_chart",
        "scatter": "scatter_plot",
        "funnel": "funnel_chart",
        "kpi": "kpi_card",
        "gauge": "kpi_card",
    }
    return aliases.get(normalized, normalized)


def _visual_type_from_option(option: dict[str, Any]) -> str | None:
    governance = option.get("visual_governance")
    if isinstance(governance, dict):
        for key in ("applied_visual", "requested_visual", "recommended_visual"):
            visual_type = _canonical_visual_type(governance.get(key))
            if visual_type:
                return visual_type

    series = option.get("series")
    if not isinstance(series, list) or not series:
        return None

    first_series = series[0] if isinstance(series[0], dict) else {}
    series_type = _canonical_visual_type(first_series.get("type"))
    if not series_type:
        return None
    if series_type == "pie_chart":
        radius = first_series.get("radius")
        if isinstance(radius, list) and len(radius) >= 2:
            return "donut_chart"
        return "pie_chart"
    return series_type


def _unique_visual_types(chart_options: Any) -> list[str]:
    visual_types: list[str] = []
    for option in _safe_list(chart_options):
        if not isinstance(option, dict):
            continue
        visual_type = _visual_type_from_option(option)
        if visual_type and visual_type not in visual_types:
            visual_types.append(visual_type)
    return visual_types


def _normalize_prompt(prompt: Any) -> str | None:
    raw_prompt = _normalize_text(prompt)
    if not raw_prompt:
        return None
    try:
        payload = json.loads(raw_prompt)
    except Exception:
        return raw_prompt
    if isinstance(payload, dict):
        normalized_text = _normalize_text(payload.get("text"))
        return normalized_text or raw_prompt
    return raw_prompt


def _contains_any(text: str, tokens: tuple[str, ...]) -> bool:
    return any(token in text for token in tokens)


def _is_expiry_window_prompt(normalized_prompt: str) -> bool:
    if not normalized_prompt:
        return False
    if not _contains_any(
        normalized_prompt,
        (
            "venc",
            "caduc",
            "expir",
            "por vencer",
            "a vencer",
            "vencimiento",
            "fecha de venc",
            "fecha venc",
        ),
    ):
        return False
    return bool(
        re.search(
            r"\b\d{1,3}\s*(dia|dias|day|days|semana|semanas|week|weeks|mes|meses|month|months)\b",
            normalized_prompt,
        )
    ) or _contains_any(normalized_prompt, ("pronto", "proximo", "próximo", "near term", "upcoming"))


def _has_complex_semantic_constraints(normalized_prompt: str) -> bool:
    if not normalized_prompt:
        return False

    temporal_markers = (
        "tendencia",
        "trend",
        "histor",
        "evolucion",
        "evolución",
        "mensual",
        "semanal",
        "diario",
        "trimestral",
        "anual",
        "por mes",
        "por semana",
        "por dia",
        "por día",
        "por año",
        "timeline",
        "serie temporal",
    )
    restrictive_markers = (
        "desde",
        "hasta",
        "entre",
        "compar",
        "versus",
        " vs ",
        "acumulad",
        "suma total",
        "total del top",
        "total de los top",
        "totales de los",
        "sum of top",
        "total of top",
        "filtra",
        "filtrar",
        "solo ",
        "unicamente",
        "únicamente",
        "al corte",
        "corte",
    )
    has_temporal = _contains_any(normalized_prompt, temporal_markers)
    has_restriction = _contains_any(normalized_prompt, restrictive_markers)
    has_top_n = bool(re.search(r"\btop\s*\d{1,3}\b", normalized_prompt))
    has_rollup_top_n = has_top_n and _contains_any(
        normalized_prompt,
        (
            "suma total",
            "total del top",
            "total de los top",
            "totales de los",
            "sum of top",
            "total of top",
            "acumulad",
        ),
    )

    if has_rollup_top_n:
        return True
    if has_temporal and (has_restriction or has_top_n):
        return True
    return False


def _infer_requested_visual_family(prompt: str | None, live_summary: dict[str, Any]) -> str | None:
    normalized_prompt = _normalize_text(prompt).lower()
    dimension_analysis_style = (
        " por " in f" {normalized_prompt} "
        and _contains_any(normalized_prompt, ("analisis", "análisis", "analiza", "analysis", "realiza"))
    )
    generic_visual_request = _contains_any(
        normalized_prompt,
        ("grafico", "gráfico", "chart", "visualiza", "visualizar", "grafica", "gráfica"),
    )
    visual_token_map = (
        ("funnel_chart", ("funnel", "embudo")),
        ("treemap", ("treemap", "tree map", "mapa de arbol", "mapa de árbol")),
        ("line_chart", ("line chart", "linea", "línea", "trend", "tendencia", "evolucion", "evolución")),
        ("bar_chart", ("bar chart", "barra", "barras")),
        ("donut_chart", ("donut", "rosca", "anillo")),
        ("pie_chart", ("pie", "torta", "pastel")),
        ("scatter_plot", ("scatter", "dispersion", "dispersión")),
        ("kpi_card", ("kpi", "indicador", "tarjeta", "card", "total")),
    )
    for visual_family, tokens in visual_token_map:
        candidate_tokens = tokens
        if visual_family == "kpi_card" and dimension_analysis_style:
            candidate_tokens = tuple(token for token in tokens if token != "total")
        if normalized_prompt and _contains_any(normalized_prompt, candidate_tokens):
            return visual_family
    if generic_visual_request:
        live_visual_types = [
            _canonical_visual_type(value)
            for value in _safe_list(live_summary.get("visual_types"))
        ]
        for visual_type in live_visual_types:
            if visual_type:
                return visual_type
    live_visual_types = [
        _canonical_visual_type(value)
        for value in _safe_list(live_summary.get("visual_types"))
    ]
    for visual_type in live_visual_types:
        if visual_type:
            return visual_type
    return None


def _classify_prompt_type(prompt: str | None, live_summary: dict[str, Any]) -> str:
    normalized_prompt = _normalize_text(prompt).lower()
    requested_visual = _infer_requested_visual_family(prompt, live_summary)
    explicit_chart_tokens = (
        "grafico", "gráfico", "chart", "visualiza", "visualizar", "grafica", "gráfica",
        "barra", "barras", "linea", "línea", "pie", "donut", "treemap", "scatter",
    )

    if _is_expiry_window_prompt(normalized_prompt):
        return "expiry_window_analysis"
    if _has_complex_semantic_constraints(normalized_prompt):
        return "trend_request"
    if _contains_any(normalized_prompt, ("analisis completo", "análisis completo", "complete analysis", "dashboard", "resumen ejecutivo")):
        return "complete_analysis"
    if _contains_any(normalized_prompt, ("compara", "comparar", "comparativa", "comparison", "versus", " vs ")):
        # V6.5: Smart Comparative Gate — detect multi-intent co-signals.
        # If the prompt ALSO contains temporal markers or restriction tokens,
        # it's a complex multi-intent prompt (e.g., "Evolución mensual comparando top 5").
        # These MUST be delegated to the SemanticTranslator, not the generic comparative bundle.
        _temporal_cosignals = (
            "mensual", "semanal", "diario", "trimestral", "anual",
            "evolucion", "evolución", "tendencia", "histor",
            "por mes", "por semana", "por año",
        )
        _restriction_cosignals = (
            "top ", "solo ", "exclusivamente", "unicamente", "únicamente",
            "filtrad", "especificamente", "específicamente",
        )
        has_temporal = _contains_any(normalized_prompt, _temporal_cosignals)
        has_restriction = _contains_any(normalized_prompt, _restriction_cosignals) or bool(re.search(r"\btop\s*\d", normalized_prompt))
        if has_temporal or has_restriction:
            # Multi-intent: delegate to SemanticTranslator via trend_request
            return "trend_request"
        return "comparative_analysis"
    if _contains_any(normalized_prompt, ("funnel", "embudo")):
        return "funnel_request"
    if _contains_any(normalized_prompt, explicit_chart_tokens) and requested_visual in {
        "bar_chart", "line_chart", "pie_chart", "donut_chart", "treemap", "scatter_plot", "kpi_card", "funnel_chart"
    }:
        return "chart_request"
    if _contains_any(normalized_prompt, (
        "proyecc", "proyección", "proyeccion", "forecast", "predic", "predecir",
        "pronostic", "pronóstic", "futuro", "prever", "estimacion", "estimación",
    )):
        return "predictive_analysis"
    if _contains_any(normalized_prompt, ("tendencia", "trend", "histor", "evolucion", "evolución")):
        return "trend_request"
    # V6.5: Temporal grain markers → trend_request (ANTES del catch-all " por ")
    # Prompts como "Evolución mensual de gastos por tipo" deben ser trend_request
    # para que el SemanticTranslator genere planes multi-intención ricos,
    # no un dimension_analysis genérico que pierde el eje temporal.
    if _contains_any(normalized_prompt, (
        "mensual", "semanal", "diario", "trimestral", "anual",
        "por mes", "por semana", "por dia", "por día", "por año",
        "mes a mes", "year over year", "yoy", "mom",
    )):
        return "trend_request"
    if " por " in f" {normalized_prompt} ":
        return "dimension_analysis"
    if re.search(r"\btop\s*\d", normalized_prompt) or _contains_any(
        normalized_prompt,
        ("mayores", "mejores", "menores", "peores", "principales", "ranking", "mayor", "menor"),
    ):
        return "dimension_analysis"
    if _contains_any(normalized_prompt, ("kpi", "indicador", "tarjeta", "card", "cuanto", "cuánto")):
        return "kpi_request"
    if _contains_any(normalized_prompt, ("analiza", "analyze", "analisis", "análisis", "analysis")):
        return "generic_analysis"
    if _contains_any(normalized_prompt, ("distribu", "desglose", "breakdown", "composicion", "composición")):
        return "dimension_analysis"
    return "other"


def _fetch_uploaded_file_row(service_client: Any, file_id: str) -> dict[str, Any] | None:
    response = (
        service_client.table("uploaded_files")
        .select("id, user_id, team_id, file_name, storage_path, created_at")
        .eq("id", file_id)
        .single()
        .execute()
    )
    return dict(response.data or {}) if getattr(response, "data", None) else None


def build_live_runtime_summary(
    *,
    status: str,
    prompt: str | None,
    final_struct: dict[str, Any] | None,
    dataset_contract: dict[str, Any] | None,
    live_duration_ms: int | None = None,
) -> dict[str, Any]:
    payload = _safe_dict(final_struct)
    contract = _safe_dict(dataset_contract)
    chart_options = _safe_list(payload.get("chart_options"))
    metrics_payload = _safe_dict(payload.get("metrics"))
    data_payload = payload.get("data")
    explainability_payload = _safe_list(payload.get("explainability"))
    recommendations = _safe_list(payload.get("recommendations"))

    has_json_data = isinstance(data_payload, list) and bool(data_payload)
    has_arrow_data = bool(payload.get("arrow_data"))
    has_snapshot_data = bool(payload.get("snapshot_arrow"))

    return {
        "status": _normalize_text(status) or "unknown",
        "prompt": _normalize_prompt(prompt),
        "analysis_length": len(_normalize_text(payload.get("analysis"))),
        "chart_count": len(chart_options),
        "visual_types": _unique_visual_types(chart_options),
        "metric_keys": sorted(
            str(key)
            for key in metrics_payload.keys()
            if _normalize_text(key)
        ),
        "metric_count": len(metrics_payload),
        "recommendation_count": len(recommendations),
        "explainability_count": len(explainability_payload),
        "has_json_data": has_json_data,
        "has_arrow_data": has_arrow_data,
        "has_snapshot_data": has_snapshot_data,
        "dataset_mode": _normalize_text(contract.get("dataset_mode")) or None,
        "time_axis": _normalize_text(contract.get("time_axis")) or None,
        "date_column_count": len(_safe_list(contract.get("date_columns"))),
        "dimension_column_count": len(_safe_list(contract.get("dimension_columns"))),
        "contract_metric_count": len(_safe_list(contract.get("metric_columns"))),
        "live_duration_ms": _coerce_int(live_duration_ms),
    }


def _shadow_candidate_contract(execution: CanonicalShadowQueryExecution) -> dict[str, Any]:
    candidate_df = get_selected_candidate_dataframe(execution.pipeline_result.analytical_adapter_runtime)
    if candidate_df is None:
        return {}
    attrs = getattr(candidate_df, "attrs", {}) or {}
    contract = _safe_dict(attrs.get("semantic_contract"))
    return {
        "dataset_mode": _normalize_text(contract.get("dataset_mode")) or None,
        "time_axis": _normalize_text(contract.get("time_axis")) or None,
        "metric_column_count": len(_safe_list(contract.get("metric_columns"))),
        "dimension_column_count": len(_safe_list(contract.get("dimension_columns"))),
    }


def _shadow_visual_types(shadow_summary: dict[str, Any]) -> list[str]:
    visual_types: list[str] = []
    for row in _safe_list(shadow_summary.get("executions")):
        if not isinstance(row, dict):
            continue
        if _normalize_text(row.get("status")) != "success":
            continue
        visual_type = _canonical_visual_type(row.get("chart_type"))
        if visual_type and visual_type not in visual_types:
            visual_types.append(visual_type)
    return visual_types


def _successful_shadow_executions(shadow_summary: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in _safe_list(shadow_summary.get("executions")):
        if not isinstance(row, dict):
            continue
        if _normalize_text(row.get("status")) != "success":
            continue
        rows.append(row)
    return rows


def _comparable_shadow_chart_count(shadow_summary: dict[str, Any]) -> int:
    count = 0
    for row in _successful_shadow_executions(shadow_summary):
        visual_type = _canonical_visual_type(row.get("chart_type"))
        if visual_type == "kpi_card":
            continue
        count += 1
    return count


def _primary_visual_type(visual_types: list[str]) -> str | None:
    for visual_type in visual_types:
        normalized = _canonical_visual_type(visual_type)
        if normalized:
            return normalized
    return None


def build_shadow_live_divergence_summary(
    *,
    live_summary: dict[str, Any],
    shadow_summary: dict[str, Any],
    shadow_candidate_contract: dict[str, Any],
) -> dict[str, Any]:
    live_visual_types = [str(value) for value in list(live_summary.get("visual_types") or []) if _normalize_text(value)]
    shadow_visual_types = _shadow_visual_types(shadow_summary)
    live_chart_count = _coerce_int(live_summary.get("chart_count"))
    shadow_chart_count = _coerce_int(shadow_summary.get("successful_plan_count"))
    shadow_comparable_chart_count = _comparable_shadow_chart_count(shadow_summary)
    live_dataset_mode = _normalize_text(live_summary.get("dataset_mode")) or None
    shadow_dataset_mode = _normalize_text(shadow_candidate_contract.get("dataset_mode")) or None
    live_time_axis = _normalize_text(live_summary.get("time_axis")) or None
    shadow_time_axis = _normalize_text(shadow_candidate_contract.get("time_axis")) or None
    shadow_status = _normalize_text(shadow_summary.get("shadow_query_status")) or "unknown"
    live_visual_set = {
        normalized
        for normalized in (_canonical_visual_type(value) for value in live_visual_types)
        if normalized
    }
    shadow_visual_set = {
        normalized
        for normalized in (_canonical_visual_type(value) for value in shadow_visual_types)
        if normalized
    }
    shadow_has_additive_overage = (
        shadow_status == "query_executed"
        and shadow_comparable_chart_count >= live_chart_count
        and bool(live_visual_set)
        and live_visual_set.issubset(shadow_visual_set)
    )

    mismatches: list[str] = []
    if shadow_status != "query_executed":
        mismatches.append("shadow_query_not_executed")
    if live_dataset_mode and shadow_dataset_mode and live_dataset_mode != shadow_dataset_mode:
        mismatches.append("dataset_mode_mismatch")
    if live_time_axis and shadow_time_axis and live_time_axis != shadow_time_axis:
        mismatches.append("time_axis_mismatch")
    if live_chart_count != shadow_comparable_chart_count and not shadow_has_additive_overage:
        mismatches.append("chart_count_gap")
    if live_visual_set and shadow_visual_set and not live_visual_set.intersection(shadow_visual_set):
        mismatches.append("visual_type_mismatch")

    if shadow_status == "query_executed" and not mismatches:
        alignment_grade = "high_alignment"
    elif shadow_status in {"query_executed", "partial_query_success"}:
        alignment_grade = "partial_alignment"
    else:
        alignment_grade = "low_alignment"

    return {
        "alignment_grade": alignment_grade,
        "mismatches": mismatches,
        "live_chart_count": live_chart_count,
        "shadow_chart_count": shadow_chart_count,
        "shadow_comparable_chart_count": shadow_comparable_chart_count,
        "shadow_additive_chart_overage": max(shadow_comparable_chart_count - live_chart_count, 0),
        "live_visual_types": live_visual_types,
        "shadow_visual_types": shadow_visual_types,
        "live_dataset_mode": live_dataset_mode,
        "shadow_dataset_mode": shadow_dataset_mode,
        "live_time_axis": live_time_axis,
        "shadow_time_axis": shadow_time_axis,
    }


def observe_canonical_shadow_runtime(
    *,
    task_id: str,
    file_id: str,
    prompt: str | None,
    live_summary: dict[str, Any],
    uploaded_file_row: dict[str, Any] | None = None,
    service_client: Any | None = None,
) -> dict[str, Any]:
    if not is_canonical_shadow_traffic_mirror_enabled():
        return {
            "observer_status": "disabled",
            "task_id": task_id,
            "file_id": file_id,
        }

    normalized_live_summary = _safe_dict(live_summary)
    live_status = _normalize_text(normalized_live_summary.get("status")) or "unknown"
    if live_status != "completed":
        return {
            "observer_status": "skipped_incomplete_live_status",
            "task_id": task_id,
            "file_id": file_id,
            "live_status": live_status,
        }

    service = service_client or get_supabase_service_client()
    uploaded_row = dict(uploaded_file_row) if isinstance(uploaded_file_row, dict) else _fetch_uploaded_file_row(service, file_id)
    if not uploaded_row:
        return {
            "observer_status": "missing_uploaded_file",
            "task_id": task_id,
            "file_id": file_id,
        }

    file_name = _normalize_text(uploaded_row.get("file_name"))
    if settings.CANONICAL_SHADOW_TRAFFIC_MIRROR_TABULAR_ONLY and not _is_tabular_file_name(file_name):
        return {
            "observer_status": "skipped_non_tabular",
            "task_id": task_id,
            "file_id": file_id,
            "file_name": file_name,
        }

    prompt_text = _normalize_prompt(prompt)
    prompt_type = _classify_prompt_type(prompt_text, normalized_live_summary)
    requested_visual_family = _infer_requested_visual_family(prompt_text, normalized_live_summary)
    shadow_started_at = perf_counter()
    execution = run_canonical_shadow_query_for_uploaded_file(
        file_id=file_id,
        service_client=service,
        uploaded_file_row=uploaded_row,
        prompt=prompt_text,
        prompt_type=prompt_type,
        requested_visual_family=requested_visual_family,
        max_plans=max(int(settings.CANONICAL_SHADOW_TRAFFIC_MIRROR_MAX_PLANS or 0), 1),
    )
    shadow_duration_ms = int((perf_counter() - shadow_started_at) * 1000)
    shadow_summary = summarize_canonical_shadow_query_execution(execution)
    shadow_candidate_contract = _shadow_candidate_contract(execution)
    divergence_summary = build_shadow_live_divergence_summary(
        live_summary=normalized_live_summary,
        shadow_summary=shadow_summary,
        shadow_candidate_contract=shadow_candidate_contract,
    )
    live_primary_visual = _primary_visual_type(
        [str(value) for value in list(normalized_live_summary.get("visual_types") or []) if _normalize_text(value)]
    )
    shadow_primary_visual = _primary_visual_type(
        [str(value) for value in list(divergence_summary.get("shadow_visual_types") or []) if _normalize_text(value)]
    )

    live_duration_ms = _coerce_int(normalized_live_summary.get("live_duration_ms"))
    shadow_over_live_ratio = None
    if live_duration_ms > 0:
        shadow_over_live_ratio = round(shadow_duration_ms / live_duration_ms, 4)

    observer_summary = {
        "observer_status": "observed",
        "task_id": task_id,
        "file_id": file_id,
        "file_name": file_name,
        "prompt": prompt_text,
        "prompt_type": prompt_type,
        "requested_visual_family": requested_visual_family,
        "live": normalized_live_summary,
        "shadow": {
            **shadow_summary,
            "candidate_contract": shadow_candidate_contract,
        },
        "divergence": divergence_summary,
        "latency": {
            "live_duration_ms": live_duration_ms,
            "shadow_duration_ms": shadow_duration_ms,
            "shadow_over_live_ratio": shadow_over_live_ratio,
        },
    }

    emit_structured_log(
        "canonical_shadow_runtime_observed",
        task_id=task_id,
        file_id=file_id,
        file_name=file_name,
        readiness_grade=shadow_summary.get("readiness_grade"),
        shadow_query_status=shadow_summary.get("shadow_query_status"),
        alignment_grade=divergence_summary.get("alignment_grade"),
        live_chart_count=normalized_live_summary.get("chart_count"),
        shadow_chart_count=shadow_summary.get("successful_plan_count"),
        live_duration_ms=live_duration_ms,
        shadow_duration_ms=shadow_duration_ms,
        shadow_over_live_ratio=shadow_over_live_ratio,
        prompt_type=prompt_type,
        requested_visual_family=requested_visual_family,
        live_primary_visual=live_primary_visual,
        shadow_primary_visual=shadow_primary_visual,
    )
    try:
        track_shadow_runtime_observed(
            task_id=task_id,
            file_id=file_id,
            user_id=_normalize_text(uploaded_row.get("user_id")) or None,
            team_id=_normalize_text(uploaded_row.get("team_id")) or None,
            file_name=file_name,
            readiness_grade=_normalize_text(shadow_summary.get("readiness_grade")) or None,
            shadow_query_status=_normalize_text(shadow_summary.get("shadow_query_status")) or None,
            alignment_grade=_normalize_text(divergence_summary.get("alignment_grade")) or None,
            mismatch_count=len(_safe_list(divergence_summary.get("mismatches"))),
            live_chart_count=_coerce_int(normalized_live_summary.get("chart_count")),
            shadow_chart_count=_coerce_int(shadow_summary.get("successful_plan_count")),
            shadow_duration_ms=shadow_duration_ms,
            shadow_over_live_ratio=_coerce_float(shadow_over_live_ratio),
            prompt_type=prompt_type,
            requested_visual_family=requested_visual_family,
            live_primary_visual=live_primary_visual,
            shadow_primary_visual=shadow_primary_visual,
        )
    except Exception as telemetry_error:
        emit_structured_log(
            "canonical_shadow_runtime_telemetry_error",
            level="warning",
            task_id=task_id,
            file_id=file_id,
            error=str(telemetry_error)[:240],
        )

    return observer_summary
