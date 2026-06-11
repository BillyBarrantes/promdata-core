from copy import deepcopy

from app.services.cloud_connectors import get_watchdog_runtime_status


class _FakeResponse:
    def __init__(self, data):
        self.data = data


class _FakeTable:
    def __init__(self, client, name: str):
        self.client = client
        self.name = name
        self.filters: list[tuple[str, object]] = []

    def select(self, _fields: str):
        return self

    def eq(self, key: str, value):
        self.filters.append((key, value))
        return self

    def execute(self):
        rows = self.client.tables.get(self.name, [])

        def matches(row: dict) -> bool:
            return all(row.get(key) == value for key, value in self.filters)

        return _FakeResponse([deepcopy(row) for row in rows if matches(row)])


class _FakeServiceClient:
    def __init__(self, *, connections: list[dict], targets: list[dict]):
        self.tables = {
            "cloud_oauth_connections": connections,
            "cloud_watch_targets": targets,
        }

    def table(self, name: str):
        return _FakeTable(self, name)


def _get_provider_state(payload: dict, provider_id: str) -> dict:
    for state in payload["provider_states"]:
        if state["provider_id"] == provider_id:
            return state
    raise AssertionError(f"No se encontró provider_state para {provider_id}")


def test_watchdog_runtime_status_reports_attention_for_pending_sync(monkeypatch) -> None:
    monkeypatch.setattr("app.services.cloud_connectors.settings.CONNECTOR_WATCHDOG_ENABLED", True)
    monkeypatch.setattr("app.services.cloud_connectors.settings.CONNECTOR_POLL_INTERVAL_SECONDS", 30)

    service_client = _FakeServiceClient(
        connections=[
            {
                "provider": "google_drive",
                "user_id": "00000000-0000-4000-8000-000000000001",
                "status": "active",
                "last_refreshed_at": "2026-04-28T10:00:00+00:00",
            }
        ],
        targets=[
            {
                "provider": "google_drive",
                "user_id": "00000000-0000-4000-8000-000000000001",
                "is_active": True,
                "updated_at": "2026-04-28T10:02:00+00:00",
                "created_at": "2026-04-28T10:00:00+00:00",
                "metadata": {
                    "watchdog": {
                        "pending_change": True,
                        "sync_state": "pending_sync",
                        "last_polled_at": "2026-04-28T10:01:00+00:00",
                        "provider_contract": {"contract_status": "active"},
                    }
                },
            }
        ],
    )

    payload = get_watchdog_runtime_status(service_client=service_client, user_id="00000000-0000-4000-8000-000000000001")

    assert payload["connected_provider_count"] == 1
    assert payload["active_target_count"] == 1
    assert payload["pending_target_count"] == 1
    assert payload["operational_state"] == "attention"
    google_state = _get_provider_state(payload, "google_drive")
    assert google_state["contract_state"] == "pending_sync"
    assert google_state["sync_summary"] == "Hay cambios detectados pendientes de sincronización o reimportación."
    assert google_state["recommended_action"] == "Verificar ahora y reimportar los archivos remotos pendientes."


def test_watchdog_runtime_status_reports_idle_without_targets(monkeypatch) -> None:
    monkeypatch.setattr("app.services.cloud_connectors.settings.CONNECTOR_WATCHDOG_ENABLED", True)
    monkeypatch.setattr("app.services.cloud_connectors.settings.CONNECTOR_POLL_INTERVAL_SECONDS", 30)

    service_client = _FakeServiceClient(
        connections=[
            {
                "provider": "onedrive",
                "user_id": "00000000-0000-4000-8000-000000000001",
                "status": "active",
                "last_refreshed_at": "2026-04-28T10:00:00+00:00",
            }
        ],
        targets=[],
    )

    payload = get_watchdog_runtime_status(service_client=service_client, user_id="00000000-0000-4000-8000-000000000001")

    assert payload["connected_provider_count"] == 1
    assert payload["active_target_count"] == 0
    assert payload["operational_state"] == "idle"
    assert payload["summary"] == "Watchdog listo, sin archivos vigilados todavía."
    onedrive_state = _get_provider_state(payload, "onedrive")
    assert onedrive_state["recommended_action"] == "Marcar archivos críticos como vigilados para activar re-sync."


def test_watchdog_runtime_status_preserves_runtime_counts_when_env_disabled(monkeypatch) -> None:
    monkeypatch.setattr("app.services.cloud_connectors.settings.CONNECTOR_WATCHDOG_ENABLED", False)
    monkeypatch.setattr("app.services.cloud_connectors.settings.CONNECTOR_POLL_INTERVAL_SECONDS", 30)

    service_client = _FakeServiceClient(
        connections=[
            {
                "provider": "google_drive",
                "user_id": "00000000-0000-4000-8000-000000000001",
                "status": "active",
                "last_refreshed_at": "2026-04-28T10:00:00+00:00",
            },
            {
                "provider": "onedrive",
                "user_id": "00000000-0000-4000-8000-000000000001",
                "status": "active",
                "last_refreshed_at": "2026-04-28T10:00:00+00:00",
            },
        ],
        targets=[
            {
                "provider": "google_drive",
                "user_id": "00000000-0000-4000-8000-000000000001",
                "is_active": True,
                "updated_at": "2026-04-28T10:02:00+00:00",
                "created_at": "2026-04-28T10:00:00+00:00",
                "metadata": {
                    "watchdog": {
                        "pending_change": False,
                        "sync_state": "synced",
                        "last_polled_at": "2026-04-28T10:01:00+00:00",
                        "provider_contract": {"contract_status": "polling_only"},
                    }
                },
            },
            {
                "provider": "onedrive",
                "user_id": "00000000-0000-4000-8000-000000000001",
                "is_active": True,
                "updated_at": "2026-04-28T10:02:00+00:00",
                "created_at": "2026-04-28T10:00:00+00:00",
                "metadata": {
                    "watchdog": {
                        "pending_change": False,
                        "sync_state": "synced",
                        "last_polled_at": "2026-04-28T10:01:00+00:00",
                        "provider_contract": {"contract_status": "active"},
                    }
                },
            },
        ],
    )

    payload = get_watchdog_runtime_status(service_client=service_client, user_id="00000000-0000-4000-8000-000000000001")

    assert payload["enabled"] is False
    assert payload["connected_provider_count"] == 2
    assert payload["active_target_count"] == 2
    assert payload["synced_target_count"] == 2
    assert payload["fallback_provider_count"] == 1
    assert payload["operational_state"] == "attention"
    google_state = _get_provider_state(payload, "google_drive")
    assert google_state["fallback_target_count"] == 1
    assert google_state["recommended_action"] == "Verificar ahora y revisar polling, credenciales o conectividad."
