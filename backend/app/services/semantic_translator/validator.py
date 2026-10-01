import json
from datetime import datetime
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
    TimeGrain,
    TimeTrendIntent,
    canonicalize_aggregation,
)
from app.core.time_grain import MIN_SERIES_POINTS, detect_explicit_grain, resolve_time_grain
from app.core.analytical_contract import (
    ANALYTICAL_CONTRACT_VERSION,
    ColumnResolution,
    FilterSpec,
    QueryAnalyticalContractV1,
)

from app.core.structured_logging import emit_structured_log
from app.services.ai_response_cache import build_cache_key, get_cached_json, set_cached_json
from app.services.direction_detector import should_split_by_flow_direction
from app.services.metric_semantics import infer_metric_unit_from_column_name, normalize_semantic_text
from app.services.semantic_translator.temporal_resolver import resolve_temporal_filter_value
from app.services.semantic_translator.core import (
    extract_domain_residue,
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


def _keep_known_filter_columns(filters: Any, available_columns: set[str], scope: str) -> Any:
    """[F1.6b] Filtra columnas desconocidas y LOGGEA lo descartado (antes silencioso)."""
    if not isinstance(filters, list):
        return filters
    kept = [f for f in filters if isinstance(f, dict) and f.get("column") in available_columns]
    if len(kept) != len(filters):
        dropped = [
            str(f.get("column"))
            for f in filters
            if isinstance(f, dict) and f.get("column") not in available_columns
        ]
        emit_structured_log(
            "translator_unknown_filter_dropped",
            level="warning",
            scope=scope,
            dropped=dropped[:10],
        )
    return kept


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
                intent['filters'] = _keep_known_filter_columns(
                    intent['filters'], available_columns, "intent.filters"
                )

            if 'negative_filters' in intent and isinstance(intent['negative_filters'], list):
                intent['negative_filters'] = _keep_known_filter_columns(
                    intent['negative_filters'], available_columns, "intent.negative_filters"
                )

            # [HARDENING 2026-09] positive_filters también se sanea (antes solo
            # filters/negative_filters), para coherencia con el merge del engine.
            if 'positive_filters' in intent and isinstance(intent['positive_filters'], list):
                intent['positive_filters'] = _keep_known_filter_columns(
                    intent['positive_filters'], available_columns, "intent.positive_filters"
                )

            scalar_metric_fields = ['plot_metric', 'ranking_metric', 'secondary_value_column']
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
        item['filters'] = _keep_known_filter_columns(
            item['filters'], available_columns, "plan.filters"
        )

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
            "contract_version": ANALYTICAL_CONTRACT_VERSION,
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
        "aggregation": canonicalize_aggregation(payload.get("aggregation")),
        "visual_protocol": normalize_semantic_text(str(payload.get("visual_protocol") or "")).replace(" ", "_") or None,
        "requires_time": bool(payload.get("requires_time", requires_time)),
    }


def _score_metric_prompt_relevance(
    column_name: str, surface_prompt: str
) -> int:
    col_norm = normalize_semantic_text(str(column_name).replace("_", " "))
    compact_col = col_norm.replace(" ", "")
    compact_prompt = surface_prompt.replace(" ", "")
    score = 0

    if compact_col and compact_col in compact_prompt:
        score += 100 + len(compact_col)

    score += sum(10 for token in col_norm.split() if len(token) > 1 and token in surface_prompt)

    if any(keyword in col_norm for keyword in (
        "stock", "cantidad", "venta", "ingreso", "importe", "monto",
        "precio", "costo", "volumen", "unidades", "piezas",
    )):
        score += 4

    return score


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

    ranked: list[tuple[int, str]] = []
    for column_name in metric_candidates:
        score = _score_metric_prompt_relevance(column_name, surface_prompt)
        ranked.append((score, column_name))

    ranked.sort(key=lambda item: (-item[0], item[1]))
    return ranked[0][1] if ranked else None


def resolve_contract_column_resolution(
    hint: str | None,
    columns: list[str],
    schema_profile: dict | None = None,
    allowed_roles: set[str] | None = None,
    aggregation: str | None = None,
) -> ColumnResolution:
    """Resuelve analíticamente una columna y retorna un ColumnResolution tipado.

    Aplica las 4 reglas innegociables del Fortress Standard:
      1. 'incompatible': Agregaciones aritméticas (SUM, AVG, etc.) sobre IDs, claves o fechas.
      2. 'resolved': Coincidencia exacta de nombre o match unívoco dominante.
      3. 'ambiguous': Múltiples candidatos plausibles sin match unívoco.
      4. 'missing': La columna o métrica no existe en el conjunto de datos.
    """
    if not hint:
        return ColumnResolution(column_name=None, status="missing", reason="Nombre o sugerencia de columna vacío o nulo")

    schema_profile = schema_profile or {}

    # Regla 1: Validar incompatibilidad de agregación
    canonical_agg = canonicalize_aggregation(aggregation) if aggregation is not None else None
    if canonical_agg in ("sum", "avg"):
        target_col = hint if hint in columns else None
        if target_col:
            col_meta = schema_profile.get(target_col, {})
            col_role = col_meta.get("role")
            col_type = str(col_meta.get("type", "")).lower()
            if col_role in ("identifier", "entity_key") or "id" in target_col.lower().split("_"):
                return ColumnResolution(
                    column_name=target_col,
                    status="incompatible",
                    candidates=[target_col],
                    reason=f"Agregación '{aggregation}' no es válida sobre la columna identificadora '{target_col}'.",
                )
            if col_role == "date" or "date" in col_type or "time" in col_type:
                return ColumnResolution(
                    column_name=target_col,
                    status="incompatible",
                    candidates=[target_col],
                    reason=f"Agregación '{aggregation}' no es válida sobre la columna temporal '{target_col}'.",
                )

    # Regla 2: Coincidencia exacta directa en lista de columnas
    if hint in columns:
        role = schema_profile.get(hint, {}).get("role")
        if not allowed_roles or role in allowed_roles:
            return ColumnResolution(column_name=hint, status="resolved", candidates=[hint])

    from app.services.semantic_translator.core import resolve_segment_columns
    candidates = resolve_segment_columns(hint, columns, schema_profile=schema_profile, allowed_roles=allowed_roles)

    if not candidates:
        return ColumnResolution(
            column_name=None,
            status="missing",
            candidates=[],
            reason=f"La columna o métrica '{hint}' no existe en el conjunto de datos.",
        )

    # Candidato único
    if len(candidates) == 1:
        return ColumnResolution(column_name=candidates[0], status="resolved", candidates=candidates)

    # Si hay múltiples candidatos, comprobar si el hint tiene match exacto insensible a mayúsculas/guiones
    norm_hint = hint.strip().lower().replace("_", " ")
    exact_matches = [
        c for c in candidates
        if c.strip().lower() == hint.strip().lower() or c.strip().lower().replace("_", " ") == norm_hint
    ]
    if len(exact_matches) == 1:
        return ColumnResolution(column_name=exact_matches[0], status="resolved", candidates=candidates)

    # Si hay múltiples candidatos y exactamente uno cumple con allowed_roles
    if allowed_roles:
        matching_role = [
            c for c in candidates if schema_profile.get(c, {}).get("role") in allowed_roles
        ]
        if len(matching_role) == 1:
            return ColumnResolution(column_name=matching_role[0], status="resolved", candidates=candidates)

    # Ambigüedad: Múltiples columnas posibles sin ganador unívoco
    return ColumnResolution(
        column_name=None,
        status="ambiguous",
        candidates=candidates,
        reason=f"Ambigüedad: Múltiples columnas posibles para '{hint}': {', '.join(candidates)}",
    )


def resolve_contract_column(
    hint: str | None,
    columns: list[str],
    schema_profile: dict | None = None,
    allowed_roles: set[str] | None = None,
    aggregation: str | None = None,
) -> str | None:
    res = resolve_contract_column_resolution(
        hint,
        columns,
        schema_profile=schema_profile,
        allowed_roles=allowed_roles,
        aggregation=aggregation,
    )
    if res.status == "resolved":
        return res.column_name

    return None


def build_query_analytical_contract(
    *,
    query_id: str,
    file_id: str,
    intent_type: str,
    plans: list[AnalysisPlan],
    columns: list[str],
    schema_profile: dict | None = None,
    clarification_prompt: str | None = None,
) -> QueryAnalyticalContractV1:
    """Construye un QueryAnalyticalContractV1 evaluando resoluciones y determinando el estado analítico."""
    schema_profile = schema_profile or {}

    metric_resolutions: list[ColumnResolution] = []
    dimension_resolutions: list[ColumnResolution] = []
    filter_specs: list[FilterSpec] = []

    state = "valid"
    block_reason: str | None = None
    prompt_clarification: str | None = clarification_prompt

    for plan in plans:
        intent = plan.main_intent
        # 1. Métricas
        metrics: list[str] = list(getattr(intent, "metrics", None) or [])
        if getattr(intent, "metric", None):
            metrics.append(str(getattr(intent, "metric")))
        if getattr(intent, "value_column", None):
            metrics.append(str(getattr(intent, "value_column")))

        agg = getattr(intent, "aggregation", "sum")
        for m in metrics:
            res = resolve_contract_column_resolution(
                m, columns, schema_profile=schema_profile, allowed_roles={"metric"}, aggregation=agg
            )
            metric_resolutions.append(res)
            if res.status == "incompatible":
                state = "blocked"
                block_reason = res.reason
            elif res.status == "missing" and state != "blocked":
                state = "blocked"
                block_reason = res.reason
            elif res.status == "ambiguous" and state not in ("blocked", "clarification_required"):
                state = "clarification_required"
                prompt_clarification = res.reason

        # 2. Dimensiones
        dims: list[str] = []
        if getattr(intent, "dimension", None):
            dims.append(str(getattr(intent, "dimension")))
        for g in (getattr(intent, "group_by", None) or []):
            dims.append(str(g))

        for d in dims:
            res = resolve_contract_column_resolution(
                d, columns, schema_profile=schema_profile, allowed_roles={"dimension", "identifier"}
            )
            dimension_resolutions.append(res)
            if res.status == "missing" and state != "blocked":
                state = "blocked"
                block_reason = res.reason
            elif res.status == "ambiguous" and state not in ("blocked", "clarification_required"):
                state = "clarification_required"
                prompt_clarification = res.reason

        # 3. Filtros
        for f in (getattr(intent, "filters", None) or []):
            col = str(getattr(f, "column", ""))
            op = getattr(f, "operator", "==")
            val = getattr(f, "value", None)
            filter_specs.append(
                FilterSpec(column=col, operator=getattr(op, "value", str(op)), value=val, origin="system")
            )

    return QueryAnalyticalContractV1(
        query_id=query_id,
        file_id=file_id,
        intent_type=intent_type,
        metrics=metric_resolutions,
        dimensions=dimension_resolutions,
        filters=filter_specs,
        state=state,
        clarification_prompt=prompt_clarification,
        block_reason=block_reason,
    )



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
            emit_structured_log(
                "translator_filter_column_unresolved",
                level="warning",
                column=raw_column,
            )
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
                    except Exception as exc:
                        emit_structured_log(
                            "translator_temporal_filter_invalid_dropped",
                            level="warning",
                            filter_payload=rf,
                            error=str(exc)[:160],
                        )
                continue
        # ── Fin Temporal Resolver ──

        role = col_role
        # [V2.3] Eliminado: conversión ilike para dimensiones. Causaba falsos
        # positivos (ej: "Inactivo" contiene "activo"). El motor Ibis ya maneja
        # lowercase/uppercase exacto vía UPPER(col) == UPPER(val) en ibis_engine.py:454.
        try:
            validated = DataFilter.model_validate(
                {"column": resolved_column, "operator": operator, "value": value}
            )
        except Exception as exc:
            emit_structured_log(
                "translator_filter_invalid_dropped",
                level="warning",
                column=resolved_column,
                operator=operator,
                error=str(exc)[:160],
            )
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


def _canon_scalar(value: Any) -> str:
    """Canonicaliza un escalar para la firma de dedup (independiente del tipo).

    DeepSeek alterna tipos al emitir JSON (2024 vs "2024", 1 vs 1.0). Sin esta
    normalización, dos planes idénticos tendrían firmas distintas y el duplicado
    real sobreviviría.
    """
    if value is None:
        return ""
    if isinstance(value, bool):  # antes que int (bool es subclase de int)
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        as_float = float(value)
        return str(int(as_float)) if as_float.is_integer() else repr(as_float)
    text = str(value).strip()
    try:
        as_float = float(text)
        return str(int(as_float)) if as_float.is_integer() else repr(as_float)
    except (ValueError, TypeError):
        return text.casefold()


def _filter_fingerprint(intent: Any) -> tuple:
    """Huella canónica de TODOS los filtros de un intent.

    Incluye filters/positive_filters/negative_filters porque cambian el universo
    de datos: un plan filtrado por ciudad='Lima' NO es duplicado de uno filtrado
    por ciudad='Arequipa'.
    """
    raw_filters = (
        list(getattr(intent, "filters", []) or [])
        + list(getattr(intent, "positive_filters", []) or [])
        + list(getattr(intent, "negative_filters", []) or [])
    )
    normalized: list[tuple[str, str, Any]] = []
    for data_filter in raw_filters:
        column = str(getattr(data_filter, "column", "") or "").strip()
        operator = str(
            getattr(getattr(data_filter, "operator", ""), "value", getattr(data_filter, "operator", "")) or ""
        ).strip()
        value = getattr(data_filter, "value", "")
        if isinstance(value, (list, tuple, set)):
            value_fp: Any = tuple(sorted(_canon_scalar(item) for item in value))
        else:
            value_fp = _canon_scalar(value)
        normalized.append((column, operator, value_fp))
    return tuple(sorted(normalized))


def _plan_dedup_signature(plan: Any) -> tuple:
    """Firma estructural de un plan para deduplicación (ADR V2.4).

    Lee desde main_intent (no desde el AnalysisPlan): visual_protocol vive en
    BaseIntent. Soporta métricas en lista (DescriptiveIntent/DiagnosticIntent) y
    dimensiones compuestas (group_by/split_dimension).
    """
    intent = getattr(plan, "main_intent", None)
    if intent is None:
        return ("__no_intent__", id(plan))

    intent_type = str(getattr(intent, "type", "") or "")

    dimensions: set[str] = set()
    for attr in ("dimension", "date_column", "split_dimension"):
        value = getattr(intent, attr, None)
        if value:
            dimensions.add(str(value))
    for value in getattr(intent, "group_by", None) or []:
        if value:
            dimensions.add(str(value))

    metrics: set[str] = set()
    for attr in ("metric", "value_column", "analysis_metric", "target_metric", "secondary_value_column"):
        value = getattr(intent, attr, None)
        if value:
            metrics.add(str(value))
    for value in getattr(intent, "metrics", None) or []:
        if value:
            metrics.add(str(value))

    aggregation = str(
        getattr(intent, "aggregation", "") or getattr(plan, "aggregation_function", "") or ""
    )
    visual_raw = getattr(intent, "visual_protocol", None)
    visual = str(getattr(visual_raw, "value", visual_raw) or "")

    return (
        intent_type,
        tuple(sorted(dimensions)),
        tuple(sorted(metrics)),
        aggregation,
        visual,
        _filter_fingerprint(intent),
    )


def _contract_span_days(dataset_contract: dict | None, date_column: str | None) -> float | None:
    """Rango temporal (en días) del contrato, si aplica al eje del plan."""
    if not isinstance(dataset_contract, dict) or not date_column:
        return None
    time_axis = str(dataset_contract.get("time_axis") or "").strip()
    if time_axis and time_axis != str(date_column):
        return None
    evidence = dataset_contract.get("evidence") if isinstance(dataset_contract.get("evidence"), dict) else {}
    raw_min = evidence.get("min_date") or dataset_contract.get("min_date")
    raw_max = evidence.get("max_date") or dataset_contract.get("max_date")
    if not raw_min or not raw_max:
        return None
    try:
        start = datetime.fromisoformat(str(raw_min))
        end = datetime.fromisoformat(str(raw_max))
    except (TypeError, ValueError):
        return None
    return max((end - start).total_seconds() / 86400.0, 0.0)


def _plan_covered_concepts(plan: AnalysisPlan) -> set[str]:
    intent = getattr(plan, "main_intent", None)
    if intent is None:
        return set()
    covered: set[str] = set()
    for attr in ("dimension", "value_column", "date_column", "split_dimension", "ranking_metric", "secondary_value_column"):
        value = getattr(intent, attr, None)
        if isinstance(value, str) and value:
            covered.add(value)
    metrics = getattr(intent, "metrics", None)
    if isinstance(metrics, (list, tuple)):
        covered.update(str(m) for m in metrics if m)
    return covered


def _log_named_concept_coverage(
    plans: list[AnalysisPlan],
    schema_profile: dict | None,
    prompt: str | None,
) -> None:
    """Telemetría de la clase (no destructiva): ¿el prompt nombra un concepto del
    schema que ningún plan cubre? Permite detectar blind spots en producción sin
    romper planes legítimos."""
    if not prompt or not isinstance(schema_profile, dict) or not plans:
        return
    try:
        residue = extract_domain_residue(prompt)
        if not residue:
            return
        covered: set[str] = set()
        for plan in plans:
            covered.update(_plan_covered_concepts(plan))
        matched: list[str] = []
        for column_name in schema_profile.keys():
            col_norm = normalize_surface_text(str(column_name).replace("_", " "))
            col_tokens = [t for t in col_norm.split() if len(t) > 2]
            if not col_tokens:
                continue
            if any(token in residue for token in col_tokens):
                matched.append(str(column_name))
        missing = [col for col in matched if col not in covered]
        if missing:
            emit_structured_log(
                "semantic_named_concept_not_covered",
                level="warning",
                prompt=prompt[:200],
                domain_residue=residue[:10],
                uncovered_concepts=missing[:10],
                covered_concepts=sorted(covered)[:10],
            )
    except Exception:
        return


def _residue_token_covered(token: str, plans: list[AnalysisPlan], schema_profile: dict | None) -> bool:
    """¿El token del prompt se relaciona con alguna columna del schema o plan?"""
    token_norm = normalize_surface_text(str(token))
    if len(token_norm) <= 3:
        # Demasiado corto para juzgar cobertura: no se divulga (fail-closed).
        return True
    for column_name in (schema_profile or {}).keys():
        col_norm = normalize_surface_text(str(column_name).replace("_", " "))
        if col_norm and (token_norm in col_norm or col_norm in token_norm):
            return True
    for plan in plans:
        for concept in _plan_covered_concepts(plan):
            c_norm = normalize_surface_text(str(concept).replace("_", " "))
            if c_norm and (token_norm in c_norm or c_norm in token_norm):
                return True
    return False


def _plans_used_metrics(plans: list[AnalysisPlan]) -> list[str]:
    metrics: list[str] = []
    for plan in plans:
        intent = getattr(plan, "main_intent", None)
        if intent is None:
            continue
        for attr in ("value_column", "metric", "date_column"):
            value = getattr(intent, attr, None)
            if isinstance(value, str) and value and value not in metrics:
                metrics.append(value)
        for extra in getattr(intent, "metrics", None) or []:
            if isinstance(extra, str) and extra and extra not in metrics:
                metrics.append(extra)
    return metrics


def build_coverage_disclosure(
    plans: list[AnalysisPlan],
    schema_profile: dict | None,
    prompt: str | None,
    extra_concepts: list[str] | None = None,
) -> str | None:
    """[P6 2026-09] Nota honesta cuando el usuario nombra algo que no existe.

    Dos señales:
    1. `extra_concepts`: lo que el LLM marcó como no resoluble (autoridad
       semántica; entiende sinónimos y typos).
    2. Fallback determinista ESTRECHO: solo si el pedido es esencialmente UN
       concepto y ninguna columna/plan lo cubre. Evita falsos positivos en
       prompts con palabras de relleno ("top", "relación", "productos"...).

    Nunca bloquea: siempre se responde. Es puro (no ejecuta consultas).
    """
    if not plans:
        return None
    concepts = [str(c).strip() for c in (extra_concepts or []) if str(c).strip()]
    if not concepts and prompt and isinstance(schema_profile, dict):
        try:
            residue = extract_domain_residue(prompt)
        except Exception:
            residue = []
        if len(residue) == 1 and not _residue_token_covered(residue[0], plans, schema_profile):
            concepts = [residue[0]]
    if not concepts:
        return None
    missing = ", ".join(f"«{c}»" for c in sorted(set(concepts))[:5])
    used = _plans_used_metrics(plans)
    used_label = ", ".join(used[:3]) if used else "la información disponible"
    return (
        f"⚠️ No se encontró una columna relacionada con {missing}. "
        f"El análisis se generó con {used_label}. "
        f"Revisa el nombre o carga un archivo que contenga esa medida."
    )


def apply_coverage_disclosure(
    plans: list[AnalysisPlan],
    schema_profile: dict | None,
    prompt: str | None,
    extra_concepts: list[str] | None = None,
) -> str | None:
    """Calcula y adjunta la divulgación de cobertura a los planes (aditivo)."""
    disclosure = build_coverage_disclosure(plans, schema_profile, prompt, extra_concepts)
    if disclosure:
        for plan in plans:
            plan.coverage_disclosure = disclosure
    return disclosure


def _apply_temporal_invariants(
    plans: list[AnalysisPlan],
    schema_profile: dict | None,
    dataset_contract: dict | None,
    prompt: str | None,
) -> list[AnalysisPlan]:
    """Invariantes temporales universales (domain-agnostic).

    1. Un trend sobre un eje con < 2 periodos distintos no es una serie → se
       descarta (evita "tendencias" inferidas de un único punto).
    2. La granularidad se adapta al rango real de los datos: si MONTH colapsaría
       la serie a < 2 puntos, se refina a WEEK/DAY. Si el usuario pidió una
       granularidad explícita, se respeta.
    """
    schema_profile = schema_profile if isinstance(schema_profile, dict) else {}
    explicit_grain = detect_explicit_grain(prompt) if prompt else None
    kept: list[AnalysisPlan] = []
    for plan in plans:
        intent = getattr(plan, "main_intent", None)
        if getattr(intent, "type", None) != "trend":
            kept.append(plan)
            continue

        date_column = getattr(intent, "date_column", None)
        raw_cardinality = schema_profile.get(date_column, {}).get("cardinality") if date_column else None
        try:
            cardinality = int(raw_cardinality) if raw_cardinality is not None else None
        except (TypeError, ValueError):
            cardinality = None

        if cardinality is not None and cardinality < MIN_SERIES_POINTS:
            emit_structured_log(
                "semantic_trend_dropped_insufficient_axis",
                level="warning",
                title=plan.title,
                date_column=date_column,
                distinct_dates=cardinality,
            )
            continue

        span_days = _contract_span_days(dataset_contract, date_column)
        requested = getattr(intent, "grain", None) or TimeGrain.MONTH
        adapted = resolve_time_grain(
            requested,
            span_days,
            cardinality,
            prompt_explicit=explicit_grain,
        )
        if adapted != requested:
            try:
                intent.grain = adapted
            except (ValueError, TypeError):
                pass
            emit_structured_log(
                "semantic_trend_grain_adapted",
                title=plan.title,
                date_column=date_column,
                requested_grain=str(getattr(requested, "value", requested)),
                adapted_grain=str(getattr(adapted, "value", adapted)),
                span_days=round(span_days, 2) if span_days is not None else None,
                distinct_dates=cardinality,
            )
        kept.append(plan)
    return kept


def _neutralize_hallucinated_secondary_columns(
    plans: list[AnalysisPlan],
    schema_profile: dict | None,
) -> list[AnalysisPlan]:
    """[#5 2026-09] Fail-closed: si un trend declara `secondary_value_column`
    que no existe en el schema, se anula (el trend queda de una sola serie).

    Nunca bloquea el plan: un campo opcional alucinado degrada al comportamiento
    previo, jamás mata el análisis. Corre en `finalize_plans` (choque point de
    las 13 rutas: macro, simple, unified, caché) porque la ruta unified NO pasa
    por `sanitize_translator_payload_item`.
    """
    if not plans:
        return plans
    known = set((schema_profile or {}).keys())
    if not known:
        # Sin columnas conocidas no se puede validar: no se toca (el engine
        # vuelve a aplicar su propio guard antes de ejecutar).
        return plans
    for plan in plans:
        intent = getattr(plan, "main_intent", None)
        if intent is None or getattr(intent, "type", None) != "trend":
            continue
        secondary = getattr(intent, "secondary_value_column", None)
        if isinstance(secondary, str) and secondary and secondary not in known:
            try:
                intent.secondary_value_column = None
            except (ValueError, TypeError):
                continue
            emit_structured_log(
                "semantic_secondary_metric_dropped",
                level="warning",
                title=getattr(plan, "title", None),
                secondary_value_column=secondary,
            )
    return plans


_COMBO_VISUALS = {"combo_chart", "dual_axis_chart"}


def _visual_protocol_id(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "").strip().lower()


def _apply_derived_secondary_default(
    plans: list[AnalysisPlan],
    prompt: str | None,
    schema_profile: dict | None,
) -> list[AnalysisPlan]:
    """[Fase 1.3 2026-09] Activa la serie derivada MoM para un combo de una sola métrica.

    Cuando el usuario pide "gráfico combinado"/"doble eje" (o el plan declara
    `dual_axis_chart`) sobre un trend con UNA sola métrica real, no existe una 2ª
    columna: se declara la serie derivada `mom_pct` (variación % período a
    período), que el engine emite como `extra_info.secondary_value`. Domain-
    agnostic y fail-closed: nunca inventa columnas.
    """
    if not plans:
        return plans
    try:
        from app.services.visual_recommendation_engine import (
            extract_prompt_visual_requests,
            normalize_visual_id,
        )

        requested = {
            normalize_visual_id(value)
            for value in extract_prompt_visual_requests(prompt or "")
        }
    except Exception:
        requested = set()
    asks_combo = bool(requested & _COMBO_VISUALS)

    for plan in plans:
        intent = getattr(plan, "main_intent", None)
        if intent is None or getattr(intent, "type", None) != "trend":
            continue
        if getattr(intent, "secondary_value_column", None):
            continue
        if getattr(intent, "derived_secondary", None):
            continue
        requested_visual = _visual_protocol_id(getattr(intent, "visual_protocol", None))
        if asks_combo or requested_visual in _COMBO_VISUALS:
            intent.derived_secondary = "mom_pct"
            # Si el usuario pidió explícitamente un combinado y el plan venía
            # como línea, elevamos el protocolo para que el engine emita el
            # chart_type correcto (si no, gobernanza respeta la línea).
            if asks_combo and requested_visual not in _COMBO_VISUALS:
                try:
                    intent.visual_protocol = VisualProtocol.DUAL_AXIS
                except (ValueError, TypeError):
                    pass
            emit_structured_log(
                "semantic_derived_secondary_default",
                visual_protocol=requested_visual,
                derived_secondary="mom_pct",
                title=getattr(plan, "title", None),
            )
    return plans


def finalize_plans(
    plans: list[AnalysisPlan],
    schema_profile: dict | None,
    dataset_contract: dict | None = None,
    prompt: str | None = None,
) -> list[AnalysisPlan]:
    decision = should_split_by_flow_direction(schema_profile or {})
    emit_structured_log(
        "direction_guard_decision",
        level="info",
        should_split=decision["should_split"],
        column_name=decision.get("column_name"),
        confidence=decision.get("confidence"),
        rationale=decision.get("rationale"),
        schema_keys=list((schema_profile or {}).keys())[:15],
    )
    guarded_plans = apply_direction_guard_to_distribution_plans(plans, schema_profile)

    # [#5 2026-09] Fail-closed: anular la 2ª métrica del trend si no existe en
    # el schema (evita crash de DuckDB por columna alucinada en la ruta unified).
    guarded_plans = _neutralize_hallucinated_secondary_columns(guarded_plans, schema_profile)

    # [FIX V2.4] Desduplicar planes basandose en estructura del intent +
    # dimensión(es) + métrica(s) + agregación + tipo visual + huella de filtros.
    # Previene duplicados donde 2 planes tienen la misma data pero distinto título,
    # SIN borrar planes legítimos (p.ej. dos KPIs distintos, dos distribuciones con
    # group_by distinto, o comparaciones Lima vs Arequipa / 2023 vs 2024).
    dedup_plans = []
    seen = set()
    for p in guarded_plans:
        signature = _plan_dedup_signature(p)
        if signature not in seen:
            seen.add(signature)
            dedup_plans.append(p)

    finalized = _apply_temporal_invariants(
        dedup_plans, schema_profile, dataset_contract, prompt
    )
    # [Fase 1.3] Combo de una sola métrica → serie derivada MoM.
    finalized = _apply_derived_secondary_default(finalized, prompt, schema_profile)
    _log_named_concept_coverage(finalized, schema_profile, prompt)
    # [P6 2026-09] Divulgación honesta de cobertura (fallback determinista).
    apply_coverage_disclosure(finalized, schema_profile, prompt)
    return finalized


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
