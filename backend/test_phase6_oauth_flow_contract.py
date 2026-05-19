from urllib.parse import parse_qs, urlparse

from app.core.config import settings
from app.services.cloud_oauth import (
    build_frontend_oauth_redirect_url,
    build_oauth_authorization_url,
    get_default_connector_return_to,
    get_provider_from_route_slug,
)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run() -> None:
    original_google_client_id = settings.GOOGLE_DRIVE_CLIENT_ID
    original_google_client_secret = settings.GOOGLE_DRIVE_CLIENT_SECRET
    original_microsoft_client_id = settings.MICROSOFT_ONEDRIVE_CLIENT_ID
    original_microsoft_client_secret = settings.MICROSOFT_ONEDRIVE_CLIENT_SECRET
    original_backend_public_url = settings.BACKEND_PUBLIC_URL
    original_frontend_app_url = settings.FRONTEND_APP_URL

    settings.GOOGLE_DRIVE_CLIENT_ID = "google-client"
    settings.GOOGLE_DRIVE_CLIENT_SECRET = "google-secret"
    settings.MICROSOFT_ONEDRIVE_CLIENT_ID = "ms-client"
    settings.MICROSOFT_ONEDRIVE_CLIENT_SECRET = "ms-secret"
    settings.BACKEND_PUBLIC_URL = "http://localhost:8000"
    settings.FRONTEND_APP_URL = "http://localhost:3000"

    try:
        _assert(get_provider_from_route_slug("google") == "google_drive", "google route slug inválido")
        _assert(get_provider_from_route_slug("microsoft") == "onedrive", "microsoft route slug inválido")
        _assert(
            get_default_connector_return_to() == "http://localhost:3000/cargar-datos",
            "default return_to inválido",
        )

        google_url = build_oauth_authorization_url(
            "google_drive",
            state="state-123",
            code_verifier="verifier-abc",
        )
        google_query = parse_qs(urlparse(google_url).query)
        _assert("state-123" in google_query.get("state", []), "google state ausente")
        _assert("offline" in " ".join(google_query.get("access_type", [])), "google access_type ausente")
        _assert(
            google_query.get("redirect_uri", [""])[0] == "http://localhost:8000/api/v1/auth/google/callback",
            "google redirect_uri inválido",
        )
        _assert(bool(google_query.get("code_challenge")), "google code_challenge ausente")

        microsoft_url = build_oauth_authorization_url(
            "onedrive",
            state="state-456",
            code_verifier="verifier-def",
        )
        microsoft_query = parse_qs(urlparse(microsoft_url).query)
        _assert("state-456" in microsoft_query.get("state", []), "microsoft state ausente")
        _assert(
            microsoft_query.get("redirect_uri", [""])[0] == "http://localhost:8000/api/v1/auth/microsoft/callback",
            "microsoft redirect_uri inválido",
        )
        _assert(microsoft_query.get("response_mode", [""])[0] == "query", "microsoft response_mode inválido")
        _assert(bool(microsoft_query.get("code_challenge")), "microsoft code_challenge ausente")

        redirect_url = build_frontend_oauth_redirect_url(
            "google_drive",
            status="connected",
            message="Cuenta conectada",
            redirect_to="http://localhost:3000/cargar-datos",
        )
        redirect_query = parse_qs(urlparse(redirect_url).query)
        _assert(redirect_query.get("oauth_provider", [""])[0] == "google_drive", "oauth_provider inválido")
        _assert(redirect_query.get("oauth_status", [""])[0] == "connected", "oauth_status inválido")
    finally:
        settings.GOOGLE_DRIVE_CLIENT_ID = original_google_client_id
        settings.GOOGLE_DRIVE_CLIENT_SECRET = original_google_client_secret
        settings.MICROSOFT_ONEDRIVE_CLIENT_ID = original_microsoft_client_id
        settings.MICROSOFT_ONEDRIVE_CLIENT_SECRET = original_microsoft_client_secret
        settings.BACKEND_PUBLIC_URL = original_backend_public_url
        settings.FRONTEND_APP_URL = original_frontend_app_url

    print("OK: phase6 oauth contract")


if __name__ == "__main__":
    run()
