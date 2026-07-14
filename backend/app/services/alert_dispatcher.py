"""
Alert dispatcher — envía alertas a todos los canales configurados (Slack, Telegram, etc.).

Cada canal es independiente. Si uno falla, los otros siguen funcionando.
Si un canal no está configurado (token/URL vacío), se salta silenciosamente.
"""
from __future__ import annotations

from typing import Any


# ──────────────────────────────────────────────
# 1. ERROR 500 — Internal server error
# ──────────────────────────────────────────────

async def dispatch_500(path: str, method: str, status_code: int) -> None:
    from app.services.slack_alert import send_alert
    from app.services.telegram_alert import send_telegram_500

    try:
        await send_alert("CRITICAL", "Backend 500 error", {
            "path": path, "method": method, "status_code": status_code,
        })
    except Exception:
        pass

    try:
        await send_telegram_500(path, method, status_code)
    except Exception:
        pass


def dispatch_500_background(path: str, method: str, status_code: int) -> None:
    from app.services.slack_alert import send_alert_background
    from app.services.telegram_alert import send_telegram_background_500

    send_alert_background("CRITICAL", "Backend 500 error", {
        "path": path, "method": method, "status_code": status_code,
    })
    send_telegram_background_500(path, method, status_code)


# ──────────────────────────────────────────────
# 2. INYECCIÓN DE PROMPT
# ──────────────────────────────────────────────

async def dispatch_injection(user_id: str, reason: str) -> None:
    from app.services.slack_alert import send_alert
    from app.services.telegram_alert import send_telegram_injection

    try:
        await send_alert("WARNING", "Prompt injection detected", {
            "user_id": user_id, "reason": reason,
        })
    except Exception:
        pass

    try:
        await send_telegram_injection(user_id, reason)
    except Exception:
        pass


def dispatch_injection_background(user_id: str, reason: str) -> None:
    from app.services.slack_alert import send_alert_background
    from app.services.telegram_alert import send_telegram_background_injection

    send_alert_background("WARNING", "Prompt injection detected", {
        "user_id": user_id, "reason": reason,
    })
    send_telegram_background_injection(user_id, reason)


# ──────────────────────────────────────────────
# 3. CIRCUIT BREAKER OPEN
# ──────────────────────────────────────────────

def dispatch_circuit_open(
    failures: int,
    threshold: int,
    recovery: int,
    error: str,
) -> None:
    from app.services.slack_alert import send_alert_background
    from app.services.telegram_alert import send_telegram_background_circuit_open

    send_alert_background("CRITICAL", "Gemini circuit breaker OPEN", {
        "consecutive_failures": failures,
        "failure_threshold": threshold,
        "recovery_timeout_seconds": recovery,
        "error": error[:300],
    })
    send_telegram_background_circuit_open(failures, threshold, recovery, error)
