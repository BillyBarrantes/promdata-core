"""
test_expiry_intent.py
═══════════════════════════════════════════════════════════════════
Regression gate (2026-09):
  Mejora 4 — Intención determinista de "vencimientos" (expiry).
  Mejora 6 — Contrato temporal enriquecido (snapshot_axis / event_axes).
"""
from __future__ import annotations

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))

from app.core.analytical_contract import AnalyticalContract
from app.services.data_engine import DataEngine
from app.services.expiry_intent import (
    build_expiry_analysis_bundle,
    is_expiry_request,
    resolve_expiry_bounds,
    resolve_expiry_column,
)


# ═══════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════
def _df() -> pd.DataFrame:
    return pd.DataFrame({
        "fecha_de_stock": pd.to_datetime(
            ["2021-06-30", "2021-07-31", "2021-07-31", "2021-07-31"]
        ),
        "fecaduc_feprefercons": pd.to_datetime(
            ["2021-08-10", "2021-08-20", "2024-01-01", "2021-05-01"]
        ),
        "material": ["M1", "M2", "M3", "M4"],
        "texto_breve_de_material": ["Prod A", "Prod B", "Prod C", "Prod D"],
        "stock_disponible": [10, 20, 30, 40],
    })


_SCHEMA = {
    "fecha_de_stock": {"type": "temporal", "role": "date", "cardinality": 2},
    "fecaduc_feprefercons": {"type": "temporal", "role": "date", "cardinality": 4},
    "material": {"role": "identifier", "cardinality": 4},
    "texto_breve_de_material": {"role": "dimension", "cardinality": 4},
    "stock_disponible": {"role": "metric"},
}

_CONTRACT = {
    "time_axis": "fecha_de_stock",
    "snapshot_axis": "fecha_de_stock",
    "event_axes": ["fecaduc_feprefercons"],
    "date_columns": ["fecha_de_stock", "fecaduc_feprefercons"],
    "metric_columns": ["stock_disponible"],
    "dataset_mode": "snapshot",
    "snapshot_guard_allowed": True,
}


# ═══════════════════════════════════════════════════════════════════
# Mejora 4
# ═══════════════════════════════════════════════════════════════════
def test_is_expiry_request() -> None:
    assert is_expiry_request("productos pronto a vencer en los proximos 60 dias")
    assert is_expiry_request("materiales que estan venciendo hasta el 30 de agosto")
    assert is_expiry_request("cualquier cosa", prompt_type="expiry_window_analysis")
    assert not is_expiry_request("analisis de ventas por region")


def test_resolve_expiry_column_prefers_future_dates() -> None:
    col = resolve_expiry_column(_df(), _SCHEMA, _CONTRACT, "2021-07-31")
    assert col == "fecaduc_feprefercons"


def test_resolve_expiry_bounds_relative() -> None:
    assert resolve_expiry_bounds(
        "productos a vencer en los proximos 60 dias", "2021-07-31"
    ) == (("2021-07-31", "2021-09-29"), "relative")


def test_resolve_expiry_bounds_open() -> None:
    assert resolve_expiry_bounds("productos pronto a vencer", "2021-07-31") == (
        ("2021-07-31", None), "open"
    )


def test_resolve_expiry_bounds_explicit_aborts() -> None:
    """Una cota explícita (hasta una fecha) se delega al LLM (no sobre-incluir)."""
    assert resolve_expiry_bounds(
        "materiales que vencen hasta el 30 de agosto", "2021-07-31"
    ) is None


def test_build_expiry_bundle() -> None:
    plans = build_expiry_analysis_bundle(
        prompt="productos pronto a vencer en los proximos 60 dias",
        columns=list(_df().columns),
        schema_profile=_SCHEMA,
        dataset_contract=_CONTRACT,
        candidate_df=_df(),
        reference_date="2021-07-31",
    )
    assert plans is not None and len(plans) == 3
    for plan in plans:
        cols = {f.column for f in plan.main_intent.filters}
        assert cols == {"fecaduc_feprefercons"}
        assert plan.metric_polarity is not None
    # KPI + distribución + tendencia
    types = {type(p.main_intent).__name__ for p in plans}
    assert types == {"DescriptiveIntent", "DistributionIntent", "TimeTrendIntent"}


def test_build_expiry_bundle_none_for_other_prompts() -> None:
    assert build_expiry_analysis_bundle(
        prompt="analisis de ventas por region",
        columns=list(_df().columns),
        schema_profile=_SCHEMA,
        dataset_contract=_CONTRACT,
        candidate_df=_df(),
        reference_date="2021-07-31",
    ) is None


# ═══════════════════════════════════════════════════════════════════
# Mejora 6 — Contrato temporal enriquecido
# ═══════════════════════════════════════════════════════════════════
def test_contract_roundtrip_preserves_temporal_vocabulary() -> None:
    contract = AnalyticalContract.from_legacy_dict(dataset_contract=_CONTRACT)
    dc = contract.dataset_contract
    assert dc.snapshot_axis == "fecha_de_stock"
    assert dc.event_axes == ["fecaduc_feprefercons"]

    restored = contract.to_legacy_dataset_contract()
    assert restored["snapshot_axis"] == "fecha_de_stock"
    assert restored["event_axes"] == ["fecaduc_feprefercons"]
    assert restored["time_axis"] == "fecha_de_stock"  # retro-compatible


def test_contract_backward_compatible_without_new_keys() -> None:
    legacy = {
        "dataset_mode": "snapshot",
        "time_axis": "fecha_de_stock",
        "date_columns": ["fecha_de_stock"],
    }
    contract = AnalyticalContract.from_legacy_dict(dataset_contract=legacy)
    assert contract.dataset_contract.snapshot_axis == "fecha_de_stock"
    assert contract.dataset_contract.event_axes == []


def test_data_engine_emits_snapshot_and_event_axes() -> None:
    rows = []
    for date in ["2021-03-31", "2021-04-30", "2021-05-31", "2021-06-30", "2021-07-31"]:
        for i in range(20):
            rows.append({
                "fecha_de_stock": pd.Timestamp(date),
                "fecaduc_feprefercons": pd.Timestamp("2021-01-01")
                + pd.Timedelta(days=(i * 13 + 200)),
                "material": f"M{i:02d}",
                "stock_disponible": 100 + i,
            })
    df = pd.DataFrame(rows)
    schema = {
        "fecha_de_stock": {"type": "temporal", "role": "date", "cardinality": 5},
        "fecaduc_feprefercons": {"type": "temporal", "role": "date", "cardinality": 40},
        "material": {"role": "identifier", "cardinality": 20},
        "stock_disponible": {"role": "metric"},
    }
    contract = DataEngine._infer_dataset_semantic_contract(df, schema)
    assert contract["time_axis"] == "fecha_de_stock"
    assert contract["snapshot_axis"] == "fecha_de_stock"
    assert contract["event_axes"] == ["fecaduc_feprefercons"]
