"""
test_snapshot_period_axis.py
═══════════════════════════════════════════════════════════════════
Regression gate — Eje de periodo en datasets con múltiples fechas.

Escenario protegido (incidente multi-hoja Mar–Jul 2021):
  Un libro multi-hoja unificado puede tener DOS columnas temporales:
    - `fecha_de_stock`        → eje real de periodo (pocos cortes, denso)
    - `fecaduc_feprefercons`  → fecha accesoria de alta cardinalidad
  Si el contrato elige la accesoria como `time_axis`, el filtro 'latest'
  se resuelve sobre ella y el análisis entero colapsa a unas pocas filas.

Blindajes:
  B1 — `_select_period_time_axis` prefiere el eje denso (data-driven).
  B2 — `_infer_dataset_semantic_contract` adopta el eje correcto y
       clasifica el dataset como snapshot sin depender del orden.
  B3 — La vista unificada preserva el orden de columnas del frame primario
       (no alfabético).
  B4 — El macro bundle conserva el filtro 'latest' en KPI/distribución
       pero NO lo inyecta en la intención trend (que debe evolucionar).
"""
from __future__ import annotations

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))


def _build_multi_sheet_like_df() -> pd.DataFrame:
    """Simula el frame unificado de 5 hojas mensuales de stock."""
    stock_dates = ["2021-03-31", "2021-04-30", "2021-05-31", "2021-06-30", "2021-07-31"]
    materials = [f"MAT-{i:04d}" for i in range(50)]  # se repiten en cada corte
    rows = []
    for date in stock_dates:
        for idx, material in enumerate(materials):
            rows.append(
                {
                    "fecha_de_stock": pd.Timestamp(date),
                    # Caducidad: alta cardinalidad, casi única por fila.
                    "fecaduc_feprefercons": pd.Timestamp("2021-01-01")
                    + pd.Timedelta(days=(idx * 7 + int(date[:4]) + int(date[5:7]))),
                    "material": material,
                    "stock_disponible": 100 + idx,
                    "tipo_almacen": "130" if idx % 2 == 0 else "400",
                }
            )
    return pd.DataFrame(rows)


def _multi_sheet_schema() -> dict:
    return {
        "fecaduc_feprefercons": {"type": "temporal", "role": "date", "cardinality": 40},
        "fecha_de_stock": {"type": "temporal", "role": "date", "cardinality": 5},
        "material": {"type": "id", "role": "identifier", "cardinality": 50},
        "tipo_almacen": {"type": "categorical", "role": "dimension", "cardinality": 2},
        "stock_disponible": {"type": "numeric", "role": "metric", "cardinality": 50},
    }


# ── B1: selección data-driven del eje de periodo ───────────────────────
def test_select_period_time_axis_prefers_dense_snapshot_axis() -> None:
    """Entre fecha_de_stock (densa) y fecaduc (alta cardinalidad), gana la densa."""
    from app.services.data_engine import DataEngine

    df = _build_multi_sheet_like_df()
    selected = DataEngine._select_period_time_axis(
        df, ["fecaduc_feprefercons", "fecha_de_stock"]
    )
    assert selected == "fecha_de_stock", (
        f"Se esperaba el eje de periodo 'fecha_de_stock', recibido '{selected}'"
    )


def test_select_period_time_axis_single_column_is_identity() -> None:
    """Con una sola columna fecha el comportamiento es idéntico (no regression)."""
    from app.services.data_engine import DataEngine

    df = _build_multi_sheet_like_df()
    assert (
        DataEngine._select_period_time_axis(df, ["fecha_de_stock"])
        == "fecha_de_stock"
    )


# ── B2: contrato semántico adopta el eje correcto ──────────────────────
def test_contract_picks_stock_axis_and_snapshot_mode() -> None:
    """El contrato debe adoptar fecha_de_stock y seguir siendo snapshot."""
    from app.services.data_engine import DataEngine

    df = _build_multi_sheet_like_df()
    contract = DataEngine._infer_dataset_semantic_contract(df, _multi_sheet_schema())

    assert contract["time_axis"] == "fecha_de_stock", (
        f"time_axis incorrecto: {contract['time_axis']}. "
        "El eje accesorio de caducidad no debe desplazar al eje de periodo."
    )
    assert contract["dataset_mode"] == "snapshot", contract["dataset_mode"]
    evidence = contract.get("evidence", {})
    assert evidence.get("rows_at_max_date", 0) >= 40, (
        f"El corte de fecha_de_stock debe tener filas completas, no una rebanada: {evidence}"
    )


# ── B3: la vista unificada preserva el orden del frame primario ────────
def test_unified_view_preserves_primary_column_order() -> None:
    """El orden del frame primario manda; no debe ordenarse alfabéticamente."""
    from app.core.canonical_artifacts import (
        CanonicalMaterializationStatus,
        CanonicalMaterializedFrame,
    )
    from app.services.canonical_bundle_materializer import (
        _build_unified_materialized_view,
    )

    primary_cols = [
        "fecha_de_stock",
        "fecaduc_feprefercons",
        "material",
        "tipo_almacen",
        "stock_disponible",
    ]
    # Las hojas relacionadas podrían venir con distinto orden.
    related_cols = sorted(primary_cols)

    def _frame(frame_id: str, cols: list[str]) -> CanonicalMaterializedFrame:
        return CanonicalMaterializedFrame(
            frame_id=frame_id,
            label=frame_id,
            status=CanonicalMaterializationStatus.READY,
            column_names=list(cols),
            row_count=1,
            records=[{col: "x" for col in cols}],
        )

    view = _build_unified_materialized_view(
        _frame("sheet::30-04-2021", primary_cols),
        [
            _frame("sheet::31-05-2021", related_cols),
            _frame("sheet::30-06-2021", related_cols),
        ],
    )
    assert view is not None, "La vista unificada debe construirse"
    assert view.column_names[0] == "fecha_de_stock", view.column_names
    assert view.column_names.index("fecha_de_stock") < view.column_names.index(
        "fecaduc_feprefercons"
    ), view.column_names


# ── B4: trend evoluciona, KPI/distribución conservan el corte ──────────
def test_macro_bundle_snapshot_exempts_trend_from_latest() -> None:
    """Snapshot: KPI y distribución llevan 'latest'; trend NO (debe evolucionar)."""
    from app.services.semantic_translator.planner import build_macro_analysis_bundle

    contract = {
        "dataset_mode": "snapshot",
        "time_axis": "fecha_de_stock",
        "date_columns": ["fecaduc_feprefercons", "fecha_de_stock"],
        "snapshot_guard_allowed": True,
    }
    schema = _multi_sheet_schema()
    columns = [
        "fecha_de_stock",
        "fecaduc_feprefercons",
        "material",
        "tipo_almacen",
        "stock_disponible",
    ]

    plans = build_macro_analysis_bundle(
        "realiza un analisis gerencial",
        columns,
        schema_profile=schema,
        dataset_contract=contract,
    )
    assert plans, "El macro bundle debe generar planes"

    trend_plans = [p for p in plans if p.main_intent.type == "trend"]
    assert trend_plans, "Debe existir una vista de evolución temporal"
    for plan in trend_plans:
        filters = getattr(plan.main_intent, "filters", []) or []
        assert filters == [], (
            f"El trend '{plan.title}' no debe colapsar con filtro latest: {filters}"
        )

    non_trend_plans = [p for p in plans if p.main_intent.type != "trend"]
    assert any(
        any(getattr(f, "value", "") == "latest" for f in (getattr(p.main_intent, "filters", []) or []))
        for p in non_trend_plans
    ), "KPI/distribución deben conservar el filtro del corte actual"
