from __future__ import annotations

from itertools import zip_longest
from typing import Any

from app.core.canonical_artifacts import (
    CanonicalArtifactBundle,
    CanonicalFrameRelation,
    CanonicalMaterializationStatus,
    CanonicalMaterializedBundle,
    CanonicalMaterializedFrame,
    CanonicalMaterializedView,
    CanonicalTabularFrame,
)
from app.services.canonical_bundle_orchestrator import infer_frame_relations
from app.services.canonical_header_normalizer import normalize_canonical_header


def _normalize_column_name(name: str, *, index: int) -> str:
    return normalize_canonical_header(name, index=index)


def _frame_aliases(frame: CanonicalTabularFrame) -> tuple[list[str], dict[str, str]]:
    aliases: dict[str, str] = {}
    normalized_columns: list[str] = []
    for index, original_name in enumerate(frame.column_names, start=1):
        normalized = _normalize_column_name(original_name, index=index)
        candidate = normalized
        suffix = 2
        while candidate in aliases:
            candidate = f"{normalized}_{suffix}"
            suffix += 1
        aliases[candidate] = str(original_name or "").strip() or candidate
        normalized_columns.append(candidate)
    return normalized_columns, aliases


def _coerce_rows_to_records(
    *,
    rows: list[Any],
    normalized_columns: list[str],
    aliases: dict[str, str],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for row in list(rows or []):
        if isinstance(row, dict):
            record = {}
            for key in normalized_columns:
                original_name = aliases.get(key, key)
                if key in row:
                    record[key] = row.get(key)
                else:
                    record[key] = row.get(original_name)
            records.append(record)
            continue
        values = list(row or [])
        record = {}
        for key, value in zip_longest(normalized_columns, values, fillvalue=None):
            if key is None:
                continue
            record[key] = value
        records.append(record)
    return records


def _resolve_frame_records(frame: CanonicalTabularFrame) -> tuple[list[dict[str, Any]], CanonicalMaterializationStatus, dict[str, Any]]:
    metadata = frame.metadata if isinstance(frame.metadata, dict) else {}
    normalized_columns, aliases = _frame_aliases(frame)

    if metadata.get("delegated"):
        return [], CanonicalMaterializationStatus.DEFERRED, {"column_aliases": aliases, "payload_kind": "delegated"}

    rows_payload = metadata.get("rows_payload")
    if isinstance(rows_payload, list) and rows_payload:
        return (
            _coerce_rows_to_records(rows=rows_payload, normalized_columns=normalized_columns, aliases=aliases),
            CanonicalMaterializationStatus.READY,
            {"column_aliases": aliases, "payload_kind": "rows_payload"},
        )

    sample_rows = metadata.get("sample_rows")
    if isinstance(sample_rows, list) and sample_rows:
        return (
            _coerce_rows_to_records(rows=sample_rows, normalized_columns=normalized_columns, aliases=aliases),
            CanonicalMaterializationStatus.PREVIEW_ONLY,
            {"column_aliases": aliases, "payload_kind": "sample_rows"},
        )

    return [], CanonicalMaterializationStatus.EMPTY, {"column_aliases": aliases, "payload_kind": "empty"}


def materialize_frame(
    frame: CanonicalTabularFrame,
    *,
    relation: CanonicalFrameRelation | None = None,
) -> CanonicalMaterializedFrame:
    records, status, metadata = _resolve_frame_records(frame)
    return CanonicalMaterializedFrame(
        frame_id=frame.frame_id,
        label=frame.label,
        status=status,
        relation_type=relation.relation_type if relation else None,
        join_keys=list(relation.join_keys) if relation else [],
        row_count=len(records),
        column_names=list(metadata["column_aliases"].keys()),
        records=records,
        metadata={
            **metadata,
            "source_frame_row_count": int(frame.row_count or 0),
            "extraction_confidence": float(frame.extraction_confidence or 0.0),
            "relation_confidence": float(relation.confidence) if relation else None,
        },
    )


def _status_from_inputs(statuses: list[CanonicalMaterializationStatus]) -> CanonicalMaterializationStatus:
    normalized = list(statuses)
    if not normalized:
        return CanonicalMaterializationStatus.EMPTY
    if all(status == CanonicalMaterializationStatus.DEFERRED for status in normalized):
        return CanonicalMaterializationStatus.DEFERRED
    if any(status == CanonicalMaterializationStatus.PREVIEW_ONLY for status in normalized):
        return CanonicalMaterializationStatus.PREVIEW_ONLY
    if any(status == CanonicalMaterializationStatus.DEFERRED for status in normalized):
        return CanonicalMaterializationStatus.PREVIEW_ONLY
    if any(status == CanonicalMaterializationStatus.READY for status in normalized):
        return CanonicalMaterializationStatus.READY
    return CanonicalMaterializationStatus.EMPTY


def _header_overlap_from_names(names1: list[str], names2: list[str]) -> float:
    if not names1 or not names2:
        return 0.0
    s1 = {n.lower().strip() for n in names1}
    s2 = {n.lower().strip() for n in names2}
    return len(s1 & s2) / max(len(s1), len(s2))


def _build_unified_materialized_view(
    primary_frame: CanonicalMaterializedFrame,
    related_frames: list[CanonicalMaterializedFrame],
) -> CanonicalMaterializedView | None:
    """Build a unified view by UNIONing ALL raw frames with schema drift tolerance.

    Normalizes column names (LOWER+TRIM), aligns by name, NULL-fills missing columns.
    Only activates when 3+ frames share >=85% header overlap.
    """
    all_frames = [primary_frame] + (related_frames or [])
    if len(all_frames) < 3:
        return None

    # Normalize column names per frame
    normalized_schemas: list[dict[str, str]] = []
    for frame in all_frames:
        lowered = {col.lower().strip(): col for col in (frame.column_names or [])}
        normalized_schemas.append(lowered)

    # Header overlap check: all pairs must have >=85% overlap
    for i in range(len(all_frames)):
        for j in range(i + 1, len(all_frames)):
            if _header_overlap_from_names(
                all_frames[i].column_names or [],
                all_frames[j].column_names or [],
            ) < 0.85:
                return None

    # Column intersection: only columns present in ALL frames survive
    common_cols_norm = set(normalized_schemas[0].keys())
    for schema in normalized_schemas[1:]:
        common_cols_norm &= set(schema.keys())
    if not common_cols_norm:
        return None

    # Build unified records: concatenate all frames with normalized column names
    unified_records: list[dict[str, Any]] = []
    for i, frame in enumerate(all_frames):
        schema = normalized_schemas[i]
        for row in (frame.records or []):
            unified_row: dict[str, Any] = {}
            for col_norm in common_cols_norm:
                original_col = schema[col_norm]
                unified_row[col_norm] = row.get(original_col)
            unified_records.append(unified_row)

    if not unified_records:
        return None

    sorted_columns = sorted(common_cols_norm)
    return CanonicalMaterializedView(
        view_id="unified_all__sheets",
        view_type="likely_union",
        status=primary_frame.status,
        source_frame_ids=[f.frame_id for f in all_frames],
        row_count=len(unified_records),
        column_names=sorted_columns,
        records=unified_records,
        metadata={
            "materialization_mode": "unified_all",
            "unified_frame_count": len(all_frames),
        },
    )


def _concat_records(
    left: CanonicalMaterializedFrame,
    right: CanonicalMaterializedFrame,
) -> CanonicalMaterializedView | None:
    if not left.records or not right.records:
        return None
    all_columns: list[str] = []
    for column in [*left.column_names, *right.column_names]:
        if column not in all_columns:
            all_columns.append(column)
    records: list[dict[str, Any]] = []
    for source in (left.records, right.records):
        for row in source:
            normalized_row = {column: row.get(column) for column in all_columns}
            records.append(normalized_row)
    status = _status_from_inputs([left.status, right.status])
    return CanonicalMaterializedView(
        view_id=f"{left.frame_id}__{right.frame_id}__union_preview",
        view_type="likely_union",
        status=status,
        source_frame_ids=[left.frame_id, right.frame_id],
        row_count=len(records),
        column_names=all_columns,
        records=records,
        metadata={"materialization_mode": "preview_union"},
    )


def _merge_records_left(
    left: CanonicalMaterializedFrame,
    right: CanonicalMaterializedFrame,
    *,
    join_keys: list[str],
) -> CanonicalMaterializedView | None:
    if not left.records or not right.records or not join_keys:
        return None

    right_lookup: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in right.records:
        key = tuple(row.get(join_key) for join_key in join_keys)
        if any(value not in (None, "") for value in key):
            right_lookup[key] = row

    right_extra_columns = [column for column in right.column_names if column not in join_keys]
    merged_records: list[dict[str, Any]] = []
    for left_row in left.records:
        key = tuple(left_row.get(join_key) for join_key in join_keys)
        right_row = right_lookup.get(key, {})
        merged_row = dict(left_row)
        for column in right_extra_columns:
            target_column = column if column not in merged_row else f"{right.frame_id}__{column}"
            merged_row[target_column] = right_row.get(column)
        merged_records.append(merged_row)

    merged_columns = list(merged_records[0].keys()) if merged_records else list(left.column_names)
    status = _status_from_inputs([left.status, right.status])
    return CanonicalMaterializedView(
        view_id=f"{left.frame_id}__{right.frame_id}__join_preview",
        view_type="likely_join",
        status=status,
        source_frame_ids=[left.frame_id, right.frame_id],
        row_count=len(merged_records),
        column_names=merged_columns,
        records=merged_records,
        metadata={"materialization_mode": "preview_left_join", "join_keys": join_keys},
    )


def materialize_bundle(
    bundle: CanonicalArtifactBundle,
    *,
    primary_frame_id: str | None = None,
) -> CanonicalMaterializedBundle:
    frame_by_id = {frame.frame_id: frame for frame in bundle.tabular_frames}
    resolved_primary_id = primary_frame_id or str(bundle.metadata.get("primary_frame_id") or "") or None
    primary_frame = frame_by_id.get(resolved_primary_id) if resolved_primary_id else None
    if primary_frame is None and bundle.tabular_frames:
        primary_frame = bundle.tabular_frames[0]
        resolved_primary_id = primary_frame.frame_id

    if primary_frame is None:
        return CanonicalMaterializedBundle(
            primary_frame_id=None,
            status=CanonicalMaterializationStatus.EMPTY,
            metadata={"materializer_backend": "python"},
        )

    relations = list(bundle.frame_relations) or infer_frame_relations(bundle)
    relation_map: dict[str, CanonicalFrameRelation] = {}
    related_frame_ids: list[str] = []
    for relation in relations:
        if relation.left_frame_id == resolved_primary_id:
            relation_map[relation.right_frame_id] = relation
            related_frame_ids.append(relation.right_frame_id)
        elif relation.right_frame_id == resolved_primary_id:
            relation_map[relation.left_frame_id] = relation
            related_frame_ids.append(relation.left_frame_id)

    primary_materialized = materialize_frame(primary_frame)
    related_materialized: list[CanonicalMaterializedFrame] = []
    derived_views: list[CanonicalMaterializedView] = []

    for frame_id in related_frame_ids:
        frame = frame_by_id.get(frame_id)
        if frame is None:
            continue
        relation = relation_map.get(frame_id)
        materialized = materialize_frame(frame, relation=relation)
        related_materialized.append(materialized)
        if relation is None:
            continue
        if relation.relation_type == "likely_union":
            derived = _concat_records(primary_materialized, materialized)
        elif relation.relation_type == "likely_join":
            derived = _merge_records_left(primary_materialized, materialized, join_keys=list(relation.join_keys))
        else:
            derived = None
        if derived is not None:
            derived_views.append(derived)

    # ── UNIFIED ALL SHEETS VIEW ────────────────────────────────
    # If 3+ frames share >=85% schema overlap, build a unified
    # view that UNIONs ALL raw frames with schema drift tolerance.
    # This becomes the highest-priority candidate, ensuring ALL
    # dashboard plans see the complete multi-year dataset.
    # ───────────────────────────────────────────────────────────
    if primary_materialized and related_materialized:
        unified_view = _build_unified_materialized_view(
            primary_materialized, related_materialized,
        )
        if unified_view:
            derived_views.append(unified_view)

    overall_status = _status_from_inputs(
        [primary_materialized.status, *[frame.status for frame in related_materialized], *[view.status for view in derived_views]]
    )
    return CanonicalMaterializedBundle(
        primary_frame_id=resolved_primary_id,
        status=overall_status,
        primary_frame=primary_materialized,
        related_frames=related_materialized,
        derived_views=derived_views,
        metadata={
            "materializer_backend": "python",
            "relation_count": len(relations),
            "related_frame_count": len(related_materialized),
            "derived_view_count": len(derived_views),
            # [FASE 1 MULTI-HOJA] Relaciones serializadas para consumo cross-sheet
            "frame_relations": [
                r.model_dump() if hasattr(r, "model_dump") else dict(r)
                for r in relations
            ],
        },
    )
