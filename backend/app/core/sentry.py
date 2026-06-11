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


# Mensajes que identifican eventos sintéticos emitidos por tests.
# Los tests de PromData usan "boom" como error simulado para validar
# la lógica de fallback. Filtrarlos evita ruido en Sentry.
_SENTRY_TEST_FINGERPRINTS: tuple[str, ...] = ("boom",)


def _is_test_synthetic_event(event: dict, hint: dict | None) -> bool:
    """
    Detecta si un evento de Sentry proviene de un test fixture.

    Inspecciona 3 ubicaciones comunes donde el mensaje puede vivir:
    1. logentry.formatted (string JSON-encodeado usado por emit_structured_log)
    2. logentry.message (mensaje crudo de logging)
    3. exception.values[].value (mensaje de la excepción capturada)
    """
    logentry = event.get("logentry") or {}
    formatted = str(logentry.get("formatted") or "")
    message = str(logentry.get("message") or "")

    if any(fp in formatted for fp in _SENTRY_TEST_FINGERPRINTS):
        return True
    if any(fp in message for fp in _SENTRY_TEST_FINGERPRINTS):
        return True

    for exc in (event.get("exception") or {}).get("values") or []:
        if any(fp in str(exc.get("value") or "") for fp in _SENTRY_TEST_FINGERPRINTS):
            return True

    return False


def _sentry_before_send(event: dict, hint: dict | None) -> dict | None:
    """
    Hook before_send de Sentry: descarta selectivamente eventos
    sinteticos de tests cuyo mensaje sea 'boom'.

    Devuelve None para descartar el evento, o el evento intacto
    para enviarlo a Sentry.
    """
    if _is_test_synthetic_event(event, hint):
        return None
    return event


def init_sentry() -> None:
    """
    Inicializa Sentry si SENTRY_DSN está configurado en el entorno.

    - FastApiIntegration: captura excepciones en endpoints y añade
      contexto del request (método, path, status code).
    - StarletteIntegration: requerida por FastApiIntegration para
      capturar el ciclo de vida del request correctamente.
    - CeleryIntegration: captura excepciones en tasks de Celery,
      incluyendo los tasks de analysis_tasks.py.

    Filtra eventos sinteticos de tests (mensaje == 'boom') via
    before_send para no contaminar el dashboard de Sentry.
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
        # Filtrar eventos sinteticos de tests (mensaje == 'boom').
        before_send=_sentry_before_send,
    )
    print(
        f"[SENTRY] Inicializado — environment='{settings.APP_DEPLOY_ENV}' "
        f"release='{settings.APP_BUILD_VERSION}'.",
        flush=True,
    )
