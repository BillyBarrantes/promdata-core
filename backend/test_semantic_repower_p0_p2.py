"""
test_semantic_repower_p0_p2.py
═══════════════════════════════════════════════════════════════════
Regression gate de la repotenciación semántica (2026-09):

  P0.2 — Pre-agregación multi-hoja NO debe destruir columnas requeridas
         por el plan (dimensión/fecha/filtros).
  P0.3 — El fast-path macro no debe interceptar prompts con periodo o
         fecha concreta (2021-W30, "semana 30", "60 días", ISO date).
  P1   — Ventanas relativas deterministas ("próximos/últimos N ...") que
         garantizan AMBAS cotas ancladas al reference_date.
  P2   — Periodos ISO-week ("2021-W30", "semana 30 del 2021") resueltos a
         rango lunes-domingo ISO.
"""
from __future__ import annotations

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))

from app.core.semantic_grammar import (
    AnalysisPlan,
    DataFilter,
    DistributionIntent,
    PreAggregationSpec,
)
from app.services.ibis_engine import IbisEngine
from app.services.semantic_translator.core import (
    contains_concrete_temporal_specifier,
    looks_broad_analysis_request,
)
from app.services.semantic_translator.temporal_resolver import (
    _parse_week_token,
    _window_bounds,
    apply_relative_time_windows,
    detect_relative_window,
    normalize_intent_temporal_filters,
    resolve_temporal_filter_value,
)


def _distribution_plan(
    *,
    dimension: str,
    metric: str = "stock_disponible",
    filters: list[DataFilter] | None = None,
    pre_aggregation: PreAggregationSpec | None = None,
) -> AnalysisPlan:
    return AnalysisPlan(
        main_intent=DistributionIntent(
            rationale="test",
            dimension=dimension,
            metric=metric,
            limit=10,
            filters=filters or [],
        ),
        title="Test",
        pre_aggregation=pre_aggregation,
    )


# ═══════════════════════════════════════════════════════════════════
# P0.2 — Pre-agregación no destructiva
# ═══════════════════════════════════════════════════════════════════
def test_plan_required_columns_includes_dimension_and_filters() -> None:
    """El guard debe ver la dimensión y las columnas de filtro del plan."""
    plan = _distribution_plan(
        dimension="texto_breve_de_material",
        filters=[DataFilter.model_validate(
            {"column": "fecaduc_feprefercons", "operator": ">=", "value": "2021-07-31"}
        )],
    )
    required = IbisEngine._plan_required_primary_columns(plan)
    assert "texto_breve_de_material" in required
    assert "fecaduc_feprefercons" in required
    assert "stock_disponible" in required


def test_preaggregation_skipped_when_plan_needs_dropped_column(capsys, tmp_path) -> None:
    """Si la pre-agregación eliminaría una dimensión del plan, se omite."""
    primary_df = pd.DataFrame({
        "material": [f"M{i % 10:02d}" for i in range(100)],
        "texto_breve_de_material": [f"Producto {i % 10}" for i in range(100)],
        "stock_disponible": [float(i) for i in range(100)],
    })
    parquet_path = os.path.join(str(tmp_path), "primary.parquet")
    primary_df.to_parquet(parquet_path)

    plan = _distribution_plan(
        dimension="texto_breve_de_material",
        pre_aggregation=PreAggregationSpec(
            group_by=["material"], metrics=["stock_disponible"], aggregation="sum",
        ),
    )
    result = IbisEngine.execute_plan(parquet_path=parquet_path, plan=plan, recipe_mode=True)
    output = capsys.readouterr().out

    assert "Pre-agregación OMITIDA" in output, output
    assert "error" not in result, result
    assert result.get("type") == "echarts"


def test_preaggregation_runs_when_columns_preserved(capsys, tmp_path) -> None:
    """Si el plan solo necesita el group_by y la métrica, la pre-agregación corre."""
    primary_df = pd.DataFrame({
        "material": [f"M{i % 10:02d}" for i in range(100)],
        "stock_disponible": [float(i) for i in range(100)],
    })
    parquet_path = os.path.join(str(tmp_path), "primary.parquet")
    primary_df.to_parquet(parquet_path)

    plan = _distribution_plan(
        dimension="material",
        pre_aggregation=PreAggregationSpec(
            group_by=["material"], metrics=["stock_disponible"], aggregation="sum",
        ),
    )
    result = IbisEngine.execute_plan(parquet_path=parquet_path, plan=plan, recipe_mode=True)
    output = capsys.readouterr().out

    assert "Pre-agregación OMITIDA" not in output, output
    assert "Tabla PRIMARIA pre-agregada" in output, output
    assert "error" not in result, result


# ═══════════════════════════════════════════════════════════════════
# P0.3 — Fast-path macro no intercepta periodos concretos
# ═══════════════════════════════════════════════════════════════════
def test_concrete_temporal_specifier_detects_iso_week() -> None:
    assert contains_concrete_temporal_specifier("stock correspondiente a 2021-w30")
    assert contains_concrete_temporal_specifier("materiales de la semana 30 del 2021")
    assert contains_concrete_temporal_specifier("productos a vencer en los proximos 60 dias")
    assert contains_concrete_temporal_specifier("ventas del 2021-07-31")


def test_looks_broad_skips_week_prompt() -> None:
    """Un prompt con semana/periodo concreto NO es broad (no macro bypass)."""
    assert looks_broad_analysis_request(
        "realiza un analisis del stock correspondiente a 2021-W30"
    ) is False


def test_looks_broad_keeps_generic_prompt() -> None:
    """No-regresión: el prompt genérico sigue usando el fast-path macro."""
    assert looks_broad_analysis_request("realiza un analisis gerencial") is True


# ═══════════════════════════════════════════════════════════════════
# P1 — Ventanas relativas deterministas
# ═══════════════════════════════════════════════════════════════════
def test_detect_relative_window_es_en() -> None:
    assert detect_relative_window("productos a vencer en los próximos 60 días") == {
        "direction": "next", "quantity": 60, "unit": "dia"
    }
    assert detect_relative_window("ultimos 3 meses") == {
        "direction": "last", "quantity": 3, "unit": "mes"
    }
    assert detect_relative_window("next 2 weeks") == {
        "direction": "next", "quantity": 2, "unit": "semana"
    }
    assert detect_relative_window("sin ventana temporal") is None


def test_window_bounds_next_60_days() -> None:
    assert _window_bounds(
        {"direction": "next", "quantity": 60, "unit": "dia"}, "2021-07-31"
    ) == ("2021-07-31", "2021-09-29")


def test_apply_relative_window_adds_upper_bound() -> None:
    """El LLM emite solo '>= referencia'; el resolver agrega '<= referencia+N'."""
    plan = _distribution_plan(
        dimension="material",
        filters=[DataFilter.model_validate(
            {"column": "fecaduc_feprefercons", "operator": ">=", "value": "2021-07-31"}
        )],
    )
    schema = {
        "fecaduc_feprefercons": {"type": "temporal", "role": "date"},
        "fecha_de_stock": {"type": "temporal", "role": "date"},
        "material": {"type": "identifier"},
        "stock_disponible": {"role": "metric"},
    }
    out = apply_relative_time_windows(
        [plan],
        "productos a vencer en los proximos 60 dias",
        reference_date="2021-07-31",
        schema_profile=schema,
        time_axis="fecha_de_stock",
    )
    filters = out[0].main_intent.filters
    bounds = {(f.column, str(f.value)) for f in filters}
    assert ("fecaduc_feprefercons", "2021-07-31") in bounds
    assert ("fecaduc_feprefercons", "2021-09-29") in bounds


def test_apply_relative_window_noop_without_temporal_filter() -> None:
    """Sin filtro temporal no se inventa columna (degradación segura)."""
    plan = _distribution_plan(dimension="material")
    out = apply_relative_time_windows(
        [plan], "proximos 30 dias", reference_date="2021-07-31",
        schema_profile={"material": {"role": "identifier"}},
    )
    assert out[0].main_intent.filters == []


# ═══════════════════════════════════════════════════════════════════
# P2 — Periodos ISO-week → rango de fechas
# ═══════════════════════════════════════════════════════════════════
def test_parse_week_token_formats() -> None:
    expected = ("2021-07-26", "2021-08-01")
    assert _parse_week_token("2021-W30", "fecha_de_stock", {"_dataset_year": 2021}) == expected
    assert _parse_week_token("2021w30", "fecha_de_stock", {"_dataset_year": 2021}) == expected
    assert _parse_week_token("semana 30 del 2021", "fecha_de_stock", {"_dataset_year": 2021}) == expected
    assert _parse_week_token(
        "semana 30", "fecha_de_stock", {"_dataset_year": 2021}
    ) == expected


def test_resolve_temporal_filter_value_expands_iso_week() -> None:
    resolved = resolve_temporal_filter_value(
        "fecha_de_stock", "==", "2021-W30", schema_profile={"_dataset_year": 2021}
    )
    assert resolved == [
        {"column": "fecha_de_stock", "operator": ">=", "value": "2021-07-26"},
        {"column": "fecha_de_stock", "operator": "<=", "value": "2021-08-01"},
    ]


def test_normalize_intent_temporal_filters_expands_week() -> None:
    plan = _distribution_plan(
        dimension="material",
        filters=[DataFilter.model_validate(
            {"column": "fecha_de_stock", "operator": "==", "value": "2021-W30"}
        )],
    )
    schema = {
        "fecha_de_stock": {"type": "temporal", "role": "date"},
        "_dataset_year": 2021,
    }
    corrected = normalize_intent_temporal_filters(plan.main_intent, schema)
    assert corrected is not plan.main_intent
    values = sorted(str(f.value) for f in corrected.filters)
    assert values == ["2021-07-26", "2021-08-01"]
