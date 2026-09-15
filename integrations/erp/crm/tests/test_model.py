"""The envelope, the closed vocabularies, and the refusal type (issue #650)."""

from __future__ import annotations

import pytest

from integrations.erp.crm import model
from integrations.erp.crm.model import ACTIONS, KINDS, REFUSALS, SCHEMA_VERSION, Document, Finding, Refused


def test_kinds_are_the_four_feature_families() -> None:
    assert set(KINDS) == {
        "lead",
        "opportunity",
        "customer",
        "project",
        "task",
        "timesheet",
        "inspection",
        "support-issue",
    }


def test_actions_are_unique_and_sorted() -> None:
    assert len(set(ACTIONS)) == len(ACTIONS)
    assert list(ACTIONS) == sorted(ACTIONS)


def test_refusal_vocabulary_is_well_formed() -> None:
    assert REFUSALS
    for code in REFUSALS:
        assert code == code.strip()
        assert code == code.lower()
        assert " " not in code
        assert "-" in code or code.isalpha()


def test_refused_carries_the_reason_and_names_the_offender() -> None:
    refusal = Refused("unknown-kind", "kind 'sprocket' is not declared")
    assert refusal.reason == "unknown-kind"
    assert refusal.detail == "kind 'sprocket' is not declared"
    assert str(refusal) == "unknown-kind: kind 'sprocket' is not declared"
    assert refusal.to_dict() == {
        "reason": "unknown-kind",
        "detail": "kind 'sprocket' is not declared",
    }


def test_a_refusal_without_a_detail_renders_as_its_code() -> None:
    assert str(Refused("schema-violation")) == "schema-violation"


def test_an_invented_refusal_code_is_refused_at_the_raise() -> None:
    """A refuser that invents a code fails here rather than reaching a caller."""
    with pytest.raises(ValueError, match="closed refusal vocabulary"):
        Refused("teleported", "made up")


def test_finding_renders_its_code_and_offender() -> None:
    finding = Finding("audit-broken", "seq 3 digest does not reproduce", kind="lead", ref="LEAD-0001")
    assert finding.to_dict() == {
        "code": "audit-broken",
        "detail": "seq 3 digest does not reproduce",
        "kind": "lead",
        "ref": "LEAD-0001",
    }


def test_document_round_trips_through_its_envelope() -> None:
    document = Document(
        kind="lead",
        id="LEAD-0001",
        tenant="acme",
        state="new",
        title="Northwind",
        owner="rep-1",
        fields={"company": "Northwind Traders", "stage": "new"},
    )
    assert document.to_dict() == {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "lead",
        "id": "LEAD-0001",
        "tenant": "acme",
        "state": "new",
        "title": "Northwind",
        "owner": "rep-1",
        "fields": {"company": "Northwind Traders", "stage": "new"},
    }


def test_document_is_immutable_and_derives_new_values() -> None:
    document = Document(kind="lead", id="LEAD-0001", tenant="acme", state="new", fields={"company": "X"})
    moved = document.with_state("contacted")
    patched = document.with_fields({"company": "Y"})
    assert document.state == "new" and document.fields == {"company": "X"}
    assert moved.state == "contacted" and moved.fields == {"company": "X"}
    assert patched.fields == {"company": "Y"} and patched.state == "new"
    with pytest.raises(Exception):
        document.state = "contacted"  # type: ignore[misc]


def test_document_with_fields_does_not_alias_the_callers_mapping() -> None:
    source = {"company": "X"}
    document = Document(kind="lead", id="LEAD-0001", tenant="acme", state="new", fields=source)
    source["company"] = "mutated"
    assert document.fields["company"] == "X"


def test_schema_version_is_pinned() -> None:
    assert SCHEMA_VERSION == model.SCHEMA_VERSION
