import json
import time
import uuid
from types import SimpleNamespace

from fastapi.testclient import TestClient

import app.api.routes as routes
from app.core.supabase_client import get_supabase_service_client
from app.main import app

USER_ID = "30aeadaa-4977-4173-8c65-de12140ba353"
QUESTION_OK = "¿Cuál es el horario de trabajo?"
QUESTION_BAD = "¿Cuál es la política de viajes interplanetarios a Marte?"
CONTENT = (
    "Políticas de trabajo internas de PromData. "
    "El horario de trabajo es de 10:00 a.m. a 3:00 p.m. de lunes a viernes. "
    "El equipo debe registrar asistencia diaria y reportar incidencias al coordinador."
)


def main() -> None:
    title = f"Smoke Ask {uuid.uuid4().hex[:8]}"
    file_name = f"smoke_ask_{uuid.uuid4().hex[:8]}.txt"

    service = get_supabase_service_client()
    original_auth = routes._get_authenticated_user
    routes._get_authenticated_user = lambda token: (
        get_supabase_service_client(),
        SimpleNamespace(id=USER_ID),
    )

    document_id = None
    bucket_name = None
    storage_path = None

    try:
        client = TestClient(app)
        headers = {"Authorization": "Bearer smoke-test-token"}

        upload_response = client.post(
            "/api/v1/knowledge/documents/upload",
            headers=headers,
            data={"title": title},
            files={"file": (file_name, CONTENT.encode("utf-8"), "text/plain")},
        )
        if upload_response.status_code != 202:
            raise RuntimeError(f"UPLOAD_FAIL: {upload_response.status_code} {upload_response.text}")

        upload_payload = upload_response.json()
        document = upload_payload["document"]
        document_id = document["id"]
        bucket_name = document["bucket_name"]
        storage_path = document["storage_path"]

        deadline = time.time() + 180
        indexed = None
        while time.time() < deadline:
            rows = service.table("knowledge_documents").select(
                "id,status,chunk_count,last_error"
            ).eq("id", document_id).limit(1).execute().data
            if rows:
                indexed = rows[0]
                if indexed["status"] == "indexed" and int(indexed.get("chunk_count") or 0) > 0:
                    break
                if indexed["status"] == "failed":
                    raise RuntimeError(f"INDEX_FAIL: {indexed.get('last_error')}")
            time.sleep(5)

        if not indexed or indexed["status"] != "indexed":
            raise RuntimeError("INDEX_TIMEOUT")

        ask_ok = client.post(
            "/api/v1/knowledge/ask",
            headers=headers,
            json={"question": QUESTION_OK, "limit": 4, "document_ids": [document_id]},
        )
        if ask_ok.status_code != 200:
            raise RuntimeError(f"ASK_OK_FAIL: {ask_ok.status_code} {ask_ok.text}")
        ask_ok_payload = ask_ok.json()

        ask_bad = client.post(
            "/api/v1/knowledge/ask",
            headers=headers,
            json={"question": QUESTION_BAD, "limit": 4, "document_ids": [document_id]},
        )
        if ask_bad.status_code != 200:
            raise RuntimeError(f"ASK_BAD_FAIL: {ask_bad.status_code} {ask_bad.text}")
        ask_bad_payload = ask_bad.json()

        print(
            "ASK_SMOKE_RESULT",
            json.dumps(
                {
                    "document_id": document_id,
                    "question_ok": QUESTION_OK,
                    "answer_ok": ask_ok_payload.get("answer"),
                    "grounded_ok": ask_ok_payload.get("grounded"),
                    "insufficient_ok": ask_ok_payload.get("insufficient_evidence"),
                    "citations_ok": ask_ok_payload.get("citations"),
                    "question_bad": QUESTION_BAD,
                    "answer_bad": ask_bad_payload.get("answer"),
                    "grounded_bad": ask_bad_payload.get("grounded"),
                    "insufficient_bad": ask_bad_payload.get("insufficient_evidence"),
                    "citations_bad": ask_bad_payload.get("citations"),
                },
                ensure_ascii=False,
            ),
        )
    finally:
        routes._get_authenticated_user = original_auth
        if document_id:
            try:
                service.table("knowledge_document_chunks").delete().eq("document_id", document_id).execute()
            except Exception as cleanup_error:
                print("CLEANUP_CHUNKS_ERROR", cleanup_error)
            try:
                service.table("knowledge_documents").delete().eq("id", document_id).execute()
            except Exception as cleanup_error:
                print("CLEANUP_DOCUMENT_ERROR", cleanup_error)
        if bucket_name and storage_path:
            try:
                service.storage.from_(bucket_name).remove([storage_path])
            except Exception as cleanup_error:
                print("CLEANUP_STORAGE_ERROR", cleanup_error)


if __name__ == "__main__":
    main()
