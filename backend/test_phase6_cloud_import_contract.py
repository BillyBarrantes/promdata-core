from copy import deepcopy

from app.services import cloud_imports, cloud_oauth


class _FakeResponse:
    def __init__(self, data):
        self.data = data


class _FakeBucket:
    def __init__(self, buckets: dict[str, dict[str, bytes]], bucket_name: str):
        self.buckets = buckets
        self.bucket_name = bucket_name

    def upload(self, path: str, content: bytes, _options=None):
        self.buckets.setdefault(self.bucket_name, {})[path] = bytes(content)

    def remove(self, paths: list[str]):
        bucket = self.buckets.setdefault(self.bucket_name, {})
        removed = []
        for path in paths:
            if path in bucket:
                del bucket[path]
                removed.append(path)
        return removed


class _FakeStorage:
    def __init__(self):
        self.buckets: dict[str, dict[str, bytes]] = {}

    def from_(self, bucket_name: str):
        return _FakeBucket(self.buckets, bucket_name)


class _FakeTable:
    def __init__(self, client, name: str):
        self.client = client
        self.name = name
        self.filters: list[tuple[str, object]] = []
        self._limit: int | None = None
        self._action: str | None = None
        self._payload = None

    def select(self, _fields: str):
        return self

    def eq(self, key: str, value):
        self.filters.append((key, value))
        return self

    def limit(self, value: int):
        self._limit = value
        return self

    def insert(self, payload):
        self._action = "insert"
        self._payload = payload
        return self

    def update(self, payload):
        self._action = "update"
        self._payload = payload
        return self

    def execute(self):
        rows = self.client.tables.setdefault(self.name, [])

        def matches(row: dict) -> bool:
            return all(row.get(key) == value for key, value in self.filters)

        if self._action == "insert":
            payload = deepcopy(self._payload)
            payload.setdefault("id", f"{self.name}-{len(rows) + 1}")
            payload.setdefault("created_at", "2026-04-30T10:00:00+00:00")
            payload.setdefault("updated_at", "2026-04-30T10:00:00+00:00")
            rows.append(payload)
            return _FakeResponse([deepcopy(payload)])

        if self._action == "update":
            updated_rows = []
            for row in rows:
                if matches(row):
                    row.update(deepcopy(self._payload))
                    row["updated_at"] = "2026-04-30T10:05:00+00:00"
                    updated_rows.append(deepcopy(row))
            return _FakeResponse(updated_rows)

        result = [deepcopy(row) for row in rows if matches(row)]
        if self._limit is not None:
            result = result[: self._limit]
        return _FakeResponse(result)


class _FakeServiceClient:
    def __init__(self):
        self.tables = {
            "team_members": [
                {"user_id": "user-1", "team_id": "team-1"},
            ],
            "uploaded_files": [],
            "cloud_watch_targets": [
                {
                    "id": "wt-1",
                    "user_id": "user-1",
                    "provider": "google_drive",
                    "target_id": "remote-1",
                    "target_name": "Reporte Abril.xlsx",
                    "linked_file_id": None,
                    "is_active": True,
                    "metadata": {
                        "watchdog": {
                            "pending_change": True,
                            "pending_change_summary": "Cambio remoto detectado",
                            "last_client_notified_at": "2026-04-30T10:00:00Z",
                            "last_notified_change_signature": "rev-1",
                            "sync_state": "pending_sync",
                            "last_change_detected_at": "2026-04-30T10:00:00Z",
                            "last_error": "timeout",
                        },
                    },
                },
            ],
        }
        self.storage = _FakeStorage()

    def table(self, name: str):
        return _FakeTable(self, name)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _run_remote_download_contract() -> None:
    original_requests_get = cloud_oauth.requests.get

    def fake_get(url, params=None, headers=None, timeout=None):
        if "googleapis.com/drive/v3/files/google-sheet-1/export" in url:
            return _FakeHttpResponse(content=b"sheet-bytes")
        if "googleapis.com/drive/v3/files/google-sheet-1" in url:
            return _FakeHttpResponse({
                "id": "google-sheet-1",
                "name": "Planeacion Abril",
                "mimeType": cloud_oauth.GOOGLE_SHEETS_MIME,
                "modifiedTime": "2026-04-14T09:00:00Z",
                "webViewLink": "https://docs.google.com/spreadsheets/d/google-sheet-1/edit",
            })
        if "graph.microsoft.com/v1.0/me/drive/items/onedrive-file-1" in url and params:
            return _FakeHttpResponse({
                "id": "onedrive-file-1",
                "name": "Inventario.csv",
                "size": 128,
                "file": {"mimeType": "text/csv"},
                "lastModifiedDateTime": "2026-04-14T10:00:00Z",
                "webUrl": "https://onedrive.live.com/?id=onedrive-file-1",
            })
        if "graph.microsoft.com/v1.0/me/drive/items/onedrive-file-1" in url and not params:
            return _FakeHttpResponse({
                "id": "onedrive-file-1",
                "name": "Inventario.csv",
                "size": 128,
                "file": {"mimeType": "text/csv"},
                "lastModifiedDateTime": "2026-04-14T10:00:00Z",
                "webUrl": "https://onedrive.live.com/?id=onedrive-file-1",
                "@microsoft.graph.downloadUrl": "https://download.example/onedrive-file-1",
            })
        if "download.example/onedrive-file-1" in url:
            return _FakeHttpResponse(content=b"csv-bytes")
        raise AssertionError(f"URL inesperada en test: {url}")

    cloud_oauth.requests.get = fake_get
    try:
        google_payload = cloud_oauth.download_provider_remote_file(
            "google_drive",
            connection_row={"provider": "google_drive", "access_token": "token-google"},
            service_client=None,
            item_id="google-sheet-1",
        )
        _assert(google_payload["file_name"] == "Planeacion_Abril.xlsx", "Google Sheets debe exportarse a XLSX")
        _assert(google_payload["source_type"] == "google_export", "Google Sheets debe marcar export transparente")
        _assert(google_payload["bytes"] == b"sheet-bytes", "Contenido exportado Google inválido")

        onedrive_payload = cloud_oauth.download_provider_remote_file(
            "onedrive",
            connection_row={"provider": "onedrive", "access_token": "token-ms"},
            service_client=None,
            item_id="onedrive-file-1",
        )
        _assert(onedrive_payload["file_name"] == "Inventario.csv", "Nombre OneDrive inválido")
        _assert(onedrive_payload["source_type"] == "binary_download", "OneDrive debe descargar binario directo")
        _assert(onedrive_payload["bytes"] == b"csv-bytes", "Contenido OneDrive inválido")
    finally:
        cloud_oauth.requests.get = original_requests_get


class _FakeHttpResponse:
    def __init__(self, payload=None, ok: bool = True, text: str = "", content: bytes = b""):
        self._payload = payload or {}
        self.ok = ok
        self.text = text
        self.content = content

    def json(self):
        return self._payload


def _run_logical_refresh_contract() -> None:
    original_download = cloud_imports.download_provider_remote_file
    original_get_connection = cloud_imports.get_user_oauth_connection
    original_now_storage_stamp = cloud_imports._now_storage_stamp

    remote_state = {
        "file_name": "Reporte Abril.xlsx",
        "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "bytes": b"version-1",
        "source_type": "binary_download",
        "remote_item": {
            "id": "remote-1",
            "name": "Reporte Abril.xlsx",
        },
    }
    storage_stamps = iter([1714464000000, 1714464001000, 1714464002000])

    def fake_download(provider_id, connection_row=None, service_client=None, item_id=None):
        _assert(provider_id == "google_drive", "Provider inesperado en refresh lógico")
        _assert(item_id == "remote-1", "Item inesperado en refresh lógico")
        return deepcopy(remote_state)

    def fake_get_connection(user_id, provider_id, service_client):
        _assert(user_id == "user-1", "Usuario inesperado al resolver conexión")
        _assert(provider_id == "google_drive", "Provider inesperado al resolver conexión")
        return {"id": "conn-1", "provider": "google_drive", "status": "active"}

    cloud_imports.download_provider_remote_file = fake_download
    cloud_imports.get_user_oauth_connection = fake_get_connection
    cloud_imports._now_storage_stamp = lambda: next(storage_stamps)

    client = _FakeServiceClient()
    try:
        first_import = cloud_imports.materialize_cloud_import(
            user_id="user-1",
            provider_id="google_drive",
            item_id="remote-1",
            service_client=client,
            connection_row={"id": "conn-1", "provider": "google_drive", "status": "active"},
        )
        _assert(first_import["import_action"] == "created_new_file", "El primer import debe crear el archivo lógico")
        _assert(len(client.tables["uploaded_files"]) == 1, "Solo debe existir un uploaded_file tras el import inicial")
        first_uploaded_file = deepcopy(client.tables["uploaded_files"][0])
        first_storage_path = first_uploaded_file["storage_path"]
        _assert(first_import["uploaded_file_id"] == first_uploaded_file["id"], "El response debe exponer el file_id creado")
        _assert(
            client.storage.buckets["dash-uploads"][first_storage_path] == b"version-1",
            "El blob inicial debe persistirse en storage",
        )

        first_watchdog_state = client.tables["cloud_watch_targets"][0]["metadata"]["watchdog"]
        _assert(client.tables["cloud_watch_targets"][0]["linked_file_id"] == first_uploaded_file["id"], "El watch target debe enlazarse al file lógico")
        _assert(first_watchdog_state["pending_change"] is False, "El enlace debe limpiar pending_change")
        _assert(first_watchdog_state["sync_state"] == "synced", "El enlace debe dejar sync_state en synced")
        _assert(first_watchdog_state["last_notified_change_signature"] is None, "El enlace debe limpiar la firma notificada")

        remote_state["file_name"] = "Reporte Abril Ajustado.xlsx"
        remote_state["bytes"] = b"version-2"

        second_import = cloud_imports.materialize_cloud_import(
            user_id="user-1",
            provider_id="google_drive",
            item_id="remote-1",
            service_client=client,
            connection_row={"id": "conn-1", "provider": "google_drive", "status": "active"},
        )
        _assert(second_import["import_action"] == "refreshed_existing_file", "La reimportación manual debe refrescar el file lógico existente")
        _assert(len(client.tables["uploaded_files"]) == 1, "La reimportación no debe duplicar uploaded_files")
        _assert(second_import["uploaded_file_id"] == first_uploaded_file["id"], "La reimportación debe conservar el mismo file_id")

        second_uploaded_file = deepcopy(client.tables["uploaded_files"][0])
        second_storage_path = second_uploaded_file["storage_path"]
        _assert(second_storage_path != first_storage_path, "El refresh debe rotar storage_path")
        _assert(second_uploaded_file["file_name"] == "Reporte Abril Ajustado.xlsx", "El refresh debe actualizar el nombre visible")
        _assert(first_storage_path not in client.storage.buckets["dash-uploads"], "El blob previo debe limpiarse tras el refresh")
        _assert(
            client.storage.buckets["dash-uploads"][second_storage_path] == b"version-2",
            "El blob refrescado debe persistirse en el nuevo storage_path",
        )

        updated_metadata = deepcopy(client.tables["cloud_watch_targets"][0]["metadata"])
        updated_metadata["watchdog"]["pending_change"] = True
        updated_metadata["watchdog"]["pending_change_summary"] = "Nueva revisión detectada"
        updated_metadata["watchdog"]["sync_state"] = "pending_sync"
        updated_metadata["watchdog"]["last_notified_change_signature"] = "rev-2"
        updated_metadata["watchdog"]["last_change_detected_at"] = "2026-04-30T10:10:00Z"
        client.tables["cloud_watch_targets"][0]["metadata"] = updated_metadata

        remote_state["bytes"] = b"version-3"
        synced_uploaded_file = cloud_imports.sync_uploaded_file_from_pending_watch_target(
            user_id="user-1",
            uploaded_file=deepcopy(client.tables["uploaded_files"][0]),
            service_client=client,
        )
        _assert(synced_uploaded_file["id"] == first_uploaded_file["id"], "El sync pendiente debe conservar el mismo file_id")
        _assert(len(client.tables["uploaded_files"]) == 1, "El sync pendiente tampoco debe duplicar uploaded_files")

        third_uploaded_file = deepcopy(client.tables["uploaded_files"][0])
        third_storage_path = third_uploaded_file["storage_path"]
        _assert(third_storage_path != second_storage_path, "El sync pendiente debe refrescar nuevamente el storage_path")
        _assert(second_storage_path not in client.storage.buckets["dash-uploads"], "El refresh pendiente debe limpiar el blob anterior")
        _assert(
            client.storage.buckets["dash-uploads"][third_storage_path] == b"version-3",
            "El sync pendiente debe escribir la versión remota más reciente",
        )

        final_watchdog_state = client.tables["cloud_watch_targets"][0]["metadata"]["watchdog"]
        _assert(final_watchdog_state["pending_change"] is False, "El sync pendiente debe limpiar pending_change")
        _assert(final_watchdog_state["sync_state"] == "synced", "El sync pendiente debe volver a synced")
        _assert(final_watchdog_state["pending_change_summary"] is None, "El sync pendiente debe limpiar el resumen pendiente")
        _assert(final_watchdog_state["last_change_detected_at"] is None, "El sync pendiente debe limpiar la marca de cambio detectado")
    finally:
        cloud_imports.download_provider_remote_file = original_download
        cloud_imports.get_user_oauth_connection = original_get_connection
        cloud_imports._now_storage_stamp = original_now_storage_stamp


def run() -> None:
    _run_remote_download_contract()
    _run_logical_refresh_contract()
    print("OK: phase6 cloud import contract")


if __name__ == "__main__":
    run()
