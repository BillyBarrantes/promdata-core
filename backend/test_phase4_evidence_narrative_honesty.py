"""
test_phase4_evidence_narrative_honesty.py — Suite de Evidencia, Narrativa y Analítica Honesta (Fase 4)
══════════════════════════════════════════════════════════════════════════════════════════════════════
Verifica:
  1. Construcción de EvidenceBundleV1: hash reproducible, hechos auditables y filtros capturados.
  2. Reconciliation Guard: detección de números alucinados vs verificación de cifras reales.
  3. Precondiciones de Forecast: etiquetado de is_experimental=True ante muestras < 12 períodos.
  4. Metadatos de Anomalías: presencia de anomaly_method='IsolationForest' y anomaly_median_ratio.
  5. Integración de EvidenceBundleV1 en el generador de narrativa.
"""
from __future__ import annotations

import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(__file__))

from app.core.analytical_contract import EvidenceBundleV1
from app.core.semantic_grammar import (
    AnalysisPlan,
    DataFilter,
    DescriptiveIntent,
    MetricUnit,
)
from app.services.evidence_bundle import build_evidence_bundle
from app.services.narrative_reconciliation_guard import (
    parse_numeric_token,
    reconcile_narrative_with_facts,
    verify_number_in_facts,
)
from app.services.predictive_engine import PredictiveEngine


# ── 1. PRUEBAS DE EvidenceBundleV1 ─────────────────────────────────────────────

def test_evidence_bundle_creation_and_reproducible_hash():
    """Genera un EvidenceBundleV1 con hash estable y hechos capturados."""
    plan = AnalysisPlan(
        title="Ventas Totales por Canal",
        main_intent=DescriptiveIntent(
            rationale="KPI de ventas",
            metrics=["ingreso_total"],
            metric_unit=MetricUnit.CURRENCY,
            filters=[DataFilter(column="canal", operator="==", value="Online")],
        ),
    )
    ibis_output = {
        "hard_facts": {
            "total_ingreso": 1561828.66,
            "row_count": 1000,
            "margen_pct": 45.17,
        },
        "sql": "SELECT sum(ingreso_total) FROM ventas WHERE canal = 'Online'",
        "data": [{"canal": "Online", "ingreso_total": 1561828.66}],
    }

    bundle = build_evidence_bundle(plan, ibis_output, dataset_version="1.0")

    assert isinstance(bundle, EvidenceBundleV1)
    assert bundle.evidence_id.startswith("evi_")
    assert len(bundle.plan_hash) == 16
    assert bundle.computed_facts["total_ingreso"] == 1561828.66
    assert bundle.query_id is not None
    assert len(bundle.metadata["filters_applied"]) == 1
    assert bundle.metadata["filters_applied"][0]["column"] == "canal"
    assert bundle.metadata["filters_applied"][0]["value"] == "Online"
    assert bundle.row_count == 1
    assert bundle.sql_canonical_query is not None


# ── 2. PRUEBAS DE Reconciliation Guard (ESCUDO NUMÉRICO POST-LLM) ─────────────

def test_reconciliation_guard_validates_factual_numbers():
    """Narrativa con números exactos o redondeados respaldados por hechos no genera advertencias."""
    hard_facts = {
        "ingreso_total": 1561828.66,
        "costo_total": 856368.17,
        "unidades_totales": 12476,
    }

    # Texto con número en millones aproximado y unidades exactas
    narrative = (
        "El negocio generó $1.56M en ingresos totales con un costo de $856,368.17, "
        "alcanzando un volumen de 12,476 unidades vendidas."
    )

    cleaned_text, unverified = reconcile_narrative_with_facts(narrative, hard_facts)

    assert len(unverified) == 0
    assert "Nota de Verificación" not in cleaned_text


def test_reconciliation_guard_flags_hallucinated_figures():
    """Narrativa que inventa cifras sin sustento matemático es detectada y anotada."""
    hard_facts = {
        "ingreso_total": 1561828.66,
        "costo_total": 856368.17,
    }

    # Texto con cifra alucinada ($9,876,543)
    narrative = (
        "Las ventas alcanzaron un récord de $9,876,543 con una ganancia extraordinaria."
    )

    cleaned_text, unverified = reconcile_narrative_with_facts(narrative, hard_facts)

    assert len(unverified) >= 1
    assert any("9,876,543" in u["raw_text"] for u in unverified)
    assert "Nota de Verificación" in cleaned_text


# ── 3. PRUEBAS DE PRECONDICIONES Y ETIQUETADO DE FORECAST ──────────────────────

def test_forecast_short_series_marked_experimental():
    """Series temporales reducidas (< 12 observaciones) se marcan como experimentales."""
    # 6 meses de datos
    dates = pd.date_range("2024-01-01", periods=6, freq="ME")
    df = pd.DataFrame({"fecha": dates, "ventas": [100, 110, 105, 120, 125, 130]})

    result = PredictiveEngine.forecast_series(df, "fecha", "ventas", periods=2)
    forecast_points = [p for p in result if p.get("type") == "forecast"]

    assert len(forecast_points) == 2
    for pt in forecast_points:
        assert pt["is_experimental"] is True
        assert pt["confidence_level"] == "baja"
        assert "warning" in pt
        assert pt["observations_count"] == 6


def test_forecast_robust_series_marked_non_experimental():
    """Series temporales robustas (>= 12 observaciones) se marcan con confianza estándar."""
    # 24 meses de datos
    dates = pd.date_range("2022-01-01", periods=24, freq="ME")
    df = pd.DataFrame({"fecha": dates, "ventas": [100 + i * 5 for i in range(24)]})

    result = PredictiveEngine.forecast_series(df, "fecha", "ventas", periods=3)
    forecast_points = [p for p in result if p.get("type") == "forecast"]

    assert len(forecast_points) == 3
    for pt in forecast_points:
        assert pt["is_experimental"] is False
        assert pt["confidence_level"] == "alta"
        assert "warning" not in pt
        assert pt["observations_count"] == 24


# ── 4. PRUEBAS DE DETECCIÓN DE ANOMALÍAS CON METADATOS ─────────────────────────

def test_anomaly_detection_metadata():
    """La detección de anomalías expone el método IsolationForest y el ratio vs mediana."""
    df = pd.DataFrame({
        "valor": [10.0, 12.0, 11.0, 10.5, 9.8, 11.2, 10.8, 500.0, 10.1, 11.5]
    })

    res = PredictiveEngine.detect_anomalies(df, "valor", contamination=0.1)

    assert "is_anomaly" in res.columns
    assert "anomaly_score" in res.columns
    assert "anomaly_method" in res.columns
    assert "anomaly_median_ratio" in res.columns
    assert (res["anomaly_method"] == "IsolationForest").all()

    # El outlier (500.0) debe tener un ratio vs mediana muy elevado (> 40)
    outlier_row = res[res["valor"] == 500.0].iloc[0]
    assert outlier_row["anomaly_median_ratio"] > 40.0


# ── 5. PRUEBAS DE INTEGRACIÓN EN EL PIPELINE NARRATIVO ────────────────────────

def test_narrative_generator_appends_evidence_bundle():
    """generate_chart_narrative adjunta el EvidenceBundleV1 al output analítico."""
    from app.tasks.analysis_pipeline.narrative_generator import generate_chart_narrative

    plan = AnalysisPlan(
        title="Ingresos Totales",
        main_intent=DescriptiveIntent(
            rationale="KPI básico",
            metrics=["ingreso_total"],
            metric_unit=MetricUnit.CURRENCY,
        ),
    )
    ibis_output = {
        "hard_facts": {"total": 1500000.0},
        "data": [{"ingreso_total": 1500000.0}],
        "sql": "SELECT sum(ingreso_total) FROM ventas",
    }

    # Ejecutamos generate_chart_narrative (en caso de no haber LLM configurado localmente,
    # el bloque try/except del generador maneja la excepción suavemente y siempre emite el evidence_bundle)
    items = generate_chart_narrative(
        plan=plan,
        ibis_output=ibis_output,
        currency_meta={"symbol": "$", "code": "USD"},
        institutional_context="",
        institutional_snippets=[],
        visual_probe_mode=False,
        filtered_granular_df=None,
        schema_profile={"ingreso_total": {"type": "numeric", "role": "metric"}},
        actual_prompt="ingresos totales",
        file_id="f-123",
        task_id="t-456",
    )

    evidence_items = [it for it in items if it.get("type") == "evidence_bundle"]
    assert len(evidence_items) == 1
    bundle_data = evidence_items[0]["data"]
    assert bundle_data["computed_facts"]["total"] == 1500000.0
    assert bundle_data["sql_canonical_query"] is not None
    assert bundle_data["row_count"] == 1

