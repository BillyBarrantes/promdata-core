import json
from typing import Any

from app.core.config import settings
from app.core.gemini_client import genai
from app.core.langfuse_client import record_llm_call
from app.core.structured_logging import emit_structured_log
from app.services.ai_response_cache import build_cache_key, get_cached_json, set_cached_json
from app.services.semantic_translator.core import normalize_surface_text
from app.services.semantic_translator.validator import (
    normalize_semantic_router_decision,
    normalize_router_semantic_contract,
    parse_translator_payload,
    schema_fingerprint,
)


def route_prompt_with_semantic_router(
    prompt: str,
    columns: list[str],
    schema_profile: dict | None = None,
    dataset_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    surface_prompt = normalize_surface_text(prompt)
    schema_fp = schema_fingerprint(columns, schema_profile=schema_profile, dataset_contract=dataset_contract)
    router_cache_key = build_cache_key(
        "semantic_router",
        {"prompt": surface_prompt, "schema_fingerprint": schema_fp},
    )
    cached_decision = get_cached_json("semantic_router", router_cache_key)
    if isinstance(cached_decision, dict):
        normalized_cached_decision = normalize_semantic_router_decision(cached_decision)
        _contract_file_id = (
            str((dataset_contract or {}).get("file_id") or "").strip()
            if isinstance(dataset_contract, dict)
            else ""
        )
        emit_structured_log(
            "semantic_router_cache_hit",
            route=normalized_cached_decision.get("route"),
            confidence=normalized_cached_decision.get("confidence"),
            detected_intent=normalized_cached_decision.get("detected_intent"),
            prompt=str(surface_prompt)[:180],
            file_id=_contract_file_id or None,
            cache_key_prefix=router_cache_key[:16],
        )
        return normalized_cached_decision

    router_schema = {
        "route": "SIMPLE|COMPLEJO",
        "confidence": "float 0.0-1.0",
        "detected_intent": "trend|distribution|descriptive|diagnostic|predictive|unknown",
        "requires_time": "boolean",
        "reason_codes": "list[str]",
        "semantic_contract": {
            "intent": "trend|distribution|descriptive|diagnostic|predictive|unknown",
            "metric": "métrica principal si no hay diferencia entre ranking y gráfico",
            "plot_metric": "métrica que el usuario quiere ver/graficar",
            "ranking_metric": "métrica usada para ordenar o seleccionar Top N; null si es igual a plot_metric",
            "ranking_direction": "desc|asc",
            "time_axis": "nombre o concepto del eje temporal; null si no aplica",
            "dimension": "nombre o concepto de dimensión; null para totales globales",
            "group_by": "list[str] dimensiones adicionales solicitadas",
            "positive_filters": "list[{column, operator, value}] para filtros inclusivos",
            "negative_filters": "list[{column, operator, value}] para exclusiones explícitas",
            "top_n": "integer|null",
            "series_mode": "split|sum|none",
            "grain": "month|week|day|quarter|year|null",
            "aggregation": "sum|avg|count|min|max",
            "visual_protocol": "line_chart|bar_chart|pie_chart|treemap|kpi|null",
            "requires_time": "boolean",
        },
    }
    router_instruction = f"""
    ERES EL ROUTER SEMÁNTICO DE PROMDATA.
    Tu única tarea es clasificar el riesgo de interpretación del prompt.
    No analices datos, no generes planes y no expliques fuera del JSON.

    Devuelve JSON estricto compatible con:
    {json.dumps(router_schema, ensure_ascii=False)}

    Usa route="SIMPLE" solo si la intención humana es única, directa y puede resolverse con un plan determinístico.
    Usa route="COMPLEJO" si hay negaciones, exclusiones, instrucciones de separación/consolidación,
    señales mixtas, múltiples vistas, ambigüedad, filtros compuestos, ranking por métrica diferente,
    causa raíz o baja confianza.
    Ante duda, route="COMPLEJO".
    Siempre llena semantic_contract. Si el usuario pide un total global por tiempo, dimension=null y series_mode="none".
    Si pide Top N con una línea por elemento, series_mode="split". Si pide Top N consolidado, series_mode="sum".
    Si el usuario pide "graficar X pero ordenar por Y", usa plot_metric=X y ranking_metric=Y.
    Si el usuario excluye valores, usa negative_filters con operator="not_in" o "!=".

    IMPORTANTE — FORMATO DE FECHAS EN FILTROS:
    Los valores de filtros en columnas de fecha/timestamp DEBEN ser fechas ISO
    (YYYY-MM-DD) o expresiones numéricas. NUNCA uses nombres de meses ni palabras
    sueltas como valor de filtro temporal.
    Correcto: "value": "2021-06-01"  |  Incorrecto: "value": "junio"
    Si el usuario dice "junio y julio", traduce a: operator ">=" value "YYYY-06-01"
    y un segundo filtro operator "<=" value "YYYY-07-31".
    INSTRUCCIÓN CRÍTICA — AÑO EN FECHAS: El marcador YYYY debe reemplazarse
    con FECHA_REFERENCIA_DATASET si existe en la topología del análisis,
    o inferirse del rango de fechas real del dataset. Si no hay información
    del año del dataset, usa el año de los datos que ves en el contexto.

    COLUMNAS: {list(columns or [])}
    SCHEMA_FINGERPRINT: {schema_fp}
    """
    try:
        model = genai.GenerativeModel(
            model_name=settings.NARRATIVE_FAST_MODEL_NAME,
            generation_config={"response_mime_type": "application/json", "temperature": 0.0},
        )
        _router_input = f"{router_instruction}\n\nPROMPT: {prompt}"
        with record_llm_call(
            "semantic_routing",
            model_name=settings.NARRATIVE_FAST_MODEL_NAME,
            prompt=_router_input,
            trace_id=None,
            trace_name="semantic_router",
        ) as lf_span:
            response = model.generate_content(_router_input)
            lf_span["output"] = response.text
        parsed_decision = parse_translator_payload(response.text.strip())
        normalized_decision = normalize_semantic_router_decision(parsed_decision)
        set_cached_json(
            "semantic_router",
            router_cache_key,
            normalized_decision,
            settings.SEMANTIC_TRANSLATOR_CACHE_TTL_SECONDS,
        )
        emit_structured_log(
            "semantic_router_decision",
            route=normalized_decision.get("route"),
            original_route=normalized_decision.get("original_route"),
            confidence=normalized_decision.get("confidence"),
            detected_intent=normalized_decision.get("detected_intent"),
            requires_time=normalized_decision.get("requires_time"),
            reason_codes=normalized_decision.get("reason_codes"),
        )
        return normalized_decision
    except Exception as router_error:
        emit_structured_log(
            "semantic_router_error",
            level="warning",
            error=str(router_error)[:200],
        )
        return {
            "route": "COMPLEJO",
            "confidence": 0.0,
            "detected_intent": "unknown",
            "requires_time": False,
            "reason_codes": ["router_error", "conservative_policy"],
            "original_route": "COMPLEJO",
        }
