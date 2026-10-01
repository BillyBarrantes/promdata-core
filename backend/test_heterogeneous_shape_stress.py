"""Estrés de formas heterogéneas (Fase 3) — invariantes del contrato L1.

Para "miles de bases de datos distintas" la garantía no es un caso puntual sino
que el contrato no se rompa ni mienta ante CUALQUIER forma. Este test corre el
profiler REAL de producción (`build_canonical_schema_profile`) + la inferencia de
contrato (`DataEngine._infer_dataset_semantic_contract`) sobre una matriz de
formas heterogéneas y verifica invariantes estructurales (nunca lanza, el eje
ordinal siempre es una columna real, `metric_semantics` solo referencia métricas,
`dataset_mode` dentro del dominio, etc.).
"""
from __future__ import annotations

import pandas as pd
import pytest

from app.core.semantic_grammar import AnalysisPlan, DescriptiveIntent, VisualProtocol
from app.services.canonical_schema_profiler import build_canonical_schema_profile
from app.services.canonical_tabular_production_executor import _apply_cumulative_metric_guard
from app.services.data_engine import DataEngine

_MONTHS = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio"]
_KNOWN_MODES = {"snapshot", "flow", "hybrid", "undetermined", "unknown"}


def _increasing(n: int) -> list[int]:
    return [10 * (i + 1) for i in range(n)]


def _decreasing(n: int) -> list[int]:
    return [10 * (n - i) for i in range(n)]


def _volume_month_shape(rows: int = 5000) -> pd.DataFrame:
    """Volumen (miles de filas) con eje de mes-texto y métrica acumulada monótona."""
    meses = [
        "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
        "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
    ]
    records = [
        {"mes": meses[i % 12], "saldo_acumulado": (i % 12 + 1) * 1000 + i // 12}
        for i in range(rows)
    ]
    return pd.DataFrame(records)


_SHAPES: dict[str, pd.DataFrame] = {
    "real_datetime_increasing": pd.DataFrame(
        {
            "fecha": pd.to_datetime([f"2021-0{m}-01" for m in range(1, 8)]),
            "saldo_acumulado": _increasing(7),
        }
    ),
    "real_datetime_decreasing": pd.DataFrame(
        {
            "fecha": pd.to_datetime([f"2021-0{m}-01" for m in range(1, 8)]),
            "saldo_acumulado": _decreasing(7),
        }
    ),
    "month_text_increasing": pd.DataFrame({"mes": _MONTHS, "saldo_acumulado": _increasing(7)}),
    "month_text_nonmonotonic": pd.DataFrame(
        {"mes": _MONTHS, "saldo_acumulado": [10, 40, 20, 60, 30, 80, 50]}
    ),
    "iso_period_increasing": pd.DataFrame(
        {"periodo": ["2021-01", "2021-02", "2021-03", "2021-04"], "saldo": _increasing(4)}
    ),
    "year_increasing": pd.DataFrame({"anio": ["2019", "2020", "2021"], "ventas": _increasing(3)}),
    "week_iso": pd.DataFrame(
        {"semana": ["2021-W01", "2021-W02", "2021-W03"], "pedidos": [3, 5, 8]}
    ),
    "quarter": pd.DataFrame({"trimestre": ["Q1 2021", "Q2 2021", "Q3 2021"], "meta": [10, 20, 30]}),
    "no_temporal_axis": pd.DataFrame(
        {"region": ["A", "B", "C", "D"], "saldo_acumulado": _increasing(4)}
    ),
    "text_only_no_metric": pd.DataFrame({"material": ["A", "B", "C"], "nota": ["x", "y", "z"]}),
    "negative_values": pd.DataFrame(
        {"fecha": pd.to_datetime(["2021-01-01", "2021-02-01", "2021-03-01"]), "flujo": [100, -40, -20]}
    ),
    "ratio_metric": pd.DataFrame(
        {"fecha": pd.to_datetime(["2021-01-01", "2021-02-01", "2021-03-01"]), "tasa": [0.1, 0.5, 0.9]}
    ),
    "two_metrics": pd.DataFrame(
        {
            "fecha": pd.to_datetime(["2021-01-01", "2021-02-01", "2021-03-01"]),
            "ventas": [10, 20, 30],
            "margen": [1.0, 2.0, 3.0],
        }
    ),
    "high_cardinality_ids": pd.DataFrame(
        {"id_doc": [f"D{i:04d}" for i in range(60)], "monto": list(range(60))}
    ),
    "single_row": pd.DataFrame({"fecha": pd.to_datetime(["2021-01-01"]), "ventas": [10]}),
    "two_rows": pd.DataFrame({"mes": ["Enero", "Febrero"], "ventas": [10, 20]}),
    "null_metric": pd.DataFrame({"mes": ["Enero", "Febrero", "Marzo"], "ventas": [None, None, None]}),
    "mixed_content": pd.DataFrame({"mes": _MONTHS, "codigo": ["SALDOS", "010731", "010732", "010733", "010734", "010735", "010736"], "stock": _increasing(7)}),
    "month_text_with_year": pd.DataFrame(
        {"mes": ["Ene-2021", "Feb-2021", "Mar-2021", "Abr-2021"], "saldo_acumulado": _increasing(4)}
    ),
    "volume_month_text": _volume_month_shape(),
}


def _contract_for(df: pd.DataFrame) -> tuple[pd.DataFrame, dict, dict]:
    working_df, schema_profile, _ = build_canonical_schema_profile(df.copy())
    contract = DataEngine._infer_dataset_semantic_contract(working_df, schema_profile)
    return working_df, schema_profile, contract


@pytest.mark.parametrize("shape_name", sorted(_SHAPES))
def test_contract_invariants_hold_for_every_shape(shape_name: str) -> None:
    df = _SHAPES[shape_name]
    working_df, schema_profile, contract = _contract_for(df)

    assert isinstance(contract, dict)
    assert contract["dataset_mode"] in _KNOWN_MODES
    assert isinstance(contract["snapshot_guard_allowed"], bool)
    assert contract["time_axis_kind"] in {"date", "ordinal", None}

    ordinal_axis = contract["ordinal_axis"]
    if ordinal_axis is not None:
        assert ordinal_axis in working_df.columns
        assert len(contract["ordinal_axis_order"]) >= 2
        # El orden debe ser determinista (mismos valores, mismo orden).
        assert contract["ordinal_axis_order"] == DataEngine._infer_dataset_semantic_contract(
            working_df.copy(), schema_profile
        )["ordinal_axis_order"]

    metrics = set(contract["metric_columns"])
    for metric, signals in contract.get("metric_semantics", {}).items():
        assert metric in metrics
        assert signals.get("cumulative_monotonic_direction") in {"inc", "dec", None}


@pytest.mark.parametrize("shape_name", sorted(_SHAPES))
def test_guard_never_crashes_and_only_flips_accumulated(shape_name: str) -> None:
    df = _SHAPES[shape_name]
    working_df, schema_profile, contract = _contract_for(df)
    working_df.attrs["semantic_contract"] = contract
    working_df.attrs["schema_profile"] = schema_profile

    metric_candidates = list(contract["metric_columns"])
    if not metric_candidates:
        pytest.skip("shape sin métricas")
    metric = metric_candidates[0]
    plan = AnalysisPlan(
        main_intent=DescriptiveIntent(
            rationale="x",
            metrics=[metric],
            aggregation="sum",
            visual_protocol=VisualProtocol.KPI,
        ),
        title=f"{metric} total",
        column_aliases={},
    )

    _apply_cumulative_metric_guard([plan], working_df, f"analisis de {metric}")

    # Solo puede quedar en sum/max/min; y si cambió, la métrica tenía léxico de
    # acumulación (el prompt del test NO lo trae) y una dirección monótona probada.
    assert plan.main_intent.aggregation in {"sum", "max", "min"}
    if plan.main_intent.aggregation != "sum":
        assert "acumul" in metric.lower()


def test_ordinal_order_is_chronological_not_alphabetical() -> None:
    _, _, contract = _contract_for(_SHAPES["month_text_increasing"])
    assert contract["ordinal_axis"] == "mes"
    assert contract["ordinal_axis_order"] == _MONTHS
    # Alfabético sería ["Abril","Enero",...]; el contrato debe ser cronológico.
    assert contract["ordinal_axis_order"] != sorted(_MONTHS)


def test_volume_shape_scales_and_detects_axis() -> None:
    df = _SHAPES["volume_month_text"]
    working_df, _, contract = _contract_for(df)

    assert len(working_df) == 5000
    assert contract["ordinal_axis"] == "mes"
    assert contract["metric_semantics"]["saldo_acumulado"]["cumulative_monotonic_direction"] == "inc"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
