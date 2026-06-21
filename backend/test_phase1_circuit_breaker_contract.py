"""
FASE 1 — Día 2: contrato de Circuit Breaker para Gemini.

Valida que el circuito degrade rápido ante saturación de Gemini y que el
orquestador persista un estado rate_limited sin activar rutas legacy.
"""

import json
from types import SimpleNamespace

import pytest

from app.core.circuit_breaker import (
    GeminiCircuitBreaker,
    GeminiCircuitOpenError,
    is_recoverable_gemini_error,
)
from app.core.config import settings
from app.tasks.analysis_pipeline import orchestrator


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


class _FakeQuery:
    def __init__(self, table_name: str, owner: "_FakeSupabase") -> None:
        self._table_name = table_name
        self._owner = owner
        self._payload: dict | None = None

    def select(self, *_args, **_kwargs):
        return self

    def update(self, payload):
        self._payload = dict(payload or {})
        self._owner.updates.append({"table": self._table_name, "payload": self._payload})
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def single(self):
        return self

    def execute(self):
        if self._payload is not None:
            return SimpleNamespace(data=self._payload)
        if self._table_name == "analysis_tasks":
            return SimpleNamespace(data={"created_at": "2026-01-01T00:00:00Z", "user_id": "user-1"})
        if self._table_name == "uploaded_files":
            return SimpleNamespace(
                data={
                    "id": "file-1",
                    "user_id": "user-1",
                    "team_id": "team-1",
                    "file_name": "dataset.xlsx",
                    "storage_path": "dash-uploads/u/dataset.xlsx",
                    "created_at": "2026-01-01T00:00:00Z",
                }
            )
        return SimpleNamespace(data={})


class _FakeSupabase:
    def __init__(self) -> None:
        self.updates: list[dict] = []

    def table(self, table_name: str) -> _FakeQuery:
        return _FakeQuery(table_name, self)


def test_recoverable_gemini_errors_are_classified_without_sdk_dependency():
    assert is_recoverable_gemini_error(RuntimeError("429 RESOURCE_EXHAUSTED"))
    assert is_recoverable_gemini_error(RuntimeError("499 CANCELLED"))
    assert is_recoverable_gemini_error(TimeoutError("timed out"))
    assert not is_recoverable_gemini_error(ValueError("invalid JSON shape"))


def test_circuit_opens_after_threshold_and_fails_fast():
    clock = _FakeClock()
    circuit = GeminiCircuitBreaker(failure_threshold=2, recovery_timeout_seconds=30, clock=clock, max_retries=0)
    calls = {"count": 0}

    def _failing_call():
        calls["count"] += 1
        raise RuntimeError("429 RESOURCE_EXHAUSTED")

    for _ in range(2):
        with pytest.raises(RuntimeError):
            circuit.call(_failing_call)

    assert circuit.state == GeminiCircuitBreaker.OPEN
    with pytest.raises(GeminiCircuitOpenError):
        circuit.call(_failing_call)
    assert calls["count"] == 2


def test_circuit_half_open_success_closes_after_recovery_window():
    clock = _FakeClock()
    circuit = GeminiCircuitBreaker(failure_threshold=1, recovery_timeout_seconds=10, clock=clock)

    with pytest.raises(RuntimeError):
        circuit.call(lambda: (_ for _ in ()).throw(RuntimeError("503 service unavailable")))

    clock.advance(10)
    assert circuit.state == GeminiCircuitBreaker.HALF_OPEN
    assert circuit.call(lambda: "ok") == "ok"
    assert circuit.state == GeminiCircuitBreaker.CLOSED
    assert circuit.stats["consecutive_failures"] == 0


def test_circuit_half_open_failure_reopens():
    clock = _FakeClock()
    circuit = GeminiCircuitBreaker(failure_threshold=1, recovery_timeout_seconds=10, clock=clock)

    with pytest.raises(RuntimeError):
        circuit.call(lambda: (_ for _ in ()).throw(RuntimeError("429 quota")))

    clock.advance(10)
    with pytest.raises(RuntimeError):
        circuit.call(lambda: (_ for _ in ()).throw(RuntimeError("499 cancelled")))

    assert circuit.state == GeminiCircuitBreaker.OPEN


def test_non_recoverable_errors_do_not_open_the_circuit():
    circuit = GeminiCircuitBreaker(failure_threshold=1)

    with pytest.raises(ValueError):
        circuit.call(lambda: (_ for _ in ()).throw(ValueError("schema validation failed")))

    assert circuit.state == GeminiCircuitBreaker.CLOSED


def test_config_exposes_gemini_circuit_breaker_thresholds():
    assert settings.GEMINI_CIRCUIT_BREAKER_ENABLED is True
    assert settings.GEMINI_CIRCUIT_FAILURE_THRESHOLD >= 1
    assert settings.GEMINI_CIRCUIT_RECOVERY_TIMEOUT_SECONDS >= 1
    assert settings.GEMINI_CIRCUIT_HALF_OPEN_MAX_CALLS >= 1


def test_universal_orchestrator_persists_rate_limited_when_circuit_is_open(monkeypatch):
    fake_supabase = _FakeSupabase()

    monkeypatch.setattr(orchestrator, "get_supabase_client", lambda: fake_supabase)
    monkeypatch.setattr(orchestrator, "get_cached_analysis", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(orchestrator, "track_analysis_stage_latency_batch", lambda **_kwargs: None)
    monkeypatch.setattr(orchestrator, "emit_structured_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(orchestrator, "_compute_queue_wait_ms", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(orchestrator, "_shadow_observer_normalize_prompt", lambda prompt: prompt)
    monkeypatch.setattr(orchestrator, "_shadow_observer_classify_prompt_type", lambda *_args, **_kwargs: "chart_request")
    monkeypatch.setattr(orchestrator, "extract_prompt_visual_requests", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(orchestrator.settings, "UNIVERSAL_TABULAR_PRODUCTION_EXECUTOR_ENABLED", True)
    monkeypatch.setattr(orchestrator.settings, "CANONICAL_SHADOW_TRAFFIC_MIRROR_ENABLED", False)
    monkeypatch.setattr(
        orchestrator,
        "execute_canonical_tabular_production_analysis",
        lambda **_kwargs: (_ for _ in ()).throw(GeminiCircuitOpenError(12)),
    )

    result = orchestrator.execute_universal_tabular_task(
        task_id="task-1",
        file_id="file-1",
        prompt="analiza el archivo",
        user_token="token-1",
    )

    assert result == "rate_limited"
    final_update = fake_supabase.updates[-1]["payload"]
    assert final_update["status"] == "rate_limited"
    payload = json.loads(final_update["results_json"])
    assert "Servicio de IA saturado" in payload["analysis"]
