from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import Any

try:
    from redis import Redis
    from redis.exceptions import RedisError
except Exception:  # pragma: no cover - fallback defensivo para entornos sin redis client
    Redis = None  # type: ignore[assignment]

    class RedisError(Exception):
        pass

from app.core.config import settings
from app.core.structured_logging import emit_structured_log


_CACHE_PREFIX = "promdata:ai_cache"
_REDIS_CLIENT: Redis | None = None
_REDIS_INIT_ATTEMPTED = False
_REDIS_LOCK = threading.Lock()
_MEMORY_CACHE: dict[str, tuple[float, str]] = {}
_MEMORY_LOCK = threading.Lock()


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(inner_value) for key, inner_value in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


def _get_redis_client() -> Redis | None:
    global _REDIS_CLIENT, _REDIS_INIT_ATTEMPTED

    if _REDIS_INIT_ATTEMPTED:
        return _REDIS_CLIENT

    with _REDIS_LOCK:
        if _REDIS_INIT_ATTEMPTED:
            return _REDIS_CLIENT

        _REDIS_INIT_ATTEMPTED = True
        if Redis is None:
            emit_structured_log(
                "ai_response_cache_storage_fallback_memory",
                level="warning",
                error="redis_client_missing",
                storage_url=settings.RATE_LIMIT_STORAGE_URL,
            )
            _REDIS_CLIENT = None
            return _REDIS_CLIENT
        try:
            client = Redis.from_url(
                settings.RATE_LIMIT_STORAGE_URL,
                decode_responses=True,
                socket_connect_timeout=0.5,
                socket_timeout=0.5,
                health_check_interval=30,
            )
            client.ping()
            _REDIS_CLIENT = client
        except Exception as exc:
            _REDIS_CLIENT = None
            emit_structured_log(
                "ai_response_cache_storage_fallback_memory",
                level="warning",
                error=str(exc)[:180],
                storage_url=settings.RATE_LIMIT_STORAGE_URL,
            )
    return _REDIS_CLIENT


def build_cache_key(namespace: str, payload: dict[str, Any]) -> str:
    normalized = json.dumps(
        {
            "namespace": namespace,
            "payload": _json_safe(payload),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _memory_get(storage_key: str) -> Any | None:
    now = time.monotonic()
    with _MEMORY_LOCK:
        cached = _MEMORY_CACHE.get(storage_key)
        if not cached:
            return None
        expires_at, serialized = cached
        if expires_at <= now:
            _MEMORY_CACHE.pop(storage_key, None)
            return None
    try:
        return json.loads(serialized)
    except Exception:
        return None


def _memory_set(storage_key: str, payload: Any, ttl_seconds: int) -> None:
    serialized = json.dumps(_json_safe(payload), ensure_ascii=False, separators=(",", ":"))
    expires_at = time.monotonic() + max(int(ttl_seconds or 0), 1)
    with _MEMORY_LOCK:
        _MEMORY_CACHE[storage_key] = (expires_at, serialized)


def get_cached_json(namespace: str, key: str) -> Any | None:
    storage_key = f"{_CACHE_PREFIX}:{namespace}:{key}"
    redis_client = _get_redis_client()
    if redis_client is not None:
        try:
            cached = redis_client.get(storage_key)
            if cached:
                return json.loads(cached)
        except RedisError as exc:
            emit_structured_log(
                "ai_response_cache_read_error",
                level="warning",
                namespace=namespace,
                error=str(exc)[:180],
            )
    return _memory_get(storage_key)


def set_cached_json(namespace: str, key: str, payload: Any, ttl_seconds: int) -> None:
    storage_key = f"{_CACHE_PREFIX}:{namespace}:{key}"
    safe_payload = _json_safe(payload)
    serialized = json.dumps(safe_payload, ensure_ascii=False, separators=(",", ":"))
    redis_client = _get_redis_client()
    if redis_client is not None:
        try:
            redis_client.setex(storage_key, max(int(ttl_seconds or 0), 1), serialized)
            return
        except RedisError as exc:
            emit_structured_log(
                "ai_response_cache_write_error",
                level="warning",
                namespace=namespace,
                error=str(exc)[:180],
            )
    _memory_set(storage_key, safe_payload, ttl_seconds)
