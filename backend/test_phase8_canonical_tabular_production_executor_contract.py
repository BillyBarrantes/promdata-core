from types import SimpleNamespace

import pandas as pd

from app.core.semantic_grammar import AnalysisPlan
from app.services import canonical_tabular_production_executor as production_executor
from app.services.canonical_shadow_query_runner import _blocked_plan_metrics


def test_production_executor_uses_semantic_translator_without_shadow_visual_bundle(monkeypatch):
    candidate_df = pd.DataFrame({"mes": ["Jan-2026"], "venta_total": [100]})
    candidate_df.attrs["schema_profile"] = {
        "mes": {"role": "date", "type": "date"},
        "venta_total": {"role": "metric", "type": "numeric"},
    }
    candidate_df.attrs["semantic_contract"] = {
        "dataset_mode": "flow",
        "time_axis": "mes",
        "metric_columns": ["venta_total"],
        "date_columns": ["mes"],
    }
    pipeline_result = SimpleNamespace(
        canonical_bundle_summary={"file_name": "ventas.xlsx"},
        metadata={"pipeline_status": "ready"},
        materialized_bundle_summary={},
        preview_runtime_summary={},
        analytical_adapter_summary={},
        runtime_comparison_summary={},
        analytical_adapter_runtime=SimpleNamespace(
            analytical_bundle=SimpleNamespace(selected_candidate_id="primary__sheet1")
        ),
    )
    plan = SimpleNamespace(
        title="Evolución mensual",
        main_intent=SimpleNamespace(type="trend", value_column="venta_total", visual_protocol="line_chart"),
        column_aliases={},
        glossary_hint=None,
    )
    captured = {}

    def _fake_translate(*args, **_kwargs):
        captured["translator_args"] = args
        return [plan]

    monkeypatch.setattr(production_executor, "get_selected_candidate_dataframe", lambda *_: candidate_df)
    monkeypatch.setattr(production_executor.SemanticTranslator, "translate", _fake_translate)
    monkeypatch.setattr(production_executor, "_persist_shadow_candidate", lambda *_args, **_kwargs: ("production_file", "/tmp/production.parquet"))
    monkeypatch.setattr(production_executor, "_blocked_plan_metrics", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        production_executor,
        "_get_ibis_engine_cls",
        lambda: SimpleNamespace(
            execute_plan=lambda *_args, **_kwargs: {
                "type": "echarts",
                "chart_type": "line",
                "title": "Evolución mensual",
                "data": [{"name": "Jan-2026", "value": 100}],
            }
        ),
    )

    execution = production_executor.build_canonical_tabular_production_execution(
        file_id="file-1",
        pipeline_result=pipeline_result,
        prompt="Realiza un gráfico de evolución mensual",
        max_plans=3,
    )

    assert captured["translator_args"][0] == "Realiza un gráfico de evolución mensual"
    assert execution.prompt_strategy == "production_semantic_translator"
    assert execution.metadata["production_query_status"] == "query_executed"
    assert execution.execution_summaries[0]["status"] == "success"


def test_production_analysis_marks_final_struct_as_production(monkeypatch):
    execution = SimpleNamespace(
        execution_summaries=[{"status": "success"}],
        metadata={"production_query_status": "query_executed"},
        prompt_strategy="production_semantic_translator",
    )

    monkeypatch.setattr(
        production_executor,
        "run_canonical_dark_pipeline_for_uploaded_file",
        lambda **_: SimpleNamespace(),
    )
    monkeypatch.setattr(
        production_executor,
        "build_canonical_tabular_production_execution",
        lambda **_: execution,
    )
    monkeypatch.setattr(
        production_executor,
        "_build_final_struct",
        lambda _execution: (
            {"traceability": {"runtime": "canonical_tabular_canary"}, "chart_options": [{}]},
            {"dataset_mode": "flow"},
            [],
        ),
    )

    result = production_executor.execute_canonical_tabular_production_analysis(
        file_id="file-1",
        prompt="Analiza ventas",
        service_client=object(),
    )

    assert result.status == "completed"
    assert result.final_struct["traceability"]["runtime"] == "canonical_tabular_production"
    assert result.final_struct["traceability"]["prompt_strategy"] == "production_semantic_translator"


def test_production_metric_guard_allows_numeric_contract_ranking_metric() -> None:
    candidate_df = pd.DataFrame(
        {
            "producto": ["A", "B"],
            "ingreso_total": [1000.0, 100.0],
            "cantidad": [2, 50],
        }
    )
    candidate_df.attrs["schema_profile"] = {
        "producto": {"role": "dimension", "type": "categorical"},
        "ingreso_total": {"role": "metric", "type": "numeric"},
        "cantidad": {"role": "dimension", "type": "numeric"},
    }
    candidate_df.attrs["shadow_metric_gate"] = {
        "safe_metric_columns": ["ingreso_total"],
        "blocked_metric_columns": ["cantidad"],
    }
    plan = AnalysisPlan.model_validate(
        {
            "main_intent": {
                "type": "distribution",
                "rationale": "Mostrar ingreso total, rankear por cantidad.",
                "filters": [],
                "metric_unit": "currency",
                "visual_protocol": "bar_chart",
                "dimension": "producto",
                "metric": "ingreso_total",
                "plot_metric": "ingreso_total",
                "ranking_metric": "cantidad",
                "ranking_direction": "desc",
                "limit": 3,
            },
            "title": "Ingresos Top 3 por cantidad",
            "column_aliases": {"producto": "Producto", "ingreso_total": "Ingreso Total", "cantidad": "Cantidad"},
            "metric_polarity": "favorable",
        }
    )

    assert _blocked_plan_metrics(plan, candidate_df) == []


def test_production_metric_guard_blocks_text_contract_ranking_metric() -> None:
    candidate_df = pd.DataFrame(
        {
            "producto": ["A", "B"],
            "ingreso_total": [1000.0, 100.0],
            "cantidad": ["alta", "baja"],
        }
    )
    candidate_df.attrs["schema_profile"] = {
        "producto": {"role": "dimension", "type": "categorical"},
        "ingreso_total": {"role": "metric", "type": "numeric"},
        "cantidad": {"role": "dimension", "type": "categorical"},
    }
    candidate_df.attrs["shadow_metric_gate"] = {
        "safe_metric_columns": ["ingreso_total"],
        "blocked_metric_columns": ["cantidad"],
    }
    plan = AnalysisPlan.model_validate(
        {
            "main_intent": {
                "type": "distribution",
                "rationale": "Intento inválido de rankear por texto.",
                "filters": [],
                "metric_unit": "currency",
                "visual_protocol": "bar_chart",
                "dimension": "producto",
                "metric": "ingreso_total",
                "plot_metric": "ingreso_total",
                "ranking_metric": "cantidad",
                "ranking_direction": "desc",
                "limit": 3,
            },
            "title": "Ingresos Top 3 por texto",
            "column_aliases": {"producto": "Producto", "ingreso_total": "Ingreso Total", "cantidad": "Cantidad"},
            "metric_polarity": "favorable",
        }
    )

    assert _blocked_plan_metrics(plan, candidate_df) == ["cantidad"]
