from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse


ROOT_DIR = Path(__file__).resolve().parent.parent


def _parse_env_file(path: Path) -> dict[str, str]:
    payload: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        payload[key.strip()] = value.strip()
    return payload


def _load_environment_examples() -> dict[str, dict[str, str]]:
    return {
        "frontend_dev": _parse_env_file(ROOT_DIR / ".env.example"),
        "frontend_staging": _parse_env_file(ROOT_DIR / ".env.staging.example"),
        "frontend_prod": _parse_env_file(ROOT_DIR / ".env.production.example"),
        "backend_dev": _parse_env_file(ROOT_DIR / "backend/.env.example"),
        "backend_staging": _parse_env_file(ROOT_DIR / "backend/.env.staging.example"),
        "backend_prod": _parse_env_file(ROOT_DIR / "backend/.env.production.example"),
    }


def _is_https_public_url(raw_url: str) -> bool:
    parsed = urlparse(str(raw_url).strip())
    hostname = (parsed.hostname or "").strip().lower()
    return parsed.scheme == "https" and bool(parsed.netloc) and hostname not in {"localhost", "127.0.0.1", "0.0.0.0"}


def test_platform_examples_expose_required_surface_area() -> None:
    examples = _load_environment_examples()

    required_frontend_keys = {
        "NEXT_PUBLIC_SUPABASE_URL",
        "NEXT_PUBLIC_SUPABASE_ANON_KEY",
        "NEXT_PUBLIC_API_BASE_URL",
        "NEXT_PUBLIC_DEBUG_MIDDLEWARE",
        "FRONTEND_PORT",
        "BACKEND_PORT",
        "REDIS_PORT",
    }
    required_backend_keys = {
        "APP_DEPLOY_ENV",
        "APP_RELEASE_CHANNEL",
        "APP_BUILD_VERSION",
        "APP_BUILD_SHA",
        "SUPABASE_URL",
        "SUPABASE_KEY",
        "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_ANON_KEY",
        "SUPABASE_JWT_SECRET",
        "GEMINI_API_KEY",
        "GEMINI_CLIENT_PROVIDER",
        "GEMINI_VERTEX_PROJECT",
        "GEMINI_VERTEX_LOCATION",
        "LLM_PROVIDER",
        "DEEPSEEK_API_KEY",
        "DEEPSEEK_BASE_URL",
        "DEEPSEEK_MODEL",
        "AI_MODEL_NAME",
        "NARRATIVE_FAST_MODEL_NAME",
        "NARRATIVE_STRICT_MODEL_NAME",
        "CELERY_BROKER_URL",
        "CELERY_RESULT_BACKEND",
        "BACKEND_PUBLIC_URL",
        "FRONTEND_APP_URL",
        "GOOGLE_DRIVE_CLIENT_ID",
        "GOOGLE_DRIVE_CLIENT_SECRET",
        "GOOGLE_DRIVE_WATCH_MODE",
        "GOOGLE_DRIVE_WEBHOOK_CALLBACK_URL",
        "MICROSOFT_ONEDRIVE_CLIENT_ID",
        "MICROSOFT_ONEDRIVE_CLIENT_SECRET",
        "MICROSOFT_ONEDRIVE_TENANT_ID",
        "MICROSOFT_ONEDRIVE_WATCH_MODE",
        "CONNECTOR_WATCHDOG_ENABLED",
        "CONNECTOR_POLL_INTERVAL_SECONDS",
        "RATE_LIMIT_ENABLED",
        "RATE_LIMIT_STORAGE_URL",
    }

    for env_name, payload in examples.items():
        required_keys = required_frontend_keys if env_name.startswith("frontend_") else required_backend_keys
        missing = sorted(key for key in required_keys if key not in payload or payload[key] == "")
        assert not missing, f"{env_name} carece de variables requeridas: {missing}"


def test_platform_examples_keep_frontend_backend_urls_in_sync() -> None:
    examples = _load_environment_examples()
    environment_pairs = {
        "dev": ("frontend_dev", "backend_dev"),
        "staging": ("frontend_staging", "backend_staging"),
        "prod": ("frontend_prod", "backend_prod"),
    }

    for env_name, (frontend_key, backend_key) in environment_pairs.items():
        frontend_payload = examples[frontend_key]
        backend_payload = examples[backend_key]

        assert frontend_payload["NEXT_PUBLIC_API_BASE_URL"].rstrip("/") == backend_payload["BACKEND_PUBLIC_URL"].rstrip("/"), (
            f"{env_name}: NEXT_PUBLIC_API_BASE_URL y BACKEND_PUBLIC_URL deben apuntar al mismo endpoint público"
        )
        assert urlparse(frontend_payload["NEXT_PUBLIC_API_BASE_URL"]).path in {"", "/"}, (
            f"{env_name}: la URL base pública no debe contener /api ni otro path"
        )
        assert backend_payload["FRONTEND_APP_URL"].strip(), f"{env_name}: FRONTEND_APP_URL no puede quedar vacío"


def test_examples_pin_deepseek_flash_as_the_active_analysis_model() -> None:
    examples = _load_environment_examples()

    for env_name in ("backend_dev", "backend_staging", "backend_prod"):
        payload = examples[env_name]
        assert payload["LLM_PROVIDER"] == "deepseek", f"{env_name}: DeepSeek debe ser el proveedor activo"
        assert payload["DEEPSEEK_BASE_URL"] == "https://api.deepseek.com"
        for key in ("DEEPSEEK_MODEL", "AI_MODEL_NAME", "NARRATIVE_FAST_MODEL_NAME", "NARRATIVE_STRICT_MODEL_NAME"):
            assert payload[key] == "DeepSeek-V4.1-flash", f"{env_name}: {key} debe usar DeepSeek-V4.1-flash"
        assert payload["GEMINI_API_KEY"], f"{env_name}: Gemini se conserva para embeddings"


def test_production_examples_use_app_domain_as_a_single_origin() -> None:
    examples = _load_environment_examples()
    frontend = examples["frontend_prod"]
    backend = examples["backend_prod"]
    app_domain = frontend["APP_DOMAIN"]

    assert app_domain
    for value in (
        frontend["NEXT_PUBLIC_API_BASE_URL"],
        backend["BACKEND_PUBLIC_URL"],
        backend["FRONTEND_APP_URL"],
    ):
        parsed = urlparse(value)
        assert parsed.scheme == "https"
        assert parsed.hostname == app_domain
        assert parsed.path in {"", "/"}, "Las URLs base no deben incluir /api"


def test_vps_compose_derives_domain_and_real_frontend_build_inputs() -> None:
    compose = (ROOT_DIR / "docker-compose.prod.yml").read_text(encoding="utf-8")
    caddyfile = (ROOT_DIR / "Caddyfile").read_text(encoding="utf-8")

    assert "{$APP_DOMAIN}" in caddyfile
    assert "NEXT_PUBLIC_SUPABASE_URL: ${NEXT_PUBLIC_SUPABASE_URL:?" in compose
    assert "NEXT_PUBLIC_SUPABASE_ANON_KEY: ${NEXT_PUBLIC_SUPABASE_ANON_KEY:?" in compose
    assert "NEXT_PUBLIC_API_BASE_URL: https://${APP_DOMAIN:?" in compose
    assert "BACKEND_PUBLIC_URL: https://${APP_DOMAIN:?" in compose
    assert "FRONTEND_APP_URL: https://${APP_DOMAIN:?" in compose
    assert "NEXT_PUBLIC_API_URL" not in compose
    assert "GEMINI_RPM_GOVERNOR" not in compose
    assert "gemini-3.7-flash" not in compose
    assert "livion.lat" not in compose + caddyfile


def test_staging_and_prod_examples_enforce_secure_operational_contract() -> None:
    examples = _load_environment_examples()

    for env_name in ("staging", "prod"):
        frontend_payload = examples[f"frontend_{env_name}"]
        backend_payload = examples[f"backend_{env_name}"]

        assert _is_https_public_url(frontend_payload["NEXT_PUBLIC_API_BASE_URL"]), (
            f"{env_name}: NEXT_PUBLIC_API_BASE_URL debe ser HTTPS público"
        )
        assert _is_https_public_url(backend_payload["BACKEND_PUBLIC_URL"]), (
            f"{env_name}: BACKEND_PUBLIC_URL debe ser HTTPS público"
        )
        assert _is_https_public_url(backend_payload["FRONTEND_APP_URL"]), (
            f"{env_name}: FRONTEND_APP_URL debe ser HTTPS público"
        )
        assert frontend_payload["NEXT_PUBLIC_DEBUG_MIDDLEWARE"] == "0", (
            f"{env_name}: NEXT_PUBLIC_DEBUG_MIDDLEWARE debe estar deshabilitado"
        )

        watchdog_enabled = backend_payload["CONNECTOR_WATCHDOG_ENABLED"].strip().lower() == "true"
        if watchdog_enabled and backend_payload["GOOGLE_DRIVE_WATCH_MODE"].strip().lower() == "webhook":
            assert _is_https_public_url(backend_payload["GOOGLE_DRIVE_WEBHOOK_CALLBACK_URL"]), (
                f"{env_name}: GOOGLE_DRIVE_WEBHOOK_CALLBACK_URL debe ser HTTPS público si el watchdog usa webhook"
            )
