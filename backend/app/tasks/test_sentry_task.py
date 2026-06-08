"""
Test task for Sentry observability verification.
Este módulo existe SOLO para validar que Sentry captura excepciones
del worker de Celery. NO debe usarse en producción.

Uso desde el worker:
    from app.tasks.test_sentry_task import trigger_sentry_test
    trigger_sentry_task.delay()
"""
from __future__ import annotations

from app.celery_app import celery_app


@celery_app.task(
    name="app.tasks.test_sentry_task.trigger_sentry_test",
    bind=True,
    max_retries=0,
)
def trigger_sentry_test(self) -> dict:
    """Task que SIEMPRE falla con un ValueError para validar Sentry.

    Después de validar, este módulo se debe ELIMINAR del repo.
    """
    error_msg = "TEST DE ALARMA SENTRY — Celery worker error capture (no real)"
    raise ValueError(error_msg)
