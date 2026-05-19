from __future__ import annotations

import warnings

import pandas as pd

from app.services.canonical_schema_profiler import build_canonical_schema_profile
from app.services.canonical_temporal_inference import apply_temporal_coercion, infer_temporal_series


def test_phase8_temporal_inference_detects_year_first_dates_without_warnings() -> None:
    series = pd.Series(["2026-01-01", "2026-01-02", "2026-01-03"])
    with warnings.catch_warnings(record=True) as captured:
        result = infer_temporal_series(series)

    assert result["detected"] is True
    assert result["format_family"] == "year_first"
    assert not captured


def test_phase8_temporal_inference_resolves_dayfirst_with_disambiguating_values() -> None:
    series = pd.Series(["13/01/2026", "14/01/2026", "15/01/2026"])
    result = infer_temporal_series(series)

    assert result["detected"] is True
    assert result["day_month_order"] == "dayfirst"


def test_phase8_temporal_inference_rejects_ambiguous_numeric_dates() -> None:
    series = pd.Series(["01/02/2026", "02/03/2026", "03/04/2026"])
    result = infer_temporal_series(series)

    assert result["detected"] is False
    assert result["ambiguous"] is True


def test_phase8_schema_profiler_coerces_temporal_columns_without_dataengine_warning() -> None:
    df = pd.DataFrame(
        [
            {"fecha": "2026-01-01", "region": "North", "amount": 100},
            {"fecha": "2026-01-02", "region": "South", "amount": 120},
        ]
    )
    with warnings.catch_warnings(record=True) as captured:
        working_df, schema_profile, temporal_report = build_canonical_schema_profile(df)

    assert pd.api.types.is_datetime64_any_dtype(working_df["fecha"])
    assert schema_profile["fecha"]["role"] == "date"
    assert temporal_report["fecha"]["detected"] is True
    assert not captured
