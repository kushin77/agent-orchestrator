"""The composed turn guard, end to end (issue #507 acceptance across the lane)."""

from __future__ import annotations

import json

import pytest

from guardrails.chat.egress import OutboundTurn
from guardrails.chat.policy import ControlBinding
from guardrails.chat.verdict import DecisionLevel

from chat_fixtures import seeded_secret

THREE_LEVELS = {"block", "warn", "log"}


def test_a_clean_grounded_turn_is_allowed_and_every_guard_runs(guard, clean_turn, benign_envelope):
    outcome = guard.guard_turn(clean_turn, envelope=benign_envelope)

    assert outcome.decision is DecisionLevel.LOG
    assert outcome.allowed is True
    assert outcome.aborted is False
    assert outcome.undecidable is False
    assert [item.guard for item in outcome.guards()] == ["retrieval", "egress", "policy"]
    assert all(item.ran for item in outcome.guards())
    assert "<untrusted>" in outcome.dispatch_text
    assert "handover note" in outcome.dispatch_text


def test_every_recorded_verdict_is_one_of_the_three_levels(guard, clean_turn, poisoned_envelope, secret_turn):
    for outcome in (
        guard.guard_turn(clean_turn),
        guard.guard_turn(clean_turn, envelope=poisoned_envelope),
        guard.guard_turn(secret_turn),
    ):
        for recorded in outcome.guards():
            assert recorded.decision.value in THREE_LEVELS
            if not recorded.ran:
                assert recorded.decision is DecisionLevel.BLOCK, "undecidable must not log ok"


def test_a_poisoned_retrieved_source_aborts_the_turn_and_never_reaches_the_prompt(
    guard, clean_turn, poisoned_envelope
):
    from chat_fixtures import POISONED_TICKET_TEXT

    outcome = guard.guard_turn(clean_turn, envelope=poisoned_envelope)

    assert outcome.decision is DecisionLevel.BLOCK
    assert outcome.aborted is True
    assert outcome.dispatch_text == ""
    record = json.dumps(outcome.to_dict(), sort_keys=True)
    assert POISONED_TICKET_TEXT not in record
    assert "Ignore all previous instructions" not in record
    assert outcome.retrieval.quarantined_ids == ("ticket:OPS-1187",)
    assert "inj.override.ignore_previous" in outcome.retrieval.outcome.reason


def test_a_benign_twin_document_keeps_the_same_turn_allowed(guard, clean_turn, benign_envelope):
    """The refusal above is caused by the poisoned document, not by a blanket denial."""
    outcome = guard.guard_turn(clean_turn, envelope=benign_envelope)
    assert outcome.retrieval.decision is DecisionLevel.LOG
    assert outcome.allowed is True


def test_a_seeded_secret_aborts_the_turn_and_is_never_echoed(guard, secret_turn):
    secret = seeded_secret()
    outcome = guard.guard_turn(secret_turn)

    assert outcome.decision is DecisionLevel.BLOCK
    assert outcome.aborted is True
    assert outcome.dispatch_text == ""
    assert outcome.allowed is False
    record = json.dumps(outcome.to_dict(), sort_keys=True)
    assert secret not in record
    assert "secret.generic_api_key" in record


def test_a_suspicious_document_is_admitted_warned_and_delimited(
    guard, clean_turn, suspicious_envelope
):
    from chat_fixtures import SUSPICIOUS_TICKET_TEXT

    outcome = guard.guard_turn(clean_turn, envelope=suspicious_envelope)

    assert outcome.decision is DecisionLevel.WARN
    assert outcome.allowed is True
    assert "<untrusted>" in outcome.dispatch_text
    assert SUSPICIOUS_TICKET_TEXT in outcome.dispatch_text
    assert outcome.retrieval.decision is DecisionLevel.WARN


def test_an_unavailable_controls_registry_refuses_the_turn(clean_turn, tmp_path, guard):
    outcome = guard.guard_turn(
        clean_turn, controls=ControlBinding.from_path(tmp_path / "absent-controls.yaml")
    )

    assert outcome.decision is DecisionLevel.BLOCK
    assert outcome.aborted is True
    assert outcome.undecidable is True
    assert outcome.policy.ran is False
    assert outcome.to_dict()["undecidable"] is True


def test_an_unreadable_grounding_envelope_refuses_the_turn(guard, clean_turn):
    outcome = guard.guard_turn(clean_turn, envelope={"fragments": [{"text": "no id"}]})

    assert outcome.decision is DecisionLevel.BLOCK
    assert outcome.retrieval.outcome.ran is False
    assert outcome.aborted is True
    assert outcome.dispatch_text == ""


def test_a_warned_turn_still_dispatches_its_redacted_payload(guard, pii_turn):
    outcome = guard.guard_turn(pii_turn)

    assert outcome.decision is DecisionLevel.WARN
    assert outcome.allowed is True
    assert "ada.lovelace@contoso.example" not in outcome.dispatch_text
    assert "<REDACTED_EMAIL>" in outcome.dispatch_text


def test_inbound_validation_after_a_dispatched_turn(guard, clean_turn, benign_envelope):
    turn = guard.guard_turn(clean_turn, envelope=benign_envelope)
    outcome = guard.validate_output(
        turn,
        {
            "text": "The restore finished at 04:12 UTC, and the vendor advisory is unrelated.",
            "claims": [
                {"text": "the restore finished at 04:12 UTC", "source_id": "ticket:OPS-1187"},
                {"text": "file the vendor advisory against the change record"},
            ],
        },
    )

    assert outcome.decision is DecisionLevel.WARN
    assert outcome.accepted is True
    assert outcome.flagged_ids == (1,)
    assert guard.record(turn, outcome)["inbound_decision"] == "warn"


def test_inbound_validation_refuses_an_invented_authority(guard, clean_turn, benign_envelope):
    turn = guard.guard_turn(clean_turn, envelope=benign_envelope)
    outcome = guard.validate_output(
        turn,
        {
            "text": "Per the vendor advisory, patch immediately.",
            "claims": [{"text": "patch immediately", "source_id": "vendor:advisory-2026-11"}],
        },
    )

    assert outcome.decision is DecisionLevel.BLOCK
    assert outcome.accepted is False
    assert "never supplied" in outcome.claims[0].reason


def test_inbound_validation_infers_the_admitted_grounding_from_the_turn(
    guard, clean_turn, benign_envelope
):
    turn = guard.guard_turn(clean_turn, envelope=benign_envelope)
    grounded = guard.validate_output(
        turn,
        {
            "text": "The restore finished at 04:12 UTC.",
            "claims": [
                {
                    "text": "the restore finished at 04:12 UTC",
                    "source_id": "ticket:OPS-1187",
                    "quote": "restore job completed at 04:12 UTC",
                }
            ],
        },
    )
    assert grounded.decision is DecisionLevel.LOG

    invented = guard.validate_output(
        turn,
        {
            "text": "An unrelated claim.",
            "claims": [{"text": "unrelated claim", "source_id": "kb:never-supplied"}],
        },
    )
    assert invented.decision is DecisionLevel.BLOCK


def test_an_answer_for_a_refused_turn_is_undecidable(guard, secret_turn):
    turn = guard.guard_turn(secret_turn)
    assert turn.aborted is True

    outcome = guard.validate_output(turn, {"text": "Sure, here is the summary.", "claims": []})

    assert outcome.decision is DecisionLevel.BLOCK
    assert outcome.outcome.ran is False
    assert outcome.accepted is False


def test_verdicts_attach_only_on_an_exact_identifier_match(guard, clean_turn):
    turn = guard.guard_turn(clean_turn, turn_id="turn-42")
    records = [
        {"event_type": "scrub_blocked", "call_id": "turn-42"},
        {"event_type": "egress_denied", "call_id": "turn-420"},
        {"event_type": "sbom_scan", "severity": "warning"},
    ]
    split = guard.attach_verdicts(turn, records)

    assert [record["call_id"] for record in split.attached] == ["turn-42"]
    assert len(split.standalone) == 2
    assert split.attached[0] is records[0]


def test_a_turn_id_is_minted_when_the_caller_supplies_none(guard, clean_turn):
    first = guard.guard_turn(clean_turn)
    second = guard.guard_turn(clean_turn)
    assert first.turn_id
    assert first.turn_id != second.turn_id
    assert guard.attach_verdicts(first, [{"call_id": first.turn_id}]).attached


def test_the_record_is_safe_to_log(guard, pii_turn, secret_turn):
    """The record names rules, counts and hashes - the input text never rides it."""
    allowed = guard.guard_turn(pii_turn).to_dict()
    assert "ada.lovelace@contoso.example" not in json.dumps(allowed, sort_keys=True)
    assert "<REDACTED_EMAIL>" in allowed["dispatch_text"]
    assert allowed["egress"]["components"][0]["redacted"] is True

    refused = guard.guard_turn(secret_turn).to_dict()
    assert refused["dispatch_text"] == ""
    assert seeded_secret() not in json.dumps(refused, sort_keys=True)


def test_tool_arguments_travel_through_the_turn_guard(guard):
    turn = OutboundTurn(
        user_prompt="Call the ticket tool.",
        tool_arguments={"summary": "the restore finished at 04:12 UTC"},
    )
    outcome = guard.guard_turn(turn)

    assert outcome.allowed is True
    assert "tool_arguments:summary" in outcome.dispatch_text
    assert "the restore finished at 04:12 UTC" in outcome.dispatch_text


@pytest.mark.parametrize("fragment_text", ["", "   "])
def test_an_empty_retrieved_fragment_is_not_a_pass(guard, clean_turn, fragment_text):
    """A fragment that carries nothing is scanned and reported, never assumed fine."""
    outcome = guard.guard_turn(
        clean_turn, envelope={"fragments": [{"source_id": "kb:empty", "text": fragment_text}]}
    )
    assert outcome.retrieval.decision is DecisionLevel.LOG
    assert outcome.retrieval.outcome.evidence["fragments_considered"] == 1
    assert outcome.allowed is True
