# En: backend/app/tasks/analysis_pipeline/orchestrator.py
"""
Orquestador del pipeline de análisis — legacy + universal tabular.

Responsabilidad: SOLO coordinación de flujo. Toda la lógica de negocio
vive en los módulos del pipeline (data_loader, memory_router, plan_generator,
plan_executor, chart_generator, narrative_generator, response_builder,
payload_shedder).
"""
import json
import traceback
from time import perf_counter
from typing import Any

from celery.exceptions import SoftTimeLimitExceeded

from app.core.circuit_breaker import GeminiCircuitOpenError
from app.core.config import settings
from app.core.supabase_client import get_supabase_client
from app.core.structured_logging import emit_structured_log
from app.core.serializers import CustomEncoder, convert_keys_to_str
from app.core.redis_client import publish_task_progress

from app.services.enterprise_telemetry import (
    track_analysis_completed,
    track_analysis_stage_latency_batch,
    track_canary_runtime_execution_fallback,
    track_canary_runtime_execution_observed,
    track_canary_runtime_route_fallback,
    track_canary_runtime_route_observed,
)
from app.services.canonical_tabular_canary_executor import (
    execute_canonical_tabular_canary_analysis,
)
from app.services.canonical_tabular_production_executor import (
    execute_canonical_tabular_production_analysis,
)
from app.services.canonical_shadow_runtime_observer import (
    _classify_prompt_type as _shadow_observer_classify_prompt_type,
    _normalize_prompt as _shadow_observer_normalize_prompt,
    build_live_runtime_summary,
)
from app.services.visual_recommendation_engine import (
    extract_prompt_visual_requests,
    normalize_visual_id,
)
from app.services.file_cache import get_cached_analysis, set_cached_analysis
from app.services.analysis_memory_context import unwrap_prompt_payload

from app.tasks.analysis_pipeline.data_loader import (
    load_dataset_for_task, _compute_queue_wait_ms,
)
from app.tasks.analysis_pipeline.plan_executor import execute_plans
from app.tasks.analysis_pipeline.response_builder import build_final_response_struct
from app.tasks.analysis_pipeline.payload_shedder import save_analysis_with_payload_shedding


def _log_runtime_route(task_id: str, file_id: str, runtime_route: str | None, actual_prompt: str) -> None:
    emit_structured_log("analysis_pipeline_orchestrator", "task_routing", {
        "task_id": task_id,
        "file_id": file_id,
        "runtime_route": runtime_route,
        "prompt_length": len(actual_prompt),
    })


def _extract_cached_result_payload(cached_payload: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    if not isinstance(cached_payload, dict):
        return None
    status = str(cached_payload.get("status") or "completed")
    result_payload = (
        cached_payload.get("result")
        or cached_payload.get("results_json")
        or cached_payload.get("final_struct")
    )
    if not isinstance(result_payload, dict):
        return None
    return status, result_payload


def _safe_json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _normalize_cache_compare_text(value: str | None) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _load_cache_parent_context(
    *,
    sb: Any,
    file_id: str,
    prompt: str,
    runtime: str,
) -> tuple[dict[str, Any] | None, bool]:
    actual_prompt, parent_task_id = unwrap_prompt_payload(prompt)
    if not parent_task_id:
        return None, False
    try:
        response = (
            sb.table("analysis_tasks")
            .select("id,file_id,prompt,results_json")
            .eq("id", parent_task_id)
            .single()
            .execute()
        )
    except Exception as cache_parent_error:
        emit_structured_log(
            "analysis_file_cache_parent_context_error",
            level="warning",
            file_id=file_id,
            runtime=runtime,
            error=str(cache_parent_error)[:240],
        )
        return None, False
    parent_row = dict(getattr(response, "data", None) or {})
    if not parent_row or str(parent_row.get("file_id") or "") != str(file_id):
        return None, False
    parent_prompt, _ = unwrap_prompt_payload(str(parent_row.get("prompt") or ""))
    exact_prompt_repeat = (
        _normalize_cache_compare_text(actual_prompt)
        == _normalize_cache_compare_text(parent_prompt)
    )
    return (
        {
            "parent_prompt": parent_prompt or "",
            "result_payload": _safe_json_dict(parent_row.get("results_json")),
        },
        exact_prompt_repeat,
    )


def _try_restore_cached_analysis(
    *,
    sb: Any,
    task_id: str,
    file_id: str,
    prompt: str,
    runtime: str,
    parent_context: dict[str, Any] | None = None,
    allow_unscoped_fallback: bool = False,
) -> str | None:
    try:
        cached_payload = get_cached_analysis(
            file_id,
            prompt,
            parent_context=parent_context,
            allow_unscoped_fallback=allow_unscoped_fallback,
        )
        parsed = _extract_cached_result_payload(cached_payload or {})
        if not parsed:
            emit_structured_log(
                "analysis_file_cache_miss",
                task_id=task_id,
                file_id=file_id,
                runtime=runtime,
            )
            return None
        status, result_payload = parsed
        sb.table("analysis_tasks").update({
            "status": status,
            "results_json": json.dumps(result_payload, cls=CustomEncoder),
        }).eq("id", task_id).execute()
        emit_structured_log(
            "analysis_file_cache_restored",
            task_id=task_id,
            file_id=file_id,
            runtime=runtime,
            status=status,
        )
        return status
    except Exception as cache_error:
        emit_structured_log(
            "analysis_file_cache_restore_error",
            level="warning",
            task_id=task_id,
            file_id=file_id,
            runtime=runtime,
            error=str(cache_error)[:240],
        )
        return None


def _try_store_completed_analysis_cache(
    *,
    file_id: str,
    prompt: str,
    status: str,
    result_payload: dict[str, Any],
    runtime: str,
    parent_context: dict[str, Any] | None = None,
    write_unscoped_alias: bool = False,
) -> None:
    if status != "completed" or not isinstance(result_payload, dict):
        return
    try:
        set_cached_analysis(
            file_id,
            prompt,
            {
                "status": status,
                "result": result_payload,
            },
            parent_context=parent_context,
            write_unscoped_alias=write_unscoped_alias,
        )
        emit_structured_log(
            "analysis_file_cache_stored",
            file_id=file_id,
            runtime=runtime,
            status=status,
        )
    except Exception as cache_error:
        emit_structured_log(
            "analysis_file_cache_store_error",
            level="warning",
            file_id=file_id,
            runtime=runtime,
            error=str(cache_error)[:240],
        )


def _handle_canary_route_telemetry(
    task_id: str, file_id: str, runtime_route: dict, actual_prompt: str,
) -> None:
    """Emit telemetry for canary routing decisions. Best-effort, never raises."""
    try:
        runtime_prompt_type = _shadow_observer_classify_prompt_type(
            _shadow_observer_normalize_prompt(actual_prompt), {},
        )
        emit_structured_log(
            "analysis_runtime_route_received",
            task_id=task_id, file_id=file_id,
            requested_runtime=runtime_route.get("requested_runtime"),
            effective_runtime=runtime_route.get("effective_runtime"),
            decision_mode=runtime_route.get("decision_mode"),
            decision_reason=runtime_route.get("decision_reason"),
            health_status=runtime_route.get("health_status"),
        )
        try:
            track_canary_runtime_route_observed(
                task_id=task_id, file_id=file_id,
                user_id=runtime_route.get("user_id"),
                team_id=runtime_route.get("team_id"),
                file_name=runtime_route.get("file_name"),
                prompt_type=runtime_prompt_type,
                requested_runtime=runtime_route.get("requested_runtime"),
                effective_runtime=runtime_route.get("effective_runtime"),
                decision_mode=runtime_route.get("decision_mode"),
                decision_reason=runtime_route.get("decision_reason"),
                health_status=runtime_route.get("health_status"),
                eligible=bool(runtime_route.get("eligible")),
                bucket_value=runtime_route.get("bucket_value"),
                traffic_percent=runtime_route.get("traffic_percent"),
                allowlist_match=runtime_route.get("allowlist_match"),
                health_ready_for_functional_canary=bool(
                    runtime_route.get("health_ready_for_functional_canary")
                ),
            )
            if runtime_route.get("requested_runtime") != runtime_route.get("effective_runtime"):
                track_canary_runtime_route_fallback(
                    task_id=task_id, file_id=file_id,
                    user_id=runtime_route.get("user_id"),
                    team_id=runtime_route.get("team_id"),
                    file_name=runtime_route.get("file_name"),
                    prompt_type=runtime_prompt_type,
                    requested_runtime=runtime_route.get("requested_runtime"),
                    fallback_runtime=runtime_route.get("effective_runtime"),
                    decision_reason=runtime_route.get("decision_reason"),
                )
        except Exception as canary_telemetry_error:
            emit_structured_log(
                "canonical_canary_telemetry_error", level="warning",
                task_id=task_id, file_id=file_id,
                error=str(canary_telemetry_error)[:240],
            )
        if runtime_route.get("requested_runtime") != runtime_route.get("effective_runtime"):
            emit_structured_log(
                "analysis_runtime_route_fallback",
                task_id=task_id, file_id=file_id,
                requested_runtime=runtime_route.get("requested_runtime"),
                fallback_runtime=runtime_route.get("effective_runtime"),
                decision_reason=runtime_route.get("decision_reason"),
            )
    except Exception:
        pass


def execute_legacy_task(task_id: str, file_id: str, prompt: str, user_token: str, runtime_route: Any = None) -> Any:
    """
    Execute an analysis using the legacy pipeline.
    Delegates to the original generar_analisis in analysis_tasks.py.
    """
    from app.tasks.analysis_pipeline.legacy_codegen import run_legacy_analysis_pipeline

    supabase = get_supabase_client()
    emit_structured_log("analysis_legacy_pipeline", "task_started", {
        "task_id": task_id, "file_id": file_id,
    })
    task_started_at = perf_counter()
    runtime_route = convert_keys_to_str(runtime_route or {})

    # ── Initialize safe variables ──
    code_dna = None
    parent_analysis_summary = None
    parent_task_id = None
    memory_router_decision = "fresh"
    traceability_plan_entries: list[dict[str, Any]] = []
    schema_profile: dict[str, Any] = {}
    currency_meta: dict[str, Any] = {}
    dataset_contract: dict[str, Any] = {}
    cleaning_notes: Any = []
    institutional_snippets: list[Any] = []
    final_error_message: str | None = None
    final_struct: dict[str, Any] | None = None
    user_id = None
    actual_prompt = prompt
    format_override = {"enabled": False}
    explicit_visual_requests: list[str] = []
    visual_probe_mode = False
    main_df = None
    parent_structured_context: dict[str, Any] | None = None
    response: Any = None
    status = "failed"
    plans_result: list = []

    try:
        sb = supabase
        sb.table('analysis_tasks').update({'status': 'processing'}).eq('id', task_id).execute()

        if runtime_route:
            _handle_canary_route_telemetry(task_id, file_id, runtime_route, actual_prompt)

        task_data_resp = sb.table('analysis_tasks').select('user_id').eq('id', task_id).single().execute()
        user_id = task_data_resp.data.get('user_id') if task_data_resp.data else None

        parent_cache_context, allow_unscoped_cache_fallback = _load_cache_parent_context(
            sb=sb,
            file_id=file_id,
            prompt=prompt,
            runtime="legacy",
        )
        cached_status = _try_restore_cached_analysis(
            sb=sb,
            task_id=task_id,
            file_id=file_id,
            prompt=prompt,
            runtime="legacy",
            parent_context=parent_cache_context,
            allow_unscoped_fallback=allow_unscoped_cache_fallback,
        )
        if cached_status:
            return cached_status

        # ── Delegate to legacy analysis pipeline ──
        result = run_legacy_analysis_pipeline(
            sb=sb,
            task_id=task_id,
            file_id=file_id,
            prompt=prompt,
            user_token=user_token,
            user_id=user_id,
        )

        response = result["response"]
        status = result["status"]
        actual_prompt = result["actual_prompt"]
        parent_task_id = result.get("parent_task_id")
        memory_router_decision = result.get("memory_router_decision", "fresh")
        format_override = result.get("format_override", {"enabled": False})
        schema_profile = result.get("schema_profile", {})
        currency_meta = result.get("currency_meta", {})
        institutional_snippets = result.get("institutional_snippets", [])
        traceability_plan_entries = result.get("traceability_plan_entries", [])
        plans_result = result.get("plans_result", [])
        main_df = result.get("main_df")
        dataset_contract = result.get("dataset_contract", {})
        cleaning_notes = result.get("cleaning_notes", [])
        parquet_path = result.get("parquet_path", "")

    except SoftTimeLimitExceeded as e:
        final_error_message = str(e)
        emit_structured_log(
            "task_timeout_soft",
            level="warning",
            task_id=task_id,
            file_id=file_id,
        )
        response = [{"type": "error", "content": "Tu análisis fue demasiado complejo. Intenta con un filtro más específico."}]
        status = 'timeout'
        parquet_path = ""
    except GeminiCircuitOpenError as e:
        final_error_message = str(e)
        emit_structured_log(
            "gemini_circuit_open_task_rate_limited",
            level="warning",
            task_id=task_id,
            file_id=file_id,
            runtime="legacy",
            recovery_seconds=e.recovery_seconds,
        )
        response = [{
            "type": "error",
            "content": (
                "## ⚠️ Servicio de IA saturado\n\n"
                "El servicio de análisis está temporalmente saturado. "
                "Intenta nuevamente en unos segundos."
            ),
        }]
        status = 'rate_limited'
        parquet_path = ""
    except Exception as e:
        final_error_message = str(e)
        response = [{"type": "error", "content": f"Error del sistema: {str(e)}"}]
        status = 'failed'
        parquet_path = ""

    final_status, json_output, final_struct = build_final_response_struct(
        response=response,
        parquet_path=parquet_path if 'parquet_path' in locals() else "",
        main_df=main_df,
        file_id=file_id,
        task_id=task_id,
        user_id=user_id,
        prompt=prompt,
        actual_prompt=actual_prompt,
        parent_task_id=parent_task_id,
        memory_router_decision=memory_router_decision,
        format_override=format_override,
        schema_profile=schema_profile,
        currency_meta=currency_meta,
        institutional_snippets=institutional_snippets,
        traceability_plan_entries=traceability_plan_entries,
        plans_result=plans_result,
        status=status,
        final_error_message=final_error_message,
    )
    status = final_status
    _try_store_completed_analysis_cache(
        file_id=file_id,
        prompt=prompt,
        status=status,
        result_payload=final_struct or {},
        runtime="legacy",
        parent_context=parent_cache_context if 'parent_cache_context' in locals() else None,
        write_unscoped_alias=allow_unscoped_cache_fallback if 'allow_unscoped_cache_fallback' in locals() else False,
    )

    live_duration_ms = int((perf_counter() - task_started_at) * 1000)
    sb.table('analysis_tasks').update({'status': status, 'results_json': json_output}).eq('id', task_id).execute()
    try:
        track_analysis_completed(
            task_id=task_id, file_id=file_id, user_id=user_id,
            status=status, duration_ms=live_duration_ms,
            final_struct=final_struct,
            dataset_contract=dataset_contract,
            cleaning_notes=cleaning_notes,
        )
    except Exception as telemetry_error:
        emit_structured_log(
            "enterprise_telemetry_error", level="error",
            task_id=task_id, file_id=file_id, status=status,
            error=str(telemetry_error)[:240],
        )
    if settings.CANONICAL_SHADOW_TRAFFIC_MIRROR_ENABLED and status == "completed":
        try:
            live_summary = build_live_runtime_summary(
                status=status, prompt=actual_prompt,
                final_struct=final_struct,
                dataset_contract=dataset_contract,
                live_duration_ms=live_duration_ms,
            )
            from app.tasks.analysis_tasks import observe_canonical_shadow_runtime_task
            observe_canonical_shadow_runtime_task.delay(
                task_id=task_id, file_id=file_id,
                prompt=actual_prompt,
                live_summary=convert_keys_to_str(live_summary),
            )
        except Exception as shadow_observer_error:
            emit_structured_log(
                "canonical_shadow_runtime_observer_dispatch_error",
                level="warning", task_id=task_id, file_id=file_id,
                status=status, error=str(shadow_observer_error)[:240],
            )
    if status == "completed":
        publish_task_progress(task_id, {"status": "completed"})
    return status


def execute_universal_tabular_task(task_id: str, file_id: str, prompt: str, user_token: str, runtime_route: Any = None) -> Any:
    """Execute an analysis using the universal tabular (new) pipeline."""
    supabase = get_supabase_client()
    task_started_at = perf_counter()
    runtime_route = convert_keys_to_str(runtime_route or {})
    prompt_type = _shadow_observer_classify_prompt_type(
        _shadow_observer_normalize_prompt(prompt), {},
    )
    prompt_visual_requests = extract_prompt_visual_requests(prompt)
    requested_visual_family = normalize_visual_id(prompt_visual_requests[0]) if prompt_visual_requests else None
    production_executor_enabled = bool(settings.UNIVERSAL_TABULAR_PRODUCTION_EXECUTOR_ENABLED)
    runtime_label = "universal_tabular_production" if production_executor_enabled else "universal_tabular"
    user_id_for_metrics = ""
    stage_latency_buffer: list[dict[str, Any]] = []

    def _record_stage_latency(
        stage_name: str,
        *,
        started_at: float | None = None,
        duration_ms: int | None = None,
        status: str = "processing",
    ) -> None:
        measured_duration = duration_ms if duration_ms is not None else int((perf_counter() - float(started_at)) * 1000)
        stage_latency_buffer.append({
            "stage_name": stage_name,
            "duration_ms": max(int(measured_duration), 0),
            "status": status,
        })

    def _flush_stage_latency_buffer() -> None:
        if not stage_latency_buffer:
            return
        try:
            track_analysis_stage_latency_batch(
                task_id=task_id, file_id=file_id,
                user_id=user_id_for_metrics or None,
                runtime=runtime_label, prompt_type=prompt_type,
                stage_metrics=list(stage_latency_buffer),
            )
        except Exception as stage_metric_error:
            emit_structured_log(
                "analysis_stage_latency_batch_track_error",
                level="warning", task_id=task_id, file_id=file_id,
                stage_count=len(stage_latency_buffer),
                error=str(stage_metric_error)[:240],
            )
        finally:
            stage_latency_buffer.clear()

    try:
        sb = supabase
        task_metadata_started_at = perf_counter()
        task_metadata_row = (
            sb.table("analysis_tasks")
            .select("created_at, user_id")
            .eq("id", task_id)
            .single()
            .execute()
        )
        task_metadata = dict(task_metadata_row.data or {})
        user_id_for_metrics = str(task_metadata.get("user_id") or "")
        queue_wait_ms = _compute_queue_wait_ms(task_metadata.get("created_at"))
        if queue_wait_ms is not None:
            _record_stage_latency("queue_wait", duration_ms=queue_wait_ms, status="processing")
        _record_stage_latency("task_metadata_lookup", started_at=task_metadata_started_at)

        mark_processing_started_at = perf_counter()
        sb.table('analysis_tasks').update({'status': 'processing'}).eq('id', task_id).execute()
        _record_stage_latency("mark_processing_status", started_at=mark_processing_started_at)

        uploaded_file_lookup_started_at = perf_counter()
        uploaded_file_row = (
            sb.table("uploaded_files")
            .select("id, user_id, team_id, file_name, storage_path, created_at")
            .eq("id", file_id)
            .single()
            .execute()
        )
        uploaded_row_data = dict(uploaded_file_row.data or {})
        if not user_id_for_metrics:
            user_id_for_metrics = str(uploaded_row_data.get("user_id") or "")
        _record_stage_latency("uploaded_file_lookup", started_at=uploaded_file_lookup_started_at)

        file_cache_started_at = perf_counter()
        parent_cache_context, allow_unscoped_cache_fallback = _load_cache_parent_context(
            sb=sb,
            file_id=file_id,
            prompt=prompt,
            runtime=runtime_label,
        )
        cached_status = _try_restore_cached_analysis(
            sb=sb,
            task_id=task_id,
            file_id=file_id,
            prompt=prompt,
            runtime=runtime_label,
            parent_context=parent_cache_context,
            allow_unscoped_fallback=allow_unscoped_cache_fallback,
        )
        if cached_status:
            _record_stage_latency("file_cache_lookup", started_at=file_cache_started_at, status="completed")
            _record_stage_latency("worker_task_total", started_at=task_started_at, status=cached_status)
            _flush_stage_latency_buffer()
            return cached_status
        _record_stage_latency("file_cache_lookup", started_at=file_cache_started_at, status="miss")

        analysis_execution_started_at = perf_counter()
        if production_executor_enabled:
            canary_result = execute_canonical_tabular_production_analysis(
                file_id=file_id, prompt=prompt, service_client=sb,
                uploaded_file_row=uploaded_row_data,
                max_plans=max(int(settings.CANONICAL_SHADOW_TRAFFIC_MIRROR_MAX_PLANS or 3), 1),
            )
            if settings.CANONICAL_SHADOW_TRAFFIC_MIRROR_ENABLED:
                from app.tasks.analysis_tasks import observe_canonical_tabular_canary_runtime_task
                observe_canonical_tabular_canary_runtime_task.delay(
                    task_id, file_id, prompt, prompt_type, requested_visual_family,
                )
        else:
            canary_result = execute_canonical_tabular_canary_analysis(
                file_id=file_id, prompt=prompt, service_client=sb,
                uploaded_file_row=uploaded_row_data,
                prompt_type=prompt_type,
                requested_visual_family=requested_visual_family,
                max_plans=max(int(settings.CANONICAL_SHADOW_TRAFFIC_MIRROR_MAX_PLANS or 3), 1),
            )
        _record_stage_latency("analysis_execution", started_at=analysis_execution_started_at)

        persist_result_started_at = perf_counter()
        save_analysis_with_payload_shedding(sb, task_id, canary_result)
        _try_store_completed_analysis_cache(
            file_id=file_id,
            prompt=prompt,
            status=canary_result.status,
            result_payload=canary_result.final_struct,
            runtime=runtime_label,
            parent_context=parent_cache_context,
            write_unscoped_alias=allow_unscoped_cache_fallback,
        )
        _record_stage_latency(
            "persist_analysis_result",
            started_at=persist_result_started_at,
            status=canary_result.status,
        )

        telemetry_started_at = perf_counter()
        try:
            track_analysis_completed(
                task_id=task_id, file_id=file_id,
                user_id=user_id_for_metrics,
                status=canary_result.status,
                duration_ms=int((perf_counter() - task_started_at) * 1000),
                final_struct=canary_result.final_struct,
                dataset_contract=canary_result.dataset_contract,
                cleaning_notes=canary_result.cleaning_notes,
            )
        except Exception as canary_telemetry_error:
            emit_structured_log(
                "canonical_tabular_canary_track_analysis_completed_error",
                level="warning", task_id=task_id, file_id=file_id,
                error=str(canary_telemetry_error)[:240],
            )
        if not production_executor_enabled:
            try:
                track_canary_runtime_execution_observed(
                    task_id=task_id, file_id=file_id,
                    user_id=str((uploaded_file_row.data or {}).get("user_id") or ""),
                    team_id=str((uploaded_file_row.data or {}).get("team_id") or ""),
                    file_name=str((uploaded_file_row.data or {}).get("file_name") or ""),
                    prompt_type=prompt_type,
                    execution_status=canary_result.status,
                    candidate_id=canary_result.execution.metadata.get("candidate_id"),
                    prompt_strategy=canary_result.execution.prompt_strategy,
                    chart_count=len(list(canary_result.final_struct.get("chart_options") or [])),
                    duration_ms=int((perf_counter() - task_started_at) * 1000),
                )
            except Exception as canary_execution_metric_error:
                emit_structured_log(
                    "canonical_tabular_canary_execution_metric_error",
                    level="warning", task_id=task_id, file_id=file_id,
                    error=str(canary_execution_metric_error)[:240],
                )
        else:
            emit_structured_log(
                "canonical_tabular_production_execution_observed",
                task_id=task_id, file_id=file_id,
                candidate_id=canary_result.execution.metadata.get("candidate_id"),
                prompt_strategy=canary_result.execution.prompt_strategy,
                chart_count=len(list(canary_result.final_struct.get("chart_options") or [])),
                duration_ms=int((perf_counter() - task_started_at) * 1000),
            )
        _record_stage_latency("emit_telemetry", started_at=telemetry_started_at, status=canary_result.status)
        _record_stage_latency("worker_task_total", started_at=task_started_at, status=canary_result.status)
        _flush_stage_latency_buffer()
        emit_structured_log(
            "canonical_tabular_production_task_completed"
            if production_executor_enabled
            else "canonical_tabular_canary_task_completed",
            task_id=task_id, file_id=file_id,
            runtime=runtime_label,
            chart_count=len(list(canary_result.final_struct.get("chart_options") or [])),
            candidate_id=canary_result.execution.metadata.get("candidate_id"),
            prompt_strategy=canary_result.execution.prompt_strategy,
        )
        if canary_result.status == "completed":
            publish_task_progress(task_id, {"status": "completed"})
        return canary_result.status
    except SoftTimeLimitExceeded as timeout_error:
        _record_stage_latency("worker_task_failed", started_at=task_started_at, status="timeout")
        _flush_stage_latency_buffer()
        emit_structured_log(
            "task_timeout_soft",
            level="warning",
            task_id=task_id,
            file_id=file_id,
        )
        sb.table('analysis_tasks').update({
            'status': 'timeout',
            'results_json': json.dumps({
                "analysis": (
                    "## ⚠️ Análisis Demasiado Complejo\n\n"
                    "Tu solicitud ha excedido el tiempo máximo de procesamiento. "
                    "Intenta con un filtro más específico."
                ),
                "metrics": {},
                "chart_options": [],
                "data": [],
                "recommendations": [],
                "explainability": [],
            }, cls=CustomEncoder),
        }).eq('id', task_id).execute()
        return "timeout"
    except GeminiCircuitOpenError as circuit_error:
        _record_stage_latency("worker_task_failed", started_at=task_started_at, status="rate_limited")
        _flush_stage_latency_buffer()
        emit_structured_log(
            "gemini_circuit_open_task_rate_limited",
            level="warning",
            task_id=task_id,
            file_id=file_id,
            runtime=runtime_label,
            recovery_seconds=circuit_error.recovery_seconds,
        )
        sb.table('analysis_tasks').update({
            'status': 'rate_limited',
            'results_json': json.dumps({
                "analysis": (
                    "## ⚠️ Servicio de IA saturado\n\n"
                    "El servicio de análisis está temporalmente saturado. "
                    "Intenta nuevamente en unos segundos."
                ),
                "metrics": {},
                "chart_options": [],
                "data": [],
                "recommendations": [],
                "explainability": [],
                "error_trace": str(circuit_error)[:300],
            }, cls=CustomEncoder),
        }).eq('id', task_id).execute()
        return "rate_limited"
    except Exception as canary_error:
        _record_stage_latency("worker_task_failed", started_at=task_started_at, status="failed")
        _flush_stage_latency_buffer()
        try:
            uploaded_row_data = dict((uploaded_file_row.data or {})) if 'uploaded_file_row' in locals() else {}
            track_canary_runtime_execution_fallback(
                task_id=task_id, file_id=file_id,
                user_id=str(uploaded_row_data.get("user_id") or ""),
                team_id=str(uploaded_row_data.get("team_id") or ""),
                file_name=str(uploaded_row_data.get("file_name") or ""),
                prompt_type=prompt_type,
                fallback_reason=(
                    "production_runtime_execution_error"
                    if production_executor_enabled
                    else "canary_runtime_execution_error"
                ),
            )
        except Exception as canary_fallback_metric_error:
            emit_structured_log(
                "canonical_tabular_canary_fallback_metric_error",
                level="warning", task_id=task_id, file_id=file_id,
                error=str(canary_fallback_metric_error)[:240],
            )
        emit_structured_log(
            "canonical_tabular_production_task_fallback"
            if production_executor_enabled
            else "canonical_tabular_canary_task_fallback",
            level="warning", task_id=task_id, file_id=file_id,
            error=str(canary_error)[:240],
        )

        _real_error_str = str(canary_error)
        emit_structured_log(
            "canonical_tabular_execution_real_error",
            level="error", task_id=task_id, file_id=file_id,
            error_type=type(canary_error).__name__,
            error_full=_real_error_str[:500],
            runtime="production" if production_executor_enabled else "canary",
        )

        _LEGACY_SHIELD_ROW_THRESHOLD = 100_000
        _is_big_file = False
        _is_transient_error = False
        _is_logical_error = False
        try:
            _error_str = str(canary_error).lower()
            _transient_signals = ("521", "520", "502", "503", "web server is down",
                                  "connection", "timeout", "temporarily unavailable",
                                  "service unavailable", "pgrst")
            _is_transient_error = any(signal in _error_str for signal in _transient_signals)

            _logical_error_signals = ("empty_result", "argmax of an empty", "argmin of an empty",
                                      "column not found", "does not exist", "no matching",
                                      "no se encontraron", "empty sequence")
            _is_logical_error = any(signal in _error_str for signal in _logical_error_signals)

            _actual_row_count = 0
            try:
                import os as _os
                import json as _json
                import glob as _glob
                _sidecar_pattern = f"/tmp/promdata_cache/shadow_query_{str(file_id).replace('-', '_')}_primary_*.contract.json"
                _sidecar_files = sorted(_glob.glob(_sidecar_pattern), key=_os.path.getmtime, reverse=True)
                if _sidecar_files:
                    with open(_sidecar_files[0], 'r') as _sf:
                        _sidecar = _json.load(_sf)
                    _actual_row_count = int(_sidecar.get('row_count', 0) or _sidecar.get('rows_at_max', 0) or 0)
            except Exception:
                pass

            if _actual_row_count == 0:
                _is_big_file = False
            else:
                _is_big_file = (
                    _actual_row_count > _LEGACY_SHIELD_ROW_THRESHOLD
                    and not _is_transient_error
                    and not _is_logical_error
                )
        except Exception:
            pass

        if _is_big_file:
            emit_structured_log(
                "big_data_legacy_shield_activated",
                level="warning", task_id=task_id, file_id=file_id,
                canary_error=str(canary_error)[:240],
                reason="legacy_runtime_blocked_for_big_data_oom_prevention",
            )
            sb.table('analysis_tasks').update({
                'status': 'failed',
                'results_json': json.dumps({
                    "analysis": (
                        "## ⚠️ Archivo de Alto Volumen\n\n"
                        "El archivo contiene un volumen de datos que excede la capacidad de procesamiento "
                        "del pipeline actual. Por favor intenta:\n\n"
                        "- Reducir el número de hojas del archivo Excel\n"
                        "- Filtrar los datos antes de cargarlos\n"
                        "- Dividir el archivo en períodos más cortos\n\n"
                        "Nuestro equipo está optimizando el motor para soportar este volumen."
                    ),
                    "metrics": {},
                    "chart_options": [],
                    "data": [],
                    "recommendations": [],
                    "explainability": [],
                }, cls=CustomEncoder),
            }).eq('id', task_id).execute()
            return "failed"

        emit_structured_log(
            "canonical_tabular_execution_failed_no_fallback",
            level="error", task_id=task_id, file_id=file_id,
            error=str(canary_error)[:240],
            reason="production_runtime_failed_legacy_removed",
        )
        sb.table('analysis_tasks').update({
            'status': 'failed',
            'results_json': json.dumps({
                "analysis": (
                    "## ⚠️ Error en el Análisis\n\n"
                    "El motor de análisis no pudo procesar tu solicitud en este momento. "
                    "Esto puede ocurrir por:\n\n"
                    "- Combinación de filtros que no genera resultados\n"
                    "- Datos con formato inesperado en alguna columna\n"
                    "- Solicitud compleja que requiere ajuste de prompt\n\n"
                    "Intenta reformular tu pregunta o verifica que los filtros aplicados "
                    "coincidan con valores existentes en tus datos."
                ),
                "metrics": {},
                "chart_options": [],
                "data": [],
                "recommendations": [],
                "explainability": [],
                "error_trace": str(canary_error)[:300],
            }, cls=CustomEncoder),
        }).eq('id', task_id).execute()
        return "failed"
