# En: backend/app/celery_app.py

from celery import Celery
from app.core.config import settings

celery_app = Celery(
    "tasks",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND
)

celery_app.conf.update(
    task_track_started=True,
    # [REDIS CLOUD TLS] Configuración SSL requerida para conexiones externas
    # desde Google Cloud Run hacia Redis Cloud (db.redis.io).
    # ssl_cert_reqs=None evita errores de verificación de certificado.
    broker_use_ssl={
        "ssl_cert_reqs": None,
    } if (settings.CELERY_BROKER_URL or "").startswith("rediss://") else None,
    redis_backend_use_ssl={
        "ssl_cert_reqs": None,
    } if (settings.CELERY_RESULT_BACKEND or "").startswith("rediss://") else None,
)

# --- LA SOLUCIÓN DEFINITIVA ---
# En lugar de autodiscover, importamos explícitamente el módulo que contiene nuestras tareas.
# Esto fuerza a Celery a ver y registrar el decorador @celery_app.task.
celery_app.autodiscover_tasks(['app.tasks'])
from app.tasks import analysis_tasks
from app.tasks import document_tasks
from app.tasks import cloud_sync_tasks
