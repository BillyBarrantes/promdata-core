from __future__ import annotations

import json
from typing import Any

from fastapi import Request

from app.core.config import settings
from app.services.audit_logger import log_audit_event

_SKIP_PREFIXES = tuple(
    p.strip() for p in settings.AUDIT_LOG_SKIP_PATHS.split(",") if p.strip()
)


def _should_log(method: str, path: str) -> bool:
    if path.startswith(_SKIP_PREFIXES):
        return False
    return True


async def audit_middleware(request: Request, call_next: Any) -> Any:
    response = await call_next(request)

    method = request.method
    path = request.url.path

    if not _should_log(method, path):
        return response

    user_id: str | None = None
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        if token:
            try:
                from app.core.rate_limit import _extract_user_id_from_token
                user_id = _extract_user_id_from_token(token)
            except Exception:
                pass

    if user_id:
        log_audit_event(
            user_id=user_id,
            method=method,
            path=path,
            status_code=response.status_code,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            request_body_preview=None,
        )

    return response
