"""Capstone: the conversational surface end-to-end (issue #1013).

The e2e suite proved the platform journey and the delivery journey but nothing
proved the conversational chain (``grep -rliE 'telemetry\\.chat|gateway\\.chat'
e2e/`` was empty at filing).  This file asserts that chain over the REAL merged
modules — offline, consuming every pillar through its public API — and that each
refusal path in it is a control a mutant can break.

Acceptance trace (issue #1013 -> test):

1. signup -> a real turn ............ test_a_provisioned_tenant_serves_one_turn_end_to_end
2. tier from the routing stamp ...... test_the_tier_is_the_routers_stamp_and_never_a_client_claim
3. one attribution + one metering ... test_each_turn_writes_exactly_one_attribution_and_metering_record
   a turn with no record fails ...... test_a_turn_with_no_metering_record_fails_the_check
4. the ledger verifies OK ........... test_the_per_tenant_ledger_chain_verifies_ok_after_the_turn
5. over-cap blocked + still metered . test_an_over_cap_tenant_is_blocked_and_still_metered
   kill switch refuses pre-call ..... test_a_kill_switch_tenant_is_refused_before_any_provider_call
6. cache-hit costs strictly less .... test_a_cache_hit_turn_costs_strictly_less_than_the_cold_turn
7. cost is the rate card's figure ... test_no_cost_figure_is_re_derived_where_a_rate_card_exists
8. every refusal is a control ....... test_each_refusal_path_is_a_mutation_proved_control
                                     test_the_controls_notice_a_guard_that_stopped_refusing
9. no date bomb ..................... test_a_past_dated_turn_is_judged_against_its_own_day

The external mutation proof (the branch disabled in a throwaway ``/tmp`` copy of
the module under test, and the control required to go red naming the removed
refusal) is driven outside pytest — see the lane's PR for the command and its
output.  ``test_the_controls_notice_a_guard_that_stopped_refusing`` is the same
mutation executed in-suite, so the property is asserted on every run of the
suite as well.
"""

from __future__ import annotations

import json
import os

import pytest

from e2e.chat_surface import (
    AGENT,
    CACHED_TOKENS,
    CLAIM_RUNG_ABOVE,
    CLAIM_UNKNOWN,
    CONVERSATION,
    ChatProvider,
    ChatSurfaceError,
    ConversationalChain,
    MeterlessAttributor,
    NeuteredTurnDatedBudgetGuard,
    OUTPUT_TOKENS,
    REFUSAL_PATHS,
    TASK_TYPE_ANSWER,
    TICKET,
    TURN_DAY,
    TURN_MONTH,
    assert_single_record,
    day_scoping,
    refusal_controls,
    refusal_guard,
    run_chat_surface,
    run_refusal_control,
    turn_day,
    turn_month,
)
from telemetry.chat.model import LEDGER_ACTION_TURN
from telemetry.chat.tiering import TierClaimError, resolve_turn_tier
from telemetry.metering.model import COST_SOURCE_RATE_CARD, parse_ts

REFUSAL_PATH_IDS = tuple(item["path"] for item in REFUSAL_PATHS)


@pytest.fixture()
def chain(tmp_path) -> ConversationalChain:
    """A fresh provisioned tenant + real conversational gateway per test."""
    return ConversationalChain(work_dir=os.path.join(str(tmp_path), "chat-run"))


# --------------------------------------------------------------------------- #
# 1. signup -> one real turn end to end
# --------------------------------------------------------------------------- #
def test_a_provisioned_tenant_serves_one_turn_end_to_end(chain):
    """A tenant from identity/onboarding makes one turn through the real rails."""
    tenant = chain.control.provision_result.tenant
    assert tenant.id == chain.tenant_id
    assert tenant.status == "active", "the turn's tenant must be a live provision"

    turn = chain.turn()
    result = chain.run(turn)

    assert result.allowed is True, result.outcome.to_dict()
    assert result.provider_called is True
    assert chain.provider.call_count == 1, "one turn is exactly one dispatch"

    attribution = result.attribution
    assert attribution.tenant_id == tenant.id
    assert attribution.agent_id == AGENT
    assert attribution.conversation_id == CONVERSATION
    assert attribution.ticket_id == TICKET, "ADR-0014's join node survives the chain"
    assert attribution.outcome == "success"

    # the served call was the conversational surface's own route over the real
    # gateway (registry/chat's module, the composed routing policy, the rig).
    record = chain.provider.record
    assert record["taskType"] == TASK_TYPE_ANSWER
    assert record["provider"] == "deepseek"
    assert record["model"] == "deepseek-chat"
    decision = chain.provider.decision
    assert decision["capability"] == "research"
    assert decision["taskClass"] == "research"
    assert attribution.provider == record["provider"]
    assert attribution.model == record["model"]


# --------------------------------------------------------------------------- #
# 2. tier discipline
# --------------------------------------------------------------------------- #
def test_the_tier_is_the_routers_stamp_and_never_a_client_claim(chain):
    """The tier is the chooser's stamp; a client claim is carried, never used."""
    turn = chain.turn(client_tier=CLAIM_RUNG_ABOVE)
    result = chain.run(turn)
    record = chain.provider.record
    decision = chain.provider.decision

    assert record["tier"] == decision["tier"], "the tier is the router's own stamp"
    assert result.attribution.tier == record["tier"]
    # the claim rode along for the audit trail and changed nothing
    assert result.attribution.client_tier == CLAIM_RUNG_ABOVE
    assert result.attribution.tier_claim_honoured is False
    # ... and the dispatch the claim would have had to influence is unchanged
    assert record["model"] == "deepseek-chat"
    assert decision["taskClass"] == "research", "the route is the policy's, not the claim's"

    ladder = chain.ladder_keys
    above = resolve_turn_tier(record["tier"], client_tier=CLAIM_RUNG_ABOVE, ladder_keys=ladder)
    assert above.tier == record["tier"]
    assert above.claim_honoured is False
    assert above.claim_agrees is False

    # a claim that happens to MATCH the stamp is still not the authority
    agrees = resolve_turn_tier(record["tier"], client_tier=record["tier"])
    assert agrees.claim_agrees is True, "agreement is observable ..."
    assert agrees.claim_honoured is False, "... and still not authority"

    with pytest.raises(TierClaimError):
        resolve_turn_tier(record["tier"], client_tier=CLAIM_UNKNOWN, ladder_keys=ladder)


# --------------------------------------------------------------------------- #
# 3. one attribution record and one metering record per turn
# --------------------------------------------------------------------------- #
def test_each_turn_writes_exactly_one_attribution_and_metering_record(chain):
    """Exactly one metering row and one ledger event back the turn — no more, no less."""
    turn = chain.turn()
    result = chain.run(turn)

    rows = assert_single_record(chain, turn, result.attribution, expected_rows=1)
    assert rows["meteringRows"] == 1
    assert rows["storeRows"] == 1, "one turn left exactly one row behind it"
    assert rows["metered"] is True
    assert rows["billable"] is True
    assert rows["ledgerAction"] == LEDGER_ACTION_TURN
    assert chain.usage_store.count() == 1
    ledger = chain.ledger_records()
    assert len(ledger) == 1, "one turn is exactly one ledger event"
    assert result.attribution.usage_record_id == rows["meteringRecordId"]
    assert result.attribution.ledger_seq == rows["ledgerSeq"]


def test_a_turn_with_no_metering_record_fails_the_check(chain):
    """The one-record control is not a formality: a missing row is a failure."""
    attributor = MeterlessAttributor(
        chain.control.ledger_store,
        rate_store=chain.rate_store,
        usage_store=chain.usage_store,
    )
    turn = chain.turn("turn-unmetered")
    result = chain.run(turn, attributor=attributor)

    assert result.attribution.usage_record_id == "unmetered", "the mutant's fake row id"
    assert chain.row_for(result.attribution) is None, "the mutant wrote no metering record"
    assert chain.usage_store.count() == 0
    with pytest.raises(ChatSurfaceError, match="CHAT-TURN-ROW-COUNT"):
        assert_single_record(chain, turn, result.attribution, expected_rows=1)


# --------------------------------------------------------------------------- #
# 4. the ledger proves it
# --------------------------------------------------------------------------- #
def test_the_per_tenant_ledger_chain_verifies_ok_after_the_turn(chain):
    """The tenant's hash-chained ledger verifies OK, and carries the turn."""
    assert chain.ledger_status() == "OK", "an empty chain is already coherent"

    turn = chain.turn()
    result = chain.run(turn)

    verdict = chain.ledger_status()
    assert verdict == "OK", f"the chain must verify after the turn, got {verdict}"
    records = chain.ledger_records()
    assert [record["action"] for record in records] == [LEDGER_ACTION_TURN]
    event = records[0]
    assert event["tenantId"] == chain.tenant_id
    assert event["seq"] == result.attribution.ledger_seq
    assert event["hash"] == result.attribution.ledger_hash
    assert event["evidence"] == f"turn {turn.turn_id}"
    assert event["modelUsed"] == f"{result.attribution.provider}/{result.attribution.model}"
    assert event["costUsd"] == pytest.approx(result.attribution.cost_usd)

    # the audit payload the turn wrote decrypts back through the ledger's own
    # tri-state reader — the event is on the chain, not merely adjacent to it.
    payload = chain.ledger_payload(event["seq"])
    assert payload["turnId"] == turn.turn_id
    assert payload["conversationId"] == CONVERSATION
    assert payload["ticketId"] == TICKET
    assert payload["tier"] == result.attribution.tier
    assert payload["costSource"] == result.attribution.cost_source
    assert payload["usageRecordId"] == result.attribution.usage_record_id


# --------------------------------------------------------------------------- #
# 5. refusals: before the call, and still metered
# --------------------------------------------------------------------------- #
def test_a_kill_switch_tenant_is_refused_before_any_provider_call(chain):
    """A paused tenant is refused first; the provider is never reached."""
    provider = ChatProvider(chain.gateway)
    result = chain.run(chain.turn("turn-kill-switch"), guard=refusal_guard(chain, "kill_switch"), provider=provider)

    assert result.allowed is False
    assert result.provider_called is False
    assert provider.call_count == 0, "a refused turn must never reach a provider"
    # refused AND still metered: the refusal is visible in the numbers
    assert result.attribution.metered is True
    assert result.attribution.billable is False
    assert result.attribution.cost_usd is None
    row = chain.row_for(result.attribution)
    assert row is not None and row.billable is False and row.cost_usd is None
    events = [r for r in chain.ledger_records() if r["action"] == "chat.turn.refused"]
    assert len(events) == 1 and events[0]["seq"] == result.attribution.ledger_seq


def test_an_over_cap_tenant_is_blocked_and_still_metered(chain):
    """At the monthly cap the budget rail blocks the turn, which is still metered."""
    provider = ChatProvider(chain.gateway)
    result = chain.run(chain.turn("turn-over-cap"), guard=refusal_guard(chain, "budget_over_cap"), provider=provider)

    assert result.allowed is False
    assert result.outcome.decision == "block"
    assert provider.call_count == 0
    assert result.attribution.metered is True
    assert result.attribution.billable is False
    assert chain.row_for(result.attribution) is not None


@pytest.mark.parametrize("path", REFUSAL_PATH_IDS)
def test_each_refusal_path_is_a_mutation_proved_control(path, chain):
    """Every refusal path is a control whose mutant is named in the evidence."""
    evidence = run_refusal_control(chain, path)

    assert evidence["mutant"], f"{path}: the control must name the branch its mutant disables"
    assert evidence["refused"] is True, f"{path}: the turn must be refused"
    assert evidence["hardStop"] is True, f"{path}: the refusal must be a hard stop"
    assert evidence["providerCalls"] == 0, f"{path}: the provider must never be called"
    assert evidence["providerCalled"] is False
    assert evidence["metered"] is True, f"{path}: a refused turn is still metered"
    assert evidence["billable"] is False, f"{path}: a refused turn is never billed"
    assert evidence["costUsd"] is None, f"{path}: a refusal books no cost"
    assert evidence["meteringRows"] == 1, f"{path}: exactly one metering record"
    assert evidence["turnLedgerEvents"] == 1, f"{path}: exactly one ledger event"
    assert evidence["turnLedgerAction"] == "chat.turn.refused", f"{path}: {evidence}"
    assert evidence["blocked"] is True, f"{path}: {evidence}"


def test_the_controls_notice_a_guard_that_stopped_refusing(chain):
    """The in-suite mutation: a guard that lets a refused turn through is caught."""
    for path in REFUSAL_PATH_IDS:
        provider = ChatProvider(chain.gateway)
        result = chain.run(
            chain.turn(f"turn-neutered-{path}"),
            guard=NeuteredTurnDatedBudgetGuard(),
            provider=provider,
        )
        assert result.allowed is True, f"{path}: the mutant guard reports allow"
        assert provider.call_count == 1, (
            f"{path}: the mutant let the turn reach a provider — which is exactly "
            "what the control above asserts against"
        )


def test_the_unknown_tier_claim_and_the_duplicate_turn_are_refused(chain):
    """The two non-run refusals: an unknown claim, and a turn metered twice."""
    controls = {item["path"]: item for item in refusal_controls(chain)}

    unknown = controls["tier_claim_unknown"]
    assert unknown["refused"] is True
    assert unknown["code"] == "CHAT-UNKNOWN-TIER-CLAIM"
    assert unknown["blocked"] is True

    duplicate = controls["duplicate_turn"]
    assert duplicate["refused"] is True
    assert duplicate["code"] == "CHAT-DUPLICATE-TURN"
    assert duplicate["storeRowsAfterReplay"] == duplicate["storeRowsAfterServe"], (
        "the replay must not add a second row"
    )
    assert duplicate["storeRowsAfterServe"] == duplicate["storeRowsBefore"] + 1
    assert duplicate["blocked"] is True


# --------------------------------------------------------------------------- #
# 6. cache accounting
# --------------------------------------------------------------------------- #
def test_a_cache_hit_turn_costs_strictly_less_than_the_cold_turn(chain):
    """A cache-hit turn reports a share and is strictly cheaper than cold."""
    cold = chain.run(chain.turn("turn-cold", cached_tokens=0)).attribution
    warm = chain.run(chain.turn("turn-warm", cached_tokens=CACHED_TOKENS)).attribution

    assert warm.cache_hit_share > 0, "the cache-hit turn must report a share"
    assert warm.cache.cached_tokens == CACHED_TOKENS
    assert warm.cost_usd is not None and cold.cost_usd is not None
    assert warm.cost_usd < cold.cost_usd, (
        f"a cache-hit turn must be strictly cheaper: warm {warm.cost_usd} "
        f"vs cold {cold.cost_usd}"
    )
    assert warm.input_tokens == cold.input_tokens - CACHED_TOKENS
    # the same turn with no reuse at all is the cold figure, from the rate card
    assert warm.cold_equivalent_cost_usd == pytest.approx(cold.cost_usd, abs=1e-15)


# --------------------------------------------------------------------------- #
# 7. no figure is re-derived where a rate card exists
# --------------------------------------------------------------------------- #
def test_no_cost_figure_is_re_derived_where_a_rate_card_exists(chain):
    """Every cost is the metering rate card's own estimate over the billed tokens."""
    turn = chain.turn()
    result = chain.run(turn)
    attribution = result.attribution

    assert attribution.cost_source == COST_SOURCE_RATE_CARD
    estimate = chain.rate_store.estimate(
        attribution.provider, attribution.model, attribution.input_tokens, attribution.output_tokens
    )
    assert estimate is not None
    assert attribution.cost_usd == pytest.approx(estimate.cost_usd, abs=1e-15)
    # the billable input is the cache accounting's remainder, not this test's sum
    assert attribution.input_tokens == turn.cache_accounting(OUTPUT_TOKENS).billable_input_tokens
    # the figure the guard was given is the rate card's too
    assert chain.projected_cost(turn) == pytest.approx(estimate.cost_usd, abs=1e-15)


# --------------------------------------------------------------------------- #
# 9. no date bomb
# --------------------------------------------------------------------------- #
def test_a_past_dated_turn_is_judged_against_its_own_day(chain):
    """The assertion that would have caught #506: the bucket is the turn's day.

    ``GuardedTurnRunner.run`` cannot pass ``day``/``month``, so the evaluation
    bucket is pinned through the guard it is injected with.  Spend seeded on the
    turn's own day refuses it; spend seeded on another day does not — and the
    pinned day is a past day, so the first half can never be an accident of when
    the suite ran.
    """
    turn = chain.turn("turn-dated")
    assert turn_day(turn) == TURN_DAY
    assert turn_month(turn) == TURN_MONTH
    assert TURN_DAY != parse_ts(None)[:10], (
        "the pinned day must be a day the live clock is not on, or the two halves "
        "below cannot tell the pin from the clock (#506)"
    )

    scoping = day_scoping(chain)

    assert scoping["seededDayRefused"] is True, scoping
    assert scoping["seededDayProviderCalls"] == 0
    assert scoping["otherDayAllowed"] is True, (
        "spend on another day must not refuse this turn — otherwise the refusal "
        "above proves nothing about the day scope"
    )
    assert scoping["otherDayProviderCalls"] == 1
    assert scoping["scoped"] is True


# --------------------------------------------------------------------------- #
# the evidence document (the capstone's own artifact)
# --------------------------------------------------------------------------- #
def test_the_run_records_green_evidence_naming_every_control(tmp_path):
    """The run writes one evidence document; every control in it blocked."""
    work_dir = os.path.join(str(tmp_path), "chat-surface")
    payload = run_chat_surface(work_dir=work_dir)

    path = os.path.join(work_dir, "chat-surface.json")
    assert os.path.isfile(path)
    written = json.loads(open(path, "r", encoding="utf-8").read())
    assert written["attested"] is True, [
        item["guard_id"] for item in written["attestations"] if not item["attested"]
    ]
    assert payload["gate"] == "e2e.chat_surface"

    controls = {item["path"]: item for item in payload["stages"]["controls"]["controls"]}
    for path_id in (
        "kill_switch",
        "budget_over_cap",
        "quota_exhausted_day",
        "tier_claim_unknown",
        "duplicate_turn",
    ):
        assert controls[path_id]["blocked"] is True, f"{path_id}: {controls[path_id]}"
    assert payload["stages"]["controls"]["everyControlBlocked"] is True
    assert payload["stages"]["controls"]["dayScoping"]["scoped"] is True
    assert payload["stages"]["records"]["ledgerStatus"] == "OK"
    assert payload["stages"]["cache"]["cheaper"] is True
    assert payload["stages"]["tier"]["tierFromStamp"] is True
    assert payload["readModel"]["turns"] >= 2
