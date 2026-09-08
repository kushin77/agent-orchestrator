"""Cost/capacity guard tests (issue #16, criterion 3).

Context/token caps per tenant+agent are enforced at the gateway via the
injected limits facade (issue #19), never by the model.  Negative guarantees:

- an exhausted enforce token budget -> explicit ``blocked`` (backpressure),
  no provider call, never a silent success;
- a rate-limit denial -> explicit ``rate_limited``;
- an identical cached prompt -> zero-cost ``cache_hit`` (accounted);
- output-throttle refusal -> explicit ``refused``.
"""

from __future__ import annotations

import pytest

from proxy import contract
from proxy.model import TaskRequest

from support import (
    VALID_CLASSIFY_JSON,
    ScriptedBackend,
    build_gateway,
    make_agent,
    make_long_task,
)


def _request(**over):
    values = dict(tenant_id="acme", task_type="classify-route",
                  input={"input": "billing outage"})
    values.update(over)
    return TaskRequest(**values)


_backend_holder = None


@pytest.fixture(autouse=True)
def _fresh_backend_holder():
    global _backend_holder
    _backend_holder = ScriptedBackend()
    _backend_holder.on(
        "deepseek", lambda c, i: _backend_holder.result("deepseek", VALID_CLASSIFY_JSON)
    )
    yield
    _backend_holder = None


def _build_with_limits(engine, task=None):
    return build_gateway(
        agent=make_agent(),
        task=task or make_long_task(),
        backend=_backend_holder,
        limits=engine,
    )[0]


class TestBudgetEnforcement:
    def test_budget_exhausted_blocks_never_silent(self):
        from limits.budget import BudgetController, BudgetMode, BudgetPolicy
        from limits.limiter import LimitsEngine

        engine = LimitsEngine(
            budget=BudgetController(
                default_policy=BudgetPolicy(cap_tokens=50, mode=BudgetMode.ENFORCE)
            )
        )
        gateway = _build_with_limits(engine)
        result = gateway.dispatch("orchestrator", _request())
        assert result.outcome == contract.OUTCOME_BLOCKED
        assert not result.served()
        assert result.content is None
        assert result.record.outcome == contract.OUTCOME_BLOCKED
        assert "budget" in (result.error or "")
        assert _backend_holder.calls == []  # the provider was never called

    def test_budget_allows_under_cap(self):
        from limits.budget import BudgetController, BudgetMode, BudgetPolicy
        from limits.limiter import LimitsEngine

        engine = LimitsEngine(
            budget=BudgetController(
                default_policy=BudgetPolicy(cap_tokens=1_000_000, mode=BudgetMode.ENFORCE)
            )
        )
        gateway = _build_with_limits(engine)
        result = gateway.dispatch("orchestrator", _request())
        assert result.served()
        assert result.outcome == contract.OUTCOME_SUCCESS


class TestRateLimit:
    def test_rate_limited_is_explicit(self):
        from limits.limiter import LimitsEngine
        from limits.ratelimit import RateLimitPolicy, RateLimiter, rate_scope

        engine = LimitsEngine(
            rate=RateLimiter(
                default_policy=RateLimitPolicy(limit=1, window_seconds=60, burst=1)
            )
        )
        engine.rate.check(rate_scope("acme", "orchestrator", "LOW"))  # burn the token
        gateway = _build_with_limits(engine)
        result = gateway.dispatch("orchestrator", _request())
        assert result.outcome == contract.OUTCOME_RATE_LIMITED
        assert not result.served()
        assert result.content is None
        assert _backend_holder.calls == []


class TestCacheHit:
    def test_second_identical_dispatch_is_zero_cost_cache_hit(self):
        from limits.cache import SemanticCache
        from limits.limiter import LimitsEngine

        engine = LimitsEngine(cache=SemanticCache())
        gateway = _build_with_limits(engine)
        first = gateway.dispatch("orchestrator", _request())
        assert first.outcome == contract.OUTCOME_SUCCESS
        second = gateway.dispatch("orchestrator", _request())
        assert second.outcome == contract.OUTCOME_CACHE_HIT
        assert second.provider is None  # no provider call on a cache hit
        assert second.served()
        assert second.content["route"] == "support"  # previously-validated typed output
        assert second.record.outcome == contract.OUTCOME_CACHE_HIT
        # the second dispatch never touched the provider backend
        assert len(_backend_holder.calls) == 1


class TestOutputThrottle:
    def test_output_refused_is_explicit(self):
        from limits.limiter import LimitsEngine
        from limits.throttle import OutputThrottle

        engine = LimitsEngine(
            throttle=OutputThrottle(caps={}, default_cap=1, mode="refuse")
        )
        gateway = _build_with_limits(engine)
        result = gateway.dispatch("orchestrator", _request())
        assert result.outcome == contract.OUTCOME_REFUSED
        assert not result.served()
        assert result.content is None
        assert "throttle" in (result.error or "")
