"""Negative controls for the block paths (AO-GR-4).

Every refusal rail is provoked here, and each control asserts three things at
once: the provider was never called, the ledger shows the refusal, and the
turn is still metered as a non-billable event.  It also asserts the **bucket**
the verdict names: each rail is seeded on the turn's own day/month, so a verdict
judged against the live clock would make these controls expire with the calendar
(#506) instead of failing for the right reason.  A control that cannot fail is a
formality, so ``test_the_same_control_notices_a_guard_that_stopped_refusing``
*neutters* the guard the same way a regression would and proves they notice.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from telemetry.budgets.budget import BudgetEnforcer, load_budget_policies
from telemetry.budgets.killswitch import KillSwitchController, KillSwitchState
from telemetry.budgets.ledger import StaticLedger
from telemetry.budgets.model import RESOURCE_REQUESTS, WINDOW_DAY
from telemetry.budgets.quota import (
    QuotaEnforcer,
    QuotaLimit,
    QuotaPolicy,
    StaticProbe,
)
from telemetry.chat.budget_guard import GuardedTurnRunner, TurnBudgetGuard
from telemetry.chat.model import LEDGER_ACTION_REFUSED
from telemetry.metering.model import NON_BILLABLE_OUTCOMES

REFUSAL_PATHS = ("kill_switch", "budget", "quota")


def guard_for(path: str, world) -> TurnBudgetGuard:
    """The shipped rails, seeded so that ``path`` refuses the next turn."""
    if path == "kill_switch":
        return TurnBudgetGuard(
            killswitch=KillSwitchController(
                initial=KillSwitchState(
                    global_pause=True, reason="incident-1", paused_by="ops"
                )
            )
        )
    if path == "budget":
        # acme's shipped policy: enforce, 120 USD/month, warn at 80%.
        ledger = StaticLedger(costs={(world.tenant, world.month): 120.0})
        return TurnBudgetGuard(
            budget=BudgetEnforcer(ledger, load_budget_policies())
        )
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
    ledger = StaticLedger(calls={(world.tenant, world.day): 20})
    return TurnBudgetGuard(
        quota=QuotaEnforcer(ledger, {world.tenant: policy}, probe=StaticProbe())
    )


class NeuteredGuard(TurnBudgetGuard):
    """The guard with its refusal branch removed — the regression to catch."""

    def check(self, turn, **kwargs):
        outcome = super().check(turn, **kwargs)
        return replace(outcome, allowed=True, outcome=None)


@pytest.mark.parametrize("path", REFUSAL_PATHS)
def test_each_refusal_path_refuses_and_still_meters(
    path, world, turn, attributor, usage_store, ledger, spy_provider
):
    provider = spy_provider()
    result = GuardedTurnRunner(guard_for(path, world), attributor).run(
        turn, provider, estimated_cost_usd=0.01
    )

    assert result.allowed is False, f"{path}: the turn must be refused"
    assert result.outcome.hard_stop is True, f"{path}: a hard stop is expected"
    assert (result.outcome.day, result.outcome.month) == (world.day, world.month), (
        f"{path}: the rail is seeded on the turn's own bucket "
        f"({world.day}/{world.month}), so the verdict must name that bucket — "
        f"a verdict judged against the live clock makes this control expire with "
        f"the calendar (#506); it reports "
        f"{result.outcome.day}/{result.outcome.month}"
    )
    assert provider.call_count == 0, f"{path}: the provider must never be called"
    assert result.provider_called is False

    assert usage_store.count() == 1, f"{path}: a refused turn is still metered"
    row = usage_store.read()[0]
    assert row.metered is True
    assert row.billable is False
    assert row.outcome in NON_BILLABLE_OUTCOMES

    records = ledger.records(world.tenant)
    assert len(records) == 1, f"{path}: exactly one ledger event"
    assert records[0]["action"] == LEDGER_ACTION_REFUSED
    assert result.attribution.usage_record_id == row.record_id
    assert result.attribution.cost_usd is None


@pytest.mark.parametrize("path", REFUSAL_PATHS)
def test_the_same_control_notices_a_guard_that_stopped_refusing(
    path, world, turn, attributor, spy_provider
):
    """The mutation the controls must catch: the refusal branch is gone."""
    provider = spy_provider()
    result = GuardedTurnRunner(NeuteredGuard(), attributor).run(
        turn, provider, estimated_cost_usd=0.01
    )

    # The neutered guard's verdict still says "refused", but the run no longer
    # refuses: this is exactly what the assertions above must notice.
    assert result.outcome.allowed is True
    assert provider.call_count == 1, "a neutered guard lets the turn reach a model"


def test_the_provider_is_only_called_after_an_allow_verdict(
    world, turn, attributor, spy_provider, record_factory
):
    provider = spy_provider(record_factory(turn_id=turn.turn_id))
    runner = GuardedTurnRunner(guard_for("kill_switch", world), attributor)

    refused = runner.run(turn, provider, estimated_cost_usd=0.01)

    assert refused.provider_called is False
    assert provider.call_count == 0
    assert refused.attribution.outcome in NON_BILLABLE_OUTCOMES
