"""
test_phase3_semantic_contracts_accuracy.py — Suite de Capa Semántica, Contratos y Exactitud (Fase 3)
══════════════════════════════════════════════════════════════════════════════════════════════════
Verifica:
  1. ColumnResolution unívoca: Coincidencia exacta o dominante resuelve a status='resolved'.
  2. Detección de ambigüedad: Múltiples columnas plausibles se marcan como status='ambiguous'.
  3. Métrica no encontrada: Columna inexistente se clasifica como status='missing'.
  4. Incompatibilidad analítica: SUM/AVG sobre identificadores o fechas se clasifica como status='incompatible'.
  5. Contrato QueryAnalyticalContractV1: Estados binarios 'valid', 'clarification_required', 'blocked'.
  6. Blindaje de planner: Cero sustitución ciega de candidates[0] ante dimensiones ambiguas.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from app.core.analytical_contract import (
    ColumnResolution,
    QueryAnalyticalContractV1,
)
from app.core.semantic_grammar import (
    AnalysisPlan,
    DescriptiveIntent,
    DistributionIntent,
    MetricUnit,
)
from app.services.semantic_translator import (
    build_query_analytical_contract,
    resolve_contract_column,
    resolve_contract_column_resolution,
)


@pytest.fixture
def retail_schema():
    return {
        "columns": [
            "fecha_transaccion", "region", "producto", "canal",
            "unidades", "costo_unitario", "precio_venta", "ingreso_total", "margen", "cliente_id"
        ],
        "profile": {
            "fecha_transaccion": {"type": "temporal", "role": "date", "cardinality": 365},
            "region": {"type": "categorical", "role": "dimension", "cardinality": 5},
            "producto": {"type": "categorical", "role": "dimension", "cardinality": 10},
            "canal": {"type": "categorical", "role": "dimension", "cardinality": 4},
            "cliente_id": {"type": "categorical", "role": "identifier", "cardinality": 800},
            "unidades": {"type": "numeric", "role": "metric", "cardinality": 50},
            "costo_unitario": {"type": "numeric", "role": "metric", "cardinality": 100},
            "precio_venta": {"type": "numeric", "role": "metric", "cardinality": 120},
            "ingreso_total": {"type": "numeric", "role": "metric", "cardinality": 300},
            "margen": {"type": "numeric", "role": "metric", "cardinality": 280},
        }
    }


# ── 1. PRUEBAS DE RESOLUCIÓN UNÍVOCA (STATUS='RESOLVED') ───────────────────────

def test_resolution_exact_match(retail_schema):
    """Coincidencia de nombre exacto resuelve inmediatamente a 'resolved'."""
    res = resolve_contract_column_resolution(
        "ingreso_total",
        retail_schema["columns"],
        schema_profile=retail_schema["profile"],
    )
    assert res.status == "resolved"
    assert res.column_name == "ingreso_total"


def test_resolution_single_dominant_candidate(retail_schema):
    """Segmento que coincide unívocamente con una sola columna resuelve a 'resolved'."""
    res = resolve_contract_column_resolution(
        "margen de ganancia",
        retail_schema["columns"],
        schema_profile=retail_schema["profile"],
        allowed_roles={"metric"},
    )
    assert res.status == "resolved"
    assert res.column_name == "margen"


# ── 2. PRUEBAS DE AMBIGÜEDAD (STATUS='AMBIGUOUS') ──────────────────────────────

def test_resolution_ambiguous_candidates_detected():
    """Término que coincide igualmente con 2 o más columnas debe marcarse como 'ambiguous'."""
    cols = ["fecha_registro", "fecha_despacho", "monto"]
    profile = {
        "fecha_registro": {"type": "temporal", "role": "date"},
        "fecha_despacho": {"type": "temporal", "role": "date"},
        "monto": {"type": "numeric", "role": "metric"},
    }

    # Usuario escribe 'fecha' de forma ambigua
    res = resolve_contract_column_resolution(
        "fecha",
        cols,
        schema_profile=profile,
        allowed_roles={"date"},
    )
    assert res.status == "ambiguous"
    assert res.column_name is None
    assert "fecha_registro" in res.candidates
    assert "fecha_despacho" in res.candidates

    # resolve_contract_column debe retornar None (cero selección a ciegas)
    assert resolve_contract_column("fecha", cols, schema_profile=profile) is None


# ── 3. PRUEBAS DE COLUMNA INEXISTENTE (STATUS='MISSING') ──────────────────────

def test_resolution_missing_column(retail_schema):
    """Columna inexistente se marca como 'missing' y no produce columna inventada."""
    res = resolve_contract_column_resolution(
        "temperatura_ambiente",
        retail_schema["columns"],
        schema_profile=retail_schema["profile"],
    )
    assert res.status == "missing"
    assert res.column_name is None
    assert len(res.candidates) == 0


# ── 4. PRUEBAS DE INCOMPATIBILIDAD ANALÍTICA (STATUS='INCOMPATIBLE') ──────────

def test_resolution_incompatible_aggregation_on_identifier(retail_schema):
    """SUM sobre un identificador (cliente_id) se marca como 'incompatible'."""
    res = resolve_contract_column_resolution(
        "cliente_id",
        retail_schema["columns"],
        schema_profile=retail_schema["profile"],
        aggregation="sum",
    )
    assert res.status == "incompatible"
    assert "no es válida" in res.reason.lower()


def test_resolution_incompatible_aggregation_on_date(retail_schema):
    """AVG sobre una columna de fecha (fecha_transaccion) se marca como 'incompatible'."""
    res = resolve_contract_column_resolution(
        "fecha_transaccion",
        retail_schema["columns"],
        schema_profile=retail_schema["profile"],
        aggregation="avg",
    )
    assert res.status == "incompatible"
    assert "no es válida sobre la columna temporal" in res.reason.lower()


# ── 5. PRUEBAS DE QueryAnalyticalContractV1 (ESTADOS DEL CONTRATO) ─────────────

def test_build_query_contract_valid_state(retail_schema):
    """Plan con métricas y dimensiones válidas produce estado 'valid'."""
    plan = AnalysisPlan(
        title="Ingresos Totales",
        main_intent=DescriptiveIntent(
            rationale="KPI de ingresos",
            metrics=["ingreso_total"],
            metric_unit=MetricUnit.CURRENCY,
        ),
    )

    contract = build_query_analytical_contract(
        query_id="q-001",
        file_id="f-retail",
        intent_type="descriptive",
        plans=[plan],
        columns=retail_schema["columns"],
        schema_profile=retail_schema["profile"],
    )

    assert contract.state == "valid"
    assert len(contract.metrics) == 1
    assert contract.metrics[0].status == "resolved"
    assert contract.block_reason is None


def test_build_query_contract_blocked_on_missing_metric(retail_schema):
    """Plan que hace referencia a una métrica inexistente resulta en estado 'blocked'."""
    plan = AnalysisPlan(
        title="Métrica Fantasma",
        main_intent=DescriptiveIntent(
            rationale="KPI inválido",
            metrics=["volumen_no_existente"],
            metric_unit=MetricUnit.NUMBER,
        ),
    )

    contract = build_query_analytical_contract(
        query_id="q-002",
        file_id="f-retail",
        intent_type="descriptive",
        plans=[plan],
        columns=retail_schema["columns"],
        schema_profile=retail_schema["profile"],
    )

    assert contract.state == "blocked"
    assert contract.block_reason is not None
    assert "no existe" in contract.block_reason.lower()


def test_build_query_contract_clarification_on_ambiguity():
    """Plan que contiene una columna ambigua transiciona a 'clarification_required'."""
    cols = ["ventas_netas", "ventas_brutas", "region"]
    profile = {
        "ventas_netas": {"type": "numeric", "role": "metric"},
        "ventas_brutas": {"type": "numeric", "role": "metric"},
        "region": {"type": "categorical", "role": "dimension"},
    }

    plan = AnalysisPlan(
        title="Ventas por Región",
        main_intent=DistributionIntent(
            rationale="Distribución regional",
            dimension="region",
            metric="ventas",  # Ambigua entre ventas_netas y ventas_brutas
            metric_unit=MetricUnit.CURRENCY,
        ),
    )

    contract = build_query_analytical_contract(
        query_id="q-003",
        file_id="f-test",
        intent_type="distribution",
        plans=[plan],
        columns=cols,
        schema_profile=profile,
    )

    assert contract.state == "clarification_required"
    assert contract.clarification_prompt is not None
    assert "ventas_netas" in contract.clarification_prompt
    assert "ventas_brutas" in contract.clarification_prompt
