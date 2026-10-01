from types import SimpleNamespace

import pandas as pd
import pytest

from app.services.canonical_shadow_query_runner import CanonicalShadowQueryExecution
from app.services.canonical_tabular_canary_executor import (
    _build_final_struct,
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


class TestEmptyResultHandling:
    def _candidate_df(self):
        df = pd.DataFrame({"canal": ["A", "B"], "venta": [100, 200]})
        df.attrs["semantic_contract"] = {"dataset_mode": "flow"}
        return df

    def _make_execution(self, execution_results, plans=None, shadow_status="query_executed"):
        if plans is None:
            plan = SimpleNamespace(
                title="Unidades con Multas",
                main_intent=SimpleNamespace(type="distribution", visual_protocol="bar_chart"),
            )
            plans = [plan]
        return CanonicalShadowQueryExecution(
            pipeline_result=SimpleNamespace(
                analytical_adapter_runtime=SimpleNamespace()
            ),
            readiness_summary={"readiness_grade": "pilot_candidate"},
            query_prompt="test",
            prompt_strategy="production_semantic_translator",
            plans=plans,
            plan_summaries=[{"title": p.title} for p in plans],
            execution_summaries=[{
                "status": (
                    "success"
                    if not r.get("error") or r.get("error") == "empty_result"
                    else "error"
                ),
            } for r in execution_results],
            execution_results=execution_results,
            metadata={
                "file_id": "file-1",
                "candidate_id": "primary__sheet::2021",
                "shadow_query_status": shadow_status,
            },
        )

    def test_empty_result_direct_struct(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.canonical_tabular_canary_executor.get_selected_candidate_dataframe",
            lambda *_: self._candidate_df(),
        )
        execution = self._make_execution([
            {"error": "empty_result", "message": "No hay datos", "title": "Test"}
        ])
        final_struct, _, _ = _build_final_struct(execution)
        assert final_struct["chart_options"]
        assert final_struct["chart_options"][0]["type"] == "empty_result"
        assert "No hay datos" in final_struct["chart_options"][0]["description"]
        assert "primary_plan_failed" not in final_struct.get("traceability", {})

    def test_empty_result_preserves_filters_applied(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.canonical_tabular_canary_executor.get_selected_candidate_dataframe",
            lambda *_: self._candidate_df(),
        )
        execution = self._make_execution([
            {
                "error": "empty_result",
                "message": "No data with filters",
                "filters_applied": ["destino = Barranca", "tipo_unidad = pesado"],
            }
        ])
        final_struct, _, _ = _build_final_struct(execution)
        assert final_struct["chart_options"]
        opt = final_struct["chart_options"][0]
        assert len(opt["filters_applied"]) == 2
        assert "destino = Barranca" in str(opt["filters_applied"])

    def test_real_error_raises_runtime_error(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.canonical_tabular_canary_executor.get_selected_candidate_dataframe",
            lambda *_: self._candidate_df(),
        )
        execution = self._make_execution(
            [{"error": "column_not_found", "message": "no existe"}],
            shadow_status="query_failed",
        )
        with pytest.raises(RuntimeError, match="canonical_canary"):
            _build_final_struct(execution)

    def test_real_error_mixed_with_empty_result(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.canonical_tabular_canary_executor.get_selected_candidate_dataframe",
            lambda *_: self._candidate_df(),
        )
        plan1 = SimpleNamespace(
            title="Plan 1",
            main_intent=SimpleNamespace(type="distribution", visual_protocol="bar_chart"),
        )
        plan2 = SimpleNamespace(
            title="Plan 2",
            main_intent=SimpleNamespace(type="distribution", visual_protocol="bar_chart"),
        )
        execution = self._make_execution(
            [
                {
                    "type": "echarts", "chart_type": "bar",
                    "title": "Plan con datos",
                    "data": [{"name": "A", "value": 100}],
                    "hard_facts": {"top_1_name": "A", "top_1_val": 100, "total_analyzed": 100},
                },
                {"error": "empty_result", "message": "Sin datos", "title": "Plan 2"},
            ],
            plans=[plan1, plan2],
        )
        final_struct, _, _ = _build_final_struct(execution)
        assert len(final_struct["chart_options"]) == 2
        has_empty = any(o.get("type") == "empty_result" for o in final_struct["chart_options"])
        has_echarts = any(o.get("type") != "empty_result" for o in final_struct["chart_options"])
        assert has_empty, f"Expected empty_result chart, got: {[o.get('type') for o in final_struct['chart_options']]}"
        assert has_echarts, f"Expected real data chart"

    def test_empty_result_all_plans_e2e(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.canonical_tabular_canary_executor.get_selected_candidate_dataframe",
            lambda *_: self._candidate_df(),
        )
        monkeypatch.setattr(
            "app.services.canonical_tabular_canary_executor.generate_dashboard_executive_summary",
            lambda **_: {"headline": "OK", "overview": "ok", "key_findings": [],
                          "risks": [], "actions": [], "caveats": []},
        )
        execution = self._make_execution([
            {"error": "empty_result", "message": "No se encontraron datos", "title": "Plan 1"},
            {"error": "empty_result", "message": "Sin resultados", "title": "Plan 2"},
            {"error": "empty_result", "message": "Filtros sin coincidencias", "title": "Plan 3"},
        ])
        monkeypatch.setattr(
            "app.services.canonical_tabular_canary_executor.run_canonical_shadow_query_for_uploaded_file",
            lambda **_: execution,
        )
        result = execute_canonical_tabular_canary_analysis(
            file_id="file-1", prompt="test", service_client=object(),
        )
        assert result.status == "completed"
        chart_options = result.final_struct["chart_options"]
        assert chart_options
        assert all(o["type"] == "empty_result" for o in chart_options)
        assert any("No se encontraron datos" in str(o.get("description", "")) for o in chart_options)
        assert "primary_plan_failed" not in result.final_struct.get("traceability", {})
