import pytest

from app.services import enterprise_telemetry as telemetry


def test_summarize_analysis_payload_extracts_enterprise_dimensions() -> None:
    final_struct = {
        "analysis": "Resumen ejecutivo",
        "chart_options": [
            {
                "visual_governance": {"applied_visual": "bar_chart"},
                "series": [{"type": "bar"}],
            },
            {
                "series": [{"type": "pie", "radius": ["40%", "70%"]}],
            },
        ],
        "data": [{"a": 1}, {"a": 2}],
        "recommendations": [{"title": "r1"}],
        "explainability": [{"title": "e1"}],
        "snapshot_row_count": 100,
        "arrow_data": "abc",
    }
    dataset_contract = {"dataset_mode": "flow", "confidence_score": 0.91}
    cleaning_notes = ["low entropy", "mixed content"]

    summary = telemetry.summarize_analysis_payload(
        final_struct=final_struct,
        dataset_contract=dataset_contract,
        cleaning_notes=cleaning_notes,
    )

    assert summary["chart_count"] == 2
    assert summary["visual_types"] == ["bar_chart", "donut_chart"]
    assert summary["data_row_count"] == 2
    assert summary["dataset_mode"] == "flow"
    assert summary["dataset_contract_confidence"] == 0.91
    assert summary["quality_note_count"] == 2
    assert summary["has_arrow_data"] is True


def test_summarize_saved_report_content_detects_visual_kind() -> None:
    content = {
        "type": "configuracion_echarts",
        "layout": {"x": 0, "y": 0, "w": 6, "h": 4},
        "option": {
            "visual_source_payload": {"rows": [{"name": "Lima", "value": 10}]},
            "series": [{"type": "pie", "radius": ["45%", "72%"]}],
        },
    }

    summary = telemetry.summarize_saved_report_content(content)

    assert summary["content_kind"] == "chart"
    assert summary["chart_count"] == 1
    assert summary["visual_types"] == ["donut_chart"]
    assert summary["has_layout"] is True
    assert summary["has_visual_source_payload"] is True


def test_track_analysis_completed_emits_enterprise_metrics(monkeypatch) -> None:
    captured: list[dict] = []

    def _fake_emit(event: str, level: str = "info", **fields):
        captured.append({"event": event, "level": level, **fields})

    monkeypatch.setattr(telemetry, "emit_structured_log", _fake_emit)
    monkeypatch.setattr(telemetry, "_persist_enterprise_metric_event", lambda **_: None)

    telemetry.track_analysis_completed(
        task_id="task-1",
        file_id="file-1",
        user_id="user-1",
        status="completed",
        duration_ms=1450,
        final_struct={
            "chart_options": [{"series": [{"type": "bar"}]}],
            "data": [{"x": 1}],
        },
        dataset_contract={"dataset_mode": "flow", "confidence_score": 0.88},
        cleaning_notes=["warn-1"],
    )

    metric_names = [item["metric_name"] for item in captured if item["event"] == "enterprise_metric_observed"]
    assert "analysis_completed" in metric_names
    assert "analysis_duration_ms" in metric_names
    assert "charts_generated" in metric_names
    assert "visual_type_generated" in metric_names
    assert "dataset_contract_confidence" in metric_names
    assert "dataset_quality_warnings" in metric_names


def test_track_file_preview_generated_emits_quality_metrics(monkeypatch) -> None:
    captured: list[dict] = []

    def _fake_emit(event: str, level: str = "info", **fields):
        captured.append({"event": event, "level": level, **fields})

    monkeypatch.setattr(telemetry, "emit_structured_log", _fake_emit)
    monkeypatch.setattr(telemetry, "_persist_enterprise_metric_event", lambda **_: None)

    telemetry.track_file_preview_generated(
        user_id="user-1",
        file_id="file-1",
        preview_payload={
            "row_count": 120,
            "column_count": 8,
            "selected_sheet": "Ventas",
            "quality_profile": {
                "health_score": 72,
                "health_status": "warning",
                "alert_count": 3,
            },
        },
    )

    metric_names = [item["metric_name"] for item in captured if item["event"] == "enterprise_metric_observed"]
    assert "file_preview_generated" in metric_names
    assert "preview_health_score" in metric_names
    assert "preview_alert_count" in metric_names
    assert "preview_health_status_observed" in metric_names


def test_track_canary_runtime_route_observed_emits_route_metrics(monkeypatch) -> None:
    captured: list[dict] = []

    def _fake_emit(event: str, level: str = "info", **fields):
        captured.append({"event": event, "level": level, **fields})

    monkeypatch.setattr(telemetry, "emit_structured_log", _fake_emit)
    monkeypatch.setattr(telemetry, "_persist_enterprise_metric_event", lambda **_: None)

    telemetry.track_canary_runtime_route_observed(
        task_id="task-1",
        file_id="file-1",
        user_id="user-1",
        team_id="team-1",
        file_name="ventas.xlsx",
        prompt_type="complete_analysis",
        requested_runtime="universal_tabular",
        effective_runtime="legacy",
        decision_mode="traffic_percent",
        decision_reason="functional_switch_disabled",
        health_status="dry_run",
        eligible=True,
        bucket_value=3,
        traffic_percent=5,
        allowlist_match=None,
        health_ready_for_functional_canary=False,
    )
    telemetry.track_canary_runtime_route_fallback(
        task_id="task-1",
        file_id="file-1",
        user_id="user-1",
        team_id="team-1",
        file_name="ventas.xlsx",
        prompt_type="complete_analysis",
        requested_runtime="universal_tabular",
        fallback_runtime="legacy",
        decision_reason="functional_switch_disabled",
    )

    metric_names = [item["metric_name"] for item in captured if item["event"] == "enterprise_metric_observed"]
    assert "canary_runtime_route_observed" in metric_names
    assert "canary_runtime_route_fallback" in metric_names


def test_track_canary_runtime_execution_metrics_emit(monkeypatch) -> None:
    captured: list[dict] = []

    def _fake_emit(event: str, level: str = "info", **fields):
        captured.append({"event": event, "level": level, **fields})

    monkeypatch.setattr(telemetry, "emit_structured_log", _fake_emit)
    monkeypatch.setattr(telemetry, "_persist_enterprise_metric_event", lambda **_: None)

    telemetry.track_canary_runtime_execution_observed(
        task_id="task-2",
        file_id="file-2",
        user_id="user-2",
        team_id="team-2",
        file_name="stock.xlsx",
        prompt_type="complete_analysis",
        execution_status="completed",
        candidate_id="primary__stock",
        prompt_strategy="macro_dimension_bundle",
        chart_count=2,
        duration_ms=950,
    )
    telemetry.track_canary_runtime_execution_fallback(
        task_id="task-2",
        file_id="file-2",
        user_id="user-2",
        team_id="team-2",
        file_name="stock.xlsx",
        prompt_type="complete_analysis",
        fallback_reason="canary_runtime_execution_error",
    )

    metric_names = [item["metric_name"] for item in captured if item["event"] == "enterprise_metric_observed"]
    assert "canary_runtime_execution_observed" in metric_names
    assert "canary_runtime_execution_duration_ms" in metric_names
    assert "canary_runtime_execution_fallback" in metric_names


def test_summarize_enterprise_telemetry_events_builds_executive_buckets() -> None:
    summary = telemetry.summarize_enterprise_telemetry_events(
        metric_rows=[
            {"metric_name": "analysis_requested", "metric_value": 2, "dimensions": {}},
            {"metric_name": "analysis_completed", "metric_value": 2, "dimensions": {}},
            {"metric_name": "analysis_duration_ms", "metric_value": 1200, "dimensions": {}},
            {"metric_name": "analysis_duration_ms", "metric_value": 800, "dimensions": {}},
            {"metric_name": "dataset_contract_confidence", "metric_value": 0.92, "dimensions": {}},
            {"metric_name": "dataset_quality_warnings", "metric_value": 3, "dimensions": {}},
            {"metric_name": "knowledge_question_executed", "metric_value": 4, "dimensions": {}},
            {"metric_name": "grounded_answer", "metric_value": 3, "dimensions": {}},
            {"metric_name": "insufficient_evidence_answer", "metric_value": 1, "dimensions": {}},
            {"metric_name": "preview_health_score", "metric_value": 84, "dimensions": {"health_status": "healthy"}},
            {"metric_name": "preview_health_status_observed", "metric_value": 1, "dimensions": {"health_status": "healthy"}},
            {"metric_name": "preview_health_status_observed", "metric_value": 1, "dimensions": {"health_status": "warning"}},
            {"metric_name": "preview_alert_count", "metric_value": 2, "dimensions": {"health_status": "warning"}},
            {"metric_name": "charts_generated", "metric_value": 3, "dimensions": {}},
            {"metric_name": "visual_type_generated", "metric_value": 1, "dimensions": {"visual_type": "bar_chart"}},
            {"metric_name": "visual_type_generated", "metric_value": 1, "dimensions": {"visual_type": "bar_chart"}},
            {"metric_name": "saved_visual_type", "metric_value": 1, "dimensions": {"visual_type": "donut_chart"}},
            {"metric_name": "connector_file_imported", "metric_value": 1, "dimensions": {"provider": "google_drive"}},
            {"metric_name": "cloud_sync_job_completed", "metric_value": 1, "dimensions": {"status": "succeeded"}},
            {"metric_name": "cloud_sync_job_completed", "metric_value": 1, "dimensions": {"status": "failed"}},
            {"metric_name": "cloud_sync_duration_ms", "metric_value": 4500, "dimensions": {"status": "succeeded"}},
            {
                "metric_name": "shadow_runtime_observed",
                "metric_value": 1,
                "created_at": "2026-05-08T12:00:00+00:00",
                "dimensions": {
                    "task_id": "t1",
                    "file_name": "ventas.xlsx",
                    "prompt_type": "chart_request",
                    "requested_visual_family": "bar_chart",
                    "alignment_grade": "high_alignment",
                    "shadow_query_status": "query_executed",
                    "mismatch_count": 0,
                    "live_primary_visual": "bar_chart",
                    "shadow_primary_visual": "bar_chart",
                },
            },
            {
                "metric_name": "shadow_runtime_observed",
                "metric_value": 1,
                "created_at": "2026-05-07T12:00:00+00:00",
                "dimensions": {
                    "task_id": "t2",
                    "file_name": "ventas.xlsx",
                    "prompt_type": "chart_request",
                    "requested_visual_family": "bar_chart",
                    "alignment_grade": "partial_alignment",
                    "shadow_query_status": "query_executed",
                    "mismatch_count": 1,
                    "live_primary_visual": "bar_chart",
                    "shadow_primary_visual": "treemap",
                },
            },
            {
                "metric_name": "shadow_runtime_observed",
                "metric_value": 1,
                "created_at": "2026-04-20T12:00:00+00:00",
                "dimensions": {
                    "task_id": "t3",
                    "file_name": "stock.xlsx",
                    "prompt_type": "complete_analysis",
                    "requested_visual_family": "line_chart",
                    "alignment_grade": "low_alignment",
                    "shadow_query_status": "partial_query_success",
                    "mismatch_count": 2,
                    "live_primary_visual": "line_chart",
                    "shadow_primary_visual": "bar_chart",
                },
            },
            {
                "metric_name": "shadow_runtime_duration_ms",
                "metric_value": 1100,
                "created_at": "2026-05-08T12:00:00+00:00",
                "dimensions": {
                    "task_id": "t1",
                    "file_name": "ventas.xlsx",
                    "prompt_type": "chart_request",
                    "requested_visual_family": "bar_chart",
                },
            },
            {
                "metric_name": "shadow_runtime_duration_ms",
                "metric_value": 1400,
                "created_at": "2026-05-07T12:00:00+00:00",
                "dimensions": {
                    "task_id": "t2",
                    "file_name": "ventas.xlsx",
                    "prompt_type": "chart_request",
                    "requested_visual_family": "bar_chart",
                },
            },
            {
                "metric_name": "shadow_runtime_duration_ms",
                "metric_value": 2100,
                "created_at": "2026-04-20T12:00:00+00:00",
                "dimensions": {
                    "task_id": "t3",
                    "file_name": "stock.xlsx",
                    "prompt_type": "complete_analysis",
                    "requested_visual_family": "line_chart",
                },
            },
            {
                "metric_name": "shadow_runtime_alignment",
                "metric_value": 1,
                "created_at": "2026-05-08T12:00:00+00:00",
                "dimensions": {
                    "task_id": "t1",
                    "file_name": "ventas.xlsx",
                    "prompt_type": "chart_request",
                    "requested_visual_family": "bar_chart",
                    "shadow_over_live_ratio": 0.8,
                },
            },
            {
                "metric_name": "canary_runtime_route_observed",
                "metric_value": 1,
                "created_at": "2026-05-08T12:00:00+00:00",
                "dimensions": {
                    "task_id": "c1",
                    "file_name": "ventas.xlsx",
                    "team_id": "team-1",
                    "prompt_type": "chart_request",
                    "requested_runtime": "universal_tabular",
                    "effective_runtime": "legacy",
                    "decision_mode": "traffic_percent",
                },
            },
            {
                "metric_name": "canary_runtime_route_fallback",
                "metric_value": 1,
                "created_at": "2026-05-08T12:00:00+00:00",
                "dimensions": {
                    "task_id": "c1",
                    "file_name": "ventas.xlsx",
                    "team_id": "team-1",
                    "prompt_type": "chart_request",
                    "requested_runtime": "universal_tabular",
                    "fallback_runtime": "legacy",
                    "decision_reason": "functional_switch_disabled",
                },
            },
            {
                "metric_name": "shadow_runtime_alignment",
                "metric_value": 1,
                "created_at": "2026-05-07T12:00:00+00:00",
                "dimensions": {
                    "task_id": "t2",
                    "file_name": "ventas.xlsx",
                    "prompt_type": "chart_request",
                    "requested_visual_family": "bar_chart",
                    "shadow_over_live_ratio": 0.9,
                },
            },
        ],
        window_days=30,
        telemetry_ready=True,
    )

    assert summary["telemetry_ready"] is True
    assert summary["usage"]["analyses_requested"] == 2
    assert summary["confidence"]["avg_dataset_contract_confidence"] == 0.92
    assert summary["confidence"]["grounded_answer_rate"] == 0.75
    assert summary["confidence"]["cloud_sync_success_rate"] == 0.5
    assert summary["confidence"]["preview_health_status_counts"]["healthy"] == 1
    assert summary["product"]["generated_visual_types"][0] == {"key": "bar_chart", "count": 2}
    assert summary["latency"]["avg_analysis_duration_ms"] == 1000.0
    assert summary["shadow_runtime"]["observed_count"] == 3
    assert summary["shadow_runtime"]["alignment_counts"]["high_alignment"] == 1
    assert summary["shadow_runtime"]["divergence_by_prompt_type"][0]["prompt_type"] == "complete_analysis"
    assert summary["shadow_runtime"]["divergence_by_prompt_type"][0]["divergence_score"] == 1.0
    assert summary["shadow_runtime"]["divergence_by_visual_family"][0]["requested_visual_family"] == "line_chart"
    assert summary["shadow_runtime"]["divergence_by_file_name"][0]["file_name"] == "stock.xlsx"
    assert summary["shadow_runtime"]["observed_file_count"] == 2
    assert summary["shadow_runtime"]["observed_task_count"] == 3
    assert len(summary["shadow_runtime"]["stability_by_window"]) == 3
    assert summary["shadow_runtime"]["stability_by_window"][0]["window_days"] == 1
    assert summary["canary_routing"]["observed_count"] == 1
    assert summary["canary_routing"]["requested_runtime_counts"]["universal_tabular"] == 1
    assert summary["canary_routing"]["effective_runtime_counts"]["legacy"] == 1
    assert summary["canary_routing"]["fallback_count"] == 1
    assert summary["canary_routing"]["distribution_by_prompt_type"][0]["prompt_type"] == "chart_request"


def run_assertions() -> None:
    test_summarize_analysis_payload_extracts_enterprise_dimensions()
    test_summarize_saved_report_content_detects_visual_kind()

    monkeypatch = pytest.MonkeyPatch()
    try:
        test_track_analysis_completed_emits_enterprise_metrics(monkeypatch)
        monkeypatch.undo()

        monkeypatch = pytest.MonkeyPatch()
        test_track_file_preview_generated_emits_quality_metrics(monkeypatch)
        monkeypatch.undo()

        monkeypatch = pytest.MonkeyPatch()
        test_track_canary_runtime_route_observed_emits_route_metrics(monkeypatch)
    finally:
        monkeypatch.undo()

    test_summarize_enterprise_telemetry_events_builds_executive_buckets()
