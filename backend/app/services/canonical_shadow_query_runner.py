from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Any

import pandas as pd

from app.core.config import settings
from app.core.semantic_grammar import (
    AnalysisPlan,
    DataFilter,
    DescriptiveIntent,
    DistributionIntent,
    FilterOperator,
    MetricPolarity,
    MetricUnit,
    PredictiveIntent,
    TimeGrain,
    TimeTrendIntent,
    VisualProtocol,
)
from app.core.structured_logging import emit_structured_log
from app.services.analysis_memory_context import (
    apply_parent_context_to_placeholder_filters,
    build_parent_memory_context_text,
    load_parent_analysis_context,
    unwrap_prompt_payload,
)
from app.services.canonical_analytical_contract_adapter import get_selected_candidate_dataframe
from app.services.canonical_dark_runtime_orchestrator import (
    CanonicalDarkRuntimePipelineResult,
    run_canonical_dark_pipeline_for_uploaded_file,
)
from app.services.canonical_header_normalizer import fold_header_text
from app.services.canonical_shadow_format_comparator import build_shadow_format_readiness_summary
from app.services.data_engine import DataEngine
from app.services.metric_semantics import infer_metric_unit_from_column_name
from app.services.semantic_translator import SemanticTranslator
from app.services.visual_recommendation_engine import extract_prompt_visual_requests, normalize_visual_id


def is_canonical_shadow_query_runtime_enabled() -> bool:
    return settings.CANONICAL_SHADOW_QUERY_RUNTIME_ENABLED


@dataclass
class CanonicalShadowQueryExecution:
    pipeline_result: CanonicalDarkRuntimePipelineResult
    readiness_summary: dict[str, Any]
    query_prompt: str | None
    prompt_strategy: str | None
    plans: list[AnalysisPlan]
    plan_summaries: list[dict[str, Any]]
    execution_summaries: list[dict[str, Any]]
    execution_results: list[dict[str, Any]]
    metadata: dict[str, Any]


def _slugify(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")
    return normalized or "candidate"


def _humanize_label(value: str) -> str:
    text = fold_header_text(value).strip()
    if not text:
        return str(value or "").strip()
    return re.sub(r"\s+", " ", text.replace("_", " ")).strip().title()


def _list_str(values: list[Any] | None) -> list[str]:
    return [str(value) for value in list(values or []) if str(value or "").strip()]


def _schema_metric_candidates(schema_profile: dict[str, Any]) -> list[str]:
    numeric_metrics: list[str] = []
    fallback_metrics: list[str] = []
    for column_name, info in schema_profile.items():
        if not isinstance(info, dict):
            continue
        if str(info.get("role") or "").strip().lower() != "metric":
            continue
        fallback_metrics.append(str(column_name))
        if str(info.get("type") or "").strip().lower() == "numeric":
            numeric_metrics.append(str(column_name))
    return numeric_metrics or fallback_metrics


def _prompt_metric_affinity(metric_column: str, prompt: str | None) -> float:
    """Score how well a metric column name matches user prompt terms (0.0 – 1.0).

    Uses token overlap between the normalized column name and the prompt to
    prioritize the metric the user explicitly mentioned. This is a lightweight
    heuristic — not NLP — and only reorders candidates, never invents new ones.
    """
    if not prompt:
        return 0.0
    prompt_lower = str(prompt).lower()
    # Normalize column name: "salario_mensual" → ["salario", "mensual"]
    col_tokens = [t for t in re.sub(r"[^a-záéíóúñü0-9]+", " ", metric_column.lower()).split() if len(t) > 2]
    if not col_tokens:
        return 0.0
    matched = sum(1 for token in col_tokens if token in prompt_lower)
    return matched / len(col_tokens)


def _shadow_safe_metric_columns(candidate_df: pd.DataFrame) -> list[str]:
    """Return metric columns verified as safe for aggregation (sum/avg).

    V6.5: Added DataFrame-level dtype guard.  If schema_profile returns columns
    tagged as 'metric' but their actual pandas dtype is non-numeric (object/string),
    they are excluded to prevent 'StringColumn has no attribute sum' in Ibis.
    """
    attrs = getattr(candidate_df, "attrs", {}) or {}
    schema_profile = attrs.get("schema_profile", {}) or {}
    shadow_metric_gate = attrs.get("shadow_metric_gate", {}) or {}
    safe_metric_columns = [
        str(value)
        for value in list(shadow_metric_gate.get("safe_metric_columns") or [])
        if str(value or "").strip()
    ]
    candidates = safe_metric_columns or _schema_metric_candidates(schema_profile)
    if not candidates:
        return []

    # V6.5 Guard: Validate actual DataFrame dtype — reject non-numeric columns
    validated: list[str] = []
    for col in candidates:
        if col not in candidate_df.columns:
            continue
        if pd.api.types.is_numeric_dtype(candidate_df[col]):
            validated.append(col)
        else:
            print(f"⚠️ [SHADOW] Metric column '{col}' excluded — dtype '{candidate_df[col].dtype}' is not numeric")
    return validated or candidates  # Fallback to original if ALL fail (edge case)


def _build_shadow_prompt(candidate_df: pd.DataFrame) -> tuple[str | None, str | None]:
    attrs = getattr(candidate_df, "attrs", {}) or {}
    contract = attrs.get("semantic_contract", {}) or {}
    contract_metric_columns = _list_str(contract.get("metric_columns"))
    metric_columns = _shadow_safe_metric_columns(candidate_df) or contract_metric_columns
    dimension_columns = _list_str(contract.get("dimension_columns"))
    identifier_columns = _list_str(contract.get("identifier_columns"))
    date_columns = _list_str(contract.get("date_columns"))
    time_axis = str(contract.get("time_axis") or "").strip()

    metric_column = metric_columns[0] if metric_columns else None
    primary_dimension = dimension_columns[0] if dimension_columns else identifier_columns[0] if identifier_columns else None
    date_column = time_axis or (date_columns[0] if date_columns else None)

    if metric_column and primary_dimension:
        return (
            f"Realiza un análisis completo de {_humanize_label(metric_column)} por {_humanize_label(primary_dimension)}",
            "macro_dimension_bundle",
        )
    if metric_column and date_column:
        return (
            f"Realiza un análisis completo de {_humanize_label(metric_column)} por {_humanize_label(date_column)}",
            "macro_trend_bundle",
        )
    if metric_column:
        return (
            f"Realiza un análisis completo de {_humanize_label(metric_column)}",
            "macro_metric_bundle",
        )
    return None, None


_PROMPT_DATE_PATTERNS = (
    ("%Y-%m-%d", re.compile(r"\b\d{4}-\d{2}-\d{2}\b")),
    ("%d-%m-%Y", re.compile(r"\b\d{1,2}-\d{1,2}-\d{4}\b")),
    ("%d/%m/%Y", re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b")),
    ("%d.%m.%Y", re.compile(r"\b\d{1,2}\.\d{1,2}\.\d{4}\b")),
)


def _safe_metric_column(candidate_df: pd.DataFrame, *, prompt: str | None = None) -> str | None:
    metric_columns = _shadow_safe_metric_columns(candidate_df)
    if not metric_columns:
        return None
    if not prompt or len(metric_columns) <= 1:
        return metric_columns[0]

    # Prompt-aware: reorder by affinity to user's words
    scored = [(col, _prompt_metric_affinity(col, prompt)) for col in metric_columns]
    scored.sort(key=lambda x: -x[1])
    best_col, best_score = scored[0]
    if best_score > 0:
        return best_col
    return metric_columns[0]


def _safe_metric_columns(candidate_df: pd.DataFrame) -> list[str]:
    return [str(value) for value in _shadow_safe_metric_columns(candidate_df) if str(value or "").strip()]


def _schema_profile(candidate_df: pd.DataFrame) -> dict[str, Any]:
    return dict((getattr(candidate_df, "attrs", {}) or {}).get("schema_profile", {}) or {})


def _semantic_contract(candidate_df: pd.DataFrame) -> dict[str, Any]:
    return dict((getattr(candidate_df, "attrs", {}) or {}).get("semantic_contract", {}) or {})


def _metric_unit(metric_column: str | None) -> MetricUnit:
    inferred = infer_metric_unit_from_column_name(metric_column)
    return inferred if isinstance(inferred, MetricUnit) else MetricUnit.NUMBER


def _column_role(schema_profile: dict[str, Any], column_name: str) -> str:
    return str(schema_profile.get(column_name, {}).get("role") or "").strip().lower()


def _column_cardinality(schema_profile: dict[str, Any], column_name: str) -> int:
    return int(schema_profile.get(column_name, {}).get("cardinality") or 0)


def _visual_protocol_from_family(
    requested_visual_family: str | None,
    *,
    default: VisualProtocol = VisualProtocol.BAR,
) -> VisualProtocol:
    mapping = {
        "bar_chart": VisualProtocol.BAR,
        "line_chart": VisualProtocol.LINE,
        "area_chart": VisualProtocol.AREA,
        "pie_chart": VisualProtocol.PIE,
        "donut_chart": VisualProtocol.PIE,
        "treemap": VisualProtocol.TREEMAP,
        "scatter_plot": VisualProtocol.SCATTER,
        "funnel_chart": VisualProtocol.FUNNEL,
        "heatmap_chart": VisualProtocol.HEATMAP,
        "waterfall_chart": VisualProtocol.WATERFALL,
        "boxplot_chart": VisualProtocol.BOXPLOT,
        "histogram_chart": VisualProtocol.HISTOGRAM,
        "dual_axis_chart": VisualProtocol.DUAL_AXIS,
        "combo_chart": VisualProtocol.DUAL_AXIS,
        "kpi_card": VisualProtocol.KPI,
    }
    return mapping.get(str(requested_visual_family or "").strip(), default)


_SHADOW_PARITY_VISUAL_FAMILIES = {
    "bar_chart",
    "pie_chart",
    "donut_chart",
    "treemap",
    "funnel_chart",
    "scatter_plot",
}


def _requested_visual_family_from_prompt(prompt: str | None) -> str | None:
    requested_visuals = extract_prompt_visual_requests(prompt)
    if not requested_visuals:
        return None
    return normalize_visual_id(requested_visuals[0])


def _best_shadow_date_column(candidate_df: pd.DataFrame) -> str | None:
    schema_profile = _schema_profile(candidate_df)
    contract = _semantic_contract(candidate_df)
    time_axis = str(contract.get("time_axis") or "").strip()
    if time_axis and time_axis in schema_profile:
        role = str(schema_profile.get(time_axis, {}).get("role") or "").strip().lower()
        if role == "date":
            return time_axis

    ranked = sorted(
        (
            (
                int(info.get("cardinality") or 0),
                str(column_name),
            )
            for column_name, info in schema_profile.items()
            if isinstance(info, dict) and str(info.get("role") or "").strip().lower() == "date"
        ),
        key=lambda item: (-item[0], item[1]),
    )
    if not ranked:
        return None
    for cardinality, column_name in ranked:
        if cardinality > 1:
            return column_name
    return ranked[0][1]


def _prompt_explicitly_requests_temporal_analysis(
    prompt: str | None,
    requested_visual_family: str | None = None,
) -> bool:
    if str(requested_visual_family or "").strip() in {"line_chart", "area_chart"}:
        return True
    normalized_prompt = SemanticTranslator._normalize_surface_text(prompt)
    if not normalized_prompt:
        return False
    temporal_markers = (
        "tendencia",
        "evolucion",
        "evolución",
        "historico",
        "histórico",
        "timeline",
        "serie temporal",
        "mensual",
        "semanal",
        "diario",
        "trimestral",
        "anual",
        "por mes",
        "por semana",
        "por dia",
        "por día",
        "por año",
        "mes a mes",
        "year over year",
        "yoy",
        "mom",
    )
    return any(marker in normalized_prompt for marker in temporal_markers)


def _extract_prompt_top_n(prompt: str | None) -> int | None:
    """Extract explicit 'top N' from the user prompt.

    V6.5: Universal prompt-aware limit extraction. Parses patterns like
    'top 5', 'Top 10', 'top3', 'los 5 mejores'. Returns the integer N
    or None if no explicit limit was requested.
    """
    normalized = SemanticTranslator._normalize_surface_text(prompt)
    if not normalized:
        return None
    # Pattern: "top 5", "top 10", "top3"
    match = re.search(r'\btop\s*(\d{1,2})\b', normalized)
    if match:
        return max(2, min(int(match.group(1)), 30))
    # Pattern: "los 5 mejores/mayores/principales"
    match = re.search(r'\blos?\s+(\d{1,2})\s+(mejores|mayores|principales|peores|menores)\b', normalized)
    if match:
        return max(2, min(int(match.group(1)), 30))
    return None


def _default_dimension_limit(
    schema_profile: dict[str, Any],
    column_name: str,
    *,
    default: int = 10,
    prompt_override: int | None = None,
) -> int:
    """Determine dimension limit for Top-N queries.

    V6.5: Added prompt_override. When the user explicitly requests 'top 5',
    this takes absolute priority over cardinality-based defaults.
    """
    # V6.5: Explicit user request takes priority
    if prompt_override is not None and 2 <= prompt_override <= 30:
        return prompt_override
    cardinality = int(schema_profile.get(column_name, {}).get("cardinality") or 0)
    if 0 < cardinality <= 12:
        return cardinality
    return default


def _extract_prompt_iso_dates(prompt: str | None) -> list[str]:
    normalized_prompt = str(prompt or "").strip()
    if not normalized_prompt:
        return []

    resolved: list[str] = []
    for fmt, pattern in _PROMPT_DATE_PATTERNS:
        for match in pattern.findall(normalized_prompt):
            try:
                parsed = datetime.strptime(match, fmt)
            except Exception:
                continue
            iso_value = parsed.date().isoformat()
            if iso_value not in resolved:
                resolved.append(iso_value)
    return resolved


def _literal_filters(candidate_df: pd.DataFrame, prompt: str | None) -> list[DataFilter]:
    if settings.UNIVERSAL_TABULAR_PRODUCTION_EXECUTOR_ENABLED:
        return []
    if not str(prompt or "").strip():
        return []
    literal_catalog = dict((getattr(candidate_df, "attrs", {}) or {}).get("literal_filter_catalog", {}) or {})
    if not literal_catalog:
        return []
    try:
        filters = SemanticTranslator._detect_literal_filters(str(prompt), literal_catalog) or []
    except Exception:
        return []
    return [value for value in list(filters) if isinstance(value, DataFilter)]


def _best_shadow_dimension_candidate(
    candidate_df: pd.DataFrame,
    *,
    exclude: set[str] | None = None,
    dimensions_only: bool = False,
    bounded_only: bool = False,
) -> str | None:
    schema_profile = _schema_profile(candidate_df)
    exclude = exclude or set()
    ranked: list[tuple[int, int, str]] = []
    for column_name, info in schema_profile.items():
        if not isinstance(info, dict):
            continue
        name = str(column_name)
        if name in exclude:
            continue
        role = _column_role(schema_profile, name)
        if role not in {"dimension", "identifier"}:
            continue
        if dimensions_only and role != "dimension":
            continue
        cardinality = _column_cardinality(schema_profile, name)
        if bounded_only and (cardinality < 2 or cardinality > 12):
            continue
        ranked.append(
            (
                0 if role == "dimension" else 1,
                0 if 2 <= cardinality <= 12 else 1,
                cardinality if cardinality > 0 else 999999,
                name,
            )
        )
    ranked.sort()
    return ranked[0][3] if ranked else None


def _resolve_shadow_dimension(prompt: str | None, candidate_df: pd.DataFrame, *, exclude: set[str] | None = None) -> str | None:
    schema_profile = _schema_profile(candidate_df)
    columns = [str(column_name) for column_name in list(candidate_df.columns)]
    surface_prompt = SemanticTranslator._normalize_surface_text(prompt)
    segment = SemanticTranslator._extract_primary_dimension_segment(surface_prompt)
    resolved = SemanticTranslator._resolve_segment_columns(
        segment or surface_prompt,
        columns,
        schema_profile=schema_profile,
        allowed_roles={"dimension", "identifier"},
    )
    exclude = exclude or set()
    for column_name in resolved:
        if column_name not in exclude:
            return column_name
    return SemanticTranslator._pick_best_dimension_column(
        surface_prompt,
        columns,
        schema_profile=schema_profile,
        exclude=exclude,
    )


def _prefer_shadow_dimension(
    candidate_df: pd.DataFrame,
    *,
    prompt: str | None,
    exclude: set[str] | None = None,
) -> str | None:
    schema_profile = _schema_profile(candidate_df)
    exclude = exclude or set()
    resolved = _resolve_shadow_dimension(prompt, candidate_df, exclude=exclude)
    if resolved and _column_role(schema_profile, resolved) == "dimension":
        return resolved
    if resolved and resolved not in exclude and _column_role(schema_profile, resolved) == "identifier":
        preferred_dimension = _best_shadow_dimension_candidate(
            candidate_df,
            exclude=exclude | {resolved},
            dimensions_only=True,
        )
        return preferred_dimension or resolved
    return resolved or _best_shadow_dimension_candidate(
        candidate_df,
        exclude=exclude,
        dimensions_only=True,
    ) or _best_shadow_dimension_candidate(
        candidate_df,
        exclude=exclude,
        dimensions_only=False,
    )


def _shadow_secondary_dimension(
    candidate_df: pd.DataFrame,
    *,
    prompt: str | None,
    exclude: set[str] | None = None,
) -> str | None:
    schema_profile = _schema_profile(candidate_df)
    columns = [str(column_name) for column_name in list(candidate_df.columns)]
    surface_prompt = SemanticTranslator._normalize_surface_text(prompt)
    secondary = SemanticTranslator._pick_best_dimension_column(
        surface_prompt,
        columns,
        schema_profile=schema_profile,
        exclude=exclude or set(),
    )
    if secondary:
        return secondary
    return _best_shadow_dimension_candidate(
        candidate_df,
        exclude=exclude,
        dimensions_only=True,
    )


def _build_shadow_visual_parity_dimension_plans(
    prompt: str | None,
    candidate_df: pd.DataFrame,
    *,
    metric_column: str,
    literal_filters: list[DataFilter],
) -> tuple[list[AnalysisPlan], str | None, str | None]:
    schema_profile = _schema_profile(candidate_df)
    filter_columns = {
        str(filter_row.column)
        for filter_row in literal_filters
        if str(getattr(filter_row, "column", "") or "").strip()
    }
    primary_dimension = _best_shadow_dimension_candidate(
        candidate_df,
        exclude=filter_columns,
        dimensions_only=True,
        bounded_only=True,
    ) or _prefer_shadow_dimension(
        candidate_df,
        prompt=prompt,
        exclude=filter_columns,
    )
    if not primary_dimension:
        return [], None, None

    secondary_dimension = _shadow_secondary_dimension(
        candidate_df,
        prompt=prompt,
        exclude=filter_columns | {primary_dimension},
    )
    metric_label = _humanize_label(metric_column)
    primary_label = _humanize_label(primary_dimension)
    metric_unit = _metric_unit(metric_column)
    plans: list[AnalysisPlan] = [
        AnalysisPlan(
            main_intent=DistributionIntent(
                rationale=(
                    "La paridad shadow prioriza composición categórica cuando el observer ya "
                    "confirmó una expectativa visual tipo pie/donut en el runtime vivo."
                ),
                filters=literal_filters,
                dimension=primary_dimension,
                metric=metric_column,
                limit=min(_default_dimension_limit(schema_profile, primary_dimension), 5),
                metric_unit=metric_unit,
                visual_protocol=VisualProtocol.PIE,
            ),
            title=f"Composición de {metric_label} por {primary_label}",
            column_aliases={
                metric_column: metric_label,
                primary_dimension: primary_label,
            },
            metric_polarity=MetricPolarity.NEUTRAL,
        )
    ]

    comparison_dimension = secondary_dimension or primary_dimension
    comparison_label = _humanize_label(comparison_dimension)
    comparison_visual = (
        VisualProtocol.TREEMAP
        if comparison_dimension != primary_dimension
        else VisualProtocol.BAR
    )
    plans.append(
        AnalysisPlan(
            main_intent=DistributionIntent(
                rationale=(
                    "La segunda vista mantiene explicabilidad categórica sobre una dimensión "
                    "acotada para sostener el espejo visual sin inventar KPI aislados."
                ),
                filters=literal_filters,
                dimension=comparison_dimension,
                metric=metric_column,
                limit=_default_dimension_limit(schema_profile, comparison_dimension),
                metric_unit=metric_unit,
                visual_protocol=comparison_visual,
            ),
            title=f"Distribución de {metric_label} por {comparison_label}",
            column_aliases={
                metric_column: metric_label,
                comparison_dimension: comparison_label,
            },
            metric_polarity=MetricPolarity.NEUTRAL,
        )
    )
    return plans[:2], prompt, "shadow_dimension_visual_parity_bundle"


def _build_shadow_chart_visual_parity_plans(
    prompt: str | None,
    candidate_df: pd.DataFrame,
    *,
    requested_visual_family: str | None = None,
) -> tuple[list[AnalysisPlan], str | None, str | None]:
    metric_column = _safe_metric_column(candidate_df, prompt=prompt)
    if not metric_column:
        return [], None, None

    literal_filters = _literal_filters(candidate_df, prompt)
    filter_columns = {
        str(filter_row.column)
        for filter_row in literal_filters
        if str(getattr(filter_row, "column", "") or "").strip()
    }
    primary_dimension = _prefer_shadow_dimension(
        candidate_df,
        prompt=prompt,
        exclude=filter_columns,
    )
    if not primary_dimension:
        return [], None, None

    schema_profile = _schema_profile(candidate_df)
    cardinality = _column_cardinality(schema_profile, primary_dimension)
    selected_family = str(requested_visual_family or "").strip()
    if not selected_family:
        selected_family = "treemap" if cardinality > 12 else "bar_chart"
    elif selected_family in {"pie_chart", "donut_chart"} and cardinality > 6:
        selected_family = "treemap"

    visual_protocol = _visual_protocol_from_family(
        selected_family,
        default=VisualProtocol.TREEMAP if cardinality > 12 else VisualProtocol.BAR,
    )
    metric_label = _humanize_label(metric_column)
    dimension_label = _humanize_label(primary_dimension)
    plan = AnalysisPlan(
        main_intent=DistributionIntent(
            rationale=(
                "La paridad shadow fija una sola vista categórica para requests de gráfico "
                "genérico y prioriza la familia visual ya observada o la densidad real."
            ),
            filters=literal_filters,
            dimension=primary_dimension,
            metric=metric_column,
            limit=_default_dimension_limit(schema_profile, primary_dimension),
            metric_unit=_metric_unit(metric_column),
            visual_protocol=visual_protocol,
        ),
        title=f"{metric_label} por {dimension_label}",
        column_aliases={
            metric_column: metric_label,
            primary_dimension: dimension_label,
        },
        metric_polarity=MetricPolarity.NEUTRAL,
    )
    return [plan], prompt, "shadow_chart_visual_parity_bundle"


def _build_shadow_scatter_visual_parity_plans(
    prompt: str | None,
    candidate_df: pd.DataFrame,
) -> tuple[list[AnalysisPlan], str | None, str | None]:
    normalized_prompt = str(prompt or "").strip()
    if not normalized_prompt:
        return [], None, None
    columns = [str(column_name) for column_name in list(candidate_df.columns)]
    schema_profile = _schema_profile(candidate_df)
    plans = SemanticTranslator._build_explicit_scatter_plan(
        normalized_prompt,
        columns,
        schema_profile=schema_profile,
    )
    if not plans:
        return [], None, None
    return list(plans), normalized_prompt, "shadow_scatter_visual_parity_bundle"


def _extract_heatmap_axis_segments(prompt: str | None) -> tuple[str | None, str | None]:
    surface_prompt = SemanticTranslator._normalize_surface_text(prompt)
    if not surface_prompt:
        return None, None
    match = re.search(
        r"\bpor\s+(.+?)\s+y\s+(.+?)(?=$|,|\s+con\s+|\s+para\s+)",
        surface_prompt,
        flags=re.IGNORECASE,
    )
    if not match:
        return None, None
    return match.group(1).strip(" .,:;"), match.group(2).strip(" .,:;")


def _resolve_visual_metric_column(
    prompt: str | None,
    candidate_df: pd.DataFrame,
) -> str | None:
    metric_column = _safe_metric_column(candidate_df, prompt=prompt)
    if metric_column:
        return metric_column
    schema_profile = _schema_profile(candidate_df)
    columns = [str(column_name) for column_name in list(candidate_df.columns)]
    metric_candidates = SemanticTranslator._resolve_segment_columns(
        SemanticTranslator._normalize_surface_text(prompt),
        columns,
        schema_profile=schema_profile,
        allowed_roles={"metric"},
    )
    return metric_candidates[0] if metric_candidates else None


def _build_shadow_explicit_advanced_visual_plans(
    prompt: str | None,
    candidate_df: pd.DataFrame,
    *,
    requested_visual_family: str,
) -> tuple[list[AnalysisPlan], str | None, str | None]:
    schema_profile = _schema_profile(candidate_df)
    metric_column = _resolve_visual_metric_column(prompt, candidate_df)
    if not metric_column:
        return [], None, None

    literal_filters = _literal_filters(candidate_df, prompt)
    filter_columns = {
        str(filter_row.column)
        for filter_row in literal_filters
        if str(getattr(filter_row, "column", "") or "").strip()
    }
    metric_label = _humanize_label(metric_column)
    metric_unit = _metric_unit(metric_column)
    surface_prompt = SemanticTranslator._normalize_surface_text(prompt)

    if requested_visual_family == "heatmap_chart":
        dim_a_segment, dim_b_segment = _extract_heatmap_axis_segments(surface_prompt)
        columns = [str(column_name) for column_name in list(candidate_df.columns)]
        dim_a_candidates = SemanticTranslator._resolve_segment_columns(
            dim_a_segment or surface_prompt,
            columns,
            schema_profile=schema_profile,
            allowed_roles={"dimension", "identifier", "date"},
        )
        dim_b_candidates = SemanticTranslator._resolve_segment_columns(
            dim_b_segment or surface_prompt,
            columns,
            schema_profile=schema_profile,
            allowed_roles={"dimension", "identifier", "date"},
        )

        primary_dimension = dim_a_candidates[0] if dim_a_candidates else None
        secondary_dimension = None
        for candidate in dim_b_candidates:
            if candidate != primary_dimension:
                secondary_dimension = candidate
                break
        if not primary_dimension:
            primary_dimension = _best_shadow_date_column(candidate_df) or _prefer_shadow_dimension(
                candidate_df,
                prompt=prompt,
                exclude=filter_columns,
            )
        if not secondary_dimension:
            secondary_dimension = _shadow_secondary_dimension(
                candidate_df,
                prompt=prompt,
                exclude=filter_columns | ({primary_dimension} if primary_dimension else set()),
            )
        if not primary_dimension or not secondary_dimension:
            return [], None, None

        plan = AnalysisPlan(
            main_intent=DistributionIntent(
                rationale=(
                    "El heatmap explícito cruza dos ejes categóricos/temporales para medir "
                    "intensidad real de la métrica sin degradar a barras genéricas."
                ),
                filters=literal_filters,
                dimension=primary_dimension,
                metric=metric_column,
                group_by=[secondary_dimension],
                limit=_default_dimension_limit(schema_profile, primary_dimension),
                metric_unit=metric_unit,
                visual_protocol=VisualProtocol.HEATMAP,
            ),
            title=f"Intensidad de {metric_label} por {_humanize_label(primary_dimension)} y {_humanize_label(secondary_dimension)}",
            column_aliases={
                metric_column: metric_label,
                primary_dimension: _humanize_label(primary_dimension),
                secondary_dimension: _humanize_label(secondary_dimension),
            },
            metric_polarity=MetricPolarity.NEUTRAL,
        )
        return [plan], prompt, "shadow_heatmap_visual_bundle"

    primary_dimension = _prefer_shadow_dimension(
        candidate_df,
        prompt=prompt,
        exclude=filter_columns,
    )
    if not primary_dimension:
        return [], None, None

    visual_protocol = _visual_protocol_from_family(requested_visual_family)
    limit = SemanticTranslator._extract_top_limit(surface_prompt) or _default_dimension_limit(
        schema_profile,
        primary_dimension,
    )
    plan = AnalysisPlan(
        main_intent=DistributionIntent(
            rationale=(
                "El shadow runtime respeta el visual explícito solicitado y mantiene el cálculo "
                "sobre la dimensión más consistente del contrato analítico."
            ),
            filters=literal_filters,
            dimension=primary_dimension,
            metric=metric_column,
            limit=limit,
            metric_unit=metric_unit,
            visual_protocol=visual_protocol,
        ),
        title=f"{metric_label} por {_humanize_label(primary_dimension)}",
        column_aliases={
            metric_column: metric_label,
            primary_dimension: _humanize_label(primary_dimension),
        },
        metric_polarity=MetricPolarity.NEUTRAL,
    )
    return [plan], prompt, "shadow_explicit_visual_bundle"


def _build_shadow_dimension_parity_plans(
    prompt: str | None,
    candidate_df: pd.DataFrame,
    *,
    requested_visual_family: str | None = None,
) -> tuple[list[AnalysisPlan], str | None, str | None]:
    metric_column = _safe_metric_column(candidate_df, prompt=prompt)
    if not metric_column:
        return [], None, None

    schema_profile = _schema_profile(candidate_df)
    literal_filters = _literal_filters(candidate_df, prompt)
    filter_columns = {
        str(filter_row.column)
        for filter_row in literal_filters
        if str(getattr(filter_row, "column", "") or "").strip()
    }
    if requested_visual_family in {"pie_chart", "donut_chart"} or (
        not requested_visual_family and literal_filters
    ):
        visual_parity_plans = _build_shadow_visual_parity_dimension_plans(
            prompt,
            candidate_df,
            metric_column=metric_column,
            literal_filters=literal_filters,
        )
        if visual_parity_plans[0]:
            return visual_parity_plans

    primary_dimension = _prefer_shadow_dimension(
        candidate_df,
        prompt=prompt,
        exclude=filter_columns,
    )
    if not primary_dimension:
        return [], None, None

    metric_label = _humanize_label(metric_column)
    primary_label = _humanize_label(primary_dimension)
    metric_unit = _metric_unit(metric_column)
    # V6.5: Extract explicit "top N" from prompt to override cardinality-based default
    prompt_top_n = _extract_prompt_top_n(prompt)
    effective_limit = _default_dimension_limit(
        schema_profile, primary_dimension, prompt_override=prompt_top_n,
    )
    plans: list[AnalysisPlan] = [
        AnalysisPlan(
            main_intent=DistributionIntent(
                rationale=(
                    "El shadow runtime prioriza un ranking directo sobre la dimensión solicitada "
                    "para evitar que el planner derive métricas o ejes inconsistentes."
                ),
                filters=literal_filters,
                dimension=primary_dimension,
                metric=metric_column,
                limit=effective_limit,
                metric_unit=metric_unit,
                visual_protocol=VisualProtocol.BAR,
            ),
            title=f"Top {effective_limit} {primary_label} por {metric_label}",
            column_aliases={
                metric_column: metric_label,
                primary_dimension: primary_label,
            },
            metric_polarity=MetricPolarity.NEUTRAL,
        )
    ]

    date_column = _best_shadow_date_column(candidate_df)
    wants_temporal_analysis = _prompt_explicitly_requests_temporal_analysis(
        prompt,
        requested_visual_family=requested_visual_family,
    )
    if (
        wants_temporal_analysis
        and date_column
        and int(schema_profile.get(date_column, {}).get("cardinality") or 0) > 1
    ):
        date_label = _humanize_label(date_column)
        # V6.5: When the prompt requests temporal + dimension analysis,
        # inject split_dimension to generate multi-series line chart.
        split_dim = primary_dimension if prompt_top_n else None
        split_limit = prompt_top_n if prompt_top_n else None
        plans.append(
            AnalysisPlan(
                main_intent=TimeTrendIntent(
                    rationale=(
                    "La paridad shadow agrega una tendencia explícita cuando existe un eje "
                    "temporal con señal suficiente en el contrato paralelo."
                ),
                filters=literal_filters,
                date_column=date_column,
                value_column=metric_column,
                metric_unit=metric_unit,
                visual_protocol=VisualProtocol.LINE,
                split_dimension=split_dim,
                split_limit=split_limit,
            ),
                title=f"Evolución de {metric_label} por {date_label}",
                column_aliases={
                    metric_column: metric_label,
                    date_column: date_label,
                },
                metric_polarity=MetricPolarity.NEUTRAL,
            )
        )
    elif date_column:
        emit_structured_log(
            "canonical_shadow_dimension_temporal_plan_skipped",
            prompt_preview=str(prompt or "")[:200],
            date_column=date_column,
            reason="prompt_not_temporal",
        )

    secondary_dimension = _shadow_secondary_dimension(
        candidate_df,
        prompt=prompt,
        exclude=filter_columns | {primary_dimension},
    )
    if secondary_dimension:
        secondary_label = _humanize_label(secondary_dimension)
        plans.append(
            AnalysisPlan(
                main_intent=DistributionIntent(
                    rationale=(
                        "La tercera vista se fuerza a composición jerárquica para sostener paridad "
                        "visual con el runtime vivo sin depender de inferencia generativa."
                    ),
                    filters=literal_filters,
                    dimension=secondary_dimension,
                    metric=metric_column,
                    limit=_default_dimension_limit(schema_profile, secondary_dimension),
                    metric_unit=metric_unit,
                    visual_protocol=VisualProtocol.TREEMAP,
                ),
                title=f"Distribución de {metric_label} por {secondary_label}",
                column_aliases={
                    metric_column: metric_label,
                    secondary_dimension: secondary_label,
                },
                metric_polarity=MetricPolarity.NEUTRAL,
            )
        )

    if len(plans) < 3:
        plans.append(
            AnalysisPlan(
                main_intent=DescriptiveIntent(
                    rationale=(
                        "Se agrega un KPI de control para completar el bundle sin introducir "
                        "tendencias temporales no solicitadas por el usuario."
                    ),
                    filters=literal_filters,
                    metrics=[metric_column],
                    metric_unit=metric_unit,
                    aggregation="sum",
                    visual_protocol=VisualProtocol.KPI,
                ),
                title=f"{metric_label} Total",
                column_aliases={metric_column: metric_label},
                metric_polarity=MetricPolarity.NEUTRAL,
            )
        )
    return plans[:3], prompt, "shadow_dimension_parity_bundle"


def _build_shadow_generic_parity_plans(
    prompt: str | None,
    candidate_df: pd.DataFrame,
    *,
    requested_visual_family: str | None = None,
) -> tuple[list[AnalysisPlan], str | None, str | None]:
    metric_column = _safe_metric_column(candidate_df, prompt=prompt)
    if not metric_column:
        return [], None, None

    literal_filters = _literal_filters(candidate_df, prompt)
    filter_columns = {
        str(filter_row.column)
        for filter_row in literal_filters
        if str(getattr(filter_row, "column", "") or "").strip()
    }
    primary_dimension = _prefer_shadow_dimension(
        candidate_df,
        prompt=prompt,
        exclude=filter_columns,
    )
    if not primary_dimension:
        return [], None, None

    schema_profile = _schema_profile(candidate_df)
    cardinality = _column_cardinality(schema_profile, primary_dimension)
    metric_label = _humanize_label(metric_column)
    dimension_label = _humanize_label(primary_dimension)
    metric_unit = _metric_unit(metric_column)
    selected_family = str(requested_visual_family or "").strip()
    if selected_family in {"pie_chart", "donut_chart"}:
        primary_visual = VisualProtocol.PIE
        secondary_visual = VisualProtocol.BAR
    else:
        primary_visual = VisualProtocol.BAR
        secondary_visual = VisualProtocol.PIE if 0 < cardinality <= 6 else VisualProtocol.TREEMAP

    plans: list[AnalysisPlan] = [
        AnalysisPlan(
            main_intent=DistributionIntent(
                rationale=(
                    "La paridad shadow reemplaza el bundle macro por una lectura categórica "
                    "compacta cuando el vivo devuelve composiciones visuales de dos vistas."
                ),
                filters=literal_filters,
                dimension=primary_dimension,
                metric=metric_column,
                limit=_default_dimension_limit(schema_profile, primary_dimension),
                metric_unit=metric_unit,
                visual_protocol=primary_visual,
            ),
            title=f"{metric_label} por {dimension_label}",
            column_aliases={
                metric_column: metric_label,
                primary_dimension: dimension_label,
            },
            metric_polarity=MetricPolarity.NEUTRAL,
        ),
        AnalysisPlan(
            main_intent=DistributionIntent(
                rationale=(
                    "La segunda vista shadow mantiene la misma dimensión para cerrar paridad "
                    "de conteo sin introducir KPI o tendencia no pedidos por el vivo."
                ),
                filters=literal_filters,
                dimension=primary_dimension,
                metric=metric_column,
                limit=_default_dimension_limit(schema_profile, primary_dimension),
                metric_unit=metric_unit,
                visual_protocol=secondary_visual,
            ),
            title=f"Distribución de {metric_label} por {dimension_label}",
            column_aliases={
                metric_column: metric_label,
                primary_dimension: dimension_label,
            },
            metric_polarity=MetricPolarity.NEUTRAL,
        ),
    ]
    secondary_dimension = _shadow_secondary_dimension(
        candidate_df,
        prompt=prompt,
        exclude=filter_columns | {primary_dimension},
    )
    tertiary_dimension = secondary_dimension or primary_dimension
    tertiary_label = _humanize_label(tertiary_dimension)
    tertiary_cardinality = _column_cardinality(schema_profile, tertiary_dimension)
    used_visuals = {primary_visual, secondary_visual}
    if 0 < tertiary_cardinality <= 6 and VisualProtocol.PIE not in used_visuals:
        tertiary_visual = VisualProtocol.PIE
    elif VisualProtocol.TREEMAP not in used_visuals:
        tertiary_visual = VisualProtocol.TREEMAP
    elif VisualProtocol.BAR not in used_visuals:
        tertiary_visual = VisualProtocol.BAR
    else:
        tertiary_visual = VisualProtocol.PIE if 0 < tertiary_cardinality <= 6 else VisualProtocol.TREEMAP

    plans.append(
        AnalysisPlan(
            main_intent=DistributionIntent(
                rationale=(
                    "La tercera vista shadow cierra la paridad histórica de conteo con una "
                    "lectura categórica adicional sobre la dimensión más explicativa disponible."
                ),
                filters=literal_filters,
                dimension=tertiary_dimension,
                metric=metric_column,
                limit=_default_dimension_limit(schema_profile, tertiary_dimension),
                metric_unit=metric_unit,
                visual_protocol=tertiary_visual,
            ),
            title=f"Composición de {metric_label} por {tertiary_label}",
            column_aliases={
                metric_column: metric_label,
                tertiary_dimension: tertiary_label,
            },
            metric_polarity=MetricPolarity.NEUTRAL,
        )
    )
    return plans[:3], prompt, "shadow_generic_visual_parity_bundle"


def _build_shadow_comparative_parity_plans(
    prompt: str | None,
    candidate_df: pd.DataFrame,
) -> tuple[list[AnalysisPlan], str | None, str | None]:
    metric_column = _safe_metric_column(candidate_df, prompt=prompt)
    date_column = _best_shadow_date_column(candidate_df)
    if not metric_column or not date_column:
        return [], None, None

    schema_profile = _schema_profile(candidate_df)
    if int(schema_profile.get(date_column, {}).get("cardinality") or 0) <= 1:
        return [], None, None

    metric_label = _humanize_label(metric_column)
    date_label = _humanize_label(date_column)
    metric_unit = _metric_unit(metric_column)
    prompt_dates = _extract_prompt_iso_dates(prompt)
    comparison_filters: list[DataFilter] = []
    if len(prompt_dates) >= 2:
        ordered_dates = sorted(prompt_dates)
        comparison_filters = [
            DataFilter(column=date_column, operator=FilterOperator.GREATER_EQUAL, value=ordered_dates[0]),
            DataFilter(column=date_column, operator=FilterOperator.LESS_EQUAL, value=ordered_dates[-1]),
        ]

    comparison_dimension = _resolve_shadow_dimension(prompt, candidate_df)
    if comparison_dimension == date_column:
        comparison_dimension = None
    if not comparison_dimension:
        comparison_dimension = SemanticTranslator._pick_best_dimension_column(
            SemanticTranslator._normalize_surface_text(prompt),
            [str(column_name) for column_name in list(candidate_df.columns)],
            schema_profile=schema_profile,
            exclude={date_column},
        )

    plans: list[AnalysisPlan] = [
        AnalysisPlan(
            main_intent=DistributionIntent(
                rationale=(
                    "La paridad comparativa inicia con una comparación directa entre periodos "
                    "sobre el eje temporal explícito del contrato paralelo."
                ),
                filters=comparison_filters,
                dimension=date_column,
                metric=metric_column,
                limit=2 if comparison_filters else 10,
                metric_unit=metric_unit,
                visual_protocol=VisualProtocol.BAR,
            ),
            title=f"Comparativa de {metric_label} por {date_label}",
            column_aliases={
                metric_column: metric_label,
                date_column: date_label,
            },
            metric_polarity=MetricPolarity.NEUTRAL,
        ),
        AnalysisPlan(
            main_intent=TimeTrendIntent(
                rationale=(
                    "La segunda vista mantiene una tendencia temporal para preservar lectura de "
                    "cambio, incluso cuando el prompt comparativo es breve."
                ),
                filters=comparison_filters,
                date_column=date_column,
                value_column=metric_column,
                metric_unit=metric_unit,
                visual_protocol=VisualProtocol.LINE,
            ),
            title=f"Evolución de {metric_label} por {date_label}",
            column_aliases={
                metric_column: metric_label,
                date_column: date_label,
            },
            metric_polarity=MetricPolarity.NEUTRAL,
        ),
    ]

    if comparison_dimension:
        dimension_label = _humanize_label(comparison_dimension)
        plans.append(
            AnalysisPlan(
                main_intent=DistributionIntent(
                    rationale=(
                        "La tercera vista aporta composición comparativa sobre la dimensión más "
                        "explicativa disponible, sin forzar filtros exactos frágiles."
                    ),
                    dimension=comparison_dimension,
                    metric=metric_column,
                    limit=_default_dimension_limit(schema_profile, comparison_dimension),
                    metric_unit=metric_unit,
                    visual_protocol=VisualProtocol.TREEMAP,
                ),
                title=f"Distribución de {metric_label} por {dimension_label}",
                column_aliases={
                    metric_column: metric_label,
                    comparison_dimension: dimension_label,
                },
                metric_polarity=MetricPolarity.NEUTRAL,
            )
        )

    if len(plans) < 3:
        return [], None, None
    return plans[:3], prompt, "shadow_comparative_parity_bundle"


def _build_shadow_predictive_parity_plans(
    prompt: str | None,
    candidate_df: pd.DataFrame,
) -> tuple[list[AnalysisPlan], str | None, str | None]:
    """Build a predictive analysis bundle: forecast + historical trend + dimensional breakdown."""
    metric_column = _safe_metric_column(candidate_df, prompt=prompt)
    date_column = _best_shadow_date_column(candidate_df)
    if not metric_column or not date_column:
        return [], None, None

    schema_profile = _schema_profile(candidate_df)
    date_cardinality = int(schema_profile.get(date_column, {}).get("cardinality") or 0)
    if date_cardinality < 4:
        # Need at least 4 temporal periods for meaningful forecast
        return [], None, None

    metric_label = _humanize_label(metric_column)
    date_label = _humanize_label(date_column)
    metric_unit = _metric_unit(metric_column)

    # Infer grain from cardinality heuristic
    if date_cardinality <= 10:
        grain = TimeGrain.YEAR
    elif date_cardinality <= 60:
        grain = TimeGrain.MONTH
    else:
        grain = TimeGrain.DAY

    # Plan 1: Predictive Forecast (PredictiveIntent → PredictiveEngine)
    plans: list[AnalysisPlan] = [
        AnalysisPlan(
            main_intent=PredictiveIntent(
                rationale=(
                    "Proyección temporal usando Holt-Winters sobre la serie histórica completa. "
                    "Genera forecast con intervalos de confianza para períodos futuros."
                ),
                date_column=date_column,
                value_column=metric_column,
                analysis_subtype="forecast",
                grain=grain,
                horizon=6,
                metric_unit=metric_unit,
                visual_protocol=VisualProtocol.LINE,
            ),
            title=f"Proyección de {metric_label}",
            column_aliases={
                metric_column: metric_label,
                date_column: date_label,
            },
            metric_polarity=MetricPolarity.NEUTRAL,
        ),
    ]

    # Plan 2: Historical Trend (context for the forecast)
    plans.append(
        AnalysisPlan(
            main_intent=TimeTrendIntent(
                rationale=(
                    "Contexto histórico que complementa la proyección, mostrando la tendencia "
                    "real con crecimiento MoM/YoY y detección de picos/valles."
                ),
                date_column=date_column,
                value_column=metric_column,
                grain=grain,
                metric_unit=metric_unit,
                visual_protocol=VisualProtocol.LINE,
            ),
            title=f"Tendencia Histórica de {metric_label}",
            column_aliases={
                metric_column: metric_label,
                date_column: date_label,
            },
            metric_polarity=MetricPolarity.NEUTRAL,
        )
    )

    # Plan 3: Dimensional breakdown (which segments drive the metric)
    dimension_column = _resolve_shadow_dimension(prompt, candidate_df)
    if dimension_column and dimension_column != date_column:
        dimension_label = _humanize_label(dimension_column)
        plans.append(
            AnalysisPlan(
                main_intent=DistributionIntent(
                    rationale=(
                        "Descomposición dimensional que muestra qué segmentos impulsan la métrica "
                        "proyectada, para que el ejecutivo identifique drivers de cambio."
                    ),
                    dimension=dimension_column,
                    metric=metric_column,
                    limit=_default_dimension_limit(schema_profile, dimension_column),
                    metric_unit=metric_unit,
                    visual_protocol=VisualProtocol.BAR,
                ),
                title=f"Distribución de {metric_label} por {dimension_label}",
                column_aliases={
                    metric_column: metric_label,
                    dimension_column: dimension_label,
                },
                metric_polarity=MetricPolarity.NEUTRAL,
            )
        )

    if len(plans) < 2:
        return [], None, None
    return plans[:3], prompt, "shadow_predictive_parity_bundle"


def _requires_semantic_translator_delegate(
    *,
    prompt: str,
    prompt_type: str,
    inferred_visual_family: str | None,
) -> bool:
    normalized_prompt = SemanticTranslator._normalize_surface_text(prompt)
    explicit_visual_request = bool(str(inferred_visual_family or "").strip())
    is_explicit_chart_path = prompt_type in {"chart_request", "funnel_request"} and explicit_visual_request

    temporal_markers = (
        "tendencia",
        "trend",
        "histor",
        "evolucion",
        "evolución",
        "mensual",
        "semanal",
        "diario",
        "trimestral",
        "anual",
        "por mes",
        "por semana",
        "por dia",
        "por día",
        "por año",
        "timeline",
        "serie temporal",
    )
    restrictive_markers = (
        "desde",
        "hasta",
        "entre",
        "compar",
        "versus",
        " vs ",
        "filtra",
        "filtrar",
        "solo ",
        "unicamente",
        "únicamente",
        "acumulad",
        "suma total",
        "total del top",
        "total de los top",
        "totales de los",
        "sum of top",
        "total of top",
        "al corte",
        "corte",
    )
    has_temporal = any(marker in normalized_prompt for marker in temporal_markers)
    has_restriction = any(marker in normalized_prompt for marker in restrictive_markers)
    has_top_n = bool(re.search(r"\btop\s*\d{1,3}\b", normalized_prompt))
    has_rollup_top_n = has_top_n and any(
        marker in normalized_prompt
        for marker in (
            "suma total",
            "total del top",
            "total de los top",
            "totales de los",
            "sum of top",
            "total of top",
            "acumulad",
        )
    )

    if prompt_type == "expiry_window_analysis":
        return True

    if has_rollup_top_n:
        return True
    if has_temporal and (has_restriction or has_top_n):
        return True

    if is_explicit_chart_path:
        return False

    semantic_critical_markers = (
        "venc",
        "caduc",
        "expir",
        "por vencer",
        "a vencer",
        "vencimiento",
        "fecha de venc",
        "aging",
        "antiguedad",
        "antigüedad",
        "desde",
        "hasta",
        "entre",
        "as of",
        "al corte",
        "corte",
    )
    if any(marker in normalized_prompt for marker in semantic_critical_markers):
        return True

    if re.search(
        r"\b\d{1,3}\s*(dia|dias|day|days|semana|semanas|week|weeks|mes|meses|month|months)\b",
        normalized_prompt,
    ):
        return True

    if prompt_type in {
        "complete_analysis",
        "generic_analysis",
        "trend_request",
        "kpi_request",
    } and not explicit_visual_request:
        return True

    return False


def _build_shadow_strategy_bundle(
    *,
    prompt: str | None,
    prompt_type: str | None,
    candidate_df: pd.DataFrame,
    requested_visual_family: str | None = None,
) -> tuple[list[AnalysisPlan], str | None, str | None]:
    normalized_prompt = str(prompt or "").strip()
    if not normalized_prompt or not prompt_type:
        return [], None, None
    inferred_visual_family = requested_visual_family or _requested_visual_family_from_prompt(normalized_prompt)

    if _requires_semantic_translator_delegate(
        prompt=normalized_prompt,
        prompt_type=prompt_type,
        inferred_visual_family=inferred_visual_family,
    ):
        emit_structured_log(
            "canonical_shadow_strategy_delegate_to_translator",
            prompt_preview=normalized_prompt[:200],
            prompt_type=prompt_type,
            requested_visual_family=inferred_visual_family,
            reason="semantic_critical_prompt",
        )
        return [], None, None

    if (
        inferred_visual_family
        and prompt_type in {"chart_request", "dimension_analysis", "generic_analysis"}
        and inferred_visual_family not in _SHADOW_PARITY_VISUAL_FAMILIES
    ):
        explicit_advanced_plans = _build_shadow_explicit_advanced_visual_plans(
            normalized_prompt,
            candidate_df,
            requested_visual_family=inferred_visual_family,
        )
        if explicit_advanced_plans[0]:
            return explicit_advanced_plans
        emit_structured_log(
            "canonical_shadow_strategy_delegate_to_translator",
            prompt_preview=normalized_prompt[:200],
            prompt_type=prompt_type,
            requested_visual_family=inferred_visual_family,
            reason="advanced_visual_family_fallback",
        )
        return [], None, None

    if prompt_type == "chart_request":
        scatter_parity_plans = _build_shadow_scatter_visual_parity_plans(
            normalized_prompt,
            candidate_df,
        )
        if scatter_parity_plans[0]:
            return scatter_parity_plans
        if inferred_visual_family == "scatter_plot":
            return _build_shadow_scatter_visual_parity_plans(normalized_prompt, candidate_df)
        return _build_shadow_chart_visual_parity_plans(
            normalized_prompt,
            candidate_df,
            requested_visual_family=inferred_visual_family,
        )
    if prompt_type == "dimension_analysis":
        return _build_shadow_dimension_parity_plans(
            normalized_prompt,
            candidate_df,
            requested_visual_family=inferred_visual_family,
        )
    if prompt_type == "comparative_analysis":
        return _build_shadow_comparative_parity_plans(normalized_prompt, candidate_df)
    if prompt_type == "predictive_analysis":
        return _build_shadow_predictive_parity_plans(normalized_prompt, candidate_df)
    if prompt_type == "generic_analysis":
        return _build_shadow_generic_parity_plans(
            normalized_prompt,
            candidate_df,
            requested_visual_family=inferred_visual_family,
        )
    return [], None, None


def _build_topology_context(candidate_df: pd.DataFrame) -> str:
    attrs = getattr(candidate_df, "attrs", {}) or {}
    translator_context = str(attrs.get("translator_context_summary") or "").strip()
    if translator_context:
        reference_date = attrs.get("reference_date")
        emit_structured_log("topology_context_reference_date",
            has_reference_date=bool(reference_date),
            reference_date=reference_date or None)
        if reference_date:
            translator_context += (
                f"\nFECHA_REFERENCIA_DATASET: {reference_date}"
                "\nINSTRUCCIÓN: Usa FECHA_REFERENCIA_DATASET como 'hoy' para filtros temporales relativos."
            )
        return translator_context
    topology_rules = attrs.get("topology_rules", {}) or {}
    return str(topology_rules)


def _build_glossary_context(candidate_df: pd.DataFrame) -> str:
    attrs = getattr(candidate_df, "attrs", {}) or {}
    literal_catalog = attrs.get("literal_filter_catalog", {}) or {}
    if not isinstance(literal_catalog, dict) or not literal_catalog:
        return "Sin glosario."
    lines: list[str] = []
    for column_name, values in sorted(literal_catalog.items()):
        safe_values = [str(value) for value in list(values or [])[:5] if str(value or "").strip()]
        if not safe_values:
            continue
        lines.append(f"- '{column_name}': valores literales observados {safe_values}")
    return "\n".join(lines) if lines else "Sin glosario."


def _protected_columns(candidate_df: pd.DataFrame) -> list[str]:
    attrs = getattr(candidate_df, "attrs", {}) or {}
    schema_profile = attrs.get("schema_profile", {}) or {}
    contract = attrs.get("semantic_contract", {}) or {}
    protected: set[str] = set(_list_str(contract.get("dimension_columns")))
    protected.update(_list_str(contract.get("identifier_columns")))
    for column_name, info in schema_profile.items():
        if not isinstance(info, dict):
            continue
        if str(info.get("role") or "").strip().lower() in {"dimension", "identifier"}:
            protected.add(str(column_name))
    return sorted(protected)


def _shadow_file_id(file_id: str, candidate_id: str) -> str:
    return f"shadow_query_{_slugify(file_id)}_{_slugify(candidate_id)}"


def _ensure_shadow_snapshot_guard_column(candidate_df: pd.DataFrame) -> pd.DataFrame:
    working_df = candidate_df.copy()
    working_df.attrs = dict(getattr(candidate_df, "attrs", {}) or {})
    contract = _semantic_contract(working_df)
    if not bool(contract.get("snapshot_guard_allowed")):
        return working_df
    if "is_latest_snapshot" in working_df.columns:
        return working_df

    time_axis = str(contract.get("time_axis") or "").strip()
    if not time_axis or time_axis not in working_df.columns:
        return working_df

    parsed_dates = pd.to_datetime(working_df[time_axis], errors="coerce")
    if parsed_dates.isna().all():
        return working_df

    max_date = parsed_dates.max()
    working_df["is_latest_snapshot"] = parsed_dates == max_date
    return working_df


def _get_ibis_engine_cls():
    from app.services.ibis_engine import IbisEngine

    return IbisEngine


def _persist_shadow_candidate(
    candidate_df: pd.DataFrame,
    *,
    file_id: str,
    candidate_id: str,
    related_frames: dict[str, pd.DataFrame] | None = None,
) -> tuple[str | None, str | None, dict[str, str]]:
    working_df = _ensure_shadow_snapshot_guard_column(candidate_df)
    working_df.attrs["cleaning_notes"] = "shadow_query_runtime_candidate"
    shadow_file_id = _shadow_file_id(file_id, candidate_id)
    parquet_path = DataEngine.commit_to_parquet(working_df, shadow_file_id)

    # [FASE 4 MULTI-HOJA] Persistir frames relacionados
    related_paths: dict[str, str] = {}
    if related_frames and parquet_path:
        for frame_id, frame_df in related_frames.items():
            related_shadow_id = _shadow_file_id(file_id, frame_id)
            related_path = DataEngine.commit_to_parquet(frame_df, related_shadow_id)
            if related_path:
                related_paths[frame_id] = related_path

    if not parquet_path:
        return shadow_file_id, None, related_paths
    return shadow_file_id, parquet_path, related_paths


def _plan_visual_protocol(plan: AnalysisPlan) -> str | None:
    visual_protocol = getattr(plan.main_intent, "visual_protocol", None)
    if hasattr(visual_protocol, "value"):
        return str(visual_protocol.value)
    if visual_protocol is not None:
        return str(visual_protocol)
    return None


def _summarize_plan(plan: AnalysisPlan, index: int) -> dict[str, Any]:
    main_intent = plan.main_intent
    return {
        "plan_index": index,
        "title": plan.title,
        "intent_type": getattr(main_intent, "type", None),
        "visual_protocol": _plan_visual_protocol(plan),
        "column_alias_count": len(plan.column_aliases or {}),
        "glossary_hint": plan.glossary_hint,
    }


def _summarize_execution_result(plan: AnalysisPlan, result: dict[str, Any], index: int) -> dict[str, Any]:
    data_payload = result.get("data")
    if isinstance(data_payload, list):
        data_points = len(data_payload)
    elif isinstance(data_payload, dict):
        data_points = len(data_payload)
    else:
        data_points = 0
    return {
        "plan_index": index,
        "title": plan.title,
        "intent_type": getattr(plan.main_intent, "type", None),
        "visual_protocol": _plan_visual_protocol(plan),
        # [V3] empty_result es una ejecución exitosa: Ibis ejecutó el plan correctamente
        # pero no encontró datos con los filtros aplicados. No es un error de sistema.
        # Tratarlo como error activaría el Big Data Shield innecesariamente.
        "status": (
            "success"
            if not result.get("error") or result.get("error") == "empty_result"
            else "error"
        ),
        "result_type": result.get("type"),
        "chart_type": result.get("chart_type"),
        "data_points": data_points,
        "has_filtered_granular_df": "filtered_granular_df" in result,
        "error": result.get("error"),
    }


def _blocked_execution_result(
    plan: AnalysisPlan,
    *,
    index: int,
    error: str,
    blocked_metrics: list[str],
) -> dict[str, Any]:
    return {
        "plan_index": index,
        "title": plan.title,
        "intent_type": getattr(plan.main_intent, "type", None),
        "visual_protocol": _plan_visual_protocol(plan),
        "status": "blocked",
        "result_type": None,
        "chart_type": None,
        "data_points": 0,
        "has_filtered_granular_df": False,
        "error": error,
        "blocked_metrics": blocked_metrics,
    }


def _plan_metric_columns(plan: AnalysisPlan) -> list[str]:
    main_intent = plan.main_intent
    intent_type = getattr(main_intent, "type", None)
    extended_metrics = [
        str(value).strip()
        for value in (
            getattr(main_intent, "plot_metric", None),
            getattr(main_intent, "ranking_metric", None),
        )
        if str(value or "").strip()
    ]
    if intent_type == "descriptive":
        return [*extended_metrics, *[str(value) for value in list(getattr(main_intent, "metrics", []) or []) if str(value or "").strip()]]
    if intent_type == "trend":
        return [*extended_metrics, str(getattr(main_intent, "value_column", "") or "").strip()]
    if intent_type == "distribution":
        return [*extended_metrics, str(getattr(main_intent, "metric", "") or "").strip()]
    if intent_type == "diagnostic":
        metrics = [str(value) for value in list(getattr(main_intent, "metrics", []) or []) if str(value or "").strip()]
        primary_metric = str(getattr(main_intent, "metric", "") or "").strip()
        if primary_metric:
            metrics.insert(0, primary_metric)
        return [*extended_metrics, *metrics]
    if intent_type == "predictive":
        metrics = [str(getattr(main_intent, "value_column", "") or "").strip()]
        alias_metric = str(getattr(main_intent, "metric", "") or "").strip()
        if alias_metric:
            metrics.append(alias_metric)
        return [*extended_metrics, *metrics]
    return []


def _contract_metric_columns(plan: AnalysisPlan) -> set[str]:
    main_intent = plan.main_intent
    return {
        str(value).strip()
        for value in (
            getattr(main_intent, "plot_metric", None),
            getattr(main_intent, "ranking_metric", None),
        )
        if str(value or "").strip()
    }


def _is_numeric_dataframe_column(candidate_df: pd.DataFrame, column_name: str) -> bool:
    if column_name not in candidate_df.columns:
        return False
    try:
        return bool(pd.api.types.is_numeric_dtype(candidate_df[column_name]))
    except Exception:
        return False


def _blocked_plan_metrics(plan: AnalysisPlan, candidate_df: pd.DataFrame) -> list[str]:
    schema_profile = _schema_profile(candidate_df)
    contract_metrics = _contract_metric_columns(plan)
    if _plan_visual_protocol(plan) == VisualProtocol.SCATTER.value:
        blocked_metrics: list[str] = []
        safe_metric_columns = set(_safe_metric_columns(candidate_df))
        for metric in _plan_metric_columns(plan):
            if not metric:
                continue
            if metric in safe_metric_columns:
                continue
            if metric in contract_metrics and _is_numeric_dataframe_column(candidate_df, metric):
                continue
            if _column_role(schema_profile, metric) == "date":
                continue
            blocked_metrics.append(metric)
        return blocked_metrics

    safe_metric_columns = set(_shadow_safe_metric_columns(candidate_df))
    if not safe_metric_columns:
        return [
            metric
            for metric in _plan_metric_columns(plan)
            if metric and not (metric in contract_metrics and _is_numeric_dataframe_column(candidate_df, metric))
        ]
    return [
        metric
        for metric in _plan_metric_columns(plan)
        if metric
        and metric not in safe_metric_columns
        and not (metric in contract_metrics and _is_numeric_dataframe_column(candidate_df, metric))
    ]


def build_canonical_shadow_query_execution(
    *,
    file_id: str,
    pipeline_result: CanonicalDarkRuntimePipelineResult,
    prompt: str | None = None,
    service_client: Any | None = None,
    prompt_type: str | None = None,
    requested_visual_family: str | None = None,
    max_plans: int = 3,
) -> CanonicalShadowQueryExecution:
    readiness_summary = build_shadow_format_readiness_summary(
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

    candidate_df = get_selected_candidate_dataframe(pipeline_result.analytical_adapter_runtime)
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
                "shadow_query_status": "no_candidate",
                "candidate_id": None,
            },
        )

    actual_prompt, parent_task_id = unwrap_prompt_payload(prompt)
    selected_candidate_id = str(
        pipeline_result.analytical_adapter_runtime.analytical_bundle.selected_candidate_id or ""
    ).strip()
    strategy_plans, strategy_prompt, strategy_name = _build_shadow_strategy_bundle(
        prompt=actual_prompt,
        prompt_type=prompt_type,
        candidate_df=candidate_df,
        requested_visual_family=requested_visual_family,
    )
    query_prompt, prompt_strategy = (strategy_prompt, strategy_name) if strategy_plans else (
        (actual_prompt, "custom_prompt") if actual_prompt else _build_shadow_prompt(candidate_df)
    )
    if not query_prompt or not prompt_strategy:
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
                "shadow_query_status": "no_prompt_strategy",
                "candidate_id": selected_candidate_id,
            },
        )

    schema_profile = getattr(candidate_df, "attrs", {}).get("schema_profile", {}) or {}
    dataset_contract = getattr(candidate_df, "attrs", {}).get("semantic_contract", {}) or {}
    parent_context = load_parent_analysis_context(
        service_client=service_client,
        parent_task_id=parent_task_id,
        file_id=file_id,
        columns=list(candidate_df.columns),
    )
    plans = strategy_plans or (
        SemanticTranslator.translate(
            query_prompt,
            list(candidate_df.columns),
            _build_glossary_context(candidate_df),
            _build_topology_context(candidate_df),
            memory_context=build_parent_memory_context_text(parent_context),
            schema_profile=schema_profile,
            dataset_contract=dataset_contract,
        ) or []
    )
    plans = apply_parent_context_to_placeholder_filters(
        plans=plans,
        parent_context=parent_context,
    )
    bounded_plans = list(plans[: max(int(max_plans or 0), 1)])
    plan_summaries = [_summarize_plan(plan, index + 1) for index, plan in enumerate(bounded_plans)]

    shadow_file_id, parquet_path, _related_paths = _persist_shadow_candidate(
        candidate_df,
        file_id=file_id,
        candidate_id=selected_candidate_id,
    )
    execution_summaries: list[dict[str, Any]] = []
    execution_results: list[dict[str, Any]] = []
    if parquet_path:
        protected_cols = _protected_columns(candidate_df)
        ibis_engine_cls = _get_ibis_engine_cls()
        for index, plan in enumerate(bounded_plans, start=1):
            blocked_metrics = _blocked_plan_metrics(plan, candidate_df)
            if blocked_metrics:
                blocked_result = _blocked_execution_result(
                    plan,
                    index=index,
                    error=(
                        "Shadow Metric Guard bloqueó el plan: las métricas "
                        f"{blocked_metrics} no son agregables en el contrato paralelo."
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
            )
            execution_summaries.append(_summarize_execution_result(plan, result, index))
            execution_results.append(dict(result) if isinstance(result, dict) else {"error": "invalid_execution_result"})

    success_count = sum(1 for row in execution_summaries if row.get("status") == "success")
    shadow_query_status = (
        "query_executed"
        if execution_summaries and success_count == len(execution_summaries)
        else "partial_query_success"
        if execution_summaries and success_count > 0
        else "query_failed"
        if bounded_plans
        else "no_plans"
    )

    emit_structured_log(
        "canonical_shadow_query_executed",
        file_id=file_id,
        candidate_id=selected_candidate_id,
        readiness_grade=readiness_summary.get("readiness_grade"),
        prompt_strategy=prompt_strategy,
        plan_count=len(bounded_plans),
        success_count=success_count,
        shadow_query_status=shadow_query_status,
    )

    return CanonicalShadowQueryExecution(
        pipeline_result=pipeline_result,
        readiness_summary=readiness_summary,
        query_prompt=query_prompt,
        prompt_strategy=prompt_strategy,
        plans=bounded_plans,
        plan_summaries=plan_summaries,
        execution_summaries=execution_summaries,
        execution_results=execution_results,
        metadata={
            "file_id": file_id,
            "candidate_id": selected_candidate_id,
            "shadow_file_id": shadow_file_id,
            "shadow_parquet_path": parquet_path,
            "shadow_query_status": shadow_query_status,
            "parent_task_id": parent_task_id,
            "parent_context_filter_count": len(list((parent_context or {}).get("filters") or [])),
        },
    )


def run_canonical_shadow_query_for_uploaded_file(
    *,
    file_id: str,
    service_client: Any,
    uploaded_file_row: dict[str, Any] | None = None,
    mime_type: str | None = None,
    prompt: str | None = None,
    prompt_type: str | None = None,
    requested_visual_family: str | None = None,
    max_plans: int = 3,
) -> CanonicalShadowQueryExecution:
    pipeline_result = run_canonical_dark_pipeline_for_uploaded_file(
        file_id=file_id,
        service_client=service_client,
        uploaded_file_row=uploaded_file_row,
        mime_type=mime_type,
    )
    return build_canonical_shadow_query_execution(
        file_id=file_id,
        pipeline_result=pipeline_result,
        prompt=prompt,
        service_client=service_client,
        prompt_type=prompt_type,
        requested_visual_family=requested_visual_family,
        max_plans=max_plans,
    )


def summarize_canonical_shadow_query_execution(
    execution: CanonicalShadowQueryExecution,
) -> dict[str, Any]:
    return {
        "file_id": execution.metadata.get("file_id"),
        "candidate_id": execution.metadata.get("candidate_id"),
        "readiness_grade": execution.readiness_summary.get("readiness_grade"),
        "readiness_score": execution.readiness_summary.get("readiness_score"),
        "query_prompt": execution.query_prompt,
        "prompt_strategy": execution.prompt_strategy,
        "plan_count": len(execution.plans),
        "successful_plan_count": sum(1 for row in execution.execution_summaries if row.get("status") == "success"),
        "shadow_query_status": execution.metadata.get("shadow_query_status"),
        "shadow_parquet_path": execution.metadata.get("shadow_parquet_path"),
        "plans": execution.plan_summaries,
        "executions": execution.execution_summaries,
    }
