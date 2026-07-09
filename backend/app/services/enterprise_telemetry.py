from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.supabase_client import get_supabase_service_client
from app.core.structured_logging import emit_structured_log


TELEMETRY_VERSION = "phase6.v1"
TELEMETRY_EVENT_SOURCE = "backend"
TELEMETRY_TABLE = "enterprise_telemetry_events"


def _normalize_text(value: Any) -> str:
    return str(value or "").replace("\x00", "").strip()


def _coerce_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _coerce_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _safe_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _series_visual_type(option: dict[str, Any]) -> str | None:
    # FIRST: Look in visual_source_payload.chart_type (APPLIED type from ChartFactory conversion)
    vsp = option.get("visual_source_payload")
    if isinstance(vsp, dict):
        applied_type = _normalize_text(vsp.get("chart_type"))
        if applied_type:
            return applied_type

    governance = option.get("visual_governance")
    if isinstance(governance, dict):
        for key in ("applied_visual", "requested_visual", "recommended_visual"):
            visual_type = _normalize_text(governance.get(key))
            if visual_type:
                return visual_type

    series = option.get("series")
    if not isinstance(series, list) or not series:
        return None

    primary_series = series[0] if isinstance(series[0], dict) else {}
    series_type = _normalize_text(primary_series.get("type"))
    if not series_type:
        return None

    if series_type == "pie":
        radius = primary_series.get("radius")
        if isinstance(radius, list) and len(radius) >= 2:
            return "donut_chart"
        return "pie_chart"

    return series_type


def _unique_visual_types(chart_options: Any) -> list[str]:
    visual_types: list[str] = []
    for option in _safe_list(chart_options):
        if not isinstance(option, dict):
            continue
        visual_type = _series_visual_type(option)
        if visual_type and visual_type not in visual_types:
            visual_types.append(visual_type)
    return visual_types


def _quality_note_count(cleaning_notes: Any) -> int:
    if isinstance(cleaning_notes, list):
        return len(cleaning_notes)
    if isinstance(cleaning_notes, dict):
        return len(cleaning_notes)
    return 0


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _coerce_metric_float(value: Any) -> float:
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def _normalize_dimension_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, dict):
        return {
            _normalize_text(key): _normalize_dimension_value(inner_value)
            for key, inner_value in value.items()
            if _normalize_text(key)
        }
    if isinstance(value, (list, tuple, set)):
        return [_normalize_dimension_value(item) for item in value]
    return _normalize_text(value)


def _normalize_dimensions(dimensions: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, value in dimensions.items():
        normalized_key = _normalize_text(key)
        if not normalized_key:
            continue
        normalized[normalized_key] = _normalize_dimension_value(value)
    return normalized


def _build_enterprise_metric_payload(
    *,
    metric_domain: str,
    metric_name: str,
    metric_value: int | float,
    metric_unit: str,
    dimensions: dict[str, Any],
) -> dict[str, Any]:
    return {
        "telemetry_version": TELEMETRY_VERSION,
        "event_source": TELEMETRY_EVENT_SOURCE,
        "metric_domain": _normalize_text(metric_domain) or "unknown",
        "metric_name": _normalize_text(metric_name) or "unknown",
        "metric_value": _coerce_metric_float(metric_value),
        "metric_unit": _normalize_text(metric_unit) or "count",
        "user_id": dimensions.get("user_id"),
        "team_id": dimensions.get("team_id"),
        "dimensions": dimensions,
    }


def _persist_enterprise_metric_event(
    *,
    metric_domain: str,
    metric_name: str,
    metric_value: int | float,
    metric_unit: str,
    dimensions: dict[str, Any],
) -> None:
    payload = _build_enterprise_metric_payload(
        metric_domain=metric_domain,
        metric_name=metric_name,
        metric_value=metric_value,
        metric_unit=metric_unit,
        dimensions=dimensions,
    )

    try:
        get_supabase_service_client().table(TELEMETRY_TABLE).insert(payload).execute()
    except Exception as exc:
        emit_structured_log(
            "enterprise_metric_persist_error",
            level="warning",
            metric_domain=payload["metric_domain"],
            metric_name=payload["metric_name"],
            error=str(exc)[:240],
        )


def _persist_enterprise_metric_events_batch(payloads: list[dict[str, Any]]) -> None:
    if not payloads:
        return
    try:
        get_supabase_service_client().table(TELEMETRY_TABLE).insert(payloads).execute()
        return
    except Exception as exc:
        emit_structured_log(
            "enterprise_metric_batch_persist_error",
            level="warning",
            batch_size=len(payloads),
            error=str(exc)[:240],
        )
    for payload in payloads:
        try:
            get_supabase_service_client().table(TELEMETRY_TABLE).insert(payload).execute()
        except Exception as fallback_exc:
            emit_structured_log(
                "enterprise_metric_persist_error",
                level="warning",
                metric_domain=_normalize_text(payload.get("metric_domain")) or "unknown",
                metric_name=_normalize_text(payload.get("metric_name")) or "unknown",
                error=str(fallback_exc)[:240],
            )


def _round_ratio(value: float | None, digits: int = 4) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def _count_metric_events(
    metric_rows: list[dict[str, Any]],
    metric_name: str,
    *,
    dimension_key: str | None = None,
) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for row in metric_rows:
        if _normalize_text(row.get("metric_name")) != metric_name:
            continue
        dimensions = _safe_dict(row.get("dimensions"))
        if dimension_key:
            bucket = _normalize_text(dimensions.get(dimension_key)) or "unknown"
        else:
            bucket = metric_name
        counts[bucket] += int(round(_coerce_metric_float(row.get("metric_value"))))
    return dict(counts)


def _average_metric_value(metric_rows: list[dict[str, Any]], metric_name: str) -> float | None:
    values = [
        _coerce_metric_float(row.get("metric_value"))
        for row in metric_rows
        if _normalize_text(row.get("metric_name")) == metric_name
    ]
    if not values:
        return None
    return _round_ratio(sum(values) / len(values))


def _sum_metric_value(metric_rows: list[dict[str, Any]], metric_name: str) -> int:
    total = sum(
        _coerce_metric_float(row.get("metric_value"))
        for row in metric_rows
        if _normalize_text(row.get("metric_name")) == metric_name
    )
    return int(round(total))


def _top_dimension_counts(
    metric_rows: list[dict[str, Any]],
    metric_name: str,
    *,
    dimension_key: str,
    limit: int = 5,
) -> list[dict[str, int | str]]:
    raw_counts = _count_metric_events(metric_rows, metric_name, dimension_key=dimension_key)
    ordered = sorted(raw_counts.items(), key=lambda item: (-item[1], item[0]))
    return [
        {"key": key, "count": count}
        for key, count in ordered[:limit]
    ]


def _average_metric_value_by_dimension(
    metric_rows: list[dict[str, Any]],
    metric_name: str,
    *,
    dimension_key: str,
) -> dict[str, float]:
    buckets: dict[str, list[float]] = defaultdict(list)
    for row in metric_rows:
        if _normalize_text(row.get("metric_name")) != metric_name:
            continue
        dimensions = _safe_dict(row.get("dimensions"))
        bucket = _normalize_text(dimensions.get(dimension_key)) or "unknown"
        buckets[bucket].append(_coerce_metric_float(row.get("metric_value")))
    return {
        bucket: _round_ratio(sum(values) / len(values))
        for bucket, values in buckets.items()
        if values
    }


def _top_count_items(counts: dict[str, int], *, limit: int = 5) -> list[dict[str, int | str]]:
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [
        {"key": key, "count": count}
        for key, count in ordered[:limit]
    ]


def _top_average_items(
    averages: dict[str, float],
    *,
    limit: int = 5,
) -> list[dict[str, float | str]]:
    ordered = sorted(
        (
            (key, value)
            for key, value in averages.items()
            if value is not None
        ),
        key=lambda item: (-float(item[1]), item[0]),
    )
    return [
        {"key": key, "avg_ms": float(value)}
        for key, value in ordered[:limit]
    ]


def _task_id_from_dimensions(row: dict[str, Any]) -> str:
    return _normalize_text(_safe_dict(row.get("dimensions")).get("task_id"))


def _coerce_iso_datetime(value: Any) -> datetime | None:
    text = _normalize_text(value)
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except Exception:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _bucket_shadow_runtime_rows(
    metric_rows: list[dict[str, Any]],
    *,
    metric_name: str,
    allowed_task_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in metric_rows:
        if _normalize_text(row.get("metric_name")) != metric_name:
            continue
        task_id = _task_id_from_dimensions(row)
        if allowed_task_ids and task_id not in allowed_task_ids:
            continue
        rows.append(row)
    return rows


def _filter_shadow_runtime_rows_since(
    metric_rows: list[dict[str, Any]],
    *,
    since_utc: datetime | None,
) -> list[dict[str, Any]]:
    if since_utc is None:
        return list(metric_rows)
    filtered: list[dict[str, Any]] = []
    for row in metric_rows:
        created_at = _coerce_iso_datetime(row.get("created_at"))
        if created_at is None:
            continue
        if created_at >= since_utc:
            filtered.append(row)
    return filtered


def _build_shadow_runtime_summary_from_rows(
    *,
    observed_rows: list[dict[str, Any]],
    duration_rows: list[dict[str, Any]],
    alignment_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    def _build_bucket_summary(bucket_key: str) -> list[dict[str, Any]]:
        buckets: dict[str, dict[str, Any]] = {}

        for row in observed_rows:
            dimensions = _safe_dict(row.get("dimensions"))
            bucket = _normalize_text(dimensions.get(bucket_key)) or "unknown"
            entry = buckets.setdefault(
                bucket,
                {
                    "bucket": bucket,
                    "observed_count": 0,
                    "alignment_counts": defaultdict(int),
                    "shadow_query_status_counts": defaultdict(int),
                    "mismatch_total": 0,
                    "live_primary_visual_counts": defaultdict(int),
                    "shadow_primary_visual_counts": defaultdict(int),
                    "requested_visual_counts": defaultdict(int),
                    "duration_values": [],
                    "ratio_values": [],
                },
            )
            event_count = max(int(round(_coerce_metric_float(row.get("metric_value")))), 1)
            alignment_grade = _normalize_text(dimensions.get("alignment_grade")) or "unknown"
            shadow_query_status = _normalize_text(dimensions.get("shadow_query_status")) or "unknown"
            mismatch_count = max(_coerce_int(dimensions.get("mismatch_count")), 0)
            live_primary_visual = _normalize_text(dimensions.get("live_primary_visual")) or "unknown"
            shadow_primary_visual = _normalize_text(dimensions.get("shadow_primary_visual")) or "unknown"
            requested_visual_family = _normalize_text(dimensions.get("requested_visual_family")) or "unknown"

            entry["observed_count"] += event_count
            entry["alignment_counts"][alignment_grade] += event_count
            entry["shadow_query_status_counts"][shadow_query_status] += event_count
            entry["mismatch_total"] += mismatch_count * event_count
            entry["live_primary_visual_counts"][live_primary_visual] += event_count
            entry["shadow_primary_visual_counts"][shadow_primary_visual] += event_count
            entry["requested_visual_counts"][requested_visual_family] += event_count

        for row in duration_rows:
            dimensions = _safe_dict(row.get("dimensions"))
            bucket = _normalize_text(dimensions.get(bucket_key)) or "unknown"
            if bucket not in buckets:
                continue
            buckets[bucket]["duration_values"].append(_coerce_metric_float(row.get("metric_value")))

        for row in alignment_rows:
            dimensions = _safe_dict(row.get("dimensions"))
            bucket = _normalize_text(dimensions.get(bucket_key)) or "unknown"
            if bucket not in buckets:
                continue
            shadow_over_live_ratio = _coerce_float(dimensions.get("shadow_over_live_ratio"))
            if shadow_over_live_ratio is not None:
                buckets[bucket]["ratio_values"].append(shadow_over_live_ratio)

        summaries: list[dict[str, Any]] = []
        for bucket, raw_entry in buckets.items():
            observed_count = max(int(raw_entry["observed_count"]), 1)
            partial_count = int(raw_entry["alignment_counts"].get("partial_alignment", 0))
            low_count = int(raw_entry["alignment_counts"].get("low_alignment", 0))
            divergence_score = _round_ratio(
                ((partial_count * 0.5) + low_count) / observed_count
            )
            duration_values = [float(value) for value in raw_entry["duration_values"] if value is not None]
            ratio_values = [float(value) for value in raw_entry["ratio_values"] if value is not None]

            summaries.append(
                {
                    bucket_key: bucket,
                    "observed_count": observed_count,
                    "alignment_counts": dict(raw_entry["alignment_counts"]),
                    "shadow_query_status_counts": dict(raw_entry["shadow_query_status_counts"]),
                    "divergence_score": divergence_score,
                    "avg_mismatch_count": _round_ratio(raw_entry["mismatch_total"] / observed_count),
                    "avg_shadow_duration_ms": _round_ratio(
                        sum(duration_values) / len(duration_values)
                    ) if duration_values else None,
                    "avg_shadow_over_live_ratio": _round_ratio(
                        sum(ratio_values) / len(ratio_values)
                    ) if ratio_values else None,
                    "top_requested_visual_families": _top_count_items(dict(raw_entry["requested_visual_counts"])),
                    "top_live_primary_visuals": _top_count_items(dict(raw_entry["live_primary_visual_counts"])),
                    "top_shadow_primary_visuals": _top_count_items(dict(raw_entry["shadow_primary_visual_counts"])),
                }
            )

        return sorted(
            summaries,
            key=lambda item: (
                -(item.get("divergence_score") or 0.0),
                -int(item.get("observed_count") or 0),
                _normalize_text(item.get(bucket_key)),
            ),
        )

    if not observed_rows:
        return {
            "observed_count": 0,
            "alignment_counts": {},
            "avg_shadow_duration_ms": None,
            "avg_shadow_over_live_ratio": None,
            "divergence_by_prompt_type": [],
            "divergence_by_visual_family": [],
        }

    alignment_counts = _count_metric_events(
        observed_rows,
        "shadow_runtime_observed",
        dimension_key="alignment_grade",
    )
    ratio_values = [
        ratio
        for ratio in (
            _coerce_float(_safe_dict(row.get("dimensions")).get("shadow_over_live_ratio"))
            for row in alignment_rows
        )
        if ratio is not None
    ]
    return {
        "observed_count": _sum_metric_value(observed_rows, "shadow_runtime_observed"),
        "alignment_counts": alignment_counts,
        "avg_shadow_duration_ms": _average_metric_value(duration_rows, "shadow_runtime_duration_ms"),
        "avg_shadow_over_live_ratio": _round_ratio(sum(ratio_values) / len(ratio_values)) if ratio_values else None,
        "divergence_by_prompt_type": _build_bucket_summary("prompt_type"),
        "divergence_by_visual_family": _build_bucket_summary("requested_visual_family"),
        "divergence_by_file_name": _build_bucket_summary("file_name"),
    }


def summarize_shadow_runtime_telemetry(
    *,
    metric_rows: list[dict[str, Any]],
    allowed_task_ids: set[str] | None = None,
    window_days_options: list[int] | None = None,
) -> dict[str, Any]:
    observed_rows = _bucket_shadow_runtime_rows(
        metric_rows,
        metric_name="shadow_runtime_observed",
        allowed_task_ids=allowed_task_ids,
    )
    duration_rows = _bucket_shadow_runtime_rows(
        metric_rows,
        metric_name="shadow_runtime_duration_ms",
        allowed_task_ids=allowed_task_ids,
    )
    alignment_rows = _bucket_shadow_runtime_rows(
        metric_rows,
        metric_name="shadow_runtime_alignment",
        allowed_task_ids=allowed_task_ids,
    )

    summary = _build_shadow_runtime_summary_from_rows(
        observed_rows=observed_rows,
        duration_rows=duration_rows,
        alignment_rows=alignment_rows,
    )
    if not observed_rows:
        return summary

    observed_created_at = [
        created_at
        for created_at in (_coerce_iso_datetime(row.get("created_at")) for row in observed_rows)
        if created_at is not None
    ]
    prompt_types = {
        _normalize_text(_safe_dict(row.get("dimensions")).get("prompt_type"))
        for row in observed_rows
        if _normalize_text(_safe_dict(row.get("dimensions")).get("prompt_type"))
    }
    file_names = {
        _normalize_text(_safe_dict(row.get("dimensions")).get("file_name"))
        for row in observed_rows
        if _normalize_text(_safe_dict(row.get("dimensions")).get("file_name"))
    }
    task_ids = {
        task_id
        for task_id in (_task_id_from_dimensions(row) for row in observed_rows)
        if task_id
    }

    windows = sorted({int(value) for value in list(window_days_options or [1, 7, 30]) if int(value) > 0})
    now_utc = datetime.now(timezone.utc)
    stability_by_window: list[dict[str, Any]] = []
    for window_days in windows:
        since_utc = now_utc - timedelta(days=int(window_days))
        observed_window = _filter_shadow_runtime_rows_since(observed_rows, since_utc=since_utc)
        duration_window = _filter_shadow_runtime_rows_since(duration_rows, since_utc=since_utc)
        alignment_window = _filter_shadow_runtime_rows_since(alignment_rows, since_utc=since_utc)
        window_summary = _build_shadow_runtime_summary_from_rows(
            observed_rows=observed_window,
            duration_rows=duration_window,
            alignment_rows=alignment_window,
        )
        stability_by_window.append(
            {
                "window_days": int(window_days),
                "observed_count": window_summary.get("observed_count"),
                "alignment_counts": window_summary.get("alignment_counts"),
                "avg_shadow_duration_ms": window_summary.get("avg_shadow_duration_ms"),
                "avg_shadow_over_live_ratio": window_summary.get("avg_shadow_over_live_ratio"),
                "divergence_by_prompt_type": window_summary.get("divergence_by_prompt_type"),
                "divergence_by_visual_family": window_summary.get("divergence_by_visual_family"),
                "divergence_by_file_name": window_summary.get("divergence_by_file_name"),
            }
        )

    summary.update(
        {
            "observed_prompt_type_count": len(prompt_types),
            "observed_file_count": len(file_names),
            "observed_task_count": len(task_ids),
            "earliest_observed_at": min(observed_created_at).isoformat() if observed_created_at else None,
            "latest_observed_at": max(observed_created_at).isoformat() if observed_created_at else None,
            "stability_by_window": stability_by_window,
        }
    )
    return summary


def _bucket_canary_route_rows(
    metric_rows: list[dict[str, Any]],
    *,
    metric_name: str,
    allowed_task_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in metric_rows:
        if _normalize_text(row.get("metric_name")) != metric_name:
            continue
        task_id = _task_id_from_dimensions(row)
        if allowed_task_ids and task_id not in allowed_task_ids:
            continue
        rows.append(row)
    return rows


def _build_canary_route_summary_from_rows(
    *,
    observed_rows: list[dict[str, Any]],
    fallback_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    if not observed_rows:
        return {
            "observed_count": 0,
            "requested_runtime_counts": {},
            "effective_runtime_counts": {},
            "decision_mode_counts": {},
            "fallback_count": 0,
            "distribution_by_prompt_type": [],
            "distribution_by_file_name": [],
            "distribution_by_team_id": [],
        }

    def _bucket_distribution(bucket_key: str) -> list[dict[str, Any]]:
        buckets: dict[str, dict[str, Any]] = {}
        for row in observed_rows:
            dimensions = _safe_dict(row.get("dimensions"))
            bucket = _normalize_text(dimensions.get(bucket_key)) or "unknown"
            entry = buckets.setdefault(
                bucket,
                {
                    "bucket": bucket,
                    "observed_count": 0,
                    "requested_runtime_counts": defaultdict(int),
                    "effective_runtime_counts": defaultdict(int),
                    "decision_mode_counts": defaultdict(int),
                    "fallback_count": 0,
                },
            )
            event_count = max(int(round(_coerce_metric_float(row.get("metric_value")))), 1)
            requested_runtime = _normalize_text(dimensions.get("requested_runtime")) or "unknown"
            effective_runtime = _normalize_text(dimensions.get("effective_runtime")) or "unknown"
            decision_mode = _normalize_text(dimensions.get("decision_mode")) or "unknown"
            entry["observed_count"] += event_count
            entry["requested_runtime_counts"][requested_runtime] += event_count
            entry["effective_runtime_counts"][effective_runtime] += event_count
            entry["decision_mode_counts"][decision_mode] += event_count

        for row in fallback_rows:
            dimensions = _safe_dict(row.get("dimensions"))
            bucket = _normalize_text(dimensions.get(bucket_key)) or "unknown"
            if bucket not in buckets:
                continue
            event_count = max(int(round(_coerce_metric_float(row.get("metric_value")))), 1)
            buckets[bucket]["fallback_count"] += event_count

        result: list[dict[str, Any]] = []
        for bucket, raw_entry in buckets.items():
            observed_count = max(int(raw_entry["observed_count"]), 1)
            requested_counts = dict(raw_entry["requested_runtime_counts"])
            effective_counts = dict(raw_entry["effective_runtime_counts"])
            fallback_count = int(raw_entry["fallback_count"])
            result.append(
                {
                    bucket_key: bucket,
                    "observed_count": observed_count,
                    "requested_runtime_counts": requested_counts,
                    "effective_runtime_counts": effective_counts,
                    "decision_mode_counts": dict(raw_entry["decision_mode_counts"]),
                    "fallback_count": fallback_count,
                    "candidate_ratio": _round_ratio(
                        int(requested_counts.get("universal_tabular", 0)) / observed_count
                    ),
                    "effective_universal_ratio": _round_ratio(
                        int(effective_counts.get("universal_tabular", 0)) / observed_count
                    ),
                    "fallback_ratio": _round_ratio(fallback_count / observed_count),
                }
            )

        return sorted(
            result,
            key=lambda item: (
                -int(item.get("observed_count") or 0),
                -float(item.get("candidate_ratio") or 0.0),
                _normalize_text(item.get(bucket_key)),
            ),
        )

    return {
        "observed_count": _sum_metric_value(observed_rows, "canary_runtime_route_observed"),
        "requested_runtime_counts": _count_metric_events(
            observed_rows,
            "canary_runtime_route_observed",
            dimension_key="requested_runtime",
        ),
        "effective_runtime_counts": _count_metric_events(
            observed_rows,
            "canary_runtime_route_observed",
            dimension_key="effective_runtime",
        ),
        "decision_mode_counts": _count_metric_events(
            observed_rows,
            "canary_runtime_route_observed",
            dimension_key="decision_mode",
        ),
        "fallback_count": _sum_metric_value(fallback_rows, "canary_runtime_route_fallback"),
        "distribution_by_prompt_type": _bucket_distribution("prompt_type"),
        "distribution_by_file_name": _bucket_distribution("file_name"),
        "distribution_by_team_id": _bucket_distribution("team_id"),
    }


def summarize_canary_route_telemetry(
    *,
    metric_rows: list[dict[str, Any]],
    allowed_task_ids: set[str] | None = None,
    window_days_options: list[int] | None = None,
) -> dict[str, Any]:
    observed_rows = _bucket_canary_route_rows(
        metric_rows,
        metric_name="canary_runtime_route_observed",
        allowed_task_ids=allowed_task_ids,
    )
    fallback_rows = _bucket_canary_route_rows(
        metric_rows,
        metric_name="canary_runtime_route_fallback",
        allowed_task_ids=allowed_task_ids,
    )
    summary = _build_canary_route_summary_from_rows(
        observed_rows=observed_rows,
        fallback_rows=fallback_rows,
    )
    if not observed_rows:
        return summary

    observed_created_at = [
        created_at
        for created_at in (_coerce_iso_datetime(row.get("created_at")) for row in observed_rows)
        if created_at is not None
    ]
    task_ids = {
        task_id
        for task_id in (_task_id_from_dimensions(row) for row in observed_rows)
        if task_id
    }
    windows = sorted({int(value) for value in list(window_days_options or [1, 7, 30]) if int(value) > 0})
    now_utc = datetime.now(timezone.utc)
    stability_by_window: list[dict[str, Any]] = []
    for window_days in windows:
        since_utc = now_utc - timedelta(days=int(window_days))
        observed_window = _filter_shadow_runtime_rows_since(observed_rows, since_utc=since_utc)
        fallback_window = _filter_shadow_runtime_rows_since(fallback_rows, since_utc=since_utc)
        window_summary = _build_canary_route_summary_from_rows(
            observed_rows=observed_window,
            fallback_rows=fallback_window,
        )
        stability_by_window.append(
            {
                "window_days": int(window_days),
                "observed_count": window_summary.get("observed_count"),
                "requested_runtime_counts": window_summary.get("requested_runtime_counts"),
                "effective_runtime_counts": window_summary.get("effective_runtime_counts"),
                "fallback_count": window_summary.get("fallback_count"),
                "distribution_by_prompt_type": window_summary.get("distribution_by_prompt_type"),
            }
        )

    summary.update(
        {
            "observed_task_count": len(task_ids),
            "earliest_observed_at": min(observed_created_at).isoformat() if observed_created_at else None,
            "latest_observed_at": max(observed_created_at).isoformat() if observed_created_at else None,
            "stability_by_window": stability_by_window,
        }
    )
    return summary


def summarize_analysis_payload(
    *,
    final_struct: dict[str, Any] | None,
    dataset_contract: dict[str, Any] | None,
    cleaning_notes: Any,
) -> dict[str, Any]:
    payload = final_struct if isinstance(final_struct, dict) else {}
    contract = dataset_contract if isinstance(dataset_contract, dict) else {}

    chart_options = _safe_list(payload.get("chart_options"))
    visual_types = _unique_visual_types(chart_options)
    analysis_text = _normalize_text(payload.get("analysis"))

    return {
        "chart_count": len(chart_options),
        "visual_types": visual_types,
        "data_row_count": len(_safe_list(payload.get("data"))),
        "recommendation_count": len(_safe_list(payload.get("recommendations"))),
        "explainability_count": len(_safe_list(payload.get("explainability"))),
        "snapshot_row_count": _coerce_int(payload.get("snapshot_row_count")),
        "has_arrow_data": bool(payload.get("arrow_data")),
        "has_snapshot_arrow": bool(payload.get("snapshot_arrow")),
        "dataset_mode": _normalize_text(contract.get("dataset_mode")) or "undetermined",
        "dataset_contract_confidence": _coerce_float(contract.get("confidence_score")),
        "quality_note_count": _quality_note_count(cleaning_notes),
        "analysis_length": len(analysis_text),
    }


def summarize_saved_report_content(content: Any) -> dict[str, Any]:
    payload = content if isinstance(content, dict) else {}
    report_type = _normalize_text(payload.get("type")) or "unknown"

    chart_options: list[dict[str, Any]] = []
    if isinstance(payload.get("option"), dict):
        chart_options = [payload.get("option")]
    elif isinstance(payload.get("chart_options"), list):
        chart_options = [opt for opt in payload["chart_options"] if isinstance(opt, dict)]

    visual_types = _unique_visual_types(chart_options)

    if report_type == "configuracion_echarts" and visual_types:
        primary_kind = "chart"
    elif report_type == "smart_table":
        primary_kind = "smart_table"
    elif report_type == "tabla_datos":
        primary_kind = "table"
    elif report_type == "metricas_clave":
        primary_kind = "metrics"
    elif report_type == "mensaje_resumen":
        primary_kind = "summary"
    else:
        primary_kind = report_type

    option = chart_options[0] if chart_options else {}
    return {
        "content_kind": primary_kind,
        "chart_count": len(chart_options),
        "visual_types": visual_types,
        "has_layout": isinstance(payload.get("layout"), dict),
        "has_visual_source_payload": isinstance(option.get("visual_source_payload"), dict),
    }


def emit_enterprise_metric(
    *,
    metric_domain: str,
    metric_name: str,
    metric_value: int | float,
    metric_unit: str = "count",
    **dimensions: Any,
) -> None:
    normalized_dimensions = _normalize_dimensions(dimensions)

    emit_structured_log(
        "enterprise_metric_observed",
        telemetry_version=TELEMETRY_VERSION,
        metric_domain=metric_domain,
        metric_name=metric_name,
        metric_value=metric_value,
        metric_unit=metric_unit,
        **normalized_dimensions,
    )
    _persist_enterprise_metric_event(
        metric_domain=metric_domain,
        metric_name=metric_name,
        metric_value=metric_value,
        metric_unit=metric_unit,
        dimensions=normalized_dimensions,
    )


def emit_enterprise_metrics_batch(
    *,
    metric_events: list[dict[str, Any]],
) -> None:
    payloads: list[dict[str, Any]] = []
    for event in metric_events:
        metric_domain = _normalize_text(event.get("metric_domain")) or "unknown"
        metric_name = _normalize_text(event.get("metric_name")) or "unknown"
        metric_value = _coerce_metric_float(event.get("metric_value"))
        metric_unit = _normalize_text(event.get("metric_unit")) or "count"
        dimensions = _normalize_dimensions(_safe_dict(event.get("dimensions")))

        emit_structured_log(
            "enterprise_metric_observed",
            telemetry_version=TELEMETRY_VERSION,
            metric_domain=metric_domain,
            metric_name=metric_name,
            metric_value=metric_value,
            metric_unit=metric_unit,
            **dimensions,
        )

        payloads.append(
            _build_enterprise_metric_payload(
                metric_domain=metric_domain,
                metric_name=metric_name,
                metric_value=metric_value,
                metric_unit=metric_unit,
                dimensions=dimensions,
            )
        )

    _persist_enterprise_metric_events_batch(payloads)


def track_analysis_requested(
    *,
    task_id: str,
    file_id: str,
    user_id: str | None,
    prompt: str | None,
) -> None:
    clean_prompt = _normalize_text(prompt)
    emit_enterprise_metric(
        metric_domain="usage",
        metric_name="analysis_requested",
        metric_value=1,
        task_id=task_id,
        file_id=file_id,
        user_id=user_id,
        prompt_length=len(clean_prompt),
    )


def track_analysis_completed(
    *,
    task_id: str,
    file_id: str,
    user_id: str | None,
    status: str,
    duration_ms: int,
    final_struct: dict[str, Any] | None,
    dataset_contract: dict[str, Any] | None,
    cleaning_notes: Any,
) -> None:
    summary = summarize_analysis_payload(
        final_struct=final_struct,
        dataset_contract=dataset_contract,
        cleaning_notes=cleaning_notes,
    )

    emit_enterprise_metric(
        metric_domain="usage",
        metric_name="analysis_completed",
        metric_value=1,
        metric_unit="count",
        task_id=task_id,
        file_id=file_id,
        user_id=user_id,
        status=_normalize_text(status) or "unknown",
        dataset_mode=summary["dataset_mode"],
        chart_count=summary["chart_count"],
    )
    emit_enterprise_metric(
        metric_domain="latency",
        metric_name="analysis_duration_ms",
        metric_value=max(duration_ms, 0),
        metric_unit="ms",
        task_id=task_id,
        file_id=file_id,
        user_id=user_id,
        status=_normalize_text(status) or "unknown",
        dataset_mode=summary["dataset_mode"],
    )

    if summary["chart_count"] > 0:
        emit_enterprise_metric(
            metric_domain="product",
            metric_name="charts_generated",
            metric_value=summary["chart_count"],
            metric_unit="count",
            task_id=task_id,
            file_id=file_id,
            user_id=user_id,
            dataset_mode=summary["dataset_mode"],
        )

    for visual_type in summary["visual_types"]:
        emit_enterprise_metric(
            metric_domain="product",
            metric_name="visual_type_generated",
            metric_value=1,
            metric_unit="count",
            task_id=task_id,
            file_id=file_id,
            user_id=user_id,
            visual_type=visual_type,
            dataset_mode=summary["dataset_mode"],
        )

    if summary["dataset_contract_confidence"] is not None:
        emit_enterprise_metric(
            metric_domain="confidence",
            metric_name="dataset_contract_confidence",
            metric_value=summary["dataset_contract_confidence"],
            metric_unit="ratio",
            task_id=task_id,
            file_id=file_id,
            user_id=user_id,
            dataset_mode=summary["dataset_mode"],
        )

    if summary["quality_note_count"] > 0:
        emit_enterprise_metric(
            metric_domain="confidence",
            metric_name="dataset_quality_warnings",
            metric_value=summary["quality_note_count"],
            metric_unit="count",
            task_id=task_id,
            file_id=file_id,
            user_id=user_id,
            dataset_mode=summary["dataset_mode"],
        )


def track_analysis_stage_latency(
    *,
    task_id: str,
    file_id: str,
    user_id: str | None,
    stage_name: str,
    duration_ms: int,
    status: str | None = None,
    runtime: str | None = None,
    prompt_type: str | None = None,
) -> None:
    emit_enterprise_metric(
        metric_domain="latency",
        metric_name="analysis_stage_duration_ms",
        metric_value=max(int(duration_ms), 0),
        metric_unit="ms",
        task_id=task_id,
        file_id=file_id,
        user_id=user_id,
        stage_name=_normalize_text(stage_name) or "unknown",
        status=_normalize_text(status) or None,
        runtime=_normalize_text(runtime) or None,
        prompt_type=_normalize_text(prompt_type) or None,
    )


def track_analysis_stage_latency_batch(
    *,
    task_id: str,
    file_id: str,
    user_id: str | None,
    runtime: str | None,
    prompt_type: str | None,
    stage_metrics: list[dict[str, Any]],
) -> None:
    metric_events: list[dict[str, Any]] = []
    for row in stage_metrics:
        stage_name = _normalize_text(row.get("stage_name")) or "unknown"
        metric_events.append(
            {
                "metric_domain": "latency",
                "metric_name": "analysis_stage_duration_ms",
                "metric_value": max(_coerce_int(row.get("duration_ms")), 0),
                "metric_unit": "ms",
                "dimensions": {
                    "task_id": task_id,
                    "file_id": file_id,
                    "user_id": user_id,
                    "stage_name": stage_name,
                    "status": _normalize_text(row.get("status")) or None,
                    "runtime": _normalize_text(runtime) or None,
                    "prompt_type": _normalize_text(prompt_type) or None,
                },
            }
        )
    emit_enterprise_metrics_batch(metric_events=metric_events)


def track_shadow_runtime_observed(
    *,
    task_id: str,
    file_id: str,
    user_id: str | None,
    team_id: str | None,
    file_name: str | None,
    readiness_grade: str | None,
    shadow_query_status: str | None,
    alignment_grade: str | None,
    mismatch_count: int,
    live_chart_count: int,
    shadow_chart_count: int,
    shadow_duration_ms: int,
    shadow_over_live_ratio: float | None,
    prompt_type: str | None,
    requested_visual_family: str | None,
    live_primary_visual: str | None,
    shadow_primary_visual: str | None,
) -> None:
    normalized_alignment = _normalize_text(alignment_grade) or "unknown"
    normalized_status = _normalize_text(shadow_query_status) or "unknown"
    normalized_readiness = _normalize_text(readiness_grade) or "unknown"
    normalized_file_name = _normalize_text(file_name) or None
    normalized_prompt_type = _normalize_text(prompt_type) or "unknown"
    normalized_requested_visual_family = _normalize_text(requested_visual_family) or "unknown"
    normalized_live_primary_visual = _normalize_text(live_primary_visual) or "unknown"
    normalized_shadow_primary_visual = _normalize_text(shadow_primary_visual) or "unknown"

    emit_enterprise_metric(
        metric_domain="usage",
        metric_name="shadow_runtime_observed",
        metric_value=1,
        metric_unit="count",
        task_id=task_id,
        file_id=file_id,
        user_id=user_id,
        team_id=team_id,
        file_name=normalized_file_name,
        readiness_grade=normalized_readiness,
        shadow_query_status=normalized_status,
        alignment_grade=normalized_alignment,
        mismatch_count=max(_coerce_int(mismatch_count), 0),
        live_chart_count=max(_coerce_int(live_chart_count), 0),
        shadow_chart_count=max(_coerce_int(shadow_chart_count), 0),
        prompt_type=normalized_prompt_type,
        requested_visual_family=normalized_requested_visual_family,
        live_primary_visual=normalized_live_primary_visual,
        shadow_primary_visual=normalized_shadow_primary_visual,
    )
    emit_enterprise_metric(
        metric_domain="latency",
        metric_name="shadow_runtime_duration_ms",
        metric_value=max(_coerce_int(shadow_duration_ms), 0),
        metric_unit="ms",
        task_id=task_id,
        file_id=file_id,
        user_id=user_id,
        team_id=team_id,
        file_name=normalized_file_name,
        alignment_grade=normalized_alignment,
        shadow_query_status=normalized_status,
        prompt_type=normalized_prompt_type,
        requested_visual_family=normalized_requested_visual_family,
        live_primary_visual=normalized_live_primary_visual,
        shadow_primary_visual=normalized_shadow_primary_visual,
    )
    emit_enterprise_metric(
        metric_domain="confidence",
        metric_name="shadow_runtime_alignment",
        metric_value=1,
        metric_unit="count",
        task_id=task_id,
        file_id=file_id,
        user_id=user_id,
        team_id=team_id,
        file_name=normalized_file_name,
        readiness_grade=normalized_readiness,
        alignment_grade=normalized_alignment,
        shadow_query_status=normalized_status,
        shadow_over_live_ratio=_round_ratio(shadow_over_live_ratio),
        prompt_type=normalized_prompt_type,
        requested_visual_family=normalized_requested_visual_family,
        live_primary_visual=normalized_live_primary_visual,
        shadow_primary_visual=normalized_shadow_primary_visual,
    )


def track_canary_runtime_route_observed(
    *,
    task_id: str,
    file_id: str,
    user_id: str | None,
    team_id: str | None,
    file_name: str | None,
    prompt_type: str | None,
    requested_runtime: str | None,
    effective_runtime: str | None,
    decision_mode: str | None,
    decision_reason: str | None,
    health_status: str | None,
    eligible: bool,
    bucket_value: int | None,
    traffic_percent: int | None,
    allowlist_match: str | None,
    health_ready_for_functional_canary: bool,
) -> None:
    emit_enterprise_metric(
        metric_domain="usage",
        metric_name="canary_runtime_route_observed",
        metric_value=1,
        metric_unit="count",
        task_id=task_id,
        file_id=file_id,
        user_id=user_id,
        team_id=team_id,
        file_name=_normalize_text(file_name) or None,
        prompt_type=_normalize_text(prompt_type) or "unknown",
        requested_runtime=_normalize_text(requested_runtime) or "unknown",
        effective_runtime=_normalize_text(effective_runtime) or "unknown",
        decision_mode=_normalize_text(decision_mode) or "unknown",
        decision_reason=_normalize_text(decision_reason) or "unknown",
        health_status=_normalize_text(health_status) or "unknown",
        eligible=bool(eligible),
        bucket_value=_coerce_int(bucket_value),
        traffic_percent=_coerce_int(traffic_percent),
        allowlist_match=_normalize_text(allowlist_match) or "none",
        health_ready_for_functional_canary=bool(health_ready_for_functional_canary),
    )


def track_canary_runtime_route_fallback(
    *,
    task_id: str,
    file_id: str,
    user_id: str | None,
    team_id: str | None,
    file_name: str | None,
    prompt_type: str | None,
    requested_runtime: str | None,
    fallback_runtime: str | None,
    decision_reason: str | None,
) -> None:
    emit_enterprise_metric(
        metric_domain="usage",
        metric_name="canary_runtime_route_fallback",
        metric_value=1,
        metric_unit="count",
        task_id=task_id,
        file_id=file_id,
        user_id=user_id,
        team_id=team_id,
        file_name=_normalize_text(file_name) or None,
        prompt_type=_normalize_text(prompt_type) or "unknown",
        requested_runtime=_normalize_text(requested_runtime) or "unknown",
        fallback_runtime=_normalize_text(fallback_runtime) or "unknown",
        decision_reason=_normalize_text(decision_reason) or "unknown",
    )


def track_canary_runtime_execution_observed(
    *,
    task_id: str,
    file_id: str,
    user_id: str | None,
    team_id: str | None,
    file_name: str | None,
    prompt_type: str | None,
    execution_status: str | None,
    candidate_id: str | None,
    prompt_strategy: str | None,
    chart_count: int,
    duration_ms: int,
) -> None:
    emit_enterprise_metric(
        metric_domain="usage",
        metric_name="canary_runtime_execution_observed",
        metric_value=1,
        metric_unit="count",
        task_id=task_id,
        file_id=file_id,
        user_id=user_id,
        team_id=team_id,
        file_name=_normalize_text(file_name) or None,
        prompt_type=_normalize_text(prompt_type) or "unknown",
        execution_status=_normalize_text(execution_status) or "unknown",
        candidate_id=_normalize_text(candidate_id) or "unknown",
        prompt_strategy=_normalize_text(prompt_strategy) or "unknown",
        chart_count=max(_coerce_int(chart_count), 0),
    )
    emit_enterprise_metric(
        metric_domain="latency",
        metric_name="canary_runtime_execution_duration_ms",
        metric_value=max(_coerce_int(duration_ms), 0),
        metric_unit="ms",
        task_id=task_id,
        file_id=file_id,
        user_id=user_id,
        team_id=team_id,
        file_name=_normalize_text(file_name) or None,
        prompt_type=_normalize_text(prompt_type) or "unknown",
        execution_status=_normalize_text(execution_status) or "unknown",
        candidate_id=_normalize_text(candidate_id) or "unknown",
        prompt_strategy=_normalize_text(prompt_strategy) or "unknown",
    )


def track_canary_runtime_execution_fallback(
    *,
    task_id: str,
    file_id: str,
    user_id: str | None,
    team_id: str | None,
    file_name: str | None,
    prompt_type: str | None,
    fallback_reason: str | None,
) -> None:
    emit_enterprise_metric(
        metric_domain="usage",
        metric_name="canary_runtime_execution_fallback",
        metric_value=1,
        metric_unit="count",
        task_id=task_id,
        file_id=file_id,
        user_id=user_id,
        team_id=team_id,
        file_name=_normalize_text(file_name) or None,
        prompt_type=_normalize_text(prompt_type) or "unknown",
        fallback_reason=_normalize_text(fallback_reason) or "unknown",
    )


def track_report_saved(
    *,
    report_id: Any,
    presentation_id: str | None,
    file_id: str | None,
    user_id: str | None,
    content: Any,
) -> None:
    summary = summarize_saved_report_content(content)

    emit_enterprise_metric(
        metric_domain="usage",
        metric_name="report_saved",
        metric_value=1,
        report_id=report_id,
        presentation_id=presentation_id,
        file_id=file_id,
        user_id=user_id,
        content_kind=summary["content_kind"],
    )

    for visual_type in summary["visual_types"]:
        emit_enterprise_metric(
            metric_domain="product",
            metric_name="saved_visual_type",
            metric_value=1,
            report_id=report_id,
            presentation_id=presentation_id,
            file_id=file_id,
            user_id=user_id,
            visual_type=visual_type,
        )


def track_file_preview_generated(
    *,
    user_id: str | None,
    file_id: str,
    preview_payload: dict[str, Any] | None,
) -> None:
    payload = preview_payload if isinstance(preview_payload, dict) else {}
    quality_profile = _safe_dict(payload.get("quality_profile"))
    health_status = _normalize_text(quality_profile.get("health_status")) or "unknown"

    emit_enterprise_metric(
        metric_domain="usage",
        metric_name="file_preview_generated",
        metric_value=1,
        user_id=user_id,
        file_id=file_id,
        row_count=_coerce_int(payload.get("row_count")),
        column_count=_coerce_int(payload.get("column_count")),
        selected_sheet=_normalize_text(payload.get("selected_sheet")) or None,
    )

    if quality_profile:
        emit_enterprise_metric(
            metric_domain="confidence",
            metric_name="preview_health_score",
            metric_value=_coerce_int(quality_profile.get("health_score")),
            metric_unit="score",
            user_id=user_id,
            file_id=file_id,
            health_status=health_status,
        )
        emit_enterprise_metric(
            metric_domain="confidence",
            metric_name="preview_alert_count",
            metric_value=_coerce_int(quality_profile.get("alert_count")),
            metric_unit="count",
            user_id=user_id,
            file_id=file_id,
            health_status=health_status,
        )
        emit_enterprise_metric(
            metric_domain="confidence",
            metric_name="preview_health_status_observed",
            metric_value=1,
            user_id=user_id,
            file_id=file_id,
            health_status=health_status,
        )


def track_knowledge_ask_executed(
    *,
    user_id: str | None,
    team_id: str | None,
    response_payload: dict[str, Any] | None,
) -> None:
    payload = response_payload if isinstance(response_payload, dict) else {}
    grounded = bool(payload.get("grounded"))
    insufficient_evidence = bool(payload.get("insufficient_evidence"))

    emit_enterprise_metric(
        metric_domain="usage",
        metric_name="knowledge_question_executed",
        metric_value=1,
        user_id=user_id,
        team_id=team_id,
        retrieved_count=_coerce_int(payload.get("retrieved_count")),
        citations_count=len(_safe_list(payload.get("citations"))),
    )

    if grounded:
        emit_enterprise_metric(
            metric_domain="confidence",
            metric_name="grounded_answer",
            metric_value=1,
            user_id=user_id,
            team_id=team_id,
        )

    if insufficient_evidence:
        emit_enterprise_metric(
            metric_domain="confidence",
            metric_name="insufficient_evidence_answer",
            metric_value=1,
            user_id=user_id,
            team_id=team_id,
        )


def track_knowledge_document_uploaded(
    *,
    user_id: str | None,
    team_id: str | None,
    document_id: Any,
    mime_type: str | None,
    file_size_bytes: int,
) -> None:
    emit_enterprise_metric(
        metric_domain="usage",
        metric_name="knowledge_document_uploaded",
        metric_value=1,
        user_id=user_id,
        team_id=team_id,
        document_id=document_id,
        mime_type=_normalize_text(mime_type) or "unknown",
    )
    emit_enterprise_metric(
        metric_domain="usage",
        metric_name="knowledge_document_bytes_uploaded",
        metric_value=max(file_size_bytes, 0),
        metric_unit="bytes",
        user_id=user_id,
        team_id=team_id,
        document_id=document_id,
        mime_type=_normalize_text(mime_type) or "unknown",
    )


def track_connector_file_imported(
    *,
    provider: str,
    user_id: str | None,
    uploaded_file_id: Any,
    source_type: str | None,
) -> None:
    emit_enterprise_metric(
        metric_domain="usage",
        metric_name="connector_file_imported",
        metric_value=1,
        provider=_normalize_text(provider) or "unknown",
        user_id=user_id,
        uploaded_file_id=uploaded_file_id,
        source_type=_normalize_text(source_type) or "unknown",
    )


def track_cloud_sync_job_queued(
    *,
    user_id: str | None,
    team_id: str | None,
    job_id: Any,
    provider: str | None,
    watch_target_id: Any,
    linked_file_id: Any,
    trigger_source: str | None,
) -> None:
    emit_enterprise_metric(
        metric_domain="usage",
        metric_name="cloud_sync_job_queued",
        metric_value=1,
        user_id=user_id,
        team_id=team_id,
        job_id=job_id,
        provider=_normalize_text(provider) or "unknown",
        watch_target_id=watch_target_id,
        linked_file_id=linked_file_id,
        trigger_source=_normalize_text(trigger_source) or "unknown",
    )


def track_cloud_sync_job_completed(
    *,
    user_id: str | None,
    team_id: str | None,
    job_id: Any,
    provider: str | None,
    watch_target_id: Any,
    linked_file_id: Any,
    status: str,
    duration_ms: int,
) -> None:
    normalized_status = _normalize_text(status) or "unknown"

    emit_enterprise_metric(
        metric_domain="usage",
        metric_name="cloud_sync_job_completed",
        metric_value=1,
        user_id=user_id,
        team_id=team_id,
        job_id=job_id,
        provider=_normalize_text(provider) or "unknown",
        watch_target_id=watch_target_id,
        linked_file_id=linked_file_id,
        status=normalized_status,
    )
    emit_enterprise_metric(
        metric_domain="latency",
        metric_name="cloud_sync_duration_ms",
        metric_value=max(int(duration_ms or 0), 0),
        metric_unit="ms",
        user_id=user_id,
        team_id=team_id,
        job_id=job_id,
        provider=_normalize_text(provider) or "unknown",
        watch_target_id=watch_target_id,
        linked_file_id=linked_file_id,
        status=normalized_status,
    )


def list_enterprise_telemetry_events(
    *,
    user_id: str,
    service_client: Any,
    window_days: int = 30,
) -> list[dict[str, Any]] | None:
    window_days = max(1, min(int(window_days or 30), 90))
    created_after = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()

    try:
        response = service_client.table(TELEMETRY_TABLE) \
            .select("metric_domain, metric_name, metric_value, metric_unit, dimensions, created_at") \
            .eq("user_id", user_id) \
            .gte("created_at", created_after) \
            .order("created_at", desc=False) \
            .limit(5000) \
            .execute()
        return response.data or []
    except Exception as exc:
        emit_structured_log(
            "enterprise_telemetry_summary_fetch_error",
            level="warning",
            user_id=user_id,
            window_days=window_days,
            error=str(exc)[:240],
        )
        return None


def summarize_enterprise_telemetry_events(
    *,
    metric_rows: list[dict[str, Any]],
    window_days: int,
    telemetry_ready: bool = True,
) -> dict[str, Any]:
    knowledge_questions = _sum_metric_value(metric_rows, "knowledge_question_executed")
    grounded_answers = _sum_metric_value(metric_rows, "grounded_answer")
    insufficient_evidence_answers = _sum_metric_value(metric_rows, "insufficient_evidence_answer")
    cloud_sync_completed = [
        row for row in metric_rows
        if _normalize_text(row.get("metric_name")) == "cloud_sync_job_completed"
    ]
    successful_syncs = sum(
        1
        for row in cloud_sync_completed
        if _normalize_text(_safe_dict(row.get("dimensions")).get("status")) == "succeeded"
    )
    stage_latency_by_stage = _average_metric_value_by_dimension(
        metric_rows,
        "analysis_stage_duration_ms",
        dimension_key="stage_name",
    )
    stage_latency_by_runtime = _average_metric_value_by_dimension(
        metric_rows,
        "analysis_stage_duration_ms",
        dimension_key="runtime",
    )

    return {
        "telemetry_ready": telemetry_ready,
        "window_days": max(1, int(window_days or 30)),
        "generated_at": _utc_now_iso(),
        "event_count": len(metric_rows),
        "usage": {
            "analyses_requested": _sum_metric_value(metric_rows, "analysis_requested"),
            "analyses_completed": _sum_metric_value(metric_rows, "analysis_completed"),
            "reports_saved": _sum_metric_value(metric_rows, "report_saved"),
            "knowledge_questions": knowledge_questions,
            "knowledge_documents_uploaded": _sum_metric_value(metric_rows, "knowledge_document_uploaded"),
            "connector_imports": _sum_metric_value(metric_rows, "connector_file_imported"),
            "cloud_sync_jobs_queued": _sum_metric_value(metric_rows, "cloud_sync_job_queued"),
            "cloud_sync_jobs_completed": _sum_metric_value(metric_rows, "cloud_sync_job_completed"),
            "file_previews": _sum_metric_value(metric_rows, "file_preview_generated"),
        },
        "confidence": {
            "avg_dataset_contract_confidence": _average_metric_value(metric_rows, "dataset_contract_confidence"),
            "dataset_quality_warning_count": _sum_metric_value(metric_rows, "dataset_quality_warnings"),
            "avg_preview_health_score": _average_metric_value(metric_rows, "preview_health_score"),
            "preview_alert_count": _sum_metric_value(metric_rows, "preview_alert_count"),
            "preview_health_status_counts": _count_metric_events(
                metric_rows,
                "preview_health_status_observed",
                dimension_key="health_status",
            ),
            "grounded_answer_rate": _round_ratio(
                grounded_answers / knowledge_questions if knowledge_questions else None
            ),
            "insufficient_evidence_rate": _round_ratio(
                insufficient_evidence_answers / knowledge_questions if knowledge_questions else None
            ),
            "cloud_sync_success_rate": _round_ratio(
                successful_syncs / len(cloud_sync_completed) if cloud_sync_completed else None
            ),
        },
        "product": {
            "charts_generated": _sum_metric_value(metric_rows, "charts_generated"),
            "generated_visual_types": _top_dimension_counts(
                metric_rows,
                "visual_type_generated",
                dimension_key="visual_type",
            ),
            "saved_visual_types": _top_dimension_counts(
                metric_rows,
                "saved_visual_type",
                dimension_key="visual_type",
            ),
            "connector_provider_mix": _top_dimension_counts(
                metric_rows,
                "connector_file_imported",
                dimension_key="provider",
            ),
        },
        "latency": {
            "avg_analysis_duration_ms": _average_metric_value(metric_rows, "analysis_duration_ms"),
            "avg_cloud_sync_duration_ms": _average_metric_value(metric_rows, "cloud_sync_duration_ms"),
            "avg_analysis_queue_wait_ms": stage_latency_by_stage.get("queue_wait"),
            "avg_analysis_stage_duration_ms_by_stage": stage_latency_by_stage,
            "avg_analysis_stage_duration_ms_by_runtime": stage_latency_by_runtime,
            "slowest_analysis_stages": _top_average_items(stage_latency_by_stage, limit=5),
        },
        "shadow_runtime": summarize_shadow_runtime_telemetry(metric_rows=metric_rows),
        "canary_routing": summarize_canary_route_telemetry(metric_rows=metric_rows),
    }


def build_enterprise_telemetry_summary_for_user(
    *,
    user_id: str,
    service_client: Any,
    window_days: int = 30,
) -> dict[str, Any]:
    metric_rows = list_enterprise_telemetry_events(
        user_id=user_id,
        service_client=service_client,
        window_days=window_days,
    )
    return summarize_enterprise_telemetry_events(
        metric_rows=metric_rows or [],
        window_days=window_days,
        telemetry_ready=metric_rows is not None,
    )
