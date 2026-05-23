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
    )
    print(
        f"[SENTRY] Inicializado — environment='{settings.APP_DEPLOY_ENV}' "
        f"release='{settings.APP_BUILD_VERSION}'.",
        flush=True,
    )
