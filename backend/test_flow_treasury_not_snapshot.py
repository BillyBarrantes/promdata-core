import os
import sys
import uuid

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "."))

from app.core.config import settings
from app.services.data_engine import DataEngine
from app.services.semantic_translator.core import should_default_to_latest_snapshot
from app.services import file_cache


def _schema(entity_role: str = "identifier") -> dict:
    return {
        "fecha": {"role": "date", "cardinality": 23, "cardinality_ratio": 0.17},
        "clave": {"role": entity_role, "cardinality_ratio": 1.0},
        "importe": {"role": "metric", "cardinality_ratio": 0.9},
        "canal": {"role": "dimension", "cardinality_ratio": 0.1},
        "estado": {"role": "dimension", "cardinality_ratio": 0.1},
    }


def _treasury_ledger() -> pd.DataFrame:
    dates = []
    for day in range(1, 23):
        dates.extend([pd.Timestamp("2024-01-01") + pd.Timedelta(days=day - 1)] * (6 if day <= 9 else 5))
    dates.extend([pd.Timestamp("2024-01-23")] * 18)
    assert len(dates) == 137
    return pd.DataFrame(
        {
            "fecha": dates,
            "clave": [f"TX-{index:03d}" for index in range(137)],
            "importe": [100 + index for index in range(137)],
            "canal": ["Debito automatico" if index % 2 else "Transferencia" for index in range(137)],
            "estado": ["Sin validar" if index % 3 else "Validado" for index in range(137)],
        }
    )


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


def _calibration_panel() -> pd.DataFrame:
    rows = []
    for period in pd.date_range("2023-01-31", periods=5, freq="ME"):
        for entity in ("A", "B", "C", "D", "E"):
            rows.append(
                {
                    "fecha": period,
                    "clave": entity,
                    "importe": 100 + len(rows),
                    "canal": "Canal de diseno",
                    "estado": "Validado",
                }
            )
    return pd.DataFrame(rows)


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


def test_treasury_ledger_is_fail_closed_without_losing_categories():
    ledger = _treasury_ledger()

    contract = DataEngine._infer_dataset_semantic_contract(ledger, _schema())
    cleaned_ledger, _, _, _, _ = DataEngine.unify_and_clean({"tesoreria": ledger}, {})
    pipeline_contract = cleaned_ledger.attrs["semantic_contract"]

    assert contract["dataset_mode"] == "unknown"
    assert contract["snapshot_guard_allowed"] is False
    assert contract["contract_state"] == "unknown"
    assert contract["evidence"]["entity_key_confidence"] < settings.SEMANTIC_CONTRACT_ENTITY_KEY_CONFIDENCE_THRESHOLD
    assert contract["evidence"]["rows_at_max_date"] == 18
    assert contract["evidence"]["rows_at_max_ratio"] >= 0.08
    assert contract["evidence"]["snapshot_score"] == 4
    assert contract["evidence"]["flow_score"] == 2
    assert contract["evidence"]["allow_cut_legacy"] is False
    assert pipeline_contract["dataset_mode"] == "unknown"
    assert "is_latest_snapshot" not in cleaned_ledger.columns
    assert cleaned_ledger["canal"].nunique() == 2
    assert cleaned_ledger["estado"].nunique() == 2
    assert cleaned_ledger.groupby("canal")["importe"].sum().sum() == ledger["importe"].sum()


def test_complete_snapshot_can_use_the_versioned_legacy_bridge(monkeypatch):
    # F2/F3 elevarían este caso a `confirmed_file_contract`/`bootstrap_verified`;
    # aquí se verifica explícitamente el puente legacy, así que se desactivan.
    monkeypatch.setattr(settings, "SEMANTIC_CONTRACT_F2_CONFIRM", False)
    monkeypatch.setattr(settings, "SEMANTIC_CONTRACT_F3_BOOTSTRAP", False)

    contract = DataEngine._infer_dataset_semantic_contract(_complete_snapshot(), _schema())

    assert contract["dataset_mode"] == "snapshot"
    assert contract["snapshot_guard_allowed"] is True
    assert contract["contract_state"] == "legacy_inferred"
    assert contract["evidence"]["allow_cut_legacy"] is True
    assert contract["evidence"]["entity_period_coverage"] == 1.0


def test_confidence_threshold_is_versioned_and_passes_its_design_fixture():
    contract = DataEngine._infer_dataset_semantic_contract(_calibration_panel(), _schema())

    assert settings.SEMANTIC_CONTRACT_ENTITY_KEY_CONFIDENCE_VERSION == "f0-calibrated-v1"
    assert contract["evidence"]["entity_key_confidence"] >= settings.SEMANTIC_CONTRACT_ENTITY_KEY_CONFIDENCE_THRESHOLD
    assert contract["evidence"]["allow_cut_legacy"] is True


def test_partial_snapshot_cannot_gain_a_cut_from_latest_rows_alone():
    contract = DataEngine._infer_dataset_semantic_contract(_partial_snapshot(), _schema())

    assert contract["dataset_mode"] != "snapshot"
    assert contract["snapshot_guard_allowed"] is False
    assert contract["evidence"]["allow_cut_legacy"] is False
    assert contract["evidence"]["entity_period_coverage"] <= 0.25


def test_invalid_confidence_configuration_fails_closed(monkeypatch):
    monkeypatch.setattr(settings, "SEMANTIC_CONTRACT_ENTITY_KEY_CONFIDENCE_THRESHOLD", None)

    contract = DataEngine._infer_dataset_semantic_contract(_complete_snapshot(), _schema())

    assert contract["dataset_mode"] == "unknown"
    assert contract["snapshot_guard_allowed"] is False
    assert contract["evidence"]["allow_cut_legacy"] is False


def test_missing_confidence_version_fails_closed(monkeypatch):
    monkeypatch.setattr(settings, "SEMANTIC_CONTRACT_ENTITY_KEY_CONFIDENCE_VERSION", "")

    contract = DataEngine._infer_dataset_semantic_contract(_complete_snapshot(), _schema())

    assert contract["dataset_mode"] == "unknown"
    assert contract["snapshot_guard_allowed"] is False
    assert contract["evidence"]["allow_cut_legacy"] is False


def test_disabled_f0_gate_preserves_the_legacy_classifier(monkeypatch):
    monkeypatch.setattr(settings, "SEMANTIC_CONTRACT_F0_GATE", False)

    contract = DataEngine._infer_dataset_semantic_contract(_treasury_ledger(), _schema())

    assert contract["dataset_mode"] == "snapshot"
    assert contract["snapshot_guard_allowed"] is True
    assert contract["contract_state"] == "legacy_inferred"


def test_unknown_contract_never_injects_a_latest_cut():
    assert should_default_to_latest_snapshot(
        "muestra el resumen",
        dataset_contract={
            "dataset_mode": "unknown",
            "snapshot_guard_allowed": False,
            "time_axis": "fecha",
            "date_columns": ["fecha"],
        },
        schema_profile={"fecha": {"cardinality": 23}},
    ) is False


def test_stale_sidecar_is_reinferred_with_the_f0_contract_version(monkeypatch):
    # El foco es la reinferencia por versión, no la etiqueta; se fijan F2/F3 en
    # False para conservar la aserción del puente legacy sin ambigüedad.
    monkeypatch.setattr(settings, "SEMANTIC_CONTRACT_F2_CONFIRM", False)
    monkeypatch.setattr(settings, "SEMANTIC_CONTRACT_F3_BOOTSTRAP", False)

    snapshot = _complete_snapshot()
    snapshot.attrs["semantic_contract"] = DataEngine._infer_dataset_semantic_contract(snapshot, _schema())
    snapshot.attrs["schema_profile"] = _schema()
    file_id = f"semantic-f0-{uuid.uuid4().hex}"
    parquet_path = DataEngine.commit_to_parquet(snapshot, file_id)
    if not parquet_path:
        pytest.skip("Parquet dependencies are not available in this environment.")

    stale_payload = DataEngine.load_sidecar_payload(parquet_path)
    stale_payload["version"] = "phase_1_foundation"
    DataEngine._write_sidecar_payload(DataEngine._contract_path_from_parquet_path(parquet_path), stale_payload)

    refreshed = DataEngine.load_semantic_contract(parquet_path)

    assert refreshed["version"] == DataEngine.semantic_contract_version()
    assert refreshed["contract_state"] == "legacy_inferred"


def test_selective_cache_invalidation_does_not_touch_another_file(monkeypatch):
    monkeypatch.setattr(file_cache, "_get_redis", lambda: None)
    monkeypatch.setattr(file_cache, "_HAS_PARQUET", False)
    with file_cache._MEMORY_LOCK:
        file_cache._MEMORY_CACHE.clear()

    file_cache.set_cached_analysis("f0-a", "resumen", {"result": {"value": "a"}})
    file_cache.set_cached_analysis("f0-a", "detalle", {"result": {"value": "a2"}})
    file_cache.set_cached_analysis("f0-b", "resumen", {"result": {"value": "b"}})

    file_cache.invalidate_file_cache("f0-a")

    assert file_cache.get_cached_analysis("f0-a", "resumen") is None
    assert file_cache.get_cached_analysis("f0-a", "detalle") is None
    assert file_cache.get_cached_analysis("f0-b", "resumen") == {"result": {"value": "b"}}
