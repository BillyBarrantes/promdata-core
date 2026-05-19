from app.core.semantic_grammar import AnalysisPlan
from app.services.visual_recommendation_engine import (
    build_visual_governance,
    extract_prompt_visual_requests,
)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _build_temporal_plan() -> AnalysisPlan:
    return AnalysisPlan.model_validate(
        {
            "title": "Tendencia mensual de ventas",
            "column_aliases": {
                "fecha": "Fecha",
                "ventas": "Ventas",
            },
            "main_intent": {
                "type": "trend",
                "rationale": "Serie temporal para evaluar evolucion de ventas.",
                "date_column": "fecha",
                "value_column": "ventas",
                "metric": "ventas",
                "metric_unit": "currency",
                "visual_protocol": "pie_chart",
            },
        }
    )


def _build_distribution_plan() -> AnalysisPlan:
    return AnalysisPlan.model_validate(
        {
            "title": "Participacion por categoria",
            "column_aliases": {
                "categoria": "Categoria",
                "participacion": "Participacion",
            },
            "main_intent": {
                "type": "distribution",
                "rationale": "Lectura de composicion para entender participacion por categoria.",
                "metrics": ["participacion"],
                "group_by": ["categoria"],
                "dimension": "categoria",
                "metric_unit": "percentage",
                "visual_protocol": "bar_chart",
                "metric": "participacion",
            },
        }
    )


def _build_predictive_plan() -> AnalysisPlan:
    return AnalysisPlan.model_validate(
        {
            "title": "Forecast operativo",
            "column_aliases": {
                "periodo": "Periodo",
                "stock": "Stock",
                "variacion": "Variacion",
            },
            "main_intent": {
                "type": "predictive",
                "rationale": "Proyeccion temporal con una metrica principal y una senal secundaria.",
                "date_column": "periodo",
                "metric": "stock",
                "metrics": ["stock", "variacion"],
                "metric_unit": "quantity",
                "value_column": "stock",
                "visual_protocol": "bar_chart",
            },
        }
    )


def _build_diagnostic_plan() -> AnalysisPlan:
    return AnalysisPlan.model_validate(
        {
            "title": "Relacion entre variables operativas",
            "column_aliases": {
                "rotacion": "Rotacion",
                "margen": "Margen",
                "impacto": "Impacto",
            },
            "main_intent": {
                "type": "diagnostic",
                "rationale": "Relacion entre variables con intensidad adicional.",
                "metric": "margen",
                "metrics": ["rotacion", "margen", "impacto"],
                "metric_unit": "quantity",
                "visual_protocol": "scatter_plot",
            },
        }
    )


def run() -> None:
    temporal_governance = build_visual_governance(
        _build_temporal_plan(),
        {
            "data": [
                {"name": "2026-01", "value": 1200},
                {"name": "2026-02", "value": 1350},
                {"name": "2026-03", "value": 1420},
            ],
        },
        "pie_chart",
    )
    _assert(temporal_governance["requested_visual"] == "pie_chart", "Debe preservar el visual pedido")
    _assert(temporal_governance["recommended_visual"] == "line_chart", "Series temporales deben recomendar Line")
    _assert(temporal_governance["applied_visual"] == "line_chart", "Pie temporal invalido debe reemplazarse por Line")
    _assert(bool(temporal_governance["blocked_reason"]), "Debe explicar por que el visual fue bloqueado")
    _assert(isinstance(temporal_governance.get("catalog"), list), "Debe exponer catalogo visual")
    _assert(
        any(item["id"] == "line_chart" and item["enabled"] for item in temporal_governance["catalog"]),
        "Line debe quedar habilitado en series temporales",
    )
    _assert(
        any(item["id"] == "pie_chart" and not item["enabled"] for item in temporal_governance["catalog"]),
        "Pie debe quedar bloqueado cuando el dataset es temporal",
    )

    distribution_governance = build_visual_governance(
        _build_distribution_plan(),
        {
            "data": [
                {"name": "A", "value": 44},
                {"name": "B", "value": 28},
                {"name": "C", "value": 18},
                {"name": "D", "value": 10},
            ],
        },
        "gauge_chart",
    )
    _assert(distribution_governance["requested_visual"] == "gauge_chart", "Debe aceptar el visual solicitado")
    _assert(distribution_governance["applied_visual"] == "pie_chart", "Gauge con varias categorias debe bloquearse")
    _assert(
        distribution_governance["recommended_visual"] == "pie_chart",
        "Composicion de pocas categorias debe recomendar Donut",
    )
    _assert(
        "pie_chart" in distribution_governance["allowed_replacements"],
        "Debe exponer alternativas compatibles",
    )
    _assert(
        any(item["id"] == "gauge_chart" and not item["enabled"] for item in distribution_governance["catalog"]),
        "Gauge debe quedar visible pero bloqueado cuando no aplica",
    )

    predictive_governance = build_visual_governance(
        _build_predictive_plan(),
        {
            "data": [
                {"name": "2026-01", "value": 100, "extra_info": {"secondary_value": 5.1}},
                {"name": "2026-02", "value": 115, "extra_info": {"secondary_value": 6.4}},
                {"name": "2026-03", "value": 130, "extra_info": {"secondary_value": 7.2}},
            ],
        },
        "bar_chart",
    )
    _assert(
        predictive_governance["recommended_visual"] == "dual_axis_chart",
        "Predictive temporal con metrica secundaria debe recomendar Dual Axis",
    )
    _assert(
        predictive_governance["applied_visual"] == "bar_chart",
        "Si el visual pedido es valido debe respetarse aunque no sea el recomendado",
    )
    _assert(
        bool(predictive_governance["advisory_reason"]),
        "Cuando se respeta un visual valido distinto al recomendado debe existir aviso tecnico",
    )
    _assert(
        any(item["id"] == "combo_chart" and item["enabled"] for item in predictive_governance["catalog"]),
        "Combo debe quedar habilitado cuando existe tiempo y metrica secundaria",
    )

    dense_governance = build_visual_governance(
        _build_distribution_plan(),
        {
            "data": [{"name": f"Categoria {index}", "value": index} for index in range(1, 31)],
        },
        "bar_chart",
    )
    _assert(dense_governance["recommended_visual"] == "smart_table", "Alta densidad debe priorizar Smart Table")
    _assert(
        any(item["id"] == "smart_table" and item["recommended"] for item in dense_governance["catalog"]),
        "Smart Table debe marcarse como recomendada en alta densidad",
    )

    multiseries_governance = build_visual_governance(
        _build_distribution_plan(),
        {
            "data": [
                {"name": "Lima", "ventas": 120, "margen": 24},
                {"name": "Norte", "ventas": 95, "margen": 19},
                {"name": "Sur", "ventas": 88, "margen": 17},
            ],
        },
        "stacked_bar_chart",
    )
    _assert(
        multiseries_governance["applied_visual"] == "stacked_bar_chart",
        "Stacked Bar debe respetarse cuando el dataset trae multiples series por categoria",
    )
    _assert(
        any(item["id"] == "stacked_bar_chart" and item["enabled"] for item in multiseries_governance["catalog"]),
        "Stacked Bar debe figurar como opcion habilitada si el dataset soporta multi-serie",
    )

    bubble_governance = build_visual_governance(
        _build_diagnostic_plan(),
        {
            "data": [
                {"name": "A", "rotacion": 12, "margen": 22, "impacto": 35},
                {"name": "B", "rotacion": 15, "margen": 18, "impacto": 42},
                {"name": "C", "rotacion": 10, "margen": 27, "impacto": 28},
            ],
        },
        "bubble_chart",
    )
    _assert(
        bubble_governance["applied_visual"] == "bubble_chart",
        "Bubble debe respetarse cuando existe tercera magnitud en un analisis diagnostico",
    )
    _assert(
        any(item["id"] == "bubble_chart" and item["enabled"] for item in bubble_governance["catalog"]),
        "Bubble debe quedar habilitado cuando el dataset soporta tamano por punto",
    )

    locked_visuals = extract_prompt_visual_requests(
        "Quiero un grafico de dispersion y luego un heatmap para este analisis."
    )
    _assert(
        locked_visuals == ["scatter_plot", "heatmap_chart"],
        "Debe detectar y ordenar los visuales explicitamente pedidos por el usuario",
    )

    locked_temporal_governance = build_visual_governance(
        _build_temporal_plan(),
        {
            "data": [
                {"name": "2026-01", "value": 1200},
                {"name": "2026-02", "value": 1350},
                {"name": "2026-03", "value": 1420},
            ],
        },
        "pie_chart",
        requested_visual_locked=True,
    )
    _assert(
        locked_temporal_governance["strict_rejection"] is True,
        "Si el usuario bloqueo el visual pedido y no aplica, debe activarse rechazo estricto",
    )
    _assert(
        locked_temporal_governance["applied_visual"] == "pie_chart",
        "El contrato debe preservar el visual pedido como referencia cuando se rechaza en modo estricto",
    )
    _assert(
        locked_temporal_governance["fallback_visual"] is None,
        "En rechazo estricto no debe degradar silenciosamente a otro visual",
    )

    print("OK: phase2 visual governance contract")


if __name__ == "__main__":
    run()
