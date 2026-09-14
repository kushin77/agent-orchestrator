"""The consumed citations-envelope contract: strict, fail-closed reading (issue #507)."""

from __future__ import annotations

import pytest

from guardrails.chat.envelope import (
    GroundingEnvelope,
    GroundingError,
    GroundingFragment,
)


def test_a_well_formed_envelope_is_read_and_keeps_extra_keys(benign_envelope):
    envelope = GroundingEnvelope.from_mapping(benign_envelope)
    assert len(envelope) == 1
    assert envelope.ids() == ("ticket:OPS-1187",)
    assert "ticket:OPS-1187" in envelope
    fragment = envelope.get("ticket:OPS-1187")
    assert isinstance(fragment, GroundingFragment)
    assert fragment.kind == "ticket"
    assert fragment.text == benign_envelope["fragments"][0]["text"]
    assert envelope.get("ticket:OPS-9999") is None


def test_unknown_fragment_keys_are_preserved_verbatim():
    envelope = GroundingEnvelope.from_mapping(
        {
            "fragments": [
                {
                    "source_id": "kb:runbook/restore",
                    "text": "Restore takes twenty minutes.",
                    "score": 0.87,
                    "retriever": "hybrid",
                }
            ]
        }
    )
    fragment = envelope.get("kb:runbook/restore")
    assert fragment.to_dict() == {
        "source_id": "kb:runbook/restore",
        "text": "Restore takes twenty minutes.",
        "kind": "document",
        "score": 0.87,
        "retriever": "hybrid",
    }


def test_a_duplicate_source_id_is_refused_rather_than_deduplicated():
    """A duplicate makes every citation into that id ambiguous."""
    with pytest.raises(GroundingError) as raised:
        GroundingEnvelope.from_mapping(
            {
                "fragments": [
                    {"source_id": "ticket:OPS-1187", "text": "first chunk"},
                    {"source_id": "ticket:OPS-1187", "text": "second chunk"},
                ]
            }
        )
    assert "duplicate source_id" in str(raised.value)


@pytest.mark.parametrize(
    "document",
    [
        {"fragments": None},
        {"text": "no fragments key at all"},
        {"fragments": [{"text": "no source_id"}]},
        {"fragments": [{"source_id": "   ", "text": "blank id"}]},
        {"fragments": [{"source_id": "a"}]},
        {"fragments": [{"source_id": "a", "text": "ok", "kind": ""}]},
        {"fragments": ["not a mapping"]},
        ["not a mapping at all"],
        "a bare string",
    ],
)
def test_unreadable_envelopes_raise_rather_than_default_to_no_grounding(document):
    with pytest.raises(GroundingError):
        GroundingEnvelope.from_mapping(document)


def test_fragment_sequence_type_is_checked():
    with pytest.raises(GroundingError):
        GroundingEnvelope.from_fragments({"source_id": "a", "text": "b"})


def test_a_constructed_envelope_is_validated_too():
    good = GroundingFragment(source_id="a", text="body")
    duplicated = (good, GroundingFragment(source_id="a", text="other"))
    with pytest.raises(GroundingError):
        GroundingEnvelope(duplicated)

    with pytest.raises(GroundingError):
        GroundingEnvelope(("not a fragment",))


def test_json_round_trip_and_invalid_json(benign_envelope):
    import json

    envelope = GroundingEnvelope.from_json(json.dumps(benign_envelope))
    assert envelope.ids() == ("ticket:OPS-1187",)
    assert envelope.to_mapping() == {
        "fragments": [dict(benign_envelope["fragments"][0], kind="ticket")]
    }

    with pytest.raises(GroundingError) as raised:
        GroundingEnvelope.from_json("{not json")
    assert "not valid JSON" in str(raised.value)
