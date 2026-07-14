import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from dotenv import load_dotenv


_BACKEND_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
_LOCAL_REDIS_URL = "redis://127.0.0.1:6379/0"
_DOCKER_REDIS_URL = "redis://redis:6379/0"

load_dotenv(dotenv_path=_BACKEND_ENV_PATH, override=False)


def _env_bool(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def _is_running_in_container() -> bool:
    return Path("/.dockerenv").exists()


def _normalize_redis_url_for_runtime(url: str) -> str:
    raw = str(url or "").strip()
    if not raw:
        return _LOCAL_REDIS_URL

    if not _is_running_in_container():
        return raw

    try:
        parsed = urlsplit(raw)
    except Exception:
        return raw

    if parsed.scheme not in {"redis", "rediss"}:
        return raw

    host = (parsed.hostname or "").strip().lower()
    if host not in {"127.0.0.1", "localhost"}:
        return raw

    docker_target = urlsplit(_DOCKER_REDIS_URL)
    port = parsed.port or docker_target.port
    auth = ""
    if parsed.username:
        auth = parsed.username
        if parsed.password:
            auth = f"{auth}:{parsed.password}"
        auth = f"{auth}@"

    new_netloc = f"{auth}{docker_target.hostname}:{port}" if port else f"{auth}{docker_target.hostname}"
    normalized = parsed._replace(netloc=new_netloc)
    return urlunsplit(normalized)


class Settings:
    APP_DEPLOY_ENV: str = os.getenv("APP_DEPLOY_ENV", "development")
    APP_RELEASE_CHANNEL: str = os.getenv("APP_RELEASE_CHANNEL", "local")
    APP_BUILD_VERSION: str = os.getenv("APP_BUILD_VERSION", "0.1.0-local")
    APP_BUILD_SHA: str = os.getenv("APP_BUILD_SHA", "local")
    SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
    SUPABASE_KEY: str = os.getenv("SUPABASE_KEY", "")
    SUPABASE_SERVICE_ROLE_KEY: str = os.getenv("SUPABASE_SERVICE_ROLE_KEY", os.getenv("SUPABASE_KEY", ""))
    SUPABASE_ANON_KEY: str = os.getenv("SUPABASE_ANON_KEY", "")
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_CLIENT_PROVIDER: str = (os.getenv("GEMINI_CLIENT_PROVIDER", "genai") or "genai").strip().lower()
    GEMINI_VERTEX_PROJECT: str = os.getenv("GEMINI_VERTEX_PROJECT", "promdata-enterprise")
    GEMINI_VERTEX_LOCATION: str = os.getenv("GEMINI_VERTEX_LOCATION", "global")
    CELERY_BROKER_URL: str = _normalize_redis_url_for_runtime(
        os.getenv("CELERY_BROKER_URL", _LOCAL_REDIS_URL)
    )
    SUPABASE_JWT_SECRET: str = os.getenv("SUPABASE_JWT_SECRET", "")
    CELERY_RESULT_BACKEND: str = _normalize_redis_url_for_runtime(
        os.getenv("CELERY_RESULT_BACKEND", _LOCAL_REDIS_URL)
    )
    AI_MODEL_NAME= os.getenv("AI_MODEL_NAME", "gemini-3.5-flash")
    BACKEND_PUBLIC_URL: str = os.getenv("BACKEND_PUBLIC_URL", "http://localhost:8000")
    FRONTEND_APP_URL: str = os.getenv("FRONTEND_APP_URL", "http://localhost:3000")
    OAUTH_STATE_TTL_SECONDS: int = int(os.getenv("OAUTH_STATE_TTL_SECONDS", "900"))
    OAUTH_TOKEN_REFRESH_SKEW_SECONDS: int = int(os.getenv("OAUTH_TOKEN_REFRESH_SKEW_SECONDS", "300"))
    OAUTH_TOKEN_ENCRYPTION_KEY: str = os.getenv("OAUTH_TOKEN_ENCRYPTION_KEY", "")
    KNOWLEDGE_DOCUMENTS_BUCKET: str = os.getenv("KNOWLEDGE_DOCUMENTS_BUCKET", "knowledge-documents")
    KNOWLEDGE_EMBEDDING_DIMENSIONS: int = int(os.getenv("KNOWLEDGE_EMBEDDING_DIMENSIONS", "768"))
    KNOWLEDGE_MAX_CHUNK_CHARS: int = int(os.getenv("KNOWLEDGE_MAX_CHUNK_CHARS", "1400"))
    KNOWLEDGE_CHUNK_OVERLAP_CHARS: int = int(os.getenv("KNOWLEDGE_CHUNK_OVERLAP_CHARS", "220"))
    KNOWLEDGE_DEFAULT_TOP_K: int = int(os.getenv("KNOWLEDGE_DEFAULT_TOP_K", "4"))
    KNOWLEDGE_FALLBACK_SCAN_LIMIT: int = int(os.getenv("KNOWLEDGE_FALLBACK_SCAN_LIMIT", "250"))
    GOOGLE_DRIVE_CLIENT_ID: str = os.getenv("GOOGLE_DRIVE_CLIENT_ID", "")
    GOOGLE_DRIVE_CLIENT_SECRET: str = os.getenv("GOOGLE_DRIVE_CLIENT_SECRET", "")
    GOOGLE_DRIVE_SCOPES: str = os.getenv(
        "GOOGLE_DRIVE_SCOPES",
        "openid email profile https://www.googleapis.com/auth/drive.readonly"
    )
    GOOGLE_DRIVE_WATCH_MODE: str = os.getenv("GOOGLE_DRIVE_WATCH_MODE", "webhook")
    GOOGLE_DRIVE_WEBHOOK_CALLBACK_URL: str = os.getenv("GOOGLE_DRIVE_WEBHOOK_CALLBACK_URL", "")
    GOOGLE_DRIVE_WEBHOOK_EXPIRATION_SECONDS: int = int(os.getenv("GOOGLE_DRIVE_WEBHOOK_EXPIRATION_SECONDS", "604800"))
    GOOGLE_DRIVE_WEBHOOK_RENEWAL_SKEW_SECONDS: int = int(os.getenv("GOOGLE_DRIVE_WEBHOOK_RENEWAL_SKEW_SECONDS", "3600"))
    MICROSOFT_ONEDRIVE_CLIENT_ID: str = os.getenv("MICROSOFT_ONEDRIVE_CLIENT_ID", "")
    MICROSOFT_ONEDRIVE_CLIENT_SECRET: str = os.getenv("MICROSOFT_ONEDRIVE_CLIENT_SECRET", "")
    MICROSOFT_ONEDRIVE_SCOPES: str = os.getenv(
        "MICROSOFT_ONEDRIVE_SCOPES",
        "offline_access openid profile email Files.Read User.Read"
    )
    MICROSOFT_ONEDRIVE_TENANT_ID: str = os.getenv("MICROSOFT_ONEDRIVE_TENANT_ID", "common")
    MICROSOFT_ONEDRIVE_WATCH_MODE: str = os.getenv("MICROSOFT_ONEDRIVE_WATCH_MODE", "polling")
    CONNECTOR_WATCHDOG_ENABLED: str = os.getenv("CONNECTOR_WATCHDOG_ENABLED", "false")
    CONNECTOR_AUTO_SYNC_ENABLED: bool = _env_bool("CONNECTOR_AUTO_SYNC_ENABLED", True)
    CONNECTOR_POLL_INTERVAL_SECONDS: int = int(os.getenv("CONNECTOR_POLL_INTERVAL_SECONDS", "300"))
    RATE_LIMIT_ENABLED: bool = _env_bool("RATE_LIMIT_ENABLED", True)
    RATE_LIMIT_STORAGE_URL: str = _normalize_redis_url_for_runtime(
        os.getenv(
            "RATE_LIMIT_STORAGE_URL",
            os.getenv("CELERY_BROKER_URL", _LOCAL_REDIS_URL),
        )
    )
    RATE_LIMIT_WINDOW_SECONDS: int = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))
    RATE_LIMIT_ANALYZE_LIMIT: int = int(os.getenv("RATE_LIMIT_ANALYZE_LIMIT", "8"))
    RATE_LIMIT_BURST_WINDOW_SECONDS: int = int(os.getenv("RATE_LIMIT_BURST_WINDOW_SECONDS", "5"))
    RATE_LIMIT_BURST_ANALYZE_LIMIT: int = int(os.getenv("RATE_LIMIT_BURST_ANALYZE_LIMIT", "2"))
    CONCURRENT_TASKS_PER_USER: int = int(os.getenv("CONCURRENT_TASKS_PER_USER", "2"))
    CONCURRENT_TASKS_TTL_SECONDS: int = int(os.getenv("CONCURRENT_TASKS_TTL_SECONDS", "3600"))
    RATE_LIMIT_CHAT_LIMIT: int = int(os.getenv("RATE_LIMIT_CHAT_LIMIT", "30"))
    RATE_LIMIT_KNOWLEDGE_ASK_LIMIT: int = int(os.getenv("RATE_LIMIT_KNOWLEDGE_ASK_LIMIT", "20"))
    RATE_LIMIT_TEAM_CACHE_TTL_SECONDS: int = int(os.getenv("RATE_LIMIT_TEAM_CACHE_TTL_SECONDS", "300"))
    REDIS_MAX_CONNECTIONS_RATE_LIMIT: int = int(os.getenv("REDIS_MAX_CONNECTIONS_RATE_LIMIT", "6"))
    REDIS_MAX_CONNECTIONS_AI_CACHE: int = int(os.getenv("REDIS_MAX_CONNECTIONS_AI_CACHE", "6"))
    REDIS_MAX_CONNECTIONS_HEALTHCHECK: int = int(os.getenv("REDIS_MAX_CONNECTIONS_HEALTHCHECK", "2"))
    REDIS_MAX_CONNECTIONS_DEFAULT: int = int(os.getenv("REDIS_MAX_CONNECTIONS_DEFAULT", "3"))
    REDIS_MAX_CONNECTIONS_PUBSUB: int = int(os.getenv("REDIS_MAX_CONNECTIONS_PUBSUB", "2"))
    TASK_PROGRESS_CHANNEL_PREFIX: str = os.getenv("TASK_PROGRESS_CHANNEL_PREFIX", "task_progress")
    CELERY_BROKER_POOL_LIMIT: int = int(os.getenv("CELERY_BROKER_POOL_LIMIT", "5"))
    CELERY_RESULT_BACKEND_MAX_CONNECTIONS: int = int(
        os.getenv("CELERY_RESULT_BACKEND_MAX_CONNECTIONS", "5")
    )
    CELERY_TASK_SOFT_TIME_LIMIT: int = int(os.getenv("CELERY_TASK_SOFT_TIME_LIMIT", "300"))
    CELERY_TASK_HARD_TIME_LIMIT: int = int(os.getenv("CELERY_TASK_HARD_TIME_LIMIT", "330"))
    CELERY_TASK_HEAVY_SOFT_TIME_LIMIT: int = int(os.getenv("CELERY_TASK_HEAVY_SOFT_TIME_LIMIT", "300"))
    CELERY_TASK_HEAVY_HARD_TIME_LIMIT: int = int(os.getenv("CELERY_TASK_HEAVY_HARD_TIME_LIMIT", "360"))
    CELERY_QUEUE_DEFAULT: str = os.getenv("CELERY_QUEUE_DEFAULT", "default")
    CELERY_QUEUE_ANALYSIS: str = os.getenv("CELERY_QUEUE_ANALYSIS", "analysis")
    CELERY_QUEUE_BACKGROUND: str = os.getenv("CELERY_QUEUE_BACKGROUND", "background")
    GEMINI_CIRCUIT_BREAKER_ENABLED: bool = _env_bool("GEMINI_CIRCUIT_BREAKER_ENABLED", True)
    GEMINI_CIRCUIT_FAILURE_THRESHOLD: int = int(os.getenv("GEMINI_CIRCUIT_FAILURE_THRESHOLD", "15"))
    GEMINI_CIRCUIT_RECOVERY_TIMEOUT_SECONDS: int = int(os.getenv("GEMINI_CIRCUIT_RECOVERY_TIMEOUT_SECONDS", "45"))
    GEMINI_CIRCUIT_HALF_OPEN_MAX_CALLS: int = int(os.getenv("GEMINI_CIRCUIT_HALF_OPEN_MAX_CALLS", "2"))
    GEMINI_RETRY_MAX_RETRIES: int = int(os.getenv("GEMINI_RETRY_MAX_RETRIES", "4"))
    GEMINI_RETRY_BASE_DELAY_SECONDS: float = float(os.getenv("GEMINI_RETRY_BASE_DELAY_SECONDS", "1.0"))
    GEMINI_RETRY_MAX_DELAY_SECONDS: float = float(os.getenv("GEMINI_RETRY_MAX_DELAY_SECONDS", "15.0"))
    GEMINI_RETRY_JITTER_SECONDS: float = float(os.getenv("GEMINI_RETRY_JITTER_SECONDS", "0.5"))
    AI_RESPONSE_CACHE_TTL_SECONDS: int = int(os.getenv("AI_RESPONSE_CACHE_TTL_SECONDS", "1800"))
    SEMANTIC_TRANSLATOR_CACHE_TTL_SECONDS: int = int(
        os.getenv("SEMANTIC_TRANSLATOR_CACHE_TTL_SECONDS", str(AI_RESPONSE_CACHE_TTL_SECONDS))
    )
    NARRATIVE_CACHE_TTL_SECONDS: int = int(
        os.getenv("NARRATIVE_CACHE_TTL_SECONDS", str(AI_RESPONSE_CACHE_TTL_SECONDS))
    )
    DETERMINISTIC_VISUAL_FASTPATH_ENABLED: bool = _env_bool("DETERMINISTIC_VISUAL_FASTPATH_ENABLED", True)
    FILE_INTELLIGENCE_ROUTER_ENABLED: bool = _env_bool("FILE_INTELLIGENCE_ROUTER_ENABLED", False)
    FILE_INTELLIGENCE_STRICT_SIGNATURES: bool = _env_bool("FILE_INTELLIGENCE_STRICT_SIGNATURES", True)
    CANONICAL_EXTRACTION_PIPELINE_ENABLED: bool = _env_bool("CANONICAL_EXTRACTION_PIPELINE_ENABLED", False)
    FILE_INTELLIGENCE_ENABLE_PDF_TABLE_EXTRACTION: bool = _env_bool(
        "FILE_INTELLIGENCE_ENABLE_PDF_TABLE_EXTRACTION",
        False,
    )
    FILE_INTELLIGENCE_ENABLE_IMAGE_OCR: bool = _env_bool(
        "FILE_INTELLIGENCE_ENABLE_IMAGE_OCR",
        False,
    )
    CANONICAL_NATIVE_TABULAR_EXTRACTION_ENABLED: bool = _env_bool(
        "CANONICAL_NATIVE_TABULAR_EXTRACTION_ENABLED",
        False,
    )
    CANONICAL_NATIVE_TABULAR_MAX_ROWS: int = int(
        os.getenv("CANONICAL_NATIVE_TABULAR_MAX_ROWS", "5000")
    )
    CANONICAL_NATIVE_TABULAR_MAX_COLUMNS: int = int(
        os.getenv("CANONICAL_NATIVE_TABULAR_MAX_COLUMNS", "200")
    )
    CANONICAL_NATIVE_TABULAR_ANALYTICS_MAX_ROWS: int = int(
        os.getenv("CANONICAL_NATIVE_TABULAR_ANALYTICS_MAX_ROWS", "250000")
    )
    CANONICAL_NATIVE_TABULAR_ANALYTICS_MAX_COLUMNS: int = int(
        os.getenv("CANONICAL_NATIVE_TABULAR_ANALYTICS_MAX_COLUMNS", "512")
    )
    CANONICAL_NATIVE_TABULAR_MAX_FRAMES: int = int(
        os.getenv("CANONICAL_NATIVE_TABULAR_MAX_FRAMES", "12")
    )
    CANONICAL_DOCUMENT_TABLE_QUALITY_GATE_ENABLED: bool = _env_bool(
        "CANONICAL_DOCUMENT_TABLE_QUALITY_GATE_ENABLED",
        False,
    )
    CANONICAL_DOCUMENT_TABLE_QUALITY_GATE_MIN_SCORE: float = float(
        os.getenv("CANONICAL_DOCUMENT_TABLE_QUALITY_GATE_MIN_SCORE", "0.68")
    )
    CANONICAL_DOCUMENT_TABLE_QUALITY_GATE_MIN_ROWS: int = int(
        os.getenv("CANONICAL_DOCUMENT_TABLE_QUALITY_GATE_MIN_ROWS", "1")
    )
    CANONICAL_DOCUMENT_TABLE_QUALITY_GATE_MIN_COLUMNS: int = int(
        os.getenv("CANONICAL_DOCUMENT_TABLE_QUALITY_GATE_MIN_COLUMNS", "2")
    )
    CANONICAL_IBIS_PREVIEW_RUNTIME_ENABLED: bool = _env_bool(
        "CANONICAL_IBIS_PREVIEW_RUNTIME_ENABLED",
        False,
    )
    CANONICAL_ANALYTICAL_CONTRACT_ADAPTER_ENABLED: bool = _env_bool(
        "CANONICAL_ANALYTICAL_CONTRACT_ADAPTER_ENABLED",
        False,
    )
    CANONICAL_SHADOW_METRIC_VALIDITY_GATE_ENABLED: bool = _env_bool(
        "CANONICAL_SHADOW_METRIC_VALIDITY_GATE_ENABLED",
        False,
    )
    CANONICAL_SHADOW_METRIC_VALIDITY_MIN_PARSEABLE_RATIO: float = float(
        os.getenv("CANONICAL_SHADOW_METRIC_VALIDITY_MIN_PARSEABLE_RATIO", "0.7")
    )
    CANONICAL_SHADOW_METRIC_PROMOTION_MIN_PARSEABLE_RATIO: float = float(
        os.getenv("CANONICAL_SHADOW_METRIC_PROMOTION_MIN_PARSEABLE_RATIO", "0.85")
    )
    CANONICAL_DARK_RUNTIME_ORCHESTRATOR_ENABLED: bool = _env_bool(
        "CANONICAL_DARK_RUNTIME_ORCHESTRATOR_ENABLED",
        False,
    )
    CANONICAL_SHADOW_QUERY_RUNTIME_ENABLED: bool = _env_bool(
        "CANONICAL_SHADOW_QUERY_RUNTIME_ENABLED",
        False,
    )
    CANONICAL_SHADOW_TRAFFIC_MIRROR_ENABLED: bool = _env_bool(
        "CANONICAL_SHADOW_TRAFFIC_MIRROR_ENABLED",
        False,
    )
    CANONICAL_SHADOW_TRAFFIC_MIRROR_TABULAR_ONLY: bool = _env_bool(
        "CANONICAL_SHADOW_TRAFFIC_MIRROR_TABULAR_ONLY",
        True,
    )
    CANONICAL_SHADOW_TRAFFIC_MIRROR_MAX_PLANS: int = int(
        os.getenv("CANONICAL_SHADOW_TRAFFIC_MIRROR_MAX_PLANS", "3")
    )
    CANONICAL_TABULAR_CANARY_ROUTER_ENABLED: bool = _env_bool(
        "CANONICAL_TABULAR_CANARY_ROUTER_ENABLED",
        False,
    )
    CANONICAL_TABULAR_CANARY_FUNCTIONAL_SWITCH_ENABLED: bool = _env_bool(
        "CANONICAL_TABULAR_CANARY_FUNCTIONAL_SWITCH_ENABLED",
        False,
    )
    CANONICAL_TABULAR_CANARY_FAIL_OPEN_ENABLED: bool = _env_bool(
        "CANONICAL_TABULAR_CANARY_FAIL_OPEN_ENABLED",
        True,
    )
    CANONICAL_TABULAR_CANARY_TRAFFIC_PERCENT: int = int(
        os.getenv("CANONICAL_TABULAR_CANARY_TRAFFIC_PERCENT", "0")
    )
    CANONICAL_TABULAR_CANARY_ALLOWLIST_TEAM_IDS: str = os.getenv(
        "CANONICAL_TABULAR_CANARY_ALLOWLIST_TEAM_IDS",
        "",
    )
    CANONICAL_TABULAR_CANARY_ALLOWLIST_USER_IDS: str = os.getenv(
        "CANONICAL_TABULAR_CANARY_ALLOWLIST_USER_IDS",
        "",
    )
    CANONICAL_TABULAR_CANARY_ALLOWLIST_FILE_IDS: str = os.getenv(
        "CANONICAL_TABULAR_CANARY_ALLOWLIST_FILE_IDS",
        "",
    )
    CANONICAL_TABULAR_CANARY_BUCKET_SALT: str = os.getenv(
        "CANONICAL_TABULAR_CANARY_BUCKET_SALT",
        "tabular-v1",
    )
    CANONICAL_TABULAR_CANARY_REQUIRE_SHADOW_EVIDENCE: bool = _env_bool(
        "CANONICAL_TABULAR_CANARY_REQUIRE_SHADOW_EVIDENCE",
        True,
    )
    CANONICAL_TABULAR_CANARY_MIN_OBSERVED_TASKS: int = int(
        os.getenv("CANONICAL_TABULAR_CANARY_MIN_OBSERVED_TASKS", "12")
    )
    CANONICAL_TABULAR_CANARY_MIN_ALIGNMENT_RATE: float = float(
        os.getenv("CANONICAL_TABULAR_CANARY_MIN_ALIGNMENT_RATE", "0.98")
    )
    CANONICAL_TABULAR_CANARY_MAX_DIVERGENCE_SCORE: float = float(
        os.getenv("CANONICAL_TABULAR_CANARY_MAX_DIVERGENCE_SCORE", "0.02")
    )
    CANONICAL_TABULAR_CANARY_SHADOW_REPORT_PATH: str = os.getenv(
        "CANONICAL_TABULAR_CANARY_SHADOW_REPORT_PATH",
        str(Path(__file__).resolve().parents[2] / ".shadow_runtime_report.json"),
    )
    CANONICAL_TABULAR_CANARY_FALLBACK_REPORT_PATH: str = os.getenv(
        "CANONICAL_TABULAR_CANARY_FALLBACK_REPORT_PATH",
        "/tmp/promdata_real_shadow_traffic_report.json",
    )
    UNIVERSAL_TABULAR_PRODUCTION_EXECUTOR_ENABLED: bool = _env_bool(
        "UNIVERSAL_TABULAR_PRODUCTION_EXECUTOR_ENABLED",
        False,
    )
    SENTRY_DSN: str = os.getenv("SENTRY_DSN", "")
    LANGFUSE_SECRET_KEY: str = os.getenv("LANGFUSE_SECRET_KEY", "")
    LANGFUSE_PUBLIC_KEY: str = os.getenv("LANGFUSE_PUBLIC_KEY", "")
    LANGFUSE_HOST: str = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")
    NARRATIVE_FAST_MODEL_NAME: str = os.getenv("NARRATIVE_FAST_MODEL_NAME", "gemini-3.5-flash")
    NARRATIVE_STRICT_MODEL_NAME: str = os.getenv("NARRATIVE_STRICT_MODEL_NAME", "gemini-3.1-pro-preview")
    UNIVERSAL_TABULAR_RESULT_SOFT_LIMIT_BYTES: int = int(
        os.getenv("UNIVERSAL_TABULAR_RESULT_SOFT_LIMIT_BYTES", "1500000")
    )
    # [FIX 2026-06-08] Supabase HTTP client timeouts. Supabase plan free tiene un
    # Disk IO budget limitado — cuando se agota, el PostgREST edge puede tardar
    # 10+ segundos en responder. Estos timeouts fail-fast evitan que nuestro
    # backend se cuelgue y retornen 500 genéricos en vez de 503 informativos.
    SUPABASE_CONNECT_TIMEOUT_SECONDS: float = float(
        os.getenv("SUPABASE_CONNECT_TIMEOUT_SECONDS", "3.0")
    )
    SUPABASE_READ_TIMEOUT_SECONDS: float = float(
        os.getenv("SUPABASE_READ_TIMEOUT_SECONDS", "8.0")
    )
    SUPABASE_WRITE_TIMEOUT_SECONDS: float = float(
        os.getenv("SUPABASE_WRITE_TIMEOUT_SECONDS", "5.0")
    )
    SUPABASE_POOL_TIMEOUT_SECONDS: float = float(
        os.getenv("SUPABASE_POOL_TIMEOUT_SECONDS", "3.0")
    )

    # ------------------------------------------------------------------ #
    # Fase 3.1: Audit logging
    # ------------------------------------------------------------------ #
    AUDIT_LOG_ENABLED: bool = _env_bool("AUDIT_LOG_ENABLED", True)
    AUDIT_LOG_BODY_MAX_BYTES: int = int(os.getenv("AUDIT_LOG_BODY_MAX_BYTES", "1024"))
    AUDIT_LOG_SKIP_PATHS: str = os.getenv(
        "AUDIT_LOG_SKIP_PATHS", "/health,/health/live,/health/ready,/health/observability"
    )

    # ------------------------------------------------------------------ #
    # Fase 3.5: Slack alerting
    # ------------------------------------------------------------------ #
    SLACK_ALERT_WEBHOOK_URL: str = os.getenv("SLACK_ALERT_WEBHOOK_URL", "")

settings = Settings()
