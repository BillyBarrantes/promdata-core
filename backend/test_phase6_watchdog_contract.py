from copy import deepcopy

from app.services import cloud_watchdog


class _FakeResponse:
    def __init__(self, data):
        self.data = data


class _FakeTable:
    def __init__(self, client, name: str):
        self.client = client
        self.name = name
        self.filters: list[tuple[str, object]] = []
        self._limit: int | None = None
        self._order: tuple[str, bool] | None = None
        self._action: str | None = None
        self._payload = None
        self._on_conflict: str | None = None

    def select(self, _fields: str):
        return self

    def eq(self, key: str, value):
        self.filters.append((key, value))
        return self

    def limit(self, value: int):
        self._limit = value
        return self

    def order(self, key: str, desc: bool = False):
        self._order = (key, desc)
        return self

    def upsert(self, payload, on_conflict: str):
        self._action = "upsert"
        self._payload = payload
        self._on_conflict = on_conflict
        return self

    def update(self, payload):
        self._action = "update"
        self._payload = payload
        return self

    def execute(self):
        rows = self.client.tables.setdefault(self.name, [])

        def matches(row: dict) -> bool:
            return all(row.get(key) == value for key, value in self.filters)

        if self._action == "upsert":
            conflict_keys = [item.strip() for item in str(self._on_conflict or "").split(",") if item.strip()]
            existing = None
            for row in rows:
                if all(row.get(key) == self._payload.get(key) for key in conflict_keys):
                    existing = row
                    break
            if existing:
                existing.update(deepcopy(self._payload))
                return _FakeResponse([deepcopy(existing)])
            payload = deepcopy(self._payload)
            payload.setdefault("id", f"wt-{len(rows) + 1}")
            payload.setdefault("created_at", "2026-04-15T00:00:00+00:00")
            payload.setdefault("updated_at", "2026-04-15T00:00:00+00:00")
            rows.append(payload)
            return _FakeResponse([deepcopy(payload)])

        if self._action == "update":
            updated_rows = []
            for row in rows:
                if matches(row):
                    row.update(deepcopy(self._payload))
                    row["updated_at"] = "2026-04-15T00:05:00+00:00"
                    updated_rows.append(deepcopy(row))
            return _FakeResponse(updated_rows)

        result = [deepcopy(row) for row in rows if matches(row)]
        if self._order:
            key, desc = self._order
            result.sort(key=lambda item: item.get(key) or "", reverse=desc)
        if self._limit is not None:
            result = result[: self._limit]
        return _FakeResponse(result)


class _FakeServiceClient:
    def __init__(self):
        self.tables = {
            "cloud_watch_targets": [],
            "cloud_oauth_connections": [
                {
                    "id": "conn-onedrive",
                    "user_id": "00000000-0000-4000-8000-000000000001",
                    "provider": "onedrive",
                    "status": "active",
                    "access_token": "token-ms",
                },
                {
                    "id": "conn-google",
                    "user_id": "00000000-0000-4000-8000-000000000001",
                    "provider": "google_drive",
                    "status": "active",
                    "access_token": "token-google",
                },
            ],
        }

    def table(self, name: str):
        return _FakeTable(self, name)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run() -> None:
    original_get_provider_remote_file = cloud_watchdog.get_provider_remote_file
    original_google_drive_get_start_page_token = cloud_watchdog._google_drive_get_start_page_token
    original_google_drive_list_changes_page = cloud_watchdog._google_drive_list_changes_page

    remote_state = {
        "onedrive-file-1": {
            "id": "onedrive-file-1",
            "name": "Inventario Mayo.xlsx",
            "provider": "onedrive",
            "extension": "xlsx",
            "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "size_bytes": 2048,
            "etag": "etag-v1",
            "ctag": "ctag-v1",
            "modified_at": "2026-04-15T10:00:00Z",
            "web_url": "https://onedrive.example/onedrive-file-1",
            "ingest_source_type": "binary_file",
        },
        "google-sheet-1": {
            "id": "google-sheet-1",
            "name": "Planeacion Abril",
            "provider": "google_drive",
            "extension": "gsheet",
            "mime_type": "application/vnd.google-apps.spreadsheet",
            "size_bytes": None,
            "modified_at": "2026-04-15T09:00:00Z",
            "web_url": "https://docs.google.com/spreadsheets/d/google-sheet-1/edit",
            "ingest_source_type": "google_sheet",
        },
    }

    def fake_get_provider_remote_file(provider_id: str, *, connection_row, service_client, item_id: str):
        item = remote_state[item_id]
        return deepcopy(item)

    google_change_pages: dict[str, dict] = {}

    def fake_google_drive_get_start_page_token(connection_row: dict[str, object]) -> str:
        return "page-0"

    def fake_google_drive_list_changes_page(connection_row: dict[str, object], *, page_token: str) -> dict:
        payload = google_change_pages.get(page_token)
        if payload:
            return deepcopy(payload)
        return {
            "changes": [],
            "newStartPageToken": page_token,
        }

    cloud_watchdog.get_provider_remote_file = fake_get_provider_remote_file
    cloud_watchdog._google_drive_get_start_page_token = fake_google_drive_get_start_page_token
    cloud_watchdog._google_drive_list_changes_page = fake_google_drive_list_changes_page

    service_client = _FakeServiceClient()

    try:
        onedrive_target = cloud_watchdog.upsert_watch_target(
            user_id="00000000-0000-4000-8000-000000000001",
            provider_id="onedrive",
            connection_row={"id": "conn-onedrive", "provider": "onedrive"},
            remote_item=remote_state["onedrive-file-1"],
            service_client=service_client,
        )
        _assert(onedrive_target["target_id"] == "onedrive-file-1", "Target OneDrive inválido")
        _assert(onedrive_target["pending_change"] is False, "Alta inicial no debe quedar pendiente")
        _assert(onedrive_target["watchdog_mode"] == "polling", "OneDrive debe quedar en polling")

        google_target = cloud_watchdog.upsert_watch_target(
            user_id="00000000-0000-4000-8000-000000000001",
            provider_id="google_drive",
            connection_row={"id": "conn-google", "provider": "google_drive"},
            remote_item=remote_state["google-sheet-1"],
            service_client=service_client,
        )
        _assert(google_target["contract_status"] == "pending_registration", "Google debe quedar con contrato pendiente")

        listed_targets = cloud_watchdog.list_user_watch_targets(
            user_id="00000000-0000-4000-8000-000000000001",
            provider_id="onedrive",
            service_client=service_client,
        )
        _assert(len(listed_targets) == 1, "Debe listar un target activo de OneDrive")

        cloud_watchdog.link_watch_target_to_uploaded_file(
            user_id="00000000-0000-4000-8000-000000000001",
            provider_id="onedrive",
            target_id="onedrive-file-1",
            uploaded_file_id="uploaded-123",
            service_client=service_client,
        )
        linked_targets = cloud_watchdog.list_user_watch_targets(
            user_id="00000000-0000-4000-8000-000000000001",
            provider_id="onedrive",
            service_client=service_client,
        )
        _assert(linked_targets[0]["linked_file_id"] == "uploaded-123", "Debe enlazar el uploaded_file_id")

        remote_state["onedrive-file-1"]["size_bytes"] = 4096
        remote_state["onedrive-file-1"]["modified_at"] = "2026-04-15T12:00:00Z"
        remote_state["onedrive-file-1"]["etag"] = "etag-v2"
        remote_state["onedrive-file-1"]["ctag"] = "ctag-v2"
        poll_result = cloud_watchdog.poll_user_watch_targets(
            user_id="00000000-0000-4000-8000-000000000001",
            provider_id=None,
            service_client=service_client,
        )
        _assert(poll_result["checked_count"] == 2, "Google y OneDrive deben entrar al polling base")
        _assert(poll_result["new_change_count"] == 1, "Debe detectar exactamente un cambio nuevo")
        _assert(poll_result["skipped_contract_count"] == 0, "Google no debe saltarse en fallback polling")
        _assert(poll_result["changes"][0]["target_name"] == "Inventario Mayo.xlsx", "Nombre de cambio inválido")

        google_metadata_response = service_client.table("cloud_watch_targets").select("*").eq("id", google_target["id"]).limit(1).execute()
        google_watchdog = deepcopy(google_metadata_response.data[0]["metadata"]["watchdog"])
        _assert(google_watchdog["mode"] == "polling", "Google debe usar polling fallback en runtime")
        _assert(google_watchdog["provider_contract"]["contract_status"] == "polling_only", "Google debe degradar a polling_only sin webhook público")

        remote_state["onedrive-file-1"]["size_bytes"] = 4096
        remote_state["onedrive-file-1"]["modified_at"] = "2026-04-15T12:00:00Z"
        remote_state["onedrive-file-1"]["etag"] = "etag-v3"
        remote_state["onedrive-file-1"]["ctag"] = "ctag-v3"
        metadata_response = service_client.table("cloud_watch_targets").select("*").eq("id", onedrive_target["id"]).limit(1).execute()
        updated_metadata = deepcopy(metadata_response.data[0]["metadata"])
        updated_metadata["watchdog"]["pending_change"] = False
        updated_metadata["watchdog"]["pending_change_summary"] = None
        updated_metadata["watchdog"]["last_change_detected_at"] = None
        updated_metadata["watchdog"]["last_client_notified_at"] = None
        updated_metadata["watchdog"]["last_notified_change_signature"] = None
        service_client.table("cloud_watch_targets").update({
            "metadata": updated_metadata,
        }).eq("id", onedrive_target["id"]).execute()
        etag_only_poll = cloud_watchdog.poll_user_watch_targets(
            user_id="00000000-0000-4000-8000-000000000001",
            provider_id="onedrive",
            service_client=service_client,
        )
        _assert(etag_only_poll["new_change_count"] == 1, "Debe detectar cambio por eTag/cTag aun sin variar otros campos")

        remote_state["onedrive-file-1"]["size_bytes"] = 6144
        remote_state["onedrive-file-1"]["modified_at"] = "2026-04-15T12:15:00Z"
        remote_state["onedrive-file-1"]["etag"] = "etag-v4"
        remote_state["onedrive-file-1"]["ctag"] = "ctag-v4"
        repeated_pending_poll = cloud_watchdog.poll_user_watch_targets(
            user_id="00000000-0000-4000-8000-000000000001",
            provider_id="onedrive",
            service_client=service_client,
        )
        _assert(
            repeated_pending_poll["new_change_count"] == 1,
            "Debe volver a notificar si el mismo archivo pendiente recibe una nueva revisión remota",
        )
        serialized_pending_targets = cloud_watchdog.list_user_watch_targets(
            user_id="00000000-0000-4000-8000-000000000001",
            provider_id="onedrive",
            service_client=service_client,
        )
        _assert(serialized_pending_targets[0]["sync_state"] == "pending_sync", "La serialización debe exponer pending_sync")
        _assert(
            bool(serialized_pending_targets[0]["last_change_detected_at"]),
            "La serialización debe exponer la hora del último cambio detectado",
        )

        second_poll = cloud_watchdog.poll_user_watch_targets(
            user_id="00000000-0000-4000-8000-000000000001",
            provider_id="onedrive",
            service_client=service_client,
        )
        _assert(second_poll["new_change_count"] == 0, "No debe repetir cambios ya pendientes")

        remote_state["google-sheet-1"]["modified_at"] = "2026-04-15T13:30:00Z"
        google_change_pages["page-0"] = {
            "changes": [
                {
                    "fileId": "google-sheet-1",
                    "removed": False,
                    "file": {
                        "id": "google-sheet-1",
                        "name": "Planeacion Abril",
                        "mimeType": "application/vnd.google-apps.spreadsheet",
                        "modifiedTime": "2026-04-15T13:30:00Z",
                    },
                }
            ],
            "newStartPageToken": "page-1",
        }
        google_poll = cloud_watchdog.poll_user_watch_targets(
            user_id="00000000-0000-4000-8000-000000000001",
            provider_id="google_drive",
            service_client=service_client,
        )
        _assert(google_poll["checked_count"] == 1, "Google debe revisar un target activo")
        _assert(google_poll["new_change_count"] == 1, "Google debe detectar el cambio vía Changes API")
        _assert(google_poll["changes"][0]["target_name"] == "Planeacion Abril", "Nombre de cambio Google inválido")

        google_metadata_after_change = service_client.table("cloud_watch_targets").select("*").eq("id", google_target["id"]).limit(1).execute()
        changed_google_watchdog = deepcopy(google_metadata_after_change.data[0]["metadata"]["watchdog"])
        _assert(changed_google_watchdog["pending_change"] is True, "Google debe quedar pendiente de sincronización")
        _assert(changed_google_watchdog["sync_state"] == "pending_sync", "Google debe marcar pending_sync tras cambio detectado")

        remote_state["google-sheet-1"]["modified_at"] = "2026-04-15T13:45:00Z"
        google_change_pages["page-1"] = {
            "changes": [
                {
                    "fileId": "google-sheet-1",
                    "removed": False,
                    "file": {
                        "id": "google-sheet-1",
                        "name": "Planeacion Abril",
                        "mimeType": "application/vnd.google-apps.spreadsheet",
                        "modifiedTime": "2026-04-15T13:45:00Z",
                    },
                }
            ],
            "newStartPageToken": "page-2",
        }
        repeated_google_poll = cloud_watchdog.poll_user_watch_targets(
            user_id="00000000-0000-4000-8000-000000000001",
            provider_id="google_drive",
            service_client=service_client,
        )
        _assert(
            repeated_google_poll["new_change_count"] == 1,
            "Google debe volver a notificar nuevas revisiones del mismo archivo aunque siga pendiente",
        )

        deactivated = cloud_watchdog.deactivate_watch_target(
            user_id="00000000-0000-4000-8000-000000000001",
            provider_id="onedrive",
            watch_target_id=onedrive_target["id"],
            service_client=service_client,
        )
        _assert(deactivated is True, "Debe desactivar el target existente")
    finally:
        cloud_watchdog.get_provider_remote_file = original_get_provider_remote_file
        cloud_watchdog._google_drive_get_start_page_token = original_google_drive_get_start_page_token
        cloud_watchdog._google_drive_list_changes_page = original_google_drive_list_changes_page

    print("OK: phase6 watchdog contract")


if __name__ == "__main__":
    run()
