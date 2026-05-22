# En: backend/app/tasks/analysis_tasks.py

from app.celery_app import celery_app
from app.core.config import settings
from app.core.serializers import CustomEncoder, convert_keys_to_str
from app.core.supabase_client import get_supabase_client
from app.services.chart_factory import ChartFactory
# --- AQUI ESTÁ LA CLAVE: EL NUEVO MOTOR ---
from app.services.data_engine import DataEngine
from app.services.auto_analyst import AutoAnalyst
from app.services.predictive_engine import PredictiveEngine
from app.services.document_rag import (
    build_knowledge_context_block,
    resolve_user_team_id,
    search_knowledge_documents,
)
from app.services.analysis_explainability import build_analysis_explainability
from app.services.analysis_diagnostic_context import build_enterprise_diagnostic_context
from app.services.analysis_traceability import (
    build_traceability_payload,
    build_traceability_plan_entry,
)
from app.services.visual_recommendation_engine import (
    build_visual_governance,
    extract_prompt_visual_requests,
    normalize_visual_id,
    resolve_visual_protocol_value,
    should_enable_visual_probe_mode,
)
from app.services.metric_semantics import align_plan_metrics_with_prompt
from app.services.enterprise_telemetry import (
    track_analysis_completed,
    track_canary_runtime_execution_fallback,
    track_canary_runtime_execution_observed,
    track_canary_runtime_route_fallback,
    track_canary_runtime_route_observed,
)
from app.services.ai_response_cache import build_cache_key, get_cached_json, set_cached_json
from app.services.canonical_shadow_runtime_observer import (
    _classify_prompt_type as _shadow_observer_classify_prompt_type,
    _normalize_prompt as _shadow_observer_normalize_prompt,
    build_live_runtime_summary,
    observe_canonical_shadow_runtime,
)
from app.services.canonical_tabular_canary_executor import (
    execute_canonical_tabular_canary_analysis,
)
from app.services.canonical_tabular_production_executor import (
    execute_canonical_tabular_production_analysis,
)

# 👇 AGREGAR ESTO (MOTORES FASE 1.2) 👇
from app.services.semantic_translator import SemanticTranslator
from app.services.ibis_engine import IbisEngine
from app.services.smart_table_builder import (
    should_use_smart_table,
    should_offer_hybrid_smart_table,
    echarts_to_smart_table,
)
from app.core.arrow_utils import (
    should_use_arrow,
    evaluate_records_arrow_transport,
    evaluate_dataframe_arrow_transport,
    records_to_arrow_base64,
    dataframe_to_arrow_base64,
)
from app.core.prompt_format_override import (
    detect_format_override_from_prompt as core_detect_format_override_from_prompt,
    normalize_prompt_rules as core_normalize_prompt_rules,
)
from app.core.structured_logging import emit_structured_log
from app.core.semantic_grammar import AnalysisPlan, VisualProtocol
# 👆 ------------------------------- 👆

import pandas as pd
import io
import json
import traceback
import numpy as np
import google.generativeai as genai
import re
import unicodedata
import warnings
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import datetime
from time import perf_counter
from typing import Any

# Librerías Científicas (Importación Segura)
try:
    import sklearn
    from sklearn.ensemble import IsolationForest, RandomForestRegressor
    from statsmodels.tsa.holtwinters import ExponentialSmoothing
except ImportError:
    pass

# Suprimir advertencias
warnings.filterwarnings("ignore")

def clean_business_terms(text_data: str) -> str:
    """
    Intermediario de Vocabulario V1.
    Elimina snake_case y tecnicismos antes de que Gemini lea los datos.
    """
    # 1. Diccionario de Reemplazo Directo (Personalizable)
    replacements = {
        "total_venta_pen": "Ventas Totales (S/)",
        "cantidad_vendida": "Unidades Vendidas",
        "metric_value": "Valor",
        "_virtual_snapshot_date_": "Fecha de Corte",
        "None": "N/A",
        "nan": "0"
    }
    
    clean_text = text_data
    for old, new in replacements.items():
        clean_text = clean_text.replace(old, new)
        
    # 2. Limpieza Genérica de Snake Case (cualquier_cosa_asi -> Cualquier Cosa Asi)
    def replacer(match):
        return match.group(0).replace('_', ' ').title()
    
    clean_text = re.sub(r'\b[a-z]+(_[a-z]+)+\b', replacer, clean_text)
    
    return clean_text

def _recursive_round(obj, decimals=2):
    """Redondeo recursivo para estructuras JSON."""
    if isinstance(obj, float):
        return round(obj, decimals)
    if isinstance(obj, dict):
        return {k: _recursive_round(v, decimals) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_recursive_round(x, decimals) for x in obj]
    return obj


def select_narrative_model_name(
    *,
    intent_type: str,
    institutional_context: str,
    compliance_result: dict[str, Any] | None,
) -> str:
    compliance_result = compliance_result or {}
    if compliance_result.get("matched"):
        return settings.NARRATIVE_STRICT_MODEL_NAME
    if intent_type == "predictive":
        return settings.NARRATIVE_STRICT_MODEL_NAME
    return settings.NARRATIVE_FAST_MODEL_NAME

def build_widget_query_contract(plan, schema_profile: dict | None = None) -> dict:
    """
    Contrato reactivo mínimo para recomputar widgets localmente en DuckDB-WASM
    sin alterar el shape actual del payload.
    """
    intent = getattr(plan, 'main_intent', None)
    if not intent:
        return {}

    schema_profile = schema_profile or {}

    def _role(column_name: str | None) -> str | None:
        if not column_name:
            return None
        return schema_profile.get(column_name, {}).get('role')

    def _pick_first(candidates, allowed_roles: set[str]) -> str | None:
        for candidate in candidates:
            if not candidate:
                continue
            if _role(candidate) in allowed_roles:
                return candidate
        return None

    raw_metrics = []
    explicit_metric = getattr(intent, 'metric', None)
    explicit_value_column = getattr(intent, 'value_column', None)
    metrics_list = list(getattr(intent, 'metrics', None) or [])

    if explicit_metric:
        raw_metrics.append(explicit_metric)
    if explicit_value_column and explicit_value_column not in raw_metrics:
        raw_metrics.append(explicit_value_column)
    for metric_name in metrics_list:
        if metric_name and metric_name not in raw_metrics:
            raw_metrics.append(metric_name)

    valid_metrics = [
        metric_name for metric_name in raw_metrics
        if _role(metric_name) == 'metric'
    ]

    raw_dimension_candidates = [
        getattr(intent, 'dimension', None),
        getattr(intent, 'date_column', None),
    ]
    raw_dimension_candidates.extend(list(getattr(intent, 'group_by', None) or []))

    primary_dimension = _pick_first(raw_dimension_candidates, {'dimension', 'identifier', 'date'})
    valid_group_by = [
        column_name for column_name in (getattr(intent, 'group_by', None) or [])
        if _role(column_name) in {'dimension', 'identifier', 'date'} and column_name != primary_dimension
    ]

    contract = {
        "intent_type": getattr(intent, 'type', None),
        "visual_protocol": getattr(getattr(intent, 'visual_protocol', None), 'value', None),
        "aggregation": getattr(intent, 'aggregation', 'sum'),
        "metric": valid_metrics[0] if valid_metrics else None,
        "metrics": valid_metrics or None,
        "value_column": explicit_value_column if _role(explicit_value_column) == 'metric' else (valid_metrics[0] if valid_metrics else None),
        "dimension": primary_dimension,
        "group_by": valid_group_by or None,
        "limit": getattr(intent, 'limit', None),
        "barmode": getattr(intent, 'barmode', None),
        "title": getattr(plan, 'title', None),
    }
    normalized_contract = {k: v for k, v in contract.items() if v not in (None, [], {})}
    has_metric = any(normalized_contract.get(key) for key in ('metric', 'value_column', 'metrics'))
    has_dimension = bool(normalized_contract.get('dimension'))

    if not has_metric or not has_dimension:
        return {}

    return normalized_contract


def should_force_smart_table_from_prompt(prompt_text: str) -> bool:
    """
    Fuerza Smart Table cuando el usuario lo solicita explícitamente.
    Evita depender del semáforo de densidad para estos casos.
    """
    if not prompt_text:
        return False

    normalized = str(prompt_text).lower()
    explicit_smart_table = any(
        marker in normalized
        for marker in (
            "smart table",
            "smarttable",
            "tabla inteligente",
            "tabla smart",
        )
    )
    explicit_table = "tabla" in normalized
    explicit_chart = any(marker in normalized for marker in ("grafico", "gráfico", "chart"))

    return explicit_smart_table or (explicit_table and not explicit_chart)


def _normalize_prompt_token(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_text = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", ascii_text.replace("_", " ").strip().lower())


def _pick_first_metric_column(schema_profile: dict | None, prompt_text: str) -> str | None:
    if not schema_profile:
        return None
    prompt_norm = _normalize_prompt_token(prompt_text)
    metric_candidates = [
        column_name
        for column_name, info in schema_profile.items()
        if info.get("role") == "metric"
    ]
    if not metric_candidates:
        return None

    preferred_keywords = ("stock", "cantidad", "volume", "volumen", "total", "importe", "monto")
    scored = []
    for column_name in metric_candidates:
        col_norm = _normalize_prompt_token(column_name)
        score = 0
        if col_norm and col_norm in prompt_norm:
            score += 6
        if any(keyword in col_norm and keyword in prompt_norm for keyword in preferred_keywords):
            score += 4
        score += sum(1 for token in col_norm.split() if len(token) > 2 and token in prompt_norm)
        scored.append((score, column_name))
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def _pick_heatmap_axes(schema_profile: dict | None, prompt_text: str) -> tuple[str | None, str | None]:
    if not schema_profile:
        return (None, None)

    prompt_norm = _normalize_prompt_token(prompt_text)
    temporal_candidates = [
        column_name
        for column_name, info in schema_profile.items()
        if info.get("type") in {"temporal", "date"} or info.get("role") == "date"
    ]
    dimension_candidates = [
        column_name
        for column_name, info in schema_profile.items()
        if info.get("role") in {"dimension", "identifier"} and info.get("type") != "temporal"
    ]

    def _score_column(column_name: str, bonuses: tuple[str, ...]) -> int:
        col_norm = _normalize_prompt_token(column_name)
        score = 0
        if col_norm and col_norm in prompt_norm:
            score += 8
        score += sum(1 for token in col_norm.split() if len(token) > 2 and token in prompt_norm)
        if any(keyword in col_norm and keyword in prompt_norm for keyword in bonuses):
            score += 6
        return score

    x_axis = None
    if temporal_candidates:
        ranked_temporal = sorted(
            temporal_candidates,
            key=lambda col: _score_column(col, ("fecha", "date", "tiempo", "mes", "dia", "periodo")),
            reverse=True,
        )
        x_axis = ranked_temporal[0]

    y_axis = None
    if dimension_candidates:
        ranked_dims = sorted(
            dimension_candidates,
            key=lambda col: _score_column(col, ("tipo", "categoria", "almacen", "segmento", "canal", "grupo")),
            reverse=True,
        )
        for candidate in ranked_dims:
            if candidate != x_axis:
                y_axis = candidate
                break

    if not x_axis and dimension_candidates:
        x_axis = dimension_candidates[0]
    if not y_axis and dimension_candidates:
        for candidate in dimension_candidates:
            if candidate != x_axis:
                y_axis = candidate
                break

    return (x_axis, y_axis)


def coerce_plan_for_forced_heatmap(
    plan: AnalysisPlan,
    prompt_text: str,
    schema_profile: dict | None,
) -> AnalysisPlan:
    """
    Si el usuario fija Heatmap pero el plan no trae contrato matricial,
    lo corrige a Distribution con dos ejes + métrica para evitar salida vacía.
    """
    intent = getattr(plan, "main_intent", None)
    visual_protocol = getattr(intent, "visual_protocol", None)
    visual_value = (
        str(getattr(visual_protocol, "value", visual_protocol or "")).lower()
        if intent else ""
    )
    if visual_value not in {"heatmap", "heatmap_chart"}:
        return plan

    has_dimension = bool(getattr(intent, "dimension", None))
    has_group_by = bool(getattr(intent, "group_by", None))
    has_metric = bool(getattr(intent, "metric", None) or getattr(intent, "value_column", None))
    if has_dimension and has_group_by and has_metric:
        return plan

    x_axis, y_axis = _pick_heatmap_axes(schema_profile, prompt_text)
    metric_column = _pick_first_metric_column(schema_profile, prompt_text)
    if not (x_axis and y_axis and metric_column):
        return plan

    original_payload = plan.model_dump(mode="json")
    original_intent = original_payload.get("main_intent", {})
    forced_intent = {
        "type": "distribution",
        "rationale": original_intent.get("rationale") or "Matriz de intensidad cruzada para detectar concentración por ejes.",
        "filters": original_intent.get("filters", []),
        "metric_unit": original_intent.get("metric_unit"),
        "visual_protocol": "heatmap",
        "dimension": x_axis,
        "metric": metric_column,
        "group_by": [y_axis],
        "limit": 15,
        "barmode": "stacked",
    }
    original_payload["main_intent"] = forced_intent

    try:
        coerced_plan = AnalysisPlan.model_validate(original_payload)
        print(
            "🎯 [HEATMAP CONTRACT FIX] Plan corregido a distribución matricial "
            f"({x_axis} x {y_axis}, metric={metric_column})."
        )
        return coerced_plan
    except Exception as coercion_error:
        print(f"⚠️ [HEATMAP CONTRACT FIX] No se pudo corregir plan: {coercion_error}")
        return plan


def _normalize_prompt_rules(text: str) -> str:
    return core_normalize_prompt_rules(text)


def detect_format_override_from_prompt(prompt_text: str) -> dict:
    return core_detect_format_override_from_prompt(prompt_text)


def _humanize_table_key(raw_key: str) -> str:
    key = str(raw_key or "").replace("_", " ").strip()
    return key[:1].upper() + key[1:] if key else "Valor"


def coerce_chart_rows_to_table_rows(chart_rows, plan) -> list[dict]:
    """
    Convierte la salida analítica del motor (name/value/extra_info) a filas tabulares
    seguras, preservando el contenido útil sin depender del Chart Factory.
    """
    if not isinstance(chart_rows, list):
        return []

    intent = getattr(plan, "main_intent", None)
    aliases = getattr(plan, "column_aliases", {}) or {}
    group_by = getattr(intent, "group_by", None) or []
    dimension_source = (
        getattr(intent, "dimension", None)
        or getattr(intent, "date_column", None)
        or (group_by[0] if group_by else None)
    )
    metric_source = (
        getattr(intent, "metric", None)
        or getattr(intent, "value_column", None)
        or (getattr(intent, "metrics", [None]) or [None])[0]
    )
    dimension_label = aliases.get(dimension_source, "Categoría")
    metric_label = aliases.get(metric_source, "Valor")

    table_rows: list[dict] = []
    for row in chart_rows:
        if isinstance(row, dict):
            table_row: dict = {}
            if "name" in row:
                table_row[dimension_label] = row["name"]
            if "value" in row:
                table_row[metric_label] = row["value"]

            extra_info = row.get("extra_info", {})
            if isinstance(extra_info, dict):
                for extra_key, extra_val in extra_info.items():
                    if extra_key in {"unit_suffix", "type"}:
                        continue
                    if isinstance(extra_val, (str, int, float, bool)):
                        table_row[_humanize_table_key(extra_key)] = extra_val

            for key, value in row.items():
                if key in {"name", "value", "extra_info"}:
                    continue
                if isinstance(value, (str, int, float, bool)):
                    table_row[_humanize_table_key(key)] = value

            if table_row:
                table_rows.append(table_row)
        else:
            table_rows.append({metric_label: row})

    return table_rows

# --- CONFIGURACIÓN ---
genai.configure(api_key=settings.GEMINI_API_KEY)

# --- 1. MEMORIA Y RAG ---

def get_embedding(text):
    """Genera embedding para memoria vectorial (Modelo moderno)."""
    try:
        result = genai.embed_content(
            model="models/gemini-embedding-001",
            content=text,
            task_type="retrieval_document",
            title="Analysis Context"
        )
        return result['embedding']
    except Exception as e:
        print(f"Error embedding: {e}")
        return None

def guardar_insight_aprendido(supabase, user_id, description, code_snippet, data_dna):
    try:
        emb = get_embedding(description)
        if emb:
            data = {
                "user_id": user_id,
                "description": description,
                "sql_snippet": code_snippet,
                "embedding": emb,
                "created_at": datetime.now().isoformat(),
                "metadata": json.dumps(data_dna) 
            }
            try:
                supabase.table('historical_insights').insert(data).execute()
                print(f">>> [MEMORIA] Insight guardado.")
            except Exception as db_e:
                print(f">>> [MEMORIA] Error insertando: {db_e}")
    except Exception as e:
        print(f"Error general guardando memoria: {e}")


def _fetch_institutional_knowledge_context(
    *,
    supabase_client: Any,
    user_id: str | None,
    query: str,
) -> str:
    context_block, _ = _fetch_institutional_knowledge_payload(
        supabase_client=supabase_client,
        user_id=user_id,
        query=query,
    )
    return context_block


def _fetch_institutional_knowledge_payload(
    *,
    supabase_client: Any,
    user_id: str | None,
    query: str,
) -> tuple[str, list[Any]]:
    normalized_query = str(query or "").strip()
    if not user_id or not normalized_query:
        return "", []

    try:
        team_id = resolve_user_team_id(user_id=user_id, service_client=supabase_client)
        snippets = search_knowledge_documents(
            user_id=user_id,
            team_id=team_id,
            query=normalized_query,
            service_client=supabase_client,
            limit=settings.KNOWLEDGE_DEFAULT_TOP_K,
        )
        context_block = build_knowledge_context_block(snippets)
        if context_block:
            emit_structured_log(
                "analysis_knowledge_context_injected",
                user_id=user_id,
                team_id=team_id,
                snippet_count=len(snippets),
                query_preview=normalized_query[:160],
            )
        return context_block, snippets
    except Exception as exc:
        emit_structured_log(
            "analysis_knowledge_context_error",
            level="warning",
            user_id=user_id,
            query_preview=normalized_query[:160],
            error=str(exc)[:240],
        )
        return "", []


def _normalize_rule_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    return normalized.lower().strip()


def _parse_rule_threshold(raw_value: str, scale: str | None) -> float | None:
    text = str(raw_value or "").strip().replace(" ", "")
    if not text:
        return None
    if "," in text and "." in text:
        text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        threshold = float(text)
    except ValueError:
        return None

    normalized_scale = _normalize_rule_text(scale or "")
    if normalized_scale in {"k", "mil"}:
        threshold *= 1_000
    elif normalized_scale in {"m", "mm", "millon", "millones"}:
        threshold *= 1_000_000
    return threshold


def _split_rule_sentences(text: str) -> list[str]:
    content = str(text or "").strip()
    if not content:
        return []
    return [
        sentence.strip(" -\n\t")
        for sentence in re.split(r"(?<=[\.\!\?\n])\s+", content)
        if sentence.strip()
    ]


def _extract_action_from_rule_sentence(sentence: str) -> str:
    normalized_sentence = " ".join(str(sentence or "").split())
    if not normalized_sentence:
        return ""

    action_patterns = [
        r"(?:entonces|debe|deben|debera|deberan)\s+(?P<action>.+)$",
        r"[,;:]\s*(?P<action>[^.;]+)$",
    ]
    for pattern in action_patterns:
        match = re.search(pattern, normalized_sentence, flags=re.IGNORECASE)
        if match:
            action = match.group("action").strip(" .")
            if action:
                return action[0].upper() + action[1:]
    return normalized_sentence.strip(" .")


def _extract_institutional_rules(snippets: list[Any]) -> list[dict[str, Any]]:
    if not snippets:
        return []

    rules: list[dict[str, Any]] = []
    threshold_pattern = re.compile(
        r"(?:si|cuando)\s+(?:el|la|los|las)?\s*(?P<metric>[a-zA-Z0-9áéíóúÁÉÍÓÚñÑ_/\-\s]{2,60}?)\s+"
        r"(?P<comparator>supera|supere|pasa de|pase de|excede|exceda|sobrepasa|sobrepase|rebasa|rebase|"
        r"es mayor que|sea mayor que|es menor que|sea menor que|cae por debajo de|caiga por debajo de|"
        r"baja de|baje de|sube de|suba de|>=|<=|>|<)\s*"
        r"(?P<threshold>[\d\.,]+)\s*(?P<scale>k|m|mm|mil|millones?)?",
        flags=re.IGNORECASE,
    )

    for snippet in snippets:
        snippet_content = getattr(snippet, "content", "")
        for sentence in _split_rule_sentences(snippet_content):
            match = threshold_pattern.search(sentence)
            if not match:
                continue

            threshold_value = _parse_rule_threshold(match.group("threshold"), match.group("scale"))
            if threshold_value is None:
                continue

            comparator_raw = _normalize_rule_text(match.group("comparator"))
            direction = "gt"
            if comparator_raw in {"es menor que", "sea menor que", "cae por debajo de", "caiga por debajo de", "baja de", "baje de", "<", "<="}:
                direction = "lt"

            rules.append({
                "metric": str(match.group("metric") or "").strip(),
                "direction": direction,
                "threshold": threshold_value,
                "action": _extract_action_from_rule_sentence(sentence),
                "source_sentence": sentence.strip(),
                "document_title": getattr(snippet, "document_title", "Documento institucional"),
                "document_file_name": getattr(snippet, "document_file_name", ""),
            })

    return rules


def _extract_numeric_observations(payload: Any) -> list[float]:
    observations: list[float] = []

    def _walk(value: Any) -> None:
        if isinstance(value, bool) or value is None:
            return
        if isinstance(value, (int, float)):
            observations.append(float(value))
            return
        if isinstance(value, dict):
            for key, child in value.items():
                if str(key).lower() in {"value", "total", "metric_value", "amount", "count", "stock"}:
                    _walk(child)
            return
        if isinstance(value, list):
            for item in value:
                _walk(item)

    _walk(payload)
    return [value for value in observations if np.isfinite(value)]


def _build_compliance_metric_context(*, actual_prompt: str, plan: Any) -> str:
    tokens = [str(actual_prompt or ""), str(getattr(plan, "title", "") or "")]
    intent = getattr(plan, "main_intent", None)
    if intent is not None:
        for attr in ("metric", "value_column"):
            value = getattr(intent, attr, None)
            if value:
                tokens.append(str(value))
        for value in list(getattr(intent, "metrics", None) or []):
            if value:
                tokens.append(str(value))
    return _normalize_rule_text(" ".join(tokens))


def _evaluate_institutional_compliance(
    *,
    snippets: list[Any],
    actual_prompt: str,
    plan: Any,
    ibis_output: dict[str, Any],
) -> dict[str, Any]:
    rules = _extract_institutional_rules(snippets)
    if not rules:
        return {"matched": False}

    metric_context = _build_compliance_metric_context(actual_prompt=actual_prompt, plan=plan)
    observed_values = _extract_numeric_observations(ibis_output.get("data", []))
    if not observed_values:
        observed_values = _extract_numeric_observations(ibis_output.get("hard_facts", {}))
    if not observed_values:
        return {"matched": False, "rules_detected": len(rules)}

    best_observed = max(observed_values)
    best_match: dict[str, Any] | None = None

    for rule in rules:
        normalized_metric = _normalize_rule_text(rule.get("metric", ""))
        if normalized_metric and normalized_metric not in metric_context:
            continue

        threshold = float(rule["threshold"])
        direction = str(rule["direction"])
        matched = best_observed > threshold if direction == "gt" else best_observed < threshold
        if not matched:
            continue

        best_match = {
            "matched": True,
            "observed_value": best_observed,
            "threshold": threshold,
            "direction": direction,
            "action": str(rule["action"]),
            "rule_sentence": str(rule["source_sentence"]),
            "document_title": str(rule["document_title"]),
            "document_file_name": str(rule["document_file_name"]),
        }
        break

    if best_match:
        emit_structured_log(
            "analysis_institutional_rule_enforced",
            document_title=best_match["document_title"],
            observed_value=best_match["observed_value"],
            threshold=best_match["threshold"],
            direction=best_match["direction"],
            action=best_match["action"][:180],
            rule_sentence=best_match["rule_sentence"][:220],
        )
        return best_match

    return {
        "matched": False,
        "rules_detected": len(rules),
        "observed_value": best_observed,
    }


def _force_markdown_action_block(text: str, mandated_action: str) -> str:
    content = str(text or "").strip()
    action_line = f"**Acción:** {mandated_action.strip()}"
    if not content:
        return action_line

    replacement_pattern = re.compile(
        r"\*\*Acción:\*\*.*?(?=\n\*\*|\n##|\Z)",
        flags=re.IGNORECASE | re.DOTALL,
    )
    if replacement_pattern.search(content):
        return replacement_pattern.sub(action_line, content, count=1)
    return f"{content}\n{action_line}"

# --- 2. GUARDIÁN SEMÁNTICO (ARQUITECTURA MACRO: DISCRIMINADOR SEMÁNTICO) ---

def fetch_team_glossary(supabase_client):
    """
    Descarga el 'Cerebro del Equipo' y clasifica el conocimiento en dos niveles:
    1. NIVEL INGENIERÍA: Sinónimos técnicos simples para limpieza de datos.
    2. NIVEL ESTRATEGIA: Reglas de negocio, fórmulas y lógica compleja para el LLM.
    """
    try:
        response = supabase_client.table('business_glossary').select('term, definition').execute()
        glossary_map = {}
        
        # A. Conceptos Técnicos (Solo para limpieza automática de columnas)
        target_concepts = {
            'fecha': ['fecha', 'date', 'day', 'time', 'periodo'],
            'fecha_vencimiento': ['vencimiento', 'caducidad', 'expiración', 'expiry'],
            'stock': ['stock', 'inventario', 'existencia', 'saldo', 'disponible', 'on_hand'],
            'sku': ['sku', 'código', 'id', 'identificador', 'material', 'item', 'referencia'],
            'costo': ['costo', 'cost', 'precio', 'valor', 'importe', 'monto']
        }

        # B. Detonadores de Lógica (Si aparece esto, es una Regla de Negocio, no un sinónimo)
        logic_triggers = [
            'calcular', 'considerar', 'equivale', 'representa', 'es la suma', 'restar', 'multiplicar', 'dividir', # Verbos
            '+', '-', '*', '/', '%', '>', '<', '=', # Operadores
            'donde', 'cuando', 'si ', 'entonces' # Condicionales
        ]

        for item in response.data:
            term_raw = str(item['term']).strip()
            definition_raw = str(item['definition']).strip()
            definition_lower = definition_raw.lower()
            
            # --- FASE 1: DETECCIÓN DE COMPLEJIDAD (Regla de Negocio) ---
            # Si la definición contiene matemática o lógica, es SAGRADA. No la tocamos.
            is_complex_rule = any(trigger in definition_lower for trigger in logic_triggers)
            
            if is_complex_rule:
                # Es una regla (ej: "Calorías * 0.5"). La pasamos intacta al Cerebro.
                glossary_map[term_raw] = definition_raw
                continue # Saltamos al siguiente término, ya está clasificado.

            # --- FASE 2: NORMALIZACIÓN TÉCNICA (Solo si no es compleja) ---
            # Si llegamos aquí, es probable que sea un sinónimo simple (ej: "Qty" es "Stock").
            mapped_col = None
            for technical_name, keywords in target_concepts.items():
                # Verificamos si la definición encaja en nuestros conceptos base
                if any(kw in definition_lower for kw in keywords):
                    mapped_col = technical_name
                    break 
            
            if mapped_col:
                # Es técnico. Normalizamos la clave para que el DataEngine la encuentre.
                term_clean = term_raw.lower().replace(' ', '_').replace('/', '_').replace('.', '')
                glossary_map[term_clean] = mapped_col
            else:
                # No es compleja, pero tampoco encaja en nuestros técnicos.
                # Ante la duda, la guardamos intacta para el LLM (Mejor prevenir que borrar).
                glossary_map[term_raw] = definition_raw
                
        print(f">>> [GLOSARIO] Conocimiento cargado: {len(glossary_map)} términos.")
        return glossary_map
        
    except Exception as e:
        print(f">>> [GLOSARIO] Error de carga (No crítico): {e}")
        return {}

def detect_data_dna(df):
    cols = [str(c).lower().strip() for c in df.columns]
    money_terms = ['precio', 'costo', 'venta', 'revenue', 'monto', 'importe', 's/.', '$', 'usd', 'price', 'cost']
    risk_terms = ['vencimiento', 'caducidad', 'expiry', 'fecaduc', 'fecha_venc']
    id_terms = ['id', 'cod', 'sku', 'ean', 'lote', 'batch', 'dni', 'ruc', 'order', 'material']
    
    has_money = any(any(t in c for t in money_terms) for c in cols)
    has_risk = any(any(t in c for t in risk_terms) for c in cols)
    
    forbidden_sums = [c for c in df.columns if any(t in str(c).lower() for t in id_terms) and pd.api.types.is_numeric_dtype(df[c])]
    
    return {
        "ADN_FINANCIERO": has_money,
        "ADN_RIESGO": has_risk,
        "COLUMNAS_PROHIBIDAS_SUMA": forbidden_sums,
        "COLUMNAS_DETECTADAS": list(df.columns)
    }

# --- 3. HERRAMIENTAS ANALÍTICAS ---

def forecast_series(df, date_col, value_col, horizon_months=3):
    try:
        if df.empty or len(df) < 4: return [{"error": "Datos insuficientes para forecast (min 4 periodos)."}]
        df[date_col] = pd.to_datetime(df[date_col], errors='coerce')
        df = df.sort_values(date_col)
        freq = 'M' if horizon_months > 2 else 'W'
        series = df.set_index(date_col)[value_col].resample(freq).sum().fillna(0)
        
        model = ExponentialSmoothing(series, trend='add', seasonal='add', seasonal_periods=4, damped_trend=True).fit()
        forecast = model.forecast(horizon_months)
        
        history = series.reset_index(); history.columns = ['fecha', 'valor']; history['tipo'] = 'Histórico'
        future = forecast.reset_index(); future.columns = ['fecha', 'valor']; future['tipo'] = 'Proyección'
        
        result = pd.concat([history, future]).sort_values('fecha')
        result['fecha'] = result['fecha'].dt.strftime('%Y-%m-%d')
        return result.to_dict(orient='records')
    except Exception as e: return [{"error": f"Error Forecast: {str(e)}"}]

def detect_anomalies(df, value_col, contamination=0.05):
    try:
        # Usar el motor robusto de Fase 2
        result_df = PredictiveEngine.detect_anomalies(df, value_col, contamination=contamination)
        
        # Filtrar solo anomalías
        if 'is_anomaly' in result_df.columns:
            anomalies = result_df[result_df['is_anomaly']]
            # Retornar top 50 por valor absoluto (para mostrar las más impactantes)
            return anomalies.nlargest(50, value_col).to_dict(orient='records')
        
        return []
    except Exception as e: return [{"error": str(e)}]

def analyze_key_drivers(df, target_col):
    try:
        df_clean = df.copy().dropna(subset=[target_col])
        numeric_cols = df_clean.select_dtypes(include=[np.number]).columns.drop(target_col, errors='ignore').tolist()
        if not numeric_cols: return []
        X = df_clean[numeric_cols].fillna(0); y = df_clean[target_col]
        rf = RandomForestRegressor(n_estimators=50, max_depth=5); rf.fit(X, y)
        imps = pd.DataFrame({'feature': numeric_cols, 'importance': rf.feature_importances_})
        return imps.sort_values('importance', ascending=False).head(5).to_dict(orient='records')
    except Exception as e: return [{"error": str(e)}]

# --- 4. DATA ENGINEERING ---

def detect_header_row(df_raw):
    """Detecta heurísticamente la fila de cabecera real en Excels sucios."""
    for i in range(min(10, len(df_raw))):
        # Cuenta cuántas celdas en la fila tienen texto válido (longitud > 1)
        row_valid_count = df_raw.iloc[i].dropna().astype(str).map(len).gt(1).sum()
        # Si hay más de 1 columna con datos, asumimos que es la cabecera
        if row_valid_count > 1: return i
    return 0

def preprocess_dataframe(df, dynamic_glossary={}):
    """
    Protocolo de Limpieza V5 (Inteligente, Dinámico y Blindado).
    Integra Glosario del Usuario + Detección Automática de Patrones + Compatibilidad Total.
    """
    # 1. Normalización Agresiva de Nombres (Para coincidir con el Glosario y arreglar CEDI)
    df.columns = [
        str(c).strip().lower()
        .replace('á', 'a').replace('é', 'e').replace('í', 'i').replace('ó', 'o').replace('ú', 'u')
        .replace('/', '_').replace('-', '_').replace('.', '').replace(' ', '_')\
        .replace('(', '').replace(')', '')\
        .replace('$', '').replace('%', '')
        for c in df.columns
    ]
    
    # 2. Mapeo Semántico (FUSIÓN: Tu mapa actual + Nuevas reglas + Glosario)
    semantic_map = {
        # --- Tu mapa original (Mantenido para compatibilidad) ---
        'feprefercons': 'fecha_vencimiento', 'fecaduc': 'fecha_vencimiento', 
        'fe_caduc': 'fecha_vencimiento', 'expiry_date': 'fecha_vencimiento',
        'vencimiento': 'fecha_vencimiento',
        'ubicacion': 'ubicacion', 'almacen': 'almacen', 'warehouse': 'almacen', 'tienda': 'almacen',
        'texto_breve_de_material': 'descripcion', 'material': 'sku', 'item': 'sku', 'codigo': 'sku',
        'stock_disponible': 'stock', 'libre_utilizacion': 'stock', 'qty': 'stock', 'on_hand': 'stock', 'cantidad': 'stock',
        'region_country_name': 'pais', 'country': 'pais', 'territory': 'pais',
        
        # --- Nuevas reglas universales (Para arreglar CEDI) ---
        'fecaduc_feprefercons': 'fecha_vencimiento', 
        'fecha_de_stock': 'fecha', 'dia': 'fecha'
    }
    
    # ¡AQUÍ ESTÁ LA MAGIA! El conocimiento del usuario (Glosario) tiene la última palabra.
    semantic_map.update(dynamic_glossary)
    
    new_cols = {k: v for k, v in semantic_map.items() if k in df.columns}
    df = df.rename(columns=new_cols)

    # Definiciones de Seguridad
    id_keywords = ['id', 'sku', 'cod', 'dni', 'ruc', 'lote', 'batch', 'material']
    date_keywords = ['fecha', 'date', 'time', 'periodo', 'vencimiento', 'caducidad', 'fec']
    num_keywords = ['stock', 'cantidad', 'valor', 'precio', 'costo', 'peso', 'altura', 'variacion', 'balance', 'importe', 'monto', 'total']
    
    for col in df.columns:
        col_str = str(col).lower()
        
        # A. Limpieza básica de errores Excel (Paso previo obligatorio)
        df[col] = df[col].replace([r'^#.*!', r'^#N/A', 'nan', 'NaN', 'null'], np.nan, regex=True)

        # B. BLINDAJE ID (Tus SKUs son sagrados)
        if any(k in col_str for k in id_keywords):
            df[col] = df[col].astype(str).str.strip().replace('nan', '')
            continue 

        # C. BLINDAJE TEMPORAL AVANZADO (Nombre O Contenido)
        # 1. ¿El nombre dice que es fecha?
        is_date_name = any(k in col_str for k in date_keywords)
        is_date_content = False
        
        # 2. ¿El contenido PARECE fecha? (Esto salva fechas con nombres raros)
        if df[col].dtype == 'object' and not is_date_name:
            # Revisa una muestra de 10 filas
            sample = df[col].dropna().head(10).astype(str)
            # Regex para YYYY-MM-DD o DD/MM/YYYY
            if sample.str.match(r'(\d{4}-\d{2}-\d{2})|(\d{2}/\d{2}/\d{4})').sum() > 5:
                is_date_content = True

        if is_date_name or is_date_content:
            # Si es fecha, convertimos AHORA y saltamos al siguiente.
            # Así el regex numérico JAMÁS destruirá esta columna.
            df[col] = pd.to_datetime(df[col], errors='coerce')
            continue 

        # D. NORMALIZACIÓN CATEGÓRICA (Tu lógica original para textos)
        if df[col].dtype == 'object' and not any(k in col_str for k in num_keywords):
            df[col] = df[col].astype(str).str.strip().str.title().replace('Nan', '')

        # E. LIMPIEZA NUMÉRICA QUIRÚRGICA (Tu lógica V4 original)
        is_semantic_num = any(k in col_str for k in num_keywords)
        
        if is_semantic_num or df[col].dtype == 'object':
            if df[col].dtype == 'object':
                # Punto 1: Separador Tramposo
                clean_col = df[col].astype(str).str.replace(r'\s+[-]\s+', ' ', regex=True)
                
                # Punto 2: Ruido Financiero
                clean_col = clean_col.str.replace(r'[^\d.,-]', '', regex=True)
                
                # Punto 8: Dilema Punto y Coma
                def clean_regional(val):
                    if not val: return val
                    if ',' in val and '.' in val:
                        if val.rfind(',') > val.rfind('.'): return val.replace('.', '').replace(',', '.')
                        else: return val.replace(',', '')
                    elif ',' in val: return val.replace(',', '.')
                    return val

                df[col] = pd.to_numeric(clean_col.apply(clean_regional), errors='coerce')
            else:
                df[col] = pd.to_numeric(df[col], errors='coerce')

            # Relleno de stock vacío con 0
            if 'stock' in col_str or 'cantidad' in col_str:
                df[col] = df[col].fillna(0)
            
    return df

def get_dataframe_from_storage(supabase, file_id, glossary_map={}):
    """
    Lee el archivo crudo del Storage. 
    NOTA: Ya NO limpiamos aquí. La limpieza la hará el DataEngine en el siguiente paso.
    """
    resp = supabase.table('uploaded_files').select('storage_path').eq('id', file_id).single().execute()
    file_bytes = supabase.storage.from_('dash-uploads').download(resp.data['storage_path'])
    f_io = io.BytesIO(file_bytes)
    
    audit_log = []
    dfs = {}
    
    try:
        # Intentamos Excel (Multisheet)
        try:
            xls = pd.ExcelFile(f_io)
            for sheet in xls.sheet_names:
                # Lectura CRUDA, sin header heurístico (pandas default)
                # El DataEngine se encargará de normalizar después
                df_sheet = pd.read_excel(xls, sheet_name=sheet) 
                dfs[sheet] = df_sheet
        except:
            # Fallback a CSV
            f_io.seek(0)
            df = pd.read_csv(f_io, encoding='latin-1', on_bad_lines='skip')
            dfs['principal'] = df
            
        return dfs, audit_log
    except Exception as e:
        raise Exception(f"Error crítico leyendo archivo raw: {str(e)}")

# --- 5. CEREBRO ESTRATÉGICO (SINCRONIZADO) ---

# 👇 AGREGAMOS 'memory_context' A LOS ARGUMENTOS 👇
def planificar_estrategia(model, prompt, data_info, adn, glossary_context, gen_config, facts_json, memory_context=""):
    reglas_financieras = "✅ Datos financieros detectados." if adn['ADN_FINANCIERO'] else "⛔ PROHIBIDO calcular dinero ($)."
    
    planning_prompt = f"""
    Actúa como un **Gerente de Estrategia de Datos (C-Level)**.
    
    CONTEXTO DEL NEGOCIO (GLOSARIO): {glossary_context}
    
    # 👇 INFORMACIÓN CRÍTICA DE MEMORIA 👇
    ESTADO DE LA CONVERSACIÓN: {memory_context}
    (Si hay un estado previo, TU PRIORIDAD es mantener ese enfoque. No cambies de categoría/tema a menos que el usuario lo pida explícitamente).
    
    # 👇 [CAMBIO 2: CANDADO ANTI-ALUCINACIONES] 👇
    REGLAS DE HONESTIDAD (OBLIGATORIAS):
    1. Si el usuario pide un término (ej: "Ventas", "Costo") y NO existe en las columnas NI en el Glosario:
       - ⛔ ESTÁ PROHIBIDO inventar métricas sustitutas (ej: No uses "Calorías" como "Ventas" ni "Duración" como "Ingresos").
       - Tu "intencion" debe ser: "Reportar Falta de Información: [Término]".
       - En "pasos_tacticos" indica claramente: "No se encontró el campo '[Término]' en el archivo ni en el Glosario. Por favor, defínelo en el Glosario de Equipo."
    2. Si el término existe en el GLOSARIO, úsalo prioritariamente sobre tu intuición.
    # 👆 FIN DEL CANDADO 👆
    
    SOLICITUD ACTUAL: "{prompt}"
    DATOS DISPONIBLES: {data_info}
    HECHOS GLOBALES: {facts_json}
    RESTRICCIONES: {reglas_financieras}
    
    TU OBJETIVO: Decodificar la intención.
    Responde SOLO un JSON: {{ "intencion": "...", "pasos_tacticos": [...] }}
    """
    try:
        response = model.generate_content(planning_prompt, generation_config=gen_config)
        text = response.text.replace('```json', '').replace('```', '').strip()
        start, end = text.find('{'), text.rfind('}') + 1
        plan_json = json.loads(text[start:end]) 
        
        print("\n" + "🧠"*40)
        print(f"🧠 [ESPÍA MENTAL] INTENCIÓN: {plan_json.get('intencion')}")
        print("🧠"*40 + "\n")
        return plan_json

    except Exception as e:
        print(f"Error planificando: {e}")
        return {"intencion": "Análisis General", "pasos_tacticos": ["Explorar datos"]}

# --- 6. EJECUCIÓN COGNITIVA (NUEVO MOTOR V2) ---

def generar_analisis(dfs: dict, prompt: str, audit_log: list, user_token: str, supabase_client, user_id, parent_context: str, glossary_map: dict, topology_rules: dict, cleaning_notes: str, currency_meta: dict = {}, prev_code_override=None):
    
    # Modelo centralizado desde config.py (settings.AI_MODEL_NAME)
    model_name = settings.AI_MODEL_NAME
    config_json = { "temperature": 0.2, "top_p": 0.95, "max_output_tokens": 8192, "response_mime_type": "application/json" }
    config_code = { "temperature": 0.1, "top_p": 0.95, "max_output_tokens": 8192 }

    model = genai.GenerativeModel(model_name=model_name)
    main_df = list(dfs.values())[0]
    adn = detect_data_dna(main_df)
    cols = list(main_df.columns)

    # 👇 CORRECCIÓN OBLIGATORIA: Usar 'prev_code_override' 👇
    if prev_code_override:
        print(">>> [MODO MEMORIA] Bloqueando AutoAnalyst global. Usando contexto focalizado.")
        facts_json = json.dumps({"INFO": "DATOS GLOBALES OCULTOS POR MEMORIA"}, indent=2)
        hard_facts = {} 
    else:
        print(">>> [MODO GLOBAL] Calculando hechos duros con AutoAnalyst...")
        hard_facts = AutoAnalyst.analyze(main_df, currency_meta=currency_meta)
        facts_json = json.dumps(hard_facts, indent=2, ensure_ascii=False)
    # 👆 FIN CORRECCIÓN 👆
    
    # Contexto Enriquecido
    glossary_text = "\n".join([f"- '{k}': {v}" for k, v in glossary_map.items()]) if glossary_map else "Sin glosario."
    topology_text = "\n".join([f"- COLUMNA '{k}': {v}" for k, v in topology_rules.items()]) if topology_rules else "Sin reglas detectadas."
    
    data_info = f"""
    [METADATA CRÍTICA]
    Columnas Reales: {cols}
    [NOTAS DE LIMPIEZA AUTOMÁTICA - LEER]:
    {cleaning_notes}
    (Si el usuario pide un código, revisa si fue normalizado a MAYÚSCULAS arriba).
    [REGLAS FÍSICAS (TOPOLOGÍA)]:
    {topology_text}
    """

    # 👇 [CAMBIO 2: SINCRONIZACIÓN DE HEMISFERIOS] 👇
    # Definimos qué sabe el planificador sobre el pasado para que no alucine temas nuevos
    txt_memoria = "Sin contexto previo. Es una nueva conversación."
    
    if prev_code_override:
        # SI HAY CÓDIGO PREVIO, LE GRITAMOS AL ESTRATEGA:
        txt_memoria = (
            "⚠️ ATENCIÓN CRÍTICA: El usuario está en un 'Drill-Down' (Profundización). "
            "Ya existe un FILTRO ACTIVO en el código previo (ver prev_code_override). "
            "La intención del usuario es PROFUNDIZAR en los datos YA FILTRADOS. "
            "NO CAMBIES DE TEMA NI DE CATEGORÍA a menos que se pida explícitamente."
        )

    # Llamamos al planificador pasándole este nuevo contexto (memory_context)
    plan = planificar_estrategia(model, prompt, data_info, adn, glossary_text, config_json, facts_json, memory_context=txt_memoria)
    # 👆 FIN DEL CAMBIO 👇
    
    # 👇 [LÓGICA MAESTRA] PRIMERO CALCULAMOS LA PLANTILLA (FUERA DEL PROMPT) 👇
    base_template = "    df = dfs['principal_unificado'].copy()"
    
    code_to_inject = prev_code_override if prev_code_override else ""
    
    if code_to_inject:
        print(f"💉 [INYECCIÓN] Inyectando código de filtro previo en la plantilla.")
        clean_prev = code_to_inject.replace("import pandas as pd", "").replace("def execute_analysis(dfs):", "").strip()
        
        base_template = (
            f"    # [ESTADO RECUPERADO DE MEMORIA]\n"
            f"    # El DataFrame 'df' YA INICIA FILTRADO por tu ejecución anterior:\n"
            f"    df = dfs['principal_unificado'].copy()\n" 
            f"    {clean_prev}\n"
            f"    # [FIN ESTADO PREVIO] --------------------------------\n"
            f"    # INSTRUCCIÓN DE SEGURIDAD (VÁLVULA DE ESCAPE): \n"
            f"    # Si el usuario pide 'Analizar Todo', 'Ver Global' o cambia de tema, \n"
            f"    # IGNORA el filtro de arriba y REINICIA 'df' con: df = dfs['principal_unificado'].copy()"
        )
    # 👆 FIN LÓGICA 👇
    
    code_prompt = (
        f"Eres un **Python Data Scientist Senior & Consultor de Negocios**.\n"
        f"OBJETIVO: {plan.get('intencion')}\n\n"
        
        f"# [CONTEXTO GLOBAL (REFERENCIA)]:\n"
        f"{facts_json}\n"
        f"⚠️ INSTRUCCIÓN CRÍTICA: Estos hechos son globales. Si el usuario pregunta por algo específico (ej: 'Saldos', 'Zona Norte'), IGNORA el contexto global y GENERA CÓDIGO para filtrar el DataFrame específicamente.\n\n"

        f"GLOSARIO: {glossary_text}\n"
        f"REGLAS TOPOLÓGICAS: {topology_text}\n"
        f"DATA INFO: {data_info}\n"
        f"CONTEXTO PREVIO: {parent_context}\n\n"
        
        f"INSTRUCCIONES DE FORMATO Y ESTILO (OBLIGATORIAS):\n"
        f"1. **CERO HTML:** Está PROHIBIDO usar etiquetas como <b>, <br>. Usa SOLO Markdown (*cursiva*, **negrita**).\n"
        f"2. **IDIOMA:** Todo texto visible (títulos, ejes, análisis) DEBE estar en **ESPAÑOL DE NEGOCIOS**.\n"
        f"3. **PROFUNDIDAD:** No solo describas. Calcula desviaciones, impactos y explora el 'por qué'.\n\n"

        f"Genera la función `execute_analysis(dfs)` que retorna un JSON.\n\n"
        
        f"REGLAS DE ORO PARA CÓDIGO PYTHON:\n"
        f"1. **FILTRADO OBLIGATORIO:** Si el objetivo menciona una categoría, almacén o fecha, tu código DEBE empezar filtrando: `df = df[df['columna'] == 'Valor']`.\n"
        f"2. **SEPARACIÓN VISUAL:** PROHIBIDO mezclar métricas incompatibles (ej. Evolución vs Pareto) en un solo gráfico. Genera múltiples claves en 'chart_data' (ej. 'chart_trend', 'chart_pareto').\n"
        f"3. **SNAPSHOTS:** Para stocks/saldos, usa siempre: `df[df['Fecha'] == df['Fecha'].max()]`.\n"
        f"4. **FORMATO GRÁFICO (ECHARTS SIMPLE):**\n"
        f"   - Tu salida 'chart_data' debe ser un diccionario de configs ECharts.\n"
        f"   - NO uses librerías visuales (plotly/matplotlib). Solo estructuras de datos Python.\n"
        f"   - Usa listas simples para 'data'. No objetos complejos.\n\n"
        
        f"   EJEMPLO Barras/Líneas:\n"
        f"   {{\n"
        f"       'title': {{ 'text': 'Ventas', 'left': 'center' }},\n"
        f"       'tooltip': {{ 'trigger': 'axis' }},\n"
        f"       'xAxis': {{ 'type': 'category', 'data': ['Ene', 'Feb'] }},\n"
        f"       'yAxis': {{ 'type': 'value' }},\n"
        f"       'series': [ {{ 'data': [100, 200], 'type': 'bar', 'name': 'Total' }} ]\n"
        f"   }}\n\n"
        
        f"   EJEMPLO Pie:\n"
        f"   {{\n"
        f"       'title': {{ 'text': 'Zona', 'left': 'center' }},\n"
        f"       'tooltip': {{ 'trigger': 'item' }},\n"
        f"       'series': [ {{ 'type': 'pie', 'data': [ {{'value': 10, 'name': 'A'}}, {{'value': 20, 'name': 'B'}} ] }} ]\n"
        f"   }}\n\n"
        
        f"```python\n"
        f"import pandas as pd\n"
        f"import numpy as np\n"
        f"def execute_analysis(dfs):\n"
        # 👇 AQUÍ ESTÁ EL CAMBIO MAESTRO: INYECTAMOS LA REALIDAD 👇
        f"{base_template}\n" 
        # 👆 La IA verá el código de filtro YA ESCRITO dentro de la función
        
        f"    results = {{}}\n"
        f"    try:\n"
        f"        # Lógica de análisis...\n"
        f"        results = {{ 'status': 'success', 'chart_data': {{...}}, 'summary': {{...}} }}\n"
        f"    except Exception as e:\n"
        f"        results = {{ 'status': 'error', 'message': str(e) }}\n"
        f"    return results\n"
        f"```"
    )
    
    # ... (El resto del bloque try/catch/exec y la parte de Síntesis Visual se mantienen IGUALES) ...
    # (Solo asegúrate de pasar cleaning_notes en los argumentos al inicio)
    
    # ... COPIAR RESTO DE TU LÓGICA DE EJECUCIÓN EXISTENTE AQUÍ ...
    # ... (No cambia nada de la ejecución ni de ChartFactory) ...
    
    # PARCHE RÁPIDO PARA MANTENER TU CÓDIGO:
    # Copia el bloque try/exec y synthesis de tu archivo actual, son compatibles.
    # Lo único nuevo es el 'code_prompt' mejorado de arriba.
    
    # --- Retorno del código original para que no te pierdas ---

    # 🔄 1. BUCLE DE REINTENTO (MECANISMO DE ESTABILIDAD)
    # Intentamos 2 veces. Si falla el código a la primera, la IA se corrige sola.
    max_retries = 2
    final_results = {}

    results = {"status": "error", "message": "No iniciado"}
    code = None
    
    for attempt in range(max_retries):
        try:
            print(f"🔄 [INTENTO {attempt + 1}/{max_retries}] Generando y ejecutando código...")
            
            # Si estamos en el segundo intento (attempt > 0), le damos una pista del error a la IA
            current_prompt = code_prompt
            if attempt > 0 and 'error_msg' in locals():
                current_prompt += f"\n\nATENCIÓN: El intento anterior falló con este error: {error_msg}. CORRIGE EL CÓDIGO."

            # Generación
            resp_code = model.generate_content(current_prompt, generation_config=config_code)
            text_response = resp_code.text
            match = re.search(r'```python(.*?)```', text_response, re.DOTALL)
            code = match.group(1).strip() if match else text_response.replace('```python','').replace('```','').strip()

            # Espía Código (Solo mostramos el primero para no ensuciar la terminal)
            if attempt == 0:
                print("\n" + "🐍"*40)
                print("🐍 [ESPÍA CÓDIGO] Código propuesto por la IA:")
                print(code[:300] + "..." if len(code) > 300 else code)
                print("🐍"*40 + "\n")

            # Ejecución
            safe_globals = {
                'pd': pd, 'np': np, 'dfs': dfs, 
                'forecast_series': forecast_series, 
                'detect_anomalies': detect_anomalies, 
                'analyze_key_drivers': analyze_key_drivers, 
                'final_results': {}
            }
            exec(f"{code}\n\nfinal_results = execute_analysis(dfs)", safe_globals)
            results = safe_globals.get('final_results', {})

            # Evaluación de Éxito
            if results.get('status') == 'success':
                print(f"✅ Éxito en el intento {attempt + 1}")
                break # ¡Salió bien! Rompemos el bucle
            else:
                error_msg = results.get('message', 'Error lógico desconocido')
                print(f"⚠️ Fallo Lógico en intento {attempt + 1}: {error_msg}")

        except Exception as e:
            error_msg = str(e)
            print(f"🔥 Error Técnico en intento {attempt + 1}: {error_msg}")
            results = {"status": "error", "message": error_msg}
            # El bucle continuará al siguiente intento automáticamente

    # 🌉 2. PUENTE AUTOMÁTICO V5 (CORREGIDO: DETECCIÓN INTELIGENTE)
    ai_generated_charts = False
    if isinstance(results, dict) and results.get('chart_data'):
        if 'injected_charts' not in results: results['injected_charts'] = []
        
        raw_data = results['chart_data']
        charts_to_process = {}

        # --- [CORRECCIÓN QUIRÚRGICA: DETECCIÓN DE GRÁFICO ÚNICO] ---
        # Si la IA mandó el gráfico directo (tiene 'series' o 'xAxis' en la raíz), lo empaquetamos.
        # Esto evita que Python "descuartice" el gráfico iterando sobre 'title', 'tooltip', etc.
        is_single_chart = 'series' in raw_data or 'xAxis' in raw_data or 'yAxis' in raw_data
        
        if is_single_chart:
            print("🧠 [PUENTE] Detectado gráfico único directo (Estructura Plana). Empaquetando...")
            # Lo envolvemos en un diccionario con un nombre genérico para que el bucle funcione
            charts_to_process = {"Análisis Visual": raw_data}
        else:
            # Si no es plano, asumimos que ya viene como diccionario de gráficos {"Grafico1": {...}}
            charts_to_process = raw_data
        # ------------------------------------------------------------
        
        print(f"🧠 [PUENTE] Procesando {len(charts_to_process)} gráficos reales...")
        
        for key, option in charts_to_process.items():
            # Limpieza básica de títulos
            clean_title = key.replace('_', ' ').replace('chart', '').title().strip()
            
            # Validación de Seguridad: Si 'option' no es un diccionario (ej: es un string), lo saltamos
            if not isinstance(option, dict):
                continue

            results['injected_charts'].append({
                "type": "configuracion_echarts",
                "title": clean_title,
                "option": option
            })
        ai_generated_charts = True

    # 💉 3. INYECCIÓN DE RESPALDO (SOLO SI FALLÓ TODO)
    if not ai_generated_charts and 'hard_facts' in locals() and hard_facts and isinstance(results, dict):
        print("⚠️ [RESPALDO] La IA no generó gráficos. Activando Inyección Global.")
        if 'injected_charts' not in results: results['injected_charts'] = []
        
        if hard_facts.get('pareto'):
            p = hard_facts['pareto'][0]
            if 'chart_data' in p:
                opt = ChartFactory.build_pareto_chart(f"Pareto Global: {p['dimension']}", p['chart_data'])
                results['injected_charts'].append({"type": "configuracion_echarts", "title": f"Concentración ({p['dimension']})", "option": opt})

        if hard_facts.get('tendencias'):
            for t in hard_facts['tendencias']:
                if 'chart_data' in t:
                    opt = ChartFactory.build_line_chart(f"Tendencia Global: {t['metrica']}", t['chart_data'])
                    results['injected_charts'].append({"type": "configuracion_echarts", "title": f"Evolución Histórica", "option": opt})

    # Validaciones Finales
    if results.get('status') == 'error': return [{"type": "error_analitico", "content": results.get('message')}]
    if user_id and results.get('status') == 'success': guardar_insight_aprendido(supabase_client, user_id, f"Analysis: {prompt}", code, adn)

    # 🎨 SÍNTESIS VISUAL Y RECUPERACIÓN (CORREGIDO Y REORDENADO)
    
    # 1. PRIMERO: Definimos la lista y recuperamos los gráficos inyectados (Python necesita esto ANTES del prompt)
    hydrated = []
    if isinstance(results, dict) and results.get('injected_charts'):
        print(f"📦 [RECUPERACIÓN] Agregando {len(results['injected_charts'])} gráficos inyectados al reporte.")
        hydrated.extend(results['injected_charts'])

    # 2. SEGUNDO: Ahora sí podemos preguntar 'if hydrated' para configurar el prompt sin errores
    instruccion_visual = ""
    if hydrated:
        # LÓGICA ANTI-DUPLICADOS: Si ya hay gráficos, prohibimos a la IA hacer más.
        instruccion_visual = 'NO generes objetos "chart_template". Solo genera "mensaje_resumen". Los gráficos ya fueron inyectados.'
    else:
        # Si no hay gráficos, le damos permiso de intentarlo.
        instruccion_visual = 'Si es necesario, puedes generar objetos "chart_template" para visualizar datos.'

    # 3. TERCERO: Prompt de Síntesis ESTRATÉGICA (Nivel C-Level) — [+ UAT Transparency Refactor]
    syn_prompt = f"""
    Actúa como un **Consultor de Negocios Senior**.
    
    CONTEXTO TÉCNICO: Se han generado los siguientes datos y gráficos (Python):
    INPUT: {json.dumps(results, cls=CustomEncoder)[:50000]}
    
    TU MISIÓN OBLIGATORIA:
    1. Interpreta los datos con visión de negocio.
    2. Genera un objeto "mensaje_resumen" que tenga la siguiente ESTRUCTURA DE ALTO IMPACTO (Formato Markdown):
       - **Titular del Hallazgo:** Una frase potente que resuma lo más importante.
       - **Análisis Detallado:** Mínimo 2 párrafos explicando qué pasó, por qué pasó y qué significan los números.
       - **Recomendación:** Una acción concreta basada en el dato.
    
    3. {instruccion_visual} (Si ya hay gráficos inyectados, úsalos como evidencia en tu texto).
    
    PROTOCOLO DE TRANSPARENCIA NARRATIVA (OBLIGATORIO):
    - TRAZABILIDAD: PROHIBIDO usar términos opacos como "segmento principal", "grupo líder". Si agrupas elementos, NÓMBRALOS individualmente. Ej: "Los 3 productos principales (A, B y C) suman X".
    - JUSTIFICACIÓN DE UNIVERSOS: Si citas un total, especifica a qué corresponde y diferéncialos del total global. Ej: "De las 5,386,970 unidades totales, los Top 10 productos concentran 2,839,784".
    - LENGUAJE DE NEGOCIO: Escribe para un gerente o analista junior. CERO jerga de Data Science. Si haces una operación matemática, explícala con palabras simples.
    - INTEGRIDAD DE UNIDADES: Si la métrica es física (Stock, Cantidad, Volumen, Unidades), PROHIBIDO usar símbolos de moneda ($, €) o términos financieros (capital, portafolio, ingresos). Usa moneda SOLO si la métrica implica dinero (Precio, Costo, Venta, Ingreso, Monto).
    
    FORMATO DE SALIDA (JSON Puro):
    [
      {{ 
        "type": "mensaje_resumen", 
        "content": "### 🚀 Titular Impactante\\n\\n**Análisis:** Aquí el texto profundo...\\n\\n💡 **Recomendación:** ..." 
      }}
    ]
    """
    
    try:
        final = model.generate_content(syn_prompt, generation_config=config_json)
        text = final.text[final.text.find('['):final.text.rfind(']')+1]
        final_json = json.loads(text)
        
        # 4. CUARTO: Procesamos la respuesta de texto de la IA
        for item in final_json:
            if item.get('type') == 'chart_template':
                # Solo procesamos esto si la IA ignoró la instrucción o si no había gráficos inyectados
                opt = {}
                tpl = item.get('template')
                data = item.get('data', [])
                title = item.get('title', '')
                
                if tpl == 'bar_chart': opt = ChartFactory.build_bar_chart(title, data)
                elif tpl == 'line_chart': opt = ChartFactory.build_line_chart(title, data)
                elif tpl == 'pie_chart': opt = ChartFactory.build_pie_chart(title, data)
                elif tpl == 'dual_axis_chart': opt = ChartFactory.build_dual_axis_chart(title, item.get('categories', []), item.get('bar_data', []), item.get('line_data', []), "Volumen", "Tendencia")
                
                if "error" not in opt: 
                    hydrated.append({"type": "configuracion_echarts", "title": title, "option": opt})
            else:
                # Es el resumen de texto, lo agregamos a la lista
                hydrated.append(item)

    except Exception as e: 
        print(f"Error en síntesis visual: {e}")
        # Fallback de Seguridad: Si la IA falla al hablar, al menos devolvemos los gráficos que calculó Python
        if not hydrated:
            return [{"type": "error", "content": f"Error visual: {str(e)}"}]

    # 👇 [OPTIMIZACIÓN MACRO] GUARDADO DE ADN (CÓDIGO) 👇
    # Guardamos el código ejecutado exitosamente en una capa invisible.
    # Esto servirá como el "Punto de Restauración" para el siguiente turno.
    if code:
        hydrated.append({
            "type": "internal_code_context", 
            "content": code,
            "dna": adn 
        })
    
    return hydrated

# --- 7. TAREA CELERY (VERSIÓN FINAL CORREGIDA) ---

@celery_app.task(name="perform_analysis_task")
def perform_analysis_task(task_id, file_id, prompt, user_token, runtime_route=None):
    sb = get_supabase_client()
    task_started_at = perf_counter()
    runtime_route = convert_keys_to_str(runtime_route or {})

    # 1. INICIALIZACIÓN SEGURA DE VARIABLES
    facts_json_strategy = None
    code_dna = None
    parent_analysis_summary = None  # 🧠 [FASE 3F] Huella del análisis anterior para memoria Ibis
    parent_task_id = None
    memory_router_decision = "fresh"
    traceability_plan_entries: list[dict[str, Any]] = []
    schema_profile: dict[str, Any] = {}
    currency_meta: dict[str, Any] = {}
    dataset_contract: dict[str, Any] = {}
    cleaning_notes: Any = []
    institutional_snippets: list[Any] = []
    final_error_message: str | None = None
    final_struct: dict[str, Any] | None = None
    user_id = None
    actual_prompt = prompt
    format_override = {"enabled": False}
    explicit_visual_requests: list[str] = []
    visual_probe_mode = False
    main_df = None

    try:
        sb.table('analysis_tasks').update({'status': 'processing'}).eq('id', task_id).execute()
        if runtime_route:
            runtime_prompt_type = _shadow_observer_classify_prompt_type(
                _shadow_observer_normalize_prompt(actual_prompt),
                {},
            )
            emit_structured_log(
                "analysis_runtime_route_received",
                task_id=task_id,
                file_id=file_id,
                requested_runtime=runtime_route.get("requested_runtime"),
                effective_runtime=runtime_route.get("effective_runtime"),
                decision_mode=runtime_route.get("decision_mode"),
                decision_reason=runtime_route.get("decision_reason"),
                health_status=runtime_route.get("health_status"),
            )
            try:
                track_canary_runtime_route_observed(
                    task_id=task_id,
                    file_id=file_id,
                    user_id=runtime_route.get("user_id"),
                    team_id=runtime_route.get("team_id"),
                    file_name=runtime_route.get("file_name"),
                    prompt_type=runtime_prompt_type,
                    requested_runtime=runtime_route.get("requested_runtime"),
                    effective_runtime=runtime_route.get("effective_runtime"),
                    decision_mode=runtime_route.get("decision_mode"),
                    decision_reason=runtime_route.get("decision_reason"),
                    health_status=runtime_route.get("health_status"),
                    eligible=bool(runtime_route.get("eligible")),
                    bucket_value=runtime_route.get("bucket_value"),
                    traffic_percent=runtime_route.get("traffic_percent"),
                    allowlist_match=runtime_route.get("allowlist_match"),
                    health_ready_for_functional_canary=bool(
                        runtime_route.get("health_ready_for_functional_canary")
                    ),
                )
                if runtime_route.get("requested_runtime") != runtime_route.get("effective_runtime"):
                    track_canary_runtime_route_fallback(
                        task_id=task_id,
                        file_id=file_id,
                        user_id=runtime_route.get("user_id"),
                        team_id=runtime_route.get("team_id"),
                        file_name=runtime_route.get("file_name"),
                        prompt_type=runtime_prompt_type,
                        requested_runtime=runtime_route.get("requested_runtime"),
                        fallback_runtime=runtime_route.get("effective_runtime"),
                        decision_reason=runtime_route.get("decision_reason"),
                    )
            except Exception as canary_telemetry_error:
                emit_structured_log(
                    "canonical_canary_telemetry_error",
                    level="warning",
                    task_id=task_id,
                    file_id=file_id,
                    error=str(canary_telemetry_error)[:240],
                )
            if runtime_route.get("requested_runtime") != runtime_route.get("effective_runtime"):
                emit_structured_log(
                    "analysis_runtime_route_fallback",
                    task_id=task_id,
                    file_id=file_id,
                    requested_runtime=runtime_route.get("requested_runtime"),
                    fallback_runtime=runtime_route.get("effective_runtime"),
                    decision_reason=runtime_route.get("decision_reason"),
                )

        # --- 🕵️ [ESPÍA 1] DIAGNÓSTICO DE ENTRADA ---
        print("\n" + "🕵️"*20)
        print(f"🕵️ [ESPÍA 1 - ENTRADA] Prompt Crudo Recibido: {prompt[:200]}...") 
        # ... (Diagnóstico visual opcional) ...
        print("🕵️"*20 + "\n")
        
        task_data_resp = sb.table('analysis_tasks').select('user_id').eq('id', task_id).single().execute()
        user_id = task_data_resp.data.get('user_id') if task_data_resp.data else None
        parent_context = ""
        actual_prompt = prompt 
        format_override = {"enabled": False}
        institutional_context = ""

        # -------------------------------------------------------------------------------------
        # [ORDEN CORREGIDO] 1. MEMORIA INTELIGENTE CON ROUTER SEMÁNTICO
        # -------------------------------------------------------------------------------------
        # Variable para almacenar el prompt anterior (necesitamos recuperarlo de la tarea padre)
        prev_prompt_text = "" 
        
        try:
            prompt_data = json.loads(actual_prompt)
            if isinstance(prompt_data, dict):
                actual_prompt = prompt_data.get('text', actual_prompt)
                parent_id = prompt_data.get('parent_id')
                parent_task_id = parent_id
                format_override = detect_format_override_from_prompt(actual_prompt)
                if format_override.get('enabled'):
                    print(
                        f"🧾 [FORMAT OVERRIDE] Activado → {format_override.get('renderer')} | "
                        f"motivo: {format_override.get('reason')}"
                    )
                
                if parent_id:
                    # A. Recuperamos la tarea padre para ver qué se preguntó antes
                    parent_task = sb.table('analysis_tasks').select('prompt, results_json').eq('id', parent_id).single().execute()
                    
                    if parent_task.data:
                        # Extraemos el texto del prompt anterior
                        raw_prev = parent_task.data.get('prompt', '')
                        try: prev_prompt_text = json.loads(raw_prev).get('text', raw_prev)
                        except: prev_prompt_text = raw_prev

                        # B. ROUTER DE MEMORIA (El Portero Inteligente)
                        # Preguntamos: ¿Seguimos hablando de lo mismo?
                        should_keep_memory = SemanticTranslator.evaluate_continuity(actual_prompt, prev_prompt_text)
                        
                        if should_keep_memory:
                            memory_router_decision = "keep"
                            print(f"🔗 [MEMORIA] Router aprobó continuidad. Recuperando ADN...")
                            emit_structured_log(
                                "memory_router_decision",
                                decision="keep",
                                prompt=actual_prompt[:160],
                                parent_prompt=prev_prompt_text[:160],
                            )
                            results_raw = parent_task.data.get('results_json')
                            if isinstance(results_raw, str):
                                try: results_raw = json.loads(results_raw)
                                except: results_raw = None
                            
                            # 🧠 [FASE 3F] Extraer huella del análisis anterior (schema-agnostic)
                            parent_analysis_summary = {
                                "prev_prompt": prev_prompt_text,
                                "prev_titles": [],
                                "prev_analysis": "",
                            }
                            if isinstance(results_raw, dict):
                                # Extraer títulos de chart_options (estructura real del frontend)
                                for chart_opt in results_raw.get('chart_options', []):
                                    if isinstance(chart_opt, dict):
                                        title_obj = chart_opt.get('title', {})
                                        if isinstance(title_obj, dict):
                                            t = title_obj.get('text', '')
                                            if t:
                                                parent_analysis_summary["prev_titles"].append(t)
                                        elif isinstance(title_obj, str) and title_obj:
                                            parent_analysis_summary["prev_titles"].append(title_obj)
                                # Extraer narrativa resumida (para contexto adicional)
                                analysis_text = results_raw.get('analysis', '')
                                if analysis_text:
                                    parent_analysis_summary["prev_analysis"] = analysis_text[:300]
                            
                            print(f"🧠 [MEMORIA] ADN recuperado: {len(parent_analysis_summary['prev_titles'])} gráficos del padre")
                            print(f"🧠 [MEMORIA] Títulos: {parent_analysis_summary['prev_titles']}")
                            print(f"🧠 [MEMORIA] Prompt padre: '{prev_prompt_text[:80]}...'")
                        else:
                            memory_router_decision = "reset"
                            print(f"✂️ [MEMORIA] Router detectó CAMBIO DE TEMA. Memoria reiniciada.")
                            emit_structured_log(
                                "memory_router_decision",
                                decision="reset",
                                prompt=actual_prompt[:160],
                                parent_prompt=prev_prompt_text[:160],
                            )
                            parent_context = "" # Limpiamos explícitamente
                            code_dna = None     # Anulamos el código previo
                            
        except Exception as e:
            print(f"⚠️ Error procesando memoria: {e}")

        institutional_context, institutional_snippets = _fetch_institutional_knowledge_payload(
            supabase_client=sb,
            user_id=user_id,
            query=actual_prompt,
        )

        if not format_override.get('enabled'):
            format_override = detect_format_override_from_prompt(actual_prompt)
            if format_override.get('enabled'):
                print(
                    f"🧾 [FORMAT OVERRIDE] Activado → {format_override.get('renderer')} | "
                    f"motivo: {format_override.get('reason')}"
                )
                emit_structured_log(
                    "format_override_activated",
                    prompt=actual_prompt[:200],
                    renderer=format_override.get("renderer"),
                    reason=format_override.get("reason"),
                    single_plan=format_override.get("single_plan"),
                )

        explicit_visual_requests = extract_prompt_visual_requests(actual_prompt)
        visual_probe_mode = should_enable_visual_probe_mode(actual_prompt, explicit_visual_requests)
        if explicit_visual_requests and not format_override.get("enabled"):
            emit_structured_log(
                "explicit_visual_request_detected",
                prompt=actual_prompt[:200],
                requested_visuals=explicit_visual_requests,
            )
        if visual_probe_mode:
            emit_structured_log(
                "visual_probe_mode_enabled",
                prompt=actual_prompt[:200],
                requested_visuals=explicit_visual_requests,
            )

        # -------------------------------------------------------------------------------------
        # [ORDEN CORREGIDO] 2. GLOSARIO Y LECTURA DE ARCHIVOS
        # -------------------------------------------------------------------------------------
        print(f"--- 🚀 LEYENDO ARCHIVO V3 ---")
        
        glossary_map = {}
        if user_id:
            try: glossary_map = fetch_team_glossary(sb) 
            except: pass

        parquet_path = ""
        cached_dataset = DataEngine.load_cached_dataset(file_id)
        if cached_dataset:
            main_df, parquet_path, cached_sidecar = cached_dataset
            topology_rules = getattr(main_df, 'attrs', {}).get('topology_rules', {}) or cached_sidecar.get('_topology_rules', {}) or {}
            schema_profile = getattr(main_df, 'attrs', {}).get('schema_profile', {}) or cached_sidecar.get('_schema_profile', {}) or {}
            currency_meta = getattr(main_df, 'attrs', {}).get('currency_meta', {}) or cached_sidecar.get('_currency_meta', {}) or {}
            dataset_contract = getattr(main_df, 'attrs', {}).get('semantic_contract', {}) or {}
            cleaning_notes = getattr(main_df, 'attrs', {}).get('cleaning_notes', '') or cached_sidecar.get('_cleaning_notes', '')
            emit_structured_log(
                "data_engine_cache_hit",
                file_id=file_id,
                rows=len(main_df),
                cols=len(main_df.columns),
                parquet_path=parquet_path,
            )
            print(
                f"⚡ [DATA ENGINE CACHE] Reutilizando parquet+sidecar local: "
                f"{parquet_path} ({len(main_df)} filas, {len(main_df.columns)} cols)"
            )
        else:
            # Lectura y Limpieza
            resp = sb.table('uploaded_files').select('storage_path, file_name').eq('id', file_id).single().execute()
            file_bytes = sb.storage.from_('dash-uploads').download(resp.data['storage_path'])
            
            raw_dfs = DataEngine.read_file(file_bytes, resp.data['file_name'])
            
            # Data Engine aplica limpieza + glosario (V7: includes schema_profile)
            _clean_result = DataEngine.unify_and_clean(raw_dfs, glossary_map)
            # Handle both old 4-tuple and new 5-tuple return signatures
            if len(_clean_result) == 5:
                main_df, topology_rules, cleaning_notes, currency_meta, schema_profile = _clean_result
            else:
                main_df, topology_rules, cleaning_notes, currency_meta = _clean_result
                schema_profile = {}
            dataset_contract = getattr(main_df, 'attrs', {}).get('semantic_contract', {}) or {}

            # -------------------------------------------------------------------------------------
            # [FASE 1.1] PERSISTENCIA PARQUET
            # -------------------------------------------------------------------------------------
            parquet_path = DataEngine.commit_to_parquet(main_df, file_id)

        # -------------------------------------------------------------------------------------
        # [FASE 1.2] CEREBRO HÍBRIDO (SEMANTIC KERNEL)
        # Ahora sí es seguro: 'actual_prompt' está limpio y 'code_dna' verificado.
        # -------------------------------------------------------------------------------------
        ibis_response = None
        
        if parquet_path and not code_dna:
            print("\n" + "🧠"*20)
            print("🧠 [SEMANTIC KERNEL] Intentando interpretación estricta (Sin alucinaciones)...")
            try:
                # [V8] Pass schema_profile + topology + DATE CONTEXT for richer context
                cached_topology_context = getattr(main_df, 'attrs', {}).get('translator_context_summary', '') if main_df is not None else ""
                topology_context = cached_topology_context or str(topology_rules)
                enriched_summary = {}
                if schema_profile and not cached_topology_context:
                    # 🧠 [FASE 4B] SEMANTIC ROLE DISCOVERY
                    # Detectar si es ENTITY (ID) o ATTRIBUTE (Categoría) basado en cardinalidad
                    for col, info in schema_profile.items():
                        role_tag = info['role']
                        if role_tag == 'dimension':
                            cardinality = int(info.get('cardinality') or 0)
                            if cardinality > 50:
                                role_tag = f"dimension [ENTITY/ID] (Card: {cardinality})"
                            else:
                                role_tag = f"dimension [ATTRIBUTE] (Card: {cardinality})"
                        enriched_summary[col] = f"{info['type']} | Role: {role_tag}"

                    topology_context = f"SCHEMA (Semantic Tags): {enriched_summary}\nTOPOLOGY: {topology_rules}"

                if institutional_context and not visual_probe_mode:
                    topology_context += f"\n{institutional_context}"

                if dataset_contract:
                    topology_context += (
                        "\nDATASET_CONTRACT: "
                        f"mode={dataset_contract.get('dataset_mode')} | "
                        f"snapshot_guard_allowed={dataset_contract.get('snapshot_guard_allowed')} | "
                        f"time_axis={dataset_contract.get('time_axis')} | "
                        f"entity_key={dataset_contract.get('entity_key')}"
                    )
                    evidence = dataset_contract.get('evidence', {})
                    topology_context += (
                        "\nDATASET_EVIDENCE: "
                        f"avg_rows_per_period={evidence.get('avg_rows_per_period')} | "
                        f"rows_at_max_ratio={evidence.get('rows_at_max_ratio')} | "
                        f"metric_at_max_ratio={evidence.get('metric_at_max_ratio')} | "
                        f"repeated_entity_ratio={evidence.get('repeated_entity_ratio')} | "
                        f"snapshot_score={evidence.get('snapshot_score')} | "
                        f"flow_score={evidence.get('flow_score')}"
                    )
                
                # 📅 [V8] DATE CONTEXT: Tell Gemini what "today" means for this dataset
                cached_reference_date = getattr(main_df, 'attrs', {}).get('reference_date') if main_df is not None else None
                if cached_reference_date:
                    ref_date = str(cached_reference_date)
                elif dataset_contract.get('snapshot_guard_allowed'):
                    # Dataset has snapshot dates → use the latest as reference
                    date_cols_for_ref = [c for c, info in schema_profile.items() if info.get('role') == 'date']
                    if date_cols_for_ref:
                        ref_col = date_cols_for_ref[0]
                        try:
                            ref_date = str(main_df[ref_col].max().date())
                        except Exception:
                            ref_date = str(pd.Timestamp.now().date())
                    else:
                        ref_date = str(pd.Timestamp.now().date())
                else:
                    ref_date = str(pd.Timestamp.now().date())
                
                topology_context += f"\nFECHA_REFERENCIA_DATASET: {ref_date}"
                topology_context += f"\nINSTRUCCIÓN: Usa FECHA_REFERENCIA_DATASET como 'hoy' para filtros temporales relativos (ej: 'próximo a vencer' = fecaduc < FECHA_REFERENCIA + 90 días). Para filtros de fecha, usa el formato ISO: YYYY-MM-DD."
                if institutional_context and institutional_context not in topology_context and not visual_probe_mode:
                    topology_context += f"\n{institutional_context}"
                
                # 🧠 [FASE 3F] Construir contexto de memoria para el Translator
                memory_text = ""
                if parent_analysis_summary and parent_analysis_summary.get('prev_prompt'):
                    analysis_snippet = parent_analysis_summary.get('prev_analysis', '')[:200]
                    visual_replacement_request = SemanticTranslator.is_visual_replacement_request(actual_prompt)
                    if visual_replacement_request:
                        memory_text = (
                            f"ANÁLISIS ANTERIOR DEL USUARIO: '{parent_analysis_summary['prev_prompt']}'\n"
                            f"RESUMEN NARRATIVO PREVIO: {analysis_snippet}\n"
                            "REGLA: conserva solo el contexto analítico y los filtros; "
                            "NO heredes títulos ni tipo de gráfico del análisis anterior."
                        )
                    elif format_override.get('enabled'):
                        memory_text = (
                            f"ANÁLISIS ANTERIOR DEL USUARIO: '{parent_analysis_summary['prev_prompt']}'\n"
                            f"RESUMEN NARRATIVO PREVIO: {analysis_snippet}\n"
                            f"REGLA: usa la memoria solo para CONTEXTO ANALÍTICO, no para heredar formato visual."
                        )
                    else:
                        titles_str = ' | '.join(parent_analysis_summary['prev_titles']) if parent_analysis_summary.get('prev_titles') else 'No disponibles'
                        memory_text = (
                            f"ANÁLISIS ANTERIOR DEL USUARIO: '{parent_analysis_summary['prev_prompt']}'\n"
                            f"GRÁFICOS GENERADOS: [{titles_str}]\n"
                            f"RESUMEN NARRATIVO PREVIO: {analysis_snippet}"
                        )
                    print(f"🧠 [MEMORIA → TRANSLATOR] Inyectando contexto: {memory_text[:150]}...")

                if memory_text and SemanticTranslator.should_bypass_memory_context(
                    actual_prompt,
                    list(main_df.columns),
                    schema_profile=schema_profile,
                ):
                    memory_text = ""
                    parent_analysis_summary = None
                    memory_router_decision = "self_contained_bypass"
                    print("⚡ [MEMORIA] Prompt autocontenido detectado. Se omite continuidad para ruta rápida.")
                    emit_structured_log(
                        "memory_context_bypassed",
                        reason="self_contained_prompt",
                        prompt=actual_prompt[:160],
                        parent_prompt=prev_prompt_text[:160],
                    )
                
                # 🧠 [FASE 3F] COMPONENTE 1: Intent Classifier (determinístico)
                memory_instruction = SemanticTranslator._classify_memory_intent(actual_prompt, memory_text)
                if format_override.get('enabled'):
                    format_instruction = format_override.get('translator_instruction', '')
                    memory_instruction = (
                        f"{memory_instruction}\n{format_instruction}".strip()
                        if memory_instruction else format_instruction
                    )
                else:
                    format_instruction = ""
                
                # 🎯 [FASE 4B] COMPONENTE 2: Dynamic Literal Filter Indexer (10k items)
                # Extraer valores únicos de columnas dimensionales con estrategia de memoria
                dimension_values = getattr(main_df, 'attrs', {}).get('literal_filter_catalog', {}) if main_df is not None else {}
                if dimension_values:
                    print(f"🎯 [LITERAL FILTER] {len(dimension_values)} columnas dimensionales indexadas (sidecar cache)")
                elif schema_profile:
                    dimension_values = {}
                    for col_name, col_info in schema_profile.items():
                        if col_info.get('role') == 'dimension' and col_name in main_df.columns:
                            try:
                                nunique = main_df[col_name].nunique()
                                
                                # Estrategia Híbrida de Memoria:
                                # 1. Strings Cortos (IDs, Códigos) → Límite 10,000 (para cubrir 'Ubicación')
                                # 2. Strings Largos (Descripciones) → Límite 1,000 (para proteger RAM)
                                
                                # Check promedio de longitud (heurística rápida)
                                sample_len = main_df[col_name].dropna().astype(str).str.len().mean()
                                
                                limit = 1000
                                if sample_len < 50: # IDs cortos
                                    limit = 10000
                                
                                if nunique <= limit:
                                    unique_vals = main_df[col_name].dropna().unique().tolist()
                                    dimension_values[col_name] = unique_vals
                                    # print(f"   ℹ️ Indexando {col_name} ({nunique} items, limit {limit})")
                            except Exception:
                                pass
                
                if dimension_values and not getattr(main_df, 'attrs', {}).get('literal_filter_catalog', {}):
                    print(f"🎯 [LITERAL FILTER] {len(dimension_values)} columnas dimensionales indexadas (Smart Limits)")
                
                literal_filters = SemanticTranslator._detect_literal_filters(actual_prompt, dimension_values)
                
                plans_result = SemanticTranslator.translate(
                    actual_prompt, 
                    list(main_df.columns), 
                    str(glossary_map),
                    topology_context,
                    memory_context=memory_text,
                    memory_instruction=memory_instruction,
                    format_instruction=format_instruction,
                    schema_profile=schema_profile,
                    dataset_contract=dataset_contract,
                )
                
                # 🎯 [FASE 3B] Multi-Plan: Normalizar siempre a lista
                if not plans_result:
                    plans_result = []
                elif not isinstance(plans_result, list):
                    plans_result = [plans_result]

                plans_result = align_plan_metrics_with_prompt(
                    plans_result,
                    actual_prompt,
                    schema_profile,
                    currency_meta,
                )

                if format_override.get('enabled') and format_override.get('single_plan') and len(plans_result) > 1:
                    print(
                        f"🧾 [FORMAT OVERRIDE] Reduciendo multi-plan a 1 plan "
                        f"({len(plans_result)} → 1) por restricción tabular explícita"
                    )
                    emit_structured_log(
                        "format_override_plan_reduction",
                        original_plan_count=len(plans_result),
                        reduced_plan_count=1,
                        prompt=actual_prompt[:200],
                    )
                    plans_result = plans_result[:1]

                if explicit_visual_requests and not format_override.get("enabled"):
                    if len(explicit_visual_requests) == 1 and len(plans_result) > 1:
                        emit_structured_log(
                            "explicit_visual_request_plan_reduction",
                            prompt=actual_prompt[:200],
                            requested_visual=explicit_visual_requests[0],
                            original_plan_count=len(plans_result),
                            reduced_plan_count=1,
                        )
                        plans_result = plans_result[:1]

                    for plan_idx, plan in enumerate(plans_result):
                        forced_visual = explicit_visual_requests[min(plan_idx, len(explicit_visual_requests) - 1)]
                        canonical_visual = normalize_visual_id(forced_visual)
                        protocol_visual = resolve_visual_protocol_value(canonical_visual)
                        try:
                            plan.main_intent.visual_protocol = VisualProtocol(protocol_visual)
                            print(
                                f"🎯 [VISUAL LOCK] Plan {plan_idx+1} fijado a "
                                f"{canonical_visual} ({protocol_visual}) por soberanía del prompt."
                            )
                        except Exception as visual_lock_error:
                            print(
                                f"⚠️ [VISUAL LOCK] No se pudo fijar '{forced_visual}' "
                                f"({protocol_visual}): {visual_lock_error}"
                            )
                
                # 🎯 [FASE 3F] Inyectar filtros literales detectados en TODOS los planes
                # [V2] El Literal Filter Indexer tiene PRIORIDAD sobre el filtro de Gemini
                # si el operador emitido por Gemini no está en el conjunto de operadores
                # soportados por IbisEngine. En ese caso, REEMPLAZA el filtro de Gemini
                # con el filtro del índice local (que siempre usa FilterOperator.EQUALS).
                _SUPPORTED_IBIS_OPS: set = {"==", "!=", "in", "not_in", "contains",
                                             "ilike", "like", "starts_with", "ends_with",
                                             "not_contains", "not_like",
                                             ">", "<", ">=", "<="}
                if literal_filters and plans_result:
                    for plan in plans_result:
                        for lf in literal_filters:
                            # Buscar si Gemini ya tiene un filtro para esta columna
                            gemini_filter = next(
                                (f for f in plan.main_intent.filters if f.column == lf.column),
                                None
                            )
                            if gemini_filter is None:
                                # No hay filtro de Gemini para esta columna → inyectar
                                plan.main_intent.filters.append(lf)
                                print(f"🎯 [LITERAL FILTER → PLAN] Inyectado: {lf.column} == '{lf.value}' en '{plan.title}'")
                            else:
                                # Hay filtro de Gemini → verificar si su operador es soportado
                                gemini_op = str(getattr(gemini_filter.operator, 'value', gemini_filter.operator) or '').strip()
                                if gemini_op not in _SUPPORTED_IBIS_OPS:
                                    # Operador de Gemini no soportado → REEMPLAZAR con el del índice
                                    plan.main_intent.filters.remove(gemini_filter)
                                    plan.main_intent.filters.append(lf)
                                    print(
                                        f"🔄 [LITERAL FILTER → REPLACE] Operador '{gemini_op}' no soportado. "
                                        f"Reemplazando filtro de Gemini por índice local: "
                                        f"{lf.column} == '{lf.value}' en '{plan.title}'"
                                    )
                                else:
                                    # [V4] GUARD: Filtro multi-valor (IN/NOT_IN con lista) del LLM
                                    # es una decisión analítica superior. NUNCA degradar a ==.
                                    if gemini_op in {"in", "not_in"} and isinstance(gemini_filter.value, list):
                                        print(
                                            f"✅ [LITERAL FILTER → SKIP] Filtro multi-valor preservado: "
                                            f"{gemini_filter.column} {gemini_op} {gemini_filter.value}"
                                        )
                                    # Operador válido pero verificar si el valor coincide exactamente
                                    elif str(gemini_filter.value).upper() != str(lf.value).upper():
                                        # El valor de Gemini no coincide con el valor real del dataset
                                        # (ej: 'ingresos' vs 'Ingreso') → REEMPLAZAR
                                        plan.main_intent.filters.remove(gemini_filter)
                                        plan.main_intent.filters.append(lf)
                                        print(
                                            f"🔄 [LITERAL FILTER → REPLACE] Valor de Gemini '{gemini_filter.value}' "
                                            f"difiere del valor real '{lf.value}'. "
                                            f"Reemplazando para columna '{lf.column}' en '{plan.title}'."
                                        )
                                    else:
                                        # Operador válido + valor coincide → conservar el de Gemini
                                        print(f"✅ [LITERAL FILTER → SKIP] Filtro de Gemini ya es correcto: "
                                              f"{gemini_filter.column} {gemini_op} '{gemini_filter.value}'")
                
                ibis_response = []  # Acumulador global para todos los planes
                
                # 🛡️ [FASE 4D] IMMUTABILITY LOCK: Extract protected columns
                # Only extract if schema_profile is available (V7 logic)
                protected_cols = []
                if schema_profile:
                    protected_cols = [
                        col for col, info in schema_profile.items() 
                        if info.get('role') in ['dimension', 'identifier'] or info.get('type') in ['categorical', 'id']
                    ]
                    if protected_cols:
                        print(f"🔒 [IMMUTABILITY LOCK] Columnas protegidas contra Auto-Cast: {protected_cols}")

                for plan_idx, plan in enumerate(plans_result):
                    plan = coerce_plan_for_forced_heatmap(
                        plan=plan,
                        prompt_text=actual_prompt,
                        schema_profile=schema_profile,
                    )
                    plans_result[plan_idx] = plan
                    print(f"🧠 [SEMANTIC KERNEL] Plan {plan_idx+1}/{len(plans_result)} Validado: {plan.main_intent.type} -> '{plan.title}'")
                    base_query_contract = build_widget_query_contract(plan, schema_profile)
                    trace_plan_entry = build_traceability_plan_entry(
                        plan=plan,
                        schema_profile=schema_profile,
                        query_contract=base_query_contract,
                    )
                    
                    # 🛡️ [V8] Anti-Hallucination + 📖 [FASE 3B] Glossary Hint as recommendation
                    if plan.glossary_hint:
                        print(f"📖 [GLOSSARY HINT] {plan.glossary_hint}")
                        ibis_response.append({
                            "type": "recomendaciones",
                            "data": [f"📖 {plan.glossary_hint}"]
                        })
                        trace_plan_entry["execution"] = {
                            "status": "skipped",
                            "reason": "glossary_hint",
                            "message": plan.glossary_hint,
                        }
                        traceability_plan_entries.append(trace_plan_entry)
                        continue  # Skip this plan but process remaining
                    
                    # Usamos IbisEngine importado - Passing protected_cols [FASE 4D]
                    ibis_output = IbisEngine.execute_plan(parquet_path, plan, protected_cols=protected_cols)
                    
                    if ibis_output and "error" not in ibis_output:
                        print("🚀 [IBIS] Ejecución exitosa (Zero-Latency).")
                        execution_summary = {
                            "status": "success",
                            "output_type": ibis_output.get('type'),
                            "row_count": len(ibis_output.get('data', [])) if isinstance(ibis_output.get('data'), list) else 0,
                            "applied_visual": None,
                            "smart_table": False,
                        }
                        
                        # ---------------------------------------------------------------------------------
                        # [FASE 2] INTELIGENCIA PREDICTIVA (FORECASTING & ANOMALIES)
                        # ---------------------------------------------------------------------------------
                        from app.services.predictive_engine import PredictiveEngine
                        # import pandas as pd  <-- Removido para evitar UnboundLocalError, usa el import global
                        
                        # Detectamos intención predictiva (Keywords o Intent Type)
                        prompt_lower = plan.title.lower() if plan.title else ""
                        is_predictive = 'predictive' in str(plan.main_intent.type) or any(x in prompt_lower for x in ['proyecci', 'futuro', 'pronostic', 'predicci', 'tendencia futura'])
                        
                        if is_predictive:
                            print("🔮 [PREDICTIVE ENGINE] Activando Forecasting...")
                            # Heurística: Buscar columnas Fecha y Valor
                            # [V7] Schema-driven column discovery (no hardcoded keywords)
                            if schema_profile:
                                date_cols = [c for c, info in schema_profile.items() if info['type'] == 'temporal']
                                num_cols = [c for c, info in schema_profile.items() if info['role'] == 'metric']
                            else:
                                date_cols = [c for c in main_df.columns if pd.api.types.is_datetime64_any_dtype(main_df[c])]
                                num_cols = [c for c in main_df.columns if pd.api.types.is_numeric_dtype(main_df[c])]
                            
                            if date_cols and num_cols:
                                # [V7] Use the first metric column (no keyword matching)
                                value_col = num_cols[0]
                                
                                # If the plan's intent references a specific column, prefer it
                                if hasattr(plan.main_intent, 'value_column') and plan.main_intent.value_column in num_cols:
                                    value_col = plan.main_intent.value_column
                                elif hasattr(plan.main_intent, 'metrics') and plan.main_intent.metrics:
                                    for m in plan.main_intent.metrics:
                                        if m in num_cols:
                                            value_col = m
                                            break
                                
                                # [V7] Data-driven aggregation: check topology rules
                                aggregation = 'sum'  # Safe default
                                col_topology = topology_rules.get(value_col, '')
                                if 'SNAPSHOT' in col_topology:
                                    aggregation = 'last'
                                    print(f"🔮 [PREDICTIVE ENGINE] Snapshot column detected → aggregation: last")

                                print(f"🔮 [PREDICTIVE ENGINE] Columna Seleccionada: {value_col} | Filas: {len(main_df)}")

                                # Usamos la columna detectada
                                raw_forecast = PredictiveEngine.forecast_series(
                                    main_df, 
                                    date_cols[0], 
                                    value_col,
                                    aggregation_method=aggregation
                                )
                                if raw_forecast:
                                    # [CRITICAL] Re-formatear para ChartFactory (encapsular metadata en extra_info)
                                    formatted_forecast = []
                                    for item in raw_forecast:
                                        formatted_forecast.append({
                                            "name": item['date'],
                                            "value": item['value'],
                                            "extra_info": {
                                                "type": item['type'],
                                                "lower_ci": item.get('lower_ci'),
                                                "upper_ci": item.get('upper_ci')
                                            }
                                        })
                                    
                                    ibis_output['data'] = formatted_forecast
                                    ibis_output['chart_type'] = 'line_chart'
                                    plan.title = f"{plan.title} (Proyección)"
                                    print(f"🔮 [PREDICTIVE ENGINE] Proyección generada: {len(formatted_forecast)} puntos.")
                                else:
                                    print("⚠️ [PREDICTIVE ENGINE] No se pudo generar forecast (datos insuficientes o error).")

                        # ibis_response initialized before loop (line 935)
                        
                        # [OPTIMIZACIÓN FASE 1] Redondeo Global (2 decimales)
                        if 'data' in ibis_output:
                            ibis_output['data'] = _recursive_round(ibis_output['data'], 2)

                        filtered_granular_df = None

                        # A. Gráficos/Tablas
                        if ibis_output.get('type') == 'echarts':
                            if format_override.get('enabled') and format_override.get('renderer') == 'tabla_datos':
                                table_rows = coerce_chart_rows_to_table_rows(ibis_output.get('data', []), plan)
                                ibis_response.append({
                                    "type": "tabla_datos",
                                    "title": plan.title,
                                    "data": table_rows,
                                })
                                print(
                                    f"🧾 [FORMAT OVERRIDE] ChartFactory omitido para '{plan.title}' "
                                    f"→ tabla_datos ({len(table_rows)} filas)"
                                )
                                emit_structured_log(
                                    "format_override_chartfactory_bypassed",
                                    plan_title=plan.title,
                                    output_type="tabla_datos",
                                    row_count=len(table_rows),
                                )
                                execution_summary["output_type"] = "table"
                                execution_summary["row_count"] = len(table_rows)
                                execution_summary["format_override_renderer"] = format_override.get('renderer')
                                trace_plan_entry["execution"] = execution_summary
                                traceability_plan_entries.append(trace_plan_entry)
                                continue
                            
                            # Extraer el dataset granular YA FILTRADO del motor Ibis
                            filtered_granular_df = ibis_output.pop('filtered_granular_df', None)
                            
                            chart_opt = {}
                            requested_visual_locked = bool(explicit_visual_requests) and not format_override.get("enabled")
                            locked_visual = (
                                explicit_visual_requests[min(plan_idx, len(explicit_visual_requests) - 1)]
                                if requested_visual_locked
                                else None
                            )
                            requested_chart_type = locked_visual or ibis_output.get('chart_type', 'bar')
                            visual_governance = build_visual_governance(
                                plan,
                                ibis_output,
                                requested_chart_type,
                                requested_visual_locked=requested_visual_locked,
                            )
                            c_type = visual_governance.get('applied_visual', requested_chart_type)
                            execution_summary["applied_visual"] = c_type
                            execution_summary["requested_visual_locked"] = requested_visual_locked

                            if visual_governance.get("strict_rejection"):
                                blocked_reason = visual_governance.get("blocked_reason") or "El visual solicitado no cumple el contrato técnico del dataset."
                                blocked_label = visual_governance.get("requested_label", requested_chart_type)
                                suggested_label = visual_governance.get("recommended_label")
                                recommendation = (
                                    f"No pude renderizar {blocked_label} para '{plan.title}'. "
                                    f"Motivo: {blocked_reason}"
                                )
                                if suggested_label:
                                    recommendation += f" Visual sugerido: {suggested_label}."

                                execution_summary["status"] = "blocked"
                                execution_summary["output_type"] = "visual_request_blocked"
                                execution_summary["blocked_reason"] = blocked_reason
                                ibis_response.append({
                                    "type": "recomendaciones",
                                    "data": [recommendation],
                                })
                                trace_plan_entry["execution"] = execution_summary
                                traceability_plan_entries.append(trace_plan_entry)
                                emit_structured_log(
                                    "visual_request_blocked",
                                    plan_title=plan.title,
                                    requested_visual=visual_governance.get("requested_visual"),
                                    blocked_reason=blocked_reason,
                                    recommended_visual=visual_governance.get("recommended_visual"),
                                )
                                continue
                            
                            # Mapeo exhaustivo de Fábrica
                            # Mapeo exhaustivo de Fábrica (Normalización V6.5 & Contexto Monetario)
                            # SOLO inyectamos moneda si la intención es financiera y tiene esa propiedad
                            metric_unit = getattr(plan.main_intent, 'metric_unit', None)
                            use_currency = currency_meta if metric_unit == 'currency' else None
                            
                            if c_type in ['line', 'line_chart']:
                                chart_opt = ChartFactory.build_line_chart(
                                    plan.title,
                                    ibis_output['data'],
                                    currency_meta=use_currency,
                                    area=False,
                                )
                            elif c_type in ['area', 'area_chart']:
                                chart_opt = ChartFactory.build_line_chart(
                                    plan.title,
                                    ibis_output['data'],
                                    currency_meta=use_currency,
                                    area=True,
                                )
                            elif c_type in ['bar', 'bar_chart']:
                                chart_opt = ChartFactory.build_bar_chart(plan.title, ibis_output['data'], currency_meta=use_currency, barmode=ibis_output.get('barmode'))
                            elif c_type in ['stacked_bar', 'stacked_bar_chart']:
                                chart_opt = ChartFactory.build_bar_chart(
                                    plan.title,
                                    ibis_output['data'],
                                    currency_meta=use_currency,
                                    barmode='stacked',
                                )
                            elif c_type in ['pie', 'pie_chart']:
                                chart_opt = ChartFactory.build_pie_chart(plan.title, ibis_output['data'], currency_meta=use_currency)
                            elif c_type in ['waterfall', 'waterfall_chart']:
                                chart_opt = ChartFactory.create_chart('waterfall', plan.title, ibis_output['data'])
                            elif c_type in ['funnel', 'funnel_chart']:
                                chart_opt = ChartFactory.build_funnel_chart(plan.title, ibis_output['data'], currency_meta=use_currency)
                            elif c_type in ['boxplot', 'boxplot_chart']:
                                chart_opt = ChartFactory.build_boxplot(plan.title, ibis_output['data'], currency_meta=use_currency, outliers=ibis_output.get('outliers'))
                            elif c_type in ['scatter', 'scatter_plot', 'scatter_chart']:
                                chart_opt = ChartFactory.create_chart('scatter', plan.title, ibis_output['data'],
                                    x_label=ibis_output.get('x_label', ''), y_label=ibis_output.get('y_label', ''))
                            elif c_type in ['bubble', 'bubble_chart']:
                                chart_opt = ChartFactory.create_chart(
                                    'bubble',
                                    plan.title,
                                    ibis_output['data'],
                                    x_label=ibis_output.get('x_label', ''),
                                    y_label=ibis_output.get('y_label', ''),
                                )
                            elif c_type in ['treemap', 'treemap_chart']:
                                chart_opt = ChartFactory.create_chart('treemap', plan.title, ibis_output['data'])
                            elif c_type in ['histogram', 'histogram_chart']:
                                chart_opt = ChartFactory.create_chart('histogram', plan.title, ibis_output['data'])
                            elif c_type in ['heatmap', 'heatmap_chart']:
                                chart_opt = ChartFactory.create_chart(
                                    'heatmap',
                                    plan.title,
                                    ibis_output['data'],
                                    x_label=ibis_output.get('x_label', ''),
                                    y_label=ibis_output.get('y_label', ''),
                                )
                            elif c_type in ['gauge', 'gauge_chart']:
                                chart_opt = ChartFactory.build_gauge_chart(plan.title, ibis_output['data'])
                            elif c_type in ['gantt', 'gantt_chart']:
                                chart_opt = ChartFactory.build_gantt_chart(plan.title, ibis_output['data'])
                            elif c_type in ['dual_axis', 'dual_axis_chart']:
                                # 🎯 [FASE 3B] Gráfico combinado: Barras (izq) + Línea (der)
                                chart_data = ibis_output['data']
                                categories = [d['name'] for d in chart_data]
                                bar_data = [d['value'] for d in chart_data]
                                line_data = [d.get('extra_info', {}).get('secondary_value', d.get('extra_info', {}).get('growth', d.get('extra_info', {}).get('yoy', 0))) for d in chart_data]
                                # Clean line_data: parse strings like '21.5%' to float
                                clean_line = []
                                for v in line_data:
                                    if isinstance(v, str) and '%' in v:
                                        try: clean_line.append(float(v.replace('%', '')))
                                        except: clean_line.append(0)
                                    elif isinstance(v, (int, float)): clean_line.append(float(v))
                                    else: clean_line.append(0)
                                bar_name = plan.column_aliases.get(getattr(plan.main_intent, 'metric', ''), 'Volumen')
                                line_name = 'Variación %'
                                chart_opt = ChartFactory.build_dual_axis_chart(
                                    plan.title, categories, bar_data, clean_line,
                                    bar_name=bar_name, line_name=line_name
                                )
                            elif c_type in ['combo', 'combo_chart']:
                                chart_data = ibis_output['data']
                                categories = [d['name'] for d in chart_data]
                                bar_data = [d['value'] for d in chart_data]
                                line_data = [d.get('extra_info', {}).get('secondary_value', d.get('extra_info', {}).get('growth', d.get('extra_info', {}).get('yoy', 0))) for d in chart_data]
                                clean_line = []
                                for v in line_data:
                                    if isinstance(v, str) and '%' in v:
                                        try: clean_line.append(float(v.replace('%', '')))
                                        except: clean_line.append(0)
                                    elif isinstance(v, (int, float)): clean_line.append(float(v))
                                    else: clean_line.append(0)
                                bar_name = plan.column_aliases.get(getattr(plan.main_intent, 'metric', ''), 'Volumen')
                                line_name = 'Variación %'
                                chart_opt = ChartFactory.build_dual_axis_chart(
                                    plan.title, categories, bar_data, clean_line,
                                    bar_name=bar_name, line_name=line_name
                                )
                            else:
                                if requested_visual_locked:
                                    print(
                                        f"⚠️ [VISUAL LOCK] Tipo '{c_type}' no tiene renderer directo; "
                                        "se bloquea el fallback silencioso."
                                    )
                                    chart_opt = None
                                else:
                                    # Fallback seguro
                                    print(f"⚠️ [IBIS] Tipo de gráfico desconocido '{c_type}'. Usando BarChart por defecto.")
                                    chart_opt = ChartFactory.build_bar_chart(plan.title, ibis_output['data'], currency_meta=use_currency)
                            
                            if isinstance(chart_opt, dict) and chart_opt.get("error"):
                                print(
                                    f"⚠️ [CHART FACTORY] Opción inválida para '{plan.title}': "
                                    f"{chart_opt.get('error')}"
                                )
                                emit_structured_log(
                                    "chart_factory_invalid_option",
                                    plan_title=plan.title,
                                    chart_type=c_type,
                                    error=str(chart_opt.get("error"))[:240],
                                )
                                chart_opt = None

                            # Solo agregamos si se generó algo válido
                            if chart_opt:
                                chart_opt['visual_governance'] = visual_governance
                                chart_opt['visual_source_payload'] = {
                                    "title": plan.title,
                                    "chart_type": c_type,
                                    "requested_chart_type": requested_chart_type,
                                    "rows": ibis_output.get('data', []),
                                    "x_label": ibis_output.get('x_label'),
                                    "y_label": ibis_output.get('y_label'),
                                    "barmode": ibis_output.get('barmode'),
                                    "metric_unit": metric_unit,
                                }

                                if visual_governance.get("override_applied"):
                                    emit_structured_log(
                                        "visual_governance_override_applied",
                                        plan_title=plan.title,
                                        requested_visual=visual_governance.get("requested_visual"),
                                        recommended_visual=visual_governance.get("recommended_visual"),
                                        applied_visual=visual_governance.get("applied_visual"),
                                        blocked_reason=visual_governance.get("blocked_reason"),
                                    )
                                elif visual_governance.get("requested_visual") != visual_governance.get("recommended_visual"):
                                    emit_structured_log(
                                        "visual_governance_advisory_emitted",
                                        plan_title=plan.title,
                                        requested_visual=visual_governance.get("requested_visual"),
                                        recommended_visual=visual_governance.get("recommended_visual"),
                                        applied_visual=visual_governance.get("applied_visual"),
                                    )

                                widget_query_contract = build_widget_query_contract(plan, schema_profile)
                                if widget_query_contract:
                                    chart_opt['query_contract'] = widget_query_contract

                                # 🏹 INSERTAR ARROW_DATA DE GRANULARIDAD
                                # Nota de arquitectura:
                                # Esta ruta se mantiene en Arrow incluso para payloads chicos porque
                                # alimenta el motor DuckDB-WASM local (cross-filter/drill-down).
                                # No existe aún un fallback JSON equivalente en frontend que preserve
                                # el mismo contrato reactivo sin regressions.
                                if filtered_granular_df is not None and not filtered_granular_df.empty:
                                    try:
                                        granular_arrow_decision = evaluate_dataframe_arrow_transport(filtered_granular_df)
                                        print(
                                            f"🏹 [ARROW DECISION] chart_granular → arrow | "
                                            f"forzado por contrato interactivo DuckDB-WASM | "
                                            f"{granular_arrow_decision['reason']}"
                                        )
                                        emit_structured_log(
                                            "arrow_transport_decision",
                                            payload_kind="chart_granular",
                                            mode="arrow",
                                            forced=True,
                                            reason=granular_arrow_decision["reason"],
                                            rows=len(filtered_granular_df),
                                            cols=granular_arrow_decision["column_count"],
                                            estimated_bytes=granular_arrow_decision["estimated_bytes"],
                                        )
                                        chart_opt['granular_arrow'] = dataframe_to_arrow_base64(filtered_granular_df)
                                        print(
                                            f"🏹 [ARROW] Gráfico enriquecido con tabla granular: "
                                            f"{len(filtered_granular_df)} filas, "
                                            f"{granular_arrow_decision['column_count']} cols, "
                                            f"{granular_arrow_decision['estimated_bytes']} bytes estimados"
                                        )
                                    except Exception as e:
                                        print(f"⚠️ [ARROW] No se pudo adjuntar granular_arrow: {e}")

                                # 📋 [FASE 2] Semáforo de Densidad + Forzado por Prompt
                                force_smart_table = should_force_smart_table_from_prompt(actual_prompt)
                                hybrid_smart_table = should_offer_hybrid_smart_table(chart_opt)
                                use_smart_table = False if requested_visual_locked and c_type != "smart_table" else (
                                    force_smart_table
                                    or should_use_smart_table(chart_opt)
                                    or hybrid_smart_table
                                )

                                if use_smart_table:
                                    execution_summary["smart_table"] = True
                                    default_view_mode = 'chart' if hybrid_smart_table and not force_smart_table else 'table'
                                    smart_payload = echarts_to_smart_table(
                                        chart_opt,
                                        plan.title,
                                        default_view_mode=default_view_mode
                                    )
                                    if smart_payload.get('row_count', 0) > 0:
                                        execution_summary["output_type"] = "smart_table"
                                        execution_summary["row_count"] = int(smart_payload.get('row_count') or 0)
                                        if 'granular_arrow' in chart_opt:
                                            smart_payload['granular_arrow'] = chart_opt['granular_arrow']
                                        if widget_query_contract:
                                            smart_payload['query_contract'] = widget_query_contract
                                        ibis_response.append(smart_payload)
                                        activation_reason = (
                                            "prompt explícito"
                                            if force_smart_table
                                            else "vista híbrida temporal"
                                            if hybrid_smart_table
                                            else "semáforo de densidad"
                                        )
                                        print(
                                            f"📋 [SMART TABLE] Activada para '{plan.title}' "
                                            f"({smart_payload['row_count']} filas, motivo: {activation_reason})"
                                        )
                                    else:
                                        print(
                                            f"⚠️ [SMART TABLE] Conversión omitida para '{plan.title}' "
                                            "(sin filas válidas). Fallback a gráfico."
                                        )
                                        execution_summary["smart_table"] = False
                                        if 'recipe_sql' in ibis_output:
                                            chart_opt['recipe_sql'] = ibis_output['recipe_sql']
                                            chart_opt['recipe_visual_protocol'] = ibis_output.get('recipe_visual_protocol')
                                        ibis_response.append({ "type": "configuracion_echarts", "title": plan.title, "option": chart_opt })
                                else:
                                    execution_summary["output_type"] = "echarts"
                                    if 'recipe_sql' in ibis_output:
                                        chart_opt['recipe_sql'] = ibis_output['recipe_sql']
                                        chart_opt['recipe_visual_protocol'] = ibis_output.get('recipe_visual_protocol')

                                    ibis_response.append({ "type": "configuracion_echarts", "title": plan.title, "option": chart_opt })
                            else:
                                print("⚠️ [IBIS] ChartFactory devolvió opciones vacías.")

                        elif ibis_output.get('type') == 'table':
                            execution_summary["output_type"] = "table"
                            ibis_response.append({ "type": "tabla_datos", "title": plan.title, "data": ibis_output['data'] })
                            
                        elif ibis_output.get('type') == 'kpi':
                            execution_summary["output_type"] = "kpi"
                            # Agregamos métricas clave directamente
                            # Convertimos el dict de datos en una lista de métricas para el reporte
                            metrics_data = ibis_output.get('data', {})
                            execution_summary["row_count"] = len(metrics_data) if isinstance(metrics_data, dict) else 0
                            ibis_response.append({ "type": "metricas_clave", "data": metrics_data })
                            
                            # 🎯 [FASE 3C] Gauge solo para % reales (0-100). Valores absolutos → solo KPI card.
                            if len(metrics_data) == 1:
                                key = list(metrics_data.keys())[0]
                                val = list(metrics_data.values())[0]
                                if isinstance(val, (int, float)):
                                    # Heurística universal: gauge solo si parece porcentaje (0-100)
                                    metric_unit = getattr(plan.main_intent, 'metric_unit', None)
                                    is_percentage = metric_unit == 'percentage' or (0 <= val <= 100)
                                    if is_percentage:
                                        gauge_opt = ChartFactory.build_gauge_chart(key, val)
                                        ibis_response.append({ "type": "configuracion_echarts", "title": key, "option": gauge_opt })
                                    else:
                                        print(f"📊 [KPI] Valor absoluto ({val:,.0f}), omitiendo gauge — se muestra como KPI card.")
                        
                        # [MEJORA FASE 1.4] NARRATIVA ESTRATÉGICA CON ARSENAL GRÁFICO
                        print("🧠 [NARRATIVA] Generando insights con Protocolos Dinámicos...")
                        
                        # 1. CANDADO DE MÉTRICA (MEMORY LOCK)
                        # Leemos la unidad que decidió el Traductor y la forzamos en la narrativa.
                        
                        # [FIX] Prioridad Absoluta: Meta-Data del DataEngine (La Verdad sobre el Archivo)
                        # Solo asignamos símbolo si REALMENTE existe en metadata. Si no, lo dejamos vacío.
                        detected_symbol = currency_meta.get('symbol') if currency_meta else None
                        detected_code = currency_meta.get('code', 'USD') if currency_meta else 'USD'
                        
                        # Regla por defecto: Si no hay moneda detectada, ASUMIMOS CANTIDAD/NEUTRO salvo evidencia contraria.
                        unit_instruction = "REGLA DE FORMATO: No detecté moneda explícita. Si hablas de 'Stock', 'Cantidad' o 'Unidades', NO uses signos de dinero ($/S/)."

                        # [MEJORA] Si tenemos moneda REAL detectada, la imponemos.
                        if detected_symbol:
                            unit_instruction = f"REGLA DE FORMATO: El archivo tiene moneda {detected_code}. Si hablas de montos, USA EL SÍMBOLO '{detected_symbol}'. Si hablas de Stock/Unidades, usa números neutros."
                        
                        # [V7] Schema-driven unit detection — no hardcoded keywords
                        # The metric_unit field from the plan intent drives this decision.
                        prompt_lower = plan.title.lower() if plan.title else ""

                        intent = plan.main_intent
                        if hasattr(intent, 'metric_unit'):
                            if intent.metric_unit == 'quantity':
                                unit_instruction = "⚠️ CANDADO ACTIVO: Estás analizando VOLUMEN FÍSICO. PROHIBIDO usar signos de moneda. Habla de 'Unidades' / 'Cajas'."
                            elif intent.metric_unit == 'currency':
                                # Si el intent es explícitamente dinero, usamos el símbolo detectado o fallback ($) solo si es necesario
                                sym = detected_symbol if detected_symbol else '$'
                                unit_instruction = f"⚠️ CANDADO ACTIVO: Estás analizando DINERO. Usa el símbolo '{sym}' para los montos."
                            elif intent.metric_unit == 'percentage':
                                unit_instruction = "⚠️ CANDADO ACTIVO: Estás analizando TASAS O MÁRGENES. Usa siempre el símbolo %."
                        
                        # [CAMBIO QUIRÚRGICO] Limpieza antes del Prompt
                        safe_narrative_data = convert_keys_to_str(ibis_output.get('data', []))
                        raw_data_str = json.dumps(safe_narrative_data, default=str)[:2500]
                        clean_data_str = clean_business_terms(raw_data_str) # <--- ¡AQUÍ LLAMAMOS AL LIMPIADOR!
                        
                        # 🧭 [FASE 3D] Contexto de Polaridad para la Narrativa
                        polarity = getattr(plan, 'metric_polarity', 'neutral')
                        polarity_instruction = ""
                        if polarity == "unfavorable":
                            polarity_instruction = "CONTEXTO DE NEGOCIO: Esta métrica es DESFAVORABLE (el negocio busca reducirla: vencimientos, merma, errores, deudas). Si la tendencia BAJA → es positivo, celebra. Si SUBE → es preocupante, alerta."
                        elif polarity == "favorable":
                            polarity_instruction = "CONTEXTO DE NEGOCIO: Esta métrica es FAVORABLE (el negocio busca aumentarla: ventas, ingresos, producción). Si la tendencia SUBE → es positivo. Si BAJA → es preocupante."
                        
                        # 🔬 [FASE 3E] Transparencia Metodológica para Predictivos
                        methodology_instruction = ""
                        intent_type = getattr(intent, 'type', '')
                        if intent_type == 'predictive':
                            methodology_instruction = (
                                "METODOLOGÍA: El pronóstico fue calculado mediante Suavización Exponencial "
                                "(Holt-Winters) con tendencia aditiva sobre los datos históricos disponibles. "
                                "Incluye en tu análisis UNA frase breve mencionando este método para dar "
                                "confianza al usuario. Ej: 'Proyección basada en Suavización Exponencial (Holt-Winters).'"
                            )
                        
                        # 📋 [FASE 3E] Contexto de Filtros Aplicados
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
                        
                        # 2. PROMPT DE NARRATIVA (Analista Ejecutivo) — [FASE 3C + UAT Transparency Refactor]
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
                                    )
                                    print(f"⚡ [NARRATIVA CACHE] Hit ({narrative_model_name})")

                            if not narrative_text:
                                emit_structured_log(
                                    "chart_narrative_model_selected",
                                    model=narrative_model_name,
                                    intent_type=intent_type,
                                    plan_title=plan.title,
                                )
                                try:
                                    narrative_model = genai.GenerativeModel(narrative_model_name)
                                    # ⚡️ [PARALLEL NARRATIVE] Ejecutamos generate_content en un hilo
                                    # para no bloquear el event-loop. Timeout de 45s por narrativa.
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
                                    print(
                                        f"⚠️ [NARRATIVA] Fallback a {settings.NARRATIVE_STRICT_MODEL_NAME} "
                                        f"tras error en {narrative_model_name}: {primary_narrative_error}"
                                    )
                                    narrative_model = genai.GenerativeModel(settings.NARRATIVE_STRICT_MODEL_NAME)
                                    narrative_resp = narrative_model.generate_content(narrative_prompt)
                                    narrative_text = str(narrative_resp.text or "").strip()

                                if mandated_action:
                                    narrative_text = _force_markdown_action_block(narrative_text, mandated_action)

                                set_cached_json(
                                    "chart_narrative",
                                    narrative_cache_key,
                                    {"content": narrative_text},
                                    settings.NARRATIVE_CACHE_TTL_SECONDS,
                                )
                            
                            ibis_response.append({
                                "type": "mensaje_resumen",
                                "content": narrative_text
                            })
                            ibis_response.append({
                                "type": "explicabilidad_analitica",
                                "data": explainability_payload,
                            })
                        except Exception as e:
                            print(f"⚠️ [NARRATIVA FALLÓ]: {e}")
                            # Fallback elegante: Si falla la narrativa, NO matamos los gráficos.
                            ibis_response.append({
                                "type": "mensaje_resumen",
                                "content": "### 📊 Análisis Calculado Exitosamente\nLos datos han sido procesados. Revisa los gráficos adjuntos para el detalle."
                            })
                            ibis_response.append({
                                "type": "explicabilidad_analitica",
                                "data": explainability_payload,
                            })
                        
                        # 🎯 [PHASE 3] PRESCRIPTIVE ENGINE — Smart Recommendations
                        try:
                            from app.services.predictive_engine import PredictiveEngine
                            hard_facts = ibis_output.get('hard_facts', {})
                            if hard_facts:
                                # 🎯 [FASE 3C] Pasar contexto temático para recomendaciones relevantes
                                analysis_context = plan.title if plan.title else ""
                                # 🧭 [FASE 3D] Pasar polaridad para invertir interpretación de tendencia
                                polarity = getattr(plan, 'metric_polarity', 'neutral')
                                recommendations = [] if visual_probe_mode else PredictiveEngine.generate_recommendations(hard_facts, context=analysis_context, polarity=polarity)
                                if recommendations:
                                    print(f"💡 [PRESCRIPTIVE] {len(recommendations)} recomendaciones generadas")
                                    ibis_response.append({
                                        "type": "recomendaciones",
                                        "data": recommendations
                                    })
                        except Exception as rx_e:
                            print(f"⚠️ [PRESCRIPTIVE] Error (no crítico): {rx_e}")
                        trace_plan_entry["execution"] = execution_summary
                        traceability_plan_entries.append(trace_plan_entry)
                    else:
                        print(f"⚠️ [IBIS] Error en ejecución: {ibis_output.get('error')}")
                        trace_plan_entry["execution"] = {
                            "status": "error",
                            "output_type": ibis_output.get('type') if isinstance(ibis_output, dict) else None,
                            "error": str(ibis_output.get('error'))[:240] if isinstance(ibis_output, dict) else "unknown",
                        }
                        traceability_plan_entries.append(trace_plan_entry)
            except Exception as sk_e:
                print(f"🔥 [SEMANTIC KERNEL] Fallo Crítico en Ibis: {sk_e}")
                # "Que el servidor falle fuerte a que el sistema me mienta"
                raise Exception(f"Falla en Motor Ibis (Titanium Guard): {sk_e}")
                
            print("🧠"*20 + "\n")
        
        if parquet_path: print(f"🌉 [PUENTE FASE 1.2] Archivo Parquet listo en: {parquet_path}")

        # --- 🕵️ [ESPÍA 3] DIAGNÓSTICO FINAL ---
        print("\n" + "🧠"*20)
        print(f"🧠 [ESPÍA 3] Motor Ibis finalizado. Resultados: {len(ibis_response) if ibis_response else 0}")
        print("🧠"*20 + "\n")

        # 4. DECISIÓN FINAL: SWICHTEO DESTRUIDO (Fase 3 Finalizada)
        if not ibis_response:
             raise Exception("IbisEngine no devolvió resultados válidos ni gráficos.")
             
        # CAMINO ÚNICO (IBIS TITANIUM)
        response = ibis_response
        status = 'completed'
        
    except Exception as e:
        print(traceback.format_exc())
        final_error_message = str(e)
        response = [{"type": "error", "content": f"Error del sistema: {str(e)}"}]
        status = 'failed'

    # LOG FINAL
    try:
        # --- FIX DASHBOARDS: Estructura Plana para Frontend ---
        # 🎯 [PHASE 3] Triple Vista: chart_options is now a LIST to support multiple charts
        final_struct = {
            "analysis": "",
            "metrics": {},
            "chart_options": [],
            "data": [],
            "recommendations": [],
            "explainability": [],
        }
        
        # Procesamos la lista heterogénea 'response'
        if isinstance(response, list):
            for item in response:
                if item.get('type') == 'mensaje_resumen':
                    final_struct['analysis'] += item.get('content', '') + "\n\n"
                elif item.get('type') == 'metricas_clave':
                    metrics_payload = item.get('data', {})
                    if isinstance(metrics_payload, dict) and metrics_payload:
                        final_struct['metrics'].update(metrics_payload)
                elif item.get('type') == 'configuracion_echarts':
                    # 🎯 [PHASE 3] Collect ALL charts, not just the first
                    chart_opt = item.get('option', {})
                    if chart_opt:
                        final_struct['chart_options'].append(chart_opt)
                elif item.get('type') == 'tabla_datos':
                    raw_data = item.get('data', [])
                    # 🏹 [FASE 3] Arrow Transport: tabla_datos con encoding binario
                    arrow_decision = evaluate_records_arrow_transport(raw_data) if raw_data else None
                    if arrow_decision:
                        print(
                            f"🏹 [ARROW DECISION] tabla_datos → {arrow_decision['mode']} | "
                            f"{arrow_decision['reason']}"
                        )
                        emit_structured_log(
                            "arrow_transport_decision",
                            payload_kind="tabla_datos",
                            mode=arrow_decision["mode"],
                            forced=False,
                            reason=arrow_decision["reason"],
                            rows=len(raw_data),
                            cols=arrow_decision["column_count"],
                            estimated_bytes=arrow_decision["estimated_bytes"],
                        )
                    if raw_data and arrow_decision and arrow_decision['use_arrow']:
                        try:
                            final_struct['arrow_data'] = records_to_arrow_base64(raw_data)
                            final_struct['arrow_row_count'] = len(raw_data)
                            final_struct['data'] = []  # Vaciar JSON para ahorrar peso
                            print(
                                f"🏹 [ARROW] tabla_datos codificada: {len(raw_data)} filas, "
                                f"{arrow_decision['column_count']} cols, {arrow_decision['estimated_bytes']} bytes estimados"
                            )
                        except Exception as arrow_err:
                            print(f"⚠️ [ARROW] Fallback a JSON: {arrow_err}")
                            final_struct['data'] = raw_data
                    else:
                        final_struct['data'] = raw_data
                elif item.get('type') == 'smart_table':
                    # 📋 [FASE 2] Smart Table se transporta dentro de chart_options
                    # El frontend lo detecta por item.type === 'smart_table'
                    # 🏹 [FASE 3] Arrow adicional para Fase 4 (DuckDB-WASM)
                    st_data = item.get('data', [])
                    arrow_decision = evaluate_records_arrow_transport(st_data) if st_data else None
                    if arrow_decision:
                        print(
                            f"🏹 [ARROW DECISION] smart_table → {arrow_decision['mode']} | "
                            f"{arrow_decision['reason']}"
                        )
                        emit_structured_log(
                            "arrow_transport_decision",
                            payload_kind="smart_table",
                            mode=arrow_decision["mode"],
                            forced=False,
                            reason=arrow_decision["reason"],
                            rows=len(st_data),
                            cols=arrow_decision["column_count"],
                            estimated_bytes=arrow_decision["estimated_bytes"],
                        )
                    if st_data and arrow_decision and arrow_decision['use_arrow']:
                        try:
                            item['arrow_data'] = records_to_arrow_base64(st_data)
                            print(
                                f"🏹 [ARROW] smart_table enriquecida: {len(st_data)} filas, "
                                f"{arrow_decision['column_count']} cols, {arrow_decision['estimated_bytes']} bytes estimados"
                            )
                        except Exception as arrow_err:
                            print(f"⚠️ [ARROW] Smart Table sin Arrow (fallback JSON): {arrow_err}")
                    final_struct['chart_options'].append(item)
                elif item.get('type') == 'recomendaciones':
                    # 🎯 [FASE 3B] Extend, don't overwrite (Multi-plan can produce multiple recommendation blocks)
                    final_struct['recommendations'].extend(item.get('data', []))
                elif item.get('type') == 'explicabilidad_analitica':
                    payload = item.get('data')
                    if isinstance(payload, dict) and payload:
                        final_struct['explainability'].append(payload)
                elif item.get('type') == 'error':
                     final_struct['analysis'] += f"⚠️ ERROR: {item.get('content')}\n"

        if isinstance(response, dict) and 'status' in response: # Fallback legacy dict
             final_struct['analysis'] = str(response)

        # 🦆 [FASE 4] SNAPSHOT ARROW: Dataset completo para DuckDB-WASM cross-filtering
        # Lee el Parquet snapshot (post-limpieza, SIEMPRE disponible) y lo serializa
        # a Arrow IPC base64. Esto da al frontend el dataset GRANULAR completo,
        # independiente del tipo de visualización que el Strategist decidió generar.
        try:
            if parquet_path:
                snapshot_df = main_df if isinstance(main_df, pd.DataFrame) and not main_df.empty else None
                snapshot_source = "memory" if snapshot_df is not None else "parquet"
                cached_snapshot_arrow = DataEngine.load_cached_snapshot_arrow(file_id)

                if snapshot_df is None:
                    snapshot_df = pd.read_parquet(parquet_path)

                if not snapshot_df.empty:
                    arrow_decision = evaluate_dataframe_arrow_transport(snapshot_df)
                    print(
                        f"🦆 [SNAPSHOT ARROW DECISION] snapshot → arrow | "
                        f"{arrow_decision['reason']}"
                    )
                    emit_structured_log(
                        "arrow_transport_decision",
                        payload_kind="snapshot",
                        mode="arrow",
                        forced=True,
                        reason=arrow_decision["reason"],
                        rows=len(snapshot_df),
                        cols=arrow_decision["column_count"],
                        estimated_bytes=arrow_decision["estimated_bytes"],
                    )
                    if cached_snapshot_arrow:
                        final_struct['snapshot_arrow'] = cached_snapshot_arrow
                        print(
                            f"⚡ [SNAPSHOT ARROW CACHE] Hit ({snapshot_source}): "
                            f"{len(snapshot_df)} filas, {len(snapshot_df.columns)} columnas"
                        )
                    else:
                        final_struct['snapshot_arrow'] = dataframe_to_arrow_base64(snapshot_df)
                        if DataEngine.persist_cached_snapshot_arrow(file_id, final_struct['snapshot_arrow']):
                            print(
                                f"💾 [SNAPSHOT ARROW CACHE] Persistido ({snapshot_source}): "
                                f"{len(snapshot_df)} filas, {len(snapshot_df.columns)} columnas"
                            )
                    final_struct['snapshot_row_count'] = len(snapshot_df)
                    final_struct['snapshot_columns'] = list(snapshot_df.columns)
                    print(f"🦆 [SNAPSHOT ARROW] Dataset completo serializado: {len(snapshot_df)} filas, {len(snapshot_df.columns)} columnas → Arrow base64")
        except Exception as snapshot_err:
            print(f"⚠️ [SNAPSHOT ARROW] No se pudo serializar (no crítico): {snapshot_err}")

        final_struct['traceability'] = build_traceability_payload(
            task_id=task_id,
            file_id=file_id,
            user_id=user_id,
            raw_prompt=prompt,
            actual_prompt=actual_prompt,
            parent_task_id=parent_task_id,
            memory_decision=memory_router_decision,
            format_override=format_override,
            schema_profile=schema_profile,
            currency_meta=currency_meta,
            institutional_snippets=institutional_snippets,
            plan_entries=traceability_plan_entries,
            final_struct=final_struct,
            status=status,
            error_message=final_error_message,
        )

        json_output = json.dumps(final_struct, cls=CustomEncoder)
        
        print("\n" + "="*40)
        print(f"🕵️ [ESPÍA BACKEND] STATUS: {status}")
        print("="*40 + "\n")
    except Exception as spy_error:
        print(f"🕵️ [ESPÍA ERROR]: {spy_error}")
        json_output = json.dumps({"analysis": str(response)}, default=str)

    live_duration_ms = int((perf_counter() - task_started_at) * 1000)
    sb.table('analysis_tasks').update({'status': status, 'results_json': json_output}).eq('id', task_id).execute()
    try:
        track_analysis_completed(
            task_id=task_id,
            file_id=file_id,
            user_id=user_id,
            status=status,
            duration_ms=live_duration_ms,
            final_struct=final_struct,
            dataset_contract=dataset_contract,
            cleaning_notes=cleaning_notes,
        )
    except Exception as telemetry_error:
        emit_structured_log(
            "enterprise_telemetry_error",
            level="error",
            task_id=task_id,
            file_id=file_id,
            status=status,
            error=str(telemetry_error)[:240],
        )
    if settings.CANONICAL_SHADOW_TRAFFIC_MIRROR_ENABLED and status == "completed":
        try:
            live_summary = build_live_runtime_summary(
                status=status,
                prompt=actual_prompt,
                final_struct=final_struct,
                dataset_contract=dataset_contract,
                live_duration_ms=live_duration_ms,
            )
            observe_canonical_shadow_runtime_task.delay(
                task_id=task_id,
                file_id=file_id,
                prompt=actual_prompt,
                live_summary=convert_keys_to_str(live_summary),
            )
        except Exception as shadow_observer_error:
            emit_structured_log(
                "canonical_shadow_runtime_observer_dispatch_error",
                level="warning",
                task_id=task_id,
                file_id=file_id,
                status=status,
                error=str(shadow_observer_error)[:240],
            )
    return status


@celery_app.task(name="observe_canonical_shadow_runtime")
def observe_canonical_shadow_runtime_task(task_id, file_id, prompt, live_summary):
    try:
        observer_summary = observe_canonical_shadow_runtime(
            task_id=task_id,
            file_id=file_id,
            prompt=prompt,
            live_summary=convert_keys_to_str(live_summary),
        )
        return observer_summary.get("observer_status", "unknown")
    except Exception as shadow_error:
        emit_structured_log(
            "canonical_shadow_runtime_observer_error",
            level="warning",
            task_id=task_id,
            file_id=file_id,
            error=str(shadow_error)[:240],
        )
        return "error"


def _save_analysis_task_result_with_payload_shedding(sb, task_id: str, runtime_result) -> None:
    json_output = json.dumps(runtime_result.final_struct, cls=CustomEncoder)
    try:
        sb.table('analysis_tasks').update(
            {'status': runtime_result.status, 'results_json': json_output}
        ).eq('id', task_id).execute()
        return
    except Exception as save_error:
        _heavy_keys = ("snapshot_arrow", "arrow_data")
        _stripped = []
        for key in _heavy_keys:
            if key in runtime_result.final_struct:
                del runtime_result.final_struct[key]
                _stripped.append(key)
        for chart_opt in runtime_result.final_struct.get("chart_options", []):
            if isinstance(chart_opt, dict) and "granular_arrow" in chart_opt:
                del chart_opt["granular_arrow"]
                _stripped.append("granular_arrow")
        if not _stripped:
            raise
        print(
            f"🛡️ [PAYLOAD SHEDDING] Reintentando guardado sin: {', '.join(_stripped)}. "
            f"Error original: {str(save_error)[:120]}"
        )
        json_output = json.dumps(runtime_result.final_struct, cls=CustomEncoder)
        sb.table('analysis_tasks').update(
            {'status': runtime_result.status, 'results_json': json_output}
        ).eq('id', task_id).execute()


@celery_app.task(name="observe_canonical_tabular_canary_runtime")
def observe_canonical_tabular_canary_runtime_task(
    task_id,
    file_id,
    prompt,
    prompt_type=None,
    requested_visual_family=None,
):
    sb = get_supabase_client()
    started_at = perf_counter()
    try:
        uploaded_file_row = (
            sb.table("uploaded_files")
            .select("id, user_id, team_id, file_name, storage_path, created_at")
            .eq("id", file_id)
            .single()
            .execute()
        )
        uploaded_row_data = dict(uploaded_file_row.data or {})
        canary_result = execute_canonical_tabular_canary_analysis(
            file_id=file_id,
            prompt=prompt,
            service_client=sb,
            uploaded_file_row=uploaded_row_data,
            prompt_type=prompt_type,
            requested_visual_family=requested_visual_family,
            max_plans=max(int(settings.CANONICAL_SHADOW_TRAFFIC_MIRROR_MAX_PLANS or 3), 1),
        )
        try:
            track_canary_runtime_execution_observed(
                task_id=task_id,
                file_id=file_id,
                user_id=str(uploaded_row_data.get("user_id") or ""),
                team_id=str(uploaded_row_data.get("team_id") or ""),
                file_name=str(uploaded_row_data.get("file_name") or ""),
                prompt_type=prompt_type,
                execution_status=canary_result.status,
                candidate_id=canary_result.execution.metadata.get("candidate_id"),
                prompt_strategy=canary_result.execution.prompt_strategy,
                chart_count=len(list(canary_result.final_struct.get("chart_options") or [])),
                duration_ms=int((perf_counter() - started_at) * 1000),
            )
        except Exception as metric_error:
            emit_structured_log(
                "canonical_tabular_background_canary_metric_error",
                level="warning",
                task_id=task_id,
                file_id=file_id,
                error=str(metric_error)[:240],
            )
        emit_structured_log(
            "canonical_tabular_background_canary_completed",
            task_id=task_id,
            file_id=file_id,
            candidate_id=canary_result.execution.metadata.get("candidate_id"),
            prompt_strategy=canary_result.execution.prompt_strategy,
            chart_count=len(list(canary_result.final_struct.get("chart_options") or [])),
            duration_ms=int((perf_counter() - started_at) * 1000),
        )
        return canary_result.status
    except Exception as canary_error:
        emit_structured_log(
            "canonical_tabular_background_canary_error",
            level="warning",
            task_id=task_id,
            file_id=file_id,
            error=str(canary_error)[:240],
            duration_ms=int((perf_counter() - started_at) * 1000),
        )
        return "error"


@celery_app.task(name="perform_analysis_task_universal_tabular")
def perform_analysis_task_universal_tabular(task_id, file_id, prompt, user_token, runtime_route=None):
    sb = get_supabase_client()
    task_started_at = perf_counter()
    runtime_route = convert_keys_to_str(runtime_route or {})
    prompt_type = _shadow_observer_classify_prompt_type(
        _shadow_observer_normalize_prompt(prompt),
        {},
    )
    prompt_visual_requests = extract_prompt_visual_requests(prompt)
    requested_visual_family = normalize_visual_id(prompt_visual_requests[0]) if prompt_visual_requests else None
    production_executor_enabled = bool(settings.UNIVERSAL_TABULAR_PRODUCTION_EXECUTOR_ENABLED)
    try:
        sb.table('analysis_tasks').update({'status': 'processing'}).eq('id', task_id).execute()
        uploaded_file_row = (
            sb.table("uploaded_files")
            .select("id, user_id, team_id, file_name, storage_path, created_at")
            .eq("id", file_id)
            .single()
            .execute()
        )
        uploaded_row_data = dict(uploaded_file_row.data or {})
        if production_executor_enabled:
            canary_result = execute_canonical_tabular_production_analysis(
                file_id=file_id,
                prompt=prompt,
                service_client=sb,
                uploaded_file_row=uploaded_row_data,
                max_plans=max(int(settings.CANONICAL_SHADOW_TRAFFIC_MIRROR_MAX_PLANS or 3), 1),
            )
            if settings.CANONICAL_SHADOW_TRAFFIC_MIRROR_ENABLED:
                observe_canonical_tabular_canary_runtime_task.delay(
                    task_id,
                    file_id,
                    prompt,
                    prompt_type,
                    requested_visual_family,
                )
        else:
            canary_result = execute_canonical_tabular_canary_analysis(
                file_id=file_id,
                prompt=prompt,
                service_client=sb,
                uploaded_file_row=uploaded_row_data,
                prompt_type=prompt_type,
                requested_visual_family=requested_visual_family,
                max_plans=max(int(settings.CANONICAL_SHADOW_TRAFFIC_MIRROR_MAX_PLANS or 3), 1),
            )

        # --- Payload-shedding save: si el JSON es demasiado grande para PostgREST,
        # stripeamos los blobs binarios pesados y reintentamos. Los charts se renderizan
        # con data agregada; el snapshot es un nice-to-have para cross-filter.
        _save_analysis_task_result_with_payload_shedding(sb, task_id, canary_result)
        try:
            track_analysis_completed(
                task_id=task_id,
                file_id=file_id,
                user_id=str((uploaded_file_row.data or {}).get("user_id") or ""),
                status=canary_result.status,
                duration_ms=int((perf_counter() - task_started_at) * 1000),
                final_struct=canary_result.final_struct,
                dataset_contract=canary_result.dataset_contract,
                cleaning_notes=canary_result.cleaning_notes,
            )
        except Exception as canary_telemetry_error:
            emit_structured_log(
                "canonical_tabular_canary_track_analysis_completed_error",
                level="warning",
                task_id=task_id,
                file_id=file_id,
                error=str(canary_telemetry_error)[:240],
            )
        if not production_executor_enabled:
            try:
                track_canary_runtime_execution_observed(
                    task_id=task_id,
                    file_id=file_id,
                    user_id=str((uploaded_file_row.data or {}).get("user_id") or ""),
                    team_id=str((uploaded_file_row.data or {}).get("team_id") or ""),
                    file_name=str((uploaded_file_row.data or {}).get("file_name") or ""),
                    prompt_type=prompt_type,
                    execution_status=canary_result.status,
                    candidate_id=canary_result.execution.metadata.get("candidate_id"),
                    prompt_strategy=canary_result.execution.prompt_strategy,
                    chart_count=len(list(canary_result.final_struct.get("chart_options") or [])),
                    duration_ms=int((perf_counter() - task_started_at) * 1000),
                )
            except Exception as canary_execution_metric_error:
                emit_structured_log(
                    "canonical_tabular_canary_execution_metric_error",
                    level="warning",
                    task_id=task_id,
                    file_id=file_id,
                    error=str(canary_execution_metric_error)[:240],
                )
        else:
            emit_structured_log(
                "canonical_tabular_production_execution_observed",
                task_id=task_id,
                file_id=file_id,
                candidate_id=canary_result.execution.metadata.get("candidate_id"),
                prompt_strategy=canary_result.execution.prompt_strategy,
                chart_count=len(list(canary_result.final_struct.get("chart_options") or [])),
                duration_ms=int((perf_counter() - task_started_at) * 1000),
            )
        emit_structured_log(
            "canonical_tabular_production_task_completed"
            if production_executor_enabled
            else "canonical_tabular_canary_task_completed",
            task_id=task_id,
            file_id=file_id,
            runtime="universal_tabular_production" if production_executor_enabled else "universal_tabular",
            chart_count=len(list(canary_result.final_struct.get("chart_options") or [])),
            candidate_id=canary_result.execution.metadata.get("candidate_id"),
            prompt_strategy=canary_result.execution.prompt_strategy,
        )
        return canary_result.status
    except Exception as canary_error:
        try:
            uploaded_row_data = dict((uploaded_file_row.data or {})) if 'uploaded_file_row' in locals() else {}
            track_canary_runtime_execution_fallback(
                task_id=task_id,
                file_id=file_id,
                user_id=str(uploaded_row_data.get("user_id") or ""),
                team_id=str(uploaded_row_data.get("team_id") or ""),
                file_name=str(uploaded_row_data.get("file_name") or ""),
                prompt_type=prompt_type,
                fallback_reason=(
                    "production_runtime_execution_error"
                    if production_executor_enabled
                    else "canary_runtime_execution_error"
                ),
            )
        except Exception as canary_fallback_metric_error:
            emit_structured_log(
                "canonical_tabular_canary_fallback_metric_error",
                level="warning",
                task_id=task_id,
                file_id=file_id,
                error=str(canary_fallback_metric_error)[:240],
            )
        emit_structured_log(
            "canonical_tabular_production_task_fallback"
            if production_executor_enabled
            else "canonical_tabular_canary_task_fallback",
            level="warning",
            task_id=task_id,
            file_id=file_id,
            error=str(canary_error)[:240],
        )

        # --- 🛡️ BIG DATA SHIELD: Cortacircuito Legacy ---
        # Si el Canary falla en un archivo grande, el legacy runtime
        # 🛡️ [BIG DATA SHIELD V2] — Protección contra OOM en archivos grandes.
        # Solo se activa cuando:
        #   1. El archivo tiene más de 100K filas (umbral empírico validado en producción)
        #   2. El error NO es un error lógico como 'empty_result' (filtro sin coincidencias)
        #   3. El error NO es un error transitorio de red/Supabase
        # Si el error es 'empty_result', se permite el fallback al legacy para que
        # el usuario reciba un mensaje amigable en lugar del error de volumen.
        _LEGACY_SHIELD_ROW_THRESHOLD = 100_000  # Umbral empírico: 100K filas
        _is_big_file = False
        _is_transient_error = False
        _is_logical_error = False
        try:
            _error_str = str(canary_error).lower()
            # Detectar errores transitorios de red/Supabase que NO son de payload
            _transient_signals = ("521", "520", "502", "503", "web server is down",
                                  "connection", "timeout", "temporarily unavailable",
                                  "service unavailable", "pgrst")
            _is_transient_error = any(signal in _error_str for signal in _transient_signals)

            # [V2] Detectar errores lógicos (filtro vacío, columna faltante, etc.)
            # que NO deben disparar el shield de volumen — son bugs de contrato, no de memoria
            _logical_error_signals = ("empty_result", "argmax of an empty", "argmin of an empty",
                                       "column not found", "does not exist", "no matching",
                                       "no se encontraron", "empty sequence")
            _is_logical_error = any(signal in _error_str for signal in _logical_error_signals)

            # [V2] Usar fila-count real del sidecar de datos si está disponible
            _actual_row_count = 0
            try:
                import os, json as _json
                _sidecar_path = f"/tmp/promdata_cache/shadow_query_{str(file_id).replace('-', '_')}_primary_sheet_sheet1.contract.json"
                if os.path.exists(_sidecar_path):
                    with open(_sidecar_path, 'r') as _sf:
                        _sidecar = _json.load(_sf)
                    _actual_row_count = int(_sidecar.get('row_count', 0) or _sidecar.get('rows_at_max', 0) or 0)
            except Exception:
                pass

            # Si no tenemos fila-count del sidecar, usar estimación conservadora por extensión
            if _actual_row_count == 0:
                _file_name = str(uploaded_row_data.get("file_name") or "") if 'uploaded_row_data' in locals() else ""
                _file_ext = _file_name.rsplit(".", 1)[-1].lower() if "." in _file_name else ""
                # Conservador: solo marcamos como big si es .xlsx Y el error no es lógico
                _is_big_file = (
                    _file_ext in {"xlsx", "xls"}
                    and "canary_not_ready" not in _error_str
                    and not _is_transient_error
                    and not _is_logical_error
                )
            else:
                # Tenemos fila-count real: usar umbral empírico de 100K
                _is_big_file = (
                    _actual_row_count > _LEGACY_SHIELD_ROW_THRESHOLD
                    and not _is_transient_error
                    and not _is_logical_error
                )
                print(
                    f"📊 [BIG DATA SHIELD] Fila-count real: {_actual_row_count:,} "
                    f"| Umbral: {_LEGACY_SHIELD_ROW_THRESHOLD:,} "
                    f"| Es big: {_is_big_file} "
                    f"| Error lógico: {_is_logical_error}"
                )
        except Exception:
            pass

        if _is_big_file:
            emit_structured_log(
                "big_data_legacy_shield_activated",
                level="warning",
                task_id=task_id,
                file_id=file_id,
                canary_error=str(canary_error)[:240],
                reason="legacy_runtime_blocked_for_big_data_oom_prevention",
            )
            print(
                f"🛡️ [BIG DATA SHIELD] Legacy fallback BLOQUEADO para archivo grande. "
                f"El Canary falló: {str(canary_error)[:120]}. "
                f"Fail-fast activado para prevenir OOM."
            )
            sb.table('analysis_tasks').update({
                'status': 'failed',
                'results_json': json.dumps({
                    "analysis": (
                        "## ⚠️ Archivo de Alto Volumen\n\n"
                        "El archivo contiene un volumen de datos que excede la capacidad de procesamiento "
                        "del pipeline actual. Por favor intenta:\n\n"
                        "- Reducir el número de hojas del archivo Excel\n"
                        "- Filtrar los datos antes de cargarlos\n"
                        "- Dividir el archivo en períodos más cortos\n\n"
                        "Nuestro equipo está optimizando el motor para soportar este volumen."
                    ),
                    "metrics": {},
                    "chart_options": [],
                    "data": [],
                    "recommendations": [],
                    "explainability": [],
                }, cls=CustomEncoder),
            }).eq('id', task_id).execute()
            return "failed"

        fallback_route = dict(runtime_route or {})
        fallback_route["requested_runtime"] = fallback_route.get("requested_runtime") or "universal_tabular"
        fallback_route["effective_runtime"] = "legacy"
        fallback_route["decision_reason"] = (
            "production_runtime_execution_error"
            if production_executor_enabled
            else "canary_runtime_execution_error"
        )
        fallback_route["health_status"] = fallback_route.get("health_status") or "blocked"
        return perform_analysis_task.run(
            task_id,
            file_id,
            prompt,
            user_token,
            runtime_route=fallback_route,
        )
