"""Envelope parsing and per-kind field validation (issue #650)."""

from __future__ import annotations

import pytest

from integrations.erp.crm import documents
from integrations.erp.crm.model import SCHEMA_VERSION, Document, Refused

TENANT = "acme"


def envelope(**overrides):
    payload = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "lead",
        "id": "LEAD-0001",
        "tenant": TENANT,
        "state": "new",
        "fields": {"company": "Northwind Traders", "stage": "new"},
    }
    payload.update(overrides)
    return payload


def test_a_valid_envelope_parses_into_a_document(definitions) -> None:
    document = documents.parse(envelope(), definitions)
    assert document.kind == "lead"
    assert document.state == "new"
    assert document.fields == {"company": "Northwind Traders", "stage": "new"}


def test_envelope_of_round_trips_through_parse(definitions) -> None:
    document = documents.parse(envelope(title="Northwind", owner="rep-1"), definitions)
    assert documents.parse(documents.envelope_of(document), definitions) == document


def test_a_missing_envelope_field_is_a_schema_violation_naming_it(definitions) -> None:
    payload = envelope()
    del payload["id"]
    with pytest.raises(Refused, match="schema-violation") as caught:
        documents.parse(payload, definitions, where="ENV-0001")
    assert "missing required field 'id'" in caught.value.detail


def test_a_malformed_id_is_a_schema_violation(definitions) -> None:
    with pytest.raises(Refused, match="schema-violation") as caught:
        documents.parse(envelope(id="lead-1"), definitions)
    assert "does not match" in caught.value.detail


def test_an_unknown_envelope_key_is_a_schema_violation(definitions) -> None:
    with pytest.raises(Refused, match="schema-violation") as caught:
        documents.parse(envelope(owner_notes="hello"), definitions)
    assert "unknown field 'owner_notes'" in caught.value.detail


def test_an_undeclared_kind_is_refused_by_name(definitions) -> None:
    with pytest.raises(Refused, match="unknown-kind") as caught:
        documents.parse(envelope(kind="sprocket"), definitions)
    assert "sprocket" in caught.value.detail


def test_a_state_of_another_kind_is_refused_by_name(definitions) -> None:
    """The envelope is well formed; the state simply does not belong to this kind."""
    with pytest.raises(Refused, match="unknown-state") as caught:
        documents.parse(envelope(state="won"), definitions)
    assert "won" in caught.value.detail
    assert "lead" in caught.value.detail


def test_a_field_the_kind_does_not_allow_is_refused_by_name(definitions) -> None:
    with pytest.raises(Refused, match="unknown-field") as caught:
        documents.parse(envelope(fields={"company": "X", "stage": "new", "planet": "Mars"}), definitions)
    assert "planet" in caught.value.detail


def test_a_missing_required_field_is_refused_by_name(definitions) -> None:
    with pytest.raises(Refused, match="missing-field") as caught:
        documents.parse(envelope(fields={"stage": "new"}), definitions)
    assert "company" in caught.value.detail


def test_a_value_of_the_wrong_declared_type_is_refused(definitions) -> None:
    with pytest.raises(Refused, match="invalid-value") as caught:
        documents.parse(
            envelope(fields={"company": "X", "stage": "new", "value": "a lot"}),
            definitions,
            where="LEAD-0001",
        )
    assert "LEAD-0001.value" in caught.value.detail


def test_a_bool_is_not_accepted_for_a_declared_integer(definitions) -> None:
    with pytest.raises(Refused, match="invalid-value") as caught:
        documents.parse(envelope(fields={"company": "X", "stage": "new", "value": True}), definitions)
    assert "got bool" in caught.value.detail


def test_a_negative_declared_integer_is_refused(definitions) -> None:
    with pytest.raises(Refused, match="invalid-value") as caught:
        documents.parse(envelope(fields={"company": "X", "stage": "new", "value": -5}), definitions)
    assert "must be >= 0" in caught.value.detail


def test_a_term_outside_the_declared_vocabulary_is_refused_by_name(definitions) -> None:
    with pytest.raises(Refused, match="unknown-vocabulary-term") as caught:
        documents.parse(envelope(fields={"company": "X", "stage": "epic"}), definitions)
    assert "'epic'" in caught.value.detail
    assert "lead-stages" in caught.value.detail


def test_a_non_string_vocabulary_value_is_refused(definitions) -> None:
    with pytest.raises(Refused, match="unknown-vocabulary-term"):
        documents.parse(envelope(fields={"company": "X", "stage": 1}), definitions)


def test_validate_range_enforces_its_bounds(definitions) -> None:
    document = Document(kind="timesheet", id="TS-0001", tenant=TENANT, state="draft", fields={"minutes": 0})
    with pytest.raises(Refused, match="invalid-value") as caught:
        documents.validate_range(document, definitions, "minutes", minimum=1, maximum=1440)
    assert "below the minimum 1" in caught.value.detail

    document = Document(kind="timesheet", id="TS-0001", tenant=TENANT, state="draft", fields={"minutes": 5000})
    with pytest.raises(Refused, match="invalid-value") as caught:
        documents.validate_range(document, definitions, "minutes", minimum=1, maximum=1440)
    assert "above the maximum 1440" in caught.value.detail


def test_validate_range_refuses_a_field_the_kind_does_not_allow(definitions) -> None:
    document = Document(kind="lead", id="LEAD-0001", tenant=TENANT, state="new", fields={})
    with pytest.raises(Refused, match="unknown-field"):
        documents.validate_range(document, definitions, "planet", minimum=1)


def test_validate_range_refuses_a_non_integer(definitions) -> None:
    document = Document(kind="timesheet", id="TS-0001", tenant=TENANT, state="draft", fields={"minutes": "60"})
    with pytest.raises(Refused, match="invalid-value") as caught:
        documents.validate_range(document, definitions, "minutes", minimum=1)
    assert "expected an integer" in caught.value.detail


def test_every_declared_kind_accepts_a_minimal_document(definitions) -> None:
    """Each kind's own required fields are sufficient to build a valid document."""
    minimal = {
        "lead": {"company": "X", "stage": "new"},
        "opportunity": {"company": "X", "amount": 1, "currency": "EUR"},
        "customer": {"legal_name": "X", "tier": "standard"},
        "project": {"project_type": "delivery"},
        "task": {"project": "PROJ-0001", "task_type": "review"},
        "timesheet": {"project": "PROJ-0001", "task": "TASK-0001", "minutes": 1, "work_date": "2026-09-01"},
        "inspection": {"template": "incoming", "subject": "batch"},
        "support-issue": {"priority": "p1", "channel": "email", "opened_at": "2026-09-01T09:00:00Z"},
    }
    assert sorted(minimal) == documents.known_kinds(definitions)
    for kind, fields in minimal.items():
        payload = envelope(kind=kind, id="DOC-0001", state=definitions.kind(kind).initial, fields=fields)
        assert documents.parse(payload, definitions).kind == kind
