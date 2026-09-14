"""The read model carries the numbers; it never re-derives them.

Each rollup is compared against the attributions it was built from, so a read
model that started re-pricing (or dropping a refused turn) fails here.
"""

from __future__ import annotations

import pytest

from telemetry.chat.budget_guard import GuardedTurnRunner, TurnBudgetGuard
from telemetry.chat.readmodel import ChatFinOpsReadModel


def test_the_turn_view_carries_the_attributions_numbers(
    attributor, turn, record_factory, world
):
    attribution = attributor.attribute(turn, record_factory(turn_id=turn.turn_id))
    model = ChatFinOpsReadModel()

    view = model.add(attribution)

    assert view.turn_id == turn.turn_id
    assert view.cost_usd == attribution.cost_usd
    assert view.cost_source == attribution.cost_source
    assert view.cold_equivalent_cost_usd == attribution.cold_equivalent_cost_usd
    assert view.cache_hit_share == attribution.cache_hit_share
    assert view.cached_tokens == world.cached_tokens
    assert view.ticket_id == world.ticket
    assert view.usage_record_id == attribution.usage_record_id
    assert model.turn(turn.turn_id) is view
    assert model.turn("nope") is None


def test_rollups_sum_the_reported_costs_and_never_reprice(
    attributor, turn, cold_turn, record_factory, world
):
    warm = attributor.attribute(turn, record_factory(turn_id=turn.turn_id))
    cold = attributor.attribute(
        cold_turn, record_factory(turn_id=cold_turn.turn_id)
    )
    model = ChatFinOpsReadModel()
    model.add(warm)
    model.add(cold)

    tenant = model.tenant(world.tenant)
    conversation = model.conversation(world.conversation)
    totals = model.totals()

    assert tenant.turns == 2
    assert tenant.cost_usd == pytest.approx(warm.cost_usd + cold.cost_usd, abs=1e-12)
    assert conversation.cost_usd == tenant.cost_usd == totals.cost_usd
    assert totals.billable_turns == 2
    assert totals.refused_turns == 0
    assert totals.cache_hit_turns == 1
    assert totals.cache_savings_usd > 0.0
    assert totals.cache_hit_share > 0.0


def test_a_refused_turn_is_counted_without_a_charge(
    world, turn, attributor, usage_store, spy_provider, record_factory
):
    from telemetry.budgets.killswitch import KillSwitchController, KillSwitchState

    guard = TurnBudgetGuard(
        killswitch=KillSwitchController(
            initial=KillSwitchState(global_pause=True, reason="incident-1")
        )
    )
    provider = spy_provider(record_factory(turn_id=turn.turn_id))
    result = GuardedTurnRunner(guard, attributor).run(turn, provider)

    model = ChatFinOpsReadModel()
    view = model.add(result.attribution, result.outcome)
    rollup = model.tenant(world.tenant)

    assert provider.call_count == 0
    assert view.budget_allowed is False
    assert view.budget_known is True
    assert view.budget_hard_stop is True
    assert view.cost_usd is None
    assert rollup.turns == 1
    assert rollup.refused_turns == 1
    assert rollup.billable_turns == 0
    assert rollup.cost_usd == 0.0, "a prevented turn is never charged"
    assert usage_store.count() == 1, "and it is still metered"


def test_the_ticket_rollup_joins_the_turns_a_ticket_spawned(
    attributor, turn, record_factory, world
):
    attribution = attributor.attribute(turn, record_factory(turn_id=turn.turn_id))
    model = ChatFinOpsReadModel()
    model.add(attribution)

    ticket = model.ticket(world.ticket)

    assert ticket.turns == 1
    assert ticket.cost_usd == pytest.approx(attribution.cost_usd, abs=1e-12)
    assert model.ticket("kushin77/agent-orchestrator#999").turns == 0


def test_the_read_model_serializes_the_tenant_numbers(
    attributor, turn, record_factory
):
    attribution = attributor.attribute(turn, record_factory(turn_id=turn.turn_id))
    model = ChatFinOpsReadModel()
    model.add(attribution)

    payload = model.to_dict()

    assert payload["totals"]["turns"] == 1
    assert payload["turns"][0]["turnId"] == turn.turn_id
    assert payload["turns"][0]["costUsd"] == attribution.cost_usd
