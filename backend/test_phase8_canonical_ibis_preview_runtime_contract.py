from __future__ import annotations

from app.core.config import settings
from app.core.canonical_artifacts import (
    CanonicalMaterializationStatus,
    CanonicalMaterializedBundle,
    CanonicalMaterializedFrame,
    CanonicalMaterializedView,
)
from app.services.canonical_ibis_preview_runtime import (
    build_canonical_ibis_preview_runtime,
    describe_canonical_ibis_preview_runtime,
    execute_canonical_preview_table,
    is_canonical_ibis_preview_runtime_enabled,
    select_default_preview_table_name,
    summarize_materialized_bundle_status,
)


def test_phase8_ibis_preview_runtime_flag_defaults_to_off(monkeypatch) -> None:
    monkeypatch.setattr(settings, "CANONICAL_IBIS_PREVIEW_RUNTIME_ENABLED", False)
    assert is_canonical_ibis_preview_runtime_enabled() is False


def test_phase8_ibis_preview_runtime_registers_primary_related_and_derived_tables() -> None:
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
        related_frames=[
            CanonicalMaterializedFrame(
                frame_id="payroll",
                label="Payroll",
                status=CanonicalMaterializationStatus.PREVIEW_ONLY,
                relation_type="likely_join",
                join_keys=["employee_id"],
                row_count=2,
                column_names=["employee_id", "salary"],
                records=[
                    {"employee_id": "E-1", "salary": "1500"},
                    {"employee_id": "E-2", "salary": "1200"},
                ],
            )
        ],
        derived_views=[
            CanonicalMaterializedView(
                view_id="employees__payroll__join_preview",
                view_type="likely_join",
                status=CanonicalMaterializationStatus.PREVIEW_ONLY,
                source_frame_ids=["employees", "payroll"],
                row_count=2,
                column_names=["employee_id", "department", "salary"],
                records=[
                    {"employee_id": "E-1", "department": "Sales", "salary": "1500"},
                    {"employee_id": "E-2", "department": "Finance", "salary": "1200"},
                ],
            )
        ],
    )

    runtime = build_canonical_ibis_preview_runtime(materialized)
    summary = describe_canonical_ibis_preview_runtime(runtime)

    assert summary["table_count"] == 3
    assert summary["preview_backend"] in {"ibis_duckdb", "pandas_fallback"}
    assert "primary__employees" in runtime.tables
    assert "related__payroll" in runtime.tables
    assert "derived__employees__payroll__join_preview" in runtime.tables


def test_phase8_ibis_preview_runtime_executes_preview_query() -> None:
    materialized = CanonicalMaterializedBundle(
        primary_frame_id="sales",
        status=CanonicalMaterializationStatus.READY,
        primary_frame=CanonicalMaterializedFrame(
            frame_id="sales",
            label="Sales",
            status=CanonicalMaterializationStatus.READY,
            row_count=2,
            column_names=["region", "amount"],
            records=[
                {"region": "North", "amount": 100},
                {"region": "South", "amount": 120},
            ],
        ),
    )

    runtime = build_canonical_ibis_preview_runtime(materialized)
    table_name = select_default_preview_table_name(runtime)
    result = execute_canonical_preview_table(runtime, table_name=table_name or "", limit=1)

    assert table_name == "primary__sales"
    assert result["row_count"] == 1
    assert result["columns"] == ["region", "amount"]
    assert isinstance(result["rows"], list)
    assert isinstance(result["sql"], str)


def test_phase8_ibis_preview_runtime_summarizes_deferred_status() -> None:
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
        related_frames=[],
        derived_views=[],
    )

    summary = summarize_materialized_bundle_status(materialized)

    assert summary["bundle_status"] == "deferred"
    assert summary["preview_ready_tables"] == 0
    assert summary["deferred_tables"] == 1
