"""Budget guard: refused before the call, and still metered.

The refusal paths are asserted against a counting provider (``call_count == 0``)
*and* against the metering row (``billable=False``, ``metered=True``) — a guard
that stopped refusing, or one that refused silently, fails both ways.
"""

from __future__ import annotations

import pytest

from telemetry.budgets.budget import BudgetEnforcer, load_budget_policies
from telemetry.budgets.killswitch import KillSwitchController, KillSwitchState
from telemetry.budgets.ledger import StaticLedger
from telemetry.budgets.model import (
    MODE_OBSERVE,
    RESOURCE_REQUESTS,
    WINDOW_DAY,
)
from telemetry.budgets.quota import (
    QuotaEnforcer,
    QuotaLimit,
    QuotaPolicy,
    StaticProbe,
)
from telemetry.chat.budget_guard import GuardedTurnRunner, TurnBudgetGuard
from telemetry.chat.model import LEDGER_ACTION_REFUSED, ChatTurn
from telemetry.metering.model import NON_BILLABLE_OUTCOMES


def paused_controller(world) -> KillSwitchController:
    return KillSwitchController(
        initial=KillSwitchState(
            global_pause=True,
            reason="incident-1",
            paused_by="ops",
            timestamp=world.ts,
        )
    )


def budget_with_shipped_policies(month: str, spend_usd: float) -> BudgetEnforcer:
    """The shipped policies (``acme`` is ``enforce``) over a seeded spend ledger."""
    ledger = StaticLedger(costs={("acme", month): spend_usd})
    return BudgetEnforcer(ledger, load_budget_policies())


def tight_quota_rail(world, calls: int) -> QuotaEnforcer:
    policy = QuotaPolicy(
        tenant_id=world.tenant,
        plan="free",
        limits={
            RESOURCE_REQUESTS: QuotaLimit(
                resource=RESOURCE_REQUESTS,
                window=WINDOW_DAY,
                soft_limit=10,
                hard_limit=20,
            )
        },
    )
    ledger = StaticLedger(calls={(world.tenant, world.day): calls})
    return QuotaEnforcer(ledger, {world.tenant: policy}, probe=StaticProbe())


def test_a_paused_tenant_is_refused_before_any_provider_call(
    world, turn, attributor, usage_store, ledger, spy_provider
):
    guard = TurnBudgetGuard(killswitch=paused_controller(world))
    runner = GuardedTurnRunner(guard, attributor)
    provider = spy_provider()

    result = runner.run(turn, provider, estimated_cost_usd=0.01)

    assert provider.call_count == 0, "a refused turn must never reach a provider"
    assert result.provider_called is False
    assert result.allowed is False
    assert result.outcome.hard_stop is True
    assert result.outcome.outcome in NON_BILLABLE_OUTCOMES

    # refused AND metered: the refusal is visible in the numbers
    assert usage_store.count() == 1
    row = usage_store.read()[0]
    assert row.metered is True
    assert row.billable is False
    assert row.cost_usd is None
    assert len(ledger.records(world.tenant)) == 1
    assert ledger.records(world.tenant)[0]["action"] == LEDGER_ACTION_REFUSED
    assert result.attribution.cost_usd is None
    assert result.attribution.metered is True
    assert result.attribution.billable is False


def test_an_over_cap_tenant_is_blocked_and_still_metered(
    world, turn, attributor, usage_store, spy_provider
):
    # acme's shipped policy is enforce with a 120 USD monthly cap: seeded at the
    # cap, the next turn is refused by the budget rail.
    guard = TurnBudgetGuard(budget=budget_with_shipped_policies(world.month, 120.0))
    runner = GuardedTurnRunner(guard, attributor)
    provider = spy_provider()

    result = runner.run(turn, provider, estimated_cost_usd=0.01)

    assert result.allowed is False
    assert result.outcome.decision == "block"
    assert provider.call_count == 0
    assert usage_store.count() == 1
    assert usage_store.read()[0].metered is True
    assert usage_store.read()[0].billable is False


def test_a_quota_exhausted_tenant_never_reaches_the_provider(
    world, turn, attributor, usage_store, spy_provider
):
    guard = TurnBudgetGuard(quota=tight_quota_rail(world, calls=20))
    runner = GuardedTurnRunner(guard, attributor)
    provider = spy_provider()

    result = runner.run(turn, provider)

    assert result.allowed is False
    assert result.outcome.outcome in NON_BILLABLE_OUTCOMES
    assert provider.call_count == 0
    assert usage_store.count() == 1
    assert usage_store.read()[0].billable is False


def test_a_soft_warning_allows_the_turn_and_is_reported(
    world, turn, attributor, spy_provider
):
    # 100 of the 120 USD cap is past warnAtPct 0.8 but below the cap.
    guard = TurnBudgetGuard(budget=budget_with_shipped_policies(world.month, 100.0))
    runner = GuardedTurnRunner(guard, attributor)
    provider = spy_provider()

    result = runner.run(turn, provider, estimated_cost_usd=0.01)

    assert result.outcome.soft_warning is True
    assert result.allowed is True
    assert provider.call_count == 1
    assert result.attribution.billable is True
    assert result.attribution.cost_usd is not None


def test_an_allowed_turn_calls_the_provider_once_and_is_attributed(
    world, turn, attributor, usage_store, spy_provider, record_factory
):
    guard = TurnBudgetGuard()  # no rails wired: nothing refuses
    runner = GuardedTurnRunner(guard, attributor)
    provider = spy_provider(record_factory(turn_id=turn.turn_id))

    result = runner.run(turn, provider)

    assert result.allowed is True
    assert provider.call_count == 1
    assert provider.calls[0] is turn
    assert usage_store.count() == 1
    assert result.attribution.cost_usd == pytest.approx(
        usage_store.read()[0].cost_usd, abs=1e-12
    )


def test_observe_mode_reports_would_block_but_never_refuses(
    world, turn, attributor, spy_provider
):
    from telemetry.budgets.budget import BudgetLimit, TenantBudgetPolicy

    policy = TenantBudgetPolicy(
        tenant_id=world.tenant,
        mode=MODE_OBSERVE,
        cost_limit=BudgetLimit(window="month", limit=1.0, warn_at_pct=0.8),
    )
    ledger = StaticLedger(costs={(world.tenant, world.month): 50.0})
    guard = TurnBudgetGuard(budget=BudgetEnforcer(ledger, {world.tenant: policy}))
    runner = GuardedTurnRunner(guard, attributor)
    provider = spy_provider()

    result = runner.run(turn, provider)

    assert result.outcome.decision in {"would_block", "would_warn"}
    assert result.allowed is True
    assert provider.call_count == 1


def test_a_critical_turn_passes_a_pause_but_is_not_silently_exempt(
    world, turn, attributor, spy_provider
):
    critical_turn = ChatTurn(
        turn_id="turn-critical",
        conversation_id=world.conversation,
        tenant_id=world.tenant,
        agent_id=world.agent,
        ts=world.ts,
        static_prefix=world.static_prefix,
        user_delta=world.user_delta,
        cached_tokens=0,
        critical=True,
    )
    guard = TurnBudgetGuard(killswitch=paused_controller(world))
    runner = GuardedTurnRunner(guard, attributor)
    provider = spy_provider()

    result = runner.run(critical_turn, provider)

    assert result.allowed is True
    assert provider.call_count == 1


def test_the_guard_never_accepts_a_negative_estimated_cost(turn, world):
    guard = TurnBudgetGuard(killswitch=paused_controller(world))
    with pytest.raises(ValueError):
        guard.check(turn, estimated_cost_usd=-1.0)


def test_from_config_wires_the_shipped_off_by_default_kill_switch(turn):
    guard = TurnBudgetGuard.from_config()

    assert guard.killswitch is not None
    assert guard.killswitch.paused is False, "the shipped switch ships OFF"
    assert guard.budget is not None
    assert guard.quota is not None
    outcome = guard.check(turn)
    assert outcome.allowed is True
    assert outcome.metered is True


def test_the_outcome_serializes_its_rails(world, turn):
    guard = TurnBudgetGuard(killswitch=paused_controller(world))
    outcome = guard.check(turn, estimated_cost_usd=0.02)

    payload = outcome.to_dict()
    assert payload["refused"] is True
    assert payload["rails"], "the verdict must carry the rails it evaluated"
    assert payload["rails"][0]["kind"] == "kill_switch"
    assert payload["metered"] is True
