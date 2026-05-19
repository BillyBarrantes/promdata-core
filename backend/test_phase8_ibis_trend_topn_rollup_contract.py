import pandas as pd
import pytest

ibis = pytest.importorskip("ibis")

from app.core.semantic_grammar import DistributionIntent, TimeTrendIntent
from app.services.ibis_engine import IbisEngine


def test_phase8_ibis_trend_rollup_sum_generates_single_series() -> None:
    dataframe = pd.DataFrame(
        [
            {"fecha": "2026-01-01", "producto": "A", "ventas": 100},
            {"fecha": "2026-01-01", "producto": "B", "ventas": 40},
            {"fecha": "2026-01-01", "producto": "C", "ventas": 20},
            {"fecha": "2026-02-01", "producto": "A", "ventas": 110},
            {"fecha": "2026-02-01", "producto": "B", "ventas": 30},
            {"fecha": "2026-02-01", "producto": "C", "ventas": 10},
        ]
    )
    dataframe["fecha"] = pd.to_datetime(dataframe["fecha"])
    table = ibis.memtable(dataframe)

    intent = TimeTrendIntent.model_validate(
        {
            "rationale": "Evolución de la suma top 2",
            "date_column": "fecha",
            "value_column": "ventas",
            "grain": "month",
            "fill_missing": True,
            "split_dimension": "producto",
            "split_limit": 2,
            "top_n_aggregation_mode": "sum",
            "visual_protocol": "line_chart",
        }
    )

    result = IbisEngine._analyze_trend(table, intent)

    assert result["type"] == "echarts"
    assert result["chart_type"] == "line_chart"
    assert result.get("hard_facts", {}).get("top_n_aggregation_mode") == "sum"
    assert result.get("hard_facts", {}).get("series_count") == 1
    assert all("value" in row for row in result.get("data", []))
    assert all("producto" not in row for row in result.get("data", []))


def test_phase8_ibis_trend_split_mode_preserves_multi_series_shape() -> None:
    dataframe = pd.DataFrame(
        [
            {"fecha": "2026-01-01", "producto": "A", "ventas": 100},
            {"fecha": "2026-01-01", "producto": "B", "ventas": 40},
            {"fecha": "2026-02-01", "producto": "A", "ventas": 110},
            {"fecha": "2026-02-01", "producto": "B", "ventas": 30},
        ]
    )
    dataframe["fecha"] = pd.to_datetime(dataframe["fecha"])
    table = ibis.memtable(dataframe)

    intent = TimeTrendIntent.model_validate(
        {
            "rationale": "Evolución top 2 por producto",
            "date_column": "fecha",
            "value_column": "ventas",
            "grain": "month",
            "fill_missing": True,
            "split_dimension": "producto",
            "split_limit": 2,
            "top_n_aggregation_mode": "split",
            "visual_protocol": "line_chart",
        }
    )

    result = IbisEngine._analyze_trend(table, intent)

    assert result["type"] == "echarts"
    assert result["chart_type"] == "line_chart"
    assert result.get("hard_facts", {}).get("top_n_aggregation_mode") == "split"
    assert result.get("hard_facts", {}).get("series_count", 0) >= 2
    assert any("A" in row or "B" in row for row in result.get("data", []))


def test_phase8_ibis_distribution_ranks_by_separate_metric_and_excludes_values() -> None:
    dataframe = pd.DataFrame(
        [
            {"producto": "A", "categoria": "Hardware", "ingreso_total": 1000, "cantidad": 2},
            {"producto": "B", "categoria": "Hardware", "ingreso_total": 100, "cantidad": 50},
            {"producto": "C", "categoria": "Software", "ingreso_total": 9000, "cantidad": 500},
            {"producto": "D", "categoria": "Hardware", "ingreso_total": 300, "cantidad": 40},
        ]
    )
    table = ibis.memtable(dataframe)
    intent = DistributionIntent.model_validate(
        {
            "rationale": "Graficar ingresos, elegir Top 2 por cantidad y excluir Software.",
            "filters": [],
            "negative_filters": [{"column": "categoria", "operator": "not_in", "value": ["Software"]}],
            "metric_unit": "currency",
            "visual_protocol": "bar_chart",
            "dimension": "producto",
            "metric": "ingreso_total",
            "plot_metric": "ingreso_total",
            "ranking_metric": "cantidad",
            "ranking_direction": "desc",
            "limit": 2,
        }
    )

    filtered = IbisEngine._apply_intent_filters(table, intent)
    result = IbisEngine._analyze_distribution(filtered, intent)

    names = [row["name"] for row in result["data"]]
    assert names == ["B", "D"]
    assert [row["value"] for row in result["data"]] == [100.0, 300.0]
    assert result["hard_facts"]["ranking_metric"] == "cantidad"
