from types import SimpleNamespace

import pandas as pd

from app.services.canonical_shadow_query_runner import CanonicalShadowQueryExecution
from app.services.canonical_tabular_canary_executor import (
    execute_canonical_tabular_canary_analysis,
)


def test_canary_executor_builds_final_struct_from_shadow_execution(monkeypatch):
    candidate_df = pd.DataFrame({"canal": ["Retail", "Online"], "venta_total": [120, 80]})
    candidate_df.attrs["semantic_contract"] = {
        "dataset_mode": "flow",
        "metric_columns": ["venta_total"],
        "dimension_columns": ["canal"],
    }
    candidate_df.attrs["currency_meta"] = {"symbol": "$"}
    candidate_df.attrs["cleaning_notes"] = ["normalized_headers"]

    plan = SimpleNamespace(
        title="Ventas por Canal",
        main_intent=SimpleNamespace(type="distribution", visual_protocol="bar_chart"),
    )
    execution = CanonicalShadowQueryExecution(
        pipeline_result=SimpleNamespace(analytical_adapter_runtime=SimpleNamespace()),
        readiness_summary={"readiness_grade": "pilot_candidate"},
        query_prompt="Analiza ventas por canal",
        prompt_strategy="shadow_dimension_visual_parity_bundle",
        plans=[plan],
        plan_summaries=[{"title": "Ventas por Canal"}],
        execution_summaries=[{"status": "success", "chart_type": "bar"}],
        execution_results=[
            {
                "type": "echarts",
                "chart_type": "bar",
                "title": "Ventas por Canal",
                "data": [
                    {"name": "Retail", "value": 120},
                    {"name": "Online", "value": 80},
                ],
                "x_axis": "canal",
                "y_axis": "venta_total",
                "hard_facts": {"top_1_name": "Retail", "top_1_val": 120, "total_analyzed": 200},
            }
        ],
        metadata={
            "file_id": "file-1",
            "candidate_id": "primary__ventas",
            "shadow_query_status": "query_executed",
        },
    )

    monkeypatch.setattr(
        "app.services.canonical_tabular_canary_executor.run_canonical_shadow_query_for_uploaded_file",
        lambda **_: execution,
    )
    monkeypatch.setattr(
        "app.services.canonical_tabular_canary_executor.get_selected_candidate_dataframe",
        lambda *_: candidate_df,
    )
    monkeypatch.setattr(
        "app.services.canonical_tabular_canary_executor.generate_dashboard_executive_summary",
        lambda **_: {
            "headline": "Resumen ejecutivo de ventas",
            "overview": "El canal Retail concentra la mayor parte del valor observado.",
            "key_findings": ["Retail lidera el valor total analizado."],
            "risks": [],
            "actions": ["Profundizar en la brecha entre Retail y Online."],
            "caveats": [],
        },
    )

    result = execute_canonical_tabular_canary_analysis(
        file_id="file-1",
        prompt="Analiza ventas por canal",
        service_client=object(),
    )

    assert result.status == "completed"
    assert result.dataset_contract["dataset_mode"] == "flow"
    assert result.final_struct["chart_options"]
    assert "Resumen ejecutivo de ventas" in result.final_struct["analysis"]
    assert "Acciones sugeridas" in result.final_struct["analysis"]
    assert "recommendations" not in result.final_struct
    assert result.final_struct["chart_options"][0]["visual_source_payload"]["rows"]
    assert result.final_struct["chart_options"][0]["visual_governance"]["catalog"]
    assert result.final_struct["arrow_row_count"] == 2
    assert result.final_struct["snapshot_row_count"] == 2
    assert result.final_struct["snapshot_columns"] == ["canal", "venta_total"]
    assert result.final_struct["traceability"]["runtime"] == "canonical_tabular_canary"
