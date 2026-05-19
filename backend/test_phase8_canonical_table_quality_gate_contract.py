from __future__ import annotations

from app.core.canonical_artifacts import (
    ArtifactOperationalMode,
    ArtifactSourceKind,
    ArtifactSupportLevel,
    CanonicalArtifactBundle,
    CanonicalSourceManifest,
    CanonicalTabularFrame,
    CanonicalTextBlock,
)
from app.core.config import settings
from app.services.canonical_table_quality_gate import (
    apply_canonical_document_table_quality_gate,
    is_canonical_document_table_quality_gate_enabled,
    profile_canonical_table_quality,
)


def _build_manifest() -> CanonicalSourceManifest:
    return CanonicalSourceManifest(
        file_name="seed_report.pdf",
        extension="pdf",
        source_kind=ArtifactSourceKind.PDF,
        support_level=ArtifactSupportLevel.DOCUMENT_QA,
        preferred_mode=ArtifactOperationalMode.DOCUMENT_INTELLIGENCE,
        candidate_modes=[ArtifactOperationalMode.DOCUMENT_INTELLIGENCE, ArtifactOperationalMode.HYBRID],
    )


def test_phase8_document_table_quality_gate_flag_defaults_to_off() -> None:
    assert is_canonical_document_table_quality_gate_enabled() is False


def test_phase8_document_table_quality_gate_profiles_strong_table() -> None:
    frame = CanonicalTabularFrame(
        frame_id="pdf-text-table-1",
        label="PDF Text Table #1",
        row_count=2,
        column_count=3,
        column_names=["Canal", "Ingreso", "Margen"],
        extraction_confidence=0.55,
        metadata={
            "source_kind": "pdf_text_table",
            "sample_rows": [
                ["Online", "1200", "18"],
                ["Retail", "900", "12"],
            ],
        },
    )

    profile = profile_canonical_table_quality(frame)

    assert profile["passed"] is True
    assert profile["score"] >= settings.CANONICAL_DOCUMENT_TABLE_QUALITY_GATE_MIN_SCORE
    assert profile["metric_header_ratio"] > 0


def test_phase8_document_table_quality_gate_promotes_document_bundle(monkeypatch) -> None:
    monkeypatch.setattr(settings, "CANONICAL_DOCUMENT_TABLE_QUALITY_GATE_ENABLED", True)
    bundle = CanonicalArtifactBundle(
        source_manifest=_build_manifest(),
        tabular_frames=[
            CanonicalTabularFrame(
                frame_id="pdf-text-table-1",
                label="PDF Text Table #1",
                row_count=2,
                column_count=3,
                column_names=["Canal", "Ingreso", "Margen"],
                extraction_confidence=0.55,
                metadata={
                    "source_kind": "pdf_text_table",
                    "sample_rows": [
                        ["Online", "1200", "18"],
                        ["Retail", "900", "12"],
                    ],
                },
            )
        ],
        text_blocks=[CanonicalTextBlock(block_id="block-1", text="Resumen", extraction_confidence=1.0)],
    )

    result = apply_canonical_document_table_quality_gate(bundle)

    assert result.source_manifest.analytics_ready is True
    assert result.source_manifest.preferred_mode == ArtifactOperationalMode.HYBRID
    assert result.metadata["analytics_ready_frame_ids"] == ["pdf-text-table-1"]
    assert result.tabular_frames[0].metadata["quality_gate"]["passed"] is True


def test_phase8_document_table_quality_gate_rejects_weak_single_column_table(monkeypatch) -> None:
    monkeypatch.setattr(settings, "CANONICAL_DOCUMENT_TABLE_QUALITY_GATE_ENABLED", True)
    bundle = CanonicalArtifactBundle(
        source_manifest=_build_manifest(),
        tabular_frames=[
            CanonicalTabularFrame(
                frame_id="docx-table-1",
                label="DOCX Table #1",
                row_count=1,
                column_count=1,
                column_names=["Observacion"],
                extraction_confidence=0.95,
                metadata={
                    "source_kind": "docx_table",
                    "sample_rows": [["OK"]],
                },
            )
        ],
        text_blocks=[CanonicalTextBlock(block_id="block-1", text="Checklist", extraction_confidence=1.0)],
    )

    result = apply_canonical_document_table_quality_gate(bundle)

    assert result.source_manifest.analytics_ready is False
    assert result.tabular_frames[0].metadata["quality_gate"]["passed"] is False
    assert "insufficient_columns" in result.tabular_frames[0].metadata["quality_gate"]["rejected_reasons"]


def test_phase8_document_table_quality_gate_promotes_ocr_table(monkeypatch) -> None:
    monkeypatch.setattr(settings, "CANONICAL_DOCUMENT_TABLE_QUALITY_GATE_ENABLED", True)
    bundle = CanonicalArtifactBundle(
        source_manifest=CanonicalSourceManifest(
            file_name="seed_report.png",
            extension="png",
            source_kind=ArtifactSourceKind.IMAGE,
            support_level=ArtifactSupportLevel.OCR_ONLY,
            preferred_mode=ArtifactOperationalMode.DOCUMENT_INTELLIGENCE,
            candidate_modes=[ArtifactOperationalMode.DOCUMENT_INTELLIGENCE, ArtifactOperationalMode.HYBRID],
        ),
        tabular_frames=[
            CanonicalTabularFrame(
                frame_id="ocr-table-1",
                label="OCR Table #1",
                row_count=2,
                column_count=3,
                column_names=["Canal", "Ingreso", "Margen"],
                extraction_confidence=0.92,
                metadata={
                    "source_kind": "ocr_table",
                    "sample_rows": [
                        ["Online", "1200", "18"],
                        ["Retail", "900", "12"],
                    ],
                },
            )
        ],
        text_blocks=[CanonicalTextBlock(block_id="block-1", text="Canal Ingreso Margen", extraction_confidence=0.9)],
    )

    result = apply_canonical_document_table_quality_gate(bundle)

    assert result.source_manifest.analytics_ready is True
    assert result.source_manifest.preferred_mode == ArtifactOperationalMode.HYBRID
    assert result.metadata["analytics_ready_frame_ids"] == ["ocr-table-1"]
