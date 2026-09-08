"""Rate-limit seam: every MCP tool call passes an injected rate gate.

The gateway depends only on the :class:`RateGate` shape. The shipped adapter,
:class:`LimitsRateGate`, consumes the gateway/limits token-bucket rate limiter
(issue #19, ``gateway/limits/ratelimit.py``) - the sibling cost/capacity layer
of the same pillar - rather than reimplementing a bucket. Scopes are isolated
per (tenant, agent, tool) so one tenant's burst never starves another.

Rate limiting ships flag-gated: a gateway built without a ``rate_gate`` does
not limit (safe default OFF per the fleet doctrine); ``build_gateway`` wires a
:class:`LimitsRateGate` so the shipped default limits every call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class RateDecision:
    """Outcome of one rate-gate check."""

    scope: str
    allowed: bool
    remaining: float = 0.0

    @property
    def denied(self) -> bool:
        return not self.allowed


class RateGate(Protocol):
    """Injected per-scope limiter."""

    def consume(self, scope: str) -> RateDecision:
        """Consume one token for ``scope`` and report whether it is allowed."""
        ...

    def reset(self, scope: str) -> None:
        ...


class UnlimitedRateGate:
    """No-op gate (rate limiting disabled)."""

    def consume(self, scope: str) -> RateDecision:
        return RateDecision(scope=scope, allowed=True, remaining=float("inf"))

    def reset(self, scope: str) -> None:
        return None


class LimitsRateGate:
    """Adapts a gateway/limits ``RateLimiter`` to :class:`RateGate`.

    ``limits`` is imported lazily (gateway/ must be on ``sys.path`` for the
    sibling package to resolve); a deployment that does not carry gateway/limits
    can supply any other :class:`RateGate` instead.
    """

    def __init__(self, limiter=None, default_policy=None, clock=None) -> None:
        self._limiter = limiter
        self._default_policy = default_policy
        self._clock = clock
        self._owns = limiter is None

    def _limiter_instance(self):
        if self._limiter is not None:
            return self._limiter
        try:
            from limits.ratelimit import RateLimiter  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "gateway/limits is not importable: add the gateway/ directory "
                "to sys.path to use LimitsRateGate, or inject another RateGate"
            ) from exc
        self._limiter = RateLimiter(
            default_policy=self._default_policy, clock=self._clock
        )
        return self._limiter

    def consume(self, scope: str) -> RateDecision:
        limiter = self._limiter_instance()
        decision = limiter.check(scope)
        return RateDecision(
            scope=scope,
            allowed=decision.allowed,
            remaining=decision.tokens_remaining,
        )

    def reset(self, scope: str) -> None:
        self._limiter_instance().reset(scope)


def scope_for(session_tenant_id: str, agent_id: str, tool_name: str) -> str:
    """Rate-limit scope key: ``tenant:agent:tool`` (isolated buckets)."""
    return f"{session_tenant_id}:{agent_id}:{tool_name}"
