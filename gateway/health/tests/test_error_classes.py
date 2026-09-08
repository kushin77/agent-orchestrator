"""Error-class classification tests (issue #18 AC 1).

The monitor records error classes for every failure (timeout / rate_limit /
http_5xx / auth / output_invalid / unavailable / unknown) and surfaces them
in the health status and events. Classification is duck-typed so the gateway
can pass providers-layer exceptions without the health lane importing them.
"""

from __future__ import annotations

import pytest

from health import classify_error


class ProviderTimeoutError(Exception):
    pass


class ProviderRateLimitError(Exception):
    pass


class CircuitOpenError(Exception):
    pass


class RetryExhaustedError(Exception):
    pass


class AuthenticationError(Exception):
    pass


class InvalidApiKeyError(Exception):
    pass


class OutputValidationError(Exception):
    pass


class SchemaDefinitionError(Exception):
    pass


class ServerError(Exception):
    pass


class ConnectionResetError(Exception):
    pass


class MysteryProviderError(Exception):
    pass


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (ProviderTimeoutError("t"), "timeout"),
        (ProviderRateLimitError("r"), "rate_limit"),
        (CircuitOpenError("o"), "unavailable"),
        (RetryExhaustedError("e"), "unavailable"),
        (AuthenticationError("a"), "auth"),
        (InvalidApiKeyError("k"), "auth"),
        (OutputValidationError("v"), "output_invalid"),
        (SchemaDefinitionError("s"), "output_invalid"),
        (ServerError("5"), "http_5xx"),
        (ConnectionResetError("c"), "unavailable"),
        (MysteryProviderError("?"), "unknown"),
        (TimeoutError("builtin"), "timeout"),
    ],
)
def test_classify_error(exc: BaseException, expected: str) -> None:
    assert classify_error(exc) == expected


def test_record_failure_with_exception_classifies(monitor) -> None:
    monitor.record_failure(
        "deepseek", "deepseek-chat", error=ProviderTimeoutError("timed out")
    )
    monitor.record_failure(
        "deepseek", "deepseek-chat", error=AuthenticationError("bad key")
    )
    st = monitor.health_status("deepseek", "deepseek-chat")
    counts = st["window"]["error_counts"]
    assert counts["timeout"] == 1
    assert counts["auth"] == 1
    assert st["last_error_class"] == "auth"


def test_explicit_error_class_overrides_exception(monitor) -> None:
    monitor.record_failure(
        "deepseek",
        "deepseek-chat",
        error=ProviderTimeoutError("timed out"),
        error_class="http_5xx",  # caller's classification wins
    )
    st = monitor.health_status("deepseek", "deepseek-chat")
    assert st["window"]["error_counts"]["http_5xx"] == 1
    assert "timeout" not in st["window"]["error_counts"]


def test_unknown_error_class_when_none_given(monitor) -> None:
    monitor.record_failure("deepseek", "deepseek-chat")
    st = monitor.health_status("deepseek", "deepseek-chat")
    assert st["window"]["error_counts"]["unknown"] == 1


def test_error_class_surfaces_in_events(monitor, audit) -> None:
    for _ in range(5):
        monitor.record_success("deepseek", "deepseek-chat")
    monitor.record_failure("deepseek", "deepseek-chat", error_class="timeout")
    monitor.record_failure("deepseek", "deepseek-chat", error_class="timeout")
    degraded = [e for e in audit.events if e.kind == "degraded"]
    assert degraded
    assert degraded[0].error_class == "timeout"
