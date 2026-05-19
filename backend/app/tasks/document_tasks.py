from __future__ import annotations

from app.celery_app import celery_app
from app.core.structured_logging import emit_structured_log
from app.core.supabase_client import get_supabase_service_client
from app.services.document_rag import mark_knowledge_document_failed, process_knowledge_document


@celery_app.task(name="process_knowledge_document_task")
def process_knowledge_document_task(document_id: str, user_id: str) -> dict[str, str]:
    service_client = get_supabase_service_client()
    try:
        process_knowledge_document(
            document_id=document_id,
            user_id=user_id,
            service_client=service_client,
        )
        return {
            "status": "indexed",
            "document_id": document_id,
        }
    except Exception as exc:
        mark_knowledge_document_failed(
            document_id=document_id,
            error_message=str(exc),
            service_client=service_client,
        )
        emit_structured_log(
            "knowledge_document_index_error",
            level="error",
            user_id=user_id,
            document_id=document_id,
            error=str(exc),
        )
        raise
