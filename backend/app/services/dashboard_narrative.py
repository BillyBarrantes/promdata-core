from __future__ import annotations

import json
import re
from typing import Any

import google.generativeai as genai

from app.core.langfuse_client import record_llm_call

from app.core.config import settings
from app.core.structured_logging import emit_structured_log

genai.configure(api_key=settings.GEMINI_API_KEY)


def _normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _normalize_string_list(value: Any, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []

    normalized: list[str] = []
    for entry in value:
        text = _normalize_text(entry)
        if text and text not in normalized:
            normalized.append(text)
        if len(normalized) >= limit:
            break
    return normalized


def _build_widgets_context(widgets: list[dict[str, Any]], *, max_chars: int = 6000) -> str:
    blocks: list[str] = []
    consumed = 0

    for index, widget in enumerate(widgets, start=1):
        title = _normalize_text(widget.get("title")) or f"Widget {index}"
        widget_type = _normalize_text(widget.get("widget_type")) or "widget"
        visual_type = _normalize_text(widget.get("visual_type")) or widget_type
        metric = _normalize_text(widget.get("metric"))
        dimension = _normalize_text(widget.get("dimension"))
        aggregation = _normalize_text(widget.get("aggregation"))
        file_id = _normalize_text(widget.get("file_id")) or "sin_archivo"
        facts = _normalize_string_list(widget.get("facts"), limit=6)

        metadata_parts = [
            f"tipo={widget_type}",
            f"visual={visual_type}",
            f"file_id={file_id}",
        ]
        if metric:
            metadata_parts.append(f"metrica={metric}")
        if dimension:
            metadata_parts.append(f"dimension={dimension}")
        if aggregation:
            metadata_parts.append(f"agregacion={aggregation}")

        block_lines = [f"[WIDGET {index}] {title}", " | ".join(metadata_parts)]
        if facts:
            block_lines.extend(f"- {fact}" for fact in facts)

        block = "\n".join(block_lines)
        if consumed + len(block) > max_chars and blocks:
            break

        blocks.append(block)
        consumed += len(block)

    return "\n\n".join(blocks)


def _fallback_summary(
    *,
    presentation_name: str,
    filter_scope: list[str],
    widgets: list[dict[str, Any]],
) -> dict[str, Any]:
    widget_titles = [_normalize_text(widget.get("title")) for widget in widgets]
    overview_parts = [
        f"El lienzo {presentation_name or 'actual'} consolida {len(widgets)} widgets listos para lectura ejecutiva."
    ]
    if filter_scope:
        overview_parts.append(f"El análisis está acotado por los filtros activos: {', '.join(filter_scope)}.")

    findings: list[str] = []
    for widget in widgets[:3]:
        title = _normalize_text(widget.get("title")) or "Widget"
        facts = _normalize_string_list(widget.get("facts"), limit=1)
        if facts:
            findings.append(f"{title}: {facts[0]}")
        else:
            findings.append(f"{title}: visual disponible para revisión ejecutiva.")

    caveats: list[str] = []
    unique_file_ids = {
        _normalize_text(widget.get("file_id"))
        for widget in widgets
        if _normalize_text(widget.get("file_id"))
    }
    if len(unique_file_ids) > 1:
        caveats.append("El lienzo mezcla widgets de múltiples archivos; conviene interpretar comparaciones con ese contexto.")

    return {
        "headline": f"Resumen ejecutivo de {presentation_name or 'dashboard'}",
        "overview": " ".join(overview_parts),
        "key_findings": findings,
        "risks": [],
        "actions": [
            "Validar si los widgets principales cubren los KPIs prioritarios de la reunión.",
            "Usar los filtros activos como alcance explícito antes de presentar conclusiones."
        ],
        "caveats": caveats,
        "widget_count": len(widgets),
        "mixed_sources": len(unique_file_ids) > 1,
        "filter_scope": filter_scope,
    }


def generate_dashboard_executive_summary(
    *,
    presentation_name: str,
    global_filters: dict[str, str],
    widgets: list[dict[str, Any]],
) -> dict[str, Any]:
    normalized_presentation_name = _normalize_text(presentation_name) or "dashboard"
    filter_scope = [
        f"{_normalize_text(key)}={_normalize_text(value)}"
        for key, value in (global_filters or {}).items()
        if _normalize_text(key) and _normalize_text(value)
    ]
    normalized_widgets = [widget for widget in widgets if isinstance(widget, dict)]

    if not normalized_widgets:
        return _fallback_summary(
            presentation_name=normalized_presentation_name,
            filter_scope=filter_scope,
            widgets=[],
        )

    if not settings.GEMINI_API_KEY:
        return _fallback_summary(
            presentation_name=normalized_presentation_name,
            filter_scope=filter_scope,
            widgets=normalized_widgets,
        )

    unique_file_ids = {
        _normalize_text(widget.get("file_id"))
        for widget in normalized_widgets
        if _normalize_text(widget.get("file_id"))
    }
    mixed_sources = len(unique_file_ids) > 1
    widgets_context = _build_widgets_context(normalized_widgets)

    prompt = f"""
    ACTUA COMO DIRECTOR DE ANALISIS EJECUTIVO DE PROMDATA.

    Tu trabajo es sintetizar un dashboard ya calculado. No tienes acceso al dataset completo, solo a los hechos visibles por widget.
    Regla principal: usa exclusivamente la evidencia listada. Si algo no esta soportado por los widgets, no lo afirmes.

    CONTEXTO DEL LIENZO:
    - nombre: {normalized_presentation_name}
    - widgets: {len(normalized_widgets)}
    - mezcla_multiples_archivos: {"si" if mixed_sources else "no"}
    - filtros_activos: {", ".join(filter_scope) if filter_scope else "sin filtros activos"}

    WIDGETS DISPONIBLES:
    {widgets_context}

    DEVUELVE JSON VALIDO CON ESTA ESTRUCTURA:
    {{
      "headline": "titulo ejecutivo corto",
      "overview": "parrafo ejecutivo de maximo 70 palabras",
      "key_findings": ["hallazgo 1", "hallazgo 2", "hallazgo 3"],
      "risks": ["riesgo 1", "riesgo 2"],
      "actions": ["accion 1", "accion 2", "accion 3"],
      "caveats": ["limitacion 1", "limitacion 2"]
    }}

    REGLAS OBLIGATORIAS:
    - Escribe en espanol ejecutivo, directo y sin adornos.
    - Cada hallazgo debe referenciar al menos un widget o dato visible.
    - Si hay filtros activos, incorporalos en overview o caveats.
    - Si mezcla_multiples_archivos = si, debes advertirlo en caveats.
    - No inventes porcentajes, totales o tendencias que no esten explicitamente visibles.
    - Maximos: 3 hallazgos, 2 riesgos, 3 acciones, 2 caveats.
    """

    try:
        model = genai.GenerativeModel(
            model_name=settings.AI_MODEL_NAME,
            generation_config={
                "response_mime_type": "application/json",
                "temperature": 0.1,
            },
        )
        with record_llm_call(
            "dashboard_executive_summary",
            model_name=settings.AI_MODEL_NAME,
            prompt=prompt,
            trace_id=None,
            trace_name="dashboard_narrative",
            metadata={"presentation_name": normalized_presentation_name},
        ) as lf_span:
            response = model.generate_content(prompt)
            lf_span["output"] = response.text
        payload = json.loads(response.text)
    except Exception as exc:
        emit_structured_log(
            "dashboard_executive_summary_generation_error",
            level="warning",
            presentation_name=normalized_presentation_name,
            widget_count=len(normalized_widgets),
            error=str(exc)[:240],
        )
        return _fallback_summary(
            presentation_name=normalized_presentation_name,
            filter_scope=filter_scope,
            widgets=normalized_widgets,
        )

    result = {
        "headline": _normalize_text(payload.get("headline")) or f"Resumen ejecutivo de {normalized_presentation_name}",
        "overview": _normalize_text(payload.get("overview")) or f"El dashboard {normalized_presentation_name} contiene {len(normalized_widgets)} widgets ejecutivos.",
        "key_findings": _normalize_string_list(payload.get("key_findings"), limit=3),
        "risks": _normalize_string_list(payload.get("risks"), limit=2),
        "actions": _normalize_string_list(payload.get("actions"), limit=3),
        "caveats": _normalize_string_list(payload.get("caveats"), limit=2),
        "widget_count": len(normalized_widgets),
        "mixed_sources": mixed_sources,
        "filter_scope": filter_scope,
    }

    if mixed_sources and not any("archivo" in caveat.lower() for caveat in result["caveats"]):
        result["caveats"].append("El lienzo combina widgets de múltiples archivos; interpreta comparaciones con ese alcance.")

    if filter_scope and not result["overview"]:
        result["overview"] = f"El análisis está acotado por {', '.join(filter_scope)}."

    emit_structured_log(
        "dashboard_executive_summary_generated",
        presentation_name=normalized_presentation_name,
        widget_count=len(normalized_widgets),
        filter_count=len(filter_scope),
        mixed_sources=mixed_sources,
    )
    return result
