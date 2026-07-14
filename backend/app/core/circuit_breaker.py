from __future__ import annotations

import math
import random
import threading
import time
from collections.abc import Callable
from typing import Any, TypeVar

from app.core.structured_logging import emit_structured_log


T = TypeVar("T")


class GeminiCircuitOpenError(RuntimeError):
    """Raised when the Gemini circuit is open and calls must fail fast."""

    def __init__(self, recovery_seconds: int) -> None:
        self.recovery_seconds = max(int(recovery_seconds), 0)
        super().__init__(
            "Gemini está temporalmente saturado. "
            f"Reintenta en {self.recovery_seconds} segundos."
        )


class GeminiQuotaExceededError(RuntimeError):
    """Raised when Gemini quota is exhausted after all retries are consumed.

    Signals a transient Vertex AI regional congestion — the user should retry
    in ~1 minute. This is NOT a code bug, it is a cloud provider capacity event.
    """

    def __init__(self, message: str = "") -> None:
        super().__init__(
            message
            or "Vertex AI experimenta congestión regional temporal. "
               "Por favor, reintente en 1 minuto."
        )


def is_recoverable_gemini_error(error: BaseException) -> bool:
    """Classify transient Gemini failures without depending on SDK internals."""

    text = f"{type(error).__name__} {error}".lower()
    recoverable_markers = (
        "429",
        "resource_exhausted",
        "quota",
        "rate limit",
        "rate_limit",
        "499",
        "cancelled",
        "canceled",
        "deadline",
        "timeout",
        "timed out",
        "503",
        "504",
        "temporarily unavailable",
        "service unavailable",
        "connection reset",
        "connection aborted",
    )
    return any(marker in text for marker in recoverable_markers)


class GeminiCircuitBreaker:
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(
        self,
        *,
        enabled: bool = True,
        failure_threshold: int = 5,
        recovery_timeout_seconds: int = 30,
        half_open_max_calls: int = 1,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 15.0,
        jitter: float = 0.5,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.enabled = bool(enabled)
        self.failure_threshold = max(int(failure_threshold), 1)
        self.recovery_timeout_seconds = max(int(recovery_timeout_seconds), 1)
        self.half_open_max_calls = max(int(half_open_max_calls), 1)
        self._max_retries = max(int(max_retries), 0)
        self._base_delay = max(float(base_delay), 0.1)
        self._max_delay = max(float(max_delay), self._base_delay)
        self._jitter = max(float(jitter), 0.0)
        self._clock = clock or time.monotonic
        self._lock = threading.RLock()
        self._state = self.CLOSED
        self._opened_at: float | None = None
        self._consecutive_failures = 0
        self._half_open_in_flight = 0

    @property
    def state(self) -> str:
        with self._lock:
            self._refresh_state_locked()
            return self._state

    @property
    def stats(self) -> dict[str, Any]:
        with self._lock:
            self._refresh_state_locked()
            return {
                "state": self._state,
                "consecutive_failures": self._consecutive_failures,
                "failure_threshold": self.failure_threshold,
                "recovery_timeout_seconds": self.recovery_timeout_seconds,
                "half_open_max_calls": self.half_open_max_calls,
                "opened_at": self._opened_at,
                "half_open_in_flight": self._half_open_in_flight,
            }

    def call(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Wraps a callable with circuit breaker protection + retry with backoff.

        Retry policy:
        - Only recoverable errors (429, timeout, 503, etc.) trigger retries.
        - Non-recoverable errors (ValueError, JSON parse, 400) fail immediately.
        - After exhausting all retries, the error is recorded as a real failure
          in the circuit breaker and re-raised.
        - If the circuit is OPEN, raises GeminiCircuitOpenError without retrying.
        """
        self.before_call()
        total_attempts = 1 + self._max_retries  # 1 original + N retries

        for attempt in range(1, total_attempts + 1):
            try:
                result = fn(*args, **kwargs)
                self.record_success()
                return result
            except Exception as error:
                is_last = attempt == total_attempts
                is_recoverable = is_recoverable_gemini_error(error)

                if not is_recoverable or is_last:
                    # Error no-recuperable o reintentos agotados → fallo real
                    self.record_failure(error)
                    # Si es 429/RESOURCE_EXHAUSTED y se agotaron reintentos,
                    # propagar excepción específica para graceful degradation.
                    if is_last and is_recoverable:
                        _error_text = str(error).lower()
                        if "429" in _error_text or "resource_exhausted" in _error_text:
                            emit_structured_log(
                                "vertex_ai_429_count",
                                level="warning",
                                error_snippet=str(error)[:200],
                                retries_exhausted=self._max_retries,
                            )
                            raise GeminiQuotaExceededError(str(error)[:200])
                    raise

                # ── Exponential Backoff + Jitter ──
                # TODO: Evaluar parseo de header Retry-After de Vertex AI
                #       para respetar el delay sugerido por Google en lugar
                #       del backoff propio. Requiere envolver el SDK.
                delay = min(
                    self._base_delay * (2 ** (attempt - 1))
                    + random.uniform(0, self._jitter),
                    self._max_delay,
                )

                # ── TRADE-OFF: time.sleep() vs SoftTimeLimitExceeded ──
                # Este sleep consume tiempo del budget del task de Celery.
                # Con max_retries=3, el peor caso acumula ~7.9s de backoff
                # (1+2+4 + jitter). Si un task tiene soft_time_limit=180s,
                # quedan ~172s para la lógica real — impacto marginal.
                # Si Celery lanza SoftTimeLimitExceeded durante el sleep,
                # la excepción interrumpe el sleep inmediatamente (Python
                # raises en el thread principal) y el orquestador ya la
                # captura con su handler de timeout. No hay riesgo de
                # task zombie.
                time.sleep(delay)

                emit_structured_log(
                    "gemini_retry_backoff",
                    level="info",
                    attempt=attempt,
                    max_retries=self._max_retries,
                    delay_seconds=round(delay, 2),
                    error_snippet=str(error)[:120],
                )

        # Unreachable — the loop always returns or raises. Defensive guard.
        raise RuntimeError("GeminiCircuitBreaker.call: unreachable code reached")  # pragma: no cover

    def before_call(self) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._refresh_state_locked()
            if self._state == self.OPEN:
                raise GeminiCircuitOpenError(self._remaining_recovery_seconds_locked())
            if self._state == self.HALF_OPEN:
                if self._half_open_in_flight >= self.half_open_max_calls:
                    raise GeminiCircuitOpenError(self._remaining_recovery_seconds_locked())
                self._half_open_in_flight += 1

    def record_success(self) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._state = self.CLOSED
            self._opened_at = None
            self._consecutive_failures = 0
            self._half_open_in_flight = max(self._half_open_in_flight - 1, 0)

    def record_failure(self, error: BaseException) -> None:
        if not self.enabled:
            return
        with self._lock:
            if self._state == self.HALF_OPEN:
                self._half_open_in_flight = max(self._half_open_in_flight - 1, 0)
            if not is_recoverable_gemini_error(error):
                return
            self._consecutive_failures += 1
            if self._state == self.HALF_OPEN or self._consecutive_failures >= self.failure_threshold:
                self._state = self.OPEN
                self._opened_at = self._clock()
                self._alert_slack_open(error)

    def _alert_slack_open(self, error: BaseException) -> None:
        try:
            from app.services.slack_alert import send_alert_background
            send_alert_background(
                "CRITICAL",
                "Gemini circuit breaker OPEN",
                {
                    "consecutive_failures": self._consecutive_failures,
                    "failure_threshold": self.failure_threshold,
                    "recovery_timeout_seconds": self.recovery_timeout_seconds,
                    "error": str(error)[:300],
                },
            )
        except Exception:
            pass

    def _refresh_state_locked(self) -> None:
        if self._state != self.OPEN or self._opened_at is None:
            return
        if self._clock() - self._opened_at >= self.recovery_timeout_seconds:
            self._state = self.HALF_OPEN
            self._half_open_in_flight = 0

    def _remaining_recovery_seconds_locked(self) -> int:
        if self._opened_at is None:
            return self.recovery_timeout_seconds
        elapsed = self._clock() - self._opened_at
        return max(int(math.ceil(self.recovery_timeout_seconds - elapsed)), 0)
