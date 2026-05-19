from __future__ import annotations

import re
from typing import Any

import pandas as pd

from app.core.config import settings
from app.services.canonical_header_normalizer import compact_header_semantic_text


_SUSPICIOUS_METRIC_FRAGMENTS = {
    "address",
    "code",
    "email",
    "fax",
    "geolocation",
    "geometry",
    "id",
    "key",
    "label",
    "location",
    "manager",
    "name",
    "phone",
    "sku",
    "status",
    "type",
    "ubicacion",
    "url",
    "warehouse",
    "zip",
    "zipcode",
}
_TRUSTED_METRIC_FRAGMENTS = {
    "amount",
    "avg",
    "balance",
    "burn",
    "calories",
    "cost",
    "count",
    "discount",
    "duration",
    "hours",
    "importe",
    "income",
    "ingreso",
    "margin",
    "margen",
    "monto",
    "pct",
    "percent",
    "porcentaje",
    "price",
    "precio",
    "qty",
    "quantity",
    "rate",
    "revenue",
    "sale",
    "sales",
    "score",
    "stock",
    "subtotal",
    "tiempo",
    "time",
    "total",
    "units",
    "value",
    "variacion",
    "variation",
    "volume",
    "weight",
}


def is_canonical_shadow_metric_validity_gate_enabled() -> bool:
    return settings.CANONICAL_SHADOW_METRIC_VALIDITY_GATE_ENABLED


def _semantic_name(value: Any) -> str:
    return compact_header_semantic_text(value)


def _looks_trusted_metric(column_name: str) -> bool:
    name = _semantic_name(column_name)
    return any(fragment in name for fragment in _TRUSTED_METRIC_FRAGMENTS)


def _looks_suspicious_metric(column_name: str) -> bool:
    name = _semantic_name(column_name)
    return any(fragment in name for fragment in _SUSPICIOUS_METRIC_FRAGMENTS)


def _parse_numeric_token(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None

    negative = text.startswith("(") and text.endswith(")")
    cleaned = text.replace("(", "").replace(")", "")
    cleaned = re.sub(r"(?i)\b(?:usd|pen|eur|soles?)\b", "", cleaned)
    cleaned = cleaned.replace("S/.", "").replace("s/.", "").replace("S/", "").replace("s/", "")
    cleaned = cleaned.replace("$", "").replace("€", "").replace("£", "")
    cleaned = cleaned.replace("\u00a0", "").replace(" ", "")
    cleaned = "".join(character for character in cleaned if character.isdigit() or character in {".", ",", "-", "+"})
    if not any(character.isdigit() for character in cleaned):
        return None

    if cleaned.count(",") and cleaned.count("."):
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif cleaned.count(","):
        if cleaned.count(",") == 1 and len(cleaned.rsplit(",", 1)[-1]) <= 2:
            cleaned = cleaned.replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif cleaned.count(".") > 1:
        last_segment = cleaned.rsplit(".", 1)[-1]
        if len(last_segment) == 3:
            cleaned = cleaned.replace(".", "")
        else:
            head, tail = cleaned.rsplit(".", 1)
            cleaned = head.replace(".", "") + "." + tail

    try:
        value_num = float(cleaned)
    except Exception:
        return None
    return -value_num if negative and value_num > 0 else value_num


def _coerce_numeric_series(series: pd.Series) -> tuple[pd.Series, float]:
    if pd.api.types.is_numeric_dtype(series):
        coerced = pd.to_numeric(series, errors="coerce")
        non_null = int(series.notna().sum())
        ratio = 1.0 if non_null > 0 else 0.0
        return coerced, ratio

    text_series = series.astype("string").fillna("").str.strip()
    non_empty_mask = text_series != ""
    non_empty_count = int(non_empty_mask.sum())
    if non_empty_count == 0:
        return pd.to_numeric(series, errors="coerce"), 0.0

    parsed = text_series.map(_parse_numeric_token)
    ratio = float(parsed[non_empty_mask].notna().sum()) / float(non_empty_count)
    return pd.to_numeric(parsed, errors="coerce"), ratio


def _refresh_profile_stats(info: dict[str, Any], series: pd.Series, *, total_rows: int) -> dict[str, Any]:
    refreshed = dict(info)
    refreshed["cardinality"] = int(series.nunique(dropna=True))
    refreshed["cardinality_ratio"] = round(refreshed["cardinality"] / max(int(total_rows or 0), 1), 3)
    return refreshed


def _downgraded_role(column_name: str, info: dict[str, Any]) -> tuple[str, str]:
    cardinality_ratio = float(info.get("cardinality_ratio") or 0.0)
    if _looks_suspicious_metric(column_name) or cardinality_ratio >= 0.95:
        return "identifier", "id"
    return "dimension", "categorical"


def apply_canonical_shadow_metric_validity_gate(
    dataframe: pd.DataFrame,
    schema_profile: dict[str, Any],
) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any]]:
    if not settings.CANONICAL_SHADOW_METRIC_VALIDITY_GATE_ENABLED:
        return dataframe, schema_profile, {
            "applied": False,
            "safe_metric_columns": [],
            "blocked_metric_columns": [],
            "promoted_metric_columns": [],
            "coerced_metric_columns": [],
        }

    working_df = dataframe.copy()
    sanitized_profile: dict[str, Any] = {
        str(column_name): dict(info or {})
        for column_name, info in dict(schema_profile or {}).items()
    }
    safe_metric_columns: list[str] = []
    blocked_metric_columns: list[str] = []
    promoted_metric_columns: list[str] = []
    coerced_metric_columns: list[str] = []
    column_reports: dict[str, Any] = {}
    total_rows = len(working_df.index)
    total_columns = len(working_df.columns)
    has_date_signal = any(
        str(info.get("role") or "").strip().lower() == "date"
        for info in sanitized_profile.values()
        if isinstance(info, dict)
    )

    for column_name, info in list(sanitized_profile.items()):
        if column_name not in working_df.columns or not isinstance(info, dict):
            continue

        role = str(info.get("role") or "").strip().lower()
        if role == "date":
            continue

        series = working_df[column_name]
        trusted_metric_name = _looks_trusted_metric(column_name)
        suspicious_metric_name = _looks_suspicious_metric(column_name)
        cardinality_ratio = float(info.get("cardinality_ratio") or 0.0)
        coerced_series, parseable_ratio = _coerce_numeric_series(series)
        is_numeric_metric = pd.api.types.is_numeric_dtype(series)
        should_keep_metric = False
        should_promote_metric = False

        if role == "metric":
            should_keep_metric = is_numeric_metric or (
                parseable_ratio >= settings.CANONICAL_SHADOW_METRIC_VALIDITY_MIN_PARSEABLE_RATIO
                and not (suspicious_metric_name and not trusted_metric_name and cardinality_ratio >= 0.85)
            )
        elif role in {"dimension", "identifier", "unknown"}:
            should_promote_metric = (
                parseable_ratio >= settings.CANONICAL_SHADOW_METRIC_PROMOTION_MIN_PARSEABLE_RATIO
                and not suspicious_metric_name
                and (
                    trusted_metric_name
                    or total_columns == 1
                    or (has_date_signal and total_columns <= 3)
                )
            )

        if should_keep_metric or should_promote_metric:
            if not pd.api.types.is_numeric_dtype(series):
                working_df[column_name] = coerced_series
                coerced_metric_columns.append(column_name)
            updated_info = _refresh_profile_stats(info, working_df[column_name], total_rows=total_rows)
            updated_info["type"] = "numeric"
            updated_info["role"] = "metric"
            sanitized_profile[column_name] = updated_info
            safe_metric_columns.append(column_name)
            if should_promote_metric:
                promoted_metric_columns.append(column_name)
            column_reports[column_name] = {
                "action": "promoted_to_metric" if should_promote_metric else "metric_validated",
                "parseable_ratio": round(parseable_ratio, 4),
                "trusted_metric_name": trusted_metric_name,
                "suspicious_metric_name": suspicious_metric_name,
            }
            continue

        if role == "metric":
            downgraded_role, downgraded_type = _downgraded_role(column_name, info)
            updated_info = _refresh_profile_stats(info, series, total_rows=total_rows)
            updated_info["role"] = downgraded_role
            updated_info["type"] = downgraded_type
            sanitized_profile[column_name] = updated_info
            blocked_metric_columns.append(column_name)
            column_reports[column_name] = {
                "action": "metric_blocked",
                "parseable_ratio": round(parseable_ratio, 4),
                "trusted_metric_name": trusted_metric_name,
                "suspicious_metric_name": suspicious_metric_name,
                "downgraded_role": downgraded_role,
            }

    report = {
        "applied": True,
        "safe_metric_columns": sorted(dict.fromkeys(safe_metric_columns)),
        "blocked_metric_columns": sorted(dict.fromkeys(blocked_metric_columns)),
        "promoted_metric_columns": sorted(dict.fromkeys(promoted_metric_columns)),
        "coerced_metric_columns": sorted(dict.fromkeys(coerced_metric_columns)),
        "column_reports": column_reports,
    }
    return working_df, sanitized_profile, report
