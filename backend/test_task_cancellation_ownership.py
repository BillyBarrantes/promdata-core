"""Regresión de aislamiento multiusuario para la cancelación de tareas."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, call

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

import app.api.routes as routes
from app.main import app


TASK_ID = "64e07061-c760-40b2-8478-332e2fa9f8c4"
USER_ID = "30aeadaa-4977-4173-8c65-de12140ba353"


def _build_supabase_client(*, visible_task: bool) -> tuple[MagicMock, MagicMock, MagicMock]:
    """Crea un cliente Supabase fluido y expone builders para verificar su alcance."""
    client = MagicMock()
    table = MagicMock()
    select_query = MagicMock()
    update_query = MagicMock()

    client.table.return_value = table
    table.select.return_value = select_query
    select_query.eq.return_value = select_query
    select_query.limit.return_value = select_query
    select_query.execute.return_value = SimpleNamespace(
        data=[{"id": TASK_ID}] if visible_task else []
    )
    table.update.return_value = update_query
    update_query.eq.return_value = update_query
    update_query.execute.return_value = SimpleNamespace(data=[{"id": TASK_ID}])
    return client, table, select_query


def test_cancel_task_does_not_revoke_or_update_task_owned_by_another_user(
    monkeypatch: MonkeyPatch,
) -> None:
    """Un task_id no visible para el usuario autenticado no alcanza a Celery ni UPDATE."""
    client, table, select_query = _build_supabase_client(visible_task=False)
    revoke = MagicMock()
    monkeypatch.setattr(
        routes,
        "_get_authenticated_user",
        lambda token: (client, SimpleNamespace(id=USER_ID)),
    )
    monkeypatch.setattr(routes.celery_app.control, "revoke", revoke)

    response = TestClient(app).post(
        f"/api/v1/tasks/{TASK_ID}/cancel",
        headers={"Authorization": "Bearer test-user-token"},
    )

    assert response.status_code == 404
    assert select_query.eq.call_args_list == [call("id", TASK_ID), call("user_id", USER_ID)]
    table.update.assert_not_called()
    revoke.assert_not_called()


def test_cancel_task_updates_and_revokes_only_after_ownership_check(
    monkeypatch: MonkeyPatch,
) -> None:
    """La ruta mantiene la respuesta de éxito y filtra SELECT/UPDATE por propietario."""
    client, table, select_query = _build_supabase_client(visible_task=True)
    revoke = MagicMock()
    monkeypatch.setattr(
        routes,
        "_get_authenticated_user",
        lambda token: (client, SimpleNamespace(id=USER_ID)),
    )
    monkeypatch.setattr(routes.celery_app.control, "revoke", revoke)

    response = TestClient(app).post(
        f"/api/v1/tasks/{TASK_ID}/cancel",
        headers={"Authorization": "Bearer test-user-token"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "cancelled",
        "message": f"Tarea {TASK_ID} detenida permanentemente.",
    }
    assert select_query.eq.call_args_list == [call("id", TASK_ID), call("user_id", USER_ID)]
    assert table.update.call_args == call({"status": "cancelled"})
    update_query = table.update.return_value
    assert update_query.eq.call_args_list == [call("id", TASK_ID), call("user_id", USER_ID)]
    revoke.assert_called_once_with(TASK_ID, terminate=True, signal="SIGKILL")
