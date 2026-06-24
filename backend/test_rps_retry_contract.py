"""
Tests de contrato para el salto RPM → RPS.

Validan las 3 capas de defensa:
  1. Retry con Exponential Backoff + Jitter (circuit_breaker.py)
  2. Sentry Noise Filter (_before_send en sentry.py)
  3. Recalibración del Circuit Breaker (config.py thresholds)

Protocolo: Zero Deletion — estos tests son ADITIVOS.
"""
import time
from unittest.mock import patch

import pytest

from app.core.circuit_breaker import (
    GeminiCircuitBreaker,
    GeminiCircuitOpenError,
    is_recoverable_gemini_error,
)
from app.core.config import settings
from app.core.sentry import _before_send


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────

class _FakeClock:
    """Reloj determinista para tests de circuit breaker."""

    def __init__(self, start: float = 0.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


def _make_circuit(**overrides) -> GeminiCircuitBreaker:
    defaults = dict(
        failure_threshold=15,
        recovery_timeout_seconds=45,
        half_open_max_calls=2,
        max_retries=3,
        base_delay=0.001,   # ultra-rápido para tests
        max_delay=0.01,
        jitter=0.001,
    )
    defaults.update(overrides)
    return GeminiCircuitBreaker(**defaults)


# ─────────────────────────────────────────────────────────────────────
# CAPA 1: Retry con Exponential Backoff
# ─────────────────────────────────────────────────────────────────────

class TestRetryBackoff:
    """Tests de la capa de retry dentro de circuit_breaker.call()."""

    def test_retry_resolves_transient_429(self):
        """Falla 2 veces con 429, 3er intento succeeds → resultado OK."""
        circuit = _make_circuit()
        attempts = {"count": 0}

        def _flaky():
            attempts["count"] += 1
            if attempts["count"] <= 2:
                raise RuntimeError("429 RESOURCE_EXHAUSTED")
            return "success"

        result = circuit.call(_flaky)

        assert result == "success"
        assert attempts["count"] == 3
        assert circuit.state == GeminiCircuitBreaker.CLOSED
        assert circuit.stats["consecutive_failures"] == 0

    def test_retry_exhausted_records_real_failure(self):
        """3 reintentos fallidos → record_failure() se llama UNA vez, raise."""
        circuit = _make_circuit(max_retries=3)
        calls = {"count": 0}

        def _always_fail():
            calls["count"] += 1
            raise RuntimeError("429 RESOURCE_EXHAUSTED")

        with pytest.raises(RuntimeError, match="429"):
            circuit.call(_always_fail)

        # 1 original + 3 reintentos = 4 intentos totales
        assert calls["count"] == 4
        # Solo 1 fallo real registrado en el circuit breaker
        assert circuit.stats["consecutive_failures"] == 1

    def test_non_recoverable_error_fails_immediately(self):
        """Un ValueError pasa directo sin retry."""
        circuit = _make_circuit(max_retries=3)
        calls = {"count": 0}

        def _bad_schema():
            calls["count"] += 1
            raise ValueError("invalid JSON shape")

        with pytest.raises(ValueError, match="invalid JSON"):
            circuit.call(_bad_schema)

        # Solo 1 intento — sin reintentos
        assert calls["count"] == 1
        # ValueError no es recoverable → no incrementa consecutive_failures
        assert circuit.stats["consecutive_failures"] == 0

    def test_jitter_adds_randomness_to_delay(self):
        """Los delays medidos tienen varianza > 0 (no son determinísticos)."""
        circuit = _make_circuit(
            max_retries=2,
            base_delay=0.01,
            max_delay=1.0,
            jitter=0.05,
        )

        measured_delays: list[float] = []

        original_sleep = time.sleep

        def _capturing_sleep(seconds):
            measured_delays.append(seconds)
            # No dormimos realmente en tests

        def _always_fail():
            raise RuntimeError("429 RESOURCE_EXHAUSTED")

        with patch("app.core.circuit_breaker.time.sleep", side_effect=_capturing_sleep):
            with pytest.raises(RuntimeError):
                circuit.call(_always_fail)

        # Con max_retries=2, hay 2 sleeps (retry #1 y #2)
        assert len(measured_delays) == 2
        # Los delays deben ser > 0 (backoff real)
        assert all(d > 0 for d in measured_delays)
        # Los delays deben ser distintos (jitter los diferencia)
        # Con exponential backoff, el 2do delay base es mayor que el 1ro
        assert measured_delays[1] > measured_delays[0]

    def test_circuit_open_skips_retry(self):
        """Si el circuit está OPEN, GeminiCircuitOpenError sin intentar."""
        clock = _FakeClock()
        circuit = _make_circuit(
            failure_threshold=1,
            max_retries=0,
            clock=clock,
        )
        calls = {"count": 0}

        # Abrir el circuit con 1 fallo
        def _fail():
            calls["count"] += 1
            raise RuntimeError("429 RESOURCE_EXHAUSTED")

        with pytest.raises(RuntimeError):
            circuit.call(_fail)

        assert circuit.state == GeminiCircuitBreaker.OPEN
        calls["count"] = 0

        # Ahora cualquier llamada debe fallar INMEDIATAMENTE con CircuitOpenError
        with pytest.raises(GeminiCircuitOpenError):
            circuit.call(_fail)

        # La función NUNCA fue invocada — el circuit la bloqueó
        assert calls["count"] == 0


# ─────────────────────────────────────────────────────────────────────
# CAPA 2: Sentry Noise Filter
# ─────────────────────────────────────────────────────────────────────

class TestSentryNoiseFilter:
    """Tests del _before_send hook en sentry.py."""

    def test_sentry_filters_429_as_warning(self):
        """Un evento 429 es rebajado a 'warning' con fingerprint agrupado."""
        event = {"level": "error"}
        hint = {
            "exc_info": (
                RuntimeError,
                RuntimeError("429 RESOURCE_EXHAUSTED"),
                None,
            )
        }

        result = _before_send(event, hint)

        assert result is not None
        assert result["level"] == "warning"
        assert result["fingerprint"] == ["gemini-429-exhausted"]

    def test_sentry_filters_circuit_open_as_warning(self):
        """GeminiCircuitOpenError se rebaja a 'warning' con fingerprint propio."""
        error = GeminiCircuitOpenError(45)
        event = {"level": "error"}
        hint = {
            "exc_info": (type(error), error, None)
        }

        result = _before_send(event, hint)

        assert result is not None
        assert result["level"] == "warning"
        assert result["fingerprint"] == ["gemini-circuit-open"]

    def test_sentry_filters_transient_errors(self):
        """503, timeout, cancelled → warning con fingerprint transient."""
        for error_msg in ["503 Service Unavailable", "timed out", "499 CANCELLED"]:
            event = {"level": "error"}
            hint = {"exc_info": (RuntimeError, RuntimeError(error_msg), None)}

            result = _before_send(event, hint)

            assert result["level"] == "warning", f"Falló para: {error_msg}"
            assert result["fingerprint"] == ["gemini-transient-error"], f"Falló para: {error_msg}"

    def test_sentry_passes_real_errors(self):
        """Un TypeError no es filtrado ni rebajado."""
        event = {"level": "error"}
        hint = {
            "exc_info": (
                TypeError,
                TypeError("'NoneType' object is not subscriptable"),
                None,
            )
        }

        result = _before_send(event, hint)

        assert result is not None
        assert result["level"] == "error"  # NO fue rebajado
        assert "fingerprint" not in result  # NO fue agrupado


# ─────────────────────────────────────────────────────────────────────
# CAPA 3: Recalibración del Circuit Breaker
# ─────────────────────────────────────────────────────────────────────

class TestRPSThresholds:
    """Tests de los thresholds recalibrados para 2×20 workers."""

    def test_circuit_breaker_rps_thresholds(self):
        """failure_threshold=15, recovery=45, half_open=2."""
        assert settings.GEMINI_CIRCUIT_FAILURE_THRESHOLD == 15
        assert settings.GEMINI_CIRCUIT_RECOVERY_TIMEOUT_SECONDS == 45
        assert settings.GEMINI_CIRCUIT_HALF_OPEN_MAX_CALLS == 2

    def test_retry_config_defaults(self):
        """Retry settings tienen los valores del plan RPS."""
        assert settings.GEMINI_RETRY_MAX_RETRIES == 3
        assert settings.GEMINI_RETRY_BASE_DELAY_SECONDS == 1.0
        assert settings.GEMINI_RETRY_MAX_DELAY_SECONDS == 15.0
        assert settings.GEMINI_RETRY_JITTER_SECONDS == 0.5
