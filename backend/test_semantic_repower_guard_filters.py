"""
test_semantic_repower_guard_filters.py
═══════════════════════════════════════════════════════════════════
Regression gate (2026-09) — Guard de snapshot + filtros temporales.

  Fix A — Trend sobre fecha ACCESORIA debe aplicar el snapshot guard
           (ADR-TEMPORAL-001), incluso si el contrato trae `date_columns`.
  Fix B — `normalize_intent_temporal_filters` normaliza las TRES listas
           (filters / positive_filters / negative_filters).
  Mejora 1 — Anti-0-silencioso: valores temporales irresolubles se
           descartan; `between` nativo permite negar rangos correctamente.
  Mejora 2 — Trimestre / quincena / año → rango ISO.
  Mejora 3 — Narrativa honesta cuando no hay widgets (sin datos).
"""
from __future__ import annotations

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))

import ibis

from app.core.semantic_grammar import (
    DataFilter,
    DistributionIntent,
)
from app.services.dashboard_narrative import _fallback_summary
from app.services.ibis_engine import IbisEngine
from app.services.semantic_translator.temporal_resolver import (
    normalize_intent_temporal_filters,
    resolve_temporal_filter_value,
)
from app.services.snapshot_guard import should_apply_latest_snapshot_filter


# ═══════════════════════════════════════════════════════════════════
# Fix A — Snapshot guard para trend sobre fecha accesoria
# ═══════════════════════════════════════════════════════════════════
_ACCESSORY_CONTRACT = {
    "snapshot_guard_allowed": True,
    "dataset_mode": "snapshot",
    "time_axis": "fecha_de_stock",
    "date_columns": ["fecaduc_feprefercons", "fecha_de_stock"],
}


def _make_trend(date_column: str):
    from app.core.semantic_grammar import TimeTrendIntent

    return TimeTrendIntent(
        rationale="test",
        date_column=date_column,
        value_column="stock_disponible",
    )


def test_accessory_date_trend_applies_guard_with_date_columns() -> None:
    """Contrato CON date_columns: trend sobre caducidad → guard True."""
    guard = should_apply_latest_snapshot_filter(
        _make_trend("fecaduc_feprefercons"),
        ["fecha_de_stock", "fecaduc_feprefercons", "stock_disponible", "is_latest_snapshot"],
        _ACCESSORY_CONTRACT,
    )
    assert guard is True


def test_time_axis_trend_skips_guard() -> None:
    """No-regresión: trend sobre el time_axis sigue evolucionando (guard False)."""
    guard = should_apply_latest_snapshot_filter(
        _make_trend("fecha_de_stock"),
        ["fecha_de_stock", "fecaduc_feprefercons", "stock_disponible", "is_latest_snapshot"],
        _ACCESSORY_CONTRACT,
    )
    assert guard is False


def test_hybrid_accessory_trend_applies_guard() -> None:
    """Comportamiento documentado: hybrid accessory trend también aísla al corte."""
    contract = dict(_ACCESSORY_CONTRACT, dataset_mode="hybrid")
    guard = should_apply_latest_snapshot_filter(
        _make_trend("fecaduc_feprefercons"),
        ["fecha_de_stock", "fecaduc_feprefercons", "stock_disponible", "is_latest_snapshot"],
        contract,
    )
    assert guard is True


def test_snapshot_distribution_still_applies_guard() -> None:
    """No-regresión: distribución sobre snapshot sigue aplicando el guard."""
    intent = DistributionIntent(
        rationale="test", dimension="tipo_almacen", metric="stock_disponible", filters=[]
    )
    guard = should_apply_latest_snapshot_filter(
        intent,
        ["tipo_almacen", "stock_disponible", "is_latest_snapshot"],
        _ACCESSORY_CONTRACT,
    )
    assert guard is True


# ═══════════════════════════════════════════════════════════════════
# Fix B / Mejora 1 — Normalización de las tres listas
# ═══════════════════════════════════════════════════════════════════
_SCHEMA = {
    "fecaduc_feprefercons": {"type": "temporal", "role": "date"},
    "fecha_de_stock": {"type": "temporal", "role": "date"},
    "material": {"role": "identifier"},
    "stock_disponible": {"role": "metric"},
    "_dataset_year": 2021,
}


def _intent(**kwargs):
    return DistributionIntent(
        rationale="test", dimension="material", metric="stock_disponible", **kwargs
    )


def test_positive_filters_are_normalized() -> None:
    """El string crudo '2021-W30' en positive_filters ya no llega a DuckDB."""
    raw = DataFilter.model_validate(
        {"column": "fecaduc_feprefercons", "operator": "==", "value": "2021-W30"}
    )
    out = normalize_intent_temporal_filters(
        _intent(filters=[raw], positive_filters=[raw]), _SCHEMA
    )
    for f in list(out.filters) + list(out.positive_filters):
        assert str(f.value) != "2021-W30", (f.column, f.operator, f.value)
    values = {str(f.value) for f in out.positive_filters}
    assert values == {"2021-07-26", "2021-08-01"}


def test_negative_filters_range_becomes_single_between() -> None:
    """Un rango negado se emite como UN between (para ~(A&B) correcto)."""
    raw = DataFilter.model_validate(
        {"column": "fecaduc_feprefercons", "operator": "==", "value": "2021-W30"}
    )
    out = normalize_intent_temporal_filters(_intent(negative_filters=[raw]), _SCHEMA)
    assert len(out.negative_filters) == 1
    nf = out.negative_filters[0]
    assert str(nf.operator) in {"between", "FilterOperator.BETWEEN"}
    assert list(nf.value) == ["2021-07-26", "2021-08-01"]


def test_unresolvable_temporal_value_is_dropped() -> None:
    """Anti-0-silencioso: valor temporal crudo irresoluble se descarta."""
    raw = DataFilter.model_validate(
        {"column": "fecha_de_stock", "operator": "==", "value": "60 dias"}
    )
    out = normalize_intent_temporal_filters(_intent(filters=[raw]), _SCHEMA)
    assert list(out.filters) == []


def test_dedup_across_filters_and_positive() -> None:
    """El mismo filtro en filters y positive_filters se deduplica."""
    raw = DataFilter.model_validate(
        {"column": "fecaduc_feprefercons", "operator": "==", "value": "2021-W30"}
    )
    out = normalize_intent_temporal_filters(
        _intent(filters=[raw], positive_filters=[raw]), _SCHEMA
    )
    assert len(out.filters) == 2 and len(out.positive_filters) == 2


def test_symbolic_latest_is_preserved() -> None:
    """No-regresión: el token simbólico 'latest' no se descarta."""
    raw = DataFilter.model_validate(
        {"column": "fecha_de_stock", "operator": "==", "value": "latest"}
    )
    out = normalize_intent_temporal_filters(_intent(filters=[raw]), _SCHEMA)
    assert len(out.filters) == 1
    assert str(out.filters[0].value) == "latest"


# ═══════════════════════════════════════════════════════════════════
# Mejora 1 — between nativo y negación
# ═══════════════════════════════════════════════════════════════════
def test_build_between_and_negation() -> None:
    table = ibis.memtable(pd.DataFrame({
        "fecha": pd.to_datetime([
            "2021-07-01", "2021-07-10", "2021-07-20", "2021-08-05",
        ]),
        "monto": [1, 2, 3, 4],
    }))
    between = DataFilter.model_validate(
        {"column": "fecha", "operator": "between", "value": ["2021-07-10", "2021-07-20"]}
    )
    expr = IbisEngine._build_filter_expression(table, between)
    assert int(table.filter(expr).count().execute()) == 2
    assert int(table.filter(~expr).count().execute()) == 2


# ═══════════════════════════════════════════════════════════════════
# Mejora 2 — Trimestre / quincena / año
# ═══════════════════════════════════════════════════════════════════
def test_quarter_resolution() -> None:
    expected = [
        {"column": "fecha", "operator": ">=", "value": "2021-07-01"},
        {"column": "fecha", "operator": "<=", "value": "2021-09-30"},
    ]
    assert resolve_temporal_filter_value("fecha", "==", "2021-Q3", _SCHEMA) == expected
    assert resolve_temporal_filter_value(
        "fecha", "==", "tercer trimestre de 2021", _SCHEMA
    ) == expected


def test_quincena_resolution() -> None:
    assert resolve_temporal_filter_value(
        "fecha", "==", "segunda quincena de julio 2021", _SCHEMA
    ) == [
        {"column": "fecha", "operator": ">=", "value": "2021-07-16"},
        {"column": "fecha", "operator": "<=", "value": "2021-07-31"},
    ]


def test_bare_year_resolution() -> None:
    assert resolve_temporal_filter_value("fecha", "==", "2021", _SCHEMA) == [
        {"column": "fecha", "operator": ">=", "value": "2021-01-01"},
        {"column": "fecha", "operator": "<=", "value": "2021-12-31"},
    ]


# ═══════════════════════════════════════════════════════════════════
# Mejora 3 — Narrativa honesta ante resultado vacío
# ═══════════════════════════════════════════════════════════════════
def test_fallback_summary_empty_is_honest() -> None:
    summary = _fallback_summary(
        presentation_name="Analisis Universal",
        filter_scope=["fecaduc_feprefercons=2021-W30"],
        widgets=[],
    )
    assert summary["widget_count"] == 0
    assert "sin datos" in summary["headline"].lower()
    assert "no se encontraron registros" in summary["overview"].lower()
