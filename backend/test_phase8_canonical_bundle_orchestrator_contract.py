from __future__ import annotations

from app.core.canonical_artifacts import (
    ArtifactAvailabilityStatus,
    ArtifactOperationalMode,
    ArtifactSupportLevel,
    ArtifactSourceKind,
    CanonicalArtifactBundle,
    CanonicalSourceManifest,
    CanonicalTabularFrame,
)
from app.services.canonical_bundle_orchestrator import infer_frame_relations, summarize_frame_graph


def _manifest() -> CanonicalSourceManifest:
    return CanonicalSourceManifest(
        file_name="enterprise-bundle.xlsx",
        extension="xlsx",
        source_kind=ArtifactSourceKind.SPREADSHEET,
        support_level=ArtifactSupportLevel.FULL_ANALYTICS,
        availability_status=ArtifactAvailabilityStatus.ACTIVE,
        preferred_mode=ArtifactOperationalMode.ANALYTICAL,
        analytics_ready=True,
    )


def test_phase8_orchestrator_infers_join_candidate_from_shared_identifier() -> None:
    bundle = CanonicalArtifactBundle(
        source_manifest=_manifest(),
        tabular_frames=[
            CanonicalTabularFrame(
                frame_id="employees",
                label="Employees",
                row_count=10,
                column_count=2,
                column_names=["employee_id", "department"],
                extraction_confidence=0.95,
                metadata={"sample_rows": [["E-1", "Sales"], ["E-2", "Finance"]]},
            ),
            CanonicalTabularFrame(
                frame_id="payroll",
                label="Payroll",
                row_count=10,
                column_count=2,
                column_names=["employee_id", "salary"],
                extraction_confidence=0.95,
                metadata={"sample_rows": [["E-1", "1500"], ["E-2", "1200"]]},
            ),
        ],
    )

    relations = infer_frame_relations(bundle)

    assert len(relations) == 1
    assert relations[0].relation_type == "likely_join"
    assert relations[0].join_keys == ["employee_id"]
    assert relations[0].confidence > 0.7


def test_phase8_orchestrator_summarizes_primary_related_frames() -> None:
    bundle = CanonicalArtifactBundle(
        source_manifest=_manifest(),
        tabular_frames=[
            CanonicalTabularFrame(
                frame_id="sales_q1",
                label="Sales Q1",
                row_count=50,
                column_count=3,
                column_names=["region", "amount", "month"],
                extraction_confidence=0.9,
                metadata={"sample_rows": [["North", "100", "Jan"]]},
            ),
            CanonicalTabularFrame(
                frame_id="sales_q2",
                label="Sales Q2",
                row_count=48,
                column_count=3,
                column_names=["region", "amount", "month"],
                extraction_confidence=0.91,
                metadata={"sample_rows": [["South", "120", "Apr"]]},
            ),
        ],
    )

    summary = summarize_frame_graph(bundle, primary_frame_id="sales_q1")

    assert summary["relation_count"] == 1
    assert summary["dominant_relation_type"] == "likely_union"
    assert summary["related_frame_ids"] == ["sales_q2"]
