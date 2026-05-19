from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.canonical_artifacts import CanonicalArtifactBundle, CanonicalMaterializedBundle
from app.core.config import settings
from app.core.structured_logging import emit_structured_log
from app.services.artifact_extraction_service import (
    build_canonical_bundle_for_uploaded_file,
    summarize_canonical_bundle,
)
from app.services.canonical_analytical_contract_adapter import (
    CanonicalAnalyticalAdapterRuntime,
    build_canonical_analytical_adapter_runtime,
    get_selected_candidate_dataframe,
    summarize_canonical_analytical_adapter_runtime,
)
from app.services.canonical_bundle_materializer import materialize_bundle
from app.services.canonical_ibis_preview_runtime import (
    CanonicalIbisPreviewRuntime,
    build_canonical_ibis_preview_runtime,
    describe_canonical_ibis_preview_runtime,
    summarize_materialized_bundle_status,
)
from app.services.canonical_runtime_comparator import compare_selected_candidate_against_active_runtime


def is_canonical_dark_runtime_orchestrator_enabled() -> bool:
    return settings.CANONICAL_DARK_RUNTIME_ORCHESTRATOR_ENABLED


@dataclass
class CanonicalDarkRuntimePipelineResult:
    canonical_bundle: CanonicalArtifactBundle
    canonical_bundle_summary: dict[str, Any]
    materialized_bundle: CanonicalMaterializedBundle
    materialized_bundle_summary: dict[str, Any]
    preview_runtime: CanonicalIbisPreviewRuntime
    preview_runtime_summary: dict[str, Any]
    analytical_adapter_runtime: CanonicalAnalyticalAdapterRuntime
    analytical_adapter_summary: dict[str, Any]
    runtime_comparison_summary: dict[str, Any]
    metadata: dict[str, Any]


def _pipeline_status(
    *,
    bundle_summary: dict[str, Any],
    analytical_summary: dict[str, Any],
    comparison_summary: dict[str, Any],
) -> str:
    if analytical_summary.get("selected_candidate_id"):
        if comparison_summary.get("comparison_grade") in {"high_alignment", "partial_alignment", "no_active_runtime"}:
            return "ready_for_shadow_compare"
        return "shadow_compare_low_alignment"
    if bundle_summary.get("tabular_frame_count"):
        return "tabular_detected_without_candidate"
    if bundle_summary.get("text_block_count") or bundle_summary.get("layout_block_count"):
        return "document_only"
    return "empty"


def run_canonical_dark_pipeline_for_uploaded_file(
    *,
    file_id: str,
    service_client: Any,
    uploaded_file_row: dict[str, Any] | None = None,
    mime_type: str | None = None,
) -> CanonicalDarkRuntimePipelineResult:
    canonical_bundle = build_canonical_bundle_for_uploaded_file(
        file_id=file_id,
        service_client=service_client,
        uploaded_file_row=uploaded_file_row,
        mime_type=mime_type,
    )
    canonical_bundle_summary = summarize_canonical_bundle(canonical_bundle)

    materialized_bundle = materialize_bundle(
        canonical_bundle,
        primary_frame_id=canonical_bundle.metadata.get("primary_frame_id"),
    )
    materialized_bundle_summary = summarize_materialized_bundle_status(materialized_bundle)

    preview_runtime = build_canonical_ibis_preview_runtime(materialized_bundle)
    preview_runtime_summary = describe_canonical_ibis_preview_runtime(preview_runtime)

    analytical_adapter_runtime = build_canonical_analytical_adapter_runtime(preview_runtime)
    analytical_adapter_summary = summarize_canonical_analytical_adapter_runtime(analytical_adapter_runtime)

    selected_candidate_df = get_selected_candidate_dataframe(analytical_adapter_runtime)
    runtime_comparison_summary = compare_selected_candidate_against_active_runtime(
        file_id=file_id,
        candidate_df=selected_candidate_df,
    )

    pipeline_status = _pipeline_status(
        bundle_summary=canonical_bundle_summary,
        analytical_summary=analytical_adapter_summary,
        comparison_summary=runtime_comparison_summary,
    )

    emit_structured_log(
        "canonical_dark_runtime_pipeline_built",
        file_id=file_id,
        pipeline_status=pipeline_status,
        support_level=canonical_bundle_summary.get("support_level"),
        preferred_mode=canonical_bundle_summary.get("preferred_mode"),
        selected_candidate_id=analytical_adapter_summary.get("selected_candidate_id"),
        candidate_count=analytical_adapter_summary.get("candidate_count"),
        comparison_grade=runtime_comparison_summary.get("comparison_grade"),
        preview_backend=preview_runtime_summary.get("preview_backend"),
    )

    return CanonicalDarkRuntimePipelineResult(
        canonical_bundle=canonical_bundle,
        canonical_bundle_summary=canonical_bundle_summary,
        materialized_bundle=materialized_bundle,
        materialized_bundle_summary=materialized_bundle_summary,
        preview_runtime=preview_runtime,
        preview_runtime_summary=preview_runtime_summary,
        analytical_adapter_runtime=analytical_adapter_runtime,
        analytical_adapter_summary=analytical_adapter_summary,
        runtime_comparison_summary=runtime_comparison_summary,
        metadata={
            "file_id": file_id,
            "pipeline_status": pipeline_status,
        },
    )


def summarize_canonical_dark_pipeline_result(
    result: CanonicalDarkRuntimePipelineResult,
) -> dict[str, Any]:
    return {
        "file_id": result.metadata.get("file_id"),
        "pipeline_status": result.metadata.get("pipeline_status"),
        "support_level": result.canonical_bundle_summary.get("support_level"),
        "preferred_mode": result.canonical_bundle_summary.get("preferred_mode"),
        "tabular_frame_count": result.canonical_bundle_summary.get("tabular_frame_count"),
        "text_block_count": result.canonical_bundle_summary.get("text_block_count"),
        "layout_block_count": result.canonical_bundle_summary.get("layout_block_count"),
        "selected_candidate_id": result.analytical_adapter_summary.get("selected_candidate_id"),
        "candidate_count": result.analytical_adapter_summary.get("candidate_count"),
        "preview_backend": result.preview_runtime_summary.get("preview_backend"),
        "comparison_grade": result.runtime_comparison_summary.get("comparison_grade"),
        "active_runtime_available": result.runtime_comparison_summary.get("active_runtime_available"),
    }
