from __future__ import annotations

from app.core.canonical_artifacts import ArtifactLineageRef, CanonicalTabularFrame


def _normalize_header_name(name: str) -> str:
    return "".join(ch.lower() for ch in str(name or "").strip() if ch.isalnum())


def _header_signature(frame: CanonicalTabularFrame) -> tuple[str, ...]:
    normalized = [_normalize_header_name(name) for name in frame.column_names]
    return tuple(name for name in normalized if name)


def _page_numbers(frame: CanonicalTabularFrame) -> list[int]:
    pages = [int(ref.page_number) for ref in frame.lineage if ref.page_number is not None]
    return sorted(set(pages))


def _sample_rows(frame: CanonicalTabularFrame) -> list[list[str]]:
    metadata = frame.metadata if isinstance(frame.metadata, dict) else {}
    sample_rows = metadata.get("sample_rows") or []
    normalized: list[list[str]] = []
    for row in list(sample_rows):
        normalized.append([str(cell or "").strip() for cell in list(row or [])])
    return normalized


def _can_merge_fragment_pair(left: CanonicalTabularFrame, right: CanonicalTabularFrame) -> bool:
    left_kind = str((left.metadata or {}).get("source_kind") or "")
    right_kind = str((right.metadata or {}).get("source_kind") or "")
    if left_kind != "pdf_table" or right_kind != "pdf_table":
        return False

    left_signature = _header_signature(left)
    right_signature = _header_signature(right)
    if not left_signature or left_signature != right_signature:
        return False

    left_pages = _page_numbers(left)
    right_pages = _page_numbers(right)
    if not left_pages or not right_pages:
        return False

    return right_pages[0] == (left_pages[-1] + 1)


def _merge_frame_pair(
    left: CanonicalTabularFrame,
    right: CanonicalTabularFrame,
    *,
    merged_index: int,
) -> CanonicalTabularFrame:
    left_metadata = dict(left.metadata or {})
    right_metadata = dict(right.metadata or {})
    left_samples = _sample_rows(left)
    right_samples = _sample_rows(right)
    combined_samples = (left_samples + right_samples)[:5]

    merged_lineage: list[ArtifactLineageRef] = [*left.lineage, *right.lineage]
    merged_confidence = round((float(left.extraction_confidence) + float(right.extraction_confidence)) / 2.0, 4)

    return CanonicalTabularFrame(
        frame_id=f"pdf-merged-table-{merged_index}",
        label=left.label,
        row_count=int(left.row_count or 0) + int(right.row_count or 0),
        column_count=max(int(left.column_count or 0), int(right.column_count or 0)),
        column_names=list(left.column_names or right.column_names),
        extraction_confidence=merged_confidence,
        lineage=merged_lineage,
        metadata={
            **left_metadata,
            **right_metadata,
            "source_kind": "pdf_table",
            "fragment_consolidated": True,
            "fragment_count": int(left_metadata.get("fragment_count") or 1)
            + int(right_metadata.get("fragment_count") or 1),
            "consolidated_from": [
                *list(left_metadata.get("consolidated_from") or [left.frame_id]),
                *list(right_metadata.get("consolidated_from") or [right.frame_id]),
            ],
            "sample_rows": combined_samples,
        },
    )


def consolidate_fragmented_frames(frames: list[CanonicalTabularFrame]) -> list[CanonicalTabularFrame]:
    if len(frames) < 2:
        return list(frames)

    ordered = sorted(
        list(frames),
        key=lambda frame: (
            _page_numbers(frame)[0] if _page_numbers(frame) else 10**9,
            frame.label,
            frame.frame_id,
        ),
    )
    consolidated: list[CanonicalTabularFrame] = []
    pending: CanonicalTabularFrame | None = None
    merged_index = 1

    for frame in ordered:
        if pending is None:
            pending = frame
            continue
        if _can_merge_fragment_pair(pending, frame):
            pending = _merge_frame_pair(pending, frame, merged_index=merged_index)
            merged_index += 1
            continue
        consolidated.append(pending)
        pending = frame

    if pending is not None:
        consolidated.append(pending)
    return consolidated
