from __future__ import annotations

from typing import Any

import pandas as pd

from app.services.data_engine import DataEngine


def _set_from(values: list[Any] | None) -> set[str]:
    return {str(value).strip() for value in list(values or []) if str(value or "").strip()}


def _safe_schema_profile(df: pd.DataFrame | None, sidecar_payload: dict[str, Any] | None) -> dict[str, Any]:
    sidecar_payload = sidecar_payload or {}
    attrs = getattr(df, "attrs", {}) or {}
    return attrs.get("schema_profile", {}) or sidecar_payload.get("_schema_profile", {}) or {}


def _safe_dataset_contract(df: pd.DataFrame | None, sidecar_payload: dict[str, Any] | None) -> dict[str, Any]:
    sidecar_payload = sidecar_payload or {}
    attrs = getattr(df, "attrs", {}) or {}
    return attrs.get("semantic_contract", {}) or {
        key: value
        for key, value in sidecar_payload.items()
        if not str(key).startswith("_")
    }


def _comparison_grade(summary: dict[str, Any]) -> str:
    if not summary.get("active_runtime_available"):
        return "no_active_runtime"
    if not summary.get("candidate_available"):
        return "no_candidate"

    column_overlap_ratio = float(summary.get("column_overlap_ratio") or 0.0)
    metric_overlap_ratio = float(summary.get("metric_overlap_ratio") or 0.0)
    exact_mode_match = bool(summary.get("exact_dataset_mode_match"))
    exact_time_axis_match = bool(summary.get("exact_time_axis_match"))

    if exact_mode_match and exact_time_axis_match and column_overlap_ratio >= 0.8:
        return "high_alignment"
    if column_overlap_ratio >= 0.5 or metric_overlap_ratio >= 0.5:
        return "partial_alignment"
    return "low_alignment"


def compare_selected_candidate_against_active_runtime(
    *,
    file_id: str,
    candidate_df: pd.DataFrame | None,
) -> dict[str, Any]:
    if candidate_df is None:
        return {
            "active_runtime_available": False,
            "candidate_available": False,
            "comparison_grade": "no_candidate",
            "reason": "selected_candidate_missing",
        }

    cached_dataset = DataEngine.load_cached_dataset(file_id)
    candidate_schema_profile = getattr(candidate_df, "attrs", {}).get("schema_profile", {}) or {}
    candidate_contract = getattr(candidate_df, "attrs", {}).get("semantic_contract", {}) or {}
    candidate_columns = set(map(str, candidate_df.columns))

    if not cached_dataset:
        return {
            "active_runtime_available": False,
            "candidate_available": True,
            "candidate_row_count": int(len(candidate_df.index)),
            "candidate_column_count": int(len(candidate_df.columns)),
            "candidate_dataset_mode": candidate_contract.get("dataset_mode"),
            "candidate_time_axis": candidate_contract.get("time_axis"),
            "comparison_grade": "no_active_runtime",
            "reason": "active_runtime_cache_missing",
        }

    active_df, parquet_path, sidecar_payload = cached_dataset
    active_schema_profile = _safe_schema_profile(active_df, sidecar_payload)
    active_contract = _safe_dataset_contract(active_df, sidecar_payload)
    active_columns = set(map(str, active_df.columns))

    candidate_metric_columns = _set_from(candidate_contract.get("metric_columns"))
    candidate_dimension_columns = _set_from(candidate_contract.get("dimension_columns"))
    candidate_identifier_columns = _set_from(candidate_contract.get("identifier_columns"))
    active_metric_columns = _set_from(active_contract.get("metric_columns"))
    active_dimension_columns = _set_from(active_contract.get("dimension_columns"))
    active_identifier_columns = _set_from(active_contract.get("identifier_columns"))

    column_overlap = active_columns & candidate_columns
    metric_overlap = active_metric_columns & candidate_metric_columns
    dimension_overlap = active_dimension_columns & candidate_dimension_columns
    identifier_overlap = active_identifier_columns & candidate_identifier_columns

    summary = {
        "active_runtime_available": True,
        "candidate_available": True,
        "active_parquet_path": parquet_path,
        "candidate_row_count": int(len(candidate_df.index)),
        "candidate_column_count": int(len(candidate_df.columns)),
        "active_row_count": int(len(active_df.index)),
        "active_column_count": int(len(active_df.columns)),
        "candidate_dataset_mode": candidate_contract.get("dataset_mode"),
        "active_dataset_mode": active_contract.get("dataset_mode"),
        "candidate_time_axis": candidate_contract.get("time_axis"),
        "active_time_axis": active_contract.get("time_axis"),
        "candidate_entity_key": candidate_contract.get("entity_key"),
        "active_entity_key": active_contract.get("entity_key"),
        "candidate_schema_column_count": len(candidate_schema_profile),
        "active_schema_column_count": len(active_schema_profile),
        "column_overlap_count": len(column_overlap),
        "column_overlap_ratio": round(len(column_overlap) / max(len(candidate_columns), 1), 4),
        "metric_overlap_count": len(metric_overlap),
        "metric_overlap_ratio": round(len(metric_overlap) / max(len(candidate_metric_columns), 1), 4) if candidate_metric_columns else 0.0,
        "dimension_overlap_count": len(dimension_overlap),
        "dimension_overlap_ratio": round(len(dimension_overlap) / max(len(candidate_dimension_columns), 1), 4) if candidate_dimension_columns else 0.0,
        "identifier_overlap_count": len(identifier_overlap),
        "exact_dataset_mode_match": candidate_contract.get("dataset_mode") == active_contract.get("dataset_mode"),
        "exact_time_axis_match": candidate_contract.get("time_axis") == active_contract.get("time_axis"),
        "exact_entity_key_match": candidate_contract.get("entity_key") == active_contract.get("entity_key"),
    }
    summary["comparison_grade"] = _comparison_grade(summary)
    return summary
