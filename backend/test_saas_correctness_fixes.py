"""[2026-09] Correcciones de corrección/UX reportadas en pruebas reales (P2–P6).

Blindan las 6 clases de fallo detectadas con datos reales, sin tocar guards ni
contratos F0–F4:

- P2: orden cronológico del gráfico combinado (mes+año).
- P3: grano temporal cuando el span es desconocido + no degradar series a tabla.
- P4: ranking "top N" habilita Pareto.
- P5-A: no pisar visuales especializados válidos con un override premium.
- P5-B: segunda categórica del prompt como color/serie del scatter.
- P6: divulgación honesta de concepto no cubierto (nunca sustitución silenciosa).
"""
from __future__ import annotations

import ibis
import pandas as pd
import pytest

from app.core.semantic_grammar import (
    AnalysisPlan,
    DiagnosticIntent,
    DistributionIntent,
    TimeTrendIntent,
    VisualProtocol,
)
from app.core.time_grain import TimeGrain, resolve_time_grain
from app.services.canonical_tabular_canary_executor import (
    _premium_promotion_allowed,
)
from app.services.chart_factory import ChartFactory
from app.services.ibis_engine import IbisEngine
from app.services.smart_table_builder import should_use_smart_table
from app.services.semantic_translator.validator import (
    apply_coverage_disclosure,
    build_coverage_disclosure,
)
from app.services.visual_recommendation_engine import (
    _build_allowed_replacements,
    _recommend_visual,
)


# ══════════════════════════════════════════════════════════════════════
# P2 — Orden cronológico del combo (mes + año)
# ══════════════════════════════════════════════════════════════════════

def test_order_temporal_categories_month_year() -> None:
    data = [
        {"name": "Mayo 2021", "stock": 30, "mom_pct": 5.0},
        {"name": "Junio 2021", "stock": 10, "mom_pct": -2.0},
        {"name": "Marzo 2021", "stock": 50, "mom_pct": 1.0},
        {"name": "Abril 2021", "stock": 20, "mom_pct": 3.0},
    ]
    is_temporal, ordered = ChartFactory._order_temporal_categories(data)
    assert is_temporal is True
    assert [d["name"] for d in ordered] == [
        "Marzo 2021", "Abril 2021", "Mayo 2021", "Junio 2021",
    ]


def test_combo_preserves_chronology_not_value_order() -> None:
    data = [
        {"name": "Mayo 2021", "stock": 30, "mom_pct": 5.0},
        {"name": "Junio 2021", "stock": 10, "mom_pct": -2.0},
        {"name": "Marzo 2021", "stock": 50, "mom_pct": 1.0},
        {"name": "Abril 2021", "stock": 20, "mom_pct": 3.0},
    ]
    option = ChartFactory.build_combo_chart("Combinado", data)
    assert option["xAxis"]["data"] == [
        "Marzo 2021", "Abril 2021", "Mayo 2021", "Junio 2021",
    ]


def test_combo_non_temporal_still_sorted_by_value() -> None:
    data = [
        {"name": "A", "v": 1, "w": 0.1},
        {"name": "B", "v": 9, "w": 0.9},
        {"name": "C", "v": 5, "w": 0.5},
    ]
    option = ChartFactory.build_combo_chart("Ranking", data)
    assert option["xAxis"]["data"] == ["B", "C", "A"]


# ══════════════════════════════════════════════════════════════════════
# P3 — Grano con span desconocido + no degradar series temporales a tabla
# ══════════════════════════════════════════════════════════════════════

def test_grain_unknown_span_does_not_collapse_to_day() -> None:
    assert resolve_time_grain(TimeGrain.MONTH, None, 403) == TimeGrain.MONTH


def test_grain_known_span_still_refines() -> None:
    # 30 días con grano mensual → 1 periodo → refina a semanal (>=2).
    assert resolve_time_grain(TimeGrain.MONTH, 30.0, 30) == TimeGrain.WEEK


def test_grain_explicit_prompt_is_respected() -> None:
    assert resolve_time_grain(TimeGrain.MONTH, None, 403, prompt_explicit=TimeGrain.DAY) == TimeGrain.DAY


def test_smart_table_not_applied_over_temporal_axis() -> None:
    months = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio",
              "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
    labels = [f"{months[i % 12]} {2018 + i // 12}" for i in range(40)]
    option = {"xAxis": {"type": "category", "data": labels}}
    assert should_use_smart_table(option, chart_type="dual_axis_chart") is False


def test_smart_table_still_applied_over_dense_categorical() -> None:
    option = {
        "xAxis": {
            "type": "category",
            "data": [f"SKU-{i}" for i in range(1, 41)],
        }
    }
    assert should_use_smart_table(option, chart_type="bar_chart") is True


# ══════════════════════════════════════════════════════════════════════
# P4 — Ranking "top N" habilita Pareto
# ══════════════════════════════════════════════════════════════════════

def _distribution_plan(ranking: bool = True) -> AnalysisPlan:
    return AnalysisPlan(
        main_intent=DistributionIntent(
            rationale="x",
            dimension="sku",
            metric="ventas",
            ranking_metric="ventas" if ranking else None,
            limit=10,
            visual_protocol=VisualProtocol.BAR,
        ),
        title="Top productos",
        column_aliases={},
    )


def _flat_distribution(rows: int = 10) -> dict:
    return {"data": [{"name": f"p{i}", "value": 100 - i * 10} for i in range(rows)]}


def test_ranking_recommends_pareto_even_with_low_concentration() -> None:
    visual, _ = _recommend_visual(_distribution_plan(ranking=True), _flat_distribution())
    assert visual == "pareto_chart"


def test_non_ranking_low_concentration_stays_bar() -> None:
    visual, _ = _recommend_visual(_distribution_plan(ranking=False), _flat_distribution())
    assert visual != "pareto_chart"


def test_pareto_is_in_allowed_replacements_for_topn() -> None:
    allowed = _build_allowed_replacements(_distribution_plan(ranking=True), _flat_distribution())
    assert "pareto_chart" in allowed


# ══════════════════════════════════════════════════════════════════════
# P5-A — No pisar visuales especializados válidos
# ══════════════════════════════════════════════════════════════════════

def test_premium_promotion_blocks_specialized_visuals() -> None:
    assert _premium_promotion_allowed("scatter_plot", "bubble_chart") is False
    assert _premium_promotion_allowed("boxplot_chart", "bubble_chart") is False
    assert _premium_promotion_allowed("heatmap_chart", "bubble_chart") is False


def test_premium_promotion_allows_base_visuals() -> None:
    assert _premium_promotion_allowed("bar_chart", "dual_axis_chart") is True
    assert _premium_promotion_allowed("line_chart", "pareto_chart") is True


def test_premium_promotion_noop_when_equal_or_unknown() -> None:
    assert _premium_promotion_allowed("bar_chart", "bar_chart") is False
    assert _premium_promotion_allowed("scatter_plot", "scatter_plot") is False
    assert _premium_promotion_allowed("bar_chart", None) is False


# ══════════════════════════════════════════════════════════════════════
# P5-B — Segunda categórica del prompt → color/serie del scatter
# ══════════════════════════════════════════════════════════════════════

def test_diagnostic_scatter_uses_second_categorical_as_series() -> None:
    df = pd.DataFrame(
        {
            "producto": ["A", "A", "A", "A", "B", "B", "B", "B"],
            "metodo_pago": ["Tarjeta", "Tarjeta", "Efectivo", "Efectivo",
                             "Tarjeta", "Tarjeta", "Efectivo", "Efectivo"],
            "precio": [10, 12, 11, 13, 20, 22, 21, 23],
            "cantidad": [1, 2, 3, 4, 5, 6, 7, 8],
            "peso": [5, 5, 5, 5, 9, 9, 9, 9],
        }
    )
    intent = DiagnosticIntent(
        rationale="x",
        metrics=["precio", "cantidad"],
        dimension="producto",
        group_by=["producto", "metodo_pago"],
        visual_protocol=VisualProtocol.SCATTER,
    )
    out = IbisEngine._analyze_diagnostic(ibis.memtable(df), intent)

    assert out["chart_type"] == "scatter"
    series_values = {row.get("series") for row in out["data"]}
    assert series_values == {"Tarjeta", "Efectivo"}


def test_diagnostic_scatter_without_group_by_is_unchanged() -> None:
    df = pd.DataFrame(
        {
            "producto": ["A", "B", "C"],
            "precio": [10, 20, 30],
            "cantidad": [1, 2, 3],
        }
    )
    intent = DiagnosticIntent(
        rationale="x",
        metrics=["precio", "cantidad"],
        dimension="producto",
        visual_protocol=VisualProtocol.SCATTER,
    )
    out = IbisEngine._analyze_diagnostic(ibis.memtable(df), intent)
    assert all(row.get("series") == row.get("name") for row in out["data"])


# ══════════════════════════════════════════════════════════════════════
# P6 — Divulgación honesta de concepto no cubierto
# ══════════════════════════════════════════════════════════════════════

def _trend_plan(value_column: str = "stock_disponible") -> AnalysisPlan:
    return AnalysisPlan(
        main_intent=TimeTrendIntent(
            rationale="x",
            date_column="fecha_de_stock",
            value_column=value_column,
        ),
        title="Evolución",
        column_aliases={},
    )


def test_disclosure_when_single_named_measure_is_absent() -> None:
    schema = {"fecha_de_stock": {}, "stock_disponible": {}, "material": {}}
    note = build_coverage_disclosure(
        [_trend_plan()], schema, "evolucion en el tiempo de ventas"
    )
    assert note is not None
    assert "ventas" in note
    assert "stock_disponible" in note


def test_no_disclosure_for_covered_prompt() -> None:
    schema = {"stock_disponible": {}, "material": {}}
    note = build_coverage_disclosure(
        [_trend_plan()], schema, "top productos por stock"
    )
    assert note is None


def test_disclosure_from_llm_extra_concepts() -> None:
    schema = {"fecha_de_stock": {}, "stock_disponible": {}}
    note = build_coverage_disclosure(
        [_trend_plan()], schema, "relacion de margen y utilidad",
        extra_concepts=["margen", "utilidad"],
    )
    assert note is not None
    assert "margen" in note and "utilidad" in note


def test_apply_disclosure_sets_plan_field() -> None:
    schema = {"fecha_de_stock": {}, "stock_disponible": {}}
    plan = _trend_plan()
    assert plan.coverage_disclosure is None
    apply_coverage_disclosure([plan], schema, "evolucion en el tiempo de ventas")
    assert plan.coverage_disclosure is not None
    assert "ventas" in plan.coverage_disclosure


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
