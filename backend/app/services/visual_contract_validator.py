"""
visual_contract_validator.py — Validador Estricto y Constructor de VisualContractV1.
════════════════════════════════════════════════════════════════════════════════════
Garantiza que toda opción de ECharts emitida hacia el frontend sea determinista,
accesible y cumpla el contrato tipado VisualContractV1.
"""
from __future__ import annotations

from typing import Any

from app.core.analytical_contract import VisualContractV1
from app.core.structured_logging import emit_structured_log

SAAS_ACCESSIBLE_PALETTE = [
    '#2563eb',  # Blue 600
    '#06b6d4',  # Cyan 500
    '#8b5cf6',  # Violet 500
    '#10b981',  # Emerald 500
    '#f43f5e',  # Rose 500
    '#f59e0b',  # Amber 500
    '#64748b',  # Slate 500
    '#818cf8',  # Indigo 400
]

CANONICAL_CHART_TYPES = {
    "bar": "bar",
    "bar_chart": "bar",
    "line": "line",
    "line_chart": "line",
    "area": "area",
    "area_chart": "area",
    "pie": "pie",
    "pie_chart": "pie",
    "donut": "donut",
    "donut_chart": "donut",
    "scatter": "scatter",
    "scatter_chart": "scatter",
    "kpi": "kpi",
    "table": "table",
    "smart_table": "table",
    "gauge": "gauge",
    "gauge_chart": "gauge",
    "heatmap": "heatmap",
    "heatmap_chart": "heatmap",
    "funnel": "funnel",
    "funnel_chart": "funnel",
    "waterfall": "waterfall",
    "waterfall_chart": "waterfall",
}


def validate_and_normalize_echarts_option(
    option: dict[str, Any],
    chart_type: str,
) -> dict[str, Any]:
    """
    Valida y normaliza estructuralmente un diccionario de opción ECharts.
    Inyecta tooltips, colores SaaS accesibles y corrige ejes cartesianos ausentes.
    """
    if not isinstance(option, dict):
        return {"error": "Opción de gráfico inválida: no es un diccionario"}

    norm_type = CANONICAL_CHART_TYPES.get(chart_type.lower(), chart_type.lower())

    # 1. Asegurar paleta de colores SaaS accesible
    if "color" not in option or not isinstance(option.get("color"), list):
        option["color"] = list(SAAS_ACCESSIBLE_PALETTE)

    # 2. Configurar Tooltip según tipo de gráfico
    if "tooltip" not in option or not isinstance(option.get("tooltip"), dict):
        option["tooltip"] = {}

    tooltip = option["tooltip"]
    if "trigger" not in tooltip:
        if norm_type in ("pie", "donut", "gauge", "funnel"):
            tooltip["trigger"] = "item"
        else:
            tooltip["trigger"] = "axis"

    # 3. Validación de gráficos cartesianos (bar, line, area, scatter, waterfall)
    is_cartesian = norm_type in ("bar", "line", "area", "scatter", "waterfall", "histogram", "combo", "dual_axis")
    if is_cartesian:
        if "xAxis" not in option:
            option["xAxis"] = {"type": "category", "data": []}
        if "yAxis" not in option:
            option["yAxis"] = {"type": "value"}

    # 4. Validación de series
    series = option.get("series")
    if series is None:
        option["series"] = []
    elif isinstance(series, dict):
        option["series"] = [series]
    elif not isinstance(series, list):
        option["series"] = []

    return option


def should_fallback_to_kpi_or_table(
    data: Any,
    chart_type: str,
) -> tuple[bool, str]:
    """
    Determina si un conjunto de datos es insuficiente para el gráfico solicitado
    y debe degradar suavemente a 'kpi' o 'table'.
    """
    if data is None:
        return True, "table"

    if isinstance(data, list):
        if len(data) == 0:
            return True, "table"
        if len(data) == 1 and chart_type in ("bar", "line", "area", "scatter"):
            # Un solo punto de datos en gráfico cartesiano -> Mejor como KPI
            return True, "kpi"

    return False, chart_type


def build_visual_contract(
    chart_type: str,
    option: dict[str, Any],
    plan: Any = None,
    ibis_output: dict[str, Any] | None = None,
    evidence_id: str | None = None,
) -> VisualContractV1:
    """
    Construye un VisualContractV1 completo con metadatos de accesibilidad y series.
    """
    norm_type = CANONICAL_CHART_TYPES.get(chart_type.lower(), chart_type.lower())
    norm_option = validate_and_normalize_echarts_option(option, chart_type=norm_type)

    ibis_output = ibis_output or {}
    series_list = norm_option.get("series", [])

    series_summary: list[dict[str, Any]] = []
    total_data_points = 0

    for s in series_list:
        if isinstance(s, dict):
            s_name = str(s.get("name") or "Serie")
            s_type = str(s.get("type") or norm_type)
            s_data = s.get("data")
            pt_count = len(s_data) if isinstance(s_data, list) else 0
            total_data_points += pt_count
            series_summary.append({
                "name": s_name,
                "type": s_type,
                "data_point_count": pt_count,
            })

    x_axis_name = None
    y_axis_name = None

    if "xAxis" in norm_option and isinstance(norm_option["xAxis"], dict):
        x_axis_name = norm_option["xAxis"].get("name") or ibis_output.get("x_axis") or ibis_output.get("x_label")
    if "yAxis" in norm_option and isinstance(norm_option["yAxis"], dict):
        y_axis_name = norm_option["yAxis"].get("name") or ibis_output.get("y_axis") or ibis_output.get("y_label")

    metric_unit = getattr(getattr(plan, "main_intent", None), "metric_unit", None)
    format_currency = "$" if metric_unit == "currency" else None
    format_percentage = bool(metric_unit == "percentage")

    title = getattr(plan, "title", norm_option.get("title", {}).get("text", "Gráfico"))
    accessibility_desc = (
        f"Visualización tipo {norm_type.upper()}: '{title}'. "
        f"Contiene {len(series_summary)} serie(s) con un total de {total_data_points} punto(s) de datos."
    )
    if x_axis_name:
        accessibility_desc += f" Eje X: {x_axis_name}."
    if y_axis_name:
        accessibility_desc += f" Eje Y: {y_axis_name}."

    return VisualContractV1(
        chart_type=norm_type,
        x_axis=str(x_axis_name) if x_axis_name else None,
        y_axis=str(y_axis_name) if y_axis_name else None,
        series=series_summary,
        format_currency=format_currency,
        format_percentage=format_percentage,
        evidence_id=evidence_id,
        metadata={
            "series_count": len(series_summary),
            "data_point_count": total_data_points,
            "accessibility_description": accessibility_desc,
            "color_palette_size": len(norm_option.get("color", [])),
        },
    )
