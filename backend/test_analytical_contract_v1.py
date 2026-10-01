"""
test_analytical_contract_v1.py
═══════════════════════════════════════════════════════════════════
F1.1 — AnalyticalContract v1: typed envelope + FilterSpec with origin
═══════════════════════════════════════════════════════════════════

Tests contratados:
  C1 — Constructor vacío produce contract_version="1.0" + defaults
  C2 — FilterSpec requiere origin (falla sin él)
  C3 — FilterSpec rechaza operador inválido (FilterOperator type safety)
  C4 — from_legacy_dict preserva todas las claves del dict B
  C5 — to_legacy_dataset_contract produce dict idéntico al original
  C6 — Roundtrip completo: dict → AnalyticalContract → dict
  C7 — Temporal Fortress keys preservadas en roundtrip
  C8 — FilterSpec con origin "user"/"llm"/"system" válidos
  C9 — FilterSpec rechaza origin inválido
  C10 — to_legacy_filters produce lista de dicts con origin
  C11 — from_legacy_dict con None produce contract vacío
  C12 — canonical_dimensions preservado en roundtrip bit-exact
  C13 — canonical_dimensions ausente si fuente no lo tenía
  C14 — contract_version usa constante compartida
"""
from __future__ import annotations

import copy
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from app.core.analytical_contract import ANALYTICAL_CONTRACT_VERSION, AnalyticalContract, DatasetContractV1, FilterSpec, TemporalContractV1
from app.core.semantic_grammar import FilterOperator

# ═══ Sample legacy dict (dict B from data_engine.py:961-972) ═══
LEGACY_DICT = {
    "version": "phase_1_foundation",
    "dataset_mode": "snapshot",
    "snapshot_guard_allowed": True,
    "time_axis": "fecha_de_stock",
    "date_columns": ["fecha_de_stock"],
    "metric_columns": ["stock_disponible"],
    "dimension_columns": ["material", "ubicacion"],
    "identifier_columns": ["id_material"],
    "entity_key": "id_material",
    "evidence": {
        "total_rows": 1000,
        "unique_dates": 12,
        "avg_rows_per_period": 83.33,
        "min_date": "2021-01-31",
        "max_date": "2021-12-31",
        "rows_at_max_date": 85,
        "rows_at_max_ratio": 0.085,
        "primary_metric": "stock_disponible",
        "metric_at_max_ratio": 0.12,
        "repeated_entity_ratio": 0.85,
        "snapshot_score": 7,
        "flow_score": 2,
        "score_reasons": ["multi_period_dataset", "avg_rows_per_period=83.33"],
    },
}


def test_empty_contract_has_version_1_0() -> None:
    c = AnalyticalContract()
    assert c.contract_version == "1.0"
    assert c.dataset_contract.dataset_mode == "undetermined"
    assert c.filters == []


def test_filterspec_requires_origin() -> None:
    FilterSpec(column="x", operator="==", value=1, origin="user")

    with pytest.raises(Exception):
        FilterSpec(column="x", operator="==", value=1)


def test_filterspec_valid_origin_values() -> None:
    for origin in ("user", "llm", "system"):
        f = FilterSpec(column="c", operator="==", value="v", origin=origin)
        assert f.origin == origin


def test_filterspec_invalid_origin_rejected() -> None:
    with pytest.raises(Exception):
        FilterSpec(column="c", operator="==", value="v", origin="admin")


def test_from_legacy_dict_preserves_all_keys() -> None:
    contract = AnalyticalContract.from_legacy_dict(dataset_contract=LEGACY_DICT)
    dc = contract.dataset_contract

    assert dc.dataset_mode == "snapshot"
    assert dc.snapshot_guard_allowed is True
    assert dc.time_axis == "fecha_de_stock"
    assert dc.date_columns == ["fecha_de_stock"]
    assert dc.metric_columns == ["stock_disponible"]
    assert dc.dimension_columns == ["material", "ubicacion"]
    assert dc.identifier_columns == ["id_material"]
    assert dc.entity_key == "id_material"
    assert dc.evidence["total_rows"] == 1000
    assert dc.evidence["snapshot_score"] == 7
    assert contract.metadata["legacy_version"] == "phase_1_foundation"


def test_to_legacy_dataset_contract_matches_original() -> None:
    contract = AnalyticalContract.from_legacy_dict(dataset_contract=LEGACY_DICT)
    restored = contract.to_legacy_dataset_contract()

    assert restored["version"] == "phase_1_foundation"
    assert restored["dataset_mode"] == "snapshot"
    assert restored["snapshot_guard_allowed"] is True
    assert restored["time_axis"] == "fecha_de_stock"
    assert restored["date_columns"] == ["fecha_de_stock"]
    assert restored["metric_columns"] == ["stock_disponible"]
    assert restored["dimension_columns"] == ["material", "ubicacion"]
    assert restored["identifier_columns"] == ["id_material"]
    assert restored["entity_key"] == "id_material"
    assert restored["evidence"]["total_rows"] == 1000
    assert restored["evidence"]["score_reasons"] == [
        "multi_period_dataset",
        "avg_rows_per_period=83.33",
    ]


def test_roundtrip_preserves_all_values() -> None:
    original = copy.deepcopy(LEGACY_DICT)
    contract = AnalyticalContract.from_legacy_dict(dataset_contract=original)
    restored = contract.to_legacy_dataset_contract()

    for key in original:
        assert key in restored, f"Missing key in restored: {key}"
        assert restored[key] == original.get(key), (
            f"Mismatch for key '{key}': "
            f"expected {original.get(key)!r}, got {restored[key]!r}"
        )


def test_temporal_fortress_keys_preserved() -> None:
    fortress_keys = [
        "time_axis",
        "date_columns",
        "dataset_mode",
        "snapshot_guard_allowed",
        "evidence",
    ]
    contract = AnalyticalContract.from_legacy_dict(dataset_contract=LEGACY_DICT)
    restored = contract.to_legacy_dataset_contract()

    for key in fortress_keys:
        assert key in restored, f"Temporal Fortress key '{key}' missing from restored dict"


def test_from_legacy_with_filters() -> None:
    filters = [
        {"column": "material", "operator": "==", "value": "MAT-001", "origin": "user"},
        {"column": "stock_disponible", "operator": ">", "value": 0, "origin": "system"},
    ]
    contract = AnalyticalContract.from_legacy_dict(
        dataset_contract=LEGACY_DICT, filters=filters
    )
    assert len(contract.filters) == 2
    assert contract.filters[0].origin == "user"
    assert contract.filters[0].column == "material"
    assert contract.filters[1].origin == "system"
    assert contract.filters[1].value == 0


def test_to_legacy_filters_preserves_origin() -> None:
    filters = [
        {"column": "material", "operator": "==", "value": "MAT-001", "origin": "user"},
        {"column": "stock_disponible", "operator": ">", "value": 0, "origin": "llm"},
    ]
    contract = AnalyticalContract.from_legacy_dict(
        dataset_contract=LEGACY_DICT, filters=filters
    )
    legacy = contract.to_legacy_filters()
    assert len(legacy) == 2
    assert legacy[0]["origin"] == "user"
    assert legacy[1]["origin"] == "llm"
    assert legacy[0]["column"] == "material"


def test_from_legacy_dict_none_produces_empty() -> None:
    contract = AnalyticalContract.from_legacy_dict()
    assert contract.dataset_contract.dataset_mode == "undetermined"
    assert contract.filters == []
    assert contract.temporal_contract is None


def test_with_temporal_contract() -> None:
    contract = AnalyticalContract.from_legacy_dict(
        dataset_contract=LEGACY_DICT,
        temporal_contract={
            "dataset_year": 2021,
            "min_date": "2021-01-01",
            "max_date": "2021-12-31",
            "temporal_resolution": "monthly",
        },
    )
    assert contract.temporal_contract is not None
    assert contract.temporal_contract.dataset_year == 2021
    assert contract.temporal_contract.min_date == "2021-01-01"
    assert contract.temporal_contract.max_date == "2021-12-31"


def test_flow_dataset_roundtrip() -> None:
    flow_dict = {
        "version": "phase_1_foundation",
        "dataset_mode": "flow",
        "snapshot_guard_allowed": False,
        "time_axis": "fecha",
        "date_columns": ["fecha"],
        "metric_columns": ["venta"],
        "dimension_columns": ["producto", "region"],
        "identifier_columns": ["id"],
        "entity_key": "id",
        "evidence": {
            "total_rows": 500,
            "reason": "flow_detected",
        },
    }
    contract = AnalyticalContract.from_legacy_dict(dataset_contract=flow_dict)
    restored = contract.to_legacy_dataset_contract()

    assert restored["dataset_mode"] == "flow"
    assert restored["snapshot_guard_allowed"] is False
    assert restored["time_axis"] == "fecha"
    assert restored["evidence"]["reason"] == "flow_detected"


def test_filterspec_invalid_operator_rejected() -> None:
    with pytest.raises(Exception):
        FilterSpec(column="x", operator="bogus_operator", value=1, origin="user")


def test_filterspec_valid_operator_accepted() -> None:
    f = FilterSpec(column="x", operator="==", value=1, origin="user")
    assert isinstance(f.operator, FilterOperator)
    assert f.operator == FilterOperator.EQUALS

    f2 = FilterSpec(column="x", operator=">=", value=10, origin="llm")
    assert f2.operator == FilterOperator.GREATER_EQUAL


def test_roundtrip_preserves_canonical_dimensions() -> None:
    enriched = {
        "version": "phase_1_foundation",
        "dataset_mode": "snapshot",
        "snapshot_guard_allowed": True,
        "time_axis": "fecha",
        "date_columns": ["fecha"],
        "metric_columns": ["venta"],
        "dimension_columns": ["producto"],
        "identifier_columns": [],
        "entity_key": None,
        "evidence": {"total_rows": 100},
        "canonical_dimensions": {
            "producto": {
                "canonical_groups": 8,
                "collapsed_variant_groups": 3,
                "humanized_values": 5,
            },
        },
    }
    contract = AnalyticalContract.from_legacy_dict(dataset_contract=enriched)
    assert "canonical_dimensions" in contract.metadata
    assert contract.metadata["canonical_dimensions"]["producto"]["canonical_groups"] == 8

    restored = contract.to_legacy_dataset_contract()
    assert "canonical_dimensions" in restored
    assert restored["canonical_dimensions"]["producto"]["collapsed_variant_groups"] == 3


def test_canonical_dimensions_absent_when_not_in_source() -> None:
    clean = {
        "version": "phase_1_foundation",
        "dataset_mode": "flow",
        "snapshot_guard_allowed": False,
        "time_axis": "fecha",
        "date_columns": ["fecha"],
        "metric_columns": ["venta"],
        "dimension_columns": ["producto"],
        "identifier_columns": [],
        "entity_key": None,
        "evidence": {"total_rows": 50},
    }
    contract = AnalyticalContract.from_legacy_dict(dataset_contract=clean)
    assert "canonical_dimensions" not in contract.metadata

    restored = contract.to_legacy_dataset_contract()
    assert "canonical_dimensions" not in restored


def test_contract_version_uses_constant() -> None:
    c = AnalyticalContract()
    assert c.contract_version == ANALYTICAL_CONTRACT_VERSION
    assert c.contract_version is ANALYTICAL_CONTRACT_VERSION
