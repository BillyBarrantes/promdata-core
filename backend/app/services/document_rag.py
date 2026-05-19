from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import io
import math
import os
import re
from typing import Any

import google.generativeai as genai
import numpy as np

from app.core.config import settings
from app.core.structured_logging import emit_structured_log
from app.services.governance import build_document_governance_metadata

KNOWLEDGE_DOCUMENTS_TABLE = "knowledge_documents"
KNOWLEDGE_DOCUMENT_CHUNKS_TABLE = "knowledge_document_chunks"
KNOWLEDGE_MATCH_RPC = "match_knowledge_document_chunks"


@dataclass
class KnowledgeSnippet:
    document_id: str
    document_title: str
    document_file_name: str
    chunk_index: int
    content: str
    similarity: float | None
    source_kind: str
    metadata: dict[str, Any]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _slugify_filename(file_name: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", file_name or "documento")
    return normalized.strip("._") or "documento"


def _estimate_token_count(text: str) -> int:
    normalized = _normalize_whitespace(text)
    if not normalized:
        return 0
    return max(1, math.ceil(len(normalized) / 4))


def _cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    a = np.array(vec_a, dtype=float)
    b = np.array(vec_b, dtype=float)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def _parse_embedding(raw_embedding: Any) -> list[float]:
    if isinstance(raw_embedding, list):
        return [float(value) for value in raw_embedding]
    if isinstance(raw_embedding, str):
        stripped = raw_embedding.strip().strip("[]")
        if not stripped:
            return []
        return [float(part.strip()) for part in stripped.split(",") if part.strip()]
    return []


def _coerce_embedding_dimensions(embedding: list[float], *, expected_dimensions: int) -> list[float]:
    normalized = [float(value) for value in embedding]
    if expected_dimensions <= 0:
        raise ValueError("KNOWLEDGE_EMBEDDING_DIMENSIONS debe ser mayor que cero.")
    if len(normalized) == expected_dimensions:
        return normalized
    if len(normalized) > expected_dimensions:
        emit_structured_log(
            "knowledge_embedding_dimension_truncated",
            expected_dimensions=expected_dimensions,
            actual_dimensions=len(normalized),
        )
        return normalized[:expected_dimensions]
    emit_structured_log(
        "knowledge_embedding_dimension_padded",
        level="warning",
        expected_dimensions=expected_dimensions,
        actual_dimensions=len(normalized),
    )
    return normalized + [0.0] * (expected_dimensions - len(normalized))


def resolve_user_team_id(*, user_id: str, service_client: Any) -> str:
    response = service_client.table("team_members") \
        .select("team_id") \
        .eq("user_id", user_id) \
        .limit(1) \
        .execute()

    if not response.data:
        raise ValueError("No se encontró team_id para el usuario autenticado.")

    team_id = response.data[0].get("team_id")
    if not team_id:
        raise ValueError("El usuario no tiene un team_id válido.")
    return str(team_id)


def infer_knowledge_source_kind(*, file_name: str, mime_type: str | None) -> str:
    extension = os.path.splitext(file_name or "")[1].lower()
    normalized_mime = str(mime_type or "").lower()
    if extension == ".pdf" or normalized_mime == "application/pdf":
        return "pdf"
    if extension in {".txt", ".md"} or normalized_mime.startswith("text/"):
        return "text"
    return "unsupported"


def extract_document_text(*, file_name: str, mime_type: str | None, file_bytes: bytes) -> tuple[str, dict[str, Any]]:
    source_kind = infer_knowledge_source_kind(file_name=file_name, mime_type=mime_type)
    extraction_meta: dict[str, Any] = {
        "source_kind": source_kind,
        "file_size_bytes": len(file_bytes),
    }

    if source_kind == "pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise ValueError("Falta instalar 'pypdf' para procesar documentos PDF.") from exc
        reader = PdfReader(io.BytesIO(file_bytes))
        page_texts: list[str] = []
        for page_index, page in enumerate(reader.pages):
            raw_text = page.extract_text() or ""
            normalized = _normalize_whitespace(raw_text)
            if normalized:
                page_texts.append(normalized)
            extraction_meta.setdefault("pages_with_text", []).append(page_index + 1 if normalized else None)
        extracted_text = "\n\n".join(page_texts).strip()
        extraction_meta["page_count"] = len(reader.pages)
        extraction_meta["pages_extracted"] = len(page_texts)
        if not extracted_text:
            raise ValueError("No se pudo extraer texto legible del PDF.")
        return extracted_text, extraction_meta

    if source_kind == "text":
        decoded_text = ""
        for candidate_encoding in ("utf-8", "latin-1"):
            try:
                decoded_text = file_bytes.decode(candidate_encoding)
                break
            except UnicodeDecodeError:
                continue
        normalized = _normalize_whitespace(decoded_text)
        if not normalized:
            raise ValueError("El documento de texto está vacío o no es legible.")
        return normalized, extraction_meta

    raise ValueError("Formato documental no soportado todavía. Usa PDF, TXT o MD.")


def chunk_document_text(
    text: str,
    *,
    chunk_size: int | None = None,
    overlap: int | None = None,
) -> list[str]:
    normalized_text = _normalize_whitespace(text)
    if not normalized_text:
        return []

    effective_chunk_size = max(400, int(chunk_size or settings.KNOWLEDGE_MAX_CHUNK_CHARS))
    effective_overlap = max(0, int(overlap or settings.KNOWLEDGE_CHUNK_OVERLAP_CHARS))
    effective_overlap = min(effective_overlap, effective_chunk_size // 3)

    paragraphs = [
        _normalize_whitespace(paragraph)
        for paragraph in re.split(r"(?:\n\s*\n)+", text)
        if _normalize_whitespace(paragraph)
    ]
    if not paragraphs:
        paragraphs = [normalized_text]

    chunks: list[str] = []
    current_parts: list[str] = []
    current_length = 0

    for paragraph in paragraphs:
        paragraph_length = len(paragraph)
        if paragraph_length > effective_chunk_size:
            if current_parts:
                chunks.append(" ".join(current_parts).strip())
                current_parts = []
                current_length = 0
            start = 0
            while start < paragraph_length:
                end = min(paragraph_length, start + effective_chunk_size)
                segment = paragraph[start:end].strip()
                if segment:
                    chunks.append(segment)
                if end >= paragraph_length:
                    break
                start = max(end - effective_overlap, start + 1)
            continue

        projected_length = current_length + paragraph_length + (1 if current_parts else 0)
        if projected_length > effective_chunk_size and current_parts:
            chunks.append(" ".join(current_parts).strip())
            if effective_overlap > 0 and chunks[-1]:
                overlap_text = chunks[-1][-effective_overlap:].strip()
                current_parts = [overlap_text] if overlap_text else []
                current_length = len(overlap_text)
            else:
                current_parts = []
                current_length = 0

        current_parts.append(paragraph)
        current_length += paragraph_length + (1 if current_parts else 0)

    if current_parts:
        chunks.append(" ".join(current_parts).strip())

    return [chunk for chunk in chunks if chunk]


def generate_embedding_vector(text: str) -> list[float]:
    expected_dimensions = int(settings.KNOWLEDGE_EMBEDDING_DIMENSIONS)
    request_payload = {
        "model": "models/gemini-embedding-001",
        "content": text,
        "task_type": "retrieval_document",
        "title": "Institutional Knowledge Chunk",
    }
    try:
        response = genai.embed_content(
            **request_payload,
            output_dimensionality=expected_dimensions,
        )
    except TypeError:
        emit_structured_log(
            "knowledge_embedding_dimension_native_unsupported",
            level="warning",
            expected_dimensions=expected_dimensions,
        )
        response = genai.embed_content(**request_payload)
    embedding = response.get("embedding")
    if not embedding or not isinstance(embedding, list):
        raise ValueError("Gemini no devolvió un embedding válido.")
    return _coerce_embedding_dimensions(
        [float(value) for value in embedding],
        expected_dimensions=expected_dimensions,
    )


def _document_bucket_name() -> str:
    return str(settings.KNOWLEDGE_DOCUMENTS_BUCKET or "knowledge-documents").strip() or "knowledge-documents"


def create_knowledge_document_record(
    *,
    user_id: str,
    team_id: str,
    title: str,
    file_name: str,
    mime_type: str | None,
    storage_path: str,
    file_size_bytes: int,
    service_client: Any,
) -> dict[str, Any]:
    source_kind = infer_knowledge_source_kind(file_name=file_name, mime_type=mime_type)
    payload = {
        "user_id": user_id,
        "team_id": team_id,
        "title": title,
        "file_name": file_name,
        "bucket_name": _document_bucket_name(),
        "storage_path": storage_path,
        "mime_type": mime_type or "application/octet-stream",
        "file_size_bytes": int(file_size_bytes),
        "source_kind": source_kind,
        "status": "queued",
        "chunk_count": 0,
        "word_count": 0,
        "metadata": {
            "ingestion_mode": "async",
            "uploaded_at": _now_iso(),
        },
    }
    payload["metadata"] = build_document_governance_metadata(
        metadata=payload["metadata"],
        user_id=user_id,
        team_id=team_id,
        revision_kind="create",
    )
    response = service_client.table(KNOWLEDGE_DOCUMENTS_TABLE).insert(payload).execute()
    if not response.data:
        raise ValueError("No se pudo registrar el documento institucional.")
    return response.data[0]


def process_knowledge_document(
    *,
    document_id: str,
    user_id: str,
    service_client: Any,
) -> dict[str, Any]:
    document_response = service_client.table(KNOWLEDGE_DOCUMENTS_TABLE) \
        .select("*") \
        .eq("id", document_id) \
        .eq("user_id", user_id) \
        .limit(1) \
        .execute()
    if not document_response.data:
        raise ValueError("No se encontró el documento institucional para indexar.")

    document_row = document_response.data[0]
    service_client.table(KNOWLEDGE_DOCUMENTS_TABLE).update({
        "status": "processing",
        "last_error": None,
        "processed_at": None,
    }).eq("id", document_id).execute()

    file_bytes = service_client.storage.from_(document_row["bucket_name"]).download(document_row["storage_path"])
    extracted_text, extraction_meta = extract_document_text(
        file_name=str(document_row.get("file_name") or ""),
        mime_type=document_row.get("mime_type"),
        file_bytes=file_bytes,
    )
    chunks = chunk_document_text(extracted_text)
    if not chunks:
        raise ValueError("No se pudieron generar fragmentos indexables del documento.")

    chunk_rows: list[dict[str, Any]] = []
    for chunk_index, chunk_text in enumerate(chunks):
        embedding = generate_embedding_vector(chunk_text)
        chunk_rows.append({
            "document_id": document_row["id"],
            "user_id": user_id,
            "team_id": document_row["team_id"],
            "document_title": document_row["title"],
            "document_file_name": document_row["file_name"],
            "chunk_index": chunk_index,
            "content": chunk_text,
            "embedding": embedding,
            "source_kind": document_row["source_kind"],
            "metadata": {
                "char_count": len(chunk_text),
                "estimated_tokens": _estimate_token_count(chunk_text),
            },
        })

    try:
        service_client.table(KNOWLEDGE_DOCUMENT_CHUNKS_TABLE).delete().eq("document_id", document_row["id"]).execute()
    except Exception:
        pass

    insert_response = service_client.table(KNOWLEDGE_DOCUMENT_CHUNKS_TABLE).insert(chunk_rows).execute()
    if insert_response.data is None and hasattr(insert_response, "error") and insert_response.error:
        raise ValueError(f"No se pudieron persistir los chunks vectoriales: {insert_response.error}")

    processed_metadata = {
        **_safe_dict(document_row.get("metadata")),
        "extraction": extraction_meta,
        "last_indexed_at": _now_iso(),
    }
    processed_metadata = build_document_governance_metadata(
        metadata=processed_metadata,
        user_id=user_id,
        team_id=str(document_row["team_id"]),
        revision_kind="index",
        increment_index_revision=True,
    )
    updated_response = service_client.table(KNOWLEDGE_DOCUMENTS_TABLE).update({
        "status": "indexed",
        "chunk_count": len(chunk_rows),
        "word_count": len(extracted_text.split()),
        "processed_at": _now_iso(),
        "metadata": processed_metadata,
        "last_error": None,
    }).eq("id", document_row["id"]).execute()
    updated_row = updated_response.data[0] if updated_response.data else {
        **document_row,
        "status": "indexed",
        "chunk_count": len(chunk_rows),
        "word_count": len(extracted_text.split()),
        "metadata": processed_metadata,
    }

    emit_structured_log(
        "knowledge_document_indexed",
        user_id=user_id,
        document_id=document_row["id"],
        title=document_row.get("title"),
        chunk_count=len(chunk_rows),
        source_kind=document_row.get("source_kind"),
    )
    return updated_row


def mark_knowledge_document_failed(
    *,
    document_id: str,
    error_message: str,
    service_client: Any,
) -> None:
    current_response = service_client.table(KNOWLEDGE_DOCUMENTS_TABLE) \
        .select("user_id, team_id, metadata") \
        .eq("id", document_id) \
        .limit(1) \
        .execute()
    current_row = current_response.data[0] if current_response.data else {}
    metadata = build_document_governance_metadata(
        metadata=_safe_dict(current_row.get("metadata")),
        user_id=str(current_row.get("user_id") or ""),
        team_id=str(current_row.get("team_id") or ""),
        revision_kind="index_error",
        increment_revision=True,
    ) if current_row else None

    payload = {
        "status": "error",
        "last_error": error_message[:500],
        "processed_at": _now_iso(),
    }
    if metadata:
        payload["metadata"] = metadata

    service_client.table(KNOWLEDGE_DOCUMENTS_TABLE).update(payload).eq("id", document_id).execute()


def list_knowledge_documents(
    *,
    user_id: str,
    team_id: str,
    service_client: Any,
) -> list[dict[str, Any]]:
    response = service_client.table(KNOWLEDGE_DOCUMENTS_TABLE) \
        .select("*") \
        .eq("user_id", user_id) \
        .eq("team_id", team_id) \
        .order("created_at", desc=True) \
        .execute()
    return response.data or []


def _fallback_semantic_search(
    *,
    user_id: str,
    team_id: str,
    query_embedding: list[float],
    service_client: Any,
    limit: int,
    document_ids: list[str] | None = None,
) -> list[KnowledgeSnippet]:
    response = service_client.table(KNOWLEDGE_DOCUMENT_CHUNKS_TABLE) \
        .select("*") \
        .eq("user_id", user_id) \
        .eq("team_id", team_id) \
        .limit(int(settings.KNOWLEDGE_FALLBACK_SCAN_LIMIT)) \
        .execute()

    candidates: list[KnowledgeSnippet] = []
    allowed_document_ids = set(document_ids or [])
    for row in response.data or []:
        if allowed_document_ids and str(row.get("document_id") or "") not in allowed_document_ids:
            continue
        content = _normalize_whitespace(str(row.get("content") or ""))
        if not content:
            continue
        similarity = _cosine_similarity(query_embedding, _parse_embedding(row.get("embedding")))
        candidates.append(KnowledgeSnippet(
            document_id=str(row.get("document_id") or ""),
            document_title=str(row.get("document_title") or "Documento institucional"),
            document_file_name=str(row.get("document_file_name") or ""),
            chunk_index=int(row.get("chunk_index") or 0),
            content=content,
            similarity=similarity,
            source_kind=str(row.get("source_kind") or "unknown"),
            metadata=_safe_dict(row.get("metadata")),
        ))

    candidates.sort(key=lambda item: item.similarity or 0.0, reverse=True)
    return candidates[:limit]


def search_knowledge_documents(
    *,
    user_id: str,
    team_id: str,
    query: str,
    service_client: Any,
    limit: int | None = None,
    document_ids: list[str] | None = None,
) -> list[KnowledgeSnippet]:
    normalized_query = _normalize_whitespace(query)
    if not normalized_query:
        return []

    effective_limit = max(1, min(int(limit or settings.KNOWLEDGE_DEFAULT_TOP_K), 12))
    query_embedding = generate_embedding_vector(normalized_query)
    document_filter = [str(document_id) for document_id in (document_ids or []) if document_id]

    try:
        rpc_payload = {
            "query_embedding": query_embedding,
            "match_count": effective_limit,
            "filter_user_id": user_id,
            "filter_team_id": team_id,
        }
        if document_filter:
            rpc_payload["filter_document_ids"] = document_filter
        rpc_response = service_client.rpc(KNOWLEDGE_MATCH_RPC, rpc_payload).execute()
        snippets: list[KnowledgeSnippet] = []
        for row in rpc_response.data or []:
            snippets.append(KnowledgeSnippet(
                document_id=str(row.get("document_id") or ""),
                document_title=str(row.get("document_title") or "Documento institucional"),
                document_file_name=str(row.get("document_file_name") or ""),
                chunk_index=int(row.get("chunk_index") or 0),
                content=_normalize_whitespace(str(row.get("content") or "")),
                similarity=float(row.get("similarity")) if row.get("similarity") is not None else None,
                source_kind=str(row.get("source_kind") or "unknown"),
                metadata=_safe_dict(row.get("metadata")),
            ))
        if snippets:
            return snippets[:effective_limit]
    except Exception as exc:
        emit_structured_log(
            "knowledge_semantic_search_fallback",
            level="warning",
            user_id=user_id,
            team_id=team_id,
            error=str(exc)[:240],
        )

    return _fallback_semantic_search(
        user_id=user_id,
        team_id=team_id,
        query_embedding=query_embedding,
        service_client=service_client,
        limit=effective_limit,
        document_ids=document_filter,
    )


def build_knowledge_context_block(snippets: list[KnowledgeSnippet], *, max_chars: int = 3500) -> str:
    if not snippets:
        return ""

    lines = [
        "CONTEXTO DOCUMENTAL INSTITUCIONAL DISPONIBLE:",
        "PRIORIDAD MÁXIMA: si este contexto contiene límites, umbrales, alertas, restricciones o acciones obligatorias aplicables a los datos analizados, debes cumplirlos literalmente.",
        "PROHIBIDO sustituir una acción institucional obligatoria por una recomendación genérica.",
    ]
    consumed_chars = 0
    for index, snippet in enumerate(snippets, start=1):
        similarity_text = f"{snippet.similarity:.3f}" if snippet.similarity is not None else "n/a"
        excerpt = snippet.content[:700].strip()
        block = (
            f"[FUENTE {index}] {snippet.document_title} "
            f"(archivo: {snippet.document_file_name or 'sin_nombre'}, fragmento {snippet.chunk_index}, similitud={similarity_text})\n"
            f"{excerpt}"
        )
        if consumed_chars + len(block) > max_chars and lines:
            break
        lines.append(block)
        consumed_chars += len(block)
    return "\n\n".join(lines).strip()
