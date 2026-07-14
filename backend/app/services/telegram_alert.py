"""
Telegram alert sender — natural language messages in Spanish (Fase 3.6).
"""
from __future__ import annotations

import os
import threading
from datetime import datetime, timezone
from typing import Any

import httpx

from app.core.structured_logging import emit_structured_log

_TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
_TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


def _datetime_now() -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%d/%m/%Y %H:%M UTC")


def _format_500(path: str, method: str, status_code: int) -> str:
    return (
        "<b>¡Algo salió mal!</b> 🚨\n\n"
        "Se produjo un error inesperado al procesar una solicitud "
        f"en el endpoint <code>{method} {path}</code>.\n\n"
        f"<b>Código de error:</b> HTTP {status_code}\n"
        f"<b>Ruta:</b> {path}\n\n"
        "El error fue capturado sin exponer detalles internos al usuario. "
        "Revisa los logs de Cloud Run para el trace completo.\n\n"
        f"🕐 {_datetime_now()}"
    )


def _format_injection(user_id: str, reason: str) -> str:
    return (
        "<b>¡Intento de manipulación detectado!</b> ⚠️\n\n"
        "Un usuario intentó engañar al asistente con un prompt malicioso "
        "y fue bloqueado automáticamente.\n\n"
        "<b>Código de error:</b> HTTP 400 (Bad Request)\n"
        f"<b>Usuario:</b> <code>{user_id}</code>\n"
        f"<b>Motivo:</b> {reason}\n"
        "<b>Endpoints afectados:</b> Análisis de datos, Base de Conocimiento\n\n"
        f"🕐 {_datetime_now()}"
    )


def _format_circuit_open(
    failures: int, threshold: int, recovery: int, error: str
) -> str:
    return (
        "<b>Gemini está saturado</b> ☁️🔌\n\n"
        "El circuito de protección se abrió tras detectar "
        f"<b>{failures} fallos consecutivos</b> en Vertex AI. "
        "Las solicitudes a Gemini ahora fallarán rápido sin consumir "
        "recursos hasta que el servicio se recupere.\n\n"
        "<b>Código de error:</b> HTTP 503 (Servicio no disponible temporalmente)\n"
        f"<b>Tiempo de recuperación estimado:</b> {recovery} segundos\n"
        f"<b>Error original:</b> {error}\n\n"
        f"🕐 {_datetime_now()}"
    )


def _build_payload(text: str) -> dict:
    return {
        "chat_id": _TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }


async def send_telegram_500(path: str, method: str, status_code: int) -> bool:
    if not _TELEGRAM_BOT_TOKEN or not _TELEGRAM_CHAT_ID:
        return False
    text = _format_500(path, method, status_code)
    return await _post(text)


async def send_telegram_injection(user_id: str, reason: str) -> bool:
    if not _TELEGRAM_BOT_TOKEN or not _TELEGRAM_CHAT_ID:
        return False
    text = _format_injection(user_id, reason)
    return await _post(text)


async def send_telegram_circuit_open(
    failures: int, threshold: int, recovery: int, error: str
) -> bool:
    if not _TELEGRAM_BOT_TOKEN or not _TELEGRAM_CHAT_ID:
        return False
    text = _format_circuit_open(failures, threshold, recovery, error)
    return await _post(text)


async def _post(text: str) -> bool:
    payload = _build_payload(text)
    try:
        url = f"https://api.telegram.org/bot{_TELEGRAM_BOT_TOKEN}/sendMessage"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
        ok = resp.status_code == 200
        level = "info" if ok else "warning"
        emit_structured_log(
            "telegram_alert_sent" if ok else "telegram_alert_failed",
            level=level,
            status_code=resp.status_code if not ok else None,
            error_snippet=str(resp.text)[:150] if not ok else None,
        )
        return ok
    except Exception as exc:
        emit_structured_log(
            "telegram_alert_error",
            level="warning",
            error=str(exc)[:180],
        )
        return False


# ── Sync wrappers (for circuit breaker and other sync contexts) ──

def _post_sync(text: str) -> bool:
    payload = _build_payload(text)
    try:
        url = f"https://api.telegram.org/bot{_TELEGRAM_BOT_TOKEN}/sendMessage"
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(url, json=payload)
        ok = resp.status_code == 200
        return ok
    except Exception:
        return False


def send_telegram_500_sync(path: str, method: str, status_code: int) -> bool:
    if not _TELEGRAM_BOT_TOKEN or not _TELEGRAM_CHAT_ID:
        return False
    text = _format_500(path, method, status_code)
    return _post_sync(text)


def send_telegram_injection_sync(user_id: str, reason: str) -> bool:
    if not _TELEGRAM_BOT_TOKEN or not _TELEGRAM_CHAT_ID:
        return False
    text = _format_injection(user_id, reason)
    return _post_sync(text)


def send_telegram_circuit_open_sync(
    failures: int, threshold: int, recovery: int, error: str
) -> bool:
    if not _TELEGRAM_BOT_TOKEN or not _TELEGRAM_CHAT_ID:
        return False
    text = _format_circuit_open(failures, threshold, recovery, error)
    return _post_sync(text)


def send_telegram_background_500(path: str, method: str, status_code: int) -> None:
    threading.Thread(
        target=send_telegram_500_sync,
        args=(path, method, status_code),
        daemon=True,
    ).start()


def send_telegram_background_injection(user_id: str, reason: str) -> None:
    threading.Thread(
        target=send_telegram_injection_sync,
        args=(user_id, reason),
        daemon=True,
    ).start()


def send_telegram_background_circuit_open(
    failures: int, threshold: int, recovery: int, error: str
) -> None:
    threading.Thread(
        target=send_telegram_circuit_open_sync,
        args=(failures, threshold, recovery, error),
        daemon=True,
    ).start()
