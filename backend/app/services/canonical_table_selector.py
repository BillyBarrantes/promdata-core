from __future__ import annotations

from typing import Any

from app.core.canonical_artifacts import CanonicalArtifactBundle, CanonicalTabularFrame


def _score_frame(frame: CanonicalTabularFrame) -> tuple[float, dict[str, Any]]:
    row_count = max(int(frame.row_count or 0), 0)
    column_count = max(int(frame.column_count or 0), 0)
    extraction_confidence = float(frame.extraction_confidence or 0.0)
    column_names = [str(name or "").strip() for name in frame.column_names]

    non_empty_headers = sum(1 for name in column_names if name)
    header_quality = (non_empty_headers / len(column_names)) if column_names else 0.0
    metadata = frame.metadata if isinstance(frame.metadata, dict) else {}
    delegated_bonus = 0.2 if metadata.get("delegated") else 0.0
    table_bonus = 0.15 if metadata.get("source_kind") in {"pdf_table", "docx_table"} else 0.0
    quality_gate = metadata.get("quality_gate") if isinstance(metadata.get("quality_gate"), dict) else {}
    quality_gate_score = float(quality_gate.get("score") or 0.0)
    analytics_ready_bonus = 0.25 if quality_gate.get("passed") else 0.0
    quality_gate_penalty = 0.2 if quality_gate and not quality_gate.get("passed") else 0.0
    width_penalty = 0.25 if column_count > 40 else 0.0

    row_score = min(row_count / 250.0, 1.0)
    column_score = min(column_count / 15.0, 1.0) if column_count else 0.0

    score = (
        extraction_confidence * 0.45
        + row_score * 0.25
        + column_score * 0.1
        + header_quality * 0.25
        + delegated_bonus
        + table_bonus
        + quality_gate_score * 0.15
        + analytics_ready_bonus
        - width_penalty
        - quality_gate_penalty
    )
    diagnostics = {
        "row_count": row_count,
        "column_count": column_count,
        "extraction_confidence": extraction_confidence,
        "header_quality": round(header_quality, 4),
        "delegated_bonus": delegated_bonus,
        "table_bonus": table_bonus,
        "quality_gate_score": round(quality_gate_score, 4),
        "analytics_ready_bonus": analytics_ready_bonus,
        "quality_gate_penalty": quality_gate_penalty,
        "width_penalty": width_penalty,
        "score": round(score, 4),
    }
    return score, diagnostics


def rank_canonical_frames(bundle: CanonicalArtifactBundle) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    for frame in bundle.tabular_frames:
        score, diagnostics = _score_frame(frame)
        ranked.append(
            {
                "frame_id": frame.frame_id,
                "label": frame.label,
                "score": round(score, 4),
                "diagnostics": diagnostics,
                "column_names": list(frame.column_names),
            }
        )
    ranked.sort(key=lambda item: (-item["score"], item["label"]))
    return ranked


def select_primary_frame(bundle: CanonicalArtifactBundle) -> CanonicalTabularFrame | None:
    if not bundle.tabular_frames:
        return None
    ranked = rank_canonical_frames(bundle)
    winner_id = ranked[0]["frame_id"]
    for frame in bundle.tabular_frames:
        if frame.frame_id == winner_id:
            return frame
    return bundle.tabular_frames[0]
