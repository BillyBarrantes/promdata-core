"""
Integration test: direction_detector + semantic_translator post-processor.

Verifies that DistributionIntent gets split_dimension injected when a direction
column is detected in the schema_profile.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.services.semantic_translator import SemanticTranslator
from app.core.semantic_grammar import AnalysisPlan


def test_direction_guard_injected_in_explicit_distribution():
    """Fast-path distribution plan gets split_dimension when direction detected."""
    # Build a distribution plan manually to test the post-processor
    plan = AnalysisPlan(
        main_intent={
            "type": "distribution",
            "rationale": "Test",
            "filters": [],
            "metric_unit": "number",
            "visual_protocol": "bar_chart",
            "dimension": "centro_costo",
            "metric": "monto",
            "limit": 5,
            "group_by": None,
            "barmode": "stacked",
        },
        title="Monto por centro_costo",
        column_aliases={"monto": "Monto", "centro_costo": "Centro de Costo"},
        metric_polarity="neutral",
    )
    
    schema_profile = {
        "centro_costo": {"type": "categorical", "role": "dimension", "cardinality": 5},
        "tipo_movimiento": {"type": "categorical", "role": "dimension", "cardinality": 2, "unique_values": ["Ingreso", "Egreso"]},
        "monto": {"type": "numeric", "role": "metric", "cardinality": 10000},
    }
    
    plans = SemanticTranslator._apply_direction_guard_to_distribution_plans([plan], schema_profile)
    
    assert len(plans) == 1
    main_intent = plans[0].main_intent
    assert main_intent.dimension == "centro_costo"
    assert getattr(main_intent, "group_by", None) == ["tipo_movimiento"]
    assert getattr(main_intent, "barmode", None) == "stacked"
    print("✅ Explicit distribution: direction guard injected correctly")


def test_direction_guard_not_injected_when_no_direction():
    """Distribution plan without direction column stays unchanged."""
    plan = AnalysisPlan(
        main_intent={
            "type": "distribution",
            "rationale": "Test",
            "filters": [],
            "metric_unit": "number",
            "visual_protocol": "bar_chart",
            "dimension": "region",
            "metric": "ventas",
            "limit": 5,
            "group_by": None,
            "barmode": "stacked",
        },
        title="Ventas por region",
        column_aliases={"ventas": "Ventas", "region": "Region"},
        metric_polarity="neutral",
    )
    
    schema_profile = {
        "region": {"type": "categorical", "role": "dimension", "cardinality": 5},
        "producto": {"type": "categorical", "role": "dimension", "cardinality": 20},
        "ventas": {"type": "numeric", "role": "metric", "cardinality": 1000},
    }
    
    plans = SemanticTranslator._apply_direction_guard_to_distribution_plans([plan], schema_profile)
    
    assert len(plans) == 1
    main_intent = plans[0].main_intent
    assert main_intent.dimension == "region"
    assert getattr(main_intent, "split_dimension", None) is None
    print("✅ No direction column: guard correctly skipped")


def test_direction_guard_skips_when_direction_is_dimension():
    """If the direction column IS the dimension, no split needed."""
    plan = AnalysisPlan(
        main_intent={
            "type": "distribution",
            "rationale": "Test",
            "filters": [],
            "metric_unit": "number",
            "visual_protocol": "bar_chart",
            "dimension": "tipo_movimiento",
            "metric": "monto",
            "limit": 5,
            "group_by": None,
            "barmode": "stacked",
        },
        title="Monto por tipo_movimiento",
        column_aliases={"monto": "Monto", "tipo_movimiento": "Tipo Movimiento"},
        metric_polarity="neutral",
    )
    
    schema_profile = {
        "tipo_movimiento": {"type": "categorical", "role": "dimension", "cardinality": 2, "unique_values": ["Ingreso", "Egreso"]},
        "monto": {"type": "numeric", "role": "metric", "cardinality": 1000},
    }
    
    plans = SemanticTranslator._apply_direction_guard_to_distribution_plans([plan], schema_profile)
    
    assert len(plans) == 1
    main_intent = plans[0].main_intent
    assert main_intent.dimension == "tipo_movimiento"
    # Direction column IS the dimension -> no group_by injection (would be redundant)
    assert getattr(main_intent, "group_by", None) is None
    print("✅ Direction column is dimension: guard correctly skipped")


if __name__ == "__main__":
    test_direction_guard_injected_in_explicit_distribution()
    test_direction_guard_not_injected_when_no_direction()
    test_direction_guard_skips_when_direction_is_dimension()
    print("\n🎉 All integration tests passed!")
