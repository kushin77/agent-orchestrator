"""Thin-handler tests (issue #16, criterion 1): REST envelope + streaming."""

from __future__ import annotations

import pytest

from proxy import contract
from proxy.handler import (
    GatewayHandler,
    outcome_status,
    parse_task_request,
    TaskBodyError,
)
from proxy.sinks import ListCallRecordSink

from support import (
    VALID_CLASSIFY_JSON,
    ScriptedBackend,
    build_gateway,
    make_agent,
    make_task,
)


def _handler(backend, agent=None, health=None):
    audit = ListCallRecordSink()
    gateway, *_ = build_gateway(
        agent=agent or make_agent(),
        task=make_task(),
        backend=backend,
        audit=audit,
        health=health,
    )
    return GatewayHandler(gateway)


BODY = {"tenantId": "acme", "taskType": "classify-route",
        "input": {"input": "billing outage"}}


class TestParseTaskRequest:
    def test_valid_body(self):
        request = parse_task_request(BODY)
        assert request.tenant_id == "acme"
        assert request.task_type == "classify-route"
        assert request.input == {"input": "billing outage"}
        assert request.stream is False

    def test_missing_tenant_id_refused(self):
        with pytest.raises(TaskBodyError):
            parse_task_request({"taskType": "classify-route"})

    def test_missing_task_type_refused(self):
        with pytest.raises(TaskBodyError):
            parse_task_request({"tenantId": "acme"})

    def test_non_object_body_refused(self):
        with pytest.raises(TaskBodyError):
            parse_task_request(["not", "an", "object"])


class TestOutcomeStatus:
    def test_status_mapping(self):
        assert outcome_status(contract.OUTCOME_SUCCESS) == 200
        assert outcome_status(contract.OUTCOME_CACHE_HIT) == 200
        assert outcome_status(contract.OUTCOME_BLOCKED) == 429
        assert outcome_status(contract.OUTCOME_RATE_LIMITED) == 429
        assert outcome_status(contract.OUTCOME_REFUSED) == 422
        assert outcome_status(contract.OUTCOME_CANNOT_ASSESS) == 422
        assert outcome_status(contract.OUTCOME_NO_HEALTHY_ROUTE) == 503
        assert outcome_status(contract.OUTCOME_DENIED) == 403
        assert outcome_status(contract.OUTCOME_FAILED) == 502


class TestHandleTask:
    def test_success_envelope(self):
        backend = ScriptedBackend().on(
            "deepseek", lambda c, i: backend.result("deepseek", VALID_CLASSIFY_JSON)
        )
        envelope = _handler(backend).handle_task("orchestrator", BODY)
        assert envelope["status"] == 200
        assert envelope["result"]["outcome"] == contract.OUTCOME_SUCCESS
        assert envelope["result"]["content"]["route"] == "support"
        assert envelope["record"]["outcome"] == contract.OUTCOME_SUCCESS

    def test_capability_denied_envelope(self):
        backend = ScriptedBackend()
        handler = _handler(
            backend, agent=make_agent("coder", capabilities=frozenset({"code-author"}))
        )
        envelope = handler.handle_task("coder", BODY)
        assert envelope["status"] == 403
        assert envelope["result"]["outcome"] == contract.OUTCOME_DENIED

    def test_no_healthy_route_envelope(self):
        backend = ScriptedBackend().on(
            "deepseek", lambda c, i: backend.result("deepseek", VALID_CLASSIFY_JSON)
        )
        handler = _handler(
            backend, health={"deepseek": False, "openai": False, "ollama": False}
        )
        envelope = handler.handle_task("orchestrator", BODY)
        assert envelope["status"] == 503
        assert envelope["result"]["outcome"] == contract.OUTCOME_NO_HEALTHY_ROUTE


class TestHandleTaskStream:
    def test_stream_yields_events_then_terminal_envelope(self):
        backend = ScriptedBackend().on(
            "deepseek", lambda c, i: backend.result("deepseek", VALID_CLASSIFY_JSON)
        )
        handler = _handler(backend)
        items = list(handler.handle_task_stream("orchestrator", BODY))
        assert all(isinstance(item, dict) for item in items)
        # incremental event envelopes first...
        events = [item for item in items if "event" in item]
        assert len(events) >= 5
        assert events[0]["event"]["stage"] == "received"
        # ...then one terminal envelope
        terminal = items[-1]
        assert terminal["status"] == 200
        assert terminal["result"]["outcome"] == contract.OUTCOME_SUCCESS
