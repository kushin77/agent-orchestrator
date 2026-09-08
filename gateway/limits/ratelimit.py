"""Token-bucket rate limiter keyed by scope (per tenant/agent/model tier).

Implements the token-bucket algorithm from the cannibalized fleet rate
limiter (leaderboard scripts/guard/rate-limiter.sh) in pure Python, with the
same semantics: capacity = burst (max tokens instantly available), refill rate
= limit/window_seconds.  ``check`` consumes one token when available and
reports the seconds to wait when the bucket is empty (the proxy can either
reject or sleep-and-retry).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from limits.fingerprint import scope_key

REASON_RATE_LIMITED = "rate_limited"


@dataclass(frozen=True)
class RateLimitPolicy:
    """Rate policy for one scope: ``limit`` calls per window with a burst."""

    limit: int = 100
    window_seconds: float = 60.0
    burst: int | None = None  # token-bucket capacity; defaults to ``limit``

    def __post_init__(self) -> None:
        if int(self.limit) != self.limit or self.limit <= 0:
            raise ValueError("limit must be a positive integer")
        if float(self.window_seconds) <= 0:
            raise ValueError("window_seconds must be positive")
        burst = self.limit if self.burst is None else self.burst
        if int(burst) != burst or burst <= 0:
            raise ValueError("burst must be a positive integer")
        object.__setattr__(self, "burst", burst)

    @property
    def capacity(self) -> float:
        return float(self.burst)

    @property
    def refill_per_second(self) -> float:
        return self.limit / self.window_seconds


class TokenBucket:
    """A single token bucket: capacity tokens, refilled over time."""

    def __init__(
        self,
        capacity: float,
        refill_per_second: float,
        *,
        clock=time.monotonic,
    ) -> None:
        self.capacity = float(capacity)
        self.refill_per_second = float(refill_per_second)
        self.clock = clock
        self.tokens = self.capacity
        self.updated_at = self.clock()

    def _refill(self, now: float) -> None:
        elapsed = now - self.updated_at
        if elapsed > 0:
            self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_second)
            self.updated_at = now

    def consume(self, tokens: float = 1.0) -> tuple[bool, float, float]:
        """Try to consume ``tokens``.

        Returns (allowed, tokens_remaining, wait_seconds).  wait_seconds is 0
        when allowed and the estimated seconds until enough tokens exist when
        denied.
        """
        now = self.clock()
        self._refill(now)
        if self.tokens + 1e-9 >= tokens:
            self.tokens -= tokens
            return True, self.tokens, 0.0
        need = tokens - self.tokens
        if self.refill_per_second > 0:
            wait = need / self.refill_per_second
        else:
            wait = float("inf")
        return False, self.tokens, wait

    def snapshot(self) -> dict:
        now = self.clock()
        self._refill(now)
        return {"tokens": round(self.tokens, 6), "capacity": self.capacity}


@dataclass(frozen=True)
class RateLimitDecision:
    """Outcome of one rate-limit check."""

    scope: str
    limit: int
    window_seconds: float
    capacity: float
    tokens_remaining: float
    allowed: bool
    wait_seconds: float = 0.0
    reason: str | None = None


class RateLimiter:
    """Per-scope token-bucket rate limiter (scopes are isolated buckets)."""

    def __init__(
        self,
        policies: dict[str, RateLimitPolicy] | None = None,
        default_policy: RateLimitPolicy | None = None,
        *,
        clock=time.monotonic,
    ) -> None:
        self.policies = dict(policies or {})
        self.default_policy = default_policy or RateLimitPolicy()
        self.clock = clock
        self._buckets: dict[str, TokenBucket] = {}

    def policy_for(self, scope: str) -> RateLimitPolicy:
        return self.policies.get(scope, self.default_policy)

    def bucket_for(self, scope: str) -> TokenBucket:
        bucket = self._buckets.get(scope)
        if bucket is None:
            policy = self.policy_for(scope)
            bucket = TokenBucket(
                policy.capacity,
                policy.refill_per_second,
                clock=self.clock,
            )
            self._buckets[scope] = bucket
        return bucket

    def check(self, scope: str, tokens: float = 1.0) -> RateLimitDecision:
        """Consume ``tokens`` for ``scope`` and report whether it is allowed."""
        policy = self.policy_for(scope)
        bucket = self.bucket_for(scope)
        allowed, remaining, wait = bucket.consume(tokens)
        return RateLimitDecision(
            scope=scope,
            limit=policy.limit,
            window_seconds=policy.window_seconds,
            capacity=policy.capacity,
            tokens_remaining=remaining,
            allowed=allowed,
            wait_seconds=wait,
            reason=None if allowed else REASON_RATE_LIMITED,
        )

    def reset(self, scope: str) -> None:
        """Reset a scope's bucket to full capacity (e.g. on policy change)."""
        self._buckets.pop(scope, None)

    def status(self, scope: str) -> dict:
        policy = self.policy_for(scope)
        snap = self.bucket_for(scope).snapshot()
        return {
            "scope": scope,
            "limit": policy.limit,
            "window_seconds": policy.window_seconds,
            "capacity": policy.capacity,
            "tokens_remaining": snap["tokens"],
        }


def rate_scope(tenant: str, agent: str, model_tier: str) -> str:
    """Rate-limit scope for a (tenant, agent, model tier) triple."""
    return scope_key(tenant, agent, model_tier)
