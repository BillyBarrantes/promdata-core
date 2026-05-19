from __future__ import annotations

from typing import Any

import math
import pandas as pd


def _humanize_column(column_name: str | None, aliases: dict[str, str]) -> str | None:
    if not column_name:
        return None
    alias = aliases.get(column_name)
    if alias:
        return alias
    text = str(column_name).replace("_", " ").strip()
    return text[:1].upper() + text[1:] if text else None


def _coerce_numeric(series: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(series):
        numeric = series.astype(float)
    else:
        numeric = pd.to_numeric(series, errors="coerce")
    return numeric.replace([float("inf"), float("-inf")], pd.NA).dropna()


def _resolve_target_metric(intent: Any, schema_profile: dict[str, Any], df: pd.DataFrame) -> str | None:
    raw_candidates = []
    for attr in ("metric", "value_column"):
        value = getattr(intent, attr, None)
        if value:
            raw_candidates.append(value)
    for value in list(getattr(intent, "metrics", None) or []):
        if value and value not in raw_candidates:
            raw_candidates.append(value)

    for candidate in raw_candidates:
        role = schema_profile.get(candidate, {}).get("role")
        if candidate in df.columns and role == "metric":
            return str(candidate)
    return None


def _resolve_dimension_candidates(intent: Any, schema_profile: dict[str, Any], df: pd.DataFrame) -> list[str]:
    preferred = []
    for attr in ("dimension", "date_column"):
        value = getattr(intent, attr, None)
        if value:
            preferred.append(str(value))
    for value in list(getattr(intent, "group_by", None) or []):
        if value:
            preferred.append(str(value))

    ordered = []
    seen: set[str] = set()
    for candidate in preferred:
        if candidate in df.columns and candidate not in seen:
            ordered.append(candidate)
            seen.add(candidate)

    for column_name, info in schema_profile.items():
        if column_name in seen or column_name not in df.columns:
            continue
        if info.get("role") in {"dimension", "date", "identifier"}:
            ordered.append(str(column_name))
            seen.add(str(column_name))

    return ordered


def _aggregate_metric(grouped: pd.core.groupby.SeriesGroupBy, aggregation: str) -> pd.Series:
    if aggregation == "avg":
        return grouped.mean()
    if aggregation == "count":
        return grouped.count()
    if aggregation == "max":
        return grouped.max()
    if aggregation == "min":
        return grouped.min()
    return grouped.sum()


def _build_segment_pressure(
    *,
    intent: Any,
    aliases: dict[str, str],
    schema_profile: dict[str, Any],
    df: pd.DataFrame,
    target_metric: str,
) -> dict[str, Any] | None:
    aggregation = str(getattr(intent, "aggregation", "sum") or "sum")
    for dimension in _resolve_dimension_candidates(intent, schema_profile, df):
        if dimension == target_metric:
            continue
        series = df[dimension].dropna()
        unique_count = int(series.nunique())
        if unique_count < 2 or unique_count > 60:
            continue

        frame = df[[dimension, target_metric]].copy()
        frame[target_metric] = pd.to_numeric(frame[target_metric], errors="coerce")
        frame = frame.dropna(subset=[target_metric, dimension])
        if frame.empty:
            continue

        grouped = _aggregate_metric(frame.groupby(dimension)[target_metric], aggregation).sort_values(ascending=False)
        if grouped.empty:
            continue

        total = float(grouped[grouped > 0].sum())
        if total <= 0:
            total = float(grouped.abs().sum())
        if total <= 0:
            continue

        top_segments = []
        for rank, (segment_name, segment_value) in enumerate(grouped.head(3).items(), start=1):
            share_pct = round(float(segment_value) / total * 100, 2)
            top_segments.append(
                {
                    "rank": rank,
                    "name": str(segment_name),
                    "value": round(float(segment_value), 4),
                    "share_pct": share_pct,
                }
            )

        if not top_segments:
            continue

        leader_share = float(top_segments[0]["share_pct"])
        concentration_score = round(max(0.0, min(1.0, leader_share / 60.0)), 2)
        dimension_label = _humanize_column(dimension, aliases) or str(dimension)
        return {
            "dimension": dimension,
            "dimension_label": dimension_label,
            "concentration_score": concentration_score,
            "top_segments": top_segments,
            "summary": f"El segmento líder en {dimension_label} concentra {round(leader_share, 1)}% de la señal analizada.",
        }
    return None


def _build_segment_divergence(
    *,
    intent: Any,
    aliases: dict[str, str],
    schema_profile: dict[str, Any],
    df: pd.DataFrame,
    target_metric: str,
) -> dict[str, Any] | None:
    aggregation = str(getattr(intent, "aggregation", "sum") or "sum")
    for dimension in _resolve_dimension_candidates(intent, schema_profile, df):
        if dimension == target_metric:
            continue
        series = df[dimension].dropna()
        unique_count = int(series.nunique())
        if unique_count < 3 or unique_count > 60:
            continue

        frame = df[[dimension, target_metric]].copy()
        frame[target_metric] = pd.to_numeric(frame[target_metric], errors="coerce")
        frame = frame.dropna(subset=[target_metric, dimension])
        if frame.empty:
            continue

        grouped = _aggregate_metric(frame.groupby(dimension)[target_metric], aggregation).sort_values(ascending=False)
        if len(grouped) < 3:
            continue

        values = grouped.astype(float)
        median_value = float(values.median()) if len(values) else 0.0
        leader_name = str(grouped.index[0])
        leader_value = float(grouped.iloc[0])
        tail_name = str(grouped.index[-1])
        tail_value = float(grouped.iloc[-1])

        if median_value <= 0 or leader_value <= 0:
            continue

        leader_to_median_ratio = leader_value / median_value
        leader_to_tail_ratio = (leader_value / tail_value) if tail_value > 0 else None
        divergence_score = round(max(0.0, min(1.0, (leader_to_median_ratio - 1.0) / 2.5)), 2)
        dimension_label = _humanize_column(dimension, aliases) or str(dimension)

        return {
            "dimension": dimension,
            "dimension_label": dimension_label,
            "leader_segment": leader_name,
            "leader_value": round(leader_value, 4),
            "median_value": round(median_value, 4),
            "tail_segment": tail_name,
            "tail_value": round(tail_value, 4),
            "leader_to_median_ratio": round(leader_to_median_ratio, 2),
            "leader_to_tail_ratio": round(leader_to_tail_ratio, 2) if leader_to_tail_ratio is not None and math.isfinite(leader_to_tail_ratio) else None,
            "divergence_score": divergence_score,
            "summary": f"{leader_name} se distancia de la mediana de {dimension_label} en {round(leader_to_median_ratio, 2)}x.",
        }
    return None


def _build_driver_relations(
    *,
    aliases: dict[str, str],
    schema_profile: dict[str, Any],
    df: pd.DataFrame,
    target_metric: str,
) -> list[dict[str, Any]]:
    target_series = _coerce_numeric(df[target_metric])
    if target_series.empty or float(target_series.std(ddof=0) or 0.0) == 0.0:
        return []

    relations: list[dict[str, Any]] = []
    metric_candidates = [
        column_name
        for column_name, info in schema_profile.items()
        if column_name in df.columns
        and column_name != target_metric
        and info.get("role") == "metric"
    ]

    for column_name in metric_candidates:
        candidate_frame = df[[target_metric, column_name]].copy()
        candidate_frame[target_metric] = pd.to_numeric(candidate_frame[target_metric], errors="coerce")
        candidate_frame[column_name] = pd.to_numeric(candidate_frame[column_name], errors="coerce")
        candidate_frame = candidate_frame.dropna()
        if len(candidate_frame) < 8:
            continue
        if float(candidate_frame[column_name].std(ddof=0) or 0.0) == 0.0:
            continue

        corr = float(candidate_frame[target_metric].corr(candidate_frame[column_name]))
        if not math.isfinite(corr):
            continue

        abs_corr = abs(corr)
        if abs_corr < 0.35:
            continue

        strength = "strong" if abs_corr >= 0.75 else "moderate" if abs_corr >= 0.55 else "emerging"
        relations.append(
            {
                "column": column_name,
                "label": _humanize_column(column_name, aliases) or str(column_name),
                "correlation": round(corr, 3),
                "direction": "positive" if corr >= 0 else "negative",
                "strength": strength,
                "support_size": int(len(candidate_frame)),
            }
        )

    return sorted(relations, key=lambda item: abs(float(item["correlation"])), reverse=True)[:3]


def build_enterprise_diagnostic_context(
    *,
    plan: Any,
    granular_df: pd.DataFrame | None,
    schema_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if granular_df is None or granular_df.empty:
        return {}

    schema_profile = schema_profile or {}
    intent = getattr(plan, "main_intent", None)
    if not intent:
        return {}

    target_metric = _resolve_target_metric(intent, schema_profile, granular_df)
    if not target_metric:
        return {}

    aliases = getattr(plan, "column_aliases", {}) or {}
    segment_pressure = _build_segment_pressure(
        intent=intent,
        aliases=aliases,
        schema_profile=schema_profile,
        df=granular_df,
        target_metric=target_metric,
    )
    segment_divergence = _build_segment_divergence(
        intent=intent,
        aliases=aliases,
        schema_profile=schema_profile,
        df=granular_df,
        target_metric=target_metric,
    )
    driver_relations = _build_driver_relations(
        aliases=aliases,
        schema_profile=schema_profile,
        df=granular_df,
        target_metric=target_metric,
    )

    if not segment_pressure and not segment_divergence and not driver_relations:
        return {}

    return {
        "target_metric": target_metric,
        "target_metric_label": _humanize_column(target_metric, aliases) or str(target_metric),
        "segment_pressure": segment_pressure,
        "segment_divergence": segment_divergence,
        "driver_relations": driver_relations,
    }
