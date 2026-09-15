"""The lead -> opportunity -> customer conversion flow (issue #650, AC1).

Acceptance criterion 1: "Lead -> opportunity -> customer conversion is
state-machine-driven and audited." Both halves are measured here — the machine
decides legality, and the rail holds the record — plus a negative control for
each precondition the machine cannot express.
"""

from __future__ import annotations

import pytest

from integrations.erp.crm import flows
from integrations.erp.crm.model import Refused

AT = "2026-09-01T09:00:00Z"


def test_the_golden_path_converts_a_lead_into_a_customer(golden) -> None:
    space = golden.workspace
    assert space.get("LEAD-0001").state == "converted"
    assert space.get("OPP-0001").state == "won"
    assert space.get("CUST-0001").state == "active"
    assert space.get("CUST-0001").fields["legal_name"] == "Northwind Traders"


def test_the_conversion_is_audited_step_by_step(golden) -> None:
    entries = golden.workspace.rail.for_ref("LEAD-0001")
    assert [(entry.action, entry.from_state, entry.to_state) for entry in entries] == [
        ("create", "", "new"),
        ("advance", "new", "contacted"),
        ("advance", "contacted", "qualified"),
        ("convert", "qualified", "converted"),
    ]
    assert [entry.seq for entry in entries] == sorted(entry.seq for entry in entries)


def test_the_opportunity_and_customer_are_born_on_the_same_rail(golden) -> None:
    rail = golden.workspace.rail
    assert [(entry.action, entry.to_state) for entry in rail.for_ref("OPP-0001")][0] == (
        "convert",
        "open",
    )
    assert [(entry.action, entry.to_state) for entry in rail.for_ref("CUST-0001")] == [("win", "active")]


def test_the_whole_rail_is_a_valid_chain(golden) -> None:
    assert golden.workspace.rail.verify() == []
    assert golden.findings == ()


def test_the_workspace_is_immutable_across_the_flow(space, definitions) -> None:
    """Each flow returns a new workspace, so an earlier step stays readable."""
    fresh = flows.create_document(
        space,
        "lead",
        "LEAD-0001",
        {"company": "Northwind Traders", "stage": "new", "value": 1000},
        actor="rep-1",
        at=AT,
    )
    fresh = flows.advance(fresh, "LEAD-0001", "contacted", actor="rep-1", at=AT)
    fresh = flows.advance(fresh, "LEAD-0001", "qualified", actor="rep-1", at=AT)
    converted = flows.convert_lead(fresh, "LEAD-0001", "OPP-0001", actor="rep-1", at=AT)
    assert "OPP-0001" in converted.documents
    assert "OPP-0001" not in fresh.documents
    assert fresh.get("LEAD-0001").state == "qualified"
    assert len(fresh.rail) < len(converted.rail)


def test_an_unqualified_lead_is_refused_by_name(space) -> None:
    fresh = flows.create_document(
        space,
        "lead",
        "LEAD-0001",
        {"company": "Northwind Traders", "stage": "new"},
        actor="rep-1",
        at=AT,
    )
    fresh = flows.advance(fresh, "LEAD-0001", "contacted", actor="rep-1", at=AT)
    with pytest.raises(Refused, match="not-qualified") as caught:
        flows.convert_lead(fresh, "LEAD-0001", "OPP-0001", actor="rep-1", at=AT)
    assert "contacted" in caught.value.detail and "qualified" in caught.value.detail


def test_a_lead_converts_once(golden) -> None:
    with pytest.raises(Refused, match="already-converted") as caught:
        flows.convert_lead(golden.workspace, "LEAD-0001", "OPP-0002", actor="rep-1", at=AT)
    assert "LEAD-0001" in caught.value.detail


def test_an_opportunity_that_is_not_at_proposal_is_refused_by_the_machine(golden) -> None:
    """Win legality is the machine's call, so there is one place that decides it."""
    with pytest.raises(Refused, match="illegal-transition") as caught:
        flows.win_opportunity(golden.workspace, "OPP-0001", "CUST-0002", actor="rep-1", at=AT)
    assert "won" in caught.value.detail


def test_an_opportunity_cannot_be_won_twice(space) -> None:
    fresh = flows.create_document(
        space,
        "opportunity",
        "OPP-0001",
        {"company": "X", "amount": 10, "stage": "discovery", "currency": "EUR"},
        actor="rep-1",
        at=AT,
    )
    fresh = flows.advance(fresh, "OPP-0001", "proposal", actor="rep-1", at=AT)
    fresh = flows.win_opportunity(fresh, "OPP-0001", "CUST-0001", actor="rep-1", at=AT)
    with pytest.raises(Refused, match="illegal-transition"):
        flows.win_opportunity(fresh, "OPP-0001", "CUST-0002", actor="rep-1", at=AT)


def test_a_lead_that_is_lost_is_terminal(golden, definitions) -> None:
    from integrations.erp.crm import workflow

    lost = golden.workspace.get("LEAD-0001").with_state("lost")
    assert workflow.is_terminal(lost, definitions)
    assert not definitions.kind("lead").allows("lost", "qualified")


def test_the_wrong_kind_is_refused_by_name(golden) -> None:
    with pytest.raises(Refused, match="unknown-document") as caught:
        flows.convert_lead(golden.workspace, "OPP-0001", "OPP-0009", actor="rep-1", at=AT)
    assert "not a lead" in caught.value.detail


def test_findings_report_a_document_that_drifted_from_its_declaration(golden) -> None:
    """The whole-set check reports rather than raises, and names the offender."""
    drifted = golden.workspace.get("LEAD-0001").with_state("delivered")
    broken = flows.Workspace(
        tenant=golden.workspace.tenant,
        definitions=golden.workspace.definitions,
        documents={**golden.workspace.documents, "LEAD-0001": drifted},
        rail=golden.workspace.rail,
    )
    findings = broken.findings()
    assert [finding.code for finding in findings] == ["unknown-state"]
    assert findings[0].ref == "LEAD-0001"
