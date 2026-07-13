from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from app.core.structured_logging import emit_structured_log
from app.core.arrow_utils import dataframe_to_arrow_base64, records_to_arrow_base64
from app.services.analysis_memory_context import build_plan_query_contract, build_result_semantic_context
from app.services.canonical_analytical_contract_adapter import get_selected_candidate_dataframe
from app.services.canonical_shadow_query_runner import (
    CanonicalShadowQueryExecution,
    run_canonical_shadow_query_for_uploaded_file,
)
from app.services.chart_factory import ChartFactory
from app.services.dashboard_narrative import generate_dashboard_executive_summary
from app.services.smart_table_builder import should_use_smart_table, echarts_to_smart_table, build_smart_table_from_records
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
        "combo_chart": "combo",
        "dual_axis_chart": "combo",
        "smart_table": "smart_table",
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

def _get_plan_intent_prop(plan: Any, prop: str) -> Any:
    if isinstance(plan, dict):
        return plan.get("main_intent", {}).get(prop)
    intent = getattr(plan, "main_intent", None)
    return getattr(intent, prop, None)

def _safe_metric_unit(plan: Any) -> str | None:
    metric_unit = _get_plan_intent_prop(plan, "metric_unit")
    return getattr(metric_unit, "value", metric_unit) if metric_unit is not None else None


def _get_plan_visual_protocol(plan: Any) -> str | None:
    vp = _get_plan_intent_prop(plan, "visual_protocol")
    if hasattr(vp, "value"):
        return str(vp.value)
    return str(vp) if vp else None


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
    
    # Defensive initialization - extract chart_data early to avoid UnboundLocalError
    chart_data = _safe_list(result_payload.get("data"))

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

    # ── Comparison Metadata Feeding ──────────────────────────────────
    try:
        comparison = hard_facts.get("comparison")
        if comparison and isinstance(comparison, dict):
            _year_from = comparison.get("year_from", "")
            _year_to = comparison.get("year_to", "")
            _total_var = comparison.get("total_variation", 0)
            _positive = comparison.get("positive_changes", 0)
            _negative = comparison.get("negative_changes", 0)
            _entities = comparison.get("total_entities", len(chart_data) if chart_data else 0)
            _period_text = f"{_year_from} vs {_year_to}" if _year_from and _year_to else (_year_to or "períodos")
            facts.append(
                f"{title}: comparación {_period_text}, variación neta {_total_var}, "
                f"{_positive} aumentos y {_negative} disminuciones en {_entities} entidades."
            )
    except Exception as e:
        print(f"⚠️ [CANARY SPY] Error procesando metadatos de comparación: {e}")
        import traceback
        traceback.print_exc()

    # ── Post-Aggregation Fact Feeding ──────────────────────────────────
    # Inject the compact aggregated data[] that DuckDB already computed
    # so Gemini can narrate the FULL temporal/dimensional scope, not just
    # the truncated snapshot.  Cost: ~200 chars, 0 bytes extra memory.
    try:
        data_summary = _build_aggregated_data_summary(chart_data)
        if data_summary:
            facts.append(f"{title}: {data_summary}")
    except Exception as e:
        print(f"⚠️ [CANARY SPY] Error construyendo data_summary: {e}")
        import traceback
        traceback.print_exc()

    # Predictive widgets need more facts (history + forecast)
    max_facts = 5 if hard_facts.get("forecast_points") is not None else 4
    if facts:
        return facts[:max_facts]

    try:
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
    except Exception as e:
        print(f"⚠️ [CANARY SPY] Error procesando chart_data fallback: {e}")
        import traceback
        traceback.print_exc()
    
    return facts[:max_facts]


def _build_summary_widgets(
    *,
    execution: CanonicalShadowQueryExecution,
    chart_options: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    widgets: list[dict[str, Any]] = []
    _chart_idx = 0
    for index, (plan, result_payload) in enumerate(zip(execution.plans, execution.execution_results), start=1):
        if not isinstance(result_payload, dict) or result_payload.get("error"):
            continue

        title = _normalize_text(result_payload.get("title")) or plan.title or f"Widget {index}"
        result_type = _normalize_text(result_payload.get("type")).lower()

        # Use the APPLIED visual type from chart_options (post-ChartFactory conversion),
        # not the ORIGINAL from result_payload. Uses dedicated counter to handle
        # index mismatch when _build_chart_option returns None for some results.
        visual_type = _normalize_text(result_payload.get("chart_type"))
        if _chart_idx < len(chart_options):
            chart_opt = chart_options[_chart_idx]
            if isinstance(chart_opt, dict):
                vsp = chart_opt.get("visual_source_payload")
                if isinstance(vsp, dict) and vsp.get("chart_type"):
                    visual_type = _normalize_text(vsp.get("chart_type"))
        _chart_idx += 1
        visual_type = normalize_visual_id(visual_type)

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


def _extract_direction_notes(plans: list) -> str | None:
    """Extract flow-direction context from plans for the dashboard narrative."""
    direction_cols: set[str] = set()
    for plan in plans:
        intent = getattr(plan, "main_intent", None)
        if not intent:
            continue
        # DistributionIntent: direction injected as group_by
        for col in _safe_list(getattr(intent, "group_by", None)):
            direction_cols.add(str(col))
        # TimeTrendIntent: direction injected as split_dimension
        split_dim = getattr(intent, "split_dimension", None)
        if split_dim:
            direction_cols.add(str(split_dim))
    if not direction_cols:
        return None
    cols_str = ", ".join(sorted(direction_cols))
    return (
        f"La(s) columna(s) '{cols_str}' contiene(n) flujos opuestos contrapuestos. "
        "Cuando analices dimensiones con flujos opuestos contrapuestos "
        "(como Ingreso vs. Egreso, Entrada vs. Salida), reporta cada flujo por "
        "separado y evita consolidar su suma como un único monto total de negocio. "
        "Para cualquier otra métrica estándar del dataset que no sea un flujo "
        "opuesto, las reglas normales de agregación y suma total son válidas."
    )


def _build_chart_option(
    *,
    plan: Any,
    title: str,
    result_payload: dict[str, Any],
    currency_meta: dict[str, Any],
    schema_profile: dict[str, Any],
    _snapshot_resolved_date: dict | None = None,
) -> dict[str, Any] | None:
    original_ui_chart_type = normalize_visual_id(result_payload.get("chart_type"))

    # ── V6.6 FIX: Evaluar Gobernanza ANTES de generar el gráfico ──
    visual_governance = build_visual_governance(
        plan,
        result_payload,
        original_ui_chart_type,
        requested_visual_locked=False,
    )
    ui_chart_type = visual_governance.get("applied_visual") or original_ui_chart_type
    chart_type = _normalize_chart_type(ui_chart_type)

    # ── ADR-VISUAL-007 (Governance Auto-Heal Gate) ────────────────────
    # When the Ibis engine's output chart_type ('_eng_ct') diverges from
    # the LLM's original visual protocol ('_llm_vp'), and governance
    # recommends a structurally superior visual ('_rec_visual') versus
    # what was applied ('_app_visual'), override the applied type.
    # Governance recommendations come from structural data properties
    # (cardinality, metric count, temporal axis) and are domain-agnostic.
    # ──────────────────────────────────────────────────────────────────
    _rec_visual = visual_governance.get("recommended_visual")
    _app_visual = visual_governance.get("applied_visual")
    _llm_vp = _get_plan_visual_protocol(plan)
    _eng_ct = result_payload.get("chart_type")

    # [V2.2] PREMIUM VISUALS OVERRIDE: Si governance recomienda un
    # visual premium (combo_chart, dual_axis_chart, smart_table),
    # forzar el override incluso si el engine no hizo fallback.
    # El UX premium gana sobre la timidez del motor.
    # [V2.3] TEMP-001: No forzar smart_table sobre line_chart para trends.
    #   Un trend+split con 101 registros es normal; smart_table degrada el UX.
    PREMIUM_VISUALS = {"combo_chart", "dual_axis_chart", "smart_table"}
    if (_rec_visual and _rec_visual in PREMIUM_VISUALS
        and _rec_visual != ui_chart_type):
        _skip_smart_table = (
            _rec_visual == "smart_table"
            and (
                _get_plan_intent_prop(plan, "type") == "trend"
                or (_llm_vp and normalize_visual_id(_llm_vp) in ("line_chart", "scatter_plot"))
            )
        )
        if _skip_smart_table:
            print(f"🔄 [TEMP-001] Eximiendo trend/line_chart/scatter_plot de smart_table override (manteniendo {ui_chart_type})")
        else:
            print(f"🔄 [PREMIUM OVERRIDE] Gov recomienda: {_rec_visual}, forzando override desde {ui_chart_type}")
            ui_chart_type = _rec_visual
            chart_type = _normalize_chart_type(_rec_visual)

            # [V2.5] Scatter→Combo data transformation
            # Cuando el override convierte scatter_plot → combo_chart, los datos
            # vienen como {x_value, y_value, raw_name}. build_combo_chart espera
            # {name, <metrica_1>, <metrica_2>}. Renombramos dinámicamente usando
            # x_axis/y_axis del payload como nombres de métrica humanizados.
            if _rec_visual == "combo_chart" and (_llm_vp and normalize_visual_id(_llm_vp) == "scatter_plot"):
                _raw_data = result_payload.get("data")
                _x_axis = str(result_payload.get("x_axis", "Metrica X"))
                _y_axis = str(result_payload.get("y_axis", "Metrica Y"))
                if isinstance(_raw_data, list) and _raw_data:
                    _transformed = []
                    for _rec in _raw_data:
                        if isinstance(_rec, dict) and "x_value" in _rec and "y_value" in _rec:
                            _name = str(_rec.get("raw_name", _rec.get("name", f"Punto {len(_transformed)+1}")))
                            _transformed.append({
                                "name": _name,
                                _x_axis: float(_rec.get("x_value", 0)),
                                _y_axis: float(_rec.get("y_value", 0)),
                            })
                    if _transformed:
                        result_payload["data"] = _transformed
                        print(f"🔄 [SCATTER→COMBO] Transformados {len(_transformed)} registros: {_x_axis} (bar) + {_y_axis} (line)")

    if (_rec_visual and _app_visual and _rec_visual != _app_visual
        and _llm_vp and _eng_ct
        and normalize_visual_id(_llm_vp) != normalize_visual_id(_eng_ct)):
        gov_override_type = _normalize_chart_type(_rec_visual)
        if gov_override_type and gov_override_type != chart_type:
            print(f"🔄 [GOVERNANCE OVERRIDE] Engine fallback: {_llm_vp} → {_eng_ct}, Gov recommends: {_rec_visual} → applying")
            ui_chart_type = _rec_visual
            chart_type = gov_override_type

    # ── ADR-VISUAL-004 (Post-Guard) ────────────────────────────────────
    # The planner assigns visual_protocol BEFORE data is computed.
    # Treemap can slip through when the data contains comparison deltas
    # (negative values). Redirect to bar_chart (grouped) at the last gate.
    # ───────────────────────────────────────────────────────────────────
    if chart_type == "treemap":
        _data_list = result_payload.get("data")
        if isinstance(_data_list, list) and _data_list:
            _first = _data_list[0] if isinstance(_data_list[0], dict) else {}
            _has_comparison_keys = any(
                k for k in _first
                if k not in ("name", "extra_info") and not str(k).startswith("_")
                and isinstance(_first.get(k), (int, float)) and _first[k] < 0
            )
            if _has_comparison_keys:
                chart_type = "bar"
                print(f"🔄 [POST-GUARD] Treemap → Bar Chart (datos con variaciones negativas detectadas)")

    # ── ADR-VISUAL-006 (Smart Table Contractual) ────────────────────
    # When visual_recommendation_engine selects "smart_table" as the
    # recommended visual, chart_type arrives as "smart_table".
    # ChartFactory has no handler for this → would fall to bar_chart.
    # Intercept here: build smart_table payload directly from records.
    # ──────────────────────────────────────────────────────────
    if chart_type == "smart_table":
        _raw_data = result_payload.get("data")
        if isinstance(_raw_data, list) and _raw_data and isinstance(_raw_data[0], dict):
            _safe_mu = _safe_metric_unit(plan)
            smart_payload = build_smart_table_from_records(
                records=_raw_data,
                title=title,
                is_percentage=_safe_mu == "percentage",
            )
            if smart_payload:
                smart_payload["visual_source_payload"] = {
                    "title": title,
                    "chart_type": "smart_table",
                    "requested_chart_type": visual_governance.get("requested_visual") or ui_chart_type,
                    "rows": _raw_data,
                    "barmode": result_payload.get("barmode"),
                    "metric_unit": _safe_mu,
                    "is_percentage": _safe_mu == "percentage",
                }
                smart_payload["visual_governance"] = visual_governance
                _st_query_contract = build_plan_query_contract(plan, schema_profile)
                if _st_query_contract:
                    smart_payload["query_contract"] = _st_query_contract
                print(f"📊 [SMART TABLE] Contractual: {len(_raw_data)} records → smart_table directa")
                return smart_payload
        # Si records vacíos o malformados, fall through a ChartFactory

    # [V2.6] Promover extra_info.secondary_value a top-level key para que
    # build_combo_chart lo detecte como 2da métrica numérica.
    # El descriptive guarda la 2da métrica en extra_info (no como top-level).
    # [V2.7] Usar nombre real de la métrica desde plan.main_intent.metrics en
    # vez de la clave generica "secondary_metric" (D3 - fix tooltip names).
    if chart_type == "combo":
        _raw_data = result_payload.get("data")
        if isinstance(_raw_data, list):
            for _rec in _raw_data:
                if isinstance(_rec, dict):
                    _xtra = _rec.get("extra_info")
                    if isinstance(_xtra, dict) and "secondary_value" in _xtra:
                        _sec_metric_name = "secondary_metric"
                        if plan and hasattr(plan, "main_intent") and plan.main_intent:
                            _metrics_list = getattr(plan.main_intent, "metrics", None) or []
                            if len(_metrics_list) >= 2 and _metrics_list[1]:
                                _sec_metric_name = _metrics_list[1]
                        _rec[_sec_metric_name] = float(_xtra["secondary_value"])

    # [V2.5] Dual-axis derivation: si el chart_type final es combo (dual_axis_chart
    # o combo_chart) pero los datos tienen solo 1 metrica numerica, derivar
    # la 2da serie como % de variacion intermensual (MoM).
    # El .fillna(0.0) evita NaN en la primera fila que rompe serializacion JSON.
    if chart_type == "combo":
        _raw_data = result_payload.get("data")
        if isinstance(_raw_data, list) and len(_raw_data) >= 2:
            _num_keys = []
            if _raw_data and isinstance(_raw_data[0], dict):
                for _k in _raw_data[0].keys():
                    if _k not in ('name', 'extra_info') and not _k.startswith('_'):
                        try:
                            float(_raw_data[0][_k])
                            _num_keys.append(_k)
                        except (ValueError, TypeError):
                            continue
            if len(_num_keys) == 1:
                _primary_key = _num_keys[0]
                _mom_col = "mom_pct"
                _prev_val = None
                for _rec in _raw_data:
                    _cur_val = float(_rec.get(_primary_key, 0))
                    if _prev_val is not None and _prev_val != 0:
                        _rec[_mom_col] = round((_cur_val - _prev_val) / _prev_val * 100, 2)
                    else:
                        _rec[_mom_col] = 0.0
                    _prev_val = _cur_val
                print(f"🔄 [DUAL-AXIS] Derivada metrica MoM: {_mom_col} para {len(_raw_data)} periodos")

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

    safe_metric_unit = _safe_metric_unit(plan)
    # Smart Table detection: if chart has >20 categories, convert to Smart Table
    # [V2.3] Pasar chart_type original para que should_use_smart_table pueda
    # eximir line_chart/area_chart (trends con split tienen muchos periodos).
    _applied_type = ui_chart_type
    if should_use_smart_table(option, chart_type=original_ui_chart_type):
        smart_table_payload = echarts_to_smart_table(option, title, is_percentage=safe_metric_unit == "percentage")
        if smart_table_payload and smart_table_payload.get("type") == "smart_table":
            option = smart_table_payload
            _applied_type = "smart_table"
            print(f"🔄 [SMART TABLE] Chart converted to Smart Table ({len(_safe_list(result_payload.get('data')))} rows)")
    else:
        # Detect stacked bar: multiple series with stack property or barmode
        series_list = option.get("series", [])
        if isinstance(series_list, list) and len(series_list) > 1:
            has_stack = any(isinstance(s, dict) and s.get("stack") for s in series_list)
            if has_stack or result_payload.get("barmode") == "stacked":
                _applied_type = "stacked_bar_chart"

    print(f"🔍 [VISUAL TYPE] Original: {original_ui_chart_type}, Applied: {_applied_type}")

    option["visual_source_payload"] = {
        "title": title,
        "chart_type": _applied_type,
        "requested_chart_type": visual_governance.get("requested_visual") or ui_chart_type,
        "rows": _safe_list(result_payload.get("data")),
        "x_label": result_payload.get("x_axis"),
        "y_label": result_payload.get("y_axis"),
        "barmode": result_payload.get("barmode"),
        "metric_unit": safe_metric_unit,
        "is_percentage": safe_metric_unit == "percentage",
    }
    option["visual_governance"] = visual_governance
    query_contract = build_plan_query_contract(plan, schema_profile)
    if query_contract:
        option["query_contract"] = query_contract

    # [FIX 2026-06-08] Inyectar los filtros base del plan (e.g. "Tipo Movimiento = Ingreso")
    # en el chart_option para que el frontend los pueda combinar con el clic del
    # usuario en "Filtrar aquí". Sin esto, DuckDB solo filtra por el clic y la
    # tabla resultante incluye TODOS los registros (ej. Ingresos + Egresos).
    # Solo se extraen filtros con formato column+value; operadores !=
    # se serializan como "op value" para que el frontend los pueda parsear.
    plan_filters: dict[str, str] = {}
    intent = getattr(plan, "main_intent", None)
    if intent is not None:
        for f in getattr(intent, "filters", []) or []:
            col = str(getattr(f, "column", "") or "").strip()
            val = getattr(f, "value", None)
            op = str(getattr(f, "operator", "==") or "==").strip()
            if not col or val is None:
                continue
            val_str = str(val)
            op_lower = op.lower()
            # Multi-value filters (IN_LIST): skip — frontend cross-filter
            # engine only supports single-value equality matching.
            if op_lower == "in" and isinstance(val, list):
                continue
            # Text search operators (ILIKE, LIKE, CONTAINS): strip operator
            # prefix and pass the raw search term to the frontend. The
            # sanitizeFilterValue regex handles the rest.
            if op_lower in {"ilike", "like", "contains", "starts_with", "ends_with", "not_contains", "not_like"}:
                plan_filters[col] = val_str
                continue
            # Standard: equals without prefix, other operators with prefix
            plan_filters[col] = f"{op} {val_str}" if op != "==" else val_str

    # [FIX 2026-06-??] Cross-filter snapshot inheritance (v2)
    # El frontend resuelve filtros contra columnas físicas de DuckDB.
    # is_latest_snapshot es un flag booleano virtual que no siempre
    # sobrevive la serialización Arrow → el frontend lo descarta.
    # En su lugar inyectamos la columna de fecha real + valor resuelto
    # (ej. fecha_de_stock = '2021-07-31') que SÍ es columna física
    # en snapshot_arrow y el frontend matchea vía L1 sin degradación.
    resolved_date = _snapshot_resolved_date or result_payload.get("_snapshot_resolved_date")
    if resolved_date:
        date_col = resolved_date.get("column")
        date_val = resolved_date.get("value")
        if date_col and date_val and date_col not in plan_filters:
            plan_filters[date_col] = date_val

    if plan_filters:
        option["chart_base_filters"] = plan_filters

    filtered_granular_df = result_payload.get("filtered_granular_df")
    if filtered_granular_df is None:
        # [FIX 2026-06-08] Fallback: si el plan no inyecta filtered_granular_df
        # explícitamente, generamos uno desde result_payload['data'] (los records
        # que ECharts ya usa para renderizar el chart). Sin este fallback,
        # granular_arrow queda en 0KB para >80% de los charts y el frontend
        # no puede hacer cross-filter sobre el chart.
        chart_data = result_payload.get("data")
        if isinstance(chart_data, list) and chart_data:
            try:
                filtered_granular_df = pd.DataFrame(chart_data)
            except Exception as df_exc:
                print(f"⚠️ [CANARY-ARROW] No se pudo derivar filtered_granular_df desde data: {df_exc}")
                filtered_granular_df = None
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

    # [FIX 2026-06] Extraer _snapshot_resolved_date del primer result_payload
    # + cómputo directo desde candidate_df como fallback con blindaje cronológico.
    # Se ejecuta ANTES del loop de chart options porque _build_chart_option
    # necesita este valor para inyectar chart_base_filters.
    _snapshot_resolved_date = next(
        (rp["_snapshot_resolved_date"] for rp in execution.execution_results
         if isinstance(rp, dict) and rp.get("_snapshot_resolved_date")),
        None
    )
    if _snapshot_resolved_date is None and dataset_contract.get("snapshot_guard_allowed"):
        time_axis = str(dataset_contract.get("time_axis") or "").strip()
        if time_axis and isinstance(candidate_df, pd.DataFrame) and not candidate_df.empty and time_axis in candidate_df.columns:
            try:
                series_dt = pd.to_datetime(candidate_df[time_axis], errors='coerce')
                if not series_dt.dropna().empty:
                    max_date = series_dt.max()
                    date_str = max_date.strftime('%Y-%m-%d')
                else:
                    max_date = candidate_df[time_axis].max()
                    date_str = max_date.strftime('%Y-%m-%d') if hasattr(max_date, 'strftime') else str(max_date)
                if date_str:
                    _snapshot_resolved_date = {"column": time_axis, "value": date_str}
                    print(f"📸 [SNAPSHOT-RESOLVED-DIRECT] Fecha máxima cronológica: '{time_axis}' = '{date_str}'")
            except Exception as e:
                print(f"⚠️ [SNAPSHOT-RESOLVED-DIRECT] Error en parsing cronológico: {e}")

    analysis_blocks: list[str] = []
    _primary_plan_failed = False
    _primary_plan_error = None
    for plan_idx, (plan, result_payload) in enumerate(zip(execution.plans, execution.execution_results)):
        if not isinstance(result_payload, dict):
            result_payload = {}
        if result_payload.get("error"):
            if plan_idx == 0:
                _primary_plan_failed = True
                _primary_plan_error = str(result_payload.get("error"))
                emit_structured_log(
                    "primary_plan_failed",
                    plan_idx=0,
                    intent_type=getattr(getattr(plan, "main_intent", None), "type", None),
                    error=_primary_plan_error,
                    total_plans=len(execution.plans),
                    successful_plans=sum(1 for r in execution.execution_results if isinstance(r, dict) and not r.get("error")),
                )
                print(f"⚠️ [PRIMARY PLAN FAILED] Plan 1 ({getattr(getattr(plan, 'main_intent', None), 'type', '?')}) "
                      f"falló: {_primary_plan_error}")
            continue

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
            # [FIX V2.2] Doble barrera: No inyectar moneda si el plan explícitamente la prohíbe
            # [FIX V2.3] Usar _safe_metric_unit en vez de getattr encadenado (soporta dict + Pydantic)
            plan_metric_unit = str(_safe_metric_unit(plan) or "").lower()
            plan_currency_meta = None if plan_metric_unit in ["number", "percentage"] else currency_meta

            option = _build_chart_option(
                plan=plan,
                title=title,
                result_payload=result_payload,
                currency_meta=plan_currency_meta,
                schema_profile=_safe_dict(attrs.get("schema_profile")),
                _snapshot_resolved_date=_snapshot_resolved_date,
            )
            if option:
                final_struct["chart_options"].append(option)
            # [FIX 2026-06-08] data_by_chart: poblar SIEMPRE los records de cada chart
            # para que el frontend pueda usar cross-filter sobre cualquier chart,
            # no solo sobre el primero. data se mantiene para el primer chart
            # (compatibilidad con código legacy que lee data directamente).
            if isinstance(result_payload.get("data"), list):
                chart_data = _safe_list(result_payload.get("data"))
                if chart_data:
                    data_by_chart = final_struct.setdefault("data_by_chart", {})
                    chart_key = (
                        (option.get("id") if option and option.get("id") else None)
                        or title
                        or f"chart_{len(data_by_chart)}"
                    )
                    data_by_chart[chart_key] = chart_data
                    if not final_struct["data"]:
                        final_struct["data"] = chart_data
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

    if _primary_plan_failed:
        final_struct["traceability"]["primary_plan_failed"] = True
        final_struct["traceability"]["primary_plan_error"] = _primary_plan_error
        # [V2.2] Buscar plan de reemplazo en los resultados exitosos.
        # Si Plan 1 (el principal) falló, pero hay charts exitosos,
        # reetiquetar el primer chart exitoso como "primary_replacement"
        # para que el frontend pueda destacarlo.
        exitosos = [o for o in final_struct.get("chart_options", []) if o.get("type")]
        if exitosos:
            exitosos[0]["is_primary_replacement"] = True
            print(f"🔄 [PRIMARY RECOVERY] Reetiquetando chart '{exitosos[0].get('title', '?')}' como primary_replacement")

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
        elif _snapshot_resolved_date:
            date_col = _snapshot_resolved_date["column"]
            date_val = _snapshot_resolved_date["value"]
            try:
                mask = candidate_df[date_col].astype(str).str.contains(date_val)
                snapshot_df = candidate_df[mask]
                if len(snapshot_df) == 0:
                    print(
                        f"⚠️ [SNAPSHOT-RESOLVED] Formato no-ISO en '{date_col}': "
                        f"filtro '{date_val}' retornó 0 filas. "
                        f"Activando fallback completo con orden descendente."
                    )
                    snapshot_df = candidate_df
                snapshot_df = snapshot_df.sort_values(by=date_col, ascending=False)
                print(
                    f"📸 [SNAPSHOT-RESOLVED] Filtrado por fecha máxima "
                    f"'{date_col}' = '{date_val}': {len(snapshot_df)} filas"
                )
            except Exception as e:
                print(f"⚠️ [SNAPSHOT-RESOLVED] Fallback filter falló: {e}")

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
            # Guard de tamanho: si el Arrow base64 excede el budget, lo descartamos
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
    else:
        # [FIX 2026-06-08] Cascade de fallbacks para snapshot_arrow.
        # Si candidate_df es None o está vacío (lo cual ocurre en la mayoría
        # de los paths del canary), intentamos recuperar el snapshot desde:
        # 1. candidate_dataframe del adapter runtime (si el plan lo expone)
        # 2. data_by_chart de la sección data_by_chart que acabamos de poblar
        # 3. main_df del dataframe de análisis (atributo del execution)
        snapshot_df = None
        try:
            candidate_alt = attrs.get("candidate_dataframe")
            if isinstance(candidate_alt, pd.DataFrame) and not candidate_alt.empty:
                snapshot_df = candidate_alt
        except Exception:
            pass
        if snapshot_df is None:
            # Reconstruir desde los records que ya recolectamos
            data_by_chart = final_struct.get("data_by_chart", {}) or {}
            for chart_key, chart_data in data_by_chart.items():
                if isinstance(chart_data, list) and chart_data:
                    try:
                        snapshot_df = pd.DataFrame(chart_data)
                        break
                    except Exception:
                        continue
        if snapshot_df is not None and not snapshot_df.empty:
            if "is_latest_snapshot" in snapshot_df.columns:
                latest_mask = snapshot_df["is_latest_snapshot"] == True
                if bool(latest_mask.any()):
                    snapshot_df = snapshot_df[latest_mask]
            elif _snapshot_resolved_date:
                date_col = _snapshot_resolved_date["column"]
                date_val = _snapshot_resolved_date["value"]
                try:
                    mask = snapshot_df[date_col].astype(str).str.contains(date_val)
                    filtered = snapshot_df[mask]
                    if len(filtered) == 0:
                        print(
                            f"⚠️ [SNAPSHOT-RESOLVED-FALLBACK] Formato no-ISO en '{date_col}': "
                            f"filtro '{date_val}' retornó 0 filas. "
                            f"Activando fallback completo con orden descendente."
                        )
                        filtered = snapshot_df
                    snapshot_df = filtered.sort_values(by=date_col, ascending=False)
                    print(
                        f"📸 [SNAPSHOT-RESOLVED-FALLBACK] Filtrado por fecha máxima "
                        f"'{date_col}' = '{date_val}': {len(snapshot_df)} filas"
                    )
                except Exception as e:
                    print(f"⚠️ [SNAPSHOT-RESOLVED-FALLBACK] Fallback filter falló: {e}")
            serialization_df = (
                snapshot_df.head(_MAX_SNAPSHOT_ROWS)
                if len(snapshot_df) > _MAX_SNAPSHOT_ROWS
                else snapshot_df
            )
            snapshot_arrow = _try_dataframe_to_arrow_base64(serialization_df)
            if snapshot_arrow and len(snapshot_arrow) <= _MAX_PAYLOAD_BYTES:
                final_struct["snapshot_arrow"] = snapshot_arrow
                final_struct["snapshot_row_count"] = len(snapshot_df)
                final_struct["snapshot_serialized_rows"] = len(serialization_df)
                final_struct["snapshot_columns"] = list(snapshot_df.columns)
                print(
                    f"🦆 [SNAPSHOT-FALLBACK] snapshot_arrow derivado desde data_by_chart: "
                    f"{len(snapshot_df)} filas, {len(snapshot_df.columns)} cols"
                )

    summary_widgets = _build_summary_widgets(
        execution=execution,
        chart_options=final_struct["chart_options"],
    )
    final_struct["traceability"]["semantic_context"] = build_result_semantic_context(
        plans=list(execution.plans or []),
        schema_profile=_safe_dict(attrs.get("schema_profile")),
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
        data_notes=_extract_direction_notes(list(execution.plans or [])),
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
