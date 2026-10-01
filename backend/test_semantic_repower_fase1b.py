"""
test_semantic_repower_fase1b.py
═══════════════════════════════════════════════════════════════════
Fase 1b — Rigor matemático & fechas (solo reparación):

  F1.6 — El schema profile expone unique_values para columnas categóricas de
         baja cardinalidad, habilitando la detección de dirección por VALORES
         (antes solo por nombre + cardinalidad exactamente 2).

Tests deterministas, sin LLM.
"""
from __future__ import annotations

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))

from app.services.canonical_schema_profiler import build_canonical_schema_profile
from app.services.dashboard_narrative import _reconcile_executive_summary
from app.services.direction_detector import should_split_by_flow_direction
from app.services.semantic_translator.temporal_resolver import resolve_temporal_filter_value


def test_schema_profile_exposes_unique_values_low_cardinality() -> None:
    df = pd.DataFrame(
        [
            {"tipo_movimiento": "Ingreso", "monto": 10},
            {"tipo_movimiento": "Egreso", "monto": 20},
            {"tipo_movimiento": "Transferencia", "monto": 30},
        ]
    )
    _, schema_profile, _ = build_canonical_schema_profile(df)
    assert "unique_values" in schema_profile["tipo_movimiento"]
    assert set(schema_profile["tipo_movimiento"]["unique_values"]) == {
        "Ingreso",
        "Egreso",
        "Transferencia",
    }


def test_direction_detected_from_values_cardinality_3() -> None:
    df = pd.DataFrame(
        [
            {"tipo_movimiento": "Ingreso", "monto": 10},
            {"tipo_movimiento": "Egreso", "monto": 20},
            {"tipo_movimiento": "Transferencia", "monto": 30},
        ]
    )
    _, schema_profile, _ = build_canonical_schema_profile(df)
    decision = should_split_by_flow_direction(schema_profile)
    assert decision["should_split"] is True
    assert decision["column_name"] == "tipo_movimiento"


def test_direction_not_detected_for_non_antonym_values() -> None:
    df = pd.DataFrame(
        [
            {"cargo": "Asistente", "estado": "Activo"},
            {"cargo": "Gerente", "estado": "Inactivo"},
        ]
    )
    _, schema_profile, _ = build_canonical_schema_profile(df)
    decision = should_split_by_flow_direction(schema_profile)
    assert decision["should_split"] is False


# ── F1.5: árbitro numérico del resumen ejecutivo ──────────────────────
def test_reconcile_adds_caveat_for_unverified_figure() -> None:
    widgets = [{"facts": ["Ingresos por Mes: líder Enero con valor 1000 (30% del total)."]}]
    payload = {
        "headline": "Resumen",
        "overview": "El total consolidado fue 999999.",
        "key_findings": [],
        "actions": [],
        "risks": [],
        "caveats": [],
    }
    out = _reconcile_executive_summary(payload, widgets)
    assert any("no pudieron verificarse" in caveat for caveat in out["caveats"])


def test_reconcile_no_caveat_when_figures_supported() -> None:
    widgets = [{"facts": ["Ingresos totales: 1000"]}]
    payload = {
        "headline": "Resumen",
        "overview": "El total consolidado fue 1000.",
        "key_findings": [],
        "actions": [],
        "risks": [],
        "caveats": [],
    }
    out = _reconcile_executive_summary(payload, widgets)
    assert out["caveats"] == []



# ── F1.4: resolvedor temporal ─────────────────────────────────────────
def test_between_months_cross_year() -> None:
    result = resolve_temporal_filter_value(
        "fecha", "between", "noviembre and febrero", schema_profile={"_dataset_year": 2025}
    )
    assert result is not None
    assert result[0]["operator"] == ">=" and result[0]["value"] == "2025-11-01"
    assert result[1]["operator"] == "<=" and result[1]["value"].startswith("2026-02")


def test_between_iso_year_corrected() -> None:
    result = resolve_temporal_filter_value(
        "fecha", "between", "2023-01-01 and 2023-06-30", schema_profile={"_dataset_year": 2021}
    )
    assert result is not None
    assert result[0]["value"] == "2021-01-01"
    assert result[1]["value"] == "2021-06-30"


def test_contiguous_months_still_range() -> None:
    result = resolve_temporal_filter_value(
        "fecha", "in", ["junio", "julio"], schema_profile={"_dataset_year": 2025}
    )
    assert result is not None
    assert result[0]["value"] == "2025-06-01"
    assert result[1]["value"] == "2025-07-31"


def test_noncontiguous_months_approximate_range() -> None:
    result = resolve_temporal_filter_value(
        "fecha", "in", ["enero", "junio"], schema_profile={"_dataset_year": 2025}
    )
    assert result is not None
    assert result[0]["value"] == "2025-01-01"
    assert result[1]["value"] == "2025-06-30"


# ── F1.2: agregación no aditiva ───────────────────────────────────────
def test_additive_metric_stays_sum() -> None:
    from app.core.semantic_grammar import TimeTrendIntent
    from app.services.ibis_engine import IbisEngine

    intent = TimeTrendIntent(rationale="x", date_column="fecha", value_column="ventas")
    assert IbisEngine._metric_aggregation_method(intent, "ventas") == "sum"


def test_percentage_unit_uses_mean() -> None:
    from app.core.semantic_grammar import TimeTrendIntent
    from app.services.ibis_engine import IbisEngine

    intent = TimeTrendIntent(
        rationale="x", date_column="fecha", value_column="margen", metric_unit="percentage"
    )
    assert IbisEngine._metric_aggregation_method(intent, "margen") == "mean"


def test_non_additive_token_uses_mean() -> None:
    from app.core.semantic_grammar import DistributionIntent
    from app.services.ibis_engine import IbisEngine

    intent = DistributionIntent(rationale="x", dimension="depto", metric="precio_promedio")
    assert IbisEngine._metric_aggregation_method(intent, "precio_promedio") == "mean"


def test_explicit_avg_aggregation_uses_mean() -> None:
    from app.core.semantic_grammar import TimeTrendIntent
    from app.services.ibis_engine import IbisEngine

    intent = TimeTrendIntent(
        rationale="x", date_column="fecha", value_column="ventas", aggregation="avg"
    )
    assert IbisEngine._metric_aggregation_method(intent, "ventas") == "mean"


# ── F1.5: sin falso positivo por formato de número (float sin separador) ─
def test_reconcile_handles_unseparated_fact_numbers() -> None:
    widgets = [{"facts": ["Salario por Departamento: líder IT con valor 8679889.19."]}]
    payload = {
        "headline": "Resumen",
        "overview": "IT alcanzó 8,679,889.19 en salario.",
        "key_findings": [],
        "actions": [],
        "risks": [],
        "caveats": [],
    }
    out = _reconcile_executive_summary(payload, widgets)
    assert out["caveats"] == []


# ── F1.5: cifras derivadas se aceptan; inventadas se marcan ───────────
def test_derived_difference_is_accepted() -> None:
    from app.services.narrative_reconciliation_guard import reconcile_narrative_with_facts

    facts = {"v": [8679889.19, 8072526.56]}
    _, unverified = reconcile_narrative_with_facts(
        "IT 8,679,889.19 y Finanzas 8,072,526.56, diferencia de 607,363", facts
    )
    assert unverified == []


def test_invented_figure_is_flagged() -> None:
    from app.services.narrative_reconciliation_guard import reconcile_narrative_with_facts

    facts = {"v": [8679889.19, 8072526.56]}
    _, unverified = reconcile_narrative_with_facts(
        "El total inventado fue 999,999,999", facts
    )
    assert any(item["raw_text"] == "999,999,999" for item in unverified)

