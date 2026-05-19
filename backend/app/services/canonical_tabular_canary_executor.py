from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from app.core.arrow_utils import dataframe_to_arrow_base64, records_to_arrow_base64
from app.services.canonical_analytical_contract_adapter import get_selected_candidate_dataframe
from app.services.canonical_shadow_query_runner import (
    CanonicalShadowQueryExecution,
    run_canonical_shadow_query_for_uploaded_file,
)
from app.services.chart_factory import ChartFactory
from app.services.dashboard_narrative import generate_dashboard_executive_summary
from app.services.visual_recommendation_engine import build_visual_governance, normalize_visual_id


@dataclass
class CanonicalTabularCanaryExecutionResult:
    status: str
    final_struct: dict[str, Any]
    dataset_contract: dict[str, Any]
    cleaning_notes: Any
    execution: CanonicalShadowQueryExecution


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _safe_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _safe_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _normalize_chart_type(value: Any) -> str:
    normalized = _normalize_text(value).lower()
    mapping = {
        "bar_chart": "bar",
        "line_chart": "line",
        "area_chart": "area",
        "pie_chart": "pie",
        "donut_chart": "pie",
        "treemap": "treemap",
        "scatter_plot": "scatter",
        "funnel_chart": "funnel",
        "kpi_card": "gauge",
    }
    return mapping.get(normalized, normalized or "bar")


def _candidate_attrs(execution: CanonicalShadowQueryExecution) -> dict[str, Any]:
    candidate_df = get_selected_candidate_dataframe(execution.pipeline_result.analytical_adapter_runtime)
    if candidate_df is None:
        return {}
    return getattr(candidate_df, "attrs", {}) or {}


def _analysis_line_from_kpi(title: str, data: dict[str, Any]) -> str:
    parts = []
    for key, value in data.items():
        label = str(key).replace("_", " ").strip().title()
        parts.append(f"{label}: {value}")
    joined = ", ".join(parts[:3])
    return f"## {title}\nValor calculado: {joined}."


def _analysis_line_from_hard_facts(title: str, hard_facts: dict[str, Any]) -> str:
    prioritized_keys = (
        "top_1_name",
        "top_1_val",
        "top_1_share",
        "overall_growth_pct",
        "trend",
        "peak_period",
        "peak_value",
        "total_analyzed",
        "stage_count",
    )
    parts: list[str] = []
    for key in prioritized_keys:
        if key not in hard_facts:
            continue
        label = str(key).replace("_", " ").strip().title()
        parts.append(f"{label}: {hard_facts[key]}")
    if not parts:
        return f"## {title}\nResultado analítico generado por Universal Pipeline."
    return f"## {title}\nHallazgo: {' | '.join(parts[:4])}."


def _safe_metric_unit(plan: Any) -> str | None:
    metric_unit = getattr(getattr(plan, "main_intent", None), "metric_unit", None)
    return getattr(metric_unit, "value", metric_unit) if metric_unit is not None else None


def _try_records_to_arrow_base64(rows: list[dict[str, Any]]) -> str | None:
    try:
        return records_to_arrow_base64(rows)
    except (ModuleNotFoundError, ValueError):
        return None


def _try_dataframe_to_arrow_base64(df: pd.DataFrame) -> str | None:
    try:
        return dataframe_to_arrow_base64(df)
    except (ModuleNotFoundError, ValueError):
        return None


def _build_aggregated_data_summary(data: list[Any], *, max_items: int = 12) -> str | None:
    """Build a compact summary of aggregated chart data for narrative context.

    DuckDB already reduced 200K+ rows to a small grouped table (5-15 rows) for
    chart rendering.  We serialize that tiny table as a one-liner so Gemini can
    narrate the full temporal/dimensional scope without needing raw data.

    Also handles PredictiveEngine output where the forecast type is stored in
    extra_info.type (IbisEngine format: {name, value, extra_info: {type: "forecast"}}).
    """
    if not data:
        return None

    def _row_type(row: dict) -> str | None:
        """Extract the forecast/history type from either root or extra_info."""
        if row.get("type"):
            return str(row["type"])
        ei = row.get("extra_info")
        if isinstance(ei, dict) and ei.get("type"):
            return str(ei["type"])
        return None

    def _row_ci(row: dict) -> tuple[Any, Any]:
        """Extract confidence interval from either root or extra_info."""
        ei = row.get("extra_info") or {}
        lower = row.get("lower_ci") or (ei.get("lower_ci") if isinstance(ei, dict) else None)
        upper = row.get("upper_ci") or (ei.get("upper_ci") if isinstance(ei, dict) else None)
        return lower, upper

    # Detect predictive format: type == "forecast" at root or in extra_info
    has_forecast = any(
        isinstance(row, dict) and _row_type(row) == "forecast"
        for row in data
    )

    if has_forecast:
        history = [r for r in data if isinstance(r, dict) and _row_type(r) == "history"]
        forecast = [r for r in data if isinstance(r, dict) and _row_type(r) == "forecast"]
        parts: list[str] = []

        # Use 'name' (IbisEngine format) or 'date' (PredictiveEngine raw format)
        def _label(r: dict) -> str:
            return str(r.get("name") or r.get("date") or "?")

        if history:
            tail = history[-4:]
            h_pairs = [f"{_label(r)}={r.get('value')}" for r in tail]
            parts.append(f"Histórico reciente: {', '.join(h_pairs)}")
        if forecast:
            f_pairs = [f"{_label(r)}={r.get('value')}" for r in forecast[:6]]
            lower, upper = _row_ci(forecast[0])
            ci_text = ""
            if lower is not None and upper is not None:
                ci_text = f" (intervalo confianza: {lower}-{upper})"
            parts.append(f"PROYECCIÓN FUTURA: {', '.join(f_pairs)}{ci_text}")
        return " | ".join(parts) if parts else None

    # Standard chart format: {name, value}
    rows = [row for row in data if isinstance(row, dict) and row.get("name") is not None and row.get("value") is not None]
    if not rows:
        return None
    bounded = rows[:max_items]
    pairs = [f"{row['name']}={row['value']}" for row in bounded]
    suffix = f" (+{len(rows) - max_items} más)" if len(rows) > max_items else ""
    return f"Datos: {', '.join(pairs)}{suffix}"


def _build_widget_facts(title: str, result_payload: dict[str, Any]) -> list[str]:
    facts: list[str] = []
    hard_facts = _safe_dict(result_payload.get("hard_facts"))

    if hard_facts.get("top_1_name") is not None and hard_facts.get("top_1_val") is not None:
        share_text = f" ({hard_facts.get('top_1_share')}% del total)" if hard_facts.get("top_1_share") is not None else ""
        facts.append(
            f"{title}: líder {hard_facts.get('top_1_name')} con valor {hard_facts.get('top_1_val')}{share_text}."
        )
    if hard_facts.get("overall_growth_pct") is not None:
        trend_text = f" tendencia {hard_facts.get('trend')}." if hard_facts.get("trend") else "."
        facts.append(f"{title}: cambio total {hard_facts.get('overall_growth_pct')}% con{trend_text}")
    if hard_facts.get("peak_period") is not None and hard_facts.get("peak_value") is not None:
        facts.append(f"{title}: pico en {hard_facts.get('peak_period')} con valor {hard_facts.get('peak_value')}.")
    if hard_facts.get("total_analyzed") is not None:
        facts.append(f"{title}: universo analizado {hard_facts.get('total_analyzed')}.")
    # [V4] Per-series stats for multi-series charts
    series_stats = hard_facts.get("series_stats")
    if series_stats and isinstance(series_stats, list):
        for ss in series_stats[:5]:
            s_name = ss.get("name", "?")
            s_growth = ss.get("growth_pct")
            s_trend = ss.get("trend", "")
            s_peak = ss.get("peak_period")
            if s_growth is not None:
                peak_text = f", pico en {s_peak}" if s_peak else ""
                facts.append(
                    f"{title}: serie '{s_name}' {s_growth}% ({s_trend}{peak_text})."
                )
    # Forecast-specific hard_facts from PredictiveEngine
    if hard_facts.get("forecast_points") is not None:
        facts.append(
            f"{title}: proyección genera {hard_facts.get('forecast_points')} periodos futuros "
            f"sobre {hard_facts.get('total_points', '?')} puntos totales (histórico+forecast)."
        )

    # ── Post-Aggregation Fact Feeding ──────────────────────────────────
    # Inject the compact aggregated data[] that DuckDB already computed
    # so Gemini can narrate the FULL temporal/dimensional scope, not just
    # the truncated snapshot.  Cost: ~200 chars, 0 bytes extra memory.
    chart_data = _safe_list(result_payload.get("data"))
    data_summary = _build_aggregated_data_summary(chart_data)
    if data_summary:
        facts.append(f"{title}: {data_summary}")

    # Predictive widgets need more facts (history + forecast)
    max_facts = 5 if hard_facts.get("forecast_points") is not None else 4
    if facts:
        return facts[:max_facts]

    for row in chart_data[:3]:
        if not isinstance(row, dict):
            continue
        if row.get("name") is not None and row.get("value") is not None:
            facts.append(f"{title}: {row.get('name')} = {row.get('value')}.")
        elif row.get("date") is not None and row.get("value") is not None:
            facts.append(f"{title}: {row.get('date')} = {row.get('value')} ({row.get('type', 'data')}).")
        elif row:
            preview = ", ".join(f"{key}: {value}" for key, value in list(row.items())[:3])
            if preview:
                facts.append(f"{title}: {preview}.")
    return facts[:max_facts]


def _build_summary_widgets(
    *,
    execution: CanonicalShadowQueryExecution,
    chart_options: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    widgets: list[dict[str, Any]] = []
    for index, (plan, result_payload) in enumerate(zip(execution.plans, execution.execution_results), start=1):
        if not isinstance(result_payload, dict) or result_payload.get("error"):
            continue

        title = _normalize_text(result_payload.get("title")) or plan.title or f"Widget {index}"
        result_type = _normalize_text(result_payload.get("type")).lower()
        visual_type = normalize_visual_id(result_payload.get("chart_type"))
        if result_type == "kpi":
            visual_type = "gauge_chart"

        intent = getattr(plan, "main_intent", None)
        widgets.append(
            {
                "title": title,
                "widget_type": result_type or "widget",
                "visual_type": visual_type,
                "metric": getattr(intent, "metric", None)
                or getattr(intent, "value_column", None)
                or (_safe_list(getattr(intent, "metrics", None))[0] if _safe_list(getattr(intent, "metrics", None)) else None),
                "dimension": getattr(intent, "dimension", None) or getattr(intent, "date_column", None),
                "aggregation": getattr(intent, "aggregation", None) or ("sum" if result_type == "kpi" else None),
                "file_id": execution.metadata.get("file_id"),
                "facts": _build_widget_facts(title, result_payload),
            }
        )
    return widgets


def _format_executive_summary(summary: dict[str, Any]) -> str:
    headline = _normalize_text(summary.get("headline")) or "Resumen ejecutivo"
    overview = _normalize_text(summary.get("overview"))
    findings = [str(value).strip() for value in list(summary.get("key_findings") or []) if str(value or "").strip()]
    risks = [str(value).strip() for value in list(summary.get("risks") or []) if str(value or "").strip()]
    actions = [str(value).strip() for value in list(summary.get("actions") or []) if str(value or "").strip()]
    caveats = [str(value).strip() for value in list(summary.get("caveats") or []) if str(value or "").strip()]

    blocks: list[str] = [f"## {headline}"]
    if overview:
        blocks.append(overview)
    if findings:
        blocks.append("**Hallazgos clave**\n" + "\n".join(f"- {entry}" for entry in findings[:3]))
    if actions:
        blocks.append("**Acciones sugeridas**\n" + "\n".join(f"- {entry}" for entry in actions[:3]))
    if risks:
        blocks.append("**Riesgos**\n" + "\n".join(f"- {entry}" for entry in risks[:2]))
    if caveats:
        blocks.append("**Caveats**\n" + "\n".join(f"- {entry}" for entry in caveats[:2]))
    return "\n\n".join(blocks).strip()


def _build_chart_option(
    *,
    plan: Any,
    title: str,
    result_payload: dict[str, Any],
    currency_meta: dict[str, Any],
) -> dict[str, Any] | None:
    chart_type = _normalize_chart_type(result_payload.get("chart_type"))
    ui_chart_type = normalize_visual_id(result_payload.get("chart_type"))
    option = ChartFactory.create_chart(
        chart_type,
        title,
        result_payload.get("data"),
        x_label=result_payload.get("x_axis"),
        y_label=result_payload.get("y_axis"),
        currency_meta=currency_meta or None,
        barmode=result_payload.get("barmode"),
    )
    if not isinstance(option, dict) or option.get("error"):
        return None
    visual_governance = build_visual_governance(
        plan,
        result_payload,
        ui_chart_type,
        requested_visual_locked=False,
    )
    safe_metric_unit = _safe_metric_unit(plan)
    option["visual_source_payload"] = {
        "title": title,
        "chart_type": ui_chart_type,
        "requested_chart_type": visual_governance.get("requested_visual") or ui_chart_type,
        "rows": _safe_list(result_payload.get("data")),
        "x_label": result_payload.get("x_axis"),
        "y_label": result_payload.get("y_axis"),
        "barmode": result_payload.get("barmode"),
        "metric_unit": safe_metric_unit,
    }
    option["visual_governance"] = visual_governance

    filtered_granular_df = result_payload.get("filtered_granular_df")
    if isinstance(filtered_granular_df, pd.DataFrame) and not filtered_granular_df.empty:
        granular_arrow = _try_dataframe_to_arrow_base64(filtered_granular_df)
        if granular_arrow and len(granular_arrow) <= 4 * 1024 * 1024:
            option["granular_arrow"] = granular_arrow
    return option


def _build_final_struct(execution: CanonicalShadowQueryExecution) -> tuple[dict[str, Any], dict[str, Any], Any]:
    attrs = _candidate_attrs(execution)
    candidate_df = get_selected_candidate_dataframe(execution.pipeline_result.analytical_adapter_runtime)
    dataset_contract = _safe_dict(attrs.get("semantic_contract"))
    currency_meta = _safe_dict(attrs.get("currency_meta"))
    cleaning_notes = attrs.get("cleaning_notes", [])
    final_struct: dict[str, Any] = {
        "analysis": "",
        "metrics": {},
        "chart_options": [],
        "data": [],
        "explainability": [],
        "traceability": {
            "runtime": "canonical_tabular_canary",
            "prompt_strategy": execution.prompt_strategy,
            "candidate_id": execution.metadata.get("candidate_id"),
            "plan_count": len(execution.plans),
            "dataset_mode": dataset_contract.get("dataset_mode"),
            "time_axis": dataset_contract.get("time_axis"),
        },
    }

    analysis_blocks: list[str] = []
    for plan, result_payload in zip(execution.plans, execution.execution_results):
        if not isinstance(result_payload, dict):
            continue
        if result_payload.get("error"):
            continue
        title = _normalize_text(result_payload.get("title")) or plan.title or "Resultado"
        result_type = _normalize_text(result_payload.get("type")).lower()
        hard_facts = _safe_dict(result_payload.get("hard_facts"))

        if result_type == "kpi":
            metrics_payload = _safe_dict(result_payload.get("data"))
            final_struct["metrics"].update(metrics_payload)
            analysis_blocks.append(_analysis_line_from_kpi(title, metrics_payload))
            continue

        if result_type == "echarts":
            option = _build_chart_option(
                plan=plan,
                title=title,
                result_payload=result_payload,
                currency_meta=currency_meta,
            )
            if option:
                final_struct["chart_options"].append(option)
            if not final_struct["data"] and isinstance(result_payload.get("data"), list):
                final_struct["data"] = _safe_list(result_payload.get("data"))
            analysis_blocks.append(_analysis_line_from_hard_facts(title, hard_facts))
            final_struct["explainability"].append(
                {
                    "title": title,
                    "intent_type": getattr(plan.main_intent, "type", None),
                    "visual_protocol": str(
                        getattr(getattr(plan, "main_intent", None), "visual_protocol", None) or ""
                    )
                    or None,
                    "hard_facts": hard_facts,
                }
            )

    if not analysis_blocks:
        raise RuntimeError(f"canonical_canary_empty_result:{execution.metadata.get('shadow_query_status')}")

    if final_struct["data"]:
        arrow_data = _try_records_to_arrow_base64(_safe_list(final_struct["data"]))
        if arrow_data and len(arrow_data) <= 4 * 1024 * 1024:
            final_struct["arrow_data"] = arrow_data
        final_struct["arrow_row_count"] = len(_safe_list(final_struct["data"]))

    # --- Big Data Shield: Lightweight Snapshot Serialization ---
    # El motor analítico SIEMPRE procesa el 100% de los datos.
    # Pero la serialización Arrow del snapshot se trunca para evitar que
    # el payload JSON exceda el límite de PostgREST/Supabase (~6MB JSONB).
    # El Canary guarda en DB, así que el cap es más agresivo que en legacy.
    _MAX_SNAPSHOT_ROWS = 10_000
    _MAX_PAYLOAD_BYTES = 4 * 1024 * 1024  # 4MB max para PostgREST

    if isinstance(candidate_df, pd.DataFrame) and not candidate_df.empty:
        snapshot_df = candidate_df
        if "is_latest_snapshot" in candidate_df.columns:
            latest_mask = candidate_df["is_latest_snapshot"] == True
            if bool(latest_mask.any()):
                snapshot_df = candidate_df[latest_mask]

        total_snapshot_rows = len(snapshot_df)
        serialization_df = snapshot_df

        if total_snapshot_rows > _MAX_SNAPSHOT_ROWS:
            serialization_df = snapshot_df.head(_MAX_SNAPSHOT_ROWS)
            print(
                f"🛡️ [BIG DATA SHIELD] Snapshot truncado para serialización: "
                f"{total_snapshot_rows:,} → {_MAX_SNAPSHOT_ROWS:,} filas "
                f"(análisis procesó 100% de la data)"
            )

        snapshot_arrow = _try_dataframe_to_arrow_base64(serialization_df)
        if snapshot_arrow:
            # Guard de tamaño: si el Arrow base64 excede el budget, lo descartamos
            # para que el payload total no supere el límite de PostgREST.
            if len(snapshot_arrow) <= _MAX_PAYLOAD_BYTES:
                final_struct["snapshot_arrow"] = snapshot_arrow
            else:
                print(
                    f"🛡️ [PAYLOAD GUARD] snapshot_arrow descartado: "
                    f"{len(snapshot_arrow):,} bytes > {_MAX_PAYLOAD_BYTES:,} límite. "
                    f"El frontend cargará los datos desde DuckDB-WASM."
                )
        final_struct["snapshot_row_count"] = total_snapshot_rows
        final_struct["snapshot_serialized_rows"] = len(serialization_df)
        final_struct["snapshot_columns"] = list(snapshot_df.columns)

    summary_widgets = _build_summary_widgets(
        execution=execution,
        chart_options=final_struct["chart_options"],
    )

    # V6.5: Extract literal filters from ALL plans to inject into narrative context.
    # Previously global_filters={} was hardcoded, causing Gemini to write
    # "sin filtros activos" even when DuckDB had applied strict filters.
    extracted_filters: dict[str, str] = {}
    for plan in execution.plans:
        intent = getattr(plan, "main_intent", None)
        if not intent:
            continue
        for f in getattr(intent, "filters", []):
            col = str(getattr(f, "column", "") or "").strip()
            val = str(getattr(f, "value", "") or "").strip()
            op = str(getattr(f, "operator", "==") or "==").strip()
            if col and val:
                extracted_filters[col] = f"{op} {val}" if op != "==" else val

    executive_summary = generate_dashboard_executive_summary(
        presentation_name="Analisis Universal",
        global_filters=extracted_filters,
        widgets=summary_widgets,
    )
    narrative_text = _format_executive_summary(executive_summary)
    final_struct["analysis"] = narrative_text or "\n\n".join(analysis_blocks)
    final_struct["traceability"]["narrative_source"] = "dashboard_executive_summary"
    return final_struct, dataset_contract, cleaning_notes


def execute_canonical_tabular_canary_analysis(
    *,
    file_id: str,
    prompt: str | None,
    service_client: Any,
    uploaded_file_row: dict[str, Any] | None = None,
    prompt_type: str | None = None,
    requested_visual_family: str | None = None,
    max_plans: int = 3,
) -> CanonicalTabularCanaryExecutionResult:
    execution = run_canonical_shadow_query_for_uploaded_file(
        file_id=file_id,
        service_client=service_client,
        uploaded_file_row=uploaded_file_row,
        prompt=prompt,
        prompt_type=prompt_type,
        requested_visual_family=requested_visual_family,
        max_plans=max_plans,
    )
    successful_count = sum(1 for row in execution.execution_summaries if row.get("status") == "success")
    # [V3] Relajar la puerta: aceptar partial_query_success si hay ≥1 plan exitoso.
    # _build_final_struct() ya filtra resultados con error internamente.
    if successful_count <= 0:
        raise RuntimeError(
            f"canonical_canary_not_ready:{execution.metadata.get('shadow_query_status')}:{successful_count}"
        )
    final_struct, dataset_contract, cleaning_notes = _build_final_struct(execution)
    return CanonicalTabularCanaryExecutionResult(
        status="completed",
        final_struct=final_struct,
        dataset_contract=dataset_contract,
        cleaning_notes=cleaning_notes,
        execution=execution,
    )
