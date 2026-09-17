"""e2e/erp_finops_golden — PF-10: the sandbox money chain (issue #675).

PF-10 is the end-to-end sandbox simulation of the ERP module's money chain:
an AI agent triggers a customer sign-up (CRM), the automated billing routine
meters the ERP document operations the customer's sale generated (FinOps),
and the revenue-recognition rules validate the journal entry the invoice
posts (the transactional spine) — all offline, deterministic, and with zero
manual intervention.

Each hop is read through the module that owns it, never re-implemented:

| Stage  | Real module consumed      | What is proven                                                        |
|--------|---------------------------|-----------------------------------------------------------------------|
| signup | ``integrations/erp/crm``  | a lead is qualified, converted and won into a customer, on the audited rail |
| billing| ``integrations/erp/finops`` | every document operation the sale performed is metered onto the audit chain and usage feed, rolled up, and billed for the tenant |
| revenue| ``integrations/erp/tx``   | the invoice's ``gl-posting`` is a real journal entry whose income line credits the invoice's own net total |

The three negative controls each prove a refusal path, and each is paired with
the same measurement coming out the other way when the one thing under test
changes (no false green — a guard that refused everything, or refused nothing,
could not pass):

| Control               | Refusal, by name          | The refusal proves                                             |
|-----------------------|---------------------------|----------------------------------------------------------------|
| duplicated conversion | crm ``already-converted`` | converting a lead twice is idempotent: no second opportunity appears |
| failed payment        | tx ``unbalanced-posting`` | a posting that does not balance leaves the general ledger untouched |
| malformed event       | finops ``invalid-timestamp`` | an unparseable metered event is quarantined: nothing is written |

Offline by construction: no network, no sockets, no docker, no keys, no
wall-clock read. The clocks are injected — the CRM clock below, the tx
``spine.TIMELINE``, and the finops ``stamp`` — so every value in the evidence
is a function of the scenario, never of when the simulation ran.

Run from the repo root:

    python3 -m e2e.erp_finops_golden --out /tmp/pf10   # the evidence, as JSON
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from e2e._paths import REPO_ROOT, ensure_sys_paths

ensure_sys_paths()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from e2e.wiring import write_evidence  # noqa: E402

# --------------------------------------------------------------------------- #
# scenario constants
# --------------------------------------------------------------------------- #

#: The tenant the simulation runs under. It is the finops lane's default tenant
#: and is declared in the shipped budget catalog, so its ERP operations are
#: billable (an undeclared tenant is refused ``budget-unknown-tenant``).
TENANT = "acme"

#: The actor the simulation attributes every step to — PF-10's "AI agent".
ACTOR = "agent:pf10-e2e"

#: The customer the sign-up earns. The tx golden scenario names this party on
#: every document it raises, so the sale the billing routine meters and the
#: revenue the spine recognizes belong to the customer the sign-up created.
#: The suite asserts the two coincide rather than trusting the comment.
CUSTOMER_ID = "CUST-1"

#: The CRM sign-up clock, injected so the rail is a function of the scenario.
CLOCK = {
    "opened": "2026-03-01T09:00:00Z",
    "contacted": "2026-03-01T10:00:00Z",
    "qualified": "2026-03-01T11:00:00Z",
    "converted": "2026-03-01T12:00:00Z",
    "proposal": "2026-03-02T09:00:00Z",
    "won": "2026-03-02T10:00:00Z",
}


@dataclass(frozen=True)
class Simulation:
    """One full run: the three stages, each with its own measured failures."""

    signup: Dict[str, Any]
    billing: Dict[str, Any]
    revenue: Dict[str, Any]

    def failures(self) -> Tuple[str, ...]:
        return tuple(
            f"{stage}: {failure}"
            for stage, evidence in (
                ("signup", self.signup),
                ("billing", self.billing),
                ("revenue", self.revenue),
            )
            for failure in evidence.get("failures", ())
        )


# --------------------------------------------------------------------------- #
# stage 1 — sign-up (CRM: lead -> opportunity -> customer)
# --------------------------------------------------------------------------- #
def _run_signup() -> Any:
    """Drive the CRM conversion funnel and return the resulting workspace."""
    from integrations.erp.crm import flows as crm
    from integrations.erp.crm.model import KIND_LEAD

    space = crm.workspace(TENANT)
    space = crm.create_document(
        space,
        KIND_LEAD,
        "LEAD-SIM-0001",
        {"company": "Northwind Traders", "stage": "new", "source": "web", "value": 250000},
        actor=ACTOR,
        at=CLOCK["opened"],
        title="Northwind renewal",
        owner="rep-1",
    )
    space = crm.advance(space, "LEAD-SIM-0001", "contacted", actor=ACTOR, at=CLOCK["contacted"])
    space = crm.advance(space, "LEAD-SIM-0001", "qualified", actor=ACTOR, at=CLOCK["qualified"])
    space = crm.convert_lead(space, "LEAD-SIM-0001", "OPP-SIM-0001", actor=ACTOR, at=CLOCK["converted"])
    space = crm.advance(space, "OPP-SIM-0001", "proposal", actor=ACTOR, at=CLOCK["proposal"])
    space = crm.win_opportunity(space, "OPP-SIM-0001", CUSTOMER_ID, actor=ACTOR, at=CLOCK["won"])
    return space


def _stage_signup() -> Dict[str, Any]:
    """Measure the sign-up: the customer it earned, and the rail that licensed it."""
    from integrations.erp.crm.model import KIND_CUSTOMER

    failures: List[str] = []
    space = _run_signup()
    for finding in space.findings():
        failures.append(f"{finding.code}: {finding.detail}")

    customer = space.get(CUSTOMER_ID)
    if customer.kind != KIND_CUSTOMER:
        failures.append(
            f"{CUSTOMER_ID} is a {customer.kind}, not a {KIND_CUSTOMER} after the win"
        )
    declared_initial = space.definitions.kind(KIND_CUSTOMER).initial
    if customer.state != declared_initial:
        failures.append(
            f"the customer is in state {customer.state!r}, not its declared "
            f"initial state {declared_initial!r}"
        )

    return {
        "failures": failures,
        "tenant": TENANT,
        "actor": ACTOR,
        "customerId": CUSTOMER_ID,
        "customerKind": customer.kind,
        "customerState": customer.state,
        "customerFields": dict(customer.fields),
        "documents": sorted(space.documents),
        "railEntries": len(space.rail),
        "railHead": space.rail.head,
    }


# --------------------------------------------------------------------------- #
# the tx cycle, shared by the billing and revenue stages
# --------------------------------------------------------------------------- #
def _run_tx_cycle() -> Any:
    """Run the transactional spine's golden cycle once (the sale)."""
    from integrations.erp.tx import spine
    from integrations.erp.tx.definitions import load as load_definitions

    return spine.golden_path("pf10", load_definitions())


# --------------------------------------------------------------------------- #
# stage 2 — billing (FinOps: meter every operation, roll up, bill)
# --------------------------------------------------------------------------- #
def _stage_billing(gp: Any) -> Dict[str, Any]:
    """Meter the operations the sale actually performed and bill the tenant.

    The operations are walked from the tx spine's own rail — the same entries a
    later auditor reads — so the bill covers exactly what happened, with no
    second list to drift from it. The billing clock is the finops harness's
    injected ``stamp`` (monotonic), independent of the spine's timeline.
    """
    from integrations.erp.finops.harness import build_workspace, stamp
    from integrations.erp.finops.model import Refused
    from integrations.erp.finops.rollup import ErpRollup

    failures: List[str] = []
    workspace = build_workspace()
    model = workspace.model

    def core_action(kind: str, from_state: str, to_state: str) -> str:
        for transition in model.workflow_for(kind).transitions:
            if transition.from_state == from_state and transition.to == to_state:
                return transition.action
        raise AssertionError(f"no declared transition {from_state!r} -> {to_state!r} for {kind}")

    events = []
    for index, entry in enumerate(gp.workspace.rail):
        at = stamp(index)
        if entry.from_state == "":
            events.append(
                workspace.meter.create(
                    entry.kind, tenant=TENANT, document_id=entry.ref, actor=ACTOR, at=at
                )
            )
        else:
            events.append(
                workspace.meter.transition(
                    entry.kind,
                    tenant=TENANT,
                    document_id=entry.ref,
                    actor=ACTOR,
                    from_state=entry.from_state,
                    action=core_action(entry.kind, entry.from_state, entry.to_state),
                    target=entry.to_state,
                    at=at,
                )
            )

    verdict = workspace.audit.verify(TENANT)
    if verdict.status != "OK":
        failures.append(f"the audit chain is {verdict.status}, not OK")
    if workspace.audit.count(TENANT) != len(events):
        failures.append(
            f"the audit chain holds {workspace.audit.count(TENANT)} record(s) for "
            f"{len(events)} metered operation(s)"
        )
    if workspace.usage.count() != len(events):
        failures.append(
            f"the usage feed holds {workspace.usage.count()} record(s) for "
            f"{len(events)} metered operation(s)"
        )

    rollup = ErpRollup(workspace.reporter)
    try:
        row = rollup.bill(TENANT)
    except Refused as refusal:
        row = None
        failures.append(f"the bill was refused: {refusal.code}: {refusal.detail}")
    try:
        tail = rollup.certify(workspace.audit, TENANT)
    except Refused as refusal:
        tail = None
        failures.append(f"the cost could not be certified: {refusal.code}: {refusal.detail}")

    return {
        "failures": failures,
        "tenant": TENANT,
        "meteredOperations": len(events),
        "auditRecords": workspace.audit.count(TENANT),
        "auditActions": list(workspace.audit.actions(TENANT)),
        "auditVerdict": verdict.status,
        "usageRecords": workspace.usage.count(),
        "bill": row.to_dict() if row is not None else None,
        "certifiedTail": list(tail) if tail is not None else None,
    }


# --------------------------------------------------------------------------- #
# stage 3 — revenue recognition (tx: the invoice's real journal entry)
# --------------------------------------------------------------------------- #
def _stage_revenue(gp: Any) -> Dict[str, Any]:
    """Measure the journal entry the invoice posts — the revenue recognition."""
    from integrations.erp.tx.model import ROLE_INCOME, ROLE_RECEIVABLE, TxDocument

    failures: List[str] = []
    for finding in gp.findings:
        failures.append(f"{finding.code}: {finding.detail}")

    space = gp.workspace
    definitions = space.definitions
    invoice = next(
        document for document in space.of_kind(definitions.chain[-1])
    )
    postings = list(space.of_kind(definitions.accounting_kind))
    if not postings:
        failures.append("the invoice derived no gl-posting document")
    posting = postings[0] if postings else None

    is_real_document = isinstance(posting, TxDocument)
    if not is_real_document:
        failures.append("the journal entry is not a real spine document")

    voucher_type = posting.body.get("voucher_type") if posting is not None else None
    voucher_id = posting.body.get("voucher_id") if posting is not None else None
    if voucher_type != invoice.kind:
        failures.append(
            f"the journal entry cites voucher_type {voucher_type!r}, not the "
            f"invoice's {invoice.kind!r}"
        )
    if voucher_id != invoice.id:
        failures.append(
            f"the journal entry cites voucher_id {voucher_id!r}, not the "
            f"invoice's {invoice.id!r}"
        )

    income_account = space.policy.resolve(ROLE_INCOME)
    receivable_account = space.policy.resolve(ROLE_RECEIVABLE)
    income_credited = sum(
        float(entry.credit)
        for entry in space.ledger.entries
        if entry.account == income_account
    )
    invoice_net = invoice.body.get("net_total")
    if income_credited <= 0:
        failures.append(f"the income account {income_account} was never credited")
    if isinstance(invoice_net, (int, float)) and income_credited != float(invoice_net):
        failures.append(
            f"the income account credits {income_credited:.2f}, not the invoice's "
            f"own net total {float(invoice_net):.2f}"
        )

    for finding in space.ledger.verify():
        failures.append(f"{finding.code}: {finding.detail}")
    debits, credits = space.ledger.totals()
    if debits != credits or debits <= 0:
        failures.append(
            f"the ledger posts {debits:.2f} of debits against {credits:.2f} of credits"
        )

    return {
        "failures": failures,
        "chain": list(definitions.chain),
        "steps": list(gp.steps),
        "invoiceId": invoice.id,
        "invoiceParty": invoice.body.get("party"),
        "invoiceNetTotal": invoice_net,
        "accountingKind": definitions.accounting_kind,
        "postingIsRealDocument": is_real_document,
        "posting": {
            "id": posting.id,
            "kind": posting.kind,
            "state": posting.state,
            "voucherType": voucher_type,
            "voucherId": voucher_id,
            "lines": list(posting.body.get("lines") or []),
        }
        if posting is not None
        else None,
        "incomeAccount": income_account,
        "receivableAccount": receivable_account,
        "incomeCredited": income_credited,
        "ledger": {
            "entries": space.ledger.to_list(),
            "balances": space.ledger.balances(),
            "totals": {"debits": debits, "credits": credits},
            "verify": [finding.to_dict() for finding in space.ledger.verify()],
        },
    }


# --------------------------------------------------------------------------- #
# the whole chain
# --------------------------------------------------------------------------- #
def probe_golden_path() -> Simulation:
    """Run the three stages over one sale and return the measured evidence."""
    gp = _run_tx_cycle()
    return Simulation(
        signup=_stage_signup(),
        billing=_stage_billing(gp),
        revenue=_stage_revenue(gp),
    )


# --------------------------------------------------------------------------- #
# negative controls — each proves a refusal, and each could have failed
# --------------------------------------------------------------------------- #
def probe_negative_controls() -> Dict[str, Any]:
    """Provoke the three refusals and record, for each, that the guard blocked.

    Each control passes only when the real module refuses by name, and each is
    paired with the same measurement coming out the other way when the one thing
    under test changes — so a guard that refused everything, or nothing, fails.
    """
    controls = [
        _control_duplicated_conversion(),
        _control_failed_payment(),
        _control_malformed_event(),
    ]
    failed = [
        control["controlId"] for control in controls if not control["passed"]
    ]
    return {
        "controls": controls,
        "failedControls": failed,
        "passed": not failed,
    }


def _control_duplicated_conversion() -> Dict[str, Any]:
    """A lead converts once: the second conversion is refused, idempotently."""
    from integrations.erp.crm import flows as crm
    from integrations.erp.crm.model import Refused

    space = _run_signup()
    before = sorted(space.documents)
    refused = False
    reason = ""
    detail = ""
    try:
        crm.convert_lead(space, "LEAD-SIM-0001", "OPP-SIM-DUP", actor=ACTOR, at=CLOCK["won"])
    except Refused as refusal:
        refused = True
        reason = refusal.reason
        detail = refusal.detail
    after = sorted(space.documents)
    passed = refused and reason == "already-converted" and before == after
    return {
        "controlId": "duplicated-conversion-idempotent",
        "passed": passed,
        "refusedBy": reason,
        "detail": detail,
        "evidence": {
            "firstConversionProducedOpportunity": "OPP-SIM-0001" in space.documents,
            "documentsUnchanged": before == after,
            "documentsAfter": after,
        },
    }


def _control_failed_payment() -> Dict[str, Any]:
    """A posting that does not balance is refused and the ledger is untouched."""
    from integrations.erp.tx import spine
    from integrations.erp.tx.definitions import load as load_definitions
    from integrations.erp.tx.model import ROLE_INCOME, Refused

    definitions = load_definitions()
    scenario = spine.golden_scenario()
    chain = definitions.chain
    timeline = spine.TIMELINE
    space = spine.workspace(definitions, scenario)
    space = spine.draft(space, chain[0], scenario.ids[0], scenario.bodies[0], at=timeline["quoted"])
    space = spine.submit(space, scenario.ids[0], at=timeline["submitted"])
    space = spine.complete(space, scenario.ids[0], at=timeline["accepted"])
    space = spine.raise_document(
        space, chain[1], scenario.ids[1], scenario.ids[0], scenario.bodies[1], at=timeline["submitted"]
    )
    space = spine.submit(space, scenario.ids[1], at=timeline["submitted"])
    space = spine.deliver(space, scenario.ids[2], scenario.ids[1], scenario.bodies[2], at=timeline["delivered"])

    entries_before = len(space.ledger.entries)
    unbalanced = dict(scenario.bodies[3])
    unbalanced["total"] = 999.0  # net 100.00 + tax 5.00 = 105.00, not 999.00
    refused = False
    reason = ""
    detail = ""
    try:
        spine.invoice(space, scenario.ids[3], scenario.ids[2], unbalanced, at=timeline["invoiced"])
    except Refused as refusal:
        refused = True
        reason = refusal.reason
        detail = refusal.detail

    # The pairing: the same flow with the balanced body does post the ledger, so
    # the refusal is the unbalanced figure's, not a flow that never posts.
    posted = spine.invoice(
        space, scenario.ids[3], scenario.ids[2], scenario.bodies[3], at=timeline["invoiced"]
    )
    income_account = posted.policy.resolve(ROLE_INCOME)
    income_credited = sum(
        float(entry.credit)
        for entry in posted.ledger.entries
        if entry.account == income_account
    )

    passed = (
        refused
        and reason == "unbalanced-posting"
        and entries_before == 0
        and len(space.ledger.entries) == entries_before
        and len(posted.ledger.entries) > entries_before
        and income_credited > 0
    )
    return {
        "controlId": "failed-payment-ledger-untouched",
        "passed": passed,
        "refusedBy": reason,
        "detail": detail,
        "evidence": {
            "ledgerEntriesBefore": entries_before,
            "ledgerEntriesAfterRefusal": len(space.ledger.entries),
            "ledgerUntouched": len(space.ledger.entries) == entries_before,
            "balancedPostingEntries": len(posted.ledger.entries),
            "balancedIncomeCredited": income_credited,
        },
    }


def _control_malformed_event() -> Dict[str, Any]:
    """An unparseable metered event is quarantined: refused, nothing written."""
    from integrations.erp.finops.harness import build_workspace, stamp
    from integrations.erp.finops.model import Refused

    workspace = build_workspace()
    audit_before = workspace.audit.count(TENANT)
    usage_before = workspace.usage.count()
    refused = False
    reason = ""
    detail = ""
    try:
        workspace.meter.create(
            "sales-order", tenant=TENANT, document_id="SO-MAL", actor=ACTOR, at="half past three"
        )
    except Refused as refusal:
        refused = True
        reason = refusal.code
        detail = refusal.detail

    # The pairing: a well-formed event is metered normally, so the quarantine is
    # the malformed timestamp's, not a sink that never writes.
    workspace.meter.create(
        "sales-order", tenant=TENANT, document_id="SO-WELL", actor=ACTOR, at=stamp(0)
    )

    passed = (
        refused
        and reason == "invalid-timestamp"
        and workspace.audit.count(TENANT) == audit_before + 1
        and workspace.usage.count() == usage_before + 1
    )
    return {
        "controlId": "malformed-event-quarantined",
        "passed": passed,
        "refusedBy": reason,
        "detail": detail,
        "evidence": {
            "auditRecordsAfter": workspace.audit.count(TENANT),
            "usageRecordsAfter": workspace.usage.count(),
            "quarantinedWroteNothing": (
                workspace.audit.count(TENANT) == audit_before + 1
                and workspace.usage.count() == usage_before + 1
            ),
        },
    }


# --------------------------------------------------------------------------- #
# the evidence writer / CLI
# --------------------------------------------------------------------------- #
def run_pf10(*, work_dir: Optional[str] = None) -> Dict[str, Any]:
    """Run the simulation and its controls, and return JSON-serializable evidence."""
    simulation = probe_golden_path()
    controls = probe_negative_controls()
    failures = list(simulation.failures())
    if not controls["passed"]:
        failures.append(f"controls: {', '.join(controls['failedControls'])} failed")
    payload = {
        "gate": "e2e.erp_finops_golden",
        "issue": 675,
        "tenant": TENANT,
        "signup": simulation.signup,
        "billing": simulation.billing,
        "revenue": simulation.revenue,
        "negativeControls": controls,
        "failures": failures,
        "passed": not failures,
    }
    if work_dir:
        write_evidence(work_dir, "erp-finops-golden.json", payload)
    return payload


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    out = None
    if len(argv) > 1 and argv[0] == "--out":
        out = argv[1]
    payload = run_pf10(work_dir=out)
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    print(f"PF-10 SIMULATION: {'PASS' if payload['passed'] else 'FAIL'}")
    return 0 if payload["passed"] else 1


if __name__ == "__main__":  # pragma: no cover - a manual entry point
    raise SystemExit(main())
