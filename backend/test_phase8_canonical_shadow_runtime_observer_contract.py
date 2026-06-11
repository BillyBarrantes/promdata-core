from app.core.config import settings
from app.services.canonical_shadow_runtime_observer import (
    _classify_prompt_type,
    build_live_runtime_summary,
    build_shadow_live_divergence_summary,
    observe_canonical_shadow_runtime,
)


def test_phase8_shadow_runtime_observer_respects_disabled_flag(monkeypatch) -> None:
    monkeypatch.setattr(settings, "CANONICAL_SHADOW_TRAFFIC_MIRROR_ENABLED", False)

    summary = observe_canonical_shadow_runtime(
        task_id="task-1",
        file_id="file-1",
        prompt="Analiza ventas",
        live_summary={"status": "completed"},
        uploaded_file_row={"file_name": "dataset.csv"},
        service_client=object(),
    )

    assert summary["observer_status"] == "disabled"


def test_phase8_shadow_runtime_observer_skips_non_tabular_when_protected(monkeypatch) -> None:
    monkeypatch.setattr(settings, "CANONICAL_SHADOW_TRAFFIC_MIRROR_ENABLED", True)
    monkeypatch.setattr(settings, "CANONICAL_SHADOW_TRAFFIC_MIRROR_TABULAR_ONLY", True)

    summary = observe_canonical_shadow_runtime(
        task_id="task-1",
        file_id="file-1",
        prompt="Analiza documento",
        live_summary={"status": "completed"},
        uploaded_file_row={"file_name": "memo.docx"},
        service_client=object(),
    )

    assert summary["observer_status"] == "skipped_non_tabular"


def test_phase8_shadow_runtime_observer_compares_live_vs_shadow(monkeypatch) -> None:
    monkeypatch.setattr(settings, "CANONICAL_SHADOW_TRAFFIC_MIRROR_ENABLED", True)
    monkeypatch.setattr(settings, "CANONICAL_SHADOW_TRAFFIC_MIRROR_TABULAR_ONLY", True)
    monkeypatch.setattr(settings, "CANONICAL_SHADOW_TRAFFIC_MIRROR_MAX_PLANS", 3)
    monkeypatch.setattr(
        "app.services.canonical_shadow_runtime_observer.run_canonical_shadow_query_for_uploaded_file",
        lambda **kwargs: object(),
    )
    monkeypatch.setattr(
        "app.services.canonical_shadow_runtime_observer.summarize_canonical_shadow_query_execution",
        lambda execution: {
            "readiness_grade": "pilot_candidate",
            "shadow_query_status": "query_executed",
            "successful_plan_count": 1,
            "executions": [
                {
                    "status": "success",
                    "chart_type": "bar_chart",
                }
            ],
        },
    )
    monkeypatch.setattr(
        "app.services.canonical_shadow_runtime_observer._shadow_candidate_contract",
        lambda execution: {
            "dataset_mode": "flow",
            "time_axis": "fecha",
        },
    )
    captured_telemetry = {}
    monkeypatch.setattr(
        "app.services.canonical_shadow_runtime_observer.track_shadow_runtime_observed",
        lambda **kwargs: captured_telemetry.update(kwargs),
    )

    live_summary = build_live_runtime_summary(
        status="completed",
        prompt='{"text":"Analiza ventas por región"}',
        final_struct={
            "analysis": "ok",
            "chart_options": [
                {
                    "series": [{"type": "bar"}],
                }
            ],
            "metrics": {"ventas": 120.0},
            "data": [{"region": "North", "ventas": 120.0}],
        },
        dataset_contract={
            "dataset_mode": "flow",
            "time_axis": "fecha",
            "metric_columns": ["ventas"],
            "dimension_columns": ["region"],
        },
        live_duration_ms=1000,
    )

    summary = observe_canonical_shadow_runtime(
        task_id="task-1",
        file_id="file-1",
        prompt='{"text":"Analiza ventas por región"}',
        live_summary=live_summary,
        uploaded_file_row={"file_name": "ventas.xlsx", "user_id": "00000000-0000-4000-8000-000000000001", "team_id": "00000000-0000-4000-8000-000000000002"},
        service_client=object(),
    )

    assert summary["observer_status"] == "observed"
    assert summary["divergence"]["alignment_grade"] == "high_alignment"
    assert summary["latency"]["shadow_duration_ms"] >= 0
    assert summary["prompt_type"] == "dimension_analysis"
    assert summary["requested_visual_family"] == "bar_chart"
    assert captured_telemetry["alignment_grade"] == "high_alignment"
    assert captured_telemetry["shadow_query_status"] == "query_executed"
    assert captured_telemetry["prompt_type"] == "dimension_analysis"
    assert captured_telemetry["requested_visual_family"] == "bar_chart"
    assert captured_telemetry["live_primary_visual"] == "bar_chart"
    assert captured_telemetry["shadow_primary_visual"] == "bar_chart"


def test_phase8_shadow_runtime_observer_normalizes_funnel_alias() -> None:
    divergence = build_shadow_live_divergence_summary(
        live_summary={
            "chart_count": 1,
            "visual_types": ["funnel_chart"],
            "dataset_mode": "snapshot",
            "time_axis": "fecha_de_stock",
        },
        shadow_summary={
            "shadow_query_status": "query_executed",
            "successful_plan_count": 1,
            "executions": [
                {
                    "status": "success",
                    "chart_type": "funnel",
                }
            ],
        },
        shadow_candidate_contract={
            "dataset_mode": "snapshot",
            "time_axis": "fecha_de_stock",
        },
    )

    assert divergence["alignment_grade"] == "high_alignment"
    assert divergence["mismatches"] == []


def test_phase8_shadow_runtime_observer_ignores_additive_kpi_overage() -> None:
    divergence = build_shadow_live_divergence_summary(
        live_summary={
            "chart_count": 2,
            "visual_types": ["bar_chart", "treemap"],
            "dataset_mode": "snapshot",
            "time_axis": "fecha_de_stock",
        },
        shadow_summary={
            "shadow_query_status": "query_executed",
            "successful_plan_count": 3,
            "executions": [
                {"status": "success", "chart_type": "kpi"},
                {"status": "success", "chart_type": "bar_chart"},
                {"status": "success", "chart_type": "treemap"},
            ],
        },
        shadow_candidate_contract={
            "dataset_mode": "snapshot",
            "time_axis": "fecha_de_stock",
        },
    )

    assert divergence["alignment_grade"] == "high_alignment"
    assert divergence["mismatches"] == []
    assert divergence["shadow_chart_count"] == 3
    assert divergence["shadow_comparable_chart_count"] == 2
    assert divergence["shadow_additive_chart_overage"] == 0


def test_phase8_shadow_runtime_observer_classifies_complete_analysis_request(monkeypatch) -> None:
    monkeypatch.setattr(settings, "CANONICAL_SHADOW_TRAFFIC_MIRROR_ENABLED", True)
    monkeypatch.setattr(settings, "CANONICAL_SHADOW_TRAFFIC_MIRROR_TABULAR_ONLY", True)
    monkeypatch.setattr(settings, "CANONICAL_SHADOW_TRAFFIC_MIRROR_MAX_PLANS", 3)
    monkeypatch.setattr(
        "app.services.canonical_shadow_runtime_observer.run_canonical_shadow_query_for_uploaded_file",
        lambda **kwargs: object(),
    )
    monkeypatch.setattr(
        "app.services.canonical_shadow_runtime_observer.summarize_canonical_shadow_query_execution",
        lambda execution: {
            "readiness_grade": "pilot_candidate",
            "shadow_query_status": "query_executed",
            "successful_plan_count": 2,
            "executions": [
                {"status": "success", "chart_type": "line_chart"},
                {"status": "success", "chart_type": "bar_chart"},
            ],
        },
    )
    monkeypatch.setattr(
        "app.services.canonical_shadow_runtime_observer._shadow_candidate_contract",
        lambda execution: {
            "dataset_mode": "flow",
            "time_axis": "fecha",
        },
    )

    summary = observe_canonical_shadow_runtime(
        task_id="task-2",
        file_id="file-2",
        prompt='{"text":"Realiza un análisis completo de ventas"}',
        live_summary={
            "status": "completed",
            "chart_count": 2,
            "visual_types": ["line_chart", "bar_chart"],
            "dataset_mode": "flow",
            "time_axis": "fecha",
            "live_duration_ms": 900,
        },
        uploaded_file_row={"file_name": "ventas.xlsx", "user_id": "00000000-0000-4000-8000-000000000001", "team_id": "00000000-0000-4000-8000-000000000002"},
        service_client=object(),
    )

    assert summary["observer_status"] == "observed"
    assert summary["prompt_type"] == "complete_analysis"
    assert summary["requested_visual_family"] == "line_chart"


def test_phase8_shadow_runtime_observer_classifies_comparative_request(monkeypatch) -> None:
    monkeypatch.setattr(settings, "CANONICAL_SHADOW_TRAFFIC_MIRROR_ENABLED", True)
    monkeypatch.setattr(settings, "CANONICAL_SHADOW_TRAFFIC_MIRROR_TABULAR_ONLY", True)
    monkeypatch.setattr(settings, "CANONICAL_SHADOW_TRAFFIC_MIRROR_MAX_PLANS", 3)
    monkeypatch.setattr(
        "app.services.canonical_shadow_runtime_observer.run_canonical_shadow_query_for_uploaded_file",
        lambda **kwargs: object(),
    )
    monkeypatch.setattr(
        "app.services.canonical_shadow_runtime_observer.summarize_canonical_shadow_query_execution",
        lambda execution: {
            "readiness_grade": "pilot_candidate",
            "shadow_query_status": "partial_query_success",
            "successful_plan_count": 1,
            "executions": [{"status": "success", "chart_type": "bar_chart"}],
        },
    )
    monkeypatch.setattr(
        "app.services.canonical_shadow_runtime_observer._shadow_candidate_contract",
        lambda execution: {
            "dataset_mode": "snapshot",
            "time_axis": "fecha_de_stock",
        },
    )

    summary = observe_canonical_shadow_runtime(
        task_id="task-3",
        file_id="file-3",
        prompt='{"text":"Compara junio vs julio por tipo de almacen"}',
        live_summary={
            "status": "completed",
            "chart_count": 3,
            "visual_types": ["bar_chart"],
            "dataset_mode": "snapshot",
            "time_axis": "fecha_de_stock",
            "live_duration_ms": 500,
        },
        uploaded_file_row={"file_name": "stock.xlsx", "user_id": "00000000-0000-4000-8000-000000000001", "team_id": "00000000-0000-4000-8000-000000000002"},
        service_client=object(),
    )

    assert summary["observer_status"] == "observed"
    assert summary["prompt_type"] == "comparative_analysis"


def test_phase8_shadow_runtime_observer_classifies_expiry_window_request() -> None:
    prompt_type = _classify_prompt_type(
        '{"text":"Analiza materiales que vencen en 60 dias desde la fecha de stock"}',
        {},
    )
    assert prompt_type == "expiry_window_analysis"


def test_phase8_shadow_runtime_observer_prioritizes_complex_trend_over_chart_token() -> None:
    prompt_type = _classify_prompt_type(
        '{"text":"Realiza un grafico de evolucion mensual del top 5 productos por ventas"}',
        {},
    )
    assert prompt_type == "trend_request"


def test_phase8_shadow_runtime_observer_does_not_overclassify_total_as_kpi(monkeypatch) -> None:
    monkeypatch.setattr(settings, "CANONICAL_SHADOW_TRAFFIC_MIRROR_ENABLED", True)
    monkeypatch.setattr(settings, "CANONICAL_SHADOW_TRAFFIC_MIRROR_TABULAR_ONLY", True)
    monkeypatch.setattr(settings, "CANONICAL_SHADOW_TRAFFIC_MIRROR_MAX_PLANS", 3)
    monkeypatch.setattr(
        "app.services.canonical_shadow_runtime_observer.run_canonical_shadow_query_for_uploaded_file",
        lambda **kwargs: object(),
    )
    monkeypatch.setattr(
        "app.services.canonical_shadow_runtime_observer.summarize_canonical_shadow_query_execution",
        lambda execution: {
            "readiness_grade": "pilot_candidate",
            "shadow_query_status": "query_executed",
            "successful_plan_count": 3,
            "executions": [
                {"status": "success", "chart_type": "bar_chart"},
                {"status": "success", "chart_type": "treemap"},
                {"status": "success", "chart_type": "line_chart"},
            ],
        },
    )
    monkeypatch.setattr(
        "app.services.canonical_shadow_runtime_observer._shadow_candidate_contract",
        lambda execution: {
            "dataset_mode": "flow",
            "time_axis": "fecha",
        },
    )

    summary = observe_canonical_shadow_runtime(
        task_id="task-4",
        file_id="file-4",
        prompt='{"text":"realiza un analisis de la categoria Electrónica. por cantidad vendida y Total venta"}',
        live_summary={
            "status": "completed",
            "chart_count": 2,
            "visual_types": ["pie_chart", "treemap"],
            "dataset_mode": "flow",
            "time_axis": "fecha",
            "live_duration_ms": 300,
        },
        uploaded_file_row={"file_name": "ventas.xlsx", "user_id": "00000000-0000-4000-8000-000000000001", "team_id": "00000000-0000-4000-8000-000000000002"},
        service_client=object(),
    )

    assert summary["observer_status"] == "observed"
    assert summary["prompt_type"] == "dimension_analysis"
    assert summary["requested_visual_family"] == "pie_chart"


def test_phase8_shadow_runtime_observer_uses_live_visual_for_generic_chart_request(monkeypatch) -> None:
    monkeypatch.setattr(settings, "CANONICAL_SHADOW_TRAFFIC_MIRROR_ENABLED", True)
    monkeypatch.setattr(settings, "CANONICAL_SHADOW_TRAFFIC_MIRROR_TABULAR_ONLY", True)
    monkeypatch.setattr(settings, "CANONICAL_SHADOW_TRAFFIC_MIRROR_MAX_PLANS", 3)
    monkeypatch.setattr(
        "app.services.canonical_shadow_runtime_observer.run_canonical_shadow_query_for_uploaded_file",
        lambda **kwargs: object(),
    )
    monkeypatch.setattr(
        "app.services.canonical_shadow_runtime_observer.summarize_canonical_shadow_query_execution",
        lambda execution: {
            "readiness_grade": "pilot_candidate",
            "shadow_query_status": "query_executed",
            "successful_plan_count": 1,
            "executions": [{"status": "success", "chart_type": "treemap"}],
        },
    )
    monkeypatch.setattr(
        "app.services.canonical_shadow_runtime_observer._shadow_candidate_contract",
        lambda execution: {
            "dataset_mode": "snapshot",
            "time_axis": "fecha_de_stock",
        },
    )

    summary = observe_canonical_shadow_runtime(
        task_id="task-5",
        file_id="file-5",
        prompt='{"text":"Dame un grafico por tipo de almacen"}',
        live_summary={
            "status": "completed",
            "chart_count": 1,
            "visual_types": ["treemap"],
            "dataset_mode": "snapshot",
            "time_axis": "fecha_de_stock",
            "live_duration_ms": 250,
        },
        uploaded_file_row={"file_name": "stock.xlsx", "user_id": "00000000-0000-4000-8000-000000000001", "team_id": "00000000-0000-4000-8000-000000000002"},
        service_client=object(),
    )

    assert summary["observer_status"] == "observed"
    assert summary["prompt_type"] == "chart_request"
    assert summary["requested_visual_family"] == "treemap"
