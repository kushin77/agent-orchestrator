"""Tier discipline: the chooser owns the tier, escalation needs evidence.

Each test here fails if the chat surface ever starts trusting a client tier or
granting an escalation the chooser did not earn.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from telemetry.chat.tiering import (
    CODE_ESCALATION_NOT_OBSERVED,
    CODE_UNKNOWN_TIER_CLAIM,
    CODE_UNRESOLVED_TIER,
    EscalationRefused,
    TierClaimError,
    TierUnresolvedError,
    escalate_on_observed_failure,
    resolve_turn_tier,
)

LADDER = ("L0", "L1", "L2")


def test_the_records_routing_stamp_is_the_authority(world):
    resolution = resolve_turn_tier("L0")

    assert resolution.tier == "L0"
    assert resolution.source == "gateway_record"
    assert resolution.claim_honoured is False


def test_a_client_tier_claim_is_recorded_and_never_honoured(world):
    resolution = resolve_turn_tier("L0", client_tier="L2", ladder_keys=LADDER)

    assert resolution.tier == "L0", "the chooser's stamp wins, not the client's ask"
    assert resolution.client_tier == "L2"
    assert resolution.claim_honoured is False
    assert resolution.claim_agrees is False
    assert "ignored" in resolution.reason


def test_an_agreeing_claim_still_is_not_the_authority():
    resolution = resolve_turn_tier("L1", client_tier="L1", ladder_keys=LADDER)

    assert resolution.tier == "L1"
    assert resolution.claim_honoured is False
    assert resolution.claim_agrees is True


def test_a_missing_routing_stamp_is_unresolved_even_with_a_client_claim():
    with pytest.raises(TierUnresolvedError) as excinfo:
        resolve_turn_tier(None, client_tier="L2", ladder_keys=LADDER)

    assert CODE_UNRESOLVED_TIER in str(excinfo.value)
    assert "cannot stand in for the chooser" in str(excinfo.value)


def test_an_unknown_client_tier_claim_is_refused():
    with pytest.raises(TierClaimError) as excinfo:
        resolve_turn_tier("L0", client_tier="ultra", ladder_keys=LADDER)

    assert CODE_UNKNOWN_TIER_CLAIM in str(excinfo.value)


def test_escalation_without_observed_difficulty_is_refused():
    chooser = FakeChooser()

    with pytest.raises(EscalationRefused) as excinfo:
        escalate_on_observed_failure(
            chooser, Choice("L0"), observed_failures=0, client_requested_tier="L2"
        )

    assert CODE_ESCALATION_NOT_OBSERVED in str(excinfo.value)
    assert chooser.calls == [], "nothing may be escalated on a client's word"


def test_escalation_moves_one_rung_on_observed_difficulty_only():
    chooser = FakeChooser()
    escalated = escalate_on_observed_failure(
        chooser,
        Choice("L0"),
        observed_failures=1,
        client_requested_tier="L2",
    )

    assert escalated.tier == "L1", "one observed failure moves one rung, not to L2"
    assert chooser.calls == [("escalate", "observed-failure x1")]


@dataclass(frozen=True)
class Choice:
    tier: str


class FakeChooser:
    """Records what the tier discipline asked it to do (the real one is injected)."""

    def __init__(self) -> None:
        self.calls: list = []

    def escalate_on_failure(self, choice, trigger="failure", tokens=None):
        self.calls.append(("escalate", trigger))
        return Choice(tier="L1")
