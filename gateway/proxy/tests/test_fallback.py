"""Fallback-chain tests (issue #16, criterion 2): health + availability.

Negative guarantees verified here:

- primary unhealthy  -> the next healthy candidate serves (never a silent pass);
- all unhealthy      -> explicit ``no_healthy_route`` (no provider call);
- unavailable primary -> availability fallback to the next candidate;
- all candidates unavailable -> explicit ``failed``.
"""

from __future__ import annotations

from proxy import contract
from proxy.backend import BackendUnavailableError
from proxy.model import TaskRequest

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


def _default_gateway(backend, health=None):
    gateway, *_ = build_gateway(
        agent=make_agent(), task=make_task(), backend=backend, health=health
    )
    return gateway


class TestHealthFallback:
    def test_primary_unhealthy_serves_fallback_provider(self):
        backend = ScriptedBackend()
        backend.on("deepseek", lambda c, i: (_ for _ in ()).throw(
            AssertionError("deepseek must not be called")))
        backend.on("openai", lambda c, i: backend.result("openai", VALID_CLASSIFY_JSON))
        gateway = _default_gateway(backend, health={"deepseek": False})

        result = gateway.dispatch("orchestrator", _request())
        assert result.served()
        assert result.outcome == contract.OUTCOME_SUCCESS
        assert result.provider == "openai"
        called = [c.provider for c, _ in backend.calls]
        assert called == ["openai"]  # unhealthy primary never attempted

    def test_primary_unhealthy_falls_all_the_way_to_local(self):
        backend = ScriptedBackend()
        backend.on("ollama", lambda c, i: backend.result("ollama", VALID_CLASSIFY_JSON))
        gateway = _default_gateway(
            backend, health={"deepseek": False, "openai": False}
        )
        result = gateway.dispatch("orchestrator", _request())
        assert result.served()
        assert result.provider == "ollama"
        assert [c.provider for c, _ in backend.calls] == ["ollama"]

    def test_all_unhealthy_is_explicit_failure(self):
        backend = ScriptedBackend().on(
            "deepseek",
            lambda c, i: backend.result("deepseek", VALID_CLASSIFY_JSON),
        )
        gateway = _default_gateway(
            backend,
            health={"deepseek": False, "openai": False, "ollama": False},
        )
        result = gateway.dispatch("orchestrator", _request())
        assert result.outcome == contract.OUTCOME_NO_HEALTHY_ROUTE
        assert not result.served()
        assert result.content is None
        assert result.record.outcome == contract.OUTCOME_NO_HEALTHY_ROUTE
        assert backend.calls == []


class TestAvailabilityFallback:
    def test_unavailable_primary_falls_back(self):
        backend = ScriptedBackend()
        backend.on(
            "deepseek",
            lambda c, i: (_ for _ in ()).throw(
                BackendUnavailableError("deepseek down")),
        )
        backend.on("openai", lambda c, i: backend.result("openai", VALID_CLASSIFY_JSON))
        gateway = _default_gateway(backend)

        result = gateway.dispatch("orchestrator", _request())
        assert result.served()
        assert result.provider == "openai"
        assert [c.provider for c, _ in backend.calls] == ["deepseek", "openai"]

    def test_all_candidates_unavailable_is_explicit_failure(self):
        backend = ScriptedBackend()
        for provider in ("deepseek", "openai", "ollama"):
            backend.on(provider, lambda c, i, p=provider: (_ for _ in ()).throw(
                BackendUnavailableError(f"{p} down")))
        gateway = _default_gateway(backend)

        result = gateway.dispatch("orchestrator", _request())
        assert result.outcome == contract.OUTCOME_FAILED
        assert not result.served()
        assert result.content is None
        assert result.record.outcome == contract.OUTCOME_FAILED
        assert len(backend.calls) == 3  # every candidate attempted, all failed
