from __future__ import annotations

from typing import Any

from app.core.canonical_artifacts import (
    ArtifactOperationalMode,
    ArtifactSourceKind,
    CanonicalArtifactBundle,
    CanonicalTabularFrame,
)
from app.core.config import settings
from app.services.canonical_header_normalizer import normalize_canonical_header


_METRIC_HEADER_FRAGMENTS = {
    "amount",
    "balance",
    "calories",
    "cost",
    "count",
    "discount",
    "expense",
    "hours",
    "importe",
    "income",
    "ingreso",
    "margin",
    "margen",
    "monto",
    "pct",
    "percent",
    "porcentaje",
    "price",
    "qty",
    "quantity",
    "rate",
    "revenue",
    "sales",
    "score",
    "stock",
    "total",
    "value",
    "variacion",
    "variation",
}
_DOCUMENT_ANALYTICS_SOURCE_KINDS = {
    ArtifactSourceKind.PDF.value,
    ArtifactSourceKind.WORD_PROCESSOR.value,
    ArtifactSourceKind.IMAGE.value,
}


def is_canonical_document_table_quality_gate_enabled() -> bool:
    return settings.CANONICAL_DOCUMENT_TABLE_QUALITY_GATE_ENABLED


def _normalize_name(value: Any) -> str:
    return normalize_canonical_header(value)


def _sample_rows(frame: CanonicalTabularFrame) -> list[list[str]]:
    metadata = frame.metadata if isinstance(frame.metadata, dict) else {}
    raw_rows = list(metadata.get("sample_rows") or [])
    normalized_rows: list[list[str]] = []
    for row in raw_rows:
        normalized_rows.append([str(cell or "").strip() for cell in list(row or [])])
    return normalized_rows


def _safe_divide(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator


def _non_empty_cell_ratio(rows: list[list[str]], expected_width: int) -> float:
    width = max(int(expected_width or 0), 1)
    total_cells = 0
    non_empty_cells = 0
    for row in rows:
        trimmed = list(row[:width]) + [""] * max(width - len(row), 0)
        total_cells += width
        non_empty_cells += sum(1 for cell in trimmed if str(cell or "").strip())
    return _safe_divide(non_empty_cells, total_cells)


def _parseable_numeric_ratio(rows: list[list[str]]) -> float:
    total = 0
    numeric = 0
    for row in rows:
        for value in row:
            text = str(value or "").strip()
            if not text:
                continue
            total += 1
            normalized = text.replace(",", "").replace("%", "")
            try:
                float(normalized)
            except Exception:
                continue
            numeric += 1
    return _safe_divide(numeric, total)


def _metric_header_ratio(column_names: list[str]) -> float:
    usable_headers = [_normalize_name(name) for name in column_names if _normalize_name(name)]
    if not usable_headers:
        return 0.0
    matches = 0
    for header in usable_headers:
        parts = {part for part in header.split("_") if part}
        if parts & _METRIC_HEADER_FRAGMENTS:
            matches += 1
    return _safe_divide(matches, len(usable_headers))


def _header_quality(column_names: list[str], expected_width: int) -> float:
    width = max(int(expected_width or 0), len(column_names), 1)
    non_empty = sum(1 for name in column_names[:width] if str(name or "").strip())
    return _safe_divide(non_empty, width)


def _header_uniqueness(column_names: list[str]) -> float:
    normalized = [_normalize_name(name) for name in column_names if _normalize_name(name)]
    if not normalized:
        return 0.0
    return _safe_divide(len(set(normalized)), len(normalized))


def _table_kind_bonus(frame: CanonicalTabularFrame) -> float:
    source_kind = str((frame.metadata or {}).get("source_kind") or "").strip().lower()
    if source_kind in {"pdf_table", "docx_table"}:
        return 0.12
    if source_kind in {"pdf_text_table", "ocr_table"}:
        return 0.08
    return 0.0


def profile_canonical_table_quality(frame: CanonicalTabularFrame) -> dict[str, Any]:
    row_count = max(int(frame.row_count or 0), 0)
    column_count = max(int(frame.column_count or 0), 0)
    column_names = [str(name or "").strip() for name in list(frame.column_names or [])]
    rows = _sample_rows(frame)
    header_quality = _header_quality(column_names, column_count)
    header_uniqueness = _header_uniqueness(column_names)
    body_fill_ratio = _non_empty_cell_ratio(rows, column_count) if rows else 0.0
    numeric_ratio = _parseable_numeric_ratio(rows)
    metric_ratio = _metric_header_ratio(column_names)
    extraction_confidence = float(frame.extraction_confidence or 0.0)
    row_score = min(row_count / 25.0, 1.0)
    column_score = min(column_count / 10.0, 1.0) if column_count else 0.0
    source_bonus = _table_kind_bonus(frame)
    width_penalty = 0.2 if column_count > 40 else 0.0
    sparsity_penalty = 0.2 if rows and body_fill_ratio < 0.4 else 0.0
    weak_metric_penalty = 0.15 if numeric_ratio < 0.15 and metric_ratio < 0.2 else 0.0

    score = (
        extraction_confidence * 0.2
        + header_quality * 0.2
        + header_uniqueness * 0.1
        + body_fill_ratio * 0.18
        + row_score * 0.12
        + column_score * 0.08
        + max(numeric_ratio, metric_ratio) * 0.12
        + source_bonus
        - width_penalty
        - sparsity_penalty
        - weak_metric_penalty
    )

    rejected_reasons: list[str] = []
    if row_count < settings.CANONICAL_DOCUMENT_TABLE_QUALITY_GATE_MIN_ROWS:
        rejected_reasons.append("insufficient_rows")
    if column_count < settings.CANONICAL_DOCUMENT_TABLE_QUALITY_GATE_MIN_COLUMNS:
        rejected_reasons.append("insufficient_columns")
    if header_quality < 0.6:
        rejected_reasons.append("weak_headers")
    if header_uniqueness < 0.6:
        rejected_reasons.append("repeated_headers")
    if rows and body_fill_ratio < 0.45:
        rejected_reasons.append("sparse_body")
    if numeric_ratio < 0.15 and metric_ratio < 0.2:
        rejected_reasons.append("missing_metric_signal")
    if score < settings.CANONICAL_DOCUMENT_TABLE_QUALITY_GATE_MIN_SCORE:
        rejected_reasons.append("score_below_threshold")

    passed = not rejected_reasons
    return {
        "passed": passed,
        "score": round(score, 4),
        "row_count": row_count,
        "column_count": column_count,
        "header_quality": round(header_quality, 4),
        "header_uniqueness": round(header_uniqueness, 4),
        "body_fill_ratio": round(body_fill_ratio, 4),
        "numeric_ratio": round(numeric_ratio, 4),
        "metric_header_ratio": round(metric_ratio, 4),
        "source_bonus": round(source_bonus, 4),
        "width_penalty": round(width_penalty, 4),
        "sparsity_penalty": round(sparsity_penalty, 4),
        "weak_metric_penalty": round(weak_metric_penalty, 4),
        "rejected_reasons": rejected_reasons,
    }


def _append_warning(bundle: CanonicalArtifactBundle, message: str) -> None:
    if message and message not in bundle.source_manifest.warnings:
        bundle.source_manifest.warnings.append(message)


def _promote_document_bundle(bundle: CanonicalArtifactBundle, qualified_frame_ids: list[str]) -> None:
    manifest = bundle.source_manifest
    manifest.analytics_ready = True
    if bundle.has_document_payload():
        manifest.preferred_mode = ArtifactOperationalMode.HYBRID
        manifest.candidate_modes = [
            ArtifactOperationalMode.HYBRID,
            ArtifactOperationalMode.ANALYTICAL,
            ArtifactOperationalMode.DOCUMENT_INTELLIGENCE,
        ]
    else:
        manifest.preferred_mode = ArtifactOperationalMode.ANALYTICAL
        manifest.candidate_modes = [ArtifactOperationalMode.ANALYTICAL]
    bundle.metadata["analytics_ready_frame_ids"] = qualified_frame_ids


def apply_canonical_document_table_quality_gate(
    bundle: CanonicalArtifactBundle,
) -> CanonicalArtifactBundle:
    if not settings.CANONICAL_DOCUMENT_TABLE_QUALITY_GATE_ENABLED:
        return bundle

    source_kind = bundle.source_manifest.source_kind.value
    if source_kind not in _DOCUMENT_ANALYTICS_SOURCE_KINDS:
        return bundle

    qualified_frame_ids: list[str] = []
    profiles: list[dict[str, Any]] = []
    for frame in bundle.tabular_frames:
        profile = profile_canonical_table_quality(frame)
        profiles.append({"frame_id": frame.frame_id, **profile})
        frame.metadata["quality_gate"] = profile
        frame.metadata["analytics_ready"] = profile["passed"]
        if profile["passed"]:
            qualified_frame_ids.append(frame.frame_id)

    bundle.metadata["quality_gate_applied"] = True
    bundle.metadata["quality_gate_profiles"] = profiles
    bundle.metadata["analytics_ready_frame_ids"] = qualified_frame_ids

    if qualified_frame_ids:
        _promote_document_bundle(bundle, qualified_frame_ids)
    elif bundle.tabular_frames:
        _append_warning(
            bundle,
            "Las tablas extraídas del documento no alcanzaron calidad analítica; el archivo se mantiene en modo documental.",
        )
    return bundle
