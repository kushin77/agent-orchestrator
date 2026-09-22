"""The transactional spine's document model (ERP-03, issue #648).

This is the model half of the ERP module's **transactional-spine** lane: the
envelope a spine document carries, the closed action and refusal vocabularies, and
the value types every other module in this package is written against. It holds no
workflow, no ledger and no ERP fact — a document here is a *value* whose state only
ever changes through a transition the durable model declares
(``integrations.erp.core``) and which the audit rail then records.

**Why nothing about ERP is restated here.** The document kinds, their states,
their transitions, their ``docstatus`` values and the shape of every field are
owned by ERP-02 (#647) and by the indexer catalogue ERP-01 (#646) serves. This
module therefore declares *no* kind, *no* state and *no* transition: it resolves
all of them through :mod:`.definitions`, which reads the indexer and the ERP-02
model. Its own vocabularies are the two things ERP-02 has no opinion about —
the actions the spine records on its rail, and the reasons the spine itself
refuses — plus the posting **roles** of a derived ledger entry, which is the one
shape this lane introduces (see :mod:`.ledger`).

**Why the refusal vocabulary is closed.** :data:`REFUSALS` is the complete set of
machine-readable reasons this package can refuse for, and :class:`Refused`
refuses to carry a code outside it. A refusal that invents a code is a refusal a
caller cannot branch on and a gate cannot assert, so it is a bug in the refuser,
not a condition of the input — and it fails here, at the raise, rather than
silently reaching a consumer. ``negative_control.py`` provokes every code in the
set and ``tests/test_negative_control.py`` fails when the provoked set and this
set diverge.

---knowledge---
module_id: integrations.erp.tx.model
system: integrations
app: erp
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [Refused, Finding, TxDocument]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Tuple

#: The envelope version every spine document carries.
SCHEMA_VERSION = 1

# --- the spine's action vocabulary ------------------------------------------

ACTION_DRAFT = "draft"
ACTION_SUBMIT = "submit"
ACTION_COMPLETE = "complete"
ACTION_DELIVER = "deliver"
ACTION_INVOICE = "invoice"
ACTION_POST = "post"
ACTION_CANCEL = "cancel"
ACTION_REVERSE = "reverse"

#: The closed vocabulary of actions the spine records. A rail entry whose action
#: is not one of these is refused (``unknown-action``): the rail is the record an
#: auditor reads back, so its action column cannot be free text. These are the
#: *spine's* verbs — what the loop did — which is deliberately not the same thing
#: as a document's own transition action (that one comes from ERP-02).
ACTIONS: Tuple[str, ...] = (
    ACTION_CANCEL,
    ACTION_COMPLETE,
    ACTION_DELIVER,
    ACTION_DRAFT,
    ACTION_INVOICE,
    ACTION_POST,
    ACTION_REVERSE,
    ACTION_SUBMIT,
)

# --- the posting roles ------------------------------------------------------

#: The roles a derived ledger entry is written against. This is the one piece of
#: vocabulary the *spine* owns: ERP-02 declares that a ``gl-posting`` line names
#: an ``account``, but nothing upstream says which account a sales invoice's
#: receivable side belongs to — that is the spine's own derivation contract. The
#: account **values** are never declared here; they are scenario input supplied
#: through :class:`~.ledger.PostingPolicy`, so no ERP fact is restated and no
#: chart of accounts is baked into the code.
#:
#: Only two roles, and deliberately so. A tax entry needs no role at all: ERP-02's
#: invoice schema requires every tax line to name its own ``account``, so the
#: spine reads it from the document instead of guessing it from a chart. And no
#: role exists for the stock side, because ERP-02's ``gl-posting`` accepts a
#: closed ``voucher_type`` enum that does not include ``delivery-note`` — a
#: delivery moves stock but cannot commit the ledger, so a role only a delivery
#: could use would be vocabulary nothing can reach.
ROLE_INCOME = "income"
ROLE_RECEIVABLE = "receivable"

POSTING_ROLES: Tuple[str, ...] = (
    ROLE_INCOME,
    ROLE_RECEIVABLE,
)

# --- the closed refusal vocabulary ------------------------------------------

#: Every reason this package refuses for. Sorted, so a diff reads.
REFUSALS = frozenset(
    {
        # the declaration layer (indexer + ERP-02)
        "catalogue-invalid",
        "definitions-invalid",
        "index-unavailable",
        "not-drivable",
        "undeclared-document",
        "unknown-document-kind",
        # document fields
        "invalid-value",
        "missing-field",
        # the durable model's own refusal, re-raised in this vocabulary
        "illegal-transition",
        "schema-violation",
        # the spine's state discipline
        "already-reversed",
        "already-submitted",
        "cancelled-document",
        "not-submitted",
        "unknown-action",
        # the workspace and its ledgers
        "currency-mismatch",
        "duplicate-id",
        "live-dependant",
        "unknown-document",
        "unknown-item",
        "unknown-warehouse",
        "wrong-document",
        # the selling cycle's quantities
        "over-delivery",
        "over-invoice",
        # the accounting derivation
        "not-a-stock-item",
        "unbalanced-posting",
        "unknown-account-role",
        # the rail
        "audit-broken",
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
    """One inconsistency found by a *whole-set* check (the rail, the ledgers).

    A refusal is raised and names the first offender, because a transition that
    is illegal is illegal now. A check over a whole structure (an audit chain, a
    ledger's balance proof) reports **every** problem instead, so a repaired
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


# --- a document -------------------------------------------------------------


def _require_identifier(value: Any, field_name: str, ref: str) -> str:
    """A non-empty string that is not all whitespace, or a named refusal."""
    if not isinstance(value, str) or not value.strip():
        raise Refused(
            "invalid-value", f"{ref}: {field_name} must be a non-empty string"
        )
    return value


@dataclass(frozen=True)
class TxDocument:
    """One spine document: the ERP-02 body, and the spine's own handle on it.

    ``body`` is the document exactly as ERP-02 validated it — ``doctype``, ``id``,
    ``state``, ``docstatus`` and every family field. ``state`` and ``docstatus``
    are *views* of the body rather than copied attributes, so there is one source
    of truth and no way for the handle to disagree with the document. The spine
    never rewrites a family field; it only moves ``state``/``docstatus`` along a
    transition ERP-02 declares, which is why :meth:`advanced` demands the new
    state and docstatus rather than computing them: the workflow is the authority
    and it lives in :class:`~.definitions.DefinitionSet`, not here.
    """

    kind: str
    id: str
    body: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # A frozen value type must not alias the caller's mapping: otherwise a
        # caller holding the source dict can mutate a document it never touched,
        # and the "frozen" promise would hold only for the attributes.
        object.__setattr__(self, "body", dict(self.body))

    @property
    def state(self) -> str:
        value = self.body.get("state")
        return value if isinstance(value, str) else ""

    @property
    def docstatus(self) -> int:
        value = self.body.get("docstatus")
        return value if isinstance(value, int) and not isinstance(value, bool) else -1

    @property
    def company(self) -> str:
        value = self.body.get("company")
        return str(value) if isinstance(value, str) else ""

    @property
    def currency(self) -> str:
        value = self.body.get("currency")
        return str(value) if isinstance(value, str) else ""

    def link(self, field_name: str) -> Optional[str]:
        """The id this document points at through ``field_name``, when it does."""
        value = self.body.get(field_name)
        return value if isinstance(value, str) and value.strip() else None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "kind": self.kind,
            "id": self.id,
            "state": self.state,
            "docstatus": self.docstatus,
            "body": dict(self.body),
        }

    def with_body(self, body: Mapping[str, Any]) -> "TxDocument":
        """The same handle over a body the caller has changed.

        Used to attach derived data a family schema still validates (a posting
        date, an outstanding amount); state and docstatus travel in the body, so
        they cannot be silently dropped by this call.
        """
        return TxDocument(kind=self.kind, id=self.id, body=dict(body))

    def advanced(self, state: str, docstatus: int) -> "TxDocument":
        """The same document at a state its workflow declared.

        ``docstatus`` is passed rather than derived so the caller cannot *guess*
        it: :class:`~.definitions.DefinitionSet` reads it off the workflow state,
        and the only caller is the spine's own transition helper.
        """
        body = dict(self.body)
        body["state"] = state
        body["docstatus"] = docstatus
        return TxDocument(kind=self.kind, id=self.id, body=body)

    @classmethod
    def of(cls, kind: str, id: str, body: Mapping[str, Any]) -> "TxDocument":
        """Build a handle from a validated ERP-02 body.

        The state and docstatus are required from the body rather than defaulted,
        so a document that arrives without them is a refusal at the caller
        (:mod:`.spine` validates the body first) and never a silent ``draft``.
        """
        ref = _require_identifier(id, "id", f"{kind}:document")
        state = body.get("state")
        docstatus = body.get("docstatus")
        if not isinstance(state, str) or not state.strip():
            raise Refused("missing-field", f"{ref}: the document declares no state")
        if isinstance(docstatus, bool) or not isinstance(docstatus, int):
            raise Refused("missing-field", f"{ref}: the document declares no docstatus")
        return cls(kind=kind, id=ref, body=dict(body))
