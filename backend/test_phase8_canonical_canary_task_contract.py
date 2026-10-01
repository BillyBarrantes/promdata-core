import pytest
import json
from types import SimpleNamespace

pytest.importorskip("celery")

from app.tasks.analysis_pipeline import orchestrator
import app.tasks.analysis_tasks as analysis_tasks


class _FakeQuery:
    def __init__(self, payload):
        self._payload = payload

    def select(self, *_args, **_kwargs):
        return self

    def update(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def single(self):
        return self

    def execute(self):
        return SimpleNamespace(data=self._payload)


class _FakeSupabase:
    def table(self, name):
        if name == "uploaded_files":
            return _FakeQuery(
                {
                    "id": "file-1",
                    "user_id": "user-1",
                    "team_id": "team-1",
                    "file_name": "ventas.csv",
                    "storage_path": "dash-uploads/u/file.xlsx",
                    "created_at": "2026-05-09T00:00:00+00:00",
                }
            )
        return _FakeQuery({})


class _CaptureUpdateQuery:
    def __init__(self, capture):
        self._capture = capture

    def update(self, payload):
        self._capture["payload"] = payload
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def execute(self):
        return SimpleNamespace(data=self._capture.get("payload"))


class _CaptureSupabase:
    def __init__(self):
        self.capture = {}

    def table(self, _name):
        return _CaptureUpdateQuery(self.capture)


class _GracefulFailSupabase:
    """Supabase fake para el contrato de fallo graceful: soporta los reads
    previos (metadata de task + uploaded_files) y captura el update final."""

    def __init__(self):
        self.update_capture = {}

    def table(self, name):
        if name == "uploaded_files":
            return _FakeQuery(
                {
                    "id": "file-1",
                    "user_id": "user-1",
                    "team_id": "team-1",
                    "file_name": "ventas.csv",
                    "storage_path": "dash-uploads/u/file.xlsx",
                    "created_at": "2026-05-09T00:00:00+00:00",
                }
            )
        if name == "analysis_tasks":
            return _GracefulFailTaskQuery(self.update_capture)
        return _FakeQuery({})


class _GracefulFailTaskQuery:
    def __init__(self, capture):
        self._capture = capture

    def select(self, *_args, **_kwargs):
        return self

    def update(self, payload):
        self._capture["payload"] = payload
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def single(self):
        return self

    def execute(self):
        return SimpleNamespace(data={"created_at": "2026-05-09T00:00:00+00:00", "user_id": "user-1"})


def test_canary_task_fails_gracefully_on_executor_error_without_legacy_fallback(monkeypatch):
    """Contrato vigente (commit 1e37b85e, phase-0 hardening): si el ejecutor
    canónico falla con un error real, la tarea se marca 'failed' con un
    mensaje amigable al usuario y NO hay fallback silencioso a legacy.

    Reemplaza al test obsoleto `test_canary_task_falls_back_to_legacy_on_executor_error`,
    que documentaba el contrato anterior (fallback automático a legacy).
    Fase 2 del Plan Maestro reintroducirá fallback GOBERNADO y medido;
    este test deberá actualizarse entonces.
    """
    legacy_calls = {}
    supabase_fake = _GracefulFailSupabase()

    monkeypatch.setattr(orchestrator.settings, "UNIVERSAL_TABULAR_PRODUCTION_EXECUTOR_ENABLED", False)
    monkeypatch.setattr(orchestrator, "get_supabase_client", lambda: supabase_fake)
    monkeypatch.setattr(orchestrator, "get_cached_analysis", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(orchestrator, "set_cached_analysis", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        orchestrator,
        "execute_canonical_tabular_canary_analysis",
        lambda **_: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    monkeypatch.setattr(
        analysis_tasks.perform_analysis_task,
        "run",
        lambda *args, **kwargs: legacy_calls.setdefault("call", {"args": args, "kwargs": kwargs}),
    )

    result = analysis_tasks.perform_analysis_task_universal_tabular.run(
        "task-1",
        "file-1",
        "Analiza ventas por canal",
        "token-1",
        runtime_route={"requested_runtime": "universal_tabular", "effective_runtime": "universal_tabular"},
    )

    # 1. Legacy NUNCA es invocado (no hay fallback silencioso).
    assert "call" not in legacy_calls, "Legacy fue invocado: fallback silencioso re-introducido"

    # 2. La tarea retorna 'failed'.
    assert result == "failed"

    # 3. La tarea queda marcada 'failed' con mensaje amigable al usuario.
    payload = supabase_fake.update_capture.get("payload")
    assert payload is not None, "La tarea no fue actualizada tras el error"
    assert payload["status"] == "failed"
    results_json = json.loads(payload["results_json"])
    assert "Error en el Análisis" in results_json["analysis"]
    assert results_json["chart_options"] == []
    assert "boom" in results_json["error_trace"]


def test_universal_tabular_task_uses_production_executor_and_async_canary(monkeypatch):
    captured = {}
    runtime_result = SimpleNamespace(
        status="completed",
        final_struct={"analysis": "ok", "chart_options": [{}], "traceability": {"runtime": "canonical_tabular_production"}},
        dataset_contract={"dataset_mode": "flow"},
        cleaning_notes=[],
        execution=SimpleNamespace(
            metadata={"candidate_id": "primary__sheet1"},
            prompt_strategy="production_semantic_translator",
        ),
    )

    monkeypatch.setattr(orchestrator.settings, "UNIVERSAL_TABULAR_PRODUCTION_EXECUTOR_ENABLED", True)
    monkeypatch.setattr(orchestrator.settings, "CANONICAL_SHADOW_TRAFFIC_MIRROR_ENABLED", True)
    monkeypatch.setattr(orchestrator, "get_supabase_client", lambda: _FakeSupabase())
    monkeypatch.setattr(orchestrator, "get_cached_analysis", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(orchestrator, "set_cached_analysis", lambda *_args, **_kwargs: None)
    def _fake_production_executor(**kwargs):
        captured["production_kwargs"] = kwargs
        return runtime_result

    monkeypatch.setattr(
        orchestrator,
        "execute_canonical_tabular_production_analysis",
        _fake_production_executor,
    )
    monkeypatch.setattr(
        orchestrator,
        "execute_canonical_tabular_canary_analysis",
        lambda **_: (_ for _ in ()).throw(AssertionError("canary must run only in background")),
    )
    monkeypatch.setattr(
        analysis_tasks.observe_canonical_tabular_canary_runtime_task,
        "delay",
        lambda *args: captured.setdefault("background_args", args),
    )
    monkeypatch.setattr(orchestrator, "track_analysis_completed", lambda **_: None)
    monkeypatch.setattr(orchestrator, "track_canary_runtime_execution_observed", lambda **_: None)

    result = analysis_tasks.perform_analysis_task_universal_tabular.run(
        "task-1",
        "file-1",
        "Realiza un gráfico de evolución mensual",
        "token-1",
        runtime_route={"requested_runtime": "universal_tabular", "effective_runtime": "universal_tabular"},
    )

    assert result == "completed"
    assert captured["production_kwargs"]["file_id"] == "file-1"
    assert captured["background_args"][:3] == (
        "task-1",
        "file-1",
        "Realiza un gráfico de evolución mensual",
    )


def test_payload_shedding_preserves_granular_arrow_when_snapshot_strip_is_enough(monkeypatch):
    sb = _CaptureSupabase()
    runtime_result = SimpleNamespace(
        status="completed",
        final_struct={
            "analysis": "ok",
            "snapshot_arrow": "S" * 5000,
            "arrow_data": "A" * 40,
            "chart_options": [{"title": {"text": "Chart"}, "granular_arrow": "G" * 40}],
        },
    )

    monkeypatch.setattr(orchestrator.settings, "UNIVERSAL_TABULAR_RESULT_SOFT_LIMIT_BYTES", 1000)

    analysis_tasks._save_analysis_task_result_with_payload_shedding(
        sb,
        "task-1",
        runtime_result,
    )

    saved = json.loads(sb.capture["payload"]["results_json"])
    assert "snapshot_arrow" not in saved
    assert "arrow_data" not in saved
    assert "granular_arrow" not in saved["chart_options"][0]
