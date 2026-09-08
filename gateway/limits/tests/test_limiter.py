"""LimitsEngine integration: cache-hit accounting, observe/enforce, rate
limiting, output throttle refusal/trim, and config wiring."""

from __future__ import annotations

import pytest

from limits.backpressure import DEGRADE
from limits.budget import BudgetController, BudgetMode, BudgetPolicy
from limits.cache import SemanticCache
from limits.config import CacheConfig, LimitsConfig
from limits.limiter import (
    KIND_BUDGET_EXCEEDED,
    KIND_CACHE_HIT,
    KIND_RATE_LIMITED,
    LimitsEngine,
    build_engine,
)
from limits.model import CACHE_HIT, ModelCallRequest
from limits.ratelimit import RateLimitPolicy, RateLimiter
from limits.throttle import OutputThrottle

PROMPT = "What is the fastest way to fail open?"


def _req(**overrides) -> ModelCallRequest:
    base = dict(
        tenant="acme",
        agent="coder",
        model_tier="LOW",
        task_type="summarize",
        prompt=PROMPT,
    )
    base.update(overrides)
    return ModelCallRequest(**base)


class TestCacheHitAccounting:
    def test_identical_call_is_zero_cost_cache_hit(self):
        engine = build_engine()
        calls: list[str] = []

        def provider(req):
            calls.append(req.prompt)
            return ("some summary", {"input_tokens": 10, "output_tokens": 5})

        first, first_result = engine.execute(_req(), provider)
        assert first.kind == "allow"
        assert first_result.metering.outcome == "provider"
        assert first_result.metering.zero_cost is False

        second, _second_result = engine.execute(_req(), provider)
        # The cache hit IS accounted in the metering record.
        assert second.kind == KIND_CACHE_HIT
        assert second.served() is True
        assert second.metering.outcome == CACHE_HIT
        assert second.metering.cached is True
        assert second.metering.zero_cost is True
        assert second.metering.cache_key is not None
        assert second.response == "some summary"
        assert len(calls) == 1  # provider bypassed on the hit

    def test_cache_stats_record_the_hit(self):
        engine = build_engine()
        engine.execute(_req(), lambda r: "answer")
        engine.execute(_req(), lambda r: "answer")
        stats = engine.cache.stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 1

    def test_different_prompt_is_not_a_hit(self):
        engine = build_engine()
        engine.execute(_req(), lambda r: "answer")
        decision, _ = engine.execute(
            _req(prompt="A completely different question."), lambda r: "answer"
        )
        assert decision.kind == "allow"

    def test_cacheable_false_skips_store(self):
        engine = LimitsEngine(cache=SemanticCache(), cacheable=False)
        engine.execute(_req(), lambda r: "answer")
        decision, _ = engine.execute(_req(), lambda r: "answer")
        assert decision.kind == "allow"  # nothing cached -> provider again
        assert engine.cache.stats()["entries"] == 0


class TestBudgetInEngine:
    def test_observe_budget_does_not_block_but_reports_would_block(self):
        engine = build_engine()
        engine.budget = BudgetController(
            default_policy=BudgetPolicy(cap_tokens=5, mode=BudgetMode.OBSERVE)
        )
        long_prompt = "long prompt " * 40  # ~400 chars -> ~100 estimated tokens
        # way over the tiny cap, but observe mode never blocks
        decision = engine.guard(_req(prompt=long_prompt), requested_tokens=500)
        assert decision.kind == "allow"
        assert decision.served() is True
        assert decision.budget is not None
        assert decision.budget.would_block is True
        # usage is still recorded when the call completes
        result = engine.complete(
            _req(prompt=long_prompt), "ok", input_tokens=10, output_tokens=0
        )
        assert result.metering.outcome == "provider"

    def test_enforce_budget_blocks_and_backpressures(self):
        engine = build_engine()
        engine.budget = BudgetController(
            default_policy=BudgetPolicy(cap_tokens=5, mode=BudgetMode.ENFORCE)
        )
        long_prompt = "long prompt " * 40
        decision = engine.guard(_req(prompt=long_prompt), requested_tokens=500)
        assert decision.kind == KIND_BUDGET_EXCEEDED
        assert decision.served() is False
        assert decision.backpressure is not None
        assert decision.backpressure.action == DEGRADE  # default strategy
        assert decision.metering.reason == "budget_exceeded"


class TestRateLimitInEngine:
    def test_rate_limited_call_is_explicit(self):
        engine = build_engine()
        engine.rate = RateLimiter(
            default_policy=RateLimitPolicy(limit=60, window_seconds=60, burst=1)
        )
        decision, _ = engine.execute(_req(prompt="first"), lambda r: "a")
        assert decision.kind == "allow"
        blocked, _ = engine.execute(_req(prompt="second"), lambda r: "b")
        assert blocked.kind == KIND_RATE_LIMITED
        assert blocked.served() is False
        assert blocked.metering.reason == "rate_limited"
        assert blocked.rate is not None and blocked.rate.reason == "rate_limited"


class TestOutputThrottleInEngine:
    def test_oversized_output_trimmed(self):
        engine = build_engine()
        decision, result = engine.execute(
            _req(task_type="summarize"),
            lambda r: "word " * 3000,  # ~3000 tokens, over the summarize cap
        )
        assert decision.kind == "allow"
        assert result.trimmed is True
        assert result.response is not None
        assert len(result.response) < len("word " * 3000)

    def test_refuse_mode_refuses_oversized_output_and_does_not_cache(self):
        engine = LimitsEngine(
            throttle=OutputThrottle(mode="refuse"),
            cache=SemanticCache(),
            budget=BudgetController(),
            rate=RateLimiter(),
        )
        decision, result = engine.execute(
            _req(task_type="summarize"),
            lambda r: ("word " * 3000, {"input_tokens": 4, "output_tokens": 3000}),
        )
        assert decision.kind == "allow"  # allowed to CALL; output then refused
        assert result.refused is True
        assert result.response is None
        assert result.metering.outcome == "refused"
        assert result.metering.reason == "output_cap_exceeded"
        # a refused response must NOT be cached as a success
        again, _ = engine.execute(_req(), lambda r: "fresh")
        assert again.kind == "allow"


class TestConfigWiring:
    def test_build_engine_uses_committed_default_config(self):
        engine = build_engine()
        assert engine.cache is not None
        assert isinstance(engine.budget, BudgetController)
        assert engine.throttle.max_tokens("summarize") < engine.throttle.max_tokens("architecture")

    def test_disabled_cache_skips_cache_layer(self):
        cfg = LimitsConfig(cache=CacheConfig(enabled=False))
        engine = build_engine(cfg)
        assert engine.cache is None
        engine.execute(_req(), lambda r: "answer")
        decision, _ = engine.execute(_req(), lambda r: "answer")
        assert decision.kind == "allow"  # no cache to hit


class TestGuardValidation:
    def test_metering_record_rejects_unknown_outcome(self):
        from limits.model import MeteringRecord

        with pytest.raises(ValueError):
            MeteringRecord(
                tenant="acme",
                agent="coder",
                model_tier="LOW",
                task_type=None,
                request_id="r",
                outcome="made-up",
            )

    def test_request_requires_identity(self):
        with pytest.raises(ValueError):
            ModelCallRequest(tenant="", agent="coder", model_tier="LOW")
