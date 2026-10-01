"""
test_phase6_interaction_contract.py — Suite de Interacción Dinámica y Filtros Cruzados (Fase 6)
════════════════════════════════════════════════════════════════════════════════════════════════
Verifica:
  1. Contrato InteractionContractV1 y política de recálculo ('local' vs 'backend_required').
  2. Detección de invalidación de Snapshot Guard ante refiltrado temporal.
  3. Reconciliación de filtros interactivos vs filtros base del prompt original.
  4. Aplicación determinista de filtros a DataFrames (escalar, in, operadores de rango).
  5. Deselección y resolución de conflictos entre filtros.
"""
from __future__ import annotations

import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(__file__))

from app.core.analytical_contract import InteractionContractV1
from app.services.interaction_engine import (
    apply_interaction_filters_to_dataframe,
    evaluate_interaction_policy,
    reconcile_interaction_filters,
)


# ── 1. PRUEBAS DE POLÍTICA DE RECÁLCULO (LOCAL VS BACKEND_REQUIRED) ───────────

def test_interaction_policy_local_for_categorical_cross_filter():
    """Clicks en barras o categorías dentro del dataset se resuelven como 'local'."""
    contract = evaluate_interaction_policy(
        base_filters={"canal": "Online"},
        active_filters={"region": "Norte"},
        schema_profile={"canal": {"role": "dimension"}, "region": {"role": "dimension"}},
        dataset_contract={"dataset_mode": "flow"},
    )

    assert isinstance(contract, InteractionContractV1)
    assert contract.recomputation_policy == "local"
    assert contract.snapshot_complete is True
    assert contract.active_filters == {"region": "Norte"}


def test_interaction_policy_backend_required_on_metric_change():
    """Cambio de métrica solicitado exige recálculo holístico en backend."""
    contract = evaluate_interaction_policy(
        base_filters={"canal": "Online"},
        active_filters={"region": "Norte"},
        requested_changes={"metric": "margen"},
    )

    assert contract.recomputation_policy == "backend_required"


def test_interaction_policy_backend_required_on_predictive_request():
    """Solicitud de pronóstico interactivo exige recálculo en backend."""
    contract = evaluate_interaction_policy(
        base_filters={},
        active_filters={"region": "Norte"},
        requested_changes={"forecast": True},
    )

    assert contract.recomputation_policy == "backend_required"


def test_interaction_policy_invalidates_snapshot_guard_on_time_axis_filter():
    """Alterar el eje de tiempo en un dataset de tipo snapshot exige recálculo en backend."""
    contract = evaluate_interaction_policy(
        base_filters={"is_latest_snapshot": True},
        active_filters={"fecha_corte": "2024-03-31"},
        dataset_contract={
            "dataset_mode": "snapshot",
            "time_axis": "fecha_corte",
            "snapshot_guard_allowed": True,
        },
    )

    assert contract.recomputation_policy == "backend_required"
    assert contract.snapshot_complete is False


# ── 2. RECONCILIACIÓN DE FILTROS (CONFLICTOS Y DESELECCIÓN) ───────────────────

def test_reconcile_filters_merges_base_and_active():
    """Combina filtros base y activos sin colisión."""
    base = {"canal": "Online"}
    active = {"region": "Norte", "categoria": "Calzado"}

    merged = reconcile_interaction_filters(base, active)

    assert merged == {
        "canal": "Online",
        "region": "Norte",
        "categoria": "Calzado",
    }


def test_reconcile_filters_active_overrides_base_on_same_column():
    """Un filtro interactivo del usuario sobreescribe el filtro base en la misma columna."""
    base = {"region": "Sur", "canal": "Online"}
    active = {"region": "Norte"}

    merged = reconcile_interaction_filters(base, active)

    assert merged["region"] == "Norte"
    assert merged["canal"] == "Online"


def test_reconcile_filters_deselect_removes_filter():
    """Deseleccionar un filtro interactivo (valor vacío o []) lo remueve del conjunto activo."""
    base = {"canal": "Online"}
    active = {"canal": "", "region": []}

    merged = reconcile_interaction_filters(base, active)

    assert "canal" not in merged
    assert "region" not in merged


# ── 3. APLICACIÓN DETERMINISTA DE FILTROS A DATAFRAME ─────────────────────────

def test_apply_interaction_filters_scalar_and_in():
    """Aplica deterministamente filtros escalares y de inclusión múltiple."""
    df = pd.DataFrame([
        {"id": 1, "region": "Norte", "canal": "Online", "ventas": 100},
        {"id": 2, "region": "Sur", "canal": "Online", "ventas": 200},
        {"id": 3, "region": "Norte", "canal": "Tienda", "ventas": 150},
        {"id": 4, "region": "Centro", "canal": "Online", "ventas": 300},
    ])

    # 1. Filtro escalar
    res_scalar = apply_interaction_filters_to_dataframe(df, {"canal": "Online"})
    assert len(res_scalar) == 3
    assert (res_scalar["canal"] == "Online").all()

    # 2. Filtro IN (multiselección)
    res_in = apply_interaction_filters_to_dataframe(df, {"region": ["Norte", "Sur"]})
    assert len(res_in) == 3
    assert set(res_in["region"]) == {"Norte", "Sur"}


def test_apply_interaction_filters_range_operators():
    """Aplica operadores de comparación numérica (>=, <=)."""
    df = pd.DataFrame([
        {"producto": "A", "precio": 50},
        {"producto": "B", "precio": 150},
        {"producto": "C", "precio": 250},
        {"producto": "D", "precio": 350},
    ])

    res = apply_interaction_filters_to_dataframe(df, {"precio": {">=": 150, "<=": 300}})
    assert len(res) == 2
    assert set(res["producto"]) == {"B", "C"}


# ── 4. PRUEBA DE INTEGRACIÓN DEL ENDPOINT /interaction/evaluate ───────────────

def test_evaluate_interaction_endpoint_integration(monkeypatch):
    """El endpoint /interaction/evaluate procesa la petición y responde con InteractionEvaluateResponse."""
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    from app.main import app
    import app.api.routes as routes_module

    fake_user = SimpleNamespace(id="user_smoke_phase6")
    fake_client = SimpleNamespace()

    monkeypatch.setattr(routes_module, "_get_authenticated_user", lambda token: (fake_client, fake_user))
    monkeypatch.setattr(routes_module, "resolve_user_team_scope", lambda user_id, service_client: None)
    monkeypatch.setattr(
        routes_module,
        "get_user_uploaded_file_scope_or_404",
        lambda user_id, team_id, file_id, service_client: {"id": file_id, "user_id": user_id},
    )

    client = TestClient(app)
    response = client.post(
        "/api/v1/interaction/evaluate",
        headers={"Authorization": "Bearer smoke_token_phase6"},
        json={
            "file_id": "file-phase6-001",
            "base_filters": {"canal": "Online"},
            "active_filters": {"region": "Norte"},
            "requested_changes": {},
            "evidence_id": "evi_test_123",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["is_valid"] is True
    assert data["merged_filters"] == {"canal": "Online", "region": "Norte"}
    assert data["contract"]["recomputation_policy"] == "local"
    assert data["contract"]["snapshot_complete"] is True
    assert data["contract"]["evidence_id"] == "evi_test_123"

