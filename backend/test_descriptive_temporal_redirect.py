"""Test de regresión: descriptive + requires_time + dimension → trend+split.

Verifica que build_plan_from_router_contract redirige correctamente un contrato
clasificado como 'descriptive' con señales temporales+dimensionales a un plan
de tipo 'trend' con split_dimension, en lugar de generar un bar chart estático.

Este test protege contra la regresión donde "análisis por región del 2025"
producía un solo gráfico de barras con totales en vez de un gráfico de líneas
con una serie por región mostrando la evolución mensual.
"""
import pytest
from app.services.semantic_translator.planner import build_plan_from_router_contract
from app.core.semantic_grammar import AnalysisPlan, MetricPolarity


# ── Fixtures ──────────────────────────────────────────────────────────

COLUMNS = [
    "fecha_venta", "region", "categoria", "producto",
    "cantidad", "precio_unitario", "ingreso_total",
]

SCHEMA_PROFILE = {
    "fecha_venta": {"role": "date", "cardinality": 365},
    "region": {"role": "dimension", "cardinality": 5},
    "categoria": {"role": "dimension", "cardinality": 8},
    "producto": {"role": "dimension", "cardinality": 20},
    "cantidad": {"role": "metric", "cardinality": 50},
    "precio_unitario": {"role": "metric", "cardinality": 100},
    "ingreso_total": {"role": "metric", "cardinality": 200},
}

DATASET_CONTRACT = {"dataset_mode": "flow", "time_axis": "fecha_venta"}


# ── Test cases ────────────────────────────────────────────────────────

class TestDescriptiveTemporalRedirect:
    """Verifica el redirect V2.3: descriptive + temporal + dimension → trend+split."""

    def test_descriptive_with_requires_time_and_dimension_produces_trend(self):
        """Caso principal: el router clasificó como descriptive pero con
        requires_time=True y dimension=region → debe producir trend+split."""
        router_decision = {
            "route": "SIMPLE",
            "confidence": 0.95,
            "detected_intent": "descriptive",
            "requires_time": True,
            "semantic_contract": {
                "intent": "descriptive",
                "metric": "ingreso_total",
                "dimension": "region",
                "series_mode": "none",
                "time_axis": None,
                "requires_time": True,
                "top_n": None,
                "grain": "month",
                "aggregation": "sum",
                "visual_protocol": "bar_chart",
                "positive_filters": [
                    {"column": "fecha_venta", "operator": ">=", "value": "2025-01-01"},
                    {"column": "fecha_venta", "operator": "<=", "value": "2025-12-31"},
                ],
                "negative_filters": [],
            },
        }
        plans = build_plan_from_router_contract(
            router_decision, COLUMNS,
            schema_profile=SCHEMA_PROFILE,
            dataset_contract=DATASET_CONTRACT,
        )
        assert plans is not None, "Plans should not be None"
        assert len(plans) >= 1, "Should produce at least 1 plan"
        intent = plans[0].main_intent
        intent_dict = intent if isinstance(intent, dict) else intent.dict()
        assert intent_dict["type"] == "trend", (
            f"Expected 'trend' but got '{intent_dict['type']}'. "
            "descriptive + requires_time + dimension must redirect to trend."
        )
        assert intent_dict.get("split_dimension") is not None, (
            "split_dimension must be populated for dimensional trend"
        )
        assert intent_dict.get("date_column") == "fecha_venta", (
            "date_column should be resolved from schema"
        )

    def test_descriptive_with_time_axis_and_dimension_produces_trend(self):
        """Variante: time_axis explícito en el contrato (en vez de requires_time)."""
        router_decision = {
            "route": "SIMPLE",
            "confidence": 0.9,
            "detected_intent": "descriptive",
            "requires_time": False,
            "semantic_contract": {
                "intent": "descriptive",
                "metric": "ingreso_total",
                "dimension": "categoria",
                "time_axis": "fecha_venta",
                "series_mode": "none",
                "requires_time": False,
                "top_n": None,
                "grain": "month",
                "aggregation": "sum",
                "visual_protocol": "bar_chart",
                "positive_filters": [],
                "negative_filters": [],
            },
        }
        plans = build_plan_from_router_contract(
            router_decision, COLUMNS,
            schema_profile=SCHEMA_PROFILE,
            dataset_contract=DATASET_CONTRACT,
        )
        assert plans is not None
        intent = plans[0].main_intent
        intent_dict = intent if isinstance(intent, dict) else intent.dict()
        assert intent_dict["type"] == "trend"
        assert intent_dict.get("split_dimension") == "categoria"

    def test_descriptive_without_temporal_signals_remains_distribution(self):
        """Sin requires_time ni time_axis, descriptive+dimension debe seguir
        produciendo un bar chart (distribution), NO redirigir a trend."""
        router_decision = {
            "route": "SIMPLE",
            "confidence": 0.95,
            "detected_intent": "descriptive",
            "requires_time": False,
            "semantic_contract": {
                "intent": "descriptive",
                "metric": "ingreso_total",
                "dimension": "region",
                "series_mode": "none",
                "time_axis": None,
                "requires_time": False,
                "top_n": None,
                "grain": None,
                "aggregation": "sum",
                "visual_protocol": "bar_chart",
                "positive_filters": [],
                "negative_filters": [],
            },
        }
        plans = build_plan_from_router_contract(
            router_decision, COLUMNS,
            schema_profile=SCHEMA_PROFILE,
            dataset_contract=DATASET_CONTRACT,
        )
        assert plans is not None
        intent = plans[0].main_intent
        intent_dict = intent if isinstance(intent, dict) else intent.dict()
        assert intent_dict["type"] == "distribution", (
            "Without temporal signals, descriptive+dimension should remain distribution"
        )

    def test_descriptive_with_temporal_but_no_dimension_remains_kpi(self):
        """requires_time pero sin dimension → debe seguir siendo KPI/descriptive,
        NO redirigir a trend (no hay dimensión para hacer split)."""
        router_decision = {
            "route": "SIMPLE",
            "confidence": 0.9,
            "detected_intent": "descriptive",
            "requires_time": True,
            "semantic_contract": {
                "intent": "descriptive",
                "metric": "ingreso_total",
                "dimension": None,
                "series_mode": "none",
                "time_axis": None,
                "requires_time": True,
                "top_n": None,
                "grain": None,
                "aggregation": "sum",
                "visual_protocol": "kpi",
                "positive_filters": [
                    {"column": "fecha_venta", "operator": ">=", "value": "2025-01-01"},
                    {"column": "fecha_venta", "operator": "<=", "value": "2025-12-31"},
                ],
                "negative_filters": [],
            },
        }
        plans = build_plan_from_router_contract(
            router_decision, COLUMNS,
            schema_profile=SCHEMA_PROFILE,
            dataset_contract=DATASET_CONTRACT,
        )
        assert plans is not None
        intent = plans[0].main_intent
        # Should be DescriptiveIntent (Pydantic model) or dict with type="descriptive"
        if isinstance(intent, dict):
            assert intent["type"] != "trend", (
                "Without dimension, should NOT redirect to trend"
            )
        else:
            # DescriptiveIntent Pydantic model
            assert hasattr(intent, "metrics"), (
                "Should be a DescriptiveIntent (KPI), not a trend"
            )

    def test_distribution_temporal_redirect_still_works(self):
        """Verifica que el redirect existente distribution→trend (V2.2) no se rompió."""
        router_decision = {
            "route": "SIMPLE",
            "confidence": 0.9,
            "detected_intent": "distribution",
            "requires_time": True,
            "semantic_contract": {
                "intent": "distribution",
                "metric": "ingreso_total",
                "dimension": "region",
                "time_axis": "fecha_venta",
                "series_mode": "none",
                "requires_time": True,
                "top_n": None,
                "grain": "month",
                "aggregation": "sum",
                "visual_protocol": "bar_chart",
                "positive_filters": [],
                "negative_filters": [],
            },
        }
        plans = build_plan_from_router_contract(
            router_decision, COLUMNS,
            schema_profile=SCHEMA_PROFILE,
            dataset_contract=DATASET_CONTRACT,
        )
        assert plans is not None
        intent = plans[0].main_intent
        intent_dict = intent if isinstance(intent, dict) else intent.dict()
        assert intent_dict["type"] == "trend", (
            "distribution + requires_time must still redirect to trend"
        )
        assert intent_dict.get("split_dimension") is not None

    def test_trend_intent_passes_through_unchanged(self):
        """Verifica que un intent=trend con series_mode=split sigue funcionando
        directamente sin necesitar el redirect."""
        router_decision = {
            "route": "SIMPLE",
            "confidence": 0.95,
            "detected_intent": "trend",
            "requires_time": True,
            "semantic_contract": {
                "intent": "trend",
                "metric": "ingreso_total",
                "dimension": "region",
                "time_axis": "fecha_venta",
                "series_mode": "split",
                "requires_time": True,
                "top_n": None,
                "grain": "month",
                "aggregation": "sum",
                "visual_protocol": "line_chart",
                "positive_filters": [],
                "negative_filters": [],
            },
        }
        plans = build_plan_from_router_contract(
            router_decision, COLUMNS,
            schema_profile=SCHEMA_PROFILE,
            dataset_contract=DATASET_CONTRACT,
        )
        assert plans is not None
        intent = plans[0].main_intent
        intent_dict = intent if isinstance(intent, dict) else intent.dict()
        assert intent_dict["type"] == "trend"
        assert intent_dict.get("split_dimension") == "region"


class TestTripleVistaDashboardEnrichment:
    """Verifica que _build_complementary_dashboard_plans genera 3 planes
    para un dashboard completo desde la ruta SIMPLE."""

    def test_trend_primary_gets_distribution_and_kpi_complements(self):
        """Trend+split por región debe producir 3 planes:
        trend (línea multi-serie) + distribution (barras) + KPI."""
        from app.services.semantic_translator.planner import (
            _build_complementary_dashboard_plans,
        )

        primary_plan_intent = {
            "type": "trend",
            "date_column": "fecha_venta",
            "value_column": "ingreso_total",
            "grain": "month",
            "fill_missing": True,
            "split_dimension": "region",
            "split_limit": None,
            "filters": [
                {"column": "fecha_venta", "operator": ">=", "value": "2025-01-01"},
                {"column": "fecha_venta", "operator": "<=", "value": "2025-12-31"},
            ],
            "visual_protocol": "line_chart",
            "metric_unit": "number",
            "rationale": "Test",
        }
        primary_plans = [
            AnalysisPlan(
                main_intent=primary_plan_intent,
                title="Evolución de Ingreso Total por Fecha Venta",
                column_aliases={"ingreso_total": "Ingreso Total", "fecha_venta": "Fecha Venta"},
                metric_polarity=MetricPolarity.NEUTRAL,
            )
        ]
        router_decision = {
            "route": "SIMPLE",
            "detected_intent": "trend",
            "semantic_contract": {
                "dimension": "region",
                "metric": "ingreso_total",
            },
        }
        result = _build_complementary_dashboard_plans(
            primary_plans, router_decision, COLUMNS,
            schema_profile=SCHEMA_PROFILE,
            dataset_contract=DATASET_CONTRACT,
        )
        assert len(result) == 3, (
            f"Expected 3 plans (Triple Vista) but got {len(result)}"
        )
        # Plan 1: trend (primary, unchanged)
        intent_1 = result[0].main_intent
        type_1 = intent_1.get("type") if isinstance(intent_1, dict) else getattr(intent_1, "type", "")
        assert type_1 == "trend"

        # Plan 2: distribution by dimension
        intent_2 = result[1].main_intent
        type_2 = intent_2.get("type") if isinstance(intent_2, dict) else getattr(intent_2, "type", "")
        assert type_2 == "distribution", (
            f"Plan 2 should be distribution, got '{type_2}'"
        )

        # Plan 3: KPI descriptive
        intent_3 = result[2].main_intent
        type_3 = intent_3.get("type") if isinstance(intent_3, dict) else getattr(intent_3, "type", "")
        assert type_3 == "descriptive", (
            f"Plan 3 should be descriptive KPI, got '{type_3}'"
        )

    def test_distribution_primary_gets_trend_and_kpi_complements(self):
        """Distribution por región debe producir 3 planes:
        distribution (barras) + trend (línea) + KPI."""
        from app.services.semantic_translator.planner import (
            _build_complementary_dashboard_plans,
            DistributionIntent,
        )
        from app.core.semantic_grammar import VisualProtocol, MetricUnit

        primary_plans = [
            AnalysisPlan(
                main_intent=DistributionIntent(
                    rationale="Test",
                    filters=[],
                    dimension="region",
                    metric="ingreso_total",
                    limit=5,
                    metric_unit=MetricUnit.NUMBER,
                    visual_protocol=VisualProtocol.BAR,
                ),
                title="Ingreso Total por Región",
                column_aliases={"ingreso_total": "Ingreso Total", "region": "Región"},
                metric_polarity=MetricPolarity.NEUTRAL,
            )
        ]
        router_decision = {
            "route": "SIMPLE",
            "detected_intent": "distribution",
            "semantic_contract": {"dimension": "region", "metric": "ingreso_total"},
        }
        result = _build_complementary_dashboard_plans(
            primary_plans, router_decision, COLUMNS,
            schema_profile=SCHEMA_PROFILE,
            dataset_contract=DATASET_CONTRACT,
        )
        assert len(result) == 3, (
            f"Expected 3 plans (Triple Vista) but got {len(result)}"
        )
        # Plan 2: trend
        intent_2 = result[1].main_intent
        type_2 = intent_2.get("type") if isinstance(intent_2, dict) else getattr(intent_2, "type", "")
        assert type_2 == "trend", f"Plan 2 should be trend, got '{type_2}'"

        # Plan 3: KPI
        intent_3 = result[2].main_intent
        type_3 = intent_3.get("type") if isinstance(intent_3, dict) else getattr(intent_3, "type", "")
        assert type_3 == "descriptive", f"Plan 3 should be KPI, got '{type_3}'"

    def test_simple_kpi_does_not_get_enriched(self):
        """Un KPI sin dimensión no debe enriquecerse — el usuario pidió un número."""
        from app.services.semantic_translator.planner import (
            _build_complementary_dashboard_plans,
            DescriptiveIntent,
        )
        from app.core.semantic_grammar import VisualProtocol, MetricUnit

        primary_plans = [
            AnalysisPlan(
                main_intent=DescriptiveIntent(
                    rationale="Test",
                    filters=[],
                    metrics=["ingreso_total"],
                    metric_unit=MetricUnit.NUMBER,
                    aggregation="sum",
                    visual_protocol=VisualProtocol.KPI,
                ),
                title="Ingreso Total",
                column_aliases={"ingreso_total": "Ingreso Total"},
                metric_polarity=MetricPolarity.NEUTRAL,
            )
        ]
        router_decision = {
            "route": "SIMPLE",
            "detected_intent": "descriptive",
            "semantic_contract": {"metric": "ingreso_total", "dimension": None},
        }
        result = _build_complementary_dashboard_plans(
            primary_plans, router_decision, COLUMNS,
            schema_profile=SCHEMA_PROFILE,
            dataset_contract=DATASET_CONTRACT,
        )
        assert len(result) == 1, (
            f"Simple KPI should NOT be enriched, but got {len(result)} plans"
        )

    def test_already_three_plans_are_not_enriched(self):
        """Si ya hay 3+ planes, no se enriquece."""
        from app.services.semantic_translator.planner import (
            _build_complementary_dashboard_plans,
            DescriptiveIntent,
        )
        from app.core.semantic_grammar import VisualProtocol, MetricUnit

        plans = [
            AnalysisPlan(
                main_intent=DescriptiveIntent(
                    rationale="Test",
                    filters=[], metrics=["ingreso_total"],
                    metric_unit=MetricUnit.NUMBER, aggregation="sum",
                    visual_protocol=VisualProtocol.KPI,
                ),
                title=f"Plan {i}",
                column_aliases={"ingreso_total": "Ingreso Total"},
                metric_polarity=MetricPolarity.NEUTRAL,
            )
            for i in range(3)
        ]
        router_decision = {"route": "SIMPLE", "semantic_contract": {}}
        result = _build_complementary_dashboard_plans(
            plans, router_decision, COLUMNS,
            schema_profile=SCHEMA_PROFILE,
            dataset_contract=DATASET_CONTRACT,
        )
        assert len(result) == 3, "Should return exactly 3 plans unchanged"

