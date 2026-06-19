# En: backend/app/tasks/analysis_pipeline/plan_generator.py
"""Plan generation functions — extracted from analysis_tasks.py."""

from typing import Any
from datetime import datetime
import json
import re
import math
import unicodedata

from app.core.prompt_format_override import (
    detect_format_override_from_prompt as core_detect_format_override_from_prompt,
    normalize_prompt_rules as core_normalize_prompt_rules,
)

from app.core.semantic_grammar import AnalysisPlan
from app.core.langfuse_client import record_llm_call
from app.core.config import settings


def _recursive_round(obj: Any, decimals: int = 2) -> Any:
    """Redondeo recursivo para estructuras JSON."""
    if isinstance(obj, float):
        return round(obj, decimals)
    if isinstance(obj, dict):
        return {k: _recursive_round(v, decimals) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_recursive_round(x, decimals) for x in obj]
    return obj


def select_narrative_model_name(*, intent_type: str, institutional_context: str, compliance_result: dict[str, Any] | None) -> str:
    compliance_result = compliance_result or {}
    if compliance_result.get("matched"):
        return settings.NARRATIVE_STRICT_MODEL_NAME
    if intent_type == "predictive":
        return settings.NARRATIVE_STRICT_MODEL_NAME
    return settings.NARRATIVE_FAST_MODEL_NAME


def build_widget_query_contract(plan: Any, schema_profile: dict | None = None) -> dict:
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

    def _pick_first(candidates: list, allowed_roles: set[str]) -> str | None:
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
    filters = []
    for filter_obj in list(getattr(intent, 'filters', None) or []):
        column_name = str(getattr(filter_obj, 'column', '') or '').strip()
        if not column_name or _role(column_name) not in {'dimension', 'identifier', 'date'}:
            continue
        filters.append({
            "column": column_name,
            "operator": getattr(getattr(filter_obj, 'operator', None), 'value', None) or getattr(filter_obj, 'operator', '=='),
            "value": getattr(filter_obj, 'value', None),
        })
    if filters:
        contract["filters"] = filters
    normalized_contract = {k: v for k, v in contract.items() if v not in (None, [], {})}
    has_metric = any(normalized_contract.get(key) for key in ('metric', 'value_column', 'metrics'))
    has_dimension = bool(normalized_contract.get('dimension'))

    if not has_metric or not has_dimension:
        return {}

    return normalized_contract


def should_force_smart_table_from_prompt(prompt_text: str) -> bool:
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


def coerce_plan_for_forced_heatmap(plan: AnalysisPlan, prompt_text: str, schema_profile: dict | None) -> AnalysisPlan:
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
        return coerced_plan
    except Exception as coercion_error:
        return plan


def _normalize_prompt_rules(text: str) -> str:
    return core_normalize_prompt_rules(text)


def detect_format_override_from_prompt(prompt_text: str) -> dict:
    return core_detect_format_override_from_prompt(prompt_text)


def _humanize_table_key(raw_key: str) -> str:
    key = str(raw_key or "").replace("_", " ").strip()
    return key[:1].upper() + key[1:] if key else "Valor"


def coerce_chart_rows_to_table_rows(chart_rows: list, plan: Any) -> list[dict]:
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


def planificar_estrategia(model: Any, prompt: str, data_info: str, adn: dict, glossary_context: str, gen_config: dict, facts_json: str, memory_context: str = "") -> dict:
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
        with record_llm_call(
            "planning",
            model_name=str(getattr(model, "model_name", settings.AI_MODEL_NAME)),
            prompt=planning_prompt,
            trace_id=None,
            trace_name="planificar_estrategia",
        ) as lf_span:
            response = model.generate_content(planning_prompt, generation_config=gen_config)
            lf_span["output"] = response.text
        text = response.text.replace('```json', '').replace('```', '').strip()
        start, end = text.find('{'), text.rfind('}') + 1
        plan_json = json.loads(text[start:end])

        return plan_json

    except Exception as e:
        return {"intencion": "Análisis General", "pasos_tacticos": ["Explorar datos"]}


def build_semantic_context(dfs: dict, prompt: str, plan: dict, adn: dict, field_schema: dict, issue: str | None, currency_meta: dict, cleaning_notes: str) -> tuple:
    format_override = detect_format_override_from_prompt(prompt)
    normalized_rules = _normalize_prompt_rules(prompt)

    issues_list = [issue] if issue else []

    data_info = {
        "shape": {k: str(v.shape) for k, v in dfs.items()},
        "columns": {k: list(v.columns) for k, v in dfs.items()},
        "dna": adn,
        "field_schema": field_schema,
        "currency_meta": currency_meta,
        "cleaning_notes": cleaning_notes,
    }
    return data_info, format_override, normalized_rules, issues_list


def translate_plans(model: Any, prompt: str, data_info: dict, adn: dict, glossary_context: str, gen_config: dict, facts_json: str, memory_context: str) -> dict:
    plan = planificar_estrategia(model, prompt, data_info, adn, glossary_context, gen_config, facts_json, memory_context)
    return plan


def inject_literal_filters(plan: dict, prompt: str) -> dict:
    return plan
