"""The envelope document: every required field, refused BY NAME (issue #793).

The defect this exists for is that governance lived in a prompt string where
nothing could refuse anything. So the negative control here is the point of the
suite: for EVERY field the envelope requires, a document without it must be
refused, and the refusal must name that field.
"""

from __future__ import annotations

from typing import Any

import pytest

from governance.spawn import model


def test_a_well_formed_document_is_admitted(envelope_fields: dict[str, Any]) -> None:
    fields = dict(envelope_fields)
    spawn_meta = fields.pop("spawn")
    document = model.assemble(fields, spawn=spawn_meta)

    assert document["schema"] == "spawn-envelope/v1"
    assert document["version"] == 1
    assert model.validate(document) == []


@pytest.mark.parametrize("field", model.REQUIRED_FIELDS)
def test_every_required_field_is_refused_by_name_when_absent(field: str, envelope: dict) -> None:
    """One provocation per field: remove it, and the refusal names it."""
    document = dict(envelope)
    document.pop(field)

    refusals = model.validate(document)

    assert refusals, f"a document without {field!r} was admitted"
    assert field in [refusal.field for refusal in refusals], (
        f"the refusal did not name {field!r}: {[r.line() for r in refusals]}"
    )


@pytest.mark.parametrize("field", model.REQUIRED_FIELDS)
def test_every_required_field_is_refused_when_empty(field: str, envelope: dict) -> None:
    """Present-but-empty is not present: `claim: {}` must not pass as a claim."""
    document = dict(envelope)
    document[field] = "" if isinstance(document[field], str) else {}

    refusals = model.validate(document)

    assert field in [refusal.field for refusal in refusals], [r.line() for r in refusals]


def test_a_document_without_the_schema_is_refused_by_name(envelope: dict) -> None:
    document = dict(envelope)
    document["schema"] = "spawn-envelope/v0"

    refusals = model.validate(document)

    assert [r.field for r in refusals] == ["schema"]


def test_the_schema_is_pinned_so_a_consumer_can_refuse_a_version_it_cannot_read() -> None:
    assert model.SCHEMA.endswith("/v1")
    assert model.VERSION == 1
    assert model.validate({"schema": "spawn-envelope/v2"})[0].field == "schema"


def test_an_envelope_that_is_not_an_object_is_refused() -> None:
    assert model.validate(["not", "a", "document"])[0].field == "envelope"


def test_every_refusal_is_reported_together_so_one_pass_fixes_all_of_them(envelope: dict) -> None:
    document = dict(envelope)
    del document["claim"], document["verify"], document["trailer"]

    named = {refusal.field for refusal in model.validate(document)}

    assert {"claim", "verify", "trailer"} <= named


def test_a_trailer_that_does_not_name_the_issue_is_refused(envelope: dict) -> None:
    document = dict(envelope)
    document["trailer"] = "Refs kushin77/agent-orchestrator#1"

    refusals = model.validate(document)

    assert [r.field for r in refusals] == ["trailer"]
    assert "does not match" in refusals[0].reason


def test_a_branch_that_does_not_name_the_issue_is_refused(envelope: dict) -> None:
    document = dict(envelope)
    document["session"] = {**document["session"], "branch": "feature/whatever"}

    refusals = model.validate(document)

    assert [r.field for r in refusals] == ["session.branch"]


def test_a_claim_on_another_lane_is_refused(envelope: dict) -> None:
    """One issue = one lane = one claim; a claim for a sibling lane is not a claim."""
    document = dict(envelope)
    document["claim"] = {**document["claim"], "lane": "some-other-lane"}

    refusals = model.validate(document)

    assert [r.field for r in refusals] == ["claim.lane"]


def test_a_worktree_that_is_not_absolute_is_refused(envelope: dict) -> None:
    document = dict(envelope)
    document["worktree"] = "relative/path"

    refusals = model.validate(document)

    assert [r.field for r in refusals] == ["worktree"]


def test_a_capacity_that_could_not_be_assessed_is_refused(envelope: dict) -> None:
    """Fail-closed: an unmeasurable ceiling is never rounded up to a permit."""
    document = dict(envelope)
    document["capacity"] = {
        **document["capacity"],
        "assessed": False,
        "problems": ["/proc/meminfo: unreadable"],
    }

    refusals = model.validate(document)

    assert [r.field for r in refusals] == ["capacity"]
    assert "unreadable" in refusals[0].reason


def test_a_budget_with_no_cap_is_refused(envelope: dict) -> None:
    document = dict(envelope)
    document["budget"] = {**document["budget"], "cap": 0}

    refusals = model.validate(document)

    assert [r.field for r in refusals] == ["budget.cap"]


def test_a_gate_permit_with_no_concurrency_is_refused(envelope: dict) -> None:
    document = dict(envelope)
    document["capacity"] = {
        **document["capacity"],
        "permit": {**document["capacity"]["permit"], "max_concurrent": 0},
    }

    refusals = model.validate(document)

    assert [r.field for r in refusals] == ["capacity.permit.max_concurrent"]


def test_a_missing_nested_key_is_refused_by_its_dotted_name(envelope: dict) -> None:
    document = dict(envelope)
    document["capacity"] = {**document["capacity"], "permit": {"store": "/x"}}

    named = [refusal.field for refusal in model.validate(document)]

    assert "capacity.permit.worktree_key" in named
    assert "capacity.permit.lock" in named
    assert "capacity.permit.max_concurrent" in named


def test_assemble_refuses_instead_of_returning_a_document(envelope_fields: dict) -> None:
    fields = dict(envelope_fields)
    fields.pop("spawn")
    fields["claim"] = {"owner": "", "state": "unclaimed", "lane": ""}

    with pytest.raises(model.EnvelopeRefused) as refused:
        model.assemble(fields, spawn={"path": "local", "agent": "x"})

    assert "claim.owner" in str(refused.value)
    assert "spawn envelope REFUSED" in str(refused.value)


def test_a_spawn_path_outside_the_declared_set_is_refused(envelope_fields: dict) -> None:
    """Two regimes are governed; a third would be a regime nobody governs."""
    fields = dict(envelope_fields)
    spawn_meta = fields.pop("spawn")
    fields["spawn"] = {"path": "teleport", "agent": "x"}

    with pytest.raises(model.EnvelopeRefused) as refused:
        model.assemble(fields, spawn=fields["spawn"])

    assert "spawn.path" in str(refused.value)


def test_parse_refuses_a_document_that_is_not_json() -> None:
    with pytest.raises(model.EnvelopeRefused) as refused:
        model.parse("{not json")

    assert refused.value.refusals[0].field == "envelope"


def test_round_trip_through_the_canonical_serialisation(envelope: dict) -> None:
    assert model.parse(model.dumps(envelope)) == envelope
