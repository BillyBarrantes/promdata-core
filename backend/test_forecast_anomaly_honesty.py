"""
test_forecast_anomaly_honesty.py — Honestidad de KPI/forecast/anomalías (Fase E).

Cubre:
  1. wants_gauge: un KPI solo renderiza gauge con unidad explícita 'percentage'
     (no por rango 0..100).
  2. forecast_series: no emite bandas de confianza no calibradas; todo pronóstico
     queda etiquetado como experimental.
  3. detect_anomalies: omite columnas no numéricas o constantes (IDs/categóricas).
"""
from __future__ import annotations

import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(__file__))

from app.services.predictive_engine import PredictiveEngine
from app.tasks.analysis_pipeline.plan_executor import wants_gauge

requires_predictive = pytest.mark.skipif(
    not PredictiveEngine.is_available(),
    reason="statsmodels/sklearn no disponibles",
)


# ── 1. wants_gauge ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "unit,expected",
    [
        ("percentage", True),
        ("quantity", False),
        ("currency", False),
        (None, False),
    ],
)
def test_wants_gauge_only_for_explicit_percentage(unit, expected):
    assert wants_gauge(unit) is expected


# ── 2. forecast sin bandas no calibradas ───────────────────────────────────────

@requires_predictive
def test_forecast_hides_uncalibrated_confidence_bands():
    dates = pd.date_range("2023-01-31", periods=18, freq="ME")
    df = pd.DataFrame({"fecha": dates, "ventas": [100 + i * 5 for i in range(18)]})

    out = PredictiveEngine.forecast_series(df, "fecha", "ventas")
    assert out, "se esperaba una proyección con 18 observaciones"

    forecast_items = [item for item in out if item["type"] == "forecast"]
    assert forecast_items, "se esperaban ítems de tipo forecast"

    for item in forecast_items:
        assert item["lower_ci"] is None
        assert item["upper_ci"] is None
        assert item["interval_estimated"] is False


# ── 3. guardas de anomalías ────────────────────────────────────────────────────

def test_anomalies_skip_non_numeric_column():
    df = pd.DataFrame({"producto": ["A", "B", "C", "D", "E", "F", "G"]})
    result = PredictiveEngine.detect_anomalies(df, "producto")
    assert "is_anomaly" not in result.columns


def test_anomalies_skip_constant_column():
    df = pd.DataFrame({"stock": [5, 5, 5, 5, 5, 5]})
    result = PredictiveEngine.detect_anomalies(df, "stock")
    assert "is_anomaly" not in result.columns


@requires_predictive
def test_anomalies_run_on_varying_numeric_column():
    df = pd.DataFrame({"ventas": [1, 2, 3, 4, 5, 6, 7, 8, 9, 100]})
    result = PredictiveEngine.detect_anomalies(df, "ventas")
    assert "is_anomaly" in result.columns
