from copy import deepcopy

from app.services import cloud_sync_jobs


class _FakeResponse:
    def __init__(self, data):
        self.data = data


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
            payload.setdefault("created_at", "2026-04-30T11:00:00+00:00")
            payload.setdefault("updated_at", "2026-04-30T11:00:00+00:00")
            rows.append(payload)
            return _FakeResponse([deepcopy(payload)])

        if self._action == "update":
            updated_rows = []
            for row in rows:
                if matches(row):
                    row.update(deepcopy(self._payload))
                    row["updated_at"] = "2026-04-30T11:05:00+00:00"
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
                {"user_id": "00000000-0000-4000-8000-000000000001", "team_id": "00000000-0000-4000-8000-000000000002"},
            ],
            "cloud_watch_targets": [
                {
                    "id": "wt-1",
                    "user_id": "00000000-0000-4000-8000-000000000001",
                    "provider": "google_drive",
                    "target_id": "remote-1",
                    "target_name": "Reporte Abril.xlsx",
                    "linked_file_id": "uploaded-1",
                    "is_active": True,
                    "metadata": {
                        "remote_snapshot": {
                            "modified_at": "2026-04-30T11:00:00Z",
                            "etag": "etag-v1",
                            "ctag": "ctag-v1",
                            "size_bytes": 1024,
                        },
                        "watchdog": {
                            "pending_change": True,
                            "pending_change_summary": "Cambio remoto detectado",
                            "last_notified_change_signature": "remote-1:2026-04-30T11:00:00Z:etag-v1:ctag-v1:1024",
                            "sync_state": "pending_sync",
                            "last_change_detected_at": "2026-04-30T11:00:00Z",
                        },
                    },
                },
                {
                    "id": "wt-2",
                    "user_id": "00000000-0000-4000-8000-000000000001",
                    "provider": "onedrive",
                    "target_id": "remote-2",
                    "target_name": "Sin enlace.xlsx",
                    "linked_file_id": None,
                    "is_active": True,
                    "metadata": {
                        "watchdog": {
                            "pending_change": True,
                            "last_notified_change_signature": "remote-2:rev-a",
                            "sync_state": "pending_sync",
                        },
                    },
                },
            ],
            "cloud_sync_jobs": [],
        }

    def table(self, name: str):
        return _FakeTable(self, name)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run() -> None:
    client = _FakeServiceClient()

    enqueue_summary = cloud_sync_jobs.enqueue_cloud_sync_jobs_for_watchdog_changes(
        user_id="00000000-0000-4000-8000-000000000001",
        changes=[
            {
                "watch_target_id": "wt-1",
                "provider": "google_drive",
                "target_id": "remote-1",
                "change_summary": "Cambio remoto detectado",
            },
            {
                "watch_target_id": "wt-2",
                "provider": "onedrive",
                "target_id": "remote-2",
                "change_summary": "Cambio sin file enlazado",
            },
        ],
        service_client=client,
        trigger_source="poll",
    )
    _assert(enqueue_summary["queued_count"] == 1, "Debe encolar solo el target con file enlazado")
    _assert(enqueue_summary["skipped_unlinked_count"] == 1, "Debe marcar skip para targets sin linked_file_id")
    _assert(len(client.tables["cloud_sync_jobs"]) == 1, "Debe persistir un único job")
    first_job = deepcopy(client.tables["cloud_sync_jobs"][0])
    _assert(first_job["status"] == "queued", "El job nuevo debe quedar en cola")

    queued_watchdog = client.tables["cloud_watch_targets"][0]["metadata"]["watchdog"]
    _assert(queued_watchdog["auto_sync_status"] == "queued", "El watch target debe marcar auto_sync queued")
    _assert(queued_watchdog["last_auto_sync_job_id"] == first_job["id"], "Debe persistir el último job asociado")

    pending_candidates = cloud_sync_jobs.collect_pending_auto_sync_candidates(
        user_id="00000000-0000-4000-8000-000000000001",
        provider_id="google_drive",
        service_client=client,
    )
    _assert(len(pending_candidates) == 1, "Debe recolectar el target enlazado en pending_sync")
    _assert(pending_candidates[0]["watch_target_id"] == "wt-1", "El candidato de auto sync debe apuntar al target correcto")

    client.tables["cloud_watch_targets"][0]["metadata"]["watchdog"]["pending_change"] = False
    client.tables["cloud_watch_targets"][0]["metadata"]["watchdog"]["sync_state"] = "synced"
    synced_candidates = cloud_sync_jobs.collect_pending_auto_sync_candidates(
        user_id="00000000-0000-4000-8000-000000000001",
        provider_id="google_drive",
        service_client=client,
    )
    _assert(len(synced_candidates) == 0, "No debe recolectar targets ya sincronizados")
    client.tables["cloud_watch_targets"][0]["metadata"]["watchdog"]["pending_change"] = True
    client.tables["cloud_watch_targets"][0]["metadata"]["watchdog"]["sync_state"] = "pending_sync"

    duplicate_while_active = cloud_sync_jobs.enqueue_cloud_sync_jobs_for_watchdog_changes(
        user_id="00000000-0000-4000-8000-000000000001",
        changes=[{
            "watch_target_id": "wt-1",
            "provider": "google_drive",
            "target_id": "remote-1",
            "change_summary": "Cambio remoto detectado",
        }],
        service_client=client,
        trigger_source="poll",
    )
    _assert(duplicate_while_active["queued_count"] == 0, "No debe duplicar jobs con uno activo en cola")
    _assert(duplicate_while_active["skipped_active_job_count"] == 1, "Debe registrar skip por job activo")

    client.tables["cloud_sync_jobs"][0]["status"] = "succeeded"
    duplicate_after_success = cloud_sync_jobs.enqueue_cloud_sync_jobs_for_watchdog_changes(
        user_id="00000000-0000-4000-8000-000000000001",
        changes=[{
            "watch_target_id": "wt-1",
            "provider": "google_drive",
            "target_id": "remote-1",
            "change_summary": "Cambio remoto detectado",
        }],
        service_client=client,
        trigger_source="poll",
    )
    _assert(duplicate_after_success["queued_count"] == 0, "No debe crear un job nuevo para la misma revisión ya registrada")
    _assert(duplicate_after_success["skipped_duplicate_revision_count"] == 1, "Debe registrar dedupe por revisión")

    success_job_response = client.table("cloud_sync_jobs").insert({
        "team_id": "00000000-0000-4000-8000-000000000002",
        "user_id": "00000000-0000-4000-8000-000000000001",
        "watch_target_id": "wt-1",
        "linked_file_id": "uploaded-1",
        "provider": "google_drive",
        "target_id": "remote-1",
        "revision_signature": "remote-1:2026-04-30T11:15:00Z:etag-v2:ctag-v2:2048",
        "trigger_source": "poll",
        "status": "queued",
        "attempt_count": 0,
        "metadata": {},
    }).execute()
    success_job_id = success_job_response.data[0]["id"]

    def fake_materialize_success(*, user_id, provider_id, item_id, service_client):
        _assert(user_id == "00000000-0000-4000-8000-000000000001", "Usuario inesperado en ejecución exitosa")
        _assert(provider_id == "google_drive", "Provider inesperado en ejecución exitosa")
        _assert(item_id == "remote-1", "Target inesperado en ejecución exitosa")
        return {
            "uploaded_file_id": "uploaded-1",
            "storage_path": "user-1/1714470000000_Reporte Abril.xlsx",
        }

    success_result = cloud_sync_jobs.execute_cloud_sync_job(
        job_id=success_job_id,
        materialize_import_fn=fake_materialize_success,
        service_client=client,
    )
    _assert(success_result["status"] == "succeeded", "La ejecución exitosa debe reportar succeeded")
    executed_job = next(row for row in client.tables["cloud_sync_jobs"] if row["id"] == success_job_id)
    _assert(executed_job["status"] == "succeeded", "El job debe quedar persistido como succeeded")
    success_watchdog = client.tables["cloud_watch_targets"][0]["metadata"]["watchdog"]
    _assert(success_watchdog["auto_sync_status"] == "synced", "El watch target debe quedar en synced tras éxito")
    _assert(success_watchdog["last_auto_sync_error"] is None, "El éxito debe limpiar errores previos")

    failure_job_response = client.table("cloud_sync_jobs").insert({
        "team_id": "00000000-0000-4000-8000-000000000002",
        "user_id": "00000000-0000-4000-8000-000000000001",
        "watch_target_id": "wt-1",
        "linked_file_id": "uploaded-1",
        "provider": "google_drive",
        "target_id": "remote-1",
        "revision_signature": "remote-1:2026-04-30T11:20:00Z:etag-v3:ctag-v3:4096",
        "trigger_source": "poll",
        "status": "queued",
        "attempt_count": 0,
        "metadata": {},
    }).execute()
    failure_job_id = failure_job_response.data[0]["id"]

    def fake_materialize_failure(*, user_id, provider_id, item_id, service_client):
        raise RuntimeError("provider timeout")

    try:
        cloud_sync_jobs.execute_cloud_sync_job(
            job_id=failure_job_id,
            materialize_import_fn=fake_materialize_failure,
            service_client=client,
        )
    except RuntimeError as exc:
        _assert(str(exc) == "provider timeout", "La ejecución fallida debe propagar el error original")
    else:
        raise AssertionError("La ejecución fallida debe lanzar excepción")

    failed_job = next(row for row in client.tables["cloud_sync_jobs"] if row["id"] == failure_job_id)
    _assert(failed_job["status"] == "failed", "El job fallido debe quedar persistido como failed")
    failed_watchdog = client.tables["cloud_watch_targets"][0]["metadata"]["watchdog"]
    _assert(failed_watchdog["auto_sync_status"] == "manual_attention", "El watch target debe degradar a manual_attention")
    _assert("provider timeout" in str(failed_watchdog["last_auto_sync_error"]), "El error del auto sync debe quedar trazado")

    print("OK: phase6 cloud sync job contract")


if __name__ == "__main__":
    run()
