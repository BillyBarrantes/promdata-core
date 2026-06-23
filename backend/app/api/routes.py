# En: backend/app/api/routes.py

import os
import httpx
from fastapi import APIRouter, HTTPException, Depends, Query, Request, Response, UploadFile, File, Form
from fastapi.responses import RedirectResponse
from fastapi.security import OAuth2PasswordBearer
from app.api.schemas import (
    AnalysisRequest, AnalysisTaskResponse, AnalysisHistoryResponse, ReportSaveRequest, ChatMessage, PresentationCreate,
    ReportLayoutBulkUpdateRequest, PresentationUpdate, DashboardExecutiveSummaryRequest,
    DashboardExecutiveSummaryResponse, CloudConnectorProviderResponse,
    WatchdogStatusResponse, OAuthAuthorizationResponse, CloudRemoteFileListResponse,
    CloudRemoteImportRequest, CloudRemoteImportResponse, CloudWatchTargetRequest,
    CloudWatchTargetResponse, CloudWatchTargetListResponse, CloudWatchdogPollRequest,
    CloudWatchdogPollResponse, FilePreviewResponse, KnowledgeDocumentListResponse,
    KnowledgeDocumentUploadResponse, KnowledgeQueryRequest, KnowledgeQueryResponse,
    KnowledgeAskRequest, KnowledgeAskResponse, EnterpriseTelemetrySummaryResponse,
)
from typing import Any, Optional
from app.tasks.analysis_tasks import (
    perform_analysis_task,
    perform_analysis_task_universal_tabular,
)
from app.tasks.cloud_sync_tasks import perform_cloud_sync_job_task
from app.celery_app import celery_app
from app.core.config import settings
from app.core.rate_limit import enforce_rate_limit, enforce_burst_limit, acquire_concurrency_slot
from app.core.structured_logging import emit_structured_log
from app.services.cloud_connectors import get_cloud_connector_catalog, get_watchdog_runtime_status
from app.services.cloud_oauth import (
    build_frontend_oauth_redirect_url,
    create_oauth_authorization_request,
    exchange_oauth_code_for_tokens,
    fetch_provider_account_profile,
    get_user_oauth_connection,
    get_oauth_state_record,
    get_provider_remote_file,
    get_provider_from_route_slug,
    get_user_oauth_connections,
    get_user_watch_target_counts,
    list_provider_remote_files,
    mark_oauth_state_result,
    upsert_oauth_connection,
    validate_oauth_state_record,
)
from app.services.cloud_watchdog import (
    _google_drive_stop_channel,
    _safe_connection_metadata,
    _safe_dict,
    _safe_google_changes_contract,
    deactivate_watch_target,
    ensure_google_drive_watch_contract,
    list_user_watch_targets,
    poll_user_watch_targets,
    upsert_watch_target,
)
from app.services.cloud_imports import (
    materialize_cloud_import,
    sync_uploaded_file_from_pending_watch_target as sync_uploaded_file_from_pending_watch_target_service,
)
from app.services.cloud_sync_jobs import (
    collect_pending_auto_sync_candidates,
    enqueue_cloud_sync_jobs_for_watchdog_changes,
    mark_cloud_sync_job_dispatch_failed,
)
from app.services.file_preview import build_file_preview_payload
from app.services.canonical_canary_health import build_canonical_tabular_canary_health
from app.services.canonical_canary_router import build_canonical_tabular_canary_route
from app.services.document_rag import (
    build_knowledge_context_block,
    create_knowledge_document_record,
    list_knowledge_documents,
    resolve_user_team_id,
    search_knowledge_documents,
)
from app.services.knowledge_qa import answer_knowledge_question
from app.services.enterprise_telemetry import (
    build_enterprise_telemetry_summary_for_user,
    track_analysis_requested,
    track_connector_file_imported,
    track_file_preview_generated,
    track_knowledge_ask_executed,
    track_knowledge_document_uploaded,
    track_report_saved,
)
from app.services.dashboard_narrative import generate_dashboard_executive_summary
from app.services.analysis_traceability import summarize_history_item
from app.services.governance import (
    build_document_governance_metadata,
    get_user_presentation_scope_or_404,
    get_user_report_scope_or_404,
    get_user_uploaded_file_scope_or_404,
    resolve_user_team_scope,
    stamp_report_content_governance,
)
from app.core.supabase_client import get_supabase_service_client, get_supabase_user_client
from app.tasks.document_tasks import process_knowledge_document_task
from app.api.sse_progress import router as sse_router
from supabase import create_client, Client
import uuid
import time
import json # <-- Aseguramos que la importación está aquí

router = APIRouter()
router.include_router(sse_router)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")


def _preview_text(value: Optional[str], limit: int = 120) -> str:
    text = str(value or "").replace("\x00", "").strip()
    return text[:limit]


def _get_authenticated_user(token: str):
    client = get_supabase_user_client(token)
    user_response = client.auth.get_user()
    if not user_response or not user_response.user or not user_response.user.id:
        raise HTTPException(status_code=401, detail="Token de usuario inválido o expirado.")
    return client, user_response.user


def _get_user_uploaded_file_or_404(*, user_id: str, file_id: str, service_client):
    response = service_client.table("uploaded_files") \
        .select("id, file_name, storage_path, created_at") \
        .eq("id", file_id) \
        .eq("user_id", user_id) \
        .limit(1) \
        .execute()

    if not response.data:
        raise HTTPException(status_code=404, detail="No se encontró el archivo solicitado.")

    return response.data[0]


def _dispatch_cloud_sync_job_with_fallback(job_id: str) -> tuple[bool, str | None]:
    try:
        perform_cloud_sync_job_task.apply_async(args=[job_id], ignore_result=True)
        return True, "apply_async"
    except Exception as primary_error:
        emit_structured_log(
            "cloud_sync_job_dispatch_primary_failed",
            level="warning",
            cloud_sync_job_id=job_id,
            error=str(primary_error),
        )

    try:
        celery_app.send_task(
            "perform_cloud_sync_job_task",
            args=[job_id],
            kwargs={},
            ignore_result=True,
        )
        return True, "send_task"
    except Exception as broker_fallback_error:
        emit_structured_log(
            "cloud_sync_job_dispatch_broker_fallback_failed",
            level="warning",
            cloud_sync_job_id=job_id,
            error=str(broker_fallback_error),
        )

    try:
        perform_cloud_sync_job_task(job_id)
        return True, "inline_fallback"
    except Exception as inline_fallback_error:
        return False, str(inline_fallback_error)


def _serialize_knowledge_document(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    return {
        "id": row.get("id"),
        "title": row.get("title"),
        "file_name": row.get("file_name"),
        "bucket_name": row.get("bucket_name"),
        "storage_path": row.get("storage_path"),
        "mime_type": row.get("mime_type"),
        "file_size_bytes": int(row.get("file_size_bytes") or 0),
        "source_kind": row.get("source_kind"),
        "status": row.get("status"),
        "chunk_count": int(row.get("chunk_count") or 0),
        "word_count": int(row.get("word_count") or 0),
        "last_error": row.get("last_error"),
        "created_at": row.get("created_at"),
        "processed_at": row.get("processed_at"),
        "metadata": metadata,
    }


def _sync_uploaded_file_from_pending_watch_target(
    *,
    user_id: str,
    uploaded_file: dict[str, Any],
    service_client: Any,
) -> dict[str, Any]:
    watch_target_response = service_client.table("cloud_watch_targets") \
        .select("*") \
        .eq("user_id", user_id) \
        .eq("linked_file_id", uploaded_file["id"]) \
        .eq("is_active", True) \
        .limit(1) \
        .execute()

    if not watch_target_response.data:
        return uploaded_file

    watch_target = watch_target_response.data[0]
    synced_file = sync_uploaded_file_from_pending_watch_target_service(
        user_id=user_id,
        uploaded_file=uploaded_file,
        service_client=service_client,
    )

    if (
        synced_file.get("storage_path") != uploaded_file.get("storage_path")
        or synced_file.get("file_name") != uploaded_file.get("file_name")
    ):
        emit_structured_log(
            "api_uploaded_file_synced_from_watch_target",
            user_id=user_id,
            file_id=synced_file["id"],
            provider=watch_target.get("provider"),
            watch_target_id=watch_target.get("id"),
            target_id=watch_target.get("target_id"),
            storage_path=synced_file.get("storage_path"),
        )
    return synced_file


def _hydrate_connector_catalog_for_user(user_id: str) -> list[dict]:
    service_client = get_supabase_service_client()
    providers = get_cloud_connector_catalog()
    connection_index = {
        row.get("provider"): row
        for row in get_user_oauth_connections(user_id, service_client)
        if row.get("provider")
    }
    target_counts = get_user_watch_target_counts(user_id, service_client)

    hydrated_providers = []
    for provider in providers:
        connection = connection_index.get(provider["id"])
        hydrated_providers.append({
            **provider,
            "connected": bool(connection and connection.get("status") == "active"),
            "connection_id": connection.get("id") if connection else None,
            "connection_status": connection.get("status") if connection else None,
            "connected_account_email": connection.get("external_account_email") if connection else None,
            "connected_account_name": connection.get("external_account_name") if connection else None,
            "watch_target_count": target_counts.get(provider["id"], 0),
            "last_refreshed_at": connection.get("last_refreshed_at") if connection else None,
        })
    return hydrated_providers


def _find_google_connection_by_channel(
    *,
    service_client: Any,
    channel_id: str,
    channel_token: Optional[str],
) -> dict[str, Any] | None:
    if not channel_id:
        return None

    response = service_client.table("cloud_oauth_connections") \
        .select("*") \
        .eq("provider", "google_drive") \
        .eq("status", "active") \
        .execute()

    for row in response.data or []:
        google_changes = _safe_google_changes_contract(row)
        channel = _safe_dict(google_changes.get("channel"))
        if str(channel.get("id") or "").strip() != channel_id:
            continue
        expected_token = str(channel.get("token") or "").strip()
        if channel_token and expected_token and channel_token != expected_token:
            continue
        return row
    return None


def _start_oauth_flow(provider_id: str, token: str, redirect_to: Optional[str] = None):
    _, user = _get_authenticated_user(token)
    service_client = get_supabase_service_client()
    auth_payload = create_oauth_authorization_request(
        provider_id,
        user_id=user.id,
        redirect_to=redirect_to,
        service_client=service_client,
    )
    emit_structured_log(
        "oauth_flow_started",
        provider=provider_id,
        user_id=user.id,
        return_to=auth_payload["return_to"],
        state_expires_at=auth_payload["state_expires_at"],
    )
    return auth_payload


def _handle_oauth_callback(
    provider_route_slug: str,
    *,
    code: Optional[str],
    state: Optional[str],
    error: Optional[str],
    error_description: Optional[str],
):
    provider_id = get_provider_from_route_slug(provider_route_slug)
    service_client = get_supabase_service_client()
    state_record = get_oauth_state_record(provider_id, state or "", service_client) if state else None
    redirect_to = (state_record or {}).get("redirect_to")

    emit_structured_log(
        "oauth_callback_received",
        provider=provider_id,
        has_code=bool(code),
        has_state=bool(state),
        provider_error=_preview_text(error, 80) if error else None,
    )

    if error:
        error_message = _preview_text(error_description or error, 180)
        if state_record:
            mark_oauth_state_result(
                state_record["id"],
                service_client=service_client,
                status="cancelled" if str(error).lower() == "access_denied" else "error",
                error_message=error_message,
            )
        emit_structured_log(
            "oauth_callback_error",
            level="error",
            provider=provider_id,
            state_id=(state_record or {}).get("id"),
            error=error_message,
        )
        return RedirectResponse(
            build_frontend_oauth_redirect_url(
                provider_id,
                status="error",
                message=error_message,
                redirect_to=redirect_to,
            )
        )

    if not state_record:
        emit_structured_log(
            "oauth_callback_error",
            level="error",
            provider=provider_id,
            error="State OAuth inválido o inexistente",
        )
        return RedirectResponse(
            build_frontend_oauth_redirect_url(
                provider_id,
                status="error",
                message="State OAuth inválido o inexistente",
                redirect_to=redirect_to,
            )
        )

    if not code:
        error_message = "Callback OAuth sin código de autorización"
        mark_oauth_state_result(
            state_record["id"],
            service_client=service_client,
            status="error",
            error_message=error_message,
        )
        emit_structured_log(
            "oauth_callback_error",
            level="error",
            provider=provider_id,
            user_id=state_record.get("user_id"),
            state_id=state_record.get("id"),
            error=error_message,
        )
        return RedirectResponse(
            build_frontend_oauth_redirect_url(
                provider_id,
                status="error",
                message=error_message,
                redirect_to=redirect_to,
            )
        )

    try:
        validate_oauth_state_record(state_record)
        token_payload = exchange_oauth_code_for_tokens(
            provider_id,
            code=code,
            code_verifier=state_record["code_verifier"],
        )
        profile_payload = fetch_provider_account_profile(
            provider_id,
            access_token=token_payload["access_token"],
        )
        connection = upsert_oauth_connection(
            provider_id,
            user_id=state_record["user_id"],
            token_payload=token_payload,
            profile_payload=profile_payload,
            service_client=service_client,
        )
        mark_oauth_state_result(
            state_record["id"],
            service_client=service_client,
            status="connected",
        )
        emit_structured_log(
            "oauth_callback_completed",
            provider=provider_id,
            user_id=state_record["user_id"],
            connection_id=connection.get("id"),
            connected_account_email=connection.get("external_account_email"),
        )
        return RedirectResponse(
            build_frontend_oauth_redirect_url(
                provider_id,
                status="connected",
                message="Cuenta conectada correctamente",
                redirect_to=redirect_to,
            )
        )
    except Exception as exc:
        error_message = _preview_text(str(exc), 200)
        mark_oauth_state_result(
            state_record["id"],
            service_client=service_client,
            status="error",
            error_message=error_message,
        )
        emit_structured_log(
            "oauth_callback_error",
            level="error",
            provider=provider_id,
            user_id=state_record.get("user_id"),
            state_id=state_record.get("id"),
            error=error_message,
        )
        return RedirectResponse(
            build_frontend_oauth_redirect_url(
                provider_id,
                status="error",
                message=error_message,
                redirect_to=redirect_to,
            )
        )

@router.post("/analyze", response_model=AnalysisTaskResponse, status_code=202)
@router.post("/analyze/", response_model=AnalysisTaskResponse, status_code=202, include_in_schema=False)
def start_analysis(
    request: Request,
    request_body: AnalysisRequest,
    token: str = Depends(oauth2_scheme)
):
    print(f"🕵️\u200d♂️ [ESPÍA BACKEND] Petición POST recibida en el endpoint | {__import__('datetime').datetime.utcnow().isoformat()}Z")
    try:
        enforce_rate_limit(
            request=request,
            token=token,
            scope="analyze",
            limit=settings.RATE_LIMIT_ANALYZE_LIMIT,
            window_seconds=settings.RATE_LIMIT_WINDOW_SECONDS,
        )
        
        # Burst Limit Protection
        enforce_burst_limit(
            request=request,
            token=token,
            scope="analyze",
            limit=settings.RATE_LIMIT_BURST_ANALYZE_LIMIT,
            window_seconds=settings.RATE_LIMIT_BURST_WINDOW_SECONDS,
        )

        supabase: Client = get_supabase_user_client(token)
        user_response = supabase.auth.get_user()
        current_user_id = user_response.user.id

        if not current_user_id:
            raise HTTPException(status_code=401, detail="Token de usuario inválido o expirado.")

        # Concurrency Limit Protection
        if not acquire_concurrency_slot(token, settings.CONCURRENT_TASKS_PER_USER, settings.CONCURRENT_TASKS_TTL_SECONDS):
            raise HTTPException(
                status_code=429,
                detail="Tienes demasiados análisis en curso. Por favor, espera a que terminen antes de solicitar uno nuevo.",
            )

        team_id = resolve_user_team_scope(user_id=current_user_id, service_client=supabase)
        uploaded_file_row = get_user_uploaded_file_scope_or_404(
            user_id=current_user_id,
            team_id=team_id,
            file_id=request_body.file_id,
            service_client=supabase,
        )

        # Sanitizar prompt
        safe_prompt = request_body.prompt.replace('\x00', '') if request_body.prompt else request_body.prompt

        new_task_id = uuid.uuid4()
        canary_health = build_canonical_tabular_canary_health()
        runtime_route = build_canonical_tabular_canary_route(
            task_id=str(new_task_id),
            file_id=request_body.file_id,
            file_name=str(uploaded_file_row.get("file_name") or ""),
            user_id=current_user_id,
            team_id=team_id,
            prompt=safe_prompt,
            health_summary=canary_health,
        )
        db_response = supabase.table('analysis_tasks').insert({
            "id": str(new_task_id),
            "file_id": request_body.file_id,
            "status": "pending",
            "prompt": safe_prompt,
            "user_id": current_user_id
        }).execute()

        if db_response.data is None and hasattr(db_response, 'error') and db_response.error:
            raise Exception(f"Error en Supabase: {db_response.error.message}")

        dispatch_task = (
            perform_analysis_task_universal_tabular
            if runtime_route.get("effective_runtime") == "universal_tabular"
            else perform_analysis_task
        )
        dispatch_task.delay(
            task_id=str(new_task_id),
            file_id=request_body.file_id,
            prompt=safe_prompt,
            user_token=token,
            runtime_route=runtime_route,
        )

        emit_structured_log(
            "api_analysis_task_created",
            task_id=str(new_task_id),
            file_id=request_body.file_id,
            user_id=current_user_id,
            prompt_preview=_preview_text(safe_prompt),
            runtime_route=runtime_route.get("effective_runtime"),
            runtime_route_mode=runtime_route.get("decision_mode"),
            runtime_route_reason=runtime_route.get("decision_reason"),
            runtime_task_target="perform_analysis_task_universal_tabular"
            if runtime_route.get("effective_runtime") == "universal_tabular"
            else "perform_analysis_task",
        )
        emit_structured_log(
            "api_analysis_runtime_route_selected",
            task_id=str(new_task_id),
            file_id=request_body.file_id,
            user_id=current_user_id,
            team_id=team_id,
            file_name=str(uploaded_file_row.get("file_name") or ""),
            requested_runtime=runtime_route.get("requested_runtime"),
            effective_runtime=runtime_route.get("effective_runtime"),
            decision_mode=runtime_route.get("decision_mode"),
            decision_reason=runtime_route.get("decision_reason"),
            health_status=runtime_route.get("health_status"),
            bucket_value=runtime_route.get("bucket_value"),
            traffic_percent=runtime_route.get("traffic_percent"),
        )
        track_analysis_requested(
            task_id=str(new_task_id),
            file_id=request_body.file_id,
            user_id=current_user_id,
            prompt=safe_prompt,
        )

        return AnalysisTaskResponse(task_id=str(new_task_id))

    except Exception as e:
        print(f"Error al iniciar el análisis: {e}")
        emit_structured_log(
            "api_analysis_task_error",
            level="error",
            file_id=getattr(request_body, "file_id", None),
            prompt_preview=_preview_text(getattr(request_body, "prompt", "")),
            error=str(e),
        )
        if "Invalid JWT" in str(e) or "Unauthorized" in str(e):
             raise HTTPException(status_code=401, detail=f"No se pudieron validar las credenciales: {e}")
        # [FIX 2026-06-08] Distinguir Supabase degradado de errores reales.
        # httpx.TimeoutException = request tardó más que el timeout configurado
        # IndexError/KeyError = respuesta corrupta (jwt.split falló, Disk IO agotado)
        # En ambos casos → 503 (transitorio, reintentar) en vez de 500 (bug código)
        if (
            isinstance(e, (httpx.TimeoutException, IndexError, KeyError))
            or "list index out of range" in str(e)
            or "Invalid API key" in str(e)
        ):
            emit_structured_log(
                "api_supabase_degraded",
                level="warning",
                endpoint="/analyze",
                error=str(e)[:240],
                hint="Supabase plan free Disk IO budget probablemente agotado",
            )
            raise HTTPException(
                status_code=503,
                detail="El servicio de base de datos está temporalmente no disponible. Por favor, inténtalo de nuevo en unos minutos.",
            )
        raise HTTPException(status_code=500, detail=str(e))
    
# (Removed ReportSaveRequest class definition from here)

@router.get("/tasks/{task_id}")
def get_task_status(task_id: str):
    """Consulta el estado y el resultado de una tarea de análisis."""
    try:
        supabase: Client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)

        response = supabase.table('analysis_tasks').select('status, results_json').eq('id', task_id).single().execute()

        if response.data and response.data.get('status') in ['completed', 'failed', 'timeout', 'rate_limited']:
            results_payload = response.data.get('results_json')

            if isinstance(results_payload, str):
                results_payload = json.loads(results_payload)

            print(f"DIAGNÓSTICO API: Tipo de dato enviado al frontend -> {type(results_payload)}")
            emit_structured_log(
                "api_task_status_resolved",
                task_id=task_id,
                status=response.data['status'],
                result_type=type(results_payload).__name__,
                has_result=results_payload is not None,
            )

            return {
                "status": response.data['status'],
                "result": results_payload
            }

        if response.data:
            emit_structured_log(
                "api_task_status_polled",
                task_id=task_id,
                status=response.data.get('status'),
                has_result=False,
            )
            return {"status": response.data.get('status'), "result": None}
        else:
            raise HTTPException(status_code=404, detail="Task not found")

    except Exception as e:
        print(f"Error al obtener estado de la tarea {task_id}: {e}")
        emit_structured_log(
            "api_task_status_error",
            level="error",
            task_id=task_id,
            error=str(e),
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/analysis/history", response_model=AnalysisHistoryResponse)
def get_analysis_history(
    file_id: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    token: str = Depends(oauth2_scheme),
):
    try:
        _, user = _get_authenticated_user(token)
        service_client = get_supabase_service_client()

        query = service_client.table("analysis_tasks") \
            .select("id, file_id, status, prompt, created_at, results_json") \
            .eq("user_id", user.id) \
            .order("created_at", desc=True) \
            .limit(limit)

        if file_id:
            query = query.eq("file_id", file_id)

        response = query.execute()
        rows = response.data or []
        items = []

        for row in rows:
            results_payload = row.get("results_json")
            if isinstance(results_payload, str):
                try:
                    results_payload = json.loads(results_payload)
                except Exception:
                    results_payload = None

            items.append(
                summarize_history_item(
                    task_row=row,
                    result_payload=results_payload if isinstance(results_payload, dict) else None,
                )
            )

        emit_structured_log(
            "api_analysis_history_fetched",
            user_id=user.id,
            file_id=file_id,
            count=len(items),
            limit=limit,
        )
        return {"items": items}
    except HTTPException:
        raise
    except Exception as e:
        emit_structured_log(
            "api_analysis_history_error",
            level="error",
            file_id=file_id,
            limit=limit,
            error=str(e),
        )
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/tasks/{task_id}/cancel", status_code=200)
def cancel_task(task_id: str, token: str = Depends(oauth2_scheme)):
    """Cancela una tarea en ejecución (Celery y Supabase)."""
    try:
        # 1. Detener en Celery (Kill signal)
        celery_app.control.revoke(task_id, terminate=True, signal='SIGKILL')
        
        # 2. Actualizar estado en Supabase
        supabase: Client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        supabase.auth.set_session(access_token=token, refresh_token=token)
        
        supabase.table('analysis_tasks').update({'status': 'cancelled'}).eq('id', task_id).execute()
        emit_structured_log("api_task_cancelled", task_id=task_id)
        
        return {"status": "cancelled", "message": f"Tarea {task_id} detenida permanentemente."}
        
    except Exception as e:
        print(f"Error cancelando tarea {task_id}: {e}")
        emit_structured_log(
            "api_task_cancel_error",
            level="error",
            task_id=task_id,
            error=str(e),
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/files/{file_id}/preview", response_model=FilePreviewResponse)
def get_uploaded_file_preview(
    file_id: str,
    token: str = Depends(oauth2_scheme),
):
    try:
        _, user = _get_authenticated_user(token)
        service_client = get_supabase_service_client()
        uploaded_file = _get_user_uploaded_file_or_404(
            user_id=user.id,
            file_id=file_id,
            service_client=service_client,
        )
        uploaded_file = _sync_uploaded_file_from_pending_watch_target(
            user_id=user.id,
            uploaded_file=uploaded_file,
            service_client=service_client,
        )

        file_bytes = service_client.storage.from_("dash-uploads").download(uploaded_file["storage_path"])
        preview_payload = build_file_preview_payload(
            file_id=uploaded_file["id"],
            file_name=uploaded_file["file_name"],
            file_bytes=file_bytes,
            created_at=uploaded_file.get("created_at"),
        )

        emit_structured_log(
            "api_file_preview_generated",
            user_id=user.id,
            file_id=file_id,
            row_count=preview_payload["row_count"],
            column_count=preview_payload["column_count"],
            selected_sheet=preview_payload.get("selected_sheet"),
        )
        track_file_preview_generated(
            user_id=user.id,
            file_id=file_id,
            preview_payload=preview_payload,
        )
        return preview_payload
    except HTTPException:
        raise
    except Exception as e:
        emit_structured_log(
            "api_file_preview_error",
            level="error",
            file_id=file_id,
            error=str(e),
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/telemetry/summary", response_model=EnterpriseTelemetrySummaryResponse)
def get_enterprise_telemetry_summary(
    window_days: int = Query(default=30, ge=1, le=90),
    token: str = Depends(oauth2_scheme),
):
    try:
        _, user = _get_authenticated_user(token)
        service_client = get_supabase_service_client()
        summary_payload = build_enterprise_telemetry_summary_for_user(
            user_id=user.id,
            service_client=service_client,
            window_days=window_days,
        )
        emit_structured_log(
            "api_enterprise_telemetry_summary_generated",
            user_id=user.id,
            window_days=window_days,
            event_count=summary_payload.get("event_count", 0),
            telemetry_ready=summary_payload.get("telemetry_ready", True),
        )
        return summary_payload
    except HTTPException:
        raise
    except Exception as e:
        emit_structured_log(
            "api_enterprise_telemetry_summary_error",
            level="error",
            window_days=window_days,
            error=str(e)[:240],
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/knowledge/documents/upload", response_model=KnowledgeDocumentUploadResponse, status_code=202)
async def upload_knowledge_document(
    file: UploadFile = File(...),
    title: Optional[str] = Form(default=None),
    token: str = Depends(oauth2_scheme),
):
    try:
        _, user = _get_authenticated_user(token)
        service_client = get_supabase_service_client()
        team_id = resolve_user_team_id(user_id=user.id, service_client=service_client)

        file_bytes = await file.read()
        if not file_bytes:
            raise HTTPException(status_code=400, detail="El documento está vacío.")

        safe_file_name = file.filename or "documento"
        storage_path = f"{user.id}/{int(time.time() * 1000)}_{safe_file_name.replace(' ', '_')}"
        bucket_name = settings.KNOWLEDGE_DOCUMENTS_BUCKET
        service_client.storage.from_(bucket_name).upload(
            storage_path,
            file_bytes,
            {"content-type": file.content_type or "application/octet-stream"},
        )

        document_row = create_knowledge_document_record(
            user_id=user.id,
            team_id=team_id,
            title=(title or safe_file_name).strip(),
            file_name=safe_file_name,
            mime_type=file.content_type,
            storage_path=storage_path,
            file_size_bytes=len(file_bytes),
            service_client=service_client,
        )
        process_knowledge_document_task.delay(str(document_row["id"]), user.id)

        emit_structured_log(
            "api_knowledge_document_uploaded",
            user_id=user.id,
            team_id=team_id,
            document_id=document_row.get("id"),
            title=document_row.get("title"),
            file_name=safe_file_name,
            mime_type=file.content_type,
            file_size_bytes=len(file_bytes),
        )
        track_knowledge_document_uploaded(
            user_id=user.id,
            team_id=team_id,
            document_id=document_row.get("id"),
            mime_type=file.content_type,
            file_size_bytes=len(file_bytes),
        )
        return {
            "document": _serialize_knowledge_document(document_row),
            "task_status": "queued",
        }
    except HTTPException:
        raise
    except Exception as e:
        emit_structured_log(
            "api_knowledge_document_upload_error",
            level="error",
            file_name=getattr(file, "filename", None),
            error=str(e),
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/knowledge/documents", response_model=KnowledgeDocumentListResponse)
def get_knowledge_documents(
    token: str = Depends(oauth2_scheme),
):
    try:
        _, user = _get_authenticated_user(token)
        service_client = get_supabase_service_client()
        team_id = resolve_user_team_id(user_id=user.id, service_client=service_client)
        rows = list_knowledge_documents(
            user_id=user.id,
            team_id=team_id,
            service_client=service_client,
        )
        emit_structured_log(
            "api_knowledge_documents_fetched",
            user_id=user.id,
            team_id=team_id,
            count=len(rows),
        )
        return {
            "documents": [_serialize_knowledge_document(row) for row in rows],
        }
    except HTTPException:
        raise
    except Exception as e:
        emit_structured_log(
            "api_knowledge_documents_error",
            level="error",
            error=str(e),
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/knowledge/documents/{document_id}/retry", response_model=KnowledgeDocumentUploadResponse)
def retry_knowledge_document_index(
    document_id: str,
    token: str = Depends(oauth2_scheme),
):
    try:
        _, user = _get_authenticated_user(token)
        service_client = get_supabase_service_client()
        team_id = resolve_user_team_id(user_id=user.id, service_client=service_client)

        response = service_client.table("knowledge_documents") \
            .select("*") \
            .eq("id", document_id) \
            .eq("user_id", user.id) \
            .eq("team_id", team_id) \
            .limit(1) \
            .execute()

        if not response.data:
            raise HTTPException(status_code=404, detail="No se encontró el documento solicitado.")

        document_row = response.data[0]
        current_status = str(document_row.get("status") or "").strip().lower()
        if current_status == "processing":
            return {
                "document": _serialize_knowledge_document(document_row),
                "task_status": "processing",
            }

        metadata = document_row.get("metadata") if isinstance(document_row.get("metadata"), dict) else {}
        updated_metadata = {
            **metadata,
            "retry_requested_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        updated_metadata = build_document_governance_metadata(
            metadata=updated_metadata,
            user_id=user.id,
            team_id=team_id,
            revision_kind="retry_queue",
            increment_revision=True,
        )

        update_response = service_client.table("knowledge_documents").update({
            "status": "queued",
            "last_error": None,
            "processed_at": None,
            "metadata": updated_metadata,
        }).eq("id", document_id).execute()

        queued_row = update_response.data[0] if update_response.data else {
            **document_row,
            "status": "queued",
            "last_error": None,
            "processed_at": None,
            "metadata": updated_metadata,
        }

        process_knowledge_document_task.delay(str(document_id), user.id)

        emit_structured_log(
            "api_knowledge_document_retry_queued",
            user_id=user.id,
            team_id=team_id,
            document_id=document_id,
            previous_status=current_status,
        )
        return {
            "document": _serialize_knowledge_document(queued_row),
            "task_status": "queued",
        }
    except HTTPException:
        raise
    except Exception as e:
        emit_structured_log(
            "api_knowledge_document_retry_error",
            level="error",
            document_id=document_id,
            error=str(e),
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/knowledge/query", response_model=KnowledgeQueryResponse)
def query_knowledge_documents(
    request_body: KnowledgeQueryRequest,
    token: str = Depends(oauth2_scheme),
):
    try:
        _, user = _get_authenticated_user(token)
        service_client = get_supabase_service_client()
        team_id = resolve_user_team_id(user_id=user.id, service_client=service_client)
        snippets = search_knowledge_documents(
            user_id=user.id,
            team_id=team_id,
            query=request_body.query,
            limit=request_body.limit,
            document_ids=request_body.document_ids,
            service_client=service_client,
        )
        context_block = build_knowledge_context_block(snippets)
        emit_structured_log(
            "api_knowledge_query_executed",
            user_id=user.id,
            team_id=team_id,
            query_preview=_preview_text(request_body.query, 160),
            result_count=len(snippets),
        )
        return {
            "query": request_body.query,
            "count": len(snippets),
            "snippets": [
                {
                    "document_id": snippet.document_id,
                    "document_title": snippet.document_title,
                    "document_file_name": snippet.document_file_name,
                    "chunk_index": snippet.chunk_index,
                    "content": snippet.content,
                    "similarity": snippet.similarity,
                    "source_kind": snippet.source_kind,
                    "metadata": snippet.metadata,
                }
                for snippet in snippets
            ],
            "context_block": context_block,
        }
    except HTTPException:
        raise
    except Exception as e:
        emit_structured_log(
            "api_knowledge_query_error",
            level="error",
            query_preview=_preview_text(getattr(request_body, "query", ""), 160),
            error=str(e),
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/knowledge/ask", response_model=KnowledgeAskResponse)
def ask_knowledge_documents(
    request: Request,
    request_body: KnowledgeAskRequest,
    token: str = Depends(oauth2_scheme),
):
    try:
        enforce_rate_limit(
            request=request,
            token=token,
            scope="knowledge_ask",
            limit=settings.RATE_LIMIT_KNOWLEDGE_ASK_LIMIT,
            window_seconds=settings.RATE_LIMIT_WINDOW_SECONDS,
        )

        _, user = _get_authenticated_user(token)
        service_client = get_supabase_service_client()
        team_id = resolve_user_team_id(user_id=user.id, service_client=service_client)
        snippets = search_knowledge_documents(
            user_id=user.id,
            team_id=team_id,
            query=request_body.question,
            limit=request_body.limit,
            document_ids=request_body.document_ids,
            service_client=service_client,
        )
        response_payload = answer_knowledge_question(
            question=request_body.question,
            snippets=snippets,
        )
        emit_structured_log(
            "api_knowledge_ask_executed",
            user_id=user.id,
            team_id=team_id,
            question_preview=_preview_text(request_body.question, 160),
            retrieved_count=response_payload.get("retrieved_count", 0),
            citations_count=len(response_payload.get("citations") or []),
            grounded=response_payload.get("grounded", False),
            insufficient_evidence=response_payload.get("insufficient_evidence", False),
        )
        track_knowledge_ask_executed(
            user_id=user.id,
            team_id=team_id,
            response_payload=response_payload,
        )
        return response_payload
    except HTTPException:
        raise
    except Exception as e:
        emit_structured_log(
            "api_knowledge_ask_error",
            level="error",
            question_preview=_preview_text(request_body.question, 160),
            error=str(e)[:240],
        )
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/reports", status_code=201)
def save_report(
    request_body: ReportSaveRequest,
    token: str = Depends(oauth2_scheme)
):
    try:
        if not request_body:
             raise HTTPException(status_code=400, detail="Missing request body")

        supabase: Client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        supabase.auth.set_session(access_token=token, refresh_token=token)
        user = supabase.auth.get_user()
        user_id = user.user.id

        if not user_id:
             raise HTTPException(status_code=401, detail="Usuario no autenticado")

        team_id = resolve_user_team_scope(user_id=user_id, service_client=supabase)

        presentation_id = request_body.presentation_id
        presentation_payload = None

        if request_body.file_id:
            get_user_uploaded_file_scope_or_404(
                user_id=user_id,
                team_id=team_id,
                file_id=request_body.file_id,
                service_client=supabase,
            )

        if presentation_id:
            presentation_payload = get_user_presentation_scope_or_404(
                user_id=user_id,
                team_id=team_id,
                presentation_id=presentation_id,
                service_client=supabase,
            )

        if request_body.file_id and not presentation_id:
            existing_presentation = supabase.table('presentations')\
                .select('id, name, file_id, created_at')\
                .eq('user_id', user_id)\
                .eq('file_id', request_body.file_id)\
                .order('created_at', desc=False)\
                .limit(1)\
                .execute()

            if existing_presentation.data:
                presentation_payload = existing_presentation.data[0]
                presentation_id = presentation_payload['id']
            else:
                file_name = request_body.file_id
                try:
                    uploaded_file = supabase.table('uploaded_files')\
                        .select('file_name')\
                        .eq('id', request_body.file_id)\
                        .single()\
                        .execute()
                    if uploaded_file.data and uploaded_file.data.get('file_name'):
                        file_name = uploaded_file.data['file_name']
                except Exception as file_lookup_error:
                    print(f"Warning resolviendo nombre de archivo para presentación automática: {file_lookup_error}")

                created_presentation = supabase.table('presentations').insert({
                    "user_id": user_id,
                    "name": file_name,
                    "file_id": request_body.file_id
                }).execute()

                if created_presentation.data:
                    presentation_payload = created_presentation.data[0]
                    presentation_id = presentation_payload['id']

        governed_content = stamp_report_content_governance(
            content=request_body.content,
            user_id=user_id,
            team_id=team_id,
            file_id=request_body.file_id,
            presentation_id=presentation_id,
            revision_kind="create",
        )

        # Insertar en la tabla 'saved_reports'
        # Asumimos que la tabla existe: id (auto), user_id, title, content (jsonb), created_at
        data = {
            "user_id": user_id,
            "title": request_body.title,
            "content": governed_content,
            "file_id": request_body.file_id,
            "presentation_id": presentation_id,
            "layout_x": governed_content.get('layout', {}).get('x') if isinstance(governed_content, dict) else None,
            "layout_y": governed_content.get('layout', {}).get('y') if isinstance(governed_content, dict) else None,
            "layout_w": governed_content.get('layout', {}).get('w') if isinstance(governed_content, dict) else None,
            "layout_h": governed_content.get('layout', {}).get('h') if isinstance(governed_content, dict) else None
        }
        
        response = supabase.table('saved_reports').insert(data).execute()
        report_id = response.data[0].get("id") if response.data else None
        emit_structured_log(
            "api_report_saved",
            report_id=report_id,
            presentation_id=presentation_id,
            file_id=request_body.file_id,
            user_id=user_id,
            title=_preview_text(request_body.title, limit=80),
        )
        track_report_saved(
            report_id=report_id,
            presentation_id=presentation_id,
            file_id=request_body.file_id,
            user_id=user_id,
            content=governed_content,
        )
        
        return {
            "status": "success",
            "message": "Reporte guardado",
            "data": response.data,
            "report_id": report_id,
            "presentation": presentation_payload,
            "presentation_id": presentation_id,
        }

    except Exception as e:
        print(f"Error guardando reporte: {e}")
        emit_structured_log(
            "api_report_save_error",
            level="error",
            presentation_id=getattr(request_body, "presentation_id", None),
            file_id=getattr(request_body, "file_id", None),
            title=_preview_text(getattr(request_body, "title", ""), limit=80),
            error=str(e),
        )
        raise HTTPException(status_code=500, detail=f"Error al guardar: {str(e)}")

@router.get("/reports")
def get_user_reports(presentation_id: Optional[str] = None, token: str = Depends(oauth2_scheme)):
    try:
        supabase: Client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        supabase.auth.set_session(access_token=token, refresh_token=token)
        user = supabase.auth.get_user()
        team_id = resolve_user_team_scope(user_id=user.user.id, service_client=supabase)
        
        query = supabase.table('saved_reports')\
            .select('*')\
            .eq('user_id', user.user.id)
            
        if presentation_id:
            get_user_presentation_scope_or_404(
                user_id=user.user.id,
                team_id=team_id,
                presentation_id=presentation_id,
                service_client=supabase,
            )
            query = query.eq('presentation_id', presentation_id)
            
        response = query.order('created_at', desc=True).execute()

        hydrated_reports = []
        for row in response.data or []:
            content = row.get('content') if isinstance(row, dict) else None
            if not isinstance(content, dict):
                content = {}

            layout_payload = {
                "x": row.get("layout_x"),
                "y": row.get("layout_y"),
                "w": row.get("layout_w"),
                "h": row.get("layout_h")
            }
            if any(value is not None for value in layout_payload.values()):
                content["layout"] = {
                    "x": layout_payload["x"],
                    "y": layout_payload["y"],
                    "w": layout_payload["w"],
                    "h": layout_payload["h"]
                }

            row["content"] = content
            hydrated_reports.append(row)

        emit_structured_log(
            "api_reports_fetched",
            user_id=user.user.id,
            presentation_id=presentation_id,
            count=len(hydrated_reports),
        )
            
        return hydrated_reports
    except Exception as e:
        print(f"Error obteniendo reportes: {e}")
        emit_structured_log(
            "api_reports_fetch_error",
            level="error",
            presentation_id=presentation_id,
            error=str(e),
        )
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/reports/layout", status_code=200)
def update_reports_layout(
    request_body: ReportLayoutBulkUpdateRequest,
    token: str = Depends(oauth2_scheme)
):
    try:
        if not request_body.items:
            return {"status": "success", "updated": 0}

        supabase: Client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        supabase.auth.set_session(access_token=token, refresh_token=token)
        user = supabase.auth.get_user()
        user_id = user.user.id if user and user.user and user.user.id else None
        if not user_id:
            raise HTTPException(status_code=401, detail="Usuario no autenticado")
        team_id = resolve_user_team_scope(user_id=user_id, service_client=supabase)

        requested_ids = list(dict.fromkeys(item.report_id for item in request_body.items if item.report_id))
        if not requested_ids:
            return {"status": "success", "updated": 0}

        existing_reports_response = supabase.table('saved_reports')\
            .select('id, file_id, presentation_id, content')\
            .eq('user_id', user_id)\
            .in_('id', requested_ids)\
            .execute()
        existing_reports = {
            row['id']: row
            for row in (existing_reports_response.data or [])
            if isinstance(row, dict) and row.get('id')
        }

        updated_count = 0
        skipped_count = 0
        failed_count = 0
        for item in request_body.items:
            report_row = existing_reports.get(item.report_id)
            if not report_row:
                skipped_count += 1
                continue

            current_content = report_row.get('content')
            if not isinstance(current_content, dict):
                current_content = {}
            else:
                current_content = dict(current_content)

            current_content['layout'] = {
                "x": item.x,
                "y": item.y,
                "w": item.w,
                "h": item.h
            }
            current_content = stamp_report_content_governance(
                content=current_content,
                user_id=user_id,
                team_id=team_id,
                file_id=report_row.get('file_id'),
                presentation_id=report_row.get('presentation_id'),
                revision_kind="layout_update",
                increment_layout_revision=True,
            )

            try:
                update_response = supabase.table('saved_reports')\
                    .update({
                        "content": current_content,
                        "layout_x": item.x,
                        "layout_y": item.y,
                        "layout_w": item.w,
                        "layout_h": item.h
                    })\
                    .eq('id', item.report_id)\
                    .eq('user_id', user_id)\
                    .execute()
                if hasattr(update_response, 'error') and update_response.error:
                    failed_count += 1
                    emit_structured_log(
                        "api_reports_layout_item_failed",
                        level="warning",
                        user_id=user_id,
                        report_id=item.report_id,
                        error=str(update_response.error),
                    )
                    continue
                updated_count += 1
            except Exception as update_error:
                failed_count += 1
                emit_structured_log(
                    "api_reports_layout_item_exception",
                    level="warning",
                    user_id=user_id,
                    report_id=item.report_id,
                    error=str(update_error),
                )

        emit_structured_log(
            "api_reports_layout_updated",
            user_id=user_id,
            updated=updated_count,
            skipped=skipped_count,
            failed=failed_count,
        )
        return {"status": "success", "updated": updated_count, "skipped": skipped_count, "failed": failed_count}
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error actualizando layout de reportes: {e}")
        emit_structured_log(
            "api_reports_layout_error",
            level="error",
            items_count=len(request_body.items) if request_body and request_body.items else 0,
            error=str(e),
        )
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/reports/{report_id}", status_code=204)
def delete_report(
    report_id: str,
    token: str = Depends(oauth2_scheme)
):
    try:
        supabase: Client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        supabase.auth.set_session(access_token=token, refresh_token=token)
        user = supabase.auth.get_user()
        team_id = resolve_user_team_scope(user_id=user.user.id, service_client=supabase)
        get_user_report_scope_or_404(
            user_id=user.user.id,
            team_id=team_id,
            report_id=report_id,
            service_client=supabase,
        )
        
        # RLS in Supabase ensures users can only delete their own reports
        # We just need to validly execute the delete against the correct ID
        response = supabase.table('saved_reports')\
            .delete()\
            .eq('id', report_id)\
            .execute()
        emit_structured_log(
            "api_report_deleted",
            report_id=report_id,
            user_id=user.user.id,
            deleted_count=len(response.data or []),
        )
            
        # If we wanted to be strict about 404s, we'd check response.data, 
        # but 204 No Content is standard for "it's gone (or was never there)".
        return 
        
    except Exception as e:
        print(f"Error borrando reporte: {e}")
        emit_structured_log(
            "api_report_delete_error",
            level="error",
            report_id=report_id,
            error=str(e),
        )
        raise HTTPException(status_code=500, detail=str(e))

# --- PRESENTATIONS ENDPOINTS ---

@router.get("/presentations")
def get_presentations(token: str = Depends(oauth2_scheme)):
    try:
        supabase: Client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        supabase.auth.set_session(access_token=token, refresh_token=token)
        user = supabase.auth.get_user()
        
        response = supabase.table('presentations')\
            .select('*')\
            .eq('user_id', user.user.id)\
            .order('created_at', desc=True)\
            .execute()
        emit_structured_log(
            "api_presentations_fetched",
            user_id=user.user.id,
            count=len(response.data or []),
        )
            
        return response.data
    except Exception as e:
        print(f"Error obteniendo presentaciones: {e}")
        emit_structured_log(
            "api_presentations_fetch_error",
            level="error",
            error=str(e),
        )
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/presentations", status_code=201)
def create_presentation(
    request_body: PresentationCreate,
    token: str = Depends(oauth2_scheme)
):
    try:
        supabase: Client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        supabase.auth.set_session(access_token=token, refresh_token=token)
        user = supabase.auth.get_user()
        team_id = resolve_user_team_scope(user_id=user.user.id, service_client=supabase)

        if request_body.file_id:
            get_user_uploaded_file_scope_or_404(
                user_id=user.user.id,
                team_id=team_id,
                file_id=request_body.file_id,
                service_client=supabase,
            )
        
        data = {
            "user_id": user.user.id,
            "name": request_body.name,
            "file_id": request_body.file_id
        }
        
        response = supabase.table('presentations').insert(data).execute()
        created = response.data[0] if response.data else None
        emit_structured_log(
            "api_presentation_created",
            presentation_id=created.get("id") if created else None,
            user_id=user.user.id,
            file_id=request_body.file_id,
            name=_preview_text(request_body.name, limit=80),
        )
        return response.data[0] if response.data else None
    except Exception as e:
        print(f"Error creando presentacion: {e}")
        emit_structured_log(
            "api_presentation_create_error",
            level="error",
            file_id=getattr(request_body, "file_id", None),
            name=_preview_text(getattr(request_body, "name", ""), limit=80),
            error=str(e),
        )
        raise HTTPException(status_code=500, detail=str(e))

@router.patch("/presentations/{presentation_id}", status_code=200)
def update_presentation(
    presentation_id: str,
    request_body: PresentationUpdate,
    token: str = Depends(oauth2_scheme)
):
    try:
        supabase: Client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        supabase.auth.set_session(access_token=token, refresh_token=token)
        user = supabase.auth.get_user()
        team_id = resolve_user_team_scope(user_id=user.user.id, service_client=supabase)

        get_user_presentation_scope_or_404(
            user_id=user.user.id,
            team_id=team_id,
            presentation_id=presentation_id,
            service_client=supabase,
        )

        response = supabase.table('presentations')\
            .update({"name": request_body.name})\
            .eq('id', presentation_id)\
            .eq('user_id', user.user.id)\
            .execute()
        emit_structured_log(
            "api_presentation_updated",
            presentation_id=presentation_id,
            user_id=user.user.id,
            name=_preview_text(request_body.name, limit=80),
            updated=bool(response.data),
        )

        return response.data[0] if response.data else None
    except Exception as e:
        print(f"Error actualizando presentacion: {e}")
        emit_structured_log(
            "api_presentation_update_error",
            level="error",
            presentation_id=presentation_id,
            name=_preview_text(getattr(request_body, "name", ""), limit=80),
            error=str(e),
        )
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/presentations/{presentation_id}", status_code=204)
def delete_presentation(
    presentation_id: str,
    token: str = Depends(oauth2_scheme)
):
    try:
        supabase: Client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        supabase.auth.set_session(access_token=token, refresh_token=token)
        user = supabase.auth.get_user()
        team_id = resolve_user_team_scope(user_id=user.user.id, service_client=supabase)

        get_user_presentation_scope_or_404(
            user_id=user.user.id,
            team_id=team_id,
            presentation_id=presentation_id,
            service_client=supabase,
        )

        supabase.table('saved_reports')\
            .delete()\
            .eq('presentation_id', presentation_id)\
            .eq('user_id', user.user.id)\
            .execute()

        supabase.table('presentations')\
            .delete()\
            .eq('id', presentation_id)\
            .eq('user_id', user.user.id)\
            .execute()

        emit_structured_log(
            "api_presentation_deleted",
            presentation_id=presentation_id,
            user_id=user.user.id,
        )

        return
    except Exception as e:
        print(f"Error eliminando presentacion: {e}")
        emit_structured_log(
            "api_presentation_delete_error",
            level="error",
            presentation_id=presentation_id,
            error=str(e),
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/presentations/executive-summary", response_model=DashboardExecutiveSummaryResponse)
def build_presentation_executive_summary(
    request_body: DashboardExecutiveSummaryRequest,
    token: str = Depends(oauth2_scheme)
):
    try:
        _, user = _get_authenticated_user(token)
        service_client = get_supabase_service_client()

        presentation_name = _preview_text(request_body.presentation_name, limit=120) or "Tablero Ejecutivo"
        presentation_id = (request_body.presentation_id or "").strip()
        if presentation_id:
            team_id = resolve_user_team_scope(user_id=user.id, service_client=service_client)
            presentation_row = get_user_presentation_scope_or_404(
                user_id=user.id,
                team_id=team_id,
                presentation_id=presentation_id,
                service_client=service_client,
            )
            presentation_name = _preview_text(presentation_row.get("name"), limit=120) or presentation_name

        widgets_payload = []
        for widget in request_body.widgets:
            widgets_payload.append(widget.model_dump() if hasattr(widget, "model_dump") else widget.dict())

        summary_payload = generate_dashboard_executive_summary(
            presentation_name=presentation_name,
            global_filters=request_body.global_filters or {},
            widgets=widgets_payload,
        )

        emit_structured_log(
            "api_presentation_executive_summary_generated",
            user_id=user.id,
            presentation_id=presentation_id or None,
            widget_count=len(widgets_payload),
            filter_count=len(request_body.global_filters or {}),
            mixed_sources=summary_payload.get("mixed_sources", False),
        )
        return summary_payload
    except HTTPException:
        raise
    except Exception as e:
        emit_structured_log(
            "api_presentation_executive_summary_error",
            level="error",
            presentation_id=getattr(request_body, "presentation_id", None),
            widget_count=len(getattr(request_body, "widgets", []) or []),
            error=str(e)[:240],
        )
        raise HTTPException(status_code=500, detail=str(e))

# --- CHAT HISTORY ENDPOINTS ---

@router.get("/chat/{file_id}")
def get_chat_history(
    file_id: str,
    token: str = Depends(oauth2_scheme)
):
    try:
        supabase: Client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        supabase.auth.set_session(access_token=token, refresh_token=token)
        user = supabase.auth.get_user()

        # Auto-cleanup: Delete messages older than 10 days for this file
        # We use a raw SQL query via RPC or if not enabled, we can't easily do it with simple library calls unless we have a specific function.
        # Alternatively, we can use the library's delete with filter.
        # "lt" (less than) operator on created_at.
        
        from datetime import datetime, timedelta, timezone
        cutoff_date = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()

        # Execute cleanup (fire and forget, we don't block if it fails, but good practice to await)
        try:
             supabase.table('chat_messages')\
                .delete()\
                .eq('file_id', file_id)\
                .lt('created_at', cutoff_date)\
                .execute()
        except Exception as cleanup_error:
            print(f"Warning: Cleanup failed {cleanup_error}")

        response = supabase.table('chat_messages')\
            .select('*')\
            .eq('user_id', user.user.id)\
            .eq('file_id', file_id)\
            .order('created_at', desc=False)\
            .execute()

        emit_structured_log(
            "api_chat_history_fetched",
            user_id=user.user.id,
            file_id=file_id,
            count=len(response.data or []),
        )
            
        return response.data
    except Exception as e:
        print(f"Error obteniendo chat history: {e}")
        emit_structured_log(
            "api_chat_history_error",
            level="error",
            file_id=file_id,
            error=str(e),
        )
        # [FIX 2026-06-08] Distinguir Supabase degradado de errores reales
        if (
            isinstance(e, (httpx.TimeoutException, IndexError, KeyError))
            or "list index out of range" in str(e)
            or "Invalid API key" in str(e)
        ):
            emit_structured_log(
                "api_supabase_degraded",
                level="warning",
                endpoint="/chat/{file_id}",
                error=str(e)[:240],
            )
            raise HTTPException(
                status_code=503,
                detail="El servicio de base de datos está temporalmente no disponible. Por favor, inténtalo de nuevo en unos minutos.",
            )
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/chat", status_code=201)
@router.post("/chat/", status_code=201, include_in_schema=False)
def save_chat_message(
    request: Request,
    message: ChatMessage,
    token: str = Depends(oauth2_scheme)
):
    try:
        enforce_rate_limit(
            request=request,
            token=token,
            scope="chat_message",
            limit=settings.RATE_LIMIT_CHAT_LIMIT,
            window_seconds=settings.RATE_LIMIT_WINDOW_SECONDS,
        )

        supabase: Client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        supabase.auth.set_session(access_token=token, refresh_token=token)
        user = supabase.auth.get_user()
        
        # Asegurar formato correcto para JSONB y sanitizar
        content_to_save = message.content
        if isinstance(content_to_save, str):
            content_to_save = content_to_save.replace('\x00', '')

        # Si es una lista o dict, supabase-py lo maneja, si es str, también.
        
        data = {
            "user_id": user.user.id,
            "file_id": message.file_id,
            "role": message.role,
            "content": content_to_save
        }
        
        response = supabase.table('chat_messages').insert(data).execute()
        emit_structured_log(
            "api_chat_message_saved",
            user_id=user.user.id,
            file_id=message.file_id,
            role=message.role,
            content_type=type(content_to_save).__name__,
        )
        return {"status": "success", "data": response.data}
        
    except Exception as e:
        print(f"Error guardando mensaje: {e}")
        emit_structured_log(
            "api_chat_message_error",
            level="error",
            file_id=getattr(message, "file_id", None),
            role=getattr(message, "role", None),
            error=str(e),
        )
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/chat/messages/{message_id}", status_code=204)
def delete_chat_message(
    message_id: str,
    token: str = Depends(oauth2_scheme)
):
    try:
        # Validar si es un UUID válido
        try:
            uuid.UUID(message_id)
        except ValueError:
            # Si no es UUID (ej: ID temporal del frontend), no está en DB.
            # Retornamos éxito para que el frontend lo borre de su vista.
            return

        supabase: Client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        supabase.auth.set_session(access_token=token, refresh_token=token)
        # RLS handles ownership check
        supabase.table('chat_messages').delete().eq('id', message_id).execute()
        emit_structured_log("api_chat_message_deleted", message_id=message_id)
        return
    except Exception as e:
        print(f"Error borrando mensaje chat: {e}")
        emit_structured_log(
            "api_chat_message_delete_error",
            level="error",
            message_id=message_id,
            error=str(e),
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/connectors/providers", response_model=list[CloudConnectorProviderResponse])
def get_cloud_connectors(token: str = Depends(oauth2_scheme)):
    try:
        _, user = _get_authenticated_user(token)
        providers = _hydrate_connector_catalog_for_user(user.id)
        emit_structured_log(
            "api_cloud_connectors_fetched",
            user_id=user.id,
            count=len(providers),
            configured_count=sum(1 for provider in providers if provider["configured"]),
            connected_count=sum(1 for provider in providers if provider["connected"]),
        )
        return providers
    except Exception as e:
        print(f"Error obteniendo conectores cloud: {e}")
        emit_structured_log(
            "api_cloud_connectors_error",
            level="error",
            error=str(e),
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/connectors/watchdog/status", response_model=WatchdogStatusResponse)
def get_connectors_watchdog_status(token: str = Depends(oauth2_scheme)):
    try:
        _, user = _get_authenticated_user(token)
        service_client = get_supabase_service_client()

        status_payload = get_watchdog_runtime_status(
            service_client=service_client,
            user_id=user.id,
        )
        emit_structured_log(
            "api_connectors_watchdog_status",
            user_id=user.id,
            enabled=status_payload["enabled"],
            configured_provider_count=status_payload["configured_provider_count"],
            watchdog_provider_count=status_payload["watchdog_provider_count"],
            connected_provider_count=status_payload.get("connected_provider_count", 0),
            active_target_count=status_payload.get("active_target_count", 0),
            pending_target_count=status_payload.get("pending_target_count", 0),
            operational_state=status_payload.get("operational_state"),
        )
        return status_payload
    except Exception as e:
        print(f"Error obteniendo estado de watchdog cloud: {e}")
        emit_structured_log(
            "api_connectors_watchdog_error",
            level="error",
            error=str(e),
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/connectors/{provider_id}/files", response_model=CloudRemoteFileListResponse)
def get_connector_files(
    provider_id: str,
    search: Optional[str] = Query(default=None),
    cursor: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=50),
    token: str = Depends(oauth2_scheme),
):
    try:
        _, user = _get_authenticated_user(token)
        service_client = get_supabase_service_client()
        connection = get_user_oauth_connection(user.id, provider_id, service_client)
        if not connection or connection.get("status") != "active":
            raise HTTPException(status_code=404, detail="No existe una conexión activa para este proveedor.")

        payload = list_provider_remote_files(
            provider_id,
            connection_row=connection,
            service_client=service_client,
            limit=limit,
            cursor=cursor,
            search=search,
        )
        emit_structured_log(
            "api_connector_files_listed",
            provider=provider_id,
            user_id=user.id,
            count=len(payload["files"]),
            search=_preview_text(search, 80) if search else None,
            next_cursor=payload.get("next_cursor"),
        )
        return payload
    except HTTPException:
        raise
    except Exception as e:
        emit_structured_log(
            "api_connector_files_error",
            level="error",
            provider=provider_id,
            error=str(e),
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/connectors/{provider_id}/watch-targets", response_model=CloudWatchTargetListResponse)
def get_connector_watch_targets(
    provider_id: str,
    token: str = Depends(oauth2_scheme),
):
    try:
        _, user = _get_authenticated_user(token)
        service_client = get_supabase_service_client()
        connection = get_user_oauth_connection(user.id, provider_id, service_client)
        if not connection or connection.get("status") != "active":
            raise HTTPException(status_code=404, detail="No existe una conexión activa para este proveedor.")

        targets = list_user_watch_targets(
            user_id=user.id,
            provider_id=provider_id,
            service_client=service_client,
        )
        emit_structured_log(
            "api_watch_targets_fetched",
            provider=provider_id,
            user_id=user.id,
            count=len(targets),
        )
        return {
            "provider": provider_id,
            "targets": targets,
        }
    except HTTPException:
        raise
    except Exception as e:
        emit_structured_log(
            "api_watch_targets_error",
            level="error",
            provider=provider_id,
            error=str(e),
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/connectors/{provider_id}/watch-targets", response_model=CloudWatchTargetResponse)
def create_connector_watch_target(
    provider_id: str,
    request_body: CloudWatchTargetRequest,
    token: str = Depends(oauth2_scheme),
):
    try:
        _, user = _get_authenticated_user(token)
        service_client = get_supabase_service_client()
        connection = get_user_oauth_connection(user.id, provider_id, service_client)
        if not connection or connection.get("status") != "active":
            raise HTTPException(status_code=404, detail="No existe una conexión activa para este proveedor.")

        remote_item = get_provider_remote_file(
            provider_id,
            connection_row=connection,
            service_client=service_client,
            item_id=request_body.item_id,
        )
        watch_target = upsert_watch_target(
            user_id=user.id,
            provider_id=provider_id,
            connection_row=connection,
            remote_item=remote_item,
            service_client=service_client,
        )
        if provider_id == "google_drive":
            ensure_google_drive_watch_contract(
                user_id=user.id,
                connection_row=connection,
                service_client=service_client,
            )
        emit_structured_log(
            "api_watch_target_created",
            provider=provider_id,
            user_id=user.id,
            watch_target_id=watch_target.get("id"),
            target_id=request_body.item_id,
        )
        return watch_target
    except HTTPException:
        raise
    except Exception as e:
        emit_structured_log(
            "api_watch_target_create_error",
            level="error",
            provider=provider_id,
            item_id=getattr(request_body, "item_id", None),
            error=str(e),
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/connectors/google-drive/webhook", include_in_schema=False)
async def handle_google_drive_webhook(request: Request):
    service_client = get_supabase_service_client()
    channel_id = request.headers.get("x-goog-channel-id", "").strip()
    channel_token = request.headers.get("x-goog-channel-token", "").strip() or None
    resource_id = request.headers.get("x-goog-resource-id", "").strip() or None
    resource_state = request.headers.get("x-goog-resource-state", "").strip() or None
    message_number = request.headers.get("x-goog-message-number", "").strip() or None

    emit_structured_log(
        "google_drive_webhook_received",
        channel_id=channel_id or None,
        resource_id=resource_id,
        resource_state=resource_state,
        message_number=message_number,
    )

    if not channel_id:
        emit_structured_log(
            "google_drive_webhook_ignored",
            level="warning",
            reason="missing_channel_id",
            resource_state=resource_state,
        )
        return Response(status_code=200)

    connection = _find_google_connection_by_channel(
        service_client=service_client,
        channel_id=channel_id,
        channel_token=channel_token,
    )
    if not connection:
        emit_structured_log(
            "google_drive_webhook_ignored",
            level="warning",
            reason="unknown_channel",
            channel_id=channel_id,
            resource_state=resource_state,
        )
        return Response(status_code=200)

    google_changes = _safe_google_changes_contract(connection)
    channel = _safe_dict(google_changes.get("channel"))
    expected_resource_id = str(channel.get("resource_id") or "").strip() or None
    if resource_id and expected_resource_id and resource_id != expected_resource_id:
        emit_structured_log(
            "google_drive_webhook_ignored",
            level="warning",
            reason="resource_id_mismatch",
            user_id=connection.get("user_id"),
            connection_id=connection.get("id"),
            channel_id=channel_id,
            resource_id=resource_id,
            expected_resource_id=expected_resource_id,
        )
        return Response(status_code=200)

    try:
        metadata = _safe_connection_metadata(connection)
        watchdog_metadata = _safe_dict(metadata.get("watchdog"))
        google_changes = _safe_google_changes_contract(connection)
        updated_metadata = {
            **metadata,
            "watchdog": {
                **watchdog_metadata,
                "google_changes": {
                    **google_changes,
                    "last_webhook_received_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "last_webhook_resource_state": resource_state,
                    "last_webhook_message_number": message_number,
                },
            },
        }
        service_client.table("cloud_oauth_connections").update({
            "metadata": updated_metadata,
        }).eq("id", connection["id"]).execute()
        emit_structured_log(
            "google_drive_webhook_processed",
            user_id=connection.get("user_id"),
            connection_id=connection.get("id"),
            channel_id=channel_id,
            resource_state=resource_state,
            changed_count=None,
            removed_count=None,
        )
    except Exception as exc:
        emit_structured_log(
            "google_drive_webhook_error",
            level="error",
            user_id=connection.get("user_id"),
            connection_id=connection.get("id"),
            channel_id=channel_id,
            resource_state=resource_state,
            error=str(exc),
        )

    return Response(status_code=200)


@router.delete("/connectors/{provider_id}/watch-targets/{watch_target_id}", status_code=200)
def delete_connector_watch_target(
    provider_id: str,
    watch_target_id: str,
    token: str = Depends(oauth2_scheme),
):
    try:
        _, user = _get_authenticated_user(token)
        service_client = get_supabase_service_client()
        connection = get_user_oauth_connection(user.id, provider_id, service_client)
        if not connection or connection.get("status") != "active":
            raise HTTPException(status_code=404, detail="No existe una conexión activa para este proveedor.")

        deleted = deactivate_watch_target(
            user_id=user.id,
            provider_id=provider_id,
            watch_target_id=watch_target_id,
            service_client=service_client,
        )
        if not deleted:
            raise HTTPException(status_code=404, detail="No existe un watch target activo con ese ID.")

        emit_structured_log(
            "api_watch_target_deleted",
            provider=provider_id,
            user_id=user.id,
            watch_target_id=watch_target_id,
        )
        return {"status": "success", "watch_target_id": watch_target_id}
    except HTTPException:
        raise
    except Exception as e:
        emit_structured_log(
            "api_watch_target_delete_error",
            level="error",
            provider=provider_id,
            watch_target_id=watch_target_id,
            error=str(e),
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/connectors/watchdog/poll", response_model=CloudWatchdogPollResponse)
def poll_connector_watchdog(
    request_body: CloudWatchdogPollRequest,
    token: str = Depends(oauth2_scheme),
):
    try:
        _, user = _get_authenticated_user(token)
        service_client = get_supabase_service_client()
        provider_id = request_body.provider.strip() if request_body.provider else None

        poll_payload = poll_user_watch_targets(
            user_id=user.id,
            provider_id=provider_id,
            service_client=service_client,
        )
        auto_sync_candidates = collect_pending_auto_sync_candidates(
            user_id=user.id,
            provider_id=provider_id,
            service_client=service_client,
        )
        auto_sync_enqueued_count = 0
        auto_sync_skipped_count = 0
        auto_sync_dispatch_failed_count = 0

        if settings.CONNECTOR_AUTO_SYNC_ENABLED and auto_sync_candidates:
            try:
                auto_sync_summary = enqueue_cloud_sync_jobs_for_watchdog_changes(
                    user_id=user.id,
                    changes=auto_sync_candidates,
                    service_client=service_client,
                    trigger_source="poll",
                )
                auto_sync_enqueued_count = int(auto_sync_summary["queued_count"])
                auto_sync_skipped_count = int(auto_sync_summary["skipped_count"])

                for queued_job in auto_sync_summary["queued_jobs"]:
                    dispatched, dispatch_mode_or_error = _dispatch_cloud_sync_job_with_fallback(
                        str(queued_job["id"])
                    )
                    if not dispatched:
                        auto_sync_dispatch_failed_count += 1
                        mark_cloud_sync_job_dispatch_failed(
                            job_id=str(queued_job["id"]),
                            error_summary=str(dispatch_mode_or_error or "unknown_dispatch_error"),
                            service_client=service_client,
                        )
                    else:
                        emit_structured_log(
                            "cloud_sync_job_dispatched",
                            user_id=user.id,
                            provider=provider_id,
                            cloud_sync_job_id=str(queued_job["id"]),
                            dispatch_mode=str(dispatch_mode_or_error or "unknown"),
                        )

                emit_structured_log(
                    "api_watchdog_auto_sync_dispatch_completed",
                    user_id=user.id,
                    provider=provider_id,
                    auto_sync_candidate_count=len(auto_sync_candidates),
                    auto_sync_enqueued_count=auto_sync_enqueued_count,
                    auto_sync_skipped_count=auto_sync_skipped_count,
                    auto_sync_dispatch_failed_count=auto_sync_dispatch_failed_count,
                )
            except Exception as auto_sync_error:
                emit_structured_log(
                    "api_watchdog_auto_sync_dispatch_error",
                    level="warning",
                    user_id=user.id,
                    provider=provider_id,
                    error=str(auto_sync_error),
                )

        poll_payload["auto_sync_enqueued_count"] = auto_sync_enqueued_count
        poll_payload["auto_sync_skipped_count"] = auto_sync_skipped_count
        poll_payload["auto_sync_dispatch_failed_count"] = auto_sync_dispatch_failed_count
        emit_structured_log(
            "api_watchdog_poll_completed",
            user_id=user.id,
            provider=provider_id,
            checked_count=poll_payload["checked_count"],
            new_change_count=poll_payload["new_change_count"],
            skipped_contract_count=poll_payload["skipped_contract_count"],
            error_count=poll_payload["error_count"],
            auto_sync_enqueued_count=auto_sync_enqueued_count,
            auto_sync_skipped_count=auto_sync_skipped_count,
            auto_sync_dispatch_failed_count=auto_sync_dispatch_failed_count,
        )
        return poll_payload
    except HTTPException:
        raise
    except Exception as e:
        emit_structured_log(
            "api_watchdog_poll_error",
            level="error",
            provider=getattr(request_body, "provider", None),
            error=str(e),
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/connectors/{provider_id}/import", response_model=CloudRemoteImportResponse)
def import_connector_file(
    provider_id: str,
    request_body: CloudRemoteImportRequest,
    token: str = Depends(oauth2_scheme),
):
    try:
        _, user = _get_authenticated_user(token)
        service_client = get_supabase_service_client()
        connection = get_user_oauth_connection(user.id, provider_id, service_client)
        if not connection or connection.get("status") != "active":
            raise HTTPException(status_code=404, detail="No existe una conexión activa para este proveedor.")

        materialized_import = materialize_cloud_import(
            user_id=user.id,
            provider_id=provider_id,
            item_id=request_body.item_id,
            service_client=service_client,
            connection_row=connection,
        )

        emit_structured_log(
            "api_connector_file_imported",
            provider=provider_id,
            user_id=user.id,
            uploaded_file_id=materialized_import["uploaded_file_id"],
            file_name=materialized_import["file_name"],
            source_type=materialized_import["source_type"],
            import_action=materialized_import.get("import_action"),
        )
        track_connector_file_imported(
            provider=provider_id,
            user_id=user.id,
            uploaded_file_id=materialized_import["uploaded_file_id"],
            source_type=materialized_import["source_type"],
        )
        return {
            "provider": provider_id,
            "uploaded_file_id": materialized_import["uploaded_file_id"],
            "file_name": materialized_import["file_name"],
            "storage_path": materialized_import["storage_path"],
            "source_type": materialized_import["source_type"],
        }
    except HTTPException:
        raise
    except Exception as e:
        emit_structured_log(
            "api_connector_file_import_error",
            level="error",
            provider=provider_id,
            item_id=getattr(request_body, "item_id", None),
            error=str(e),
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/connectors/{provider_id}/connection", status_code=200)
def disconnect_connector_provider(
    provider_id: str,
    token: str = Depends(oauth2_scheme),
):
    try:
        _, user = _get_authenticated_user(token)
        service_client = get_supabase_service_client()
        connection = get_user_oauth_connection(user.id, provider_id, service_client)
        if not connection:
            raise HTTPException(status_code=404, detail="No existe conexión activa para este proveedor.")

        if provider_id == "google_drive":
            google_changes = _safe_google_changes_contract(connection)
            channel = _safe_dict(google_changes.get("channel"))
            if channel.get("id") and channel.get("resource_id"):
                _google_drive_stop_channel(
                    connection,
                    channel_id=str(channel.get("id")),
                    resource_id=str(channel.get("resource_id")),
                )

        service_client.table("cloud_watch_targets").update({
            "is_active": False,
        }).eq("user_id", user.id).eq("provider", provider_id).execute()

        service_client.table("cloud_oauth_connections").delete() \
            .eq("user_id", user.id) \
            .eq("provider", provider_id) \
            .execute()

        emit_structured_log(
            "api_connector_disconnected",
            provider=provider_id,
            user_id=user.id,
            previous_connection_id=connection.get("id"),
        )
        return {"status": "success", "provider": provider_id}
    except HTTPException:
        raise
    except Exception as e:
        emit_structured_log(
            "api_connector_disconnect_error",
            level="error",
            provider=provider_id,
            error=str(e),
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/auth/google", response_model=OAuthAuthorizationResponse)
def start_google_oauth(
    redirect_to: Optional[str] = Query(default=None),
    token: str = Depends(oauth2_scheme),
):
    try:
        return _start_oauth_flow("google_drive", token, redirect_to=redirect_to)
    except HTTPException:
        raise
    except Exception as e:
        emit_structured_log(
            "oauth_flow_start_error",
            level="error",
            provider="google_drive",
            error=str(e),
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/auth/microsoft", response_model=OAuthAuthorizationResponse)
def start_microsoft_oauth(
    redirect_to: Optional[str] = Query(default=None),
    token: str = Depends(oauth2_scheme),
):
    try:
        return _start_oauth_flow("onedrive", token, redirect_to=redirect_to)
    except HTTPException:
        raise
    except Exception as e:
        emit_structured_log(
            "oauth_flow_start_error",
            level="error",
            provider="onedrive",
            error=str(e),
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/auth/google/callback", include_in_schema=False)
def google_oauth_callback(
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    error_description: Optional[str] = None,
):
    return _handle_oauth_callback(
        "google",
        code=code,
        state=state,
        error=error,
        error_description=error_description,
    )


@router.get("/auth/microsoft/callback", include_in_schema=False)
def microsoft_oauth_callback(
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    error_description: Optional[str] = None,
):
    return _handle_oauth_callback(
        "microsoft",
        code=code,
        state=state,
        error=error,
        error_description=error_description,
    )
