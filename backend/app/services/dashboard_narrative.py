from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

from app.core.gemini_client import genai

from app.core.langfuse_client import record_llm_call

from app.core.config import settings
from app.core.llm_providers.readiness import active_llm_model, llm_provider_ready
from app.core.structured_logging import emit_structured_log
from app.services.ai_response_cache import build_cache_key, get_cached_json, set_cached_json


def _collect_widget_numbers(widgets: list[dict[str, Any]]) -> list[float]:
    """[F1.5] Extrae las cifras reales de los facts de los widgets para el árbitro numérico."""
    from app.services.narrative_reconciliation_guard import _NUM_PATTERN, parse_numeric_token

    numbers: list[float] = []
    for widget in widgets:
        for fact in _normalize_string_list(widget.get("facts"), limit=12):
            for match in _NUM_PATTERN.finditer(fact):
                value = parse_numeric_token(match)
                if value is not None:
                    numbers.append(value)
    return numbers


def _reconcile_executive_summary(
    payload: dict[str, Any],
    widgets: list[dict[str, Any]],
) -> dict[str, Any]:
    """[F1.5] Audita las cifras del resumen ejecutivo contra los datos computados.

    Si el LLM cita cifras no respaldadas por los widgets, agrega una salvedad
    (respetando el máximo de 2 caveats) para no presentarlas como verdad.
    """
    from app.services.narrative_reconciliation_guard import reconcile_narrative_with_facts

    fact_numbers = _collect_widget_numbers(widgets)
    if not fact_numbers:
        return payload

    narrative_text = " ".join(
        [str(payload.get("headline") or ""), str(payload.get("overview") or "")]
        + [str(item) for item in (payload.get("key_findings") or [])]
        + [str(item) for item in (payload.get("actions") or [])]
        + [str(item) for item in (payload.get("risks") or [])]
        + [str(item) for item in (payload.get("caveats") or [])]
    )
    _, unverified = reconcile_narrative_with_facts(
        narrative_text, computed_facts={"facts": fact_numbers}
    )
    if not unverified:
        return payload

    caveats = [str(item) for item in (payload.get("caveats") or [])]
    note = (
        "Algunas cifras secundarias no pudieron verificarse contra los datos "
        "calculados y deben tomarse con cautela."
    )
    if note not in caveats and len(caveats) < 2:
        caveats.append(note)
    return {**payload, "caveats": caveats}



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


_STOPWORDS_ES = {
    "de", "del", "la", "el", "los", "las", "un", "una", "unos", "unas", "en", "y",
    "o", "a", "al", "con", "por", "para", "se", "su", "sus", "es", "son", "que",
    "como", "mas", "menos", "muy", "the", "of", "and", "to", "in",
}


def _content_token_set(text: str) -> set[str]:
    normalized = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().lower()
    return {
        token for token in re.findall(r"[a-z0-9]+", normalized)
        if token not in _STOPWORDS_ES and len(token) > 2
    }


def _drop_redundant_findings(
    findings: list[str],
    overview: str,
    *,
    similarity_threshold: float = 0.8,
) -> list[str]:
    """[B1 2026-09] Red de seguridad determinista contra la repetición
    overview ↔ hallazgos.

    Descarta un hallazgo si es un duplicado de facto del overview: substring
    en cualquier dirección, o alta similitud de tokens (Jaccard) / contención.
    Conservador por diseño: NUNCA elimina contenido con vocabulario nuevo."""
    overview_text = _normalize_text(overview).lower()
    if not overview_text:
        return findings
    overview_tokens = _content_token_set(overview)
    kept: list[str] = []
    for finding in findings:
        finding_text = _normalize_text(finding).lower()
        if not finding_text:
            continue
        if finding_text in overview_text or overview_text in finding_text:
            continue
        finding_tokens = _content_token_set(finding)
        if not finding_tokens:
            kept.append(finding)
            continue
        intersection = finding_tokens & overview_tokens
        union = finding_tokens | overview_tokens
        jaccard = len(intersection) / len(union) if union else 0.0
        containment = len(intersection) / len(finding_tokens)
        if jaccard >= similarity_threshold or containment >= 0.9:
            continue
        kept.append(finding)
    return kept


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
    # [MEJORA 3 2026-09] Honestidad ante resultado vacío: no presentar un
    # resumen genérico como si hubiera datos. Explicar el alcance y pedir
    # ajustar filtros, en vez de fabricar hallazgos/tendencias.
    if not widgets:
        overview = "No se encontraron registros que cumplan el alcance solicitado."
        if filter_scope:
            overview += f" Filtros aplicados: {', '.join(filter_scope)}."
        return {
            "headline": f"Sin datos para {presentation_name or 'el análisis'}",
            "overview": overview,
            "key_findings": [],
            "risks": [],
            "actions": [
                "Ajustar o limpiar los filtros activos y volver a ejecutar el análisis."
            ],
            "caveats": [
                "No hay filas que cumplan el filtro; evita conclusiones sobre "
                "tendencias o caídas con este alcance."
            ],
            "widget_count": 0,
            "mixed_sources": False,
            "filter_scope": filter_scope,
        }

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


def _select_summary_models() -> tuple[str, str | None]:
    primary_model = (
        _normalize_text(settings.NARRATIVE_FAST_MODEL_NAME)
        or _normalize_text(settings.AI_MODEL_NAME)
        or active_llm_model()
    )
    strict_model = _normalize_text(settings.NARRATIVE_STRICT_MODEL_NAME)
    fallback_model = strict_model if strict_model and strict_model != primary_model else None
    return primary_model, fallback_model


def _build_summary_result(
    *,
    payload: dict[str, Any],
    presentation_name: str,
    widget_count: int,
    mixed_sources: bool,
    filter_scope: list[str],
) -> dict[str, Any]:
    overview = _normalize_text(payload.get("overview")) or f"El dashboard {presentation_name} contiene {widget_count} widgets ejecutivos."
    # [B1 2026-09] Elimina hallazgos que solo repiten el overview.
    findings = _drop_redundant_findings(
        _normalize_string_list(payload.get("key_findings"), limit=3),
        overview,
    )
    result = {
        "headline": _normalize_text(payload.get("headline")) or f"Resumen ejecutivo de {presentation_name}",
        "overview": overview,
        "key_findings": findings,
        "risks": _normalize_string_list(payload.get("risks"), limit=2),
        "actions": _normalize_string_list(payload.get("actions"), limit=3),
        "caveats": _normalize_string_list(payload.get("caveats"), limit=2),
        "widget_count": widget_count,
        "mixed_sources": mixed_sources,
        "filter_scope": filter_scope,
    }

    if mixed_sources and not any("archivo" in caveat.lower() for caveat in result["caveats"]):
        result["caveats"].append("El lienzo combina widgets de múltiples archivos; interpreta comparaciones con ese alcance.")

    if filter_scope and not result["overview"]:
        result["overview"] = f"El análisis está acotado por {', '.join(filter_scope)}."
    return result


def generate_dashboard_executive_summary(
    *,
    presentation_name: str,
    global_filters: dict[str, str],
    widgets: list[dict[str, Any]],
    data_notes: str | None = None,
) -> dict[str, Any]:
    normalized_presentation_name = _normalize_text(presentation_name) or "dashboard"
    filter_scope = [
        f"{_normalize_text(key)}={_normalize_text(value)}"
        for key, value in (global_filters or {}).items()
        if _normalize_text(key) and _normalize_text(value)
    ]
    data_notes_block = f"\n    NOTAS SOBRE LOS DATOS:\n    {data_notes}\n" if data_notes else ""
    normalized_widgets = [widget for widget in widgets if isinstance(widget, dict)]

    if not normalized_widgets:
        return _fallback_summary(
            presentation_name=normalized_presentation_name,
            filter_scope=filter_scope,
            widgets=[],
        )

    if not llm_provider_ready():
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
    cache_key = build_cache_key(
        "dashboard_executive_summary",
        {
            "presentation_name": normalized_presentation_name,
            "filter_scope": filter_scope,
            "mixed_sources": mixed_sources,
            "widgets_context": widgets_context,
            "widget_count": len(normalized_widgets),
            "data_notes": data_notes,
        },
    )
    cached_summary = get_cached_json("dashboard_executive_summary", cache_key)
    if isinstance(cached_summary, dict) and _normalize_text(cached_summary.get("headline")):
        emit_structured_log(
            "dashboard_executive_summary_cache_hit",
            presentation_name=normalized_presentation_name,
            widget_count=len(normalized_widgets),
            filter_count=len(filter_scope),
            mixed_sources=mixed_sources,
            file_ids=sorted(unique_file_ids)[:5],
            cache_key_prefix=cache_key[:16],
        )
        return _build_summary_result(
            payload=cached_summary,
            presentation_name=normalized_presentation_name,
            widget_count=len(normalized_widgets),
            mixed_sources=mixed_sources,
            filter_scope=filter_scope,
        )

    prompt = f"""
    ACTUA COMO DIRECTOR DE ANALISIS EJECUTIVO DE PROMDATA.

    Tu trabajo es sintetizar un dashboard ya calculado. No tienes acceso al dataset completo, solo a los hechos visibles por widget.
    Regla principal: usa exclusivamente la evidencia listada. Si algo no esta soportado por los widgets, no lo afirmes.

    CONTEXTO DEL LIENZO:
    - nombre: {normalized_presentation_name}
    - widgets: {len(normalized_widgets)}
    - mezcla_multiples_archivos: {"si" if mixed_sources else "no"}
    - filtros_activos: {", ".join(filter_scope) if filter_scope else "sin filtros activos"}{data_notes_block}

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
    - Cada hallazgo debe estar respaldado por un dato visible, pero NUNCA uses la palabra "widget". Describe el hallazgo directamente: "Al examinar la evolucion temporal...", "El desglose por departamento revela...", "La concentracion de salarios refleja...".
    - PROHIBIDO usar frases como "segun el widget de...", "en el widget 1...", "de acuerdo con el widget de distribucion". El lector no debe percibir que un sistema automatizado genero el texto.
    - Si hay filtros activos, incorporalos en overview o caveats.
    - Si mezcla_multiples_archivos = si, debes advertirlo en caveats.
    - Si las NOTAS SOBRE LOS DATOS indican flujos opuestos (ej: Ingreso vs Egreso), NUNCA sumes ambos valores en la narrativa. Reporta cada flujo por separado o menciona ambos explicitamente.
    - AGREGACION (obligatorio): revisa el campo "agregacion" de cada widget. Si un widget se agrega como "avg" (promedio por registro, indices, tasas, porcentajes, ratios), NUNCA lo sumes entre periodos ni lo presentes como un total acumulado: describelo como promedio y aclara que NO es aditivo. Solo suma valores cuyo "agregacion" sea "sum". No mezcles un promedio con un total como si fueran comparables.
    - SERIE ACUMULADA (obligatorio): si el "agregacion" de un widget es "max" o "min", la metrica es una serie acumulada (running total) y cada punto es el valor acumulado hasta esa fecha. NUNCA sumes esos valores entre periodos (re-contaria) ni los presentes como crecimiento multiplicativo; reporta el ULTIMO valor como el acumulado real a la fecha de corte.
    - No inventes porcentajes, totales o tendencias que no esten explicitamente visibles.
    - Maximos: 3 hallazgos, 2 riesgos, 3 acciones, 2 caveats.
    - SEPARACION DE ROLES (obligatorio): el "overview" da la lectura estrategica de
      alto nivel (que paso y por que importa). Los "key_findings" aportan evidencia
      cuantitativa ESPECIFICA y NUEVA. PROHIBIDO que un hallazgo reformule el overview
      o repita una cifra ya dicha en el overview: si solo la reformula, reemplazalo por
      un dato distinto (otra dimension, comparacion entre periodos, concentracion,
      outlier, distribucion). Cada hallazgo debe agregar informacion que el overview
      no contiene.
    - COMPARACION ENTRE PERIODOS: Si algun widget contiene metadatos de comparacion
      (comparison.year_from, comparison.year_to, comparison.total_variation):
      1) Menciona EXPLICITAMENTE los dos periodos comparados en el overview
         (ej: "El analisis compara los datos de 2024 contra 2025").
      2) Si hay comparison.total_variation, reporta la variacion total neta numerica.
      3) Si hay comparison.positive_changes y comparison.negative_changes,
         menciona cuantas entidades aumentaron y cuantas disminuyeron.
      4) NO uses lenguaje generico; cada hallazgo debe describir el comportamiento
         especifico de los datos entre los periodos comparados.
      5) Si hay comparison.base_total y comparison.compared_total,
         menciona los valores absolutos de cada periodo para dar contexto.
    """

    payload: dict[str, Any] | None = None
    primary_model, fallback_model = _select_summary_models()
    model_candidates = [primary_model] + ([fallback_model] if fallback_model else [])
    generation_error: Exception | None = None

    for model_name in model_candidates:
        try:
            model = genai.GenerativeModel(
                model_name=model_name,
                generation_config={
                    "response_mime_type": "application/json",
                    "temperature": 0.1,
                },
            )
            with record_llm_call(
                "dashboard_executive_summary",
                model_name=model_name,
                prompt=prompt,
                trace_id=None,
                trace_name="dashboard_narrative",
                metadata={"presentation_name": normalized_presentation_name},
            ) as lf_span:
                response = model.generate_content(prompt)
                lf_span["output"] = response.text
            payload = json.loads(response.text)
            emit_structured_log(
                "dashboard_executive_summary_model_used",
                presentation_name=normalized_presentation_name,
                model_name=model_name,
                is_fallback=model_name != primary_model,
            )
            break
        except Exception as exc:
            generation_error = exc
            emit_structured_log(
                "dashboard_executive_summary_model_error",
                level="warning",
                presentation_name=normalized_presentation_name,
                model_name=model_name,
                is_fallback=model_name != primary_model,
                error=str(exc)[:240],
            )

    if not isinstance(payload, dict):
        emit_structured_log(
            "dashboard_executive_summary_generation_error",
            level="warning",
            presentation_name=normalized_presentation_name,
            widget_count=len(normalized_widgets),
            error=str(generation_error)[:240] if generation_error else "unknown",
        )
        return _fallback_summary(
            presentation_name=normalized_presentation_name,
            filter_scope=filter_scope,
            widgets=normalized_widgets,
        )

    payload = _reconcile_executive_summary(payload, normalized_widgets)

    result = _build_summary_result(
        payload=payload,
        presentation_name=normalized_presentation_name,
        widget_count=len(normalized_widgets),
        mixed_sources=mixed_sources,
        filter_scope=filter_scope,
    )
    set_cached_json(
        "dashboard_executive_summary",
        cache_key,
        result,
        settings.NARRATIVE_CACHE_TTL_SECONDS,
    )

    emit_structured_log(
        "dashboard_executive_summary_generated",
        presentation_name=normalized_presentation_name,
        widget_count=len(normalized_widgets),
        filter_count=len(filter_scope),
        mixed_sources=mixed_sources,
    )
    return result
