"""F2 — Confirmación de contrato por archivo (TDD).

Blindaje de la elevación `legacy_inferred` → `confirmed_file_contract`:
  * evidencia F0 fuerte (allow_cut_legacy + bridge_votes==3) + flag on → confirmado;
  * flag off → puente legacy intacto (rollback);
  * veto (tesorería) → `unknown`, nunca confirmado;
  * snapshot parcial → no confirmado;
  * gate F0 off → clasificador legacy preservado;
  * round-trip por `AnalyticalContract`.
Invariante: `dataset_mode` y `snapshot_guard_allowed` NO cambian en F2.
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
        "fecha": {"role": "date", "cardinality": 23, "cardinality_ratio": 0.17},
        "clave": {"role": "identifier", "cardinality_ratio": 1.0},
        "importe": {"role": "metric", "cardinality_ratio": 0.9},
        "canal": {"role": "dimension", "cardinality_ratio": 0.1},
        "estado": {"role": "dimension", "cardinality_ratio": 0.1},
    }


def _complete_snapshot() -> pd.DataFrame:
    rows = []
    for period in pd.date_range("2024-01-31", periods=3, freq="ME"):
        for entity in ("A", "B", "C", "D"):
            rows.append(
                {
                    "fecha": period,
                    "clave": entity,
                    "importe": 10 + len(rows),
                    "canal": "Canal A",
                    "estado": "Validado",
                }
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


def _partial_snapshot() -> pd.DataFrame:
    rows = []
    for index, period in enumerate(pd.date_range("2024-01-31", periods=4, freq="ME")):
        rows.append(
            {
                "fecha": period,
                "clave": chr(ord("A") + index),
                "importe": 20 + index,
                "canal": "Canal A",
                "estado": "Validado",
            }
        )
    return pd.DataFrame(rows)


# ═══════════════════════════════════════════════════════════════════════
# 1. Evidencia fuerte + F2 on → confirmed_file_contract
# ═══════════════════════════════════════════════════════════════════════

def test_strong_snapshot_is_confirmed_when_f2_enabled(monkeypatch) -> None:
    monkeypatch.setattr(settings, "SEMANTIC_CONTRACT_F2_CONFIRM", True)

    contract = DataEngine._infer_dataset_semantic_contract(_complete_snapshot(), _schema())

    assert contract["contract_state"] == "confirmed_file_contract"
    assert contract["evidence"]["contract_confirmed"] is True
    assert contract["evidence"]["bridge_votes"] == 3
    assert contract["evidence"]["confirmation_reasons"] == ["allow_cut_legacy", "bridge_votes=3"]
    # Invariante F2: el corte NO cambia respecto a F0.
    assert contract["dataset_mode"] == "snapshot"
    assert contract["snapshot_guard_allowed"] is True


# ═══════════════════════════════════════════════════════════════════════
# 2. Flag off → puente legacy (rollback)
# ═══════════════════════════════════════════════════════════════════════

def test_f2_disabled_keeps_legacy_bridge(monkeypatch) -> None:
    monkeypatch.setattr(settings, "SEMANTIC_CONTRACT_F2_CONFIRM", False)
    # F3 elevaría el puente a `bootstrap_verified`; aquí se aísla F2.
    monkeypatch.setattr(settings, "SEMANTIC_CONTRACT_F3_BOOTSTRAP", False)

    contract = DataEngine._infer_dataset_semantic_contract(_complete_snapshot(), _schema())

    assert contract["contract_state"] == "legacy_inferred"
    assert contract["evidence"]["contract_confirmed"] is False
    assert contract["evidence"]["confirmation_reasons"] == []
    assert contract["dataset_mode"] == "snapshot"
    assert contract["snapshot_guard_allowed"] is True


# ═══════════════════════════════════════════════════════════════════════
# 3. Veto (tesorería) → unknown, nunca confirmado
# ═══════════════════════════════════════════════════════════════════════

def test_vetoed_dataset_is_never_confirmed(monkeypatch) -> None:
    monkeypatch.setattr(settings, "SEMANTIC_CONTRACT_F2_CONFIRM", True)

    contract = DataEngine._infer_dataset_semantic_contract(_treasury_ledger(), _schema())

    assert contract["contract_state"] == "unknown"
    assert contract["evidence"]["contract_confirmed"] is False
    assert contract["snapshot_guard_allowed"] is False


# ═══════════════════════════════════════════════════════════════════════
# 4. Snapshot parcial → no confirmado
# ═══════════════════════════════════════════════════════════════════════

def test_partial_snapshot_is_not_confirmed(monkeypatch) -> None:
    monkeypatch.setattr(settings, "SEMANTIC_CONTRACT_F2_CONFIRM", True)

    contract = DataEngine._infer_dataset_semantic_contract(_partial_snapshot(), _schema())

    assert contract["contract_state"] != "confirmed_file_contract"
    assert contract["evidence"]["contract_confirmed"] is False
    assert contract["evidence"]["allow_cut_legacy"] is False


# ═══════════════════════════════════════════════════════════════════════
# 5. Gate F0 off → clasificador legacy preservado
# ═══════════════════════════════════════════════════════════════════════

def test_f0_gate_off_preserves_legacy_classifier(monkeypatch) -> None:
    monkeypatch.setattr(settings, "SEMANTIC_CONTRACT_F0_GATE", False)
    monkeypatch.setattr(settings, "SEMANTIC_CONTRACT_F2_CONFIRM", True)

    contract = DataEngine._infer_dataset_semantic_contract(_complete_snapshot(), _schema())

    assert contract["contract_state"] == "legacy_inferred"
    assert contract["evidence"]["contract_confirmed"] is False


# ═══════════════════════════════════════════════════════════════════════
# 6. Round-trip por AnalyticalContract
# ═══════════════════════════════════════════════════════════════════════

def test_analytical_contract_roundtrips_confirmed_state() -> None:
    contract = AnalyticalContract.from_legacy_dict(
        {"contract_state": "confirmed_file_contract", "dataset_mode": "snapshot"}
    )
    assert contract.dataset_contract.contract_state == "confirmed_file_contract"
    assert contract.to_legacy_dataset_contract()["contract_state"] == "confirmed_file_contract"
