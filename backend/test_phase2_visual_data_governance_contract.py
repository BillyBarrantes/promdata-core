import os
import sys

sys.path.append(os.path.join(os.getcwd(), 'backend'))

from app.core.semantic_grammar import AnalysisPlan
from app.services.snapshot_guard import should_apply_latest_snapshot_filter
from app.services.visual_recommendation_engine import should_enable_visual_probe_mode


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run() -> None:
    temporal_distribution_plan = AnalysisPlan.model_validate(
        {
            "title": "Heatmap temporal de stock",
            "column_aliases": {
                "Fecha de stock": "Fecha de stock",
                "Tipo almacén": "Tipo almacén",
                "Stock disponible": "Stock disponible",
            },
            "main_intent": {
                "type": "distribution",
                "rationale": "Matriz temporal para intensidad de stock.",
                "dimension": "Fecha de stock",
                "group_by": ["Tipo almacén"],
                "metric": "Stock disponible",
                "visual_protocol": "heatmap",
                "metric_unit": "quantity",
            },
        }
    )

    snapshot_guard_temporal = should_apply_latest_snapshot_filter(
        temporal_distribution_plan.main_intent,
        ["Fecha de stock", "Tipo almacén", "Stock disponible", "is_latest_snapshot"],
        {
            "snapshot_guard_allowed": True,
            "dataset_mode": "snapshot",
            "time_axis": "Fecha de stock",
        },
    )
    _assert(
        snapshot_guard_temporal is False,
        "El snapshot guard no debe colapsar un heatmap que usa explícitamente el eje temporal",
    )

    stock_distribution_plan = AnalysisPlan.model_validate(
        {
            "title": "Top ubicaciones por stock",
            "column_aliases": {
                "Ubicación": "Ubicación",
                "Stock disponible": "Stock disponible",
            },
            "main_intent": {
                "type": "distribution",
                "rationale": "Distribución de stock por ubicación.",
                "dimension": "Ubicación",
                "metric": "Stock disponible",
                "visual_protocol": "bar_chart",
                "metric_unit": "quantity",
            },
        }
    )
    snapshot_guard_stock = should_apply_latest_snapshot_filter(
        stock_distribution_plan.main_intent,
        ["Ubicación", "Stock disponible", "is_latest_snapshot"],
        {
            "snapshot_guard_allowed": True,
            "dataset_mode": "snapshot",
            "time_axis": "Fecha de stock",
        },
    )
    _assert(
        snapshot_guard_stock is True,
        "El snapshot guard debe seguir protegiendo distribuciones de stock no temporales",
    )

    _assert(
        should_enable_visual_probe_mode(
            "Quiero un heatmap del stock disponible por Fecha de stock y Tipo almacén."
        ) is True,
        "Un prompt de exploración visual explícita debe activar visual probe mode",
    )
    _assert(
        should_enable_visual_probe_mode(
            "Quiero un heatmap y una acción recomendada por riesgo de sobre-stock."
        ) is False,
        "Si el prompt pide acción o riesgo, no debe tratarse como exploración visual pura",
    )

    print("OK: phase2 visual data governance contract")


if __name__ == "__main__":
    run()
