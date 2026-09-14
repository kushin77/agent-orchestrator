"""The citations envelope: a documented dict shape, checked mechanically."""

from __future__ import annotations

import pytest

from registry.chat import envelope


def test_a_citation_round_trips_through_the_documented_dict_shape() -> None:
    entry = envelope.Citation.from_dict(
        {
            "fragment_id": "frag-1",
            "source_id": "ticket:AO-509",
            "revision": "r7",
            "claim": "AO-509 is in progress",
        }
    )
    assert entry.kind == "ticket"
    assert entry.to_dict() == {
        "fragment_id": "frag-1",
        "source_id": "ticket:AO-509",
        "revision": "r7",
        "claim": "AO-509 is in progress",
    }


def test_a_source_id_must_name_a_declared_identifier_family() -> None:
    for bad in ("kb:doc-1", "ticket:", "AO-509", "bridge:"):
        with pytest.raises(envelope.EnvelopeError):
            envelope.Citation.from_dict({"fragment_id": "f", "source_id": bad})
    for good in ("bridge:fleet", "tool_call:call-7", "ticket:AO-509"):
        assert envelope.Citation.from_dict(
            {"fragment_id": "f", "source_id": good}
        ).source_id == good


def test_an_unknown_citation_key_is_refused() -> None:
    with pytest.raises(envelope.EnvelopeError) as excinfo:
        envelope.Citation.from_dict(
            {"fragment_id": "f", "source_id": "ticket:AO-509", "confidence": 0.9}
        )
    assert "unknown key" in str(excinfo.value)


def test_citations_must_be_a_list() -> None:
    with pytest.raises(envelope.EnvelopeError):
        envelope.validate_citations({"fragment_id": "f", "source_id": "ticket:A"})
    assert envelope.validate_citations([]) == []


def test_the_envelope_is_ordered_and_deterministic() -> None:
    fragments = [
        {"fragment_id": "frag-b", "source_id": "bridge:fleet", "revision": "r3"},
        {"fragment_id": "frag-a", "source_id": "ticket:AO-509"},
    ]
    first = envelope.envelope_dicts(fragments)
    assert first == envelope.envelope_dicts(fragments)
    assert [entry["fragment_id"] for entry in first] == ["frag-b", "frag-a"]


def test_fabricated_names_a_source_id_that_was_never_supplied() -> None:
    citations = envelope.validate_citations(
        [
            {"fragment_id": "frag-1", "source_id": "ticket:AO-509"},
            {"fragment_id": "frag-9", "source_id": "bridge:not-supplied"},
        ]
    )
    assert envelope.fabricated(citations, ["ticket:AO-509"]) == ["bridge:not-supplied"]


def test_schema_requires_envelope_distinguishes_required_from_permitted() -> None:
    required = {
        "type": "object",
        "required": ["answer", "citations"],
        "properties": {"citations": {"type": "array"}},
    }
    permitted = {
        "type": "object",
        "required": ["answer"],
        "properties": {"citations": {"type": "array"}},
    }
    assert envelope.schema_requires_envelope(required) is True
    assert envelope.schema_requires_envelope(permitted) is False
    assert envelope.schema_requires_envelope({}) is False
    assert envelope.schema_requires_envelope("not a schema") is False


def test_citation_floor_reads_the_declared_minimum() -> None:
    assert envelope.citation_floor(
        {"properties": {"citations": {"type": "array", "minItems": 1}}}
    ) == 1
    assert envelope.citation_floor(
        {"properties": {"citations": {"type": "array", "minItems": 0}}}
    ) == 0
    assert envelope.citation_floor({}) == 0
