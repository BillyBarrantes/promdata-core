from app.core.semantic_grammar import AnalysisPlan
from app.services.analysis_memory_context import (
    apply_parent_context_to_placeholder_filters,
    build_parent_memory_context_text,
)


def test_parent_context_replaces_placeholder_filter_with_structured_filter() -> None:
    plan = AnalysisPlan.model_validate(
        {
            "main_intent": {
                "type": "trend",
                "rationale": "Continuar el analisis anterior sobre el mismo subconjunto.",
                "filters": [
                    {
                        "column": "entity_id",
                        "operator": "in",
                        "value": ["context_inherited"],
                    }
                ],
                "metric_unit": "number",
                "visual_protocol": "line_chart",
                "date_column": "event_date",
                "value_column": "amount",
                "grain": "month",
                "fill_missing": True,
                "split_dimension": "entity_id",
                "split_limit": 2,
                "top_n_aggregation_mode": "split",
            },
            "title": "Evolucion por entidad",
            "column_aliases": {},
            "metric_polarity": "neutral",
        }
    )
    parent_context = {
        "filters": [
            {
                "column": "entity_id",
                "operator": "in",
                "value": ["A-001", "A-002"],
            }
        ]
    }

    [hydrated_plan] = apply_parent_context_to_placeholder_filters(
        plans=[plan],
        parent_context=parent_context,
    )

    intent = hydrated_plan.main_intent
    assert len(intent.filters) == 1
    assert intent.filters[0].column == "entity_id"
    assert intent.filters[0].value == ["A-001", "A-002"]


def test_parent_memory_context_text_exposes_structured_context_without_forcing_inheritance() -> None:
    memory_text = build_parent_memory_context_text(
        {
            "parent_task_id": "task-1",
            "parent_prompt": "analiza un subconjunto",
            "filters": [
                {
                    "column": "entity_id",
                    "operator": "in",
                    "value": ["A-001", "A-002"],
                }
            ],
            "semantic_context": {},
        }
    )

    assert "CONTEXTO_ANALITICO_PREVIO_DISPONIBLE_JSON" in memory_text
    assert "entity_id" in memory_text
    assert "A-001" in memory_text
    assert "usa este contexto solo si el nuevo pedido se refiere" in memory_text
