"""Dispatch-core unit tests (issue #16, criterion 1) with deterministic doubles."""

from __future__ import annotations

from proxy import contract
from proxy.model import (
    STAGE_COMPLETED,
    STAGE_RECEIVED,
    STAGE_ROUTE_SELECTED,
    DispatchEvent,
    TaskRequest,
    TaskResult,
)

from support import (
    VALID_CLASSIFY_JSON,
    ScriptedBackend,
    build_gateway,
    make_agent,
    make_task,
)


def _request(**over):
    values = dict(tenant_id="acme", task_type="classify-route",
                  input={"input": "billing outage"})
    values.update(over)
    return TaskRequest(**values)


class TestDispatchSuccess:
    def test_typed_success(self):
        backend = ScriptedBackend().on(
            "deepseek",
            lambda c, i: backend.result("deepseek", VALID_CLASSIFY_JSON),
        )
        gateway, *_ = build_gateway(
            agent=make_agent(),
            task=make_task(),
            backend=backend,
        )
        result = gateway.dispatch("orchestrator", _request())
        assert result.served()
        assert result.outcome == contract.OUTCOME_SUCCESS
        assert result.content["route"] == "support"
        assert result.content["confidence"] == 0.9
        assert result.provider == "deepseek"
        assert result.tier == "LOW"
        assert result.input_tokens == 10
        assert result.output_tokens == 5
        assert result.record is not None
        assert result.record.outcome == contract.OUTCOME_SUCCESS

    def test_result_events_trace_pipeline(self):
        backend = ScriptedBackend().on(
            "deepseek",
            lambda c, i: backend.result("deepseek", VALID_CLASSIFY_JSON),
        )
        gateway, *_ = build_gateway(
            agent=make_agent(), task=make_task(), backend=backend
        )
        result = gateway.dispatch("orchestrator", _request())
        stages = [event.stage for event in result.events]
        assert stages[0] == STAGE_RECEIVED
        assert "agent_resolved" in stages
        assert "task_resolved" in stages
        assert STAGE_ROUTE_SELECTED in stages
        assert "guard" in stages
        assert "attempt" in stages
        assert stages[-1] == STAGE_COMPLETED
        assert all(isinstance(e, DispatchEvent) for e in result.events)

    def test_chooser_received_routing_context(self):
        backend = ScriptedBackend().on(
            "deepseek",
            lambda c, i: backend.result("deepseek", VALID_CLASSIFY_JSON),
        )
        from support import FakeChooser

        chooser = FakeChooser()
        gateway, *_ = build_gateway(
            agent=make_agent(), task=make_task(), backend=backend, chooser=chooser
        )
        gateway.dispatch("orchestrator", _request())
        assert chooser.calls[0]["task_class"] == "classify-route"
        assert chooser.calls[0]["tenant_id"] == "acme"
        assert chooser.calls[0]["agent_id"] == "orchestrator"


class TestDispatchFailures:
    def test_unknown_agent_is_explicit_failure_with_record(self):
        gateway, *_ = build_gateway(
            agent=make_agent(), task=make_task(),
            backend=ScriptedBackend(),
        )
        result = gateway.dispatch("nobody", _request())
        assert result.outcome == contract.OUTCOME_FAILED
        assert result.content is None  # never fabricate content
        assert result.record is not None
        assert result.record.outcome == contract.OUTCOME_FAILED

    def test_unknown_task_is_explicit_failure(self):
        gateway, *_ = build_gateway(
            agent=make_agent(), task=make_task(),
            backend=ScriptedBackend(),
        )
        result = gateway.dispatch("orchestrator", _request(task_type="ad-hoc-inline"))
        assert result.outcome == contract.OUTCOME_FAILED
        assert result.content is None

    def test_capability_denied(self):
        backend = ScriptedBackend()
        gateway, *_ = build_gateway(
            agent=make_agent("coder", capabilities=frozenset({"code-author"})),
            task=make_task(),
            backend=backend,
        )
        result = gateway.dispatch("coder", _request())
        assert result.outcome == contract.OUTCOME_DENIED
        assert result.content is None
        assert result.error and "capability" in result.error
        assert backend.calls == []  # no provider call on a boundary denial

    def test_no_healthy_route_explicit_failure(self):
        backend = ScriptedBackend().on(
            "deepseek",
            lambda c, i: backend.result("deepseek", VALID_CLASSIFY_JSON),
        )
        gateway, *_ = build_gateway(
            agent=make_agent(),
            task=make_task(),
            backend=backend,
            health={"deepseek": False, "openai": False, "ollama": False},
        )
        result = gateway.dispatch("orchestrator", _request())
        assert result.outcome == contract.OUTCOME_NO_HEALTHY_ROUTE
        assert result.content is None
        assert backend.calls == []  # never call an unhealthy provider


class TestDispatchStream:
    def test_stream_yields_events_then_result(self):
        backend = ScriptedBackend().on(
            "deepseek",
            lambda c, i: backend.result("deepseek", VALID_CLASSIFY_JSON),
        )
        gateway, *_ = build_gateway(
            agent=make_agent(), task=make_task(), backend=backend
        )
        items = list(gateway.dispatch_stream("orchestrator", _request()))
        assert isinstance(items[-1], TaskResult)
        assert items[-1].served()
        assert all(isinstance(item, DispatchEvent) for item in items[:-1])
        assert items[-1].events == tuple(items[:-1])
