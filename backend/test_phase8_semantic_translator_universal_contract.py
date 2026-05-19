from app.services.semantic_translator import SemanticTranslator
from app.core.semantic_grammar import AnalysisPlan


def test_phase8_semantic_translator_prefers_requested_ubicacion_over_lote() -> None:
    plans = SemanticTranslator._build_dimension_analysis_bundle(
        "dame un analisis por ubicacion",
        ["fecha_de_stock", "ubicacion", "lote", "stock_disponible"],
        schema_profile={
            "fecha_de_stock": {"type": "temporal", "role": "date", "cardinality": 3},
            "ubicacion": {"type": "categorical", "role": "dimension", "cardinality": 12},
            "lote": {"type": "categorical", "role": "identifier", "cardinality": 120},
            "stock_disponible": {"type": "numeric", "role": "metric", "cardinality": 50},
        },
        dataset_contract={
            "dataset_mode": "snapshot",
            "time_axis": "fecha_de_stock",
            "date_columns": ["fecha_de_stock"],
            "metric_columns": ["stock_disponible"],
            "dimension_columns": ["ubicacion"],
            "identifier_columns": ["lote"],
            "snapshot_guard_allowed": True,
        },
    )

    assert plans
    assert getattr(plans[0].main_intent, "dimension", None) == "ubicacion"


def test_phase8_semantic_translator_applies_latest_snapshot_bias_to_distribution_fastpath() -> None:
    plans = SemanticTranslator._build_explicit_distribution_plan(
        "dame un grafico por ubicacion",
        ["fecha_de_stock", "ubicacion", "stock_disponible"],
        schema_profile={
            "fecha_de_stock": {"type": "temporal", "role": "date", "cardinality": 3},
            "ubicacion": {"type": "categorical", "role": "dimension", "cardinality": 12},
            "stock_disponible": {"type": "numeric", "role": "metric", "cardinality": 50},
        },
        dataset_contract={
            "dataset_mode": "snapshot",
            "time_axis": "fecha_de_stock",
            "date_columns": ["fecha_de_stock"],
            "metric_columns": ["stock_disponible"],
            "dimension_columns": ["ubicacion"],
            "identifier_columns": [],
            "snapshot_guard_allowed": True,
        },
    )

    assert plans
    filters = list(getattr(plans[0].main_intent, "filters", []) or [])
    assert filters
    assert filters[0].column == "fecha_de_stock"
    assert str(filters[0].value).lower() == "latest"


def test_phase8_semantic_translator_trend_fastpath_supports_top_n_rollup_sum() -> None:
    plans = SemanticTranslator._build_explicit_trend_plan(
        "grafico lineal de ventas por fecha, comparando top 5 productos con suma total",
        ["fecha", "producto", "ventas"],
        schema_profile={
            "fecha": {"type": "temporal", "role": "date", "cardinality": 18},
            "producto": {"type": "categorical", "role": "dimension", "cardinality": 120},
            "ventas": {"type": "numeric", "role": "metric", "cardinality": 400},
        },
    )

    assert plans
    intent = plans[0].main_intent
    assert getattr(intent, "type", None) == "trend"
    assert getattr(intent, "split_dimension", None) == "producto"
    assert getattr(intent, "split_limit", None) == 5
    assert getattr(intent, "top_n_aggregation_mode", None) == "sum"


def test_phase8_semantic_translator_exact_restrictive_top_n_prompt_resolves_to_single_rollup_series() -> None:
    plans = SemanticTranslator._build_explicit_trend_plan(
        (
            "Realiza un gráfico de la evolución mensual de las ventas totales de los 5 productos más vendidos. "
            "No me des la evolución de cada producto, dame la suma del total de los 5 productos más vendidos por cada mes."
        ),
        ["fecha_venta", "producto", "ingreso_total"],
        schema_profile={
            "fecha_venta": {"type": "temporal", "role": "date", "cardinality": 18},
            "producto": {"type": "categorical", "role": "dimension", "cardinality": 6},
            "ingreso_total": {"type": "numeric", "role": "metric", "cardinality": 1000},
        },
    )

    assert plans
    intent = plans[0].main_intent
    assert getattr(intent, "type", None) == "trend"
    assert getattr(intent, "date_column", None) == "fecha_venta"
    assert getattr(intent, "value_column", None) == "ingreso_total"
    assert getattr(intent, "split_dimension", None) == "producto"
    assert getattr(intent, "split_limit", None) == 5
    assert getattr(intent, "top_n_aggregation_mode", None) == "sum"


def test_phase8_semantic_translator_complexity_gate_rejects_distribution_for_temporal_top_n() -> None:
    bad_plan = AnalysisPlan.model_validate(
        {
            "main_intent": {
                "type": "distribution",
                "rationale": "Ranking estático",
                "filters": [],
                "metric_unit": "currency",
                "visual_protocol": "bar_chart",
                "dimension": "producto",
                "metric": "ingreso_total",
                "limit": 5,
            },
            "title": "Top 5 productos por ingresos",
            "column_aliases": {"producto": "Producto", "ingreso_total": "Ingresos"},
            "metric_polarity": "favorable",
        }
    )

    unresolved = SemanticTranslator._fast_path_unresolved_constraints(
        "Genera un gráfico con el total de ingresos mensual pero solo mostrando el TOP 5 de los productos mas vendidos.",
        [bad_plan],
    )

    assert "temporal_top_n_requires_trend" in unresolved


def test_phase8_semantic_translator_complexity_gate_rejects_split_when_user_requests_rollup() -> None:
    bad_plan = AnalysisPlan.model_validate(
        {
            "main_intent": {
                "type": "trend",
                "rationale": "Comparación de series",
                "filters": [],
                "metric_unit": "currency",
                "visual_protocol": "line_chart",
                "date_column": "fecha_venta",
                "value_column": "ingreso_total",
                "grain": "month",
                "fill_missing": True,
                "split_dimension": "producto",
                "split_limit": 5,
                "top_n_aggregation_mode": "split",
            },
            "title": "Evolución de ingresos por producto",
            "column_aliases": {
                "fecha_venta": "Fecha Venta",
                "producto": "Producto",
                "ingreso_total": "Ingreso Total",
            },
            "metric_polarity": "favorable",
        }
    )

    unresolved = SemanticTranslator._fast_path_unresolved_constraints(
        (
            "Realiza un gráfico de la evolución mensual de las ventas totales de los 5 productos más vendidos. "
            "No me des la evolución de cada producto, dame la suma del total de los 5 productos más vendidos por cada mes."
        ),
        [bad_plan],
    )

    assert "top_n_rollup_not_satisfied" in unresolved
    assert "negated_split_not_satisfied" in unresolved


def test_phase8_semantic_translator_rollup_post_processor_is_disconnected() -> None:
    base_plan = AnalysisPlan.model_validate(
        {
            "main_intent": {
                "type": "trend",
                "rationale": "Comparar top 5",
                "filters": [],
                "metric_unit": "number",
                "visual_protocol": "line_chart",
                "date_column": "fecha",
                "value_column": "ventas",
                "grain": "month",
                "fill_missing": True,
                "split_dimension": "producto",
                "split_limit": 5,
            },
            "title": "Evolución de ventas por producto (Top 5)",
            "column_aliases": {"fecha": "Fecha", "ventas": "Ventas", "producto": "Producto"},
            "metric_polarity": "neutral",
        }
    )

    hydrated = SemanticTranslator._apply_top_n_rollup_mode_to_plans(
        "quiero la suma total del top 5 productos por mes",
        [base_plan],
    )

    assert hydrated
    assert getattr(hydrated[0].main_intent, "top_n_aggregation_mode", None) == "split"


def test_phase8_semantic_translator_extracts_prompt_text_from_json_wrapper() -> None:
    normalized = SemanticTranslator._normalize_surface_text(
        '{"text":"Genera evolución mensual del top 5 productos","parent_id":"abc-123"}'
    )
    assert normalized == "genera evolucion mensual del top 5 productos"


def test_phase8_semantic_translator_trend_fastpath_monthly_top_n_from_prompt_without_fecha_token() -> None:
    plans = SemanticTranslator._build_explicit_trend_plan(
        '{"text":"Genera un gráfico con el total de ingresos mensual mostrando el top 5 de productos más vendidos"}',
        ["fecha_venta", "producto", "ingreso_total"],
        schema_profile={
            "fecha_venta": {"type": "temporal", "role": "date", "cardinality": 18},
            "producto": {"type": "categorical", "role": "dimension", "cardinality": 6},
            "ingreso_total": {"type": "numeric", "role": "metric", "cardinality": 1000},
        },
    )

    assert plans
    intent = plans[0].main_intent
    assert getattr(intent, "type", None) == "trend"
    assert getattr(intent, "date_column", None) == "fecha_venta"
    assert getattr(intent, "split_dimension", None) == "producto"
    assert getattr(intent, "split_limit", None) == 5


def test_phase8_semantic_router_forces_complex_on_low_confidence() -> None:
    decision = SemanticTranslator._normalize_semantic_router_decision(
        {
            "route": "SIMPLE",
            "confidence": 0.84,
            "detected_intent": "trend",
            "requires_time": True,
            "reason_codes": [],
        }
    )

    assert decision["route"] == "COMPLEJO"
    assert "low_confidence" in decision["reason_codes"]


def test_phase8_semantic_router_forces_complex_on_mixed_reason_codes() -> None:
    decision = SemanticTranslator._normalize_semantic_router_decision(
        {
            "route": "SIMPLE",
            "confidence": 0.99,
            "detected_intent": "trend",
            "requires_time": True,
            "reason_codes": ["per_item"],
        }
    )

    assert decision["route"] == "COMPLEJO"
    assert "conservative_policy" in decision["reason_codes"]


def test_phase8_semantic_router_simple_route_allows_temporal_fastpath_without_graph_keyword(monkeypatch) -> None:
    monkeypatch.setattr(
        SemanticTranslator,
        "_route_prompt_with_semantic_router",
        staticmethod(
            lambda *args, **kwargs: {
                "route": "SIMPLE",
                "confidence": 0.96,
            "detected_intent": "trend",
            "requires_time": True,
            "reason_codes": [],
            "semantic_contract": {
                "intent": "trend",
                "metric": "ingreso_total",
                "time_axis": "fecha_venta",
                "dimension": "producto",
                "top_n": 5,
                "series_mode": "split",
                "grain": "month",
                "aggregation": "sum",
                "visual_protocol": "line_chart",
                "requires_time": True,
            },
            "original_route": "SIMPLE",
        }
        ),
    )

    plans = SemanticTranslator.translate(
        "Realiza un análisis de la evolución mensual de los ingresos del TOP 5 productos más vendidos.",
        ["fecha_venta", "producto", "ingreso_total"],
        glossary_context="",
        topology_context="",
        schema_profile={
            "fecha_venta": {"type": "temporal", "role": "date", "cardinality": 18},
            "producto": {"type": "categorical", "role": "dimension", "cardinality": 6},
            "ingreso_total": {"type": "numeric", "role": "metric", "cardinality": 1000},
        },
    )

    assert plans
    intent = plans[0].main_intent
    assert getattr(intent, "type", None) == "trend"
    assert getattr(intent, "date_column", None) == "fecha_venta"
    assert getattr(intent, "split_dimension", None) == "producto"
    assert getattr(intent, "split_limit", None) == 5
    assert getattr(intent, "top_n_aggregation_mode", None) == "split"


def test_phase8_semantic_router_simple_route_prioritizes_temporal_total_products_prompt(monkeypatch) -> None:
    monkeypatch.setattr(
        SemanticTranslator,
        "_route_prompt_with_semantic_router",
        staticmethod(
            lambda *args, **kwargs: {
                "route": "SIMPLE",
                "confidence": 0.97,
            "detected_intent": "trend",
            "requires_time": True,
            "reason_codes": [],
            "semantic_contract": {
                "intent": "trend",
                "metric": "ingreso_total",
                "time_axis": "fecha_venta",
                "dimension": None,
                "top_n": None,
                "series_mode": "none",
                "grain": "month",
                "aggregation": "sum",
                "visual_protocol": "line_chart",
                "requires_time": True,
            },
            "original_route": "SIMPLE",
        }
        ),
    )

    plans = SemanticTranslator.translate(
        "Realiza un gráfico de la evolución mensual de las ventas totales del total de productos.",
        ["fecha_venta", "producto", "ingreso_total"],
        glossary_context="",
        topology_context="",
        schema_profile={
            "fecha_venta": {"type": "temporal", "role": "date", "cardinality": 18},
            "producto": {"type": "categorical", "role": "dimension", "cardinality": 6},
            "ingreso_total": {"type": "numeric", "role": "metric", "cardinality": 1000},
        },
    )

    assert plans
    intent = plans[0].main_intent
    assert getattr(intent, "type", None) == "trend"
    assert getattr(intent, "date_column", None) == "fecha_venta"
    assert not getattr(intent, "split_dimension", None)


def test_phase8_advanced_contract_preserves_exclusions_and_ranking_metric() -> None:
    plan = AnalysisPlan.model_validate(
        {
            "main_intent": {
                "type": "distribution",
                "rationale": "Top productos por unidades, mostrando ingresos y excluyendo categorías no deseadas.",
                "filters": [{"column": "region", "operator": "==", "value": "Norte"}],
                "negative_filters": [{"column": "categoria", "operator": "not_in", "value": ["Software", "Accesorios"]}],
                "metric_unit": "currency",
                "visual_protocol": "bar_chart",
                "dimension": "producto",
                "metric": "ingreso_total",
                "plot_metric": "ingreso_total",
                "ranking_metric": "cantidad",
                "ranking_direction": "desc",
                "limit": 3,
                "group_by": None,
                "barmode": "stacked",
            },
            "title": "Ingresos de productos Top 3 por cantidad",
            "column_aliases": {
                "producto": "Producto",
                "ingreso_total": "Ingreso Total",
                "cantidad": "Cantidad",
                "categoria": "Categoría",
            },
            "metric_polarity": "favorable",
        }
    )

    intent = plan.main_intent
    assert getattr(intent, "plot_metric", None) == "ingreso_total"
    assert getattr(intent, "ranking_metric", None) == "cantidad"
    assert getattr(intent, "negative_filters", [])[0].operator == "not_in"


def test_phase8_semantic_router_contract_normalizes_advanced_fields() -> None:
    decision = SemanticTranslator._normalize_semantic_router_decision(
        {
            "route": "COMPLEJO",
            "confidence": 0.95,
            "detected_intent": "distribution",
            "requires_time": False,
            "reason_codes": ["ranking_metric_mismatch", "exclusion_logic"],
            "semantic_contract": {
                "intent": "distribution",
                "plot_metric": "ingreso_total",
                "ranking_metric": "cantidad",
                "ranking_direction": "desc",
                "dimension": "producto",
                "top_n": 3,
                "negative_filters": [{"column": "categoria", "operator": "not_in", "value": ["Software"]}],
            },
        }
    )

    contract = decision["semantic_contract"]
    assert decision["route"] == "COMPLEJO"
    assert contract["plot_metric"] == "ingreso_total"
    assert contract["ranking_metric"] == "cantidad"
    assert contract["negative_filters"][0]["operator"] == "not_in"
