from __future__ import annotations

from base64 import urlsafe_b64encode
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import re
import secrets
from typing import Any
from urllib.parse import urlencode, urlparse

import requests

from app.core.config import settings
from app.core.structured_logging import emit_structured_log


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


def _split_scopes(raw_scopes: str | list[str] | None) -> list[str]:
    if isinstance(raw_scopes, list):
        return [str(scope).strip() for scope in raw_scopes if str(scope).strip()]
    if not raw_scopes:
        return []
    return [scope.strip() for scope in str(raw_scopes).replace(",", " ").split() if scope.strip()]


def _trim_base_url(raw_url: str, fallback: str) -> str:
    candidate = str(raw_url or "").strip().rstrip("/")
    if not candidate:
        return fallback.rstrip("/")
    return candidate


def _sanitize_return_to_url(raw_url: str | None) -> str:
    default_url = get_default_connector_return_to()
    if not raw_url:
        return default_url
    parsed = urlparse(str(raw_url).strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return default_url
    return str(raw_url).strip()


def get_default_connector_return_to() -> str:
    base_url = _trim_base_url(settings.FRONTEND_APP_URL, "http://localhost:3000")
    return base_url if base_url.endswith("/cargar-datos") else f"{base_url}/cargar-datos"


def _generate_code_verifier() -> str:
    return secrets.token_urlsafe(72)[:96]


def _generate_state() -> str:
    return secrets.token_urlsafe(48)


def _build_code_challenge(code_verifier: str) -> str:
    digest = sha256(code_verifier.encode("utf-8")).digest()
    return urlsafe_b64encode(digest).decode("utf-8").rstrip("=")


def _get_provider_registry() -> dict[str, dict[str, Any]]:
    microsoft_tenant = str(settings.MICROSOFT_ONEDRIVE_TENANT_ID or "common").strip()
    return {
        "google_drive": {
            "route_slug": "google",
            "display_name": "Google Drive",
            "client_id": settings.GOOGLE_DRIVE_CLIENT_ID,
            "client_secret": settings.GOOGLE_DRIVE_CLIENT_SECRET,
            "scopes": _split_scopes(settings.GOOGLE_DRIVE_SCOPES),
            "auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
            "token_url": "https://oauth2.googleapis.com/token",
            "profile_url": "https://www.googleapis.com/oauth2/v3/userinfo",
        },
        "onedrive": {
            "route_slug": "microsoft",
            "display_name": "Microsoft OneDrive",
            "client_id": settings.MICROSOFT_ONEDRIVE_CLIENT_ID,
            "client_secret": settings.MICROSOFT_ONEDRIVE_CLIENT_SECRET,
            "scopes": _split_scopes(settings.MICROSOFT_ONEDRIVE_SCOPES),
            "auth_url": f"https://login.microsoftonline.com/{microsoft_tenant}/oauth2/v2.0/authorize",
            "token_url": f"https://login.microsoftonline.com/{microsoft_tenant}/oauth2/v2.0/token",
            "profile_url": "https://graph.microsoft.com/v1.0/me?$select=id,displayName,userPrincipalName,mail",
        },
    }


def get_oauth_provider(provider_id: str) -> dict[str, Any]:
    registry = _get_provider_registry()
    if provider_id not in registry:
        raise ValueError(f"Proveedor OAuth no soportado: {provider_id}")
    provider = registry[provider_id]
    if not provider["client_id"] or not provider["client_secret"]:
        raise ValueError(f"Proveedor {provider_id} sin credenciales configuradas")
    return provider


def get_provider_from_route_slug(route_slug: str) -> str:
    normalized = str(route_slug or "").strip().lower()
    for provider_id, provider in _get_provider_registry().items():
        if provider["route_slug"] == normalized:
            return provider_id
    raise ValueError(f"Slug OAuth no soportado: {route_slug}")


def build_provider_callback_url(provider_id: str) -> str:
    provider = get_oauth_provider(provider_id)
    backend_base = _trim_base_url(settings.BACKEND_PUBLIC_URL, "http://localhost:8000")
    return f"{backend_base}/api/v1/auth/{provider['route_slug']}/callback"


def build_oauth_authorization_url(provider_id: str, *, state: str, code_verifier: str) -> str:
    provider = get_oauth_provider(provider_id)
    callback_url = build_provider_callback_url(provider_id)
    common_params = {
        "client_id": provider["client_id"],
        "redirect_uri": callback_url,
        "response_type": "code",
        "scope": " ".join(provider["scopes"]),
        "state": state,
        "code_challenge": _build_code_challenge(code_verifier),
        "code_challenge_method": "S256",
    }

    if provider_id == "google_drive":
        common_params.update({
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
        })
    elif provider_id == "onedrive":
        common_params.update({"response_mode": "query"})

    return f"{provider['auth_url']}?{urlencode(common_params)}"


def build_frontend_oauth_redirect_url(
    provider_id: str,
    *,
    status: str,
    message: str | None = None,
    redirect_to: str | None = None,
) -> str:
    target = _sanitize_return_to_url(redirect_to)
    params = {
        "oauth_provider": provider_id,
        "oauth_status": status,
    }
    if message:
        params["oauth_message"] = message[:200]
    separator = "&" if "?" in target else "?"
    return f"{target}{separator}{urlencode(params)}"


def create_oauth_authorization_request(
    provider_id: str,
    *,
    user_id: str,
    redirect_to: str | None,
    service_client: Any,
) -> dict[str, Any]:
    get_oauth_provider(provider_id)
    code_verifier = _generate_code_verifier()
    state = _generate_state()
    expires_at = _now_utc() + timedelta(seconds=settings.OAUTH_STATE_TTL_SECONDS)
    safe_redirect_to = _sanitize_return_to_url(redirect_to)

    service_client.table("cloud_oauth_states").insert({
        "user_id": user_id,
        "provider": provider_id,
        "state": state,
        "code_verifier": code_verifier,
        "redirect_to": safe_redirect_to,
        "expires_at": expires_at.isoformat(),
        "status": "pending",
    }).execute()

    auth_url = build_oauth_authorization_url(provider_id, state=state, code_verifier=code_verifier)
    emit_structured_log(
        "oauth_state_persisted",
        provider=provider_id,
        user_id=user_id,
        expires_at=expires_at,
        redirect_to=safe_redirect_to,
    )
    return {
        "provider": provider_id,
        "auth_url": auth_url,
        "state_expires_at": expires_at.isoformat(),
        "return_to": safe_redirect_to,
    }


def get_oauth_state_record(provider_id: str, state: str, service_client: Any) -> dict[str, Any] | None:
    response = service_client.table("cloud_oauth_states") \
        .select("*") \
        .eq("provider", provider_id) \
        .eq("state", state) \
        .limit(1) \
        .execute()
    if response.data:
        return response.data[0]
    return None


def validate_oauth_state_record(record: dict[str, Any]) -> None:
    if not record:
        raise ValueError("State OAuth inválido o inexistente")
    if record.get("consumed_at"):
        raise ValueError("State OAuth ya consumido")
    expires_at = _parse_iso_datetime(record.get("expires_at"))
    if not expires_at or expires_at <= _now_utc():
        raise ValueError("State OAuth expirado")


def mark_oauth_state_result(
    state_id: str,
    *,
    service_client: Any,
    status: str,
    error_message: str | None = None,
) -> None:
    payload: dict[str, Any] = {
        "status": status,
        "consumed_at": _now_utc().isoformat(),
        "error_message": error_message,
    }
    service_client.table("cloud_oauth_states").update(payload).eq("id", state_id).execute()


def exchange_oauth_code_for_tokens(
    provider_id: str,
    *,
    code: str,
    code_verifier: str,
) -> dict[str, Any]:
    provider = get_oauth_provider(provider_id)
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": build_provider_callback_url(provider_id),
        "client_id": provider["client_id"],
        "client_secret": provider["client_secret"],
        "code_verifier": code_verifier,
    }
    response = requests.post(provider["token_url"], data=payload, timeout=20)
    if not response.ok:
        raise ValueError(f"Token exchange falló ({provider_id}): {response.text[:400]}")
    return response.json()


def fetch_provider_account_profile(provider_id: str, *, access_token: str) -> dict[str, Any]:
    provider = get_oauth_provider(provider_id)
    response = requests.get(
        provider["profile_url"],
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=20,
    )
    if not response.ok:
        raise ValueError(f"No se pudo resolver identidad OAuth ({provider_id}): {response.text[:400]}")
    profile = response.json()
    if provider_id == "google_drive":
        return {
            "external_account_id": profile.get("sub"),
            "external_account_email": profile.get("email"),
            "external_account_name": profile.get("name"),
            "metadata": {
                "picture": profile.get("picture"),
                "email_verified": profile.get("email_verified"),
            },
        }
    return {
        "external_account_id": profile.get("id"),
        "external_account_email": profile.get("mail") or profile.get("userPrincipalName"),
        "external_account_name": profile.get("displayName"),
        "metadata": {
            "user_principal_name": profile.get("userPrincipalName"),
        },
    }


def _normalize_token_scope_payload(token_payload: dict[str, Any], provider_id: str) -> list[str]:
    scopes = _split_scopes(token_payload.get("scope"))
    if scopes:
        return scopes
    return get_oauth_provider(provider_id)["scopes"]


SUPPORTED_DATA_EXTENSIONS = {"xlsx", "csv"}
GOOGLE_SHEETS_MIME = "application/vnd.google-apps.spreadsheet"
GOOGLE_FOLDER_MIME = "application/vnd.google-apps.folder"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CSV_MIME_CANDIDATES = {
    "text/csv",
    "application/csv",
    "text/comma-separated-values",
    "application/vnd.ms-excel",
}


def _extract_extension(file_name: str | None) -> str | None:
    normalized_name = str(file_name or "").strip().lower()
    if "." not in normalized_name:
        return None
    return normalized_name.rsplit(".", 1)[-1]


def _is_supported_data_file(file_name: str | None, mime_type: str | None) -> tuple[bool, str | None, str | None]:
    normalized_mime = str(mime_type or "").strip().lower() or None
    extension = _extract_extension(file_name)

    if normalized_mime == GOOGLE_SHEETS_MIME:
        return True, extension or "gsheet", "google_sheet"
    if normalized_mime == XLSX_MIME:
        return True, "xlsx", "binary_file"
    if normalized_mime in CSV_MIME_CANDIDATES:
        return True, "csv" if extension == "csv" or extension is None else extension, "binary_file"
    if extension in SUPPORTED_DATA_EXTENSIONS:
        return True, extension, "binary_file"
    return False, extension, None


def _coerce_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _extract_graph_next_cursor(next_link: str | None) -> str | None:
    if not next_link:
        return None
    parsed = urlparse(next_link)
    query = dict(item.split("=", 1) for item in parsed.query.split("&") if "=" in item)
    return query.get("$skiptoken")


def _normalize_cloud_search_term(raw_value: str | None) -> str | None:
    normalized = str(raw_value or "").strip()
    return normalized if normalized else None


def build_remote_ingest_descriptor(item: dict[str, Any]) -> dict[str, Any]:
    provider_id = str(item.get("provider") or "").strip()
    if item.get("item_type") == "folder":
        raise ValueError("No se puede construir ingesta para carpetas")

    source_type = item.get("ingest_source_type")
    file_name = str(item.get("name") or "archivo").strip()
    extension = item.get("extension")

    if provider_id == "google_drive":
        if source_type == "google_sheet":
            export_mime = XLSX_MIME
            export_name = file_name if file_name.lower().endswith(".xlsx") else f"{file_name}.xlsx"
            return {
                "provider": provider_id,
                "item_id": item["id"],
                "download_url": f"https://www.googleapis.com/drive/v3/files/{item['id']}/export?mimeType={export_mime}",
                "mime_type": export_mime,
                "file_name": export_name,
                "mode": "google_export",
            }
        target_name = file_name if extension else f"{file_name}.bin"
        return {
            "provider": provider_id,
            "item_id": item["id"],
            "download_url": f"https://www.googleapis.com/drive/v3/files/{item['id']}?alt=media",
            "mime_type": item.get("mime_type"),
            "file_name": target_name,
            "mode": "binary_download",
        }

    if provider_id == "onedrive":
        if not item.get("download_url"):
            raise ValueError("OneDrive no devolvió download_url para el archivo seleccionado")
        return {
            "provider": provider_id,
            "item_id": item["id"],
            "download_url": item["download_url"],
            "mime_type": item.get("mime_type"),
            "file_name": file_name,
            "mode": "binary_download",
        }

    raise ValueError(f"Proveedor no soportado para ingesta: {provider_id}")


def _sanitize_storage_file_name(file_name: str) -> str:
    normalized = re.sub(r"[^\w.\- ]+", "_", str(file_name or "").strip())
    normalized = re.sub(r"\s+", "_", normalized).strip("._")
    return normalized or "archivo_remoto"


def _build_connection_payload(
    *,
    provider_id: str,
    user_id: str,
    token_payload: dict[str, Any],
    profile_payload: dict[str, Any],
    existing_connection: dict[str, Any] | None,
) -> dict[str, Any]:
    now_utc = _now_utc()
    # [FIX 2026-06-11] Si el proveedor no devuelve expires_in (raro, pero
    # Google puede omitirlo en algunos grant types), asumimos el TTL
    # estandar de Google (3600s). Sin esto, expires_at=None y
    # connection_needs_refresh() nunca devuelve True, lo que deja
    # el access_token permanente y rompe la conexion al expirar.
    raw_expires_in = token_payload.get("expires_in")
    try:
        expires_in = int(raw_expires_in) if raw_expires_in is not None else 3600
    except (TypeError, ValueError):
        expires_in = 3600
    expires_at = now_utc + timedelta(seconds=expires_in)
    refresh_token = token_payload.get("refresh_token") or (existing_connection or {}).get("refresh_token")
    metadata = {
        **((existing_connection or {}).get("metadata") or {}),
        **(profile_payload.get("metadata") or {}),
        "connected_via": "oauth2",
    }
    payload = {
        "user_id": user_id,
        "provider": provider_id,
        "external_account_id": profile_payload.get("external_account_id"),
        "external_account_email": profile_payload.get("external_account_email"),
        "external_account_name": profile_payload.get("external_account_name"),
        "access_token": token_payload.get("access_token"),
        "refresh_token": refresh_token,
        "token_type": token_payload.get("token_type"),
        "scopes": _normalize_token_scope_payload(token_payload, provider_id),
        "expires_at": expires_at.isoformat() if expires_at else None,
        "status": "active",
        "metadata": metadata,
        "last_refreshed_at": now_utc.isoformat(),
        "last_error_at": None,
        "last_error": None,
    }
    return payload


def get_user_oauth_connections(user_id: str, service_client: Any) -> list[dict[str, Any]]:
    response = service_client.table("cloud_oauth_connections") \
        .select("id, provider, external_account_email, external_account_name, status, last_refreshed_at") \
        .eq("user_id", user_id) \
        .execute()
    return response.data or []


def get_user_oauth_connection(user_id: str, provider_id: str, service_client: Any) -> dict[str, Any] | None:
    response = service_client.table("cloud_oauth_connections") \
        .select("*") \
        .eq("user_id", user_id) \
        .eq("provider", provider_id) \
        .limit(1) \
        .execute()
    if response.data:
        return response.data[0]
    return None


def _list_google_drive_items(
    *,
    connection_row: dict[str, Any],
    limit: int,
    cursor: str | None,
    parent_id: str | None,
) -> dict[str, Any]:
    access_token = connection_row.get("access_token")
    folder_id = parent_id or "root"
    params = {
        "pageSize": str(max(1, min(limit, 50))),
        "orderBy": "folder,name_natural,modifiedTime desc",
        "fields": "nextPageToken,files(id,name,mimeType,modifiedTime,size,webViewLink,iconLink)",
        "q": f"trashed = false and '{folder_id}' in parents",
    }
    if cursor:
        params["pageToken"] = cursor
    response = requests.get(
        "https://www.googleapis.com/drive/v3/files",
        params=params,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=20,
    )
    if not response.ok:
        raise ValueError(f"No se pudo listar Google Drive: {response.text[:400]}")
    payload = response.json()
    files = []
    for item in payload.get("files", []):
        mime_type = item.get("mimeType")
        if mime_type == GOOGLE_FOLDER_MIME:
            files.append({
                "id": item["id"],
                "name": item.get("name") or "Sin nombre",
                "provider": "google_drive",
                "item_type": "folder",
                "extension": None,
                "mime_type": mime_type,
                "size_bytes": None,
                "modified_at": item.get("modifiedTime"),
                "web_url": item.get("webViewLink"),
                "download_url": None,
                "supports_analysis": False,
                "ingest_source_type": None,
            })
            continue

        is_supported, extension, source_type = _is_supported_data_file(item.get("name"), mime_type)
        if not is_supported:
            continue

        file_payload = {
            "id": item["id"],
            "name": item.get("name") or "Sin nombre",
            "provider": "google_drive",
            "item_type": "file",
            "extension": extension,
            "mime_type": mime_type,
            "size_bytes": _coerce_int(item.get("size")),
            "modified_at": item.get("modifiedTime"),
            "web_url": item.get("webViewLink"),
            "download_url": None,
            "supports_analysis": True,
            "ingest_source_type": source_type,
        }
        file_payload["download_url"] = build_remote_ingest_descriptor(file_payload)["download_url"]
        files.append(file_payload)

    return {
        "provider": "google_drive",
        "connected_account_email": connection_row.get("external_account_email"),
        "files": files,
        "next_cursor": payload.get("nextPageToken"),
        "current_folder_id": None if folder_id == "root" else folder_id,
    }


def _list_google_drive_flat_files(
    *,
    connection_row: dict[str, Any],
    limit: int,
    cursor: str | None,
    search: str | None,
) -> dict[str, Any]:
    access_token = connection_row.get("access_token")
    normalized_search = _normalize_cloud_search_term(search)
    q_parts = [
        "trashed = false",
        "("
        f"mimeType = '{GOOGLE_SHEETS_MIME}' or "
        f"mimeType = '{XLSX_MIME}' or "
        "mimeType = 'text/csv' or "
        "mimeType = 'application/csv' or "
        "mimeType = 'text/comma-separated-values' or "
        "mimeType = 'application/vnd.ms-excel'"
        ")",
    ]
    if normalized_search:
        escaped_search = normalized_search.replace("'", "\\'")
        q_parts.append(f"name contains '{escaped_search}'")

    params = {
        "pageSize": str(max(1, min(limit, 50))),
        "orderBy": "modifiedTime desc,name_natural",
        "fields": "nextPageToken,files(id,name,mimeType,modifiedTime,size,webViewLink)",
        "q": " and ".join(q_parts),
    }
    if cursor:
        params["pageToken"] = cursor

    response = requests.get(
        "https://www.googleapis.com/drive/v3/files",
        params=params,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=20,
    )
    if not response.ok:
        raise ValueError(f"No se pudo listar Google Drive: {response.text[:400]}")

    payload = response.json()
    files = []
    for item in payload.get("files", []):
        mime_type = item.get("mimeType")
        is_supported, extension, source_type = _is_supported_data_file(item.get("name"), mime_type)
        if not is_supported:
            continue
        file_payload = {
            "id": item["id"],
            "name": item.get("name") or "Sin nombre",
            "provider": "google_drive",
            "item_type": "file",
            "extension": extension,
            "mime_type": mime_type,
            "size_bytes": _coerce_int(item.get("size")),
            "modified_at": item.get("modifiedTime"),
            "web_url": item.get("webViewLink"),
            "download_url": None,
            "supports_analysis": True,
            "ingest_source_type": source_type,
        }
        file_payload["download_url"] = build_remote_ingest_descriptor(file_payload)["download_url"]
        files.append(file_payload)

    return {
        "provider": "google_drive",
        "connected_account_email": connection_row.get("external_account_email"),
        "files": files,
        "next_cursor": payload.get("nextPageToken"),
        "current_folder_id": None,
    }


def _list_onedrive_items(
    *,
    connection_row: dict[str, Any],
    limit: int,
    cursor: str | None,
    parent_id: str | None,
) -> dict[str, Any]:
    access_token = connection_row.get("access_token")
    params = {
        "$top": str(max(1, min(limit, 50))),
        "$select": "id,name,size,eTag,cTag,file,folder,lastModifiedDateTime,webUrl,@microsoft.graph.downloadUrl",
    }
    if cursor:
        params["$skiptoken"] = cursor
    endpoint = (
        "https://graph.microsoft.com/v1.0/me/drive/root/children"
        if not parent_id
        else f"https://graph.microsoft.com/v1.0/me/drive/items/{parent_id}/children"
    )
    response = requests.get(
        endpoint,
        params=params,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=20,
    )
    if not response.ok:
        raise ValueError(f"No se pudo listar OneDrive: {response.text[:400]}")
    payload = response.json()
    files = []
    for item in payload.get("value", []):
        folder_info = item.get("folder") or {}
        if folder_info:
            child_count = _coerce_int(folder_info.get("childCount"))
            if child_count == 0:
                continue
            files.append({
                "id": item["id"],
                "name": item.get("name") or "Sin nombre",
                "provider": "onedrive",
                "item_type": "folder",
                "extension": None,
                "mime_type": None,
                "size_bytes": None,
                "modified_at": item.get("lastModifiedDateTime"),
                "web_url": item.get("webUrl"),
                "download_url": None,
                "supports_analysis": False,
                "ingest_source_type": None,
            })
            continue

        mime_type = (item.get("file") or {}).get("mimeType")
        is_supported, extension, source_type = _is_supported_data_file(item.get("name"), mime_type)
        if not is_supported:
            continue

        files.append({
            "id": item["id"],
            "name": item.get("name") or "Sin nombre",
            "provider": "onedrive",
            "item_type": "file",
            "extension": extension,
            "mime_type": mime_type,
            "size_bytes": _coerce_int(item.get("size")),
            "etag": item.get("eTag"),
            "ctag": item.get("cTag"),
            "modified_at": item.get("lastModifiedDateTime"),
            "web_url": item.get("webUrl"),
            "download_url": item.get("@microsoft.graph.downloadUrl"),
            "supports_analysis": True,
            "ingest_source_type": source_type,
        })

    return {
        "provider": "onedrive",
        "connected_account_email": connection_row.get("external_account_email"),
        "files": files,
        "next_cursor": _extract_graph_next_cursor(payload.get("@odata.nextLink")),
        "current_folder_id": parent_id,
    }


def _normalize_onedrive_item(item: dict[str, Any]) -> dict[str, Any]:
    candidate = item.get("remoteItem") if isinstance(item.get("remoteItem"), dict) else item
    return candidate if isinstance(candidate, dict) else item


def _build_onedrive_flat_file(item: dict[str, Any]) -> dict[str, Any] | None:
    if item.get("folder"):
        return None
    mime_type = (item.get("file") or {}).get("mimeType")
    is_supported, extension, source_type = _is_supported_data_file(item.get("name"), mime_type)
    if not is_supported:
        return None
    return {
        "id": item["id"],
        "name": item.get("name") or "Sin nombre",
        "provider": "onedrive",
        "item_type": "file",
        "extension": extension,
        "mime_type": mime_type,
        "size_bytes": _coerce_int(item.get("size")),
        "modified_at": item.get("lastModifiedDateTime"),
        "web_url": item.get("webUrl"),
        "download_url": item.get("@microsoft.graph.downloadUrl"),
        "supports_analysis": True,
        "ingest_source_type": source_type,
    }


def _fetch_onedrive_search_payload(
    *,
    access_token: str,
    query_text: str,
    limit: int,
    cursor: str | None,
) -> dict[str, Any]:
    endpoint = f"https://graph.microsoft.com/v1.0/me/drive/root/search(q='{query_text}')"
    params: dict[str, str] = {
        "$top": str(max(1, min(limit, 50))),
        "$orderby": "lastModifiedDateTime desc",
        "$select": "id,name,size,eTag,cTag,file,folder,lastModifiedDateTime,webUrl,@microsoft.graph.downloadUrl,remoteItem",
    }
    if cursor:
        params["$skiptoken"] = cursor

    response = requests.get(
        endpoint,
        params=params,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=20,
    )
    if not response.ok:
        raise ValueError(f"No se pudo listar OneDrive: {response.text[:400]}")
    return response.json()


def _list_onedrive_flat_files(
    *,
    connection_row: dict[str, Any],
    limit: int,
    cursor: str | None,
    search: str | None,
) -> dict[str, Any]:
    access_token = connection_row.get("access_token")
    normalized_search = _normalize_cloud_search_term(search)

    files_by_id: dict[str, dict[str, Any]] = {}
    next_cursor: str | None = None

    if normalized_search:
        escaped_search = normalized_search.replace("'", "''")
        payload = _fetch_onedrive_search_payload(
            access_token=access_token,
            query_text=escaped_search,
            limit=limit,
            cursor=cursor,
        )
        next_cursor = _extract_graph_next_cursor(payload.get("@odata.nextLink"))
        raw_items = payload.get("value", [])
        for raw_item in raw_items:
            normalized_item = _normalize_onedrive_item(raw_item)
            file_payload = _build_onedrive_flat_file(normalized_item)
            if file_payload:
                files_by_id[file_payload["id"]] = file_payload
    else:
        # OneDrive search is not deterministic with a single extension token.
        # We merge multiple broad queries and deduplicate by item id so recent
        # data files are not omitted by Graph indexing quirks. We also merge the
        # current root listing because some files are visible in /children before
        # they become discoverable through /search.
        default_queries = (".xlsx", "xlsx", ".csv", "csv")
        for extension_query in default_queries:
            payload = _fetch_onedrive_search_payload(
                access_token=access_token,
                query_text=extension_query,
                limit=limit,
                cursor=None,
            )
            for raw_item in payload.get("value", []):
                normalized_item = _normalize_onedrive_item(raw_item)
                file_payload = _build_onedrive_flat_file(normalized_item)
                if file_payload:
                    files_by_id[file_payload["id"]] = file_payload

        root_listing = _list_onedrive_items(
            connection_row=connection_row,
            limit=max(limit, 100),
            cursor=None,
            parent_id=None,
        )
        for root_item in root_listing.get("files", []):
            if root_item.get("item_type") != "file" or not root_item.get("supports_analysis"):
                continue
            files_by_id[root_item["id"]] = root_item

    files = list(files_by_id.values())
    files.sort(key=lambda item: item.get("modified_at") or "", reverse=True)
    trimmed_files = files[: max(1, min(limit, 50))]
    emit_structured_log(
        "onedrive_flat_listing_built",
        user_id=connection_row.get("user_id"),
        connection_id=connection_row.get("id"),
        search=normalized_search,
        discovered_count=len(files),
        returned_count=len(trimmed_files),
    )
    return {
        "provider": "onedrive",
        "connected_account_email": connection_row.get("external_account_email"),
        "files": trimmed_files,
        "next_cursor": next_cursor,
        "current_folder_id": None,
    }


def _get_google_drive_item(connection_row: dict[str, Any], item_id: str) -> dict[str, Any]:
    access_token = connection_row.get("access_token")
    response = requests.get(
        f"https://www.googleapis.com/drive/v3/files/{item_id}",
        params={"fields": "id,name,mimeType,modifiedTime,size,webViewLink"},
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=20,
    )
    if not response.ok:
        raise ValueError(f"No se pudo resolver archivo de Google Drive: {response.text[:400]}")
    item = response.json()
    mime_type = item.get("mimeType")
    if mime_type == GOOGLE_FOLDER_MIME:
        raise ValueError("La selección corresponde a una carpeta y no a un archivo importable")
    is_supported, extension, source_type = _is_supported_data_file(item.get("name"), mime_type)
    if not is_supported:
        raise ValueError("El archivo seleccionado no es compatible para análisis")
    file_payload = {
        "id": item["id"],
        "name": item.get("name") or "Sin nombre",
        "provider": "google_drive",
        "item_type": "file",
        "extension": extension,
        "mime_type": mime_type,
        "size_bytes": _coerce_int(item.get("size")),
        "modified_at": item.get("modifiedTime"),
        "web_url": item.get("webViewLink"),
        "download_url": None,
        "supports_analysis": True,
        "ingest_source_type": source_type,
    }
    file_payload["download_url"] = build_remote_ingest_descriptor(file_payload)["download_url"]
    return file_payload


def _get_onedrive_item(connection_row: dict[str, Any], item_id: str) -> dict[str, Any]:
    access_token = connection_row.get("access_token")
    response = requests.get(
        f"https://graph.microsoft.com/v1.0/me/drive/items/{item_id}",
        params={"$select": "id,name,size,eTag,cTag,file,folder,lastModifiedDateTime,webUrl,@microsoft.graph.downloadUrl"},
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=20,
    )
    if not response.ok:
        raise ValueError(f"No se pudo resolver archivo de OneDrive: {response.text[:400]}")
    item = response.json()
    if item.get("folder"):
        raise ValueError("La selección corresponde a una carpeta y no a un archivo importable")
    mime_type = (item.get("file") or {}).get("mimeType")
    is_supported, extension, source_type = _is_supported_data_file(item.get("name"), mime_type)
    if not is_supported:
        raise ValueError("El archivo seleccionado no es compatible para análisis")
    return {
        "id": item["id"],
        "name": item.get("name") or "Sin nombre",
        "provider": "onedrive",
        "item_type": "file",
        "extension": extension,
        "mime_type": mime_type,
        "size_bytes": _coerce_int(item.get("size")),
        "etag": item.get("eTag"),
        "ctag": item.get("cTag"),
        "modified_at": item.get("lastModifiedDateTime"),
        "web_url": item.get("webUrl"),
        "download_url": item.get("@microsoft.graph.downloadUrl"),
        "supports_analysis": True,
        "ingest_source_type": source_type,
    }


def _refresh_onedrive_download_url(access_token: str, item_id: str) -> str | None:
    response = requests.get(
        f"https://graph.microsoft.com/v1.0/me/drive/items/{item_id}",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=20,
    )
    if not response.ok:
        return None
    payload = response.json()
    return payload.get("@microsoft.graph.downloadUrl")


def get_provider_remote_file(
    provider_id: str,
    *,
    connection_row: dict[str, Any],
    service_client: Any,
    item_id: str,
) -> dict[str, Any]:
    active_connection = refresh_oauth_connection_tokens(connection_row, service_client)
    if provider_id == "google_drive":
        return _get_google_drive_item(active_connection, item_id)
    if provider_id == "onedrive":
        return _get_onedrive_item(active_connection, item_id)
    raise ValueError(f"Provider file lookup no soportado: {provider_id}")


def download_provider_remote_file(
    provider_id: str,
    *,
    connection_row: dict[str, Any],
    service_client: Any,
    item_id: str,
) -> dict[str, Any]:
    active_connection = refresh_oauth_connection_tokens(connection_row, service_client)
    remote_item = get_provider_remote_file(
        provider_id,
        connection_row=active_connection,
        service_client=service_client,
        item_id=item_id,
    )
    if provider_id == "onedrive" and not remote_item.get("download_url"):
        refreshed_download_url = _refresh_onedrive_download_url(active_connection.get("access_token"), item_id)
        if refreshed_download_url:
            remote_item = {
                **remote_item,
                "download_url": refreshed_download_url,
            }
    descriptor = build_remote_ingest_descriptor(remote_item)
    headers = {}
    if provider_id == "google_drive":
        headers["Authorization"] = f"Bearer {active_connection.get('access_token')}"
    response = requests.get(
        descriptor["download_url"],
        headers=headers,
        timeout=60,
    )
    if not response.ok:
        raise ValueError(f"No se pudo descargar el archivo remoto: {response.text[:400]}")
    return {
        "file_name": _sanitize_storage_file_name(descriptor["file_name"]),
        "mime_type": descriptor.get("mime_type") or remote_item.get("mime_type") or "application/octet-stream",
        "source_type": descriptor.get("mode") or remote_item.get("ingest_source_type") or "binary_download",
        "bytes": response.content,
        "remote_item": remote_item,
    }


def get_user_watch_target_counts(user_id: str, service_client: Any) -> dict[str, int]:
    response = service_client.table("cloud_watch_targets") \
        .select("provider") \
        .eq("user_id", user_id) \
        .eq("is_active", True) \
        .execute()
    counts: dict[str, int] = {}
    for row in response.data or []:
        provider = row.get("provider")
        if provider:
            counts[provider] = counts.get(provider, 0) + 1
    return counts


def upsert_oauth_connection(
    provider_id: str,
    *,
    user_id: str,
    token_payload: dict[str, Any],
    profile_payload: dict[str, Any],
    service_client: Any,
) -> dict[str, Any]:
    existing_response = service_client.table("cloud_oauth_connections") \
        .select("*") \
        .eq("user_id", user_id) \
        .eq("provider", provider_id) \
        .limit(1) \
        .execute()
    existing_connection = existing_response.data[0] if existing_response.data else None
    payload = _build_connection_payload(
        provider_id=provider_id,
        user_id=user_id,
        token_payload=token_payload,
        profile_payload=profile_payload,
        existing_connection=existing_connection,
    )
    response = service_client.table("cloud_oauth_connections") \
        .upsert(payload, on_conflict="user_id,provider") \
        .execute()
    connection = response.data[0] if response.data else payload
    emit_structured_log(
        "oauth_connection_upserted",
        provider=provider_id,
        user_id=user_id,
        external_account_email=connection.get("external_account_email"),
        status=connection.get("status"),
    )
    return connection


def connection_needs_refresh(connection_row: dict[str, Any]) -> bool:
    """
    Determina si una conexion OAuth necesita refresh del access_token.

    [FIX 2026-06-11] Si expires_at es None o no parseable, ANTES retornabamos
    False (asumiendo que la conexion estaba bien). Esto dejaba conexiones
    rotas silenciosamente: el access_token expiraba sin que nadie lo
    refrescara, hasta que Google rechazaba la llamada con 401.

    Comportamiento nuevo: si expires_at es None, retornamos True
    (forzar refresh). Es preferible refrescar innecesariamente a fallar
    silenciosamente.
    """
    expires_at = _parse_iso_datetime(connection_row.get("expires_at"))
    if not expires_at:
        return True
    skew_seconds = max(settings.OAUTH_TOKEN_REFRESH_SKEW_SECONDS, 0)
    return _now_utc() + timedelta(seconds=skew_seconds) >= expires_at


def refresh_oauth_connection_tokens(connection_row: dict[str, Any], service_client: Any) -> dict[str, Any]:
    provider_id = str(connection_row.get("provider") or "").strip()
    if not provider_id:
        raise ValueError("Conexión OAuth sin proveedor")
    if not connection_needs_refresh(connection_row):
        return connection_row
    if not connection_row.get("refresh_token"):
        raise ValueError(f"Conexión {provider_id} sin refresh token")

    provider = get_oauth_provider(provider_id)
    response = requests.post(
        provider["token_url"],
        data={
            "grant_type": "refresh_token",
            "refresh_token": connection_row["refresh_token"],
            "client_id": provider["client_id"],
            "client_secret": provider["client_secret"],
            "redirect_uri": build_provider_callback_url(provider_id),
        },
        timeout=20,
    )
    if not response.ok:
        error_message = response.text[:400]
        service_client.table("cloud_oauth_connections").update({
            "status": "error",
            "last_error_at": _now_utc().isoformat(),
            "last_error": error_message,
        }).eq("id", connection_row["id"]).execute()
        emit_structured_log(
            "oauth_token_refresh_error",
            level="error",
            provider=provider_id,
            connection_id=connection_row.get("id"),
            error=error_message,
        )
        raise ValueError(f"Refresh token falló ({provider_id}): {error_message}")

    token_payload = response.json()
    updated_payload = {
        "access_token": token_payload.get("access_token"),
        "refresh_token": token_payload.get("refresh_token") or connection_row.get("refresh_token"),
        "token_type": token_payload.get("token_type") or connection_row.get("token_type"),
        "scopes": _normalize_token_scope_payload(token_payload, provider_id),
        "expires_at": (
            _now_utc() + timedelta(seconds=int(token_payload.get("expires_in", 3600)))
        ).isoformat(),
        "status": "active",
        "last_refreshed_at": _now_utc().isoformat(),
        "last_error_at": None,
        "last_error": None,
    }
    refresh_response = service_client.table("cloud_oauth_connections") \
        .update(updated_payload) \
        .eq("id", connection_row["id"]) \
        .execute()
    refreshed = refresh_response.data[0] if refresh_response.data else {**connection_row, **updated_payload}
    emit_structured_log(
        "oauth_token_refreshed",
        provider=provider_id,
        connection_id=connection_row.get("id"),
        user_id=connection_row.get("user_id"),
    )
    return refreshed


def list_provider_remote_files(
    provider_id: str,
    *,
    connection_row: dict[str, Any],
    service_client: Any,
    limit: int = 50,
    cursor: str | None = None,
    search: str | None = None,
) -> dict[str, Any]:
    active_connection = refresh_oauth_connection_tokens(connection_row, service_client)
    access_token = active_connection.get("access_token")
    if not access_token:
        raise ValueError(f"Conexión {provider_id} sin access token")

    if provider_id == "google_drive":
        return _list_google_drive_flat_files(
            connection_row=active_connection,
            limit=limit,
            cursor=cursor,
            search=search,
        )

    if provider_id == "onedrive":
        return _list_onedrive_flat_files(
            connection_row=active_connection,
            limit=limit,
            cursor=cursor,
            search=search,
        )

    raise ValueError(f"Provider listing no soportado: {provider_id}")
