from __future__ import annotations

import threading
import time

from fastapi import HTTPException, Request
from jose import jwt
from redis import Redis
from redis.exceptions import RedisError

from app.core.config import settings
from app.core.structured_logging import emit_structured_log
from app.core.supabase_client import get_supabase_service_client


_REDIS_CLIENT: Redis | None = None
_REDIS_INIT_ATTEMPTED = False
_REDIS_LOCK = threading.Lock()

_MEMORY_STORE: dict[str, tuple[int, int]] = {}
_MEMORY_LOCK = threading.Lock()

_TEAM_CACHE: dict[str, tuple[str | None, float]] = {}
_TEAM_CACHE_LOCK = threading.Lock()


def _get_redis_client() -> Redis | None:
    global _REDIS_CLIENT, _REDIS_INIT_ATTEMPTED

    if _REDIS_INIT_ATTEMPTED:
        return _REDIS_CLIENT

    with _REDIS_LOCK:
        if _REDIS_INIT_ATTEMPTED:
            return _REDIS_CLIENT

        _REDIS_INIT_ATTEMPTED = True
        storage_url = str(settings.RATE_LIMIT_STORAGE_URL or "").strip()
        if not storage_url:
            return None

        try:
            client = Redis.from_url(
                storage_url,
                decode_responses=True,
                socket_connect_timeout=1.0,
                socket_timeout=1.0,
            )
            client.ping()
            _REDIS_CLIENT = client
            emit_structured_log(
                "rate_limit_storage_ready",
                storage="redis",
                storage_url=storage_url,
            )
        except Exception as error:
            emit_structured_log(
                "rate_limit_storage_fallback_memory",
                level="warning",
                storage_url=storage_url,
                error=str(error)[:180],
            )
            _REDIS_CLIENT = None

    return _REDIS_CLIENT


def _extract_user_id_from_token(token: str) -> str | None:
    raw_token = str(token or "").strip()
    if not raw_token:
        return None
    try:
        claims = jwt.get_unverified_claims(raw_token)
    except Exception:
        return None
    subject = str(claims.get("sub") or "").strip()
    return subject or None


def _resolve_team_id(user_id: str | None) -> str | None:
    if not user_id:
        return None

    now = time.time()
    with _TEAM_CACHE_LOCK:
        cached_value = _TEAM_CACHE.get(user_id)
        if cached_value and cached_value[1] > now:
            return cached_value[0]

    team_id: str | None = None
    try:
        service_client = get_supabase_service_client()
        response = (
            service_client.table("team_members")
            .select("team_id")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        if response.data:
            candidate = str(response.data[0].get("team_id") or "").strip()
            team_id = candidate or None
    except Exception as error:
        emit_structured_log(
            "rate_limit_team_resolve_error",
            level="warning",
            user_id=user_id,
            error=str(error)[:180],
        )

    expires_at = now + max(int(settings.RATE_LIMIT_TEAM_CACHE_TTL_SECONDS or 300), 30)
    with _TEAM_CACHE_LOCK:
        _TEAM_CACHE[user_id] = (team_id, expires_at)
    return team_id


def _actor_key(request: Request, token: str) -> tuple[str, str | None, str | None]:
    user_id = _extract_user_id_from_token(token)
    team_id = _resolve_team_id(user_id)

    if user_id and team_id:
        return (f"team:{team_id}:user:{user_id}", user_id, team_id)
    if user_id:
        return (f"user:{user_id}", user_id, None)

    ip = str(request.client.host if request.client else "unknown").strip() or "unknown"
    return (f"ip:{ip}", None, None)


def _increment_counter(key: str, window_seconds: int) -> tuple[int, int]:
    now_epoch = int(time.time())
    redis_client = _get_redis_client()
    if redis_client is not None:
        try:
            count = int(redis_client.incr(key))
            if count == 1:
                redis_client.expire(key, window_seconds + 1)
            ttl = int(redis_client.ttl(key))
            retry_after = ttl if ttl > 0 else max(window_seconds, 1)
            return count, retry_after
        except RedisError as error:
            emit_structured_log(
                "rate_limit_redis_increment_error",
                level="warning",
                key=key,
                error=str(error)[:180],
            )

    with _MEMORY_LOCK:
        current_count, expires_at = _MEMORY_STORE.get(key, (0, now_epoch + window_seconds))
        if expires_at <= now_epoch:
            current_count = 0
            expires_at = now_epoch + window_seconds
        current_count += 1
        _MEMORY_STORE[key] = (current_count, expires_at)
        retry_after = max(expires_at - now_epoch, 1)
        return current_count, retry_after


def enforce_rate_limit(
    *,
    request: Request,
    token: str,
    scope: str,
    limit: int,
    window_seconds: int,
) -> None:
    if not settings.RATE_LIMIT_ENABLED:
        return
    if limit <= 0 or window_seconds <= 0:
        return

    now_epoch = int(time.time())
    bucket_epoch = now_epoch - (now_epoch % window_seconds)
    actor_key, user_id, team_id = _actor_key(request, token)
    rate_key = f"ratelimit:{scope}:{actor_key}:{bucket_epoch}"

    count, retry_after = _increment_counter(rate_key, window_seconds)
    if count <= limit:
        return

    emit_structured_log(
        "api_rate_limit_exceeded",
        level="warning",
        scope=scope,
        path=request.url.path,
        method=request.method,
        actor_key=actor_key,
        user_id=user_id,
        team_id=team_id,
        count=count,
        limit=limit,
        retry_after_seconds=retry_after,
    )
    raise HTTPException(
        status_code=429,
        detail="Límite de solicitudes excedido. Inténtalo de nuevo en unos segundos.",
        headers={"Retry-After": str(retry_after)},
    )


def _reset_rate_limit_state_for_tests() -> None:
    global _REDIS_CLIENT, _REDIS_INIT_ATTEMPTED

    with _REDIS_LOCK:
        _REDIS_CLIENT = None
        _REDIS_INIT_ATTEMPTED = False
    with _MEMORY_LOCK:
        _MEMORY_STORE.clear()
    with _TEAM_CACHE_LOCK:
        _TEAM_CACHE.clear()
