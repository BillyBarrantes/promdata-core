from __future__ import annotations

from app.core.canonical_artifacts import (
    ArtifactAvailabilityStatus,
    ArtifactOperationalMode,
    ArtifactSupportLevel,
    ArtifactSourceKind,
    CanonicalArtifactBundle,
    CanonicalFrameRelation,
    CanonicalMaterializationStatus,
    CanonicalSourceManifest,
    CanonicalTabularFrame,
)
from app.services.canonical_bundle_materializer import materialize_bundle


def _manifest() -> CanonicalSourceManifest:
    return CanonicalSourceManifest(
        file_name="bundle.xlsx",
        extension="xlsx",
        source_kind=ArtifactSourceKind.SPREADSHEET,
        support_level=ArtifactSupportLevel.FULL_ANALYTICS,
        availability_status=ArtifactAvailabilityStatus.ACTIVE,
        preferred_mode=ArtifactOperationalMode.ANALYTICAL,
        analytics_ready=True,
    )


def test_phase8_materializer_keeps_delegated_legacy_frames_deferred() -> None:
    bundle = CanonicalArtifactBundle(
        source_manifest=_manifest(),
        tabular_frames=[
            CanonicalTabularFrame(
                frame_id="legacy-tabular-runtime",
                label="Delegated to legacy DataEngine",
                extraction_confidence=1.0,
                metadata={"delegated": True},
            )
        ],
        metadata={"primary_frame_id": "legacy-tabular-runtime"},
    )

    materialized = materialize_bundle(bundle)

    assert materialized.status == CanonicalMaterializationStatus.DEFERRED
    assert materialized.primary_frame is not None
    assert materialized.primary_frame.status == CanonicalMaterializationStatus.DEFERRED
    assert materialized.derived_views == []


def test_phase8_materializer_builds_union_preview_for_related_frames() -> None:
    bundle = CanonicalArtifactBundle(
        source_manifest=_manifest(),
        tabular_frames=[
            CanonicalTabularFrame(
                frame_id="sales_q1",
                label="Sales Q1",
                row_count=2,
                column_count=2,
                column_names=["Region", "Amount"],
                extraction_confidence=0.95,
                metadata={"sample_rows": [["North", "100"], ["South", "120"]]},
            ),
            CanonicalTabularFrame(
                frame_id="sales_q2",
                label="Sales Q2",
                row_count=1,
                column_count=2,
                column_names=["Region", "Amount"],
                extraction_confidence=0.95,
                metadata={"sample_rows": [["East", "140"]]},
            ),
        ],
        frame_relations=[
            CanonicalFrameRelation(
                relation_id="sales_q1__sales_q2__union",
                relation_type="likely_union",
                left_frame_id="sales_q1",
                right_frame_id="sales_q2",
                confidence=0.9,
            )
        ],
        metadata={"primary_frame_id": "sales_q1"},
    )

    materialized = materialize_bundle(bundle)

    assert materialized.status == CanonicalMaterializationStatus.PREVIEW_ONLY
    assert materialized.primary_frame is not None
    assert materialized.primary_frame.row_count == 2
    assert len(materialized.related_frames) == 1
    assert len(materialized.derived_views) == 1
    assert materialized.derived_views[0].view_type == "likely_union"
    assert materialized.derived_views[0].row_count == 3


def test_phase8_materializer_builds_join_preview_for_related_frames() -> None:
    bundle = CanonicalArtifactBundle(
        source_manifest=_manifest(),
        tabular_frames=[
            CanonicalTabularFrame(
                frame_id="employees",
                label="Employees",
                row_count=2,
                column_count=2,
                column_names=["employee_id", "department"],
                extraction_confidence=0.95,
                metadata={"sample_rows": [["E-1", "Sales"], ["E-2", "Finance"]]},
            ),
            CanonicalTabularFrame(
                frame_id="payroll",
                label="Payroll",
                row_count=2,
                column_count=2,
                column_names=["employee_id", "salary"],
                extraction_confidence=0.95,
                metadata={"sample_rows": [["E-1", "1500"], ["E-2", "1200"]]},
            ),
        ],
        frame_relations=[
            CanonicalFrameRelation(
                relation_id="employees__payroll__join",
                relation_type="likely_join",
                left_frame_id="employees",
                right_frame_id="payroll",
                confidence=0.92,
                join_keys=["employee_id"],
            )
        ],
        metadata={"primary_frame_id": "employees"},
    )

    materialized = materialize_bundle(bundle)

    assert materialized.status == CanonicalMaterializationStatus.PREVIEW_ONLY
    assert len(materialized.derived_views) == 1
    assert materialized.derived_views[0].view_type == "likely_join"
    assert materialized.derived_views[0].row_count == 2
    assert "salary" in materialized.derived_views[0].column_names


def test_phase8_materializer_normalizes_unicode_document_headers() -> None:
    bundle = CanonicalArtifactBundle(
        source_manifest=_manifest(),
        tabular_frames=[
            CanonicalTabularFrame(
                frame_id="docx_table",
                label="DOCX Table",
                row_count=2,
                column_count=3,
                column_names=["Área", "Costo", "Variación %"],
                extraction_confidence=0.95,
                metadata={"sample_rows": [["Finanzas", "500", "4"], ["RRHH", "320", "2"]]},
            )
        ],
        metadata={"primary_frame_id": "docx_table"},
    )

    materialized = materialize_bundle(bundle)

    assert materialized.primary_frame is not None
    assert materialized.primary_frame.column_names == ["area", "costo", "variacion_percent"]
