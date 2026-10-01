"""
test_semantic_repower_fase2.py
═══════════════════════════════════════════════════════════════════
Fase 2 — Activar inteligencia dormida (memoria de sesión):

  D3 — La capa de memoria (continuity/bypass/intent) se activa en el path
       de producción (no solo legacy).
  D4 — El drill-down usa las dimensiones reales del análisis previo en vez
       del parse legacy "Agrupado por:" que nunca se emitía.

Tests deterministas, sin LLM.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from app.services.canonical_tabular_production_executor import _extract_previous_dimensions
from app.services.semantic_translator.memory import classify_memory_intent


def test_extract_previous_dimensions_from_semantic_context() -> None:
    parent_context = {
        "semantic_context": {
            "plans": [
                {"dimensions": ["departamento"], "split_dimension": "cargo"},
                {"dimensions": ["departamento"]},
            ]
        }
    }
    assert _extract_previous_dimensions(parent_context) == ["departamento", "cargo"]


def test_extract_previous_dimensions_empty() -> None:
    assert _extract_previous_dimensions(None) == []
    assert _extract_previous_dimensions({}) == []


def test_drill_down_uses_real_previous_dimension() -> None:
    previous_dimensions = _extract_previous_dimensions(
        {"semantic_context": {"plans": [{"dimensions": ["departamento"]}]}}
    )
    instruction = classify_memory_intent("y ahora por cargo?", "CONTEXTO", previous_dimensions)
    assert "DRILL-DOWN" in instruction
    assert "departamento" in instruction
    assert "Unknown" not in instruction


def test_drill_down_without_dimensions_still_returns_block() -> None:
    instruction = classify_memory_intent("profundiza el análisis", "CONTEXTO")
    assert "DRILL-DOWN" in instruction


def test_complement_intent_returns_block() -> None:
    instruction = classify_memory_intent("dame otro ángulo distinto", "CONTEXTO")
    assert "COMPLEMENTARIO" in instruction


# ── D5: alineación métrica-semántica en el path canónico ──────────────
def test_metric_alignment_swaps_wrong_unit_metric() -> None:
    from app.core.semantic_grammar import AnalysisPlan, DistributionIntent
    from app.services.metric_semantics import align_plan_metrics_with_prompt

    plan = AnalysisPlan(
        main_intent=DistributionIntent(rationale="x", dimension="region", metric="cantidad"),
        title="t",
    )
    schema = {"ventas": {"role": "metric"}, "cantidad": {"role": "metric"}}
    out = align_plan_metrics_with_prompt([plan], "dame las ventas totales por region", schema)
    assert getattr(out[0].main_intent, "metric", None) == "ventas"


def test_metric_alignment_keeps_correct_metric() -> None:
    from app.core.semantic_grammar import AnalysisPlan, DistributionIntent
    from app.services.metric_semantics import align_plan_metrics_with_prompt

    plan = AnalysisPlan(
        main_intent=DistributionIntent(rationale="x", dimension="region", metric="cantidad"),
        title="t",
    )
    schema = {"ventas": {"role": "metric"}, "cantidad": {"role": "metric"}}
    out = align_plan_metrics_with_prompt([plan], "dame la cantidad por region", schema)
    assert getattr(out[0].main_intent, "metric", None) == "cantidad"


# ── D2: constraint guard marca restricciones no satisfechas ───────────
def test_constraint_guard_flags_temporal_top_n_without_trend() -> None:
    from app.services.semantic_translator.validator import fast_path_unresolved_constraints

    unresolved = fast_path_unresolved_constraints("solo la evolucion mensual top 5 productos", [])
    assert "temporal_top_n_requires_trend" in unresolved


def test_constraint_guard_silent_for_simple_prompt() -> None:
    from app.services.semantic_translator.validator import fast_path_unresolved_constraints

    assert fast_path_unresolved_constraints("dame las ventas por region", []) == []


# ── D1: contrato analítico (observabilidad) ───────────────────────────
def test_analytical_contract_valid_for_resolvable_plan() -> None:
    from app.core.semantic_grammar import AnalysisPlan, DistributionIntent
    from app.services.semantic_translator.validator import build_query_analytical_contract

    plan = AnalysisPlan(
        main_intent=DistributionIntent(rationale="x", dimension="region", metric="ventas"),
        title="t",
    )
    contract = build_query_analytical_contract(
        query_id="q1",
        file_id="f1",
        intent_type="distribution",
        plans=[plan],
        columns=["region", "ventas"],
        schema_profile={
            "region": {"role": "dimension", "type": "categorical", "cardinality": 5},
            "ventas": {"role": "metric", "type": "numeric", "cardinality": 100},
        },
    )
    assert contract.state == "valid"


# ── D6: evidence bundle serializable para el drawer ───────────────────
def test_evidence_bundle_serializable_for_chart() -> None:
    from app.core.semantic_grammar import AnalysisPlan, DistributionIntent
    from app.services.evidence_bundle import build_evidence_bundle

    plan = AnalysisPlan(
        main_intent=DistributionIntent(rationale="x", dimension="region", metric="ventas"),
        title="t",
    )
    bundle = build_evidence_bundle(
        plan,
        {"hard_facts": {"top_1_val": 10}, "data": [{"name": "A", "value": 10}], "query_id": "q1"},
    )
    payload = bundle.model_dump(mode="json")
    assert isinstance(payload, dict)
    assert "evidence_id" in payload
    assert "computed_facts" in payload


# ── Regresión: schema_profile con claves inyectadas (int) no debe romper ─
def test_metric_alignment_tolerates_injected_int_keys() -> None:
    from app.core.semantic_grammar import AnalysisPlan, DistributionIntent
    from app.services.metric_semantics import align_plan_metrics_with_prompt

    plan = AnalysisPlan(
        main_intent=DistributionIntent(
            rationale="x", dimension="departamento", metric="salario_mensual"
        ),
        title="t",
    )
    schema = {
        "salario_mensual": {"role": "metric"},
        "departamento": {"role": "dimension"},
        "_dataset_year": 2026,
        "_dataset_year_min": 2018,
        "_dataset_year_max": 2026,
    }
    out = align_plan_metrics_with_prompt(
        [plan], "analiza los salarios por departamento", schema
    )
    assert getattr(out[0].main_intent, "metric", None) == "salario_mensual"
