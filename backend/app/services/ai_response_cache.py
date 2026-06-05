from __future__ import annotations

"""Caché de respuestas de IA con backend Redis (con fallback a memoria).

================================================================================
GOBERNANZA CANÓNICA DE CACHE KEYS — REGLA DE ORO PARA TODO EL EQUIPO
================================================================================

Cualquier callsite que invoque `build_cache_key(namespace, payload)` debe
garantizar que DOS prompts con intención semántica DISTINTA (aunque compartan
archivo, dimensión o agregación) produzcan llaves DIFERENTES. Esto se logra
aplicando UNA de estas dos reglas (o ambas):

  1. **Namespace distintos** para flujos con semántica disjunta.
     Ejemplos válidos:
       - "semantic_router"        → decisiones de ruteo (intent, route, confidence)
       - "semantic_translator"    → planes analíticos (AnalysisPlan JSON)
       - "chart_narrative"        → narrativas de un chart específico
       - "dashboard_executive_summary" → resumen ejecutivo de un dashboard
       - "semantic_router_schema" → fingerprint de schema (sin prompt)

  2. **Payload que contenga toda variable discriminante** del contexto.
     Si dos requests pueden producir respuestas distintas con el mismo
     namespace, los campos que las distinguen DEBEN estar en el payload:
       - `prompt` (texto exacto del usuario, normalizado)
       - `file_id` (cuando aplique)
       - `plan.metric` / `plan.dimension` (cuando aplique)
       - `glossary_context`, `format_instruction`, etc.

Anti-patrones prohibidos:
  - Usar el mismo namespace con un payload que NO incluya el prompt del
    usuario. Esto causará colisiones cuando dos archivos del mismo
    schema se analicen con prompts distintos.
  - Asumir que el TTL del cache (1800s por defecto) es la red de seguridad.
    El TTL solo limpia eventualmente; no previene respuestas incorrectas
    servidas en los primeros 30 minutos.

El versionado del esquema (`_CACHE_KEY_SCHEMA_VERSION`) garantiza que
cualquier bump de versión invalide MASIVAMENTE todas las keys generadas
con la versión anterior, sin necesidad de scripts de purga.
================================================================================
"""

import hashlib
import json
import threading
import time
from typing import Any

try:
    from redis.exceptions import RedisError
except Exception:  # pragma: no cover - fallback defensivo para entornos sin redis client
    class RedisError(Exception):
        pass

from app.core.config import settings
from app.core.redis_client import get_redis_client
from app.core.structured_logging import emit_structured_log


_CACHE_PREFIX = "promdata:ai_cache"
_CACHE_KEY_SCHEMA_VERSION = "v2"
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


def _get_redis_client():
    return get_redis_client(purpose="ai_response_cache")


def build_cache_key(namespace: str, payload: dict[str, Any]) -> str:
    normalized = json.dumps(
        {
            "_schema_version": _CACHE_KEY_SCHEMA_VERSION,
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
                emit_structured_log(
                    "ai_cache_hit",
                    namespace=namespace,
                    backend="redis",
                    key_prefix=key[:16],
                    key_schema_version=_CACHE_KEY_SCHEMA_VERSION,
                )
                return json.loads(cached)
        except RedisError as exc:
            emit_structured_log(
                "ai_response_cache_read_error",
                level="warning",
                namespace=namespace,
                error=str(exc)[:180],
            )
    memory_hit = _memory_get(storage_key)
    if memory_hit is not None:
        emit_structured_log(
            "ai_cache_hit",
            namespace=namespace,
            backend="memory_fallback",
            key_prefix=key[:16],
            key_schema_version=_CACHE_KEY_SCHEMA_VERSION,
        )
    return memory_hit


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
