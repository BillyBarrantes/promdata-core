from __future__ import annotations

from typing import Any

import pandas as pd

from app.services.canonical_temporal_inference import apply_temporal_coercion
from app.services.data_engine import DataEngine


_SEMANTIC_DIMENSION_TOKENS = {
    "ubicacion",
    "location",
    "localizacion",
    "localidad",
    "site",
    "warehouse",
    "almacen",
    "store",
    "branch",
    "sucursal",
    "region",
    "zona",
    "area",
    "department",
    "departamento",
    "team",
    "channel",
    "canal",
    "segment",
    "segmento",
    "category",
    "categoria",
    "family",
    "familia",
    "city",
    "ciudad",
    "province",
    "provincia",
}


def _has_dimension_semantic_name(column_name: str) -> bool:
    return bool(set(DataEngine._semantic_tokens(column_name)) & _SEMANTIC_DIMENSION_TOKENS)


def _looks_like_integer_coded_dimension(series: pd.Series) -> bool:
    numeric_values = pd.to_numeric(series.dropna(), errors="coerce").dropna()
    if numeric_values.empty:
        return False
    integer_like_ratio = float(((numeric_values - numeric_values.round()).abs() < 1e-9).mean())
    return integer_like_ratio >= 0.95


def _stringify_numeric_dimension(series: pd.Series) -> pd.Series:
    def _normalize_value(value: Any) -> Any:
        if pd.isna(value):
            return pd.NA
        numeric_value = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
        if pd.notna(numeric_value):
            rounded_value = round(float(numeric_value))
            if abs(float(numeric_value) - rounded_value) < 1e-9:
                return str(int(rounded_value))
        return str(value).strip()

    return series.map(_normalize_value).astype("string")


def build_canonical_schema_profile(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any]]:
    working_df, temporal_report = apply_temporal_coercion(df)
    schema_profile: dict[str, Any] = {}
    total_rows = max(len(working_df), 1)

    for column_name in working_df.columns:
        if str(column_name).startswith("_") or column_name == "is_latest_snapshot":
            continue

        series = working_df[column_name]
        cardinality = int(series.nunique())
        cardinality_ratio = round(cardinality / total_rows, 3)
        info = {"type": "unknown", "role": "unknown", "cardinality": cardinality, "cardinality_ratio": cardinality_ratio}

        temporal_info = temporal_report.get(column_name, {})
        if pd.api.types.is_datetime64_any_dtype(series) or temporal_info.get("detected"):
            info["type"] = "temporal"
            info["role"] = "date"
            schema_profile[column_name] = info
            continue

        if pd.api.types.is_numeric_dtype(series):
            if (
                _has_dimension_semantic_name(column_name)
                and not DataEngine._has_measure_semantic_name(column_name)
                and not DataEngine._has_identifier_semantic_name(column_name)
                and _looks_like_integer_coded_dimension(series)
            ):
                working_df[column_name] = _stringify_numeric_dimension(series)
                info["type"] = "categorical"
                info["role"] = "dimension"
                schema_profile[column_name] = info
                continue
            sample = series.dropna().astype(str)
            if pd.api.types.is_integer_dtype(series):
                if DataEngine._should_force_identifier_from_uniqueness(column_name, sample, cardinality, cardinality_ratio):
                    info["type"] = "id"
                    info["role"] = "identifier"
                elif cardinality <= 20 and total_rows > 50 and cardinality_ratio < 0.1:
                    info["type"] = "categorical"
                    info["role"] = "dimension"
                else:
                    info["type"] = "numeric"
                    info["role"] = "metric"
            else:
                info["type"] = "numeric"
                info["role"] = "metric"
            schema_profile[column_name] = info
            continue

        sample = series.dropna().astype(str).str.strip()
        sample = sample[sample != ""]
        sample_size = max(len(sample), 1)
        if sample.empty:
            info["type"] = "categorical"
            info["role"] = "dimension"
            schema_profile[column_name] = info
            continue

        if DataEngine._has_identifier_semantic_name(column_name):
            info["type"] = "id"
            info["role"] = "identifier"
            schema_profile[column_name] = info
            continue

        if _has_dimension_semantic_name(column_name) and not DataEngine._has_measure_semantic_name(column_name):
            working_df[column_name] = series.astype("string").str.strip()
            info["type"] = "categorical"
            info["role"] = "dimension"
            schema_profile[column_name] = info
            continue

        has_words = sample.str.contains(r"[a-zA-ZáéíóúñÑÁÉÍÓÚ]{2,}", regex=True)
        word_ratio = has_words.sum() / sample_size
        clean_nums = sample.str.replace(r"[^\d.,-]", "", regex=True)
        nums = pd.to_numeric(clean_nums.str.replace(",", ".", regex=False), errors="coerce")
        num_ratio = nums.notna().sum() / sample_size

        if word_ratio > 0.3:
            if cardinality_ratio > 0.9 and cardinality > 50:
                info["type"] = "id"
                info["role"] = "identifier"
            else:
                info["type"] = "categorical"
                info["role"] = "dimension"
        elif num_ratio > 0.8:
            if DataEngine._should_force_identifier_from_uniqueness(column_name, sample, cardinality, cardinality_ratio):
                info["type"] = "id"
                info["role"] = "identifier"
            elif cardinality <= 20 and total_rows > 50 and cardinality_ratio < 0.1:
                info["type"] = "categorical"
                info["role"] = "dimension"
            else:
                info["type"] = "numeric"
                info["role"] = "metric"
        else:
            info["type"] = "categorical"
            info["role"] = "dimension"

        schema_profile[column_name] = info

    return working_df, schema_profile, temporal_report
