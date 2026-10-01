"""Regression tests for restricting OAuth return URLs to the app origin."""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.services.cloud_oauth import _sanitize_return_to_url


TRUSTED_FRONTEND = "https://app.promdata.test"
DEFAULT_RETURN_TO = f"{TRUSTED_FRONTEND}/cargar-datos"


def test_sanitize_return_to_accepts_configured_frontend_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "FRONTEND_APP_URL", TRUSTED_FRONTEND)

    target = f"{TRUSTED_FRONTEND}/cargar-datos?source=drive"

    assert _sanitize_return_to_url(target) == target


@pytest.mark.parametrize(
    "candidate",
    [
        "https://attacker.example/phishing",
        "https://app.promdata.test.attacker.example/phishing",
        "https://app.promdata.test@attacker.example/phishing",
        "https://app.promdata.test:invalid/phishing",
        "https://app.promdata.test:0/phishing",
        "https://[malformed/phishing",
        "https://app.promdata.test/path\r\nLocation: https://attacker.example",
    ],
)
def test_sanitize_return_to_rejects_untrusted_origins(
    monkeypatch: pytest.MonkeyPatch,
    candidate: str,
) -> None:
    monkeypatch.setattr(settings, "FRONTEND_APP_URL", TRUSTED_FRONTEND)

    assert _sanitize_return_to_url(candidate) == DEFAULT_RETURN_TO


def test_sanitize_return_to_uses_configured_default_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "FRONTEND_APP_URL", TRUSTED_FRONTEND)

    assert _sanitize_return_to_url(None) == DEFAULT_RETURN_TO
