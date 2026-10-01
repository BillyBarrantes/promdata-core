"""
[QW-1] Regression test: anomaly path must return ONLY anomalous rows.

Bug (Sprint 0 / QW-1): `IbisEngine._analyze_predictive` (branch 'anomalies')
did `anomaly_result.head(50)` over the FULL DataFrame returned by
`PredictiveEngine.detect_anomalies` (which contains normal + anomalous rows),
labeling the first 50 dataset rows as anomalies. `hard_facts.total_anomalies`
reported the total dataset row count.

This test places a single clear outlier at row index 200 (beyond the old
head(50) window). With the old code the outlier was NEVER surfaced and 50
normal rows were mislabeled as anomalies. With the fix, only flagged rows
(is_anomaly=True) reach the chart and counts reflect real anomalies.
"""
from types import SimpleNamespace

import pandas as pd
import pytest

from app.services.predictive_engine import PredictiveEngine


pytestmark = pytest.mark.skipif(
    not PredictiveEngine.is_available(),
    reason="statsmodels/sklearn no instalados en este entorno",
)


def _build_df_with_late_outlier() -> pd.DataFrame:
    """300 normal rows (value ~10) + one huge outlier at index 200."""
    dates = pd.date_range(start="2024-01-01", periods=301, freq="D")
    values = [10.0 + (i % 3) * 0.1 for i in range(301)]
    values[200] = 10_000.0  # outlier far beyond the old head(50) window
    return pd.DataFrame({"fecha": dates, "monto": values})


def test_anomaly_path_returns_only_true_anomalies():
    ibis = pytest.importorskip("ibis")
    from app.services.ibis_engine import IbisEngine

    df = _build_df_with_late_outlier()
    t = ibis.memtable(df)
    intent = SimpleNamespace(
        date_column="fecha",
        value_column="monto",
        analysis_subtype="anomalies",
    )

    result = IbisEngine._analyze_predictive(t, intent)

    assert "error" not in result, f"Expected anomalies chart, got: {result}"
    assert result["type"] == "echarts"
    assert result["chart_type"] == "scatter"

    chart_values = [row["value"] for row in result["data"]]

    # 1. The real outlier (row 200) MUST be surfaced.
    assert 10_000.0 in chart_values, (
        "Outlier at row 200 not surfaced — regression to head(50) behavior"
    )

    # 2. Only anomalous rows are returned: the bulk of normal values (~10)
    #    must not dominate the payload. Old bug returned exactly 50 normal rows.
    assert len(result["data"]) < 50, (
        f"Returned {len(result['data'])} rows — likely mislabeled normal rows"
    )

    # 3. hard_facts report real anomaly counts, not total dataset rows.
    facts = result["hard_facts"]
    assert facts["total_anomalies"] == len(result["data"]) or facts["shown"] <= facts["total_anomalies"]
    assert facts["total_anomalies"] < len(df), (
        "total_anomalies must not equal dataset row count"
    )


def test_anomaly_path_without_flags_soft_fails():
    """If the detector returns no flags, respond honestly (no fake anomalies)."""
    ibis = pytest.importorskip("ibis")
    from app.services.ibis_engine import IbisEngine

    # Constant series: no anomalies possible.
    df = pd.DataFrame(
        {"fecha": pd.date_range("2024-01-01", periods=30, freq="D"),
         "monto": [5.0] * 30}
    )
    t = ibis.memtable(df)
    intent = SimpleNamespace(
        date_column="fecha",
        value_column="monto",
        analysis_subtype="anomalies",
    )

    result = IbisEngine._analyze_predictive(t, intent)

    # Either a valid (empty-ish) error or a chart containing ONLY flagged rows.
    if "error" in result:
        assert "anomal" in result["error"].lower()
    else:
        assert len(result["data"]) <= result["hard_facts"]["total_anomalies"]
