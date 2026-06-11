"""
Semantic Translator — Planner Module (Fase 0.1, Paso 4/5)

[REFACTOR 2026-06-11] Este archivo es parte de la Operacion Refactor
documentada en AGENTS.md §15.1 Plan 1 / Fase 0.1.

Responsabilidad: Construccion de AnalysisPlan: bundles, contratos de router, dimension/date picking, planes explicitos.

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
from app.core.semantic_grammar import (
    AnalysisPlan,
    DataFilter,
    DescriptiveIntent,
    DiagnosticIntent,
    DistributionIntent,
    FilterOperator,
    MetricPolarity,
    MetricUnit,
    TimeTrendIntent,
    VisualProtocol,
)
from app.core.structured_logging import emit_structured_log
from app.services.ai_response_cache import build_cache_key, get_cached_json, set_cached_json
from app.services.metric_semantics import infer_metric_unit_from_column_name, normalize_semantic_text
from app.services.visual_recommendation_engine import extract_prompt_visual_requests
from app.services.semantic_translator.core import SemanticTranslator


# ============================================================================
# Funciones module-level para planner
# ============================================================================
# Cada funcion toma `instance` como primer parametro (la clase
# SemanticTranslator) por compatibilidad con el patron de delegacion.
# Las funciones son static en su naturaleza original (no usan estado de
# instancia); el parametro `instance` se ignora en el cuerpo.


def schema_fingerprint(instance, columns: list[str], schema_profile: dict | None = None, dataset_contract: dict[str, Any] | None = None):
    return build_cache_key(
        "semantic_router_schema",
        {
            "columns": list(columns or []),
            "schema_profile": schema_profile or {},
            "dataset_contract": dataset_contract or {},
        },
    )


def normalize_semantic_router_decision(instance, payload: Any):
    if not isinstance(payload, dict):
        payload = {}

    route = str(payload.get("route") or "COMPLEJO").strip().upper()
    if route not in {"SIMPLE", "COMPLEJO"}:
        route = "COMPLEJO"

    try:
        confidence = float(payload.get("confidence", 0.0))
    except Exception:
        confidence = 0.0
    confidence = max(0.0, min(confidence, 1.0))

    reason_codes = payload.get("reason_codes") or []
    if not isinstance(reason_codes, list):
        reason_codes = [str(reason_codes)]
    normalized_reason_codes = [
        normalize_semantic_text(str(code)).replace(" ", "_")
        for code in reason_codes
        if str(code or "").strip()
    ]

    detected_intent = normalize_semantic_text(str(payload.get("detected_intent") or "unknown")).replace(" ", "_")
    if not detected_intent:
        detected_intent = "unknown"

    requires_time = bool(payload.get("requires_time", False))
    original_route = route
    if confidence < SemanticTranslator.SEMANTIC_ROUTER_CONFIDENCE_THRESHOLD:
        route = "COMPLEJO"
        if "low_confidence" not in normalized_reason_codes:
            normalized_reason_codes.append("low_confidence")
    if any(code in SemanticTranslator.SEMANTIC_ROUTER_COMPLEX_REASON_CODES for code in normalized_reason_codes):
        route = "COMPLEJO"
        if "conservative_policy" not in normalized_reason_codes:
            normalized_reason_codes.append("conservative_policy")

    semantic_contract = SemanticTranslator._normalize_router_semantic_contract(
        payload.get("semantic_contract") or payload.get("contract") or {},
        detected_intent=detected_intent,
        requires_time=requires_time,
    )
    return {
        "route": route,
        "confidence": confidence,
        "detected_intent": detected_intent,
        "requires_time": requires_time,
        "reason_codes": normalized_reason_codes,
        "original_route": original_route,
        "semantic_contract": semantic_contract,
    }


def normalize_router_semantic_contract(instance, payload: Any, detected_intent: str = "unknown", requires_time: bool = False):
    if not isinstance(payload, dict):
        payload = {}

    def _clean_text(value: Any) -> str | None:
        text = str(value or "").strip()
        if not text or text.lower() in {"none", "null", "all", "todos", "total", "global"}:
            return None
        return text

    intent = normalize_semantic_text(str(payload.get("intent") or detected_intent or "unknown")).replace(" ", "_")
    if intent not in {"trend", "distribution", "descriptive", "diagnostic", "predictive"}:
        intent = detected_intent if detected_intent in {"trend", "distribution", "descriptive", "diagnostic", "predictive"} else "unknown"

    raw_series_mode = normalize_semantic_text(
        str(
            payload.get("series_mode")
            or payload.get("top_n_aggregation_mode")
            or payload.get("aggregation_mode")
            or "none"
        )
    ).replace(" ", "_")
    series_mode_aliases = {
        "multi_series": "split",
        "per_item": "split",
        "each": "split",
        "separate": "split",
        "separado": "split",
        "desglosado": "split",
        "consolidated": "sum",
        "consolidado": "sum",
        "rollup": "sum",
        "combined": "sum",
        "single_series": "sum",
        "total": "sum",
    }
    series_mode = series_mode_aliases.get(raw_series_mode, raw_series_mode)
    if series_mode not in {"split", "sum", "none"}:
        series_mode = "none"

    try:
        top_n_value = payload.get("top_n")
        top_n = int(top_n_value) if top_n_value not in (None, "", False) else None
        if top_n is not None:
            top_n = max(1, min(top_n, 50))
    except Exception:
        top_n = None

    return {
        "intent": intent,
        "metric": _clean_text(payload.get("metric") or payload.get("metric_hint") or payload.get("value_column")),
        "plot_metric": _clean_text(payload.get("plot_metric") or payload.get("display_metric")),
        "ranking_metric": _clean_text(payload.get("ranking_metric") or payload.get("sort_metric") or payload.get("rank_metric")),
        "ranking_direction": normalize_semantic_text(str(payload.get("ranking_direction") or "desc")).replace(" ", "_") or "desc",
        "time_axis": _clean_text(payload.get("time_axis") or payload.get("date_column") or payload.get("time_dimension")),
        "dimension": _clean_text(payload.get("dimension") or payload.get("split_dimension") or payload.get("group_by")),
        "group_by": payload.get("group_by") if isinstance(payload.get("group_by"), list) else [],
        "positive_filters": payload.get("positive_filters") if isinstance(payload.get("positive_filters"), list) else [],
        "negative_filters": payload.get("negative_filters") if isinstance(payload.get("negative_filters"), list) else [],
        "top_n": top_n,
        "series_mode": series_mode,
        "grain": normalize_semantic_text(str(payload.get("grain") or "month")).replace(" ", "_") or "month",
        "aggregation": normalize_semantic_text(str(payload.get("aggregation") or "sum")).replace(" ", "_") or "sum",
        "visual_protocol": normalize_semantic_text(str(payload.get("visual_protocol") or "")).replace(" ", "_") or None,
        "requires_time": bool(payload.get("requires_time", requires_time)),
    }


def route_prompt_with_semantic_router(instance, prompt: str, columns: list[str], schema_profile: dict | None = None, dataset_contract: dict[str, Any] | None = None):
    surface_prompt = SemanticTranslator._normalize_surface_text(prompt)
    schema_fingerprint = SemanticTranslator._schema_fingerprint(
        columns,
        schema_profile=schema_profile,
        dataset_contract=dataset_contract,
    )
    router_cache_key = build_cache_key(
        "semantic_router",
        {
            "prompt": surface_prompt,
            "schema_fingerprint": schema_fingerprint,
        },
    )
    cached_decision = get_cached_json("semantic_router", router_cache_key)
    if isinstance(cached_decision, dict):
        normalized_cached_decision = SemanticTranslator._normalize_semantic_router_decision(cached_decision)
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

    COLUMNAS: {list(columns or [])}
    SCHEMA_FINGERPRINT: {schema_fingerprint}
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
        parsed_decision = SemanticTranslator._parse_translator_payload(response.text.strip())
        normalized_decision = SemanticTranslator._normalize_semantic_router_decision(parsed_decision)
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


def should_default_to_latest_snapshot(instance, surface_prompt: str, dataset_contract: dict[str, Any] | None = None, schema_profile: dict | None = None):
    dataset_contract = dataset_contract or {}
    schema_profile = schema_profile or {}
    if not dataset_contract:
        return False

    if any(
        marker in surface_prompt
        for marker in (
            "historico",
            "historial",
            "evolucion",
            "tendencia",
            "compar",
            "versus",
            " vs ",
            " contra ",
            " entre ",
            " desde ",
            " hasta ",
            " mensual",
            " semanal",
            " anual",
        )
    ):
        return False

    time_axis = str(dataset_contract.get("time_axis") or "").strip()
    date_columns = [str(value) for value in list(dataset_contract.get("date_columns") or []) if str(value or "").strip()]
    if not time_axis and not date_columns:
        return False

    if bool(dataset_contract.get("snapshot_guard_allowed")):
        return True

    dataset_mode = str(dataset_contract.get("dataset_mode") or "").strip().lower()
    if dataset_mode in {"snapshot", "hybrid"}:
        return True

    if time_axis:
        cardinality = int(schema_profile.get(time_axis, {}).get("cardinality") or 0)
        if cardinality > 1:
            return True

    return len(date_columns) >= 1


def build_default_latest_snapshot_filters(instance, surface_prompt: str, columns: list[str], dataset_contract: dict[str, Any] | None = None, schema_profile: dict | None = None):
    if not SemanticTranslator._should_default_to_latest_snapshot(
        surface_prompt,
        dataset_contract=dataset_contract,
        schema_profile=schema_profile,
    ):
        return []

    available_columns = set(columns or [])
    if "is_latest_snapshot" in available_columns:
        return [
            DataFilter(
                column="is_latest_snapshot",
                operator=FilterOperator.EQUALS,
                value="True",
            )
        ]

    dataset_contract = dataset_contract or {}
    time_axis = str(dataset_contract.get("time_axis") or "").strip()
    if time_axis and time_axis in available_columns:
        return [
            DataFilter(
                column=time_axis,
                operator=FilterOperator.EQUALS,
                value="latest",
            )
        ]

    return []


def resolve_contract_column(instance, hint: str | None, columns: list[str], schema_profile: dict | None = None, allowed_roles: set[str] | None = None):
    if not hint:
        return None

    schema_profile = schema_profile or {}
    if hint in columns:
        role = schema_profile.get(hint, {}).get("role")
        if not allowed_roles or role in allowed_roles:
            return hint

    candidates = SemanticTranslator._resolve_segment_columns(
        hint,
        columns,
        schema_profile=schema_profile,
        allowed_roles=allowed_roles,
    )
    return candidates[0] if candidates else None


def normalize_router_filters(instance, raw_filters: Any, columns: list[str], schema_profile: dict | None = None):
    if not isinstance(raw_filters, list):
        return []

    schema_profile = schema_profile or {}
    normalized_filters: list[dict[str, Any]] = []
    for filter_row in raw_filters:
        if not isinstance(filter_row, dict):
            continue

        raw_column = str(filter_row.get("column") or "").strip()
        if not raw_column:
            continue

        resolved_column = SemanticTranslator._resolve_contract_column(
            raw_column,
            columns,
            schema_profile=schema_profile,
        )
        if not resolved_column:
            continue

        value = filter_row.get("value")
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, list):
            clean_values = []
            for item in value:
                if item is None:
                    continue
                if isinstance(item, str) and not item.strip():
                    continue
                clean_values.append(item)
            if not clean_values:
                continue
            value = clean_values

        operator = filter_row.get("operator") or "=="
        try:
            validated = DataFilter.model_validate(
                {
                    "column": resolved_column,
                    "operator": operator,
                    "value": value,
                }
            )
        except Exception:
            continue
        normalized_filters.append(validated.model_dump(mode="json"))

    return normalized_filters


def build_plan_from_router_contract(instance, router_decision: dict[str, Any], columns: list[str], schema_profile: dict | None = None, dataset_contract: dict[str, Any] | None = None):
    if not settings.DETERMINISTIC_VISUAL_FASTPATH_ENABLED:
        return None

    schema_profile = schema_profile or {}
    dataset_contract = dataset_contract or {}
    contract = router_decision.get("semantic_contract") or {}
    if not isinstance(contract, dict):
        return None

    intent = str(contract.get("intent") or router_decision.get("detected_intent") or "unknown")
    metric_column = SemanticTranslator._resolve_contract_column(
        contract.get("plot_metric") or contract.get("metric"),
        columns,
        schema_profile=schema_profile,
        allowed_roles={"metric"},
    )
    # [V3 FIX] Fallback de coincidencia exacta: si el LLM nombró una columna
    # explícitamente en el contrato, respetarla aunque schema_profile la
    # marque como "dimension" (caso típico: columnas protegidas por
    # IMMUTABILITY LOCK que el router marca como "dimension" pero el LLM
    # las usa como métricas en su plan).
    if not metric_column:
        contract_metric_hint = str(contract.get("plot_metric") or contract.get("metric") or "").strip()
        if contract_metric_hint and contract_metric_hint in columns:
            metric_column = contract_metric_hint
    if not metric_column:
        metric_column = SemanticTranslator._infer_default_metric_column(
            str(contract.get("metric") or ""),
            columns,
            schema_profile=schema_profile,
        )
    if not metric_column:
        return None

    ranking_metric_column = SemanticTranslator._resolve_contract_column(
        contract.get("ranking_metric"),
        columns,
        schema_profile=schema_profile,
        allowed_roles={"metric"},
    )
    ranking_direction = str(contract.get("ranking_direction") or "desc").strip().lower()
    if ranking_direction not in {"desc", "asc"}:
        ranking_direction = "desc"

    positive_filters = SemanticTranslator._normalize_router_filters(
        contract.get("positive_filters"),
        columns,
        schema_profile=schema_profile,
    )
    negative_filters = SemanticTranslator._normalize_router_filters(
        contract.get("negative_filters"),
        columns,
        schema_profile=schema_profile,
    )

    metric_unit = infer_metric_unit_from_column_name(metric_column)
    metric_label = SemanticTranslator._humanize_column_alias(metric_column)

    if intent == "trend":
        date_column = SemanticTranslator._resolve_contract_column(
            contract.get("time_axis"),
            columns,
            schema_profile=schema_profile,
            allowed_roles={"date"},
        )
        if not date_column:
            date_column = SemanticTranslator._pick_primary_date_column(
                columns,
                schema_profile=schema_profile,
                dataset_contract=dataset_contract,
            )
        if not date_column:
            return None

        series_mode = str(contract.get("series_mode") or "none")
        top_n = contract.get("top_n")

        # [V4] Si series_mode=split pero top_n=null (usuario especificó valores exactos
        # como "almacenes 130 y 400"), inferir top_n del tamaño de la lista IN del filtro.
        # Sin esto, split_dimension nunca se asigna y IbisEngine genera una sola línea.
        if not top_n and series_mode in {"split", "sum"}:
            for pf in positive_filters:
                pf_op = str(
                    getattr(pf.get("operator"), "value", pf.get("operator")) or ""
                ).strip().lower() if isinstance(pf, dict) else ""
                pf_val = pf.get("value") if isinstance(pf, dict) else None
                if pf_op == "in" and isinstance(pf_val, list) and len(pf_val) >= 2:
                    top_n = len(pf_val)
                    print(
                        f"🔄 [SPLIT INFERENCE] top_n inferido de filtro IN: "
                        f"{pf.get('column')} IN {pf_val} → top_n={top_n}"
                    )
                    break

        split_dimension: str | None = None
        split_limit: int | None = None
        if top_n and series_mode in {"split", "sum"}:
            split_dimension = SemanticTranslator._resolve_contract_column(
                contract.get("dimension"),
                columns,
                schema_profile=schema_profile,
                allowed_roles={"dimension", "identifier"},
            )
            if not split_dimension:
                return None
            split_limit = max(2, min(int(top_n), 15))

        visual_protocol = VisualProtocol.AREA if contract.get("visual_protocol") == "area_chart" else VisualProtocol.LINE
        date_label = SemanticTranslator._humanize_column_alias(date_column)
        column_aliases = {metric_column: metric_label, date_column: date_label}
        if split_dimension:
            column_aliases[split_dimension] = SemanticTranslator._humanize_column_alias(split_dimension)

        return [
            AnalysisPlan(
                main_intent={
                    "type": "trend",
                    "rationale": "Ejecuto el contrato semántico simple emitido por el router.",
                    "filters": positive_filters,
                    "negative_filters": negative_filters,
                    "metric_unit": metric_unit.value if isinstance(metric_unit, MetricUnit) else MetricUnit.NUMBER.value,
                    "visual_protocol": visual_protocol.value,
                    "date_column": date_column,
                    "value_column": metric_column,
                    "plot_metric": metric_column,
                    "ranking_metric": ranking_metric_column,
                    "ranking_direction": ranking_direction,
                    "grain": str(contract.get("grain") or "month"),
                    "fill_missing": True,
                    "split_dimension": split_dimension,
                    "split_limit": split_limit,
                    "top_n_aggregation_mode": series_mode if series_mode in {"split", "sum"} else "split",
                },
                title=f"Evolución de {metric_label} por {date_label}",
                column_aliases=column_aliases,
                metric_polarity=MetricPolarity.NEUTRAL,
            )
        ]

    if intent == "distribution":
        dimension_column = SemanticTranslator._resolve_contract_column(
            contract.get("dimension"),
            columns,
            schema_profile=schema_profile,
            allowed_roles={"dimension", "identifier"},
        )
        if not dimension_column:
            return None
        limit = contract.get("top_n")
        if limit is None:
            cardinality = int(schema_profile.get(dimension_column, {}).get("cardinality") or 0)
            limit = cardinality if 0 < cardinality <= 12 else 10
        visual_protocol = {
            "pie_chart": VisualProtocol.PIE,
            "treemap": VisualProtocol.TREEMAP,
            "funnel_chart": VisualProtocol.FUNNEL,
        }.get(str(contract.get("visual_protocol") or ""), VisualProtocol.BAR)
        group_by_columns: list[str] = []
        for group_hint in list(contract.get("group_by") or []):
            resolved_group = SemanticTranslator._resolve_contract_column(
                str(group_hint),
                columns,
                schema_profile=schema_profile,
                allowed_roles={"dimension", "identifier", "date"},
            )
            if (
                resolved_group
                and resolved_group != dimension_column
                and resolved_group not in group_by_columns
            ):
                group_by_columns.append(resolved_group)
        dimension_label = SemanticTranslator._humanize_column_alias(dimension_column)
        return [
            AnalysisPlan(
                main_intent={
                    "type": "distribution",
                    "rationale": "Ejecuto el contrato semántico simple emitido por el router.",
                    "filters": positive_filters,
                    "negative_filters": negative_filters,
                    "metric_unit": metric_unit.value if isinstance(metric_unit, MetricUnit) else MetricUnit.NUMBER.value,
                    "visual_protocol": visual_protocol.value,
                    "dimension": dimension_column,
                    "metric": metric_column,
                    "plot_metric": metric_column,
                    "ranking_metric": ranking_metric_column,
                    "ranking_direction": ranking_direction,
                    "limit": int(limit),
                    "group_by": group_by_columns or None,
                    "barmode": "stacked",
                },
                title=f"{metric_label} por {dimension_label}",
                column_aliases={metric_column: metric_label, dimension_column: dimension_label},
                metric_polarity=MetricPolarity.NEUTRAL,
            )
        ]

    if intent == "descriptive":
        dimension_column = SemanticTranslator._resolve_contract_column(
            contract.get("dimension"),
            columns,
            schema_profile=schema_profile,
            allowed_roles={"dimension", "identifier", "date"},
        )
        group_by_columns: list[str] = []
        for group_hint in list(contract.get("group_by") or []):
            resolved_group = SemanticTranslator._resolve_contract_column(
                str(group_hint),
                columns,
                schema_profile=schema_profile,
                allowed_roles={"dimension", "identifier", "date"},
            )
            if resolved_group and resolved_group not in group_by_columns:
                group_by_columns.append(resolved_group)
        if not dimension_column and group_by_columns:
            dimension_column = group_by_columns[0]
            group_by_columns = [
                column_name for column_name in group_by_columns if column_name != dimension_column
            ]

        top_n = contract.get("top_n")
        has_segmented_request = bool(
            dimension_column
            or group_by_columns
            or (isinstance(top_n, int) and top_n > 0)
        )
        if has_segmented_request and dimension_column:
            limit = top_n if isinstance(top_n, int) and top_n > 0 else None
            if limit is None:
                cardinality = int(schema_profile.get(dimension_column, {}).get("cardinality") or 0)
                limit = cardinality if 0 < cardinality <= 12 else 10
            visual_protocol = {
                "pie_chart": VisualProtocol.PIE,
                "treemap": VisualProtocol.TREEMAP,
                "funnel_chart": VisualProtocol.FUNNEL,
                "bar_chart": VisualProtocol.BAR,
                "line_chart": VisualProtocol.LINE,
                "area_chart": VisualProtocol.AREA,
            }.get(str(contract.get("visual_protocol") or ""), VisualProtocol.BAR)
            if visual_protocol == VisualProtocol.KPI:
                visual_protocol = VisualProtocol.BAR
            dimension_label = SemanticTranslator._humanize_column_alias(dimension_column)
            return [
                AnalysisPlan(
                    main_intent={
                        "type": "distribution",
                        "rationale": "Ejecuto el contrato semántico simple segmentado emitido por el router.",
                        "filters": positive_filters,
                        "negative_filters": negative_filters,
                        "metric_unit": metric_unit.value if isinstance(metric_unit, MetricUnit) else MetricUnit.NUMBER.value,
                        "visual_protocol": visual_protocol.value,
                        "dimension": dimension_column,
                        "metric": metric_column,
                        "plot_metric": metric_column,
                        "ranking_metric": ranking_metric_column,
                        "ranking_direction": ranking_direction,
                        "limit": int(limit),
                        "group_by": group_by_columns or None,
                        "barmode": "stacked",
                    },
                    title=f"{metric_label} por {dimension_label}",
                    column_aliases={metric_column: metric_label, dimension_column: dimension_label},
                    metric_polarity=MetricPolarity.NEUTRAL,
                )
            ]

        return [
            AnalysisPlan(
                main_intent=DescriptiveIntent(
                    rationale="Ejecuto el contrato semántico simple emitido por el router.",
                    filters=positive_filters,
                    negative_filters=negative_filters,
                    metrics=[metric_column],
                    metric_unit=metric_unit if isinstance(metric_unit, MetricUnit) else MetricUnit.NUMBER,
                    aggregation=str(contract.get("aggregation") or "sum"),
                    visual_protocol=VisualProtocol.KPI,
                ),
                title=f"{metric_label} Total",
                column_aliases={metric_column: metric_label},
                metric_polarity=MetricPolarity.NEUTRAL,
            )
        ]

    return None


def pick_primary_date_column(instance, columns: list[str], schema_profile: dict | None = None, dataset_contract: dict[str, Any] | None = None):
    schema_profile = schema_profile or {}
    dataset_contract = dataset_contract or {}

    time_axis = dataset_contract.get("time_axis")
    if (
        isinstance(time_axis, str)
        and time_axis in columns
        and schema_profile.get(time_axis, {}).get("role") == "date"
    ):
        return time_axis

    date_candidates = [
        column_name
        for column_name in columns
        if schema_profile.get(column_name, {}).get("role") == "date"
    ]
    ranked = sorted(
        date_candidates,
        key=lambda column_name: (
            -int(schema_profile.get(column_name, {}).get("cardinality") or 0),
            column_name,
        ),
    )
    return ranked[0] if ranked else None


def pick_best_dimension_column(instance, prompt: str, columns: list[str], schema_profile: dict | None = None, exclude: set[str] | None = None):
    schema_profile = schema_profile or {}
    exclude = exclude or set()
    surface_prompt = SemanticTranslator._normalize_surface_text(prompt)
    compact_prompt = surface_prompt.replace(" ", "")
    ranked: list[tuple[int, str]] = []

    for column_name in columns:
        if column_name in exclude:
            continue

        info = schema_profile.get(column_name, {})
        role = info.get("role")
        if role not in {"dimension", "identifier"}:
            continue

        cardinality = int(info.get("cardinality") or 0)
        if cardinality <= 1:
            continue

        # --- Prompt-match detection (antes de aplicar penalties) ---
        col_norm = normalize_semantic_text(str(column_name).replace("_", " "))
        compact_col = col_norm.replace(" ", "")
        has_direct_prompt_match = bool(compact_col and compact_col in compact_prompt)
        token_overlap = sum(1 for token in col_norm.split() if len(token) > 1 and token in surface_prompt)

        # --- Role scoring: si el usuario menciona explícitamente la columna,
        # el role "identifier" NO recibe penalización (user intent > heuristic) ---
        score = 0
        if role == "dimension":
            score += 40
        elif has_direct_prompt_match or token_overlap >= 1:
            score += 20  # Identifier mencionado por el usuario: bonificación moderada
        else:
            score -= 10  # Identifier no mencionado: penalización estándar

        # --- Cardinality scoring: reducido cuando hay match explícito ---
        if cardinality <= 12:
            score += 20 if has_direct_prompt_match else 35
        elif cardinality <= 30:
            score += 20 if has_direct_prompt_match else 25
        elif cardinality <= 100:
            score += 12
        elif cardinality <= 300:
            score += 4
        else:
            score -= 8

        cardinality_ratio = float(info.get("cardinality_ratio") or 0.0)
        if cardinality_ratio >= 0.9:
            score -= 12
        elif cardinality_ratio <= 0.2:
            score += 6

        # --- Prompt match bonus (dominante) ---
        if has_direct_prompt_match:
            score += 80 + len(compact_col)

        score += token_overlap * 12

        ranked.append((score, column_name))

    ranked.sort(key=lambda item: (-item[0], item[1]))
    return ranked[0][1] if ranked else None


def build_dimension_analysis_bundle(instance, prompt: str, columns: list[str], schema_profile: dict | None = None, dataset_contract: dict[str, Any] | None = None):
    if not settings.DETERMINISTIC_VISUAL_FASTPATH_ENABLED:
        return None
    if not SemanticTranslator._looks_dimension_analysis_request(prompt):
        return None

    schema_profile = schema_profile or {}
    dataset_contract = dataset_contract or {}
    surface_prompt = SemanticTranslator._normalize_surface_text(prompt)
    default_snapshot_filters = SemanticTranslator._build_default_latest_snapshot_filters(
        surface_prompt,
        columns,
        dataset_contract=dataset_contract,
        schema_profile=schema_profile,
    )

    dimension_segment = SemanticTranslator._extract_primary_dimension_segment(surface_prompt)
    dimension_candidates = SemanticTranslator._resolve_segment_columns(
        dimension_segment or surface_prompt,
        columns,
        schema_profile=schema_profile,
        allowed_roles={"dimension", "identifier"},
    )
    if not dimension_candidates:
        return None

    primary_dimension = dimension_candidates[0]
    if int(schema_profile.get(primary_dimension, {}).get("cardinality") or 0) <= 1:
        return None

    metric_column = SemanticTranslator._infer_default_metric_column(
        surface_prompt,
        columns,
        schema_profile=schema_profile,
    )
    if not metric_column:
        return None

    metric_unit = infer_metric_unit_from_column_name(metric_column)
    metric_label = SemanticTranslator._humanize_column_alias(metric_column)
    primary_label = SemanticTranslator._humanize_column_alias(primary_dimension)
    primary_cardinality = int(schema_profile.get(primary_dimension, {}).get("cardinality") or 0)
    primary_limit = primary_cardinality if 0 < primary_cardinality <= 12 else 10
    primary_visual = SemanticTranslator._select_default_distribution_visual(
        primary_dimension,
        schema_profile=schema_profile,
    )

    aliases = {
        metric_column: metric_label,
        primary_dimension: primary_label,
    }
    plans: list[AnalysisPlan] = [
        AnalysisPlan(
            main_intent=DistributionIntent(
                rationale=(
                    "Priorizo la dimensión solicitada por el usuario como eje principal "
                    "para ordenar el análisis alrededor de la categoría pedida."
                ),
                filters=default_snapshot_filters,
                dimension=primary_dimension,
                metric=metric_column,
                limit=primary_limit,
                metric_unit=metric_unit if isinstance(metric_unit, MetricUnit) else MetricUnit.NUMBER,
                visual_protocol=VisualProtocol(primary_visual),
            ),
            title=f"Top {primary_limit} {primary_label} por {metric_label}",
            column_aliases=aliases.copy(),
            metric_polarity=MetricPolarity.NEUTRAL,
        )
    ]

    date_column = SemanticTranslator._pick_primary_date_column(
        columns,
        schema_profile=schema_profile,
        dataset_contract=dataset_contract,
    )
    if SemanticTranslator._has_meaningful_temporal_axis(date_column, schema_profile=schema_profile):
        date_label = SemanticTranslator._humanize_column_alias(date_column)
        trend_aliases = aliases.copy()
        trend_aliases[date_column] = date_label
        plans.append(
            AnalysisPlan(
                main_intent=TimeTrendIntent(
                    rationale=(
                        "Completo la vista por dimensión con evolución temporal real para "
                        "mostrar si el comportamiento cambia entre periodos del dataset."
                    ),
                    date_column=date_column,
                    value_column=metric_column,
                    metric_unit=metric_unit if isinstance(metric_unit, MetricUnit) else MetricUnit.NUMBER,
                    visual_protocol=VisualProtocol.LINE,
                ),
                title=f"Evolución de {metric_label} por {date_label}",
                column_aliases=trend_aliases,
                metric_polarity=MetricPolarity.NEUTRAL,
            )
        )

    secondary_dimension = SemanticTranslator._pick_best_dimension_column(
        surface_prompt,
        columns,
        schema_profile=schema_profile,
        exclude={primary_dimension},
    )
    if secondary_dimension:
        secondary_visual = SemanticTranslator._select_alternate_distribution_visual(
            secondary_dimension,
            primary_visual,
            schema_profile=schema_profile,
        )
        secondary_label = SemanticTranslator._humanize_column_alias(secondary_dimension)
        secondary_cardinality = int(schema_profile.get(secondary_dimension, {}).get("cardinality") or 0)
        secondary_limit = secondary_cardinality if 0 < secondary_cardinality <= 12 else 10
        secondary_aliases = {metric_column: metric_label, secondary_dimension: secondary_label}
        plans.append(
            AnalysisPlan(
                main_intent=DistributionIntent(
                    rationale=(
                        "Añado una segunda dimensión complementaria para contextualizar la "
                        "lectura principal sin depender del planner generativo."
                    ),
                    filters=default_snapshot_filters,
                    dimension=secondary_dimension,
                    metric=metric_column,
                    limit=secondary_limit,
                    metric_unit=metric_unit if isinstance(metric_unit, MetricUnit) else MetricUnit.NUMBER,
                    visual_protocol=VisualProtocol(secondary_visual),
                ),
                title=f"Distribución de {metric_label} por {secondary_label}",
                column_aliases=secondary_aliases,
                metric_polarity=MetricPolarity.NEUTRAL,
            )
        )

    if len(plans) < 3:
        kpi_title = f"{metric_label} Total"
        if dataset_contract.get("snapshot_guard_allowed"):
            kpi_title += " (Corte Actual)"
        plans.append(
            AnalysisPlan(
                main_intent=DescriptiveIntent(
                    rationale=(
                        "Completo el bundle con un KPI global para conservar referencia de "
                        "magnitud cuando faltan ejes suficientes para una tercera vista."
                    ),
                    filters=default_snapshot_filters,
                    metrics=[metric_column],
                    metric_unit=metric_unit if isinstance(metric_unit, MetricUnit) else MetricUnit.NUMBER,
                    aggregation="sum",
                    visual_protocol=VisualProtocol.KPI,
                ),
                title=kpi_title,
                column_aliases={metric_column: metric_label},
                metric_polarity=MetricPolarity.NEUTRAL,
            )
        )

    emit_structured_log(
        "semantic_translator_dimension_bundle_fast_path_hit",
        prompt=prompt[:200],
        plan_count=len(plans[:3]),
        metric=metric_column,
        primary_dimension=primary_dimension,
        date_column=date_column,
        dataset_mode=dataset_contract.get("dataset_mode"),
    )
    return plans[:3]


def build_macro_analysis_bundle(instance, prompt: str, columns: list[str], schema_profile: dict | None = None, dataset_contract: dict[str, Any] | None = None):
    if not settings.DETERMINISTIC_VISUAL_FASTPATH_ENABLED:
        return None
    if not SemanticTranslator._looks_broad_analysis_request(prompt):
        return None

    schema_profile = schema_profile or {}
    dataset_contract = dataset_contract or {}
    surface_prompt = SemanticTranslator._normalize_surface_text(prompt)
    default_snapshot_filters = SemanticTranslator._build_default_latest_snapshot_filters(
        surface_prompt,
        columns,
        dataset_contract=dataset_contract,
        schema_profile=schema_profile,
    )

    metric_column = SemanticTranslator._infer_default_metric_column(
        surface_prompt,
        columns,
        schema_profile=schema_profile,
    )
    if not metric_column:
        return None

    metric_unit = infer_metric_unit_from_column_name(metric_column)
    metric_label = SemanticTranslator._humanize_column_alias(metric_column)
    plans: list[AnalysisPlan] = []
    aliases = {metric_column: metric_label}

    descriptive_title = f"{metric_label} Total"
    if dataset_contract.get("snapshot_guard_allowed"):
        descriptive_title += " (Corte Actual)"
    plans.append(
        AnalysisPlan(
            main_intent=DescriptiveIntent(
                rationale=(
                    "Priorizo un KPI global para abrir el análisis con la magnitud base "
                    "más representativa del dataset."
                ),
                filters=default_snapshot_filters,
                metrics=[metric_column],
                metric_unit=metric_unit if isinstance(metric_unit, MetricUnit) else MetricUnit.NUMBER,
                aggregation="sum",
                visual_protocol=VisualProtocol.KPI,
            ),
            title=descriptive_title,
            column_aliases=aliases.copy(),
            metric_polarity=MetricPolarity.NEUTRAL,
        )
    )

    primary_visual: str | None = None
    date_column = SemanticTranslator._pick_primary_date_column(
        columns,
        schema_profile=schema_profile,
        dataset_contract=dataset_contract,
    )
    if SemanticTranslator._has_meaningful_temporal_axis(date_column, schema_profile=schema_profile):
        date_label = SemanticTranslator._humanize_column_alias(date_column)
        trend_aliases = aliases.copy()
        trend_aliases[date_column] = date_label
        plans.append(
            AnalysisPlan(
                main_intent=TimeTrendIntent(
                    rationale=(
                        "Agrego una lectura temporal para revelar tendencia y cambio "
                        "cuando el dataset ofrece un eje cronológico real."
                    ),
                    date_column=date_column,
                    value_column=metric_column,
                    metric_unit=metric_unit if isinstance(metric_unit, MetricUnit) else MetricUnit.NUMBER,
                    visual_protocol=VisualProtocol.LINE,
                ),
                title=f"Evolución de {metric_label} por {date_label}",
                column_aliases=trend_aliases,
                metric_polarity=MetricPolarity.NEUTRAL,
            )
        )

    primary_dimension = SemanticTranslator._pick_best_dimension_column(
        surface_prompt,
        columns,
        schema_profile=schema_profile,
    )
    if primary_dimension:
        primary_visual = SemanticTranslator._select_default_distribution_visual(
            primary_dimension,
            schema_profile=schema_profile,
        )
        dimension_label = SemanticTranslator._humanize_column_alias(primary_dimension)
        dimension_cardinality = int(schema_profile.get(primary_dimension, {}).get("cardinality") or 0)
        limit = dimension_cardinality if 0 < dimension_cardinality <= 12 else 10
        dist_aliases = aliases.copy()
        dist_aliases[primary_dimension] = dimension_label
        plans.append(
            AnalysisPlan(
                main_intent=DistributionIntent(
                    rationale=(
                        "Incluyo una vista de concentración para identificar qué categorías "
                        "explican el peso operativo dominante."
                    ),
                    filters=default_snapshot_filters,
                    dimension=primary_dimension,
                    metric=metric_column,
                    limit=limit,
                    metric_unit=metric_unit if isinstance(metric_unit, MetricUnit) else MetricUnit.NUMBER,
                    visual_protocol=VisualProtocol(primary_visual),
                ),
                title=f"{metric_label} por {dimension_label}",
                column_aliases=dist_aliases,
                metric_polarity=MetricPolarity.NEUTRAL,
            )
        )

    if len(plans) < 3:
        secondary_dimension = SemanticTranslator._pick_best_dimension_column(
            surface_prompt,
            columns,
            schema_profile=schema_profile,
            exclude={primary_dimension} if primary_dimension else set(),
        )
        if secondary_dimension:
            secondary_visual = SemanticTranslator._select_alternate_distribution_visual(
                secondary_dimension,
                primary_visual,
                schema_profile=schema_profile,
            )
            secondary_label = SemanticTranslator._humanize_column_alias(secondary_dimension)
            secondary_cardinality = int(schema_profile.get(secondary_dimension, {}).get("cardinality") or 0)
            secondary_limit = secondary_cardinality if 0 < secondary_cardinality <= 12 else 10
            secondary_aliases = aliases.copy()
            secondary_aliases[secondary_dimension] = secondary_label
            plans.append(
                AnalysisPlan(
                    main_intent=DistributionIntent(
                        rationale=(
                            "Completo el paquete con una segunda vista categórica para aportar "
                            "otra dimensión explicativa sin depender del planner generativo."
                        ),
                        filters=default_snapshot_filters,
                        dimension=secondary_dimension,
                        metric=metric_column,
                        limit=secondary_limit,
                        metric_unit=metric_unit if isinstance(metric_unit, MetricUnit) else MetricUnit.NUMBER,
                        visual_protocol=VisualProtocol(secondary_visual),
                    ),
                    title=f"Top {secondary_limit} {secondary_label} por {metric_label}",
                    column_aliases=secondary_aliases,
                    metric_polarity=MetricPolarity.NEUTRAL,
                )
            )

    if not plans:
        return None

    emit_structured_log(
        "semantic_translator_macro_fast_path_hit",
        prompt=prompt[:200],
        plan_count=len(plans),
        metric=metric_column,
        date_column=date_column,
        primary_dimension=primary_dimension,
        dataset_mode=dataset_contract.get("dataset_mode"),
    )
    return plans[:3]


def build_explicit_scatter_plan(instance, prompt: str, columns: list[str], schema_profile: dict | None = None):
    if not settings.DETERMINISTIC_VISUAL_FASTPATH_ENABLED:
        return None

    requested_visuals = extract_prompt_visual_requests(prompt)
    if "scatter_plot" not in requested_visuals:
        return None

    surface_prompt = SemanticTranslator._normalize_surface_text(prompt)
    if " x " not in f" {surface_prompt} " or " y " not in f" {surface_prompt} ":
        return None

    schema_profile = schema_profile or {}
    x_segment = SemanticTranslator._extract_axis_segment(surface_prompt, "x")
    y_segment = SemanticTranslator._extract_axis_segment(surface_prompt, "y")
    color_segment = SemanticTranslator._extract_axis_segment(surface_prompt, "color")

    if not x_segment or not y_segment:
        return None

    x_date_candidates = SemanticTranslator._resolve_segment_columns(
        x_segment,
        columns,
        schema_profile=schema_profile,
        allowed_roles={"date"},
    )
    x_metric_candidates = SemanticTranslator._resolve_segment_columns(
        x_segment,
        columns,
        schema_profile=schema_profile,
        allowed_roles={"metric"},
    )
    y_metric_candidates = SemanticTranslator._resolve_segment_columns(
        y_segment,
        columns,
        schema_profile=schema_profile,
        allowed_roles={"metric"},
    )
    color_candidates = SemanticTranslator._resolve_segment_columns(
        color_segment,
        columns,
        schema_profile=schema_profile,
        allowed_roles={"dimension", "identifier"},
    )

    if not y_metric_candidates:
        return None

    y_metric = y_metric_candidates[0]
    scatter_metrics: list[str] = []
    if len(x_date_candidates) >= 2:
        scatter_metrics.extend(x_date_candidates[:2])
    elif x_metric_candidates:
        scatter_metrics.append(x_metric_candidates[0])
    elif x_date_candidates:
        scatter_metrics.append(x_date_candidates[0])

    if not scatter_metrics:
        return None

    if y_metric not in scatter_metrics:
        scatter_metrics.append(y_metric)

    dimension_col = color_candidates[0] if color_candidates else None
    metric_unit = infer_metric_unit_from_column_name(y_metric)

    title = f"Dispersión de {SemanticTranslator._humanize_column_alias(y_metric)}"
    if len(x_date_candidates) >= 2:
        title += " vs. Días al Vencimiento"
    else:
        title += f" vs. {SemanticTranslator._humanize_column_alias(scatter_metrics[0])}"
    if dimension_col:
        title += f" por {SemanticTranslator._humanize_column_alias(dimension_col)}"

    aliases = {
        column_name: SemanticTranslator._humanize_column_alias(column_name)
        for column_name in [*scatter_metrics, dimension_col]
        if column_name
    }
    plan = AnalysisPlan(
        main_intent=DiagnosticIntent(
            rationale=(
                "Priorizo una vista relacional explícita para medir dispersión y contraste "
                "entre la métrica operativa y la variable pedida por el usuario."
            ),
            metric=y_metric,
            metrics=scatter_metrics,
            dimension=dimension_col,
            metric_unit=metric_unit if isinstance(metric_unit, MetricUnit) else MetricUnit.NUMBER,
            visual_protocol=VisualProtocol.SCATTER,
        ),
        title=title,
        column_aliases=aliases,
        metric_polarity=MetricPolarity.NEUTRAL,
    )
    emit_structured_log(
        "semantic_translator_fast_path_hit",
        prompt=prompt[:200],
        visual="scatter_plot",
        metrics=scatter_metrics,
        dimension=dimension_col,
    )
    return [plan]


def build_explicit_trend_plan(instance, prompt: str, columns: list[str], schema_profile: dict | None = None, allow_non_visual_prompt: bool = False):
    if not settings.DETERMINISTIC_VISUAL_FASTPATH_ENABLED:
        return None

    requested_visuals = extract_prompt_visual_requests(prompt)
    surface_prompt = SemanticTranslator._normalize_surface_text(prompt)
    generic_visual_request = SemanticTranslator._mentions_generic_visual_request(surface_prompt)
    if not requested_visuals and not generic_visual_request and not allow_non_visual_prompt:
        return None

    requested_visual = requested_visuals[0] if requested_visuals else "line_chart"
    if requested_visual not in {"line_chart", "area_chart"}:
        return None
    if not requested_visuals and not SemanticTranslator._mentions_temporal_language(surface_prompt):
        return None

    schema_profile = schema_profile or {}
    x_segment = SemanticTranslator._extract_axis_segment(surface_prompt, "x")
    y_segment = SemanticTranslator._extract_axis_segment(surface_prompt, "y")

    date_candidates = SemanticTranslator._resolve_segment_columns(
        x_segment or surface_prompt,
        columns,
        schema_profile=schema_profile,
        allowed_roles={"date"},
    )
    metric_candidates = SemanticTranslator._resolve_segment_columns(
        y_segment or surface_prompt,
        columns,
        schema_profile=schema_profile,
        allowed_roles={"metric"},
    )

    if not date_candidates or not metric_candidates:
        de_por_match = re.search(r"\bde\s+(.+?)\s+por\s+(.+?)(?=$|,)", surface_prompt, flags=re.IGNORECASE)
        if de_por_match:
            if not metric_candidates:
                metric_candidates = SemanticTranslator._resolve_segment_columns(
                    de_por_match.group(1),
                    columns,
                    schema_profile=schema_profile,
                    allowed_roles={"metric"},
                )
            if not date_candidates:
                date_candidates = SemanticTranslator._resolve_segment_columns(
                    de_por_match.group(2),
                    columns,
                    schema_profile=schema_profile,
                    allowed_roles={"date"},
                )

    if not date_candidates and SemanticTranslator._mentions_temporal_language(surface_prompt):
        fallback_date_column = SemanticTranslator._pick_primary_date_column(
            columns,
            schema_profile=schema_profile,
            dataset_contract={},
        )
        if fallback_date_column:
            date_candidates = [fallback_date_column]

    if not metric_candidates:
        default_metric = SemanticTranslator._infer_default_metric_column(
            surface_prompt,
            columns,
            schema_profile=schema_profile,
        )
        if default_metric:
            metric_candidates = [default_metric]

    if not date_candidates or not metric_candidates:
        return None

    date_column = date_candidates[0]
    metric_column = metric_candidates[0]
    explicit_top_limit = SemanticTranslator._extract_top_limit(surface_prompt)
    split_dimension: str | None = None
    split_limit: int | None = None
    top_n_aggregation_mode = "split"

    if explicit_top_limit is not None:
        split_segment = SemanticTranslator._extract_primary_dimension_segment(surface_prompt)
        top_segment_match = re.search(
            r"\btop\s+\d{1,3}\s+(.+?)(?=$|,|\s+con\s+|\s+de\s+|\s+en\s+|\s+para\s+)",
            surface_prompt,
            flags=re.IGNORECASE,
        )
        if top_segment_match:
            top_segment = top_segment_match.group(1).strip(" .,:;")
            if not split_segment or split_segment in {"fecha", "date", "periodo", "periodos", "tiempo"}:
                split_segment = top_segment
        split_segment = split_segment or surface_prompt
        split_candidates = SemanticTranslator._resolve_segment_columns(
            split_segment,
            columns,
            schema_profile=schema_profile,
            allowed_roles={"dimension", "identifier"},
        )
        for candidate in split_candidates:
            if candidate not in {date_column, metric_column}:
                split_dimension = candidate
                break
        if not split_dimension:
            fallback_split_dimension = SemanticTranslator._pick_best_dimension_column(
                surface_prompt,
                columns,
                schema_profile=schema_profile,
                exclude={date_column, metric_column},
            )
            if fallback_split_dimension:
                split_dimension = fallback_split_dimension
        if split_dimension:
            split_limit = max(2, min(int(explicit_top_limit), 15))
            if SemanticTranslator._is_top_n_rollup_request(surface_prompt):
                top_n_aggregation_mode = "sum"

    metric_unit = infer_metric_unit_from_column_name(metric_column)
    visual_protocol = VisualProtocol.LINE if requested_visual == "line_chart" else VisualProtocol.AREA

    metric_label = SemanticTranslator._humanize_column_alias(metric_column)
    date_label = SemanticTranslator._humanize_column_alias(date_column)
    if split_dimension and split_limit:
        split_label = SemanticTranslator._humanize_column_alias(split_dimension)
        if top_n_aggregation_mode == "sum":
            title = f"Evolución de {metric_label} (Suma Top {split_limit} {split_label}) por {date_label}"
        else:
            title = f"Evolución de {metric_label} por {split_label} (Top {split_limit})"
    else:
        title = f"Evolución de {metric_label} por {date_label}"

    plan = AnalysisPlan(
        main_intent={
            "type": "trend",
            "rationale": (
                "Priorizo una lectura temporal explícita para seguir la evolución de la métrica "
                "sobre el eje de tiempo pedido por el usuario."
            ),
            "filters": [],
            "metric_unit": metric_unit.value if isinstance(metric_unit, MetricUnit) else MetricUnit.NUMBER.value,
            "visual_protocol": visual_protocol.value,
            "date_column": date_column,
            "value_column": metric_column,
            "grain": "month",
            "fill_missing": True,
            "split_dimension": split_dimension,
            "split_limit": split_limit,
            "top_n_aggregation_mode": top_n_aggregation_mode,
        },
        title=title,
        column_aliases={
            metric_column: metric_label,
            date_column: date_label,
            **({split_dimension: SemanticTranslator._humanize_column_alias(split_dimension)} if split_dimension else {}),
        },
        metric_polarity=MetricPolarity.NEUTRAL,
    )
    emit_structured_log(
        "semantic_translator_fast_path_hit",
        prompt=prompt[:200],
        visual=requested_visual,
        date_column=date_column,
        metric=metric_column,
        split_dimension=split_dimension,
        split_limit=split_limit,
        top_n_aggregation_mode=top_n_aggregation_mode,
    )
    return [plan]


def build_explicit_distribution_plan(instance, prompt: str, columns: list[str], schema_profile: dict | None = None, dataset_contract: dict[str, Any] | None = None):
    if not settings.DETERMINISTIC_VISUAL_FASTPATH_ENABLED:
        return None

    requested_visuals = extract_prompt_visual_requests(prompt)
    surface_prompt = SemanticTranslator._normalize_surface_text(prompt)
    generic_visual_request = SemanticTranslator._mentions_generic_visual_request(surface_prompt)
    if not requested_visuals and not generic_visual_request:
        return None

    requested_visual = requested_visuals[0] if requested_visuals else None
    if requested_visual and requested_visual not in {"bar_chart", "pie_chart", "treemap", "funnel_chart"}:
        return None

    schema_profile = schema_profile or {}
    dataset_contract = dataset_contract or {}
    explicit_top_limit = SemanticTranslator._extract_top_limit(surface_prompt)
    top_requested = explicit_top_limit is not None
    default_snapshot_filters = SemanticTranslator._build_default_latest_snapshot_filters(
        surface_prompt,
        columns,
        dataset_contract=dataset_contract,
        schema_profile=schema_profile,
    )

    dimension_segment = None
    metric_segment = None

    top_match = re.search(r"\btop\s+\d{1,3}\s+(.+?)\s+por\s+(.+?)(?=$|,)", surface_prompt, flags=re.IGNORECASE)
    if top_match:
        dimension_segment = top_match.group(1)
        metric_segment = top_match.group(2)

    if not dimension_segment or not metric_segment:
        de_por_match = re.search(r"\bde\s+(.+?)\s+por\s+(.+?)(?=$|,)", surface_prompt, flags=re.IGNORECASE)
        if de_por_match:
            metric_segment = metric_segment or de_por_match.group(1)
            dimension_segment = dimension_segment or de_por_match.group(2)

    if not dimension_segment:
        por_match = re.search(r"\bpor\s+(.+?)(?=$|,)", surface_prompt, flags=re.IGNORECASE)
        if por_match:
            dimension_segment = por_match.group(1)

    dimension_candidates = SemanticTranslator._resolve_segment_columns(
        dimension_segment or surface_prompt,
        columns,
        schema_profile=schema_profile,
        allowed_roles={"dimension", "identifier"},
    )
    metric_candidates = SemanticTranslator._resolve_segment_columns(
        metric_segment or surface_prompt,
        columns,
        schema_profile=schema_profile,
        allowed_roles={"metric"},
    )

    if not metric_candidates:
        default_metric = SemanticTranslator._infer_default_metric_column(
            surface_prompt,
            columns,
            schema_profile=schema_profile,
        )
        if default_metric:
            metric_candidates = [default_metric]

    if not dimension_candidates or not metric_candidates:
        return None

    dimension_column = dimension_candidates[0]
    metric_column = metric_candidates[0]
    cardinality = int(schema_profile.get(dimension_column, {}).get("cardinality") or 0)
    limit = explicit_top_limit
    if limit is None:
        if cardinality and cardinality <= 12:
            limit = cardinality
        else:
            limit = 10

    selected_visual = requested_visual or SemanticTranslator._select_default_distribution_visual(
        dimension_column,
        schema_profile=schema_profile,
    )
    metric_unit = infer_metric_unit_from_column_name(metric_column)
    visual_protocol = {
        "bar_chart": VisualProtocol.BAR,
        "pie_chart": VisualProtocol.PIE,
        "treemap": VisualProtocol.TREEMAP,
        "funnel_chart": VisualProtocol.FUNNEL,
    }[selected_visual]

    if top_requested:
        title = (
            f"Top {limit} {SemanticTranslator._humanize_column_alias(dimension_column)} "
            f"por {SemanticTranslator._humanize_column_alias(metric_column)}"
        )
    else:
        title = (
            f"{SemanticTranslator._humanize_column_alias(metric_column)} "
            f"por {SemanticTranslator._humanize_column_alias(dimension_column)}"
        )

    plan = AnalysisPlan(
        main_intent={
            "type": "distribution",
            "rationale": (
                "Priorizo una vista de concentración explícita para ordenar las categorías "
                "según la métrica solicitada y exponer el ranking dominante."
            ),
            "filters": [row.model_dump(mode="json") for row in default_snapshot_filters],
            "metric_unit": metric_unit.value if isinstance(metric_unit, MetricUnit) else MetricUnit.NUMBER.value,
            "visual_protocol": visual_protocol.value,
            "dimension": dimension_column,
            "metric": metric_column,
            "limit": limit,
            "group_by": None,
            "barmode": "stacked",
        },
        title=title,
        column_aliases={
            metric_column: SemanticTranslator._humanize_column_alias(metric_column),
            dimension_column: SemanticTranslator._humanize_column_alias(dimension_column),
        },
        metric_polarity=MetricPolarity.NEUTRAL,
    )
    emit_structured_log(
        "semantic_translator_fast_path_hit",
        prompt=prompt[:200],
        visual=selected_visual,
        metric=metric_column,
        dimension=dimension_column,
        limit=limit,
    )
    return [plan]


def build_deterministic_visual_plan(instance, prompt: str, columns: list[str], schema_profile: dict | None = None, dataset_contract: dict[str, Any] | None = None, allow_non_visual_prompt: bool = False):
    builders = (
        lambda p, c, s, d: SemanticTranslator._build_explicit_scatter_plan(p, c, s),
        lambda p, c, s, d: SemanticTranslator._build_explicit_trend_plan(
            p,
            c,
            s,
            allow_non_visual_prompt=allow_non_visual_prompt,
        ),
        lambda p, c, s, d: SemanticTranslator._build_explicit_distribution_plan(p, c, s, d),
    )
    for builder in builders:
        plans = builder(prompt, columns, schema_profile, dataset_contract)
        if plans:
            return plans
    return None


def apply_top_n_rollup_mode_to_plans(instance, prompt: str, plans: list[AnalysisPlan]):
    emit_structured_log(
        "semantic_translator_legacy_rollup_postprocessor_disabled",
        prompt=prompt[:200],
        plan_count=len(plans or []),
    )
    return plans


def detect_literal_filters(instance, prompt: str, dimension_values: Dict[str, list]):
    """
    Escanea el prompt del usuario buscando tokens que coincidan con valores REALES
    del dataset en columnas dimensionales. Retorna filtros obligatorios.

    Args:
        prompt: Texto del prompt del usuario
        dimension_values: Dict {columna: [valores_únicos]} de columnas categóricas

    Returns:
        Lista de DataFilter con filtros detectados
    """
    if not dimension_values:
        return []

    detected_filters: List[DataFilter] = []

    # Stopwords que NUNCA deben matchear como valores de dato
    stopwords = {
        'un', 'una', 'el', 'la', 'los', 'las', 'de', 'del', 'en', 'por',
        'para', 'con', 'que', 'como', 'se', 'al', 'es', 'son', 'fue',
        'analisis', 'análisis', 'analiza', 'realiza', 'muestra', 'dame',
        'quiero', 'haz', 'grafico', 'gráfico', 'total', 'promedio',
        'tendencia', 'evolución', 'evolucion', 'distribución', 'distribucion',
        'profundiza', 'detalla', 'compara', 'nuevo', 'distinto',
        'más', 'mas', 'cual', 'cuál', 'datos', 'información', 'informacion',
        'ubicación', 'ubicacion', 'almacén', 'almacen', 'material', 'producto',
        'tipo', 'categoría', 'categoria', 'stock', 'cantidad', 'precio',
        'and', 'the', 'for', 'with', 'from', 'this', 'that'
    }

    # Tokenizar prompt: palabras y frases entre comillas
    # Primero buscar frases entrecomilladas (máxima prioridad)
    quoted_phrases = re.findall(r'["\']([^"\']+)["\']', prompt)

    # Luego tokens individuales (palabras de >2 caracteres que no sean stopwords)
    raw_tokens = prompt.split()
    clean_tokens = [
        t.strip('.,;:!?()[]{}"\'')
        for t in raw_tokens
        if len(t.strip('.,;:!?()[]{}"\'' )) > 2 and t.lower().strip('.,;:!?()[]{}"\'' ) not in stopwords
    ]

    # Combinar: frases entrecomilladas primero, luego tokens
    search_terms = [(phrase, True) for phrase in quoted_phrases] + [(token, False) for token in clean_tokens]

    matched_columns = set()  # Evitar duplicados por columna

    # 🧠 [FASE 4B] Dynamic Cardinality Indexer
    # Pre-procesamiento Optimizado: Convertir listas a SETS para búsqueda O(1)
    # Solo procesamos columnas que no hayamos matcheado aún (lazy)

    # Para evitar recalcular sets en cada token, lo hacemos por demanda o pre-calculamos.
    # Dado que son < 10k items, pre-calcular todo es rápido (<50ms).
    # Estructura: value_upper -> (original_value, col_name)
    # Manejo de colisiones: Si un valor existe en 2 filas, priorizamos la primera (o la más corta?)
    # Mejor estrategia: scan token vs each column set.

    columns_sets = {}
    for col, vals in dimension_values.items():
        # Filtramos None y convertimos a Upper Set
        if vals:
            columns_sets[col] = {str(v).upper(): v for v in vals if v is not None}

    for term, is_quoted in search_terms:
        term_upper = term.upper().strip()
        if len(term_upper) < 2:
            continue

        for col_name, val_map in columns_sets.items():
            if col_name in matched_columns:
                continue  # Ya matcheamos esta columna

            # 🚀 Fase 1: Búsqueda O(1) en Hash Map — coincidencia exacta
            if term_upper in val_map:
                original_value = val_map[term_upper]

                detected_filters.append(
                    DataFilter(
                        column=col_name,
                        operator=FilterOperator.EQUALS,
                        value=str(original_value)
                    )
                )
                matched_columns.add(col_name)
                print(f"🎯 [LITERAL FILTER] Match exacto: '{term}' → {col_name} == '{original_value}'")
                break  # Un token solo puede matchear una columna

            # 🔍 [V2] Fase 2: Fuzzy-Form Matching — plural/singular
            # Si el token no hace match exacto, buscar si algún valor del dataset
            # es prefijo del token o viceversa (diferencia máxima de 3 caracteres).
            # Estrategia schema-agnostic: no hardcodea reglas del idioma,
            # solo compara longitudes y prefijos para capturar:
            #   "egresos" → "Egreso", "ingresos" → "Ingreso", "ventas" → "Venta"
            # Solo aplica si el token es "suficientemente largo" (>4 chars) para
            # evitar falsos positivos con palabras cortas.
            if not is_quoted and len(term_upper) > 4:
                best_match: str | None = None
                best_diff: int = 4  # Máximo de diferencia de caracteres aceptable
                for candidate_upper, candidate_original in val_map.items():
                    len_term = len(term_upper)
                    len_cand = len(candidate_upper)
                    len_diff = abs(len_term - len_cand)
                    if len_diff >= best_diff:
                        continue
                    # Verificar que el más corto es prefijo del más largo
                    shorter = term_upper if len_term <= len_cand else candidate_upper
                    longer = candidate_upper if len_term <= len_cand else term_upper
                    if longer.startswith(shorter):
                        best_match = candidate_original
                        best_diff = len_diff

                if best_match is not None:
                    detected_filters.append(
                        DataFilter(
                            column=col_name,
                            operator=FilterOperator.EQUALS,
                            value=str(best_match)
                        )
                    )
                    matched_columns.add(col_name)
                    print(
                        f"🎯 [LITERAL FILTER] Match fuzzy-form: '{term}' → "
                        f"{col_name} == '{best_match}' (dif={best_diff} chars)"
                    )
                    break  # Un token solo puede matchear una columna

    if detected_filters:
        print(f"🎯 [LITERAL FILTER] {len(detected_filters)} filtro(s) detectado(s)")

    return detected_filters

