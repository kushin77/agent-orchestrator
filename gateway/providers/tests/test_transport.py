"""Transport-double unit tests (issue #15, offline).

The adapter layer never opens sockets: ``RecordingTransport`` serves scripted
responses and records requests; ``FailingTransport`` raises transient errors
(optionally fail-N-then-succeed for recovery tests). These tests pin the
double semantics the provider/registry suites rely on.
"""

from __future__ import annotations

import pytest

from providers.errors import ProviderUnavailableError
from providers.transport import (
    FailingTransport,
    HttpResponse,
    RecordingTransport,
    now_ms,
)

from conftest import FAKE_KEY


def test_recording_transport_returns_scripted_response_in_order() -> None:
    first = HttpResponse(status=200, body="one")
    second = HttpResponse(status=200, body="two")
    transport = RecordingTransport([first, second])
    assert transport.request("POST", "https://x/1") is first
    assert transport.request("POST", "https://x/2") is second
    assert [r["url"] for r in transport.requests] == ["https://x/1", "https://x/2"]


def test_recording_transport_raises_scripted_error() -> None:
    boom = ProviderUnavailableError("down")
    transport = RecordingTransport([boom])
    with pytest.raises(ProviderUnavailableError):
        transport.request("POST", "https://x")


def test_recording_transport_fails_loudly_when_script_exhausted() -> None:
    transport = RecordingTransport()
    with pytest.raises(ProviderUnavailableError):
        transport.request("POST", "https://x")


def test_recording_transport_records_headers_and_body() -> None:
    transport = RecordingTransport([HttpResponse(status=200)])
    transport.request(
        "POST",
        "https://x",
        headers={"authorization": f"Bearer {FAKE_KEY}"},
        body={"model": "deepseek-chat"},
        timeout_ms=1000,
    )
    recorded = transport.requests[0]
    assert recorded["headers"]["authorization"] == f"Bearer {FAKE_KEY}"
    assert recorded["body"] == {"model": "deepseek-chat"}
    assert recorded["timeout_ms"] == 1000


def test_failing_transport_always_fails_by_default() -> None:
    transport = FailingTransport()
    for _ in range(3):
        with pytest.raises(ProviderUnavailableError):
            transport.request("POST", "https://x")
    assert transport.attempts == 3


def test_failing_transport_can_fail_then_succeed() -> None:
    transport = FailingTransport(failures=2, success_body='{"ok": true}')
    for _ in range(2):
        with pytest.raises(ProviderUnavailableError):
            transport.request("POST", "https://x")
    response = transport.request("POST", "https://x")
    assert response.status == 200
    assert response.body == '{"ok": true}'


def test_now_ms_returns_positive() -> None:
    assert now_ms() > 0.0
