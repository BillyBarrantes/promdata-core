"""
generate_ground_truth.py — Cálculo determinista de ground truth con Ibis + DuckDB
══════════════════════════════════════════════════════════════════════════════════
Lee los 3 fixtures canónicos con DuckDB / Ibis y congela los valores exactos
en fixtures/v1/expected_ground_truth.json.
"""
from __future__ import annotations

import json
from pathlib import Path
import duckdb

FIXTURES_DIR = Path(__file__).resolve().parent


def compute_ground_truth() -> dict:
    ventas_file = FIXTURES_DIR / "ventas_retail_v1.csv"
    inv_file = FIXTURES_DIR / "inventario_snapshots_v1.csv"
    fin_file = FIXTURES_DIR / "finanzas_flujo_v1.csv"

    con = duckdb.connect(database=":memory:")

    # 1. Cargar tablas en DuckDB
    con.execute(f"CREATE TABLE ventas AS SELECT * FROM read_csv_auto('{ventas_file}')")
    con.execute(f"CREATE TABLE inventario AS SELECT * FROM read_csv_auto('{inv_file}')")
    con.execute(f"CREATE TABLE finanzas AS SELECT * FROM read_csv_auto('{fin_file}')")

    # ── 1. VENTAS RETAIL ───────────────────────────────────────────────────────
    v_totals = con.execute("""
        SELECT 
            ROUND(SUM(ingreso_total), 2) AS total_ingresos,
            ROUND(SUM(unidades * costo_unitario), 2) AS total_costo,
            ROUND(SUM(margen), 2) AS total_margen,
            SUM(unidades) AS total_unidades,
            COUNT(*) AS total_filas
        FROM ventas
    """).fetchone()

    canales_query = con.execute("""
        SELECT 
            canal,
            ROUND(AVG(margen), 2) AS margen_promedio,
            ROUND(SUM(ingreso_total), 2) AS total_ingresos,
            SUM(unidades) AS unidades
        FROM ventas
        GROUP BY canal
        ORDER BY canal
    """).fetchall()
    margen_por_canal = {
        row[0]: {
            "margen_promedio": float(row[1]),
            "total_ingresos": float(row[2]),
            "unidades": int(row[3]),
        }
        for row in canales_query
    }

    top5_prods = con.execute("""
        SELECT 
            producto,
            ROUND(SUM(ingreso_total), 2) AS total_ingresos,
            SUM(unidades) AS unidades
        FROM ventas
        GROUP BY producto
        ORDER BY total_ingresos DESC
        LIMIT 5
    """).fetchall()
    top_5_productos = [
        {"producto": row[0], "total_ingresos": float(row[1]), "unidades": int(row[2])}
        for row in top5_prods
    ]

    ventas_mensuales_query = con.execute("""
        SELECT 
            STRFTIME(CAST(fecha AS DATE), '%Y-%m') AS mes,
            ROUND(SUM(ingreso_total), 2) AS total_ingresos,
            SUM(unidades) AS unidades
        FROM ventas
        GROUP BY mes
        ORDER BY mes
    """).fetchall()
    ventas_mensuales = [
        {"mes": row[0], "total_ingresos": float(row[1]), "unidades": int(row[2])}
        for row in ventas_mensuales_query
    ]

    # ── 2. INVENTARIO SNAPSHOTS ────────────────────────────────────────────────
    inv_totals = con.execute("""
        SELECT 
            SUM(stock_disponible) AS suma_historica_erronea_stock,
            MAX(fecha_corte) AS max_fecha_corte,
            COUNT(DISTINCT fecha_corte) AS num_cortes,
            COUNT(*) AS total_filas
        FROM inventario
    """).fetchone()
    max_corte = str(inv_totals[1])

    stock_actual_query = con.execute(f"""
        SELECT 
            SUM(stock_disponible) AS stock_actual_total,
            ROUND(SUM(stock_disponible * costo_unitario), 2) AS valor_inventario_actual
        FROM inventario
        WHERE fecha_corte = '{max_corte}'
    """).fetchone()

    stock_almacen_query = con.execute(f"""
        SELECT 
            almacen_id,
            SUM(stock_disponible) AS stock_disponible
        FROM inventario
        WHERE fecha_corte = '{max_corte}'
        GROUP BY almacen_id
        ORDER BY almacen_id
    """).fetchall()
    stock_por_almacen_actual = {row[0]: int(row[1]) for row in stock_almacen_query}

    skus_criticos_query = con.execute(f"""
        SELECT COUNT(*) 
        FROM inventario
        WHERE fecha_corte = '{max_corte}' AND stock_disponible < stock_minimo
    """).fetchone()

    # ── 3. FINANZAS FLUJO ──────────────────────────────────────────────────────
    fin_totals = con.execute("""
        SELECT 
            ROUND(SUM(CASE WHEN tipo_movimiento = 'Ingreso' THEN monto ELSE 0 END), 2) AS total_ingresos,
            ROUND(SUM(CASE WHEN tipo_movimiento = 'Egreso' THEN monto ELSE 0 END), 2) AS total_egresos,
            COUNT(*) AS total_filas
        FROM finanzas
    """).fetchone()
    total_ing = float(fin_totals[0])
    total_egr = float(fin_totals[1])
    flujo_neto = round(total_ing - total_egr, 2)

    trimestres_query = con.execute("""
        SELECT 
            CASE 
                WHEN STRFTIME(CAST(fecha AS DATE), '%m') IN ('01','02','03') THEN '2024-Q1'
                WHEN STRFTIME(CAST(fecha AS DATE), '%m') IN ('04','05','06') THEN '2024-Q2'
                WHEN STRFTIME(CAST(fecha AS DATE), '%m') IN ('07','08','09') THEN '2024-Q3'
                ELSE '2024-Q4'
            END AS trimestre,
            ROUND(SUM(CASE WHEN tipo_movimiento = 'Ingreso' THEN monto ELSE 0 END), 2) AS ingresos,
            ROUND(SUM(CASE WHEN tipo_movimiento = 'Egreso' THEN monto ELSE 0 END), 2) AS egresos
        FROM finanzas
        GROUP BY trimestre
        ORDER BY trimestre
    """).fetchall()
    flujo_por_trimestre = {
        row[0]: {
            "ingresos": float(row[1]),
            "egresos": float(row[2]),
            "flujo_neto": round(float(row[1]) - float(row[2]), 2),
        }
        for row in trimestres_query
    }

    gasto_cc_query = con.execute("""
        SELECT 
            centro_costo,
            ROUND(SUM(monto), 2) AS total_gasto
        FROM finanzas
        WHERE tipo_movimiento = 'Egreso'
        GROUP BY centro_costo
        ORDER BY total_gasto DESC
    """).fetchall()
    gasto_por_centro_costo = {row[0]: float(row[1]) for row in gasto_cc_query}

    ground_truth = {
        "metadata": {
            "ground_truth_version": "1.0",
            "seed": 42,
            "engine": "DuckDB-Ibis-canonical",
        },
        "ventas_retail": {
            "total_filas": int(v_totals[4]),
            "total_ingresos": float(v_totals[0]),
            "total_costo": float(v_totals[1]),
            "total_margen": float(v_totals[2]),
            "total_unidades": int(v_totals[3]),
            "margen_por_canal": margen_por_canal,
            "top_5_productos": top_5_productos,
            "ventas_mensuales_2024": ventas_mensuales,
        },
        "inventario_snapshots": {
            "total_filas": int(inv_totals[3]),
            "suma_historica_erronea_stock": int(inv_totals[0]),
            "max_fecha_corte": max_corte,
            "num_cortes": int(inv_totals[2]),
            "stock_actual_corte_max": int(stock_actual_query[0]),
            "valor_inventario_actual": float(stock_actual_query[1]),
            "stock_por_almacen_actual": stock_por_almacen_actual,
            "skus_criticos_count": int(skus_criticos_query[0]),
        },
        "finanzas_flujo": {
            "total_filas": int(fin_totals[2]),
            "total_ingresos": total_ing,
            "total_egresos": total_egr,
            "flujo_neto_total": flujo_neto,
            "flujo_por_trimestre": flujo_por_trimestre,
            "gasto_por_centro_costo": gasto_por_centro_costo,
        },
    }

    con.close()
    return ground_truth


def main() -> None:
    gt = compute_ground_truth()
    out_file = FIXTURES_DIR / "expected_ground_truth.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(gt, f, indent=2, ensure_ascii=False)
    print(f"✅ Ground truth congelado con éxito en: {out_file.name}")
    print(f"   • Ventas Total Ingresos: ${gt['ventas_retail']['total_ingresos']:,.2f}")
    print(f"   • Inventario Stock Actual (corte {gt['inventario_snapshots']['max_fecha_corte']}): {gt['inventario_snapshots']['stock_actual_corte_max']:,} unidades")
    print(f"     (vs suma histórica errónea: {gt['inventario_snapshots']['suma_historica_erronea_stock']:,} unidades)")
    print(f"   • Finanzas Flujo Neto Total: ${gt['finanzas_flujo']['flujo_neto_total']:,.2f}")


if __name__ == "__main__":
    main()
