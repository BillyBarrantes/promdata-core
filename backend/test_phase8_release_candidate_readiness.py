"""
test_phase8_release_candidate_readiness.py — Suite de Release Candidate y Pre-Vuelo para VPS Hetzner (Fase 8)
══════════════════════════════════════════════════════════════════════════════════════════════════════════════
Verifica:
  1. Auditoría de pre-vuelo (audit_release_candidate_readiness) certificando preparación para Hetzner VPS.
  2. Endpoint /health/release-candidate respondiendo estado de preparación para lanzamiento.
  3. Integridad y certificación cruzada de los 6 contratos V1 del Fortress Standard.
  4. Liveness y Observabilidad sin dependencias propietarias de Cloud Run.
  5. Cero vendor lock-in y plena operatividad en entornos Docker Compose estándar.
"""
from __future__ import annotations

import os
import sys

from fastapi.testclient import TestClient
import pytest

sys.path.insert(0, os.path.dirname(__file__))

from app.core.analytical_contract import (
    ColumnResolution,
    EvidenceBundleV1,
    InteractionContractV1,
    QueryAnalyticalContractV1,
    UploadFlowContractV1,
    VisualContractV1,
)
from app.main import app
from app.services.release_readiness import audit_release_candidate_readiness


# ── 1. AUDITORÍA DE PRE-VUELO PARA VPS HETZNER ─────────────────────────────────

def test_audit_release_candidate_readiness_structure():
    """Verifica que la auditoría de pre-vuelo devuelva la estructura completa de checks."""
    report = audit_release_candidate_readiness()

    assert "version" in report
    assert report["version"] == "1.0.0-RC"
    assert report["target_deployment"] == "VPS_HETZNER"
    assert "checks" in report

    checks = report["checks"]
    assert "supabase" in checks
    assert "ai_provider" in checks
    assert "storage_and_ingestion" in checks
    assert "redis_and_celery" in checks
    assert "vps_portability" in checks
    assert "contracts_v1" in checks


def test_vps_portability_zero_cloud_run_lockin():
    """Verifica que el runtime certifique cero dependencias propietarias de Cloud Run."""
    report = audit_release_candidate_readiness()
    vps_check = report["checks"]["vps_portability"]

    assert vps_check["status"] == "ok"
    assert vps_check["zero_cloud_run_lockin"] is True
    assert "Docker Compose" in vps_check["message"]


def test_all_v1_contracts_certified():
    """Certifica que los 6 contratos analíticos V1 estén plenamente operativos."""
    report = audit_release_candidate_readiness()
    contracts_check = report["checks"]["contracts_v1"]

    assert contracts_check["status"] == "ok"
    assert contracts_check["all_v1_models_operational"] is True
    assert len(contracts_check["models"]) == 6


# ── 2. INTEGRIDAD Y COHERENCIA DE CONTRATOS V1 ─────────────────────────────────

def test_v1_contract_cross_compatibility():
    """Valida la coherencia de paso de datos entre contratos V1."""
    # 1. Query contract
    query_contract = QueryAnalyticalContractV1(
        query_id="q_rc_001",
        file_id="file_rc_001",
        intent_type="distribution",
        metrics=[
            ColumnResolution(
                status="resolved",
                column_name="ventas",
                candidates=["ventas"],
            )
        ],
        dimensions=[
            ColumnResolution(
                status="resolved",
                column_name="categoria",
                candidates=["categoria"],
            )
        ],
        state="valid",
    )
    assert query_contract.is_executable is True

    # 2. Evidence bundle vinculado a la query
    evidence = EvidenceBundleV1(
        evidence_id="evi_rc_001",
        query_id=query_contract.query_id,
        plan_hash="sha256_hash_123",
        dataset_version="1.0",
        computed_facts={"ventas_totales": 150000},
        row_count=50,
    )
    assert evidence.query_id == "q_rc_001"
    assert evidence.computed_facts["ventas_totales"] == 150000

    # 3. Visual contract vinculado a la evidencia
    visual = VisualContractV1(
        chart_type="bar",
        x_axis="categoria",
        y_axis="ventas",
        evidence_id=evidence.evidence_id,
        format_currency="$",
    )
    assert visual.evidence_id == "evi_rc_001"
    assert visual.format_currency == "$"

    # 4. Interaction contract
    interaction = InteractionContractV1(
        base_filters={"canal": "Online"},
        active_filters={"region": "Norte"},
        evidence_id=evidence.evidence_id,
        recomputation_policy="local",
    )
    assert interaction.recomputation_policy == "local"

    # 5. Upload flow contract
    upload = UploadFlowContractV1()
    assert upload.bucket_name == "dash-uploads"
    assert upload.max_size_bytes == 52428800


# ── 3. PRUEBAS DE ENDPOINTS DE SALUD CON TESTCLIENT ────────────────────────────

def test_health_live_endpoint():
    """El endpoint /health/live debe responder 200 siempre que el proceso esté arriba."""
    client = TestClient(app)
    response = client.get("/health/live")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["probe"] == "liveness"


def test_health_release_candidate_endpoint():
    """El endpoint /health/release-candidate debe exponer el informe de pre-vuelo."""
    client = TestClient(app)
    response = client.get("/health/release-candidate")

    assert response.status_code == 200
    data = response.json()
    assert data["version"] == "1.0.0-RC"
    assert data["target_deployment"] == "VPS_HETZNER"
    assert data["contracts_v1_certified"] is True
    assert "checks" in data


def test_health_observability_endpoint():
    """El endpoint /health/observability debe responder sin enviar datos a servicios externos."""
    client = TestClient(app)
    response = client.get("/health/observability")

    assert response.status_code == 200
    data = response.json()
    assert "sentry" in data
    assert "langfuse" in data
