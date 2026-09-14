"""Telemetry: the kill switch bites before the model, and every turn is attributed.

``telemetry/chat`` owns two things this surface must not re-invent: the
pre-dispatch budget refusal (with the platform kill switch as its first rail) and
the one attribution row per turn.  The refusal path is asserted against a
**counting sink** — an empty audit sink is the only proof a refused turn never
reached a provider — and against the metering row, because a refusal that is not
metered is invisible in the numbers the operator reads.
"""

from __future__ import annotations

import pytest

from chat_fixtures import (
    TENANT,
    grounded_payload,
    token_of,
    with_grounding,
)

from gateway.chat.errors import ChatSurfaceError


def paused_guard(ts: str):
    """The shipped rails with the platform kill switch engaged."""
    from telemetry.budgets.killswitch import KillSwitchController, KillSwitchState
    from telemetry.chat.budget_guard import TurnBudgetGuard

    return TurnBudgetGuard(
        killswitch=KillSwitchController(
            initial=KillSwitchState(
                global_pause=True, reason="incident-1", paused_by="ops", timestamp=ts
            )
        )
    )


def test_the_kill_switch_refuses_before_the_model_call(
    rigged, body, credential, grounding, attributor, usage_store
):
    rigged.surface.budget_guard = paused_guard("2026-09-14T09:00:00Z")
    rigged.script(grounded_payload())
    with pytest.raises(ChatSurfaceError) as raised:
        rigged.surface.completions(
            with_grounding(body, grounding), token=token_of(credential)
        )
    refusal = raised.value
    assert refusal.status == 429
    assert refusal.to_openai_error()["error"]["code"] == "budget_blocked"
    assert rigged.records == [], "a paused platform still reached a provider"
    # refused AND attributed: the spend that did not happen is visible
    assert refusal.extra["attribution"]["billable"] is False
    assert usage_store.count() == 1


def test_a_served_turn_writes_one_metering_row_and_one_ledger_event(
    rigged, body, credential, grounding, ledger, attributor, usage_store
):
    rigged.script(grounded_payload())
    response = rigged.surface.completions(
        with_grounding(body, grounding), token=token_of(credential)
    )
    assert usage_store.count() == 1
    rows = ledger.records(TENANT)
    assert len(rows) == 1
    assert rows[0]["action"] == "chat.turn"
    attribution = response["ao"]["attribution"]
    assert attribution["turnId"] == response["ao"]["turnId"]
    assert attribution["billable"] is True
    assert attribution["provider"] == "deepseek"
    assert attribution["tier"] == "LOW"
    # the tier came from the chooser's stamp, never from the client's claim
    assert attribution["tierClaimHonoured"] is False
    assert attribution["usageRecordId"]


def test_one_turn_is_metered_exactly_once(rigged, body, credential, grounding, usage_store):
    rigged.script(grounded_payload())
    for _ in range(2):
        rigged.script(grounded_payload())
        rigged.surface.completions(
            with_grounding(body, grounding), token=token_of(credential)
        )
    assert usage_store.count() == 2, "two turns must be two rows, not a duplicate"


def test_the_surface_composes_the_same_verdicts_as_the_fused_runner(
    rigged, body, credential, grounding, attributor
):
    """``GuardedTurnRunner`` is the fused form of the surface's own two steps."""
    rigged.script(grounded_payload())
    paused = paused_guard("2026-09-14T09:00:00Z")
    rigged.surface.budget_guard = paused
    with pytest.raises(ChatSurfaceError) as raised:
        rigged.surface.completions(
            with_grounding(body, grounding), token=token_of(credential)
        )
    surface_verdict = raised.value.extra["budget"]
    runner = rigged.surface.guarded_runner()
    fused = runner.run(
        surface_verdict_turn(rigged, credential),
        lambda turn: (_ for _ in ()).throw(AssertionError("provider was called")),
    )
    assert fused.provider_called is False
    assert fused.outcome.allowed is False
    assert fused.outcome.decision == surface_verdict["decision"]
    assert fused.outcome.code == surface_verdict["code"]


def surface_verdict_turn(rigged, credential):
    """The ``ChatTurn`` the surface builds for one turn (same scope, same ids)."""
    from telemetry.chat.model import ChatTurn

    return ChatTurn(
        turn_id="turn-parity",
        conversation_id=credential.conversation_id,
        tenant_id=credential.tenant_id,
        agent_id=credential.agent_id,
        ts="2026-09-14T09:00:00Z",
        client_tier="MED",
        static_prefix="",
        user_delta="What happened in OPS-1187?",
        prompt_module="chat-answer",
    )


def test_an_unattributable_dispatch_is_metered_as_a_refusal(
    rigged, body, grounding, mint, usage_store
):
    """A denied capability has no tier stamp: attributed as a refusal, not a guess."""
    from chat_fixtures import refusal_payload

    credential = mint(agent_id="architecture-sme")
    rigged.script(refusal_payload())
    with pytest.raises(ChatSurfaceError):
        rigged.surface.completions(
            with_grounding(body, grounding), token=token_of(credential)
        )
    assert usage_store.count() == 1
    row = usage_store.read()[0]
    assert row.billable is False
