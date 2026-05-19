from __future__ import annotations

from datetime import datetime, timezone
from time import perf_counter
from typing import Any

from app.core.structured_logging import emit_structured_log
from app.services.enterprise_telemetry import track_cloud_sync_job_completed, track_cloud_sync_job_queued
from app.services.governance import resolve_user_team_scope


ACTIVE_SYNC_JOB_STATUSES = {"queued", "running"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _normalize_text(value: Any) -> str:
    return str(value or "").replace("\x00", "").strip()


def _truncate_text(value: Any, limit: int = 240) -> str:
    return _normalize_text(value)[:limit]


def _build_revision_signature(*, target_id: str, metadata: dict[str, Any]) -> str:
    watchdog_state = _safe_dict(metadata.get("watchdog"))
    notified_signature = _normalize_text(watchdog_state.get("last_notified_change_signature"))
    if notified_signature:
        return notified_signature

    remote_snapshot = _safe_dict(metadata.get("remote_snapshot"))
    signature_parts = [
        _normalize_text(target_id),
        _normalize_text(remote_snapshot.get("modified_at")),
        _normalize_text(remote_snapshot.get("etag")),
        _normalize_text(remote_snapshot.get("ctag")),
        _normalize_text(remote_snapshot.get("size_bytes")),
    ]
    return ":".join(signature_parts)


def _is_watch_target_pending_sync(row: dict[str, Any]) -> bool:
    metadata = _safe_dict(row.get("metadata"))
    watchdog_state = _safe_dict(metadata.get("watchdog"))
    sync_state = _normalize_text(watchdog_state.get("sync_state")).lower()
    pending_change = bool(watchdog_state.get("pending_change"))
    return sync_state == "pending_sync" or pending_change


def _get_watch_target_by_id(
    *,
    user_id: str,
    watch_target_id: str,
    service_client: Any,
) -> dict[str, Any] | None:
    response = service_client.table("cloud_watch_targets") \
        .select("*") \
        .eq("user_id", user_id) \
        .eq("id", watch_target_id) \
        .limit(1) \
        .execute()
    if not response.data:
        return None
    return response.data[0]


def collect_pending_auto_sync_candidates(
    *,
    user_id: str,
    service_client: Any,
    provider_id: str | None = None,
) -> list[dict[str, Any]]:
    query = service_client.table("cloud_watch_targets") \
        .select("*") \
        .eq("user_id", user_id) \
        .eq("is_active", True)
    if provider_id:
        query = query.eq("provider", provider_id)
    response = query.execute()

    candidates: list[dict[str, Any]] = []
    for row in response.data or []:
        if not _normalize_text(row.get("linked_file_id")):
            continue
        if not _is_watch_target_pending_sync(row):
            continue

        metadata = _safe_dict(row.get("metadata"))
        watchdog_state = _safe_dict(metadata.get("watchdog"))
        candidates.append({
            "watch_target_id": row.get("id"),
            "provider": row.get("provider"),
            "target_id": row.get("target_id"),
            "target_name": row.get("target_name"),
            "linked_file_id": row.get("linked_file_id"),
            "change_summary": watchdog_state.get("pending_change_summary"),
            "changed_at": watchdog_state.get("last_change_detected_at"),
            "requires_reimport": True,
        })
    return candidates


def _get_sync_job_by_id(
    *,
    job_id: str,
    service_client: Any,
) -> dict[str, Any] | None:
    response = service_client.table("cloud_sync_jobs") \
        .select("*") \
        .eq("id", job_id) \
        .limit(1) \
        .execute()
    if not response.data:
        return None
    return response.data[0]


def _get_existing_sync_job(
    *,
    watch_target_id: str,
    revision_signature: str,
    service_client: Any,
) -> dict[str, Any] | None:
    response = service_client.table("cloud_sync_jobs") \
        .select("*") \
        .eq("watch_target_id", watch_target_id) \
        .eq("revision_signature", revision_signature) \
        .limit(1) \
        .execute()
    if not response.data:
        return None
    return response.data[0]


def _get_active_sync_job_for_watch_target(
    *,
    watch_target_id: str,
    service_client: Any,
) -> dict[str, Any] | None:
    for status in ACTIVE_SYNC_JOB_STATUSES:
        response = service_client.table("cloud_sync_jobs") \
            .select("*") \
            .eq("watch_target_id", watch_target_id) \
            .eq("status", status) \
            .limit(1) \
            .execute()
        if response.data:
            return response.data[0]
    return None


def _merge_watchdog_state(
    *,
    watch_target_id: str,
    service_client: Any,
    patch: dict[str, Any],
) -> dict[str, Any] | None:
    current_row = service_client.table("cloud_watch_targets") \
        .select("*") \
        .eq("id", watch_target_id) \
        .limit(1) \
        .execute()
    if not current_row.data:
        return None

    row = current_row.data[0]
    metadata = _safe_dict(row.get("metadata"))
    watchdog_state = _safe_dict(metadata.get("watchdog"))
    updated_metadata = {
        **metadata,
        "watchdog": {
            **watchdog_state,
            **patch,
        },
    }
    updated_rows = service_client.table("cloud_watch_targets").update({
        "metadata": updated_metadata,
    }).eq("id", watch_target_id).execute()
    if not updated_rows.data:
        return None
    return updated_rows.data[0]


def _mark_watch_target_auto_sync_queued(
    *,
    watch_target_id: str,
    job_id: str,
    revision_signature: str,
    trigger_source: str,
    service_client: Any,
) -> None:
    queued_at = _now_iso()
    _merge_watchdog_state(
        watch_target_id=watch_target_id,
        service_client=service_client,
        patch={
            "auto_sync_enabled": True,
            "auto_sync_status": "queued",
            "auto_sync_job_id": job_id,
            "last_auto_sync_job_id": job_id,
            "auto_sync_revision_signature": revision_signature,
            "auto_sync_trigger_source": trigger_source,
            "auto_sync_queued_at": queued_at,
            "last_auto_sync_error": None,
        },
    )


def _mark_watch_target_auto_sync_running(
    *,
    watch_target_id: str,
    job_id: str,
    service_client: Any,
) -> None:
    _merge_watchdog_state(
        watch_target_id=watch_target_id,
        service_client=service_client,
        patch={
            "auto_sync_enabled": True,
            "auto_sync_status": "running",
            "auto_sync_job_id": job_id,
            "last_auto_sync_job_id": job_id,
            "auto_sync_started_at": _now_iso(),
            "last_auto_sync_error": None,
        },
    )


def _mark_watch_target_auto_sync_succeeded(
    *,
    watch_target_id: str,
    job_id: str,
    service_client: Any,
) -> None:
    _merge_watchdog_state(
        watch_target_id=watch_target_id,
        service_client=service_client,
        patch={
            "auto_sync_enabled": True,
            "auto_sync_status": "synced",
            "auto_sync_job_id": None,
            "last_auto_sync_job_id": job_id,
            "last_auto_sync_at": _now_iso(),
            "last_auto_sync_error": None,
        },
    )


def _mark_watch_target_auto_sync_failed(
    *,
    watch_target_id: str,
    job_id: str,
    error_summary: str,
    service_client: Any,
) -> None:
    _merge_watchdog_state(
        watch_target_id=watch_target_id,
        service_client=service_client,
        patch={
            "auto_sync_enabled": True,
            "auto_sync_status": "manual_attention",
            "auto_sync_job_id": None,
            "last_auto_sync_job_id": job_id,
            "last_auto_sync_at": _now_iso(),
            "last_auto_sync_error": error_summary,
            "last_error": error_summary,
        },
    )


def enqueue_cloud_sync_jobs_for_watchdog_changes(
    *,
    user_id: str,
    changes: list[dict[str, Any]],
    service_client: Any,
    trigger_source: str = "poll",
) -> dict[str, Any]:
    team_id = resolve_user_team_scope(user_id=user_id, service_client=service_client)
    if not team_id:
        emit_structured_log(
            "cloud_sync_job_enqueue_skipped_missing_team_scope",
            level="warning",
            user_id=user_id,
            trigger_source=trigger_source,
        )
        return {
            "queued_jobs": [],
            "queued_count": 0,
            "skipped_count": len(changes),
            "skipped_unlinked_count": 0,
            "skipped_active_job_count": 0,
            "skipped_duplicate_revision_count": 0,
        }

    queued_jobs: list[dict[str, Any]] = []
    queued_count = 0
    skipped_count = 0
    skipped_unlinked_count = 0
    skipped_active_job_count = 0
    skipped_duplicate_revision_count = 0

    for change in changes:
        watch_target_id = _normalize_text(change.get("watch_target_id"))
        if not watch_target_id:
            skipped_count += 1
            continue

        watch_target = _get_watch_target_by_id(
            user_id=user_id,
            watch_target_id=watch_target_id,
            service_client=service_client,
        )
        if not watch_target or not bool(watch_target.get("is_active")):
            skipped_count += 1
            continue

        linked_file_id = _normalize_text(watch_target.get("linked_file_id"))
        if not linked_file_id:
            skipped_count += 1
            skipped_unlinked_count += 1
            continue

        metadata = _safe_dict(watch_target.get("metadata"))
        revision_signature = _build_revision_signature(
            target_id=_normalize_text(watch_target.get("target_id")),
            metadata=metadata,
        )
        if not revision_signature:
            skipped_count += 1
            continue

        active_job = _get_active_sync_job_for_watch_target(
            watch_target_id=watch_target_id,
            service_client=service_client,
        )
        if active_job:
            skipped_count += 1
            skipped_active_job_count += 1
            continue

        existing_job = _get_existing_sync_job(
            watch_target_id=watch_target_id,
            revision_signature=revision_signature,
            service_client=service_client,
        )
        if existing_job:
            skipped_count += 1
            skipped_duplicate_revision_count += 1
            continue

        insert_response = service_client.table("cloud_sync_jobs").insert({
            "team_id": team_id,
            "user_id": user_id,
            "watch_target_id": watch_target_id,
            "linked_file_id": linked_file_id,
            "provider": _normalize_text(watch_target.get("provider")),
            "target_id": _normalize_text(watch_target.get("target_id")),
            "revision_signature": revision_signature,
            "trigger_source": trigger_source,
            "status": "queued",
            "attempt_count": 0,
            "metadata": {
                "change_summary": change.get("change_summary"),
                "target_name": watch_target.get("target_name"),
            },
        }).execute()
        if not insert_response.data:
            skipped_count += 1
            continue

        job_row = insert_response.data[0]
        _mark_watch_target_auto_sync_queued(
            watch_target_id=watch_target_id,
            job_id=job_row["id"],
            revision_signature=revision_signature,
            trigger_source=trigger_source,
            service_client=service_client,
        )
        emit_structured_log(
            "cloud_sync_job_enqueued",
            user_id=user_id,
            watch_target_id=watch_target_id,
            cloud_sync_job_id=job_row["id"],
            revision_signature=revision_signature,
            provider=job_row.get("provider"),
            linked_file_id=linked_file_id,
            trigger_source=trigger_source,
        )
        track_cloud_sync_job_queued(
            user_id=user_id,
            team_id=team_id,
            job_id=job_row["id"],
            provider=job_row.get("provider"),
            watch_target_id=watch_target_id,
            linked_file_id=linked_file_id,
            trigger_source=trigger_source,
        )
        queued_jobs.append(job_row)
        queued_count += 1

    return {
        "queued_jobs": queued_jobs,
        "queued_count": queued_count,
        "skipped_count": skipped_count,
        "skipped_unlinked_count": skipped_unlinked_count,
        "skipped_active_job_count": skipped_active_job_count,
        "skipped_duplicate_revision_count": skipped_duplicate_revision_count,
    }


def start_cloud_sync_job(
    *,
    job_id: str,
    service_client: Any,
) -> dict[str, Any]:
    current_job = _get_sync_job_by_id(job_id=job_id, service_client=service_client)
    if not current_job:
        raise ValueError("No se encontró el cloud sync job solicitado.")
    if _normalize_text(current_job.get("status")) != "queued":
        return current_job

    started_at = _now_iso()
    updated_rows = service_client.table("cloud_sync_jobs").update({
        "status": "running",
        "attempt_count": int(current_job.get("attempt_count") or 0) + 1,
        "started_at": started_at,
        "error_summary": None,
    }).eq("id", job_id).execute()
    running_job = updated_rows.data[0] if updated_rows.data else {
        **current_job,
        "status": "running",
        "attempt_count": int(current_job.get("attempt_count") or 0) + 1,
        "started_at": started_at,
        "error_summary": None,
    }
    if running_job.get("watch_target_id"):
        _mark_watch_target_auto_sync_running(
            watch_target_id=str(running_job["watch_target_id"]),
            job_id=job_id,
            service_client=service_client,
        )
    emit_structured_log(
        "cloud_sync_job_started",
        user_id=running_job.get("user_id"),
        cloud_sync_job_id=job_id,
        watch_target_id=running_job.get("watch_target_id"),
        provider=running_job.get("provider"),
        linked_file_id=running_job.get("linked_file_id"),
    )
    return running_job


def mark_cloud_sync_job_succeeded(
    *,
    job_id: str,
    uploaded_file_id: str,
    storage_path: str,
    service_client: Any,
    duration_ms: int,
) -> dict[str, Any] | None:
    current_job = _get_sync_job_by_id(job_id=job_id, service_client=service_client)
    if not current_job:
        return None

    metadata = _safe_dict(current_job.get("metadata"))
    metadata.update({
        "result_uploaded_file_id": uploaded_file_id,
        "result_storage_path": storage_path,
    })
    updated_rows = service_client.table("cloud_sync_jobs").update({
        "status": "succeeded",
        "finished_at": _now_iso(),
        "error_summary": None,
        "metadata": metadata,
    }).eq("id", job_id).execute()
    completed_job = updated_rows.data[0] if updated_rows.data else {
        **current_job,
        "status": "succeeded",
        "finished_at": _now_iso(),
        "error_summary": None,
        "metadata": metadata,
    }
    if completed_job.get("watch_target_id"):
        _mark_watch_target_auto_sync_succeeded(
            watch_target_id=str(completed_job["watch_target_id"]),
            job_id=job_id,
            service_client=service_client,
        )
    emit_structured_log(
        "cloud_sync_job_succeeded",
        user_id=completed_job.get("user_id"),
        cloud_sync_job_id=job_id,
        watch_target_id=completed_job.get("watch_target_id"),
        provider=completed_job.get("provider"),
        linked_file_id=completed_job.get("linked_file_id"),
        duration_ms=duration_ms,
    )
    track_cloud_sync_job_completed(
        user_id=completed_job.get("user_id"),
        team_id=completed_job.get("team_id"),
        job_id=job_id,
        provider=completed_job.get("provider"),
        watch_target_id=completed_job.get("watch_target_id"),
        linked_file_id=completed_job.get("linked_file_id"),
        status="succeeded",
        duration_ms=duration_ms,
    )
    return completed_job


def mark_cloud_sync_job_failed(
    *,
    job_id: str,
    error_summary: str,
    service_client: Any,
    duration_ms: int | None = None,
) -> dict[str, Any] | None:
    current_job = _get_sync_job_by_id(job_id=job_id, service_client=service_client)
    if not current_job:
        return None

    normalized_error = _truncate_text(error_summary)
    metadata = _safe_dict(current_job.get("metadata"))
    metadata.update({
        "failure_reason": normalized_error,
    })
    updated_rows = service_client.table("cloud_sync_jobs").update({
        "status": "failed",
        "finished_at": _now_iso(),
        "error_summary": normalized_error,
        "metadata": metadata,
    }).eq("id", job_id).execute()
    failed_job = updated_rows.data[0] if updated_rows.data else {
        **current_job,
        "status": "failed",
        "finished_at": _now_iso(),
        "error_summary": normalized_error,
        "metadata": metadata,
    }
    if failed_job.get("watch_target_id"):
        _mark_watch_target_auto_sync_failed(
            watch_target_id=str(failed_job["watch_target_id"]),
            job_id=job_id,
            error_summary=normalized_error,
            service_client=service_client,
        )
    emit_structured_log(
        "cloud_sync_job_failed",
        level="error",
        user_id=failed_job.get("user_id"),
        cloud_sync_job_id=job_id,
        watch_target_id=failed_job.get("watch_target_id"),
        provider=failed_job.get("provider"),
        linked_file_id=failed_job.get("linked_file_id"),
        error=normalized_error,
        duration_ms=duration_ms,
    )
    track_cloud_sync_job_completed(
        user_id=failed_job.get("user_id"),
        team_id=failed_job.get("team_id"),
        job_id=job_id,
        provider=failed_job.get("provider"),
        watch_target_id=failed_job.get("watch_target_id"),
        linked_file_id=failed_job.get("linked_file_id"),
        status="failed",
        duration_ms=max(int(duration_ms or 0), 0),
    )
    return failed_job


def mark_cloud_sync_job_dispatch_failed(
    *,
    job_id: str,
    error_summary: str,
    service_client: Any,
) -> dict[str, Any] | None:
    return mark_cloud_sync_job_failed(
        job_id=job_id,
        error_summary=f"dispatch_error: {error_summary}",
        service_client=service_client,
        duration_ms=0,
    )


def execute_cloud_sync_job(
    *,
    job_id: str,
    materialize_import_fn: Any,
    service_client: Any,
) -> dict[str, Any]:
    started_job = start_cloud_sync_job(job_id=job_id, service_client=service_client)
    if _normalize_text(started_job.get("status")) != "running":
        return {
            "status": _normalize_text(started_job.get("status")) or "ignored",
            "job_id": job_id,
            "uploaded_file_id": started_job.get("linked_file_id"),
        }

    started = perf_counter()
    try:
        watch_target = _get_watch_target_by_id(
            user_id=str(started_job.get("user_id") or ""),
            watch_target_id=str(started_job.get("watch_target_id") or ""),
            service_client=service_client,
        )
        if not watch_target:
            raise ValueError("No se encontró el watch target asociado al cloud sync job.")
        if not watch_target.get("linked_file_id"):
            raise ValueError("El watch target no está enlazado a un archivo lógico.")

        result = materialize_import_fn(
            user_id=str(started_job.get("user_id") or ""),
            provider_id=str(started_job.get("provider") or ""),
            item_id=str(started_job.get("target_id") or ""),
            service_client=service_client,
        )
        duration_ms = int((perf_counter() - started) * 1000)
        mark_cloud_sync_job_succeeded(
            job_id=job_id,
            uploaded_file_id=str(result.get("uploaded_file_id") or ""),
            storage_path=str(result.get("storage_path") or ""),
            service_client=service_client,
            duration_ms=duration_ms,
        )
        return {
            "status": "succeeded",
            "job_id": job_id,
            "uploaded_file_id": result.get("uploaded_file_id"),
            "storage_path": result.get("storage_path"),
            "duration_ms": duration_ms,
        }
    except Exception as exc:
        duration_ms = int((perf_counter() - started) * 1000)
        mark_cloud_sync_job_failed(
            job_id=job_id,
            error_summary=str(exc),
            service_client=service_client,
            duration_ms=duration_ms,
        )
        raise
