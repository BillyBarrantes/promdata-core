from __future__ import annotations

import re
from typing import Any

from app.core.canonical_artifacts import CanonicalArtifactBundle, CanonicalFrameRelation, CanonicalTabularFrame
from app.services.canonical_header_normalizer import normalize_canonical_header


_ID_TOKEN_PATTERN = re.compile(r"(?:^|_)(?:id|code|codigo|key|uuid|dni|ruc|sku|folio|account|employee|client)(?:$|_)")


def _normalize_name(name: str) -> str:
    return normalize_canonical_header(name)


def _sample_rows(frame: CanonicalTabularFrame) -> list[list[str]]:
    metadata = frame.metadata if isinstance(frame.metadata, dict) else {}
    rows = metadata.get("sample_rows") or []
    return [[str(cell or "").strip() for cell in list(row or [])] for row in list(rows)]


def _header_index(frame: CanonicalTabularFrame) -> dict[str, int]:
    return {
        _normalize_name(name): index
        for index, name in enumerate(frame.column_names)
        if _normalize_name(name)
    }


def _shared_header_names(left: CanonicalTabularFrame, right: CanonicalTabularFrame) -> list[str]:
    left_headers = set(_header_index(left))
    right_headers = set(_header_index(right))
    return sorted(left_headers & right_headers)


def _header_overlap_ratio(left: CanonicalTabularFrame, right: CanonicalTabularFrame) -> float:
    left_headers = set(_header_index(left))
    right_headers = set(_header_index(right))
    if not left_headers or not right_headers:
        return 0.0
    return len(left_headers & right_headers) / max(len(left_headers), len(right_headers))


def _value_overlap_ratio(
    left: CanonicalTabularFrame,
    right: CanonicalTabularFrame,
    *,
    shared_key: str,
) -> float:
    left_index = _header_index(left).get(shared_key)
    right_index = _header_index(right).get(shared_key)
    if left_index is None or right_index is None:
        return 0.0

    left_values = {
        row[left_index]
        for row in _sample_rows(left)
        if left_index < len(row) and str(row[left_index] or "").strip()
    }
    right_values = {
        row[right_index]
        for row in _sample_rows(right)
        if right_index < len(row) and str(row[right_index] or "").strip()
    }
    if not left_values or not right_values:
        return 0.0
    return len(left_values & right_values) / min(len(left_values), len(right_values))


def _is_identifier_like(column_name: str) -> bool:
    normalized = _normalize_name(column_name)
    return bool(_ID_TOKEN_PATTERN.search(normalized))


def _build_join_relation(
    *,
    left: CanonicalTabularFrame,
    right: CanonicalTabularFrame,
    shared_keys: list[str],
) -> CanonicalFrameRelation | None:
    if not shared_keys:
        return None
    scored_keys: list[tuple[str, float, bool, int]] = []
    for key in shared_keys:
        overlap = _value_overlap_ratio(left, right, shared_key=key)
        left_idx = _header_index(left).get(key)
        right_idx = _header_index(right).get(key)
        total_unique = 0
        if left_idx is not None and right_idx is not None:
            left_vals = {
                row[left_idx]
                for row in _sample_rows(left)
                if left_idx < len(row) and str(row[left_idx] or "").strip()
            }
            right_vals = {
                row[right_idx]
                for row in _sample_rows(right)
                if right_idx < len(row) and str(row[right_idx] or "").strip()
            }
            total_unique = len(left_vals | right_vals)
        scored_keys.append((key, overlap, _is_identifier_like(key), total_unique))
    scored_keys.sort(key=lambda item: (item[1], item[2], item[3]), reverse=True)
    best_key, best_overlap, identifier_like, _ = scored_keys[0]
    if best_overlap <= 0:
        return None

    confidence = min(
        0.98,
        0.45
        + best_overlap * 0.35
        + (0.15 if identifier_like else 0.0)
        + min(len(shared_keys), 3) * 0.05,
    )
    return CanonicalFrameRelation(
        relation_id=f"{left.frame_id}__{right.frame_id}__join",
        relation_type="likely_join",
        left_frame_id=left.frame_id,
        right_frame_id=right.frame_id,
        confidence=round(confidence, 4),
        join_keys=[best_key],
        evidence={
            "shared_keys": shared_keys,
            "best_key_overlap": round(best_overlap, 4),
            "header_overlap_ratio": round(_header_overlap_ratio(left, right), 4),
        },
        metadata={"identifier_like": identifier_like},
    )


def _build_union_relation(
    *,
    left: CanonicalTabularFrame,
    right: CanonicalTabularFrame,
) -> CanonicalFrameRelation | None:
    header_overlap = _header_overlap_ratio(left, right)
    if header_overlap < 0.85:
        return None
    same_width = abs(int(left.column_count or 0) - int(right.column_count or 0)) <= 1
    if not same_width:
        return None
    confidence = min(0.95, 0.55 + header_overlap * 0.35)
    return CanonicalFrameRelation(
        relation_id=f"{left.frame_id}__{right.frame_id}__union",
        relation_type="likely_union",
        left_frame_id=left.frame_id,
        right_frame_id=right.frame_id,
        confidence=round(confidence, 4),
        join_keys=[],
        evidence={
            "header_overlap_ratio": round(header_overlap, 4),
            "left_columns": list(left.column_names),
            "right_columns": list(right.column_names),
        },
        metadata={},
    )


def infer_frame_relations(bundle: CanonicalArtifactBundle) -> list[CanonicalFrameRelation]:
    frames = list(bundle.tabular_frames)
    relations: list[CanonicalFrameRelation] = []
    for left_index in range(len(frames)):
        left = frames[left_index]
        for right in frames[left_index + 1 :]:
            shared_keys = _shared_header_names(left, right)
            join_relation = _build_join_relation(left=left, right=right, shared_keys=shared_keys)
            if join_relation is not None:
                relations.append(join_relation)
                continue
            union_relation = _build_union_relation(left=left, right=right)
            if union_relation is not None:
                relations.append(union_relation)
    relations.sort(key=lambda relation: (-relation.confidence, relation.relation_id))
    return relations


def summarize_frame_graph(
    bundle: CanonicalArtifactBundle,
    *,
    primary_frame_id: str | None = None,
) -> dict[str, Any]:
    relations = infer_frame_relations(bundle)
    related_frame_ids: list[str] = []
    if primary_frame_id:
        for relation in relations:
            if relation.left_frame_id == primary_frame_id:
                related_frame_ids.append(relation.right_frame_id)
            elif relation.right_frame_id == primary_frame_id:
                related_frame_ids.append(relation.left_frame_id)
    seen: set[str] = set()
    deduped_related = []
    for frame_id in related_frame_ids:
        if frame_id in seen:
            continue
        seen.add(frame_id)
        deduped_related.append(frame_id)
    dominant_relation = relations[0].relation_type if relations else None
    return {
        "relation_count": len(relations),
        "related_frame_ids": deduped_related,
        "dominant_relation_type": dominant_relation,
        "relations": [relation.model_dump() for relation in relations],
    }
