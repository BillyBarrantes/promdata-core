"""
Inicialización de Sentry para el backend FastAPI.

Se activa solo si SENTRY_DSN está configurado como variable de entorno.
En desarrollo local (sin DSN), el módulo es un no-op silencioso.
"""
import sentry_sdk
from sentry_sdk.integrations.celery import CeleryIntegration
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration

from app.core.config import settings


def _before_send(event: dict, hint: dict) -> dict | None:
    """Filtra ruido de errores transitorios de Gemini/Vertex AI.

    - Los 429 que fueron reintentados y resueltos en el retry loop de
      circuit_breaker.py NUNCA llegan aquí (se resolvieron internamente).
    - Los que SÍ llegan son los que agotaron todos los reintentos:
      se reportan pero como fingerprint agrupado para no inundar.
    - GeminiCircuitOpenError se rebaja a 'warning' (es un síntoma,
      no la causa raíz).
    """
    exc_info = hint.get("exc_info")
    if exc_info:
        exc_type, exc_value, _ = exc_info
        error_text = f"{exc_type.__name__} {exc_value}".lower()

        # GeminiCircuitOpenError → warning, agrupado
        if exc_type.__name__ == "GeminiCircuitOpenError":
            event["level"] = "warning"
            event["fingerprint"] = ["gemini-circuit-open"]
            return event

        # 429/quota errors → agrupar por fingerprint
        quota_markers = ("429", "resource_exhausted", "quota", "rate limit")
        if any(m in error_text for m in quota_markers):
            event["level"] = "warning"
            event["fingerprint"] = ["gemini-429-exhausted"]
            return event

        # Otros transitorios (503, timeout) → agrupar
        transient_markers = ("503", "504", "timeout", "timed out", "cancelled", "unavailable")
        if any(m in error_text for m in transient_markers):
            event["level"] = "warning"
            event["fingerprint"] = ["gemini-transient-error"]
            return event

    return event  # todo lo demás pasa sin filtro


def init_sentry() -> None:
    """
    Inicializa Sentry si SENTRY_DSN está configurado en el entorno.

    - FastApiIntegration: captura excepciones en endpoints y añade
      contexto del request (método, path, status code).
    - StarletteIntegration: requerida por FastApiIntegration para
      capturar el ciclo de vida del request correctamente.
    - CeleryIntegration: captura excepciones en tasks de Celery,
      incluyendo los tasks de analysis_tasks.py.
    """
    if not settings.SENTRY_DSN:
        print("[SENTRY] DSN no configurado — observabilidad desactivada.", flush=True)
        return

    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        integrations=[
            StarletteIntegration(transaction_style="url"),
            FastApiIntegration(transaction_style="url"),
            CeleryIntegration(monitor_beat_tasks=False),
        ],
        # Captura el 100% de los errores.
        # Solo el 5% de transacciones de performance (no saturar el tier gratuito).
        traces_sample_rate=0.05,
        # Contexto de ambiente y versión (ya configurados en Cloud Run).
        environment=settings.APP_DEPLOY_ENV,
        release=settings.APP_BUILD_VERSION,
        # No enviar PII del usuario por defecto (GDPR-safe).
        send_default_pii=False,
        # [RPS HARDENING] Filtro de ruido para errores transitorios de Gemini.
        # Los 429/503/timeout se agrupan como warnings en lugar de generar
        # alertas críticas individuales. Ver plan RPS → Día 2.
        before_send=_before_send,
    )
    print(
        f"[SENTRY] Inicializado — environment='{settings.APP_DEPLOY_ENV}' "
        f"release='{settings.APP_BUILD_VERSION}'.",
        flush=True,
    )
