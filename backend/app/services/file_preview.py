from __future__ import annotations

from datetime import date, datetime
import re
from typing import Any

import numpy as np
import pandas as pd

from app.services.data_engine import DataEngine

PREVIEW_ROW_LIMIT = 100
QUALITY_COLUMN_ISSUE_LIMIT = 12


def _pick_preview_sheet(dfs: dict[str, pd.DataFrame]) -> tuple[str | None, pd.DataFrame]:
    if not dfs:
        return None, pd.DataFrame()

    for sheet_name, frame in dfs.items():
        if frame is not None and not frame.empty:
            return sheet_name, frame

    first_sheet_name = next(iter(dfs.keys()))
    return first_sheet_name, dfs[first_sheet_name]


def _looks_boolean(value: str) -> bool:
    normalized = value.strip().lower()
    return normalized in {"true", "false", "yes", "no", "si", "sí", "0", "1"}


def _looks_numeric(value: str) -> bool:
    normalized = value.strip().replace(" ", "")
    if not normalized:
        return False

    if "," in normalized and "." in normalized:
        if normalized.rfind(",") > normalized.rfind("."):
            normalized = normalized.replace(".", "").replace(",", ".")
        else:
            normalized = normalized.replace(",", "")
    elif "," in normalized:
        normalized = normalized.replace(",", ".")

    try:
        float(normalized)
        return True
    except ValueError:
        return False


def _has_structured_datetime_signal(value: str) -> bool:
    normalized = value.strip()
    if not normalized:
        return False

    lowered = normalized.lower()
    if re.search(r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|ene|abr|ago|dic)", lowered):
        return True

    if re.search(r"\d{1,4}[-/]\d{1,2}[-/]\d{1,4}", normalized):
        return True

    if re.search(r"\d{1,2}:\d{2}", normalized):
        return True

    compact_digits = re.sub(r"\D", "", normalized)
    if compact_digits.isdigit() and len(compact_digits) in {8, 14}:
        candidate_years = []
        if len(compact_digits) >= 4:
            candidate_years.append(int(compact_digits[:4]))
            candidate_years.append(int(compact_digits[-4:]))
        return any(1900 <= year <= 2100 for year in candidate_years)

    return False


def _looks_datetime(value: str) -> bool:
    normalized = value.strip()
    if not normalized:
        return False

    if not _has_structured_datetime_signal(normalized):
        return False

    parsed = pd.to_datetime([normalized], errors="coerce", format="mixed")
    return bool(len(parsed) and not pd.isna(parsed[0]))


def infer_series_type(series: pd.Series) -> str:
    non_null = series.dropna()
    semantics = _build_series_semantics(series, int(max(len(non_null), len(series), 1)))
    return str(semantics["inferred_type"])


def _normalize_missing_series(series: pd.Series) -> pd.Series:
    if series.dtype == object or pd.api.types.is_string_dtype(series):
        return series.replace(r"^\s*$", np.nan, regex=True)
    return series


def _sampled_value_hits(series: pd.Series, sample_size: int = 200) -> tuple[list[str], int, int, int]:
    normalized = _normalize_missing_series(series)
    non_null = normalized.dropna()
    if non_null.empty:
        return [], 0, 0, 0

    sampled_values = [str(value).strip() for value in non_null.head(sample_size).tolist()]
    sampled_values = [value for value in sampled_values if value]
    if not sampled_values:
        return [], 0, 0, 0

    numeric_hits = sum(1 for value in sampled_values if _looks_numeric(value))
    datetime_hits = sum(1 for value in sampled_values if _looks_datetime(value))
    boolean_hits = sum(1 for value in sampled_values if _looks_boolean(value))
    return sampled_values, numeric_hits, datetime_hits, boolean_hits


def _is_generic_column_name(column_name: str) -> bool:
    normalized = str(column_name).strip().lower()
    return not normalized or normalized.startswith("unnamed") or normalized in {"column", "campo", "columna"}


def _normalize_column_key(column_name: str) -> str:
    return " ".join(str(column_name).strip().lower().split())


def _build_series_semantics(series: pd.Series, row_count: int) -> dict[str, Any]:
    normalized = _normalize_missing_series(series)
    non_null = normalized.dropna()
    if non_null.empty:
        return {
            "inferred_type": "text",
            "semantic_role": "dimension",
            "sample_total": 0,
            "numeric_ratio": 0.0,
            "datetime_ratio": 0.0,
            "boolean_ratio": 0.0,
            "text_like_ratio": 0.0,
            "distinct_count": 0,
            "distinct_ratio": 0.0,
            "metric_candidate": False,
            "date_candidate": False,
            "identifier_like_numeric": False,
        }

    clean_non_null = non_null.astype("string").str.strip()
    clean_non_null = clean_non_null[clean_non_null != ""]
    if clean_non_null.empty:
        return {
            "inferred_type": "text",
            "semantic_role": "dimension",
            "sample_total": 0,
            "numeric_ratio": 0.0,
            "datetime_ratio": 0.0,
            "boolean_ratio": 0.0,
            "text_like_ratio": 0.0,
            "distinct_count": 0,
            "distinct_ratio": 0.0,
            "metric_candidate": False,
            "date_candidate": False,
            "identifier_like_numeric": False,
        }

    sampled_values, numeric_hits, datetime_hits, boolean_hits = _sampled_value_hits(series)
    sample_total = len(sampled_values)
    distinct_count = int(clean_non_null.nunique())
    distinct_ratio = distinct_count / max(1, row_count)

    numeric_ratio = (numeric_hits / sample_total) if sample_total else 0.0
    datetime_ratio = (datetime_hits / sample_total) if sample_total else 0.0
    boolean_ratio = (boolean_hits / sample_total) if sample_total else 0.0
    text_like_count = max(0, sample_total - max(numeric_hits, datetime_hits, boolean_hits))
    text_like_ratio = (text_like_count / sample_total) if sample_total else 0.0

    compact_tokens = clean_non_null.str.replace(r"\s+", "", regex=True)
    integer_like_mask = compact_tokens.str.fullmatch(r"[+-]?\d+")
    decimal_like_mask = compact_tokens.str.fullmatch(r"[+-]?\d+[.,]\d+")
    integer_like_ratio = float(integer_like_mask.mean()) if len(compact_tokens) else 0.0
    decimal_like_ratio = float(decimal_like_mask.mean()) if len(compact_tokens) else 0.0

    digit_tokens = compact_tokens[integer_like_mask.fillna(False)].str.lstrip("+-")
    digit_lengths = digit_tokens.str.len()
    median_digit_length = float(digit_lengths.median()) if len(digit_lengths) else 0.0
    digit_length_span = int(digit_lengths.max() - digit_lengths.min()) if len(digit_lengths) else 0
    dominant_digit_length_ratio = (
        float(digit_lengths.value_counts(normalize=True).iloc[0])
        if len(digit_lengths)
        else 0.0
    )
    leading_zero_ratio = (
        float(digit_tokens.str.startswith("0").mean())
        if len(digit_tokens)
        else 0.0
    )
    numeric_full = pd.to_numeric(compact_tokens, errors="coerce").dropna()
    unique_numeric_count = int(numeric_full.nunique()) if len(numeric_full) else 0
    if unique_numeric_count > 0:
        numeric_min = float(numeric_full.min())
        numeric_max = float(numeric_full.max())
        numeric_range = max(0.0, numeric_max - numeric_min)
        range_coverage_ratio = unique_numeric_count / max(1.0, numeric_range + 1.0)
        unique_sorted = np.sort(numeric_full.unique())
        positive_gaps = np.diff(unique_sorted)
        positive_gaps = positive_gaps[positive_gaps > 0]
        median_unique_gap = float(np.median(positive_gaps)) if len(positive_gaps) else 0.0
    else:
        numeric_range = 0.0
        range_coverage_ratio = 0.0
        median_unique_gap = 0.0

    low_cardinality_numeric_dimension = distinct_count <= 20 and row_count > 50 and distinct_ratio < 0.1
    high_uniqueness_integer_identifier = (
        integer_like_ratio >= 0.95
        and decimal_like_ratio <= 0.02
        and distinct_ratio >= 0.85
        and median_digit_length >= 4
    )
    structured_integer_code = (
        integer_like_ratio >= 0.9
        and decimal_like_ratio <= 0.05
        and median_digit_length >= 4
        and (
            leading_zero_ratio >= 0.05
            or dominant_digit_length_ratio >= 0.8
            or (
                distinct_ratio <= 0.2
                and dominant_digit_length_ratio >= 0.6
            )
            or (
                distinct_ratio <= 0.2
                and range_coverage_ratio <= 0.001
                and median_unique_gap >= 10
            )
            or (
                distinct_ratio <= 0.05
                and numeric_range > 10000
                and median_digit_length >= 5
            )
        )
    )
    identifier_like_numeric = high_uniqueness_integer_identifier or structured_integer_code

    if boolean_ratio == 1.0 and sample_total > 0:
        inferred_type = "boolean"
        semantic_role = "boolean"
    elif datetime_ratio >= 0.85 and sample_total >= 3 and text_like_ratio <= 0.15 and not identifier_like_numeric:
        inferred_type = "datetime"
        semantic_role = "temporal"
    elif numeric_ratio >= 0.85 and sample_total >= 3:
        if low_cardinality_numeric_dimension:
            inferred_type = "categorical"
            semantic_role = "dimension"
        elif identifier_like_numeric:
            inferred_type = "identifier"
            semantic_role = "identifier"
        else:
            inferred_type = "number"
            semantic_role = "metric"
    else:
        inferred_type = "text"
        semantic_role = "dimension"

    metric_candidate = semantic_role == "metric"
    date_candidate = semantic_role == "temporal"

    return {
        "inferred_type": inferred_type,
        "semantic_role": semantic_role,
        "sample_total": sample_total,
        "numeric_ratio": numeric_ratio,
        "datetime_ratio": datetime_ratio,
        "boolean_ratio": boolean_ratio,
        "text_like_ratio": text_like_ratio,
        "distinct_count": distinct_count,
        "distinct_ratio": distinct_ratio,
        "metric_candidate": metric_candidate,
        "date_candidate": date_candidate,
        "identifier_like_numeric": identifier_like_numeric,
    }


def _build_quality_profile(df: pd.DataFrame, normalized_columns: list[str]) -> dict[str, Any]:
    row_count = int(len(df.index))
    column_count = int(len(normalized_columns))
    if row_count == 0 or column_count == 0:
        return {
            "health_score": 15,
            "health_status": "critical",
            "null_cell_count": 0,
            "null_cell_ratio": 0.0,
            "duplicate_row_count": 0,
            "duplicate_row_ratio": 0.0,
            "ambiguous_column_count": 0,
            "invalid_date_column_count": 0,
            "outlier_column_count": 0,
            "alert_count": 1,
            "alerts": [
                {
                    "code": "empty_dataset",
                    "severity": "critical",
                    "title": "Dataset vacío",
                    "message": "El archivo no contiene filas utilizables para un análisis confiable.",
                    "affected_columns": [],
                }
            ],
            "column_issues": [],
        }

    normalized_df = df.copy()
    for column_name in normalized_columns:
        if column_name in normalized_df.columns:
            normalized_df[column_name] = _normalize_missing_series(normalized_df[column_name])

    total_cells = max(1, row_count * column_count)
    null_cell_count = int(normalized_df.isna().sum().sum())
    null_cell_ratio = round(null_cell_count / total_cells, 4)
    duplicate_row_count = int(normalized_df.duplicated(keep="first").sum())
    duplicate_row_ratio = round(duplicate_row_count / max(1, row_count), 4)

    duplicate_name_buckets: dict[str, list[str]] = {}
    for column_name in normalized_columns:
        duplicate_name_buckets.setdefault(_normalize_column_key(column_name), []).append(column_name)
    duplicated_header_columns = sorted({
        column_name
        for group in duplicate_name_buckets.values()
        if len(group) > 1
        for column_name in group
    })

    alerts: list[dict[str, Any]] = []
    column_issues: list[dict[str, Any]] = []
    ambiguous_columns: list[str] = []
    invalid_date_columns: list[str] = []
    outlier_columns: list[str] = []
    high_null_columns: list[str] = []
    generic_name_columns: list[str] = []

    for column_name in normalized_columns:
        if column_name not in normalized_df.columns:
            continue

        series = normalized_df[column_name]
        semantics = _build_series_semantics(series, row_count)
        inferred_type = str(semantics["inferred_type"])
        non_null_count = int(series.notna().sum())
        null_count = int(series.isna().sum())
        null_ratio = round(null_count / max(1, row_count), 4)
        distinct_count = int(series.dropna().nunique())
        issue_flags: list[str] = []
        invalid_count = 0
        outlier_count = 0

        sample_total = int(semantics["sample_total"])
        numeric_ratio = float(semantics["numeric_ratio"])
        datetime_ratio = float(semantics["datetime_ratio"])
        text_like_ratio = float(semantics["text_like_ratio"])

        if null_ratio >= 0.2:
            issue_flags.append("high_nulls")
            high_null_columns.append(column_name)

        if _is_generic_column_name(column_name):
            issue_flags.append("generic_name")
            generic_name_columns.append(column_name)

        mixed_numeric_text = 0.15 <= numeric_ratio <= 0.85 and text_like_ratio >= 0.15
        mixed_datetime_text = 0.15 <= datetime_ratio <= 0.85 and text_like_ratio >= 0.15
        if inferred_type == "text" and sample_total >= 6 and (mixed_numeric_text or mixed_datetime_text):
            issue_flags.append("ambiguous_content")
            ambiguous_columns.append(column_name)

        if duplicated_header_columns and column_name in duplicated_header_columns:
            if "duplicated_header" not in issue_flags:
                issue_flags.append("duplicated_header")

        date_candidate = bool(semantics["date_candidate"])
        if date_candidate and non_null_count > 0:
            parsed_dates = pd.to_datetime(series, errors="coerce", format="mixed")
            invalid_count = int(parsed_dates.isna().sum() - null_count)
            if invalid_count > 0:
                issue_flags.append("invalid_dates")
                invalid_date_columns.append(column_name)

        numeric_series = pd.to_numeric(series, errors="coerce")
        valid_numeric = numeric_series.dropna()
        numeric_candidate = bool(semantics["metric_candidate"])
        if numeric_candidate and len(valid_numeric) >= 8:
            q1 = float(valid_numeric.quantile(0.25))
            q3 = float(valid_numeric.quantile(0.75))
            iqr = q3 - q1
            if np.isfinite(iqr) and iqr > 0:
                lower_bound = q1 - 1.5 * iqr
                upper_bound = q3 + 1.5 * iqr
                outlier_mask = (valid_numeric < lower_bound) | (valid_numeric > upper_bound)
                outlier_count = int(outlier_mask.sum())
                if outlier_count > 0:
                    issue_flags.append("extreme_outliers")
                    outlier_columns.append(column_name)

        if issue_flags:
            column_issues.append({
                "name": column_name,
                "inferred_type": inferred_type,
                "non_null_count": non_null_count,
                "null_count": null_count,
                "null_ratio": null_ratio,
                "distinct_count": distinct_count,
                "invalid_count": invalid_count,
                "outlier_count": outlier_count,
                "issue_flags": issue_flags,
            })

    if null_cell_ratio >= 0.1:
        alerts.append({
            "code": "missing_values",
            "severity": "warning" if null_cell_ratio < 0.25 else "critical",
            "title": "Valores faltantes relevantes",
            "message": f"El dataset presenta {null_cell_count:,} celdas vacías ({null_cell_ratio * 100:.1f}% del total).",
            "affected_columns": sorted(set(high_null_columns))[:8],
        })

    if duplicate_row_count > 0:
        alerts.append({
            "code": "duplicate_rows",
            "severity": "warning" if duplicate_row_ratio < 0.1 else "critical",
            "title": "Filas exactamente duplicadas detectadas",
            "message": f"Se encontraron {duplicate_row_count:,} filas exactamente idénticas ({duplicate_row_ratio * 100:.1f}% del dataset).",
            "affected_columns": [],
        })

    if ambiguous_columns or duplicated_header_columns or generic_name_columns:
        affected_columns = sorted(set(ambiguous_columns + duplicated_header_columns + generic_name_columns))
        alerts.append({
            "code": "ambiguous_columns",
            "severity": "warning",
            "title": "Columnas ambiguas o poco confiables",
            "message": "Algunas columnas tienen nombres genéricos, cabeceras duplicadas o contenido mixto que puede degradar la interpretación analítica.",
            "affected_columns": affected_columns[:8],
        })

    if invalid_date_columns:
        alerts.append({
            "code": "invalid_dates",
            "severity": "warning" if len(invalid_date_columns) <= 2 else "critical",
            "title": "Fechas inválidas o inconsistentes",
            "message": "Se detectaron columnas temporales con valores que no pudieron convertirse a fecha de forma consistente.",
            "affected_columns": sorted(set(invalid_date_columns))[:8],
        })

    if outlier_columns:
        alerts.append({
            "code": "extreme_outliers",
            "severity": "warning",
            "title": "Outliers extremos detectados",
            "message": "Existen columnas numéricas con valores extremos que pueden sesgar promedios, escalas o recomendaciones visuales.",
            "affected_columns": sorted(set(outlier_columns))[:8],
        })

    deductions = 0.0
    deductions += min(35.0, null_cell_ratio * 120.0)
    deductions += min(20.0, duplicate_row_ratio * 150.0)
    deductions += min(14.0, len(set(ambiguous_columns + duplicated_header_columns + generic_name_columns)) * 4.0)
    deductions += min(16.0, len(set(invalid_date_columns)) * 6.0)
    deductions += min(12.0, len(set(outlier_columns)) * 3.0)
    health_score = max(5, int(round(100.0 - deductions)))

    if health_score >= 85:
        health_status = "healthy"
    elif health_score >= 65:
        health_status = "warning"
    else:
        health_status = "critical"

    sorted_column_issues = sorted(
        column_issues,
        key=lambda issue: (
            len(issue["issue_flags"]),
            issue["null_ratio"],
            issue["invalid_count"],
            issue["outlier_count"],
        ),
        reverse=True,
    )[:QUALITY_COLUMN_ISSUE_LIMIT]

    return {
        "health_score": health_score,
        "health_status": health_status,
        "null_cell_count": null_cell_count,
        "null_cell_ratio": null_cell_ratio,
        "duplicate_row_count": duplicate_row_count,
        "duplicate_row_ratio": duplicate_row_ratio,
        "ambiguous_column_count": int(len(set(ambiguous_columns + duplicated_header_columns + generic_name_columns))),
        "invalid_date_column_count": int(len(set(invalid_date_columns))),
        "outlier_column_count": int(len(set(outlier_columns))),
        "alert_count": int(len(alerts)),
        "alerts": alerts,
        "column_issues": sorted_column_issues,
    }


def _normalize_cell_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and np.isnan(value):
        return None
    if pd.isna(value):
        return None
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.isoformat()
    if isinstance(value, np.generic):
        return value.item()
    return value


def build_file_preview_payload(
    *,
    file_id: str,
    file_name: str,
    file_bytes: bytes,
    created_at: str | None = None,
    preview_limit: int = PREVIEW_ROW_LIMIT,
) -> dict[str, Any]:
    dfs = DataEngine.read_file(file_bytes, file_name)
    selected_sheet, df = _pick_preview_sheet(dfs)

    if df is None:
        df = pd.DataFrame()

    normalized_columns = [str(column) for column in df.columns.tolist()]
    if normalized_columns:
        df = df.copy()
        df.columns = normalized_columns

    preview_df = df.head(preview_limit).copy()
    preview_rows = []
    for row in preview_df.to_dict(orient="records"):
        preview_rows.append({
            str(column_name): _normalize_cell_value(value)
            for column_name, value in row.items()
        })

    columns = [
        {
            "name": column_name,
            "inferred_type": infer_series_type(df[column_name]) if column_name in df.columns else "text",
        }
        for column_name in normalized_columns
    ]

    quality_profile = _build_quality_profile(df, normalized_columns)

    return {
        "file_id": file_id,
        "file_name": file_name,
        "selected_sheet": selected_sheet,
        "row_count": int(len(df.index)),
        "column_count": int(len(normalized_columns)),
        "preview_limit": int(preview_limit),
        "file_size_bytes": int(len(file_bytes)),
        "created_at": created_at,
        "columns": columns,
        "rows": preview_rows,
        "quality_profile": quality_profile,
    }
