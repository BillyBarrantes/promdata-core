from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import unicodedata
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
from app.services.analysis_memory_context import unwrap_prompt_payload


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


def _short_hash(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:16]


def _normalize_cache_prompt(prompt: str | None) -> str:
    normalized = unicodedata.normalize("NFC", str(prompt or ""))
    normalized = re.sub(r"\s+", " ", normalized.strip().lower())
    return normalized


def _strip_volatile_context(value: Any) -> Any:
    volatile_keys = {
        "id",
        "task_id",
        "parent_id",
        "parent_task_id",
        "created_at",
        "updated_at",
        "timestamp",
        "ts",
        "duration_ms",
        "elapsed_ms",
        "latency_ms",
    }
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        cleaned = {
            str(key): _strip_volatile_context(inner_value)
            for key, inner_value in value.items()
            if str(key).lower() not in volatile_keys
        }
        return {key: cleaned[key] for key in sorted(cleaned)}
    if isinstance(value, (list, tuple, set)):
        cleaned_items = [_strip_volatile_context(item) for item in value]
        return sorted(
            cleaned_items,
            key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True, default=str),
        )
    return str(value)


def _extract_stable_parent_context(parent_context: Any) -> dict[str, Any]:
    if not isinstance(parent_context, dict):
        return {}
    result_payload = parent_context.get("result_payload")
    if not isinstance(result_payload, dict):
        result_payload = parent_context
    traceability = result_payload.get("traceability") if isinstance(result_payload.get("traceability"), dict) else {}
    semantic_context = (
        traceability.get("semantic_context")
        if isinstance(traceability.get("semantic_context"), dict)
        else result_payload.get("semantic_context")
        if isinstance(result_payload.get("semantic_context"), dict)
        else {}
    )
    filters = []
    if isinstance(semantic_context, dict):
        filters.extend(list(semantic_context.get("filters") or []))
    for plan in list(traceability.get("plans") or []):
        if isinstance(plan, dict):
            filters.extend(list(plan.get("filters") or []))
    chart_filter_contexts = []
    for chart in list(result_payload.get("chart_options") or []):
        if not isinstance(chart, dict):
            continue
        for key in ("chart_base_filters", "base_filters", "filters"):
            if chart.get(key):
                chart_filter_contexts.extend(list(chart.get(key) or []))
    stable_context = {
        "filters": filters,
        "chart_filters": chart_filter_contexts,
        "semantic_context": semantic_context,
    }
    cleaned_context = _strip_volatile_context(stable_context)
    if not isinstance(cleaned_context, dict):
        return {}
    return {key: value for key, value in cleaned_context.items() if value not in ({}, [], None, "")}


def build_parent_context_fingerprint(parent_context: Any) -> str:
    stable_context = _extract_stable_parent_context(parent_context)
    if not stable_context:
        return ""
    serialized = json.dumps(stable_context, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _build_cache_identity(
    file_id: str,
    prompt: str | None,
    parent_context: Any = None,
) -> dict[str, str]:
    actual_prompt, _parent_task_id = unwrap_prompt_payload(prompt)
    raw_prompt = str(prompt or "")
    normalized_prompt = _normalize_cache_prompt(actual_prompt)
    parent_context_fingerprint = build_parent_context_fingerprint(parent_context)
    return {
        "file_id": str(file_id or ""),
        "raw_prompt_hash": _short_hash(raw_prompt),
        "actual_prompt": normalized_prompt,
        "actual_prompt_hash": _short_hash(normalized_prompt),
        "parent_context_fingerprint": parent_context_fingerprint,
    }


def _hash_cache_identity(identity: dict[str, str]) -> str:
    normalized = json.dumps(
        {
            "_schema_version": _CACHE_KEY_SCHEMA_VERSION,
            "file_id": identity.get("file_id") or "",
            "prompt": identity.get("actual_prompt") or "",
            "parent_context_fingerprint": identity.get("parent_context_fingerprint") or "",
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def build_file_cache_key(file_id: str, prompt: str, parent_context: Any = None) -> str:
    return _hash_cache_identity(_build_cache_identity(file_id, prompt, parent_context))


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


def _emit_cache_identity_log(event_name: str, *, file_id: str, backend: str | None, identity: dict[str, str], key_prefix: str) -> None:
    emit_structured_log(
        event_name,
        file_id=file_id,
        backend=backend,
        key_prefix=key_prefix,
        raw_prompt_hash=identity.get("raw_prompt_hash"),
        actual_prompt_hash=identity.get("actual_prompt_hash"),
        parent_context_fingerprint=(identity.get("parent_context_fingerprint") or "")[:16],
    )


def get_cached_analysis(
    file_id: str,
    prompt: str,
    parent_context: Any = None,
    allow_unscoped_fallback: bool = False,
) -> dict | None:
    identity = _build_cache_identity(file_id, prompt, parent_context)
    prompt_hash = _hash_cache_identity(identity)
    redis_key = _build_redis_key(file_id, prompt_hash)
    redis_client = _get_redis()
    fallback_identity = dict(identity, parent_context_fingerprint="")
    fallback_prompt_hash = _hash_cache_identity(fallback_identity)
    fallback_redis_key = _build_redis_key(file_id, fallback_prompt_hash)
    should_try_fallback = bool(
        allow_unscoped_fallback
        and identity.get("parent_context_fingerprint")
        and fallback_prompt_hash != prompt_hash
    )

    _emit_cache_identity_log(
        "file_cache_key_resolved",
        file_id=file_id,
        backend=None,
        identity=identity,
        key_prefix=prompt_hash[:12],
    )

    def _decode_cached_payload(cached: Any, *, backend: str, key_prefix: str, used_identity: dict[str, str]) -> dict:
        payload = json.loads(cached)
        _emit_cache_identity_log(
            "file_cache_hit",
            file_id=file_id,
            backend=backend,
            identity=used_identity,
            key_prefix=key_prefix,
        )
        return payload

    if redis_client is not None:
        try:
            cached = redis_client.get(redis_key)
            if cached:
                return _decode_cached_payload(cached, backend="redis", key_prefix=prompt_hash[:12], used_identity=identity)
            if should_try_fallback:
                cached = redis_client.get(fallback_redis_key)
                if cached:
                    return _decode_cached_payload(
                        cached,
                        backend="redis_unscoped_fallback",
                        key_prefix=fallback_prompt_hash[:12],
                        used_identity=fallback_identity,
                    )
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
                return _decode_cached_payload(
                    table.column("payload")[0].as_py(),
                    backend="parquet",
                    key_prefix=prompt_hash[:12],
                    used_identity=identity,
                )
            except Exception as exc:
                emit_structured_log(
                    "file_cache_read_error",
                    level="warning",
                    backend="parquet",
                    error=str(exc)[:180],
                )
        if should_try_fallback:
            fallback_parq_path = _parquet_path(file_id, fallback_prompt_hash)
            if os.path.isfile(fallback_parq_path):
                try:
                    table = pq.read_table(fallback_parq_path)
                    return _decode_cached_payload(
                        table.column("payload")[0].as_py(),
                        backend="parquet_unscoped_fallback",
                        key_prefix=fallback_prompt_hash[:12],
                        used_identity=fallback_identity,
                    )
                except Exception as exc:
                    emit_structured_log(
                        "file_cache_read_error",
                        level="warning",
                        backend="parquet_unscoped_fallback",
                        error=str(exc)[:180],
                    )

    memory_hit = _memory_get(redis_key)
    if memory_hit is not None:
        _emit_cache_identity_log(
            "file_cache_hit",
            file_id=file_id,
            backend="memory_fallback",
            identity=identity,
            key_prefix=prompt_hash[:12],
        )
        return memory_hit
    if should_try_fallback:
        memory_hit = _memory_get(fallback_redis_key)
        if memory_hit is not None:
            _emit_cache_identity_log(
                "file_cache_hit",
                file_id=file_id,
                backend="memory_unscoped_fallback",
                identity=fallback_identity,
                key_prefix=fallback_prompt_hash[:12],
            )
            return memory_hit
    return memory_hit


def set_cached_analysis(
    file_id: str,
    prompt: str,
    payload: dict,
    ttl_seconds: int = 3600,
    parent_context: Any = None,
    write_unscoped_alias: bool = False,
) -> None:
    identity = _build_cache_identity(file_id, prompt, parent_context)
    prompt_hash = _hash_cache_identity(identity)
    redis_key = _build_redis_key(file_id, prompt_hash)
    safe_payload = _json_safe(payload)
    serialized = json.dumps(safe_payload, ensure_ascii=False, separators=(",", ":"))
    alias_identity = dict(identity, parent_context_fingerprint="")
    alias_prompt_hash = _hash_cache_identity(alias_identity)
    should_write_alias = bool(
        write_unscoped_alias
        and identity.get("parent_context_fingerprint")
        and alias_prompt_hash != prompt_hash
    )

    redis_client = _get_redis()
    if redis_client is not None and len(serialized) <= _REDIS_MAX_SIZE:
        try:
            redis_client.setex(redis_key, max(int(ttl_seconds or 0), 1), serialized)
            if should_write_alias:
                redis_client.setex(_build_redis_key(file_id, alias_prompt_hash), max(int(ttl_seconds or 0), 1), serialized)
            _emit_cache_identity_log(
                "file_cache_write",
                file_id=file_id,
                backend="redis",
                identity=identity,
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
            if should_write_alias:
                alias_path = _parquet_path(file_id, alias_prompt_hash)
                alias_table = pa.table({"payload": array, "file_id": pa.array([file_id]), "prompt_hash": pa.array([alias_prompt_hash])})
                pq.write_table(alias_table, alias_path)
            _emit_cache_identity_log(
                "file_cache_write",
                file_id=file_id,
                backend="parquet",
                identity=identity,
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
    if should_write_alias:
        _memory_set(_build_redis_key(file_id, alias_prompt_hash), safe_payload, ttl_seconds)
    _emit_cache_identity_log(
        "file_cache_write",
        file_id=file_id,
        backend="memory_fallback",
        identity=identity,
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
