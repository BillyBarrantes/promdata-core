"""Narrative/synthesis generation — extracted from orchestrator.py."""

import json
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import Any

from app.core.config import settings
from app.core.gemini_client import genai
from app.core.langfuse_client import record_llm_event
from app.core.structured_logging import emit_structured_log
from app.core.serializers import convert_keys_to_str
from app.services.ai_response_cache import build_cache_key, get_cached_json, set_cached_json
from app.services.analysis_explainability import build_analysis_explainability
from app.services.analysis_diagnostic_context import build_enterprise_diagnostic_context
from app.services.predictive_engine import PredictiveEngine

from app.tasks.analysis_pipeline.data_loader import clean_business_terms
from app.tasks.analysis_pipeline.memory_router import (
    _evaluate_institutional_compliance,
    _force_markdown_action_block,
)
from app.tasks.analysis_pipeline.plan_generator import select_narrative_model_name


def generate_chart_narrative(
    *,
    plan: Any,
    ibis_output: dict,
    currency_meta: dict | None,
    institutional_context: str,
    institutional_snippets: list,
    visual_probe_mode: bool,
    filtered_granular_df: Any,
    schema_profile: dict,
    actual_prompt: str,
    file_id: str,
    task_id: str,
) -> list[dict[str, Any]]:
    """
    Generate narrative, explainability, and recommendations for a single chart plan.
    Returns a list of dict items to append to the ibis_response.
    """
    items: list[dict[str, Any]] = []

    detected_symbol = currency_meta.get('symbol') if currency_meta else None
    detected_code = currency_meta.get('code', 'USD') if currency_meta else 'USD'

    unit_instruction = "REGLA DE FORMATO: No detecté moneda explícita. Si hablas de 'Stock', 'Cantidad' o 'Unidades', NO uses signos de dinero ($/S/)."
    if detected_symbol:
        unit_instruction = f"REGLA DE FORMATO: El archivo tiene moneda {detected_code}. Si hablas de montos, USA EL SÍMBOLO '{detected_symbol}'. Si hablas de Stock/Unidades, usa números neutros."

    intent = plan.main_intent
    if hasattr(intent, 'metric_unit'):
        if intent.metric_unit == 'quantity':
            unit_instruction = "⚠️ CANDADO ACTIVO: Estás analizando VOLUMEN FÍSICO. PROHIBIDO usar signos de moneda. Habla de 'Unidades' / 'Cajas'."
        elif intent.metric_unit == 'currency':
            sym = detected_symbol if detected_symbol else '$'
            unit_instruction = f"⚠️ CANDADO ACTIVO: Estás analizando DINERO. Usa el símbolo '{sym}' para los montos."
        elif intent.metric_unit == 'percentage':
            unit_instruction = "⚠️ CANDADO ACTIVO: Estás analizando TASAS O MÁRGENES. Usa siempre el símbolo %."

    safe_narrative_data = convert_keys_to_str(ibis_output.get('data', []))
    raw_data_str = json.dumps(safe_narrative_data, default=str)[:2500]
    clean_data_str = clean_business_terms(raw_data_str)

    polarity = getattr(plan, 'metric_polarity', 'neutral')
    polarity_instruction = ""
    if polarity == "unfavorable":
        polarity_instruction = "CONTEXTO DE NEGOCIO: Esta métrica es DESFAVORABLE (el negocio busca reducirla: vencimientos, merma, errores, deudas). Si la tendencia BAJA → es positivo, celebra. Si SUBE → es preocupante, alerta."
    elif polarity == "favorable":
        polarity_instruction = "CONTEXTO DE NEGOCIO: Esta métrica es FAVORABLE (el negocio busca aumentarla: ventas, ingresos, producción). Si la tendencia SUBE → es positivo. Si BAJA → es preocupante."

    methodology_instruction = ""
    intent_type = getattr(intent, 'type', '')
    if intent_type == 'predictive':
        methodology_instruction = (
            "METODOLOGÍA: El pronóstico fue calculado mediante Suavización Exponencial "
            "(Holt-Winters) con tendencia aditiva sobre los datos históricos disponibles. "
            "Incluye en tu análisis UNA frase breve mencionando este método para dar "
            "confianza al usuario. Ej: 'Proyección basada en Suavización Exponencial (Holt-Winters).'"
        )

    filter_context = ""
    if hasattr(intent, 'filters') and intent.filters:
        filter_parts = []
        for f in intent.filters:
            col_alias = plan.column_aliases.get(f.column, f.column)
            filter_parts.append(f"{col_alias} {f.operator} {f.value}")
        if filter_parts:
            filter_context = f"FILTROS APLICADOS: {', '.join(filter_parts)}. Menciona brevemente estos criterios en tu análisis para que el usuario entienda el alcance de los datos."

    if visual_probe_mode:
        compliance_result = {
            "matched": False,
            "suppressed_for_visual_exploration": True,
        }
    else:
        compliance_result = _evaluate_institutional_compliance(
            snippets=institutional_snippets,
            actual_prompt=actual_prompt,
            plan=plan,
            ibis_output=ibis_output,
        )

    institutional_narrative_instruction = ""
    if institutional_context and not visual_probe_mode:
        institutional_narrative_instruction = f"""
                            CONTEXTO DOCUMENTAL INSTITUCIONAL:
                            {institutional_context}
                            IMPORTANTE - REGLAS INSTITUCIONALES:
                            - Si el contexto documental contiene límites, umbrales, alertas o acciones obligatorias aplicables a los datos, es tu máxima prioridad.
                            - Debes evaluar obligatoriamente los datos contra esas reglas antes de proponer cualquier recomendación.
                            - Si los datos incumplen una regla documental, la sección **Acción:** DEBE obedecer literalmente la directriz institucional y descartar recomendaciones genéricas en conflicto.
                            """

    compliance_instruction = ""
    mandated_action = ""
    if compliance_result.get("matched"):
        mandated_action = str(compliance_result.get("action") or "").strip()
        compliance_instruction = f"""
                            COMPLIANCE GATE ACTIVADO:
                            - Regla institucional aplicable: {compliance_result.get("rule_sentence")}
                            - Documento fuente: {compliance_result.get("document_title")}
                            - Valor observado: {compliance_result.get("observed_value")}
                            - Umbral documental: {compliance_result.get("threshold")}
                            - ACCIÓN INSTITUCIONAL OBLIGATORIA: {mandated_action}
                            - En la sección **Acción:** usa EXACTAMENTE ese texto.
                            - PROHIBIDO reemplazarla por alternativas genéricas, operativas o matemáticas.
                            """

    enterprise_diagnostic_context = build_enterprise_diagnostic_context(
        plan=plan,
        granular_df=filtered_granular_df,
        schema_profile=schema_profile,
    )

    explainability_payload = build_analysis_explainability(
        plan=plan,
        ibis_output=ibis_output,
        actual_prompt=actual_prompt,
        compliance_result=compliance_result,
        diagnostic_context=enterprise_diagnostic_context,
    )
    conclusion_gate = explainability_payload.get("conclusion_gate", {}) if isinstance(explainability_payload, dict) else {}
    analysis_guardrails = explainability_payload.get("analysis_guardrails", {}) if isinstance(explainability_payload, dict) else {}
    forecast_explainability = explainability_payload.get("forecast_explainability", {}) if isinstance(explainability_payload, dict) else {}
    conclusion_decision = str(conclusion_gate.get("decision") or "").strip()
    conclusion_instruction = ""
    if conclusion_decision == "insufficient_evidence":
        conclusion_instruction = (
            "GATE DE SUFICIENCIA: La evidencia es insuficiente para una conclusión fuerte. "
            "Debes decir explícitamente que la base actual no soporta afirmaciones firmes. "
            "PROHIBIDO presentar causalidad, certeza alta o recomendaciones agresivas no respaldadas por los datos."
        )
    elif conclusion_decision == "cautionary_conclusion":
        conclusion_instruction = (
            "GATE DE SUFICIENCIA: La lectura es válida pero prudente. "
            "Usa verbos como 'sugiere', 'indica', 'apunta a'. "
            "PROHIBIDO afirmar causalidad o certeza absoluta."
        )
    else:
        conclusion_instruction = (
            "GATE DE SUFICIENCIA: Puedes formular una conclusión firme sobre patrones observados, "
            "pero PROHIBIDO confundir correlación con causalidad."
        )
    guardrail_status = str(analysis_guardrails.get("overall_status") or "").strip()
    guardrail_summary = str(analysis_guardrails.get("summary") or "").strip()
    guardrail_instruction = ""
    if guardrail_status == "blocked":
        guardrail_instruction = (
            "GATE ESPECIALIZADO POR TIPO DE ANALISIS: "
            f"{guardrail_summary} "
            "PROHIBIDO presentar esta proyección, correlación o divergencia como hallazgo validado."
        )
    elif guardrail_status == "guarded":
        guardrail_instruction = (
            "GATE ESPECIALIZADO POR TIPO DE ANALISIS: "
            f"{guardrail_summary} "
            "Si lo mencionas, debe aparecer como hipótesis prudente y no como evidencia concluyente."
        )
    forecast_status = str(forecast_explainability.get("status") or "").strip()
    forecast_summary = str(forecast_explainability.get("summary") or "").strip()
    forecast_instruction = ""
    if forecast_status == "blocked":
        forecast_instruction = (
            "EXPLICABILIDAD DE FORECAST: "
            f"{forecast_summary} "
            "Si mencionas la proyección, debes explicar por qué no alcanza soporte suficiente."
        )
    elif forecast_status == "guarded":
        forecast_instruction = (
            "EXPLICABILIDAD DE FORECAST: "
            f"{forecast_summary} "
            "Debes hablar de la proyección como señal tentativa, no como pronóstico firme."
        )
    elif forecast_status == "clear":
        forecast_instruction = (
            "EXPLICABILIDAD DE FORECAST: "
            f"{forecast_summary} "
            "Si usas la proyección, deja claro el soporte temporal que la habilita."
        )

    narrative_prompt = f"""
                        ACTÚA COMO UN ANALISTA DE NEGOCIOS EJECUTIVO. Lenguaje directo y profesional.

                        DATOS:
                        {clean_data_str}

                        {unit_instruction}

                        {polarity_instruction}

                        {methodology_instruction}

                        {filter_context}

                        {institutional_narrative_instruction}

                        {compliance_instruction}

                        {conclusion_instruction}

                        {guardrail_instruction}

                        {forecast_instruction}

                        REGLAS OBLIGATORIAS:
                        - Máximo 150 palabras en TOTAL.
                        - Cada oración DEBE citar al menos 1 dato concreto (número, %, comparación).
                        - Cada dato citado DEBE ser información NUEVA. NO repitas el mismo número en formato diferente (ej: "5,386,970" y luego "5.39M" es redundante).
                        - Si solo hay 1 dato (KPI simple), escribe máximo 3 oraciones: dato + contexto comparativo + acción.
                        - PROHIBIDO: metáforas, lenguaje figurativo, adjetivos vacíos ("impresionante", "dramático").
                        - ENFOQUE: Hallazgo → Dato que lo sustenta → Acción recomendada.
                        - Habla de "el negocio" / "la operación", no del archivo o dataset.
                        - Escribe en español neutro.

                        PROTOCOLO DE TRANSPARENCIA NARRATIVA (OBLIGATORIO):
                        1. TRAZABILIDAD ABSOLUTA: PROHIBIDO usar términos opacos como "segmento principal", "grupo líder", "la mayoría". Si agrupas o sumas elementos, DEBES listar cuáles son por nombre. Ejemplo correcto: "Los 3 productos líderes (Cocoa WINTERS, Chips Cordillera y Glina. WINTERS) concentran X unidades". Ejemplo PROHIBIDO: "El segmento principal concentra X artículos".
                        2. JUSTIFICACIÓN DE UNIVERSOS: Cuando cites un total, especifica A QUÉ corresponde. Ejemplo correcto: "Del total de 5,386,970 unidades en inventario, esta vista muestra 2,839,784 correspondientes a los 50 productos con mayor stock". Ejemplo PROHIBIDO: "El volumen total es 2,839,784".
                        3. LENGUAJE DE NEGOCIO CLARO: Escribe para un gerente o analista junior. Cero jerga de Data Science. Si mencionas una operación matemática, explícala: "La suma de stock de los 10 principales productos..." en vez de "La agregación del primer decil...".
                        4. INTEGRIDAD SEMÁNTICA DE UNIDADES: El vocabulario y la unidad de medida DEBEN derivar ESTRICTAMENTE del nombre de la métrica analizada. Si la métrica es física (Stock, Cantidad, Volumen, Unidades, Cajas, Piezas), está ESTRICTAMENTE PROHIBIDO usar símbolos de moneda ($, €, S/) o terminología financiera (capital, portafolio, ingresos, valor, inversión). Usa términos como 'unidades', 'artículos', 'piezas' o 'volumen operativo'. Usa símbolos de moneda SOLO si la métrica explícitamente implica dinero (Precio, Costo, Venta, Ingreso, Monto, Facturación).

                        ESTRUCTURA MARKDOWN:
                        ## 📊 [Titular conciso con dato clave]
                        **Contexto:** [1-2 oraciones, DEBE especificar el universo: cuántos registros, qué filtro, qué período]
                        **Evidencia:** [2-3 bullets con datos comparativos, cada uno NOMBRANDO los elementos concretos]
                        **Acción:** [1 recomendación concreta y medible]
                        """

    try:
        intent_type = str(getattr(intent, 'type', '') or '')
        narrative_model_name = select_narrative_model_name(
            intent_type=intent_type,
            institutional_context=institutional_context,
            compliance_result=compliance_result,
        )
        narrative_cache_key = build_cache_key(
            "chart_narrative",
            {
                "prompt": actual_prompt,
                "plan": plan.model_dump(mode="json"),
                "clean_data_str": clean_data_str,
                "unit_instruction": unit_instruction,
                "polarity_instruction": polarity_instruction,
                "methodology_instruction": methodology_instruction,
                "filter_context": filter_context,
                "institutional_narrative_instruction": institutional_narrative_instruction,
                "compliance_instruction": compliance_instruction,
                "conclusion_instruction": conclusion_instruction,
                "guardrail_instruction": guardrail_instruction,
                "forecast_instruction": forecast_instruction,
                "mandated_action": mandated_action,
            },
        )
        cached_narrative_payload = get_cached_json("chart_narrative", narrative_cache_key)
        narrative_text = ""

        if isinstance(cached_narrative_payload, dict):
            cached_text = str(cached_narrative_payload.get("content") or "").strip()
            if cached_text:
                narrative_text = cached_text
                emit_structured_log(
                    "chart_narrative_cache_hit",
                    model=narrative_model_name,
                    plan_title=plan.title,
                    plan_metric=getattr(plan, "metric", None),
                    plan_dimension=getattr(plan, "dimension", None),
                    prompt=str(actual_prompt)[:180],
                    file_id=file_id,
                    cache_key_prefix=narrative_cache_key[:16],
                )

        if not narrative_text:
            emit_structured_log(
                "chart_narrative_model_selected",
                model=narrative_model_name,
                intent_type=intent_type,
                plan_title=plan.title,
            )
            try:
                narrative_model = genai.GenerativeModel(narrative_model_name)
                _narrative_executor = ThreadPoolExecutor(max_workers=1)
                _future = _narrative_executor.submit(
                    narrative_model.generate_content, narrative_prompt
                )
                try:
                    narrative_resp = _future.result(timeout=45)
                except FuturesTimeoutError:
                    raise ValueError("Narrativa timeout (45s) — se usará fallback.")
                finally:
                    _narrative_executor.shutdown(wait=False)
                narrative_text = str(narrative_resp.text or "").strip()
                record_llm_event(
                    "narrative_parallel",
                    model_name=narrative_model_name,
                    prompt=narrative_prompt,
                    output=narrative_text,
                    trace_id=task_id,
                    trace_name="perform_analysis_task",
                    metadata={"intent_type": intent_type, "plan_title": plan.title},
                )
                if not narrative_text and narrative_model_name != settings.NARRATIVE_STRICT_MODEL_NAME:
                    raise ValueError("Narrativa vacía con modelo rápido.")
            except Exception as primary_narrative_error:
                if narrative_model_name == settings.NARRATIVE_STRICT_MODEL_NAME:
                    raise
                emit_structured_log(
                    "chart_narrative_model_fallback",
                    level="warning",
                    primary_model=narrative_model_name,
                    fallback_model=settings.NARRATIVE_STRICT_MODEL_NAME,
                    error=str(primary_narrative_error)[:200],
                    plan_title=plan.title,
                )
                narrative_model = genai.GenerativeModel(settings.NARRATIVE_STRICT_MODEL_NAME)
                narrative_resp = narrative_model.generate_content(narrative_prompt)
                narrative_text = str(narrative_resp.text or "").strip()
                record_llm_event(
                    "narrative_fallback",
                    model_name=settings.NARRATIVE_STRICT_MODEL_NAME,
                    prompt=narrative_prompt,
                    output=narrative_text,
                    trace_id=task_id,
                    trace_name="perform_analysis_task",
                    metadata={"intent_type": intent_type, "plan_title": plan.title, "fallback": True},
                )

            if mandated_action:
                narrative_text = _force_markdown_action_block(narrative_text, mandated_action)

            set_cached_json(
                "chart_narrative",
                narrative_cache_key,
                {"content": narrative_text},
                settings.NARRATIVE_CACHE_TTL_SECONDS,
            )

        items.append({"type": "mensaje_resumen", "content": narrative_text})
        items.append({"type": "explicabilidad_analitica", "data": explainability_payload})
    except Exception:
        items.append({
            "type": "mensaje_resumen",
            "content": "### 📊 Análisis Calculado Exitosamente\nLos datos han sido procesados. Revisa los gráficos adjuntos para el detalle."
        })
        items.append({"type": "explicabilidad_analitica", "data": explainability_payload})

    try:
        hard_facts = ibis_output.get('hard_facts', {})
        if hard_facts:
            analysis_context = plan.title if plan.title else ""
            polarity = getattr(plan, 'metric_polarity', 'neutral')
            recommendations = [] if visual_probe_mode else PredictiveEngine.generate_recommendations(
                hard_facts, context=analysis_context, polarity=polarity
            )
            if recommendations:
                items.append({"type": "recomendaciones", "data": recommendations})
    except Exception:
        pass

    return items
