from __future__ import annotations

from typing import Any

from app.core.canonical_artifacts import CanonicalArtifactBundle, CanonicalFrameRelation
from app.core.config import settings
from app.core.structured_logging import emit_structured_log
from app.services.canonical_table_selector import rank_canonical_frames, select_primary_frame
from app.services.canonical_bundle_orchestrator import summarize_frame_graph
from app.services.canonical_table_quality_gate import apply_canonical_document_table_quality_gate
from app.services.artifact_parser_registry import ArtifactParserRegistry
from app.services.cloud_imports import DASH_UPLOADS_BUCKET


def is_canonical_extraction_pipeline_enabled() -> bool:
    return settings.CANONICAL_EXTRACTION_PIPELINE_ENABLED


def _get_uploaded_file_row(
    *,
    file_id: str,
    service_client: Any,
) -> dict[str, Any]:
    response = service_client.table("uploaded_files") \
        .select("id, user_id, team_id, file_name, storage_path, created_at") \
        .eq("id", file_id) \
        .limit(1) \
        .execute()
    if not response.data:
        raise ValueError("No se encontró el archivo solicitado para extracción canónica.")
    return response.data[0]


def _download_uploaded_file_bytes(
    *,
    storage_path: str,
    service_client: Any,
) -> bytes:
    normalized_path = str(storage_path or "").strip()
    if not normalized_path:
        raise ValueError("El archivo no tiene storage_path válido para extracción canónica.")
    return service_client.storage.from_(DASH_UPLOADS_BUCKET).download(normalized_path)


def summarize_canonical_bundle(bundle: CanonicalArtifactBundle) -> dict[str, Any]:
    manifest = bundle.source_manifest
    return {
        "file_name": manifest.file_name,
        "source_kind": manifest.source_kind.value,
        "support_level": manifest.support_level.value,
        "preferred_mode": manifest.preferred_mode.value,
        "availability_status": manifest.availability_status.value,
        "analytics_ready": manifest.analytics_ready,
        "requires_ocr": manifest.requires_ocr,
        "requires_conversion": manifest.requires_conversion,
        "tabular_frame_count": len(bundle.tabular_frames),
        "text_block_count": len(bundle.text_blocks),
        "layout_block_count": len(bundle.layout_blocks),
        "extraction_confidence": bundle.extraction_confidence,
        "warnings": list(manifest.warnings),
        "parser_name": bundle.metadata.get("parser_name"),
        "primary_frame_id": bundle.metadata.get("primary_frame_id"),
        "ranked_frame_count": len(bundle.metadata.get("ranked_frames") or []),
        "relation_count": len(bundle.frame_relations),
        "related_frame_count": len(bundle.metadata.get("related_frame_ids") or []),
        "analytics_ready_frame_count": len(bundle.metadata.get("analytics_ready_frame_ids") or []),
        "quality_gate_applied": bool(bundle.metadata.get("quality_gate_applied")),
    }


def build_canonical_bundle_for_uploaded_file(
    *,
    file_id: str,
    service_client: Any,
    uploaded_file_row: dict[str, Any] | None = None,
    mime_type: str | None = None,
) -> CanonicalArtifactBundle:
    uploaded_row = uploaded_file_row or _get_uploaded_file_row(
        file_id=file_id,
        service_client=service_client,
    )
    file_name = str(uploaded_row.get("file_name") or "archivo")
    storage_path = str(uploaded_row.get("storage_path") or "")
    file_bytes = _download_uploaded_file_bytes(
        storage_path=storage_path,
        service_client=service_client,
    )
    bundle = ArtifactParserRegistry.parse_to_bundle(
        file_name=file_name,
        file_bytes=file_bytes,
        mime_type=mime_type,
        metadata={
            "file_id": str(uploaded_row.get("id") or file_id),
            "user_id": str(uploaded_row.get("user_id") or ""),
            "team_id": str(uploaded_row.get("team_id") or ""),
            "storage_path": storage_path,
            "created_at": uploaded_row.get("created_at"),
        },
    )
    bundle = apply_canonical_document_table_quality_gate(bundle)
    ranked_frames = rank_canonical_frames(bundle)
    primary_frame = select_primary_frame(bundle)
    bundle.metadata["ranked_frames"] = ranked_frames
    bundle.metadata["primary_frame_id"] = primary_frame.frame_id if primary_frame else None
    if primary_frame:
        bundle.metadata["primary_frame_label"] = primary_frame.label
    graph_summary = summarize_frame_graph(
        bundle,
        primary_frame_id=primary_frame.frame_id if primary_frame else None,
    )
    bundle.frame_relations = [
        CanonicalFrameRelation(**row) for row in graph_summary["relations"]
    ]
    bundle.metadata["related_frame_ids"] = graph_summary["related_frame_ids"]
    bundle.metadata["dominant_relation_type"] = graph_summary["dominant_relation_type"]
    summary = summarize_canonical_bundle(bundle)
    emit_structured_log(
        "canonical_artifact_bundle_built",
        file_id=str(uploaded_row.get("id") or file_id),
        file_name=file_name,
        support_level=summary["support_level"],
        preferred_mode=summary["preferred_mode"],
        availability_status=summary["availability_status"],
        analytics_ready=summary["analytics_ready"],
        tabular_frame_count=summary["tabular_frame_count"],
        text_block_count=summary["text_block_count"],
        layout_block_count=summary["layout_block_count"],
        extraction_confidence=summary["extraction_confidence"],
        parser_name=summary["parser_name"],
        primary_frame_id=summary["primary_frame_id"],
        ranked_frame_count=summary["ranked_frame_count"],
        relation_count=summary["relation_count"],
        related_frame_count=summary["related_frame_count"],
        analytics_ready_frame_count=summary["analytics_ready_frame_count"],
        quality_gate_applied=summary["quality_gate_applied"],
    )
    return bundle
