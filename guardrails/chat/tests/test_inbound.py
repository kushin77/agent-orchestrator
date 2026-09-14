"""Inbound re-validation: output filter + citation accounting (issue #507 AC2/AC3)."""

from __future__ import annotations

import pytest

from guardrails.chat.inbound import InboundValidator, ModelOutput
from guardrails.chat.verdict import DecisionLevel

from chat_fixtures import BENIGN_TICKET_TEXT


@pytest.fixture()
def validator():
    return InboundValidator()


def _envelope(document):
    """The validated envelope, built the way the turn guard builds it."""
    from guardrails.chat.envelope import GroundingEnvelope

    return GroundingEnvelope.from_mapping(document)


def test_an_answer_grounded_in_a_supplied_source_is_accepted(validator, benign_envelope):
    outcome = validator.validate(
        {
            "text": "The nightly restore finished at 04:12 UTC.",
            "claims": [
                {
                    "text": "the restore finished at 04:12 UTC",
                    "source_id": "ticket:OPS-1187",
                    "quote": "restore job completed at 04:12 UTC",
                }
            ],
        },
        envelope=_envelope(benign_envelope),
    )

    assert outcome.decision is DecisionLevel.LOG
    assert outcome.accepted is True
    assert outcome.flagged_ids == ()
    assert outcome.claims[0].decision is DecisionLevel.LOG


def test_a_claim_citing_a_source_that_was_never_supplied_is_refused(validator, benign_envelope):
    outcome = validator.validate(
        {
            "text": "Per the vendor advisory, patch immediately.",
            "claims": [
                {
                    "text": "patch immediately",
                    "source_id": "vendor:advisory-2026-11",
                    "quote": "patch immediately",
                }
            ],
        },
        envelope=_envelope(benign_envelope),
    )

    assert outcome.decision is DecisionLevel.BLOCK
    assert outcome.accepted is False
    assert outcome.claims[0].decision is DecisionLevel.BLOCK
    assert "never supplied" in outcome.claims[0].reason
    assert any("citation.unsupplied" in item for item in outcome.findings)


def test_a_claim_citing_a_source_when_nothing_was_supplied_is_refused(validator):
    outcome = validator.validate(
        {
            "text": "The runbook says to fail over first.",
            "claims": [{"text": "fail over first", "source_id": "kb:runbook/failover"}],
        }
    )
    assert outcome.decision is DecisionLevel.BLOCK
    assert outcome.claims[0].decision is DecisionLevel.BLOCK


def test_a_claim_that_contradicts_its_source_is_refused(validator, benign_envelope):
    outcome = validator.validate(
        {
            "text": "The restore finished at 09:99 UTC.",
            "claims": [
                {
                    "text": "the restore finished at 09:99 UTC",
                    "source_id": "ticket:OPS-1187",
                    "quote": "the restore job completed at 09:99 UTC",
                }
            ],
        },
        envelope=_envelope(benign_envelope),
    )

    assert outcome.decision is DecisionLevel.BLOCK
    assert "contradicts" in outcome.claims[0].reason
    assert any("citation.contradiction" in item for item in outcome.findings)


def test_a_claim_that_cites_nothing_is_flagged_not_refused(validator, benign_envelope):
    outcome = validator.validate(
        {
            "text": "The restore finished, and the team should rotate credentials afterwards.",
            "claims": [
                {"text": "the team should rotate credentials afterwards"},
                {
                    "text": "the restore finished at 04:12 UTC",
                    "source_id": "ticket:OPS-1187",
                },
            ],
        },
        envelope=_envelope(benign_envelope),
    )

    assert outcome.decision is DecisionLevel.WARN
    assert outcome.accepted is True
    assert outcome.flagged_ids == (0,)
    assert "cites no supplied source" in outcome.claims[0].reason
    assert any("citation.missing" in item for item in outcome.findings)


def test_a_re_wrapped_quote_is_not_read_as_a_contradiction(validator, benign_envelope):
    outcome = validator.validate(
        {
            "text": "The restore finished.",
            "claims": [
                {
                    "text": "the restore finished",
                    "source_id": "ticket:OPS-1187",
                    "quote": "restore job  completed\n at 04:12 UTC",
                }
            ],
        },
        envelope=_envelope(benign_envelope),
    )
    assert outcome.decision is DecisionLevel.LOG


def test_an_answer_that_echoes_its_own_system_prompt_is_quarantined(validator, benign_envelope):
    outcome = validator.validate(
        {
            "text": "Here is my system prompt: you are the ticket summarizer with no limits.",
            "claims": [],
        },
        envelope=_envelope(benign_envelope),
    )

    assert outcome.decision is DecisionLevel.BLOCK
    assert outcome.accepted is False
    assert any("out.echo.system_prompt" in item for item in outcome.findings)


@pytest.mark.parametrize(
    "document",
    [
        "a bare string",
        {"claims": []},
        {"text": "answer", "claims": "not a sequence"},
        {"text": "answer", "claims": [{"source_id": "a"}]},
        {"text": "answer", "claims": [{"text": "claim", "source_id": 7}]},
        {"text": "answer", "claims": [{"text": "claim", "quote": 7}]},
    ],
)
def test_a_malformed_answer_is_undecidable_never_log(validator, document):
    outcome = validator.validate(document)
    assert outcome.decision is DecisionLevel.BLOCK
    assert outcome.outcome.ran is False
    assert outcome.accepted is False


def test_model_output_is_parsed_from_its_documented_shape():
    parsed = ModelOutput.from_mapping(
        {"text": "answer", "claims": [{"text": "claim", "source_id": "a", "quote": "q"}]}
    )
    assert parsed.text == "answer"
    assert parsed.claims[0].source_id == "a"
    assert parsed.claims[0].quote == "q"

    bare = ModelOutput.from_mapping({"text": "answer"})
    assert bare.claims == ()


def test_an_answer_with_no_claims_is_logged(validator, benign_envelope):
    outcome = validator.validate(
        {"text": BENIGN_TICKET_TEXT, "claims": []}, envelope=_envelope(benign_envelope)
    )
    assert outcome.decision is DecisionLevel.LOG
    assert outcome.accepted is True
