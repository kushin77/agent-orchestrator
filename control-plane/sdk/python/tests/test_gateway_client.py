"""Gateway client tests — typed tasks + streaming (issue #41 AC1)."""

from __future__ import annotations

import pytest

from aosdk.auth import TokenSource
from aosdk.errors import (
    ConfigurationError,
    ScopeDeniedError,
    TaskNotServedError,
    UnauthorizedError,
)
from aosdk.gateway import GatewayClient
from aosdk.model import DispatchEvent, TaskResult

from _fakes import FakeGatewayBackend, mint_session_token


def _client(token: str, **kwargs):
    transport = kwargs.pop("transport", FakeGatewayBackend())
    return GatewayClient(transport, token_source=TokenSource(callback=lambda: token), **kwargs)


def _acme_token(**kwargs):
    return mint_session_token("acme", subject="alice", role="admin", **kwargs)


def test_typed_dispatch_success():
    client = _client(_acme_token())
    result = client.dispatch("coder-agent", "code-review-verdict", input_={"diff": "..."})
    assert isinstance(result, TaskResult)
    assert result.served() is True
    assert result.outcome == "success"
    assert result.agent_id == "coder-agent"
    assert result.task_type == "code-review-verdict"
    assert result.content == {"verdict": "approve", "summary": "change is sound", "confidence": 0.94}
    assert result.record is not None
    assert result.record.outcome == "success"


def test_run_returns_served_result():
    client = _client(_acme_token())
    result = client.run("reviewer-agent", "summarize", input_={"topic": "incident"})
    assert result.served()
    assert "billing outage" in result.content["summary"]


def test_cache_hit_is_served():
    client = _client(_acme_token())
    result = client.dispatch("cache-agent", "classify-route", input_={"text": "billing"})
    assert result.outcome == "cache_hit"
    assert result.served()
    assert result.content["label"] == "billing"


def test_non_served_outcomes_never_carry_content():
    client = _client(_acme_token())
    denied = client.dispatch("coder-agent", "classify-route", input_={"text": "x"})
    assert denied.outcome == "denied"
    assert denied.served() is False
    assert denied.content is None
    blocked = client.dispatch("blocked-agent", "classify-route", input_={"text": "x"})
    assert blocked.outcome == "blocked"
    assert blocked.content is None
    missing = client.dispatch("ghost-agent", "ghost-task")
    assert missing.outcome == "failed"
    assert missing.content is None


def test_run_raises_on_non_served():
    client = _client(_acme_token())
    with pytest.raises(TaskNotServedError) as excinfo:
        client.run("coder-agent", "classify-route", input_={"text": "x"})
    assert excinfo.value.outcome == "denied"
    assert excinfo.value.result.outcome == "denied"


def test_streaming_yields_events_then_terminal():
    client = _client(_acme_token())
    chunks = list(client.stream("coder-agent", "code-review-verdict", input_={"diff": "..."}))
    events = [c for c in chunks if isinstance(c, DispatchEvent)]
    terminals = [c for c in chunks if isinstance(c, TaskResult)]
    assert len(terminals) == 1
    assert terminals[0].served()
    assert len(events) == 6
    assert events[0].stage == "received"
    assert events[-1].stage == "completed"


def test_stream_requires_a_streaming_transport():
    class NonStreaming:
        def request(self, method, path, **kwargs):
            return {"status": 500}

    client = _client(_acme_token(), transport=NonStreaming())
    with pytest.raises(ConfigurationError):
        list(client.stream("coder-agent", "code-review-verdict"))


def test_cross_tenant_tenant_id_override_is_refused():
    token = _acme_token()
    client = _client(token, tenant_id="globex")
    with pytest.raises(ScopeDeniedError):
        client.dispatch("coder-agent", "code-review-verdict")


def test_missing_token_raises_configuration_error():
    client = GatewayClient(FakeGatewayBackend(), token_source=TokenSource(callback=lambda: None))
    with pytest.raises(ConfigurationError):
        client.dispatch("coder-agent", "code-review-verdict")


def test_expired_token_is_rejected_before_round_trip():
    client = _client(mint_session_token("acme", ttl=-60))
    with pytest.raises(UnauthorizedError):
        client.dispatch("coder-agent", "code-review-verdict")
