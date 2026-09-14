"""Attribution: one record per turn, priced by the rate card, joined to the ticket.

Every test here fails if the behaviour regresses: the counts are exact, the
cost is compared against the rate card's own figure, and the fail-closed paths
are asserted together with their reason.
"""

from __future__ import annotations

import pytest

from telemetry.chat.attribution import CODE_DUPLICATE_TURN, CODE_IDENTITY_MISMATCH
from telemetry.chat.model import LEDGER_ACTION_TURN, ChatTurn, TurnError
from telemetry.metering.model import COST_SOURCE_ATTACHED, COST_SOURCE_RATE_CARD
from telemetry.metering.ratecards import RateCardStore


def test_one_metering_record_and_one_ledger_event_per_turn(
    attributor, usage_store, ledger, turn, record_factory, world
):
    attribution = attributor.attribute(turn, record_factory(turn_id=turn.turn_id))

    assert usage_store.count() == 1, "a turn is metered exactly once"
    assert len(ledger.records(world.tenant)) == 1, "a turn is audited exactly once"
    assert attribution.usage_record_id == usage_store.read()[0].record_id
    assert attribution.ledger_seq == ledger.records(world.tenant)[0]["seq"]
    assert ledger.records(world.tenant)[0]["action"] == LEDGER_ACTION_TURN


def test_attribution_carries_tenant_agent_conversation_and_ticket(
    attributor, turn, record_factory, world
):
    attribution = attributor.attribute(turn, record_factory(turn_id=turn.turn_id))

    assert (attribution.tenant_id, attribution.agent_id) == (
        world.tenant,
        world.agent,
    )
    assert attribution.conversation_id == world.conversation
    assert attribution.ticket_id == world.ticket
    assert attribution.ts == world.ts
    # promoted from the gateway call record, never re-derived here
    assert (attribution.provider, attribution.model, attribution.tier) == (
        world.provider,
        world.model,
        "L0",
    )
    assert attribution.output_tokens == world.output_tokens
    assert attribution.latency_ms == 420.0


def test_ticket_id_is_absent_when_the_turn_did_not_come_from_a_ticket(
    attributor, record_factory, world
):
    standalone = ChatTurn(
        turn_id="turn-2",
        conversation_id="conv-2",
        tenant_id=world.tenant,
        agent_id=world.agent,
        ts=world.ts,
        static_prefix="You are the tenant's support agent.",
        user_delta="hello",
        cached_tokens=0,
    )
    attribution = attributor.attribute(standalone, record_factory(turn_id="turn-2"))

    assert attribution.ticket_id is None
    assert attribution.to_dict()["ticketId"] is None


def test_cost_is_resolved_by_the_rate_card_and_agrees_with_the_metering_row(
    attributor, usage_store, turn, record_factory, world
):
    attribution = attributor.attribute(turn, record_factory(turn_id=turn.turn_id))

    card = RateCardStore.load_dir()
    expected = card.estimate(
        world.provider,
        world.model,
        attribution.cache.billable_input_tokens,
        world.output_tokens,
    )
    assert expected is not None
    assert attribution.cost_source == COST_SOURCE_RATE_CARD
    assert attribution.cost_usd == pytest.approx(expected.cost_usd, abs=1e-12)
    # the metering row this lane wrote is the same figure — one authority
    assert attribution.cost_usd == pytest.approx(
        usage_store.read()[0].cost_usd, abs=1e-12
    )
    # the cold equivalent is what the same turn would have cost with no reuse
    cold = card.estimate(
        world.provider,
        world.model,
        attribution.cache.prompt_tokens,
        world.output_tokens,
    )
    assert attribution.cold_equivalent_cost_usd == pytest.approx(
        cold.cost_usd, abs=1e-12
    )
    assert attribution.cost_usd < attribution.cold_equivalent_cost_usd


def test_an_unknown_model_fails_closed_rather_than_pricing_zero(
    attributor, usage_store, turn, record_factory
):
    record = record_factory(
        turn_id=turn.turn_id, provider="nomad", model="nomad-9000"
    )
    attribution = attributor.attribute(turn, record)

    assert attribution.cost_usd is None, "unknown usage is never priced as zero"
    assert attribution.cost_source is None
    assert attribution.unmetered_reason, "an unmetered turn must say why"
    # the metering row agrees: unmetered, not a fabricated zero
    row = usage_store.read()[0]
    assert row.metered is False
    assert row.cost_usd is None
    assert row.unmetered_reason


def test_the_records_own_positive_estimate_is_used_when_no_card_exists(
    attributor, turn, record_factory
):
    record = record_factory(
        turn_id=turn.turn_id,
        provider="nomad",
        model="nomad-9000",
        estimated_cost_usd=0.25,
    )
    attribution = attributor.attribute(turn, record)

    assert attribution.cost_source == COST_SOURCE_ATTACHED
    assert attribution.cost_usd == pytest.approx(0.25)


def test_a_record_for_another_tenant_is_refused(
    attributor, usage_store, turn, record_factory, world
):
    record = record_factory(turn_id=turn.turn_id, tenant=world.other_tenant)
    with pytest.raises(TurnError) as excinfo:
        attributor.attribute(turn, record)

    assert CODE_IDENTITY_MISMATCH in str(excinfo.value)
    assert usage_store.count() == 0, "a refused join writes nothing"
    assert attributor.ledger.records(world.tenant) == []


def test_attributing_the_same_turn_twice_is_refused(
    attributor, usage_store, turn, record_factory
):
    attributor.attribute(turn, record_factory(turn_id=turn.turn_id))
    with pytest.raises(TurnError) as excinfo:
        attributor.attribute(turn, record_factory(turn_id=turn.turn_id))

    assert CODE_DUPLICATE_TURN in str(excinfo.value)
    assert usage_store.count() == 1, "one turn is metered exactly once"


def test_a_whole_response_cache_hit_is_the_metering_zero_cost_path(
    attributor, turn, record_factory
):
    record = record_factory(
        turn_id=turn.turn_id,
        outcome="cache_hit",
        input_tokens=0,
        output_tokens=0,
    )
    attribution = attributor.attribute(turn, record)

    assert attribution.cache_response_hit is True
    assert attribution.cost_usd == 0.0
    assert attribution.cost_source == "cache_hit"


def test_input_tokens_are_the_billable_remainder(
    attributor, turn, record_factory, world
):
    attribution = attributor.attribute(turn, record_factory(turn_id=turn.turn_id))

    assert attribution.cache.cached_tokens > 0
    assert attribution.input_tokens == attribution.cache.billable_input_tokens
    assert attribution.input_tokens == world.billable_input_tokens
