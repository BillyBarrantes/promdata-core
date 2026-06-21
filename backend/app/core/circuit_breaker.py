from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from typing import Any, TypeVar


T = TypeVar("T")


class GeminiCircuitOpenError(RuntimeError):
    """Raised when the Gemini circuit is open and calls must fail fast."""

    def __init__(self, recovery_seconds: int) -> None:
        self.recovery_seconds = max(int(recovery_seconds), 0)
        super().__init__(
            "Gemini está temporalmente saturado. "
            f"Reintenta en {self.recovery_seconds} segundos."
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
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.enabled = bool(enabled)
        self.failure_threshold = max(int(failure_threshold), 1)
        self.recovery_timeout_seconds = max(int(recovery_timeout_seconds), 1)
        self.half_open_max_calls = max(int(half_open_max_calls), 1)
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
        self.before_call()
        try:
            result = fn(*args, **kwargs)
        except Exception as error:
            self.record_failure(error)
            raise
        self.record_success()
        return result

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
