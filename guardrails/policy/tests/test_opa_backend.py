"""OPA integration-option tests (issue #26 acceptance #4).

:class:`OpaBackend` posts the action record to an OPA ``v1/data`` API through
an injectable transport; every failure path fails CLOSED to BLOCK.  The real
OPA binary/server is not required — the transport is faked offline.
"""

from __future__ import annotations

import pytest

from policy import DecisionLevel, LocalBackend, OpaBackend, PolicyBundle, PolicyEngine


class FakeTransport:
    """Records the request and returns a canned (status, body)."""

    def __init__(self, status: int = 200, body: dict | None = None, error: Exception | None = None):
        self.status = status
        self.body = body if body is not None else {"result": True}
        self.error = error
        self.calls: list[tuple] = []

    def request(self, method, url, payload, headers, timeout):
        self.calls.append((method, url, payload, headers, timeout))
        if self.error is not None:
            raise self.error
        return self.status, self.body


def _backend(transport: FakeTransport) -> OpaBackend:
    return OpaBackend("http://opa:8181", transport=transport, timeout=2.0)


def test_opa_backend_requires_endpoint():
    with pytest.raises(ValueError):
        OpaBackend("")


def test_opa_posts_action_record_to_v1_data():
    transport = FakeTransport(body={"result": True})
    backend = _backend(transport)
    backend.evaluate("model.call", subject="agent-1", tenant="acme", context={"x": 1})
    method, url, payload, headers, _ = transport.calls[0]
    assert method == "POST"
    assert url == "http://opa:8181/v1/data/agentorchestrator/guardrails/allow"
    assert headers["content-type"] == "application/json"
    assert payload["input"]["action"] == "model.call"
    assert payload["input"]["tenant"] == "acme"
    assert payload["input"]["context"] == {"x": 1}


def test_opa_result_bool_mapping():
    assert _backend(FakeTransport(body={"result": True})).evaluate("a").decision is DecisionLevel.LOG
    assert _backend(FakeTransport(body={"result": False})).evaluate("a").decision is DecisionLevel.BLOCK


def test_opa_result_token_string_mapping():
    for token, level in (("block", DecisionLevel.BLOCK), ("warn", DecisionLevel.WARN),
                         ("log", DecisionLevel.LOG)):
        backend = _backend(FakeTransport(body={"result": token}))
        assert backend.evaluate("a").decision is level


def test_opa_result_object_mapping():
    assert _backend(FakeTransport(body={"result": {"decision": "warn", "allow": True}})
                    ).evaluate("a").decision is DecisionLevel.WARN
    assert _backend(FakeTransport(body={"result": {"allow": False}})
                    ).evaluate("a").decision is DecisionLevel.BLOCK


def test_opa_transport_failure_fails_closed():
    transport = FakeTransport(error=RuntimeError("connection refused"))
    result = _backend(transport).evaluate("a")
    assert result.decision is DecisionLevel.BLOCK
    assert result.error is not None
    assert "connection refused" in result.error


def test_opa_non_200_fails_closed():
    result = _backend(FakeTransport(status=500, body={"result": True})).evaluate("a")
    assert result.decision is DecisionLevel.BLOCK
    assert "HTTP 500" in result.error


def test_opa_missing_result_fails_closed():
    result = _backend(FakeTransport(body={"other": 1})).evaluate("a")
    assert result.decision is DecisionLevel.BLOCK
    assert "missing 'result'" in result.error


def test_opa_unknown_decision_token_fails_closed():
    result = _backend(FakeTransport(body={"result": "allow"})).evaluate("a")
    assert result.decision is DecisionLevel.BLOCK
    assert "unknown decision token" in result.error


def test_opa_unparseable_result_fails_closed():
    result = _backend(FakeTransport(body={"result": [1, 2, 3]})).evaluate("a")
    assert result.decision is DecisionLevel.BLOCK
    assert "unparseable" in result.error


def test_local_backend_adapts_engine():
    engine = PolicyEngine(PolicyBundle())
    local = LocalBackend(engine)
    result = local.evaluate("anything")
    assert result.decision is DecisionLevel.BLOCK  # empty bundle -> uncovered fail-closed
    assert result.uncovered is True
