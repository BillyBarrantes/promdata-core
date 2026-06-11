from __future__ import annotations

from dataclasses import dataclass, field

from app.services.analysis_traceability import (
    build_traceability_payload,
    build_traceability_plan_entry,
    summarize_history_item,
)


@dataclass
class DummyFilter:
    column: str
    operator: str
    value: str


@dataclass
class DummyIntent:
    type: str = "distribution"
    visual_protocol: str = "bar_chart"
    aggregation: str = "sum"
    metric: str = "stock_disponible"
    value_column: str | None = "stock_disponible"
    metrics: list[str] = field(default_factory=lambda: ["stock_disponible"])
    dimension: str = "tipo_almacen"
    date_column: str | None = None
    group_by: list[str] = field(default_factory=lambda: ["tipo_almacen"])
    filters: list[DummyFilter] = field(default_factory=lambda: [DummyFilter("tipo_almacen", "=", "130")])
    rationale: str = "Comparar volumen por almacen"
    limit: int = 10
    barmode: str | None = None
    metric_unit: str = "quantity"


@dataclass
class DummyPlan:
    title: str = "Top almacenes"
    main_intent: DummyIntent = field(default_factory=DummyIntent)
    column_aliases: dict[str, str] = field(default_factory=lambda: {
        "stock_disponible": "Stock disponible",
        "tipo_almacen": "Tipo almacen",
    })
    metric_polarity: str = "neutral"


@dataclass
class DummySnippet:
    document_id: str
    document_title: str
    document_file_name: str
    chunk_index: int
    content: str = ""
    similarity: float | None = 0.91
    source_kind: str = "knowledge_document"
    metadata: dict = field(default_factory=dict)


def main() -> None:
    plan = DummyPlan()
    schema_profile = {
        "stock_disponible": {"role": "metric", "type": "numeric"},
        "tipo_almacen": {"role": "dimension", "type": "categorical"},
    }
    plan_entry = build_traceability_plan_entry(
        plan=plan,
        schema_profile=schema_profile,
        query_contract={"metric": "stock_disponible", "dimension": "tipo_almacen"},
        execution={"status": "success", "output_type": "echarts", "applied_visual": "bar_chart"},
    )

    assert plan_entry["intent_type"] == "distribution"
    assert plan_entry["metric_roles"]["stock_disponible"] == "metric"
    assert plan_entry["filters"][0]["display"] == "Tipo almacen = 130"

    payload = build_traceability_payload(
        task_id="task-1",
        file_id="file-1",
        user_id="00000000-0000-4000-8000-000000000001",
        raw_prompt='{"text":"top almacenes","parent_id":"task-0"}',
        actual_prompt="top almacenes",
        parent_task_id="task-0",
        memory_decision="keep",
        format_override={"enabled": False},
        schema_profile=schema_profile,
        currency_meta={},
        institutional_snippets=[
            DummySnippet(
                document_id="doc-1",
                document_title="Politica de stock",
                document_file_name="politica.pdf",
                chunk_index=0,
            )
        ],
        plan_entries=[plan_entry],
        final_struct={
            "analysis": "ok",
            "metrics": {"stock": 100},
            "chart_options": [{"title": {"text": "Top almacenes"}}],
            "data": [],
            "recommendations": ["accion"],
            "explainability": [{"title": "Top almacenes"}],
            "snapshot_row_count": 18193,
        },
        status="completed",
        error_message=None,
    )

    assert payload["documents"]["source_count"] == 1
    assert payload["outputs"]["chart_count"] == 1
    assert payload["plans"][0]["execution"]["status"] == "success"

    history_item = summarize_history_item(
        task_row={
            "id": "task-1",
            "file_id": "file-1",
            "status": "completed",
            "created_at": "2026-04-25T00:00:00Z",
            "prompt": "top almacenes",
        },
        result_payload={"traceability": payload},
    )

    assert history_item["traceability_available"] is True
    assert history_item["plan_count"] == 1
    assert history_item["source_count"] == 1
    assert history_item["chart_count"] == 1
    print("ok")


if __name__ == "__main__":
    main()
