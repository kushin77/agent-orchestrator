"""The CRM / projects / quality / support document model (issue #650, EPIC #645).

This is the model half of the ERP module's **CRM-family** lane: the document
envelope, the closed kind/action/refusal vocabularies, and the refusal type
every other module in this package raises. It deliberately holds no workflow
and no store — a document here is a *value*, and the only way its state changes
is a transition that is legal under the declaration set it was created with
(``workflow.py``) and that is recorded on the audit rail (``audit.py``).

**Why the refusal vocabulary is closed.** ``REFUSALS`` is the complete set of
machine-readable reasons this package can refuse for, and :class:`Refused`
refuses to carry a code outside it. A refusal that invents a code is a refusal
a caller cannot branch on and a gate cannot assert, so it is a bug in the
refuser, not a condition of the input — and it fails here, at the raise, rather
than silently reaching a consumer. ``negative_control.py`` provokes every code
in the set, and ``tests/test_negative_control.py`` fails the suite when the
provoked set and this set diverge, so a new refusal cannot be added without a
control that demonstrates it refusing.

**Self-containment (lane boundary).** This package imports nothing from
``integrations/erp/core`` or ``integrations/erp/catalog`` — sibling lanes were
building those concurrently when this lane was cut. The one shape the CRM lane
needs (the *definition set*: states, transitions, vocabularies, SLA policies)
is declared locally under ``catalog/`` and loaded through the single seam
``definitions.load``, which is where the indexer-fed definitions of EPIC #645
will arrive. Replacing the local declaration with the indexer's is a change of
source, not of code.

---knowledge---
module_id: integrations.erp.crm.model
system: integrations
app: erp
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [Refused, Finding, Document]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Tuple

#: The envelope version every document in this module carries.
SCHEMA_VERSION = 1

# --- document kinds ---------------------------------------------------------

KIND_LEAD = "lead"
KIND_OPPORTUNITY = "opportunity"
KIND_CUSTOMER = "customer"
KIND_PROJECT = "project"
KIND_TASK = "task"
KIND_TIMESHEET = "timesheet"
KIND_INSPECTION = "inspection"
KIND_SUPPORT_ISSUE = "support-issue"

#: The four upstream surfaces this lane covers, as document families:
#: CRM (lead/opportunity/customer), projects (project/task/timesheet),
#: quality (inspection) and support (support-issue). Stored, not derived, so a
#: kind that is missing from a declaration set is visible rather than implied.
KINDS: Tuple[str, ...] = (
    KIND_LEAD,
    KIND_OPPORTUNITY,
    KIND_CUSTOMER,
    KIND_PROJECT,
    KIND_TASK,
    KIND_TIMESHEET,
    KIND_INSPECTION,
    KIND_SUPPORT_ISSUE,
)

# --- audit actions ----------------------------------------------------------

ACTION_CREATE = "create"
ACTION_ADVANCE = "advance"
ACTION_CONVERT = "convert"
ACTION_WIN = "win"
ACTION_LOG = "log"
ACTION_OUTCOME = "outcome"
ACTION_REINSPECT = "reinspect"
ACTION_SLA_CHECK = "sla-check"

#: The closed audit-action vocabulary. An audit entry whose action is not one of
#: these is refused (``unknown-action``): the rail is the record an auditor reads
#: back, so its action field cannot be free text.
ACTIONS: Tuple[str, ...] = (
    ACTION_ADVANCE,
    ACTION_CONVERT,
    ACTION_CREATE,
    ACTION_LOG,
    ACTION_OUTCOME,
    ACTION_REINSPECT,
    ACTION_SLA_CHECK,
    ACTION_WIN,
)

# --- the closed refusal vocabulary ------------------------------------------

#: Every reason this package refuses for. Sorted, so a diff reads.
REFUSALS = frozenset(
    {
        # declaration / schema layer
        "definitions-invalid",
        "schema-violation",
        "unsupported-schema-keyword",
        # definitions lookup
        "unknown-kind",
        "unknown-policy",
        "unknown-state",
        "unknown-vocabulary",
        # document fields
        "invalid-value",
        "missing-field",
        "unknown-field",
        "unknown-vocabulary-term",
        # workflow
        "already-converted",
        "illegal-transition",
        "not-qualified",
        "unknown-action",
        # workspace / store
        "duplicate-id",
        "unknown-document",
        # projects + timesheets
        "currency-mismatch",
        "duplicate-entry",
        "inactive-parent",
        "unknown-task",
        "wrong-project",
        # support SLA
        "clock-regression",
        "invalid-timestamp",
        # audit rail
        "audit-broken",
        # provenance (GR-10)
        "code-copied",
        "provenance-invalid",
    }
)


class Refused(Exception):
    """A refusal, carrying a machine-readable ``reason`` and the offender.

    ``reason`` is a member of :data:`REFUSALS`; ``detail`` names *what* was
    refused — the kind, the id, the field, the state — so a failure names the
    offender rather than merely that something is wrong. The vocabulary check
    lives here on purpose: a refuser that invents a code fails at the raise.
    """

    def __init__(self, reason: str, detail: str = "") -> None:
        if reason not in REFUSALS:
            raise ValueError(
                f"{reason!r} is not in the closed refusal vocabulary "
                f"({len(REFUSALS)} codes)"
            )
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail

    def to_dict(self) -> Dict[str, str]:
        return {"reason": self.reason, "detail": self.detail}


@dataclass(frozen=True)
class Finding:
    """One inconsistency found by a *whole-set* check (audit, definitions).

    Refusals are raised and name the first offender, because a transition that
    is illegal is illegal now. A check over a set (the audit chain, a
    declaration document) reports **every** problem instead, so a repaired
    artifact cannot hide a second defect behind the first.
    """

    code: str
    detail: str
    kind: str = ""
    ref: str = ""

    def to_dict(self) -> Dict[str, str]:
        return {
            "code": self.code,
            "detail": self.detail,
            "kind": self.kind,
            "ref": self.ref,
        }


# --- the document envelope --------------------------------------------------


@dataclass(frozen=True)
class Document:
    """One ERP document: an envelope plus the kind's own fields.

    ``state`` is the workflow state and is *only* ever changed by
    ``workflow.advance`` — the dataclass is frozen, so a caller holding a
    document cannot mutate it into a state no transition licensed. ``fields``
    holds the kind-specific data, already validated against the definition set
    by ``documents.parse``; a value that reaches this type has been measured.
    """

    kind: str
    id: str
    tenant: str
    state: str
    title: str = ""
    owner: str = ""
    fields: Mapping[str, Any] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        # A frozen value type must not alias the caller's mapping: otherwise a
        # caller holding the source dict can mutate a document it never touched,
        # and the "frozen" promise would hold only for the attributes.
        object.__setattr__(self, "fields", dict(self.fields))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "kind": self.kind,
            "id": self.id,
            "tenant": self.tenant,
            "state": self.state,
            "title": self.title,
            "owner": self.owner,
            "fields": dict(self.fields),
        }

    def with_state(self, state: str) -> "Document":
        return Document(
            kind=self.kind,
            id=self.id,
            tenant=self.tenant,
            state=state,
            title=self.title,
            owner=self.owner,
            fields=dict(self.fields),
            schema_version=self.schema_version,
        )

    def with_fields(self, fields: Mapping[str, Any]) -> "Document":
        return Document(
            kind=self.kind,
            id=self.id,
            tenant=self.tenant,
            state=self.state,
            title=self.title,
            owner=self.owner,
            fields=dict(fields),
            schema_version=self.schema_version,
        )
