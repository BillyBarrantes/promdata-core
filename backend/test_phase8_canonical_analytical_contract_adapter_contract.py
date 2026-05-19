from __future__ import annotations

import warnings

import pandas as pd

from app.core.canonical_artifacts import (
    CanonicalMaterializationStatus,
    CanonicalMaterializedBundle,
    CanonicalMaterializedFrame,
    CanonicalMaterializedView,
)
from app.core.config import settings
from app.services.canonical_analytical_contract_adapter import (
    build_canonical_analytical_adapter_runtime,
    get_selected_candidate_dataframe,
    is_canonical_analytical_contract_adapter_enabled,
    summarize_canonical_analytical_adapter_runtime,
)
from app.services.canonical_ibis_preview_runtime import build_canonical_ibis_preview_runtime


def test_phase8_analytical_contract_adapter_flag_defaults_to_off(monkeypatch) -> None:
    monkeypatch.setattr(settings, "CANONICAL_ANALYTICAL_CONTRACT_ADAPTER_ENABLED", False)
    assert is_canonical_analytical_contract_adapter_enabled() is False


def test_phase8_analytical_contract_adapter_builds_dataengine_compatible_candidate() -> None:
    materialized = CanonicalMaterializedBundle(
        primary_frame_id="sales",
        status=CanonicalMaterializationStatus.PREVIEW_ONLY,
        primary_frame=CanonicalMaterializedFrame(
            frame_id="sales",
            label="Sales",
            status=CanonicalMaterializationStatus.PREVIEW_ONLY,
            row_count=3,
            column_names=["fecha", "region", "amount"],
            records=[
                {"fecha": "2026-01-01", "region": "North", "amount": 100},
                {"fecha": "2026-01-02", "region": "South", "amount": 120},
                {"fecha": "2026-01-03", "region": "East", "amount": 140},
            ],
        ),
    )

    preview_runtime = build_canonical_ibis_preview_runtime(materialized)
    with warnings.catch_warnings(record=True) as captured:
        adapter_runtime = build_canonical_analytical_adapter_runtime(preview_runtime)
    summary = summarize_canonical_analytical_adapter_runtime(adapter_runtime)
    selected_df = get_selected_candidate_dataframe(adapter_runtime)

    assert summary["selected_candidate_id"] == "primary__sales"
    assert summary["candidate_count"] == 1
    assert summary["candidates"][0]["metric_count"] >= 1
    assert selected_df is not None
    assert "schema_profile" in selected_df.attrs
    assert "semantic_contract" in selected_df.attrs
    assert selected_df.attrs["semantic_contract"]["time_axis"] == "fecha"
    assert not captured


def test_phase8_analytical_contract_adapter_prefers_derived_candidate_when_richer() -> None:
    materialized = CanonicalMaterializedBundle(
        primary_frame_id="employees",
        status=CanonicalMaterializationStatus.PREVIEW_ONLY,
        primary_frame=CanonicalMaterializedFrame(
            frame_id="employees",
            label="Employees",
            status=CanonicalMaterializationStatus.PREVIEW_ONLY,
            row_count=2,
            column_names=["employee_id", "department"],
            records=[
                {"employee_id": "E-1", "department": "Sales"},
                {"employee_id": "E-2", "department": "Finance"},
            ],
        ),
        derived_views=[
            CanonicalMaterializedView(
                view_id="employees__attendance__join_preview",
                view_type="likely_join",
                status=CanonicalMaterializationStatus.PREVIEW_ONLY,
                source_frame_ids=["employees", "attendance"],
                row_count=2,
                column_names=["employee_id", "department", "fecha", "hours"],
                records=[
                    {"employee_id": "E-1", "department": "Sales", "fecha": "2026-01-01", "hours": 8},
                    {"employee_id": "E-2", "department": "Finance", "fecha": "2026-01-01", "hours": 7},
                ],
            )
        ],
    )

    preview_runtime = build_canonical_ibis_preview_runtime(materialized)
    with warnings.catch_warnings(record=True) as captured:
        adapter_runtime = build_canonical_analytical_adapter_runtime(preview_runtime)
    summary = summarize_canonical_analytical_adapter_runtime(adapter_runtime)

    assert summary["selected_candidate_id"] == "derived__employees__attendance__join_preview"
    assert summary["candidates"][0]["dataset_mode"] in {"snapshot", "flow", "hybrid", "undetermined"}
    assert not captured


def test_phase8_analytical_contract_adapter_handles_deferred_bundle_without_crashing() -> None:
    materialized = CanonicalMaterializedBundle(
        primary_frame_id="legacy-tabular-runtime",
        status=CanonicalMaterializationStatus.DEFERRED,
        primary_frame=CanonicalMaterializedFrame(
            frame_id="legacy-tabular-runtime",
            label="Delegated",
            status=CanonicalMaterializationStatus.DEFERRED,
            row_count=0,
            column_names=[],
            records=[],
        ),
    )

    preview_runtime = build_canonical_ibis_preview_runtime(materialized)
    adapter_runtime = build_canonical_analytical_adapter_runtime(preview_runtime)
    summary = summarize_canonical_analytical_adapter_runtime(adapter_runtime)

    assert summary["selected_candidate_id"] == "primary__legacy-tabular-runtime"
    assert summary["candidates"][0]["status"] == "empty"


def test_phase8_analytical_contract_adapter_preserves_semantic_numeric_dimension_as_string() -> None:
    materialized = CanonicalMaterializedBundle(
        primary_frame_id="inventory",
        status=CanonicalMaterializationStatus.PREVIEW_ONLY,
        primary_frame=CanonicalMaterializedFrame(
            frame_id="inventory",
            label="Inventory",
            status=CanonicalMaterializationStatus.PREVIEW_ONLY,
            row_count=3,
            column_names=["fecha_de_stock", "ubicacion", "stock_disponible"],
            records=[
                {"fecha_de_stock": "2021-07-31", "ubicacion": "SALDOS", "stock_disponible": 10},
                {"fecha_de_stock": "2021-07-31", "ubicacion": "030021", "stock_disponible": 199},
                {"fecha_de_stock": "2021-07-31", "ubicacion": "21103", "stock_disponible": 3754},
            ],
        ),
    )

    preview_runtime = build_canonical_ibis_preview_runtime(materialized)
    adapter_runtime = build_canonical_analytical_adapter_runtime(preview_runtime)
    selected_df = get_selected_candidate_dataframe(adapter_runtime)

    assert selected_df is not None
    assert selected_df["ubicacion"].dtype.name in {"string", "object"}
    assert selected_df["ubicacion"].tolist() == ["SALDOS", "030021", "21103"]
    assert selected_df.attrs["semantic_contract"]["dimension_columns"]
    assert "ubicacion" in selected_df.attrs["semantic_contract"]["dimension_columns"]


def test_phase8_analytical_contract_adapter_avoids_dimension_join_with_only_suspicious_metrics() -> None:
    materialized = CanonicalMaterializedBundle(
        primary_frame_id="dim_customer",
        status=CanonicalMaterializationStatus.READY,
        primary_frame=CanonicalMaterializedFrame(
            frame_id="dim_customer",
            label="DimCustomer",
            status=CanonicalMaterializationStatus.READY,
            row_count=2,
            column_names=["customerkey", "customerlabel"],
            records=[
                {"customerkey": 1, "customerlabel": "C-1"},
                {"customerkey": 2, "customerlabel": "C-2"},
            ],
        ),
        related_frames=[
            CanonicalMaterializedFrame(
                frame_id="dim_store",
                label="DimStore",
                status=CanonicalMaterializationStatus.READY,
                row_count=2,
                column_names=["storekey", "opendate", "employeecount", "status"],
                records=[
                    {"storekey": "S-1", "opendate": "2026-01-01", "employeecount": 12, "status": "Open"},
                    {"storekey": "S-2", "opendate": "2026-01-02", "employeecount": 10, "status": "Open"},
                ],
            )
        ],
        derived_views=[
            CanonicalMaterializedView(
                view_id="dim_customer__territory__join_preview",
                view_type="likely_join",
                status=CanonicalMaterializationStatus.READY,
                source_frame_ids=["dim_customer", "territory"],
                row_count=2,
                column_names=["customerkey", "geographykey", "salesterritorykey", "startdate", "status"],
                records=[
                    {"customerkey": 1, "geographykey": 10, "salesterritorykey": 100, "startdate": "2026-01-01", "status": "Open"},
                    {"customerkey": 2, "geographykey": 11, "salesterritorykey": 101, "startdate": "2026-01-01", "status": "Open"},
                ],
            )
        ],
    )

    preview_runtime = build_canonical_ibis_preview_runtime(materialized)
    adapter_runtime = build_canonical_analytical_adapter_runtime(preview_runtime)
    summary = summarize_canonical_analytical_adapter_runtime(adapter_runtime)

    assert summary["selected_candidate_id"] == "related__dim_store"


def test_phase8_analytical_contract_adapter_blocks_suspicious_string_metric_columns(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.canonical_shadow_metric_validity_gate.settings.CANONICAL_SHADOW_METRIC_VALIDITY_GATE_ENABLED",
        True,
    )
    materialized = CanonicalMaterializedBundle(
        primary_frame_id="inventory",
        status=CanonicalMaterializationStatus.READY,
        primary_frame=CanonicalMaterializedFrame(
            frame_id="inventory",
            label="Inventory",
            status=CanonicalMaterializationStatus.READY,
            row_count=3,
            column_names=["fecha_de_stock", "ubicacion", "stock_disponible"],
            records=[
                {"fecha_de_stock": "2026-01-01", "ubicacion": "101", "stock_disponible": 12},
                {"fecha_de_stock": "2026-01-02", "ubicacion": "102", "stock_disponible": 10},
                {"fecha_de_stock": "2026-01-03", "ubicacion": "103", "stock_disponible": 8},
            ],
        ),
    )

    preview_runtime = build_canonical_ibis_preview_runtime(materialized)
    adapter_runtime = build_canonical_analytical_adapter_runtime(preview_runtime)
    selected_df = get_selected_candidate_dataframe(adapter_runtime)

    assert selected_df is not None
    contract = selected_df.attrs["semantic_contract"]
    shadow_metric_gate = selected_df.attrs["shadow_metric_gate"]
    assert "stock_disponible" in list(contract.get("metric_columns") or [])
    assert "ubicacion" not in list(contract.get("metric_columns") or [])
    assert "ubicacion" in list(contract.get("dimension_columns") or []) or "ubicacion" in list(contract.get("identifier_columns") or [])
    assert "ubicacion" not in list(shadow_metric_gate.get("safe_metric_columns") or [])


def test_phase8_analytical_contract_adapter_coerces_parseable_text_metric_columns(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.canonical_shadow_metric_validity_gate.settings.CANONICAL_SHADOW_METRIC_VALIDITY_GATE_ENABLED",
        True,
    )
    materialized = CanonicalMaterializedBundle(
        primary_frame_id="tasks",
        status=CanonicalMaterializationStatus.READY,
        primary_frame=CanonicalMaterializedFrame(
            frame_id="tasks",
            label="Tasks",
            status=CanonicalMaterializationStatus.READY,
            row_count=3,
            column_names=["fecha", "tiempo_horas", "responsable"],
            records=[
                {"fecha": "2026-01-01", "tiempo_horas": "10,5", "responsable": "Ana"},
                {"fecha": "2026-01-02", "tiempo_horas": "12,0", "responsable": "Luis"},
                {"fecha": "2026-01-03", "tiempo_horas": "8,5", "responsable": "Eva"},
            ],
        ),
    )

    preview_runtime = build_canonical_ibis_preview_runtime(materialized)
    adapter_runtime = build_canonical_analytical_adapter_runtime(preview_runtime)
    selected_df = get_selected_candidate_dataframe(adapter_runtime)

    assert selected_df is not None
    contract = selected_df.attrs["semantic_contract"]
    shadow_metric_gate = selected_df.attrs["shadow_metric_gate"]
    assert "tiempo_horas" in list(contract.get("metric_columns") or [])
    assert pd.api.types.is_numeric_dtype(selected_df["tiempo_horas"])
    assert "tiempo_horas" in list(shadow_metric_gate.get("safe_metric_columns") or [])


def test_phase8_analytical_contract_adapter_promotes_currency_text_metric_in_small_date_table(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.canonical_shadow_metric_validity_gate.settings.CANONICAL_SHADOW_METRIC_VALIDITY_GATE_ENABLED",
        True,
    )
    materialized = CanonicalMaterializedBundle(
        primary_frame_id="prices",
        status=CanonicalMaterializationStatus.READY,
        primary_frame=CanonicalMaterializedFrame(
            frame_id="prices",
            label="Prices",
            status=CanonicalMaterializationStatus.READY,
            row_count=3,
            column_names=["codigo_sku", "fecaduc", "precio_sucio"],
            records=[
                {"codigo_sku": "00750", "fecaduc": "2024-12-31", "precio_sucio": "$ 1,500.00"},
                {"codigo_sku": "00820", "fecaduc": "2025-01-15", "precio_sucio": "S/. 200"},
                {"codigo_sku": "00010", "fecaduc": "2025-02-15", "precio_sucio": "1.200,50"},
            ],
        ),
    )

    preview_runtime = build_canonical_ibis_preview_runtime(materialized)
    adapter_runtime = build_canonical_analytical_adapter_runtime(preview_runtime)
    selected_df = get_selected_candidate_dataframe(adapter_runtime)

    assert selected_df is not None
    contract = selected_df.attrs["semantic_contract"]
    shadow_metric_gate = selected_df.attrs["shadow_metric_gate"]
    assert "precio_sucio" in list(contract.get("metric_columns") or [])
    assert list(selected_df["precio_sucio"].round(2)) == [1500.0, 200.0, 1200.5]
    assert "precio_sucio" in list(shadow_metric_gate.get("promoted_metric_columns") or [])
