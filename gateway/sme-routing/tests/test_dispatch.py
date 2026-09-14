"""Cost-control tests: per-tier caps enforced, escalation bounded, human terminal.

The three claims this file proves, all by behaviour:

1. exceeding a tier's timeout / token cap is an EXPLICIT refusal -- never a
   silent pass (``--no-escalate`` semantics);
2. with escalation authorised the ladder climbs the declared fallbacks and each
   rung is recorded;
3. when no higher tier exists the escalation TERMINATES at the distinct
   ``human_advisor`` outcome -- no exception, no unbounded loop.
"""

from __future__ import annotations

import pytest

from router import DISPATCHED, HUMAN_ADVISOR, REFUSED


# --- within caps -------------------------------------------------------------
def test_request_within_caps_dispatches_on_the_routed_tier(router) -> None:
    outcome = router.dispatch(
        {"type": "doc_update", "text": "tighten the README", "tokens": 40}
    )
    assert outcome.status == DISPATCHED
    assert outcome.tier == "flash"
    assert outcome.dispatched is True
    assert outcome.escalated is False
    assert len(outcome.attempts) == 1


def test_absent_budget_uses_the_tier_caps(router) -> None:
    outcome = router.dispatch({"text": "neutral wording"})
    assert outcome.status == DISPATCHED
    assert outcome.tier == "pro"  # the fail-safe deep path


# --- cap breaches are explicit ----------------------------------------------
def test_token_cap_breach_is_refused_explicitly(router) -> None:
    outcome = router.dispatch(
        {"type": "doc_update", "text": "tighten the README", "tokens": 9000},
        escalate=False,
    )
    assert outcome.status == REFUSED
    assert outcome.dispatched is False
    assert outcome.tier == "flash"
    assert "token cap breached" in outcome.reason
    assert "9000" in outcome.reason
    assert "4096" in outcome.reason
    assert outcome.attempts[0].accepted is False


def test_timeout_cap_breach_is_refused_explicitly(router) -> None:
    outcome = router.dispatch(
        {"text": "neutral wording", "tokens": 40, "timeout_seconds": 45},
        escalate=False,
    )
    assert outcome.status == REFUSED
    assert "timeout cap breached" in outcome.reason
    assert outcome.tier == "flash"


def test_a_breach_never_silently_falls_through(router) -> None:
    # A refused outcome must NOT be reported as dispatched on any tier.
    outcome = router.dispatch(
        {"type": "doc_update", "text": "neutral", "tokens": 9000}, escalate=False
    )
    assert outcome.status == REFUSED
    assert all(not attempt.accepted for attempt in outcome.attempts)


@pytest.mark.parametrize("tier_name", ("flash", "pro", "auditor"))
def test_check_caps_is_exact_at_the_boundary(router, tier_name: str) -> None:
    spec = router.tiers.tier(tier_name)
    accepted, reason = router.check_caps(
        tier_name, tokens=spec.max_tokens, timeout_seconds=spec.timeout_seconds
    )
    assert accepted is True
    assert "within tier" in reason

    accepted_token, token_reason = router.check_caps(tier_name, tokens=spec.max_tokens + 1)
    assert accepted_token is False
    assert str(spec.max_tokens + 1) in token_reason

    accepted_timeout, timeout_reason = router.check_caps(
        tier_name, timeout_seconds=spec.timeout_seconds + 1
    )
    assert accepted_timeout is False
    assert str(spec.timeout_seconds) in timeout_reason


# --- escalation up the declared ladder --------------------------------------
def test_escalation_climbs_to_the_declared_fallback(router) -> None:
    outcome = router.dispatch(
        {"type": "doc_update", "text": "tighten the README", "tokens": 9000}
    )
    assert outcome.status == DISPATCHED
    assert outcome.tier == "pro"
    assert outcome.escalated is True
    assert [attempt.tier for attempt in outcome.attempts] == ["flash", "pro"]
    assert outcome.attempts[0].accepted is False
    assert outcome.attempts[1].accepted is True


def test_escalation_skips_a_tier_that_cannot_take_the_request(router) -> None:
    # 40000 exceeds pro as well, so the ladder lands on auditor.
    outcome = router.dispatch(
        {"type": "doc_update", "text": "neutral", "tokens": 30000}
    )
    assert outcome.status == DISPATCHED
    assert outcome.tier == "auditor"
    assert [attempt.tier for attempt in outcome.attempts] == ["flash", "pro", "auditor"]


def test_escalation_follows_the_policy_fallbacks_not_a_hard_coded_order(router) -> None:
    for name in router.tiers.tier_order:
        spec = router.tiers.tier(name)
        assert spec.fallback is None or spec.fallback in router.tiers.tiers
    assert router.tiers.tier("flash").fallback == "pro"
    assert router.tiers.tier("pro").fallback == "auditor"
    assert router.tiers.tier("auditor").fallback is None


# --- the terminal human/advisor hand-off ------------------------------------
def test_escalation_terminates_at_the_human_advisor_outcome(router) -> None:
    outcome = router.dispatch(
        {"type": "doc_update", "text": "neutral", "tokens": 40000}
    )
    assert outcome.status == HUMAN_ADVISOR
    assert outcome.status not in (DISPATCHED, REFUSED)
    assert outcome.dispatched is False
    assert outcome.tier == "auditor"
    assert [attempt.tier for attempt in outcome.attempts] == ["flash", "pro", "auditor"]
    assert all(not attempt.accepted for attempt in outcome.attempts)
    assert "human/advisor" in outcome.reason


def test_the_terminal_outcome_is_not_an_exception(router) -> None:
    # An unhandled exception here would be a hard failure, not a terminal state.
    outcome = router.dispatch({"text": "neutral", "tokens": 999999})
    assert outcome.status == HUMAN_ADVISOR


def test_escalation_is_bounded_and_cannot_loop(router) -> None:
    outcome = router.dispatch({"text": "neutral", "tokens": 999999})
    assert len(outcome.attempts) <= len(router.tiers.tier_order)
    assert len({attempt.tier for attempt in outcome.attempts}) == len(outcome.attempts)


def test_a_breach_on_the_terminal_tier_goes_straight_to_human(router) -> None:
    outcome = router.dispatch({"text": "production secret rotation", "tokens": 40000})
    assert outcome.decision.route == "strict"
    assert outcome.decision.tier == "auditor"
    assert outcome.status == HUMAN_ADVISOR
    assert [attempt.tier for attempt in outcome.attempts] == ["auditor"]


# --- serialisation ----------------------------------------------------------
def test_outcome_serialises_to_json_shape(router) -> None:
    payload = router.dispatch({"text": "neutral"}).as_dict()
    assert set(payload) == {"status", "tier", "escalated", "attempts", "reason", "decision"}
    assert set(payload["attempts"][0]) == {"tier", "accepted", "reason"}
    assert set(payload["decision"]) >= {"route", "tier", "chain", "caps"}
