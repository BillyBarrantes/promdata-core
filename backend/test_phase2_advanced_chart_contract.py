import os
import sys
import pandas as pd

sys.path.append(os.path.join(os.getcwd(), 'backend'))

from app.services.chart_factory import ChartFactory


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run() -> None:
    waterfall = ChartFactory.build_waterfall_chart(
        "P&L",
        [
            {"name": "Ingresos", "value": 1000},
            {"name": "Costos", "value": -400},
            {"name": "Gastos", "value": -150},
        ],
    )
    _assert(isinstance(waterfall, dict) and len(waterfall.get("series", [])) == 2, "Waterfall debe construir sus dos capas de barras")

    heatmap = ChartFactory.create_chart(
        "heatmap",
        "Stock por fecha y almacén",
        [
            {"Fecha de stock": pd.Timestamp("2026-05-01"), "Tipo almacén": "A", "Stock disponible": 12},
            {"Fecha de stock": pd.Timestamp("2026-05-02"), "Tipo almacén": "A", "Stock disponible": 20},
            {"Fecha de stock": pd.Timestamp("2026-05-01"), "Tipo almacén": "B", "Stock disponible": 7},
        ],
        x_label="Fecha de stock",
        y_label="Tipo almacén",
    )
    _assert(isinstance(heatmap, dict) and heatmap.get("series", [{}])[0].get("type") == "heatmap", "Heatmap debe renderizar con fechas sin romper serialización")
    _assert(all(isinstance(item, str) for item in heatmap.get("xAxis", {}).get("data", [])), "Los ejes temporales del Heatmap deben quedar serializados como texto")

    histogram = ChartFactory.build_histogram_chart(
        "Distribución de stock",
        [10, 12, 12, 14, 18, 22, 22, 23, 30, 31, 35],
    )
    _assert(isinstance(histogram, dict) and histogram.get("series", [{}])[0].get("type") == "bar", "Histogram debe construirse sobre valores crudos")
    _assert(len(histogram.get("xAxis", {}).get("data", [])) >= 5, "Histogram debe generar bins suficientes")

    scatter = ChartFactory.create_chart(
        "scatter",
        "Relación entre lotes y stock",
        [
            {"name": "Material A", "cantidad_lotes": 4, "stock_total": 120},
            {"name": "Material B", "cantidad_lotes": 8, "stock_total": 240},
            {"name": "Material C", "cantidad_lotes": 3, "stock_total": 90},
        ],
        x_label="cantidad_lotes",
        y_label="stock_total",
    )
    _assert(isinstance(scatter, dict) and scatter.get("series", [{}])[0].get("type") == "scatter", "Scatter debe soportar objetos con ejes explícitos")
    _assert(len(scatter.get("series", [{}])[0].get("data", [])) == 3, "Scatter debe conservar todos los puntos válidos")

    funnel = ChartFactory.build_funnel_chart(
        "Concentración de stock",
        [
            {"name": "Ubicación A", "value": 300},
            {"name": "Ubicación B", "value": 210},
            {"name": "Ubicación C", "value": 120},
        ],
    )
    _assert(isinstance(funnel, dict) and funnel.get("series", [{}])[0].get("type") == "funnel", "Funnel debe renderizar como funnel real")
    _assert(bool(funnel.get("series", [{}])[0].get("label")), "Funnel debe exponer etiquetas legibles")

    print("OK: phase2 advanced chart contract")


if __name__ == "__main__":
    run()
