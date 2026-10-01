"""Conformidad por CLASE de dato — Semantic Layer Contract-First (Fase 0.1).

Blinda las clases que causaron fallos en producción y que no estaban cubiertas:

- Eje temporal ORDINAL (mes-texto ``"Enero"``, ``"2021-07"``, ``"2021-W30"``):
  el contrato L1 debe exponerlo y el guard de acumulados debe usarlo.
- Métrica ACUMULADA sobre eje texto (running total): no debe sumarse por período.
- KPI/descriptive sin ``date_column`` sobre métrica acumulada.
- Round-trip aditivo del contrato y no-regresión sobre dataset con fecha real.

Cada test reproduce el escenario exacto de fallo. Dominio y nombres de columna
son incidentales: la detección es por VALORES.
"""
from __future__ import annotations

import pandas as pd
import pytest
import ibis

from app.core.analytical_contract import AnalyticalContract
from app.core.semantic_grammar import (
    AnalysisPlan,
    DescriptiveIntent,
    DistributionIntent,
    TimeGrain,
    TimeTrendIntent,
    VisualProtocol,
)
from app.core.temporal_axis import (
    detect_ordinal_axis,
    order_by_period,
    parse_period_key,
)
from app.services.canonical_tabular_production_executor import (
    _apply_cumulative_metric_guard,
    _resolve_guard_time_column,
)
from app.services.data_engine import DataEngine
from app.services.expiry_intent import build_expiry_analysis_bundle, resolve_expiry_column
from app.services.ibis_engine import IbisEngine
from app.services.semantic_translator.validator import finalize_plans
from app.services.visual_recommendation_engine import build_visual_governance
from app.tasks.analysis_pipeline.orchestrator import _format_unsupported_shape_message

_MONTHS = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio"]
_AHORRO = [10737164, 16483894, 20809045, 28900000, 39000000, 49975348, 62233739]


def _ahorro_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "mes": _MONTHS,
            "gastos_en_pesos_chilenos": [28, 33, 36, 40, 44, 47, 51],
            "ahorro_acumulado_en_pesos": _AHORRO,
            "ahorro_acumulado_en_dolares": [12521, 19190, 22993, 30000, 40000, 51000, 63000],
        }
    )


def _ahorro_schema() -> dict:
    return {
        "mes": {"type": "categorical", "role": "dimension", "cardinality": 7, "cardinality_ratio": 1.0},
        "gastos_en_pesos_chilenos": {"type": "numeric", "role": "metric", "cardinality": 7, "cardinality_ratio": 1.0},
        "ahorro_acumulado_en_pesos": {"type": "numeric", "role": "metric", "cardinality": 7, "cardinality_ratio": 1.0},
        "ahorro_acumulado_en_dolares": {"type": "numeric", "role": "metric", "cardinality": 7, "cardinality_ratio": 1.0},
    }


def _kpi_plan(metric: str, aggregation: str = "sum") -> AnalysisPlan:
    return AnalysisPlan(
        main_intent=DescriptiveIntent(
            rationale="kpi",
            metrics=[metric],
            aggregation=aggregation,
            visual_protocol=VisualProtocol.KPI,
        ),
        title=f"{metric} total",
        column_aliases={},
    )


def _df_with_contract(contract: dict, schema: dict) -> pd.DataFrame:
    df = _ahorro_df()
    df.attrs["semantic_contract"] = contract
    df.attrs["schema_profile"] = schema
    return df


# ── Clase: eje temporal ordinal ────────────────────────────────────────


def test_detect_ordinal_axis_month_names() -> None:
    col, order = detect_ordinal_axis(_ahorro_df(), _ahorro_schema())
    assert col == "mes"
    assert order == _MONTHS


def test_parse_period_keys_are_chronological() -> None:
    assert parse_period_key("2021-07") < parse_period_key("2021-08")
    assert parse_period_key("Enero") < parse_period_key("Febrero")
    assert parse_period_key("ene-2021") < parse_period_key("feb-2021")
    assert parse_period_key("2021-W30") is not None
    assert parse_period_key("Q3 2021") is not None
    assert order_by_period(["Marzo", "Enero", "Febrero"]) == ["Enero", "Febrero", "Marzo"]


def test_year_only_axis_detected() -> None:
    df = pd.DataFrame({"anio": ["2021", "2022", "2023"], "ventas_acumuladas": [10, 20, 30]})
    schema = {
        "anio": {"type": "categorical", "role": "dimension", "cardinality": 3},
        "ventas_acumuladas": {"type": "numeric", "role": "metric", "cardinality": 3},
    }
    col, order = detect_ordinal_axis(df, schema)
    assert col == "anio"
    assert order == ["2021", "2022", "2023"]


def test_non_temporal_text_is_not_ordinal() -> None:
    df = pd.DataFrame({"material": ["Tornillo", "Tuerca", "Arandela"], "stock": [1, 2, 3]})
    col, _ = detect_ordinal_axis(df, {})
    assert col is None


def test_detect_ordinal_axis_prefers_stronger_signal() -> None:
    df = pd.DataFrame(
        {
            "anio": ["2021", "2022", "2023"],
            "mes": ["Enero", "Febrero", "Marzo"],
            "v": [1, 2, 3],
        }
    )
    # Un mes con nombre (señal fuerte) gana a un año suelto (señal débil).
    col, _ = detect_ordinal_axis(df, {})
    assert col == "mes"


# ── Clase: contrato L1 expone el eje ordinal ───────────────────────────


def test_contract_exposes_ordinal_axis_and_metric_semantics() -> None:
    contract = DataEngine._infer_dataset_semantic_contract(_ahorro_df(), _ahorro_schema())
    assert contract["ordinal_axis"] == "mes"
    assert contract["ordinal_axis_order"] == _MONTHS
    assert contract["time_axis_kind"] == "ordinal"
    assert contract["time_axis"] is None
    # No concede corte: el eje ordinal no habilita snapshot guard.
    assert contract["dataset_mode"] == "unknown"
    assert contract["snapshot_guard_allowed"] is False
    sem = contract["metric_semantics"]["ahorro_acumulado_en_pesos"]
    assert sem["cumulative_monotonic_direction"] == "inc"


def test_real_datetime_dataset_keeps_date_kind() -> None:
    df = pd.DataFrame(
        {
            "fecha": pd.to_datetime(["2024-01-01", "2024-02-01", "2024-03-01"]),
            "ventas": [10, 20, 30],
        }
    )
    schema = {
        "fecha": {"type": "temporal", "role": "date", "cardinality": 3},
        "ventas": {"type": "numeric", "role": "metric", "cardinality": 3},
    }
    contract = DataEngine._infer_dataset_semantic_contract(df, schema)
    assert contract["time_axis"] == "fecha"
    assert contract["time_axis_kind"] == "date"
    assert contract["ordinal_axis"] is None


def test_contract_roundtrip_preserves_ordinal_fields() -> None:
    contract = DataEngine._infer_dataset_semantic_contract(_ahorro_df(), _ahorro_schema())
    restored = AnalyticalContract.from_legacy_dict(dataset_contract=contract).to_legacy_dataset_contract()
    assert restored["ordinal_axis"] == "mes"
    assert restored["ordinal_axis_order"] == _MONTHS
    assert restored["time_axis_kind"] == "ordinal"
    assert restored["metric_semantics"]["ahorro_acumulado_en_pesos"]["cumulative_monotonic_direction"] == "inc"


# ── Clase: guard de acumulados sobre eje ordinal ───────────────────────


def test_resolve_guard_time_column_reads_semantic_contract_key() -> None:
    contract = DataEngine._infer_dataset_semantic_contract(_ahorro_df(), _ahorro_schema())
    df = _df_with_contract(contract, _ahorro_schema())

    class _Trend:
        date_column = None
        type = "trend"

    assert _resolve_guard_time_column(df, _Trend()) == "mes"


def test_guard_flips_kpi_to_max_from_declared_metric_semantics() -> None:
    contract = DataEngine._infer_dataset_semantic_contract(_ahorro_df(), _ahorro_schema())
    df = _df_with_contract(contract, _ahorro_schema())
    plan = _kpi_plan("ahorro_acumulado_en_pesos")

    _apply_cumulative_metric_guard([plan], df, "analisis del ahorro acumulado")

    assert plan.main_intent.aggregation == "max"
    assert "serie acumulada" in plan.title


def test_guard_flips_kpi_using_ordinal_order_without_declared_semantics() -> None:
    contract = DataEngine._infer_dataset_semantic_contract(_ahorro_df(), _ahorro_schema())
    contract = {**contract, "metric_semantics": {}}  # forzar la ruta de orden ordinal
    df = _df_with_contract(contract, _ahorro_schema())
    plan = _kpi_plan("ahorro_acumulado_en_pesos")

    _apply_cumulative_metric_guard([plan], df, "analisis del ahorro acumulado")

    assert plan.main_intent.aggregation == "max"


def test_guard_leaves_additive_metric_unchanged() -> None:
    contract = DataEngine._infer_dataset_semantic_contract(_ahorro_df(), _ahorro_schema())
    df = _df_with_contract(contract, _ahorro_schema())
    plan = _kpi_plan("gastos_en_pesos_chilenos")

    _apply_cumulative_metric_guard([plan], df, "total de gastos del periodo")

    assert plan.main_intent.aggregation == "sum"


def _iso_period_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "periodo": ["2021-01", "2021-02", "2021-03", "2021-04"],
            "saldo_acumulado": [100, 250, 400, 600],
            "ventas": [100, 150, 150, 200],
        }
    )


def _iso_period_schema() -> dict:
    return {
        "periodo": {"type": "categorical", "role": "dimension", "cardinality": 4},
        "saldo_acumulado": {"type": "numeric", "role": "metric", "cardinality": 4},
        "ventas": {"type": "numeric", "role": "metric", "cardinality": 4},
    }


def test_contract_ordinal_axis_iso_period_cumulative() -> None:
    contract = DataEngine._infer_dataset_semantic_contract(_iso_period_df(), _iso_period_schema())
    assert contract["ordinal_axis"] == "periodo"
    assert contract["ordinal_axis_order"] == ["2021-01", "2021-02", "2021-03", "2021-04"]
    assert contract["time_axis_kind"] == "ordinal"
    assert contract["metric_semantics"]["saldo_acumulado"]["cumulative_monotonic_direction"] == "inc"


def test_guard_flips_distribution_over_ordinal_axis() -> None:
    contract = DataEngine._infer_dataset_semantic_contract(_iso_period_df(), _iso_period_schema())
    df = _iso_period_df()
    df.attrs["semantic_contract"] = contract
    df.attrs["schema_profile"] = _iso_period_schema()
    plan = AnalysisPlan(
        main_intent=DistributionIntent(
            rationale="x",
            dimension="periodo",
            metric="saldo_acumulado",
            aggregation="sum",
            visual_protocol=VisualProtocol.BAR,
        ),
        title="Saldo por periodo",
        column_aliases={},
    )

    _apply_cumulative_metric_guard([plan], df, "saldo acumulado por periodo")

    assert plan.main_intent.aggregation == "max"


@pytest.mark.parametrize(
    ("values", "metric", "expected"),
    [
        ([10, 20, 30, 40], "saldo_acumulado", "max"),   # creciente + léxico → último
        ([40, 30, 20, 10], "saldo_acumulado", "min"),   # decreciente + léxico → último
        ([10, 30, 20, 50], "saldo_acumulado", "sum"),   # no monótona → no corrige
        ([10, 20, 30, 40], "ventas", "sum"),            # sin léxico de acumulación
    ],
)
def test_cumulative_guard_matrix_over_month_axis(
    values: list[int], metric: str, expected: str
) -> None:
    df = pd.DataFrame({"mes": ["Enero", "Febrero", "Marzo", "Abril"], metric: values})
    schema = {
        "mes": {"type": "categorical", "role": "dimension", "cardinality": 4},
        metric: {"type": "numeric", "role": "metric", "cardinality": 4},
    }
    contract = DataEngine._infer_dataset_semantic_contract(df, schema)
    df.attrs["semantic_contract"] = contract
    df.attrs["schema_profile"] = schema
    plan = _kpi_plan(metric)

    _apply_cumulative_metric_guard([plan], df, f"analisis del {metric}")

    assert plan.main_intent.aggregation == expected


def test_unsupported_shape_message_includes_column_hint() -> None:
    err = (
        "canonical_production_not_ready:no_plans:0: "
        "Columnas disponibles en tu dataset: fecha, ventas, region"
    )
    headline, message, status, has_hint = _format_unsupported_shape_message(err)
    assert status == "no_plans"
    assert has_hint is True
    assert "Columnas disponibles en tu dataset: fecha, ventas, region" in message
    assert "No pudimos generar" in headline


def test_unsupported_shape_message_without_hint() -> None:
    err = "canonical_production_not_ready:query_failed:0:some error"
    headline, message, status, has_hint = _format_unsupported_shape_message(err)
    assert status == "query_failed"
    assert has_hint is False
    assert "Reformula la pregunta" in message


def test_cumulative_guard_fails_closed_without_temporal_axis() -> None:
    df = pd.DataFrame({"saldo_acumulado": [10, 20, 30, 40], "region": ["A", "B", "C", "D"]})
    schema = {
        "saldo_acumulado": {"type": "numeric", "role": "metric", "cardinality": 4},
        "region": {"type": "categorical", "role": "dimension", "cardinality": 4},
    }
    contract = DataEngine._infer_dataset_semantic_contract(df, schema)
    df.attrs["semantic_contract"] = contract
    df.attrs["schema_profile"] = schema
    plan = _kpi_plan("saldo_acumulado")

    _apply_cumulative_metric_guard([plan], df, "saldo acumulado")

    # Sin eje temporal no se puede probar monotonía → no se toca (fail-closed).
    assert plan.main_intent.aggregation == "sum"


# ── Clase: combo de una sola métrica (serie derivada MoM) ──────────────


def _trend_plan(visual: VisualProtocol) -> AnalysisPlan:
    return AnalysisPlan(
        main_intent=TimeTrendIntent(
            rationale="x",
            date_column="fecha",
            value_column="ventas",
            visual_protocol=visual,
        ),
        title="Evolución de ventas",
        column_aliases={},
    )


def test_finalize_plans_activates_derived_secondary_for_combo_request() -> None:
    schema = {"fecha": {"role": "date"}, "ventas": {"role": "metric"}}
    plan = _trend_plan(VisualProtocol.DUAL_AXIS)

    out = finalize_plans(
        [plan], schema, prompt="realiza un grafico combinado de la evolucion de ventas"
    )

    assert out[0].main_intent.derived_secondary == "mom_pct"


def test_finalize_plans_keeps_single_series_without_combo_request() -> None:
    schema = {"fecha": {"role": "date"}, "ventas": {"role": "metric"}}
    plan = _trend_plan(VisualProtocol.LINE)

    out = finalize_plans([plan], schema, prompt="evolucion de ventas")

    assert out[0].main_intent.derived_secondary is None


def test_finalize_plans_elevates_line_to_dual_axis_on_combo_request() -> None:
    schema = {"fecha": {"role": "date"}, "ventas": {"role": "metric"}}
    plan = _trend_plan(VisualProtocol.LINE)

    out = finalize_plans([plan], schema, prompt="realiza un grafico combinado de ventas")

    assert out[0].main_intent.visual_protocol == VisualProtocol.DUAL_AXIS
    assert out[0].main_intent.derived_secondary == "mom_pct"


def test_trend_emits_derived_secondary_value() -> None:
    df = pd.DataFrame(
        {
            "fecha": pd.to_datetime(["2021-01-31", "2021-02-28", "2021-03-31", "2021-04-30"]),
            "ventas": [10, 20, 30, 40],
        }
    )
    intent = TimeTrendIntent(
        rationale="x",
        date_column="fecha",
        value_column="ventas",
        visual_protocol=VisualProtocol.DUAL_AXIS,
        derived_secondary="mom_pct",
        grain=TimeGrain.MONTH,
    )

    out = IbisEngine._analyze_trend(ibis.memtable(df), intent)

    secondary = [row["extra_info"].get("secondary_value") for row in out["data"]]
    assert all(value is not None for value in secondary)
    assert secondary[1] == pytest.approx(100.0)


def test_governance_keeps_dual_axis_when_secondary_value_present() -> None:
    plan = _trend_plan(VisualProtocol.DUAL_AXIS)
    ibis_output = {
        "data": [
            {"name": "Ene", "value": 10.0, "extra_info": {"secondary_value": 0.0}},
            {"name": "Feb", "value": 20.0, "extra_info": {"secondary_value": 100.0}},
            {"name": "Mar", "value": 30.0, "extra_info": {"secondary_value": 50.0}},
        ]
    }

    governance = build_visual_governance(plan, ibis_output, "dual_axis_chart")

    assert governance["applied_visual"] == "dual_axis_chart"


# ── Clase: visual explícito NO desactiva el motor de dominio (expiry) ──

_EXPIRY_DF = pd.DataFrame(
    {
        "fecha_de_stock": pd.to_datetime(
            ["2021-06-30", "2021-07-31", "2021-07-31", "2021-07-31"]
        ),
        "fecaduc_feprefercons": pd.to_datetime(
            ["2021-08-10", "2021-08-20", "2024-01-01", "2021-05-01"]
        ),
        "material": ["M1", "M2", "M3", "M4"],
        "stock_disponible": [10, 20, 30, 40],
    }
)

_EXPIRY_SCHEMA = {
    "fecha_de_stock": {"type": "temporal", "role": "date", "cardinality": 2},
    "fecaduc_feprefercons": {"type": "temporal", "role": "date", "cardinality": 4},
    "material": {"role": "identifier", "cardinality": 4},
    "stock_disponible": {"role": "metric"},
}

_EXPIRY_CONTRACT = {
    "time_axis": "fecha_de_stock",
    "snapshot_axis": "fecha_de_stock",
    "event_axes": ["fecaduc_feprefercons"],
    "date_columns": ["fecha_de_stock", "fecaduc_feprefercons"],
    "metric_columns": ["stock_disponible"],
    "dataset_mode": "snapshot",
    "snapshot_guard_allowed": True,
}


def test_resolve_expiry_column_uses_event_axes_without_date_columns() -> None:
    contract = {"event_axes": ["fecaduc_feprefercons"], "metric_columns": ["stock_disponible"]}
    col = resolve_expiry_column(_EXPIRY_DF, _EXPIRY_SCHEMA, contract, "2021-07-31")
    assert col == "fecaduc_feprefercons"


def test_expiry_bundle_runs_even_with_explicit_visual_request() -> None:
    plans = build_expiry_analysis_bundle(
        prompt="realizame un grafico combinado sobre los productos pronto a vencer",
        columns=list(_EXPIRY_DF.columns),
        schema_profile=_EXPIRY_SCHEMA,
        dataset_contract=_EXPIRY_CONTRACT,
        candidate_df=_EXPIRY_DF,
        reference_date="2021-07-31",
    )
    assert plans is not None and len(plans) == 3


def test_expiry_combo_request_activates_derived_series_after_finalize() -> None:
    prompt = "realizame un grafico combinado sobre los productos pronto a vencer"
    plans = build_expiry_analysis_bundle(
        prompt=prompt,
        columns=list(_EXPIRY_DF.columns),
        schema_profile=_EXPIRY_SCHEMA,
        dataset_contract=_EXPIRY_CONTRACT,
        candidate_df=_EXPIRY_DF,
        reference_date="2021-07-31",
    )
    out = finalize_plans(
        list(plans or []),
        _EXPIRY_SCHEMA,
        dataset_contract=_EXPIRY_CONTRACT,
        prompt=prompt,
    )
    trend_plans = [p for p in out if p.main_intent.type == "trend"]
    assert trend_plans
    assert trend_plans[0].main_intent.derived_secondary == "mom_pct"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
