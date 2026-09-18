"""The workspace, the flows and the golden path (ERP-03, issue #648).

This is the transactional spine itself: a workspace holding documents, an item
master, a warehouse set, a posting policy and the two ledgers, and the flows that
move a cycle through it.

Acceptance criterion 1 — "an offline golden flow posts a complete sales cycle with
correct stock and GL effects, deterministic and keyless" — is what
:func:`golden_path` demonstrates, and each of its three adjectives is mechanical:

* **offline**: nothing here opens a socket, reads a clock or touches a container.
  The whole cycle is values in and values out;
* **deterministic**: the clock is *injected* (:data:`TIMELINE`), the audit rail
  digests only supplied values, and both ledgers are arithmetic over them, so two
  runs produce the same rail head and the same balances — which ``cli.check``
  asserts by running the cycle twice;
* **keyless**: no identifier is derived from a time, a counter or a random
  source. Every document id is supplied by the scenario, which is what makes the
  cycle reproducible rather than merely repeatable.

Acceptance criterion 2 — "cancellation reverses prior effects; resubmitting the
same document is refused" — is :func:`cancel` plus the state discipline in
:func:`submit`: a cancellation posts the *exact* inverse of what the document
applied and then proves the net is zero (``reversal-mismatch`` otherwise), and a
second submit of an already-submitted document is ``already-submitted``.

**No kind name and no action name is written down here.** The cycle's order, the
field a document links its predecessor through, and the actions that submit and
cancel it are all read off the resolved definition set
(:class:`~.definitions.DefinitionSet`), which derives them from the ERP-02
schemas and workflows. The scenario supplies *data* — ids, parties, items,
quantities, and the chart of accounts — and the flows supply only the shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

from . import audit
from .definitions import (
    VOUCHER_ID_FIELD,
    VOUCHER_TYPE_FIELD,
    DefinitionSet,
    load as load_definitions,
)
from .ledger import (
    GeneralLedger,
    PostingPolicy,
    invert as invert_ledger,
    posting_entries,
    posting_lines,
)
from .model import (
    ACTION_CANCEL,
    ACTION_COMPLETE,
    ACTION_DRAFT,
    ACTION_POST,
    ACTION_REVERSE,
    ACTION_SUBMIT,
    Finding,
    Refused,
    TxDocument,
)
from .stock import (
    LINE_ITEM_CODE,
    LINE_QTY,
    StockLedger,
    invert as invert_stock,
    movements_for,
    quantity,
)

#: The declared clock. Every step names the moment it happened, so the rail is a
#: function of the scenario rather than of when the scenario ran.
TIMELINE: Mapping[str, str] = {
    "quoted": "2026-03-02T09:00:00Z",
    "submitted": "2026-03-02T10:00:00Z",
    "accepted": "2026-03-02T11:00:00Z",
    "delivered": "2026-03-05T08:00:00Z",
    "invoiced": "2026-03-06T09:00:00Z",
    "cancelled": "2026-03-07T09:00:00Z",
}

#: Who the rail records as acting. A scenario value, not a credential.
ACTOR = "erp-tx"

#: The document fields the spine sets itself, and therefore refuses from a
#: scenario: a caller that could set `state` could move a document without a
#: transition, which is the one thing this package exists to prevent.
RESERVED_FIELDS: Tuple[str, ...] = ("doctype", "id", "state", "docstatus")


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _quantities(lines: Any) -> Dict[str, float]:
    """Per-item quantities from a document's lines (a refusal when unusable)."""
    if not isinstance(lines, list) or not lines:
        raise Refused("missing-field", "a document with no lines delivers nothing")
    totals: Dict[str, float] = {}
    for index, line in enumerate(lines):
        if not isinstance(line, Mapping):
            raise Refused("invalid-value", f"lines[{index}] must be an object")
        item_code = line.get(LINE_ITEM_CODE)
        qty = _number(line.get(LINE_QTY))
        if not isinstance(item_code, str) or not item_code.strip():
            raise Refused("missing-field", f"lines[{index}] names no item_code")
        if qty is None:
            raise Refused("invalid-value", f"lines[{index}] carries no quantity")
        totals[item_code] = quantity(totals.get(item_code, 0.0) + qty)
    return totals


@dataclass(frozen=True)
class Workspace:
    """The spine's whole state: documents, masters, ledgers and the rail.

    Frozen, and every mutator returns a new workspace, so a caller cannot reach
    into a workspace an invariant test is holding and change it underneath. The
    ledgers are :mod:`.stock` / :mod:`.ledger` values and the rail is
    :mod:`.audit`'s, so "append-only" is a property of the types rather than a
    convention this class promises to respect.
    """

    definitions: DefinitionSet
    documents: Mapping[str, TxDocument] = field(default_factory=dict)
    items: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    warehouses: Tuple[str, ...] = ()
    policy: PostingPolicy = field(default_factory=PostingPolicy)
    stock: StockLedger = field(default_factory=StockLedger)
    ledger: GeneralLedger = field(default_factory=GeneralLedger)
    rail: audit.Rail = field(default_factory=audit.Rail)

    def __post_init__(self) -> None:
        object.__setattr__(self, "documents", dict(self.documents))
        object.__setattr__(self, "items", {k: dict(v) for k, v in self.items.items()})
        object.__setattr__(self, "warehouses", tuple(self.warehouses))

    # --- lookups ----------------------------------------------------------

    def contains(self, document_id: str) -> bool:
        return document_id in self.documents

    def get(self, document_id: str) -> TxDocument:
        document = self.documents.get(document_id)
        if document is None:
            raise Refused(
                "unknown-document",
                f"{document_id!r} is not on the workspace "
                f"({len(self.documents)} document(s) present)",
            )
        return document

    def of_kind(self, kind: str) -> Tuple[TxDocument, ...]:
        return tuple(
            document for document in self.documents.values() if document.kind == kind
        )

    def cancelled(self, kind: str, state: str) -> bool:
        """Whether ``state`` is the cancellation state ``kind``'s workflow declares."""
        return state == self.definitions.cancellation_state(kind)

    def live_of_kind(self, kind: str) -> Tuple[TxDocument, ...]:
        return tuple(
            document
            for document in self.of_kind(kind)
            if not self.cancelled(kind, document.state)
        )

    def postings_for(self, voucher_id: str) -> Tuple[TxDocument, ...]:
        """The ledger documents derived from ``voucher_id``."""
        return tuple(
            document
            for document in self.of_kind(self.definitions.accounting_kind)
            if document.link(VOUCHER_ID_FIELD) == voucher_id
        )

    def dependants(self, document_id: str) -> Tuple[TxDocument, ...]:
        """Every *live* document raised against ``document_id``.

        Deliberately excludes the postings derived from it: a ledger document is
        not a dependant of its voucher, it is the voucher's *effect*, and
        :func:`cancel` reverses it as part of cancelling the voucher. Counting it
        here would make an invoiced document uncancellable, which is the opposite
        of criterion 2.
        """
        found: List[TxDocument] = []
        for document in self.documents.values():
            if self.cancelled(document.kind, document.state):
                continue
            link = self.definitions.link_of(document.kind)
            if link is not None and document.link(link.field) == document_id:
                found.append(document)
        return tuple(found)

    # --- mutators (each returns a new workspace) --------------------------

    def with_document(self, document: TxDocument) -> "Workspace":
        documents = dict(self.documents)
        documents[document.id] = document
        return Workspace(
            definitions=self.definitions,
            documents=documents,
            items=self.items,
            warehouses=self.warehouses,
            policy=self.policy,
            stock=self.stock,
            ledger=self.ledger,
            rail=self.rail,
        )

    def with_rail(self, rail: audit.Rail) -> "Workspace":
        return Workspace(
            definitions=self.definitions,
            documents=self.documents,
            items=self.items,
            warehouses=self.warehouses,
            policy=self.policy,
            stock=self.stock,
            ledger=self.ledger,
            rail=rail,
        )

    def with_stock(self, ledger: StockLedger) -> "Workspace":
        return Workspace(
            definitions=self.definitions,
            documents=self.documents,
            items=self.items,
            warehouses=self.warehouses,
            policy=self.policy,
            stock=ledger,
            ledger=self.ledger,
            rail=self.rail,
        )

    def with_ledger(self, ledger: GeneralLedger) -> "Workspace":
        return Workspace(
            definitions=self.definitions,
            documents=self.documents,
            items=self.items,
            warehouses=self.warehouses,
            policy=self.policy,
            stock=self.stock,
            ledger=ledger,
            rail=self.rail,
        )

    # --- summary ----------------------------------------------------------

    def summary(self) -> Dict[str, Any]:
        """The measured state of the cycle: documents, stock, ledger and the head."""
        return {
            "documents": {
                document_id: {
                    "kind": document.kind,
                    "state": document.state,
                    "docstatus": document.docstatus,
                }
                for document_id, document in sorted(self.documents.items())
            },
            "stock": {
                f"{movement.item_code}@{movement.warehouse}": self.stock.balance(
                    movement.item_code, movement.warehouse
                )
                for movement in self.stock.movements
            },
            "ledgerBalances": self.ledger.balances(),
            "ledgerTotals": {
                "debits": self.ledger.totals()[0],
                "credits": self.ledger.totals()[1],
            },
            "railEntries": len(self.rail),
            "railHead": self.rail.head,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "summary": self.summary(),
            "documents": [document.to_dict() for document in self.documents.values()],
            "stock": self.stock.to_list(),
            "ledger": self.ledger.to_list(),
            "rail": self.rail.to_list(),
        }

    def findings(self) -> List[Finding]:
        """Every way the workspace contradicts itself (empty is OK)."""
        findings: List[Finding] = self.stock.verify() + self.ledger.verify() + self.rail.verify()
        for document in self.documents.values():
            declared = set(self.definitions.terminal_states(document.kind))
            if document.state not in declared and document.state not in self.definitions.workflow(
                document.kind
            ).state_names():
                findings.append(
                    Finding(
                        "invalid-value",
                        f"{document.id}: {document.state!r} is not a declared state of "
                        f"{document.kind!r}",
                        kind=document.kind,
                        ref=document.id,
                    )
                )
        return findings


# --- the flows --------------------------------------------------------------


def draft(
    space: Workspace,
    kind: str,
    document_id: str,
    body_fields: Mapping[str, Any],
    *,
    at: str,
    actor: str = ACTOR,
) -> Workspace:
    """Create a document in its initial state, validated by ERP-02.

    The state and docstatus are set from the *workflow*, never defaulted, and a
    scenario may not set them itself — a caller that could write `state` could
    move a document without a transition.
    """
    resolved = space.definitions.require(kind)
    if space.contains(document_id):
        raise Refused(
            "duplicate-id",
            f"{document_id!r} is already on the workspace as a "
            f"{space.get(document_id).kind!r} document",
        )
    clash = sorted(set(body_fields) & set(RESERVED_FIELDS))
    if clash:
        raise Refused(
            "invalid-value",
            f"{document_id}: the scenario may not set {', '.join(clash)}; the spine "
            "sets them from the workflow",
        )
    initial = space.definitions.initial_state(resolved)
    body: Dict[str, Any] = {
        "doctype": resolved,
        "id": document_id,
        "state": initial,
        "docstatus": space.definitions.docstatus_of(resolved, initial),
    }
    body.update({key: value for key, value in body_fields.items()})
    validated = space.definitions.validate(resolved, body)
    document = TxDocument.of(resolved, document_id, validated)
    rail = space.rail.append(
        at=at,
        actor=actor,
        action=ACTION_DRAFT,
        kind=resolved,
        ref=document_id,
        to_state=initial,
        note="drafted",
    )
    return space.with_document(document).with_rail(rail)


def _advance(
    space: Workspace,
    document_id: str,
    *,
    state_predicate,
    spine_action: str,
    at: str,
    actor: str,
    note: str,
) -> Workspace:
    """Move a document along the action its workflow declares for ``state_predicate``.

    The action is *not* passed in: it is derived from the workflow by asking for
    the transition that reaches a state satisfying the predicate, so no family's
    action name is written down anywhere in this package.
    """
    document = space.get(document_id)
    action = space.definitions.action_reaching(document.kind, document.state, state_predicate)
    target = space.definitions.advance(document.body, action)
    docstatus = space.definitions.docstatus_of(document.kind, target)
    moved = document.advanced(target, docstatus)
    rail = space.rail.append(
        at=at,
        actor=actor,
        action=spine_action,
        kind=document.kind,
        ref=document_id,
        from_state=document.state,
        to_state=target,
        note=note,
    )
    return space.with_document(moved).with_rail(rail)


def _stock_effects(space: Workspace, document: TxDocument, *, sign: int, at: str) -> Workspace:
    """Apply the stock movements ``document`` has, in the direction of ``sign``."""
    movements = movements_for(
        document.body,
        kind=document.kind,
        reference=document.id,
        items=space.items,
        warehouses=space.warehouses,
        at=at,
        sign=sign,
    )
    return space.with_stock(space.stock.apply(movements))


def submit(space: Workspace, document_id: str, *, at: str, actor: str = ACTOR) -> Workspace:
    """Submit a document: move it to its submitted state and apply its effects.

    Refusals, each naming the offender: ``already-submitted`` when the document is
    not in its initial state (criterion 2's "resubmitting the same document is
    refused"), ``cancelled-document`` when it is cancelled, and whatever ERP-02
    refuses when the move is not a declared transition.
    """
    document = space.get(document_id)
    kind = document.kind
    if space.cancelled(kind, document.state):
        raise Refused(
            "cancelled-document",
            f"{document_id}: a {kind} in state {document.state!r} cannot be submitted",
        )
    initial = space.definitions.initial_state(kind)
    if document.state != initial:
        raise Refused(
            "already-submitted",
            f"{document_id}: a {kind} is submitted once, and this one is already in "
            f"{document.state!r} (initial state is {initial!r})",
        )

    spine_action = ACTION_POST if kind == space.definitions.accounting_kind else ACTION_SUBMIT
    space = _advance(
        space,
        document_id,
        state_predicate=lambda state: state.docstatus == 1 and not state.terminal,
        spine_action=spine_action,
        at=at,
        actor=actor,
        note="submitted",
    )

    submitted = space.get(document_id)
    if submitted.kind in set(space.definitions.stock_kinds):
        space = _stock_effects(space, submitted, sign=-1, at=at)
    return space


def raise_document(
    space: Workspace,
    kind: str,
    document_id: str,
    against_id: str,
    body_fields: Mapping[str, Any],
    *,
    at: str,
    actor: str = ACTOR,
) -> Workspace:
    """Create ``kind`` raised against the document the schema links it to.

    The link *field* comes from the schema (``definitions.link_field``), the
    predecessor's kind must be the one the schema links to (``wrong-document``
    otherwise), it must be live (``not-submitted`` when it is still a draft or
    ``cancelled-document`` when it is cancelled), and the two must agree on
    currency (``currency-mismatch``).
    """
    resolved = space.definitions.require(kind)
    expected = space.definitions.raised_against(resolved)
    if expected is None:
        raise Refused(
            "definitions-invalid",
            f"{resolved}: the schema declares nothing to raise it against",
        )
    predecessor = space.get(against_id)
    if predecessor.kind != expected:
        raise Refused(
            "wrong-document",
            f"{document_id}: a {resolved} is raised against a {expected!r}, and "
            f"{against_id!r} is a {predecessor.kind!r}",
        )
    if space.cancelled(predecessor.kind, predecessor.state):
        raise Refused(
            "cancelled-document",
            f"{document_id}: {against_id!r} is cancelled, so nothing may be raised "
            "against it",
        )
    initial = space.definitions.initial_state(expected)
    if predecessor.state == initial:
        raise Refused(
            "not-submitted",
            f"{document_id}: {against_id!r} is still {predecessor.state!r}; it must "
            f"be submitted before a {resolved} is raised against it",
        )

    field_name = space.definitions.link_field(resolved)
    body = dict(body_fields)
    if field_name in body and body[field_name] != against_id:
        raise Refused(
            "invalid-value",
            f"{document_id}: the scenario sets {field_name!r} to "
            f"{body[field_name]!r}, which disagrees with the document it is raised "
            f"against ({against_id!r})",
        )
    body[field_name] = against_id

    currency = body.get("currency")
    if isinstance(currency, str) and predecessor.currency and currency != predecessor.currency:
        raise Refused(
            "currency-mismatch",
            f"{document_id}: the scenario declares {currency!r} while {against_id!r} "
            f"is in {predecessor.currency!r}",
        )
    return draft(space, resolved, document_id, body, at=at, actor=actor)


def _remaining(
    space: Workspace, source_kind: str, source_id: str, consumer_kind: str, field_name: str
) -> Dict[str, float]:
    """Per-item quantities still free between a document and its consumers.

    The arithmetic is a measurement over the *live* documents on the workspace, so
    a cancellation returns the quantity to the pool without any counter being
    reset — which is what makes "cancel then re-deliver" a legal sequence rather
    than a bookkeeping special case.
    """
    source = space.get(source_id)
    remaining = _quantities(source.body.get("lines"))
    for consumer in space.live_of_kind(consumer_kind):
        if consumer.link(field_name) != source_id:
            continue
        for item_code, qty in _quantities(consumer.body.get("lines")).items():
            remaining[item_code] = quantity(remaining.get(item_code, 0.0) - qty)
    return {item_code: qty for item_code, qty in remaining.items()}


def deliver(
    space: Workspace,
    document_id: str,
    order_id: str,
    body_fields: Mapping[str, Any],
    *,
    at: str,
    actor: str = ACTOR,
) -> Workspace:
    """Fulfil an order: raise the cycle's stock-committing document and submit it.

    Which family that is comes from the definition set, and the quantity check
    compares the delivery's lines against what the order still has outstanding, so
    an over-delivery is ``over-delivery`` naming the item and both figures.
    """
    kind = space.definitions.stock_kind()
    order = space.get(order_id)
    requested = _quantities(body_fields.get("lines"))
    remaining = _remaining(space, order.kind, order_id, kind, space.definitions.link_field(kind))
    over = {
        item_code: (qty, remaining.get(item_code, 0.0))
        for item_code, qty in requested.items()
        if qty > remaining.get(item_code, 0.0) + 1e-9
    }
    if over:
        item_code = sorted(over)[0]
        delivered, free = over[item_code]
        raise Refused(
            "over-delivery",
            f"{document_id}: {item_code!r} delivers {delivered} against "
            f"{order_id!r}, which has {free} outstanding",
        )
    space = raise_document(space, kind, document_id, order_id, body_fields, at=at, actor=actor)
    return submit(space, document_id, at=at, actor=actor)


def invoice(
    space: Workspace,
    document_id: str,
    delivery_id: str,
    body_fields: Mapping[str, Any],
    *,
    at: str,
    actor: str = ACTOR,
) -> Workspace:
    """Bill a delivery: raise the cycle's last family, submit it and post the ledger.

    Submitting the invoice is what commits the general ledger (the accounting
    effect is applied by :func:`submit`), and the quantity check is the mirror of
    the delivery's: an ``over-invoice`` names the item and both figures.
    """
    kind = space.definitions.chain[-1]
    delivery = space.get(delivery_id)
    requested = _quantities(body_fields.get("lines"))
    remaining = _remaining(
        space, delivery.kind, delivery_id, kind, space.definitions.link_field(kind)
    )
    over = {
        item_code: (qty, remaining.get(item_code, 0.0))
        for item_code, qty in requested.items()
        if qty > remaining.get(item_code, 0.0) + 1e-9
    }
    if over:
        item_code = sorted(over)[0]
        billed, free = over[item_code]
        raise Refused(
            "over-invoice",
            f"{document_id}: {item_code!r} is invoiced for {billed} against "
            f"{delivery_id!r}, which has {free} unbilled",
        )
    space = raise_document(space, kind, document_id, delivery_id, body_fields, at=at, actor=actor)
    space = submit(space, document_id, at=at, actor=actor)
    return _post_ledger(space, space.get(document_id), at=at, actor=actor)


def _post_ledger(space: Workspace, invoice_document: TxDocument, *, at: str, actor: str) -> Workspace:
    """Derive the ledger entry set from an invoice and post it as a document.

    The posting is a document of the derived accounting family: its lines are the
    derived rows, its voucher is the invoice, and ERP-02 validates it — including
    its own double-entry rule, so a posting this package believed balanced still
    has to survive the model's check.
    """
    kind = space.definitions.accounting_kind
    entries = posting_entries(invoice_document, space.policy, at=at)
    posting_id = f"{invoice_document.id}:GL"
    if space.contains(posting_id):
        raise Refused(
            "duplicate-id",
            f"{posting_id!r} is already on the workspace, so {invoice_document.id!r} "
            "would be posted twice",
        )
    initial = space.definitions.initial_state(kind)
    body: Dict[str, Any] = {
        "doctype": kind,
        "id": posting_id,
        "state": initial,
        "docstatus": space.definitions.docstatus_of(kind, initial),
        "company": invoice_document.company,
        "currency": invoice_document.currency,
        "posting_date": str(invoice_document.body.get("posting_date", "")),
        VOUCHER_TYPE_FIELD: invoice_document.kind,
        VOUCHER_ID_FIELD: invoice_document.id,
        "lines": posting_lines(entries),
    }
    if body[VOUCHER_TYPE_FIELD] not in space.definitions.voucher_sources:
        raise Refused(
            "wrong-document",
            f"{kind}: {invoice_document.kind!r} is not an accepted "
            f"{VOUCHER_TYPE_FIELD} ({', '.join(space.definitions.voucher_sources)})",
        )
    validated = space.definitions.validate(kind, body)
    posting = TxDocument.of(kind, posting_id, validated)
    rail = space.rail.append(
        at=at,
        actor=actor,
        action=ACTION_DRAFT,
        kind=kind,
        ref=posting_id,
        to_state=initial,
        note=f"derived from {invoice_document.id}",
    )
    space = space.with_document(posting).with_rail(rail)
    space = space.with_ledger(space.ledger.apply(entries))
    return submit(space, posting_id, at=at, actor=actor)


def cancel(space: Workspace, document_id: str, *, at: str, actor: str = ACTOR) -> Workspace:
    """Cancel a document, reversing exactly what it applied.

    Order matters and is enforced: a document with a live dependant is
    ``live-dependant`` naming the dependant, so a delivery cannot be cancelled out
    from under an invoice. Cancelling posts the exact inverse of the document's
    stock movements and ledger entries, cancels any posting derived from it, then
    cancels the document itself — and the reversal is *proved* rather than
    asserted: a net that is not zero afterwards is ``reversal-mismatch``.
    """
    document = space.get(document_id)
    kind = document.kind
    if space.cancelled(kind, document.state):
        raise Refused(
            "already-reversed",
            f"{document_id}: a {kind} in state {document.state!r} is already cancelled",
        )
    dependants = space.dependants(document_id)
    if dependants:
        names = ", ".join(sorted(dependant.id for dependant in dependants))
        raise Refused(
            "live-dependant",
            f"{document_id}: {names} still depends on it, so it cannot be cancelled "
            "while that document is live",
        )

    if document.kind in set(space.definitions.stock_kinds):
        movements = space.stock.for_document(document_id)
        space = space.with_stock(space.stock.apply(invert_stock(movements, at=at)))
        space = _record_reversal(space, document, ACTION_CANCEL, at, actor, "stock reversed")

    # The ledger rows a document wrote are keyed by the *voucher* — the document
    # they are the ledger image of — so they are reversed by the cancelled
    # document's own id, not by the id of the posting derived from it. The posting
    # is then cancelled too, so the ledger shows a reversed posting rather than a
    # posting whose source has quietly gone away.
    entries = space.ledger.for_document(document_id)
    if entries:
        space = space.with_ledger(space.ledger.apply(invert_ledger(entries, at=at)))
        space = _record_reversal(space, document, ACTION_REVERSE, at, actor, "ledger reversed")
        for posting in space.postings_for(document_id):
            space = _cancel_only(space, posting.id, at=at, actor=actor)

    space = _cancel_only(space, document_id, at=at, actor=actor)
    return space


def _record_reversal(
    space: Workspace, document: TxDocument, action: str, at: str, actor: str, note: str
) -> Workspace:
    rail = space.rail.append(
        at=at,
        actor=actor,
        action=action,
        kind=document.kind,
        ref=document.id,
        from_state=document.state,
        to_state=document.state,
        note=note,
    )
    return space.with_rail(rail)


def _cancel_only(space: Workspace, document_id: str, *, at: str, actor: str) -> Workspace:
    """Move one document to its cancellation state through its own workflow."""
    return _advance(
        space,
        document_id,
        state_predicate=lambda state: state.docstatus == 2,
        spine_action=ACTION_CANCEL,
        at=at,
        actor=actor,
        note="cancelled",
    )


def complete(space: Workspace, document_id: str, *, at: str, actor: str = ACTOR) -> Workspace:
    """Settle a submitted document, through the action its workflow declares.

    Completion is the *forward* terminal of a lifecycle (an accepted quotation, a
    paid invoice), as opposed to cancellation which is the ``docstatus`` 2 side —
    the distinction is derived from the state's docstatus, never from its label.
    """
    return _advance(
        space,
        document_id,
        state_predicate=lambda state: state.terminal and state.docstatus == 1,
        spine_action=ACTION_COMPLETE,
        at=at,
        actor=actor,
        note="completed",
    )


# --- the scenario -----------------------------------------------------------


@dataclass(frozen=True)
class Scenario:
    """The data a cycle is run with: ids, bodies, masters and the posting policy.

    Everything here is *data*: the ids (supplied, never derived — this is what
    "keyless" means), the per-position document bodies, the item master, the
    warehouse set and the chart of accounts. The scenario names no document kind
    and no field the schemas own beyond the families' own fields, because the
    cycle's kinds and link fields are resolved from the definition set.
    """

    name: str
    company: str
    currency: str
    customer: str
    item: Mapping[str, Any]
    warehouses: Tuple[str, ...]
    accounts: Mapping[str, str]
    ids: Tuple[str, ...]
    bodies: Tuple[Mapping[str, Any], ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "company": self.company,
            "currency": self.currency,
            "customer": self.customer,
            "item": dict(self.item),
            "warehouses": list(self.warehouses),
            "accounts": dict(sorted(self.accounts.items())),
            "ids": list(self.ids),
        }


#: The one item the golden scenario trades. Deliberately a *stock* item with a
#: valuation rate below its selling rate, so the cycle moves stock and the
#: ledger, and a reviewer can check both effects arithmetically.
GOLDEN_ITEM = {
    "doctype": "item",
    "id": "ITEM-GOLDEN",
    "name": "Golden Widget",
    "uom": "Nos",
    "item_group": "Products",
    "is_stock_item": True,
    "standard_rate": 20.0,
    "valuation_rate": 12.0,
}

#: The warehouses the scenario declares. ERP-02 ships no warehouse family, so the
#: set is scenario data; a movement that names a warehouse outside it is refused
#: rather than invented.
GOLDEN_WAREHOUSES = ("WH-MAIN",)

#: The chart of accounts, as scenario data. The spine declares the *roles* and
#: never the account values (see :data:`~.model.POSTING_ROLES`).
GOLDEN_ACCOUNTS = {"receivable": "AR-1100", "income": "REV-4000"}


def golden_scenario() -> Scenario:
    """The scenario the golden path runs: five of one item at twenty, taxed five.

    The figures are chosen so the whole cycle is checkable by hand: net 100.00,
    tax 5.00, gross 105.00, and a stock valuation of 60.00 leaving the warehouse.
    """
    common = {
        "company": "AO-DEMO",
        "currency": "USD",
        "transaction_date": "2026-03-02",
        "party": "CUST-1",
    }
    lines = [{"item_code": GOLDEN_ITEM["id"], "qty": 5, "rate": 20.0}]
    bodies = (
        {**common, "lines": lines, "net_total": 100.0, "total": 100.0},
        {
            **common,
            "delivery_date": "2026-03-05",
            "lines": lines,
            "net_total": 100.0,
            "total": 100.0,
        },
        {**common, "warehouse": GOLDEN_WAREHOUSES[0], "lines": lines, "total": 100.0},
        {
            **common,
            "posting_date": "2026-03-06",
            "lines": lines,
            "taxes": [{"account": "TAX-PAYABLE", "amount": 5.0}],
            "net_total": 100.0,
            "total": 105.0,
        },
    )
    return Scenario(
        name="golden",
        company="AO-DEMO",
        currency="USD",
        customer="CUST-1",
        item=dict(GOLDEN_ITEM),
        warehouses=GOLDEN_WAREHOUSES,
        accounts=dict(GOLDEN_ACCOUNTS),
        ids=("QUO-1001", "SO-1001", "DN-1001", "INV-1001"),
        bodies=bodies,
    )


def workspace(definitions: Optional[DefinitionSet], scenario: Scenario) -> Workspace:
    """Open a workspace for a scenario, with every master validated by ERP-02."""
    defs = definitions if definitions is not None else load_definitions()
    master = defs.validate_master(scenario.item["doctype"], scenario.item)
    return Workspace(
        definitions=defs,
        items={master["id"]: master},
        warehouses=scenario.warehouses,
        policy=PostingPolicy(accounts=scenario.accounts),
    )


# --- the golden path --------------------------------------------------------


@dataclass(frozen=True)
class GoldenPath:
    """The result of running a scenario: the workspace, its invariants and steps."""

    name: str
    workspace: Workspace
    findings: Tuple[Finding, ...] = ()
    steps: Tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.findings

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "steps": list(self.steps),
            "findings": [finding.to_dict() for finding in self.findings],
            **self.workspace.to_dict(),
        }


def _run_cycle(space: Workspace, scenario: Scenario, *, complete_first: bool) -> Tuple[Workspace, List[str]]:
    """Draft and submit every document in the resolved cycle, in order.

    The walk is positional: document *i* is raised against document *i-1* through
    the link field its own schema declares, so the flow never names a family.
    """
    chain = space.definitions.chain
    ids = scenario.ids
    if len(ids) != len(chain):
        raise Refused(
            "invalid-value",
            f"the scenario supplies {len(ids)} id(s) for a cycle of "
            f"{len(chain)} families ({' -> '.join(chain)})",
        )
    if len(scenario.bodies) != len(chain):
        raise Refused(
            "invalid-value",
            f"the scenario supplies {len(scenario.bodies)} body(ies) for a cycle of "
            f"{len(chain)} families",
        )

    steps: List[str] = []
    space = draft(space, chain[0], ids[0], scenario.bodies[0], at=TIMELINE["quoted"])
    steps.append(f"draft {chain[0]} {ids[0]}")
    space = submit(space, ids[0], at=TIMELINE["submitted"])
    steps.append(f"submit {chain[0]} {ids[0]}")

    if complete_first:
        space = complete(space, ids[0], at=TIMELINE["accepted"])
        steps.append(f"complete {chain[0]} {ids[0]}")

    for index in range(1, len(chain)):
        kind = chain[index]
        previous = ids[index - 1]
        document_id = ids[index]
        if kind == space.definitions.stock_kind():
            space = deliver(space, document_id, previous, scenario.bodies[index], at=TIMELINE["delivered"])
            steps.append(f"deliver {kind} {document_id} against {previous}")
        elif kind == chain[-1]:
            space = invoice(space, document_id, previous, scenario.bodies[index], at=TIMELINE["invoiced"])
            steps.append(f"invoice {kind} {document_id} against {previous}")
        else:
            space = raise_document(
                space, kind, document_id, previous, scenario.bodies[index], at=TIMELINE["submitted"]
            )
            steps.append(f"raise {kind} {document_id} against {previous}")
            space = submit(space, document_id, at=TIMELINE["submitted"])
            steps.append(f"submit {kind} {document_id}")
    return space, steps


def _cycle_findings(space: Workspace) -> List[Finding]:
    """The invariants a complete cycle must satisfy, checked on the workspace."""
    findings: List[Finding] = list(space.findings())
    definitions = space.definitions

    for kind in definitions.chain:
        documents = space.of_kind(kind)
        if not documents:
            findings.append(
                Finding("missing-field", f"the cycle has no {kind} document", kind=kind)
            )
            continue
        for document in documents:
            if definitions.initial_state(kind) == document.state:
                findings.append(
                    Finding(
                        "not-submitted",
                        f"{document.id}: the cycle left a {kind} in its initial state",
                        kind=kind,
                        ref=document.id,
                    )
                )
            if not space.rail.for_ref(document.id):
                findings.append(
                    Finding(
                        "audit-broken",
                        f"{document.id}: the cycle recorded no audit step for it",
                        kind=kind,
                        ref=document.id,
                    )
                )

    ledger = space.ledger
    if not ledger.entries:
        findings.append(Finding("unbalanced-posting", "the cycle posted no ledger entry"))
    debits, credits = ledger.totals()
    if debits != credits:
        findings.append(
            Finding(
                "unbalanced-posting",
                f"the ledger posts {debits:.2f} of debits against {credits:.2f} of credits",
            )
        )
    return findings


def golden_path(name: str = "golden", definitions: Optional[DefinitionSet] = None) -> GoldenPath:
    """Run the happy cycle and report whether its invariants hold.

    Offline, deterministic and keyless: the same call twice produces the same rail
    head and the same balances, which is what the lane's own tri-state check
    verifies by running it twice.
    """
    scenario = golden_scenario()
    space = workspace(definitions, scenario)
    space, steps = _run_cycle(space, scenario, complete_first=True)
    return GoldenPath(
        name=name,
        workspace=space,
        findings=tuple(_cycle_findings(space)),
        steps=tuple(steps),
    )


def cancellation_path(name: str = "cancel", definitions: Optional[DefinitionSet] = None) -> GoldenPath:
    """Run the same cycle, then cancel it and prove the reversal was exact.

    The cancellation order is forced by the flow itself: a document with a live
    dependant is refused, so the invoice is cancelled before the delivery it was
    billed against, and the delivery before the order.

    **The invariants here are the measurement of acceptance criterion 2, and they
    are deliberately not "the net is zero".** Under a total inversion the net of a
    cancelled document is zero *by construction*, so asserting it would be a check
    that cannot fail. What is measured instead is that both ledgers returned to
    the state they were in *before the cycle* — every balance back to zero, on the
    very quantities and accounts the reversal had to name — while still *carrying*
    the entries: a reversal that rolled the ledgers back instead of reversing them
    would pass a balance check and fail this one.
    """
    scenario = golden_scenario()
    space = workspace(definitions, scenario)
    before = space.summary()
    space, steps = _run_cycle(space, scenario, complete_first=False)
    steps = list(steps)

    for position in range(len(space.definitions.chain) - 1, -1, -1):
        document_id = scenario.ids[position]
        if space.dependants(document_id):
            continue
        document = space.get(document_id)
        space = cancel(space, document_id, at=TIMELINE["cancelled"])
        steps.append(f"cancel {document.kind} {document_id}")

    after = space.summary()
    findings: List[Finding] = list(space.findings())

    for document_id in scenario.ids:
        document = space.get(document_id)
        if not space.cancelled(document.kind, document.state):
            findings.append(
                Finding(
                    "already-reversed",
                    f"{document_id}: the cancellation pass left it in {document.state!r}",
                    kind=document.kind,
                    ref=document_id,
                )
            )

    if len(space.stock) <= len(before["stock"]) and len(space.ledger) == 0:
        findings.append(
            Finding(
                "audit-broken",
                "the cancellation pass left no trace: the ledgers carry no entries, "
                "so they were rolled back rather than reversed",
            )
        )

    for key, balance in after["stock"].items():
        if balance != 0:
            findings.append(
                Finding(
                    "audit-broken",
                    f"{key}: the stock balance is {balance} after cancellation, so "
                    "the reversal did not undo the movement it recorded",
                )
            )
    for account, balance in after["ledgerBalances"].items():
        if balance != 0:
            findings.append(
                Finding(
                    "audit-broken",
                    f"{account}: the ledger balance is {balance:.2f} after "
                    "cancellation, so the reversal did not undo the posting",
                )
            )
    if not after["stock"]:
        findings.append(
            Finding("missing-field", "the cancellation pass moved no stock to reverse")
        )
    if not after["ledgerBalances"]:
        findings.append(
            Finding("missing-field", "the cancellation pass posted nothing to reverse")
        )
    return GoldenPath(name=name, workspace=space, findings=tuple(findings), steps=tuple(steps))
