from __future__ import annotations

import threading
import time

from fastapi import HTTPException, Request
from jose import jwt
from redis.exceptions import RedisError

from app.core.config import settings
from app.core.redis_client import get_redis_client
from app.core.structured_logging import emit_structured_log
from app.core.supabase_client import get_supabase_service_client


_MEMORY_STORE: dict[str, tuple[int, int]] = {}
_MEMORY_LOCK = threading.Lock()

_TEAM_CACHE: dict[str, tuple[str | None, float]] = {}
_TEAM_CACHE_LOCK = threading.Lock()


def _get_redis_client():
    return get_redis_client(purpose="rate_limit")


def _extract_user_id_from_token(token: str) -> str | None:
    raw_token = str(token or "").strip()
    if not raw_token:
        return None
    try:
        secret = settings.SUPABASE_JWT_SECRET
        if secret:
            claims = jwt.decode(raw_token, secret, algorithms=["HS256"])
        else:
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


def enforce_burst_limit(
    *,
    request: Request,
    token: str,
    scope: str,
    limit: int,
    window_seconds: int,
) -> None:
    """Implementa protección contra ráfagas (Burst Protection)."""
    enforce_rate_limit(
        request=request,
        token=token,
        scope=f"burst:{scope}",
        limit=limit,
        window_seconds=window_seconds,
    )


def acquire_concurrency_slot(token: str, limit: int, ttl_seconds: int) -> bool:
    """Intenta adquirir un slot de concurrencia. Retorna True si se adquirió, False si excedió el límite."""
    if not settings.RATE_LIMIT_ENABLED or limit <= 0:
        return True

    user_id = _extract_user_id_from_token(token)
    if not user_id:
        return True

    key = f"concurrency:user:{user_id}"
    redis_client = _get_redis_client()
    
    if redis_client:
        try:
            count = int(redis_client.incr(key))
            if count == 1:
                redis_client.expire(key, ttl_seconds)
            if count > limit:
                redis_client.decr(key)
                emit_structured_log(
                    "api_concurrency_limit_exceeded",
                    level="warning",
                    user_id=user_id,
                    count=count,
                    limit=limit,
                )
                return False
            return True
        except RedisError as error:
            emit_structured_log(
                "rate_limit_concurrency_redis_error",
                level="warning",
                user_id=user_id,
                error=str(error)[:180],
            )

    # Fallback a memoria
    now_epoch = int(time.time())
    with _MEMORY_LOCK:
        current_count, expires_at = _MEMORY_STORE.get(key, (0, now_epoch + ttl_seconds))
        if expires_at <= now_epoch:
            current_count = 0
            expires_at = now_epoch + ttl_seconds
            
        if current_count >= limit:
            emit_structured_log(
                "api_concurrency_limit_exceeded_memory",
                level="warning",
                user_id=user_id,
                count=current_count + 1,
                limit=limit,
            )
            return False
            
        _MEMORY_STORE[key] = (current_count + 1, expires_at)
        return True


def release_concurrency_slot(user_id: str) -> None:
    """Libera un slot de concurrencia al terminar la tarea."""
    if not settings.RATE_LIMIT_ENABLED or not user_id:
        return

    key = f"concurrency:user:{user_id}"
    redis_client = _get_redis_client()
    
    if redis_client:
        try:
            count = int(redis_client.decr(key))
            if count < 0:
                redis_client.set(key, 0)
        except RedisError as error:
            emit_structured_log(
                "rate_limit_concurrency_release_error",
                level="warning",
                user_id=user_id,
                error=str(error)[:180],
            )
            
    with _MEMORY_LOCK:
        if key in _MEMORY_STORE:
            current_count, expires_at = _MEMORY_STORE[key]
            new_count = max(0, current_count - 1)
            _MEMORY_STORE[key] = (new_count, expires_at)


def _reset_rate_limit_state_for_tests() -> None:
    with _MEMORY_LOCK:
        _MEMORY_STORE.clear()
    with _TEAM_CACHE_LOCK:
        _TEAM_CACHE.clear()


def check_rate_limits_batch(
    *,
    request: Request,
    token: str,
    scope: str,
    rate_limit: int,
    rate_window: int,
    burst_limit: int,
    burst_window: int,
    max_concurrent: int,
    concurrency_ttl: int,
) -> tuple[bool, dict]:
    """Valida rate limit, burst limit y concurrencia en un solo round-trip a Redis.

    Garantías de seguridad:
    1. Agrupa los INCR en indices [0, 1, 2] y los EXPIRE después.
    2. Revierte inmediatamente el slot de concurrencia si falla rate o burst limit (evita slots fantasma).
    3. Fallback seguro a los métodos secuenciales individuales si ocurre RedisError.
    """
    if not settings.RATE_LIMIT_ENABLED:
        return True, {"slot_acquired": False, "user_id": None}

    actor_key, user_id, team_id = _actor_key(request, token)
    now_epoch = int(time.time())

    rate_bucket = now_epoch - (now_epoch % rate_window) if rate_window > 0 else now_epoch
    rate_key = f"ratelimit:{scope}:{actor_key}:{rate_bucket}"

    burst_bucket = now_epoch - (now_epoch % burst_window) if burst_window > 0 else now_epoch
    burst_key = f"ratelimit:burst:{scope}:{actor_key}:{burst_bucket}"

    conc_key = f"concurrency:user:{user_id}" if (user_id and max_concurrent > 0) else None

    redis_client = _get_redis_client()
    if redis_client is not None:
        try:
            pipe = redis_client.pipeline(transaction=False)

            # 1. Comandos INCR (Posiciones 0, 1 y opcional 2)
            pipe.incr(rate_key)       # [0]
            pipe.incr(burst_key)      # [1]
            if conc_key:
                pipe.incr(conc_key)   # [2]

            # 2. Comandos EXPIRE
            pipe.expire(rate_key, rate_window + 1)
            pipe.expire(burst_key, burst_window + 1)
            if conc_key:
                pipe.expire(conc_key, concurrency_ttl)

            results = pipe.execute()

            rate_count = int(results[0])
            burst_count = int(results[1])
            conc_count = int(results[2]) if conc_key else 0

            # Validación de Rate Limit
            if rate_limit > 0 and rate_count > rate_limit:
                redis_client.decr(rate_key)
                if conc_key:
                    redis_client.decr(conc_key)
                emit_structured_log(
                    "api_rate_limit_exceeded",
                    level="warning",
                    scope=scope,
                    path=request.url.path,
                    method=request.method,
                    actor_key=actor_key,
                    user_id=user_id,
                    team_id=team_id,
                    count=rate_count,
                    limit=rate_limit,
                    retry_after_seconds=rate_window,
                )
                return False, {
                    "error": "Límite de solicitudes excedido. Inténtalo de nuevo en unos segundos.",
                    "status_code": 429,
                    "retry_after": rate_window,
                    "slot_acquired": False,
                    "user_id": user_id,
                }

            # Validación de Burst Limit
            if burst_limit > 0 and burst_count > burst_limit:
                redis_client.decr(burst_key)
                if conc_key:
                    redis_client.decr(conc_key)
                emit_structured_log(
                    "api_burst_limit_exceeded",
                    level="warning",
                    scope=f"burst:{scope}",
                    path=request.url.path,
                    method=request.method,
                    actor_key=actor_key,
                    user_id=user_id,
                    team_id=team_id,
                    count=burst_count,
                    limit=burst_limit,
                    retry_after_seconds=burst_window,
                )
                return False, {
                    "error": "Demasiadas peticiones consecutivas (ráfaga). Espera un momento.",
                    "status_code": 429,
                    "retry_after": burst_window,
                    "slot_acquired": False,
                    "user_id": user_id,
                }

            # Validación de Concurrencia
            if conc_key and max_concurrent > 0 and conc_count > max_concurrent:
                redis_client.decr(conc_key)
                emit_structured_log(
                    "api_concurrency_limit_exceeded",
                    level="warning",
                    user_id=user_id,
                    count=conc_count,
                    limit=max_concurrent,
                )
                return False, {
                    "error": "Tienes demasiados análisis en curso. Por favor, espera a que terminen antes de solicitar uno nuevo.",
                    "status_code": 429,
                    "retry_after": 5,
                    "slot_acquired": False,
                    "user_id": user_id,
                }

            return True, {
                "slot_acquired": bool(conc_key),
                "user_id": user_id,
            }
        except RedisError as error:
            emit_structured_log(
                "rate_limit_pipeline_redis_error",
                level="warning",
                error=str(error)[:180],
            )

    # Fallback seguro a métodos individuales (incluye fallback en memoria)
    enforce_rate_limit(
        request=request,
        token=token,
        scope=scope,
        limit=rate_limit,
        window_seconds=rate_window,
    )
    enforce_burst_limit(
        request=request,
        token=token,
        scope=scope,
        limit=burst_limit,
        window_seconds=burst_window,
    )
    slot_ok = acquire_concurrency_slot(token, max_concurrent, concurrency_ttl)
    if not slot_ok:
        return False, {
            "error": "Tienes demasiados análisis en curso. Por favor, espera a que terminen antes de solicitar uno nuevo.",
            "status_code": 429,
            "retry_after": 5,
            "slot_acquired": False,
            "user_id": user_id,
        }
    return True, {"slot_acquired": True, "user_id": user_id}
