"""Token-budget enforcement: observe vs enforce, rolling window, scoping."""

from __future__ import annotations

import pytest

from limits.budget import (
    BudgetController,
    BudgetMode,
    BudgetPolicy,
    JsonlLedger,
    TokenBudget,
    UsageRecord,
)

ENFORCE = BudgetMode.ENFORCE
OBSERVE = BudgetMode.OBSERVE


class TestPolicy:
    def test_policy_validates(self):
        with pytest.raises(ValueError):
            BudgetPolicy(cap_tokens=0)
        with pytest.raises(ValueError):
            BudgetPolicy(cap_tokens=100, window_seconds=0)
        with pytest.raises(ValueError):
            BudgetPolicy(cap_tokens=100, mode="banana")

    def test_policy_mode_normalized_lower(self):
        assert BudgetPolicy(cap_tokens=100, mode="ENFORCE").mode == ENFORCE


class TestObserveVsEnforce:
    def test_observe_never_blocks_but_reports_would_block(self, clock):
        budget = TokenBudget(
            "acme::coder::LOW",
            BudgetPolicy(cap_tokens=100, mode=OBSERVE),
            clock=clock,
        )
        decision = budget.decide(150)
        assert decision.allowed is True
        assert decision.would_block is True
        assert decision.reason is None
        assert decision.used_in_window == 0
        assert decision.remaining == 100

    def test_enforce_blocks_over_cap(self, clock):
        budget = TokenBudget(
            "acme::coder::LOW",
            BudgetPolicy(cap_tokens=100, mode=ENFORCE),
            clock=clock,
        )
        decision = budget.decide(150)
        assert decision.allowed is False
        assert decision.exceeded is True
        assert decision.reason == "budget_exceeded"

    def test_enforce_allows_under_cap(self, clock):
        budget = TokenBudget(
            "acme::coder::LOW",
            BudgetPolicy(cap_tokens=100, mode=ENFORCE),
            clock=clock,
        )
        assert budget.decide(99).allowed is True
        assert budget.decide(100).allowed is True  # exactly at cap

    def test_controller_observe_default_via_mode_flag(self, clock):
        ctl = BudgetController(
            default_policy=BudgetPolicy(cap_tokens=100, mode=OBSERVE), clock=clock
        )
        assert ctl.decide("acme", "coder", "LOW", 500).allowed is True


class TestWindow:
    def test_enforce_records_and_exhausts_window(self, clock):
        budget = TokenBudget(
            "acme::coder::LOW",
            BudgetPolicy(cap_tokens=100, window_seconds=100, mode=ENFORCE),
            clock=clock,
        )
        budget.record(60)
        assert budget.used_in_window() == 60
        assert budget.decide(50).allowed is False  # 60 + 50 > 100

    def test_window_rollover_resets_usage(self, clock):
        policy = BudgetPolicy(cap_tokens=100, window_seconds=100, mode=ENFORCE)
        budget = TokenBudget("acme::coder::LOW", policy, clock=clock)
        budget.record(90)
        assert budget.decide(20).allowed is False
        clock.advance(101)  # the 90-token record falls outside the window
        assert budget.used_in_window() == 0
        assert budget.decide(20).allowed is True


class TestControllerScoping:
    def test_triple_scope_isolated(self, clock):
        ctl = BudgetController(
            default_policy=BudgetPolicy(cap_tokens=100, mode=ENFORCE), clock=clock
        )
        ctl.record("acme", "coder", "LOW", 90)
        assert ctl.decide("acme", "coder", "LOW", 20).allowed is False
        assert ctl.decide("acme", "coder", "HIGH", 20).allowed is True
        assert ctl.decide("acme", "reviewer", "LOW", 20).allowed is True

    def test_tenant_policy_aggregates_across_agents(self, clock):
        ctl = BudgetController(
            default_policy=BudgetPolicy(cap_tokens=1_000_000, mode=OBSERVE),
            tenant_policies={
                "acme": BudgetPolicy(cap_tokens=100, window_seconds=1000, mode=ENFORCE)
            },
            clock=clock,
        )
        ctl.record("acme", "coder", "LOW", 60)
        # a different agent of the same tenant hits the shared tenant cap
        decision = ctl.decide("acme", "reviewer", "HIGH", 50)
        assert decision.allowed is False
        assert decision.reason == "budget_exceeded"

    def test_exact_scope_policy_overrides_default(self, clock):
        ctl = BudgetController(
            default_policy=BudgetPolicy(cap_tokens=10_000, mode=ENFORCE),
            policies={"acme::coder::LOW": BudgetPolicy(cap_tokens=50, mode=ENFORCE)},
            clock=clock,
        )
        assert ctl.decide("acme", "coder", "LOW", 100).allowed is False
        assert ctl.decide("acme", "coder", "HIGH", 100).allowed is True

    def test_controller_merges_tenant_and_triple_decisions(self, clock):
        ctl = BudgetController(
            default_policy=BudgetPolicy(cap_tokens=100, mode=ENFORCE),
            tenant_policies={
                "acme": BudgetPolicy(cap_tokens=1000, mode=ENFORCE)
            },
            clock=clock,
        )
        decision = ctl.decide("acme", "coder", "LOW", 20)
        assert decision.allowed is True
        assert len(decision.sub_decisions) == 2  # tenant + triple levels


class TestLedger:
    def test_jsonl_ledger_persists(self, tmp_path, clock):
        path = tmp_path / "usage.jsonl"
        ledger = JsonlLedger(path)
        ledger.append(UsageRecord(scope="acme::coder::LOW", at=clock(), tokens=42))
        loaded = JsonlLedger(path)
        records = loaded.all()
        assert len(records) == 1
        assert records[0].tokens == 42

    def test_budget_controller_uses_jsonl_ledger(self, tmp_path, clock):
        ledger = JsonlLedger(tmp_path / "usage.jsonl")
        ctl = BudgetController(
            default_policy=BudgetPolicy(cap_tokens=100, mode=ENFORCE),
            ledger=ledger,
            clock=clock,
        )
        ctl.record("acme", "coder", "LOW", 80)
        assert ctl.decide("acme", "coder", "LOW", 30).allowed is False
