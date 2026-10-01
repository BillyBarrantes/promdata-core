from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException


GOVERNANCE_SCHEMA_VERSION = "1.0"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_user_team_scope(
    *,
    user_id: str,
    service_client: Any,
    explicit_team_id: str | None = None,
) -> str | None:
    """Resuelve de forma unívoca el equipo activo del usuario sin ambigüedades.

    Reglas de gobernanza multi-tenant:
    1. Si explicit_team_id está definido: valida que el usuario pertenezca a ese equipo.
       Si no pertenece, lanza HTTPException(403).
    2. Si explicit_team_id es None:
       - Si el usuario pertenece a 0 equipos: Retorna None (ámbito individual / B2C estándar).
       - Si el usuario pertenece a 1 equipo: Retorna dicho team_id.
       - Si el usuario pertenece a 2 o más equipos: Lanza HTTPException(400) exigiendo
         que la petición especifique explícitamente el team_id (elimina el riesgo de fuga
         por selección ciega de .limit(1)).
    """
    if explicit_team_id:
        try:
            resp = service_client.table("team_members") \
                .select("team_id") \
                .eq("user_id", user_id) \
                .eq("team_id", explicit_team_id) \
                .limit(1) \
                .execute()
            if not resp.data:
                raise HTTPException(
                    status_code=403,
                    detail=f"El usuario no pertenece al equipo especificado '{explicit_team_id}'.",
                )
            return explicit_team_id
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Error validando pertenencia a equipo: {str(exc)[:120]}",
            )

    try:
        response = service_client.table("team_members") \
            .select("team_id") \
            .eq("user_id", user_id) \
            .limit(2) \
            .execute()
        rows = response.data or []
        if not rows:
            return None
        if len(rows) > 1:
            raise HTTPException(
                status_code=400,
                detail="Alcance de equipo ambiguo: el usuario pertenece a múltiples equipos y la petición no especificó 'team_id'.",
            )
        team_id = rows[0].get("team_id")
        return str(team_id) if team_id else None
    except HTTPException:
        raise
    except Exception:
        return None



def get_user_uploaded_file_scope_or_404(
    *,
    user_id: str,
    team_id: str | None,
    file_id: str,
    service_client: Any,
) -> dict[str, Any]:
    response = service_client.table("uploaded_files") \
        .select("id, user_id, team_id, file_name, storage_path, created_at") \
        .eq("id", file_id) \
        .eq("user_id", user_id) \
        .limit(1) \
        .execute()

    if not response.data:
        raise HTTPException(status_code=404, detail="No se encontró el archivo solicitado.")

    row = response.data[0]
    row_team_id = str(row.get("team_id") or "").strip() or None
    if team_id and row_team_id and row_team_id != team_id:
        raise HTTPException(status_code=403, detail="El archivo no pertenece al equipo activo del usuario.")

    return row


def get_user_presentation_scope_or_404(
    *,
    user_id: str,
    team_id: str | None,
    presentation_id: str,
    service_client: Any,
) -> dict[str, Any]:
    response = service_client.table("presentations") \
        .select("id, user_id, file_id, name, created_at") \
        .eq("id", presentation_id) \
        .eq("user_id", user_id) \
        .limit(1) \
        .execute()

    if not response.data:
        raise HTTPException(status_code=404, detail="No se encontró la presentación solicitada.")

    row = response.data[0]
    linked_file_id = str(row.get("file_id") or "").strip()
    if linked_file_id:
        get_user_uploaded_file_scope_or_404(
            user_id=user_id,
            team_id=team_id,
            file_id=linked_file_id,
            service_client=service_client,
        )
    return row


def get_user_report_scope_or_404(
    *,
    user_id: str,
    team_id: str | None,
    report_id: str,
    service_client: Any,
) -> dict[str, Any]:
    response = service_client.table("saved_reports") \
        .select("id, user_id, file_id, presentation_id, title, content, created_at") \
        .eq("id", report_id) \
        .eq("user_id", user_id) \
        .limit(1) \
        .execute()

    if not response.data:
        raise HTTPException(status_code=404, detail="No se encontró el reporte solicitado.")

    row = response.data[0]
    linked_file_id = str(row.get("file_id") or "").strip()
    if linked_file_id:
        get_user_uploaded_file_scope_or_404(
            user_id=user_id,
            team_id=team_id,
            file_id=linked_file_id,
            service_client=service_client,
        )

    linked_presentation_id = str(row.get("presentation_id") or "").strip()
    if linked_presentation_id:
        get_user_presentation_scope_or_404(
            user_id=user_id,
            team_id=team_id,
            presentation_id=linked_presentation_id,
            service_client=service_client,
        )

    return row


def build_report_governance_block(
    *,
    existing_block: dict[str, Any] | None = None,
    user_id: str,
    team_id: str | None,
    file_id: str | None,
    presentation_id: str | None,
    revision_kind: str,
    increment_layout_revision: bool = False,
    increment_content_revision: bool = False,
) -> dict[str, Any]:
    previous = existing_block if isinstance(existing_block, dict) else {}

    layout_revision = int(previous.get("layout_revision") or 0)
    content_revision = int(previous.get("content_revision") or 0)
    revision = int(previous.get("revision") or 0)

    if not previous:
        layout_revision = 0
        content_revision = 1
        revision = 1
    else:
        if increment_layout_revision:
            layout_revision += 1
        if increment_content_revision:
            content_revision += 1
        if increment_layout_revision or increment_content_revision:
            revision += 1

    return {
        "schema_version": GOVERNANCE_SCHEMA_VERSION,
        "owner_user_id": user_id,
        "team_id": team_id,
        "file_id": file_id,
        "presentation_id": presentation_id,
        "access_scope": "user_team_bound" if team_id else "user_bound",
        "revision": revision,
        "content_revision": content_revision,
        "layout_revision": layout_revision,
        "last_revision_kind": revision_kind,
        "created_at": previous.get("created_at") or _now_iso(),
        "updated_at": _now_iso(),
        "updated_by": user_id,
    }


def stamp_report_content_governance(
    *,
    content: dict[str, Any] | None,
    user_id: str,
    team_id: str | None,
    file_id: str | None,
    presentation_id: str | None,
    revision_kind: str,
    increment_layout_revision: bool = False,
    increment_content_revision: bool = False,
) -> dict[str, Any]:
    base_content = dict(content) if isinstance(content, dict) else {}
    existing_block = base_content.get("governance") if isinstance(base_content.get("governance"), dict) else {}
    base_content["governance"] = build_report_governance_block(
        existing_block=existing_block,
        user_id=user_id,
        team_id=team_id,
        file_id=file_id,
        presentation_id=presentation_id,
        revision_kind=revision_kind,
        increment_layout_revision=increment_layout_revision,
        increment_content_revision=increment_content_revision,
    )
    return base_content


def build_document_governance_metadata(
    *,
    metadata: dict[str, Any] | None,
    user_id: str,
    team_id: str,
    revision_kind: str,
    increment_index_revision: bool = False,
    increment_revision: bool = False,
) -> dict[str, Any]:
    base_metadata = dict(metadata) if isinstance(metadata, dict) else {}
    existing_block = base_metadata.get("governance") if isinstance(base_metadata.get("governance"), dict) else {}
    index_revision = int(existing_block.get("index_revision") or 0)
    revision = int(existing_block.get("revision") or 0)

    if not existing_block:
        index_revision = 0
        revision = 1
    elif increment_index_revision:
        index_revision += 1
        revision += 1
    elif increment_revision:
        revision += 1

    base_metadata["governance"] = {
        "schema_version": GOVERNANCE_SCHEMA_VERSION,
        "owner_user_id": user_id,
        "team_id": team_id,
        "access_scope": "team_bound",
        "revision": revision,
        "index_revision": index_revision,
        "last_revision_kind": revision_kind,
        "created_at": existing_block.get("created_at") or _now_iso(),
        "updated_at": _now_iso(),
        "updated_by": user_id,
    }
    return base_metadata
