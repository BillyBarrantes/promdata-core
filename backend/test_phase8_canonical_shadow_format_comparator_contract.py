from __future__ import annotations

from app.services.canonical_shadow_format_comparator import (
    build_shadow_format_readiness_summary,
    summarize_shadow_corpus_readiness,
)


def test_phase8_shadow_format_comparator_marks_native_tabular_as_pilot_candidate() -> None:
    summary = build_shadow_format_readiness_summary(
        file_name="dataset.csv",
        pipeline_summary={"pipeline_status": "ready_for_shadow_compare"},
        bundle_summary={
            "source_kind": "delimited_text",
            "support_level": "full_analytics",
            "preferred_mode": "analytical",
            "analytics_ready": True,
            "quality_gate_applied": False,
            "tabular_frame_count": 1,
        },
        materialized_summary={"preview_ready_tables": 1},
        preview_summary={"tables": [{"table_name": "primary__sales", "row_count": 3, "column_count": 4}]},
        analytical_summary={
            "selected_candidate_id": "primary__sales",
            "candidate_count": 1,
            "candidates": [
                {
                    "candidate_id": "primary__sales",
                    "metric_count": 2,
                    "dimension_count": 2,
                }
            ],
        },
        runtime_comparison_summary={"comparison_grade": "no_active_runtime", "active_runtime_available": False},
    )

    assert summary["format_key"] == "csv"
    assert summary["readiness_grade"] == "pilot_candidate"
    assert summary["readiness_score"] > 0.8


def test_phase8_shadow_format_comparator_marks_document_table_as_shadow_ready() -> None:
    summary = build_shadow_format_readiness_summary(
        file_name="seed_report.docx",
        pipeline_summary={"pipeline_status": "ready_for_shadow_compare"},
        bundle_summary={
            "source_kind": "word_processor",
            "support_level": "document_qa",
            "preferred_mode": "hybrid",
            "analytics_ready": True,
            "quality_gate_applied": True,
            "tabular_frame_count": 1,
        },
        materialized_summary={"preview_ready_tables": 1},
        preview_summary={"tables": [{"table_name": "primary__docx-table-1", "row_count": 2, "column_count": 3}]},
        analytical_summary={
            "selected_candidate_id": "primary__docx-table-1",
            "candidate_count": 1,
            "candidates": [
                {
                    "candidate_id": "primary__docx-table-1",
                    "metric_count": 2,
                    "dimension_count": 1,
                }
            ],
        },
        runtime_comparison_summary={"comparison_grade": "no_active_runtime", "active_runtime_available": False},
    )

    assert summary["format_key"] == "docx"
    assert summary["readiness_grade"] == "shadow_ready"
    assert summary["quality_gate_applied"] is True


def test_phase8_shadow_format_comparator_flags_missing_candidate_as_needs_hardening() -> None:
    summary = build_shadow_format_readiness_summary(
        file_name="scan.png",
        pipeline_summary={"pipeline_status": "tabular_detected_without_candidate"},
        bundle_summary={
            "source_kind": "image",
            "support_level": "ocr_only",
            "preferred_mode": "document_intelligence",
            "analytics_ready": False,
            "quality_gate_applied": False,
            "tabular_frame_count": 1,
        },
        materialized_summary={"preview_ready_tables": 0},
        preview_summary={"tables": []},
        analytical_summary={"selected_candidate_id": None, "candidate_count": 0, "candidates": []},
        runtime_comparison_summary={"comparison_grade": "no_candidate", "active_runtime_available": False},
    )

    assert summary["readiness_grade"] in {"needs_quality_gate", "needs_hardening"}
    assert "no_selected_candidate" in summary["blockers"]


def test_phase8_shadow_format_comparator_summarizes_corpus_by_format() -> None:
    corpus = summarize_shadow_corpus_readiness(
        [
            {
                "format_key": "csv",
                "readiness_grade": "pilot_candidate",
                "readiness_score": 0.92,
                "analytics_ready": True,
                "selected_candidate_id": "primary__sales",
                "blockers": [],
            },
            {
                "format_key": "docx",
                "readiness_grade": "shadow_ready",
                "readiness_score": 0.78,
                "analytics_ready": True,
                "selected_candidate_id": "primary__docx-table-1",
                "blockers": [],
            },
            {
                "format_key": "png",
                "readiness_grade": "needs_hardening",
                "readiness_score": 0.42,
                "analytics_ready": False,
                "selected_candidate_id": None,
                "blockers": ["no_selected_candidate"],
            },
        ]
    )

    assert corpus["format_count"] == 3
    assert "csv" in corpus["integration_candidate_formats"]
    assert corpus["formats"][0]["format_key"] == "csv"
