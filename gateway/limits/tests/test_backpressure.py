"""Backpressure: budget exhausted -> explicit queue/degrade, NEVER fail open."""

from __future__ import annotations

import pytest

from limits.backpressure import (
    DEGRADE,
    QUEUE,
    BackpressureController,
    BackpressureQueue,
)
from limits.budget import BudgetController, BudgetMode, BudgetPolicy
from limits.limiter import (
    KIND_BUDGET_EXCEEDED,
    build_engine,
)
from limits.model import ModelCallRequest

EXHAUSTED_REASON = "budget_exceeded"


class TestController:
    def test_degrade_strategy_is_explicit(self):
        ctl = BackpressureController(strategy=DEGRADE)
        decision = ctl.handle(
            tenant="acme", agent="coder", model_tier="LOW", reason=EXHAUSTED_REASON
        )
        assert decision.action == DEGRADE
        assert decision.degraded is True
        assert decision.queued is False
        assert decision.reason == EXHAUSTED_REASON
        assert decision.succeeded is False  # never a silent success

    def test_queue_strategy_enqueues_job(self):
        queue = BackpressureQueue(capacity=5)
        ctl = BackpressureController(strategy=QUEUE, queue=queue)
        decision = ctl.handle(
            tenant="acme", agent="coder", model_tier="LOW", reason=EXHAUSTED_REASON
        )
        assert decision.action == QUEUE
        assert decision.queued is True
        assert decision.job_id is not None
        assert len(queue) == 1
        assert decision.succeeded is False

    def test_queue_full_degrades_explicitly(self):
        queue = BackpressureQueue(capacity=1)
        ctl = BackpressureController(strategy=QUEUE, queue=queue)
        ctl.handle(tenant="acme", agent="coder", model_tier="LOW", reason=EXHAUSTED_REASON)
        overflow = ctl.handle(
            tenant="acme", agent="coder", model_tier="LOW", reason=EXHAUSTED_REASON
        )
        assert overflow.action == DEGRADE
        assert overflow.degraded is True
        assert "queue full" in overflow.message

    def test_unknown_strategy_rejected(self):
        with pytest.raises(ValueError):
            BackpressureController(strategy="drop")


class TestNoFailOpenNegative:
    """The acceptance negative: exhausted budget -> explicit decision, never a
    silent success.  There is no code path where a blocked call looks served."""

    def test_engine_enforce_exhausted_never_silently_succeeds(self):
        engine = build_engine()
        engine.budget = BudgetController(
            default_policy=BudgetPolicy(cap_tokens=10, mode=BudgetMode.ENFORCE)
        )
        request = ModelCallRequest(
            tenant="broker",
            agent="worker",
            model_tier="HIGH",
            task_type="architecture",
            prompt="This request needs far more than ten tokens",
        )
        decision = engine.guard(request, requested_tokens=9001)

        # Explicit block: never served, explicit kind + backpressure + reason.
        assert decision.kind == KIND_BUDGET_EXCEEDED
        assert decision.served() is False
        assert decision.response is None  # no fabricated payload
        assert decision.backpressure is not None
        assert decision.backpressure.succeeded is False
        assert decision.backpressure.action in (QUEUE, DEGRADE)
        assert decision.metering.reason == EXHAUSTED_REASON
        assert decision.metering.outcome in ("queued", "degraded")
        assert decision.metering.zero_cost is True

    def test_engine_allow_path_is_the_only_success(self):
        engine = build_engine()
        request = ModelCallRequest(
            tenant="acme", agent="coder", model_tier="LOW", prompt="cheap prompt"
        )
        decision = engine.guard(request, requested_tokens=5)
        assert decision.served() is True
        assert decision.kind == "allow"

    def test_queue_strategy_backpressure_decision_succeeded_false(self):
        ctl = BackpressureController(strategy=QUEUE)
        decision = ctl.handle(
            tenant="acme", agent="coder", model_tier="LOW", reason=EXHAUSTED_REASON
        )
        assert decision.succeeded is False
        assert decision.action == QUEUE
