"""
generate_fixtures.py — Generador determinista de fixtures canónicos para PromData V1
══════════════════════════════════════════════════════════════════════════════════
Produce 3 datasets canónicos con semilla fija (seed=42):
  1. ventas_retail_v1.csv (1,000 filas: transacciones, precios, márgenes)
  2. inventario_snapshots_v1.csv (1,200 filas: cortes temporales acumulados)
  3. finanzas_flujo_v1.csv (800 filas: ingresos, egresos y centros de costo)
"""
from __future__ import annotations

import csv
from datetime import date, timedelta
from pathlib import Path
import random

SEED = 42
FIXTURES_DIR = Path(__file__).resolve().parent


def generate_ventas_retail(output_path: Path, num_rows: int = 1000) -> None:
    random.seed(SEED)
    
    regiones = ["Norte", "Sur", "Centro", "Oriente", "Occidente"]
    productos = [
        ("Laptop Pro 15", 450.00, 750.00),
        ("Monitor 4K 27", 180.00, 299.00),
        ("Teclado Mecanico RGB", 35.00, 69.90),
        ("Mouse Inalambrico Ergo", 18.00, 39.50),
        ("Auriculares USB-C", 25.00, 49.99),
        ("Impresora Laser Multifuncion", 120.00, 199.00),
        ("Disco SSD 1TB NVMe", 45.00, 89.00),
        ("Memoria RAM 16GB DDR5", 38.00, 72.00),
        ("Camara Web 1080p", 22.00, 45.00),
        ("Hub USB-C 7 en 1", 15.00, 32.50),
    ]
    canales = ["Tienda Fisica", "E-commerce", "Distribuidores", "Ventas B2B"]
    
    start_date = date(2024, 1, 1)
    end_date = date(2024, 12, 31)
    date_range_days = (end_date - start_date).days

    rows = []
    for _ in range(num_rows):
        d_offset = random.randint(0, date_range_days)
        fecha_str = (start_date + timedelta(days=d_offset)).isoformat()
        region = random.choice(regiones)
        prod_name, costo_base, precio_base = random.choice(productos)
        canal = random.choice(canales)
        
        canal_factor = 0.90 if canal == "Distribuidores" else (0.95 if canal == "Ventas B2B" else 1.0)
        unidades = random.randint(1, 8) if canal in ("Tienda Fisica", "E-commerce") else random.randint(5, 30)
        
        costo_unit = round(costo_base * random.uniform(0.98, 1.02), 2)
        precio_unit = round(precio_base * canal_factor * random.uniform(0.97, 1.03), 2)
        ingreso_total = round(unidades * precio_unit, 2)
        costo_total = round(unidades * costo_unit, 2)
        margen = round(ingreso_total - costo_total, 2)
        
        rows.append({
            "fecha": fecha_str,
            "region": region,
            "producto": prod_name,
            "canal": canal,
            "unidades": unidades,
            "costo_unitario": costo_unit,
            "precio_venta": precio_unit,
            "ingreso_total": ingreso_total,
            "margen": margen,
        })

    rows.sort(key=lambda r: r["fecha"])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "fecha", "region", "producto", "canal", "unidades",
            "costo_unitario", "precio_venta", "ingreso_total", "margen"
        ])
        writer.writeheader()
        writer.writerows(rows)


def generate_inventario_snapshots(output_path: Path) -> None:
    random.seed(SEED)
    
    cortes = ["2024-01-31", "2024-02-29", "2024-03-31", "2024-04-30"]
    almacenes = ["ALM-NORTE", "ALM-CENTRO", "ALM-SUR"]
    categorias = ["Hardware", "Accesorios", "Almacenamiento", "Redes"]
    
    sku_catalog = []
    for alm in almacenes:
        for i in range(1, 101):
            sku_id = f"SKU-{alm[:3]}-{i:03d}"
            cat = categorias[(i - 1) % len(categorias)]
            stock_minimo = random.randint(20, 60)
            costo_unitario = round(random.uniform(10.0, 350.0), 2)
            sku_catalog.append({
                "almacen_id": alm,
                "sku_id": sku_id,
                "categoria": cat,
                "stock_minimo": stock_minimo,
                "costo_unitario": costo_unitario,
            })
            
    rows = []
    current_stocks = {
        (item["almacen_id"], item["sku_id"]): random.randint(15, 180)
        for item in sku_catalog
    }
    
    for corte in cortes:
        for item in sku_catalog:
            key = (item["almacen_id"], item["sku_id"])
            delta = random.randint(-25, 30)
            new_stock = max(0, current_stocks[key] + delta)
            current_stocks[key] = new_stock
            
            rows.append({
                "fecha_corte": corte,
                "almacen_id": item["almacen_id"],
                "sku_id": item["sku_id"],
                "categoria": item["categoria"],
                "stock_disponible": new_stock,
                "stock_minimo": item["stock_minimo"],
                "costo_unitario": item["costo_unitario"],
            })

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "fecha_corte", "almacen_id", "sku_id", "categoria",
            "stock_disponible", "stock_minimo", "costo_unitario"
        ])
        writer.writeheader()
        writer.writerows(rows)


def generate_finanzas_flujo(output_path: Path, num_rows: int = 800) -> None:
    random.seed(SEED)
    
    categorias_egreso = ["Servicios Cloud", "Sueldos y Planilla", "Marketing Digital", "Logistica y Envíos", "Arriendos y Servicios", "Software Licencias"]
    categorias_ingreso = ["Venta Suscripciones SaaS", "Consultoria Analitica", "Licenciamiento Enterprise", "Soporte Tecnico"]
    centros_costo = ["Tecnologia", "Ventas", "Operaciones", "Administracion"]
    
    start_date = date(2024, 1, 1)
    end_date = date(2024, 12, 31)
    date_range_days = (end_date - start_date).days

    rows = []
    for _ in range(num_rows):
        d_offset = random.randint(0, date_range_days)
        fecha_str = (start_date + timedelta(days=d_offset)).isoformat()
        
        es_ingreso = random.random() < 0.40
        tipo = "Ingreso" if es_ingreso else "Egreso"
        
        if es_ingreso:
            cat = random.choice(categorias_ingreso)
            monto = round(random.uniform(500.0, 15000.0), 2)
            cc = random.choice(["Ventas", "Operaciones"])
        else:
            cat = random.choice(categorias_egreso)
            monto = round(random.uniform(100.0, 8000.0), 2)
            cc = random.choice(centros_costo)
            
        estado = "Conciliado" if random.random() < 0.85 else "Completado"
        
        rows.append({
            "fecha": fecha_str,
            "tipo_movimiento": tipo,
            "categoria_gasto": cat,
            "centro_costo": cc,
            "monto": monto,
            "moneda": "USD",
            "estado": estado,
        })

    rows.sort(key=lambda r: r["fecha"])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "fecha", "tipo_movimiento", "categoria_gasto", "centro_costo",
            "monto", "moneda", "estado"
        ])
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    print(f"Generando fixtures canónicos en: {FIXTURES_DIR}")
    ventas_path = FIXTURES_DIR / "ventas_retail_v1.csv"
    inv_path = FIXTURES_DIR / "inventario_snapshots_v1.csv"
    fin_path = FIXTURES_DIR / "finanzas_flujo_v1.csv"
    
    generate_ventas_retail(ventas_path, 1000)
    print(f"✅ {ventas_path.name} generado con 1,000 filas.")
    
    generate_inventario_snapshots(inv_path)
    print(f"✅ {inv_path.name} generado con 1,200 filas (4 cortes × 300 SKUs).")
    
    generate_finanzas_flujo(fin_path, 800)
    print(f"✅ {fin_path.name} generado con 800 filas.")


if __name__ == "__main__":
    main()
