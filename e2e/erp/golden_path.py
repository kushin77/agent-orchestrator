"""e2e/erp/golden_path — the ERP module's tenant journey, offline (ERP-10, #655).

One tenant, one switch, one selling cycle, every hop read through the surface that
owns it. The stage list is the acceptance criterion, in order:

1. **provision** — the tenant's console serves the ERP module with the module's own
   flag ON (ERP-07 over ERP-01's declaration and ERP-06's contract), and the module
   ships OFF (GR-5). The declaration, the served contract, the dashboard, a document
   list and the module frame are all read through the real ``ConsoleApplication``, with
   no socket and no network.
2. **cycle** — the sales cycle the acceptance names, **quotation -> sales-order ->
   delivery-note -> sales-invoice**, driven by ERP-03's spine over ERP-02's document
   model: ERP-02 validates every document, commits the stock movement on the delivery
   and posts the ledger on the invoice. The cycle's chain, its link fields, the stock
   family and the ledger family are all *resolved* — from the knowledge indexer's
   catalogue and the ERP-02 schemas — so this stage measures the resolution rather than
   restating the flow. Every hop is indexer-fed, and that is measured, not asserted.
3. **scope** — ERP-08 decides every hop's read and write for the owning tenant, using
   its shipped role map and field policies over its own offline platform fixture.
4. **metering** — ERP-09 meters every operation the cycle actually performed (one
   create per document, one transition per state move, with the action ERP-02 names),
   writes each onto the tenant's hash-chained audit ledger, prices it from the shipped
   rate card, checks the tenant's budget before the write, and certifies the cost
   against the intact chain.

**Failures are measured, not raised.** Each stage returns the evidence it gathered plus
the list of ways that evidence contradicts the acceptance criterion, and the runner
aggregates them. A stage that *raised* on the first defect would report one problem per
run and hide the rest — and a check whose failure path is an exception cannot tell
"this lane is broken" from "this lane cannot read its own inputs", which is exactly the
distinction the repository's tri-state contract exists to keep.

Offline, deterministic and keyless: no sockets, no network, no credentials, and no
wall-clock reading anywhere in a value the evidence carries.

Run from the repo root:

    python3 -m e2e.erp.golden_path [--out DIR]
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from e2e._paths import REPO_ROOT, ensure_sys_paths

ensure_sys_paths()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from e2e.erp.gate import DEFAULT_TENANT, ConsoleSession, mint_session  # noqa: E402
from e2e.wiring import write_evidence  # noqa: E402

#: The cycle the acceptance criterion names, in order. The stage asserts the chain it
#: *resolved* equals this — the resolution is what makes it a measurement, and this
#: tuple is the criterion.
CYCLE_FAMILIES: Tuple[str, ...] = (
    "quotation",
    "sales-order",
    "delivery-note",
    "sales-invoice",
)

#: ERP-01's manifest, relative to the repository root. Read through ERP-01's own
#: loader, never parsed here.
MANIFEST_RELPATH: Path = Path("integrations") / "erp" / "module.yaml"

#: The name the promoted scratch declaration carries, under the run directory.
PROMOTED_CONFIG_NAME = "promoted-feature-flags.yaml"

#: The actor every metered operation is attributed to.
METER_ACTOR = "agent:erp-e2e"


@dataclass(frozen=True)
class Stage:
    """One stage's evidence and the ways it contradicts the criterion."""

    name: str
    evidence: Dict[str, Any] = field(default_factory=dict)
    failures: Tuple[str, ...] = ()


@dataclass(frozen=True)
class CycleRun:
    """One cycle, resolved and run: the definitions and the workspace they produced.

    Every stage after ``cycle`` measures *this* object rather than rebuilding its own,
    so "the documents ERP-08 decided on" and "the operations ERP-09 metered" are the
    same documents the spine actually wrote.
    """

    definitions: Any
    path: Any

    @property
    def workspace(self) -> Any:
        return self.path.workspace

    def documents(self) -> Tuple[Any, ...]:
        """Every document the cycle holds, derived order (sorted by id)."""
        return tuple(self.workspace.documents[key] for key in sorted(self.workspace.documents))

    def hop_documents(self) -> Tuple[Any, ...]:
        """The four documents of the acceptance's cycle, in the cycle's own order."""
        return tuple(
            next(document for document in self.documents() if document.kind == kind)
            for kind in CYCLE_FAMILIES
        )


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _read(app: Any, path: str, *, cookies: Optional[Mapping[str, str]] = None) -> Tuple[int, Dict[str, Any]]:
    """One console request; anonymous unless a session cookie is supplied."""
    response = app.handle("GET", path, cookies=dict(cookies or {}))
    raw = response.as_bytes().decode("utf-8", "replace")
    try:
        payload = json.loads(raw)
    except ValueError:
        payload = {}
    return response.status, payload


def promoted_config(run_dir: Path) -> Path:
    """Write the *promoted* flag declaration the provision stage serves under.

    The shipped declaration is what the module ships with (OFF, GR-5) and it is not
    this lane's file to edit. A promotion is a reviewed act that changes exactly this
    one value, so the stage writes the promoted document into its own run directory and
    points the surface at it: the reader under test is the shipped reader, and the
    difference between the two runs is one declared value.
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / PROMOTED_CONFIG_NAME
    path.write_text(
        "# Written by e2e/erp/golden_path.py (issue #655): the shipped declaration with\n"
        "# the module's own surface promoted, and nothing else changed.\n"
        "surfaces:\n"
        "  erp_module:\n"
        "    default: on\n",
        encoding="utf-8",
    )
    return path


def canonical_digest(payload: Mapping[str, Any]) -> str:
    """A stable digest of the evidence, for the determinism assertion."""
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _movement_qty(space: Any, document: Any) -> float:
    """The quantity the stock ledger moved for ``document``, summed."""
    return sum(
        float(movement.qty)
        for movement in space.stock.movements
        if movement.document == document.id
    )


def _declared_qty(document: Any) -> float:
    """The quantity a document's own lines declare, summed."""
    total = 0.0
    for line in document.body.get("lines") or []:
        if isinstance(line, Mapping) and isinstance(line.get("qty"), (int, float)):
            total += float(line["qty"])
    return total


def _action_for_move(definitions: Any, kind: str, from_state: str, to_state: str) -> Optional[str]:
    """The action ERP-02 declares for one state move, or ``None`` when it declares none.

    The action name is the *model's*: the spine's rail records what it did, and ERP-02
    owns the vocabulary a transition is expressed in. Resolving it here rather than
    reusing the rail's action is what keeps the metered transition a declared one.
    """
    matches = [
        transition.action
        for transition in definitions.model.workflow_for(kind).transitions
        if transition.from_state == from_state and transition.to == to_state
    ]
    return matches[0] if len(matches) == 1 else None


def _state_moves(space: Any) -> Tuple[Tuple[str, str, str, str], ...]:
    """Every state move the cycle made: ``(kind, ref, from_state, to_state)``.

    A rail entry with no ``from_state`` is a *creation* (the document's first state),
    not a transition, and is recorded as a create instead.
    """
    return tuple(
        (entry.kind, entry.ref, entry.from_state, entry.to_state)
        for entry in space.rail
        if entry.from_state and entry.from_state != entry.to_state
    )


# --------------------------------------------------------------------------- #
# stage 1 — provision (ERP-01 declaration, ERP-07 console, ERP-06 contract)
# --------------------------------------------------------------------------- #
def stage_provision(
    run_dir: Path,
    *,
    repo_root: Path = REPO_ROOT,
    session: ConsoleSession,
) -> Stage:
    """Provision the module for the tenant and read it back through the console."""
    from integrations.erp.catalog.model import MANIFEST_PATH, load_manifest
    from portal.server.app import build_app
    from portal.server.config_flags import (
        ERP_MODULE_SURFACE,
        read_config_default,
        surface_enabled,
    )
    from portal.server.erp import ErpModuleSurface, build_erp_api

    failures: List[str] = []
    manifest = load_manifest(repo_root / MANIFEST_PATH)

    shipped = read_config_default(repo_root, surface=ERP_MODULE_SURFACE)
    promoted_path = promoted_config(run_dir)
    promoted = surface_enabled(repo_root, config_path=promoted_path, surface=ERP_MODULE_SURFACE)
    if shipped != "off":
        failures.append(
            f"the module ships promoting itself: surfaces.{ERP_MODULE_SURFACE}.default "
            f"reads {shipped!r}, not 'off' (GR-5)"
        )
    if not promoted:
        failures.append(
            "a declaration that promotes the surface still reads off, so the module "
            "cannot be switched on at all"
        )

    surface = ErpModuleSurface(
        repo_root=repo_root, config_path=promoted_path, api=build_erp_api(repo_root)
    )
    app = build_app(repo_root=repo_root, sso=session.sso(), erp_module_surface=surface)

    routes = (
        ("module", "/api/erp/module"),
        ("dashboard", "/api/erp/dashboard"),
        ("reports", "/api/erp/reports/inventory"),
        ("documents", f"/api/erp/documents/{CYCLE_FAMILIES[1]}"),
        ("frame", "/erp/module.html"),
    )
    statuses: Dict[str, int] = {}
    documents: Dict[str, Any] = {}
    for label, path in routes:
        status, payload = _read(app, path, cookies=session.cookies)
        statuses[label] = status
        documents[label] = payload.get("data") if isinstance(payload, Mapping) else None
        if status != 200:
            failures.append(f"the provisioned module did not serve {path} (status {status})")

    module = documents.get("module") or {}
    dashboard = documents.get("dashboard") or {}
    identity = module.get("module") or {}
    contract = module.get("contract") or {}
    model = contract.get("model") or {}
    families = [row.get("kind") for row in (dashboard.get("families") or [])]
    kinds = list(model.get("kinds") or [])

    if identity.get("id") != manifest.get("id"):
        failures.append(
            f"the served module identity is {identity.get('id')!r}, not the manifest's "
            f"{manifest.get('id')!r}"
        )
    if manifest.get("mandatory") is not True:
        failures.append("the module does not declare itself mandatory in its own manifest")
    if manifest.get("data_source") != "indexer":
        failures.append(
            f"the module declares data_source {manifest.get('data_source')!r}, not "
            "'indexer', so its claims are not indexer-fed"
        )
    if not contract:
        failures.append("the provisioned module served no ERP-06 contract, so it renders nothing")
    if families != kinds:
        failures.append(
            "the dashboard's families are not the served contract's own kinds in its own "
            f"order ({families} vs {kinds})"
        )
    missing = [kind for kind in CYCLE_FAMILIES if kind not in kinds]
    if missing:
        failures.append(
            "the provisioned module does not serve the cycle's families: " + ", ".join(missing)
        )

    evidence = {
        "manifest": {
            "id": manifest.get("id"),
            "name": manifest.get("name"),
            "mandatory": manifest.get("mandatory"),
            "dataSource": manifest.get("data_source"),
            "epic": manifest.get("epic"),
            "issue": manifest.get("issue"),
            "features": [dict(row) for row in (manifest.get("features") or [])],
            "indexerSources": [dict(row) for row in (manifest.get("indexer_sources") or [])],
        },
        "flag": {
            "surface": ERP_MODULE_SURFACE,
            "shipped": shipped,
            "promoted": "on" if promoted else "off",
            "served": (module.get("flag") or {}).get("declared"),
            "source": (module.get("flag") or {}).get("source"),
        },
        "routes": {label: {"path": path, "status": statuses[label]} for label, path in routes},
        "contract": {
            "root": model.get("root"),
            "kinds": kinds,
            "lifecycleKinds": list(model.get("lifecycleKinds") or []),
            "cycleTransitions": {
                kind: (contract.get("transitions") or {}).get(kind) for kind in CYCLE_FAMILIES
            },
            "schemaSources": contract.get("schemaSources"),
        },
        "dashboard": {
            "families": families,
            "generatedFrom": dashboard.get("generatedFrom"),
            "totals": dashboard.get("totals"),
        },
        "documents": {
            "kind": (documents.get("documents") or {}).get("kind"),
            "count": (documents.get("documents") or {}).get("count"),
        },
    }
    return Stage(name="provision", evidence=evidence, failures=tuple(failures))


# --------------------------------------------------------------------------- #
# stage 2 — the sales cycle (ERP-03 over ERP-02, every hop indexer-fed)
# --------------------------------------------------------------------------- #
def run_cycle(*, tenant: str = DEFAULT_TENANT) -> CycleRun:
    """Resolve the definitions through the indexer and run the cycle once."""
    from integrations.erp.tx import spine
    from integrations.erp.tx.definitions import load as load_definitions

    definitions = load_definitions()
    return CycleRun(definitions=definitions, path=spine.golden_path("erp-e2e", definitions))


def stage_cycle(cycle: CycleRun) -> Stage:
    """Measure the cycle: the resolved chain, the stock effect, the ledger, the rail."""
    from integrations.erp.tx import indexer
    from integrations.erp.tx.model import POSTING_ROLES, ROLE_INCOME, ROLE_RECEIVABLE

    definitions = cycle.definitions
    space = cycle.workspace
    failures: List[str] = []

    chain = tuple(definitions.chain)
    if chain != CYCLE_FAMILIES:
        failures.append(
            f"the resolved cycle is {' -> '.join(chain)}, not the acceptance's "
            f"{' -> '.join(CYCLE_FAMILIES)}"
        )
    for finding in cycle.path.findings:
        failures.append(f"the cycle contradicts itself: {finding.code}: {finding.detail}")

    # --- every hop indexer-fed: the declarations came from the index ---------
    _payload, items = indexer.load_index()
    indexed = {
        Path(item.path).name: item
        for item in indexer.query(
            items, kind=indexer.CATALOGUE_KIND, path_prefix=f"{indexer.CATALOGUE_DOCUMENTS_RELPATH}/"
        )
    }
    lane_documents = [document.to_dict() for document in definitions.lane_documents]
    unknown = [document["id"] for document in lane_documents if f"{document['id']}.json" not in indexed]
    if unknown:
        failures.append(
            "the lane's declarations are not carried by the knowledge index: " + ", ".join(unknown)
        )

    # The cycle spans two lanes' declarations (ERP-02 declares the selling documents,
    # ERP-03 owns the stock and ledger ones), so each hop is resolved from the index by
    # its own catalogue declaration and the owning issue is recorded — a hop whose
    # declaration the index does not carry is a hop nothing serves.
    hop_declarations: Dict[str, Any] = {}
    for kind in CYCLE_FAMILIES:
        item = indexed.get(f"{kind}.json")
        if item is None:
            failures.append(
                f"{kind}: the knowledge index carries no catalogue declaration for it "
                f"(indexed: {', '.join(sorted(indexed))})"
            )
            continue
        declaration = indexer.read_lane_document(
            Path(indexer.REPO_ROOT) / item.path, where=item.path
        )
        hop_declarations[kind] = declaration.to_dict()

    # --- the documents -------------------------------------------------------
    summary = space.summary()
    initial = definitions.initial_state(CYCLE_FAMILIES[0])
    for document in cycle.hop_documents():
        if document.state == initial:
            failures.append(f"{document.id}: the cycle left the {document.kind} in {document.state!r}")
        if not space.rail.for_ref(document.id):
            failures.append(f"{document.id}: the cycle recorded no audit step for it")
        if not summary["documents"].get(document.id, {}).get("kind") == document.kind:
            failures.append(f"{document.id}: the summary does not carry the {document.kind}")

    # --- the stock effect ----------------------------------------------------
    stock_kind = definitions.stock_kind()
    delivered = next(document for document in cycle.documents() if document.kind == stock_kind)
    moved = _movement_qty(space, delivered)
    declared = _declared_qty(delivered)
    if moved != -declared:
        failures.append(
            f"the delivery moved {moved} of stock for a document declaring {declared}, "
            "so the stock effect is not the delivery's own quantity"
        )
    balances = summary["stock"]
    if not balances or all(value == 0 for value in balances.values()):
        failures.append("the cycle committed no stock at all")

    # --- the general ledger --------------------------------------------------
    debits, credits = space.ledger.totals()
    if debits != credits:
        failures.append(f"the ledger posts {debits:.2f} of debits against {credits:.2f} of credits")
    if debits <= 0:
        failures.append("the cycle posted no ledger entry")
    invoice = cycle.hop_documents()[-1]
    gross = invoice.body.get("total")
    receivable = space.policy.resolve(ROLE_RECEIVABLE)
    income = space.policy.resolve(ROLE_INCOME)
    posted = {
        account: {
            "debits": sum(float(entry.debit) for entry in space.ledger.entries if entry.account == account),
            "credits": sum(float(entry.credit) for entry in space.ledger.entries if entry.account == account),
        }
        for account in sorted({entry.account for entry in space.ledger.entries})
    }
    if isinstance(gross, (int, float)):
        if posted[receivable]["debits"] != float(gross):
            failures.append(
                f"the receivable account {receivable} carries {posted[receivable]['debits']:.2f} "
                f"of debits against the invoice's own total {float(gross):.2f}"
            )
    if not posted[income]["credits"]:
        failures.append(f"the income account {income} was never credited")

    # the cycle's last family is the one the schemas say commits the ledger
    postings = [document for document in cycle.documents() if document.kind == definitions.accounting_kind]
    if not postings:
        failures.append(
            f"the cycle derived no {definitions.accounting_kind} document from the invoice"
        )

    evidence = {
        "chain": list(chain),
        "links": [link.to_dict() for link in definitions.links],
        "laneDocuments": lane_documents,
        "indexedDeclarations": sorted(indexed),
        "hopDeclarations": hop_declarations,
        "stockKinds": list(definitions.stock_kinds),
        "accountingKind": definitions.accounting_kind,
        "postingRoles": {role: space.policy.accounts.get(role) for role in POSTING_ROLES},
        "documents": summary["documents"],
        "stock": {
            "delivered": delivered.id,
            "deliveredQuantity": declared,
            "moved": moved,
            "balances": balances,
            "movements": space.stock.to_list(),
        },
        "ledger": {
            "totals": {"debits": debits, "credits": credits},
            "byAccount": posted,
            "entries": space.ledger.to_list(),
            "invoiceTotal": gross,
            "derivedPostings": [document.id for document in postings],
        },
        "rail": {"entries": len(space.rail), "head": space.rail.head, "steps": list(cycle.path.steps)},
        "model": {
            "lifecycleKinds": sorted(definitions.model.lifecycle_kinds()),
            "documentKinds": sorted(definitions.model.document_kinds()),
        },
    }
    return Stage(name="cycle", evidence=evidence, failures=tuple(failures))


# --------------------------------------------------------------------------- #
# stage 3 — ERP-08 decides every hop for the owning tenant
# --------------------------------------------------------------------------- #
def stage_scope(cycle: CycleRun, *, tenant: str = DEFAULT_TENANT) -> Stage:
    """Decide a read and a write of every hop, through ERP-08's own seams."""
    from integrations.erp.auth import platform_fixture as fixture
    from integrations.erp.auth import policies as auth_policies
    from integrations.erp.auth import roles as auth_roles
    from integrations.erp.auth.model import Principal, Request
    from integrations.erp.auth.scope import authorize

    failures: List[str] = []
    role_map = auth_roles.load_default()
    policy_set = auth_policies.load_default(kinds=role_map.kinds)

    permissions = sorted(
        {
            role_map.permission_for(kind, action)
            for kind in CYCLE_FAMILIES
            for action in ("read", "write")
        }
    )
    store, _node = fixture.build(tenant=tenant, permissions=permissions)
    principal = Principal(tenant=tenant, subject=fixture.DEFAULT_SUBJECT, roles=("Sales User",))

    hops: List[Dict[str, Any]] = []
    for document in cycle.hop_documents():
        read = authorize(
            role_map,
            policy_set,
            store,
            principal,
            Request(
                tenant=tenant,
                kind=document.kind,
                action="read",
                team=fixture.DEFAULT_TEAM,
                fields=dict(document.body),
            ),
        )
        write = authorize(
            role_map,
            policy_set,
            store,
            principal,
            Request(
                tenant=tenant,
                kind=document.kind,
                action="write",
                team=fixture.DEFAULT_TEAM,
                fields={"state": document.state, "id": document.id},
            ),
        )
        if not read.allowed:
            failures.append(
                f"{document.id}: the owning tenant's read of its own {document.kind} was "
                f"refused ({read.reason}: {read.detail})"
            )
        if not write.allowed:
            failures.append(
                f"{document.id}: the owning tenant's write of its own {document.kind} was "
                f"refused ({write.reason}: {write.detail})"
            )
        accounted = set(read.projection) | set(read.redacted)
        if accounted != set(document.body):
            failures.append(
                f"{document.id}: the projection neither carried nor withheld "
                f"{sorted(set(document.body) - accounted)}"
            )
        hops.append(
            {
                "kind": document.kind,
                "id": document.id,
                "readAllowed": read.allowed,
                "readReason": read.reason,
                "writeAllowed": write.allowed,
                "redacted": list(read.redacted),
                "advisories": list(read.advisories),
                "fieldsProjected": sorted(read.projection),
            }
        )

    # Every hop the acceptance names must be a kind ERP-08 declares. The derived
    # ledger document is the cycle's *effect* rather than a hop — ERP-02 declares it and
    # the ERP-01 catalogue does not, which is why it is outside the role map; anything
    # else outside the role map is a kind the cycle holds and nothing authorizes.
    hops_missing = [kind for kind in CYCLE_FAMILIES if kind not in role_map.kinds]
    if hops_missing:
        failures.append(
            "ERP-08's role map does not cover cycle families: " + ", ".join(hops_missing)
        )
    outside = sorted({document.kind for document in cycle.documents() if document.kind not in role_map.kinds})
    derived = {cycle.definitions.accounting_kind}
    unexpected = [kind for kind in outside if kind not in derived]
    if unexpected:
        failures.append(
            "the cycle holds documents neither the role map nor the derived ledger family "
            "accounts for: " + ", ".join(unexpected)
        )

    evidence = {
        "tenant": tenant,
        "principal": {"subject": principal.subject, "roles": list(principal.roles)},
        "permissionsGranted": permissions,
        "roleMap": {"kinds": list(role_map.kinds), "roles": list(role_map.role_names)},
        "policyKinds": sorted(policy_set.kind_coverage()),
        "hops": hops,
        "kindsOutsideRoleMap": outside,
    }
    return Stage(name="scope", evidence=evidence, failures=tuple(failures))


# --------------------------------------------------------------------------- #
# stage 4 — ERP-09 meters the cycle onto the tenant's ledger
# --------------------------------------------------------------------------- #
def stage_metering(cycle: CycleRun, *, tenant: str = DEFAULT_TENANT) -> Stage:
    """Meter every operation the cycle performed; certify the cost on the chain."""
    from integrations.erp.finops import harness
    from integrations.erp.finops.meter import event_sequence
    from integrations.erp.finops.rollup import ErpRollup

    failures: List[str] = []
    workspace = harness.build_workspace()
    space = cycle.workspace

    uncovered = workspace.meter.coverage()
    if uncovered:
        failures.append(
            "the shipped rate card does not cover the module's own surface: "
            + "; ".join(f"{finding.code}: {finding.detail}" for finding in uncovered)
        )

    events = []
    for index, document in enumerate(cycle.documents()):
        events.append(
            workspace.meter.create(
                document.kind,
                tenant=tenant,
                document_id=document.id,
                actor=METER_ACTOR,
                at=harness.stamp(index),
                document=document.body,
            )
        )
    for offset, (kind, ref, from_state, to_state) in enumerate(_state_moves(space), start=len(events)):
        action = _action_for_move(cycle.definitions, kind, from_state, to_state)
        if action is None:
            failures.append(
                f"{kind} {ref}: ERP-02 declares no single action for {from_state!r} -> {to_state!r}, "
                "so the move the cycle made is not a declared transition"
            )
            continue
        events.append(
            workspace.meter.transition(
                kind,
                tenant=tenant,
                document_id=ref,
                actor=METER_ACTOR,
                from_state=from_state,
                action=action,
                target=to_state,
                at=harness.stamp(offset),
            )
        )

    operations = len(events)
    ledger_count = workspace.audit.count(tenant)
    actions = workspace.audit.actions(tenant)
    verdict = workspace.audit.verify(tenant)
    tail = workspace.audit.tail(tenant)
    if ledger_count != operations:
        failures.append(
            f"{operations} operation(s) were metered but {ledger_count} reached the tenant's "
            "audit ledger"
        )
    if verdict.status != "OK":
        failures.append(f"the tenant's audit chain is {verdict.status}: {verdict.detail}")

    usage_count = workspace.usage.count()
    if usage_count != operations:
        failures.append(f"{operations} operation(s) were metered but {usage_count} reached the usage feed")

    rollup = ErpRollup(workspace.reporter)
    rows = [row.to_dict() for row in rollup.usage()]
    row = rollup.for_tenant(tenant)
    if row is None:
        failures.append(f"the roll-up holds no row for {tenant}, so nothing was metered")
    else:
        if row.operations != operations:
            failures.append(
                f"the roll-up counts {row.operations} operation(s) for {tenant}, not the {operations} metered"
            )
        if not row.fully_metered:
            failures.append(
                f"{row.unmetered_operations} of {row.operations} metered operation(s) carried no price"
            )
        if row.cost_usd <= 0:
            failures.append("the metered cycle cost nothing, so the rate card priced nothing")
        if row.month != harness.stamp(0)[:7]:
            failures.append(f"the roll-up bucketed {tenant} into {row.month}, not the cycle's own month")

    try:
        bill = rollup.bill(tenant)
    except Exception as exc:  # noqa: BLE001 - Refused: the tenant's bill cannot be stated
        failures.append(f"{tenant} has no billable row: {type(exc).__name__}: {exc}")
        bill = None
    try:
        certified = rollup.certify(workspace.audit, tenant)
    except Exception as exc:  # noqa: BLE001 - Refused: the chain did not verify
        failures.append(f"the metered cost could not be certified: {type(exc).__name__}: {exc}")
        certified = None
    if certified is not None and certified != tail:
        failures.append(
            f"the certified tail {certified} is not the chain's own verified tail {tail}"
        )

    decision = workspace.budget.check(tenant, requested_cost_usd=0.0, month=harness.stamp(0)[:7])
    if not decision.allowed:
        failures.append(f"the tenant's own budget refuses the cycle's metering: {decision.decision}")

    evidence = {
        "operations": operations,
        "creates": sum(1 for event in events if event.operation == "create"),
        "transitions": sum(1 for event in events if event.operation == "transition"),
        "ledger": {
            "records": ledger_count,
            "actions": list(actions),
            "sequenceMatchesMeter": tuple(actions) == event_sequence(events),
            "verdict": {"status": verdict.status, "exitCode": verdict.exit_code, "detail": verdict.detail},
            "tail": {"seq": tail[0], "hash": tail[1]},
        },
        "usage": {"records": usage_count},
        "rollup": {"rows": rows, "bill": bill.to_dict() if bill is not None else None},
        "certifiedTail": {"seq": certified[0], "hash": certified[1]} if certified is not None else None,
        "budget": {"declared": workspace.budget.declared(tenant), "decision": str(decision.decision)},
        "events": [event.to_dict() if hasattr(event, "to_dict") else str(event) for event in events],
    }
    return Stage(name="metering", evidence=evidence, failures=tuple(failures))


# --------------------------------------------------------------------------- #
# runner
# --------------------------------------------------------------------------- #
def run_erp_golden_path(
    *,
    work_dir: Optional[str] = None,
    repo_root: Path = REPO_ROOT,
    tenant: str = DEFAULT_TENANT,
    session: Optional[ConsoleSession] = None,
) -> Dict[str, Any]:
    """Run the whole journey and return JSON-serializable evidence."""
    run_dir = Path(work_dir) if work_dir else Path(os.getcwd()) / ".verify" / "e2e-erp"
    session = session or mint_session(tenant=tenant)
    cycle = run_cycle(tenant=tenant)

    stages = (
        stage_provision(run_dir, repo_root=repo_root, session=session),
        stage_cycle(cycle),
        stage_scope(cycle, tenant=tenant),
        stage_metering(cycle, tenant=tenant),
    )
    failures = [f"{stage.name}: {failure}" for stage in stages for failure in stage.failures]
    evidence = {stage.name: stage.evidence for stage in stages}
    payload = {
        "gate": "e2e.erp.golden_path",
        "issue": 655,
        "tenant": tenant,
        "cycle": list(CYCLE_FAMILIES),
        "stages": evidence,
        "failures": failures,
        "passed": not failures,
        "digest": canonical_digest(evidence),
    }
    write_evidence(str(run_dir), "erp-golden-path.json", payload)
    return payload


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    out = None
    if argv and argv[0] == "--out" and len(argv) > 1:
        out = argv[1]
    payload = run_erp_golden_path(work_dir=out)
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    print(f"ERP GOLDEN PATH: {'PASS' if payload['passed'] else 'FAIL'}")
    return 0 if payload["passed"] else 1


if __name__ == "__main__":  # pragma: no cover - a manual entry point
    raise SystemExit(main())
