import pytest
from types import SimpleNamespace

pytest.importorskip("celery")

from app.tasks import analysis_tasks


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


def test_canary_task_falls_back_to_legacy_on_executor_error(monkeypatch):
    captured = {}

    monkeypatch.setattr(analysis_tasks.settings, "UNIVERSAL_TABULAR_PRODUCTION_EXECUTOR_ENABLED", False)
    monkeypatch.setattr(analysis_tasks, "get_supabase_client", lambda: _FakeSupabase())
    monkeypatch.setattr(
        analysis_tasks,
        "execute_canonical_tabular_canary_analysis",
        lambda **_: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    monkeypatch.setattr(
        analysis_tasks.perform_analysis_task,
        "run",
        lambda task_id, file_id, prompt, user_token, runtime_route=None: captured.setdefault(
            "call",
            {
                "task_id": task_id,
                "file_id": file_id,
                "prompt": prompt,
                "user_token": user_token,
                "runtime_route": runtime_route,
            },
        )
        or "completed",
    )

    result = analysis_tasks.perform_analysis_task_universal_tabular.run(
        "task-1",
        "file-1",
        "Analiza ventas por canal",
        "token-1",
        runtime_route={"requested_runtime": "universal_tabular", "effective_runtime": "universal_tabular"},
    )

    assert captured["call"]["runtime_route"]["effective_runtime"] == "legacy"
    assert captured["call"]["runtime_route"]["decision_reason"] == "canary_runtime_execution_error"
    assert result["runtime_route"]["effective_runtime"] == "legacy"


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

    monkeypatch.setattr(analysis_tasks.settings, "UNIVERSAL_TABULAR_PRODUCTION_EXECUTOR_ENABLED", True)
    monkeypatch.setattr(analysis_tasks.settings, "CANONICAL_SHADOW_TRAFFIC_MIRROR_ENABLED", True)
    monkeypatch.setattr(analysis_tasks, "get_supabase_client", lambda: _FakeSupabase())
    def _fake_production_executor(**kwargs):
        captured["production_kwargs"] = kwargs
        return runtime_result

    monkeypatch.setattr(
        analysis_tasks,
        "execute_canonical_tabular_production_analysis",
        _fake_production_executor,
    )
    monkeypatch.setattr(
        analysis_tasks,
        "execute_canonical_tabular_canary_analysis",
        lambda **_: (_ for _ in ()).throw(AssertionError("canary must run only in background")),
    )
    monkeypatch.setattr(
        analysis_tasks.observe_canonical_tabular_canary_runtime_task,
        "delay",
        lambda *args: captured.setdefault("background_args", args),
    )
    monkeypatch.setattr(analysis_tasks, "track_analysis_completed", lambda **_: None)
    monkeypatch.setattr(analysis_tasks, "track_canary_runtime_execution_observed", lambda **_: None)

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

    monkeypatch.setattr(analysis_tasks.settings, "UNIVERSAL_TABULAR_RESULT_SOFT_LIMIT_BYTES", 1000)

    analysis_tasks._save_analysis_task_result_with_payload_shedding(
        sb,
        "task-1",
        runtime_result,
    )

    saved = analysis_tasks.json.loads(sb.capture["payload"]["results_json"])
    assert "snapshot_arrow" not in saved
    assert saved["arrow_data"] == "A" * 40
    assert saved["chart_options"][0]["granular_arrow"] == "G" * 40
