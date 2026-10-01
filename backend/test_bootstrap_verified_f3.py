"""F3 — Bootstrap verificado con expiración (TDD).

Blindaje de la elevación provisoria `legacy_inferred` → `bootstrap_verified`:
  * puente que pasa el gate pero no confirma (bridge_votes==2) + F3 on →
    `bootstrap_verified` con `bootstrap_expires_at` futuro y `bootstrap_scope`;
  * F3 off → `legacy_inferred` (rollback a F2);
  * confirmación fuerte (bridge_votes==3) gana a bootstrap;
  * `unknown` (veto) nunca bootstrapea;
  * expiry: expirado/ilegible reinfiere (fail-closed), ausente no;
  * round-trip por `AnalyticalContract`.
Invariante: `dataset_mode` y `snapshot_guard_allowed` NO cambian en F3.
"""

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "."))

from app.core.analytical_contract import (
    AnalyticalContract,
    compute_bootstrap_expiry,
    is_bootstrap_expired,
)
from app.core.config import settings
from app.services.data_engine import DataEngine


def _schema() -> dict:
    return {
        "fecha": {"role": "date", "cardinality": 3, "cardinality_ratio": 0.25},
        "clave": {"role": "identifier", "cardinality_ratio": 1.0},
        "stock_disponible": {"role": "metric", "cardinality_ratio": 0.9},
        "canal": {"role": "dimension", "cardinality_ratio": 0.1},
    }


def _bridge_snapshot() -> pd.DataFrame:
    """bridge_votes == 2: cobertura y recurrencia OK, cv de filas/periodo alto."""
    rows = []
    periods = list(pd.date_range("2024-01-31", periods=3, freq="ME"))
    for period_index, period in enumerate(periods):
        repeats = 2 if period_index == 2 else 1
        for entity in ("A", "B", "C"):
            for _ in range(repeats):
                rows.append(
                    {
                        "fecha": period,
                        "clave": entity,
                        "stock_disponible": 100.0,
                        "canal": "C1",
                    }
                )
    return pd.DataFrame(rows)


def _strong_snapshot() -> pd.DataFrame:
    """bridge_votes == 3: evidencia fuerte → confirmación (F2)."""
    rows = []
    for period in pd.date_range("2024-01-31", periods=3, freq="ME"):
        for entity in ("A", "B", "C", "D"):
            rows.append(
                {
                    "fecha": period,
                    "clave": entity,
                    "stock_disponible": 10 + len(rows),
                    "canal": "C1",
                }
            )
    return pd.DataFrame(rows)


def _treasury_schema() -> dict:
    return {
        "fecha": {"role": "date", "cardinality": 23, "cardinality_ratio": 0.17},
        "clave": {"role": "identifier", "cardinality_ratio": 1.0},
        "importe": {"role": "metric", "cardinality_ratio": 0.9},
        "canal": {"role": "dimension", "cardinality_ratio": 0.1},
        "estado": {"role": "dimension", "cardinality_ratio": 0.1},
    }


def _treasury_ledger() -> pd.DataFrame:
    dates = []
    for day in range(1, 23):
        dates.extend([pd.Timestamp("2024-01-01") + pd.Timedelta(days=day - 1)] * (6 if day <= 9 else 5))
    dates.extend([pd.Timestamp("2024-01-23")] * 18)
    return pd.DataFrame(
        {
            "fecha": dates,
            "clave": [f"TX-{index:03d}" for index in range(137)],
            "importe": [100 + index for index in range(137)],
            "canal": ["Debito automatico" if index % 2 else "Transferencia" for index in range(137)],
            "estado": ["Sin validar" if index % 3 else "Validado" for index in range(137)],
        }
    )


# ═══════════════════════════════════════════════════════════════════════
# 1. Puente no confirmado + F3 on → bootstrap_verified
# ═══════════════════════════════════════════════════════════════════════

def test_bridge_pass_becomes_bootstrap_verified(monkeypatch) -> None:
    monkeypatch.setattr(settings, "SEMANTIC_CONTRACT_F2_CONFIRM", True)
    monkeypatch.setattr(settings, "SEMANTIC_CONTRACT_F3_BOOTSTRAP", True)

    contract = DataEngine._infer_dataset_semantic_contract(_bridge_snapshot(), _schema())

    assert contract["contract_state"] == "bootstrap_verified"
    assert contract["evidence"]["bridge_votes"] == 2
    assert contract["bootstrap_scope"]["origin"] == "f0_bridge"
    assert contract["bootstrap_expires_at"] is not None
    assert is_bootstrap_expired(contract["bootstrap_expires_at"]) is False
    # Invariante F3: el corte NO cambia respecto a F0/F2.
    assert contract["dataset_mode"] == "snapshot"
    assert contract["snapshot_guard_allowed"] is True


# ═══════════════════════════════════════════════════════════════════════
# 2. F3 off → puente legacy (rollback)
# ═══════════════════════════════════════════════════════════════════════

def test_f3_disabled_keeps_legacy_bridge(monkeypatch) -> None:
    monkeypatch.setattr(settings, "SEMANTIC_CONTRACT_F3_BOOTSTRAP", False)

    contract = DataEngine._infer_dataset_semantic_contract(_bridge_snapshot(), _schema())

    assert contract["contract_state"] == "legacy_inferred"
    assert contract["bootstrap_expires_at"] is None


# ═══════════════════════════════════════════════════════════════════════
# 3. Confirmación fuerte gana al bootstrap
# ═══════════════════════════════════════════════════════════════════════

def test_confirmation_beats_bootstrap(monkeypatch) -> None:
    monkeypatch.setattr(settings, "SEMANTIC_CONTRACT_F2_CONFIRM", True)
    monkeypatch.setattr(settings, "SEMANTIC_CONTRACT_F3_BOOTSTRAP", True)

    contract = DataEngine._infer_dataset_semantic_contract(_strong_snapshot(), _schema())

    assert contract["contract_state"] == "confirmed_file_contract"
    assert contract["bootstrap_expires_at"] is None


# ═══════════════════════════════════════════════════════════════════════
# 4. Veto → unknown, nunca bootstrap
# ═══════════════════════════════════════════════════════════════════════

def test_vetoed_dataset_never_bootstraps(monkeypatch) -> None:
    monkeypatch.setattr(settings, "SEMANTIC_CONTRACT_F3_BOOTSTRAP", True)

    contract = DataEngine._infer_dataset_semantic_contract(_treasury_ledger(), _treasury_schema())

    assert contract["contract_state"] == "unknown"
    assert contract["bootstrap_expires_at"] is None


# ═══════════════════════════════════════════════════════════════════════
# 5. Semántica de expiración (fail-closed)
# ═══════════════════════════════════════════════════════════════════════

def test_bootstrap_expiry_semantics() -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    future = compute_bootstrap_expiry(now=now, ttl_hours=24)

    assert is_bootstrap_expired(future, now=now) is False
    assert is_bootstrap_expired(future, now=now + timedelta(hours=25)) is True
    # Ausente = no hay bootstrap → no expirado.
    assert is_bootstrap_expired(None) is False
    assert is_bootstrap_expired("") is False
    # Ilegible = fail-closed (expirado → reinferencia).
    assert is_bootstrap_expired("no-es-una-fecha") is True


# ═══════════════════════════════════════════════════════════════════════
# 6. Bootstrap expirado dispara reinferencia del sidecar
# ═══════════════════════════════════════════════════════════════════════

def test_expired_bootstrap_triggers_reinference() -> None:
    snapshot = _bridge_snapshot()
    snapshot.attrs["semantic_contract"] = DataEngine._infer_dataset_semantic_contract(snapshot, _schema())
    snapshot.attrs["schema_profile"] = _schema()
    file_id = f"semantic-f3-{uuid.uuid4().hex}"
    parquet_path = DataEngine.commit_to_parquet(snapshot, file_id)
    if not parquet_path:
        pytest.skip("Parquet dependencies are not available in this environment.")

    stale_payload = DataEngine.load_sidecar_payload(parquet_path)
    assert stale_payload.get("bootstrap_expires_at")
    stale_payload["bootstrap_expires_at"] = (
        datetime.now(timezone.utc) - timedelta(hours=1)
    ).isoformat()
    DataEngine._write_sidecar_payload(
        DataEngine._contract_path_from_parquet_path(parquet_path), stale_payload
    )

    refreshed = DataEngine.load_semantic_contract(parquet_path)

    assert refreshed["bootstrap_expires_at"] != stale_payload["bootstrap_expires_at"]
    assert is_bootstrap_expired(refreshed["bootstrap_expires_at"]) is False


# ═══════════════════════════════════════════════════════════════════════
# 7. Round-trip por AnalyticalContract
# ═══════════════════════════════════════════════════════════════════════

def test_analytical_contract_roundtrips_bootstrap_fields() -> None:
    expiry = compute_bootstrap_expiry(ttl_hours=48)
    contract = AnalyticalContract.from_legacy_dict(
        {
            "contract_state": "bootstrap_verified",
            "dataset_mode": "snapshot",
            "bootstrap_expires_at": expiry,
            "bootstrap_scope": {"origin": "f0_bridge"},
        }
    )
    assert contract.dataset_contract.contract_state == "bootstrap_verified"
    assert contract.dataset_contract.bootstrap_expires_at == expiry
    legacy = contract.to_legacy_dataset_contract()
    assert legacy["contract_state"] == "bootstrap_verified"
    assert legacy["bootstrap_expires_at"] == expiry
    assert legacy["bootstrap_scope"] == {"origin": "f0_bridge"}


# ═══════════════════════════════════════════════════════════════════════
# 8. Gate F0 off → clasificador legacy, nunca bootstrap
# ═══════════════════════════════════════════════════════════════════════

def test_gate_off_never_bootstraps(monkeypatch) -> None:
    monkeypatch.setattr(settings, "SEMANTIC_CONTRACT_F0_GATE", False)
    monkeypatch.setattr(settings, "SEMANTIC_CONTRACT_F3_BOOTSTRAP", True)

    contract = DataEngine._infer_dataset_semantic_contract(_bridge_snapshot(), _schema())

    assert contract["contract_state"] == "legacy_inferred"
    assert contract["bootstrap_expires_at"] is None
    assert contract["bootstrap_scope"] == {}


# ═══════════════════════════════════════════════════════════════════════
# 9. F2 off + evidencia fuerte + F3 on → bootstrap (layering)
# ═══════════════════════════════════════════════════════════════════════

def test_f2_off_falls_back_to_bootstrap(monkeypatch) -> None:
    monkeypatch.setattr(settings, "SEMANTIC_CONTRACT_F2_CONFIRM", False)
    monkeypatch.setattr(settings, "SEMANTIC_CONTRACT_F3_BOOTSTRAP", True)

    contract = DataEngine._infer_dataset_semantic_contract(_strong_snapshot(), _schema())

    assert contract["contract_state"] == "bootstrap_verified"
    assert contract["evidence"]["contract_confirmed"] is False
    assert contract["bootstrap_expires_at"] is not None


# ═══════════════════════════════════════════════════════════════════════
# 10. Early-return siempre expone las claves de bootstrap (anti-bucle)
# ═══════════════════════════════════════════════════════════════════════

def test_early_return_exposes_bootstrap_keys() -> None:
    contract = DataEngine._infer_dataset_semantic_contract(pd.DataFrame(), {})

    assert contract["bootstrap_expires_at"] is None
    assert contract["bootstrap_scope"] == {}


def test_missing_schema_profile_clears_stale_expiry() -> None:
    snapshot = _bridge_snapshot()
    snapshot.attrs["semantic_contract"] = DataEngine._infer_dataset_semantic_contract(snapshot, _schema())
    snapshot.attrs["schema_profile"] = _schema()
    file_id = f"semantic-f3-merge-{uuid.uuid4().hex}"
    parquet_path = DataEngine.commit_to_parquet(snapshot, file_id)
    if not parquet_path:
        pytest.skip("Parquet dependencies are not available in this environment.")

    # Simula un sidecar con expiry vencido y sin `_schema_profile`: la
    # reinferencia sale por early-return y debe limpiar el expiry vencido
    # (si no, cada lectura reinferiría indefinidamente).
    stale_payload = DataEngine.load_sidecar_payload(parquet_path)
    stale_payload["bootstrap_expires_at"] = (
        datetime.now(timezone.utc) - timedelta(hours=1)
    ).isoformat()
    stale_payload["_schema_profile"] = {}
    DataEngine._write_sidecar_payload(
        DataEngine._contract_path_from_parquet_path(parquet_path), stale_payload
    )

    refreshed = DataEngine.load_semantic_contract(parquet_path)

    assert refreshed["bootstrap_expires_at"] is None
