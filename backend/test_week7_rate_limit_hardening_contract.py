"""
Semana 7 — Día 4: Tests de contrato para Rate Limiting Granular (Día 3).
Valida burst protection, concurrency slots, fallback a memoria, y aislamiento entre usuarios.
"""
import pytest
from unittest.mock import patch, MagicMock
from fastapi import Request, HTTPException

from app.core.rate_limit import (
    enforce_burst_limit,
    acquire_concurrency_slot,
    release_concurrency_slot,
    _reset_rate_limit_state_for_tests,
)


@pytest.fixture(autouse=True)
def reset_rate_limit():
    """Limpia el estado en memoria antes y después de cada test."""
    _reset_rate_limit_state_for_tests()
    yield
    _reset_rate_limit_state_for_tests()


def _create_mock_request(ip="127.0.0.1"):
    request = MagicMock(spec=Request)
    request.client = MagicMock()
    request.client.host = ip
    request.url = MagicMock()
    request.url.path = "/analyze"
    request.method = "POST"
    return request


class TestBurstProtection:
    """Valida el bloqueo por ráfaga (micro-ventana de 5s)."""

    @patch("app.core.rate_limit._get_redis_client", return_value=None)
    @patch("app.core.rate_limit._extract_user_id_from_token", return_value="user-burst")
    @patch("app.core.rate_limit._resolve_team_id", return_value=None)
    def test_burst_allows_within_limit_and_blocks_excess(
        self, mock_team, mock_extract, mock_redis
    ):
        request = _create_mock_request()

        # Intento 1: Exitoso
        enforce_burst_limit(
            request=request, token="t1",
            scope="analyze", limit=2, window_seconds=5,
        )
        # Intento 2: Exitoso
        enforce_burst_limit(
            request=request, token="t1",
            scope="analyze", limit=2, window_seconds=5,
        )
        # Intento 3: Bloqueado (429)
        with pytest.raises(HTTPException) as exc_info:
            enforce_burst_limit(
                request=request, token="t1",
                scope="analyze", limit=2, window_seconds=5,
            )
        assert exc_info.value.status_code == 429


class TestConcurrencySlots:
    """Valida la concurrencia (max 2 slots por usuario) y liberación."""

    @patch("app.core.rate_limit._get_redis_client", return_value=None)
    @patch("app.core.rate_limit._extract_user_id_from_token", return_value="user-conc")
    def test_acquire_respects_limit_and_release_frees_slot(
        self, mock_extract, mock_redis,
    ):
        # Slot 1: OK
        assert acquire_concurrency_slot("t1", limit=2, ttl_seconds=60) is True
        # Slot 2: OK
        assert acquire_concurrency_slot("t1", limit=2, ttl_seconds=60) is True
        # Slot 3: Rechazado
        assert acquire_concurrency_slot("t1", limit=2, ttl_seconds=60) is False

        # Liberamos 1 slot
        release_concurrency_slot("user-conc")

        # Ahora se puede encolar otra tarea
        assert acquire_concurrency_slot("t1", limit=2, ttl_seconds=60) is True

    @patch("app.core.rate_limit._get_redis_client", return_value=None)
    @patch("app.core.rate_limit._extract_user_id_from_token", return_value=None)
    def test_acquire_allows_anonymous_users_unconditionally(
        self, mock_extract, mock_redis,
    ):
        """Si no se puede resolver un user_id (anónimo/token corrupto), se permite por defecto."""
        assert acquire_concurrency_slot("bad-token", limit=2, ttl_seconds=60) is True
        assert acquire_concurrency_slot("bad-token", limit=2, ttl_seconds=60) is True
        assert acquire_concurrency_slot("bad-token", limit=2, ttl_seconds=60) is True


class TestMemoryFallback:
    """Valida que con Redis offline, el limitador hace fallback a memoria."""

    @patch("app.core.rate_limit._get_redis_client", return_value=None)
    @patch("app.core.rate_limit._extract_user_id_from_token", return_value="user-mem")
    @patch("app.core.rate_limit._resolve_team_id", return_value=None)
    def test_burst_limit_works_without_redis(
        self, mock_team, mock_extract, mock_redis,
    ):
        """Burst limit debe funcionar idénticamente usando el store en memoria."""
        request = _create_mock_request()

        enforce_burst_limit(
            request=request, token="t1",
            scope="mem_test", limit=1, window_seconds=5,
        )
        with pytest.raises(HTTPException) as exc_info:
            enforce_burst_limit(
                request=request, token="t1",
                scope="mem_test", limit=1, window_seconds=5,
            )
        assert exc_info.value.status_code == 429

    @patch("app.core.rate_limit._get_redis_client", return_value=None)
    @patch("app.core.rate_limit._extract_user_id_from_token", return_value="user-mem2")
    def test_concurrency_works_without_redis(self, mock_extract, mock_redis):
        """Concurrency slots deben funcionar idénticamente usando el store en memoria."""
        assert acquire_concurrency_slot("t1", limit=1, ttl_seconds=60) is True
        assert acquire_concurrency_slot("t1", limit=1, ttl_seconds=60) is False

        release_concurrency_slot("user-mem2")
        assert acquire_concurrency_slot("t1", limit=1, ttl_seconds=60) is True


class TestUserIsolation:
    """Valida que la penalización de un usuario no afecta a otros."""

    @patch("app.core.rate_limit._get_redis_client", return_value=None)
    @patch("app.core.rate_limit._resolve_team_id", return_value=None)
    def test_user_a_blocked_does_not_affect_user_b(
        self, mock_team, mock_redis,
    ):
        request = _create_mock_request()

        # Usuario A agota su ráfaga (limit=1)
        with patch(
            "app.core.rate_limit._extract_user_id_from_token",
            return_value="user-A",
        ):
            enforce_burst_limit(
                request=request, token="tA",
                scope="iso", limit=1, window_seconds=5,
            )
            with pytest.raises(HTTPException) as exc_info:
                enforce_burst_limit(
                    request=request, token="tA",
                    scope="iso", limit=1, window_seconds=5,
                )
            assert exc_info.value.status_code == 429

        # Usuario B debería poder operar sin problemas
        with patch(
            "app.core.rate_limit._extract_user_id_from_token",
            return_value="user-B",
        ):
            enforce_burst_limit(
                request=request, token="tB",
                scope="iso", limit=1, window_seconds=5,
            )

    @patch("app.core.rate_limit._get_redis_client", return_value=None)
    def test_concurrency_isolated_between_users(self, mock_redis):
        """Slots de concurrencia están aislados por user_id."""
        with patch(
            "app.core.rate_limit._extract_user_id_from_token",
            return_value="user-X",
        ):
            assert acquire_concurrency_slot("tX", limit=1, ttl_seconds=60) is True
            assert acquire_concurrency_slot("tX", limit=1, ttl_seconds=60) is False

        with patch(
            "app.core.rate_limit._extract_user_id_from_token",
            return_value="user-Y",
        ):
            # User Y no está afectado por el bloqueo de User X
            assert acquire_concurrency_slot("tY", limit=1, ttl_seconds=60) is True
