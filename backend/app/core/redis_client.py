from __future__ import annotations

import threading
from typing import Optional

from redis import Redis
from redis.connection import ConnectionPool

from app.core.config import settings
from app.core.structured_logging import emit_structured_log


_POOLS: dict[str, ConnectionPool] = {}
_CLIENTS: dict[str, Redis] = {}
_POOLS_LOCK = threading.Lock()


_PURPOSE_MAX_CONNECTIONS: dict[str, int] = {
    "rate_limit": 5,
    "ai_response_cache": 5,
    "healthcheck": 2,
}


def _resolve_max_connections(purpose: str) -> int:
    if purpose == "rate_limit":
        return max(int(getattr(settings, "REDIS_MAX_CONNECTIONS_RATE_LIMIT", 5) or 5), 1)
    if purpose == "ai_response_cache":
        return max(int(getattr(settings, "REDIS_MAX_CONNECTIONS_AI_CACHE", 5) or 5), 1)
    if purpose == "healthcheck":
        return max(int(getattr(settings, "REDIS_MAX_CONNECTIONS_HEALTHCHECK", 2) or 2), 1)
    return max(int(getattr(settings, "REDIS_MAX_CONNECTIONS_DEFAULT", 5) or 5), 1)


def _build_pool(purpose: str, max_connections: int) -> ConnectionPool:
    storage_url = str(settings.RATE_LIMIT_STORAGE_URL or "").strip()
    if not storage_url:
        raise RuntimeError(f"Redis URL no configurada para purpose={purpose}")

    extra_kwargs: dict = {
        "decode_responses": True,
        "max_connections": max_connections,
        "socket_keepalive": True,
        "socket_connect_timeout": 1.0,
        "socket_timeout": 1.0,
        "health_check_interval": 30,
        "retry_on_timeout": True,
    }

    if storage_url.startswith("rediss://"):
        extra_kwargs["ssl_cert_reqs"] = None

    pool = ConnectionPool.from_url(storage_url, **extra_kwargs)

    emit_structured_log(
        "redis_pool_initialized",
        purpose=purpose,
        max_connections=max_connections,
        storage_url=storage_url,
    )
    return pool


def get_redis_client(purpose: str = "default") -> Optional[Redis]:
    """Retorna un cliente Redis compartido para el `purpose` dado.

    - Deduplica el pool por proceso (no entre procesos; cada child post-fork
      debe llamar a `reset_redis_pools()` desde el signal `worker_init`).
    - Devuelve `None` si la URL no está configurada o si el ping inicial falla.
      Los llamadores tienen fallback a memoria en ese caso.
    """
    if purpose in _CLIENTS:
        return _CLIENTS[purpose]

    with _POOLS_LOCK:
        if purpose in _CLIENTS:
            return _CLIENTS[purpose]

        try:
            max_conn = _resolve_max_connections(purpose)
            pool = _build_pool(purpose, max_conn)
            client = Redis(connection_pool=pool)
            client.ping()
            _POOLS[purpose] = pool
            _CLIENTS[purpose] = client
            return client
        except Exception as exc:
            emit_structured_log(
                "redis_pool_init_failed",
                level="warning",
                purpose=purpose,
                error=str(exc)[:180],
            )
            _CLIENTS[purpose] = None
            return None


def reset_redis_pools() -> None:
    """Limpia los pools en memoria. Llamar en el signal `worker_init` de Celery
    para que cada child post-fork construya su propio pool y no comparta sockets
    con el parent (que ya no existe o está bloqueado)."""
    with _POOLS_LOCK:
        for purpose, client in list(_CLIENTS.items()):
            try:
                if client is not None:
                    client.close()
            except Exception:
                pass
        _POOLS.clear()
        _CLIENTS.clear()


def get_pool_stats() -> dict[str, dict[str, int]]:
    """Snapshot de uso de pools. Útil para /health/ready y debugging."""
    stats: dict[str, dict[str, int]] = {}
    with _POOLS_LOCK:
        for purpose, pool in _POOLS.items():
            stats[purpose] = {
                "max_connections": int(getattr(pool, "max_connections", 0) or 0),
                "in_use_connections": int(len(getattr(pool, "_in_use_connections", {}) or {})),
                "available_connections": int(
                    len(getattr(pool, "_available_connections", []) or [])
                ),
            }
    return stats


__all__ = [
    "get_redis_client",
    "reset_redis_pools",
    "get_pool_stats",
]
