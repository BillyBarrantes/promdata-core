import os

from fastapi import FastAPI, Request as FastAPIRequest
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from redis import Redis
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.routes import router as api_router
from app.core.config import settings
from app.core.sentry import init_sentry
from app.core.structured_logging import emit_structured_log
from app.services.canonical_canary_health import build_canonical_tabular_canary_health
from app.services.runtime_governance import get_runtime_governance_payload

# ---------------------------------------------------------------------------
# Sentry: inicializar ANTES que cualquier middleware o router.
# Si SENTRY_DSN no está configurado, es un no-op silencioso.
# ---------------------------------------------------------------------------
init_sentry()

app = FastAPI(title="PromData API")

# ---------------------------------------------------------------------------
# CORS: Fuente única de orígenes permitidos
# Se leen de ALLOWED_ORIGINS (comma-separated) + FRONTEND_APP_URL (legacy).
# Si ninguno está configurado, se permite * como fallback de emergencia.
#
# Configura en Cloud Run:
#   ALLOWED_ORIGINS=https://livion.lat,https://www.livion.lat
#   FRONTEND_APP_URL=https://livion.lat
# ---------------------------------------------------------------------------
def _normalize_origin(raw: str) -> str:
    value = str(raw or "").strip().strip("'").strip('"')
    if value.endswith("/"):
        value = value[:-1]
    return value


_raw_allowed = os.getenv("ALLOWED_ORIGINS", "")
_extra_origins = [_normalize_origin(o) for o in _raw_allowed.split(",") if _normalize_origin(o)]

_frontend_url = _normalize_origin(settings.FRONTEND_APP_URL or "")

_origins_set = {"http://localhost:3000"}
if _frontend_url:
    _origins_set.add(_frontend_url)
_origins_set.update(_extra_origins)
_origins_set.discard("")

# Si no hay orígenes de producción configurados → fallback a wildcard
if len(_origins_set) == 1 and "http://localhost:3000" in _origins_set:
    origins: list = ["*"]
    _allow_credentials = False
else:
    origins = sorted(_origins_set)
    _allow_credentials = True

# Log explícito para verificar en Cloud Run
print(f"[CORS] allow_origins configurados: {origins}", flush=True)

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=_allow_credentials,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "Accept",
        "Origin",
        "X-Requested-With",
        "X-Request-ID",
        "Cache-Control",
    ],
    expose_headers=["X-Request-ID"],
    max_age=600,
)


# ---------------------------------------------------------------------------
# [ENTERPRISE v1] Sanitización global de errores HTTP
# IMPORTANTE: Registrar ANTES de include_router para que no interfiera
# con las respuestas internas de preflight OPTIONS del CORSMiddleware.
# ---------------------------------------------------------------------------
@app.exception_handler(StarletteHTTPException)
async def _sanitize_http_errors(request: FastAPIRequest, exc: StarletteHTTPException) -> JSONResponse:
    # Dejar pasar las respuestas de OPTIONS sin modificar
    if request.method == "OPTIONS":
        return JSONResponse(
            status_code=200,
            content={},
            headers={
                "Access-Control-Allow-Origin": request.headers.get("origin", "*"),
                "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
                "Access-Control-Allow-Headers": "Authorization, Content-Type, Accept, Origin, X-Requested-With, Cache-Control",
                "Access-Control-Max-Age": "600",
            },
        )
    if exc.status_code >= 500:
        emit_structured_log(
            "api_internal_error_sanitized",
            level="error",
            method=request.method,
            path=str(request.url.path),
            status_code=exc.status_code,
            internal_detail=str(exc.detail)[:500],
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": "Error interno del servidor. Por favor, inténtelo de nuevo."},
        )
    # 4xx: pasar al cliente sin modificar
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )


app.include_router(api_router, prefix="/api/v1")



@app.on_event("startup")
def _log_runtime_governance_snapshot() -> None:
    governance = get_runtime_governance_payload()
    emit_structured_log(
        "runtime_governance_snapshot",
        environment_profile=governance["environment_profile"],
        overall_status=governance["overall_status"],
        hardening_ready=governance["hardening_ready"],
        warning_count=governance["warning_count"],
        critical_count=governance["critical_count"],
        warnings=governance["warnings"],
        criticals=governance["criticals"],
    )


def _check_redis(url: str) -> tuple[bool, str | None]:
    normalized = str(url or "").strip()
    if not normalized:
        return True, None
    if not normalized.startswith(("redis://", "rediss://")):
        return True, None
    try:
        client = Redis.from_url(
            normalized,
            decode_responses=True,
            socket_connect_timeout=1.0,
            socket_timeout=1.0,
        )
        client.ping()
        return True, None
    except Exception as error:
        return False, str(error)


@app.get("/health/live", summary="Liveness Probe")
def health_live():
    return {"status": "ok", "probe": "liveness"}


@app.get("/health/ready", summary="Readiness Probe")
def health_ready():
    broker_ok, broker_error = _check_redis(settings.CELERY_BROKER_URL)
    backend_ok, backend_error = _check_redis(settings.CELERY_RESULT_BACKEND)
    canary_health = build_canonical_tabular_canary_health()
    canary_ok = (
        not bool(canary_health.get("functional_switch_enabled"))
        or bool(canary_health.get("ready_for_functional_canary"))
    )

    checks = {
        "celery_broker": {
            "ok": broker_ok,
            "target": settings.CELERY_BROKER_URL,
            "error": broker_error,
        },
        "celery_result_backend": {
            "ok": backend_ok,
            "target": settings.CELERY_RESULT_BACKEND,
            "error": backend_error,
        },
        "canonical_tabular_canary": {
            "ok": canary_ok,
            "status": canary_health.get("status"),
            "summary": canary_health.get("summary"),
            "functional_switch_enabled": canary_health.get("functional_switch_enabled"),
            "ready_for_functional_canary": canary_health.get("ready_for_functional_canary"),
        },
    }
    ready = broker_ok and backend_ok and canary_ok
    payload = {
        "status": "ok" if ready else "degraded",
        "probe": "readiness",
        "checks": checks,
    }

    if not ready:
        raise StarletteHTTPException(status_code=503, detail=payload)
    return payload


@app.get("/health/runtime", summary="Runtime Governance Summary")
def health_runtime():
    payload = get_runtime_governance_payload()
    return {
        "status": payload["overall_status"],
        "probe": "runtime_governance",
        **payload,
    }


@app.get("/health/release", summary="Release Governance Summary")
def health_release():
    payload = get_runtime_governance_payload()
    return {
        "status": payload["checks"]["release"]["status"],
        "probe": "release_governance",
        "release": payload["release"],
        "environment_profile": payload["environment_profile"],
        "hardening_ready": payload["hardening_ready"],
        "summary": payload["checks"]["release"]["summary"],
    }


@app.get("/health/secrets", summary="Secrets Governance Summary")
def health_secrets():
    payload = get_runtime_governance_payload()
    return {
        "status": payload["checks"]["secrets"]["status"],
        "probe": "secrets_governance",
        "environment_profile": payload["environment_profile"],
        "hardening_ready": payload["hardening_ready"],
        "summary": payload["checks"]["secrets"]["summary"],
        "secrets": payload["secrets"],
    }


@app.get("/health", summary="Compatibility Health Probe")
def health():
    return health_live()


@app.get("/health/canary", summary="Canonical Tabular Canary Health")
def health_canary():
    payload = build_canonical_tabular_canary_health()
    return {
        "status": payload["status"],
        "probe": "canonical_tabular_canary",
        **payload,
    }
