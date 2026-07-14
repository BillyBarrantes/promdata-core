"""GCP Secret Manager — fetch secrets with local env var fallback.

Usage:
    from app.core.secret_manager import get_secret

    api_key = get_secret("GEMINI_API_KEY")
"""
from __future__ import annotations

import os
from typing import Any

from app.core.config import settings
from app.core.structured_logging import emit_structured_log

_SECRET_MANAGER_AVAILABLE: bool | None = None


def _check_secret_manager_available() -> bool:
    global _SECRET_MANAGER_AVAILABLE
    if _SECRET_MANAGER_AVAILABLE is not None:
        return _SECRET_MANAGER_AVAILABLE
    try:
        from google.cloud import secretmanager
        _SECRET_MANAGER_AVAILABLE = True
        return True
    except ImportError:
        emit_structured_log(
            "secret_manager_unavailable",
            level="info",
            reason="google-cloud-secret-manager not installed",
        )
        _SECRET_MANAGER_AVAILABLE = False
        return False


def get_secret(secret_id: str, default: Any = None) -> Any:
    """Fetch a secret from GCP Secret Manager, falling back to env var.

    The secret must be named after the env var (e.g. 'GEMINI_API_KEY').
    Falls back to os.getenv(secret_id) if Secret Manager is unavailable.
    """
    env_val = os.getenv(secret_id)
    if not _check_secret_manager_available():
        return env_val if env_val is not None else default

    try:
        from google.cloud import secretmanager

        project = getattr(settings, "APP_GCP_PROJECT", "promdata-enterprise")
        client = secretmanager.SecretManagerServiceClient()
        name = f"projects/{project}/secrets/{secret_id}/versions/latest"
        response = client.access_secret_version(name=name)
        return response.payload.data.decode("utf-8")
    except Exception as exc:
        emit_structured_log(
            "secret_manager_fetch_failed",
            level="warning",
            secret_id=secret_id,
            error=str(exc)[:180],
        )
        return env_val if env_val is not None else default
