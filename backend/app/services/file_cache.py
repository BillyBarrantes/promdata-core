from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from typing import Any

try:
    from redis.exceptions import RedisError
except Exception:
    class RedisError(Exception):
        pass

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
    _HAS_PARQUET = True
except Exception:
    _HAS_PARQUET = False

from app.core.config import settings
from app.core.redis_client import get_redis_client
from app.core.structured_logging import emit_structured_log


_CACHE_PREFIX = "promdata:file_cache"
_CACHE_KEY_SCHEMA_VERSION = "v1"
_REDIS_MAX_SIZE = 512 * 1024
_PARQUET_BASE = "/tmp/promdata_cache/file_results"
_MEMORY_CACHE: dict[str, tuple[float, str]] = {}
_MEMORY_LOCK = threading.Lock()


def _parquet_dir(file_id: str) -> str:
    safe_id = file_id.replace("/", "_").replace("\\", "_")
    return os.path.join(_PARQUET_BASE, safe_id)


def _parquet_path(file_id: str, prompt_hash: str) -> str:
    return os.path.join(_parquet_dir(file_id), f"{prompt_hash}.parquet")


def _build_redis_key(file_id: str, prompt_hash: str) -> str:
    return f"{_CACHE_PREFIX}:{_CACHE_KEY_SCHEMA_VERSION}:{file_id}:{prompt_hash}"


def build_file_cache_key(file_id: str, prompt: str) -> str:
    normalized = json.dumps(
        {
            "_schema_version": _CACHE_KEY_SCHEMA_VERSION,
            "file_id": file_id,
            "prompt": prompt.strip().lower(),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


def _get_redis():
    return get_redis_client(purpose="file_cache")


def _memory_get(storage_key: str) -> dict | None:
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


def _memory_set(storage_key: str, payload: dict, ttl_seconds: int) -> None:
    serialized = json.dumps(_json_safe(payload), ensure_ascii=False, separators=(",", ":"))
    expires_at = time.monotonic() + max(int(ttl_seconds or 0), 1)
    with _MEMORY_LOCK:
        _MEMORY_CACHE[storage_key] = (expires_at, serialized)


def get_cached_analysis(file_id: str, prompt: str) -> dict | None:
    prompt_hash = build_file_cache_key(file_id, prompt)
    redis_key = _build_redis_key(file_id, prompt_hash)
    redis_client = _get_redis()

    if redis_client is not None:
        try:
            cached = redis_client.get(redis_key)
            if cached:
                payload = json.loads(cached)
                emit_structured_log(
                    "file_cache_hit",
                    file_id=file_id,
                    backend="redis",
                    key_prefix=prompt_hash[:12],
                )
                return payload
        except RedisError as exc:
            emit_structured_log(
                "file_cache_read_error",
                level="warning",
                backend="redis",
                error=str(exc)[:180],
            )

    if _HAS_PARQUET:
        parq_path = _parquet_path(file_id, prompt_hash)
        if os.path.isfile(parq_path):
            try:
                table = pq.read_table(parq_path)
                payload = json.loads(table.column("payload")[0].as_py())
                emit_structured_log(
                    "file_cache_hit",
                    file_id=file_id,
                    backend="parquet",
                    key_prefix=prompt_hash[:12],
                )
                return payload
            except Exception as exc:
                emit_structured_log(
                    "file_cache_read_error",
                    level="warning",
                    backend="parquet",
                    error=str(exc)[:180],
                )

    memory_hit = _memory_get(redis_key)
    if memory_hit is not None:
        emit_structured_log(
            "file_cache_hit",
            file_id=file_id,
            backend="memory_fallback",
            key_prefix=prompt_hash[:12],
        )
    return memory_hit


def set_cached_analysis(file_id: str, prompt: str, payload: dict, ttl_seconds: int = 3600) -> None:
    prompt_hash = build_file_cache_key(file_id, prompt)
    redis_key = _build_redis_key(file_id, prompt_hash)
    safe_payload = _json_safe(payload)
    serialized = json.dumps(safe_payload, ensure_ascii=False, separators=(",", ":"))

    redis_client = _get_redis()
    if redis_client is not None and len(serialized) <= _REDIS_MAX_SIZE:
        try:
            redis_client.setex(redis_key, max(int(ttl_seconds or 0), 1), serialized)
            emit_structured_log(
                "file_cache_write",
                file_id=file_id,
                backend="redis",
                size_bytes=len(serialized),
                key_prefix=prompt_hash[:12],
            )
            return
        except RedisError as exc:
            emit_structured_log(
                "file_cache_write_error",
                level="warning",
                backend="redis",
                error=str(exc)[:180],
            )

    if _HAS_PARQUET:
        try:
            parq_dir = _parquet_dir(file_id)
            os.makedirs(parq_dir, exist_ok=True)
            parq_path = _parquet_path(file_id, prompt_hash)
            serialized_payload = json.dumps(safe_payload, ensure_ascii=False)
            array = pa.array([serialized_payload], type=pa.string())
            table = pa.table({"payload": array, "file_id": pa.array([file_id]), "prompt_hash": pa.array([prompt_hash])})
            pq.write_table(table, parq_path)
            emit_structured_log(
                "file_cache_write",
                file_id=file_id,
                backend="parquet",
                size_bytes=len(serialized_payload),
                key_prefix=prompt_hash[:12],
            )
            return
        except Exception as exc:
            emit_structured_log(
                "file_cache_write_error",
                level="warning",
                backend="parquet",
                error=str(exc)[:180],
            )

    _memory_set(redis_key, safe_payload, ttl_seconds)
    emit_structured_log(
        "file_cache_write",
        file_id=file_id,
        backend="memory_fallback",
        size_bytes=len(serialized),
        key_prefix=prompt_hash[:12],
    )


def invalidate_file_cache(file_id: str, prompt: str | None = None) -> None:
    prompt_hash = build_file_cache_key(file_id, prompt or "")
    redis_key = _build_redis_key(file_id, prompt_hash)
    redis_client = _get_redis()
    if redis_client is not None:
        try:
            if prompt:
                redis_client.delete(redis_key)
            else:
                for key in redis_client.scan_iter(f"{_CACHE_PREFIX}:{_CACHE_KEY_SCHEMA_VERSION}:{file_id}:*"):
                    redis_client.delete(key)
        except RedisError:
            pass
    if _HAS_PARQUET:
        parq_path = _parquet_path(file_id, prompt_hash)
        if os.path.isfile(parq_path):
            try:
                os.remove(parq_path)
            except OSError:
                pass
    with _MEMORY_LOCK:
        _MEMORY_CACHE.pop(redis_key, None)
