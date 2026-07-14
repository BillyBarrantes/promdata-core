from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import secrets
from typing import Any
from urllib.parse import urlparse
import uuid

import requests

from app.core.config import settings
from app.core.structured_logging import emit_structured_log
from app.services.cloud_oauth import decrypt_oauth_connection_row, get_provider_remote_file, refresh_oauth_connection_tokens

GOOGLE_DRIVE_CHANGES_URL = "https://www.googleapis.com/drive/v3/changes"
GOOGLE_DRIVE_START_PAGE_TOKEN_URL = "https://www.googleapis.com/drive/v3/changes/startPageToken"
GOOGLE_DRIVE_CHANNELS_STOP_URL = "https://www.googleapis.com/drive/v3/channels/stop"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _coerce_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_connection_metadata(connection_row: dict[str, Any]) -> dict[str, Any]:
    return _safe_dict(connection_row.get("metadata"))


def _safe_connection_watchdog(connection_row: dict[str, Any]) -> dict[str, Any]:
    return _safe_dict(_safe_connection_metadata(connection_row).get("watchdog"))


def _safe_google_changes_contract(connection_row: dict[str, Any]) -> dict[str, Any]:
    return _safe_dict(_safe_connection_watchdog(connection_row).get("google_changes"))


def _build_google_drive_webhook_address() -> str:
    configured = str(settings.GOOGLE_DRIVE_WEBHOOK_CALLBACK_URL or "").strip().rstrip("/")
    if configured:
        return configured
    backend_public = str(settings.BACKEND_PUBLIC_URL or "").strip().rstrip("/")
    if not backend_public:
        return ""
    return f"{backend_public}/api/v1/connectors/google-drive/webhook"


def _is_public_https_url(candidate_url: str | None) -> bool:
    if not candidate_url:
        return False
    parsed = urlparse(str(candidate_url).strip())
    if parsed.scheme != "https" or not parsed.netloc:
        return False
    hostname = (parsed.hostname or "").strip().lower()
    return hostname not in {"localhost", "127.0.0.1", "0.0.0.0"}


def _persist_connection_metadata(
    *,
    connection_row: dict[str, Any],
    metadata: dict[str, Any],
    service_client: Any,
) -> dict[str, Any]:
    response = service_client.table("cloud_oauth_connections").update({
        "metadata": metadata,
    }).eq("id", connection_row["id"]).execute()
    if response.data:
        return response.data[0]
    return {
        **connection_row,
        "metadata": metadata,
    }


def _build_google_target_provider_contract(connection_row: dict[str, Any]) -> dict[str, Any]:
    google_changes = _safe_google_changes_contract(connection_row)
    channel = _safe_dict(google_changes.get("channel"))
    callback_url = str(google_changes.get("callback_url") or _build_google_drive_webhook_address() or "").strip()
    webhook_ready = _is_public_https_url(callback_url)
    contract_status = str(google_changes.get("contract_status") or "").strip().lower()
    if not contract_status:
        contract_status = "active" if channel.get("id") else ("pending_registration" if webhook_ready else "polling_only")
    return {
        "mode": "webhook",
        "contract_status": contract_status,
        "start_page_token": google_changes.get("page_token"),
        "channel_id": channel.get("id"),
        "channel_expiration": channel.get("expiration"),
        "fallback_polling_active": not bool(channel.get("id")),
    }


def _merge_google_provider_contract_into_target_metadata(
    *,
    metadata: dict[str, Any],
    connection_row: dict[str, Any],
    last_polled_at: str | None = None,
) -> dict[str, Any]:
    watchdog_state = _safe_dict(metadata.get("watchdog"))
    return {
        **metadata,
        "watchdog": {
            **watchdog_state,
            "mode": _resolve_runtime_poll_mode("google_drive", {
                **watchdog_state,
                "provider_contract": _build_google_target_provider_contract(connection_row),
            }),
            "provider_contract": _build_google_target_provider_contract(connection_row),
            "last_polled_at": last_polled_at or watchdog_state.get("last_polled_at"),
        },
    }


def _google_watch_expiration_ms() -> int:
    configured = max(60, int(settings.GOOGLE_DRIVE_WEBHOOK_EXPIRATION_SECONDS or 604800))
    return min(configured, 604800) * 1000


def _google_watch_needs_renewal(channel: dict[str, Any]) -> bool:
    expiration_ms = _coerce_int(channel.get("expiration"))
    if not expiration_ms:
        return True
    renewal_skew_seconds = max(0, int(settings.GOOGLE_DRIVE_WEBHOOK_RENEWAL_SKEW_SECONDS or 3600))
    expires_at = datetime.fromtimestamp(expiration_ms / 1000, tz=timezone.utc)
    return _now_utc().timestamp() >= (expires_at.timestamp() - renewal_skew_seconds)


def _google_drive_headers(access_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }


def _google_drive_get_start_page_token(connection_row: dict[str, Any]) -> str:
    response = requests.get(
        GOOGLE_DRIVE_START_PAGE_TOKEN_URL,
        params={"supportsAllDrives": "true"},
        headers={"Authorization": f"Bearer {connection_row.get('access_token')}"},
        timeout=20,
    )
    if not response.ok:
        raise ValueError(f"No se pudo obtener startPageToken de Google Drive: {response.text[:400]}")
    payload = response.json()
    page_token = str(payload.get("startPageToken") or "").strip()
    if not page_token:
        raise ValueError("Google Drive no devolvió startPageToken")
    return page_token


def _google_drive_stop_channel(connection_row: dict[str, Any], *, channel_id: str, resource_id: str) -> None:
    if not channel_id or not resource_id:
        return
    response = requests.post(
        GOOGLE_DRIVE_CHANNELS_STOP_URL,
        json={"id": channel_id, "resourceId": resource_id},
        headers=_google_drive_headers(connection_row.get("access_token")),
        timeout=20,
    )
    if not response.ok:
        emit_structured_log(
            "google_drive_watch_channel_stop_error",
            level="warning",
            user_id=connection_row.get("user_id"),
            connection_id=connection_row.get("id"),
            channel_id=channel_id,
            resource_id=resource_id,
            error=response.text[:200],
        )


def _google_drive_register_changes_channel(connection_row: dict[str, Any], *, page_token: str) -> dict[str, Any]:
    callback_url = _build_google_drive_webhook_address()
    if not _is_public_https_url(callback_url):
        return {
            "callback_url": callback_url,
            "contract_status": "polling_only",
            "channel": {},
        }

    channel_id = str(uuid.uuid4())
    channel_token = secrets.token_urlsafe(24)
    expiration_ms = int(_now_utc().timestamp() * 1000) + _google_watch_expiration_ms()
    response = requests.post(
        GOOGLE_DRIVE_CHANGES_URL + "/watch",
        params={
            "pageToken": page_token,
            "supportsAllDrives": "true",
            "includeRemoved": "true",
            "pageSize": "100",
        },
        json={
            "id": channel_id,
            "type": "web_hook",
            "address": callback_url,
            "token": channel_token,
            "expiration": expiration_ms,
        },
        headers=_google_drive_headers(connection_row.get("access_token")),
        timeout=20,
    )
    if not response.ok:
        raise ValueError(f"No se pudo registrar canal webhook de Google Drive: {response.text[:400]}")
    payload = response.json()
    return {
        "callback_url": callback_url,
        "contract_status": "active",
        "channel": {
            "id": payload.get("id") or channel_id,
            "token": payload.get("token") or channel_token,
            "resource_id": payload.get("resourceId"),
            "resource_uri": payload.get("resourceUri"),
            "expiration": _coerce_int(payload.get("expiration")) or expiration_ms,
            "status": "active",
            "renewed_at": _now_utc().isoformat(),
        },
    }


def ensure_google_drive_watch_contract(
    *,
    user_id: str,
    connection_row: dict[str, Any],
    service_client: Any,
    force_renew: bool = False,
) -> dict[str, Any]:
    active_connection = refresh_oauth_connection_tokens(connection_row, service_client)
    metadata = _safe_connection_metadata(active_connection)
    watchdog_metadata = _safe_dict(metadata.get("watchdog"))
    google_changes = _safe_google_changes_contract(active_connection)
    page_token = str(google_changes.get("page_token") or "").strip()
    if not page_token:
        page_token = _google_drive_get_start_page_token(active_connection)

    channel = _safe_dict(google_changes.get("channel"))
    contract_status = str(google_changes.get("contract_status") or "").strip().lower()
    should_register_channel = (
        _provider_watch_mode("google_drive") == "webhook"
        and (_is_public_https_url(_build_google_drive_webhook_address()))
        and (
            force_renew
            or not channel.get("id")
            or contract_status != "active"
            or _google_watch_needs_renewal(channel)
        )
    )

    if should_register_channel and channel.get("id") and channel.get("resource_id"):
        _google_drive_stop_channel(
            active_connection,
            channel_id=str(channel.get("id")),
            resource_id=str(channel.get("resource_id")),
        )

    if should_register_channel:
        registration = _google_drive_register_changes_channel(active_connection, page_token=page_token)
        channel = _safe_dict(registration.get("channel"))
        contract_status = str(registration.get("contract_status") or "active")
        callback_url = registration.get("callback_url")
    else:
        callback_url = str(google_changes.get("callback_url") or _build_google_drive_webhook_address() or "").strip()
        if not contract_status:
            contract_status = "active" if channel.get("id") else ("pending_registration" if _is_public_https_url(callback_url) else "polling_only")

    updated_metadata = {
        **metadata,
        "watchdog": {
            **watchdog_metadata,
            "google_changes": {
                **google_changes,
                "page_token": page_token,
                "callback_url": callback_url,
                "contract_status": contract_status,
                "channel": channel,
                "last_contract_refresh_at": _now_utc().isoformat(),
            },
        },
    }
    persisted_connection = _persist_connection_metadata(
        connection_row=active_connection,
        metadata=updated_metadata,
        service_client=service_client,
    )
    emit_structured_log(
        "google_drive_watch_contract_ensured",
        user_id=user_id,
        connection_id=persisted_connection.get("id"),
        contract_status=contract_status,
        channel_id=channel.get("id"),
        callback_url=callback_url,
    )
    return persisted_connection


def _google_drive_list_changes_page(connection_row: dict[str, Any], *, page_token: str) -> dict[str, Any]:
    response = requests.get(
        GOOGLE_DRIVE_CHANGES_URL,
        params={
            "pageToken": page_token,
            "supportsAllDrives": "true",
            "includeRemoved": "true",
            "pageSize": "100",
            "fields": "nextPageToken,newStartPageToken,changes(fileId,removed,file(id,name,mimeType,modifiedTime,size,webViewLink))",
        },
        headers={"Authorization": f"Bearer {connection_row.get('access_token')}"},
        timeout=20,
    )
    if response.status_code == 410:
        raise RuntimeError("google_changes_page_token_expired")
    if not response.ok:
        raise ValueError(f"No se pudo listar cambios de Google Drive: {response.text[:400]}")
    return response.json()


def consume_google_drive_connection_changes(
    *,
    user_id: str,
    connection_row: dict[str, Any],
    service_client: Any,
    force_contract_refresh: bool = False,
) -> dict[str, Any]:
    active_connection = ensure_google_drive_watch_contract(
        user_id=user_id,
        connection_row=connection_row,
        service_client=service_client,
        force_renew=force_contract_refresh,
    )
    metadata = _safe_connection_metadata(active_connection)
    watchdog_metadata = _safe_dict(metadata.get("watchdog"))
    google_changes = _safe_google_changes_contract(active_connection)
    page_token = str(google_changes.get("page_token") or "").strip()
    if not page_token:
        page_token = _google_drive_get_start_page_token(active_connection)

    changed_ids: set[str] = set()
    removed_ids: set[str] = set()
    current_page_token = page_token
    pages_processed = 0
    processed_change_count = 0

    try:
        while current_page_token and pages_processed < 10:
            payload = _google_drive_list_changes_page(active_connection, page_token=current_page_token)
            pages_processed += 1
            for item in _safe_list(payload.get("changes")):
                file_id = str(item.get("fileId") or "").strip()
                if not file_id:
                    continue
                processed_change_count += 1
                changed_ids.add(file_id)
                if bool(item.get("removed")):
                    removed_ids.add(file_id)
            next_page_token = str(payload.get("nextPageToken") or "").strip()
            new_start_page_token = str(payload.get("newStartPageToken") or "").strip()
            if next_page_token:
                current_page_token = next_page_token
                continue
            current_page_token = new_start_page_token or current_page_token
            break
    except RuntimeError as exc:
        if str(exc) != "google_changes_page_token_expired":
            raise
        current_page_token = _google_drive_get_start_page_token(active_connection)
        changed_ids.clear()
        removed_ids.clear()
        processed_change_count = 0
        emit_structured_log(
            "google_drive_changes_token_reset",
            user_id=user_id,
            connection_id=active_connection.get("id"),
        )

    updated_metadata = {
        **metadata,
        "watchdog": {
            **watchdog_metadata,
            "google_changes": {
                **google_changes,
                "page_token": current_page_token,
                "last_sync_at": _now_utc().isoformat(),
                "last_processed_change_count": processed_change_count,
            },
        },
    }
    persisted_connection = _persist_connection_metadata(
        connection_row=active_connection,
        metadata=updated_metadata,
        service_client=service_client,
    )
    emit_structured_log(
        "google_drive_changes_consumed",
        user_id=user_id,
        connection_id=persisted_connection.get("id"),
        changed_count=len(changed_ids),
        removed_count=len(removed_ids),
        processed_change_count=processed_change_count,
    )
    return {
        "connection": persisted_connection,
        "changed_ids": changed_ids,
        "removed_ids": removed_ids,
    }


def _provider_watch_mode(provider_id: str) -> str:
    if provider_id == "google_drive":
        normalized = str(settings.GOOGLE_DRIVE_WATCH_MODE or "webhook").strip().lower()
        return normalized if normalized in {"webhook", "polling"} else "webhook"
    if provider_id == "onedrive":
        normalized = str(settings.MICROSOFT_ONEDRIVE_WATCH_MODE or "polling").strip().lower()
        return normalized if normalized in {"webhook", "polling"} else "polling"
    return "polling"


def _resolve_runtime_poll_mode(provider_id: str, watchdog_state: dict[str, Any]) -> str:
    provider_contract = _safe_dict(watchdog_state.get("provider_contract"))
    if provider_id == "google_drive":
        contract_status = str(provider_contract.get("contract_status") or "").strip().lower()
        if contract_status in {"", "pending_registration", "polling_only"}:
            return "polling"
    return _provider_watch_mode(provider_id)


def _build_remote_snapshot(remote_item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": remote_item.get("id"),
        "name": remote_item.get("name"),
        "provider": remote_item.get("provider"),
        "extension": remote_item.get("extension"),
        "mime_type": remote_item.get("mime_type"),
        "size_bytes": remote_item.get("size_bytes"),
        "etag": remote_item.get("etag"),
        "ctag": remote_item.get("ctag"),
        "modified_at": remote_item.get("modified_at"),
        "web_url": remote_item.get("web_url"),
        "ingest_source_type": remote_item.get("ingest_source_type"),
    }


def _build_watchdog_metadata(
    *,
    provider_id: str,
    remote_item: dict[str, Any],
    existing_metadata: dict[str, Any] | None = None,
    reset_pending: bool = False,
) -> dict[str, Any]:
    base_metadata = dict(existing_metadata or {})
    watchdog_state = _safe_dict(base_metadata.get("watchdog"))
    remote_snapshot = _build_remote_snapshot(remote_item)
    watch_mode = _provider_watch_mode(provider_id)
    pending_change = bool(watchdog_state.get("pending_change")) and not reset_pending

    if provider_id == "google_drive":
        provider_contract = {
            **_safe_dict(watchdog_state.get("provider_contract")),
            "mode": "webhook",
            "contract_status": "pending_registration",
            "start_page_token": _safe_dict(watchdog_state.get("provider_contract")).get("start_page_token"),
        }
    else:
        provider_contract = {
            **_safe_dict(watchdog_state.get("provider_contract")),
            "mode": "polling",
            "contract_status": "active",
        }

    return {
        **base_metadata,
        "remote_snapshot": remote_snapshot,
        "watchdog": {
            **watchdog_state,
            "mode": watch_mode,
            "provider_contract": provider_contract,
            "pending_change": pending_change,
            "pending_change_summary": None if reset_pending else watchdog_state.get("pending_change_summary"),
            "last_client_notified_at": None if reset_pending else watchdog_state.get("last_client_notified_at"),
            "last_notified_change_signature": None if reset_pending else watchdog_state.get("last_notified_change_signature"),
            "last_polled_at": watchdog_state.get("last_polled_at"),
            "last_seen_modified_at": remote_snapshot.get("modified_at"),
            "last_seen_size_bytes": remote_snapshot.get("size_bytes"),
            "last_change_detected_at": None if reset_pending else watchdog_state.get("last_change_detected_at"),
            "last_error": None,
        },
    }


def serialize_watch_target_row(row: dict[str, Any]) -> dict[str, Any]:
    metadata = _safe_dict(row.get("metadata"))
    watchdog_state = _safe_dict(metadata.get("watchdog"))
    remote_snapshot = _safe_dict(metadata.get("remote_snapshot"))
    provider_contract = _safe_dict(watchdog_state.get("provider_contract"))

    return {
        "id": row.get("id"),
        "provider": row.get("provider"),
        "target_type": row.get("target_type"),
        "target_id": row.get("target_id"),
        "target_name": row.get("target_name"),
        "linked_file_id": row.get("linked_file_id"),
        "is_active": bool(row.get("is_active")),
        "watchdog_mode": watchdog_state.get("mode") or _provider_watch_mode(str(row.get("provider") or "")),
        "contract_status": provider_contract.get("contract_status"),
        "pending_change": bool(watchdog_state.get("pending_change")),
        "pending_change_summary": watchdog_state.get("pending_change_summary"),
        "sync_state": watchdog_state.get("sync_state"),
        "last_known_modified_at": remote_snapshot.get("modified_at") or watchdog_state.get("last_seen_modified_at"),
        "last_known_size_bytes": remote_snapshot.get("size_bytes") or watchdog_state.get("last_seen_size_bytes"),
        "last_polled_at": watchdog_state.get("last_polled_at"),
        "last_change_detected_at": watchdog_state.get("last_change_detected_at"),
        "auto_sync_status": watchdog_state.get("auto_sync_status"),
        "last_auto_sync_at": watchdog_state.get("last_auto_sync_at"),
        "last_auto_sync_error": watchdog_state.get("last_auto_sync_error"),
        "last_auto_sync_job_id": watchdog_state.get("last_auto_sync_job_id"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def list_user_watch_targets(
    *,
    user_id: str,
    provider_id: str,
    service_client: Any,
) -> list[dict[str, Any]]:
    response = service_client.table("cloud_watch_targets") \
        .select("*") \
        .eq("user_id", user_id) \
        .eq("provider", provider_id) \
        .eq("is_active", True) \
        .order("created_at", desc=False) \
        .execute()
    return [serialize_watch_target_row(row) for row in (response.data or [])]


def upsert_watch_target(
    *,
    user_id: str,
    provider_id: str,
    connection_row: dict[str, Any],
    remote_item: dict[str, Any],
    service_client: Any,
) -> dict[str, Any]:
    existing_response = service_client.table("cloud_watch_targets") \
        .select("*") \
        .eq("connection_id", connection_row["id"]) \
        .eq("target_id", remote_item["id"]) \
        .limit(1) \
        .execute()
    existing_row = existing_response.data[0] if existing_response.data else None
    existing_metadata = _safe_dict((existing_row or {}).get("metadata"))

    payload = {
        "connection_id": connection_row["id"],
        "user_id": user_id,
        "provider": provider_id,
        "target_type": "file",
        "target_id": remote_item["id"],
        "target_name": remote_item.get("name"),
        "linked_file_id": (existing_row or {}).get("linked_file_id"),
        "is_active": True,
        "metadata": _build_watchdog_metadata(
            provider_id=provider_id,
            remote_item=remote_item,
            existing_metadata=existing_metadata,
            reset_pending=True,
        ),
    }
    response = service_client.table("cloud_watch_targets") \
        .upsert(payload, on_conflict="connection_id,target_id") \
        .execute()
    watch_target = response.data[0] if response.data else ({**(existing_row or {}), **payload})
    emit_structured_log(
        "watch_target_upserted",
        user_id=user_id,
        provider=provider_id,
        watch_target_id=watch_target.get("id"),
        target_id=remote_item.get("id"),
        target_name=remote_item.get("name"),
    )
    return serialize_watch_target_row(watch_target)


def deactivate_watch_target(
    *,
    user_id: str,
    provider_id: str,
    watch_target_id: str,
    service_client: Any,
) -> bool:
    response = service_client.table("cloud_watch_targets") \
        .update({"is_active": False}) \
        .eq("id", watch_target_id) \
        .eq("user_id", user_id) \
        .eq("provider", provider_id) \
        .eq("is_active", True) \
        .execute()
    deactivated = bool(response.data)
    emit_structured_log(
        "watch_target_deactivated",
        user_id=user_id,
        provider=provider_id,
        watch_target_id=watch_target_id,
        deactivated=deactivated,
    )
    return deactivated


def link_watch_target_to_uploaded_file(
    *,
    user_id: str,
    provider_id: str,
    target_id: str,
    uploaded_file_id: str,
    service_client: Any,
) -> None:
    existing_response = service_client.table("cloud_watch_targets") \
        .select("*") \
        .eq("user_id", user_id) \
        .eq("provider", provider_id) \
        .eq("target_id", target_id) \
        .eq("is_active", True) \
        .limit(1) \
        .execute()
    if not existing_response.data:
        return

    existing_row = existing_response.data[0]
    metadata = _safe_dict(existing_row.get("metadata"))
    watchdog_state = _safe_dict(metadata.get("watchdog"))
    updated_metadata = {
        **metadata,
        "watchdog": {
            **watchdog_state,
            "pending_change": False,
            "pending_change_summary": None,
            "last_client_notified_at": None,
            "last_notified_change_signature": None,
            "sync_state": "synced",
            "last_change_detected_at": None,
            "last_error": None,
        },
    }
    service_client.table("cloud_watch_targets").update({
        "linked_file_id": uploaded_file_id,
        "metadata": updated_metadata,
    }).eq("id", existing_row["id"]).execute()
    emit_structured_log(
        "watch_target_linked_to_uploaded_file",
        user_id=user_id,
        provider=provider_id,
        watch_target_id=existing_row.get("id"),
        uploaded_file_id=uploaded_file_id,
    )


def _build_change_summary(previous_snapshot: dict[str, Any], current_snapshot: dict[str, Any]) -> str:
    previous_modified = previous_snapshot.get("modified_at")
    current_modified = current_snapshot.get("modified_at")
    previous_size = previous_snapshot.get("size_bytes")
    current_size = current_snapshot.get("size_bytes")
    previous_etag = previous_snapshot.get("etag")
    current_etag = current_snapshot.get("etag")
    previous_ctag = previous_snapshot.get("ctag")
    current_ctag = current_snapshot.get("ctag")

    changes: list[str] = []
    if previous_modified != current_modified and current_modified:
        changes.append("fecha de modificación actualizada")
    if previous_size != current_size:
        changes.append("tamaño actualizado")
    if previous_etag != current_etag and current_etag:
        changes.append("versión de contenido actualizada")
    if previous_ctag != current_ctag and current_ctag:
        changes.append("token de cambio actualizado")
    if previous_snapshot.get("name") != current_snapshot.get("name"):
        changes.append("nombre actualizado")

    if not changes:
        return "Se detectó una actualización remota"
    return "Cambio detectado: " + ", ".join(changes)


def _remote_snapshot_changed(previous_snapshot: dict[str, Any], current_snapshot: dict[str, Any]) -> bool:
    comparable_keys = ("name", "modified_at", "size_bytes", "mime_type", "etag", "ctag")
    return any(previous_snapshot.get(key) != current_snapshot.get(key) for key in comparable_keys)


def _build_watchdog_change_signature(
    *,
    target_id: str,
    snapshot: dict[str, Any],
    removed: bool = False,
    event_hint: str | None = None,
) -> str:
    signature_parts = [
        "removed" if removed else "updated",
        str(target_id or "").strip(),
        str(snapshot.get("name") or "").strip(),
        str(snapshot.get("modified_at") or "").strip(),
        str(snapshot.get("size_bytes") if snapshot.get("size_bytes") is not None else "").strip(),
        str(snapshot.get("mime_type") or "").strip(),
        str(snapshot.get("etag") or "").strip(),
        str(snapshot.get("ctag") or "").strip(),
        str(event_hint or "").strip(),
    ]
    return "|".join(signature_parts)


def _should_emit_watchdog_change_notification(
    *,
    change_detected: bool,
    change_signature: str | None,
    last_notified_change_signature: str | None,
) -> bool:
    if not change_detected:
        return False

    normalized_signature = str(change_signature or "").strip()
    normalized_last_signature = str(last_notified_change_signature or "").strip()

    if not normalized_signature:
        return True
    return normalized_signature != normalized_last_signature


def _persist_watch_target_metadata(
    *,
    service_client: Any,
    row: dict[str, Any],
    metadata: dict[str, Any],
    target_name: str | None = None,
) -> None:
    update_payload: dict[str, Any] = {
        "metadata": metadata,
    }
    if target_name:
        update_payload["target_name"] = target_name
    service_client.table("cloud_watch_targets").update(update_payload).eq("id", row["id"]).execute()


def poll_user_watch_targets(
    *,
    user_id: str,
    service_client: Any,
    provider_id: str | None = None,
) -> dict[str, Any]:
    targets_query = service_client.table("cloud_watch_targets") \
        .select("*") \
        .eq("user_id", user_id) \
        .eq("is_active", True)
    if provider_id:
        targets_query = targets_query.eq("provider", provider_id)
    targets_response = targets_query.execute()
    target_rows = targets_response.data or []
    if not target_rows:
        return {
            "checked_count": 0,
            "new_change_count": 0,
            "skipped_contract_count": 0,
            "error_count": 0,
            "changes": [],
        }

    providers_to_load = {row.get("provider") for row in target_rows if row.get("provider")}
    connection_index: dict[str, dict[str, Any]] = {}
    for candidate_provider in providers_to_load:
        connection_response = service_client.table("cloud_oauth_connections") \
            .select("*") \
            .eq("user_id", user_id) \
            .eq("provider", candidate_provider) \
            .eq("status", "active") \
            .limit(1) \
            .execute()
        if connection_response.data:
            connection_index[candidate_provider] = decrypt_oauth_connection_row(connection_response.data[0])

    checked_count = 0
    new_change_count = 0
    skipped_contract_count = 0
    error_count = 0
    changes: list[dict[str, Any]] = []

    google_rows_by_connection: dict[str, list[dict[str, Any]]] = defaultdict(list)
    non_google_rows: list[dict[str, Any]] = []

    for row in target_rows:
        if str(row.get("provider") or "").strip() == "google_drive" and row.get("connection_id"):
            google_rows_by_connection[str(row["connection_id"])].append(row)
        else:
            non_google_rows.append(row)

    for connection_id, provider_rows in google_rows_by_connection.items():
        current_connection = connection_index.get("google_drive")
        if not current_connection or str(current_connection.get("id")) != connection_id:
            error_count += len(provider_rows)
            continue

        checked_count += len(provider_rows)

        try:
            change_feed = consume_google_drive_connection_changes(
                user_id=user_id,
                connection_row=current_connection,
                service_client=service_client,
            )
            active_connection = change_feed["connection"]
            changed_ids = set(change_feed["changed_ids"])
            removed_ids = set(change_feed["removed_ids"])
        except Exception as exc:
            error_count += len(provider_rows)
            for row in provider_rows:
                metadata = _safe_dict(row.get("metadata"))
                watchdog_state = _safe_dict(metadata.get("watchdog"))
                updated_metadata = {
                    **metadata,
                    "watchdog": {
                        **watchdog_state,
                        "mode": _provider_watch_mode("google_drive"),
                        "last_polled_at": _now_utc().isoformat(),
                        "last_error": str(exc)[:240],
                    },
                }
                _persist_watch_target_metadata(
                    service_client=service_client,
                    row=row,
                    metadata=updated_metadata,
                )
            continue

        for row in provider_rows:
            metadata = _safe_dict(row.get("metadata"))
            watchdog_state = _safe_dict(metadata.get("watchdog"))
            existing_snapshot = _safe_dict(metadata.get("remote_snapshot"))
            was_pending = bool(watchdog_state.get("pending_change"))
            last_client_notified_at = watchdog_state.get("last_client_notified_at")
            last_notified_change_signature = watchdog_state.get("last_notified_change_signature")
            is_changed = str(row.get("target_id") or "").strip() in changed_ids
            was_removed = str(row.get("target_id") or "").strip() in removed_ids
            current_snapshot = existing_snapshot
            change_summary = watchdog_state.get("pending_change_summary")
            last_error = None
            target_name = row.get("target_name")
            poll_timestamp = _now_utc().isoformat()
            google_changes_contract = _safe_google_changes_contract(active_connection)
            change_event_hint = str(google_changes_contract.get("page_token") or "").strip()

            if is_changed and not was_removed:
                try:
                    remote_item = get_provider_remote_file(
                        "google_drive",
                        connection_row=active_connection,
                        service_client=service_client,
                        item_id=row["target_id"],
                    )
                    current_snapshot = _build_remote_snapshot(remote_item)
                    target_name = remote_item.get("name") or target_name
                    change_summary = _build_change_summary(existing_snapshot, current_snapshot)
                except Exception as exc:
                    last_error = str(exc)[:240]
                    change_summary = change_summary or "Cambio detectado en Google Drive. Se sincronizará en el siguiente intento."
            elif is_changed and was_removed:
                change_summary = "El archivo remoto fue eliminado o dejó de estar accesible en Google Drive."

            pending_change = was_pending or is_changed
            change_signature = _build_watchdog_change_signature(
                target_id=str(row.get("target_id") or ""),
                snapshot=current_snapshot,
                removed=was_removed,
                event_hint=change_event_hint,
            )
            should_notify_client = _should_emit_watchdog_change_notification(
                change_detected=is_changed,
                change_signature=change_signature,
                last_notified_change_signature=last_notified_change_signature,
            )
            updated_metadata = _merge_google_provider_contract_into_target_metadata(
                metadata={
                    **metadata,
                    "remote_snapshot": current_snapshot,
                    "watchdog": {
                        **watchdog_state,
                        "pending_change": pending_change,
                        "pending_change_summary": change_summary if pending_change else None,
                        "last_client_notified_at": poll_timestamp if should_notify_client else last_client_notified_at,
                        "last_notified_change_signature": change_signature if should_notify_client else last_notified_change_signature,
                        "sync_state": "pending_sync" if pending_change else "synced",
                        "last_change_detected_at": poll_timestamp if is_changed else watchdog_state.get("last_change_detected_at"),
                        "last_seen_modified_at": current_snapshot.get("modified_at") or watchdog_state.get("last_seen_modified_at"),
                        "last_seen_size_bytes": current_snapshot.get("size_bytes") if current_snapshot.get("size_bytes") is not None else watchdog_state.get("last_seen_size_bytes"),
                        "last_error": last_error,
                    },
                },
                connection_row=active_connection,
                last_polled_at=poll_timestamp,
            )
            _persist_watch_target_metadata(
                service_client=service_client,
                row=row,
                metadata=updated_metadata,
                target_name=target_name,
            )
            emit_structured_log(
                "watch_target_polled",
                user_id=user_id,
                provider="google_drive",
                watch_target_id=row.get("id"),
                target_id=row.get("target_id"),
                changed=is_changed,
                previous_modified_at=existing_snapshot.get("modified_at"),
                current_modified_at=current_snapshot.get("modified_at"),
                previous_etag=existing_snapshot.get("etag"),
                current_etag=current_snapshot.get("etag"),
                previous_ctag=existing_snapshot.get("ctag"),
                current_ctag=current_snapshot.get("ctag"),
            )

            if should_notify_client:
                new_change_count += 1
                changes.append({
                    "watch_target_id": row["id"],
                    "provider": "google_drive",
                    "target_id": row["target_id"],
                    "target_name": target_name or row.get("target_name"),
                    "linked_file_id": row.get("linked_file_id"),
                    "change_summary": change_summary,
                    "changed_at": updated_metadata["watchdog"].get("last_change_detected_at"),
                    "requires_reimport": True,
                })

    for row in non_google_rows:
        current_provider = str(row.get("provider") or "").strip()
        metadata = _safe_dict(row.get("metadata"))
        watchdog_state = _safe_dict(metadata.get("watchdog"))
        existing_snapshot = _safe_dict(metadata.get("remote_snapshot"))
        current_connection = connection_index.get(current_provider)

        if not current_connection:
            error_count += 1
            continue

        try:
            remote_item = get_provider_remote_file(
                current_provider,
                connection_row=current_connection,
                service_client=service_client,
                item_id=row["target_id"],
            )
            current_snapshot = _build_remote_snapshot(remote_item)
            changed = _remote_snapshot_changed(existing_snapshot, current_snapshot)
            was_pending = bool(watchdog_state.get("pending_change"))
            last_client_notified_at = watchdog_state.get("last_client_notified_at")
            last_notified_change_signature = watchdog_state.get("last_notified_change_signature")
            change_summary = watchdog_state.get("pending_change_summary")
            runtime_mode = _resolve_runtime_poll_mode(current_provider, watchdog_state)
            provider_contract = _safe_dict(watchdog_state.get("provider_contract"))
            poll_timestamp = _now_utc().isoformat()

            if changed:
                change_summary = _build_change_summary(existing_snapshot, current_snapshot)

            pending_change = was_pending or changed
            change_signature = _build_watchdog_change_signature(
                target_id=str(row.get("target_id") or ""),
                snapshot=current_snapshot,
            )
            should_notify_client = _should_emit_watchdog_change_notification(
                change_detected=changed,
                change_signature=change_signature,
                last_notified_change_signature=last_notified_change_signature,
            )
            updated_metadata = {
                **metadata,
                "remote_snapshot": current_snapshot,
                "watchdog": {
                    **watchdog_state,
                    "mode": runtime_mode,
                    "last_polled_at": poll_timestamp,
                    "last_seen_modified_at": current_snapshot.get("modified_at"),
                    "last_seen_size_bytes": current_snapshot.get("size_bytes"),
                    "pending_change": pending_change,
                    "pending_change_summary": change_summary if pending_change else None,
                    "last_client_notified_at": poll_timestamp if should_notify_client else last_client_notified_at,
                    "last_notified_change_signature": change_signature if should_notify_client else last_notified_change_signature,
                    "sync_state": "pending_sync" if pending_change else "synced",
                    "last_change_detected_at": poll_timestamp if changed else watchdog_state.get("last_change_detected_at"),
                    "last_error": None,
                    "provider_contract": {
                        **provider_contract,
                        "mode": provider_contract.get("mode") or ("webhook" if current_provider == "google_drive" else "polling"),
                        "contract_status": (
                            provider_contract.get("contract_status")
                            if current_provider == "google_drive"
                            else "active"
                        ) or ("pending_registration" if current_provider == "google_drive" else "active"),
                        "fallback_polling_active": True if current_provider == "google_drive" else provider_contract.get("fallback_polling_active"),
                    },
                },
            }
            _persist_watch_target_metadata(
                service_client=service_client,
                row=row,
                metadata=updated_metadata,
                target_name=remote_item.get("name"),
            )
            emit_structured_log(
                "watch_target_polled",
                user_id=user_id,
                provider=current_provider,
                watch_target_id=row.get("id"),
                target_id=row.get("target_id"),
                changed=changed,
                previous_modified_at=existing_snapshot.get("modified_at"),
                current_modified_at=current_snapshot.get("modified_at"),
                previous_etag=existing_snapshot.get("etag"),
                current_etag=current_snapshot.get("etag"),
                previous_ctag=existing_snapshot.get("ctag"),
                current_ctag=current_snapshot.get("ctag"),
            )

            checked_count += 1
            if should_notify_client:
                new_change_count += 1
                changes.append({
                    "watch_target_id": row["id"],
                    "provider": current_provider,
                    "target_id": row["target_id"],
                    "target_name": remote_item.get("name") or row.get("target_name"),
                    "linked_file_id": row.get("linked_file_id"),
                    "change_summary": change_summary,
                    "changed_at": updated_metadata["watchdog"].get("last_change_detected_at"),
                    "requires_reimport": True,
                })
        except Exception as exc:
            error_count += 1
            updated_metadata = {
                **metadata,
                "watchdog": {
                    **watchdog_state,
                    "mode": _provider_watch_mode(current_provider),
                    "last_polled_at": _now_utc().isoformat(),
                    "last_error": str(exc)[:240],
                },
            }
            _persist_watch_target_metadata(
                service_client=service_client,
                row=row,
                metadata=updated_metadata,
            )

    emit_structured_log(
        "watchdog_poll_executed",
        user_id=user_id,
        provider=provider_id,
        checked_count=checked_count,
        new_change_count=new_change_count,
        skipped_contract_count=skipped_contract_count,
        error_count=error_count,
    )
    return {
        "checked_count": checked_count,
        "new_change_count": new_change_count,
        "skipped_contract_count": skipped_contract_count,
        "error_count": error_count,
        "changes": changes,
    }
