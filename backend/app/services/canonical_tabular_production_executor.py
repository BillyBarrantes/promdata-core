from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Any

import pandas as pd

from app.core.structured_logging import emit_structured_log
from app.services.canonical_analytical_contract_adapter import get_selected_candidate_dataframe, get_related_frames
from app.services.canonical_dark_runtime_orchestrator import (
    run_canonical_dark_pipeline_for_uploaded_file,
)
from app.services.canonical_shadow_format_comparator import build_shadow_format_readiness_summary
from app.services.canonical_shadow_query_runner import (
    CanonicalShadowQueryExecution,
    _blocked_execution_result,
    _blocked_plan_metrics,
    _build_glossary_context,
    _build_topology_context,
    _get_ibis_engine_cls,
    _persist_shadow_candidate,
    _protected_columns,
    _summarize_execution_result,
    _summarize_plan,
)
from app.services.canonical_tabular_canary_executor import _build_final_struct
from app.services.analysis_memory_context import (
    apply_parent_context_to_placeholder_filters,
    build_parent_memory_context_text,
    load_parent_analysis_context,
    unwrap_prompt_payload,
)
from app.services.semantic_translator import SemanticTranslator
from app.services.semantic_translator.temporal_resolver import apply_relative_time_windows
from app.services.semantic_translator.validator import finalize_plans
from app.services.expiry_intent import build_expiry_analysis_bundle
from app.services.metric_semantics import align_plan_metrics_with_prompt
from app.services.semantic_translator.metric_archetype import (
    cumulative_aggregation,
    has_accumulation_lexicon,
    monotonic_direction,
)
from app.core.semantic_grammar import PreAggregationSpec


def _extract_previous_dimensions(parent_context: dict[str, Any] | None) -> list[str]:
    """[F2-D4] Dimensiones usadas en el análisis previo (para drill-down).

    Se leen del contexto estructurado del parent (semantic_context.plans[].dimensions),
    en lugar del parse legacy de "Agrupado por:" que nunca se emitía.
    """
    semantic_context = (parent_context or {}).get("semantic_context") or {}
    dimensions: list[str] = []
    for plan in list(semantic_context.get("plans") or []):
        if not isinstance(plan, dict):
            continue
        for dimension in list(plan.get("dimensions") or []):
            if dimension:
                dimensions.append(str(dimension))
        split_dimension = plan.get("split_dimension")
        if split_dimension:
            dimensions.append(str(split_dimension))
    return list(dict.fromkeys(dimensions))


# ═══════════════════════════════════════════════════════════════════════════
# [V4] METRIC HALLUCINATION GUARD — Auto-corrección de métricas alucinadas
# ═══════════════════════════════════════════════════════════════════════════
# Gemini puede inventar métricas que no existen en el dataset (ej:
# "tasa_rotacion", "conteo_inactivos"). Este middleware detecta esas
# alucinaciones y las reemplaza con métricas válidas, ajustando la
# agregación a COUNT y preservando las dimensiones del plan original.
#
# Para tasas/porcentajes: combina dimensiones (no aplica filtro exclusivo).
# Para conteos simples: inyecta el filtro correspondiente.
# ═══════════════════════════════════════════════════════════════════════════

_RATE_KEYWORDS = (
    "tasa", "porcentaje", "proporción", "ratio", "%",
    "percentage", "rate", "proportion",
)


def _detect_rate_request(prompt: str | None) -> bool:
    """Detecta si el prompt solicita una tasa, porcentaje o proporción."""
    if not prompt:
        return False
    prompt_lower = prompt.lower()
    return any(keyword in prompt_lower for keyword in _RATE_KEYWORDS)


def _extract_categorical_dimension(
    prompt: str | None,
    candidate_df: pd.DataFrame,
) -> str | None:
    """Busca columnas categóricas (cardinalidad 2-20) relevantes en el prompt.

    Prioriza coincidencia textual exacta (word-boundary) sobre heurística
    de cardinalidad, para que 'departamento' en el prompt → columna departamento
    incluso si otra columna (ej. cargo) tiene menor cardinalidad.
    """
    if candidate_df is None or candidate_df.empty:
        return None
    categorical_columns = [
        str(col) for col in candidate_df.columns
        if candidate_df[col].nunique() <= 20
        and candidate_df[col].dtype == "object"
    ]
    if not categorical_columns:
        return None
    if not prompt:
        return categorical_columns[0]
    prompt_lower = prompt.lower()
    # Paso 1: coincidencia exacta con word boundary (\b)
    for col in categorical_columns:
        col_lower = col.lower()
        if re.search(r'\b' + re.escape(col_lower) + r'\b', prompt_lower):
            return col
    # Paso 2: coincidencia por substring (fallback para nombres compuestos)
    for col in categorical_columns:
        if col.lower() in prompt_lower:
            return col
    return categorical_columns[0]


def _extract_filter_value_from_prompt(
    prompt: str | None,
    categorical_dimension: str,
    candidate_df: pd.DataFrame,
) -> str | None:
    """Extrae el valor del filtro del prompt para una columna categórica."""
    if not prompt or not categorical_dimension or categorical_dimension not in candidate_df.columns:
        return None
    unique_values = candidate_df[categorical_dimension].dropna().unique()
    prompt_lower = prompt.lower()
    for value in unique_values:
        if str(value).lower() in prompt_lower:
            return str(value)
    return None


def _find_count_metric(
    candidate_df: pd.DataFrame,
    schema_profile: dict | None = None,
) -> str:
    """Encuentra la mejor métrica para operaciones de conteo."""
    if candidate_df is None or candidate_df.empty:
        return "id"
    schema_profile = schema_profile or {}
    # Prioridad 1: columna identificador
    for col in candidate_df.columns:
        col_str = str(col).lower()
        if col_str.startswith("id_") or col_str == "id":
            return str(col)
    # Prioridad 2: columna numérica con role="metric"
    for col, info in schema_profile.items():
        if isinstance(info, dict) and info.get("role") == "metric" and col in candidate_df.columns:
            return str(col)
    # Prioridad 3: primera columna numérica
    for col in candidate_df.columns:
        try:
            if pd.api.types.is_numeric_dtype(candidate_df[col]):
                return str(col)
        except Exception:
            pass
    # Fallback: primera columna
    return str(candidate_df.columns[0])


def _apply_dimension_combination(
    plan: Any,
    categorical_dimension: str,
) -> None:
    """Combina la dimensión categórica con las existentes del plan sin sobrescribir."""
    intent = plan.main_intent
    intent_type = getattr(intent, "type", None)

    # Caso 1: Intent con group_by (DescriptiveIntent, DiagnosticIntent)
    if hasattr(intent, "group_by") and intent_type in ("descriptive", "diagnostic"):
        if intent.group_by is None:
            intent.group_by = [categorical_dimension]
        elif categorical_dimension not in intent.group_by:
            intent.group_by = list(intent.group_by) + [categorical_dimension]
        return

    # Caso 2: DistributionIntent (dimension + group_by)
    if intent_type == "distribution":
        if hasattr(intent, "group_by"):
            if intent.group_by is None:
                intent.group_by = [categorical_dimension]
            elif categorical_dimension not in intent.group_by:
                intent.group_by = list(intent.group_by) + [categorical_dimension]
        return

    # Caso 3: TimeTrendIntent (split_dimension para multi-series)
    if intent_type == "trend":
        if hasattr(intent, "split_dimension"):
            intent.split_dimension = categorical_dimension
        return

    # Caso 4: Fallback — usar group_by si existe
    if hasattr(intent, "group_by"):
        if intent.group_by is None:
            intent.group_by = [categorical_dimension]
        elif categorical_dimension not in intent.group_by:
            intent.group_by = list(intent.group_by) + [categorical_dimension]


def _replace_metric_in_plan(
    plan: Any,
    new_metric: str,
    new_aggregation: str,
) -> None:
    """Reemplaza la métrica alucinada por una válida en el plan."""
    intent = plan.main_intent
    intent_type = getattr(intent, "type", None)

    if intent_type == "descriptive":
        if hasattr(intent, "metrics") and intent.metrics:
            intent.metrics = [new_metric]
        if hasattr(intent, "aggregation"):
            intent.aggregation = new_aggregation

    elif intent_type == "trend":
        if hasattr(intent, "value_column"):
            intent.value_column = new_metric

    elif intent_type == "distribution":
        if hasattr(intent, "metric"):
            intent.metric = new_metric

    elif intent_type == "diagnostic":
        if hasattr(intent, "metric"):
            intent.metric = new_metric
        if hasattr(intent, "metrics") and intent.metrics:
            intent.metrics = [new_metric]
        if hasattr(intent, "aggregation"):
            intent.aggregation = new_aggregation

    elif intent_type == "predictive":
        if hasattr(intent, "value_column"):
            intent.value_column = new_metric


def _apply_filter_correction(
    plan: Any,
    count_metric: str,
    blocked_metrics: list,
    prompt: str | None,
    candidate_df: pd.DataFrame,
) -> None:
    """Aplica corrección con filtro para conteos simples (no tasas)."""
    intent = plan.main_intent

    # Reemplazar métrica
    _replace_metric_in_plan(plan, count_metric, "count")

    # Extraer dimensión categórica y valor del filtro
    categorical_dimension = _extract_categorical_dimension(prompt, candidate_df)

    if categorical_dimension:
        filter_value = _extract_filter_value_from_prompt(
            prompt, categorical_dimension, candidate_df
        )
        if filter_value and hasattr(intent, "filters"):
            if intent.filters is None:
                intent.filters = []
            from app.core.semantic_grammar import DataFilter, FilterOperator
            intent.filters = list(intent.filters) + [
                DataFilter(
                    column=categorical_dimension,
                    operator=FilterOperator.EQUALS,
                    value=filter_value,
                )
            ]
            emit_structured_log(
                "metric_correction_filter_injected",
                categorical_dimension=str(categorical_dimension),
                filter_value=str(filter_value),
                intent_type=getattr(intent, "type", None),
            )

    emit_structured_log(
        "metric_correction_filter_applied",
        blocked_metrics=[str(m) for m in blocked_metrics],
        count_metric=str(count_metric),
        intent_type=getattr(intent, "type", None),
    )


def _apply_metric_correction(
    plan: Any,
    count_metric: str,
    blocked_metrics: list,
    prompt: str | None,
    candidate_df: pd.DataFrame,
) -> None:
    """Aplica la corrección de métricas según el tipo de solicitud."""
    is_rate_request = _detect_rate_request(prompt)

    if is_rate_request:
        categorical_dimension = _extract_categorical_dimension(prompt, candidate_df)
        if categorical_dimension:
            _apply_dimension_combination(plan, categorical_dimension)
            _replace_metric_in_plan(plan, count_metric, "count")
            emit_structured_log(
                "metric_correction_rate_applied",
                blocked_metrics=[str(m) for m in blocked_metrics],
                count_metric=str(count_metric),
                categorical_dimension=str(categorical_dimension),
                intent_type=getattr(plan.main_intent, "type", None),
            )
        else:
            _apply_filter_correction(plan, count_metric, blocked_metrics, prompt, candidate_df)
    else:
        _apply_filter_correction(plan, count_metric, blocked_metrics, prompt, candidate_df)


def _auto_correct_hallucinated_metrics(
    plans: list[Any] | None,
    candidate_df: pd.DataFrame | None,
    prompt: str | None = None,
) -> list[Any]:
    """Auto-corrige métricas alucinadas en los planes generados por Gemini.

    [V9] AVG IMMUNITY: Si el plan explícitamente usa aggregation="avg" sobre
    columnas numéricas válidas, no se sobrescribe la agregación. El sistema de
    corrección solo actúa sobre métricas alucinadas o bloques incompletos.
    """
    if not plans or candidate_df is None:
        return plans if plans else []

    schema_profile = dict(
        (getattr(candidate_df, "attrs", {}) or {}).get("schema_profile", {}) or {}
    )
    count_metric = _find_count_metric(candidate_df, schema_profile)

    corrected_plans: list[Any] = []
    for plan in plans:
        try:
            # [V9] AVG IMMUNITY: preservar si el plan ya especifica avg sobre
            # columnas numéricas válidas — el LLM decidió correctamente.
            if _plan_has_valid_avg_on_numeric(plan, candidate_df):
                corrected_plans.append(plan)
                continue

            blocked_metrics = _blocked_plan_metrics(plan, candidate_df)
            if blocked_metrics:
                _pre_agg = getattr(plan, "pre_aggregation", None)
                _pre_agg_metrics = list(getattr(_pre_agg, "metrics", []) or []) if _pre_agg else []
                if _pre_agg_metrics:
                    blocked_metrics = [m for m in blocked_metrics if m not in _pre_agg_metrics]
            if blocked_metrics:
                _apply_metric_correction(
                    plan, count_metric, blocked_metrics, prompt, candidate_df
                )
        except Exception as _metric_guard_err:
            print(
                f"⚠️ [METRIC GUARD] Error no-fatal en auto-corrección: "
                f"{_metric_guard_err}"
            )
        corrected_plans.append(plan)

    return corrected_plans


def _intent_primary_metric(intent: Any) -> str | None:
    """Métrica principal de un intent (cualquiera de las familias)."""
    intent_type = getattr(intent, "type", None)
    if intent_type in ("descriptive", "diagnostic"):
        metrics = [m for m in (getattr(intent, "metrics", None) or []) if m]
        if metrics:
            return str(metrics[0])
    if intent_type in ("trend", "predictive"):
        value_column = getattr(intent, "value_column", None)
        if value_column:
            return str(value_column)
    if intent_type == "distribution":
        metric = getattr(intent, "metric", None)
        if metric:
            return str(metric)
    return None


def _guard_contract(candidate_df: pd.DataFrame) -> dict[str, Any]:
    """Contrato del dataset desde los attrs (soporta la clave legacy y la nueva).

    Producción escribe ``semantic_contract`` (adapter) mientras que la clave
    histórica es ``dataset_contract``; leer ambas evita que el fallback por
    contrato quede muerto.
    """
    attrs = getattr(candidate_df, "attrs", {}) or {}
    for key in ("dataset_contract", "semantic_contract"):
        value = attrs.get(key)
        if isinstance(value, dict) and value:
            return value
    return {}


def _resolve_guard_time_column(candidate_df: pd.DataFrame, intent: Any) -> str | None:
    """Eje temporal para ordenar la serie (contrato/schema, no por nombre)."""
    date_column = getattr(intent, "date_column", None)
    if isinstance(date_column, str) and date_column in candidate_df.columns:
        return date_column
    contract = _guard_contract(candidate_df)
    for key in ("time_axis", "ordinal_axis"):
        axis = str(contract.get(key) or "").strip()
        if axis and axis in candidate_df.columns:
            return axis
    profile = (getattr(candidate_df, "attrs", {}) or {}).get("schema_profile")
    if isinstance(profile, dict):
        for column in candidate_df.columns:
            if (profile.get(column) or {}).get("role") == "date":
                return column
    return None


def _ordered_metric_series(
    candidate_df: pd.DataFrame,
    time_col: str,
    metric: str,
    contract: dict[str, Any],
) -> Any:
    """Serie de la métrica ordenada cronológicamente por el eje disponible.

    Si el eje es el ordinal del contrato, ordena por el rango persistido en
    ``ordinal_axis_order`` (no alfabéticamente). Devuelve ``None`` si no se
    puede ordenar.
    """
    try:
        ordinal_axis = str(contract.get("ordinal_axis") or "").strip()
        ordinal_order = contract.get("ordinal_axis_order") or []
        if time_col == ordinal_axis and ordinal_order:
            rank = {str(value): index for index, value in enumerate(ordinal_order)}
            ordering = candidate_df[time_col].astype(str).map(rank)
            ordered_index = ordering.sort_values(kind="stable").index
            return candidate_df[metric].loc[ordered_index]
        return candidate_df[[time_col, metric]].sort_values(time_col)[metric]
    except Exception:
        return None


def _append_cumulative_disclosure(plan: Any) -> None:
    """Divulgación honesta de que la métrica es una serie acumulada."""
    title = getattr(plan, "title", None)
    if not isinstance(title, str) or not title:
        return
    suffix = "(serie acumulada · último valor)"
    if suffix in title:
        return
    try:
        plan.title = f"{title} {suffix}"
    except (ValueError, TypeError):
        pass


# Máximo de filas para evaluar monotonía sin comprometer el hot path.
_CUMULATIVE_GUARD_MAX_ROWS = 200_000


def _apply_cumulative_metric_guard(
    plans: list[Any] | None,
    candidate_df: pd.DataFrame | None,
    prompt: str | None = None,
) -> list[Any]:
    """[#4 2026-09] Corrige la agregación de métricas acumuladas (running totals).

    Una serie ya acumulada NO debe sumarse por período: re-contaría. Con DOS
    señales fail-closed (monotonía temporal AND léxico genérico de acumulación
    en nombre o prompt) y agregación efectiva 'sum', se cambia a 'max'/'min'
    (último valor del período) y se divulga en el título. Domain-agnostic:
    ninguna lista de nombres de negocio.

    Corre en el executor de producción → cubre TODAS las rutas (macro, simple,
    unified, caché). Solo actúa sobre 'sum'; una agregación explícita distinta
    (avg/min/max/count) se respeta.
    """
    if not plans:
        return []
    if candidate_df is None or candidate_df.empty:
        return plans
    if len(candidate_df) > _CUMULATIVE_GUARD_MAX_ROWS:
        return plans

    direction_cache: dict[str, str | None] = {}
    time_col_cache: dict[str, str | None] = {}
    contract = _guard_contract(candidate_df)
    metric_semantics = contract.get("metric_semantics") or {}

    for plan in plans:
        try:
            intent = getattr(plan, "main_intent", None)
            if intent is None:
                continue
            aggregation = str(getattr(intent, "aggregation", "") or "sum").strip().lower()
            if aggregation != "sum":
                continue
            metric = _intent_primary_metric(intent)
            if not metric or metric not in candidate_df.columns:
                continue
            if not has_accumulation_lexicon(metric, prompt):
                continue
            intent_key = f"{getattr(intent, 'type', '')}::{metric}"
            if metric not in direction_cache:
                declared = None
                entry = metric_semantics.get(metric)
                if isinstance(entry, dict):
                    declared = entry.get("cumulative_monotonic_direction")
                if declared in ("inc", "dec"):
                    direction_cache[metric] = declared
                else:
                    if intent_key not in time_col_cache:
                        time_col_cache[intent_key] = _resolve_guard_time_column(candidate_df, intent)
                    time_col = time_col_cache[intent_key]
                    if time_col is None or time_col not in candidate_df.columns:
                        direction_cache[metric] = None
                    else:
                        ordered = _ordered_metric_series(candidate_df, time_col, metric, contract)
                        direction_cache[metric] = monotonic_direction(ordered) if ordered is not None else None
            direction = direction_cache[metric]
            new_aggregation = cumulative_aggregation(direction)
            if not new_aggregation:
                # [Fase 0.2 2026-09] Degradación silenciosa: hay léxico de
                # acumulación pero no se pudo probar monotonía (sin eje). Se
                # registra para no ocultar un posible running total sin corregir.
                emit_structured_log(
                    "cumulative_detected_not_corrected",
                    level="warning",
                    metric=metric,
                    intent_type=getattr(intent, "type", None),
                    reason="no_monotonic_direction",
                )
                continue
            intent.aggregation = new_aggregation
            _append_cumulative_disclosure(plan)
            emit_structured_log(
                "semantic_cumulative_metric_guard",
                metric=metric,
                direction=direction,
                aggregation=new_aggregation,
                intent_type=getattr(intent, "type", None),
                title=getattr(plan, "title", None),
            )
        except Exception as _cumulative_err:
            print(f"⚠️ [CUMULATIVE GUARD] Error no-fatal: {_cumulative_err}")
    return plans


def _plan_has_valid_avg_on_numeric(plan: Any, candidate_df: pd.DataFrame | None) -> bool:
    """Verifica si el plan usa aggregation='avg' sobre columnas numéricas válidas.

    Si el LLM ya especificó correctamente un promedio sobre columnas numéricas
    reales, el sistema de corrección NO debe intervenir — inmunidad anti-regresión.

    [V9] TimeTrendIntent: Si value_column es una columna numérica válida, se
    inmuniza aunque no tenga campo de agregación explícito. La agregación
    la define el engine internamente.
    """
    if not plan or candidate_df is None or candidate_df.empty:
        return False
    intent = getattr(plan, "main_intent", None)
    if not intent:
        return False
    intent_type = getattr(intent, "type", None)

    if intent_type == "descriptive":
        if getattr(intent, "aggregation", None) == "avg":
            for m in (getattr(intent, "metrics", None) or []):
                if m in candidate_df.columns and pd.api.types.is_numeric_dtype(candidate_df[m]):
                    return True

    if intent_type == "diagnostic":
        if getattr(intent, "aggregation", None) == "avg":
            metrics_list = (getattr(intent, "metrics", None) or [])
            if getattr(intent, "metric", None) and getattr(intent, "metric", None) not in metrics_list:
                metrics_list = list(metrics_list) + [getattr(intent, "metric", None)]
            for m in metrics_list:
                if m and m in candidate_df.columns and pd.api.types.is_numeric_dtype(candidate_df[m]):
                    return True

    # [V9] TimeTrendIntent: inmunizar si value_column es numérica válida
    if intent_type == "trend":
        vc = getattr(intent, "value_column", None)
        if vc and vc in candidate_df.columns and pd.api.types.is_numeric_dtype(candidate_df[vc]):
            return True

    return False


def _is_plan_count_metric(plan: Any, metric: str) -> bool:
    """Verifica si una métrica es usada como count_metric legítima en el plan.

    El sistema de corrección automática reemplaza métricas alucinadas por
    columnas de tipo ID para conteo volumétrico (COUNT). Esta función detecta
    si una métrica bloqueada por el Metric Guard es en realidad un count_metric
    legítimo, otorgando inmunidad para evitar falsos bloqueos.

    [V2.2] Expansión a trend: TimeTrendIntent con value_column/plot_metric string
    (ej: id_empleado como conteo volumétrico en tendencia con split_dimension).
    """
    if not plan or not plan.main_intent:
        return False
    intent = plan.main_intent
    intent_type = getattr(intent, "type", None)

    # DescriptiveIntent: aggregation="count" con métrica en metrics[]
    if intent_type == "descriptive":
        return str(getattr(intent, "metric", "")) == metric

    # DistributionIntent: metric field — siempre es conteo de registros
    if intent_type == "distribution":
        return str(getattr(intent, "metric", "")) == metric

    # DiagnosticIntent: aggregation="count" con métrica en metrics o metric
    if intent_type == "diagnostic":
        if getattr(intent, "aggregation", None) == "count":
            if metric in (getattr(intent, "metrics", None) or []):
                return True
            if getattr(intent, "metric", None) == metric:
                return True

    # [FIX V2.2] Permitir conteos volumétricos en análisis de tendencias
    # [FIX V2.3] Añadir ranking_metric para cubrir id_empleado en plan de ranking+trend
    if intent_type == "trend":
        val_col = str(getattr(intent, "value_column", ""))
        plot_col = str(getattr(intent, "plot_metric", ""))
        rank_col = str(getattr(intent, "ranking_metric", ""))
        return metric in (val_col, plot_col, rank_col)

    return False


# ═══════════════════════════════════════════════════════════════════════════
# [FASE 3B MULTI-HOJA] Resolución determinista de frame_ids para JOIN
# ═══════════════════════════════════════════════════════════════════════════
# Opera sobre frame_ids (nombres de hoja), NO columnas. Es schema-agnostic
# y funciona con cualquier archivo porque solo mira identificadores
# estructurales.
#
# Normalización Unicode:
#   - NFKD decompose: "Logística" → "Logi\u0301stica"
#   - Strip combining marks (categoría 'M'): → "Logistica"
#   - .lower(): → "logistica"
#
# Esto garantiza que "logistica" en el prompt matchee "Logística" como hoja,
# y viceversa, sin depender de cómo escribió el usuario los acentos.
# ═══════════════════════════════════════════════════════════════════════════


def _normalize_text(text: str) -> str:
    """NFKD decompose → strip combining marks → lowercase.
    'Logística' → 'logistica', 'Dirección' → 'direccion'."""
    nfkd = unicodedata.normalize('NFKD', text)
    cleaned = ''.join(c for c in nfkd if not unicodedata.category(c).startswith('M'))
    return cleaned.lower()


def _extract_frame_tokens(frame_id: str) -> list[str]:
    """Extrae tokens significativos del nombre de hoja en un frame_id.

    'related__sheet::Logística' → ['logistica']
    'related__sheet::2023'     → ['2023']
    'primary__csv::main'       → ['main']
    'derived__A__B__join_preview' → [] (derivados no se matchean)
    """
    if frame_id.startswith("derived__"):
        return []
    source_id = frame_id.split('__', 1)[1] if '__' in frame_id else frame_id
    meaningful = source_id.split('::', 1)[1] if '::' in source_id else source_id
    tokens = []
    for part in re.split(r'[_\s\-]+', meaningful):
        normalized = _normalize_text(part.strip())
        if len(normalized) >= 2:
            tokens.append(normalized)
    return tokens


def _resolve_plan_frame_ids_deterministic(
    prompt: str,
    available_frame_ids: list[str],
) -> list[str]:
    """Fallback determinista: matchea prompt vs frame_id tokens con normalize de acentos.

    Si el prompt contiene '2023' → matchea frame_id 'related__sheet::2023'.
    Si el prompt contiene 'logistica' → matchea frame_id 'related__sheet::Logística'.
    Si el prompt es 'analiza ventas' y no hay hoja 'ventas' → retorna [] (0 JOINs).
    """
    if not available_frame_ids:
        return []
    normalized_prompt = _normalize_text(prompt)
    matched: list[str] = []
    for frame_id in available_frame_ids:
        for token in _extract_frame_tokens(frame_id):
            if re.search(rf'\b{re.escape(token)}\b', normalized_prompt):
                matched.append(frame_id)
                break
    return matched


def _resolve_frame_key(
    frame_id: str,
    available_keys: list[str],
) -> str | None:
    """Resuelve un frame_id corto ('sheet::2022') a su clave completa con prefijo
    ('related__sheet::2022' o 'primary__sheet::2022').

    El LLM emite IDs en formato corto ('sheet::2022') pero las claves en los
    diccionarios internos tienen prefijos ('primary__', 'related__', 'derived__').
    Esta función busca por sufijo para resolver el mismatch.
    """
    # 1. Match exacto
    if frame_id in available_keys:
        return frame_id
    # 2. Match por sufijo (prefijo + frame_id)
    suffix = f"__{frame_id}"
    for key in available_keys:
        if key == suffix or key.endswith(suffix):
            return key
    return None


def _resolve_primary_frame_from_plan(
    plan: Any,
    adapter_runtime: Any,
    candidate_df: pd.DataFrame | None,
    selected_candidate_id: str,
) -> tuple[pd.DataFrame | None, str]:
    """Re-selecciona el dataframe primario según el plan.

    Si el LLM pobló primary_frame_id con una hoja específica (ej: 'sheet::2022'),
    busca el DataFrame correspondiente en candidate_dataframes y lo retorna.
    Esto garantiza que el motor Ibis arranque desde la hoja correcta (raw sheet)
    en lugar de una vista derived__ pre-join que puede arrastrar datos espurios.

    Si primary_frame_id no está seteado o no se encuentra, retorna los valores
    actuales (0 regresión para consultas mono-hoja).
    """
    plan_primary_id = getattr(plan, "primary_frame_id", None) or ""
    if not plan_primary_id or plan_primary_id == "primary":
        return candidate_df, selected_candidate_id

    candidate_dataframes = getattr(adapter_runtime, "candidate_dataframes", {}) or {}
    available_keys = list(candidate_dataframes.keys())
    resolved_key = _resolve_frame_key(plan_primary_id, available_keys)
    if resolved_key and resolved_key in candidate_dataframes:
        new_df = candidate_dataframes[resolved_key]
        if new_df is not None:
            emit_structured_log(
                "primary_frame_re_selected",
                original=selected_candidate_id,
                resolved=resolved_key,
                plan_primary=plan_primary_id,
            )
            return new_df, resolved_key

    return candidate_df, selected_candidate_id


@dataclass
class CanonicalTabularProductionExecutionResult:
    status: str
    final_struct: dict[str, Any]
    dataset_contract: dict[str, Any]
    cleaning_notes: Any
    execution: CanonicalShadowQueryExecution


def _build_readiness_summary(pipeline_result: Any) -> dict[str, Any]:
    return build_shadow_format_readiness_summary(
        file_name=str(pipeline_result.canonical_bundle_summary.get("file_name") or ""),
        pipeline_summary={
            "pipeline_status": pipeline_result.metadata.get("pipeline_status"),
        },
        bundle_summary=pipeline_result.canonical_bundle_summary,
        materialized_summary=pipeline_result.materialized_bundle_summary,
        preview_summary=pipeline_result.preview_runtime_summary,
        analytical_summary=pipeline_result.analytical_adapter_summary,
        runtime_comparison_summary=pipeline_result.runtime_comparison_summary,
    )


def _selected_candidate_id(pipeline_result: Any) -> str:
    analytical_bundle = getattr(
        getattr(pipeline_result, "analytical_adapter_runtime", None),
        "analytical_bundle",
        None,
    )
    return str(getattr(analytical_bundle, "selected_candidate_id", "") or "").strip()


def _should_skip_related_parquets(selected_candidate_id: Any) -> bool:
    """Indica si deben omitirse los parquets de related_frames.

    [PARQUET-OPT 2026-09] Cuando el candidato seleccionado es la vista
    unified_all__ (toda la data ya consolidada), el engine nunca consulta los
    parquets de related_frames (el bloque de abajo limpia related_paths). Por
    tanto, escribirlos es I/O muerto: ~9 archivos y ~25s de worker.

    Args:
        selected_candidate_id: ID del candidato analítico seleccionado.

    Returns:
        True si el candidato es unified_all__ (omitir related frames).
    """
    return bool(
        selected_candidate_id and "unified_all__" in str(selected_candidate_id)
    )


def build_canonical_tabular_production_execution(
    *,
    file_id: str,
    pipeline_result: Any,
    prompt: str | None = None,
    service_client: Any | None = None,
    max_plans: int = 3,
    prompt_type: str | None = None,
) -> CanonicalShadowQueryExecution:
    """Execute the user-facing tabular path without Canary/Shadow strategy bundles.

    The production executor keeps the canonical extraction/contract layer, then
    sends the prompt directly to SemanticTranslator and Ibis. Shadow visual
    parity bundles are intentionally absent from this path.
    """
    readiness_summary = _build_readiness_summary(pipeline_result)
    candidate_df = get_selected_candidate_dataframe(pipeline_result.analytical_adapter_runtime)
    selected_candidate_id = _selected_candidate_id(pipeline_result)

    if candidate_df is None:
        return CanonicalShadowQueryExecution(
            pipeline_result=pipeline_result,
            readiness_summary=readiness_summary,
            query_prompt=None,
            prompt_strategy=None,
            plans=[],
            plan_summaries=[],
            execution_summaries=[],
            execution_results=[],
            metadata={
                "file_id": file_id,
                "candidate_id": None,
                "shadow_query_status": "no_candidate",
                "production_query_status": "no_candidate",
            },
        )

    actual_prompt, parent_task_id = unwrap_prompt_payload(prompt)
    schema_profile = dict((getattr(candidate_df, "attrs", {}) or {}).get("schema_profile", {}) or {})
    dataset_contract = dict((getattr(candidate_df, "attrs", {}) or {}).get("semantic_contract", {}) or {})

    # [FASE 2 MULTI-HOJA] Construir contexto de frames relacionados
    related_frames_context = ""
    adapter_runtime = pipeline_result.analytical_adapter_runtime
    if hasattr(adapter_runtime, "related_frame_ids") and adapter_runtime.related_frame_ids:
        frame_ids = adapter_runtime.related_frame_ids
        frame_relations = getattr(adapter_runtime, "frame_relations", []) or []
        related_frames_context = f"- HOJAS RELACIONADAS ({len(frame_ids)}): {frame_ids}\n"
        if frame_relations:
            joined_keys = []
            for rel in frame_relations:
                if isinstance(rel, dict) and rel.get("join_keys"):
                    joined_keys.extend(rel["join_keys"])
            if joined_keys:
                related_frames_context += (
                    f"        Claves de JOIN detectadas: {list(set(joined_keys))}\n"
                    f"        Puedes usar columnas de las hojas relacionadas con la clave común.\n"
                )

    parent_context = load_parent_analysis_context(
        service_client=service_client,
        parent_task_id=parent_task_id,
        file_id=file_id,
        columns=list(candidate_df.columns),
    )
    # [F2-D3] Activar la capa de memoria de sesión en producción:
    # continuity gate + bypass de pedidos autocontenidos + intent instruction.
    memory_context_text = build_parent_memory_context_text(parent_context)
    memory_instruction = ""
    if memory_context_text:
        previous_prompt = str((parent_context or {}).get("parent_prompt") or "")
        keep_memory = (
            SemanticTranslator.evaluate_continuity(actual_prompt, previous_prompt)
            if previous_prompt
            else True
        )
        if not keep_memory:
            emit_structured_log(
                "analysis_memory_continuity_dropped",
                level="warning",
                file_id=file_id,
                reason="topic_shift",
            )
            memory_context_text = ""
        elif SemanticTranslator.should_bypass_memory_context(
            actual_prompt, list(candidate_df.columns), schema_profile
        ):
            emit_structured_log(
                "analysis_memory_bypassed_self_contained",
                file_id=file_id,
            )
            memory_context_text = ""
        else:
            memory_instruction = SemanticTranslator._classify_memory_intent(
                actual_prompt,
                memory_context_text,
                _extract_previous_dimensions(parent_context),
            )
    plans = SemanticTranslator.translate(
        actual_prompt,
        list(candidate_df.columns),
        _build_glossary_context(candidate_df),
        _build_topology_context(candidate_df),
        memory_context=memory_context_text,
        memory_instruction=memory_instruction,
        schema_profile=schema_profile,
        dataset_contract=dataset_contract,
        related_frames_context=related_frames_context,
        candidate_df=candidate_df,
    ) or []
    # [F2-D5] Alineación métrica-semántica en el path canónico (antes solo en legacy).
    # Corrige la métrica elegida según la preferencia de unidad del prompt (mismo
    # contrato de firma; no cambia planes aditivos por defecto). Degradación segura:
    # una falla aquí nunca debe tumbar el análisis.
    try:
        plans = align_plan_metrics_with_prompt(plans, actual_prompt, schema_profile) or plans
    except Exception as exc:
        emit_structured_log(
            "analysis_metric_alignment_error",
            level="warning",
            file_id=file_id,
            error=str(exc)[:160],
        )
    plans = apply_parent_context_to_placeholder_filters(
        plans=plans,
        parent_context=parent_context,
    )

    # [P1 2026-09] Ventana temporal relativa determinista (próximos/últimos N).
    # El LLM puede emitir solo una cota; aquí se garantiza la ventana completa
    # anclada al reference_date del dataset. Degradación segura.
    try:
        plans = apply_relative_time_windows(
            plans,
            actual_prompt,
            reference_date=(getattr(candidate_df, "attrs", {}) or {}).get("reference_date"),
            schema_profile=schema_profile,
            time_axis=str(dataset_contract.get("time_axis") or "") or None,
        ) or plans
    except Exception as exc:
        emit_structured_log(
            "analysis_relative_window_error",
            level="warning",
            file_id=file_id,
            error=str(exc)[:160],
        )

    # [MEJORA 4 2026-09] Intención determinista de vencimientos: cuando el prompt
    # pide "productos a vencer", construimos el bundle sin depender del LLM
    # (columna de vencimiento + ventana + corte actual). Degradación segura:
    # si no aplica o no se puede resolver la ventana, se conservan los planes.
    try:
        expiry_plans = build_expiry_analysis_bundle(
            prompt=actual_prompt,
            columns=list(candidate_df.columns),
            schema_profile=schema_profile,
            dataset_contract=dataset_contract,
            candidate_df=candidate_df,
            reference_date=(getattr(candidate_df, "attrs", {}) or {}).get("reference_date"),
            prompt_type=prompt_type,
            max_plans=max_plans,
        )
        if expiry_plans:
            # [Fase 1.4 2026-09] El bundle determinista pasa por el choke point
            # de planes: visual advisory (combo → serie derivada) + invariantes.
            plans = finalize_plans(
                expiry_plans,
                schema_profile,
                dataset_contract=dataset_contract,
                prompt=actual_prompt,
            )
            emit_structured_log(
                "expiry_deterministic_bundle_applied",
                file_id=file_id,
                plan_count=len(plans),
            )
    except Exception as exc:
        emit_structured_log(
            "expiry_bundle_error",
            level="warning",
            file_id=file_id,
            error=str(exc)[:160],
        )

    # [F2-D2] Constraint guard (observabilidad): reporta restricciones del prompt
    # que el plan no satisfizo (temporal+top_n, rollup, split negado). No bloquea.
    try:
        unresolved_constraints = SemanticTranslator._fast_path_unresolved_constraints(
            actual_prompt, plans
        )
        if unresolved_constraints:
            emit_structured_log(
                "analysis_constraints_unresolved",
                level="warning",
                file_id=file_id,
                constraints=unresolved_constraints,
            )
    except Exception as exc:
        emit_structured_log(
            "analysis_constraints_guard_error",
            level="warning",
            file_id=file_id,
            error=str(exc)[:160],
        )

    # [F2-D1] Contrato analítico (observabilidad, no bloqueante). Computa el estado
    # (valid/blocked/clarification_required) para trazabilidad sin alterar el pipeline.
    try:
        _intent_type = (
            str(getattr(plans[0].main_intent, "type", "unknown")) if plans else "unknown"
        )
        analytical_contract = SemanticTranslator._build_query_analytical_contract(
            query_id=f"{file_id}:{len(plans)}",
            file_id=file_id,
            intent_type=_intent_type,
            plans=plans,
            columns=list(candidate_df.columns),
            schema_profile=schema_profile,
        )
        emit_structured_log(
            "analysis_contract_evaluated",
            file_id=file_id,
            contract_state=getattr(analytical_contract, "state", "unknown"),
            block_reason=getattr(analytical_contract, "block_reason", None),
        )
    except Exception as exc:
        emit_structured_log(
            "analysis_contract_error",
            level="warning",
            file_id=file_id,
            error=str(exc)[:160],
        )

    # ═══════════════════════════════════════════════════════════════════════════
    # [UNIFIED DATA MODEL] Short-circuit ANTES de FASE 3B/3C/3D
    # ═══════════════════════════════════════════════════════════════════════════
    # Cuando el candidato seleccionado es la vista unificada, toda la data ya
    # está consolidada: no hay JOIN cross-sheet ni pre-agregación transaccional.
    # Limpiar related_frame_ids/join_keys/pre_aggregation AQUÍ (y no solo en el
    # bloque tardío de ~1050) evita que la FASE 3D dispare una pre-agregación
    # automática que colapse el schema a [join_key, ...métricas] y rompa las
    # dimensiones/fechas del plan.
    # Incidente 2026-09-23: "semana 30 del 2021" matcheó tokens de frame_id
    # ('30','2021') → related_frame_ids espurios → pre-agregación → query_failed.
    # Schema-agnostic: solo mira el id del candidato, no columnas ni nombres.
    # ═══════════════════════════════════════════════════════════════════════════
    _is_unified_candidate = bool(
        selected_candidate_id and "unified_all__" in str(selected_candidate_id)
    )
    if _is_unified_candidate and plans:
        for plan in plans:
            plan.related_frame_ids = []
            plan.join_keys = []
            plan.pre_aggregation = None

    # ═══════════════════════════════════════════════════════════════════════════
    # [FASE 3B MULTI-HOJA] Resolver frame_ids para JOINs deterministas
    # ═══════════════════════════════════════════════════════════════════════════
    # Capa 2: Si el LLM pobló related_frame_ids (Capa 1), se respeta.
    # Capa 2 fallback: Si está vacío, matchea tokens del prompt vs frame_ids
    # con normalización de acentos (NFKD).
    # Schema-agnostic: solo mira identificadores de frames, no columnas.
    # ═══════════════════════════════════════════════════════════════════════════
    _available_frame_ids: list[str] = []
    if hasattr(adapter_runtime, "related_frame_ids") and adapter_runtime.related_frame_ids:
        _available_frame_ids = list(adapter_runtime.related_frame_ids)
    if plans and actual_prompt and not _is_unified_candidate:
        for plan in plans:
            if not getattr(plan, "related_frame_ids", None):
                plan.related_frame_ids = _resolve_plan_frame_ids_deterministic(
                    str(actual_prompt), _available_frame_ids,
                )

    # ═══════════════════════════════════════════════════════════════════════════
    # [FASE 3C MULTI-HOJA] Herencia determinista de join_keys del orchestrator
    # ═══════════════════════════════════════════════════════════════════════════
    # Capa 2: Si el LLM pobló join_keys (Capa 1), se respeta.
    # Capa 2 fallback: Si está vacío, heredar del orchestrator que YA calculó
    # las llaves con value overlap ratio durante la fase de canonical_bundle.
    # Garantía: si el orchestrator detectó "placa_unidad" con 95% overlap,
    # el plan la recibe aunque el LLM no la haya puesto en el JSON.
    # ═══════════════════════════════════════════════════════════════════════════
    _frame_relations = getattr(adapter_runtime, "frame_relations", []) or []
    if plans and _frame_relations and not _is_unified_candidate:
        for plan in plans:
            # Solo heredar join_keys si el plan realmente va a hacer JOIN
            if not getattr(plan, "related_frame_ids", None):
                continue
            _plan_join_keys = getattr(plan, "join_keys", None)
            if not _plan_join_keys:
                for rel in _frame_relations:
                    if isinstance(rel, dict) and rel.get("join_keys"):
                        plan.join_keys = list(rel["join_keys"])
                        break

    # ═══════════════════════════════════════════════════════════════════════════
    # [FASE 3D MULTI-HOJA] Detector de cardinalidad — Pre-agregación automática
    # ═══════════════════════════════════════════════════════════════════════════
    # Capa 2 fallback determinista: Si el LLM no especificó pre_aggregation,
    # el sistema calcula la cardinalidad de las join_keys contra el dataset real.
    # Si el ratio filas/entidad supera el umbral, los datos son transaccionales
    # (múltiples filas por entidad) y se activa pre-agregación automática.
    # Esta es la última línea de defensa que garantiza que ningún JOIN
    # transaccional escape sin consolidación previa.
    # ═══════════════════════════════════════════════════════════════════════════
    if plans and candidate_df is not None and not candidate_df.empty and not _is_unified_candidate:
        for plan in plans:
            # Solo detectar cardinalidad en planes cross-sheet (necesitan JOIN)
            if not getattr(plan, "related_frame_ids", None):
                continue
            if getattr(plan, "pre_aggregation", None) is not None:
                continue
            _plan_join_keys = getattr(plan, "join_keys", []) or []
            if not _plan_join_keys:
                continue

            join_key = _plan_join_keys[0]
            if join_key not in candidate_df.columns:
                continue

            if pd.api.types.is_numeric_dtype(candidate_df[join_key]):
                continue

            total_rows = len(candidate_df)
            distinct_values = candidate_df[join_key].nunique()
            if distinct_values == 0:
                continue

            entity_ratio = total_rows / distinct_values

            _TRANSACTIONAL_THRESHOLD = 10
            if entity_ratio > _TRANSACTIONAL_THRESHOLD:
                numeric_cols = [
                    col for col in candidate_df.columns
                    if col != join_key
                    and pd.api.types.is_numeric_dtype(candidate_df[col])
                    and not col.lower().startswith(("id_", "cod_", "dni_", "ruc_", "sku_"))
                ]
                metrics = numeric_cols[:5]
                if not metrics:
                    continue

                print(f"🧠 [FASE 3D] Cardinalidad detectada: {total_rows} filas / "
                      f"{distinct_values} valores únicos de '{join_key}' = "
                      f"{entity_ratio:.1f} filas por entidad "
                      f"(umbral: {_TRANSACTIONAL_THRESHOLD})")
                print(f"🧠 [FASE 3D] Pre-agregación automática ACTIVADA: "
                      f"GROUP BY [{join_key}], SUM({metrics[:3]})...")

                plan.pre_aggregation = PreAggregationSpec(
                    group_by=[join_key],
                    metrics=metrics,
                    aggregation="sum",
                )

    # ═══════════════════════════════════════════════════════════════════════════
    # [V3] LITERAL FILTER INDEXER — Corrección de filtros contra el dataset real
    # ═══════════════════════════════════════════════════════════════════════════
    # El LLM puede emitir valores de filtro con variaciones lingüísticas (ej.
    # "egresos" en plural cuando el dato real es "Egreso" en singular).
    # Este indexer detecta esas discrepancias usando el Fuzzy-Form Matching
    # de SemanticTranslator y reemplaza el valor del filtro ANTES de que Ibis
    # ejecute la query. Es schema-agnostic: funciona con cualquier archivo.
    # ═══════════════════════════════════════════════════════════════════════════
    _literal_filter_catalog: dict[str, list[str]] = dict(
        (getattr(candidate_df, "attrs", {}) or {}).get("literal_filter_catalog", {}) or {}
    )
    if _literal_filter_catalog and plans and actual_prompt:
        try:
            _detected_literals = SemanticTranslator._detect_literal_filters(
                str(actual_prompt), _literal_filter_catalog
            )
            if _detected_literals:
                _SUPPORTED_IBIS_OPS = {
                    "==", "!=", "in", "not_in", "contains",
                    "ilike", "like", "starts_with", "ends_with",
                    "not_contains", "not_like", ">", "<", ">=", "<=",
                }
                for plan in plans:
                    intent_filters = list(getattr(plan.main_intent, "filters", []) or [])
                    for lf in _detected_literals:
                        # Buscar si Gemini ya emitió un filtro para esta columna
                        gemini_match = next(
                            (f for f in intent_filters if f.column == lf.column),
                            None,
                        )
                        if gemini_match is None:
                            # [V5] TOKEN BOUNDARY GUARD: Solo inyectar si el valor
                            # aparece como palabra completa en el prompt Y tiene ≥4 chars.
                            # Previene falsos positivos como 'ENT' matcheando en 'vENcimiento'.
                            # [V9] Tolerancia morfológica: sufijo plural 's' opcional
                            # para que "inactivos" en el prompt matchee "Inactivo" del catálogo.
                            _lf_val_str = str(lf.value).strip()
                            if len(_lf_val_str) < 4:
                                print(
                                    f"⚠️ [LITERAL FILTER → BLOCKED] "
                                    f"Token '{_lf_val_str}' demasiado corto (<4 chars). "
                                    f"Posible falso positivo."
                                )
                                continue
                            if not re.search(
                                rf'\b{re.escape(_lf_val_str)}s?\b',
                                str(actual_prompt),
                                re.IGNORECASE,
                            ):
                                print(
                                    f"⚠️ [LITERAL FILTER → BLOCKED] "
                                    f"'{_lf_val_str}' no es token completo en el prompt. "
                                    f"Posible substring match o variante morfológica."
                                )
                                continue
                            # Columna no filtrada por Gemini → inyectar filtro literal
                            intent_filters.append(lf)
                            print(
                                f"🔄 [LITERAL FILTER → INJECT] "
                                f"Nuevo filtro: {lf.column} {getattr(lf.operator, 'value', lf.operator)} {lf.value}"
                            )
                        else:
                            # Columna ya filtrada por Gemini → verificar compatibilidad
                            gemini_op = str(
                                getattr(gemini_match.operator, "value", gemini_match.operator) or ""
                            ).strip()
                            if gemini_op not in _SUPPORTED_IBIS_OPS:
                                # Operador no soportado → reemplazar con filtro literal
                                intent_filters.remove(gemini_match)
                                intent_filters.append(lf)
                                print(
                                    f"🔄 [LITERAL FILTER → REPLACE] "
                                    f"Operador '{gemini_op}' no soportado. "
                                    f"Reemplazado: {lf.column} {getattr(lf.operator, 'value', lf.operator)} {lf.value}"
                                )
                            elif gemini_op in {"in", "not_in"} and isinstance(gemini_match.value, list):
                                # [V4] GUARD: Filtro multi-valor (IN/NOT_IN con lista) del LLM
                                # es una decisión analítica superior. NUNCA degradar a ==.
                                # Ej: tipo_almacen IN ["130","400"] → NO reemplazar por == "130"
                                print(
                                    f"✅ [LITERAL FILTER → SKIP] Filtro multi-valor preservado: "
                                    f"{gemini_match.column} {gemini_op} {gemini_match.value}"
                                )
                            elif str(gemini_match.value).upper() != str(lf.value).upper():
                                # Valor difiere (ej. 'egresos' vs 'Egreso') → reemplazar
                                intent_filters.remove(gemini_match)
                                intent_filters.append(lf)
                                print(
                                    f"🔄 [LITERAL FILTER → REPLACE] "
                                    f"Valor corregido: '{gemini_match.value}' → {lf.value} "
                                    f"en columna '{lf.column}'"
                                )
                    plan.main_intent.filters = intent_filters
        except Exception as _lf_err:
            # El indexer nunca debe bloquear la ejecución — es best-effort
            print(f"⚠️ [LITERAL FILTER] Error no-fatal en indexer canónico: {_lf_err}")

    # ═══════════════════════════════════════════════════════════════════════════
    # [V4] METRIC HALLUCINATION GUARD — Auto-corrección de métricas alucinadas
    # ═══════════════════════════════════════════════════════════════════════════
    # Gemini puede inventar métricas que no existen en el dataset (ej:
    # "tasa_rotacion", "conteo_inactivos"). Este interceptor detecta esas
    # alucinaciones y las corrige ANTES de que el Metric Guard bloquee el plan.
    # ═══════════════════════════════════════════════════════════════════════════
    plans = _auto_correct_hallucinated_metrics(plans, candidate_df, actual_prompt)

    # [#4 2026-09] Guard de métricas acumuladas: corrige sum→max/min cuando la
    # serie es acumulada (dos señales fail-closed). Cubre todas las rutas.
    plans = _apply_cumulative_metric_guard(plans, candidate_df, actual_prompt)

    bounded_plans = list(plans[: max(int(max_plans or 0), 1)])
    plan_summaries = [_summarize_plan(plan, index + 1) for index, plan in enumerate(bounded_plans)]

    # ═══════════════════════════════════════════════════════════════════════════
    # [Fase 3B MULTI-HOJA] Re-seleccionar dataframe primario según el plan
    # ═══════════════════════════════════════════════════════════════════════════
    # Si el LLM pobló primary_frame_id con una hoja específica (ej: "sheet::2022"),
    # re-seleccionamos el DataFrame correspondiente. Esto evita que el análisis
    # arranque desde una vista derived__ pre-join que arrastra datos de años no
    # solicitados, garantizando pureza en los JOINs deterministas.
    # ═══════════════════════════════════════════════════════════════════════════
    if bounded_plans:
        primary_plan = bounded_plans[0]
        candidate_df, selected_candidate_id = _resolve_primary_frame_from_plan(
            primary_plan, adapter_runtime, candidate_df, selected_candidate_id,
        )
        # Recalcular schema_profile y dataset_contract para el nuevo primary
        if candidate_df is not None and not candidate_df.empty:
            schema_profile = dict(
                (getattr(candidate_df, "attrs", {}) or {}).get("schema_profile", {}) or {}
            )

    _skip_related_parquets = _should_skip_related_parquets(selected_candidate_id)
    shadow_file_id, parquet_path, related_paths = _persist_shadow_candidate(
        candidate_df,
        file_id=file_id,
        candidate_id=selected_candidate_id,
        related_frames=None if _skip_related_parquets else (
            get_related_frames(pipeline_result.analytical_adapter_runtime)
            if hasattr(pipeline_result, "analytical_adapter_runtime")
            and hasattr(pipeline_result.analytical_adapter_runtime, "related_frame_ids") else None
        ),
    )
    # [FASE 3B MULTI-HOJA] Excluir vistas derivadas (JOINs pre-calculados del materializador).
    # El engine Ibis hace sus propios JOINs desde hojas raw — las vistas derived__
    # son pre-merged y no deben re-joinarse (causarían columnas duplicadas).
    if related_paths:
        related_paths = {
            k: v for k, v in related_paths.items()
            if not k.startswith("derived__")
        }

    # ── UNIFIED DATA MODEL: Skip JOIN logic ────────────────────
    # When the selected candidate is the unified_all__ view,
    # all cross-sheet data is already consolidated. Clear
    # related_frame_ids on all plans so the JOIN section in
    # ibis_engine is skipped entirely.
    # ───────────────────────────────────────────────────────────
    if selected_candidate_id and "unified_all__" in str(selected_candidate_id):
        related_paths = {}
        for plan in (bounded_plans or []):
            plan.related_frame_ids = []

    execution_summaries: list[dict[str, Any]] = []
    execution_results: list[dict[str, Any]] = []
    if parquet_path:
        protected_cols = _protected_columns(candidate_df)
        ibis_engine_cls = _get_ibis_engine_cls()
        for index, plan in enumerate(bounded_plans, start=1):
            blocked_metrics = _blocked_plan_metrics(plan, candidate_df)
            # 🛡️ [V9] COUNT METRIC IMMUNITY: métricas de ID usadas como count
            # no son alucinaciones — el sistema de corrección las reemplazó
            # legítimamente para conteo volumétrico de registros.
            # Se verifica que el plan use aggregation="count" o sea un
            # DistributionIntent (que siempre cuenta registros).
            _immunized = [
                m for m in (blocked_metrics or [])
                if _is_plan_count_metric(plan, m)
            ]
            if _immunized:
                for m in _immunized:
                    blocked_metrics.remove(m)
                    print(
                        f"🛡️ [METRIC GUARD → IMMUNIZED] "
                        f"'{m}' es count_metric del plan — inmunizada."
                    )
            if blocked_metrics:
                blocked_result = _blocked_execution_result(
                    plan,
                    index=index,
                    error=(
                        "Production Metric Guard bloqueó el plan: las métricas "
                        f"{blocked_metrics} no existen como columnas en el dataset. "
                        f"Columnas disponibles: "
                        f"{sorted(list(candidate_df.columns))}. "
                        "Usa solo columnas existentes en el plan — "
                        "no se permiten métricas derivadas."
                    ),
                    blocked_metrics=blocked_metrics,
                )
                execution_summaries.append(blocked_result)
                execution_results.append(dict(blocked_result))
                continue
            result = ibis_engine_cls.execute_plan(
                parquet_path,
                plan,
                protected_cols=protected_cols,
                recipe_mode=True,
                related_parquets=related_paths if related_paths else None,
            )
            execution_summaries.append(_summarize_execution_result(plan, result, index))
            execution_results.append(dict(result) if isinstance(result, dict) else {"error": "invalid_execution_result"})

    success_count = sum(1 for row in execution_summaries if row.get("status") == "success")
    production_query_status = (
        "query_executed"
        if execution_summaries and success_count == len(execution_summaries)
        else "partial_query_success"
        if execution_summaries and success_count > 0
        else "query_failed"
        if bounded_plans
        else "no_plans"
    )

    emit_structured_log(
        "canonical_tabular_production_query_executed",
        file_id=file_id,
        candidate_id=selected_candidate_id,
        readiness_grade=readiness_summary.get("readiness_grade"),
        prompt_strategy="production_semantic_translator",
        plan_count=len(bounded_plans),
        success_count=success_count,
        production_query_status=production_query_status,
    )

    return CanonicalShadowQueryExecution(
        pipeline_result=pipeline_result,
        readiness_summary=readiness_summary,
        query_prompt=actual_prompt,
        prompt_strategy="production_semantic_translator",
        plans=bounded_plans,
        plan_summaries=plan_summaries,
        execution_summaries=execution_summaries,
        execution_results=execution_results,
        metadata={
            "file_id": file_id,
            "candidate_id": selected_candidate_id,
            "shadow_file_id": shadow_file_id,
            "shadow_parquet_path": parquet_path,
            "shadow_query_status": production_query_status,
            "production_query_status": production_query_status,
            "parent_task_id": parent_task_id,
            "parent_context_filter_count": len(list((parent_context or {}).get("filters") or [])),
        },
    )


def execute_canonical_tabular_production_analysis(
    *,
    file_id: str,
    prompt: str | None,
    service_client: Any,
    uploaded_file_row: dict[str, Any] | None = None,
    mime_type: str | None = None,
    max_plans: int = 3,
    prompt_type: str | None = None,
) -> CanonicalTabularProductionExecutionResult:
    pipeline_result = run_canonical_dark_pipeline_for_uploaded_file(
        file_id=file_id,
        service_client=service_client,
        uploaded_file_row=uploaded_file_row,
        mime_type=mime_type,
    )
    execution = build_canonical_tabular_production_execution(
        file_id=file_id,
        pipeline_result=pipeline_result,
        prompt=prompt,
        service_client=service_client,
        max_plans=max_plans,
        prompt_type=prompt_type,
    )
    successful_count = sum(1 for row in execution.execution_summaries if row.get("status") == "success")
    # [V3] Extraer tipo de error dominante para que el Big Data Shield
    # pueda distinguir errores lógicos (empty_result) de errores reales.
    _dominant_error = next(
        (str(row.get("error") or "") for row in execution.execution_summaries if row.get("error")),
        "",
    )
    # [V3] Relajar la puerta: aceptar partial_query_success si hay ≥1 plan exitoso.
    # _build_final_struct() ya filtra resultados con error (línea 361-362),
    # así que solo los gráficos buenos llegan al frontend.
    if successful_count <= 0:
        _pq_status = execution.metadata.get('production_query_status', '')
        # ═══════════════════════════════════════════════════════════════
        # Fix 4: Mensaje descriptivo cuando no hay planes válidos.
        # En lugar de un error crudo, informar qué columnas SÍ tiene
        # el dataset para que el usuario entienda el mismatch.
        # ═══════════════════════════════════════════════════════════════
        _col_hint = ""
        if _pq_status == "no_plans":
            try:
                _hint_df = get_selected_candidate_dataframe(
                    pipeline_result.analytical_adapter_runtime
                )
                if _hint_df is not None:
                    _available = sorted(
                        c for c in _hint_df.columns if not c.startswith('_')
                    )
                    if _available:
                        _col_hint = (
                            f" Columnas disponibles en tu dataset: "
                            f"{', '.join(_available[:15])}"
                        )
            except Exception:
                pass  # best-effort hint, never blocks error path
        raise RuntimeError(
            f"canonical_production_not_ready:{_pq_status}:{successful_count}:{_dominant_error}{_col_hint}"
        )
    final_struct, dataset_contract, cleaning_notes = _build_final_struct(execution)
    final_struct.setdefault("traceability", {})
    final_struct["traceability"]["runtime"] = "canonical_tabular_production"
    final_struct["traceability"]["prompt_strategy"] = execution.prompt_strategy
    plans = execution.plans
    if plans and plans[0] and hasattr(plans[0], 'coverage_metadata') and plans[0].coverage_metadata:
        semantic_context = final_struct["traceability"].setdefault("semantic_context", {})
        semantic_context["metric_coverage"] = plans[0].coverage_metadata
    return CanonicalTabularProductionExecutionResult(
        status="completed",
        final_struct=final_struct,
        dataset_contract=dataset_contract,
        cleaning_notes=cleaning_notes,
        execution=execution,
    )
