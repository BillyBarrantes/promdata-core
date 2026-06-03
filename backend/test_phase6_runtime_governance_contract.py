from __future__ import annotations

from app.core.config import settings
from app.services.runtime_governance import get_runtime_governance_payload


def _set_runtime(monkeypatch, **overrides) -> None:
    defaults = {
        "APP_DEPLOY_ENV": "development",
        "APP_RELEASE_CHANNEL": "local",
        "APP_BUILD_VERSION": "0.1.0-local",
        "APP_BUILD_SHA": "local",
        "SUPABASE_URL": "https://tenant.supabase.co",
        "SUPABASE_KEY": "service-role-key",
        "SUPABASE_SERVICE_ROLE_KEY": "service-role-key",
        "SUPABASE_ANON_KEY": "anon-key",
        "SUPABASE_JWT_SECRET": "jwt-secret",
        "GEMINI_API_KEY": "gemini-key",
        "GEMINI_VERTEX_PROJECT": "promdata-enterprise",
        "CELERY_BROKER_URL": "redis://redis:6379/0",
        "CELERY_RESULT_BACKEND": "redis://redis:6379/0",
        "BACKEND_PUBLIC_URL": "http://localhost:8000",
        "FRONTEND_APP_URL": "http://localhost:3000",
        "RATE_LIMIT_ENABLED": True,
        "RATE_LIMIT_STORAGE_URL": "redis://redis:6379/0",
        "CONNECTOR_WATCHDOG_ENABLED": "false",
        "GOOGLE_DRIVE_CLIENT_ID": "google-client-id",
        "GOOGLE_DRIVE_CLIENT_SECRET": "google-client-secret",
        "GOOGLE_DRIVE_WATCH_MODE": "webhook",
        "GOOGLE_DRIVE_WEBHOOK_CALLBACK_URL": "http://localhost:8000/api/v1/connectors/google-drive/webhook",
        "MICROSOFT_ONEDRIVE_CLIENT_ID": "ms-client-id",
        "MICROSOFT_ONEDRIVE_CLIENT_SECRET": "ms-client-secret",
    }
    defaults.update(overrides)

    for key, value in defaults.items():
        monkeypatch.setattr(settings, key, value)


def test_runtime_governance_accepts_local_development_profile(monkeypatch) -> None:
    _set_runtime(monkeypatch)

    payload = get_runtime_governance_payload()

    assert payload["environment_profile"] == "development"
    assert payload["overall_status"] == "healthy"
    assert payload["hardening_ready"] is False
    assert payload["release"]["rollback_ready"] is False
    assert payload["checks"]["secrets"]["status"] == "healthy"
    assert payload["secrets"]["secrets_hardening_ready"] is True


def test_runtime_governance_reports_hardened_production_profile(monkeypatch) -> None:
    _set_runtime(
        monkeypatch,
        APP_DEPLOY_ENV="production",
        APP_RELEASE_CHANNEL="stable",
        APP_BUILD_VERSION="1.4.2",
        APP_BUILD_SHA="a1b2c3d4",
        BACKEND_PUBLIC_URL="https://api.promdata.com",
        FRONTEND_APP_URL="https://app.promdata.com",
        CONNECTOR_WATCHDOG_ENABLED="true",
        GOOGLE_DRIVE_WEBHOOK_CALLBACK_URL="https://api.promdata.com/api/v1/connectors/google-drive/webhook",
    )

    payload = get_runtime_governance_payload()

    assert payload["environment_profile"] == "production"
    assert payload["overall_status"] == "healthy"
    assert payload["hardening_ready"] is True
    assert payload["release"]["rollback_ready"] is True
    assert payload["checks"]["secrets"]["status"] == "healthy"
    assert payload["secrets"]["secrets_hardening_ready"] is True


def test_runtime_governance_flags_insecure_production_webhook(monkeypatch) -> None:
    _set_runtime(
        monkeypatch,
        APP_DEPLOY_ENV="production",
        APP_RELEASE_CHANNEL="stable",
        APP_BUILD_VERSION="1.4.2",
        APP_BUILD_SHA="a1b2c3d4",
        BACKEND_PUBLIC_URL="https://api.promdata.com",
        FRONTEND_APP_URL="https://app.promdata.com",
        CONNECTOR_WATCHDOG_ENABLED="true",
        GOOGLE_DRIVE_WEBHOOK_CALLBACK_URL="http://localhost:8000/api/v1/connectors/google-drive/webhook",
    )

    payload = get_runtime_governance_payload()

    assert payload["environment_profile"] == "production"
    assert payload["overall_status"] == "warning"
    assert payload["hardening_ready"] is False
    assert "google_webhook_not_public" in payload["warnings"]


def test_runtime_governance_allows_dev_without_supabase_jwt_secret(monkeypatch) -> None:
    _set_runtime(monkeypatch, SUPABASE_JWT_SECRET="")

    payload = get_runtime_governance_payload()

    assert payload["environment_profile"] == "development"
    assert payload["overall_status"] == "healthy"
    assert payload["checks"]["supabase"]["status"] == "healthy"
    assert payload["checks"]["supabase"]["jwt_secret_configured"] is False


def test_runtime_governance_warns_when_prod_lacks_supabase_jwt_secret(monkeypatch) -> None:
    _set_runtime(
        monkeypatch,
        APP_DEPLOY_ENV="production",
        APP_RELEASE_CHANNEL="stable",
        APP_BUILD_VERSION="1.4.2",
        APP_BUILD_SHA="a1b2c3d4",
        BACKEND_PUBLIC_URL="https://api.promdata.com",
        FRONTEND_APP_URL="https://app.promdata.com",
        CONNECTOR_WATCHDOG_ENABLED="true",
        GOOGLE_DRIVE_WEBHOOK_CALLBACK_URL="https://api.promdata.com/api/v1/connectors/google-drive/webhook",
        SUPABASE_JWT_SECRET="",
    )

    payload = get_runtime_governance_payload()

    assert payload["environment_profile"] == "production"
    assert payload["overall_status"] == "warning"
    assert payload["checks"]["supabase"]["status"] == "warning"
    assert "supabase_jwt_secret_missing" in payload["warnings"]
    assert payload["checks"]["secrets"]["status"] == "warning"
    assert payload["secrets"]["secrets_hardening_ready"] is False


def test_runtime_governance_warns_when_prod_lacks_release_metadata(monkeypatch) -> None:
    _set_runtime(
        monkeypatch,
        APP_DEPLOY_ENV="production",
        APP_RELEASE_CHANNEL="stable",
        APP_BUILD_VERSION="0.1.0",
        APP_BUILD_SHA="local",
        BACKEND_PUBLIC_URL="https://api.promdata.com",
        FRONTEND_APP_URL="https://app.promdata.com",
        CONNECTOR_WATCHDOG_ENABLED="true",
        GOOGLE_DRIVE_WEBHOOK_CALLBACK_URL="https://api.promdata.com/api/v1/connectors/google-drive/webhook",
    )

    payload = get_runtime_governance_payload()

    assert payload["environment_profile"] == "production"
    assert payload["overall_status"] == "warning"
    assert payload["hardening_ready"] is False
    assert payload["checks"]["release"]["status"] == "warning"
    assert payload["release"]["rollback_ready"] is False
    assert "release_metadata_incomplete" in payload["warnings"]


def test_runtime_governance_warns_when_prod_relies_on_implicit_service_role(monkeypatch) -> None:
    _set_runtime(
        monkeypatch,
        APP_DEPLOY_ENV="production",
        APP_RELEASE_CHANNEL="stable",
        APP_BUILD_VERSION="1.4.2",
        APP_BUILD_SHA="a1b2c3d4",
        BACKEND_PUBLIC_URL="https://api.promdata.com",
        FRONTEND_APP_URL="https://app.promdata.com",
        SUPABASE_SERVICE_ROLE_KEY="",
        CONNECTOR_WATCHDOG_ENABLED="true",
        GOOGLE_DRIVE_WEBHOOK_CALLBACK_URL="https://api.promdata.com/api/v1/connectors/google-drive/webhook",
    )

    payload = get_runtime_governance_payload()

    assert payload["environment_profile"] == "production"
    assert payload["overall_status"] == "warning"
    assert payload["checks"]["secrets"]["status"] == "warning"
    assert payload["secrets"]["secrets_hardening_ready"] is False
    assert "supabase_service_role_not_explicit" in payload["warnings"]
