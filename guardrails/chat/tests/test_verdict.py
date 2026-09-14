"""Tri-state, fail-closed verdicts and the exact-match attachment rule (issue #507 AC4)."""

from __future__ import annotations

import inspect

import pytest

from guardrails.chat.verdict import (
    DecisionLevel,
    GuardOutcome,
    aggregate,
    all_ran,
    attach,
    decided,
    finding,
    identifier_of,
    undecidable,
)

THREE_LEVELS = {"block", "warn", "log"}


def test_aggregate_takes_the_strongest_decision():
    outcomes = [
        decided("retrieval", DecisionLevel.LOG, "clean"),
        decided("egress", DecisionLevel.WARN, "redacted"),
        decided("inbound", DecisionLevel.LOG, "claims accounted for"),
    ]
    assert aggregate(outcomes) is DecisionLevel.WARN

    outcomes.append(decided("policy", DecisionLevel.BLOCK, "control refused"))
    assert aggregate(outcomes) is DecisionLevel.BLOCK


def test_a_guard_that_did_not_run_forces_block_and_is_never_log():
    """An undecidable guard must not be readable as a pass (fail-closed)."""
    outcome = undecidable("egress", "dlp scrub catalog unavailable")
    assert outcome.ran is False
    assert outcome.decision is DecisionLevel.BLOCK
    assert outcome.decision is not DecisionLevel.LOG

    only_clean = [decided("inbound", DecisionLevel.LOG, "clean"), outcome]
    assert aggregate(only_clean) is DecisionLevel.BLOCK
    assert all_ran(only_clean) is False


def test_aggregate_refuses_an_empty_guard_set():
    """A verdict with no guard behind it is undecidable, not a pass."""
    with pytest.raises(ValueError):
        aggregate([])


def test_guard_outcome_requires_a_guard_name():
    with pytest.raises(ValueError):
        decided("", DecisionLevel.LOG, "no guard named")
    with pytest.raises(ValueError):
        undecidable("", "no guard named")


def test_every_guard_verdict_is_one_of_the_three_levels():
    """The vocabulary is BLOCK / WARN / LOG - there is no "OK" verdict to log."""
    verdicts = {
        decided("egress", DecisionLevel.LOG, "clean").to_dict()["decision"],
        decided("egress", DecisionLevel.WARN, "redacted").to_dict()["decision"],
        decided("egress", DecisionLevel.BLOCK, "blocked").to_dict()["decision"],
        undecidable("egress", "could not assess").to_dict()["decision"],
    }
    assert verdicts == THREE_LEVELS
    assert not any(value in {"ok", "pass", "allow", "deny"} for value in verdicts)


def test_finding_has_no_parameter_that_could_carry_a_matched_value(secret):
    """The finding builder physically cannot echo what it matched."""
    assert set(inspect.signature(finding).parameters) == {
        "guard",
        "rule_id",
        "action",
        "klass",
        "count",
        "spans",
    }
    rendered = finding(
        guard="egress",
        rule_id="secret.generic_api_key",
        action="block",
        klass="secret",
        count=1,
        spans=((11, 11 + len(secret)),),
    )
    assert secret not in rendered
    assert "secret.generic_api_key" in rendered
    assert "action=block" in rendered


def test_verdict_attachment_requires_an_exact_identifier_match():
    """Attachment follows the platform rule: exact id, or the verdict stands alone."""
    records = [
        {"event_type": "scrub_blocked", "call_id": "turn-7"},
        {"event_type": "scrub_blocked", "turn_id": "TURN-7"},
        {"event_type": "scrub_blocked", "call_id": "turn-7 "},
        {"event_type": "scrub_blocked", "call_id": "turn-70"},
        {"event_type": "scrub_blocked", "requestId": "turn"},
        {"event_type": "scrub_blocked"},
        {"event_type": "scrub_blocked", "call_id": 7},
    ]
    split = attach(records, turn_id="turn-7")
    assert [record["call_id"] for record in split.attached if "call_id" in record] == ["turn-7"]
    assert len(split.attached) == 1
    assert len(split.standalone) == len(records) - 1
    assert len(split) == len(records)


def test_attachment_reads_the_identifiers_the_platform_emits():
    assert identifier_of({"call_id": "req_1"}) == "req_1"
    assert identifier_of({"requestId": "req_1"}) == "req_1"
    assert identifier_of({"turn_id": "t"}) == "t"
    assert identifier_of({"call_id": ""}) is None
    assert identifier_of({"call_id": None}) is None
    assert identifier_of({}) is None


def test_attachment_refuses_an_empty_turn_id():
    with pytest.raises(ValueError):
        attach([], turn_id="")


def test_guard_outcome_serializes_without_payload_fields():
    outcome = GuardOutcome(
        guard="egress",
        decision=DecisionLevel.WARN,
        reason="redacted before dispatch: pii.email",
        evidence={"rules": ["pii.email"]},
    )
    assert outcome.to_dict() == {
        "guard": "egress",
        "decision": "warn",
        "reason": "redacted before dispatch: pii.email",
        "ran": True,
        "evidence": {"rules": ["pii.email"]},
    }
    assert outcome.blocks is False
