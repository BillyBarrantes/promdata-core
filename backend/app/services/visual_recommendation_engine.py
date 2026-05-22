from __future__ import annotations

import re
import unicodedata
from typing import Any


VISUAL_LABELS = {
    "bar_chart": "Bar",
    "stacked_bar_chart": "Stacked Bar",
    "line_chart": "Line",
    "area_chart": "Area",
    "pie_chart": "Donut",
    "treemap": "Treemap",
    "gauge_chart": "Gauge",
    "scatter_plot": "Scatter",
    "bubble_chart": "Bubble",
    "heatmap_chart": "Heatmap",
    "waterfall_chart": "Waterfall",
    "funnel_chart": "Funnel",
    "boxplot_chart": "Boxplot",
    "dual_axis_chart": "Dual Axis",
    "combo_chart": "Combo",
    "smart_table": "Smart Table",
    "histogram_chart": "Histogram",
    "gantt_chart": "Gantt",
    "pareto_chart": "Pareto",
}

VISUAL_CATALOG_ORDER = [
    "bar_chart",
    "stacked_bar_chart",
    "line_chart",
    "area_chart",
    "pie_chart",
    "treemap",
    "gauge_chart",
    "scatter_plot",
    "bubble_chart",
    "heatmap_chart",
    "waterfall_chart",
    "funnel_chart",
    "boxplot_chart",
    "dual_axis_chart",
    "combo_chart",
    "histogram_chart",
    "pareto_chart",
    "gantt_chart",
    "smart_table",
]


def _normalize_visual_name(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    aliases = {
        "bar": "bar_chart",
        "bar_chart": "bar_chart",
        "stacked": "stacked_bar_chart",
        "stacked_bar": "stacked_bar_chart",
        "stacked_bar_chart": "stacked_bar_chart",
        "line": "line_chart",
        "line_chart": "line_chart",
        "area": "area_chart",
        "area_chart": "area_chart",
        "pie": "pie_chart",
        "donut": "pie_chart",
        "pie_chart": "pie_chart",
        "donut_chart": "pie_chart",
        "treemap": "treemap",
        "gauge": "gauge_chart",
        "gauge_chart": "gauge_chart",
        "scatter": "scatter_plot",
        "scatter_plot": "scatter_plot",
        "bubble": "bubble_chart",
        "bubble_chart": "bubble_chart",
        "histogram": "histogram_chart",
        "histogram_chart": "histogram_chart",
        "heatmap": "heatmap_chart",
        "heatmap_chart": "heatmap_chart",
        "waterfall": "waterfall_chart",
        "waterfall_chart": "waterfall_chart",
        "funnel": "funnel_chart",
        "funnel_chart": "funnel_chart",
        "boxplot": "boxplot_chart",
        "boxplot_chart": "boxplot_chart",
        "dual_axis": "dual_axis_chart",
        "dual_axis_chart": "dual_axis_chart",
        "combo": "combo_chart",
        "combo_chart": "combo_chart",
        "smart_table": "smart_table",
        "gantt": "gantt_chart",
        "gantt_chart": "gantt_chart",
        "pareto": "pareto_chart",
        "pareto_chart": "pareto_chart",
    }
    return aliases.get(normalized, "bar_chart")


def normalize_visual_id(value: str | None) -> str:
    """
    Canonicaliza aliases de visual a IDs del catálogo UI.
    Ej: heatmap -> heatmap_chart, funnel -> funnel_chart.
    """
    return _normalize_visual_name(value)


def resolve_visual_protocol_value(value: str | None) -> str:
    """
    Traduce IDs de visual UI al valor compatible con VisualProtocol (semantic_grammar).
    Evita fallos de lock por desalineación *_chart vs enum.
    """
    canonical_visual = _normalize_visual_name(value)
    visual_to_protocol = {
        "bar_chart": "bar_chart",
        "stacked_bar_chart": "bar_chart",
        "line_chart": "line_chart",
        "area_chart": "area_chart",
        "pie_chart": "pie_chart",
        "treemap": "treemap",
        "gauge_chart": "kpi_card",
        "scatter_plot": "scatter_plot",
        "bubble_chart": "scatter_plot",
        "heatmap_chart": "heatmap",
        "waterfall_chart": "waterfall",
        "funnel_chart": "funnel_chart",
        "boxplot_chart": "boxplot",
        "dual_axis_chart": "dual_axis_chart",
        "combo_chart": "dual_axis_chart",
        "histogram_chart": "histogram",
        "gantt_chart": "bar_chart",
        "pareto_chart": "bar_chart",
        "smart_table": "bar_chart",
    }
    return visual_to_protocol.get(canonical_visual, "bar_chart")


def _normalize_prompt_text(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(char for char in normalized if not unicodedata.combining(char)).lower()


def extract_prompt_visual_requests(prompt: str | None) -> list[str]:
    normalized_prompt = _normalize_prompt_text(prompt)
    if not normalized_prompt:
        return []

    pattern_catalog: list[tuple[str, tuple[str, ...]]] = [
        ("waterfall_chart", (r"\bcascada\b", r"\bwaterfall\b")),
        ("funnel_chart", (r"\bembudo\b", r"\bfunnel\b")),
        ("heatmap_chart", (r"\bheatmap\b", r"\bmapa(?:\s+de)?\s+calor\b")),
        ("scatter_plot", (r"\bscatter\b", r"\bdispersion\b", r"\bgrafico(?:\s+de)?\s+dispersion\b")),
        ("histogram_chart", (r"\bhistograma\b", r"\bhistogram\b")),
        ("treemap", (r"\btreemap\b",)),
        ("boxplot_chart", (r"\bboxplot\b", r"\bgrafico(?:\s+de)?\s+caja\b", r"\bcaja y bigotes\b")),
        ("dual_axis_chart", (r"\bdual axis\b", r"\bdoble eje\b")),
        ("combo_chart", (r"\bcombo\b", r"\bcombinado\b")),
        ("area_chart", (r"\barea\b", r"\barea chart\b")),
        ("line_chart", (r"\blineal\b", r"\blinea\b", r"\bline chart\b")),
        ("pie_chart", (r"\btorta\b", r"\bpastel\b", r"\bpie\b", r"\bdonut\b")),
        ("bar_chart", (r"\bbarras\b", r"\bbar chart\b", r"\bcolumnas\b")),
    ]

    matches: list[tuple[int, str]] = []
    for visual_id, patterns in pattern_catalog:
        for pattern in patterns:
            match = re.search(pattern, normalized_prompt)
            if match:
                matches.append((match.start(), visual_id))
                break

    matches.sort(key=lambda item: item[0])
    ordered_visuals: list[str] = []
    seen: set[str] = set()
    for _, visual_id in matches:
        if visual_id in seen:
            continue
        seen.add(visual_id)
        ordered_visuals.append(visual_id)
    return ordered_visuals


def should_enable_visual_probe_mode(
    prompt: str | None,
    explicit_visual_requests: list[str] | None = None,
) -> bool:
    requested_visuals = explicit_visual_requests or extract_prompt_visual_requests(prompt)

    normalized_prompt = _normalize_prompt_text(prompt)
    if not normalized_prompt:
        return False

    analysis_override_terms = [
        "accion",
        "acción",
        "recomend",
        "riesgo",
        "alerta",
        "cumpl",
        "compliance",
        "causa",
        "hallazgo",
        "insight",
        "forecast",
        "pronostic",
        "proyecci",
        "predic",
        "anomalia",
        "anomal",
    ]
    if any(term in normalized_prompt for term in analysis_override_terms):
        return False

    if requested_visuals:
        return True

    return any(term in normalized_prompt for term in ("grafico", "grafica", "chart", "visual"))


def _intent_type(plan: Any) -> str:
    return str(getattr(getattr(plan, "main_intent", None), "type", "") or "").strip().lower()


def _has_temporal_axis(plan: Any) -> bool:
    intent = getattr(plan, "main_intent", None)
    return bool(getattr(intent, "date_column", None)) or _intent_type(plan) in {"trend", "predictive"}


def _row_count(ibis_output: dict[str, Any]) -> int:
    rows = ibis_output.get("data") if isinstance(ibis_output.get("data"), list) else []
    return len(rows)


def _rows(ibis_output: dict[str, Any]) -> list[Any]:
    return ibis_output.get("data") if isinstance(ibis_output.get("data"), list) else []


def _has_secondary_metric(plan: Any, ibis_output: dict[str, Any]) -> bool:
    intent = getattr(plan, "main_intent", None)
    metrics = list(getattr(intent, "metrics", None) or [])
    if len(metrics) >= 2:
        return True
    rows = ibis_output.get("data") if isinstance(ibis_output.get("data"), list) else []
    for row in rows[:5]:
        extra = row.get("extra_info", {}) if isinstance(row, dict) else {}
        if isinstance(extra, dict) and extra.get("secondary_value") is not None:
            return True
    return False


def _is_percentage_metric(plan: Any, ibis_output: dict[str, Any]) -> bool:
    intent = getattr(plan, "main_intent", None)
    metric_unit = str(getattr(intent, "metric_unit", "") or "").strip().lower()
    if metric_unit == "percentage":
        return True
    rows = ibis_output.get("data") if isinstance(ibis_output.get("data"), list) else []
    values: list[float] = []
    for row in rows[:10]:
        if isinstance(row, dict) and isinstance(row.get("value"), (int, float)):
            values.append(float(row["value"]))
    return bool(values) and all(0 <= value <= 100 for value in values)


def _has_multiseries_shape(ibis_output: dict[str, Any]) -> bool:
    if str(ibis_output.get("barmode") or "").strip().lower() == "stacked":
        return True

    for row in _rows(ibis_output)[:10]:
        if not isinstance(row, dict):
            continue
        numeric_keys = [
            key
            for key, value in row.items()
            if key != "extra_info" and isinstance(value, (int, float))
        ]
        if len(numeric_keys) >= 2:
            return True
    return False


def _has_bubble_metric(ibis_output: dict[str, Any]) -> bool:
    for row in _rows(ibis_output)[:20]:
        if isinstance(row, (list, tuple)):
            numeric_count = sum(1 for value in row if isinstance(value, (int, float)))
            if numeric_count >= 3:
                return True
            continue
        if not isinstance(row, dict):
            continue
        extra = row.get("extra_info", {}) if isinstance(row.get("extra_info"), dict) else {}
        if isinstance(extra.get("bubble_size"), (int, float)) or isinstance(extra.get("size"), (int, float)):
            return True
        numeric_count = sum(
            1
            for key, value in row.items()
            if key != "extra_info" and isinstance(value, (int, float))
        )
        if numeric_count >= 3:
            return True
    return False


def _supports_histogram(ibis_output: dict[str, Any]) -> bool:
    numeric_rows = 0
    categorical_rows = 0

    for row in _rows(ibis_output)[:50]:
        if isinstance(row, (int, float)):
            numeric_rows += 1
            continue
        if isinstance(row, (list, tuple)):
            if any(isinstance(value, str) for value in row):
                categorical_rows += 1
            elif any(isinstance(value, (int, float)) for value in row):
                numeric_rows += 1
            continue
        if not isinstance(row, dict):
            continue
        values = [value for key, value in row.items() if key != "extra_info"]
        if any(isinstance(value, str) for value in values):
            categorical_rows += 1
        elif any(isinstance(value, (int, float)) for value in values):
            numeric_rows += 1

    return numeric_rows >= 8 and categorical_rows == 0


def _supports_scatter(ibis_output: dict[str, Any]) -> bool:
    for row in _rows(ibis_output)[:50]:
        if isinstance(row, (list, tuple)):
            numeric_count = sum(1 for value in row if isinstance(value, (int, float)))
            if numeric_count >= 2:
                return True
            continue

        if not isinstance(row, dict):
            continue

        numeric_count = sum(
            1
            for key, value in row.items()
            if key != "extra_info" and isinstance(value, (int, float))
        )
        if numeric_count >= 2:
            return True

    return False


def _build_allowed_replacements(plan: Any, ibis_output: dict[str, Any]) -> list[str]:
    intent_type = _intent_type(plan)
    has_temporal = _has_temporal_axis(plan)
    rows = _row_count(ibis_output)
    secondary_metric = _has_secondary_metric(plan, ibis_output)
    multiseries = _has_multiseries_shape(ibis_output)
    bubble_metric = _has_bubble_metric(ibis_output)
    histogram_ready = _supports_histogram(ibis_output)
    scatter_ready = _supports_scatter(ibis_output)

    if rows > 20:
        base = ["smart_table", "bar_chart"]
        if has_temporal:
            base.insert(0, "line_chart")
        if multiseries:
            base.append("stacked_bar_chart")
        return base

    if has_temporal:
        allowed = ["line_chart", "area_chart"]
        if secondary_metric:
            allowed.append("dual_axis_chart")
            allowed.append("combo_chart")
        if rows <= 12:
            allowed.append("bar_chart")
        if multiseries:
            allowed.append("stacked_bar_chart")
        return allowed

    if intent_type == "diagnostic":
        allowed = ["boxplot_chart", "heatmap_chart"]
        if scatter_ready:
            allowed.insert(0, "scatter_plot")
        if bubble_metric:
            allowed.append("bubble_chart")
        if histogram_ready:
            allowed.append("histogram_chart")
        if not scatter_ready and not bubble_metric and not histogram_ready:
            allowed.append("bar_chart")
        return allowed

    if rows <= 6:
        allowed = ["bar_chart", "pie_chart", "treemap"]
        if multiseries:
            allowed.append("stacked_bar_chart")
        return allowed

    if rows <= 20:
        allowed = ["bar_chart", "treemap", "smart_table"]
        if multiseries:
            allowed.append("stacked_bar_chart")
        if histogram_ready:
            allowed.append("histogram_chart")
        return allowed

    allowed = ["smart_table", "bar_chart"]
    if multiseries:
        allowed.append("stacked_bar_chart")
    return allowed


def _is_visual_valid(visual: str, plan: Any, ibis_output: dict[str, Any]) -> tuple[bool, str | None]:
    intent_type = _intent_type(plan)
    has_temporal = _has_temporal_axis(plan)
    rows = _row_count(ibis_output)
    secondary_metric = _has_secondary_metric(plan, ibis_output)
    is_percentage = _is_percentage_metric(plan, ibis_output)
    multiseries = _has_multiseries_shape(ibis_output)
    bubble_metric = _has_bubble_metric(ibis_output)
    histogram_ready = _supports_histogram(ibis_output)
    scatter_ready = _supports_scatter(ibis_output)

    if visual == "stacked_bar_chart" and not multiseries:
        return False, "Stacked Bar requiere multiples series comparables por categoria."
    if visual == "pie_chart" and has_temporal:
        return False, "Donut no es recomendable para series temporales."
    if visual == "pie_chart" and rows > 6:
        return False, "Donut pierde claridad cuando hay mas de 6 categorias."
    if visual == "treemap" and has_temporal:
        return False, "Treemap no es recomendable para evolucion temporal."
    if visual == "gauge_chart" and not is_percentage:
        return False, "Gauge requiere una sola metrica acotada o porcentual."
    if visual == "gauge_chart" and rows > 1:
        return False, "Gauge requiere una sola observacion principal."
    if visual == "dual_axis_chart" and not secondary_metric:
        return False, "Dual Axis requiere dos metricas o una metrica secundaria calculada."
    if visual == "combo_chart" and (not has_temporal or not secondary_metric):
        return False, "Combo requiere tiempo y una metrica secundaria para combinar barras y linea."
    if visual == "scatter_plot" and not scatter_ready:
        return False, "Scatter requiere al menos dos metricas numericas por punto."
    if visual == "bubble_chart" and not bubble_metric:
        return False, "Bubble requiere una tercera magnitud para dimensionar el tamano de cada punto."
    if visual == "histogram_chart" and not histogram_ready:
        return False, "Histogram requiere una distribucion numerica cruda, no categorias agregadas."
    if visual == "waterfall_chart" and intent_type not in {"descriptive", "distribution"}:
        return False, "Waterfall solo aplica a flujos y contribuciones acumuladas."
    if visual == "gantt_chart":
        return False, "Gantt requiere campos operativos de inicio y fin; aun no hay contrato automatico suficiente."
    if visual == "pareto_chart" and intent_type not in {"distribution", "descriptive"}:
        return False, "Pareto solo aplica a concentracion y ranking."
    return True, None


def _recommend_visual(plan: Any, ibis_output: dict[str, Any]) -> tuple[str, str]:
    intent_type = _intent_type(plan)
    has_temporal = _has_temporal_axis(plan)
    rows = _row_count(ibis_output)
    secondary_metric = _has_secondary_metric(plan, ibis_output)
    is_percentage = _is_percentage_metric(plan, ibis_output)
    scatter_ready = _supports_scatter(ibis_output)
    histogram_ready = _supports_histogram(ibis_output)
    bubble_metric = _has_bubble_metric(ibis_output)

    if rows > 25:
        return "smart_table", "El volumen visible favorece una Smart Table como visual principal."
    if has_temporal and secondary_metric:
        return "dual_axis_chart", "La vista mezcla tiempo con dos escalas y conviene un Dual Axis."
    if has_temporal and intent_type == "predictive":
        return "area_chart", "La proyeccion temporal gana legibilidad con Area para resaltar continuidad y volumen."
    if has_temporal:
        return "line_chart", "La evolucion temporal se entiende mejor con Line."
    if intent_type == "diagnostic" and scatter_ready:
        return "scatter_plot", "La intencion diagnostica prioriza relacion y dispersion entre variables."
    if intent_type == "diagnostic" and bubble_metric:
        return "bubble_chart", "La lectura diagnostica expone una tercera magnitud util para Bubble."
    if intent_type == "diagnostic" and histogram_ready:
        return "histogram_chart", "La salida disponible conserva una distribucion numerica util para Histogram."
    if intent_type == "diagnostic":
        return "bar_chart", "La salida diagnostica disponible no expone dos metricas numericas por punto; Bar preserva una lectura valida."
    if is_percentage and rows == 1:
        return "gauge_chart", "Una sola metrica porcentual se comunica mejor con Gauge."
    if rows <= 6:
        return "pie_chart", "Hay pocas categorias y la composicion se lee bien con Donut."
    if rows <= 20:
        return "treemap", "La densidad categorica media favorece Treemap frente a Donut."
    return "bar_chart", "La comparacion estructural gana claridad con barras."


def _build_visual_catalog(
    plan: Any,
    ibis_output: dict[str, Any],
    recommended: str,
    applied: str,
    allowed_replacements: list[str],
) -> list[dict[str, Any]]:
    catalog: list[dict[str, Any]] = []
    allowed_set = set(allowed_replacements or [])

    for visual_id in VISUAL_CATALOG_ORDER:
        is_valid, invalid_reason = _is_visual_valid(visual_id, plan, ibis_output)
        is_enabled = is_valid and (
            visual_id in allowed_set
            or visual_id == recommended
            or visual_id == applied
        )

        if visual_id == "smart_table" and visual_id not in allowed_set:
            is_enabled = False
            if not invalid_reason:
                invalid_reason = "Smart Table se activa cuando la densidad del dataset lo justifica."

        catalog.append(
            {
                "id": visual_id,
                "label": VISUAL_LABELS.get(visual_id, visual_id),
                "enabled": is_enabled,
                "recommended": visual_id == recommended,
                "applied": visual_id == applied,
                "reason": None if is_enabled else invalid_reason or "No es la mejor opcion para este analisis.",
            }
        )

    return catalog


def build_visual_governance(
    plan: Any,
    ibis_output: dict[str, Any],
    requested_visual: str | None,
    requested_visual_locked: bool = False,
) -> dict[str, Any]:
    requested = _normalize_visual_name(requested_visual)
    recommended, recommendation_reason = _recommend_visual(plan, ibis_output)
    allowed_replacements = _build_allowed_replacements(plan, ibis_output)

    is_valid, blocked_reason = _is_visual_valid(requested, plan, ibis_output)
    strict_rejection = bool(requested_visual_locked and not is_valid)
    applied = requested if is_valid or strict_rejection else recommended
    fallback_visual = None if strict_rejection or is_valid else recommended

    advisory_reason = None
    if is_valid and requested != recommended:
        advisory_reason = f"Se respeta {VISUAL_LABELS.get(requested, requested)}, pero {VISUAL_LABELS.get(recommended, recommended)} era la recomendacion tecnica."

    catalog = _build_visual_catalog(plan, ibis_output, recommended, applied, allowed_replacements)

    return {
        "requested_visual": requested,
        "recommended_visual": recommended,
        "applied_visual": applied,
        "requested_label": VISUAL_LABELS.get(requested, requested),
        "recommended_label": VISUAL_LABELS.get(recommended, recommended),
        "applied_label": VISUAL_LABELS.get(applied, applied),
        "recommendation_reason": recommendation_reason,
        "blocked_reason": blocked_reason,
        "advisory_reason": advisory_reason,
        "requested_visual_locked": requested_visual_locked,
        "strict_rejection": strict_rejection,
        "fallback_visual": fallback_visual,
        "override_applied": not is_valid and applied != requested,
        "allowed_replacements": allowed_replacements,
        "catalog": catalog,
    }
