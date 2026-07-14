from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from queue import Queue
from typing import Any

from app.core.config import settings
from app.core.structured_logging import emit_structured_log
from app.core.supabase_client import get_supabase_service_client

_AUDIT_QUEUE: Queue = Queue()
_WORKER_THREAD: threading.Thread | None = None
_WORKER_STOP = threading.Event()


_AUDIT_EVENTS_MUTATING = {
    "POST": "create",
    "PUT": "update",
    "PATCH": "update",
    "DELETE": "delete",
}


def _event_from_method(method: str, path: str) -> str:
    if method in _AUDIT_EVENTS_MUTATING:
        base = _AUDIT_EVENTS_MUTATING[method]
        parts = [p for p in path.split("/") if p and p != "api" and p != "v1"]
        resource = parts[0] if parts else "unknown"
        return f"{resource}_{base}"
    return f"read_{path.split('/')[-1]}" if path.split("/")[-1] else "read"


def _audit_worker() -> None:
    while not _WORKER_STOP.is_set():
        try:
            entry = _AUDIT_QUEUE.get(timeout=2)
        except Exception:
            continue

        if entry is None:
            break

        try:
            sb = get_supabase_service_client()
            sb.table("audit_logs").insert(entry).execute()
        except Exception as exc:
            emit_structured_log(
                "audit_log_write_failed",
                level="warning",
                event=entry.get("event"),
                error=str(exc)[:180],
            )


def _ensure_worker() -> None:
    global _WORKER_THREAD
    if _WORKER_THREAD is None or not _WORKER_THREAD.is_alive():
        _WORKER_STOP.clear()
        _WORKER_THREAD = threading.Thread(target=_audit_worker, daemon=True, name="audit-logger")
        _WORKER_THREAD.start()


def log_audit_event(
    *,
    user_id: str,
    event: str | None = None,
    method: str,
    path: str,
    status_code: int | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    request_body_preview: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    if not settings.AUDIT_LOG_ENABLED:
        return

    _ensure_worker()

    entry = {
        "user_id": user_id,
        "event": event or _event_from_method(method, path),
        "method": method,
        "path": path,
        "status_code": status_code,
        "ip_address": ip_address,
        "user_agent": user_agent,
        "request_body_preview": (request_body_preview or "")[:500] if request_body_preview else None,
        "metadata": json.dumps(metadata or {}),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    _AUDIT_QUEUE.put_nowait(entry)
