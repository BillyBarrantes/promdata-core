"""F4 — Retiro del puente `legacy_inferred` (TDD).

Con `SEMANTIC_CONTRACT_F4_RETIRE_BRIDGE=true`, solo `confirmed_file_contract`
(o `trusted_tenant_template`) concede corte. `legacy_inferred` y
`bootstrap_verified` pasan a `unknown` (fail-closed). Default OFF = inercia
total (comportamiento F0/F2/F3 intacto).

Invariante: el retiro NUNCA concede corte, solo lo retira, y solo cuando el
gate F0 está activo.
"""

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "."))

from app.core.analytical_contract import AnalyticalContract
from app.core.config import settings
from app.services.data_engine import DataEngine


def _schema() -> dict:
    return {
        "fecha": {"role": "date", "cardinality": 3, "cardinality_ratio": 0.25},
        "clave": {"role": "identifier", "cardinality_ratio": 1.0},
        "stock_disponible": {"role": "metric", "cardinality_ratio": 0.9},
        "canal": {"role": "dimension", "cardinality_ratio": 0.1},
    }


def _treasury_schema() -> dict:
    return {
        "fecha": {"role": "date", "cardinality": 23, "cardinality_ratio": 0.17},
        "clave": {"role": "identifier", "cardinality_ratio": 1.0},
        "importe": {"role": "metric", "cardinality_ratio": 0.9},
        "canal": {"role": "dimension", "cardinality_ratio": 0.1},
        "estado": {"role": "dimension", "cardinality_ratio": 0.1},
    }


def _bridge_snapshot() -> pd.DataFrame:
    """bridge_votes == 2: puente no confirmado."""
    rows = []
    periods = list(pd.date_range("2024-01-31", periods=3, freq="ME"))
    for period_index, period in enumerate(periods):
        repeats = 2 if period_index == 2 else 1
        for entity in ("A", "B", "C"):
            for _ in range(repeats):
                rows.append(
                    {"fecha": period, "clave": entity, "stock_disponible": 100.0, "canal": "C1"}
                )
    return pd.DataFrame(rows)


def _strong_snapshot() -> pd.DataFrame:
    """bridge_votes == 3: evidencia fuerte → confirmado."""
    rows = []
    for period in pd.date_range("2024-01-31", periods=3, freq="ME"):
        for entity in ("A", "B", "C", "D"):
            rows.append(
                {"fecha": period, "clave": entity, "stock_disponible": 10 + len(rows), "canal": "C1"}
            )
    return pd.DataFrame(rows)


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
# 1. Flag OFF → paridad F3 (el puente conserva corte)
# ═══════════════════════════════════════════════════════════════════════

def test_flag_off_preserves_bridge_cut(monkeypatch) -> None:
    monkeypatch.setattr(settings, "SEMANTIC_CONTRACT_F3_BOOTSTRAP", True)
    monkeypatch.setattr(settings, "SEMANTIC_CONTRACT_F4_RETIRE_BRIDGE", False)

    contract = DataEngine._infer_dataset_semantic_contract(_bridge_snapshot(), _schema())

    assert contract["contract_state"] == "bootstrap_verified"
    assert contract["dataset_mode"] == "snapshot"
    assert contract["snapshot_guard_allowed"] is True
    assert contract["evidence"]["bridge_retired"] is False


# ═══════════════════════════════════════════════════════════════════════
# 2. Flag ON + puente → unknown sin corte, sin bootstrap
# ═══════════════════════════════════════════════════════════════════════

def test_flag_on_retires_bridge(monkeypatch) -> None:
    monkeypatch.setattr(settings, "SEMANTIC_CONTRACT_F3_BOOTSTRAP", True)
    monkeypatch.setattr(settings, "SEMANTIC_CONTRACT_F4_RETIRE_BRIDGE", True)

    contract = DataEngine._infer_dataset_semantic_contract(_bridge_snapshot(), _schema())

    assert contract["contract_state"] == "unknown"
    assert contract["dataset_mode"] == "unknown"
    assert contract["snapshot_guard_allowed"] is False
    assert contract["bootstrap_expires_at"] is None
    assert contract["bootstrap_scope"] == {}
    assert contract["evidence"]["bridge_retired"] is True


# ═══════════════════════════════════════════════════════════════════════
# 3. Flag ON + evidencia fuerte → confirmado conserva corte
# ═══════════════════════════════════════════════════════════════════════

def test_flag_on_keeps_confirmed_cut(monkeypatch) -> None:
    monkeypatch.setattr(settings, "SEMANTIC_CONTRACT_F2_CONFIRM", True)
    monkeypatch.setattr(settings, "SEMANTIC_CONTRACT_F4_RETIRE_BRIDGE", True)

    contract = DataEngine._infer_dataset_semantic_contract(_strong_snapshot(), _schema())

    assert contract["contract_state"] == "confirmed_file_contract"
    assert contract["dataset_mode"] == "snapshot"
    assert contract["snapshot_guard_allowed"] is True
    assert contract["evidence"]["bridge_retired"] is False


# ═══════════════════════════════════════════════════════════════════════
# 4. Flag ON + veto → unknown (fail-closed preservado)
# ═══════════════════════════════════════════════════════════════════════

def test_flag_on_veto_stays_unknown(monkeypatch) -> None:
    monkeypatch.setattr(settings, "SEMANTIC_CONTRACT_F4_RETIRE_BRIDGE", True)

    contract = DataEngine._infer_dataset_semantic_contract(_treasury_ledger(), _treasury_schema())

    assert contract["contract_state"] == "unknown"
    assert contract["snapshot_guard_allowed"] is False


# ═══════════════════════════════════════════════════════════════════════
# 5. Flag ON + gate F0 off → sin retiro (clasificador legacy intacto)
# ═══════════════════════════════════════════════════════════════════════

def test_flag_on_gate_off_does_not_retire(monkeypatch) -> None:
    monkeypatch.setattr(settings, "SEMANTIC_CONTRACT_F0_GATE", False)
    monkeypatch.setattr(settings, "SEMANTIC_CONTRACT_F4_RETIRE_BRIDGE", True)

    contract = DataEngine._infer_dataset_semantic_contract(_bridge_snapshot(), _schema())

    assert contract["contract_state"] == "legacy_inferred"
    assert contract["evidence"]["bridge_retired"] is False


# ═══════════════════════════════════════════════════════════════════════
# 6. Flag ON + F3 off → el puente legacy también se retira
# ═══════════════════════════════════════════════════════════════════════

def test_flag_on_retires_legacy_inferred(monkeypatch) -> None:
    monkeypatch.setattr(settings, "SEMANTIC_CONTRACT_F3_BOOTSTRAP", False)
    monkeypatch.setattr(settings, "SEMANTIC_CONTRACT_F4_RETIRE_BRIDGE", True)

    contract = DataEngine._infer_dataset_semantic_contract(_bridge_snapshot(), _schema())

    assert contract["contract_state"] == "unknown"
    assert contract["dataset_mode"] == "unknown"
    assert contract["evidence"]["bridge_retired"] is True


# ═══════════════════════════════════════════════════════════════════════
# 7. Round-trip AnalyticalContract con estado retirado
# ═══════════════════════════════════════════════════════════════════════

def test_analytical_contract_roundtrips_retired_unknown() -> None:
    contract = AnalyticalContract.from_legacy_dict(
        {"contract_state": "unknown", "dataset_mode": "unknown"}
    )
    assert contract.dataset_contract.contract_state == "unknown"
    legacy = contract.to_legacy_dataset_contract()
    assert legacy["contract_state"] == "unknown"
    assert legacy["dataset_mode"] == "unknown"
    assert legacy["bootstrap_expires_at"] is None
