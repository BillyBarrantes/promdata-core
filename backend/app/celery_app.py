# En: backend/app/celery_app.py

from celery import Celery
from celery.signals import worker_init, task_prerun, task_postrun, task_failure
from kombu import Queue

from app.core.config import settings
from app.core.redis_client import reset_redis_pools, publish_task_progress
from app.core.sentry import init_sentry
from app.core.structured_logging import emit_structured_log

# ---------------------------------------------------------------------------
# Sentry: inicializar ANTES de crear celery_app y registrar tasks.
# Sin esto, las excepciones de las tasks (analysis_tasks, document_tasks,
# cloud_sync_tasks) son invisibles a Sentry. Ver AGENTS.md §4.1 sobre
# observabilidad obligatoria para PromData.
# Si SENTRY_DSN no está configurado, es un no-op silencioso.
# ---------------------------------------------------------------------------
init_sentry()

celery_app = Celery(
    "tasks",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    timezone='UTC',
    enable_utc=True,
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
    result_expires=24 * 3600,
    timezone='UTC',
    enable_utc=True,
    task_soft_time_limit=settings.CELERY_TASK_SOFT_TIME_LIMIT,
    task_time_limit=settings.CELERY_TASK_HARD_TIME_LIMIT,
    task_default_queue=settings.CELERY_QUEUE_DEFAULT,
    task_queues=(
        Queue(settings.CELERY_QUEUE_DEFAULT),
        Queue(settings.CELERY_QUEUE_ANALYSIS),
        Queue(settings.CELERY_QUEUE_BACKGROUND),
    ),
    task_routes={
        "perform_analysis_task": {"queue": settings.CELERY_QUEUE_ANALYSIS},
        "perform_analysis_task_universal_tabular": {"queue": settings.CELERY_QUEUE_ANALYSIS},
        "observe_canonical_shadow_runtime": {"queue": settings.CELERY_QUEUE_BACKGROUND},
        "observe_canonical_tabular_canary_runtime": {"queue": settings.CELERY_QUEUE_BACKGROUND},
        "perform_cloud_sync_job_task": {"queue": settings.CELERY_QUEUE_BACKGROUND},
        "process_knowledge_document_task": {"queue": settings.CELERY_QUEUE_DEFAULT},
    },
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


@task_prerun.connect
def _on_task_prerun(task_id, task, *args, **kwargs):
    """Publica evento de inicio en Pub/Sub"""
    publish_task_progress(task_id, {
        "task_id": task_id,
        "task_name": task.name,
        "status": "started",
        "message": "Analizando los datos..."
    })


@task_postrun.connect
def _on_task_postrun(task_id, task, *args, retval=None, state=None, **kwargs):
    """Publica evento de finalización en Pub/Sub"""
    final_status = retval if isinstance(retval, str) else (state.lower() if state else "unknown")
    payload = {
        "task_id": task_id,
        "task_name": task.name,
        "status": final_status,
        "message": "Análisis finalizado."
    }
    publish_task_progress(task_id, payload)
    
    # Liberar slot de concurrencia
    try:
        from app.core.rate_limit import release_concurrency_slot, _extract_user_id_from_token
        
        # Celery pasa los argumentos originales de la tarea en 'args' y 'kwargs' (del signal, no de la tarea en sí, 
        # para eso miramos task_args / task_kwargs que pasan en **kwargs)
        task_args = kwargs.get('args', args)
        task_kwargs = kwargs.get('kwargs', {})
        
        user_token = task_kwargs.get('user_token')
        if not user_token and len(task_args) >= 4:
            user_token = task_args[3]
            
        if user_token:
            user_id = _extract_user_id_from_token(user_token)
            if user_id:
                release_concurrency_slot(user_id)
    except Exception as e:
        pass


@task_failure.connect
def _on_task_failure(task_id, exception, args, kwargs, traceback, einfo, **kw):
    """Publica evento de fallo (incluyendo timeouts) en Pub/Sub"""
    error_message = "Ocurrió un error inesperado durante el análisis."
    if type(exception).__name__ == "SoftTimeLimitExceeded":
        error_message = "Tu análisis fue demasiado complejo. Intenta con un filtro más específico."
        
    payload = {
        "task_id": task_id,
        "status": "failed",
        "error": error_message,
        "message": error_message
    }
    publish_task_progress(task_id, payload)


# --- LA SOLUCIÓN DEFINITIVA ---
# En lugar de autodiscover, importamos explícitamente el módulo que contiene nuestras tareas.
# Esto fuerza a Celery a ver y registrar el decorador @celery_app.task.
celery_app.autodiscover_tasks(['app.tasks'])
from app.tasks import analysis_tasks
from app.tasks import document_tasks
from app.tasks import cloud_sync_tasks
