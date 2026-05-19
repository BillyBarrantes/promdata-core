from datetime import datetime, timezone
from typing import Any

from app.core.config import settings


def _as_bool(value: str | bool | None) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value or "").strip().lower()
    return normalized in {"1", "true", "yes", "on", "enabled"}


def _normalize_watch_mode(raw_mode: str | None, fallback: str = "polling") -> str:
    normalized = str(raw_mode or "").strip().lower()
    if normalized in {"webhook", "polling"}:
        return normalized
    return fallback


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _latest_iso(values: list[str | None]) -> str | None:
    latest_dt: datetime | None = None
    latest_raw: str | None = None
    for raw_value in values:
        parsed = _parse_iso_datetime(raw_value)
        if not parsed:
            continue
        if latest_dt is None or parsed > latest_dt:
            latest_dt = parsed
            latest_raw = parsed.astimezone(timezone.utc).isoformat()
    return latest_raw


def _iso_plus_seconds(raw_value: str | None, seconds: int) -> str | None:
    parsed = _parse_iso_datetime(raw_value)
    if not parsed:
        return None
    normalized = parsed.astimezone(timezone.utc)
    next_timestamp = normalized.timestamp() + max(int(seconds or 0), 0)
    return datetime.fromtimestamp(next_timestamp, tz=timezone.utc).isoformat()


def _build_provider_runtime_summary(
    *,
    connected: bool,
    target_count: int,
    pending_target_count: int,
    stale_target_count: int,
    fallback_target_count: int,
    error_target_count: int,
    contract_state: str,
) -> tuple[str, str]:
    if not connected:
        return (
            "Sin conexión activa con el proveedor.",
            "Reconectar la cuenta para reactivar vigilancia.",
        )
    if target_count == 0:
        return (
            "Proveedor conectado, sin archivos vigilados todavía.",
            "Marcar archivos críticos como vigilados para activar re-sync.",
        )
    if pending_target_count > 0:
        return (
            "Hay cambios detectados pendientes de sincronización o reimportación.",
            "Verificar ahora y reimportar los archivos remotos pendientes.",
        )
    if error_target_count > 0 or stale_target_count > 0:
        return (
            "La señal de vigilancia está degradada o desactualizada.",
            "Verificar ahora y revisar polling, credenciales o conectividad.",
        )
    if fallback_target_count > 0 or contract_state == "fallback_polling":
        return (
            "La vigilancia está operando mediante polling fallback.",
            "Mantener polling o publicar webhook HTTPS para reducir latencia.",
        )
    return (
        "Proveedor vigilado y sincronizado.",
        "No requiere acción inmediata.",
    )


def _watchdog_stale_threshold_seconds() -> int:
    base_interval = max(15, int(settings.CONNECTOR_POLL_INTERVAL_SECONDS or 30))
    return max(base_interval * 3, 120)


def _build_watchdog_provider_states(
    *,
    providers: list[dict[str, Any]],
    service_client: Any,
    user_id: str,
) -> list[dict[str, Any]]:
    connections_response = service_client.table("cloud_oauth_connections") \
        .select("provider, status, metadata, last_refreshed_at") \
        .eq("user_id", user_id) \
        .eq("status", "active") \
        .execute()
    active_connections = {
        str(row.get("provider") or "").strip(): row
        for row in (connections_response.data or [])
        if str(row.get("provider") or "").strip()
    }

    targets_response = service_client.table("cloud_watch_targets") \
        .select("*") \
        .eq("user_id", user_id) \
        .eq("is_active", True) \
        .execute()
    targets_by_provider: dict[str, list[dict[str, Any]]] = {}
    for row in (targets_response.data or []):
        provider_id = str(row.get("provider") or "").strip()
        if not provider_id:
            continue
        targets_by_provider.setdefault(provider_id, []).append(row)

    now_utc = datetime.now(timezone.utc)
    stale_threshold_seconds = _watchdog_stale_threshold_seconds()
    provider_states: list[dict[str, Any]] = []

    for provider in providers:
        provider_id = str(provider.get("id") or "").strip()
        runtime_mode = _normalize_watch_mode(provider.get("watchdog_mode"), "polling")
        connection_row = active_connections.get(provider_id, {})
        connected = bool(connection_row)
        target_rows = targets_by_provider.get(provider_id, [])

        pending_target_count = 0
        synced_target_count = 0
        stale_target_count = 0
        fallback_target_count = 0
        error_target_count = 0
        contract_statuses: set[str] = set()
        provider_last_activity_candidates: list[str | None] = [
            connection_row.get("last_refreshed_at") if isinstance(connection_row, dict) else None,
        ]
        provider_last_polled_candidates: list[str | None] = []
        provider_last_change_candidates: list[str | None] = []

        for row in target_rows:
            metadata = _safe_dict(row.get("metadata"))
            watchdog_state = _safe_dict(metadata.get("watchdog"))
            provider_contract = _safe_dict(watchdog_state.get("provider_contract"))

            if bool(watchdog_state.get("pending_change")):
                pending_target_count += 1
            if str(watchdog_state.get("sync_state") or "").strip().lower() == "synced":
                synced_target_count += 1
            if watchdog_state.get("last_error"):
                error_target_count += 1

            contract_status = str(provider_contract.get("contract_status") or "").strip().lower()
            if contract_status in {"polling_only", "pending_registration"}:
                fallback_target_count += 1
            if contract_status:
                contract_statuses.add(contract_status)

            last_polled_at = _parse_iso_datetime(watchdog_state.get("last_polled_at"))
            provider_last_polled_candidates.append(watchdog_state.get("last_polled_at"))
            provider_last_change_candidates.append(watchdog_state.get("last_change_detected_at"))
            if target_rows and (
                last_polled_at is None
                or (now_utc - last_polled_at).total_seconds() > stale_threshold_seconds
            ):
                stale_target_count += 1

            provider_last_activity_candidates.extend([
                watchdog_state.get("last_change_detected_at"),
                watchdog_state.get("last_polled_at"),
                row.get("updated_at"),
                row.get("created_at"),
            ])

        if not connected:
            contract_state = "disconnected"
            operational_state = "idle"
        elif not target_rows:
            contract_state = "idle"
            operational_state = "idle"
        elif pending_target_count > 0:
            contract_state = "pending_sync"
            operational_state = "attention"
        elif error_target_count > 0 or stale_target_count > 0:
            contract_state = "attention"
            operational_state = "degraded"
        elif fallback_target_count > 0:
            contract_state = "fallback_polling"
            operational_state = "attention"
        else:
            contract_state = "active"
            operational_state = "healthy"

        last_polled_at_raw = _latest_iso(provider_last_polled_candidates)
        last_change_detected_at = _latest_iso(provider_last_change_candidates)
        sync_summary, recommended_action = _build_provider_runtime_summary(
            connected=connected,
            target_count=len(target_rows),
            pending_target_count=pending_target_count,
            stale_target_count=stale_target_count,
            fallback_target_count=fallback_target_count,
            error_target_count=error_target_count,
            contract_state=contract_state,
        )

        provider_states.append({
            "provider_id": provider_id,
            "provider_name": provider.get("name"),
            "connected": connected,
            "runtime_mode": runtime_mode,
            "watch_target_count": len(target_rows),
            "pending_target_count": pending_target_count,
            "synced_target_count": synced_target_count,
            "stale_target_count": stale_target_count,
            "fallback_target_count": fallback_target_count,
            "error_target_count": error_target_count,
            "contract_state": contract_state,
            "operational_state": operational_state,
            "contract_statuses": sorted(contract_statuses),
            "sync_summary": sync_summary,
            "recommended_action": recommended_action,
            "last_activity_at": _latest_iso(provider_last_activity_candidates),
            "last_polled_at": last_polled_at_raw,
            "last_change_detected_at": last_change_detected_at,
            "next_check_due_at": _iso_plus_seconds(last_polled_at_raw, settings.CONNECTOR_POLL_INTERVAL_SECONDS),
        })

    return provider_states


def _summarize_watchdog_runtime(
    *,
    enabled: bool,
    provider_states: list[dict[str, Any]],
) -> dict[str, Any]:
    connected_provider_count = sum(1 for state in provider_states if state.get("connected"))
    active_target_count = sum(int(state.get("watch_target_count") or 0) for state in provider_states)
    pending_target_count = sum(int(state.get("pending_target_count") or 0) for state in provider_states)
    synced_target_count = sum(int(state.get("synced_target_count") or 0) for state in provider_states)
    fallback_provider_count = sum(1 for state in provider_states if int(state.get("fallback_target_count") or 0) > 0)
    degraded_provider_count = sum(1 for state in provider_states if state.get("operational_state") == "degraded")
    last_activity_at = _latest_iso([state.get("last_activity_at") for state in provider_states])

    if not enabled and connected_provider_count == 0 and active_target_count == 0:
        operational_state = "disabled"
        summary = "Vigilancia cloud deshabilitada en entorno."
    elif not enabled and (connected_provider_count > 0 or active_target_count > 0):
        operational_state = "attention"
        summary = "Hay conectores o archivos vigilados activos, pero el watchdog está deshabilitado en entorno."
    elif active_target_count == 0:
        operational_state = "idle"
        summary = "Watchdog listo, sin archivos vigilados todavía."
    elif pending_target_count > 0:
        operational_state = "attention"
        summary = "Se detectaron cambios pendientes de sincronización o reimportación."
    elif degraded_provider_count > 0:
        operational_state = "degraded"
        summary = "Hay conectores vigilados con señal degradada o desactualizada."
    elif fallback_provider_count > 0:
        operational_state = "attention"
        summary = "La vigilancia está activa con fallback operativo en al menos un proveedor."
    else:
        operational_state = "healthy"
        summary = "Vigilancia cloud operando con archivos sincronizados."

    return {
        "operational_state": operational_state,
        "summary": summary,
        "connected_provider_count": connected_provider_count,
        "active_target_count": active_target_count,
        "pending_target_count": pending_target_count,
        "synced_target_count": synced_target_count,
        "fallback_provider_count": fallback_provider_count,
        "last_activity_at": last_activity_at,
    }


def _build_provider_definition(
    *,
    provider_id: str,
    name: str,
    client_id: str,
    client_secret: str,
    watch_mode: str,
    docs_hint: str,
    auth_start_path: str,
    auth_callback_path: str,
) -> dict[str, Any]:
    oauth_ready = bool(client_id and client_secret)
    normalized_watch_mode = _normalize_watch_mode(watch_mode)
    watchdog_enabled = _as_bool(settings.CONNECTOR_WATCHDOG_ENABLED) and oauth_ready
    is_webhook = normalized_watch_mode == "webhook"

    return {
        "id": provider_id,
        "name": name,
        "category": "cloud_storage",
        "status": "configured" if oauth_ready else "config_pending",
        "configured": oauth_ready,
        "oauth_ready": oauth_ready,
        "auth_flow": "oauth2",
        "auth_start_path": auth_start_path,
        "auth_callback_path": auth_callback_path,
        "watchdog_mode": normalized_watch_mode,
        "watchdog_enabled": watchdog_enabled,
        "capabilities": {
            "can_import": oauth_ready,
            "can_watch": watchdog_enabled,
            "supports_webhook": is_webhook and oauth_ready,
            "supports_polling": (not is_webhook) and oauth_ready,
        },
        "notes": (
            "Listo para integración segura."
            if oauth_ready
            else f"Falta configurar credenciales en entorno ({docs_hint})."
        ),
    }


def get_cloud_connector_catalog() -> list[dict[str, Any]]:
    """
    Contrato base de Fase 6: catálogo estable de conectores cloud.
    No inicia OAuth ni depende de secretos válidos; solo expone capacidades.
    """
    return [
        _build_provider_definition(
            provider_id="google_drive",
            name="Google Drive",
            client_id=settings.GOOGLE_DRIVE_CLIENT_ID,
            client_secret=settings.GOOGLE_DRIVE_CLIENT_SECRET,
            watch_mode=settings.GOOGLE_DRIVE_WATCH_MODE,
            docs_hint="GOOGLE_DRIVE_CLIENT_ID / GOOGLE_DRIVE_CLIENT_SECRET",
            auth_start_path="/api/v1/auth/google",
            auth_callback_path="/api/v1/auth/google/callback",
        ),
        _build_provider_definition(
            provider_id="onedrive",
            name="Microsoft OneDrive",
            client_id=settings.MICROSOFT_ONEDRIVE_CLIENT_ID,
            client_secret=settings.MICROSOFT_ONEDRIVE_CLIENT_SECRET,
            watch_mode=settings.MICROSOFT_ONEDRIVE_WATCH_MODE,
            docs_hint="MICROSOFT_ONEDRIVE_CLIENT_ID / MICROSOFT_ONEDRIVE_CLIENT_SECRET",
            auth_start_path="/api/v1/auth/microsoft",
            auth_callback_path="/api/v1/auth/microsoft/callback",
        ),
    ]


def get_watchdog_runtime_status(
    *,
    service_client: Any | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    providers = get_cloud_connector_catalog()
    enabled_providers = [provider["id"] for provider in providers if provider["watchdog_enabled"]]
    configured_providers = [provider["id"] for provider in providers if provider["configured"]]
    base_payload = {
        "enabled": _as_bool(settings.CONNECTOR_WATCHDOG_ENABLED),
        "poll_interval_seconds": settings.CONNECTOR_POLL_INTERVAL_SECONDS,
        "configured_provider_count": len(configured_providers),
        "watchdog_provider_count": len(enabled_providers),
        "configured_providers": configured_providers,
        "watchdog_providers": enabled_providers,
        "connected_provider_count": 0,
        "active_target_count": 0,
        "pending_target_count": 0,
        "synced_target_count": 0,
        "fallback_provider_count": 0,
        "operational_state": "disabled" if not _as_bool(settings.CONNECTOR_WATCHDOG_ENABLED) else "idle",
        "summary": (
            "Vigilancia cloud deshabilitada en entorno."
            if not _as_bool(settings.CONNECTOR_WATCHDOG_ENABLED)
            else "Watchdog listo, sin archivos vigilados todavía."
        ),
        "last_activity_at": None,
        "provider_states": [],
    }

    if not service_client or not user_id:
        return base_payload

    provider_states = _build_watchdog_provider_states(
        providers=providers,
        service_client=service_client,
        user_id=user_id,
    )
    runtime_summary = _summarize_watchdog_runtime(
        enabled=base_payload["enabled"],
        provider_states=provider_states,
    )
    return {
        **base_payload,
        **runtime_summary,
        "provider_states": provider_states,
    }
