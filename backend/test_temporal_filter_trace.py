"""F1 — Linaje verificable de filtros temporales (TDD).

Blindaje del comportamiento introducido en F1:
  * Corte legacy F0 → origin=legacy_inference / authority=inferred.
  * Filtro temporal del usuario → origin=user_requested / authority=explicit.
  * Cuatro rutas temporales producen trazas coherentes o vacías sin error.
  * Contrato `unknown` sin evidencia → authority=unknown, sin cut.
  * Enum inválido → fallback seguro a `unknown` (no excepción).
  * `rows_before` / `rows_after` y `effect` coherentes.
  * La traza es estrictamente aditiva: no altera `chart_base_filters` ni las
    claves existentes del resultado.
  * Flag `SEMANTIC_CONTRACT_F1_TRACE=False` desactiva la traza (legacy intacto).
"""

import os
import sys
import tempfile

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "."))

from app.core.config import settings
from app.core.semantic_grammar import (
    AnalysisPlan,
    DistributionIntent,
    DataFilter,
    TimeGrain,
    TimeTrendIntent,
)
from app.services.data_engine import DataEngine
from app.services.ibis_engine import IbisEngine
from app.services.temporal_filter_trace import (
    FilterAuthority,
    FilterEffect,
    FilterOrigin,
    TemporalFilterTraceItem,
    build_temporal_filter_trace,
)


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════

def _write_parquet(df: pd.DataFrame) -> str:
    tmp = tempfile.NamedTemporaryFile(suffix=".parquet", delete=False)
    pq.write_table(pa.Table.from_pandas(df), tmp.name)
    tmp.close()
    return tmp.name


def _snapshot_df() -> pd.DataFrame:
    rows = []
    periods = pd.date_range("2024-01-31", periods=3, freq="ME")
    for idx, period in enumerate(periods):
        is_latest = idx == len(periods) - 1
        for cat in ("A", "B", "C", "D"):
            rows.append(
                {
                    "fecha": period,
                    "categoria": cat,
                    "stock_disponible": 10.0 + idx,
                    "is_latest_snapshot": is_latest,
                }
            )
    return pd.DataFrame(rows)


def _flow_df() -> pd.DataFrame:
    rows = []
    for day in range(1, 21):
        rows.append(
            {
                "fecha": pd.Timestamp("2024-01-01") + pd.Timedelta(days=day - 1),
                "categoria": "X" if day % 2 else "Y",
                "monto": float(day * 10),
            }
        )
    return pd.DataFrame(rows)


_SNAPSHOT_CONTRACT = {
    "dataset_mode": "snapshot",
    "snapshot_guard_allowed": True,
    "time_axis": "fecha",
    "date_columns": ["fecha"],
    "contract_state": "legacy_inferred",
}

_FLOW_CONTRACT = {
    "dataset_mode": "flow",
    "snapshot_guard_allowed": False,
    "time_axis": "fecha",
    "date_columns": ["fecha"],
    "contract_state": "confirmed_file_contract",
}


# ═══════════════════════════════════════════════════════════════════════
# 1. Corte legacy F0 → inferred
# ═══════════════════════════════════════════════════════════════════════

def test_legacy_snapshot_cut_marks_inferred() -> None:
    trace = build_temporal_filter_trace(
        snapshot_guard_applied=True,
        rows_before=120,
        rows_after=12,
    )
    assert trace.cut_applied is True
    assert trace.authority == FilterAuthority.INFERRED
    assert len(trace.items) == 1
    item = trace.items[0]
    assert item.origin == FilterOrigin.LEGACY_INFERENCE
    assert item.authority == FilterAuthority.INFERRED
    assert item.effect == FilterEffect.ROWS_REMOVED
    assert trace.rows_before == 120 and trace.rows_after == 12


# ═══════════════════════════════════════════════════════════════════════
# 2. Filtro temporal del usuario → explicit
# ═══════════════════════════════════════════════════════════════════════

def test_user_temporal_filter_marks_explicit() -> None:
    trace = build_temporal_filter_trace(
        filter_lists={
            "filters": [
                DataFilter(column="fecha", operator=">=", value="2024-02-01"),
            ]
        },
        temporal_columns={"fecha"},
        rows_before=100,
        rows_after=40,
    )
    assert trace.cut_applied is False
    assert trace.authority == FilterAuthority.EXPLICIT
    assert len(trace.items) == 1
    item = trace.items[0]
    assert item.column == "fecha"
    assert item.operator == ">="
    assert item.origin == FilterOrigin.USER_REQUESTED
    assert item.authority == FilterAuthority.EXPLICIT


# ═══════════════════════════════════════════════════════════════════════
# 3. Cuatro rutas temporales (sin error, traza coherente o vacía)
# ═══════════════════════════════════════════════════════════════════════

def test_four_temporal_routes_produce_coherent_trace() -> None:
    # distribution: sin guard ni filtros temporales → vacía
    r_dist = build_temporal_filter_trace(
        filter_lists={}, temporal_columns={"fecha"}, rows_before=10, rows_after=10
    )
    assert r_dist.items == []
    assert r_dist.authority == FilterAuthority.UNKNOWN
    assert r_dist.cut_applied is False

    # trend con filtro temporal → explicit
    r_trend = build_temporal_filter_trace(
        filter_lists={"filters": [DataFilter(column="fecha", operator="between", value=["2024-01-01", "2024-03-01"])]},
        temporal_columns={"fecha"},
        rows_before=10,
        rows_after=5,
    )
    assert r_trend.authority == FilterAuthority.EXPLICIT
    assert r_trend.items[0].operator == "between"

    # snapshot guard → inferred
    r_snap = build_temporal_filter_trace(
        snapshot_guard_applied=True, rows_before=10, rows_after=3
    )
    assert r_snap.authority == FilterAuthority.INFERRED

    # no temporal (filtro sobre dimensión) → vacía
    r_none = build_temporal_filter_trace(
        filter_lists={"filters": [DataFilter(column="canal", operator="==", value="X")]},
        temporal_columns={"fecha"},
        rows_before=10,
        rows_after=8,
    )
    assert r_none.items == []
    assert r_none.authority == FilterAuthority.UNKNOWN


# ═══════════════════════════════════════════════════════════════════════
# 4. Contrato unknown sin evidencia → unknown, sin cut
# ═══════════════════════════════════════════════════════════════════════

def test_unknown_contract_has_no_authority() -> None:
    trace = build_temporal_filter_trace(
        filter_lists={},
        snapshot_guard_applied=False,
        temporal_columns=set(),
        rows_before=50,
        rows_after=50,
        contract_state="unknown",
    )
    assert trace.authority == FilterAuthority.UNKNOWN
    assert trace.cut_applied is False
    assert trace.items == []


# ═══════════════════════════════════════════════════════════════════════
# 5. Enum inválido → fallback unknown (no excepción)
# ═══════════════════════════════════════════════════════════════════════

def test_invalid_enum_falls_back_to_unknown() -> None:
    item = TemporalFilterTraceItem(
        column="fecha",
        operator="==",
        value="x",
        origin="bogus_origin",
        authority="bogus_authority",
        effect="bogus_effect",
    )
    assert item.origin == FilterOrigin.UNKNOWN
    assert item.authority == FilterAuthority.UNKNOWN
    assert item.effect == FilterEffect.UNKNOWN


# ═══════════════════════════════════════════════════════════════════════
# 6. rows_before / rows_after y effect coherentes
# ═══════════════════════════════════════════════════════════════════════

def test_rows_and_effect_are_coherent() -> None:
    removed = build_temporal_filter_trace(rows_before=100, rows_after=10)
    assert removed.items == []  # sin filtros → sin items, pero conteos presentes
    assert removed.rows_before == 100 and removed.rows_after == 10

    guard_removed = build_temporal_filter_trace(
        snapshot_guard_applied=True, rows_before=100, rows_after=10
    )
    assert guard_removed.items[0].effect == FilterEffect.ROWS_REMOVED

    guard_equal = build_temporal_filter_trace(
        snapshot_guard_applied=True, rows_before=10, rows_after=10
    )
    assert guard_equal.items[0].effect == FilterEffect.NO_ROWS_REMOVED

    guard_unknown = build_temporal_filter_trace(
        snapshot_guard_applied=True, rows_before=None, rows_after=None
    )
    assert guard_unknown.items[0].effect == FilterEffect.UNKNOWN


# ═══════════════════════════════════════════════════════════════════════
# 7. Integración Ibis: traza aditiva, no altera claves existentes
# ═══════════════════════════════════════════════════════════════════════

def test_execute_plan_legacy_snapshot_cut_trace(monkeypatch) -> None:
    parquet_path = _write_parquet(_snapshot_df())
    monkeypatch.setattr(DataEngine, "load_semantic_contract", lambda _p: dict(_SNAPSHOT_CONTRACT))

    plan = AnalysisPlan(
        title="Stock por categoría",
        main_intent=DistributionIntent(
            type="distribution",
            dimension="categoria",
            metric="stock_disponible",
            rationale="Distribución de stock vigente.",
        ),
    )
    result = IbisEngine.execute_plan(parquet_path, plan)

    assert "error" not in result
    trace = result["temporal_filter_trace"]
    assert trace["cut_applied"] is True
    assert trace["authority"] == "inferred"
    assert trace["rows_after"] < trace["rows_before"]
    assert trace["items"][0]["origin"] == "legacy_inference"


def test_execute_plan_user_temporal_filter_trace(monkeypatch) -> None:
    parquet_path = _write_parquet(_flow_df())
    monkeypatch.setattr(DataEngine, "load_semantic_contract", lambda _p: dict(_FLOW_CONTRACT))

    plan = AnalysisPlan(
        title="Evolución de monto",
        main_intent=TimeTrendIntent(
            type="trend",
            date_column="fecha",
            value_column="monto",
            grain=TimeGrain.MONTH,
            rationale="Evolución filtrada.",
            filters=[DataFilter(column="fecha", operator=">=", value="2024-01-11")],
        ),
    )
    result = IbisEngine.execute_plan(parquet_path, plan)

    assert "error" not in result
    trace = result["temporal_filter_trace"]
    assert trace["authority"] == "explicit"
    assert trace["cut_applied"] is False
    assert trace["items"][0]["origin"] == "user_requested"
    assert trace["items"][0]["column"] == "fecha"
    assert trace["rows_after"] < trace["rows_before"]


def test_trace_is_additive_only(monkeypatch) -> None:
    parquet_path = _write_parquet(_snapshot_df())
    monkeypatch.setattr(DataEngine, "load_semantic_contract", lambda _p: dict(_SNAPSHOT_CONTRACT))
    plan = AnalysisPlan(
        title="Stock por categoría",
        main_intent=DistributionIntent(
            type="distribution",
            dimension="categoria",
            metric="stock_disponible",
            rationale="Distribución de stock vigente.",
        ),
    )

    monkeypatch.setattr(settings, "SEMANTIC_CONTRACT_F1_TRACE", False)
    baseline = IbisEngine.execute_plan(parquet_path, plan)

    monkeypatch.setattr(settings, "SEMANTIC_CONTRACT_F1_TRACE", True)
    traced = IbisEngine.execute_plan(parquet_path, plan)

    # Invariante de aditividad: la traza es la única clave añadida y no se
    # elimina ninguna clave existente del resultado.
    assert set(traced.keys()) - set(baseline.keys()) == {"temporal_filter_trace"}
    assert set(baseline.keys()) - set(traced.keys()) == set()
    assert isinstance(traced.get("temporal_filter_trace"), dict)


# ═══════════════════════════════════════════════════════════════════════
# 8. Flag off → sin traza (legacy intacto)
# ═══════════════════════════════════════════════════════════════════════

def test_flag_off_disables_trace(monkeypatch) -> None:
    parquet_path = _write_parquet(_snapshot_df())
    monkeypatch.setattr(DataEngine, "load_semantic_contract", lambda _p: dict(_SNAPSHOT_CONTRACT))
    monkeypatch.setattr(settings, "SEMANTIC_CONTRACT_F1_TRACE", False)

    plan = AnalysisPlan(
        title="Stock por categoría",
        main_intent=DistributionIntent(
            type="distribution",
            dimension="categoria",
            metric="stock_disponible",
            rationale="Distribución de stock vigente.",
        ),
    )
    result = IbisEngine.execute_plan(parquet_path, plan)

    assert "temporal_filter_trace" not in result


# ═══════════════════════════════════════════════════════════════════════
# 8b. F1.6 — contract_state deja de ser un parámetro muerto
# ═══════════════════════════════════════════════════════════════════════

def test_contract_state_is_persisted_in_trace() -> None:
    trace = build_temporal_filter_trace(
        snapshot_guard_applied=True,
        rows_before=10,
        rows_after=5,
        contract_state="confirmed_file_contract",
    )
    assert trace.contract_state == "confirmed_file_contract"

    defaulted = build_temporal_filter_trace(rows_before=10, rows_after=10)
    assert defaulted.contract_state is None


# ═══════════════════════════════════════════════════════════════════════
# 8c. F1.6 — filtros no temporales no generan traza (sin ruido)
# ═══════════════════════════════════════════════════════════════════════

def test_non_temporal_filter_emits_no_trace(monkeypatch) -> None:
    parquet_path = _write_parquet(_flow_df())
    monkeypatch.setattr(DataEngine, "load_semantic_contract", lambda _p: dict(_FLOW_CONTRACT))

    plan = AnalysisPlan(
        title="Monto por categoría",
        main_intent=DistributionIntent(
            type="distribution",
            dimension="categoria",
            metric="monto",
            rationale="Filtro no temporal.",
            filters=[DataFilter(column="categoria", operator="==", value="X")],
        ),
    )
    result = IbisEngine.execute_plan(parquet_path, plan)

    assert "error" not in result
    assert "temporal_filter_trace" not in result


# ═══════════════════════════════════════════════════════════════════════
# 8d. F1.6 — resumen de historial usa la autoridad MÁS FUERTE
# ═══════════════════════════════════════════════════════════════════════

def test_history_summary_uses_strongest_authority() -> None:
    from app.services.analysis_traceability import summarize_history_item

    explicit = summarize_history_item(
        task_row={"id": "t1"},
        result_payload={
            "traceability": {
                "temporal_filters": [
                    {"authority": "unknown", "cut_applied": False},
                    {"authority": "explicit", "cut_applied": False},
                ],
                "temporal_cut_applied": False,
            }
        },
    )
    assert explicit["temporal_authority"] == "explicit"

    inferred = summarize_history_item(
        task_row={"id": "t2"},
        result_payload={
            "traceability": {
                "temporal_filters": [
                    {"authority": "unknown", "cut_applied": False},
                    {"authority": "inferred", "cut_applied": True},
                ],
                "temporal_cut_applied": True,
            }
        },
    )
    assert inferred["temporal_authority"] == "inferred"
    assert inferred["temporal_cut_applied"] is True


# ═══════════════════════════════════════════════════════════════════════
# 9. F1.6 — chart_base_filters NO cambia por la presencia de la traza F1
# ═══════════════════════════════════════════════════════════════════════

def test_chart_base_filters_unaffected_by_f1_trace() -> None:
    from app.services.canonical_tabular_canary_executor import _build_chart_option

    plan = AnalysisPlan(
        title="Por categoría",
        main_intent=DistributionIntent(
            type="distribution",
            dimension="categoria",
            metric="monto",
            rationale="Filtros base.",
            filters=[
                DataFilter(column="canal", operator="==", value="X"),
                DataFilter(column="monto", operator=">", value=0),
            ],
        ),
    )
    schema_profile = {
        "categoria": {"role": "dimension", "type": "categorical"},
        "canal": {"role": "dimension", "type": "categorical"},
        "monto": {"role": "metric", "type": "numeric"},
    }
    base_payload = {
        "type": "echarts",
        "chart_type": "bar_chart",
        "data": [{"name": "A", "value": 1.0}],
        "x_axis": "categoria",
        "y_axis": "monto",
    }
    payload_with_trace = {
        **base_payload,
        "temporal_filter_trace": {"cut_applied": True, "authority": "inferred", "items": []},
    }

    without = _build_chart_option(
        plan=plan, title="Por categoría", result_payload=base_payload,
        currency_meta={}, schema_profile=schema_profile,
    )
    with_trace = _build_chart_option(
        plan=plan, title="Por categoría", result_payload=payload_with_trace,
        currency_meta={}, schema_profile=schema_profile,
    )

    assert without is not None and with_trace is not None
    assert without.get("chart_base_filters") == with_trace.get("chart_base_filters")
    # El filtro de dimensión se conserva; el de métrica se excluye por role guard.
    assert "canal" in (without.get("chart_base_filters") or {})
    assert "monto" not in (without.get("chart_base_filters") or {})

