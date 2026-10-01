"""
[MEJORA 4 2026-09] Intención determinista de "vencimientos" (expiry).

Antes de esta mejora, producción dependía 100% del LLM para analizar
"productos a vencer": el clasificador `expiry_window_analysis` existía solo en
el shadow y el traductor debía acertar columna + horizonte + corte.

Este módulo construye un bundle determinista (Triple Vista) cuando detecta
intención de vencimiento, sin depender del LLM:
  - columna de vencimiento: data-driven (fecha accesoria futura) + vocabulario
    semántico de dominio (venc/caduc/expir/prefercons),
  - ventana: "próximos/últimos N" (anclada al reference_date) o, si no hay
    horizonte, todas las caducidades futuras desde el corte,
  - corte actual: garantizado por el snapshot guard (ADR-TEMPORAL-001).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from app.core.semantic_grammar import (
    AnalysisPlan,
    DataFilter,
    DescriptiveIntent,
    DistributionIntent,
    MetricPolarity,
    MetricUnit,
    TimeTrendIntent,
    VisualProtocol,
)
from app.services.metric_semantics import infer_metric_unit_from_column_name
from app.services.semantic_translator.core import (
    contains_concrete_temporal_specifier,
    humanize_column_alias,
    pick_best_dimension_column,
)
from app.services.semantic_translator.temporal_resolver import (
    detect_relative_window,
)

# Vocabulario semántico de dominio (no específico de un usuario/dataset).
_EXPIRY_TOKENS = (
    "venc", "caduc", "expir", "prefercons", "consumo preferente",
    "consume preferente", "fecha de caduc", "fecha venc",
)
# Palabras que indican una cota temporal EXPLÍCITA (no relativa). Si aparecen y
# no hay ventana relativa, se delega al LLM (que ya resuelve fechas exactas).
_EXPLICIT_DATE_KEYWORDS = (
    "hasta", "desde", "entre", "antes de", "posterior", "previo", "al corte",
    "a la fecha", "al ",
)


def _fold(text: str) -> str:
    lowered = str(text or "").lower()
    for src, dst in (
        ("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u"),
        ("ü", "u"), ("ñ", "n"),
    ):
        lowered = lowered.replace(src, dst)
    return lowered


def is_expiry_request(prompt: str, prompt_type: str | None = None) -> bool:
    """True si el prompt pide un análisis de vencimientos/caducidad."""
    if prompt_type == "expiry_window_analysis":
        return True
    text = _fold(prompt)
    return any(token in text for token in _EXPIRY_TOKENS)


def resolve_expiry_column(
    candidate_df: pd.DataFrame,
    schema_profile: dict[str, Any],
    dataset_contract: dict[str, Any],
    reference_date: str | None,
) -> str | None:
    """Resuelve la columna de vencimiento de forma data-driven + semántica.

    Candidatas: columnas de fecha distintas del eje de corte (event_axes).
    Se prioriza la que tiene mayor proporción de valores FUTUROS respecto al
    reference_date; se desempata por vocabulario de dominio y cardinalidad.
    """
    axis = str(
        dataset_contract.get("snapshot_axis")
        or dataset_contract.get("time_axis")
        or ""
    ).strip()

    # [Fase 3 2026-09] Consume el orphan `event_axes` del contrato L1: son las
    # fechas accesorias (distintas del eje de corte) y ya vienen calculadas por
    # `data_engine`. Se prefieren; si el contrato no las trae (legacy/sidecar
    # viejo), se reconstruyen desde `date_columns` como antes.
    event_axes = [
        c for c in (dataset_contract.get("event_axes") or [])
        if c in candidate_df.columns
    ]
    if event_axes:
        candidates = [c for c in event_axes if c != axis] or list(event_axes)
    else:
        date_cols = [
            c for c in (dataset_contract.get("date_columns") or [])
            if c in candidate_df.columns
        ]
        if not date_cols:
            return None
        candidates = [c for c in date_cols if c != axis]
        if not candidates:
            candidates = list(date_cols)

    ref = pd.to_datetime(reference_date, errors="coerce") if reference_date else pd.NaT

    def _future_ratio(col: str) -> float:
        if pd.isna(ref):
            return 0.0
        series = pd.to_datetime(candidate_df[col], errors="coerce").dropna()
        if series.empty:
            return 0.0
        return float((series > ref).mean())

    def _token_hit(col: str) -> int:
        return 1 if any(t in _fold(col) for t in _EXPIRY_TOKENS) else 0

    def _cardinality(col: str) -> int:
        return int(schema_profile.get(col, {}).get("cardinality") or 0)

    ranked = sorted(
        candidates,
        key=lambda c: (_future_ratio(c), _token_hit(c), _cardinality(c)),
        reverse=True,
    )
    return ranked[0] if ranked else None


def _relative_bounds(prompt: str, reference_date: str | None) -> tuple[str, str] | None:
    window = detect_relative_window(prompt)
    if not window or not reference_date:
        return None
    try:
        anchor = datetime.fromisoformat(str(reference_date)).date()
    except (ValueError, TypeError):
        return None
    quantity = int(window.get("quantity") or 0)
    unit = str(window.get("unit") or "")
    if quantity <= 0:
        return None
    if unit == "mes":
        from dateutil.relativedelta import relativedelta
        delta = relativedelta(months=quantity)
    elif unit == "semana":
        delta = timedelta(days=7 * quantity)
    else:
        delta = timedelta(days=quantity)
    if window.get("direction") == "next":
        return anchor.isoformat(), (anchor + delta).isoformat()
    return (anchor - delta).isoformat(), anchor.isoformat()


def resolve_expiry_bounds(
    prompt: str,
    reference_date: str | None,
) -> tuple[tuple[str, str | None], str] | None:
    """Resuelve la ventana de vencimiento.

    Retorna ((lo, hi|None), kind) o None si NO se debe construir de forma
    determinista (hay una cota exacta explícita que el LLM resuelve mejor).
    """
    if not reference_date:
        return None
    relative = _relative_bounds(prompt, reference_date)
    if relative:
        return relative, "relative"

    folded = _fold(prompt)
    # Si hay una cota temporal explícita (hasta/desde/entre/fecha) sin ventana
    # relativa, delegamos al LLM para no sobre-incluir.
    if any(kw in folded for kw in _EXPLICIT_DATE_KEYWORDS):
        return None
    if contains_concrete_temporal_specifier(folded):
        return None

    # "productos pronto a vencer" sin horizonte → todas las caducidades futuras.
    return (reference_date, None), "open"


def _build_filters(expiry_col: str, bound: tuple[str, str | None]) -> list[DataFilter]:
    low, high = bound
    filters: list[DataFilter] = [
        DataFilter.model_validate(
            {"column": expiry_col, "operator": ">=", "value": low}
        )
    ]
    if high:
        filters.append(
            DataFilter.model_validate(
                {"column": expiry_col, "operator": "<=", "value": high}
            )
        )
    return filters


def build_expiry_analysis_bundle(
    *,
    prompt: str,
    columns: list[str],
    schema_profile: dict[str, Any],
    dataset_contract: dict[str, Any],
    candidate_df: pd.DataFrame,
    reference_date: str | None,
    prompt_type: str | None = None,
    max_plans: int = 3,
) -> list[AnalysisPlan] | None:
    """Construye el bundle determinista de vencimientos, o None si no aplica."""
    if candidate_df is None or candidate_df.empty:
        return None
    if not is_expiry_request(prompt, prompt_type=prompt_type):
        return None

    # [Fase 1.4 2026-09] El visual explícito del usuario es ADVISORY, no un
    # interruptor: antes, una palabra como "combinado"/"barras" abortaba el
    # bundle determinista y la semántica de vencimiento (columna + ventana) se
    # delegaba al LLM, que la trataba como una distribución genérica. Ahora el
    # bundle SIEMPRE corre; el visual se aplica después (choke point
    # `finalize_plans`), que concede el combo vía serie derivada cuando aplica.

    resolved = resolve_expiry_bounds(prompt, reference_date)
    if not resolved:
        return None
    bound, _kind = resolved

    expiry_col = resolve_expiry_column(
        candidate_df, schema_profile, dataset_contract, reference_date
    )
    if not expiry_col:
        return None

    metric = None
    contract_metrics = [
        str(m) for m in (dataset_contract.get("metric_columns") or []) if str(m or "").strip()
    ]
    if contract_metrics:
        metric = contract_metrics[0]
    if not metric:
        try:
            from app.services.semantic_translator.validator import infer_default_metric_column
            metric = infer_default_metric_column(prompt, columns, schema_profile=schema_profile)
        except Exception:
            metric = None
    if not metric:
        return None

    metric_unit = infer_metric_unit_from_column_name(metric)
    if not isinstance(metric_unit, MetricUnit):
        metric_unit = MetricUnit.NUMBER
    metric_label = humanize_column_alias(metric)
    expiry_label = humanize_column_alias(expiry_col)
    filters = _build_filters(expiry_col, bound)

    plans: list[AnalysisPlan] = []
    plans.append(
        AnalysisPlan(
            main_intent=DescriptiveIntent(
                rationale="KPI de stock en riesgo: total disponible que vence en la ventana solicitada.",
                filters=filters,
                metrics=[metric],
                metric_unit=metric_unit,
                aggregation="sum",
                visual_protocol=VisualProtocol.KPI,
            ),
            title=f"{metric_label} por vencer",
            column_aliases={metric: metric_label},
            metric_polarity=MetricPolarity.UNFAVORABLE,
        )
    )

    dimension = pick_best_dimension_column(
        prompt, columns, schema_profile=schema_profile, exclude={expiry_col, metric},
    )
    if dimension:
        dim_label = humanize_column_alias(dimension)
        cardinality = int(schema_profile.get(dimension, {}).get("cardinality") or 0)
        limit = cardinality if 0 < cardinality <= 12 else 10
        plans.append(
            AnalysisPlan(
                main_intent=DistributionIntent(
                    rationale="Concentración de los productos por vencer para priorizar la reposición.",
                    filters=filters,
                    dimension=dimension,
                    metric=metric,
                    limit=limit,
                    metric_unit=metric_unit,
                    visual_protocol=VisualProtocol.BAR,
                ),
                title=f"Por vencer por {dim_label}",
                column_aliases={metric: metric_label, dimension: dim_label},
                metric_polarity=MetricPolarity.UNFAVORABLE,
            )
        )

    plans.append(
        AnalysisPlan(
            main_intent=TimeTrendIntent(
                rationale="Calendario de vencimientos por periodo dentro de la ventana (aislado al corte actual).",
                date_column=expiry_col,
                value_column=metric,
                metric_unit=metric_unit,
                visual_protocol=VisualProtocol.LINE,
                filters=filters,
            ),
            title=f"Vencimientos por {expiry_label}",
            column_aliases={metric: metric_label, expiry_col: expiry_label},
            metric_polarity=MetricPolarity.UNFAVORABLE,
        )
    )

    return plans[: max(int(max_plans or 3), 1)]
