# En: backend/app/tasks/analysis_tasks.py
"""
Thin Celery task wrapper — delegates to analysis_pipeline modules.
All business logic has been extracted to backend/app/tasks/analysis_pipeline/.
"""

from app.celery_app import celery_app
from app.core.serializers import convert_keys_to_str
from app.core.structured_logging import emit_structured_log

from app.tasks.analysis_pipeline.orchestrator import (
    execute_legacy_task,
    execute_universal_tabular_task,
)
from app.tasks.analysis_pipeline.payload_shedder import (
    save_analysis_with_payload_shedding as _save_shed,
)

# ─────────────────────────────────────────────────────────
# CELERY TASKS (Thin wrappers — delegates to pipeline)
# ─────────────────────────────────────────────────────────

@celery_app.task(name="perform_analysis_task")
def perform_analysis_task(task_id, file_id, prompt, user_token, runtime_route=None):
    """Legacy Celery task wrapper — delegates to orchestrator."""
    return execute_legacy_task(task_id, file_id, prompt, user_token, runtime_route)


@celery_app.task(name="perform_analysis_task_universal_tabular")
def perform_analysis_task_universal_tabular(task_id, file_id, prompt, user_token, runtime_route=None):
    """Universal tabular Celery task — delegates to orchestrator."""
    return execute_universal_tabular_task(task_id, file_id, prompt, user_token, runtime_route)


@celery_app.task(name="observe_canonical_shadow_runtime")
def observe_canonical_shadow_runtime_task(task_id, file_id, prompt, live_summary):
    """Shadow runtime observer task."""
    from app.services.canonical_shadow_runtime_observer import observe_canonical_shadow_runtime
    try:
        observer_summary = observe_canonical_shadow_runtime(
            task_id=task_id,
            file_id=file_id,
            prompt=prompt,
            live_summary=convert_keys_to_str(live_summary),
        )
        return observer_summary.get("observer_status", "unknown")
    except Exception as shadow_error:
        emit_structured_log(
            "canonical_shadow_runtime_observer_error",
            level="warning",
            task_id=task_id,
            file_id=file_id,
            error=str(shadow_error)[:240],
        )
        return "error"


@celery_app.task(name="observe_canonical_tabular_canary_runtime")
def observe_canonical_tabular_canary_runtime_task(
    task_id,
    file_id,
    prompt,
    prompt_type=None,
    requested_visual_family=None,
):
    """Canary runtime observer Celery task."""
    from time import perf_counter
    from app.core.supabase_client import get_supabase_client
    from app.core.config import settings
    from app.services.enterprise_telemetry import track_canary_runtime_execution_observed
    from app.services.canonical_tabular_canary_executor import execute_canonical_tabular_canary_analysis

    sb = get_supabase_client()
    started_at = perf_counter()
    try:
        uploaded_file_row = (
            sb.table("uploaded_files")
            .select("id, user_id, team_id, file_name, storage_path, created_at")
            .eq("id", file_id)
            .single()
            .execute()
        )
        uploaded_row_data = dict(uploaded_file_row.data or {})
        canary_result = execute_canonical_tabular_canary_analysis(
            file_id=file_id,
            prompt=prompt,
            service_client=sb,
            uploaded_file_row=uploaded_row_data,
            prompt_type=prompt_type,
            requested_visual_family=requested_visual_family,
            max_plans=max(int(settings.CANONICAL_SHADOW_TRAFFIC_MIRROR_MAX_PLANS or 3), 1),
        )
        try:
            track_canary_runtime_execution_observed(
                task_id=task_id,
                file_id=file_id,
                user_id=str(uploaded_row_data.get("user_id") or ""),
                team_id=str(uploaded_row_data.get("team_id") or ""),
                file_name=str(uploaded_row_data.get("file_name") or ""),
                prompt_type=prompt_type,
                execution_status=canary_result.status,
                candidate_id=canary_result.execution.metadata.get("candidate_id"),
                prompt_strategy=canary_result.execution.prompt_strategy,
                chart_count=len(list(canary_result.final_struct.get("chart_options") or [])),
                duration_ms=int((perf_counter() - started_at) * 1000),
            )
        except Exception as metric_error:
            emit_structured_log(
                "canonical_tabular_background_canary_metric_error",
                level="warning",
                task_id=task_id,
                file_id=file_id,
                error=str(metric_error)[:240],
            )
        emit_structured_log(
            "canonical_tabular_background_canary_completed",
            task_id=task_id,
            file_id=file_id,
            candidate_id=canary_result.execution.metadata.get("candidate_id"),
            prompt_strategy=canary_result.execution.prompt_strategy,
            chart_count=len(list(canary_result.final_struct.get("chart_options") or [])),
            duration_ms=int((perf_counter() - started_at) * 1000),
        )
        return canary_result.status
    except Exception as canary_error:
        emit_structured_log(
            "canonical_tabular_background_canary_error",
            level="warning",
            task_id=task_id,
            file_id=file_id,
            error=str(canary_error)[:240],
            duration_ms=int((perf_counter() - started_at) * 1000),
        )
        return "error"


# ─────────────────────────────────────────────────────────
# PAYLOAD SHEDDING (delegates to pipeline module)
# ─────────────────────────────────────────────────────────

def _save_analysis_task_result_with_payload_shedding(sb, task_id, runtime_result):
    """Save analysis result with payload shedding — delegates to pipeline module."""
    return _save_shed(sb, task_id, runtime_result)

# Legacy `generar_analisis` has been moved to app.tasks.analysis_pipeline.legacy_codegen
