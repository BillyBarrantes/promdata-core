import json
import os
import time
import uuid
from types import SimpleNamespace

from fastapi.testclient import TestClient

import app.api.routes as routes
from app.core.supabase_client import get_supabase_service_client
from app.main import app
from app.services.document_rag import _parse_embedding, generate_embedding_vector

USER_ID = "30aeadaa-4977-4173-8c65-de12140ba353"
QUERY = "Que recomienda el plan de contingencia para la caida de ventas en Q4 y los canales retail?"
SMOKE_MODE = os.getenv("SMOKE_MODE", "full").strip().lower()


def main() -> None:
    service = get_supabase_service_client()
    team_rows = service.table("team_members").select("team_id").eq("user_id", USER_ID).limit(1).execute().data
    if not team_rows:
        raise RuntimeError("No se encontro team_members para el usuario objetivo.")
    team_id = team_rows[0]["team_id"]

    title = os.getenv("SMOKE_TITLE") or f"Smoke RAG {uuid.uuid4().hex[:8]}"
    file_name = os.getenv("SMOKE_FILE_NAME") or f"smoke_rag_{uuid.uuid4().hex[:8]}.txt"
    content = os.getenv("SMOKE_CONTENT") or (
        "Plan de contingencia corporativo Q4 para PromData. "
        "Si las ventas caen en Q4, la estrategia prioritaria es activar promociones tacticas en retail, "
        "reforzar seguimiento semanal por canal, reasignar presupuesto comercial a cuentas con mejor rotacion "
        "y escalar un comite ejecutivo de respuesta en 72 horas."
    )

    original_auth = routes._get_authenticated_user
    routes._get_authenticated_user = lambda token: (
        get_supabase_service_client(),
        SimpleNamespace(id=USER_ID),
    )

    document_id = None
    storage_path = os.getenv("SMOKE_STORAGE_PATH")
    bucket_name = os.getenv("SMOKE_BUCKET_NAME")

    try:
        client = TestClient(app)
        headers = {"Authorization": "Bearer smoke-test-token"}

        if SMOKE_MODE in {"full", "upload_only"}:
            response = client.post(
                "/api/v1/knowledge/documents/upload",
                headers=headers,
                data={"title": title},
                files={"file": (file_name, content.encode("utf-8"), "text/plain")},
            )
            print("UPLOAD_STATUS", response.status_code)
            if response.status_code != 202:
                raise RuntimeError(response.text)

            payload = response.json()
            document = payload["document"]
            document_id = document["id"]
            storage_path = document["storage_path"]
            bucket_name = document["bucket_name"]

            print(
                "UPLOAD_PAYLOAD",
                json.dumps(
                    {
                        "document_id": document_id,
                        "status": document["status"],
                        "task_status": payload["task_status"],
                        "bucket_name": bucket_name,
                        "storage_path": storage_path,
                        "title": title,
                        "file_name": file_name,
                    }
                ),
            )

            if document["status"] != "queued" or payload["task_status"] != "queued":
                raise RuntimeError("El upload no devolvio estado queued.")

            stored_bytes = service.storage.from_(bucket_name).download(storage_path)
            print("BUCKET_STORED_BYTES", len(stored_bytes))
            if stored_bytes.decode("utf-8") != content:
                raise RuntimeError("El contenido almacenado en bucket no coincide con el upload.")

            queued_row = service.table("knowledge_documents").select(
                "id,status,chunk_count"
            ).eq("id", document_id).limit(1).execute().data
            print("DB_AFTER_UPLOAD", json.dumps(queued_row[0] if queued_row else {}))
            if not queued_row:
                raise RuntimeError("La base no registro el documento tras el upload.")
            if SMOKE_MODE == "upload_only" and queued_row[0]["status"] != "queued":
                raise RuntimeError("La base no registro el documento como queued con el worker detenido.")
            if SMOKE_MODE == "upload_only":
                print(
                    "SMOKE_UPLOAD_ONLY_RESULT",
                    json.dumps(
                        {
                            "upload": "ok",
                            "bucket": "ok",
                            "db_queued": "ok",
                            "document_id": document_id,
                            "bucket_name": bucket_name,
                            "storage_path": storage_path,
                            "title": title,
                            "file_name": file_name,
                            "content": content,
                        }
                    ),
                )
                return
        elif SMOKE_MODE == "resume":
            document_id = os.getenv("SMOKE_DOCUMENT_ID")
            if not document_id or not storage_path or not bucket_name:
                raise RuntimeError("SMOKE_DOCUMENT_ID, SMOKE_STORAGE_PATH y SMOKE_BUCKET_NAME son obligatorios en modo resume.")
        else:
            raise RuntimeError(f"Modo de smoke test no soportado: {SMOKE_MODE}")

        deadline = time.time() + 180
        indexed_row = None
        while time.time() < deadline:
            rows = service.table("knowledge_documents").select(
                "id,status,chunk_count,processed_at,last_error"
            ).eq("id", document_id).limit(1).execute().data
            if rows:
                indexed_row = rows[0]
                print("POLL_STATUS", json.dumps(indexed_row))
                if indexed_row["status"] == "indexed" and int(indexed_row.get("chunk_count") or 0) > 0:
                    break
                if indexed_row["status"] == "failed":
                    raise RuntimeError(f"Indexacion fallo: {indexed_row.get('last_error')}")
            time.sleep(5)

        if not indexed_row or indexed_row["status"] != "indexed":
            raise RuntimeError("Celery no indexo el documento dentro del tiempo esperado.")

        chunk_rows = service.table("knowledge_document_chunks").select(
            "document_id,chunk_index,content,embedding"
        ).eq("document_id", document_id).limit(5).execute().data
        print("CHUNK_COUNT", len(chunk_rows))
        if not chunk_rows:
            raise RuntimeError("No se generaron chunks en la base.")
        persisted_embedding = _parse_embedding(chunk_rows[0].get("embedding"))
        print("PERSISTED_EMBEDDING_DIMENSIONS", len(persisted_embedding))
        if len(persisted_embedding) != 768:
            raise RuntimeError("El embedding persistido no tiene 768 dimensiones.")

        query_embedding = generate_embedding_vector(QUERY)
        print("QUERY_EMBEDDING_DIMENSIONS", len(query_embedding))
        if len(query_embedding) != 768:
            raise RuntimeError("El embedding de consulta no tiene 768 dimensiones.")
        rpc_rows = service.rpc(
            "match_knowledge_document_chunks",
            {
                "query_embedding": query_embedding,
                "match_count": 3,
                "filter_user_id": USER_ID,
                "filter_team_id": team_id,
                "filter_document_ids": [document_id],
            },
        ).execute().data or []
        print("RPC_RESULT_COUNT", len(rpc_rows))
        if not rpc_rows:
            raise RuntimeError("La RPC match_knowledge_document_chunks no devolvio resultados.")

        query_response = client.post(
            "/api/v1/knowledge/query",
            headers=headers,
            json={"query": QUERY, "limit": 3, "document_ids": [document_id]},
        )
        print("QUERY_STATUS", query_response.status_code)
        if query_response.status_code != 200:
            raise RuntimeError(query_response.text)

        query_payload = query_response.json()
        print(
            "QUERY_PAYLOAD",
            json.dumps(
                {
                    "count": query_payload["count"],
                    "top_document_id": query_payload["snippets"][0]["document_id"] if query_payload["snippets"] else None,
                    "top_similarity": query_payload["snippets"][0]["similarity"] if query_payload["snippets"] else None,
                }
            ),
        )
        if query_payload["count"] < 1:
            raise RuntimeError("El endpoint /knowledge/query no devolvio snippets.")
        if query_payload["snippets"][0]["document_id"] != document_id:
            raise RuntimeError("El snippet principal no corresponde al documento cargado.")

        print(
            "SMOKE_TEST_RESULT",
            json.dumps(
                {
                    "upload": "ok",
                    "bucket": "ok",
                    "db_queued": "ok",
                    "celery_indexed": "ok",
                    "rpc_query": "ok",
                    "endpoint_query": "ok",
                    "document_id": document_id,
                }
            ),
        )
    finally:
        routes._get_authenticated_user = original_auth
        if SMOKE_MODE != "upload_only" and document_id:
            try:
                service.table("knowledge_document_chunks").delete().eq("document_id", document_id).execute()
            except Exception as cleanup_error:
                print("CLEANUP_CHUNKS_ERROR", cleanup_error)
            try:
                service.table("knowledge_documents").delete().eq("id", document_id).execute()
            except Exception as cleanup_error:
                print("CLEANUP_DOCUMENT_ERROR", cleanup_error)
        if SMOKE_MODE != "upload_only" and bucket_name and storage_path:
            try:
                service.storage.from_(bucket_name).remove([storage_path])
            except Exception as cleanup_error:
                print("CLEANUP_STORAGE_ERROR", cleanup_error)


if __name__ == "__main__":
    main()
