# En: backend/app/celery_app.py

from celery import Celery
from celery.signals import worker_init

from app.core.config import settings
from app.core.redis_client import reset_redis_pools
from app.core.structured_logging import emit_structured_log

celery_app = Celery(
    "tasks",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND
)

# ⚠️ NO AGREGAR `socket_keepalive_options` (TCP_KEEPIDLE/INTVL/CNT) aquí.
# Incompatibilidad confirmada: redis-py aplica setsockopt(TCP_KEEP*) al socket
# ANTES de completar el handshake SSL en conexiones `rediss://`. Con TLS activo
# (Redis Cloud, Upstash, etc.) el socket subyacente rechaza los setsockopt con
# `OSError: [Errno 22] Invalid argument` durante _connect(), y el worker no
# puede arrancar. El síntoma exacto es:
#   `OSError: [Errno 22] Invalid argument` en `redis/connection.py:781`
# Si se necesita tuning fino de keepalive, hágalo a nivel de sistema operativo:
#   - Linux:   sysctl net.ipv4.tcp_keepalive_{time,intvl,probes}
#   - macOS:   sysctl net.inet.tcp.keep{Idle,Intvl,Cnt}
# Esto aplica transparentemente sobre cualquier socket (incluido SSL) porque lo
# gestiona el kernel, no la aplicación.
_celery_redis_kwargs: dict = {
    "socket_keepalive": True,
    "socket_connect_timeout": 5.0,
    "socket_timeout": 5.0,
    "health_check_interval": 30,
    "retry_on_timeout": True,
}

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
    # [REDIS POOL GUARD] Tope explícito de conexiones al broker/result backend
    # para evitar saturar el plan Free (30 conexiones) con prefork + concurrencia.
    broker_pool_limit=int(getattr(settings, "CELERY_BROKER_POOL_LIMIT", 5) or 5),
    broker_connection_timeout=5.0,
    broker_connection_retry_on_timeout=True,
    broker_connection_max_retries=3,
    redis_max_connections=int(
        getattr(settings, "CELERY_RESULT_BACKEND_MAX_CONNECTIONS", 5) or 5
    ),
    broker_transport_options=_celery_redis_kwargs,
    result_backend_transport_options=_celery_redis_kwargs,
    # [REDIS CLOUD MEMORY OPTIMIZATION] Expirar resultados de tareas en 12h
    # en lugar del default de Celery (1 día = 86400s). Esto reduce el uso
    # de memoria en Redis Cloud (plan Free 30MB) cuando hay acumulación
    # de tareas completadas. NO afecta a la API: el frontend consulta el
    # status de una tarea en los primeros segundos tras el submit.
    result_expires=12 * 3600,
)


@worker_init.connect
def _on_celery_worker_init(**_kwargs) -> None:
    """Post-fork hook: cada child construye su propio pool de Redis desde cero.

    Esto evita que sockets del parent (que ya no existen o están en estado
    inválido) se reutilicen en los children después del fork().
    """
    try:
        reset_redis_pools()
        emit_structured_log(
            "celery_worker_init_redis_pools_reset",
            worker_concurrency=getattr(celery_app.conf, "worker_concurrency", None),
        )
    except Exception as exc:
        emit_structured_log(
            "celery_worker_init_redis_pools_reset_error",
            level="warning",
            error=str(exc)[:180],
        )


# --- LA SOLUCIÓN DEFINITIVA ---
# En lugar de autodiscover, importamos explícitamente el módulo que contiene nuestras tareas.
# Esto fuerza a Celery a ver y registrar el decorador @celery_app.task.
celery_app.autodiscover_tasks(['app.tasks'])
from app.tasks import analysis_tasks
from app.tasks import document_tasks
from app.tasks import cloud_sync_tasks
