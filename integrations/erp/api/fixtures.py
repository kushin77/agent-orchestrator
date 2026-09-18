"""A deterministic, offline corpus and the declarations the surface is driven with.

Two things live here, and both exist for the same reason: the REST surface has
to be exercisable with **no network, no wall clock and no deployment**, so the
gate that names it measures the surface rather than the environment.

**The corpus.** One valid document per kind ERP-02 declares, with fixed ids and
one fixed date. Every document is stored through
:meth:`~integrations.erp.api.store.DocumentStore.create`, which validates it
against the model, so the corpus cannot drift into a set of documents the API
would refuse: ``cli.py check`` re-validates all of them on every run and fails
naming the kind that stopped validating.

**The declarations.** ``integrations/erp/auth`` is the lane that *owns* the role
map and the field policies, and its shipped catalogue governs the doctype surface
it declares locally. This surface serves ERP-02's kinds, which are a different
set (``party``/``item``/``gl-posting`` are kinds here and not there), and a role
map that does not cover a kind cannot authorize a request for it. So the
declarations used here are **derived from the model** — every kind ERP-02
declares, every action the auth vocabulary has — rather than hand-listed, and
they go through the auth lane's own loaders, which validate them exactly as they
validate the shipped catalogue. Deriving them is also what keeps this a *fixture*
in the honest sense: change ERP-02 and the declarations follow, with no second
list to update. When the indexer-fed catalogue lands for ERP-08, the source of
these declarations changes and none of this code does.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from integrations.erp.auth import contract as auth_contract
from integrations.erp.auth import platform_fixture as fx
from integrations.erp.auth import policies as policies_module
from integrations.erp.auth import roles as roles_module
from integrations.erp.auth.model import SCHEMA_VERSION
from integrations.erp.core import validators as core_validators

from .store import DocumentStore

__all__ = [
    "DEFAULT_TEAM",
    "DEFAULT_TENANT",
    "Declarations",
    "PLATFORM_SUBJECT",
    "ROLE_ACTIONS",
    "declarations",
    "documents",
    "field_policies",
    "model",
    "repository_root",
    "role_map",
    "seeded_store",
]

#: The tenant the golden path and the controls run in. Fixed, like every other
#: value here: a transcript that depends on the environment is not evidence.
DEFAULT_TENANT = fx.DEFAULT_TENANT
DEFAULT_TEAM = fx.DEFAULT_TEAM
#: The platform subject every fixture principal is. Reference-based, like the
#: tenant: a name the platform resolves a scope for, never a credential (GR-6).
PLATFORM_SUBJECT = fx.DEFAULT_SUBJECT


#: ``<repo>`` — from ``<repo>/integrations/erp/api/fixtures.py``.
def repository_root() -> Path:
    """The repository root the declarations and the document are rooted at."""
    return Path(__file__).resolve().parents[3]

#: The date every fixture document carries, so two runs are byte-identical.
TODAY = "2026-09-15"

#: ``role -> the ERP actions that role grants on every kind``. Every role covers
#: every kind by construction, which is what the auth lane's loader requires
#: ("a kind no role may touch is a hole in the declaration, not a policy").
#:
#: The three roles are chosen so the gate can drive each of the two gates
#: separately: an *auditor* who may only read, a *clerk* who owns the document
#: lifecycle but may not cancel a document, and a *manager* who may do anything
#: the contract can express. ``cancel`` and ``amend`` are deliberately
#: manager-only — that difference is what makes ``403`` a real answer for a clerk
#: rather than a state the fixture can never reach.
ROLE_ACTIONS: Mapping[str, Tuple[str, ...]] = {
    "ERP Auditor": ("read",),
    "ERP Clerk": ("create", "read", "write", "submit", "delete"),
    "ERP Manager": (roles_module.ACTION_WILDCARD,),
}


def model() -> Any:
    """The loaded ERP-02 document model (the one instance this process uses)."""
    return core_validators.load_model()


# --- the corpus -------------------------------------------------------------


def _line(**over: Any) -> Dict[str, Any]:
    line: Dict[str, Any] = {"item_code": "ITEM-1", "qty": 2, "rate": 10.0}
    line.update(over)
    return line


def _stock_line(**over: Any) -> Dict[str, Any]:
    line: Dict[str, Any] = {"item_code": "ITEM-1", "qty": 2, "warehouse": "MAIN"}
    line.update(over)
    return line


def _transaction(doctype: str, **over: Any) -> Dict[str, Any]:
    """The shared header of the transaction families, exactly ERP-02's shape."""
    header: Dict[str, Any] = {
        "doctype": doctype,
        "id": f"{doctype.upper()}-0001",
        "state": "draft",
        "docstatus": 0,
        "company": "ACME",
        "currency": "USD",
        "transaction_date": TODAY,
        "lines": [_line()],
    }
    header.update(over)
    return header


def documents() -> Dict[str, List[Dict[str, Any]]]:
    """One valid document per kind, keyed by kind.

    Deliberately small and explicit rather than generated from the schemas: a
    corpus derived from the schema under test cannot disagree with it, and a
    corpus that cannot disagree proves nothing about the store that validates it.
    """
    return {
        "party": [
            {
                "doctype": "party",
                "id": "CUST-0001",
                "party_type": "customer",
                "name": "Northwind Traders",
                "currency": "USD",
            }
        ],
        "item": [
            {
                "doctype": "item",
                "id": "ITEM-1",
                "name": "Widget",
                "uom": "Nos",
                "is_stock_item": True,
                "standard_rate": 10.0,
            }
        ],
        "quotation": [_transaction("quotation", party="CUST-0001", valid_till=TODAY)],
        "sales-order": [
            _transaction("sales-order", party="CUST-0001", po_reference="CUST-PO-1")
        ],
        "delivery-note": [
            _transaction(
                "delivery-note",
                party="CUST-0001",
                against_sales_order="SALES-ORDER-0001",
                warehouse="MAIN",
            )
        ],
        "sales-invoice": [
            _transaction("sales-invoice", party="CUST-0001", posting_date=TODAY)
        ],
        "purchase-order": [_transaction("purchase-order", supplier="SUPP-0001")],
        "purchase-receipt": [
            _transaction(
                "purchase-receipt",
                supplier="SUPP-0001",
                against_purchase_order="PURCHASE-ORDER-0001",
                warehouse="MAIN",
            )
        ],
        "stock-entry": [
            {
                "doctype": "stock-entry",
                "id": "STOCK-ENTRY-0001",
                "state": "draft",
                "docstatus": 0,
                "company": "ACME",
                "purpose": "material_receipt",
                "to_warehouse": "MAIN",
                "transaction_date": TODAY,
                "lines": [_stock_line()],
            }
        ],
        "gl-posting": [
            {
                "doctype": "gl-posting",
                "id": "GL-POSTING-0001",
                "state": "draft",
                "docstatus": 0,
                "company": "ACME",
                "currency": "USD",
                "posting_date": TODAY,
                "voucher_type": "sales-invoice",
                "voucher_id": "SALES-INVOICE-0001",
                "lines": [
                    {"account": "DEBTORS", "debit": 100.0},
                    {"account": "REVENUE", "credit": 100.0},
                ],
            }
        ],
    }


def seeded_store(loaded: Any, tenant: str = DEFAULT_TENANT) -> DocumentStore:
    """A store holding the corpus, every document validated by the model."""
    store = DocumentStore(loaded)
    store.seed(tenant, documents())
    return store


# --- the declarations -------------------------------------------------------


def role_map(loaded: Any) -> Any:
    """A role map covering every kind ERP-02 declares, built through the auth loader."""
    declaration = {
        "version": SCHEMA_VERSION,
        "kinds": list(loaded.document_kinds()),
        "roles": {
            role: [
                {"kind": kind, "action": action, "permlevel": 0}
                for kind in sorted(loaded.document_kinds())
                for action in actions
            ]
            for role, actions in sorted(ROLE_ACTIONS.items())
        },
    }
    return roles_module.load(declaration)


def field_policies(loaded: Any) -> Any:
    """The field rules this surface's declarations carry, through the auth loader.

    Two rules, and both are load-bearing in a different direction:

    * ``po_reference`` is **write-only** for an ERP Clerk — the customer's own
      order reference is entered by a clerk and not read back — so a clerk's read
      of a sales order *omits* the field and names it in ``redacted``. That is the
      read side of field policy, which is what "enforced by omission" means.
    * ``total`` is **read-only** for the same role: it is derived by the platform,
      so a clerk's payload carrying it is refused. That is the write side, and it
      is the only way ``field-write-denied`` becomes a reachable refusal rather
      than a code with no path.

    Both fields are declared by the ``sales-order`` schema — ``cli.check`` proves
    that, so a rule cannot sit here governing a field no family has.
    """
    declaration = {
        "version": SCHEMA_VERSION,
        "rules": [
            {
                "id": "po-reference-write-only",
                "kind": "sales-order",
                "field": "po_reference",
                "effect": _block_effect(),
                "read": False,
                "write": True,
                "roles": ["ERP Clerk"],
                "reason": "the customer's own order reference is entered by a clerk, not read back",
            },
            {
                "id": "total-read-only",
                "kind": "sales-order",
                "field": "total",
                "effect": _block_effect(),
                "read": True,
                "write": False,
                "roles": ["ERP Clerk"],
                "reason": "the order total is derived by the platform, so a clerk may read it but not set it",
            },
        ],
    }
    return policies_module.load(declaration, kinds=loaded.document_kinds())


def _block_effect() -> str:
    """The effect word a *denying* rule must carry, read from the consumed contract.

    ``policies.load`` refuses a rule whose effect disagrees with what it withholds,
    so a hand-typed word here would fail loudly at the loader — this reads the word
    out of ``guardrails/policy``'s own vocabulary instead, so there is nothing to
    mistype.
    """
    vocabulary = auth_contract.effect_vocabulary()
    for word in vocabulary:
        if str(word).lower() == policies_module.BLOCK_EFFECT:
            return str(word)
    raise AssertionError(
        f"the consumed contract declares no {policies_module.BLOCK_EFFECT!r} effect: {vocabulary}"
    )


@dataclass(frozen=True)
class Declarations:
    """The declarations one surface run is driven with."""

    model: Any
    role_map: Any
    policy_set: Any
    rbac_store: Any
    tenant: str
    team: str

    def permissions(self, role: str) -> Tuple[str, ...]:
        """The platform permissions the fixture grants for ``role``.

        The role map's own translated grants, so the two gates agree by
        construction. A control seeds a *subset* to show that they can disagree —
        and that the platform is the one that decides when they do.
        """
        return self.role_map.permissions(role)


def declarations(
    loaded: Optional[Any] = None,
    *,
    role: str = "ERP Clerk",
    permissions: Optional[Sequence[str]] = None,
) -> Declarations:
    """Build the declarations for one run, from the model, through the auth loaders."""
    active = loaded if loaded is not None else model()
    mapped = role_map(active)
    if role not in mapped.roles:
        raise AssertionError(f"{role!r} is not a declared role: {', '.join(mapped.role_names)}")
    granted = tuple(permissions) if permissions is not None else mapped.permissions(role)
    rbac_store, _node = fx.build(
        tenant=DEFAULT_TENANT,
        team=DEFAULT_TEAM,
        subject=fx.DEFAULT_SUBJECT,
        permissions=granted,
    )
    return Declarations(
        model=active,
        role_map=mapped,
        policy_set=field_policies(active),
        rbac_store=rbac_store,
        tenant=DEFAULT_TENANT,
        team=DEFAULT_TEAM,
    )
