from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from app.core.structured_logging import emit_structured_log
from app.services.cloud_oauth import download_provider_remote_file, get_user_oauth_connection
from app.services.cloud_watchdog import _build_remote_snapshot, link_watch_target_to_uploaded_file
from app.services.governance import resolve_user_team_scope


DASH_UPLOADS_BUCKET = "dash-uploads"


def _now_storage_stamp() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _build_storage_path(*, user_id: str, file_name: str) -> str:
    return f"{user_id}/{_now_storage_stamp()}_{str(file_name or '').strip()}"


def _get_active_watch_target_for_item(
    *,
    user_id: str,
    provider_id: str,
    item_id: str,
    service_client: Any,
) -> dict[str, Any] | None:
    response = service_client.table("cloud_watch_targets") \
        .select("*") \
        .eq("user_id", user_id) \
        .eq("provider", provider_id) \
        .eq("target_id", item_id) \
        .eq("is_active", True) \
        .limit(1) \
        .execute()
    if not response.data:
        return None
    return response.data[0]


def _get_watch_target_by_id(
    *,
    watch_target_id: str,
    service_client: Any,
) -> dict[str, Any] | None:
    normalized_id = str(watch_target_id or "").strip()
    if not normalized_id:
        return None

    response = service_client.table("cloud_watch_targets") \
        .select("*") \
        .eq("id", normalized_id) \
        .limit(1) \
        .execute()
    if not response.data:
        return None
    return response.data[0]


def _get_linked_uploaded_file(
    *,
    user_id: str,
    linked_file_id: str | None,
    service_client: Any,
) -> dict[str, Any] | None:
    normalized_id = str(linked_file_id or "").strip()
    if not normalized_id:
        return None

    response = service_client.table("uploaded_files") \
        .select("id, user_id, team_id, file_name, storage_path, created_at") \
        .eq("id", normalized_id) \
        .eq("user_id", user_id) \
        .limit(1) \
        .execute()
    if not response.data:
        return None
    return response.data[0]


def _create_uploaded_file_record(
    *,
    user_id: str,
    file_name: str,
    file_bytes: bytes,
    mime_type: str,
    service_client: Any,
) -> dict[str, Any]:
    team_id = resolve_user_team_scope(user_id=user_id, service_client=service_client)
    if not team_id:
        raise ValueError("No se encontró team_id para el usuario autenticado.")

    storage_path = _build_storage_path(user_id=user_id, file_name=file_name)
    storage_client = service_client.storage.from_(DASH_UPLOADS_BUCKET)
    storage_client.upload(
        storage_path,
        file_bytes,
        {"content-type": mime_type},
    )

    try:
        insert_response = service_client.table("uploaded_files").insert({
            "user_id": user_id,
            "team_id": team_id,
            "file_name": file_name,
            "storage_path": storage_path,
        }).execute()
    except Exception:
        storage_client.remove([storage_path])
        raise

    if not insert_response.data:
        storage_client.remove([storage_path])
        raise ValueError("No se pudo persistir el archivo importado.")
    return insert_response.data[0]


def _refresh_uploaded_file_record(
    *,
    uploaded_file: dict[str, Any],
    file_name: str,
    file_bytes: bytes,
    mime_type: str,
    service_client: Any,
) -> dict[str, Any]:
    current_row = deepcopy(uploaded_file)
    old_storage_path = str(current_row.get("storage_path") or "").strip()
    new_storage_path = _build_storage_path(
        user_id=str(current_row.get("user_id") or "").strip(),
        file_name=file_name,
    )

    storage_client = service_client.storage.from_(DASH_UPLOADS_BUCKET)
    storage_client.upload(
        new_storage_path,
        file_bytes,
        {
            "content-type": mime_type,
            "upsert": "true",
        },
    )

    try:
        update_response = service_client.table("uploaded_files").update({
            "file_name": file_name,
            "storage_path": new_storage_path,
        }).eq("id", current_row["id"]).eq("user_id", current_row["user_id"]).execute()
    except Exception:
        storage_client.remove([new_storage_path])
        raise

    if update_response.data:
        refreshed_row = update_response.data[0]
    else:
        refreshed_row = {
            **current_row,
            "file_name": file_name,
            "storage_path": new_storage_path,
        }

    if old_storage_path and old_storage_path != new_storage_path:
        try:
            storage_client.remove([old_storage_path])
        except Exception as exc:
            emit_structured_log(
                "cloud_import_old_blob_cleanup_failed",
                level="warning",
                file_id=current_row.get("id"),
                old_storage_path=old_storage_path,
                new_storage_path=new_storage_path,
                error=str(exc),
            )

    return refreshed_row


def _refresh_watch_target_remote_snapshot(
    *,
    watch_target_id: str,
    remote_item: dict[str, Any],
    service_client: Any,
) -> None:
    current_watch_target = _get_watch_target_by_id(
        watch_target_id=watch_target_id,
        service_client=service_client,
    )
    if not current_watch_target:
        return

    metadata = _safe_dict(current_watch_target.get("metadata"))
    watchdog_state = _safe_dict(metadata.get("watchdog"))
    remote_snapshot = _build_remote_snapshot(remote_item)
    updated_metadata = {
        **metadata,
        "remote_snapshot": remote_snapshot,
        "watchdog": {
            **watchdog_state,
            "last_seen_modified_at": remote_snapshot.get("modified_at"),
            "last_seen_size_bytes": remote_snapshot.get("size_bytes"),
            "last_error": None,
        },
    }
    service_client.table("cloud_watch_targets").update({
        "target_name": remote_item.get("name") or current_watch_target.get("target_name"),
        "metadata": updated_metadata,
    }).eq("id", current_watch_target["id"]).execute()


def materialize_cloud_import(
    *,
    user_id: str,
    provider_id: str,
    item_id: str,
    service_client: Any,
    connection_row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    active_connection = connection_row or get_user_oauth_connection(user_id, provider_id, service_client)
    if not active_connection or active_connection.get("status") != "active":
        raise ValueError("No existe una conexión activa para este proveedor.")

    remote_payload = download_provider_remote_file(
        provider_id,
        connection_row=active_connection,
        service_client=service_client,
        item_id=item_id,
    )
    file_name = str(remote_payload["file_name"])
    mime_type = str(remote_payload.get("mime_type") or "application/octet-stream")
    file_bytes = remote_payload["bytes"]

    watch_target = _get_active_watch_target_for_item(
        user_id=user_id,
        provider_id=provider_id,
        item_id=item_id,
        service_client=service_client,
    )
    linked_uploaded_file = _get_linked_uploaded_file(
        user_id=user_id,
        linked_file_id=(watch_target or {}).get("linked_file_id"),
        service_client=service_client,
    )

    if linked_uploaded_file:
        uploaded_file = _refresh_uploaded_file_record(
            uploaded_file=linked_uploaded_file,
            file_name=file_name,
            file_bytes=file_bytes,
            mime_type=mime_type,
            service_client=service_client,
        )
        import_action = "refreshed_existing_file"
    else:
        uploaded_file = _create_uploaded_file_record(
            user_id=user_id,
            file_name=file_name,
            file_bytes=file_bytes,
            mime_type=mime_type,
            service_client=service_client,
        )
        import_action = "created_new_file"

    if watch_target:
        link_watch_target_to_uploaded_file(
            user_id=user_id,
            provider_id=provider_id,
            target_id=item_id,
            uploaded_file_id=uploaded_file["id"],
            service_client=service_client,
        )
        _refresh_watch_target_remote_snapshot(
            watch_target_id=str(watch_target.get("id") or ""),
            remote_item=_safe_dict(remote_payload.get("remote_item")),
            service_client=service_client,
        )

    emit_structured_log(
        "cloud_import_materialized",
        user_id=user_id,
        provider=provider_id,
        target_id=item_id,
        watch_target_id=(watch_target or {}).get("id"),
        uploaded_file_id=uploaded_file.get("id"),
        storage_path=uploaded_file.get("storage_path"),
        import_action=import_action,
        source_type=remote_payload.get("source_type"),
    )

    return {
        "provider": provider_id,
        "uploaded_file_id": uploaded_file["id"],
        "file_name": uploaded_file["file_name"],
        "storage_path": uploaded_file["storage_path"],
        "source_type": remote_payload["source_type"],
        "watch_target_id": (watch_target or {}).get("id"),
        "import_action": import_action,
        "uploaded_file": uploaded_file,
        "remote_item": _safe_dict(remote_payload.get("remote_item")),
    }


def sync_uploaded_file_from_pending_watch_target(
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
    metadata = _safe_dict(watch_target.get("metadata"))
    watchdog_state = _safe_dict(metadata.get("watchdog"))
    sync_state = str(watchdog_state.get("sync_state") or "").strip().lower()
    pending_change = bool(watchdog_state.get("pending_change"))

    if sync_state != "pending_sync" and not pending_change:
        return uploaded_file

    provider_id = str(watch_target.get("provider") or "").strip()
    target_id = str(watch_target.get("target_id") or "").strip()
    if not provider_id or not target_id:
        return uploaded_file

    result = materialize_cloud_import(
        user_id=user_id,
        provider_id=provider_id,
        item_id=target_id,
        service_client=service_client,
    )
    return result["uploaded_file"]
