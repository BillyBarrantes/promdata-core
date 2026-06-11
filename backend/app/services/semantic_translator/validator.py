"""
Semantic Translator — Validator Module (Fase 0.1, Paso 3/5)

[REFACTOR 2026-06-11] Este archivo es parte de la Operacion Refactor
documentada en AGENTS.md §15.1 Plan 1 / Fase 0.1.

Responsabilidad: Validacion, parseo, sanitizacion de payloads Gemini y manejo de errores de modelo.

Funciones module-level extraidas desde core.py. Los metodos en core.py
ahora delegan a estas funciones pasando `SemanticTranslator` como
primer parametro (la "instance") — un requisito del patron de
delegacion que mantiene la API publica intacta.

Regla de oro: prohibido romper funcionalidades existentes. Todos los
tests existentes y los 3 call sites siguen funcionando sin cambios
porque la API publica de SemanticTranslator permanece intacta.
"""

from __future__ import annotations

import json
import re
import unicodedata
from json.decoder import JSONDecodeError, JSONDecoder
from typing import Any, Dict, List, Optional

from app.core.gemini_client import genai
from app.core.langfuse_client import record_llm_call
from app.core.config import settings
from app.core.semantic_grammar import AnalysisPlan, DataFilter, FilterOperator
from app.services.metric_semantics import normalize_semantic_text
from app.services.semantic_translator.core import SemanticTranslator


# ============================================================================
# Funciones module-level para validator
# ============================================================================
# Cada funcion toma `instance` como primer parametro (la clase
# SemanticTranslator) por compatibilidad con el patron de delegacion.
# Las funciones son static en su naturaleza original (no usan estado de
# instancia); el parametro `instance` se ignora en el cuerpo.


def extract_json_code_block(instance, raw_text: str):
    fenced_match = re.search(r"```(?:json)?\s*(.*?)\s*```", raw_text, flags=re.IGNORECASE | re.DOTALL)
    return fenced_match.group(1).strip() if fenced_match else raw_text.strip()


def split_json_documents(instance, raw_text: str):
    """
    Parser tolerante para respuestas Gemini con múltiples documentos JSON
    concatenados o con texto residual.
    """
    decoder = JSONDecoder()
    text = raw_text.strip()
    docs: list[dict | list] = []
    cursor = 0

    while cursor < len(text):
        while cursor < len(text) and text[cursor] in " \t\r\n,;":
            cursor += 1
        if cursor >= len(text):
            break
        if text[cursor] not in "[{":
            cursor += 1
            continue
        try:
            parsed, end = decoder.raw_decode(text, cursor)
            if isinstance(parsed, (dict, list)):
                docs.append(parsed)
            cursor = max(end, cursor + 1)
        except JSONDecodeError:
            cursor += 1

    return docs


def parse_translator_payload(instance, raw_text: str):
    """
    Devuelve dict/list válido incluso cuando Gemini devuelve JSON + ruido.
    """
    candidate = SemanticTranslator._extract_json_code_block(raw_text)

    try:
        return json.loads(candidate)
    except JSONDecodeError:
        docs = SemanticTranslator._split_json_documents(candidate)
        if not docs:
            raise
        if len(docs) == 1:
            return docs[0]
        return docs


def is_recoverable_translator_model_error(instance, error: Exception):
    """
    Identifica cancelaciones/timeouts del proveedor LLM que justifican retry.
    """
    error_text = str(error or "").lower()
    recoverable_markers = (
        "499",
        "cancelled",
        "canceled",
        "deadline",
        "timeout",
        "timed out",
        "504",
        "503",
        "429",
        "resource_exhausted",
        "quota",
        "rate limit",
        "rate_limit",
        "temporarily unavailable",
        "unavailable",
    )
    return any(marker in error_text for marker in recoverable_markers)


def is_quota_translator_model_error(instance, error: Exception):
    error_text = str(error or "").lower()
    quota_markers = (
        "429",
        "resource_exhausted",
        "quota",
        "rate limit",
        "rate_limit",
    )
    return any(marker in error_text for marker in quota_markers)


def select_translator_fallback_model(instance, primary_model_name: str):
    fallback_model_name = str(settings.NARRATIVE_FAST_MODEL_NAME or "").strip()
    primary_model_name = str(primary_model_name or "").strip()
    if not fallback_model_name or fallback_model_name == primary_model_name:
        return None
    return fallback_model_name


def sanitize_translator_payload_item(instance, item: dict[str, Any], columns: list[str], payload_mode: str):
    available_columns = set(columns or [])
    if 'main_intent' in item:
        intent = item['main_intent']
        if isinstance(intent, dict):
            if 'group_by' in intent and isinstance(intent['group_by'], list):
                intent['group_by'] = [c for c in intent['group_by'] if c in available_columns]

            if 'metrics' in intent and isinstance(intent['metrics'], list):
                intent['metrics'] = [c for c in intent['metrics'] if c in available_columns]
            elif 'primary_metric' in intent and isinstance(intent['primary_metric'], str):
                if payload_mode == "multi" and intent['primary_metric'] not in available_columns:
                    intent['primary_metric'] = None

            if 'filters' in intent and isinstance(intent['filters'], list):
                intent['filters'] = [
                    f for f in intent['filters']
                    if isinstance(f, dict) and f.get('column') in available_columns
                ]

            if 'negative_filters' in intent and isinstance(intent['negative_filters'], list):
                intent['negative_filters'] = [
                    f for f in intent['negative_filters']
                    if isinstance(f, dict) and f.get('column') in available_columns
                ]

            scalar_metric_fields = ['plot_metric', 'ranking_metric']
            if payload_mode == "single":
                scalar_metric_fields.extend(['value_column', 'metric', 'dimension', 'date_column'])

            for metric_field in scalar_metric_fields:
                if metric_field in intent and isinstance(intent[metric_field], str):
                    if intent[metric_field] not in available_columns:
                        intent[metric_field] = None

            if 'time_dimension' in intent and isinstance(intent['time_dimension'], str):
                if payload_mode == "multi" and intent['time_dimension'] not in available_columns:
                    intent['time_dimension'] = None

            if 'value_column' in intent and isinstance(intent['value_column'], str):
                if payload_mode == "multi" and intent['value_column'] not in available_columns:
                    intent['value_column'] = None

    if 'filters' in item and isinstance(item['filters'], list):
        item['filters'] = [
            f for f in item['filters']
            if isinstance(f, dict) and f.get('column') in available_columns
        ]

    return item


def plans_from_translator_payload(instance, parsed_data: Any, columns: list[str]):
    plans: list[AnalysisPlan] = []
    if isinstance(parsed_data, list):
        for i, item in enumerate(parsed_data[:5]):
            try:
                if isinstance(item, dict):
                    item = SemanticTranslator._sanitize_translator_payload_item(item, columns, "multi")
                plans.append(AnalysisPlan.model_validate(item))
                title_preview = item.get('title', 'Sin título') if isinstance(item, dict) else 'Sin título'
                print(f"✅ [MULTI-PLAN] Plan {i+1} validado: {title_preview[:60]}")
            except Exception as val_e:
                print(f"⚠️ [MULTI-PLAN] Plan {i+1} inválido (Alucinación bloqueada o schema roto): {val_e}")
    else:
        if isinstance(parsed_data, dict):
            parsed_data = SemanticTranslator._sanitize_translator_payload_item(parsed_data, columns, "single")
        plans.append(AnalysisPlan.model_validate(parsed_data))

    return plans


def generate_translator_plans_with_model(instance, model_name: str, translator_input: str, columns: list[str]):
    model = genai.GenerativeModel(
        model_name=model_name,
        generation_config={"response_mime_type": "application/json", "temperature": 0.0},
    )
    with record_llm_call(
        "semantic_translation",
        model_name=model_name,
        prompt=translator_input,
        trace_id=None,
        trace_name="semantic_translator",
    ) as lf_span:
        response = model.generate_content(translator_input)
        lf_span["output"] = response.text
    clean_json = response.text.strip()
    print(f"🕵️ [SEMANTIC STRATEGIST] Protocolo Activado: {clean_json[:200]}...")
    parsed_data = SemanticTranslator._parse_translator_payload(clean_json)
    return SemanticTranslator._plans_from_translator_payload(parsed_data, columns)

