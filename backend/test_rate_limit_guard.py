from fastapi import Depends, FastAPI, Request
from fastapi.security import OAuth2PasswordBearer
from fastapi.testclient import TestClient
from jose import jwt

from app.core.config import settings
from app.core.rate_limit import _reset_rate_limit_state_for_tests, enforce_rate_limit


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")


def _build_app(limit: int, window_seconds: int) -> FastAPI:
    app = FastAPI()

    @app.post("/limited")
    def limited(request: Request, token: str = Depends(oauth2_scheme)):
        enforce_rate_limit(
            request=request,
            token=token,
            scope="test_scope",
            limit=limit,
            window_seconds=window_seconds,
        )
        return {"ok": True}

    return app


def _make_token(user_id: str) -> str:
    return jwt.encode({"sub": user_id}, "rate-limit-test-secret", algorithm="HS256")


def test_rate_limit_blocks_after_threshold() -> None:
    original_enabled = settings.RATE_LIMIT_ENABLED
    original_storage = settings.RATE_LIMIT_STORAGE_URL
    settings.RATE_LIMIT_ENABLED = True
    settings.RATE_LIMIT_STORAGE_URL = ""
    _reset_rate_limit_state_for_tests()

    app = _build_app(limit=2, window_seconds=60)
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {_make_token('user-a')}"}

    try:
        first = client.post("/limited", headers=headers)
        second = client.post("/limited", headers=headers)
        third = client.post("/limited", headers=headers)
    finally:
        settings.RATE_LIMIT_ENABLED = original_enabled
        settings.RATE_LIMIT_STORAGE_URL = original_storage
        _reset_rate_limit_state_for_tests()

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429
    assert third.headers.get("Retry-After") is not None


def test_rate_limit_isolated_by_user() -> None:
    original_enabled = settings.RATE_LIMIT_ENABLED
    original_storage = settings.RATE_LIMIT_STORAGE_URL
    settings.RATE_LIMIT_ENABLED = True
    settings.RATE_LIMIT_STORAGE_URL = ""
    _reset_rate_limit_state_for_tests()

    app = _build_app(limit=1, window_seconds=60)
    client = TestClient(app)
    headers_a = {"Authorization": f"Bearer {_make_token('user-a')}"}
    headers_b = {"Authorization": f"Bearer {_make_token('user-b')}"}

    try:
        response_a_first = client.post("/limited", headers=headers_a)
        response_a_second = client.post("/limited", headers=headers_a)
        response_b_first = client.post("/limited", headers=headers_b)
    finally:
        settings.RATE_LIMIT_ENABLED = original_enabled
        settings.RATE_LIMIT_STORAGE_URL = original_storage
        _reset_rate_limit_state_for_tests()

    assert response_a_first.status_code == 200
    assert response_a_second.status_code == 429
    assert response_b_first.status_code == 200
