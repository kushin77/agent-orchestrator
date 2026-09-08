"""LimitsEngine facade: the cost/capacity control contract for the gateway.

This is the single entry point the model-gateway proxy lane (issue #16)
consumes.  One call goes through, in order:

    cache (zero-cost hit?) -> token budget (enforce blocks) -> rate limit

and, for an allowed call, the proxy performs the provider call and then calls
``complete()`` which meters real usage, enforces the output throttle per task
type, and stores a cacheable response.

NEVER-FAIL-OPEN guarantee: a budget-exhausted or rate-limited call returns a
GuardDecision whose ``served()`` is False and whose metering outcome is an
explicit block (budget_exceeded/queued/degraded/rate_limited).  There is no
path that turns a block into a silent success.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from limits import model
from limits.backpressure import BackpressureController, BackpressureDecision
from limits.budget import BudgetController, BudgetDecision
from limits.cache import SemanticCache
from limits.ratelimit import RateLimitDecision, RateLimiter, rate_scope
from limits.throttle import OutputThrottle

KIND_CACHE_HIT = "cache_hit"
KIND_ALLOW = "allow"
KIND_BUDGET_EXCEEDED = "budget_exceeded"
KIND_RATE_LIMITED = "rate_limited"

KINDS = frozenset({KIND_CACHE_HIT, KIND_ALLOW, KIND_BUDGET_EXCEEDED, KIND_RATE_LIMITED})


@dataclass(frozen=True)
class GuardDecision:
    """Outcome of guarding one model call BEFORE any provider call."""

    kind: str
    request: model.ModelCallRequest
    metering: model.MeteringRecord
    response: str | None = None  # populated on a cache hit
    cache_key: str | None = None
    budget: BudgetDecision | None = None
    rate: RateLimitDecision | None = None
    backpressure: BackpressureDecision | None = None

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"unknown guard decision kind: {self.kind!r}")

    def served(self) -> bool:
        """True only when the call was actually served (hit or allowed).

        A blocked call (budget/rate) returns False so callers can never treat
        a block as a served success.
        """
        return self.kind in (KIND_CACHE_HIT, KIND_ALLOW)


@dataclass(frozen=True)
class CompleteResult:
    """Outcome of completing an allowed call after the provider responded."""

    metering: model.MeteringRecord
    response: str | None = None
    trimmed: bool = False
    refused: bool = False


class LimitsEngine:
    """Coordinates cache + budget + rate + throttle + backpressure for a call."""

    def __init__(
        self,
        *,
        cache: SemanticCache | None = None,
        budget: BudgetController | None = None,
        rate: RateLimiter | None = None,
        throttle: OutputThrottle | None = None,
        backpressure: BackpressureController | None = None,
        cacheable: bool | Callable[[model.ModelCallRequest], bool] = True,
    ) -> None:
        # cache=None means the semantic-cache layer is DISABLED (build_engine
        # passes an explicit SemanticCache when config enables it); the other
        # components default to a fully-configured instance when not given.
        self.cache = cache
        self.budget = budget if budget is not None else BudgetController()
        self.rate = rate if rate is not None else RateLimiter()
        self.throttle = throttle if throttle is not None else OutputThrottle()
        self.backpressure = (
            backpressure if backpressure is not None else BackpressureController()
        )
        self.cacheable = cacheable

    # --- public API ----------------------------------------------------------
    def guard(
        self,
        request: model.ModelCallRequest,
        requested_tokens: int | None = None,
    ) -> GuardDecision:
        """Check cache/budget/rate for a call; does NOT call any provider."""
        req = request

        # 1. Semantic cache: a hit is zero-cost and is accounted as such.
        if self.cache is not None:
            resolution = self.cache.resolve(
                req.tenant, req.model_tier, req.prompt, task_type=req.task_type
            )
            if resolution.hit:
                metering = model.MeteringRecord(
                    tenant=req.tenant,
                    agent=req.agent,
                    model_tier=req.model_tier,
                    task_type=req.task_type,
                    request_id=req.request_id,
                    outcome=model.CACHE_HIT,
                    cache_key=resolution.key,
                    cached=True,
                    zero_cost=True,
                    reason=model.CACHE_HIT,
                )
                return GuardDecision(
                    KIND_CACHE_HIT,
                    req,
                    metering,
                    response=resolution.value,
                    cache_key=resolution.key,
                )

        estimated_input = (
            requested_tokens
            if requested_tokens is not None
            else self.throttle.token_estimate(req.prompt)
        )

        # 2. Token budget (observe logs; enforce blocks -> backpressure).
        budget_decision = self.budget.decide(
            req.tenant, req.agent, req.model_tier, estimated_input, request_id=req.request_id
        )
        if not budget_decision.allowed:
            bp = self.backpressure.handle(
                tenant=req.tenant,
                agent=req.agent,
                model_tier=req.model_tier,
                task_type=req.task_type,
                request_id=req.request_id,
                reason=budget_decision.reason or model.BUDGET_EXCEEDED,
            )
            outcome = model.QUEUED if bp.queued else model.DEGRADED
            metering = model.MeteringRecord(
                tenant=req.tenant,
                agent=req.agent,
                model_tier=req.model_tier,
                task_type=req.task_type,
                request_id=req.request_id,
                outcome=outcome,
                input_tokens=estimated_input,
                zero_cost=True,
                reason=model.BUDGET_EXCEEDED,
            )
            return GuardDecision(
                KIND_BUDGET_EXCEEDED,
                req,
                metering,
                budget=budget_decision,
                backpressure=bp,
            )

        # 3. Rate limit per (tenant, agent, tier).
        rate_decision = self.rate.check(rate_scope(req.tenant, req.agent, req.model_tier))
        if not rate_decision.allowed:
            metering = model.MeteringRecord(
                tenant=req.tenant,
                agent=req.agent,
                model_tier=req.model_tier,
                task_type=req.task_type,
                request_id=req.request_id,
                outcome=model.RATE_LIMITED,
                input_tokens=estimated_input,
                zero_cost=True,
                reason=model.RATE_LIMITED,
            )
            return GuardDecision(
                KIND_RATE_LIMITED, req, metering, rate=rate_decision
            )

        # 4. Allowed: the proxy performs the provider call, then complete().
        # The budget/rate decisions ride along so an observe-mode caller can
        # see would_block without being blocked.
        metering = model.MeteringRecord(
            tenant=req.tenant,
            agent=req.agent,
            model_tier=req.model_tier,
            task_type=req.task_type,
            request_id=req.request_id,
            outcome=model.PROVIDER,
            input_tokens=estimated_input,
        )
        return GuardDecision(
            KIND_ALLOW,
            req,
            metering,
            budget=budget_decision,
            rate=rate_decision,
        )

    def complete(
        self,
        request: model.ModelCallRequest,
        raw_response: str,
        *,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> CompleteResult:
        """Finish an allowed call: throttle output, meter, and cache the result."""
        req = request
        verdict = self.throttle.enforce(req.task_type, raw_response)
        in_tok = (
            input_tokens
            if input_tokens is not None
            else self.throttle.token_estimate(req.prompt)
        )
        out_tok = (
            output_tokens
            if output_tokens is not None
            else self.throttle.token_estimate(raw_response or "")
        )
        # Real spend is recorded whether or not the output is refused (the
        # provider already billed it); accounting stays honest.
        self.budget.record(req.tenant, req.agent, req.model_tier, in_tok + out_tok, request_id=req.request_id)

        if verdict.refused:
            metering = model.MeteringRecord(
                tenant=req.tenant,
                agent=req.agent,
                model_tier=req.model_tier,
                task_type=req.task_type,
                request_id=req.request_id,
                outcome=model.REFUSED,
                input_tokens=in_tok,
                output_tokens=out_tok,
                zero_cost=False,
                reason=verdict.reason,
            )
            return CompleteResult(metering=metering, response=None, refused=True)

        if self._is_cacheable(req) and self.cache is not None:
            self.cache.store_result(
                req.tenant,
                req.model_tier,
                req.prompt,
                verdict.output or "",
                task_type=req.task_type,
            )
        metering = model.MeteringRecord(
            tenant=req.tenant,
            agent=req.agent,
            model_tier=req.model_tier,
            task_type=req.task_type,
            request_id=req.request_id,
            outcome=model.PROVIDER,
            input_tokens=in_tok,
            output_tokens=out_tok,
            zero_cost=False,
        )
        return CompleteResult(
            metering=metering,
            response=verdict.output,
            trimmed=verdict.trimmed,
            refused=False,
        )

    def execute(
        self,
        request: model.ModelCallRequest,
        provider: Callable,
    ) -> tuple[GuardDecision, CompleteResult | None]:
        """Convenience wrapper: guard, then call provider and complete.

        ``provider`` returns the raw response text, or a (text, usage) tuple
        where usage carries ``input_tokens`` / ``output_tokens``.
        """
        decision = self.guard(request)
        if decision.kind in (KIND_CACHE_HIT,):
            return decision, None
        if decision.kind != KIND_ALLOW:
            return decision, None
        raw = provider(request)
        if isinstance(raw, tuple):
            text, usage = raw
            result = self.complete(
                request,
                text,
                input_tokens=usage.get("input_tokens"),
                output_tokens=usage.get("output_tokens"),
            )
        else:
            result = self.complete(request, raw)
        return decision, result

    # --- internal ------------------------------------------------------------
    def _is_cacheable(self, request: model.ModelCallRequest) -> bool:
        if callable(self.cacheable):
            return bool(self.cacheable(request))
        return bool(self.cacheable)


def build_engine(
    config=None,  # limits.config.LimitsConfig | None
) -> "LimitsEngine":
    """Wire a fully configured LimitsEngine from config (default limits.yaml)."""
    from limits.config import (  # local import avoids a module cycle
        build_backpressure,
        build_budget,
        build_cache,
        build_rate,
        build_throttle,
        load_config,
    )

    cfg = config if config is not None else load_config()
    cache = build_cache(cfg) if cfg.cache.enabled else None
    return LimitsEngine(
        cache=cache,
        budget=build_budget(cfg),
        rate=build_rate(cfg),
        throttle=build_throttle(cfg),
        backpressure=build_backpressure(cfg),
    )
