"""The ERP-04 ops lane's document model — ERP-02's families plus this lane's.

This is the model half of the ERP module's **procurement + manufacturing** lane
(issue #649, EPIC #645): buying documents (RFQ, purchase order, receipt,
invoice, subcontracting note) and production documents (BOM, work order,
production plan), over the same core model ERP-02 landed.

**What is consumed, and what is declared.** ``integrations/erp/core`` (#647) is
already on ``main``, so this lane CONSUMES it instead of re-declaring it:

* the buying and stock families the cycle moves — ``purchase-order``,
  ``purchase-receipt``, ``stock-entry``, ``gl-posting`` — and the two masters it
  links, ``item`` and ``party``, are validated by **ERP-02's own model**, loaded
  from ``integrations/erp/core`` through ``core.validators.load_model``;
* the shared definitions the lane's own schemas compose from (``identifier``,
  ``quantity``, ``rate``, ``money``, ``currencyCode``, ``isoDate``,
  ``docstatus``, ``transactionLine``, ``taxLine``) are ``$ref``'d out of
  ``core/schemas/document.schema.json``, so a quantity or a currency code means
  one thing across the whole module rather than one thing per family;
* the workflow data model and its structural refusals (a state jump refused by
  name, an unreachable state refused at load, docstatus never decreasing) are
  ERP-02's ``workflow`` module, exercised on this lane's own lifecycle files.

What this lane DECLARES is the part ERP-02 does not ship: the supplier-side
documents (``rfq``, ``purchase-invoice``, ``subcontracting-note``) and the
production documents (``bom``, ``work-order``, ``production-plan``), each as a
JSON-Schema family under ``schemas/`` with its lifecycle as data under
``workflows/``. Both halves are loaded by the same ``load_model`` machinery, so
the lane's families are held to the same asset contract (coverage both ways,
state/docstatus parity, provenance completeness) that ERP-02's are.

**Why the refusal vocabulary is closed.** :data:`REFUSALS` is every reason this
package can refuse for, and :class:`Refused` refuses to carry a code outside it.
A refusal with an invented code is one a caller cannot branch on and a gate
cannot assert, so it fails at the raise rather than reaching a consumer.
``negative_control.py`` provokes every code, and ``tests/test_negative_control.py``
fails the suite when the provoked set and this set diverge.

**How ERP-02's refusals reach this vocabulary.** The core model raises
``ErpError`` with its own codes (``state_jumped``, ``unbalanced_posting``, …).
Rather than leak two dialects, :func:`_translate` maps each onto this lane's code
and keeps the core code and message in the detail, so a caller branches on one
vocabulary while the evidence still names the rule that refused.

---knowledge---
module_id: integrations.erp.ops.model
system: integrations
app: erp
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [Refused, Finding, Model, load_model]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from integrations.erp.core import validators as core_validators
from integrations.erp.core.errors import ErpError

__all__ = [
    "ACTIONS",
    "CORE_KINDS",
    "KIND_BOM",
    "KIND_GL_POSTING",
    "KIND_ITEM",
    "KIND_PARTY",
    "KIND_PRODUCTION_PLAN",
    "KIND_PURCHASE_INVOICE",
    "KIND_PURCHASE_ORDER",
    "KIND_PURCHASE_RECEIPT",
    "KIND_RFQ",
    "KIND_STOCK_ENTRY",
    "KIND_SUBCONTRACTING_NOTE",
    "KIND_WORK_ORDER",
    "MANUFACTURING_KINDS",
    "MODEL_ROOT",
    "OPS_KINDS",
    "PURCHASE_CYCLE",
    "REFUSALS",
    "Finding",
    "Model",
    "Refused",
    "load_model",
]

#: This package's own root — the tree the lane's shipped assets live in.
MODEL_ROOT = Path(__file__).resolve().parent

# --- the lane's own document kinds ------------------------------------------

KIND_RFQ = "rfq"
KIND_PURCHASE_INVOICE = "purchase-invoice"
KIND_SUBCONTRACTING_NOTE = "subcontracting-note"
KIND_BOM = "bom"
KIND_WORK_ORDER = "work-order"
KIND_PRODUCTION_PLAN = "production-plan"

#: The families this lane declares, sorted so a diff reads.
OPS_KINDS: Tuple[str, ...] = (
    KIND_BOM,
    KIND_PRODUCTION_PLAN,
    KIND_PURCHASE_INVOICE,
    KIND_RFQ,
    KIND_SUBCONTRACTING_NOTE,
    KIND_WORK_ORDER,
)

#: The manufacturing half, as a named set — a work order is the only document
#: whose completion moves stock twice, and the suite asserts it.
MANUFACTURING_KINDS: Tuple[str, ...] = (KIND_BOM, KIND_PRODUCTION_PLAN, KIND_WORK_ORDER)

# --- the ERP-02 families this lane consumes ---------------------------------

KIND_PURCHASE_ORDER = "purchase-order"
KIND_PURCHASE_RECEIPT = "purchase-receipt"
KIND_STOCK_ENTRY = "stock-entry"
KIND_GL_POSTING = "gl-posting"
KIND_ITEM = "item"
KIND_PARTY = "party"

#: ERP-02's families, as this lane uses them.
CORE_KINDS: Tuple[str, ...] = (
    KIND_GL_POSTING,
    KIND_ITEM,
    KIND_PARTY,
    KIND_PURCHASE_ORDER,
    KIND_PURCHASE_RECEIPT,
    KIND_STOCK_ENTRY,
)

#: The buying cycle, in the order it is driven. Acceptance criterion 1 is that
#: this sequence posts stock and GL effects correctly, so the sequence itself is
#: declared here and asserted against the catalogue rather than implied.
PURCHASE_CYCLE: Tuple[str, ...] = (
    KIND_RFQ,
    KIND_PURCHASE_ORDER,
    KIND_PURCHASE_RECEIPT,
    KIND_PURCHASE_INVOICE,
)

# --- the closed audit-action vocabulary -------------------------------------

ACTION_ADVANCE = "advance"
ACTION_CONVERT = "convert"
ACTION_CREATE = "create"
ACTION_EXPLODE = "explode"
ACTION_INVOICE = "invoice"
ACTION_ISSUE = "issue"
ACTION_PLAN = "plan"
ACTION_POST = "post"
ACTION_PRODUCE = "produce"
ACTION_RECEIVE = "receive"

#: Every action the audit rail will carry. Free text here would make the rail
#: unqueryable, so an entry whose action is not one of these is refused
#: (``unknown-action``) — the rail is a record an auditor reads back.
ACTIONS: Tuple[str, ...] = (
    ACTION_ADVANCE,
    ACTION_CONVERT,
    ACTION_CREATE,
    ACTION_EXPLODE,
    ACTION_INVOICE,
    ACTION_ISSUE,
    ACTION_PLAN,
    ACTION_POST,
    ACTION_PRODUCE,
    ACTION_RECEIVE,
)

# --- the closed refusal vocabulary ------------------------------------------

#: Every reason this package refuses for. Sorted, so a diff reads.
REFUSALS = frozenset(
    {
        # declaration + schema layer
        "declarations-invalid",
        "schema-violation",
        "unsupported-schema-keyword",
        "unknown-kind",
        "unknown-vocabulary",
        "unknown-vocabulary-term",
        # workflow
        "illegal-transition",
        "unknown-action",
        "unknown-state",
        # envelope + store
        "duplicate-id",
        "invalid-value",
        "missing-field",
        "unknown-document",
        "unknown-field",
        # procurement
        "already-converted",
        "over-receipt",
        "purchase-order-not-submitted",
        "receipt-without-order",
        "rfq-not-submitted",
        "unknown-item",
        "unknown-supplier",
        # manufacturing
        "bom-cycle",
        "bom-item-mismatch",
        "bom-not-submitted",
        "insufficient-stock",
        "unknown-bom",
        "unknown-work-order",
        "work-order-already-completed",
        "work-order-not-submitted",
        # posting
        "unbalanced-posting",
        "unknown-posting-rule",
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
    refused — the document id, the field, the state — so a failure names the
    offender rather than merely that something is wrong. The vocabulary check
    lives on the raise, so a refuser that invents a code fails here rather than
    silently reaching a consumer.
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
    """One inconsistency found by a *whole-set* check (the rail, the assets).

    A refusal is raised and names the first offender, because a transition that
    is illegal is illegal now. A check over a set reports **every** problem
    instead, so a repaired artifact cannot hide a second defect behind the
    first.
    """

    code: str
    detail: str
    ref: str = ""

    def to_dict(self) -> Dict[str, str]:
        return {"code": self.code, "detail": self.detail, "ref": self.ref}


#: How ERP-02's refusal codes reach this lane's closed vocabulary. Every core
#: code is mapped, so no core refusal can leak a second dialect to a caller, and
#: the core code and message survive in the detail.
_TRANSLATIONS: Mapping[str, str] = {
    "invalid_body": "invalid-value",
    "invalid_transfer": "invalid-value",
    "missing_provenance": "provenance-invalid",
    "schema_violation": "schema-violation",
    "state_jumped": "illegal-transition",
    "unbalanced_posting": "unbalanced-posting",
    "unknown_action": "unknown-action",
    "unknown_document_kind": "unknown-kind",
    "unknown_state": "unknown-state",
    "unknown_workflow": "unknown-kind",
    "workflow_invalid": "declarations-invalid",
    "yaml_unavailable": "declarations-invalid",
}


def _translate(refusal: ErpError) -> Refused:
    """One core refusal, re-expressed in this lane's vocabulary.

    The core code and message survive in the detail, and so do the core
    refusal's own ``problems``: ERP-02's schema refusal reports *how many*
    violations it found in its message and puts the violations themselves in
    ``details``, so a caller that only kept the message would be told that a
    document is invalid without being told which field to open.
    """
    detail = f"{refusal.code}: {refusal.message}"
    problems = (refusal.details or {}).get("problems")
    if problems:
        detail += " — " + "; ".join(str(problem) for problem in problems[:4])
    return Refused(_TRANSLATIONS.get(refusal.code, "schema-violation"), detail)


@dataclass(frozen=True)
class Model:
    """The loaded model: ERP-02's families plus the ones this lane declares.

    Two loaded :class:`~integrations.erp.core.validators.DocumentModel` objects,
    routed by ``doctype``. A kind neither declares is refused by name rather
    than defaulted — an unknown family must never read as a valid document.
    """

    core: Any
    ops: Any

    # --- lookups ----------------------------------------------------------

    def kinds(self) -> Tuple[str, ...]:
        """Every family the composite model can validate, sorted."""
        return tuple(sorted(set(self.core.schemas) | set(self.ops.schemas)))

    def local_kinds(self) -> Tuple[str, ...]:
        """The families this lane declares (as opposed to consumes)."""
        return tuple(sorted(self.ops.schemas))

    def core_kinds(self) -> Tuple[str, ...]:
        """Every family ERP-02 ships, whether or not this lane drives it."""
        return tuple(sorted(self.core.schemas))

    def _submodel(self, kind: str) -> Optional[Any]:
        if kind in self.ops.schemas:
            return self.ops
        if kind in self.core.schemas:
            return self.core
        return None

    def has_kind(self, kind: str) -> bool:
        return self._submodel(kind) is not None

    def schema_for(self, kind: str) -> Dict[str, Any]:
        submodel = self._submodel(kind)
        if submodel is None:
            raise Refused(
                "unknown-kind",
                f"no family schema declares {kind!r}; known: {list(self.kinds())}",
            )
        return submodel.schemas[kind]

    # --- workflow ---------------------------------------------------------

    def workflow_for(self, kind: str) -> Any:
        submodel = self._submodel(kind)
        if submodel is None:
            raise Refused("unknown-kind", f"no workflow is addressable for {kind!r}")
        try:
            return submodel.workflow_for(kind)
        except ErpError as refusal:
            raise _translate(refusal) from refusal

    def state_names(self, kind: str) -> Tuple[str, ...]:
        return self.workflow_for(kind).state_names()

    def actions_from(self, kind: str, state: str) -> Tuple[str, ...]:
        workflow = self.workflow_for(kind)
        try:
            workflow.state(state)
        except ErpError as refusal:
            raise _translate(refusal) from refusal
        return workflow.actions_from(state)

    def advance(
        self, document: Mapping[str, Any], action: str, *, target: Optional[str] = None
    ) -> str:
        """The state ``action`` moves ``document`` to, refusing a jump by name."""
        kind = document.get("doctype")
        submodel = self._submodel(kind) if isinstance(kind, str) else None
        if submodel is None:
            raise Refused("unknown-kind", f"no workflow is addressable for {kind!r}")
        try:
            return submodel.advance(document, action, target=target)
        except ErpError as refusal:
            raise _translate(refusal) from refusal

    def validate_move(self, kind: str, from_state: str, to_state: str) -> str:
        """Refuse unless the move is a declared transition of ``kind``."""
        submodel = self._submodel(kind)
        if submodel is None:
            raise Refused("unknown-kind", f"no workflow is addressable for {kind!r}")
        try:
            return submodel.validate_move(kind, from_state, to_state)
        except ErpError as refusal:
            raise _translate(refusal) from refusal

    # --- validation -------------------------------------------------------

    def validate(self, kind: str, document: Any) -> Dict[str, Any]:
        """Validate one document against its family schema and its rules."""
        submodel = self._submodel(kind)
        if submodel is None:
            raise Refused(
                "unknown-kind",
                f"no family schema declares {kind!r}; known: {list(self.kinds())}",
            )
        try:
            return submodel.validate_document(kind, document)
        except ErpError as refusal:
            raise _translate(refusal) from refusal

    # --- the asset contract, for both halves ------------------------------

    def check_assets(self) -> List[str]:
        """Every way the shipped assets contradict their own contract.

        Both halves are measured: ERP-02's model over the families it ships and
        this lane's model over the families it declares. The prefixes keep a
        reader able to tell which tree a problem points at.
        """
        return [f"core: {problem}" for problem in self.core.check_assets()] + [
            f"lane: {problem}" for problem in self.ops.check_assets()
        ]

    def check_declarations(self, catalog: Any) -> List[str]:
        """Every way the lane's catalogue and its shipped assets disagree.

        The catalogue is a *pointer* file: it names the families, the lifecycles
        and the posting rules. Each claim has to resolve, and each of these can
        fail — a kind the catalogue claims to declare that no schema declares, a
        lifecycle name that matches no workflow, a posting rule posted under a
        purpose outside the declared vocabulary.
        """
        problems: List[str] = []
        for kind in catalog.kinds:
            if kind.source == "local" and kind.id not in self.ops.schemas:
                problems.append(
                    f"catalog: kind {kind.id!r} is declared source=local but no "
                    f"schemas/{kind.id}.schema.json declares it"
                )
            if kind.source == "core" and kind.id not in self.core.schemas:
                problems.append(
                    f"catalog: kind {kind.id!r} is declared source=core but ERP-02 "
                    "declares no such family"
                )
            # A master (item, party) has no lifecycle, so the lifecycle claims
            # are checked only where one is declared — and a kind that declares
            # one must have the workflow it names, not merely some workflow.
            if kind.lifecycle is not None:
                shipped = set(self.ops.workflows.names()) | set(self.core.workflows.names())
                if kind.lifecycle not in shipped:
                    problems.append(
                        f"catalog: kind {kind.id!r} names lifecycle "
                        f"{kind.lifecycle!r}, which no shipped workflow declares"
                    )
                elif self.has_kind(kind.id):
                    declared = self.workflow_for(kind.id).workflow
                    if kind.lifecycle != declared:
                        problems.append(
                            f"catalog: kind {kind.id!r} names lifecycle "
                            f"{kind.lifecycle!r} but its workflow is {declared!r}"
                        )
        for kind in self.local_kinds():
            if kind not in {entry.id for entry in catalog.kinds}:
                problems.append(
                    f"catalog: the lane ships a schema for {kind!r} that the "
                    "catalogue does not declare"
                )
        for position, rule in enumerate(catalog.postings):
            if not self.has_kind(rule.document):
                problems.append(
                    f"catalog: posting rule {position} ({rule.key!r}) posts "
                    f"document {rule.document!r}, which no family declares"
                )
            if rule.purpose is not None:
                try:
                    catalog.term("stock-purposes", rule.purpose)
                except Refused as refusal:
                    problems.append(f"catalog: posting rule {rule.key!r}: {refusal.detail}")
            try:
                catalog.term("voucher-types", rule.voucher_type)
            except Refused as refusal:
                problems.append(f"catalog: posting rule {rule.key!r}: {refusal.detail}")
        for step in PURCHASE_CYCLE:
            if step not in {entry.id for entry in catalog.kinds}:
                problems.append(
                    f"catalog: the purchase cycle's {step!r} is not a declared kind"
                )
        if catalog.flag.get("default") != "off":
            problems.append(
                f"catalog: the lane's flag defaults {catalog.flag.get('default')!r}; "
                "a new surface ships OFF until a reviewed go-live (GR-5)"
            )
        return problems


_DEFAULT_MODEL: Optional[Model] = None


def load_model(root: Optional[Path | str] = None) -> Model:
    """Load the composite model: ERP-02's families and this lane's.

    ``root`` overrides this lane's tree (the mutant harness points it at a
    scratch copy); ERP-02 is always loaded from its own package, so the two
    halves cannot be confused for one another.
    """
    lane_root = Path(root).resolve() if root is not None else MODEL_ROOT
    try:
        ops = core_validators.load_model(lane_root)
    except ErpError as refusal:
        raise _translate(refusal) from refusal
    try:
        core = core_validators.load_model()
    except ErpError as refusal:
        raise _translate(refusal) from refusal
    return Model(core=core, ops=ops)


def _default_model() -> Model:
    global _DEFAULT_MODEL
    if _DEFAULT_MODEL is None:
        _DEFAULT_MODEL = load_model()
    return _DEFAULT_MODEL
