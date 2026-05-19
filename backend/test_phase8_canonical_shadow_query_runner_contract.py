import pandas as pd
import pytest

from app.core.semantic_grammar import AnalysisPlan
from app.services.canonical_dark_runtime_orchestrator import CanonicalDarkRuntimePipelineResult
from app.services.canonical_shadow_query_runner import (
    _literal_filters,
    build_canonical_shadow_query_execution,
    summarize_canonical_shadow_query_execution,
)


@pytest.fixture(autouse=True)
def _disable_production_executor_flag_for_shadow_contracts(monkeypatch):
    monkeypatch.setattr(
        "app.services.canonical_shadow_query_runner.settings.UNIVERSAL_TABULAR_PRODUCTION_EXECUTOR_ENABLED",
        False,
    )


class _FakeBundle:
    def __init__(self) -> None:
        self.source_manifest = None


class _FakeMaterializedBundle:
    pass


class _FakePreviewRuntime:
    pass


class _FakeAnalyticalBundle:
    def __init__(self, selected_candidate_id: str) -> None:
        self.selected_candidate_id = selected_candidate_id
        self.candidates = []
        self.metadata = {}


class _FakeAnalyticalRuntime:
    def __init__(self, selected_candidate_id: str, dataframe: pd.DataFrame) -> None:
        self.analytical_bundle = _FakeAnalyticalBundle(selected_candidate_id)
        self.candidate_dataframes = {selected_candidate_id: dataframe}
        self.metadata = {"selected_candidate_id": selected_candidate_id, "candidate_count": 1}


class _FakeIbisEngine:
    @staticmethod
    def execute_plan(parquet_path, plan, protected_cols=None, recipe_mode=True):
        return {
            "type": "echarts",
            "chart_type": "bar_chart",
            "data": [{"name": "North", "value": 100.0}],
            "title": plan.title,
        }


class _EchoVisualIbisEngine:
    @staticmethod
    def execute_plan(parquet_path, plan, protected_cols=None, recipe_mode=True):
        visual_protocol = getattr(getattr(plan, "main_intent", None), "visual_protocol", None)
        chart_type = str(getattr(visual_protocol, "value", visual_protocol or "bar_chart"))
        return {
            "type": "echarts",
            "chart_type": chart_type,
            "data": [{"name": plan.title, "value": 1.0}],
            "title": plan.title,
        }


def _build_fake_pipeline(dataframe: pd.DataFrame) -> CanonicalDarkRuntimePipelineResult:
    selected_candidate_id = "primary__sheet::sales"
    return CanonicalDarkRuntimePipelineResult(
        canonical_bundle=_FakeBundle(),
        canonical_bundle_summary={
            "file_name": "ventas.xlsx",
            "source_kind": "spreadsheet",
            "support_level": "full_analytics",
            "preferred_mode": "analytical",
            "analytics_ready": True,
            "quality_gate_applied": False,
            "tabular_frame_count": 1,
        },
        materialized_bundle=_FakeMaterializedBundle(),
        materialized_bundle_summary={"preview_ready_tables": 1},
        preview_runtime=_FakePreviewRuntime(),
        preview_runtime_summary={
            "tables": [
                {
                    "table_name": selected_candidate_id,
                    "row_count": len(dataframe.index),
                    "column_count": len(dataframe.columns),
                }
            ]
        },
        analytical_adapter_runtime=_FakeAnalyticalRuntime(selected_candidate_id, dataframe),
        analytical_adapter_summary={
            "selected_candidate_id": selected_candidate_id,
            "candidate_count": 1,
            "candidates": [
                {
                    "candidate_id": selected_candidate_id,
                    "metric_count": 1,
                    "dimension_count": 1,
                }
            ],
        },
        runtime_comparison_summary={"comparison_grade": "no_active_runtime"},
        metadata={"file_id": "file-1", "pipeline_status": "ready_for_shadow_compare"},
    )


def test_phase8_shadow_literal_regex_disabled_when_production_executor_is_enabled(monkeypatch) -> None:
    dataframe = pd.DataFrame({"region": ["Este", "Norte"], "amount": [10, 20]})
    dataframe.attrs["literal_filter_catalog"] = {"region": ["Este", "Norte"]}

    monkeypatch.setattr(
        "app.services.canonical_shadow_query_runner.settings.UNIVERSAL_TABULAR_PRODUCTION_EXECUTOR_ENABLED",
        True,
    )

    assert _literal_filters(dataframe, "este análisis debe agrupar por región") == []


def test_phase8_shadow_query_runner_executes_macro_bundle(monkeypatch) -> None:
    dataframe = pd.DataFrame(
        [
            {"fecha": "2026-01-01", "region": "North", "amount": 100},
            {"fecha": "2026-01-02", "region": "South", "amount": 120},
        ]
    )
    dataframe.attrs["schema_profile"] = {
        "fecha": {"type": "temporal", "role": "date"},
        "region": {"type": "categorical", "role": "dimension"},
        "amount": {"type": "numeric", "role": "metric"},
    }
    dataframe.attrs["semantic_contract"] = {
        "dataset_mode": "flow",
        "time_axis": "fecha",
        "date_columns": ["fecha"],
        "metric_columns": ["amount"],
        "dimension_columns": ["region"],
        "identifier_columns": [],
    }
    dataframe.attrs["topology_rules"] = {"fecha": "temporal", "region": "categorical", "amount": "metric"}
    dataframe.attrs["literal_filter_catalog"] = {"region": ["North", "South"]}
    dataframe.attrs["translator_context_summary"] = "SCHEMA (Semantic Tags): amount(metric), region(dimension), fecha(date)"
    dataframe.attrs["reference_date"] = "2026-01-02"

    pipeline = _build_fake_pipeline(dataframe)

    monkeypatch.setattr(
        "app.services.canonical_shadow_query_runner.DataEngine.commit_to_parquet",
        staticmethod(lambda _df, _file_id: "/tmp/shadow-query.parquet"),
    )
    monkeypatch.setattr(
        "app.services.canonical_shadow_query_runner.SemanticTranslator.translate",
        staticmethod(
            lambda *args, **kwargs: [
                AnalysisPlan.model_validate(
                    {
                        "main_intent": {
                            "type": "distribution",
                            "rationale": "Ranking directo",
                            "filters": [],
                            "metric_unit": "number",
                            "visual_protocol": "bar_chart",
                            "dimension": "region",
                            "metric": "amount",
                            "limit": 10,
                            "group_by": None,
                            "barmode": "stacked",
                        },
                        "title": "Amount por Region",
                        "column_aliases": {"amount": "Amount", "region": "Region"},
                        "metric_polarity": "neutral",
                    }
                )
            ]
        ),
    )
    monkeypatch.setattr(
        "app.services.canonical_shadow_query_runner._get_ibis_engine_cls",
        lambda: _FakeIbisEngine,
    )

    execution = build_canonical_shadow_query_execution(
        file_id="file-1",
        pipeline_result=pipeline,
    )
    summary = summarize_canonical_shadow_query_execution(execution)

    assert execution.readiness_summary["readiness_grade"] == "pilot_candidate"
    assert execution.prompt_strategy == "macro_dimension_bundle"
    assert len(execution.plans) == 1
    assert execution.execution_summaries[0]["status"] == "success"
    assert summary["shadow_query_status"] == "query_executed"
    assert summary["successful_plan_count"] == 1


def test_phase8_shadow_query_runner_prefers_numeric_schema_metric(monkeypatch) -> None:
    dataframe = pd.DataFrame(
        [
            {"fecha_de_stock": "2026-01-01", "tipo_almacen": "130", "ubicacion": "Lima", "stock_disponible": 10},
            {"fecha_de_stock": "2026-01-02", "tipo_almacen": "400", "ubicacion": "Cusco", "stock_disponible": 15},
        ]
    )
    dataframe.attrs["schema_profile"] = {
        "fecha_de_stock": {"type": "temporal", "role": "date"},
        "tipo_almacen": {"type": "categorical", "role": "dimension"},
        "ubicacion": {"type": "categorical", "role": "metric"},
        "stock_disponible": {"type": "numeric", "role": "metric"},
    }
    dataframe.attrs["semantic_contract"] = {
        "dataset_mode": "snapshot",
        "time_axis": "fecha_de_stock",
        "date_columns": ["fecha_de_stock"],
        "metric_columns": ["ubicacion", "stock_disponible"],
        "dimension_columns": ["tipo_almacen"],
        "identifier_columns": [],
    }
    dataframe.attrs["topology_rules"] = {}
    dataframe.attrs["literal_filter_catalog"] = {}
    dataframe.attrs["translator_context_summary"] = ""
    dataframe.attrs["reference_date"] = "2026-01-02"

    pipeline = _build_fake_pipeline(dataframe)
    monkeypatch.setattr(
        "app.services.canonical_shadow_query_runner.DataEngine.commit_to_parquet",
        staticmethod(lambda _df, _file_id: None),
    )
    captured_prompt = {}

    def _fake_translate(prompt, *args, **kwargs):
        captured_prompt["value"] = prompt
        return []

    monkeypatch.setattr(
        "app.services.canonical_shadow_query_runner.SemanticTranslator.translate",
        staticmethod(_fake_translate),
    )

    execution = build_canonical_shadow_query_execution(
        file_id="file-1",
        pipeline_result=pipeline,
    )

    assert "Stock Disponible" in str(captured_prompt.get("value") or "")
    assert execution.prompt_strategy == "macro_dimension_bundle"


def test_phase8_shadow_query_runner_handles_missing_candidate() -> None:
    empty_pipeline = CanonicalDarkRuntimePipelineResult(
        canonical_bundle=_FakeBundle(),
        canonical_bundle_summary={
            "file_name": "vacio.csv",
            "source_kind": "delimited_text",
            "support_level": "full_analytics",
            "preferred_mode": "analytical",
            "analytics_ready": False,
            "quality_gate_applied": False,
            "tabular_frame_count": 0,
        },
        materialized_bundle=_FakeMaterializedBundle(),
        materialized_bundle_summary={"preview_ready_tables": 0},
        preview_runtime=_FakePreviewRuntime(),
        preview_runtime_summary={"tables": []},
        analytical_adapter_runtime=_FakeAnalyticalRuntime("missing", pd.DataFrame()),
        analytical_adapter_summary={"selected_candidate_id": None, "candidate_count": 0, "candidates": []},
        runtime_comparison_summary={"comparison_grade": "no_candidate"},
        metadata={"file_id": "file-empty", "pipeline_status": "empty"},
    )
    empty_pipeline.analytical_adapter_runtime.analytical_bundle.selected_candidate_id = None
    empty_pipeline.analytical_adapter_runtime.candidate_dataframes = {}

    execution = build_canonical_shadow_query_execution(
        file_id="file-empty",
        pipeline_result=empty_pipeline,
    )

    assert execution.metadata["shadow_query_status"] == "no_candidate"
    assert execution.plans == []
    assert execution.execution_summaries == []


def test_phase8_shadow_query_runner_blocks_non_aggregable_shadow_metric(monkeypatch) -> None:
    dataframe = pd.DataFrame(
        [
            {"fecha_de_stock": "2026-01-01", "tipo_almacen": "130", "ubicacion": "Lima", "stock_disponible": 10},
            {"fecha_de_stock": "2026-01-02", "tipo_almacen": "400", "ubicacion": "Cusco", "stock_disponible": 15},
        ]
    )
    dataframe.attrs["schema_profile"] = {
        "fecha_de_stock": {"type": "temporal", "role": "date"},
        "tipo_almacen": {"type": "categorical", "role": "dimension"},
        "ubicacion": {"type": "categorical", "role": "dimension"},
        "stock_disponible": {"type": "numeric", "role": "metric"},
    }
    dataframe.attrs["semantic_contract"] = {
        "dataset_mode": "snapshot",
        "time_axis": "fecha_de_stock",
        "date_columns": ["fecha_de_stock"],
        "metric_columns": ["stock_disponible"],
        "dimension_columns": ["tipo_almacen", "ubicacion"],
        "identifier_columns": [],
    }
    dataframe.attrs["topology_rules"] = {}
    dataframe.attrs["literal_filter_catalog"] = {}
    dataframe.attrs["translator_context_summary"] = ""
    dataframe.attrs["reference_date"] = "2026-01-02"
    dataframe.attrs["shadow_metric_gate"] = {
        "applied": True,
        "safe_metric_columns": ["stock_disponible"],
        "blocked_metric_columns": ["ubicacion"],
    }

    pipeline = _build_fake_pipeline(dataframe)
    monkeypatch.setattr(
        "app.services.canonical_shadow_query_runner.DataEngine.commit_to_parquet",
        staticmethod(lambda _df, _file_id: "/tmp/shadow-query.parquet"),
    )
    monkeypatch.setattr(
        "app.services.canonical_shadow_query_runner.SemanticTranslator.translate",
        staticmethod(
            lambda *args, **kwargs: [
                AnalysisPlan.model_validate(
                    {
                        "main_intent": {
                            "type": "distribution",
                            "rationale": "Intento inválido",
                            "filters": [],
                            "metric_unit": "number",
                            "visual_protocol": "bar_chart",
                            "dimension": "tipo_almacen",
                            "metric": "ubicacion",
                            "limit": 10,
                            "group_by": None,
                            "barmode": "stacked",
                        },
                        "title": "Ubicacion por Tipo Almacen",
                        "column_aliases": {},
                        "metric_polarity": "neutral",
                    }
                )
            ]
        ),
    )

    class _ExplodingIbisEngine:
        @staticmethod
        def execute_plan(*args, **kwargs):
            raise AssertionError("Ibis no debe ejecutarse cuando el metric guard bloquea el plan")

    monkeypatch.setattr(
        "app.services.canonical_shadow_query_runner._get_ibis_engine_cls",
        lambda: _ExplodingIbisEngine,
    )

    execution = build_canonical_shadow_query_execution(
        file_id="file-1",
        pipeline_result=pipeline,
    )

    assert execution.execution_summaries[0]["status"] == "blocked"
    assert "ubicacion" in list(execution.execution_summaries[0].get("blocked_metrics") or [])


def test_phase8_shadow_query_runner_builds_dimension_parity_bundle_without_translator(monkeypatch) -> None:
    dataframe = pd.DataFrame(
        [
            {
                "fecha_de_stock": "2026-01-01",
                "fecaduc_feprefercons": "2026-02-01",
                "ubicacion": "Lima",
                "tipo_almacen": "130",
                "stock_disponible": 10,
                "material": "A1",
            },
            {
                "fecha_de_stock": "2026-01-02",
                "fecaduc_feprefercons": "2026-02-02",
                "ubicacion": "Cusco",
                "tipo_almacen": "400",
                "stock_disponible": 15,
                "material": "A2",
            },
        ]
    )
    dataframe.attrs["schema_profile"] = {
        "fecha_de_stock": {"type": "temporal", "role": "date", "cardinality": 1},
        "fecaduc_feprefercons": {"type": "temporal", "role": "date", "cardinality": 2},
        "ubicacion": {"type": "categorical", "role": "dimension", "cardinality": 2},
        "tipo_almacen": {"type": "categorical", "role": "dimension", "cardinality": 2},
        "stock_disponible": {"type": "numeric", "role": "metric", "cardinality": 2},
        "material": {"type": "categorical", "role": "identifier", "cardinality": 2},
    }
    dataframe.attrs["semantic_contract"] = {
        "dataset_mode": "snapshot",
        "time_axis": "fecha_de_stock",
        "date_columns": ["fecha_de_stock", "fecaduc_feprefercons"],
        "metric_columns": ["stock_disponible"],
        "dimension_columns": ["ubicacion", "tipo_almacen"],
        "identifier_columns": ["material"],
    }
    dataframe.attrs["topology_rules"] = {}
    dataframe.attrs["literal_filter_catalog"] = {}
    dataframe.attrs["translator_context_summary"] = ""
    dataframe.attrs["reference_date"] = "2026-01-02"
    dataframe.attrs["shadow_metric_gate"] = {
        "applied": True,
        "safe_metric_columns": ["stock_disponible"],
        "blocked_metric_columns": ["ubicacion"],
    }

    pipeline = _build_fake_pipeline(dataframe)
    monkeypatch.setattr(
        "app.services.canonical_shadow_query_runner.DataEngine.commit_to_parquet",
        staticmethod(lambda _df, _file_id: "/tmp/shadow-query.parquet"),
    )

    def _translator_should_not_run(*args, **kwargs):
        raise AssertionError("La ruta shadow dimension parity no debe delegar al traductor crudo")

    monkeypatch.setattr(
        "app.services.canonical_shadow_query_runner.SemanticTranslator.translate",
        staticmethod(_translator_should_not_run),
    )
    monkeypatch.setattr(
        "app.services.canonical_shadow_query_runner._get_ibis_engine_cls",
        lambda: _EchoVisualIbisEngine,
    )

    execution = build_canonical_shadow_query_execution(
        file_id="file-1",
        pipeline_result=pipeline,
        prompt="dame un analisis por ubicacion",
        prompt_type="dimension_analysis",
    )
    summary = summarize_canonical_shadow_query_execution(execution)

    assert execution.prompt_strategy == "shadow_dimension_parity_bundle"
    assert [row["chart_type"] for row in execution.execution_summaries] == ["bar_chart", "treemap", "kpi_card"]
    assert execution.plans[0].main_intent.metric == "stock_disponible"
    assert execution.plans[0].main_intent.dimension == "ubicacion"
    assert summary["shadow_query_status"] == "query_executed"


def test_phase8_shadow_query_runner_builds_comparative_parity_bundle_without_translator(monkeypatch) -> None:
    dataframe = pd.DataFrame(
        [
            {"fecha": "2021-06-30", "tipo_almacen": "130", "stock_disponible": 10, "material": "A1"},
            {"fecha": "2021-07-31", "tipo_almacen": "400", "stock_disponible": 15, "material": "A2"},
            {"fecha": "2021-07-31", "tipo_almacen": "130", "stock_disponible": 20, "material": "A3"},
        ]
    )
    dataframe.attrs["schema_profile"] = {
        "fecha": {"type": "temporal", "role": "date", "cardinality": 2},
        "tipo_almacen": {"type": "categorical", "role": "dimension", "cardinality": 2},
        "stock_disponible": {"type": "numeric", "role": "metric", "cardinality": 3},
        "material": {"type": "categorical", "role": "identifier", "cardinality": 3},
    }
    dataframe.attrs["semantic_contract"] = {
        "dataset_mode": "snapshot",
        "time_axis": "fecha",
        "date_columns": ["fecha"],
        "metric_columns": ["stock_disponible"],
        "dimension_columns": ["tipo_almacen"],
        "identifier_columns": ["material"],
    }
    dataframe.attrs["topology_rules"] = {}
    dataframe.attrs["literal_filter_catalog"] = {}
    dataframe.attrs["translator_context_summary"] = ""
    dataframe.attrs["reference_date"] = "2021-07-31"
    dataframe.attrs["shadow_metric_gate"] = {
        "applied": True,
        "safe_metric_columns": ["stock_disponible"],
        "blocked_metric_columns": [],
    }

    pipeline = _build_fake_pipeline(dataframe)
    monkeypatch.setattr(
        "app.services.canonical_shadow_query_runner.DataEngine.commit_to_parquet",
        staticmethod(lambda _df, _file_id: "/tmp/shadow-query.parquet"),
    )

    def _translator_should_not_run(*args, **kwargs):
        raise AssertionError("La ruta shadow comparative parity no debe delegar al traductor crudo")

    monkeypatch.setattr(
        "app.services.canonical_shadow_query_runner.SemanticTranslator.translate",
        staticmethod(_translator_should_not_run),
    )
    monkeypatch.setattr(
        "app.services.canonical_shadow_query_runner._get_ibis_engine_cls",
        lambda: _EchoVisualIbisEngine,
    )

    execution = build_canonical_shadow_query_execution(
        file_id="file-1",
        pipeline_result=pipeline,
        prompt="compara el stock del 31-07-2021 con el stock 30-06-2021",
        prompt_type="comparative_analysis",
    )
    summary = summarize_canonical_shadow_query_execution(execution)

    assert execution.prompt_strategy == "shadow_comparative_parity_bundle"
    assert [row["chart_type"] for row in execution.execution_summaries] == ["bar_chart", "line_chart", "treemap"]
    assert execution.plans[0].main_intent.dimension == "fecha"
    assert summary["shadow_query_status"] == "query_executed"


def test_phase8_shadow_query_runner_builds_visual_parity_dimension_bundle_for_pie_requests(monkeypatch) -> None:
    dataframe = pd.DataFrame(
        [
            {
                "fecha": "2026-01-01",
                "categoria": "Electrónica",
                "nombre": "Auriculares",
                "provincia_de_venta": "Lima",
                "cantidad_vendida": 10,
                "total_venta_pen": 100.0,
            },
            {
                "fecha": "2026-01-02",
                "categoria": "Electrónica",
                "nombre": "Mouse",
                "provincia_de_venta": "Cusco",
                "cantidad_vendida": 15,
                "total_venta_pen": 150.0,
            },
            {
                "fecha": "2026-01-03",
                "categoria": "Hogar",
                "nombre": "Licuadora",
                "provincia_de_venta": "Lima",
                "cantidad_vendida": 8,
                "total_venta_pen": 80.0,
            },
        ]
    )
    dataframe.attrs["schema_profile"] = {
        "fecha": {"type": "temporal", "role": "date", "cardinality": 3},
        "categoria": {"type": "categorical", "role": "dimension", "cardinality": 2},
        "nombre": {"type": "categorical", "role": "dimension", "cardinality": 3},
        "provincia_de_venta": {"type": "categorical", "role": "dimension", "cardinality": 2},
        "cantidad_vendida": {"type": "numeric", "role": "metric", "cardinality": 3},
        "total_venta_pen": {"type": "numeric", "role": "metric", "cardinality": 3},
    }
    dataframe.attrs["semantic_contract"] = {
        "dataset_mode": "flow",
        "time_axis": "fecha",
        "date_columns": ["fecha"],
        "metric_columns": ["cantidad_vendida", "total_venta_pen"],
        "dimension_columns": ["categoria", "nombre", "provincia_de_venta"],
        "identifier_columns": [],
    }
    dataframe.attrs["topology_rules"] = {}
    dataframe.attrs["literal_filter_catalog"] = {
        "categoria": ["Electrónica", "Hogar"],
        "provincia_de_venta": ["Lima", "Cusco"],
    }
    dataframe.attrs["translator_context_summary"] = ""
    dataframe.attrs["reference_date"] = "2026-01-03"
    dataframe.attrs["shadow_metric_gate"] = {
        "applied": True,
        "safe_metric_columns": ["cantidad_vendida", "total_venta_pen"],
        "blocked_metric_columns": [],
    }

    pipeline = _build_fake_pipeline(dataframe)
    monkeypatch.setattr(
        "app.services.canonical_shadow_query_runner.DataEngine.commit_to_parquet",
        staticmethod(lambda _df, _file_id: "/tmp/shadow-query.parquet"),
    )

    def _translator_should_not_run(*args, **kwargs):
        raise AssertionError("La ruta shadow dimension visual parity no debe delegar al traductor crudo")

    monkeypatch.setattr(
        "app.services.canonical_shadow_query_runner.SemanticTranslator.translate",
        staticmethod(_translator_should_not_run),
    )
    monkeypatch.setattr(
        "app.services.canonical_shadow_query_runner._get_ibis_engine_cls",
        lambda: _EchoVisualIbisEngine,
    )

    execution = build_canonical_shadow_query_execution(
        file_id="file-1",
        pipeline_result=pipeline,
        prompt="realiza un analisis de la categoria Electrónica. por cantidad vendida y Total venta",
        prompt_type="dimension_analysis",
        requested_visual_family="pie_chart",
    )
    summary = summarize_canonical_shadow_query_execution(execution)

    assert execution.prompt_strategy == "shadow_dimension_visual_parity_bundle"
    assert [row["chart_type"] for row in execution.execution_summaries] == ["pie_chart", "treemap"]
    assert execution.plans[0].main_intent.dimension == "provincia_de_venta"
    assert [str(filter_row.column) for filter_row in execution.plans[0].main_intent.filters] == ["categoria"]
    assert summary["shadow_query_status"] == "query_executed"


def test_phase8_shadow_query_runner_allows_scatter_with_temporal_axes(monkeypatch) -> None:
    dataframe = pd.DataFrame(
        [
            {
                "fecha_de_stock": "2026-01-01",
                "fecaduc_feprefercons": "2026-01-08",
                "tipo_almacen": "130",
                "stock_disponible": 10,
            },
            {
                "fecha_de_stock": "2026-01-02",
                "fecaduc_feprefercons": "2026-01-10",
                "tipo_almacen": "400",
                "stock_disponible": 15,
            },
        ]
    )
    dataframe.attrs["schema_profile"] = {
        "fecha_de_stock": {"type": "temporal", "role": "date", "cardinality": 2},
        "fecaduc_feprefercons": {"type": "temporal", "role": "date", "cardinality": 2},
        "tipo_almacen": {"type": "categorical", "role": "dimension", "cardinality": 2},
        "stock_disponible": {"type": "numeric", "role": "metric", "cardinality": 2},
    }
    dataframe.attrs["semantic_contract"] = {
        "dataset_mode": "snapshot",
        "time_axis": "fecha_de_stock",
        "date_columns": ["fecha_de_stock", "fecaduc_feprefercons"],
        "metric_columns": ["stock_disponible"],
        "dimension_columns": ["tipo_almacen"],
        "identifier_columns": [],
    }
    dataframe.attrs["topology_rules"] = {}
    dataframe.attrs["literal_filter_catalog"] = {}
    dataframe.attrs["translator_context_summary"] = ""
    dataframe.attrs["reference_date"] = "2026-01-02"
    dataframe.attrs["shadow_metric_gate"] = {
        "applied": True,
        "safe_metric_columns": ["stock_disponible"],
        "blocked_metric_columns": [],
    }

    pipeline = _build_fake_pipeline(dataframe)
    monkeypatch.setattr(
        "app.services.canonical_shadow_query_runner.DataEngine.commit_to_parquet",
        staticmethod(lambda _df, _file_id: "/tmp/shadow-query.parquet"),
    )
    monkeypatch.setattr(
        "app.services.canonical_shadow_query_runner._get_ibis_engine_cls",
        lambda: _EchoVisualIbisEngine,
    )

    execution = build_canonical_shadow_query_execution(
        file_id="file-1",
        pipeline_result=pipeline,
        prompt=(
            "Crea un scatter donde X sea días a vencimiento "
            "(Fecaduc Feprefercons - Fecha de stock), Y sea stock disponible, "
            "y color por Tipo almacén."
        ),
        prompt_type="chart_request",
        requested_visual_family="scatter_plot",
    )
    summary = summarize_canonical_shadow_query_execution(execution)

    assert execution.prompt_strategy == "shadow_scatter_visual_parity_bundle"
    assert len(execution.plans) == 1
    assert execution.execution_summaries[0]["status"] == "success"
    assert execution.execution_summaries[0]["chart_type"] == "scatter_plot"
    assert summary["shadow_query_status"] == "query_executed"


def test_phase8_shadow_query_runner_infers_scatter_from_prompt_without_requested_family(monkeypatch) -> None:
    dataframe = pd.DataFrame(
        [
            {
                "fecha_de_stock": "2026-01-01",
                "fecaduc_feprefercons": "2026-01-08",
                "tipo_almacen": "130",
                "stock_disponible": 10,
            },
            {
                "fecha_de_stock": "2026-01-02",
                "fecaduc_feprefercons": "2026-01-10",
                "tipo_almacen": "400",
                "stock_disponible": 15,
            },
        ]
    )
    dataframe.attrs["schema_profile"] = {
        "fecha_de_stock": {"type": "temporal", "role": "date", "cardinality": 2},
        "fecaduc_feprefercons": {"type": "temporal", "role": "date", "cardinality": 2},
        "tipo_almacen": {"type": "categorical", "role": "dimension", "cardinality": 2},
        "stock_disponible": {"type": "numeric", "role": "metric", "cardinality": 2},
    }
    dataframe.attrs["semantic_contract"] = {
        "dataset_mode": "snapshot",
        "time_axis": "fecha_de_stock",
        "date_columns": ["fecha_de_stock", "fecaduc_feprefercons"],
        "metric_columns": ["stock_disponible"],
        "dimension_columns": ["tipo_almacen"],
        "identifier_columns": [],
    }
    dataframe.attrs["topology_rules"] = {}
    dataframe.attrs["literal_filter_catalog"] = {}
    dataframe.attrs["translator_context_summary"] = ""
    dataframe.attrs["reference_date"] = "2026-01-02"
    dataframe.attrs["shadow_metric_gate"] = {
        "applied": True,
        "safe_metric_columns": ["stock_disponible"],
        "blocked_metric_columns": [],
    }

    pipeline = _build_fake_pipeline(dataframe)
    monkeypatch.setattr(
        "app.services.canonical_shadow_query_runner.DataEngine.commit_to_parquet",
        staticmethod(lambda _df, _file_id: "/tmp/shadow-query.parquet"),
    )
    monkeypatch.setattr(
        "app.services.canonical_shadow_query_runner._get_ibis_engine_cls",
        lambda: _EchoVisualIbisEngine,
    )

    execution = build_canonical_shadow_query_execution(
        file_id="file-1",
        pipeline_result=pipeline,
        prompt=(
            "Crea un scatter donde X sea días a vencimiento "
            "(Fecaduc Feprefercons - Fecha de stock), Y sea stock disponible, "
            "y color por Tipo almacén."
        ),
        prompt_type="chart_request",
        requested_visual_family=None,
    )

    assert execution.prompt_strategy == "shadow_scatter_visual_parity_bundle"
    assert execution.execution_summaries[0]["chart_type"] == "scatter_plot"


def test_phase8_shadow_query_runner_promotes_literal_filtered_dimension_analysis_to_visual_parity(monkeypatch) -> None:
    dataframe = pd.DataFrame(
        [
            {
                "fecha": "2026-01-01",
                "categoria": "Electrónica",
                "provincia_de_venta": "Lima",
                "cantidad_vendida": 100,
                "total_venta_pen": 500.0,
            },
            {
                "fecha": "2026-01-02",
                "categoria": "Electrónica",
                "provincia_de_venta": "Cusco",
                "cantidad_vendida": 90,
                "total_venta_pen": 420.0,
            },
            {
                "fecha": "2026-01-03",
                "categoria": "Hogar",
                "provincia_de_venta": "Lima",
                "cantidad_vendida": 50,
                "total_venta_pen": 200.0,
            },
        ]
    )
    dataframe.attrs["schema_profile"] = {
        "fecha": {"type": "temporal", "role": "date", "cardinality": 3},
        "categoria": {"type": "categorical", "role": "dimension", "cardinality": 2},
        "provincia_de_venta": {"type": "categorical", "role": "dimension", "cardinality": 2},
        "cantidad_vendida": {"type": "numeric", "role": "metric", "cardinality": 3},
        "total_venta_pen": {"type": "numeric", "role": "metric", "cardinality": 3},
    }
    dataframe.attrs["semantic_contract"] = {
        "dataset_mode": "flow",
        "time_axis": "fecha",
        "date_columns": ["fecha"],
        "metric_columns": ["cantidad_vendida", "total_venta_pen"],
        "dimension_columns": ["categoria", "provincia_de_venta"],
        "identifier_columns": [],
    }
    dataframe.attrs["topology_rules"] = {}
    dataframe.attrs["literal_filter_catalog"] = {
        "categoria": ["Electrónica", "Hogar"],
        "provincia_de_venta": ["Lima", "Cusco"],
    }
    dataframe.attrs["translator_context_summary"] = ""
    dataframe.attrs["reference_date"] = "2026-01-03"
    dataframe.attrs["shadow_metric_gate"] = {
        "applied": True,
        "safe_metric_columns": ["cantidad_vendida", "total_venta_pen"],
        "blocked_metric_columns": [],
    }

    pipeline = _build_fake_pipeline(dataframe)
    monkeypatch.setattr(
        "app.services.canonical_shadow_query_runner.DataEngine.commit_to_parquet",
        staticmethod(lambda _df, _file_id: "/tmp/shadow-query.parquet"),
    )
    monkeypatch.setattr(
        "app.services.canonical_shadow_query_runner._get_ibis_engine_cls",
        lambda: _EchoVisualIbisEngine,
    )

    execution = build_canonical_shadow_query_execution(
        file_id="file-1",
        pipeline_result=pipeline,
        prompt="realiza un analisis de la categoria Electrónica. por cantidad vendida y Total venta",
        prompt_type="dimension_analysis",
        requested_visual_family=None,
    )

    assert execution.prompt_strategy == "shadow_dimension_visual_parity_bundle"
    assert [row["chart_type"] for row in execution.execution_summaries] == ["pie_chart", "bar_chart"]


def test_phase8_shadow_query_runner_builds_generic_visual_parity_bundle(monkeypatch) -> None:
    dataframe = pd.DataFrame(
        [
            {"fecha": "2026-01-01", "provincia": "Lima", "total_venta_pen": 100},
            {"fecha": "2026-01-02", "provincia": "Cusco", "total_venta_pen": 120},
            {"fecha": "2026-01-03", "provincia": "Piura", "total_venta_pen": 90},
        ]
    )
    dataframe.attrs["schema_profile"] = {
        "fecha": {"type": "temporal", "role": "date", "cardinality": 3},
        "provincia": {"type": "categorical", "role": "dimension", "cardinality": 3},
        "total_venta_pen": {"type": "numeric", "role": "metric", "cardinality": 3},
    }
    dataframe.attrs["semantic_contract"] = {
        "dataset_mode": "flow",
        "time_axis": "fecha",
        "date_columns": ["fecha"],
        "metric_columns": ["total_venta_pen"],
        "dimension_columns": ["provincia"],
        "identifier_columns": [],
    }
    dataframe.attrs["topology_rules"] = {}
    dataframe.attrs["literal_filter_catalog"] = {}
    dataframe.attrs["translator_context_summary"] = ""
    dataframe.attrs["reference_date"] = "2026-01-03"

    pipeline = _build_fake_pipeline(dataframe)
    monkeypatch.setattr(
        "app.services.canonical_shadow_query_runner.DataEngine.commit_to_parquet",
        staticmethod(lambda _df, _file_id: "/tmp/shadow-query.parquet"),
    )
    monkeypatch.setattr(
        "app.services.canonical_shadow_query_runner._get_ibis_engine_cls",
        lambda: _EchoVisualIbisEngine,
    )

    execution = build_canonical_shadow_query_execution(
        file_id="file-1",
        pipeline_result=pipeline,
        prompt="realiza un analisis sobre las ventas",
        prompt_type="generic_analysis",
        requested_visual_family="pie_chart",
    )

    assert execution.prompt_strategy == "shadow_generic_visual_parity_bundle"
    assert len(execution.plans) == 3
    assert [row["chart_type"] for row in execution.execution_summaries] == ["pie_chart", "bar_chart", "treemap"]


def test_phase8_shadow_query_runner_delegates_expiry_window_prompt_to_translator(monkeypatch) -> None:
    dataframe = pd.DataFrame(
        [
            {"fecha_de_stock": "2026-01-01", "fecaduc_feprefercons": "2026-02-10", "material": "A", "stock_disponible": 100},
            {"fecha_de_stock": "2026-01-01", "fecaduc_feprefercons": "2026-04-01", "material": "B", "stock_disponible": 80},
        ]
    )
    dataframe.attrs["schema_profile"] = {
        "fecha_de_stock": {"type": "temporal", "role": "date", "cardinality": 1},
        "fecaduc_feprefercons": {"type": "temporal", "role": "date", "cardinality": 2},
        "material": {"type": "categorical", "role": "dimension", "cardinality": 2},
        "stock_disponible": {"type": "numeric", "role": "metric", "cardinality": 2},
    }
    dataframe.attrs["semantic_contract"] = {
        "dataset_mode": "snapshot",
        "time_axis": "fecha_de_stock",
        "date_columns": ["fecha_de_stock", "fecaduc_feprefercons"],
        "metric_columns": ["stock_disponible"],
        "dimension_columns": ["material"],
        "identifier_columns": [],
        "snapshot_guard_allowed": True,
    }
    dataframe.attrs["topology_rules"] = {}
    dataframe.attrs["literal_filter_catalog"] = {}
    dataframe.attrs["translator_context_summary"] = ""
    dataframe.attrs["reference_date"] = "2026-01-01"

    pipeline = _build_fake_pipeline(dataframe)
    monkeypatch.setattr(
        "app.services.canonical_shadow_query_runner.DataEngine.commit_to_parquet",
        staticmethod(lambda _df, _file_id: "/tmp/shadow-query.parquet"),
    )
    monkeypatch.setattr(
        "app.services.canonical_shadow_query_runner._get_ibis_engine_cls",
        lambda: _EchoVisualIbisEngine,
    )
    monkeypatch.setattr(
        "app.services.canonical_shadow_query_runner.SemanticTranslator.translate",
        staticmethod(
            lambda *args, **kwargs: [
                AnalysisPlan.model_validate(
                    {
                        "main_intent": {
                            "type": "distribution",
                            "rationale": "Filtra vencimientos cercanos",
                            "filters": [
                                {
                                    "column": "fecaduc_feprefercons",
                                    "operator": "<",
                                    "value": "2026-03-01",
                                }
                            ],
                            "metric_unit": "number",
                            "visual_protocol": "bar_chart",
                            "dimension": "material",
                            "metric": "stock_disponible",
                            "limit": 10,
                            "group_by": None,
                            "barmode": "stacked",
                        },
                        "title": "Materiales por vencer",
                        "column_aliases": {
                            "material": "Material",
                            "stock_disponible": "Stock Disponible",
                        },
                        "metric_polarity": "unfavorable",
                    }
                )
            ]
        ),
    )

    execution = build_canonical_shadow_query_execution(
        file_id="file-expiry",
        pipeline_result=pipeline,
        prompt="Analiza los materiales que vencen en 60 dias",
        prompt_type="expiry_window_analysis",
    )

    assert execution.prompt_strategy == "custom_prompt"
    assert len(execution.plans) == 1
    assert execution.execution_summaries[0]["status"] == "success"


def test_phase8_shadow_query_runner_delegates_complex_chart_prompt_to_translator(monkeypatch) -> None:
    dataframe = pd.DataFrame(
        [
            {"fecha": "2026-01-01", "producto": "A", "ventas": 100},
            {"fecha": "2026-02-01", "producto": "B", "ventas": 120},
            {"fecha": "2026-03-01", "producto": "C", "ventas": 90},
        ]
    )
    dataframe.attrs["schema_profile"] = {
        "fecha": {"type": "temporal", "role": "date", "cardinality": 3},
        "producto": {"type": "categorical", "role": "dimension", "cardinality": 3},
        "ventas": {"type": "numeric", "role": "metric", "cardinality": 3},
    }
    dataframe.attrs["semantic_contract"] = {
        "dataset_mode": "flow",
        "time_axis": "fecha",
        "date_columns": ["fecha"],
        "metric_columns": ["ventas"],
        "dimension_columns": ["producto"],
        "identifier_columns": [],
    }
    dataframe.attrs["topology_rules"] = {}
    dataframe.attrs["literal_filter_catalog"] = {}
    dataframe.attrs["translator_context_summary"] = ""
    dataframe.attrs["reference_date"] = "2026-03-01"

    pipeline = _build_fake_pipeline(dataframe)
    monkeypatch.setattr(
        "app.services.canonical_shadow_query_runner.DataEngine.commit_to_parquet",
        staticmethod(lambda _df, _file_id: "/tmp/shadow-query.parquet"),
    )
    monkeypatch.setattr(
        "app.services.canonical_shadow_query_runner._get_ibis_engine_cls",
        lambda: _EchoVisualIbisEngine,
    )
    monkeypatch.setattr(
        "app.services.canonical_shadow_query_runner.SemanticTranslator.translate",
        staticmethod(
            lambda *args, **kwargs: [
                AnalysisPlan.model_validate(
                    {
                        "main_intent": {
                            "type": "trend",
                            "rationale": "Trend top n acumulado",
                            "filters": [],
                            "metric_unit": "number",
                            "visual_protocol": "line_chart",
                            "date_column": "fecha",
                            "value_column": "ventas",
                            "grain": "month",
                            "fill_missing": True,
                            "split_dimension": "producto",
                            "split_limit": 5,
                            "top_n_aggregation_mode": "sum",
                        },
                        "title": "Evolución de ventas (Suma Top 5 productos)",
                        "column_aliases": {"fecha": "Fecha", "ventas": "Ventas", "producto": "Producto"},
                        "metric_polarity": "neutral",
                    }
                )
            ]
        ),
    )

    execution = build_canonical_shadow_query_execution(
        file_id="file-trend-topn",
        pipeline_result=pipeline,
        prompt="Realiza un gráfico de evolución mensual de la suma total del top 5 productos por ventas",
        prompt_type="chart_request",
        requested_visual_family="line_chart",
    )

    assert execution.prompt_strategy == "custom_prompt"
    assert len(execution.plans) == 1
    assert getattr(execution.plans[0].main_intent, "top_n_aggregation_mode", None) == "sum"


def test_phase8_shadow_query_runner_builds_chart_visual_parity_bundle(monkeypatch) -> None:
    dataframe = pd.DataFrame(
        [
            {"tipo_almacen": "130", "stock_disponible": 100},
            {"tipo_almacen": "400", "stock_disponible": 80},
            {"tipo_almacen": "500", "stock_disponible": 60},
        ]
    )
    dataframe.attrs["schema_profile"] = {
        "tipo_almacen": {"type": "categorical", "role": "dimension", "cardinality": 15},
        "stock_disponible": {"type": "numeric", "role": "metric", "cardinality": 3},
    }
    dataframe.attrs["semantic_contract"] = {
        "dataset_mode": "snapshot",
        "time_axis": None,
        "date_columns": [],
        "metric_columns": ["stock_disponible"],
        "dimension_columns": ["tipo_almacen"],
        "identifier_columns": [],
    }
    dataframe.attrs["topology_rules"] = {}
    dataframe.attrs["literal_filter_catalog"] = {}
    dataframe.attrs["translator_context_summary"] = ""
    dataframe.attrs["reference_date"] = "2026-01-03"

    pipeline = _build_fake_pipeline(dataframe)
    monkeypatch.setattr(
        "app.services.canonical_shadow_query_runner.DataEngine.commit_to_parquet",
        staticmethod(lambda _df, _file_id: "/tmp/shadow-query.parquet"),
    )
    monkeypatch.setattr(
        "app.services.canonical_shadow_query_runner._get_ibis_engine_cls",
        lambda: _EchoVisualIbisEngine,
    )

    execution = build_canonical_shadow_query_execution(
        file_id="file-1",
        pipeline_result=pipeline,
        prompt="Dame un grafico por tipo de almacen",
        prompt_type="chart_request",
        requested_visual_family="treemap",
    )

    assert execution.prompt_strategy == "shadow_chart_visual_parity_bundle"
    assert len(execution.plans) == 1
    assert execution.execution_summaries[0]["chart_type"] == "treemap"


def test_phase8_shadow_query_runner_builds_explicit_heatmap_bundle_for_advanced_prompt(monkeypatch) -> None:
    dataframe = pd.DataFrame(
        [
            {"fecha_de_stock": "2021-03-31", "tipo_almacen": "130", "stock_disponible": 100},
            {"fecha_de_stock": "2021-04-30", "tipo_almacen": "400", "stock_disponible": 80},
            {"fecha_de_stock": "2021-05-31", "tipo_almacen": "130", "stock_disponible": 120},
        ]
    )
    dataframe.attrs["schema_profile"] = {
        "fecha_de_stock": {"type": "temporal", "role": "date", "cardinality": 3},
        "tipo_almacen": {"type": "categorical", "role": "dimension", "cardinality": 2},
        "stock_disponible": {"type": "numeric", "role": "metric", "cardinality": 3},
    }
    dataframe.attrs["semantic_contract"] = {
        "dataset_mode": "snapshot",
        "time_axis": "fecha_de_stock",
        "date_columns": ["fecha_de_stock"],
        "metric_columns": ["stock_disponible"],
        "dimension_columns": ["tipo_almacen"],
        "identifier_columns": [],
    }
    dataframe.attrs["topology_rules"] = {}
    dataframe.attrs["literal_filter_catalog"] = {}
    dataframe.attrs["translator_context_summary"] = ""
    dataframe.attrs["reference_date"] = "2021-05-31"

    pipeline = _build_fake_pipeline(dataframe)
    monkeypatch.setattr(
        "app.services.canonical_shadow_query_runner.DataEngine.commit_to_parquet",
        staticmethod(lambda _df, _file_id: "/tmp/shadow-query.parquet"),
    )
    monkeypatch.setattr(
        "app.services.canonical_shadow_query_runner._get_ibis_engine_cls",
        lambda: _EchoVisualIbisEngine,
    )

    execution = build_canonical_shadow_query_execution(
        file_id="file-1",
        pipeline_result=pipeline,
        prompt="Quiero un heatmap del stock disponible por Fecha de stock y Tipo almacén",
        prompt_type="chart_request",
        requested_visual_family=None,
    )

    assert execution.prompt_strategy == "shadow_heatmap_visual_bundle"
    assert len(execution.plans) == 1
    assert execution.execution_summaries[0]["chart_type"] == "heatmap"


def test_phase8_shadow_query_runner_builds_explicit_boxplot_bundle_for_advanced_prompt(monkeypatch) -> None:
    dataframe = pd.DataFrame(
        [
            {"tipo_almacen": "130", "stock_disponible": 100},
            {"tipo_almacen": "400", "stock_disponible": 80},
            {"tipo_almacen": "500", "stock_disponible": 60},
        ]
    )
    dataframe.attrs["schema_profile"] = {
        "tipo_almacen": {"type": "categorical", "role": "dimension", "cardinality": 3},
        "stock_disponible": {"type": "numeric", "role": "metric", "cardinality": 3},
    }
    dataframe.attrs["semantic_contract"] = {
        "dataset_mode": "snapshot",
        "time_axis": None,
        "date_columns": [],
        "metric_columns": ["stock_disponible"],
        "dimension_columns": ["tipo_almacen"],
        "identifier_columns": [],
    }
    dataframe.attrs["topology_rules"] = {}
    dataframe.attrs["literal_filter_catalog"] = {}
    dataframe.attrs["translator_context_summary"] = ""
    dataframe.attrs["reference_date"] = "2026-01-03"

    pipeline = _build_fake_pipeline(dataframe)
    monkeypatch.setattr(
        "app.services.canonical_shadow_query_runner.DataEngine.commit_to_parquet",
        staticmethod(lambda _df, _file_id: "/tmp/shadow-query.parquet"),
    )
    monkeypatch.setattr(
        "app.services.canonical_shadow_query_runner._get_ibis_engine_cls",
        lambda: _EchoVisualIbisEngine,
    )

    execution = build_canonical_shadow_query_execution(
        file_id="file-1",
        pipeline_result=pipeline,
        prompt="Quiero un boxplot de stock disponible por tipo almacen",
        prompt_type="dimension_analysis",
        requested_visual_family=None,
    )

    assert execution.prompt_strategy == "shadow_explicit_visual_bundle"
    assert len(execution.plans) == 1
    assert execution.execution_summaries[0]["chart_type"] == "boxplot"
