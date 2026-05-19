import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '.'))

from app.core.semantic_grammar import (
    AnalysisPlan,
    DistributionIntent,
    TimeGrain,
    TimeTrendIntent,
    MetricUnit,
)
from app.services.metric_semantics import align_plan_metrics_with_prompt


def test_sales_prompt_prefers_currency_metric_over_quantity():
    schema_profile = {
        'fecha': {'role': 'date'},
        'categoria': {'role': 'dimension'},
        'cantidad_vendida': {'role': 'metric'},
        'total_venta_pen': {'role': 'metric'},
    }
    currency_meta = {'symbol': 'S/', 'code': 'PEN'}

    plans = [
        AnalysisPlan(
            title='Tendencia de ventas',
            main_intent=TimeTrendIntent(
                rationale='Seguir la evolucion mensual',
                date_column='fecha',
                value_column='cantidad_vendida',
                grain=TimeGrain.MONTH,
            ),
        ),
        AnalysisPlan(
            title='Ventas por categoria',
            main_intent=DistributionIntent(
                rationale='Comparar el peso comercial por categoria',
                dimension='categoria',
                metric='cantidad_vendida',
            ),
        ),
    ]

    aligned = align_plan_metrics_with_prompt(
        plans,
        'realiza un analisis sobre las ventas',
        schema_profile,
        currency_meta,
    )

    trend_intent = aligned[0].main_intent
    distribution_intent = aligned[1].main_intent

    assert trend_intent.value_column == 'total_venta_pen'
    assert trend_intent.metric_unit == MetricUnit.CURRENCY
    assert distribution_intent.metric == 'total_venta_pen'
    assert distribution_intent.metric_unit == MetricUnit.CURRENCY


def test_units_prompt_keeps_quantity_metric():
    schema_profile = {
        'provincia_de_venta': {'role': 'dimension'},
        'cantidad_vendida': {'role': 'metric'},
        'total_venta_pen': {'role': 'metric'},
    }

    plans = [
        AnalysisPlan(
            title='Unidades por provincia',
            main_intent=DistributionIntent(
                rationale='Comparar volumen fisico por provincia',
                dimension='provincia_de_venta',
                metric='cantidad_vendida',
            ),
        ),
    ]

    aligned = align_plan_metrics_with_prompt(
        plans,
        'realiza un analisis de unidades vendidas por provincia',
        schema_profile,
        {'symbol': 'S/', 'code': 'PEN'},
    )

    distribution_intent = aligned[0].main_intent
    assert distribution_intent.metric == 'cantidad_vendida'
    assert distribution_intent.metric_unit == MetricUnit.QUANTITY


def test_dimension_prompt_without_explicit_unit_keeps_quantity_semantics():
    schema_profile = {
        'provincia_de_venta': {'role': 'dimension'},
        'cantidad_vendida': {'role': 'metric'},
        'total_venta_pen': {'role': 'metric'},
    }

    plans = [
        AnalysisPlan(
            title='Distribucion por provincia',
            main_intent=DistributionIntent(
                rationale='Comparar el peso territorial',
                dimension='provincia_de_venta',
                metric='cantidad_vendida',
            ),
        ),
    ]

    aligned = align_plan_metrics_with_prompt(
        plans,
        'realiza un analisis por provincia',
        schema_profile,
        {'symbol': 'S/', 'code': 'PEN'},
    )

    distribution_intent = aligned[0].main_intent
    assert distribution_intent.metric == 'cantidad_vendida'
    assert distribution_intent.metric_unit == MetricUnit.QUANTITY
