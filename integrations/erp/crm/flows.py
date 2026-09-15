"""The CRM-family flows, and the workspace they run in (issue #650).

A :class:`Workspace` is the whole state of a tenant's CRM-family data: its
documents, the declaration set they were built under, and the audit rail every
change landed on. It is **immutable** — each flow returns a new workspace — so
an earlier step of a scenario stays readable after a later one runs, and the
audit rail of the returned workspace is the rail that licensed it.

That immutability is what makes "audited" a property rather than a promise: the
only functions that produce a changed workspace are :func:`create_document`,
:func:`advance` and :func:`patch`, each of which appends to the rail before it
returns. A document cannot appear, move or change without a matching entry.

The flows are the three acceptance criteria of issue #650, written once each:

* **conversion** — :func:`convert_lead` and :func:`win_opportunity` drive
  lead → opportunity → customer through the machines, refusing a lead that is
  not qualified (``not-qualified``) and a lead that already converted
  (``already-converted``). An opportunity that is not at ``proposal`` is refused
  by the machine itself, so there is exactly one place that decides legality.
* **accumulation and ageing** — :func:`log_timesheet`, :func:`project_costs` and
  :func:`age_issue`. Cost is a derived rollup (``timesheet.py``) and ageing is a
  pure function of an injected clock (``sla.py``); :func:`record_sla_check` is
  the explicit write that records what was read.
* **inspection** — :func:`record_inspection_outcome` accepts only an outcome the
  declaration declares, and :func:`reinspect` returns a failed inspection to
  ``pending``. Both are machine transitions, so a passed inspection is terminal
  because its declaration says so, not because a function checked.

Reads are pure and writes are audited, on purpose: :func:`project_costs` and
:func:`age_issue` change nothing, so a dashboard can be rendered without
polluting the trail an auditor reads.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

from . import audit
from . import documents as documents_module
from . import sla as sla_module
from . import timesheet as timesheet_module
from . import workflow
from .definitions import DefinitionSet
from .definitions import load as load_definitions
from .model import (
    ACTION_ADVANCE,
    ACTION_CONVERT,
    ACTION_CREATE,
    ACTION_LOG,
    ACTION_OUTCOME,
    ACTION_REINSPECT,
    ACTION_SLA_CHECK,
    ACTION_WIN,
    KIND_CUSTOMER,
    KIND_INSPECTION,
    KIND_LEAD,
    KIND_OPPORTUNITY,
    KIND_PROJECT,
    KIND_SUPPORT_ISSUE,
    KIND_TASK,
    KIND_TIMESHEET,
    SCHEMA_VERSION,
    Document,
    Finding,
    Refused,
)
from .timesheet import Rollup

#: The outcome states a quality inspection may reach, derived from the
#: declaration at run time and asserted against it by tests. Stated here only so
#: a caller can validate an outcome before building fields.
INSPECTION_OUTCOMES: Tuple[str, ...] = ("failed", "passed")


@dataclass(frozen=True)
class Workspace:
    """One tenant's CRM-family state: documents, declaration set, audit rail."""

    tenant: str
    definitions: DefinitionSet
    documents: Mapping[str, Document] = field(default_factory=dict)
    rail: audit.Rail = field(default_factory=audit.Rail)

    # --- reads ---------------------------------------------------------------

    def get(self, doc_id: str) -> Document:
        document = self.documents.get(doc_id)
        if document is None:
            raise Refused(
                "unknown-document",
                f"tenant {self.tenant!r} holds no document {doc_id!r}",
            )
        return document

    def require_kind(self, doc_id: str, kind: str) -> Document:
        document = self.get(doc_id)
        if document.kind != kind:
            raise Refused(
                "unknown-document",
                f"{doc_id!r} is a {document.kind}, not a {kind}",
            )
        return document

    def of_kind(self, kind: str) -> Tuple[Document, ...]:
        return tuple(
            self.documents[doc_id]
            for doc_id in sorted(self.documents)
            if self.documents[doc_id].kind == kind
        )

    def all_documents(self) -> Tuple[Document, ...]:
        return tuple(self.documents[doc_id] for doc_id in sorted(self.documents))

    # --- writes (each one audited) -------------------------------------------

    def put(
        self,
        document: Document,
        *,
        actor: str,
        at: str,
        action: str = ACTION_CREATE,
        note: str = "",
    ) -> "Workspace":
        if document.id in self.documents:
            raise Refused(
                "duplicate-id",
                f"{document.id} already exists in tenant {self.tenant!r} "
                f"as a {self.documents[document.id].kind}",
            )
        if document.tenant != self.tenant:
            raise Refused(
                "invalid-value",
                f"{document.id}: tenant {document.tenant!r} does not match the "
                f"workspace tenant {self.tenant!r}",
            )
        documents_module.validate_fields(document, self.definitions)
        rail = self.rail.append(
            at=at,
            actor=actor,
            action=action,
            kind=document.kind,
            ref=document.id,
            from_state="",
            to_state=document.state,
            note=note,
        )
        return self._replace(document, rail)

    def advance(
        self,
        doc_id: str,
        target: str,
        *,
        actor: str,
        at: str,
        note: str = "",
        action: str = ACTION_ADVANCE,
    ) -> "Workspace":
        document = self.get(doc_id)
        moved, rail = workflow.advance(
            document,
            target,
            actor=actor,
            at=at,
            definitions=self.definitions,
            rail=self.rail,
            note=note,
            action=action,
        )
        return self._replace(moved, rail)

    def patch(
        self,
        doc_id: str,
        values: Mapping[str, Any],
        *,
        actor: str,
        at: str,
        note: str = "",
        action: str = ACTION_LOG,
    ) -> "Workspace":
        document = self.get(doc_id)
        fields = dict(document.fields)
        fields.update(values)
        updated = document.with_fields(fields)
        documents_module.validate_fields(updated, self.definitions)
        rail = self.rail.append(
            at=at,
            actor=actor,
            action=action,
            kind=updated.kind,
            ref=updated.id,
            from_state=document.state,
            to_state=updated.state,
            note=note,
        )
        return self._replace(updated, rail)

    def _replace(self, document: Document, rail: audit.Rail) -> "Workspace":
        documents = dict(self.documents)
        documents[document.id] = document
        return Workspace(
            tenant=self.tenant,
            definitions=self.definitions,
            documents=documents,
            rail=rail,
        )

    # --- self-check ----------------------------------------------------------

    def findings(self) -> List[Finding]:
        """Every way this workspace fails its own invariants (empty is OK).

        Reported rather than raised: this is the whole-set check a `check`
        verb and a test want, and a repaired document must not be able to hide
        a second problem behind it.
        """
        problems: List[Finding] = list(self.rail.verify())
        for doc_id in sorted(self.documents):
            document = self.documents[doc_id]
            if not self.definitions.has_kind(document.kind):
                problems.append(
                    Finding(
                        "unknown-kind",
                        f"{doc_id}: kind {document.kind!r} is not declared",
                        ref=doc_id,
                    )
                )
                continue
            declared = self.definitions.kind(document.kind)
            if not declared.is_state(document.state):
                problems.append(
                    Finding(
                        "unknown-state",
                        f"{doc_id}: state {document.state!r} is not a state of a "
                        f"{document.kind}",
                        kind=document.kind,
                        ref=doc_id,
                    )
                )
            try:
                documents_module.validate_fields(document, self.definitions)
            except Refused as exc:
                problems.append(
                    Finding(exc.reason, f"{doc_id}: {exc.detail}", document.kind, doc_id)
                )
        return problems


def workspace(tenant: str, definitions: Optional[DefinitionSet] = None) -> Workspace:
    """A fresh, empty workspace for ``tenant`` under the declaration set."""
    return Workspace(
        tenant=tenant,
        definitions=definitions if definitions is not None else load_definitions(),
    )


# --- generic writes ---------------------------------------------------------


def create_document(
    space: Workspace,
    kind: str,
    doc_id: str,
    fields: Mapping[str, Any],
    *,
    actor: str,
    at: str,
    title: str = "",
    owner: str = "",
    action: str = ACTION_CREATE,
    note: str = "",
) -> Workspace:
    """Create a document in its kind's declared initial state, audited.

    The caller cannot choose the state: the machine's initial state is the
    declaration's business, so a document is born where its declaration says.
    """
    declared = space.definitions.kind(kind)
    envelope = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": kind,
        "id": doc_id,
        "tenant": space.tenant,
        "state": declared.initial,
        "title": title,
        "owner": owner,
        "fields": dict(fields),
    }
    document = documents_module.parse(envelope, space.definitions, where=doc_id)
    return space.put(document, actor=actor, at=at, action=action, note=note)


def advance(
    space: Workspace,
    doc_id: str,
    target: str,
    *,
    actor: str,
    at: str,
    note: str = "",
    action: str = ACTION_ADVANCE,
) -> Workspace:
    """Move a document through its machine, audited."""
    return space.advance(doc_id, target, actor=actor, at=at, note=note, action=action)


def patch(
    space: Workspace,
    doc_id: str,
    values: Mapping[str, Any],
    *,
    actor: str,
    at: str,
    note: str = "",
    action: str = ACTION_LOG,
) -> Workspace:
    """Change a document's fields without changing its state, audited."""
    return space.patch(doc_id, values, actor=actor, at=at, note=note, action=action)


# --- CRM: lead -> opportunity -> customer -----------------------------------


def convert_lead(
    space: Workspace,
    lead_id: str,
    opportunity_id: str,
    *,
    actor: str,
    at: str,
    opportunity_fields: Optional[Mapping[str, Any]] = None,
    note: str = "",
) -> Workspace:
    """Convert a qualified lead into an opportunity.

    Two refusals the machine cannot express, because both are true *before* a
    transition is attempted:

    * ``not-qualified`` — the lead is in some other state, so there is nothing to
      convert yet. The message names the state it is in and the one it needs.
    * ``already-converted`` — the transition has already happened. The machine
      would refuse this too (``converted`` is terminal), but a caller reading
      "illegal-transition" for a second conversion would be told the wrong
      thing: the first conversion succeeded, and no state change is wanted.
    """
    lead = space.require_kind(lead_id, KIND_LEAD)
    if lead.state == "converted":
        raise Refused(
            "already-converted",
            f"{lead_id} is already converted; a lead converts once",
        )
    if lead.state != "qualified":
        raise Refused(
            "not-qualified",
            f"{lead_id} is in state {lead.state!r}; a lead must be 'qualified' "
            "to convert",
        )

    fields: Dict[str, Any] = {
        "company": lead.fields.get("company"),
        "amount": lead.fields.get("value", 0),
    }
    for name, value in (opportunity_fields or {}).items():
        fields[name] = value
    if "stage" not in fields:
        fields["stage"] = space.definitions.vocabulary("opportunity-stages")[0]
    if "currency" not in fields:
        fields["currency"] = "EUR"

    converted = advance(
        space, lead_id, "converted", actor=actor, at=at, action=ACTION_CONVERT, note=note
    )
    return create_document(
        converted,
        KIND_OPPORTUNITY,
        opportunity_id,
        fields,
        actor=actor,
        at=at,
        title=f"Opportunity from {lead_id}",
        owner=lead.owner,
        action=ACTION_CONVERT,
        note=note,
    )


def win_opportunity(
    space: Workspace,
    opportunity_id: str,
    customer_id: str,
    *,
    actor: str,
    at: str,
    customer_fields: Optional[Mapping[str, Any]] = None,
    note: str = "",
) -> Workspace:
    """Win a proposal-stage opportunity and create the customer it earns.

    Legality is the machine's call, not this function's: an opportunity that is
    not at ``proposal`` is refused by ``workflow.advance`` with the reachable
    states named, so there is one place that decides whether a sale can close.
    """
    opportunity = space.require_kind(opportunity_id, KIND_OPPORTUNITY)
    fields: Dict[str, Any] = {
        "legal_name": opportunity.fields.get("company"),
        "tier": space.definitions.vocabulary("customer-tiers")[0],
    }
    for name, value in (customer_fields or {}).items():
        fields[name] = value
    if "currency" not in fields and "currency" in opportunity.fields:
        fields["currency"] = opportunity.fields["currency"]

    won = advance(
        space,
        opportunity_id,
        "won",
        actor=actor,
        at=at,
        action=ACTION_WIN,
        note=note,
    )
    return create_document(
        won,
        KIND_CUSTOMER,
        customer_id,
        fields,
        actor=actor,
        at=at,
        title=f"Customer from {opportunity_id}",
        owner=opportunity.owner,
        action=ACTION_WIN,
        note=note,
    )


# --- projects, tasks, timesheets --------------------------------------------


def start_project(
    space: Workspace,
    project_id: str,
    fields: Mapping[str, Any],
    *,
    actor: str,
    at: str,
    title: str = "",
    owner: str = "",
) -> Workspace:
    """Create a project and move it to ``active`` (its ``planned`` state is also open)."""
    created = create_document(
        space, KIND_PROJECT, project_id, fields, actor=actor, at=at, title=title, owner=owner
    )
    return advance(created, project_id, "active", actor=actor, at=at)


def start_task(
    space: Workspace,
    task_id: str,
    fields: Mapping[str, Any],
    *,
    actor: str,
    at: str,
    title: str = "",
    owner: str = "",
) -> Workspace:
    """Create a task, which must name the project it belongs to."""
    return create_document(
        space, KIND_TASK, task_id, fields, actor=actor, at=at, title=title, owner=owner
    )


def log_timesheet(
    space: Workspace,
    entry_id: str,
    fields: Mapping[str, Any],
    *,
    actor: str,
    at: str,
    title: str = "",
    note: str = "",
) -> Workspace:
    """Log a timesheet entry (create, then submit it).

    Logging submits: a ``draft`` entry has not been logged and is not counted,
    so the flow that means "this work happened" ends in the ``submitted`` state
    the rollup counts.
    """
    created = create_document(
        space,
        KIND_TIMESHEET,
        entry_id,
        fields,
        actor=actor,
        at=at,
        title=title,
        owner=actor,
        action=ACTION_LOG,
        note=note,
    )
    return advance(
        created, entry_id, "submitted", actor=actor, at=at, action=ACTION_LOG, note=note
    )


def approve_timesheet(space: Workspace, entry_id: str, *, actor: str, at: str, note: str = "") -> Workspace:
    return advance(space, entry_id, "approved", actor=actor, at=at, action=ACTION_LOG, note=note)


def reject_timesheet(space: Workspace, entry_id: str, *, actor: str, at: str, note: str = "") -> Workspace:
    return advance(space, entry_id, "rejected", actor=actor, at=at, action=ACTION_LOG, note=note)


def project_costs(space: Workspace, project: str, task: str) -> Rollup:
    """The derived cost rollup for one task inside one project (a pure read)."""
    return timesheet_module.accumulate(
        space.all_documents(), project=project, task=task, definitions=space.definitions
    )


# --- quality: inspections ---------------------------------------------------


def start_inspection(
    space: Workspace,
    inspection_id: str,
    fields: Mapping[str, Any],
    *,
    actor: str,
    at: str,
    title: str = "",
    owner: str = "",
) -> Workspace:
    """Create an inspection and move it to ``in-progress``."""
    created = create_document(
        space,
        KIND_INSPECTION,
        inspection_id,
        fields,
        actor=actor,
        at=at,
        title=title,
        owner=owner,
    )
    return advance(created, inspection_id, "in-progress", actor=actor, at=at)


def inspection_outcomes(space: Workspace, inspection_id: str) -> Tuple[str, ...]:
    """The outcomes this inspection may record *now*, from the declaration."""
    document = space.require_kind(inspection_id, KIND_INSPECTION)
    declared = space.definitions.kind(KIND_INSPECTION)
    return tuple(
        sorted(
            state
            for state in declared.transitions.get(document.state, ())
            if state in INSPECTION_OUTCOMES
        )
    )


def record_inspection_outcome(
    space: Workspace,
    inspection_id: str,
    outcome: str,
    *,
    actor: str,
    at: str,
    note: str = "",
) -> Workspace:
    """Record a pass/fail outcome, refusing anything the declaration does not declare.

    An outcome outside :data:`INSPECTION_OUTCOMES` is ``unknown-state`` (a state
    the kind does not have at all); a declared outcome the current state cannot
    reach is ``illegal-transition``. Both come from the machine, so a new
    outcome is added by declaring it, not by editing this function.
    """
    if outcome not in INSPECTION_OUTCOMES:
        declared = space.definitions.kind(KIND_INSPECTION)
        raise Refused(
            "unknown-state",
            f"{inspection_id}: {outcome!r} is not an inspection outcome "
            f"(declared outcomes: {', '.join(INSPECTION_OUTCOMES)}; states: "
            f"{', '.join(declared.states)})",
        )
    return advance(
        space,
        inspection_id,
        outcome,
        actor=actor,
        at=at,
        action=ACTION_OUTCOME,
        note=note,
    )


def reinspect(space: Workspace, inspection_id: str, *, actor: str, at: str, note: str = "") -> Workspace:
    """Return a failed inspection to ``pending`` for another pass."""
    return advance(
        space,
        inspection_id,
        "pending",
        actor=actor,
        at=at,
        action=ACTION_REINSPECT,
        note=note,
    )


# --- support: issues and SLA ageing ----------------------------------------


def open_issue(
    space: Workspace,
    issue_id: str,
    fields: Mapping[str, Any],
    *,
    actor: str,
    at: str,
    title: str = "",
    owner: str = "",
) -> Workspace:
    """Open a support issue. ``fields`` must carry ``priority``, ``channel`` and ``opened_at``."""
    return create_document(
        space,
        KIND_SUPPORT_ISSUE,
        issue_id,
        fields,
        actor=actor,
        at=at,
        title=title,
        owner=owner,
    )


def triage_issue(space: Workspace, issue_id: str, *, actor: str, at: str, note: str = "") -> Workspace:
    return advance(space, issue_id, "in-progress", actor=actor, at=at, note=note)


def respond_to_issue(
    space: Workspace, issue_id: str, *, responded_at: str, actor: str, at: str, note: str = ""
) -> Workspace:
    """Stop the response clock by recording when the tenant first responded."""
    return patch(
        space,
        issue_id,
        {"responded_at": responded_at},
        actor=actor,
        at=at,
        note=note,
    )


def resolve_issue(
    space: Workspace,
    issue_id: str,
    *,
    resolved_at: str,
    responded_at: Optional[str] = None,
    actor: str,
    at: str,
    note: str = "",
) -> Workspace:
    """Stop the resolution clock and move the issue to ``resolved``."""
    values: Dict[str, Any] = {"resolved_at": resolved_at}
    if responded_at is not None:
        values["responded_at"] = responded_at
    recorded = patch(space, issue_id, values, actor=actor, at=at, note=note)
    return advance(recorded, issue_id, "resolved", actor=actor, at=at, note=note)


def reopen_issue(space: Workspace, issue_id: str, *, actor: str, at: str, note: str = "") -> Workspace:
    """Reopen a resolved issue (``resolved`` -> ``open``, declared by the machine)."""
    return advance(space, issue_id, "open", actor=actor, at=at, note=note)


def age_issue(space: Workspace, issue_id: str, now: str) -> sla_module.SLAState:
    """The issue's SLA ageing at ``now`` (a pure read; nothing is recorded)."""
    return sla_module.age(space.get(issue_id), now, definitions=space.definitions)


def record_sla_check(
    space: Workspace, issue_id: str, now: str, *, actor: str, at: str
) -> Tuple[Workspace, sla_module.SLAState]:
    """Read the ageing **and** record that it was read, returning both."""
    state = age_issue(space, issue_id, now)
    recorded = patch(
        space,
        issue_id,
        {},
        actor=actor,
        at=at,
        action=ACTION_SLA_CHECK,
        note=f"{state.state} at {state.now}",
    )
    return recorded, state


# --- the golden path --------------------------------------------------------


@dataclass(frozen=True)
class GoldenPath:
    """The deterministic end-to-end scenario this lane's tests and `check` use."""

    workspace: Workspace
    rollup: Rollup
    sla_states: Mapping[str, sla_module.SLAState]
    findings: Tuple[Finding, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tenant": self.workspace.tenant,
            "documents": len(self.workspace.documents),
            "auditEntries": len(self.workspace.rail),
            "auditHead": self.workspace.rail.head,
            "rollup": self.rollup.to_dict(),
            "sla": {name: state.to_dict() for name, state in sorted(self.sla_states.items())},
            "findings": [finding.to_dict() for finding in self.findings],
        }


#: The scenario's fixed clock. Held as constants so the golden path cannot drift
#: with the calendar: two runs, a year apart, produce the same audit digests.
T = {
    "opened": "2026-09-01T09:00:00Z",
    "contacted": "2026-09-01T10:00:00Z",
    "qualified": "2026-09-02T09:00:00Z",
    "converted": "2026-09-02T10:00:00Z",
    "proposal": "2026-09-03T09:00:00Z",
    "won": "2026-09-04T09:00:00Z",
    "project": "2026-09-05T09:00:00Z",
    "task": "2026-09-05T10:00:00Z",
    "ts1": "2026-09-06T09:00:00Z",
    "ts2": "2026-09-07T09:00:00Z",
    "ts3": "2026-09-08T09:00:00Z",
    "inspection": "2026-09-09T09:00:00Z",
    "inspection_failed": "2026-09-09T10:00:00Z",
    "reinspect": "2026-09-09T11:00:00Z",
    "inspection_resumed": "2026-09-09T11:30:00Z",
    "inspection_passed": "2026-09-09T12:00:00Z",
    "issue_open": "2026-09-10T08:00:00Z",
    "issue_triage": "2026-09-10T08:30:00Z",
    "issue_responded": "2026-09-10T09:00:00Z",
    "issue_resolved": "2026-09-10T15:00:00Z",
    "issue_closed": "2026-09-11T09:00:00Z",
}

#: The instants the golden path reads the SLA board at. Each one is chosen to
#: land on a different verdict, so the scenario demonstrates all three states
#: from real declarations rather than describing them.
SLA_READINGS = (
    ("ISS-0001/at-open", "ISS-0001", "2026-09-10T08:10:00Z"),
    ("ISS-0001/after-close", "ISS-0001", "2026-09-12T09:00:00Z"),
    ("ISS-0002/running", "ISS-0002", "2026-09-10T23:00:00Z"),
    ("ISS-0002/due", "ISS-0002", "2026-09-11T04:00:00Z"),
    ("ISS-0002/breached", "ISS-0002", "2026-09-11T12:00:00Z"),
)

ACTOR = "agent:erp-crm-lane"


def _at(key: str) -> str:
    return T[key]


def golden_path(tenant: str = "acme", definitions: Optional[DefinitionSet] = None) -> GoldenPath:
    """Run the whole CRM-family scenario offline and return what it produced."""
    space = workspace(tenant, definitions)

    # --- CRM: lead -> opportunity -> customer ---
    space = create_document(
        space,
        KIND_LEAD,
        "LEAD-0001",
        {"company": "Northwind Traders", "stage": "new", "source": "web", "value": 250000},
        actor=ACTOR,
        at=_at("opened"),
        title="Northwind renewal",
        owner="rep-1",
    )
    space = advance(space, "LEAD-0001", "contacted", actor=ACTOR, at=_at("contacted"))
    space = advance(space, "LEAD-0001", "qualified", actor=ACTOR, at=_at("qualified"))
    space = convert_lead(space, "LEAD-0001", "OPP-0001", actor=ACTOR, at=_at("converted"))
    space = advance(space, "OPP-0001", "proposal", actor=ACTOR, at=_at("proposal"))
    space = win_opportunity(space, "OPP-0001", "CUST-0001", actor=ACTOR, at=_at("won"))

    # --- projects, tasks, timesheets ---
    space = start_project(
        space,
        "PROJ-0001",
        {
            "project_type": "delivery",
            "customer": "CUST-0001",
            "budget_minor": 5000000,
            "currency": "EUR",
        },
        actor=ACTOR,
        at=_at("project"),
        title="Northwind delivery",
    )
    space = start_task(
        space,
        "TASK-0001",
        {
            "project": "PROJ-0001",
            "task_type": "implementation",
            "estimate_minutes": 240,
            "rate_minor": 9000,
            "currency": "EUR",
        },
        actor=ACTOR,
        at=_at("task"),
        title="Implement the integration",
    )
    for entry, minutes, work_date, moment in (
        ("TS-0001", 120, "2026-09-06", "ts1"),
        ("TS-0002", 90, "2026-09-07", "ts2"),
        ("TS-0003", 30, "2026-09-08", "ts3"),
    ):
        space = log_timesheet(
            space,
            entry,
            {
                "project": "PROJ-0001",
                "task": "TASK-0001",
                "minutes": minutes,
                "work_date": work_date,
                "rate_minor": 9000,
                "currency": "EUR",
            },
            actor="rep-1",
            at=_at(moment),
        )
    # One entry settled, one still owed, one refused: the rejected entry must not
    # reach the rollup.
    space = approve_timesheet(space, "TS-0001", actor="rep-1", at=_at("ts2"))
    space = reject_timesheet(space, "TS-0003", actor="rep-1", at=_at("ts3"))

    # --- quality: a failed inspection is re-inspectable, a passed one is terminal ---
    space = start_inspection(
        space,
        "INSP-0001",
        {"template": "incoming", "subject": "PROJ-0001 batch 1", "sample_size": 20},
        actor="qa-1",
        at=_at("inspection"),
        title="Incoming inspection",
    )
    space = record_inspection_outcome(
        space,
        "INSP-0001",
        "failed",
        actor="qa-1",
        at=_at("inspection_failed"),
        note="two units out of tolerance",
    )
    space = reinspect(space, "INSP-0001", actor="qa-1", at=_at("reinspect"))
    space = advance(
        space, "INSP-0001", "in-progress", actor="qa-1", at=_at("inspection_resumed")
    )
    space = record_inspection_outcome(
        space,
        "INSP-0001",
        "passed",
        actor="qa-1",
        at=_at("inspection_passed"),
        note="reworked batch accepted",
    )

    # --- support: a ticket resolved inside both windows, and one still ageing ---
    space = open_issue(
        space,
        "ISS-0001",
        {
            "priority": "p1",
            "channel": "portal",
            "customer": "CUST-0001",
            "subject": "Cannot export the monthly report",
            "opened_at": _at("issue_open"),
        },
        actor="portal",
        at=_at("issue_open"),
        title="Export fails",
    )
    space = triage_issue(space, "ISS-0001", actor="support-1", at=_at("issue_triage"))
    space = respond_to_issue(
        space,
        "ISS-0001",
        responded_at=_at("issue_responded"),
        actor="support-1",
        at=_at("issue_responded"),
    )
    space = resolve_issue(
        space,
        "ISS-0001",
        resolved_at=_at("issue_resolved"),
        actor="support-1",
        at=_at("issue_resolved"),
    )
    space = advance(space, "ISS-0001", "closed", actor="support-1", at=_at("issue_closed"))

    space = open_issue(
        space,
        "ISS-0002",
        {
            "priority": "p2",
            "channel": "email",
            "customer": "CUST-0001",
            "subject": "Scheduled import ran twice",
            "opened_at": _at("issue_open"),
        },
        actor="email",
        at=_at("issue_open"),
        title="Import ran twice",
    )
    space = triage_issue(space, "ISS-0002", actor="support-1", at=_at("issue_triage"))

    sla_states = {
        name: sla_module.age(space.get(issue), now, definitions=space.definitions)
        for name, issue, now in SLA_READINGS
    }
    return GoldenPath(
        workspace=space,
        rollup=project_costs(space, "PROJ-0001", "TASK-0001"),
        sla_states=sla_states,
        findings=tuple(space.findings()),
    )
