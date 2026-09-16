"""PF-10: the sandbox money chain, stage by stage (issue #675).

The probe lives in ``e2e/erp_finops_golden.py``; this suite asserts what it
measured. The subject is the chain EPIC #665's PF-10 names — an AI agent
triggers a customer sign-up, the automated billing routine meters the ERP
operations the sale generated, and the revenue-recognition rules validate the
journal entry the invoice posts — and every assertion below is either a fact
measured over the merged modules or a check that a guard genuinely blocked
when provoked.

The probe is deterministic and keyless, so it runs once per module and each
test reads a different fact about that one simulation, exactly as the sibling
``e2e/tests/test_erp_golden_path.py`` does for the ERP module.
"""

from __future__ import annotations

import pytest

from e2e.erp_finops_golden import (
    CUSTOMER_ID,
    TENANT,
    probe_golden_path,
    probe_negative_controls,
)


@pytest.fixture(scope="module")
def simulation():
    """One full run: sign-up -> billing -> revenue recognition."""
    return probe_golden_path()


@pytest.fixture(scope="module")
def controls():
    """The three negative controls, each driven through the owning module."""
    return probe_negative_controls()


def _control(controls, control_id):
    return next(
        control for control in controls["controls"] if control["controlId"] == control_id
    )


# --------------------------------------------------------------------------- #
# the golden path
# --------------------------------------------------------------------------- #
def test_no_stage_reported_a_failure(simulation):
    """The chain is green: every stage's own failure list is empty."""
    assert simulation.failures() == (), simulation.failures()


def test_the_signup_earns_the_customer_the_sale_bills(simulation):
    """The sign-up produces the customer, and the sale's party is that customer."""
    signup = simulation.signup
    assert signup["customerKind"] == "customer"
    assert signup["customerId"] == CUSTOMER_ID
    assert signup["customerState"] == "active"
    assert signup["railEntries"] > 0
    assert set(signup["documents"]) == {"CUST-1", "LEAD-SIM-0001", "OPP-SIM-0001"}
    # the tie, measured rather than asserted in prose: the sale the billing
    # routine meters and the revenue the spine recognizes are the sign-up's own
    # customer.
    assert simulation.revenue["invoiceParty"] == signup["customerId"]


def test_the_billing_routine_meters_every_operation_and_bills_the_tenant(simulation):
    """Every operation the sale performed is metered, and the bill is certified."""
    billing = simulation.billing
    assert billing["meteredOperations"] > 0
    assert billing["auditRecords"] == billing["meteredOperations"]
    assert billing["usageRecords"] == billing["meteredOperations"]
    assert billing["auditVerdict"] == "OK"
    bill = billing["bill"]
    assert bill is not None
    assert bill["tenantId"] == TENANT
    assert bill["operations"] == billing["meteredOperations"]
    assert bill["unmeteredOperations"] == 0
    assert bill["costUsd"] > 0
    # the certified tail names the same number of records the bill covers
    assert billing["certifiedTail"][0] == billing["meteredOperations"]


def test_revenue_recognition_exercises_a_real_journal_entry(simulation):
    """AC(a): the income line of the invoice's gl-posting is a real journal entry.

    The posting is a real spine document (not a stub dict) whose voucher is the
    invoice and whose lines are the ledger rows themselves; the income line
    credits the invoice's own net total.
    """
    revenue = simulation.revenue
    assert revenue["postingIsRealDocument"] is True
    posting = revenue["posting"]
    assert posting["kind"] == revenue["accountingKind"] == "gl-posting"
    assert posting["voucherType"] == "sales-invoice"
    assert posting["voucherId"] == revenue["invoiceId"]
    assert len(posting["lines"]) >= 3
    for line in posting["lines"]:
        assert bool(line.get("debit")) != bool(line.get("credit")), line
    # the ledger verifies (balanced double entry, every voucher nets to zero)
    assert revenue["ledger"]["verify"] == []
    debits = revenue["ledger"]["totals"]["debits"]
    credits = revenue["ledger"]["totals"]["credits"]
    assert debits == credits != 0
    # the revenue recognized is the invoice's own figure, not one this suite wrote
    assert revenue["incomeCredited"] == revenue["invoiceNetTotal"]
    assert revenue["incomeCredited"] > 0
    # the journal entry's lines and the ledger's rows name the same accounts
    assert {line["account"] for line in posting["lines"]} == {
        entry["account"] for entry in revenue["ledger"]["entries"]
    }


# --------------------------------------------------------------------------- #
# the negative controls
# --------------------------------------------------------------------------- #
def test_every_negative_control_passed(controls):
    assert controls["failedControls"] == [], controls["failedControls"]
    assert controls["passed"] is True


def test_a_duplicated_conversion_is_idempotent(controls):
    """AC(b): a second conversion of the same lead is refused, by name, idempotently."""
    control = _control(controls, "duplicated-conversion-idempotent")
    assert control["refusedBy"] == "already-converted"
    assert control["evidence"]["firstConversionProducedOpportunity"] is True
    assert control["evidence"]["documentsUnchanged"] is True


def test_a_failed_payment_leaves_the_ledger_untouched(controls):
    """AC(b): an unbalanced posting is refused, and the general ledger is untouched."""
    control = _control(controls, "failed-payment-ledger-untouched")
    assert control["refusedBy"] == "unbalanced-posting"
    assert control["evidence"]["ledgerEntriesBefore"] == 0
    assert control["evidence"]["ledgerUntouched"] is True
    # the pairing: the balanced body does post the ledger, so the refusal is the
    # unbalanced figure's, not a flow that never posts.
    assert control["evidence"]["balancedPostingEntries"] > 0
    assert control["evidence"]["balancedIncomeCredited"] > 0


def test_a_malformed_event_is_quarantined(controls):
    """AC(b): an unparseable metered event is quarantined: refused, nothing written."""
    control = _control(controls, "malformed-event-quarantined")
    assert control["refusedBy"] == "invalid-timestamp"
    assert control["evidence"]["quarantinedWroteNothing"] is True
    # the pairing: the one well-formed event after was metered, so the quarantine
    # is the malformed timestamp's, not a sink that never writes.
    assert control["evidence"]["auditRecordsAfter"] == 1
    assert control["evidence"]["usageRecordsAfter"] == 1
