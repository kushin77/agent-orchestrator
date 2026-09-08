"""Token-bucket rate limiter: burst window, refill, and scope isolation."""

from __future__ import annotations

import pytest

from limits.ratelimit import RateLimitPolicy, RateLimiter, TokenBucket, rate_scope


def _limiter(clock, burst=3, limit=60, window=60.0, policies=None):
    return RateLimiter(
        default_policy=RateLimitPolicy(limit=limit, window_seconds=window, burst=burst),
        policies=policies,
        clock=clock,
    )


class TestTokenBucket:
    def test_burst_capacity(self, clock):
        bucket = TokenBucket(capacity=3, refill_per_second=1.0, clock=clock)
        assert bucket.consume()[0] is True
        assert bucket.consume()[0] is True
        assert bucket.consume()[0] is True
        allowed, remaining, wait = bucket.consume()
        assert allowed is False
        assert wait > 0

    def test_refill_over_time(self, clock):
        bucket = TokenBucket(capacity=3, refill_per_second=1.0, clock=clock)
        bucket.consume()  # 2 left
        bucket.consume()  # 1 left
        assert bucket.consume()[0] is True  # empty now
        assert bucket.consume()[0] is False
        clock.advance(1.0)  # one token refilled
        assert bucket.consume()[0] is True


class TestRateLimiterBurst:
    def test_burst_then_deny(self, clock):
        limiter = _limiter(clock)
        scope = rate_scope("acme", "coder", "LOW")
        results = [limiter.check(scope).allowed for _ in range(4)]
        assert results == [True, True, True, False]

    def test_denied_decision_carries_wait_and_reason(self, clock):
        limiter = _limiter(clock)
        scope = rate_scope("acme", "coder", "LOW")
        for _ in range(3):
            limiter.check(scope)
        decision = limiter.check(scope)
        assert decision.allowed is False
        assert decision.wait_seconds > 0
        assert decision.reason == "rate_limited"

    def test_recovers_after_refill(self, clock):
        limiter = _limiter(clock, burst=1, limit=60, window=60.0)
        scope = "acme::coder::LOW"
        assert limiter.check(scope).allowed is True
        assert limiter.check(scope).allowed is False
        clock.advance(1.0)  # refill 1 token (60/60 per second)
        assert limiter.check(scope).allowed is True


class TestIsolationAndReset:
    def test_scopes_isolated(self, clock):
        limiter = _limiter(clock)
        a = "acme::coder::LOW"
        b = "acme::coder::HIGH"
        for _ in range(3):
            limiter.check(a)
        assert limiter.check(a).allowed is False
        assert limiter.check(b).allowed is True

    def test_reset_restores_full_capacity(self, clock):
        limiter = _limiter(clock)
        scope = "acme::coder::LOW"
        for _ in range(3):
            limiter.check(scope)
        assert limiter.check(scope).allowed is False
        limiter.reset(scope)
        assert limiter.check(scope).allowed is True

    def test_policy_validation(self):
        with pytest.raises(ValueError):
            RateLimitPolicy(limit=0)
        with pytest.raises(ValueError):
            RateLimitPolicy(limit=10, window_seconds=0)
        with pytest.raises(ValueError):
            RateLimitPolicy(limit=10, burst=0)

    def test_status_reports_bucket(self, clock):
        limiter = _limiter(clock)
        status = limiter.status("acme::coder::LOW")
        assert status["capacity"] == 3
        assert status["tokens_remaining"] == 3
