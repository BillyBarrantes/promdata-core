"""
Semana 7 — Día 4: Tests de contrato para Pub/Sub (Día 1).
Valida publish_task_progress, degradación elegante sin Redis, y SSE 503.
"""
import pytest
from unittest.mock import patch, MagicMock

from app.core.redis_client import publish_task_progress


class TestPublishTaskProgressSerialization:
    """Valida que publish_task_progress serializa correctamente el payload."""

    @patch("app.core.redis_client.get_redis_client")
    def test_payload_is_published_as_json_to_correct_channel(self, mock_get_client):
        mock_redis = MagicMock()
        mock_get_client.return_value = mock_redis

        payload = {"status": "started", "message": "Analizando los datos..."}
        result = publish_task_progress("task-abc-123", payload)

        assert result is True
        mock_redis.publish.assert_called_once()
        args, _ = mock_redis.publish.call_args
        # Canal correcto
        assert "task-abc-123" in args[0]
        # Payload serializado como JSON string
        assert '"status": "started"' in args[1]
        assert '"message": "Analizando los datos..."' in args[1]


class TestPublishTaskProgressGracefulDegradation:
    """Valida que sin Redis, retorna False sin romper el sistema."""

    @patch("app.core.redis_client.get_redis_client", return_value=None)
    def test_returns_false_when_redis_client_is_none(self, _mock):
        result = publish_task_progress("task-123", {"status": "started"})
        assert result is False

    @patch("app.core.redis_client.get_redis_client")
    def test_returns_false_when_publish_raises_exception(self, mock_get_client):
        mock_redis = MagicMock()
        mock_redis.publish.side_effect = Exception("Connection refused")
        mock_get_client.return_value = mock_redis

        result = publish_task_progress("task-123", {"status": "started"})
        assert result is False


class TestSSEEndpointRedisOffline:
    """Valida que el endpoint SSE retorna 503 si Redis está offline."""

    @patch("app.api.sse_progress.get_pubsub_client", return_value=None)
    @patch("app.api.sse_progress.get_supabase_service_client")
    @patch("app.api.sse_progress.get_supabase_user_client")
    def test_sse_returns_503_when_redis_unavailable(self, mock_supabase_user, mock_supabase_svc, _mock_pubsub):
        from fastapi.testclient import TestClient
        from app.main import app

        fake_user_response = MagicMock()
        fake_user_response.user = MagicMock()
        fake_user_response.user.id = "test-user-id"
        fake_client = MagicMock()
        fake_client.auth.get_user.return_value = fake_user_response
        mock_supabase_user.return_value = fake_client

        mock_svc_table = MagicMock()
        mock_svc_table.execute.return_value.data = {"id": "task-123"}
        mock_supabase_svc.return_value.table.return_value.select.return_value.eq.return_value.eq.return_value.single.return_value = mock_svc_table

        client = TestClient(app)
        response = client.get(
            "/api/v1/tasks/task-123/stream",
            headers={"Authorization": "Bearer fake-test-token"},
        )
        assert response.status_code == 503
