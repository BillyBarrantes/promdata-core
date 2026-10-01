"""
test_phase0_baseline_ground_truth.py — Suite de Integridad Baseline de Fase 0
═══════════════════════════════════════════════════════════════════════════════
Garantiza que:
  1. Los 3 fixtures canónicos existen físicamente y son reproducibles.
  2. El cálculo en tiempo real con DuckDB / Ibis coincide al 100% con expected_ground_truth.json.
  3. Los nuevos contratos tipados V1 en analytical_contract.py funcionan e importan limpiamente.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

import duckdb
import pytest

# Ensure backend is in path
sys.path.insert(0, os.path.dirname(__file__))

from app.core.analytical_contract import (
    ColumnResolution,
    EvidenceBundleV1,
    FilterSpec,
    InteractionContractV1,
    QueryAnalyticalContractV1,
    UploadFlowContractV1,
    VisualContractV1,
)

ROOT_DIR = Path(__file__).resolve().parent.parent
FIXTURES_DIR = ROOT_DIR / "fixtures" / "v1"


@pytest.fixture(scope="module")
def ground_truth_data() -> dict:
    gt_file = FIXTURES_DIR / "expected_ground_truth.json"
    assert gt_file.exists(), f"Falta el archivo {gt_file}"
    with open(gt_file, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def duckdb_con() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(database=":memory:")
    ventas_csv = FIXTURES_DIR / "ventas_retail_v1.csv"
    inv_csv = FIXTURES_DIR / "inventario_snapshots_v1.csv"
    fin_csv = FIXTURES_DIR / "finanzas_flujo_v1.csv"

    assert ventas_csv.exists(), f"Falta {ventas_csv}"
    assert inv_csv.exists(), f"Falta {inv_csv}"
    assert fin_csv.exists(), f"Falta {fin_csv}"

    con.execute(f"CREATE TABLE ventas AS SELECT * FROM read_csv_auto('{ventas_csv}')")
    con.execute(f"CREATE TABLE inventario AS SELECT * FROM read_csv_auto('{inv_csv}')")
    con.execute(f"CREATE TABLE finanzas AS SELECT * FROM read_csv_auto('{fin_csv}')")
    return con


# ── TEST 1: EXISTENCIA Y CONTEO DE FILAS DE FIXTURES ──────────────────────────

def test_fixtures_file_existence_and_row_counts(duckdb_con: duckdb.DuckDBPyConnection):
    """Verifica que los 3 archivos CSV existen y tienen el número exacto de filas contratado."""
    ventas_count = duckdb_con.execute("SELECT COUNT(*) FROM ventas").fetchone()[0]
    inv_count = duckdb_con.execute("SELECT COUNT(*) FROM inventario").fetchone()[0]
    fin_count = duckdb_con.execute("SELECT COUNT(*) FROM finanzas").fetchone()[0]

    assert ventas_count == 1000, f"Ventas debe tener 1,000 filas, tiene {ventas_count}"
    assert inv_count == 1200, f"Inventario debe tener 1,200 filas, tiene {inv_count}"
    assert fin_count == 800, f"Finanzas debe tener 800 filas, tiene {fin_count}"


# ── TEST 2: VERIFICACIÓN MATEMÁTICA DE VENTAS RETAIL ───────────────────────────

def test_ventas_retail_ground_truth_matches(duckdb_con: duckdb.DuckDBPyConnection, ground_truth_data: dict):
    """Verifica que los totales de ventas y márgenes calculados coincidan con el ground truth."""
    expected_v = ground_truth_data["ventas_retail"]

    res = duckdb_con.execute("""
        SELECT 
            ROUND(SUM(ingreso_total), 2),
            ROUND(SUM(unidades * costo_unitario), 2),
            ROUND(SUM(margen), 2),
            SUM(unidades)
        FROM ventas
    """).fetchone()

    total_ingresos = float(res[0])
    total_costo = float(res[1])
    total_margen = float(res[2])
    total_unidades = int(res[3])

    assert total_ingresos == pytest.approx(expected_v["total_ingresos"], abs=0.01)
    assert total_costo == pytest.approx(expected_v["total_costo"], abs=0.01)
    assert total_margen == pytest.approx(expected_v["total_margen"], abs=0.01)
    assert total_unidades == expected_v["total_unidades"]


def test_ventas_top_5_productos(duckdb_con: duckdb.DuckDBPyConnection, ground_truth_data: dict):
    """Verifica que el ranking Top-5 de productos coincida exactamente en orden e importes."""
    expected_top5 = ground_truth_data["ventas_retail"]["top_5_productos"]

    top5_res = duckdb_con.execute("""
        SELECT producto, ROUND(SUM(ingreso_total), 2), SUM(unidades)
        FROM ventas
        GROUP BY producto
        ORDER BY SUM(ingreso_total) DESC
        LIMIT 5
    """).fetchall()

    for idx, row in enumerate(top5_res):
        assert row[0] == expected_top5[idx]["producto"]
        assert float(row[1]) == pytest.approx(expected_top5[idx]["total_ingresos"], abs=0.01)
        assert int(row[2]) == expected_top5[idx]["unidades"]


# ── TEST 3: VERIFICACIÓN MATEMÁTICA DE INVENTARIO (SNAPSHOT GUARD) ─────────────

def test_inventario_snapshots_ground_truth_and_guard(duckdb_con: duckdb.DuckDBPyConnection, ground_truth_data: dict):
    """Verifica que el cálculo al corte MAX(fecha_corte) no sume el acumulado histórico."""
    expected_inv = ground_truth_data["inventario_snapshots"]

    # 1. Suma histórica errónea
    hist_sum = duckdb_con.execute("SELECT SUM(stock_disponible) FROM inventario").fetchone()[0]
    assert hist_sum == expected_inv["suma_historica_erronea_stock"]

    # 2. Stock actual al último corte (Snapshot Guard)
    max_corte = str(expected_inv["max_fecha_corte"])
    actual_sum = duckdb_con.execute(f"""
        SELECT SUM(stock_disponible) FROM inventario WHERE fecha_corte = '{max_corte}'
    """).fetchone()[0]

    assert actual_sum == expected_inv["stock_actual_corte_max"]
    assert actual_sum < hist_sum, "El stock al último corte debe ser menor que la suma acumulada de todos los cortes"

    # 3. Stock por almacén
    alm_res = duckdb_con.execute(f"""
        SELECT almacen_id, SUM(stock_disponible)
        FROM inventario
        WHERE fecha_corte = '{max_corte}'
        GROUP BY almacen_id
        ORDER BY almacen_id
    """).fetchall()

    for row in alm_res:
        alm_id, stock_val = row[0], int(row[1])
        assert stock_val == expected_inv["stock_por_almacen_actual"][alm_id]


# ── TEST 4: VERIFICACIÓN MATEMÁTICA DE FINANZAS Y FLUJO ─────────────────────────

def test_finanzas_flujo_ground_truth_matches(duckdb_con: duckdb.DuckDBPyConnection, ground_truth_data: dict):
    """Verifica que el flujo de caja neto (Ingresos - Egresos) coincida con el ground truth."""
    expected_fin = ground_truth_data["finanzas_flujo"]

    res = duckdb_con.execute("""
        SELECT 
            ROUND(SUM(CASE WHEN tipo_movimiento = 'Ingreso' THEN monto ELSE 0 END), 2),
            ROUND(SUM(CASE WHEN tipo_movimiento = 'Egreso' THEN monto ELSE 0 END), 2)
        FROM finanzas
    """).fetchone()

    total_ing = float(res[0])
    total_egr = float(res[1])
    flujo_neto = round(total_ing - total_egr, 2)

    assert total_ing == pytest.approx(expected_fin["total_ingresos"], abs=0.01)
    assert total_egr == pytest.approx(expected_fin["total_egresos"], abs=0.01)
    assert flujo_neto == pytest.approx(expected_fin["flujo_neto_total"], abs=0.01)


# ── TEST 5: CONTRATOS TIPADOS V1 EN ANALYTICAL_CONTRACT ────────────────────────

def test_v1_analytical_contracts_instantiation():
    """Valida que todos los nuevos modelos Pydantic V1 se puedan instanciar, serializar y validar."""
    # 1. ColumnResolution
    col_res = ColumnResolution(column_name="ingreso_total", status="resolved")
    assert col_res.status == "resolved"
    assert col_res.column_name == "ingreso_total"

    ambiguous_res = ColumnResolution(
        column_name=None,
        status="ambiguous",
        candidates=["fecha_venta", "fecha_registro"],
        reason="Múltiples columnas temporales detectadas"
    )
    assert ambiguous_res.status == "ambiguous"
    assert len(ambiguous_res.candidates) == 2

    # 2. QueryAnalyticalContractV1
    q_contract = QueryAnalyticalContractV1(
        query_id="q-12345",
        file_id="f-67890",
        intent_type="trend",
        metrics=[col_res],
        dimensions=[ColumnResolution(column_name="region", status="resolved")],
        filters=[FilterSpec(column="region", operator="==", value="Norte", origin="user")],
        state="valid",
    )
    assert q_contract.state == "valid"
    assert len(q_contract.metrics) == 1
    assert q_contract.contract_version == "1.0"

    # Serialización y deserialización Pydantic roundtrip
    dumped = q_contract.model_dump()
    reloaded = QueryAnalyticalContractV1.model_validate(dumped)
    assert reloaded.query_id == q_contract.query_id
    assert reloaded.metrics[0].column_name == "ingreso_total"

    # 3. EvidenceBundleV1
    ev_bundle = EvidenceBundleV1(
        evidence_id="ev-001",
        query_id="q-12345",
        plan_hash="sha256:abcd1234efgh5678",
        dataset_version="v1.0",
        computed_facts={"total_ingresos": 1561828.66},
        row_count=1000,
        execution_timestamp="2026-09-02T20:00:00Z",
    )
    assert ev_bundle.computed_facts["total_ingresos"] == 1561828.66

    # 4. VisualContractV1
    vis_contract = VisualContractV1(
        chart_type="line",
        x_axis="mes",
        y_axis="ingreso_total",
        series=[{"name": "Ventas", "data": [100, 200, 300]}],
        format_currency="USD",
        evidence_id="ev-001",
    )
    assert vis_contract.chart_type == "line"
    assert vis_contract.format_currency == "USD"

    # 5. InteractionContractV1
    int_contract = InteractionContractV1(
        dataset_version="v1.0",
        base_filters={"region": "Norte"},
        active_filters={"mes": "2024-05"},
        snapshot_complete=True,
        recomputation_policy="local",
        evidence_id="ev-001",
    )
    assert int_contract.snapshot_complete is True
    assert int_contract.recomputation_policy == "local"

    # 6. UploadFlowContractV1
    up_contract = UploadFlowContractV1()
    assert up_contract.flow_type == "direct_storage_rls"
    assert up_contract.bucket_name == "dash-uploads"
    assert up_contract.max_size_bytes == 52428800
