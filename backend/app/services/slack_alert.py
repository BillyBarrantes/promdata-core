"""Enterprise alerting via Slack webhook — Fase 3.5.

Usage:
    from app.services.slack_alert import (
        send_alert,       # async — for FastAPI routes
        send_alert_sync,  # sync — for Celery tasks / circuit breaker
    )

    send_alert("CRITICAL", "Rate limit threshold exceeded", {
        "current_connections": 42,
        "threshold": 30,
        "service": "promdata-backend",
    })
"""
from __future__ import annotations

import os
import threading
from typing import Any

import httpx

from app.core.structured_logging import emit_structured_log

_SLACK_WEBHOOK_URL = os.getenv("SLACK_ALERT_WEBHOOK_URL", "")


def _build_payload(severity: str, title: str, details: dict[str, Any] | None) -> dict:
    emoji = {
        "CRITICAL": ":red_circle:",
        "WARNING": ":warning:",
        "INFO": ":information_source:",
    }.get(severity, ":bell:")

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"{emoji} [{severity}] {title}"},
        },
    ]

    if details:
        fields = []
        for key, value in details.items():
            fields.append({"type": "mrkdwn", "text": f"*{key}:* {str(value)[:500]}"})

        blocks.append({
            "type": "section",
            "fields": fields[:10],
        })

    return {"blocks": blocks}


async def send_alert(
    severity: str,
    title: str,
    details: dict[str, Any] | None = None,
) -> bool:
    """Send an alert to the Slack webhook (async)."""
    if not _SLACK_WEBHOOK_URL:
        return False

    payload = _build_payload(severity, title, details)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(_SLACK_WEBHOOK_URL, json=payload)
        ok = resp.status_code == 200
        emit_structured_log(
            "slack_alert_sent" if ok else "slack_alert_failed",
            level="warning" if not ok else "info",
            severity=severity,
            title=title,
            status_code=resp.status_code if not ok else None,
        )
        return ok
    except Exception as exc:
        emit_structured_log(
            "slack_alert_error",
            level="warning",
            error=str(exc)[:180],
            title=title,
        )
        return False


def send_alert_sync(
    severity: str,
    title: str,
    details: dict[str, Any] | None = None,
) -> bool:
    """Send an alert to the Slack webhook synchronously (fire-and-forget thread)."""
    if not _SLACK_WEBHOOK_URL:
        return False

    payload = _build_payload(severity, title, details)

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(_SLACK_WEBHOOK_URL, json=payload)
        ok = resp.status_code == 200
        emit_structured_log(
            "slack_alert_sent" if ok else "slack_alert_failed",
            level="warning" if not ok else "info",
            severity=severity,
            title=title,
            status_code=resp.status_code if not ok else None,
        )
        return ok
    except Exception as exc:
        emit_structured_log(
            "slack_alert_error",
            level="warning",
            error=str(exc)[:180],
            title=title,
        )
        return False


def send_alert_background(
    severity: str,
    title: str,
    details: dict[str, Any] | None = None,
) -> None:
    """Fire a Slack alert in a daemon thread (non-blocking, no await needed)."""
    if not _SLACK_WEBHOOK_URL:
        return
    threading.Thread(
        target=send_alert_sync,
        args=(severity, title, details),
        daemon=True,
    ).start()
