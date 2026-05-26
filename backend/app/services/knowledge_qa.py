from __future__ import annotations

import json
import re
from typing import Any

from app.core.gemini_client import genai

from app.core.config import settings
from app.core.structured_logging import emit_structured_log
from app.services.document_rag import KnowledgeSnippet

genai.configure(api_key=settings.GEMINI_API_KEY)


def _normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _source_id(index: int) -> str:
    return f"FUENTE {index}"


def _build_source_payloads(
    snippets: list[KnowledgeSnippet],
    *,
    max_chars: int = 4200,
) -> tuple[str, dict[str, dict[str, Any]]]:
    lines = [
        "FUENTES DOCUMENTALES DISPONIBLES:",
        "Responde solo con evidencia contenida en estas fuentes.",
    ]
    source_map: dict[str, dict[str, Any]] = {}
    consumed_chars = 0

    for index, snippet in enumerate(snippets, start=1):
        source_id = _source_id(index)
        excerpt = _normalize_whitespace(snippet.content)[:900]
        similarity_text = f"{snippet.similarity:.3f}" if snippet.similarity is not None else "n/a"
        block = (
            f"[{source_id}] titulo={snippet.document_title} | archivo={snippet.document_file_name or 'sin_nombre'} "
            f"| chunk={snippet.chunk_index} | similitud={similarity_text}\n"
            f"{excerpt}"
        )
        if consumed_chars + len(block) > max_chars and source_map:
            break

        lines.append(block)
        source_map[source_id] = {
            "source_id": source_id,
            "document_id": snippet.document_id,
            "document_title": snippet.document_title,
            "document_file_name": snippet.document_file_name,
            "chunk_index": snippet.chunk_index,
            "snippet": excerpt,
            "similarity": snippet.similarity,
            "source_kind": snippet.source_kind,
            "metadata": snippet.metadata,
        }
        consumed_chars += len(block)

    return "\n\n".join(lines).strip(), source_map


def _normalize_citation_ids(raw_ids: Any, *, allowed_ids: set[str]) -> list[str]:
    if not isinstance(raw_ids, list):
        return []

    normalized_ids: list[str] = []
    for value in raw_ids:
        raw_text = _normalize_whitespace(str(value or "")).upper()
        match = re.search(r"FUENTE\s*(\d+)", raw_text)
        if not match:
            continue
        candidate = _source_id(int(match.group(1)))
        if candidate in allowed_ids and candidate not in normalized_ids:
            normalized_ids.append(candidate)
    return normalized_ids


def _fallback_insufficient_evidence(*, question: str, retrieved_count: int) -> dict[str, Any]:
    return {
        "question": question,
        "answer": "No encontré evidencia suficiente en los documentos disponibles para responder esa pregunta.",
        "citations": [],
        "snippets_used": 0,
        "retrieved_count": retrieved_count,
        "grounded": False,
        "insufficient_evidence": True,
    }


def answer_knowledge_question(
    *,
    question: str,
    snippets: list[KnowledgeSnippet],
) -> dict[str, Any]:
    normalized_question = _normalize_whitespace(question)
    retrieved_count = len(snippets)
    if not normalized_question:
        return _fallback_insufficient_evidence(question="", retrieved_count=retrieved_count)
    if not snippets:
        return _fallback_insufficient_evidence(question=normalized_question, retrieved_count=0)

    context_block, source_map = _build_source_payloads(snippets)
    if not source_map:
        return _fallback_insufficient_evidence(
            question=normalized_question,
            retrieved_count=retrieved_count,
        )

    prompt = f"""
    ERES EL CEREBRO DOCUMENTAL DE PROMDATA.

    TAREA:
    - Responde la pregunta del usuario usando SOLO la evidencia contenida en las fuentes documentales disponibles.
    - NO inventes datos.
    - NO uses conocimiento externo.
    - Si las fuentes no contienen evidencia suficiente o explícita para contestar, responde que no encontraste evidencia suficiente.

    REGLAS DE RESPUESTA:
    - Devuelve una respuesta breve, directa y profesional en español.
    - Si afirmas algo factual, debes respaldarlo con al menos una cita.
    - Las citas válidas son únicamente: {", ".join(source_map.keys())}.
    - No cites fuentes inexistentes.
    - Si no hay evidencia suficiente, "insufficient_evidence" debe ser true y "citation_ids" debe ser [].

    FORMATO JSON OBLIGATORIO:
    {{
      "answer": "respuesta final en español",
      "citation_ids": ["FUENTE 1", "FUENTE 2"],
      "insufficient_evidence": false
    }}

    PREGUNTA DEL USUARIO:
    {normalized_question}

    {context_block}
    """

    try:
        model = genai.GenerativeModel(
            model_name=settings.AI_MODEL_NAME,
            generation_config={
                "response_mime_type": "application/json",
                "temperature": 0.0,
            },
        )
        response = model.generate_content(prompt)
        payload = json.loads(response.text)
    except Exception as exc:
        emit_structured_log(
            "knowledge_ask_generation_error",
            level="error",
            question_preview=normalized_question[:160],
            retrieved_count=retrieved_count,
            error=str(exc)[:240],
        )
        raise

    answer = _normalize_whitespace(str(payload.get("answer") or ""))
    insufficient_evidence = bool(payload.get("insufficient_evidence"))
    citation_ids = _normalize_citation_ids(
        payload.get("citation_ids"),
        allowed_ids=set(source_map.keys()),
    )

    if insufficient_evidence or not answer:
        result = _fallback_insufficient_evidence(
            question=normalized_question,
            retrieved_count=retrieved_count,
        )
        emit_structured_log(
            "knowledge_ask_insufficient_evidence",
            question_preview=normalized_question[:160],
            retrieved_count=retrieved_count,
        )
        return result

    if not citation_ids:
        result = _fallback_insufficient_evidence(
            question=normalized_question,
            retrieved_count=retrieved_count,
        )
        emit_structured_log(
            "knowledge_ask_missing_citations",
            level="warning",
            question_preview=normalized_question[:160],
            retrieved_count=retrieved_count,
        )
        return result

    citations = [source_map[citation_id] for citation_id in citation_ids]
    grounded = bool(citations)

    emit_structured_log(
        "knowledge_ask_generated",
        question_preview=normalized_question[:160],
        retrieved_count=retrieved_count,
        citations_count=len(citations),
        grounded=grounded,
    )

    return {
        "question": normalized_question,
        "answer": answer,
        "citations": citations,
        "snippets_used": len(citations),
        "retrieved_count": retrieved_count,
        "grounded": grounded,
        "insufficient_evidence": False,
    }
