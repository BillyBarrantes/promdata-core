from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from app.core.config import settings
from app.services.canonical_canary_health import build_canonical_tabular_canary_health


def _as_bool(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _normalized_url(raw_url: str | None) -> str:
    return str(raw_url or "").strip().rstrip("/")


def _hostname_from_url(raw_url: str | None) -> str:
    candidate = _normalized_url(raw_url)
    if not candidate:
        return ""
    return (urlparse(candidate).hostname or "").strip().lower()


def _is_local_hostname(hostname: str) -> bool:
    return hostname in {"localhost", "127.0.0.1", "0.0.0.0"}


def _is_https_public_url(raw_url: str | None) -> bool:
    candidate = _normalized_url(raw_url)
    if not candidate:
        return False
    parsed = urlparse(candidate)
    hostname = (parsed.hostname or "").strip().lower()
    return parsed.scheme == "https" and bool(parsed.netloc) and not _is_local_hostname(hostname)


def _build_check(status: str, summary: str, **details: Any) -> dict[str, Any]:
    return {
        "status": status,
        "summary": summary,
        **details,
    }


def _normalized_release_value(raw_value: str | None) -> str:
    return str(raw_value or "").strip()


def _is_placeholder_release_value(raw_value: str | None) -> bool:
    normalized = _normalized_release_value(raw_value).lower()
    return normalized in {
        "",
        "local",
        "development",
        "dev",
        "unknown",
        "placeholder",
        "replace-me",
        "staging-build-sha",
        "prod-build-sha",
    }


def _normalized_secret_value(raw_value: str | None) -> str:
    return str(raw_value or "").strip()


def _infer_environment_profile() -> str:
    backend_host = _hostname_from_url(settings.BACKEND_PUBLIC_URL)
    frontend_host = _hostname_from_url(settings.FRONTEND_APP_URL)
    joined_hosts = " ".join(part for part in {backend_host, frontend_host} if part)

    if backend_host and frontend_host and _is_local_hostname(backend_host) and _is_local_hostname(frontend_host):
        return "development"
    if "staging" in joined_hosts:
        return "staging"
    if _is_https_public_url(settings.BACKEND_PUBLIC_URL) and _is_https_public_url(settings.FRONTEND_APP_URL):
        return "production"
    return "unknown"


def get_runtime_governance_payload() -> dict[str, Any]:
    """
    Runtime Governance V1.
    Resume el estado operativo del entorno sin exponer secretos, para readiness enterprise.
    """
    environment_profile = _infer_environment_profile()
    checks: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    criticals: list[str] = []

    supabase_url_ready = bool(str(settings.SUPABASE_URL or "").strip())
    supabase_service_access_ready = bool(
        str(settings.SUPABASE_SERVICE_ROLE_KEY or settings.SUPABASE_KEY or "").strip()
    )
    supabase_anon_ready = bool(str(settings.SUPABASE_ANON_KEY or "").strip())
    supabase_jwt_ready = bool(str(settings.SUPABASE_JWT_SECRET or "").strip())
    supabase_ready = supabase_url_ready and supabase_service_access_ready and supabase_anon_ready
    checks["supabase"] = _build_check(
        "healthy" if supabase_ready else "critical",
        "Supabase operativo para el runtime actual." if supabase_ready else "Faltan credenciales base operativas de Supabase.",
        configured=supabase_ready,
        url_configured=supabase_url_ready,
        service_access_configured=supabase_service_access_ready,
        anon_key_configured=supabase_anon_ready,
        jwt_secret_configured=supabase_jwt_ready,
    )
    if not supabase_ready:
        criticals.append("supabase_missing_credentials")
    elif environment_profile in {"staging", "production"} and not supabase_jwt_ready:
        warnings.append("supabase_jwt_secret_missing")
        checks["supabase"] = _build_check(
            "warning",
            "Supabase operativo, pero falta SUPABASE_JWT_SECRET para endurecimiento enterprise completo.",
            configured=supabase_ready,
            url_configured=supabase_url_ready,
            service_access_configured=supabase_service_access_ready,
            anon_key_configured=supabase_anon_ready,
            jwt_secret_configured=supabase_jwt_ready,
        )

    gemini_ready = bool(str(settings.GEMINI_API_KEY or "").strip())
    gemini_provider = str(getattr(settings, "GEMINI_CLIENT_PROVIDER", "genai") or "genai").strip().lower()
    checks["gemini"] = _build_check(
        "healthy" if gemini_ready else "critical",
        "Modelo generativo configurado." if gemini_ready else "Falta GEMINI_API_KEY.",
        configured=gemini_ready,
        model_name=str(settings.AI_MODEL_NAME or "").strip(),
        provider=gemini_provider,
    )
    if not gemini_ready:
        criticals.append("gemini_missing_api_key")

    broker_ready = bool(str(settings.CELERY_BROKER_URL or "").strip())
    result_backend_ready = bool(str(settings.CELERY_RESULT_BACKEND or "").strip())
    celery_ready = broker_ready and result_backend_ready
    checks["celery"] = _build_check(
        "healthy" if celery_ready else "critical",
        "Broker y result backend configurados." if celery_ready else "Faltan endpoints de Celery.",
        broker_configured=broker_ready,
        result_backend_configured=result_backend_ready,
    )
    if not celery_ready:
        criticals.append("celery_missing_backend")

    backend_public_https = _is_https_public_url(settings.BACKEND_PUBLIC_URL)
    frontend_public_https = _is_https_public_url(settings.FRONTEND_APP_URL)
    if environment_profile == "development":
        public_urls_status = "healthy"
        public_urls_summary = "URLs locales aceptadas para desarrollo."
    else:
        public_urls_status = "healthy" if backend_public_https and frontend_public_https else "warning"
        public_urls_summary = (
            "URLs públicas seguras configuradas."
            if public_urls_status == "healthy"
            else "Staging/producción deben usar HTTPS público en backend y frontend."
        )
        if public_urls_status != "healthy":
            warnings.append("public_urls_not_hardened")

    checks["public_urls"] = _build_check(
        public_urls_status,
        public_urls_summary,
        backend_public_url=_normalized_url(settings.BACKEND_PUBLIC_URL),
        frontend_app_url=_normalized_url(settings.FRONTEND_APP_URL),
        backend_public_https=backend_public_https,
        frontend_public_https=frontend_public_https,
    )

    rate_limit_storage_ready = bool(str(settings.RATE_LIMIT_STORAGE_URL or "").strip())
    rate_limit_enabled = bool(settings.RATE_LIMIT_ENABLED)
    if environment_profile in {"staging", "production"} and (not rate_limit_enabled or not rate_limit_storage_ready):
        rate_limit_status = "warning"
        warnings.append("rate_limit_not_hardened")
    else:
        rate_limit_status = "healthy"
    checks["rate_limit"] = _build_check(
        rate_limit_status,
        "Rate limit operativo." if rate_limit_status == "healthy" else "Rate limit debe quedar habilitado y con storage en staging/prod.",
        enabled=rate_limit_enabled,
        storage_configured=rate_limit_storage_ready,
    )

    watchdog_enabled = _as_bool(settings.CONNECTOR_WATCHDOG_ENABLED)
    google_watch_mode = str(settings.GOOGLE_DRIVE_WATCH_MODE or "").strip().lower() or "webhook"
    google_webhook_public = _is_https_public_url(settings.GOOGLE_DRIVE_WEBHOOK_CALLBACK_URL)
    google_oauth_ready = bool(str(settings.GOOGLE_DRIVE_CLIENT_ID or "").strip()) and bool(str(settings.GOOGLE_DRIVE_CLIENT_SECRET or "").strip())
    microsoft_oauth_ready = bool(str(settings.MICROSOFT_ONEDRIVE_CLIENT_ID or "").strip()) and bool(str(settings.MICROSOFT_ONEDRIVE_CLIENT_SECRET or "").strip())

    connector_status = "healthy"
    connector_summary = "Conectores cloud listos para operar."
    if watchdog_enabled and google_watch_mode == "webhook" and not google_webhook_public:
        connector_status = "warning"
        connector_summary = "Google Drive está en webhook pero su callback no es HTTPS público."
        warnings.append("google_webhook_not_public")
    elif environment_profile in {"staging", "production"} and (not google_oauth_ready or not microsoft_oauth_ready):
        connector_status = "warning"
        connector_summary = "Faltan credenciales OAuth cloud para operación enterprise completa."
        warnings.append("cloud_oauth_not_complete")

    checks["cloud_connectors"] = _build_check(
        connector_status,
        connector_summary,
        watchdog_enabled=watchdog_enabled,
        google_oauth_ready=google_oauth_ready,
        microsoft_oauth_ready=microsoft_oauth_ready,
        google_watch_mode=google_watch_mode,
        google_webhook_public=google_webhook_public,
    )

    supabase_service_role_explicit = bool(_normalized_secret_value(settings.SUPABASE_SERVICE_ROLE_KEY))
    supabase_key_present = bool(_normalized_secret_value(settings.SUPABASE_KEY))
    supabase_anon_value = _normalized_secret_value(settings.SUPABASE_ANON_KEY)
    supabase_service_value = _normalized_secret_value(settings.SUPABASE_SERVICE_ROLE_KEY or settings.SUPABASE_KEY)
    supabase_service_distinct_from_anon = bool(supabase_service_value) and bool(supabase_anon_value) and supabase_service_value != supabase_anon_value
    google_client_secret_ready = bool(_normalized_secret_value(settings.GOOGLE_DRIVE_CLIENT_SECRET))
    microsoft_client_secret_ready = bool(_normalized_secret_value(settings.MICROSOFT_ONEDRIVE_CLIENT_SECRET))

    secrets_status = "healthy"
    secrets_summary = "Postura de secretos compatible con el entorno actual."
    secrets_hardening_ready = environment_profile == "development"

    if environment_profile in {"staging", "production"}:
        secrets_hardening_ready = (
            supabase_service_role_explicit
            and supabase_service_distinct_from_anon
            and supabase_jwt_ready
            and google_client_secret_ready
            and microsoft_client_secret_ready
        )
        if not supabase_service_role_explicit:
            warnings.append("supabase_service_role_not_explicit")
        if not supabase_service_distinct_from_anon:
            warnings.append("supabase_service_role_not_distinct")
        if not google_client_secret_ready:
            warnings.append("google_client_secret_missing")
        if not microsoft_client_secret_ready:
            warnings.append("microsoft_client_secret_missing")
        if not secrets_hardening_ready:
            secrets_status = "warning"
            secrets_summary = "El entorno opera, pero aún no cumple endurecimiento enterprise de secretos."

    checks["secrets"] = _build_check(
        secrets_status,
        secrets_summary,
        supabase_service_role_explicit=supabase_service_role_explicit,
        supabase_service_access_present=supabase_key_present or supabase_service_role_explicit,
        supabase_service_distinct_from_anon=supabase_service_distinct_from_anon,
        supabase_jwt_secret_configured=supabase_jwt_ready,
        google_client_secret_configured=google_client_secret_ready,
        microsoft_client_secret_configured=microsoft_client_secret_ready,
        secrets_hardening_ready=secrets_hardening_ready,
    )

    deploy_env = _normalized_release_value(settings.APP_DEPLOY_ENV).lower() or "development"
    release_channel = _normalized_release_value(settings.APP_RELEASE_CHANNEL)
    build_version = _normalized_release_value(settings.APP_BUILD_VERSION)
    build_sha = _normalized_release_value(settings.APP_BUILD_SHA)
    build_sha_present = not _is_placeholder_release_value(build_sha)
    build_version_present = not _is_placeholder_release_value(build_version)
    release_env_matches = (
        environment_profile == "unknown"
        or deploy_env == environment_profile
        or (environment_profile == "production" and deploy_env == "prod")
    )

    release_status = "healthy"
    release_summary = "Metadatos de release y rollback disponibles."
    rollback_ready = environment_profile in {"staging", "production"}

    if environment_profile in {"staging", "production"} and not release_env_matches:
        release_status = "warning"
        release_summary = "APP_DEPLOY_ENV no coincide con el perfil operativo detectado."
        warnings.append("release_env_mismatch")
        rollback_ready = False
    elif environment_profile in {"staging", "production"} and (not build_version_present or not build_sha_present):
        release_status = "warning"
        release_summary = "Faltan metadatos de build para rollback seguro."
        warnings.append("release_metadata_incomplete")
        rollback_ready = False
    elif environment_profile == "development":
        release_summary = "Metadatos locales aceptados para desarrollo."
        rollback_ready = False

    checks["release"] = _build_check(
        release_status,
        release_summary,
        deploy_env=deploy_env,
        release_channel=release_channel or "local",
        build_version=build_version or "0.0.0-local",
        build_sha_present=build_sha_present,
        rollback_ready=rollback_ready,
        environment_profile=environment_profile,
    )

    canary_health = build_canonical_tabular_canary_health()
    canary_status = "healthy"
    canary_summary = "Canary tabular en modo seguro."
    if canary_health.get("functional_switch_enabled"):
        if canary_health.get("ready_for_functional_canary"):
            canary_status = "healthy"
            canary_summary = "Canary tabular listo para activación funcional controlada."
        else:
            canary_status = "warning"
            canary_summary = "Canary tabular con switch funcional, pero bloqueado por health gate."
            warnings.append("canonical_tabular_canary_not_ready")
    elif canary_health.get("router_enabled"):
        canary_status = "healthy"
        canary_summary = "Canary tabular en dry-run con fallback a legacy."

    checks["canonical_tabular_canary"] = _build_check(
        canary_status,
        canary_summary,
        router_enabled=bool(canary_health.get("router_enabled")),
        functional_switch_enabled=bool(canary_health.get("functional_switch_enabled")),
        fail_open_enabled=bool(canary_health.get("fail_open_enabled")),
        ready_for_functional_canary=bool(canary_health.get("ready_for_functional_canary")),
        shadow_evidence_ready=bool(canary_health.get("shadow_evidence_ready")),
        shadow_evidence_reason=canary_health.get("shadow_evidence_reason"),
    )

    overall_status = "critical" if criticals else "warning" if warnings else "healthy"
    hardening_ready = (
        environment_profile in {"staging", "production"}
        and overall_status == "healthy"
        and rollback_ready
        and secrets_hardening_ready
    )

    return {
        "environment_profile": environment_profile,
        "overall_status": overall_status,
        "hardening_ready": hardening_ready,
        "warning_count": len(warnings),
        "critical_count": len(criticals),
        "warnings": warnings,
        "criticals": criticals,
        "release": {
            "deploy_env": deploy_env,
            "release_channel": release_channel or "local",
            "build_version": build_version or "0.0.0-local",
            "build_sha_present": build_sha_present,
            "rollback_ready": rollback_ready,
        },
        "secrets": {
            "supabase_service_role_explicit": supabase_service_role_explicit,
            "supabase_service_distinct_from_anon": supabase_service_distinct_from_anon,
            "supabase_jwt_secret_configured": supabase_jwt_ready,
            "google_client_secret_configured": google_client_secret_ready,
            "microsoft_client_secret_configured": microsoft_client_secret_ready,
            "secrets_hardening_ready": secrets_hardening_ready,
        },
        "checks": checks,
    }
