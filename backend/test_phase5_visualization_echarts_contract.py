"""
test_phase5_visualization_echarts_contract.py — Suite de Visualización, ECharts y UI SaaS (Fase 5)
══════════════════════════════════════════════════════════════════════════════════════════════════
Verifica:
  1. Catálogo tipado VisualContractV1 (bar, line, area, pie, scatter, gauge).
  2. Validación estricta de opciones ECharts: tooltips, ejes cartesianos y paleta SaaS accesible.
  3. Accesibilidad: generación de accessibility_description descriptiva.
  4. Fallback visual seguro: degradación elegante a kpi o table ante datos insuficientes.
  5. Integración de visual_contract en el generador de gráficos (build_chart_config).
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from app.core.analytical_contract import VisualContractV1
from app.core.semantic_grammar import (
    AnalysisPlan,
    DescriptiveIntent,
    DistributionIntent,
    MetricUnit,
    TimeTrendIntent,
)
from app.services.chart_factory import ChartFactory
from app.services.visual_contract_validator import (
    SAAS_ACCESSIBLE_PALETTE,
    build_visual_contract,
    should_fallback_to_kpi_or_table,
    validate_and_normalize_echarts_option,
)
from app.tasks.analysis_pipeline.chart_generator import build_chart_config


# ── 1. PRUEBAS DEL CATÁLOGO TIPADO VisualContractV1 ────────────────────────────

def test_visual_contract_bar_chart():
    """Valida la generación de contrato visual para gráficos de barra."""
    raw_option = ChartFactory.build_bar_chart(
        "Ventas por Región",
        [{"name": "Norte", "value": 500}, {"name": "Sur", "value": 300}],
    )
    contract = build_visual_contract("bar", raw_option)

    assert isinstance(contract, VisualContractV1)
    assert contract.chart_type == "bar"
    assert len(contract.series) >= 1
    assert contract.metadata["series_count"] >= 1
    assert contract.metadata["data_point_count"] == 2
    assert "Norte" in str(raw_option.get("yAxis") or raw_option.get("xAxis"))


def test_visual_contract_line_and_area():
    """Valida la generación de contratos para line y area charts."""
    raw_line = ChartFactory.build_line_chart(
        "Evolución Mensual",
        [{"name": "2024-01", "value": 100}, {"name": "2024-02", "value": 150}],
        area=False,
    )
    contract_line = build_visual_contract("line", raw_line)
    assert contract_line.chart_type == "line"
    assert contract_line.metadata["data_point_count"] == 2

    raw_area = ChartFactory.build_line_chart(
        "Volumen Acumulado",
        [{"name": "2024-01", "value": 100}, {"name": "2024-02", "value": 150}],
        area=True,
    )
    contract_area = build_visual_contract("area", raw_area)
    assert contract_area.chart_type == "area"


def test_visual_contract_pie_and_scatter():
    """Valida la generación de contratos para pie y scatter charts."""
    raw_pie = ChartFactory.build_pie_chart(
        "Distribución por Canal",
        [{"name": "Online", "value": 70}, {"name": "Físico", "value": 30}],
    )
    contract_pie = build_visual_contract("pie", raw_pie)
    assert contract_pie.chart_type == "pie"
    assert contract_pie.metadata["data_point_count"] == 2

    raw_scatter = ChartFactory.create_chart(
        "scatter",
        "Correlación Precio vs Unidades",
        [{"name": "P1", "precio": 10, "unidades": 100}, {"name": "P2", "precio": 20, "unidades": 50}],
        x_label="precio",
        y_label="unidades",
    )
    contract_scatter = build_visual_contract("scatter", raw_scatter)
    assert contract_scatter.chart_type == "scatter"


# ── 2. VALIDACIÓN ESTRUCTURAL Y PALETA SAAS ───────────────────────────────────

def test_validate_and_normalize_injects_saas_palette_and_tooltip():
    """Inyecta paleta SaaS y tooltip adecuado según el tipo de gráfico."""
    incomplete_option = {
        "title": {"text": "Gráfico Básico"},
        "series": [{"type": "bar", "data": [10, 20]}],
    }

    normalized = validate_and_normalize_echarts_option(incomplete_option, chart_type="bar")

    # Inyección de paleta de colores accesible
    assert "color" in normalized
    assert normalized["color"] == SAAS_ACCESSIBLE_PALETTE

    # Inyección de tooltip con trigger 'axis' para cartesiano
    assert "tooltip" in normalized
    assert normalized["tooltip"]["trigger"] == "axis"

    # Inyección de ejes requeridos
    assert "xAxis" in normalized
    assert "yAxis" in normalized


def test_validate_pie_tooltip_trigger_item():
    """Gráficos circulares deben configurar tooltip trigger como 'item'."""
    pie_opt = {"series": [{"type": "pie", "data": [{"name": "A", "value": 1}]}]}
    normalized = validate_and_normalize_echarts_option(pie_opt, chart_type="pie")
    assert normalized["tooltip"]["trigger"] == "item"


# ── 3. ACCESIBILIDAD ──────────────────────────────────────────────────────────

def test_accessibility_description_generation():
    """Verifica que el contrato genere una descripción textual accesible para screen readers."""
    opt = ChartFactory.build_bar_chart(
        "Ingresos por Sucursal",
        [{"name": "Sucursal A", "value": 1500}],
    )
    contract = build_visual_contract("bar", opt)
    desc = contract.metadata["accessibility_description"]

    assert "BAR" in desc
    assert "Ingresos por Sucursal" in desc
    assert "punto(s) de datos" in desc


# ── 4. FALLBACK VISUAL SEGURO ─────────────────────────────────────────────────

def test_fallback_visual_safe_degradation():
    """Verifica la degradación suave a KPI o Tabla ante datos insuficientes."""
    # 0 filas -> degrada a table
    fallback_empty, target_empty = should_fallback_to_kpi_or_table([], "bar")
    assert fallback_empty is True
    assert target_empty == "table"

    # 1 sola fila en cartesiano -> degrada a kpi
    fallback_single, target_single = should_fallback_to_kpi_or_table([{"name": "Total", "value": 500}], "bar")
    assert fallback_single is True
    assert target_single == "kpi"

    # 3 filas -> no degrada
    fallback_ok, target_ok = should_fallback_to_kpi_or_table(
        [{"name": "A", "value": 10}, {"name": "B", "value": 20}, {"name": "C", "value": 30}],
        "bar",
    )
    assert fallback_ok is False
    assert target_ok == "bar"


# ── 5. INTEGRACIÓN EN EL PIPELINE (build_chart_config) ────────────────────────

def test_build_chart_config_attaches_visual_contract():
    """build_chart_config adjunta el contrato visual dentro de la opción de ECharts."""
    plan = AnalysisPlan(
        title="Top 5 Categorías",
        main_intent=DistributionIntent(
            rationale="Distribución de categorías",
            dimension="categoria",
            metric="ventas",
            metric_unit=MetricUnit.CURRENCY,
        ),
    )
    ibis_output = {
        "chart_type": "bar",
        "data": [
            {"name": "Electrónica", "value": 5000},
            {"name": "Hogar", "value": 3000},
        ],
        "x_axis": "categoria",
        "y_axis": "ventas",
    }

    items, exec_updates = build_chart_config(
        plan=plan,
        ibis_output=ibis_output,
        plan_idx=0,
        explicit_visual_requests=[],
        format_override={},
        currency_meta={"symbol": "$", "code": "USD"},
        actual_prompt="top categorias",
        filtered_granular_df=None,
        schema_profile={},
    )

    echart_items = [it for it in items if it.get("type") == "configuracion_echarts"]
    assert len(echart_items) >= 1
    opt = echart_items[0]["option"]
    assert "visual_contract" in opt
    vc = opt["visual_contract"]
    assert vc["chart_type"] == "bar"
    assert vc["format_currency"] == "$"
    assert vc["metadata"]["data_point_count"] == 2
