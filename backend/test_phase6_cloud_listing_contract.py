from app.core.config import settings
from app.services import cloud_oauth


class _FakeResponse:
    def __init__(self, payload: dict, ok: bool = True, text: str = ""):
        self._payload = payload
        self.ok = ok
        self.text = text

    def json(self):
        return self._payload


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run() -> None:
    original_google_client_id = settings.GOOGLE_DRIVE_CLIENT_ID
    original_google_client_secret = settings.GOOGLE_DRIVE_CLIENT_SECRET
    original_ms_client_id = settings.MICROSOFT_ONEDRIVE_CLIENT_ID
    original_ms_client_secret = settings.MICROSOFT_ONEDRIVE_CLIENT_SECRET
    original_requests_get = cloud_oauth.requests.get

    settings.GOOGLE_DRIVE_CLIENT_ID = "google-client"
    settings.GOOGLE_DRIVE_CLIENT_SECRET = "google-secret"
    settings.MICROSOFT_ONEDRIVE_CLIENT_ID = "ms-client"
    settings.MICROSOFT_ONEDRIVE_CLIENT_SECRET = "ms-secret"

    def fake_get(url, params=None, headers=None, timeout=None):
        if "googleapis.com/drive/v3/files" in url:
            return _FakeResponse({
                "nextPageToken": "g-next",
                "files": [
                    {
                        "id": "g1",
                        "name": "Ventas Abril.xlsx",
                        "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        "modifiedTime": "2026-04-14T07:00:00Z",
                        "size": "2048",
                        "webViewLink": "https://drive.google.com/file/d/g1/view",
                    },
                    {
                        "id": "g-noise",
                        "name": "Manual.pdf",
                        "mimeType": "application/pdf",
                        "modifiedTime": "2026-04-14T06:00:00Z",
                    }
                ],
            })
        if "graph.microsoft.com/v1.0/me/drive/root/search(q='.xlsx')" in url:
            return _FakeResponse({
                "value": [
                    {
                        "id": "m1",
                        "name": "Inventario Mayo.xlsx",
                        "size": 4096,
                        "file": {"mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
                        "lastModifiedDateTime": "2026-04-14T08:00:00Z",
                        "webUrl": "https://onedrive.live.com/?id=m1",
                        "@microsoft.graph.downloadUrl": "https://download.example/m1",
                    }
                ],
            })
        if "graph.microsoft.com/v1.0/me/drive/root/search(q='xlsx')" in url:
            return _FakeResponse({
                "value": [
                    {
                        "id": "m1",
                        "name": "Inventario Mayo.xlsx",
                        "size": 4096,
                        "file": {"mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
                        "lastModifiedDateTime": "2026-04-14T08:00:00Z",
                        "webUrl": "https://onedrive.live.com/?id=m1",
                        "@microsoft.graph.downloadUrl": "https://download.example/m1",
                    },
                ],
            })
        if "graph.microsoft.com/v1.0/me/drive/root/search(q='.csv')" in url:
            return _FakeResponse({
                "value": [
                    {
                        "id": "m2",
                        "name": "Inventario Mayo.csv",
                        "size": 1024,
                        "file": {"mimeType": "text/csv"},
                        "lastModifiedDateTime": "2026-04-14T07:00:00Z",
                        "webUrl": "https://onedrive.live.com/?id=m2",
                        "@microsoft.graph.downloadUrl": "https://download.example/m2",
                    },
                    {
                        "id": "m-noise",
                        "name": "Presentacion.pptx",
                        "size": 4096,
                        "file": {"mimeType": "application/vnd.openxmlformats-officedocument.presentationml.presentation"},
                        "lastModifiedDateTime": "2026-04-14T11:00:00Z",
                        "webUrl": "https://onedrive.live.com/?id=m-noise",
                    },
                ],
            })
        if "graph.microsoft.com/v1.0/me/drive/root/search(q='csv')" in url:
            return _FakeResponse({
                "value": [
                    {
                        "id": "m2",
                        "name": "Inventario Mayo.csv",
                        "size": 1024,
                        "file": {"mimeType": "text/csv"},
                        "lastModifiedDateTime": "2026-04-14T07:00:00Z",
                        "webUrl": "https://onedrive.live.com/?id=m2",
                        "@microsoft.graph.downloadUrl": "https://download.example/m2",
                    }
                ],
            })
        if "graph.microsoft.com/v1.0/me/drive/root/children" in url:
            return _FakeResponse({
                "value": [
                    {
                        "id": "m3",
                        "name": "Prueba Power Bi.xlsx",
                        "size": 3072,
                        "file": {"mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
                        "lastModifiedDateTime": "2026-04-14T08:30:00Z",
                        "webUrl": "https://onedrive.live.com/?id=m3",
                        "@microsoft.graph.downloadUrl": "https://download.example/m3",
                    }
                ],
            })
        if "graph.microsoft.com/v1.0/me/drive/root/search" in url:
            return _FakeResponse({
                "@odata.nextLink": "https://graph.microsoft.com/v1.0/me/drive/root/search?$skiptoken=ms-next",
                "value": [
                    {
                        "id": "m1",
                        "name": "Inventario Mayo.xlsx",
                        "size": 4096,
                        "file": {"mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
                        "lastModifiedDateTime": "2026-04-14T08:00:00Z",
                        "webUrl": "https://onedrive.live.com/?id=m1",
                        "@microsoft.graph.downloadUrl": "https://download.example/m1",
                    }
                ],
            })
        raise AssertionError(f"URL inesperada en test: {url}")

    cloud_oauth.requests.get = fake_get

    try:
        google_listing = cloud_oauth.list_provider_remote_files(
            "google_drive",
            connection_row={
                "provider": "google_drive",
                "access_token": "token-google",
                "external_account_email": "user@gmail.com",
            },
            service_client=None,
            limit=8,
        )
        _assert(google_listing["provider"] == "google_drive", "Provider Google inválido")
        _assert(google_listing["next_cursor"] == "g-next", "Cursor Google inválido")
        _assert(len(google_listing["files"]) == 1, "Cantidad Google inválida")
        _assert(google_listing["files"][0]["name"] == "Ventas Abril.xlsx", "Nombre Google inválido")
        _assert("Manual.pdf" not in [item["name"] for item in google_listing["files"]], "Google no debe incluir ruido")

        ms_listing = cloud_oauth.list_provider_remote_files(
            "onedrive",
            connection_row={
                "provider": "onedrive",
                "access_token": "token-ms",
                "external_account_email": "user@outlook.com",
            },
            service_client=None,
            limit=8,
        )
        _assert(ms_listing["provider"] == "onedrive", "Provider OneDrive inválido")
        _assert(ms_listing["next_cursor"] is None, "Listado inicial OneDrive no debe propagar cursor")
        _assert(len(ms_listing["files"]) == 3, "Cantidad OneDrive inválida")
        _assert(ms_listing["files"][0]["name"] == "Prueba Power Bi.xlsx", "Orden OneDrive inválido")
        _assert(ms_listing["files"][1]["download_url"] == "https://download.example/m1", "Download URL inválida")

        ms_search_listing = cloud_oauth.list_provider_remote_files(
            "onedrive",
            connection_row={
                "provider": "onedrive",
                "access_token": "token-ms",
                "external_account_email": "user@outlook.com",
            },
            service_client=None,
            limit=8,
            search="inventario",
        )
        _assert(ms_search_listing["next_cursor"] == "ms-next", "Cursor OneDrive search inválido")
        _assert(len(ms_search_listing["files"]) == 1, "Cantidad OneDrive search inválida")
    finally:
        settings.GOOGLE_DRIVE_CLIENT_ID = original_google_client_id
        settings.GOOGLE_DRIVE_CLIENT_SECRET = original_google_client_secret
        settings.MICROSOFT_ONEDRIVE_CLIENT_ID = original_ms_client_id
        settings.MICROSOFT_ONEDRIVE_CLIENT_SECRET = original_ms_client_secret
        cloud_oauth.requests.get = original_requests_get

    print("OK: phase6 cloud listing contract")


if __name__ == "__main__":
    run()
