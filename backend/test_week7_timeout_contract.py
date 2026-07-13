"""
Semana 7 — Día 4: Tests de contrato para Timeouts Celery (Día 2).
Valida que celery_app.conf tiene los límites correctos y que el orquestador
captura SoftTimeLimitExceeded y devuelve payload amigable.
"""
import pytest
import json
from unittest.mock import patch, MagicMock
from celery.exceptions import SoftTimeLimitExceeded

from app.celery_app import celery_app
from app.core.config import settings


class TestCeleryConfTimeouts:
    """Valida que la configuración de Celery tiene los límites inyectados."""

    def test_config_soft_time_limit_is_300(self):
        assert settings.CELERY_TASK_SOFT_TIME_LIMIT == 300

    def test_config_hard_time_limit_is_330(self):
        assert settings.CELERY_TASK_HARD_TIME_LIMIT == 330

    def test_celery_conf_soft_limit_matches_config(self):
        assert celery_app.conf.task_soft_time_limit == settings.CELERY_TASK_SOFT_TIME_LIMIT

    def test_celery_conf_hard_limit_matches_config(self):
        assert celery_app.conf.task_time_limit == settings.CELERY_TASK_HARD_TIME_LIMIT


class TestOrchestratorSoftTimeoutHandling:
    """Valida que el orquestador captura SoftTimeLimitExceeded y degrada elegantemente."""

    @patch("app.tasks.analysis_pipeline.orchestrator.emit_structured_log")
    @patch("app.tasks.analysis_pipeline.orchestrator.track_analysis_stage_latency_batch")
    @patch("app.tasks.analysis_pipeline.orchestrator.normalize_visual_id", return_value=None)
    @patch("app.tasks.analysis_pipeline.orchestrator.extract_prompt_visual_requests", return_value=[])
    @patch("app.tasks.analysis_pipeline.orchestrator._shadow_observer_classify_prompt_type", return_value="general")
    @patch("app.tasks.analysis_pipeline.orchestrator._shadow_observer_normalize_prompt", return_value="prompt")
    @patch("app.tasks.analysis_pipeline.orchestrator._compute_queue_wait_ms", return_value=None)
    @patch("app.tasks.analysis_pipeline.orchestrator.convert_keys_to_str", side_effect=lambda x: x)
    @patch("app.tasks.analysis_pipeline.orchestrator.track_canary_runtime_execution_fallback")
    @patch("app.tasks.analysis_pipeline.orchestrator.execute_canonical_tabular_production_analysis")
    @patch("app.tasks.analysis_pipeline.orchestrator.execute_canonical_tabular_canary_analysis")
    def test_universal_tabular_catches_timeout_and_returns_friendly_payload(
        self, mock_canary, mock_production, mock_track_fallback,
        mock_convert, mock_queue, mock_norm, mock_classify,
        mock_visual, mock_viz_id, mock_stage, mock_log,
    ):
        # Both executors raise SoftTimeLimitExceeded so the test works
        # regardless of the UNIVERSAL_TABULAR_PRODUCTION_EXECUTOR_ENABLED flag.
        mock_canary.side_effect = SoftTimeLimitExceeded("Time limit exceeded")
        mock_production.side_effect = SoftTimeLimitExceeded("Time limit exceeded")

        # Build a Supabase mock that supports chained calls
        mock_sb = MagicMock()

        # Mock the chained query responses
        mock_task_metadata = MagicMock()
        mock_task_metadata.data = {"created_at": "2026-01-01T00:00:00Z", "user_id": "user-123"}

        mock_uploaded_file = MagicMock()
        mock_uploaded_file.data = {
            "id": "file-123", "user_id": "user-123", "team_id": None,
            "file_name": "test.csv", "storage_path": "/test.csv",
            "created_at": "2026-01-01T00:00:00Z",
        }

        def _table_router(table_name):
            mock_table = MagicMock()
            if table_name == "analysis_tasks":
                mock_table.select.return_value.eq.return_value.single.return_value.execute.return_value = mock_task_metadata
                mock_table.update.return_value.eq.return_value.execute.return_value = MagicMock()
            elif table_name == "uploaded_files":
                mock_table.select.return_value.eq.return_value.single.return_value.execute.return_value = mock_uploaded_file
            return mock_table

        mock_sb.table.side_effect = _table_router

        with patch("app.tasks.analysis_pipeline.orchestrator.get_supabase_client", return_value=mock_sb):
            from app.tasks.analysis_pipeline.orchestrator import execute_universal_tabular_task

            status = execute_universal_tabular_task(
                task_id="task-timeout-test",
                file_id="file-123",
                prompt="prompt imposible",
                user_token="dummy-token",
            )

        # The orchestrator must return "timeout"
        assert status == "timeout"

        # Verify the structured log was emitted for the timeout
        mock_log.assert_any_call(
            "task_timeout_soft",
            level="warning",
            task_id="task-timeout-test",
            file_id="file-123",
        )
