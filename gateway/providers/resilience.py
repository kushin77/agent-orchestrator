"""Retry/backoff + circuit breaker + graceful-degradation primitives (#15).

These primitives implement the harvested ollama circuit-breaker pattern
(CLOSED -> OPEN -> HALF_OPEN with failure threshold, recovery timeout and
success threshold) and the retry-with-exponential-backoff behaviour of the
llm-triage / gmail-agent / defragsuite providers, using only the standard
library.

Rules that keep the layer honest:

- Only TRANSIENT failures (``ProviderUnavailableError`` / timeout) are
  retried and counted against the circuit breaker.
- Non-transient failures (bad key/request, invalid output, bad schema) are
  raised immediately and never trip the breaker.
- An OPEN circuit fails fast (``CircuitOpenError``) - no call is attempted
  until the recovery timeout elapses (HALF_OPEN probe).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, TypeVar

from providers.errors import (
    CircuitOpenError,
    ProviderError,
    ProviderUnavailableError,
    RetryExhaustedError,
)

T = TypeVar("T")


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True)
class CircuitBreakerSettings:
    """Tuning for one circuit breaker (mirrors ollama defaults)."""

    failure_threshold: int = 5
    recovery_timeout_s: float = 60.0
    success_threshold: int = 2


class CircuitBreaker:
    """Three-state circuit breaker for one provider (per tenant+provider)."""

    def __init__(
        self,
        name: str,
        settings: CircuitBreakerSettings | None = None,
        *,
        now: Callable[[], float] | None = None,
    ) -> None:
        self.name = name
        self.settings = settings or CircuitBreakerSettings()
        self._now = now or time.monotonic
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_ts: float | None = None

    # -- state machine ------------------------------------------------------ #
    def allow_request(self) -> bool:
        """Whether a request may proceed right now (False = fast-fail OPEN)."""
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.HALF_OPEN:
            return True
        assert self.state == CircuitState.OPEN
        if self.last_failure_ts is None:
            return True
        if self._now() - self.last_failure_ts >= self.settings.recovery_timeout_s:
            self.state = CircuitState.HALF_OPEN
            self.success_count = 0
            return True
        return False

    def record_success(self) -> None:
        if self.state == CircuitState.HALF_OPEN:
            self.success_count += 1
            if self.success_count >= self.settings.success_threshold:
                self._transition(CircuitState.CLOSED)
                self.failure_count = 0
        elif self.state == CircuitState.CLOSED:
            self.failure_count = max(0, self.failure_count - 1)

    def record_failure(self) -> None:
        self.failure_count += 1
        self.last_failure_ts = self._now()
        if self.state == CircuitState.CLOSED:
            if self.failure_count >= self.settings.failure_threshold:
                self._transition(CircuitState.OPEN)
        elif self.state == CircuitState.HALF_OPEN:
            self.success_count = 0
            self._transition(CircuitState.OPEN)

    def _transition(self, new_state: CircuitState) -> None:
        self.state = new_state

    def reset(self) -> None:
        """Return to a pristine CLOSED state (operator/testing control)."""
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_ts = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self.failure_count,
            "success_count": self.success_count,
            "last_failure_ts": self.last_failure_ts,
        }


class CircuitBreakerManager:
    """Namespaced breaker store (one breaker per tenant:provider)."""

    def __init__(self) -> None:
        self._breakers: dict[str, CircuitBreaker] = {}

    def get_or_create(
        self,
        name: str,
        settings: CircuitBreakerSettings | None = None,
    ) -> CircuitBreaker:
        if name not in self._breakers:
            self._breakers[name] = CircuitBreaker(name, settings)
        return self._breakers[name]

    def reset_all(self) -> None:
        self._breakers.clear()

    def snapshots(self) -> dict[str, dict[str, Any]]:
        return {name: breaker.snapshot() for name, breaker in self._breakers.items()}


@dataclass(frozen=True)
class RetryPolicy:
    """Exponential backoff policy (deterministic unless jitter is enabled)."""

    max_attempts: int = 3
    base_delay_s: float = 0.1
    max_delay_s: float = 1.0
    factor: float = 2.0
    jitter: bool = False


def backoff_delay(policy: RetryPolicy, attempt_index: int) -> float:
    """Delay before the next attempt (attempt_index is 0-based)."""
    delay = policy.base_delay_s * (policy.factor ** attempt_index)
    delay = min(delay, policy.max_delay_s)
    if policy.jitter:
        import random

        delay = random.uniform(0.0, delay)
    return delay


@dataclass
class RetryOutcome:
    """Result of a successful ``call_with_retries`` run."""

    result: Any
    attempts: int
    latency_ms: float


def is_transient(exc: BaseException) -> bool:
    """Whether an exception is worth retrying / counts on the breaker."""
    return isinstance(exc, ProviderUnavailableError)


def call_with_retries(
    fn: Callable[[], T],
    *,
    retry: RetryPolicy,
    breaker: CircuitBreaker | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> RetryOutcome:
    """Run ``fn`` with exponential backoff and circuit-breaker protection.

    Mirrors the ollama ``breaker.call`` + retry composition: check the circuit
    before every attempt, record successes/failures, fast-fail when OPEN, and
    surface ``RetryExhaustedError`` (chaining the last failure) once the
    attempt budget is spent.
    """
    start = time.monotonic()
    last_exc: Exception | None = None
    for attempt in range(1, retry.max_attempts + 1):
        if breaker is not None and not breaker.allow_request():
            raise CircuitOpenError(
                f"circuit breaker OPEN for {breaker.name}; failing fast",
                provider=breaker.name,
            )
        try:
            result = fn()
        except ProviderError as exc:
            if is_transient(exc):
                last_exc = exc
                if breaker is not None:
                    breaker.record_failure()
                if attempt < retry.max_attempts:
                    sleep(backoff_delay(retry, attempt - 1))
                continue
            raise
        except Exception:  # unexpected failure: count it, do not retry blindly
            if breaker is not None:
                breaker.record_failure()
            raise
        if breaker is not None:
            breaker.record_success()
        latency_ms = (time.monotonic() - start) * 1000.0
        return RetryOutcome(result=result, attempts=attempt, latency_ms=latency_ms)
    assert last_exc is not None
    raise RetryExhaustedError(
        f"provider call failed after {retry.max_attempts} attempts",
        provider=getattr(last_exc, "provider", None),
    ) from last_exc
