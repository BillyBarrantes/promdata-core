from __future__ import annotations

from datetime import datetime
import re
from typing import Any

import pandas as pd


_YEAR_FIRST_PATTERN = re.compile(r"^\d{4}[-/.]\d{1,2}[-/.]\d{1,2}$")
_DAY_MONTH_YEAR_PATTERN = re.compile(r"^\d{1,2}[-/.]\d{1,2}[-/.]\d{4}$")
_COMPACT_DATE_PATTERN = re.compile(r"^\d{8}$")
_TIMESTAMP_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}[ t]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:z|[+-]\d{2}:\d{2})?$",
    re.IGNORECASE,
)
_TEXTUAL_MONTH_PATTERN = re.compile(r"^\d{1,2}\s+[A-Za-z]{3,12}\s+\d{4}$")


def _non_empty_text_values(series: pd.Series) -> list[str]:
    cleaned = []
    for value in series.dropna().tolist():
        text = str(value or "").strip()
        if not text:
            continue
        cleaned.append(text)
    return cleaned


def _try_parse_exact(text: str, formats: list[str]) -> datetime | None:
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt)
        except Exception:
            continue
    return None


def _infer_day_month_order(values: list[str]) -> str | None:
    dayfirst_votes = 0
    monthfirst_votes = 0
    for value in values:
        if not _DAY_MONTH_YEAR_PATTERN.match(value):
            continue
        parts = re.split(r"[-/.]", value)
        if len(parts) != 3:
            continue
        try:
            first = int(parts[0])
            second = int(parts[1])
        except Exception:
            continue
        if first > 12 and second <= 12:
            dayfirst_votes += 1
        elif second > 12 and first <= 12:
            monthfirst_votes += 1
    if dayfirst_votes and monthfirst_votes:
        return None
    if dayfirst_votes:
        return "dayfirst"
    if monthfirst_votes:
        return "monthfirst"
    return None


def _parse_text_value(text: str, *, day_month_order: str | None = None) -> datetime | None:
    normalized = str(text or "").strip()
    if not normalized:
        return None

    lower = normalized.lower()
    if _TIMESTAMP_PATTERN.match(normalized):
        candidate = normalized.replace("Z", "+00:00").replace("z", "+00:00")
        try:
            return datetime.fromisoformat(candidate.replace(" ", "T"))
        except Exception:
            pass
        return _try_parse_exact(
            normalized,
            [
                "%Y-%m-%d %H:%M",
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M",
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%dT%H:%M:%S.%f",
            ],
        )

    if _YEAR_FIRST_PATTERN.match(normalized):
        return _try_parse_exact(
            normalized,
            ["%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"],
        )

    if _COMPACT_DATE_PATTERN.match(normalized):
        return _try_parse_exact(normalized, ["%Y%m%d"])

    if _TEXTUAL_MONTH_PATTERN.match(normalized):
        return _try_parse_exact(
            normalized,
            ["%d %b %Y", "%d %B %Y"],
        )

    if _DAY_MONTH_YEAR_PATTERN.match(normalized):
        if day_month_order == "dayfirst":
            return _try_parse_exact(
                normalized,
                ["%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y"],
            )
        if day_month_order == "monthfirst":
            return _try_parse_exact(
                normalized,
                ["%m-%d-%Y", "%m/%d/%Y", "%m.%d.%Y"],
            )
        return None

    if lower in {"today", "yesterday", "tomorrow"}:
        return None

    return None


def infer_temporal_series(series: pd.Series) -> dict[str, Any]:
    if pd.api.types.is_datetime64_any_dtype(series):
        return {
            "detected": True,
            "coerced": False,
            "parse_ratio": 1.0,
            "row_count": int(series.notna().sum()),
            "format_family": "native_datetime",
            "day_month_order": None,
            "ambiguous": False,
            "parsed_series": series,
        }

    values = _non_empty_text_values(series)
    if not values:
        return {
            "detected": False,
            "coerced": False,
            "parse_ratio": 0.0,
            "row_count": 0,
            "format_family": None,
            "day_month_order": None,
            "ambiguous": False,
            "parsed_series": series,
        }

    day_month_order = _infer_day_month_order(values)
    ambiguous_pattern = any(_DAY_MONTH_YEAR_PATTERN.match(value) for value in values) and day_month_order is None
    parsed_values: dict[str, datetime | None] = {}
    parsed_count = 0

    for value in values:
        if value not in parsed_values:
            parsed_values[value] = _parse_text_value(value, day_month_order=day_month_order)
        if parsed_values[value] is not None:
            parsed_count += 1

    parse_ratio = parsed_count / max(len(values), 1)
    detected = parse_ratio >= 0.8 and not ambiguous_pattern
    if not detected:
        return {
            "detected": False,
            "coerced": False,
            "parse_ratio": round(parse_ratio, 4),
            "row_count": len(values),
            "format_family": "ambiguous_numeric_date" if ambiguous_pattern else None,
            "day_month_order": day_month_order,
            "ambiguous": ambiguous_pattern,
            "parsed_series": series,
        }

    coerced_series = series.apply(
        lambda value: pd.Timestamp(parsed_values.get(str(value).strip())) if str(value or "").strip() in parsed_values and parsed_values.get(str(value).strip()) is not None else pd.NaT
        if value is not None and str(value).strip()
        else pd.NaT
    )
    format_family = "year_first"
    if day_month_order == "dayfirst":
        format_family = "day_month_year"
    elif day_month_order == "monthfirst":
        format_family = "month_day_year"
    elif any(_TIMESTAMP_PATTERN.match(value) for value in values):
        format_family = "timestamp"
    elif any(_TEXTUAL_MONTH_PATTERN.match(value) for value in values):
        format_family = "textual_month"
    elif all(_COMPACT_DATE_PATTERN.match(value) for value in values):
        format_family = "compact_yyyymmdd"

    return {
        "detected": True,
        "coerced": True,
        "parse_ratio": round(parse_ratio, 4),
        "row_count": len(values),
        "format_family": format_family,
        "day_month_order": day_month_order,
        "ambiguous": False,
        "parsed_series": coerced_series,
    }


def apply_temporal_coercion(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    working_df = df.copy()
    report: dict[str, Any] = {}
    for column_name in working_df.columns:
        series = working_df[column_name]
        result = infer_temporal_series(series)
        report[column_name] = {
            key: value
            for key, value in result.items()
            if key != "parsed_series"
        }
        if result.get("detected") and result.get("coerced"):
            working_df[column_name] = result["parsed_series"]
    return working_df, report
