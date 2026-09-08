"""Retry/backoff + circuit-breaker tests (issue #15, criterion 3).

Negative controls (no-false-green): retry-then-success, retry-then-fail,
non-transient errors are never retried, and the circuit breaker transitions
CLOSED -> OPEN (failure threshold) -> HALF_OPEN (recovery timeout) ->
CLOSED (success threshold), reopening on a HALF_OPEN failure.
"""

from __future__ import annotations

import pytest

from providers.errors import (
    CircuitOpenError,
    OutputValidationError,
    ProviderUnavailableError,
    RetryExhaustedError,
)
from providers.resilience import (
    CircuitBreaker,
    CircuitBreakerSettings,
    CircuitState,
    RetryPolicy,
    backoff_delay,
    call_with_retries,
)


class _Clock:
    """Incrementable fake clock for deterministic breaker tests."""

    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def test_backoff_delay_increases_with_attempt() -> None:
    policy = RetryPolicy(base_delay_s=0.1, max_delay_s=1.0, factor=2.0)
    delays = [backoff_delay(policy, i) for i in range(3)]
    assert delays == pytest.approx([0.1, 0.2, 0.4])
    # Capped at max_delay.
    assert backoff_delay(RetryPolicy(base_delay_s=0.5, max_delay_s=1.0), 4) == 1.0


def test_success_on_first_attempt_records_one_attempt() -> None:
    calls = []

    def ok() -> str:
        calls.append(1)
        return "done"

    outcome = call_with_retries(ok, retry=RetryPolicy(max_attempts=3))
    assert outcome.result == "done"
    assert outcome.attempts == 1
    assert len(calls) == 1


def test_transient_failures_are_retried_then_succeed() -> None:
    calls = []
    sleeps: list[float] = []

    def flaky() -> str:
        calls.append(1)
        if len(calls) < 3:
            raise ProviderUnavailableError("boom")
        return "recovered"

    outcome = call_with_retries(
        flaky,
        retry=RetryPolicy(max_attempts=5, base_delay_s=0.01),
        sleep=sleeps.append,
    )
    assert outcome.result == "recovered"
    assert outcome.attempts == 3
    assert len(sleeps) == 2  # backoff between the three attempts


def test_retry_then_fail_raises_retry_exhausted() -> None:
    sleeps: list[float] = []

    def always_down() -> str:
        raise ProviderUnavailableError("down")

    with pytest.raises(RetryExhaustedError):
        call_with_retries(
            always_down,
            retry=RetryPolicy(max_attempts=3, base_delay_s=0.01),
            sleep=sleeps.append,
        )
    assert len(sleeps) == 2  # slept between attempts, then gave up


def test_non_transient_error_is_not_retried() -> None:
    calls = []
    sleeps: list[float] = []

    def invalid() -> str:
        calls.append(1)
        raise OutputValidationError("schema mismatch")

    with pytest.raises(OutputValidationError):
        call_with_retries(
            invalid,
            retry=RetryPolicy(max_attempts=3),
            sleep=sleeps.append,
        )
    assert len(calls) == 1  # fail closed immediately
    assert sleeps == []


# --------------------------------------------------------------------------- #
# Circuit breaker
# --------------------------------------------------------------------------- #


def test_breaker_opens_after_failure_threshold() -> None:
    clock = _Clock()
    breaker = CircuitBreaker(
        "acme:deepseek",
        CircuitBreakerSettings(failure_threshold=3, recovery_timeout_s=5.0),
        now=clock,
    )
    assert breaker.state == CircuitState.CLOSED
    for _ in range(3):
        breaker.record_failure()
    assert breaker.state == CircuitState.OPEN
    assert breaker.allow_request() is False


def test_breaker_recovers_to_half_open_after_timeout() -> None:
    clock = _Clock()
    breaker = CircuitBreaker(
        "acme:deepseek",
        CircuitBreakerSettings(failure_threshold=2, recovery_timeout_s=5.0),
        now=clock,
    )
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state == CircuitState.OPEN
    assert breaker.allow_request() is False
    clock.advance(6.0)
    assert breaker.allow_request() is True  # probe allowed
    assert breaker.state == CircuitState.HALF_OPEN


def test_breaker_half_open_successes_close_it() -> None:
    clock = _Clock()
    breaker = CircuitBreaker(
        "acme:deepseek",
        CircuitBreakerSettings(
            failure_threshold=2, recovery_timeout_s=5.0, success_threshold=2
        ),
        now=clock,
    )
    breaker.record_failure()
    breaker.record_failure()
    clock.advance(6.0)
    assert breaker.allow_request() is True  # -> HALF_OPEN
    breaker.record_success()
    assert breaker.state == CircuitState.HALF_OPEN  # needs success_threshold
    breaker.record_success()
    assert breaker.state == CircuitState.CLOSED
    assert breaker.allow_request() is True


def test_breaker_half_open_failure_reopens() -> None:
    clock = _Clock()
    breaker = CircuitBreaker(
        "acme:deepseek",
        CircuitBreakerSettings(failure_threshold=2, recovery_timeout_s=5.0),
        now=clock,
    )
    breaker.record_failure()
    breaker.record_failure()
    clock.advance(6.0)
    assert breaker.allow_request() is True  # -> HALF_OPEN
    breaker.record_failure()
    assert breaker.state == CircuitState.OPEN


def test_call_with_retries_fails_fast_when_circuit_open() -> None:
    clock = _Clock()
    breaker = CircuitBreaker(
        "acme:deepseek",
        CircuitBreakerSettings(failure_threshold=1, recovery_timeout_s=60.0),
        now=clock,
    )
    calls = []

    def down() -> str:
        calls.append(1)
        raise ProviderUnavailableError("down")

    # First call: single failure trips the breaker (threshold 1).
    with pytest.raises(RetryExhaustedError):
        call_with_retries(down, retry=RetryPolicy(max_attempts=1), breaker=breaker)
    assert breaker.state == CircuitState.OPEN

    # Second call: fast-fail - the function is not invoked again.
    with pytest.raises(CircuitOpenError):
        call_with_retries(down, retry=RetryPolicy(max_attempts=3), breaker=breaker)
    assert len(calls) == 1
