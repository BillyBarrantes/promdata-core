"""Consumo de señales huérfanas del motor (Fase 3, orphans de UX).

Cada señal que el engine calcula (`cumulative`, `conversion`, `share`, `rank`,
`bubble_size`) debe tener un consumidor real o no existir. Estos tests blindan el
consumo sin tocar guards ni contratos.
"""
from __future__ import annotations

import pytest
import ibis
import pandas as pd

from app.core.semantic_grammar import (
    AnalysisPlan,
    DescriptiveIntent,
    DiagnosticIntent,
    DistributionIntent,
    VisualProtocol,
)
from app.services.canonical_tabular_canary_executor import _normalize_chart_type
from app.services.chart_factory import ChartFactory
from app.services.ibis_engine import IbisEngine
from app.services.visual_recommendation_engine import _recommend_visual


def _dist_plan(visual: VisualProtocol = VisualProtocol.BAR) -> AnalysisPlan:
    return AnalysisPlan(
        main_intent=DistributionIntent(
            rationale="x", dimension="sku", metric="ventas", visual_protocol=visual
        ),
        title="Top",
        column_aliases={},
    )


# ── cumulative → Pareto ────────────────────────────────────────────────


def test_recommend_pareto_for_concentrated_distribution() -> None:
    rows = [
        {"name": f"S{i}", "value": v}
        for i, v in enumerate([100, 60, 40, 5, 4, 3, 2, 1])
    ]
    visual, _ = _recommend_visual(_dist_plan(), {"data": rows})
    assert visual == "pareto_chart"


def test_no_pareto_for_flat_distribution() -> None:
    rows = [{"name": f"S{i}", "value": 10} for i in range(8)]
    visual, _ = _recommend_visual(_dist_plan(), {"data": rows})
    assert visual != "pareto_chart"


def test_normalize_pareto_chart_type() -> None:
    assert _normalize_chart_type("pareto_chart") == "pareto"


def test_create_pareto_uses_declared_cumulative() -> None:
    data = [
        {"name": "A", "value": 100, "extra_info": {"cumulative": "10.0%"}},
        {"name": "B", "value": 50, "extra_info": {"cumulative": "20.0%"}},
        {"name": "C", "value": 20, "extra_info": {"cumulative": "99.0%"}},
    ]
    option = ChartFactory.create_chart("pareto", "Concentración", data)

    assert option["series"][0]["type"] == "bar"
    assert option["series"][1]["type"] == "line"
    # El acumulado declarado por el engine debe ganar al recalculado.
    assert option["series"][1]["data"][-1] == 99.0


# ── conversion → funnel (label honesto de conversión) ──────────────────


def test_funnel_option_exposes_declared_conversion() -> None:
    data = [
        {"name": "Visita", "value": 1000, "extra_info": {"conversion": "100.0%"}},
        {"name": "Carrito", "value": 400, "extra_info": {"conversion": "40.0%"}},
        {"name": "Compra", "value": 100, "extra_info": {"conversion": "10.0%"}},
    ]
    option = ChartFactory.create_chart("funnel", "Embudo", data)

    series = option["series"][0]
    conversions = [item.get("conversion") for item in series["data"]]
    assert conversions == ["100.0%", "40.0%", "10.0%"]
    # El tooltip debe referenciar la conversión declarada por punto.
    assert "{@conversion}" in option["tooltip"]["formatter"]


# ── share / rank → etiquetas de barra ──────────────────────────────────


def test_bar_option_exposes_share_and_rank() -> None:
    data = [
        {"name": "A", "value": 50, "extra_info": {"share": "50.0%", "rank": "#1"}},
        {"name": "B", "value": 30, "extra_info": {"share": "30.0%", "rank": "#2"}},
        {"name": "C", "value": 20, "extra_info": {"share": "20.0%", "rank": "#3"}},
    ]
    option = ChartFactory.create_chart("bar", "Participación", data)

    names = [item.get("name") for item in option["series"][0]["data"]]
    assert set(names) == {"A", "B", "C"}
    fields = [item.get("share") for item in option["series"][0]["data"]]
    assert set(fields) == {"50.0%", "30.0%", "20.0%"}
    tooltip = option["tooltip"]
    assert "{@share}" in tooltip["formatter"]
    assert "{@rank}" in tooltip["formatter"]


# ── bubble_size → bubble chart ─────────────────────────────────────────


def _diag_plan() -> AnalysisPlan:
    return AnalysisPlan(
        main_intent=DiagnosticIntent(
            rationale="x",
            metrics=["precio", "cantidad", "peso"],
            dimension="sku",
            visual_protocol=VisualProtocol.SCATTER,
        ),
        title="Correlación",
        column_aliases={},
    )


def test_normalize_bubble_chart_type() -> None:
    assert _normalize_chart_type("bubble_chart") == "bubble"


def test_recommend_bubble_when_three_numeric_magnitudes() -> None:
    rows = [
        {"name": "A", "x_value": 1, "y_value": 2, "size_value": 10},
        {"name": "B", "x_value": 2, "y_value": 3, "size_value": 20},
        {"name": "C", "x_value": 3, "y_value": 4, "size_value": 30},
    ]
    visual, _ = _recommend_visual(_diag_plan(), {"data": rows})
    assert visual == "bubble_chart"


def test_build_bubble_chart_from_extra_info() -> None:
    data = [
        {"name": "A", "x_value": 1, "y_value": 2, "extra_info": {"bubble_size": 10}},
        {"name": "B", "x_value": 2, "y_value": 3, "extra_info": {"bubble_size": 20}},
    ]
    option = ChartFactory.create_chart("bubble", "Burbuja", data, x_label="X", y_label="Y")

    series = option["series"][0]
    assert series["type"] == "scatter"
    assert series["data"][0]["value"] == [1.0, 2.0, 10.0]


def test_diagnostic_scatter_produces_bubble_size() -> None:
    df = pd.DataFrame(
        {
            "fecha": pd.to_datetime(["2021-01-01", "2021-02-01", "2021-03-01"]),
            "sku": ["A", "B", "C"],
            "precio": [10, 20, 30],
            "cantidad": [1, 2, 3],
            "peso": [5, 10, 15],
        }
    )
    intent = _diag_plan().main_intent

    out = IbisEngine._analyze_diagnostic(ibis.memtable(df), intent)

    assert out["chart_type"] == "scatter"
    assert all("size_value" in row for row in out["data"])
    assert all(row.get("extra_info", {}).get("bubble_size") for row in out["data"])


# ── score → scatter tooltip / marca de anomalía ────────────────────────


def test_scatter_option_exposes_score() -> None:
    data = [
        {"name": "A", "x_value": 1, "y_value": 2, "extra_info": {"is_anomaly": True, "score": 0.91}},
        {"name": "B", "x_value": 2, "y_value": 3, "extra_info": {"is_anomaly": True, "score": 0.75}},
    ]
    option = ChartFactory.create_chart("scatter", "Anomalías", data)

    points = option["series"][0]["data"]
    assert points[0]["score"] == 0.91
    assert points[0]["is_anomaly"] is True
    assert "{@score}" in option["tooltip"]["formatter"]


def test_line_anomaly_mark_includes_score() -> None:
    data = [
        {"name": "Ene", "value": 10, "extra_info": {"is_anomaly": True, "score": 0.9}},
        {"name": "Feb", "value": 20, "extra_info": {}},
        {"name": "Mar", "value": 15, "extra_info": {}},
    ]
    option = ChartFactory.create_chart("line", "Anomalías", data)

    series = option["series"][0]
    marks = series.get("markPoint", {}).get("data", [])
    assert any("0.9" in str(mark.get("label", {}).get("formatter")) for mark in marks)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
