import json
from json import JSONDecoder, JSONDecodeError
from typing import Any, Optional

from app.core.config import settings
from app.core.gemini_client import genai
from app.core.langfuse_client import record_llm_call
from app.core.semantic_grammar import (
    AnalysisPlan,
    DataFilter,
    FilterOperator,
    MetricUnit,
    MetricPolarity,
    VisualProtocol,
    DescriptiveIntent,
    DistributionIntent,
    TimeTrendIntent,
)
from app.core.structured_logging import emit_structured_log
from app.services.ai_response_cache import build_cache_key, get_cached_json, set_cached_json
from app.services.direction_detector import should_split_by_flow_direction
from app.services.metric_semantics import infer_metric_unit_from_column_name, normalize_semantic_text
from app.services.semantic_translator.temporal_resolver import resolve_temporal_filter_value
from app.services.semantic_translator.core import (
    humanize_column_alias,
    normalize_surface_text,
    pick_primary_date_column,
)


def extract_json_code_block(raw_text: str) -> str:
    fenced_match = __import__("re").search(
        r"```(?:json)?\s*(.*?)\s*```", raw_text, flags=__import__("re").IGNORECASE | __import__("re").DOTALL
    )
    return fenced_match.group(1).strip() if fenced_match else raw_text.strip()


def split_json_documents(raw_text: str) -> list[dict | list]:
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


def parse_translator_payload(raw_text: str) -> dict | list:
    candidate = extract_json_code_block(raw_text)
    try:
        return json.loads(candidate)
    except JSONDecodeError:
        docs = split_json_documents(candidate)
        if not docs:
            raise
        if len(docs) == 1:
            return docs[0]
        return docs


def is_recoverable_translator_model_error(error: Exception) -> bool:
    error_text = str(error or "").lower()
    recoverable_markers = (
        "499", "cancelled", "canceled", "deadline", "timeout",
        "timed out", "504", "503", "429", "resource_exhausted",
        "quota", "rate limit", "rate_limit",
        "temporarily unavailable", "unavailable",
    )
    return any(marker in error_text for marker in recoverable_markers)


def is_quota_translator_model_error(error: Exception) -> bool:
    error_text = str(error or "").lower()
    quota_markers = ("429", "resource_exhausted", "quota", "rate limit", "rate_limit")
    return any(marker in error_text for marker in quota_markers)


def select_translator_fallback_model(primary_model_name: str) -> str | None:
    fallback_model_name = str(settings.NARRATIVE_FAST_MODEL_NAME or "").strip()
    primary_model_name = str(primary_model_name or "").strip()
    if not fallback_model_name or fallback_model_name == primary_model_name:
        return None
    return fallback_model_name


def sanitize_translator_payload_item(
    item: dict[str, Any],
    columns: list[str],
    payload_mode: str,
) -> dict[str, Any]:
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

            _VISUAL_ONLY_FIELDS = {"barmode", "chart_type", "chart_orientation"}
            for _vf in _VISUAL_ONLY_FIELDS:
                intent.pop(_vf, None)

    if 'filters' in item and isinstance(item['filters'], list):
        item['filters'] = [
            f for f in item['filters']
            if isinstance(f, dict) and f.get('column') in available_columns
        ]

    if 'join_keys' in item and isinstance(item['join_keys'], list):
        item['join_keys'] = [k for k in item['join_keys'] if k in available_columns]

    if 'pre_aggregation' in item and isinstance(item['pre_aggregation'], dict):
        agg = item['pre_aggregation']
        if 'group_by' in agg and isinstance(agg['group_by'], list):
            agg['group_by'] = [c for c in agg['group_by'] if c in available_columns]
        if 'metrics' in agg and isinstance(agg['metrics'], list):
            agg['metrics'] = [c for c in agg['metrics'] if c in available_columns]

    return item


def plans_from_translator_payload(parsed_data: Any, columns: list[str]) -> list[AnalysisPlan]:
    plans: list[AnalysisPlan] = []
    if isinstance(parsed_data, list):
        for i, item in enumerate(parsed_data[:5]):
            try:
                if isinstance(item, dict):
                    item = sanitize_translator_payload_item(item, columns, "multi")
                plans.append(AnalysisPlan.model_validate(item))
                title_preview = item.get('title', 'Sin título') if isinstance(item, dict) else 'Sin título'
                print(f"✅ [MULTI-PLAN] Plan {i+1} validado: {title_preview[:60]}")
            except Exception as val_e:
                print(f"⚠️ [MULTI-PLAN] Plan {i+1} inválido (Alucinación bloqueada o schema roto): {val_e}")
    else:
        if isinstance(parsed_data, dict):
            parsed_data = sanitize_translator_payload_item(parsed_data, columns, "single")
        plans.append(AnalysisPlan.model_validate(parsed_data))

    return plans


def generate_translator_plans_with_model(
    model_name: str,
    translator_input: str,
    columns: list[str],
) -> list[AnalysisPlan]:
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
    parsed_data = parse_translator_payload(clean_json)
    return plans_from_translator_payload(parsed_data, columns)


def schema_fingerprint(
    columns: list[str],
    schema_profile: dict | None = None,
    dataset_contract: dict[str, Any] | None = None,
) -> str:
    return build_cache_key(
        "semantic_router_schema",
        {
            "columns": list(columns or []),
            "schema_profile": schema_profile or {},
            "dataset_contract": dataset_contract or {},
        },
    )


def normalize_semantic_router_decision(payload: Any) -> dict[str, Any]:
    from app.services.semantic_translator.core import SEMANTIC_ROUTER_CONFIDENCE_THRESHOLD, SEMANTIC_ROUTER_COMPLEX_REASON_CODES

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
    if confidence < SEMANTIC_ROUTER_CONFIDENCE_THRESHOLD:
        route = "COMPLEJO"
        if "low_confidence" not in normalized_reason_codes:
            normalized_reason_codes.append("low_confidence")
    if any(code in SEMANTIC_ROUTER_COMPLEX_REASON_CODES for code in normalized_reason_codes):
        route = "COMPLEJO"
        if "conservative_policy" not in normalized_reason_codes:
            normalized_reason_codes.append("conservative_policy")

    semantic_contract = normalize_router_semantic_contract(
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


def normalize_router_semantic_contract(
    payload: Any,
    detected_intent: str = "unknown",
    requires_time: bool = False,
) -> dict[str, Any]:
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
        str(payload.get("series_mode") or payload.get("top_n_aggregation_mode") or payload.get("aggregation_mode") or "none")
    ).replace(" ", "_")
    series_mode_aliases = {
        "multi_series": "split", "per_item": "split", "each": "split",
        "separate": "split", "separado": "split", "desglosado": "split",
        "consolidated": "sum", "consolidado": "sum", "rollup": "sum",
        "combined": "sum", "single_series": "sum", "total": "sum",
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


def infer_default_metric_column(
    surface_prompt: str,
    columns: list[str],
    schema_profile: dict | None = None,
) -> str | None:
    schema_profile = schema_profile or {}
    metric_candidates = [
        column_name
        for column_name in columns
        if schema_profile.get(column_name, {}).get("role") == "metric"
    ]
    if not metric_candidates:
        return None
    if len(metric_candidates) == 1:
        return metric_candidates[0]

    compact_prompt = surface_prompt.replace(" ", "")
    ranked: list[tuple[int, str]] = []
    for column_name in metric_candidates:
        col_norm = normalize_semantic_text(str(column_name).replace("_", " "))
        compact_col = col_norm.replace(" ", "")
        score = 0

        if compact_col and compact_col in compact_prompt:
            score += 100 + len(compact_col)

        score += sum(10 for token in col_norm.split() if len(token) > 1 and token in surface_prompt)

        if any(keyword in col_norm for keyword in (
            "stock", "cantidad", "venta", "ingreso", "importe", "monto",
            "precio", "costo", "volumen", "unidades", "piezas",
        )):
            score += 4

        ranked.append((score, column_name))

    ranked.sort(key=lambda item: (-item[0], item[1]))
    return ranked[0][1] if ranked else None


def resolve_contract_column(
    hint: str | None,
    columns: list[str],
    schema_profile: dict | None = None,
    allowed_roles: set[str] | None = None,
) -> str | None:
    if not hint:
        return None

    schema_profile = schema_profile or {}
    if hint in columns:
        role = schema_profile.get(hint, {}).get("role")
        if not allowed_roles or role in allowed_roles:
            return hint

    from app.services.semantic_translator.core import resolve_segment_columns
    candidates = resolve_segment_columns(hint, columns, schema_profile=schema_profile, allowed_roles=allowed_roles)
    return candidates[0] if candidates else None


def normalize_router_filters(
    raw_filters: Any,
    columns: list[str],
    schema_profile: dict | None = None,
) -> list[dict[str, Any]]:
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

        resolved_column = resolve_contract_column(raw_column, columns, schema_profile=schema_profile)
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

        # ═══════════════════════════════════════════════════════════════
        # ADR-TEMPORAL-003: type="temporal" Structural Detection
        # Date: 2026-07-01
        # Status: ACCEPTED — DO NOT MODIFY without test_temporal_fortress.py GREEN
        #
        # DECISION: is_temporal_col debe verificar col_meta.get("type") == "temporal"
        # ademas de role=="time" y dtype patterns.
        #
        # RAZON: El canonical_schema_profiler asigna type="temporal" + role="date"
        # a columnas datetime. Sin el check de type, columnas como fecha_de_stock
        # (role="date", NO "time") no activan el temporal resolver, y los anos
        # ISO alucinados por el LLM pasan sin correccion.
        #
        # RIESGO DE ALTERAR: Si se elimina col_meta_type == "temporal", la ruta
        # SIMPLE retorna DataFrames vacios para cualquier dataset donde el
        # profiler asigna role="date" en vez de role="time". Regresion silenciosa.
        #
        # VALIDACION: test_temporal_fortress.py (T1, T2, T8)
        # ═══════════════════════════════════════════════════════════════
        # ── [V1] Temporal Resolver: resolve month names / between to ISO ──
        col_meta = schema_profile.get(resolved_column, {})
        col_role = col_meta.get("role") if isinstance(col_meta, dict) else None
        col_dtype = str(col_meta.get("dtype", "")).lower() if isinstance(col_meta, dict) else ""
        col_meta_type = str(col_meta.get("type", "")).lower() if isinstance(col_meta, dict) else ""
        is_temporal_col = (
            col_role == "time"
            or col_meta_type == "temporal"
            or "date" in col_dtype
            or "timestamp" in col_dtype
            or "datetime" in col_dtype
        )
        if is_temporal_col:
            resolved = resolve_temporal_filter_value(
                resolved_column, operator, value, schema_profile=schema_profile
            )
            if resolved:
                # El resolver produjo filtros ISO — agregar todos y saltar el filtro original
                for rf in resolved:
                    try:
                        rf_validated = DataFilter.model_validate(rf)
                        normalized_filters.append(rf_validated.model_dump(mode="json"))
                    except Exception:
                        pass
                continue
        # ── Fin Temporal Resolver ──

        role = col_role
        if operator in ("==", "=") and role == "dimension":
            operator = "ilike"
        try:
            validated = DataFilter.model_validate(
                {"column": resolved_column, "operator": operator, "value": value}
            )
        except Exception:
            continue
        normalized_filters.append(validated.model_dump(mode="json"))

    return normalized_filters


def apply_direction_guard_to_distribution_plans(
    plans: list[AnalysisPlan],
    schema_profile: dict | None,
) -> list[AnalysisPlan]:
    if not plans or not schema_profile:
        return plans

    decision = should_split_by_flow_direction(schema_profile)
    if not decision["should_split"]:
        return plans

    direction_column = decision["column_name"]
    for plan in plans:
        main_intent = getattr(plan, "main_intent", None)
        if not main_intent:
            continue
        intent_type = getattr(main_intent, "type", None)

        if intent_type in {"distribution", "descriptive"}:
            current_dimension = getattr(main_intent, "dimension", None)
            if current_dimension == direction_column:
                continue
            current_group_by = list(getattr(main_intent, "group_by", None) or [])
            if direction_column not in current_group_by:
                current_group_by.append(direction_column)
                setattr(main_intent, "group_by", current_group_by)
                if "barmode" in main_intent.model_fields:
                    main_intent.barmode = "stacked"
                emit_structured_log(
                    "direction_guard_injected_group_by",
                    plan_type="distribution",
                    dimension=current_dimension,
                    group_by=direction_column,
                    confidence=decision["confidence"],
                    rationale=decision["rationale"],
                )

        elif intent_type == "trend":
            existing_split = getattr(main_intent, "split_dimension", None)
            if existing_split == direction_column:
                continue
            setattr(main_intent, "split_dimension", direction_column)
            setattr(main_intent, "split_limit", 2)
            setattr(main_intent, "top_n_aggregation_mode", "split")
            emit_structured_log(
                "direction_guard_injected_trend_split",
                plan_type="trend",
                split_dimension=direction_column,
                replaced_split=existing_split,
                confidence=decision["confidence"],
                rationale=decision["rationale"],
            )
    return plans


def finalize_plans(
    plans: list[AnalysisPlan],
    schema_profile: dict | None,
) -> list[AnalysisPlan]:
    decision = should_split_by_flow_direction(schema_profile or {})
    emit_structured_log(
        "direction_guard_decision",
        level="critical",
        should_split=decision["should_split"],
        column_name=decision.get("column_name"),
        confidence=decision.get("confidence"),
        rationale=decision.get("rationale"),
        schema_keys=list((schema_profile or {}).keys())[:15],
    )
    return apply_direction_guard_to_distribution_plans(plans, schema_profile)


def detect_prompt_complexity(surface_prompt: str) -> dict[str, Any]:
    import re

    if not surface_prompt:
        return {
            "score": 0, "is_complex": False, "has_top_n": False,
            "has_temporal": False, "requires_rollup": False,
            "has_negated_split": False, "has_restrictive_marker": False,
        }

    from app.services.semantic_translator.core import extract_top_limit, mentions_temporal_language, is_top_n_rollup_request

    has_top_n = extract_top_limit(surface_prompt) is not None
    has_temporal = mentions_temporal_language(surface_prompt)
    requires_rollup = is_top_n_rollup_request(surface_prompt)
    has_negated_split = bool(
        re.search(
            r"\bno\b.{0,80}\b(?:cada|individual|separad[ao]s?|desglosad[ao]s?|lineas?|series?)\b",
            surface_prompt, flags=re.IGNORECASE,
        )
    )
    has_restrictive_marker = any(
        marker in surface_prompt
        for marker in (
            "pero", "solo", "solamente", "exclusivamente", "excepto", "salvo",
            "sin ", "en lugar de", "no muestres", "no me des", "no mostrar",
            "dame la suma", "consolid", "agrupad", "suma total",
        )
    )

    score = 0
    score += 2 if has_top_n and has_temporal else 0
    score += 3 if requires_rollup else 0
    score += 2 if has_negated_split else 0
    score += 1 if has_restrictive_marker else 0
    score += 1 if len(surface_prompt.split()) >= 18 else 0

    return {
        "score": score, "is_complex": score >= 3,
        "has_top_n": has_top_n, "has_temporal": has_temporal,
        "requires_rollup": requires_rollup,
        "has_negated_split": has_negated_split,
        "has_restrictive_marker": has_restrictive_marker,
    }


def fast_path_unresolved_constraints(
    prompt: str,
    plans: list[AnalysisPlan] | None,
) -> list[str]:
    surface_prompt = normalize_surface_text(prompt)
    complexity = detect_prompt_complexity(surface_prompt)
    if not complexity.get("is_complex"):
        return []

    plans = list(plans or [])
    trend_plans = [
        plan for plan in plans
        if getattr(getattr(plan, "main_intent", None), "type", None) == "trend"
    ]
    unresolved: list[str] = []

    if complexity["has_temporal"] and complexity["has_top_n"] and not trend_plans:
        unresolved.append("temporal_top_n_requires_trend")

    if complexity["requires_rollup"]:
        satisfied_rollup = any(
            getattr(plan.main_intent, "split_dimension", None)
            and getattr(plan.main_intent, "split_limit", None)
            and getattr(plan.main_intent, "top_n_aggregation_mode", None) == "sum"
            for plan in trend_plans
        )
        if not satisfied_rollup:
            unresolved.append("top_n_rollup_not_satisfied")

    if complexity["has_negated_split"]:
        split_mode_used = any(
            getattr(plan.main_intent, "split_dimension", None)
            and getattr(plan.main_intent, "top_n_aggregation_mode", "split") != "sum"
            for plan in trend_plans
        )
        if split_mode_used:
            unresolved.append("negated_split_not_satisfied")

    return unresolved
