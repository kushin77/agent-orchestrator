"""The spine's definitions, resolved — never restated (ERP-03, issue #648).

Acceptance criterion 3 of #648 is "no duplicated constants — every definition
resolves through the indexer query surface or ERP-02 schemas". This module *is*
that resolution: it holds no kind, no state, no transition, no ``docstatus`` and
no document link of its own, and every one of them is either read from the
indexer (:mod:`.indexer`) or derived from the ERP-02 model
(``integrations.erp.core``).

Three derivations are worth naming, because each replaces a constant a lane
would otherwise have written down:

1. **The document set is a query.** :func:`~.indexer.catalogue_documents`
   returns the catalogue declarations whose ``owning_issue`` is this lane's, so
   the spine learns which documents it owns from the index rather than from a
   list here.
2. **The cycle is a graph walk over ERP-02's own link fields.** A family is
   "raised against" another when its schema declares a property that *names*
   that family — ``sales-order.quotation``, ``delivery-note.against_sales_order``,
   ``sales-invoice.against_delivery_note``. :func:`resolve_chain` finds those
   links, groups the kinds into components, and returns the one linear component
   that contains a document this lane owns. The chain
   ``quotation -> sales-order -> delivery-note -> sales-invoice`` is therefore a
   *measurement of the ERP-02 schemas*, not a list written here; re-point a link
   upstream and this lane's cycle follows.
3. **The accounting and stock effects are field detections.** The family that
   commits the general ledger is the lifecycle kind whose schema declares a
   ``voucher_type`` field (``gl-posting``), and the families that commit stock
   are those that declare a warehouse-bearing top-level field (``delivery-note``,
   ``stock-entry``). The spine's "stock movement applied on delivery" and "GL
   postings on invoice" are consequently driven by the schemas too.

**What is *not* derivable, and is therefore declared here once:** the spine's
posting *roles* (:data:`~.model.POSTING_ROLES`). No upstream surface says which
account a receivable side belongs to; that is this lane's own derivation
contract, and the account *values* are supplied by the caller
(``ledger.PostingPolicy``), so no chart of accounts is baked in.

Anything the derivation cannot decide is refused by name rather than guessed: an
ambiguous link, a cycle that is not linear, or two families claiming the ledger
are each ``definitions-invalid``, naming what disagreed.

---knowledge---
module_id: integrations.erp.tx.definitions
system: integrations
app: erp
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [as_spine_refusal, Link, schema_properties, resolve_links, resolve_chain, resolve_accounting_kind, resolve_voucher_sources, DefinitionSet, (+1 more)]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from integrations.erp.core.errors import ErpError
from integrations.erp.core.validators import DocumentModel, load_model
from integrations.erp.core.workflow import DOCSTATUS_CANCELLED, DOCSTATUS_SUBMITTED

from . import indexer
from .model import Refused
from .indexer import LaneDocument

#: This lane's identity: the issue whose declarations the spine delivers. It is
#: the lane's own fact (the operator's order), not an ERP definition, so it is
#: the one identifier this module states rather than resolves.
LANE_ISSUE = 648

#: The ``x-erp-`` keys and schema fields the derivations read. Named once so the
#: detection rules can be read without hunting for string literals.
DOCUMENT_KEY = "x-erp-document"
LIFECYCLE_KEY = "x-erp-lifecycle"
WAREHOUSE_FIELD = "warehouse"
WAREHOUSE_SUFFIX = "-warehouse"
VOUCHER_TYPE_FIELD = "voucher_type"
VOUCHER_ID_FIELD = "voucher_id"
AGAINST_PREFIX = "against-"

#: The ERP-02 refusal codes this lane's vocabulary maps onto. Every code the core
#: model can raise appears here, so an unmapped refusal cannot leak a code the
#: spine's own vocabulary does not close over — it lands on ``schema-violation``
#: instead, which is a refusal this lane can name.
ERP_CODE_MAP: Mapping[str, str] = {
    "invalid_body": "invalid-value",
    "invalid_transfer": "invalid-value",
    "missing_provenance": "schema-violation",
    "schema_violation": "schema-violation",
    "state_jumped": "illegal-transition",
    "unbalanced_posting": "unbalanced-posting",
    "unknown_action": "unknown-action",
    "unknown_document_kind": "unknown-document-kind",
    "unknown_state": "invalid-value",
    "unknown_workflow": "unknown-document-kind",
    "workflow_invalid": "definitions-invalid",
    "yaml_unavailable": "definitions-invalid",
}

#: The refusal an ERP-02 code that this lane has not mapped lands on. It is a
#: refusal rather than a passthrough so the spine's vocabulary stays closed.
ERP_FALLBACK_CODE = "schema-violation"


def as_spine_refusal(exc: ErpError) -> Refused:
    """Translate one ERP-02 refusal into this lane's closed vocabulary."""
    code = ERP_CODE_MAP.get(exc.code, ERP_FALLBACK_CODE)
    return Refused(code, f"{exc.code}: {exc.message}")


def _normalise(name: str) -> str:
    return name.strip().lower().replace("_", "-")


@dataclass(frozen=True)
class Link:
    """One "raised against" link read off a schema.

    ``kind``'s ``field`` names another family, so ``kind`` is raised against
    ``target``. It is recorded with the field that produced it, because a link a
    reviewer cannot see in the schema is a link nobody can check.
    """

    kind: str
    field: str
    target: str

    def to_dict(self) -> Dict[str, str]:
        return {"kind": self.kind, "field": self.field, "target": self.target}


def schema_properties(model: DocumentModel, kind: str) -> Dict[str, Any]:
    """The declared top-level properties of ``kind``'s schema (a mapping)."""
    schema = model.schema_for(kind)
    properties = schema.get("properties")
    return dict(properties) if isinstance(properties, Mapping) else {}


def _link_of(model: DocumentModel, kind: str, known: Sequence[str]) -> Optional[Link]:
    """The single "raised against" link of ``kind``, or None when it has none.

    Two links is a refusal rather than a tie-break: a family raised against two
    others sits in two cycles at once, and picking one would make this lane's
    cycle a guess.
    """
    candidates: List[Link] = []
    for name in sorted(schema_properties(model, kind)):
        normalised = _normalise(name)
        target = (
            normalised[len(AGAINST_PREFIX):]
            if normalised.startswith(AGAINST_PREFIX)
            else normalised
        )
        if target in known and target != kind:
            candidates.append(Link(kind=kind, field=name, target=target))
    if len(candidates) > 1:
        named = ", ".join(f"{link.field} -> {link.target}" for link in candidates)
        raise Refused(
            "definitions-invalid",
            f"{kind}: the schema declares {len(candidates)} links to other "
            f"families ({named}), so the cycle through it is undecidable",
        )
    return candidates[0] if candidates else None


def resolve_links(model: DocumentModel) -> Tuple[Link, ...]:
    """Every "raised against" link across the ERP-02 lifecycle kinds, sorted."""
    known = tuple(sorted(model.lifecycle_kinds()))
    links = [_link_of(model, kind, known) for kind in known]
    return tuple(link for link in links if link is not None)


def _components(links: Sequence[Link]) -> Dict[str, str]:
    """Union-find over the link graph: each kind maps to its component root."""
    parent: Dict[str, str] = {}

    def find(node: str) -> str:
        parent.setdefault(node, node)
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    for link in links:
        union(link.kind, link.target)
    return {node: find(node) for node in sorted(parent)}


def resolve_chain(
    model: DocumentModel, owned: Iterable[str]
) -> Tuple[Tuple[str, ...], Tuple[Link, ...]]:
    """The linear document cycle this lane drives, derived from ERP-02.

    ``owned`` is the set of document kinds the indexer catalogue assigns to this
    lane. The cycle is the linear component of the link graph that contains at
    least one of them and at least two families — a single-family component is a
    standalone document, not a cycle. A component that is not linear (a kind with
    two predecessors or two successors) is ``definitions-invalid``, because a
    cycle that branches is not a cycle.
    """
    links = resolve_links(model)
    roots = _components(links)
    owned_set = set(owned)

    component_of: Dict[str, List[str]] = {}
    for kind in sorted(roots):
        component_of.setdefault(roots[kind], []).append(kind)

    candidates: List[Tuple[str, ...]] = []
    for root in sorted(component_of):
        members = component_of[root]
        if len(members) < 2:
            continue
        if not owned_set & set(members):
            continue
        candidates.append(tuple(members))

    if len(candidates) != 1:
        named = "; ".join("{" + ", ".join(component) + "}" for component in candidates)
        raise Refused(
            "definitions-invalid",
            f"the link graph contains {len(candidates)} multi-family component(s) "
            f"touching this lane's documents ({named or 'none'}), so the cycle is "
            "undecidable",
        )

    members = sorted(candidates[0])
    successors: Dict[str, List[str]] = {kind: [] for kind in members}
    predecessors: Dict[str, List[str]] = {kind: [] for kind in members}
    for link in links:
        if link.kind in successors and link.target in successors:
            successors[link.target].append(link.kind)
            predecessors[link.kind].append(link.target)

    branching = {
        kind: len(targets)
        for kind, targets in successors.items()
        if len(targets) > 1
    }
    forking = {
        kind: len(sources)
        for kind, sources in predecessors.items()
        if len(sources) > 1
    }
    if branching or forking:
        raise Refused(
            "definitions-invalid",
            f"the component {{{', '.join(members)}}} is not a linear cycle: "
            f"branches {branching or '{}'}, forks {forking or '{}'}",
        )

    if len(members) < 2:
        raise Refused("definitions-invalid", "a cycle needs at least two families")

    head = next(kind for kind in members if not predecessors[kind])
    chain: List[str] = [head]
    while successors[chain[-1]]:
        chain.append(successors[chain[-1]][0])
    if len(chain) != len(members):
        raise Refused(
            "definitions-invalid",
            f"the component {{{', '.join(members)}}} does not form one walk "
            f"({', '.join(chain)})",
        )
    return tuple(chain), links


def _declares_warehouse(model: DocumentModel, kind: str) -> bool:
    """Whether ``kind`` declares a warehouse-bearing top-level field."""
    for name in schema_properties(model, kind):
        normalised = _normalise(name)
        if normalised == WAREHOUSE_FIELD or normalised.endswith(WAREHOUSE_SUFFIX):
            return True
    return False


def resolve_accounting_kind(model: DocumentModel) -> str:
    """The one lifecycle family that commits the ledger, by its ``voucher_type``.

    Exactly one family may declare the voucher back-reference: two ledgers in one
    model means a posting could be derived from two places, and the spine would
    have to choose. The refusal names both.
    """
    declaring = tuple(
        kind
        for kind in model.lifecycle_kinds()
        if VOUCHER_TYPE_FIELD in schema_properties(model, kind)
    )
    if len(declaring) != 1:
        raise Refused(
            "definitions-invalid",
            f"{len(declaring)} lifecycle family(ies) declare a "
            f"{VOUCHER_TYPE_FIELD!r} field ({', '.join(declaring) or 'none'}); "
            "exactly one must commit the ledger",
        )
    return declaring[0]


def resolve_voucher_sources(model: DocumentModel, kind: str) -> Tuple[str, ...]:
    """The families the ledger document accepts as the source of a posting."""
    node = schema_properties(model, kind).get(VOUCHER_TYPE_FIELD)
    if not isinstance(node, Mapping):
        raise Refused(
            "definitions-invalid",
            f"{kind}: {VOUCHER_TYPE_FIELD!r} must be an object declaring its enum",
        )
    enum = node.get("enum")
    if not isinstance(enum, list) or not enum:
        raise Refused(
            "definitions-invalid",
            f"{kind}: {VOUCHER_TYPE_FIELD!r} declares no enum, so the families a "
            "posting may be derived from are undecidable",
        )
    return tuple(str(entry) for entry in enum)


@dataclass(frozen=True)
class DefinitionSet:
    """Everything the spine knows, and every way it is resolved.

    The value types here are the ERP-02 model (schemas + workflows) and the
    indexer's declarations. Nothing on this type is a copy of either: the chain,
    the stock families and the ledger family are *derived* at construction, and
    each derivation refuses rather than defaults when the ERP-02 assets disagree
    with themselves.
    """

    issue: int
    lane_documents: Tuple[LaneDocument, ...]
    model: DocumentModel
    links: Tuple[Link, ...]
    chain: Tuple[str, ...]
    stock_kinds: Tuple[str, ...]
    accounting_kind: str
    voucher_sources: Tuple[str, ...]

    # --- what the indexer declared ----------------------------------------

    def lane_ids(self) -> Tuple[str, ...]:
        return tuple(document.id for document in self.lane_documents)

    def families(self) -> Tuple[str, ...]:
        return indexer.catalogue_families(self.lane_documents)

    def drivable(self) -> Tuple[str, ...]:
        """The lane's declared documents ERP-02 can actually drive."""
        return tuple(
            document.id
            for document in self.lane_documents
            if self.has_lifecycle(document.id)
        )

    def undeclared_by_core(self) -> Tuple[LaneDocument, ...]:
        """The lane's declared documents ERP-02 ships no lifecycle for.

        Reported rather than repaired: inventing a schema or a workflow for one
        would mean writing into ERP-02's lane (``integrations/erp/core/**``), and
        a document this lane cannot drive is a fact about the board, not a defect
        the spine may paper over.
        """
        return tuple(
            document
            for document in self.lane_documents
            if not self.has_lifecycle(document.id)
        )

    def undriven(self) -> Tuple[Tuple[LaneDocument, str], ...]:
        """The lane's declarations the spine does not drive, each with its reason.

        *Why this is reported rather than made true.* An undriven declaration is
        a boundary this lane publishes, not a defect it hides: the spine drives
        *the selling cycle the schemas describe* (see :attr:`chain`), and a
        document that stands outside that cycle will be driven by the lane that
        owns its own flow. Driving one anyway would mean inventing a direction of
        effect the model does not declare, which is exactly the kind of guess
        acceptance criterion 3 forbids.
        """
        undriven: List[Tuple[LaneDocument, str]] = []
        for document in self.lane_documents:
            if document.id in set(self.addressable()):
                continue
            if not self.has_lifecycle(document.id):
                reason = (
                    "ERP-02 ships no lifecycle for it, so there is no declared way "
                    "to move it"
                )
            else:
                reason = (
                    "it stands outside the derived selling cycle, so the spine has "
                    "no declared direction of effect for it"
                )
            undriven.append((document, reason))
        return tuple(undriven)

    # --- the ERP-02 model --------------------------------------------------

    def has_lifecycle(self, kind: str) -> bool:
        return kind in set(self.model.lifecycle_kinds())

    def link_of(self, kind: str) -> Optional[Link]:
        """The link that raises ``kind`` against another family, when it has one."""
        for link in self.links:
            if link.kind == kind:
                return link
        return None

    def raised_against(self, kind: str) -> Optional[str]:
        link = self.link_of(kind)
        return link.target if link is not None else None

    def link_field(self, kind: str) -> str:
        """The field name ``kind`` carries its link in, or a refusal.

        The spine builds a cycle document by *naming the field the schema
        declares*, so no field name from another lane's schema is written down
        here.
        """
        link = self.link_of(kind)
        if link is None:
            raise Refused(
                "definitions-invalid",
                f"{kind}: the schema declares no link to another family, so the "
                "spine cannot raise it against its predecessor",
            )
        return link.field

    def addressable(self) -> Tuple[str, ...]:
        """Every kind the spine may address: the cycle, plus the ledger it derives."""
        return tuple(sorted(set(self.chain) | {self.accounting_kind}))

    def require(self, kind: Any) -> str:
        """The kind, or a refusal naming why the spine will not drive it."""
        if not isinstance(kind, str) or not kind.strip():
            raise Refused("invalid-value", "a document kind must be a non-empty string")
        if kind in self.lane_ids() and not self.has_lifecycle(kind):
            raise Refused(
                "not-drivable",
                f"{kind}: the indexer declares it for this lane but ERP-02 ships "
                "no lifecycle for it, so the spine cannot drive it",
            )
        if not self.has_lifecycle(kind):
            raise Refused(
                "unknown-document-kind",
                f"{kind}: ERP-02 declares no lifecycle for it "
                f"(known: {', '.join(self.model.lifecycle_kinds())})",
            )
        if kind not in self.addressable():
            raise Refused(
                "not-drivable",
                f"{kind}: the spine drives the derived cycle "
                f"({' -> '.join(self.chain)}) and the ledger it derives "
                f"({self.accounting_kind}), and this family is outside it",
            )
        return kind

    def workflow(self, kind: str):
        return self.model.workflow_for(self.require(kind))

    def initial_state(self, kind: str) -> str:
        return self.workflow(kind).initial

    def docstatus_of(self, kind: str, state: str) -> int:
        return self.workflow(kind).state(state).docstatus

    def actions_from(self, kind: str, state: str) -> Tuple[str, ...]:
        return self.workflow(kind).actions_from(state)

    def legal_targets(self, kind: str, state: str) -> Tuple[str, ...]:
        return self.workflow(kind).legal_targets(state)

    def terminal_states(self, kind: str) -> Tuple[str, ...]:
        return self.workflow(kind).terminal_states()

    def cancellation_state(self, kind: str) -> str:
        """The state ``kind``'s workflow declares for a cancelled document.

        Read from the workflow's own ``docstatus`` values rather than from the
        name ``cancelled``, so a family that labels the state differently is still
        recognised — and a family with no cancellation state at all is refused
        here rather than silently treated as uncancellable.
        """
        for state in self.workflow(kind).states:
            if state.docstatus == DOCSTATUS_CANCELLED:
                return state.name
        raise Refused(
            "definitions-invalid",
            f"{kind}: the workflow declares no state with docstatus "
            f"{DOCSTATUS_CANCELLED}, so the family cannot be cancelled",
        )

    def submitted_state(self, kind: str) -> str:
        """The state a submit moves ``kind`` to: docstatus 1, and not terminal."""
        initial = self.initial_state(kind)
        workflow = self.workflow(kind)
        for transition in workflow.transitions:
            if transition.from_state != initial:
                continue
            target = workflow.state(transition.to)
            if target.docstatus == DOCSTATUS_SUBMITTED and not target.terminal:
                return target.name
        raise Refused(
            "definitions-invalid",
            f"{kind}: no transition from {initial!r} reaches a submitted state, so "
            "the family has no submit step",
        )

    def action_reaching(self, kind: str, state: str, predicate) -> str:
        """The single action from ``state`` reaching a state satisfying ``predicate``.

        This is why no action name appears in the spine: "submit", "cancel" and
        "complete" are *derived* by asking the workflow which transition reaches
        the kind of state the caller wants. An ambiguous answer is a refusal
        naming every candidate, never a pick.
        """
        workflow = self.workflow(kind)
        actions = sorted(
            {
                transition.action
                for transition in workflow.transitions
                if transition.from_state == state and predicate(workflow.state(transition.to))
            }
        )
        if not actions:
            raise Refused(
                "illegal-transition",
                f"{kind}: no declared transition from {state!r} reaches the state "
                "this step needs",
            )
        if len(actions) > 1:
            raise Refused(
                "definitions-invalid",
                f"{kind}: {len(actions)} actions from {state!r} reach the state this "
                f"step needs ({', '.join(actions)}), so the step is undecidable",
            )
        return actions[0]

    def stock_kind(self) -> str:
        """The cycle's stock-committing family (exactly one, by derivation)."""
        if len(self.stock_kinds) != 1:
            raise Refused(
                "definitions-invalid",
                f"the cycle commits stock through {len(self.stock_kinds)} families "
                f"({', '.join(self.stock_kinds) or 'none'}); a delivery needs exactly one",
            )
        return self.stock_kinds[0]

    # --- the ERP-02 validators --------------------------------------------

    def validate(self, kind: str, body: Any) -> Dict[str, Any]:
        """Hold a driven document body to the ERP-02 schema and its family rules."""
        self.require(kind)
        return self.validate_master(kind, body)

    def validate_master(self, kind: str, body: Any) -> Dict[str, Any]:
        """Hold a *master* document (an item, a party) to its ERP-02 schema.

        Masters have no lifecycle, so they are not addressable by the spine's
        flows — but the spine reads them (the item master decides whether a line
        commits stock, and at what valuation), so they are still validated by the
        model rather than trusted as scenario blobs.
        """
        if not isinstance(kind, str) or kind not in set(self.model.document_kinds()):
            raise Refused(
                "unknown-document-kind",
                f"{kind!r}: ERP-02 declares no schema for it "
                f"(known: {', '.join(self.model.document_kinds())})",
            )
        try:
            return self.model.validate_document(kind, body)
        except ErpError as exc:
            raise as_spine_refusal(exc) from exc

    def advance(self, body: Mapping[str, Any], action: str, *, target: Optional[str] = None):
        """The state ``action`` moves ``body`` to, per the ERP-02 workflow."""
        kind = body.get("doctype")
        self.require(kind)
        try:
            return self.model.advance(body, action, target=target)
        except ErpError as exc:
            raise as_spine_refusal(exc) from exc

    # --- reporting ---------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "issue": self.issue,
            "laneDocuments": [document.to_dict() for document in self.lane_documents],
            "families": list(self.families()),
            "drivable": list(self.drivable()),
            "undeclaredByCore": [document.to_dict() for document in self.undeclared_by_core()],
            "links": [link.to_dict() for link in self.links],
            "chain": list(self.chain),
            "stockKinds": list(self.stock_kinds),
            "accountingKind": self.accounting_kind,
            "voucherSources": list(self.voucher_sources),
            "addressable": list(self.addressable()),
            "undriven": [
                {"id": document.id, "reason": reason}
                for document, reason in self.undriven()
            ],
        }


def load(
    *,
    root: Optional[Any] = None,
    issue: int = LANE_ISSUE,
    model: Optional[DocumentModel] = None,
) -> DefinitionSet:
    """Resolve the lane's definition set from the indexer and the ERP-02 model.

    Both halves are required. A lane that cannot read the indexer does not know
    which documents it owns, and a lane that cannot read ERP-02 does not know how
    they move; either way there is nothing honest to report, so both are refusals
    rather than empty defaults.
    """
    resolved_model = model if model is not None else load_model()
    documents = indexer.catalogue_documents(root, issue=issue)

    lane_problems = resolved_model.check_assets()
    if lane_problems:
        raise Refused(
            "definitions-invalid",
            f"the ERP-02 assets do not agree with themselves: {lane_problems[0]}",
        )

    chain, links = resolve_chain(resolved_model, (document.id for document in documents))
    accounting_kind = resolve_accounting_kind(resolved_model)
    voucher_sources = resolve_voucher_sources(resolved_model, accounting_kind)

    stock_kinds = tuple(
        sorted(kind for kind in chain if _declares_warehouse(resolved_model, kind))
    )

    if chain[-1] not in voucher_sources:
        raise Refused(
            "definitions-invalid",
            f"{chain[-1]}: the cycle's last family is not an accepted "
            f"{VOUCHER_TYPE_FIELD} source ({', '.join(voucher_sources)}), so the "
            "accounting effect is undecidable",
        )
    if not stock_kinds:
        raise Refused(
            "definitions-invalid",
            f"no family in the cycle ({' -> '.join(chain)}) declares a warehouse "
            "field, so nothing in the cycle commits stock",
        )

    return DefinitionSet(
        issue=issue,
        lane_documents=documents,
        model=resolved_model,
        links=links,
        chain=chain,
        stock_kinds=stock_kinds,
        accounting_kind=accounting_kind,
        voucher_sources=voucher_sources,
    )
