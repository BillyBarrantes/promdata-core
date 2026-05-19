from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _selected_candidate_row(
    analytical_summary: dict[str, Any],
    selected_candidate_id: str | None,
) -> dict[str, Any]:
    if not selected_candidate_id:
        return {}
    for row in list(analytical_summary.get("candidates") or []):
        if str(row.get("candidate_id") or "") == str(selected_candidate_id):
            return dict(row)
    return {}


def _selected_preview_table(
    preview_summary: dict[str, Any],
    selected_candidate_id: str | None,
) -> dict[str, Any]:
    if not selected_candidate_id:
        return {}
    for row in list(preview_summary.get("tables") or []):
        if str(row.get("table_name") or "") == str(selected_candidate_id):
            return dict(row)
    return {}


def _detect_format_key(file_name: str, source_kind: str) -> str:
    normalized = str(file_name or "").strip().lower()
    if "." in normalized:
        extension = normalized.rsplit(".", 1)[-1]
        if extension:
            return extension
    return str(source_kind or "unknown").strip().lower() or "unknown"


def _readiness_grade(
    *,
    support_level: str,
    source_kind: str,
    analytics_ready: bool,
    tabular_frame_count: int,
    selected_candidate_id: str | None,
    has_preview_data: bool,
    metric_count: int,
    comparison_grade: str,
    quality_gate_applied: bool,
) -> tuple[str, list[str]]:
    blockers: list[str] = []

    if support_level == "unsupported":
        return "unsupported", ["unsupported_format"]
    if tabular_frame_count <= 0:
        if source_kind in {"pdf", "word_processor", "image", "plain_text"}:
            return "document_only", ["no_tabular_frames"]
        return "needs_extraction", ["no_tabular_frames"]
    if not analytics_ready:
        blockers.append("analytics_not_ready")
    if not selected_candidate_id:
        blockers.append("no_selected_candidate")
    if not has_preview_data:
        blockers.append("empty_preview")
    if metric_count <= 0:
        blockers.append("no_metric_signal")
    if comparison_grade == "low_alignment":
        blockers.append("low_active_alignment")

    if not blockers:
        if support_level == "full_analytics":
            return "pilot_candidate", []
        if source_kind in {"pdf", "word_processor", "image"} and quality_gate_applied:
            return "shadow_ready", []
        return "shadow_ready", []

    if blockers == ["analytics_not_ready"] and source_kind in {"pdf", "word_processor", "image"}:
        return "needs_quality_gate", blockers
    return "needs_hardening", blockers


def build_shadow_format_readiness_summary(
    *,
    file_name: str,
    pipeline_summary: dict[str, Any],
    bundle_summary: dict[str, Any],
    materialized_summary: dict[str, Any],
    preview_summary: dict[str, Any],
    analytical_summary: dict[str, Any],
    runtime_comparison_summary: dict[str, Any],
) -> dict[str, Any]:
    source_kind = str(bundle_summary.get("source_kind") or "unknown")
    support_level = str(bundle_summary.get("support_level") or "unknown")
    selected_candidate_id = analytical_summary.get("selected_candidate_id")
    candidate_row = _selected_candidate_row(analytical_summary, selected_candidate_id)
    preview_table = _selected_preview_table(preview_summary, selected_candidate_id)

    tabular_frame_count = _safe_int(bundle_summary.get("tabular_frame_count"))
    analytics_ready = bool(bundle_summary.get("analytics_ready"))
    quality_gate_applied = bool(bundle_summary.get("quality_gate_applied"))
    metric_count = _safe_int(candidate_row.get("metric_count"))
    dimension_count = _safe_int(candidate_row.get("dimension_count"))
    preview_row_count = _safe_int(preview_table.get("row_count"))
    preview_column_count = _safe_int(preview_table.get("column_count"))
    has_preview_data = preview_row_count > 0 and preview_column_count > 0
    comparison_grade = str(runtime_comparison_summary.get("comparison_grade") or "unknown")
    format_key = _detect_format_key(file_name, source_kind)

    readiness_grade, blockers = _readiness_grade(
        support_level=support_level,
        source_kind=source_kind,
        analytics_ready=analytics_ready,
        tabular_frame_count=tabular_frame_count,
        selected_candidate_id=selected_candidate_id,
        has_preview_data=has_preview_data,
        metric_count=metric_count,
        comparison_grade=comparison_grade,
        quality_gate_applied=quality_gate_applied,
    )

    score = (
        (0.22 if analytics_ready else 0.0)
        + (0.18 if tabular_frame_count > 0 else 0.0)
        + (0.18 if selected_candidate_id else 0.0)
        + (0.14 if has_preview_data else 0.0)
        + min(metric_count, 3) * 0.08
        + (0.08 if quality_gate_applied else 0.0)
        + (
            0.12
            if comparison_grade in {"high_alignment", "partial_alignment", "no_active_runtime"}
            else 0.0
        )
    )
    score = round(min(score, 1.0), 4)

    return {
        "file_name": file_name,
        "format_key": format_key,
        "source_kind": source_kind,
        "support_level": support_level,
        "preferred_mode": str(bundle_summary.get("preferred_mode") or "unknown"),
        "pipeline_status": str(pipeline_summary.get("pipeline_status") or "unknown"),
        "readiness_grade": readiness_grade,
        "readiness_score": score,
        "blockers": blockers,
        "analytics_ready": analytics_ready,
        "quality_gate_applied": quality_gate_applied,
        "tabular_frame_count": tabular_frame_count,
        "preview_ready_tables": _safe_int(materialized_summary.get("preview_ready_tables")),
        "selected_candidate_id": selected_candidate_id,
        "candidate_count": _safe_int(analytical_summary.get("candidate_count")),
        "metric_count": metric_count,
        "dimension_count": dimension_count,
        "preview_row_count": preview_row_count,
        "preview_column_count": preview_column_count,
        "comparison_grade": comparison_grade,
        "active_runtime_available": bool(runtime_comparison_summary.get("active_runtime_available")),
    }


def summarize_shadow_corpus_readiness(
    readiness_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    rows = [dict(row) for row in list(readiness_rows or [])]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("format_key") or "unknown")].append(row)

    by_format: list[dict[str, Any]] = []
    for format_key, format_rows in grouped.items():
        readiness_counter = Counter(str(row.get("readiness_grade") or "unknown") for row in format_rows)
        blocker_counter = Counter(
            blocker
            for row in format_rows
            for blocker in list(row.get("blockers") or [])
        )
        total = len(format_rows)
        avg_score = round(
            sum(_safe_float(row.get("readiness_score")) for row in format_rows) / max(total, 1),
            4,
        )
        by_format.append(
            {
                "format_key": format_key,
                "file_count": total,
                "avg_readiness_score": avg_score,
                "analytics_ready_ratio": round(
                    sum(1 for row in format_rows if row.get("analytics_ready")) / max(total, 1),
                    4,
                ),
                "candidate_ratio": round(
                    sum(1 for row in format_rows if row.get("selected_candidate_id")) / max(total, 1),
                    4,
                ),
                "readiness_grades": dict(readiness_counter),
                "top_blockers": [item for item, _count in blocker_counter.most_common(3)],
            }
        )

    by_format.sort(
        key=lambda row: (-_safe_float(row.get("avg_readiness_score")), str(row.get("format_key"))),
    )
    integration_candidates = [
        row["format_key"]
        for row in by_format
        if any(grade in {"pilot_candidate", "shadow_ready"} for grade in row.get("readiness_grades", {}))
    ]
    return {
        "format_count": len(by_format),
        "file_count": len(rows),
        "integration_candidate_formats": integration_candidates,
        "formats": by_format,
    }
