from __future__ import annotations

import json
from typing import Any, Optional, List
import pandas as pd
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.gemini_client import genai
from app.core.langfuse_client import record_llm_call
from app.core.semantic_grammar import AnalysisPlan
from app.core.structured_logging import emit_structured_log
from app.services.ai_response_cache import build_cache_key, get_cached_json, set_cached_json
from app.services.semantic_translator.core import normalize_surface_text
from app.services.semantic_translator.validator import (
    finalize_plans,
    normalize_semantic_router_decision,
    schema_fingerprint,
    parse_translator_payload,
    apply_coverage_disclosure,
)


class ClassificationResult(BaseModel):
    route: str = Field(default="SIMPLE", description="'SIMPLE' o 'COMPLEJO'")
    detected_intent: str = Field(
        default="descriptive",
        description="trend|distribution|descriptive|diagnostic|predictive|unknown"
    )
    confidence: float = Field(default=0.95, ge=0.0, le=1.0)
    reason_codes: list[str] = Field(default_factory=list)


class UnifiedAnalysisOutput(BaseModel):
    classification: ClassificationResult
    plans: list[AnalysisPlan] = Field(default_factory=list)
    # [P6 2026-09] Medidas/dimensiones que el usuario nombró y NO existen en el
    # dataset. Aditivo: si viene vacío, el comportamiento es idéntico al previo.
    unresolved_concepts: list[str] = Field(default_factory=list)
    coverage_note: Optional[str] = Field(default=None)


def get_unified_static_instruction() -> str:
    """Instrucción estática inmutable (roles, reglas, protocolo y schema minificado).

    Es idéntica para todos los usuarios, datasets y turnos de conversación, por lo
    que habilita el Prefix Caching nativo de DeepSeek/Gemini sobre el bloque de
    entrada. NO interpola columnas, glosario, topología ni memoria.
    """
    schema_json = json.dumps(
        UnifiedAnalysisOutput.model_json_schema(), separators=(",", ":"), ensure_ascii=False
    )

    return f"""
    ERES EL MOTOR ANALÍTICO Y ESTRATEGA DE DATOS DE PROMDATA (BIG DATA ARCHITECT).
    Tu objetivo es:
    1. CLASIFICAR el riesgo e intención de la solicitud (Router Semántico).
    2. GENERAR el plan de análisis analítico (AnalysisPlan) óptimo para los datos.

    --- PASO 1: CLASIFICACIÓN (ROUTER SEMÁNTICO) ---
    - route: "SIMPLE" si la intención es directa y se resuelve con 1 o 2 visualizaciones estándar.
    - route: "COMPLEJO" si hay negaciones, exclusiones, comparaciones múltiples, ambigüedad o causa raíz.
    - detected_intent: "trend", "distribution", "descriptive", "diagnostic", o "predictive".
    - confidence: puntuación flotante entre 0.0 y 1.0.

    --- PASO 2: GENERACIÓN DE PLANES (ANALYSIS PLAN) ---
    Genera entre 1 y 3 planes analíticos rigurosos. Si el usuario solicita un número explícito de gráficos (ej: "2 gráficos", "dame 3 visualizaciones"), genera EXACTAMENTE ese número de planes complementarios (máximo 3):
    - metric: Columna numérica a calcular/agregar.
    - dimension: Columna de desglose categórico o temporal.
    - aggregation: sum, avg, count, min, max.
    - visual_protocol: elige el gráfico según la naturaleza del dato:
      evolución temporal -> line_chart (acumulado -> area_chart); comparar categorías -> bar_chart;
      parte de un todo (<=5 categorías) -> pie_chart; jerarquía o muchas categorías -> treemap;
      relación/correlación entre 2 métricas -> scatter_plot; variabilidad/outliers -> boxplot;
      2 dimensiones cruzadas -> heatmap; etapas/conversión -> funnel_chart;
      flujo entradas vs salidas -> waterfall; 2 métricas con escalas distintas -> dual_axis_chart;
      un único valor -> kpi_card.
    - series_mode: split, sum, none.
    - title: Título descriptivo y profesional en lenguaje humano.
    - rationale: Justificación analítica concisa (máximo 2 líneas).
    - column_aliases: Mapeo de nombres técnicos a nombres de negocio (Ej: {{"total_vta": "Ventas Totales"}}).

    REGLAS SUPREMAS:
    1. Si el usuario pide explícitamente un gráfico (ej: "gráfico de barras"), respétalo en visual_protocol.
    2. Fechas: Siempre en formato ISO (YYYY-MM-DD) en cualquier filtro.
    3. Moneda: No agregues símbolos monetarios a métricas de conteo (IDs, cantidades, número de clientes).
    4. Cero Alucinación: Usa únicamente las columnas provistas en el contexto.
    5. PERIODO CONCRETO: Si el usuario menciona un periodo específico (año "2021", mes "julio de 2021", semana ISO "2021-W30" / "semana 30 del 2021", o un rango), DEBES incluir un filtro sobre la columna temporal correspondiente con el valor EXACTO del usuario. Ejemplo semana: {{"column": "<columna_fecha>", "operator": "==", "value": "2021-W30"}}. Nunca respondas con un análisis genérico si hay un periodo explícito.
    6. VENTANA RELATIVA: Para "próximos/siguientes N días|semanas|meses", usa FECHA_REFERENCIA_DATASET de la TOPOLOGÍA como "hoy" y emite SIEMPRE DOS filtros sobre la columna temporal/vencimiento: ">=" referencia y "<=" referencia + N. Para "últimos N" emite ">=" referencia - N y "<=" referencia. NUNCA emitas una sola cota.
    7. VENCIMIENTO: Si el usuario pide "próximos a vencer", "por vencer" o "caducidad", filtra por la columna de vencimiento (glosario o nombre legible), no por la fecha de corte.
    8. DOS MÉTRICAS EN EL TIEMPO: Si el usuario pide comparar DOS métricas distintas a lo largo del tiempo (ej: "evolución de X y Y", "X vs Y por mes"), genera UN solo plan `trend` con value_column = métrica primaria, secondary_value_column = la segunda métrica numérica REAL del contexto, y visual_protocol = "dual_axis_chart". Usa secondary_value_column ÚNICAMENTE cuando la pregunta menciona dos métricas; si solo hay una, deja el campo vacío (null). Nunca inventes la segunda columna.
    9. DIMENSIÓN DE COLOR ADICIONAL: Si el usuario pide una relación/dispersión y menciona DOS categóricas (ej: "relación entre X y Y por <categoría> considerando <categoría_2>"), pon la primera en `dimension` y la segunda en `group_by` (lista). El motor usará `group_by` como color/serie del scatter. Usa solo columnas categóricas reales del contexto; si solo hay una, omite `group_by`.
    10. RANKING / TOP N: Si el usuario pide "top N", "los N mejores", "los más altos" o un ranking sobre una categoría, genera un plan `distribution` con `dimension` = categoría, `metric` = la métrica real, `ranking_metric` = la misma métrica, `limit` = N (o 10 si no lo dice) y `visual_protocol` = "pareto_chart". No inventes la métrica.
    11. COBERTURA HONESTA: Si el usuario nombra una medida o columna que NO existe en COLUMNAS DISPONIBLES (ej: pide "ventas" pero no hay columna de ventas), NO la sustituyas en silencio: responde igual con la mejor interpretación posible y lista el término en `unresolved_concepts` (y explica en `coverage_note`). Si todo lo pedido existe, deja `unresolved_concepts` vacío.

    OUTPUT: Devuelve ÚNICAMENTE un JSON válido que cumpla estrictamente con este Schema:
    {schema_json}
    """


def build_unified_dynamic_context(
    columns: list[str],
    glossary_context: str,
    topology_context: str,
    memory_context: str = "",
    memory_instruction: str = "",
    format_instruction: str = "",
    related_frames_context: str = "",
) -> str:
    """Contexto dinámico específico del dataset y de la sesión (cambia por request)."""
    return f"""
    TUS HERRAMIENTAS Y CONTEXTO:
    - COLUMNAS DISPONIBLES: {list(columns or [])}
    - CONTEXTO GLOSARIO: {glossary_context}
    - TOPOLOGÍA (Tipos de Datos): {topology_context}
    {related_frames_context if related_frames_context else ''}
    - MEMORIA DE SESIÓN: {memory_context if memory_context else 'Sin contexto previo. Nueva conversación.'}
    {memory_instruction if memory_instruction else ''}
    {format_instruction if format_instruction else ''}
    """


def build_unified_prompt(
    prompt: str,
    columns: list[str],
    glossary_context: str,
    topology_context: str,
    memory_context: str = "",
    memory_instruction: str = "",
    format_instruction: str = "",
    related_frames_context: str = "",
) -> str:
    """Wrapper de retrocompatibilidad: reconstruye el prompt unificado original."""
    dynamic = build_unified_dynamic_context(
        columns=columns,
        glossary_context=glossary_context,
        topology_context=topology_context,
        memory_context=memory_context,
        memory_instruction=memory_instruction,
        format_instruction=format_instruction,
        related_frames_context=related_frames_context,
    )
    return f"{get_unified_static_instruction()}\n\n{dynamic}\n\nSOLICITUD DEL USUARIO: {prompt}"


def unified_translate(
    prompt: str,
    columns: list,
    glossary_context: str,
    topology_context: str,
    memory_context: str = "",
    memory_instruction: str = "",
    format_instruction: str = "",
    schema_profile: dict | None = None,
    dataset_contract: dict[str, Any] | None = None,
    related_frames_context: str = "",
    candidate_df: pd.DataFrame | None = None,
) -> Optional[tuple[dict[str, Any], list[AnalysisPlan]]]:
    """Ejecuta la clasificación y generación de planes en un solo round-trip a Gemini.
    
    Ahorra tokens de entrada (no repite el contexto) y reduce la latencia en 10-25s.
    """
    surface_prompt = normalize_surface_text(prompt)
    schema_fp = schema_fingerprint(columns, schema_profile=schema_profile, dataset_contract=dataset_contract)
    
    cache_key = build_cache_key(
        "semantic_unified",
        {
            "prompt": surface_prompt,
            "schema_fingerprint": schema_fp,
            "glossary_context": glossary_context,
            "topology_context": topology_context,
            "memory_context": memory_context,
            "unified_contract_version": "v4",
        },
    )
    
    cached_payload = get_cached_json("semantic_unified", cache_key)
    if isinstance(cached_payload, dict) and "plans" in cached_payload:
        try:
            cached_output = UnifiedAnalysisOutput.model_validate(cached_payload)
            if cached_output.plans:
                finalized = finalize_plans(cached_output.plans, schema_profile, dataset_contract=dataset_contract, prompt=prompt)
                apply_coverage_disclosure(
                    finalized, schema_profile, prompt, cached_output.unresolved_concepts
                )
                emit_structured_log(
                    "semantic_unified_cache_hit",
                    prompt=surface_prompt[:180],
                    plan_count=len(finalized),
                    route=cached_output.classification.route,
                )
                return cached_output.classification.model_dump(), finalized
        except Exception as cache_err:
            emit_structured_log(
                "semantic_unified_cache_restore_error",
                level="warning",
                error=str(cache_err)[:180],
            )

    static_instruction = get_unified_static_instruction()
    dynamic_context = build_unified_dynamic_context(
        columns=columns,
        glossary_context=glossary_context,
        topology_context=topology_context,
        memory_context=memory_context,
        memory_instruction=memory_instruction,
        format_instruction=format_instruction,
        related_frames_context=related_frames_context,
    )
    user_message = f"{dynamic_context}\n\nSOLICITUD DEL USUARIO: {prompt}"

    model_name = str(settings.AI_MODEL_NAME or "gemini-2.5-flash").strip()
    try:
        model = genai.GenerativeModel(
            model_name=model_name,
            generation_config={
                "system_instruction": static_instruction,
                "response_mime_type": "application/json",
                "temperature": 0.1,
            },
        )
        with record_llm_call(
            "semantic_unified_generation",
            model_name=model_name,
            prompt=user_message,
            trace_id=None,
            trace_name="semantic_unified",
        ) as lf_span:
            response = model.generate_content(user_message)
            lf_span["output"] = getattr(response, "text", "")

        # 1. Intentar acceso a objeto parseado nativo
        parsed_output: Optional[UnifiedAnalysisOutput] = None
        raw_parsed = getattr(response, "parsed", None)
        if isinstance(raw_parsed, UnifiedAnalysisOutput):
            parsed_output = raw_parsed
        elif isinstance(raw_parsed, dict):
            parsed_output = UnifiedAnalysisOutput.model_validate(raw_parsed)

        # 2. Si no viene en response.parsed, parsear response.text
        if parsed_output is None:
            raw_text = getattr(response, "text", "").strip()
            if raw_text:
                payload = parse_translator_payload(raw_text)
                if isinstance(payload, dict):
                    parsed_output = UnifiedAnalysisOutput.model_validate(payload)
                elif isinstance(payload, list):
                    # Si el modelo solo devolvió la lista de planes, envolver en fallback estructurado
                    parsed_output = UnifiedAnalysisOutput(
                        classification=ClassificationResult(route="SIMPLE", detected_intent="descriptive"),
                        plans=[AnalysisPlan.model_validate(p) for p in payload if isinstance(p, dict)]
                    )

        if not parsed_output or not parsed_output.plans:
            emit_structured_log(
                "semantic_unified_empty_plans",
                level="warning",
                prompt=prompt[:180],
            )
            return None

        # Guardar en caché
        set_cached_json(
            "semantic_unified",
            cache_key,
            parsed_output.model_dump(mode="json"),
            settings.SEMANTIC_TRANSLATOR_CACHE_TTL_SECONDS,
        )

        finalized_plans = finalize_plans(parsed_output.plans, schema_profile, dataset_contract=dataset_contract, prompt=prompt)
        apply_coverage_disclosure(
            finalized_plans, schema_profile, prompt, parsed_output.unresolved_concepts
        )
        emit_structured_log(
            "semantic_unified_success",
            prompt=prompt[:180],
            plan_count=len(finalized_plans),
            route=parsed_output.classification.route,
            detected_intent=parsed_output.classification.detected_intent,
        )
        return parsed_output.classification.model_dump(), finalized_plans

    except Exception as error:
        emit_structured_log(
            "semantic_unified_error",
            level="warning",
            prompt=prompt[:180],
            error=str(error)[:200],
        )
        return None
