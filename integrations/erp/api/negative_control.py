"""The negative controls: one provocation per refusal this surface can deliver (#651).

**Why this exists.** A surface tested only on its happy path passes exactly as
green when its authorization has been replaced by ``return True``. So every
refusal is *provoked* here through a real request, and the driver fails when a
provocation is not refused, is refused under the wrong code, is refused with the
wrong status, or is refused without naming the thing it refused.

**Coverage is computed in both directions, and the parts that cannot be reached
say so by name.** Three vocabularies meet at this surface, and each is closed:

* the **surface's own** boundary codes (:data:`~integrations.erp.api.errors.BOUNDARY_CODES`)
  — every one is provoked, and the provoked set must equal it exactly.
* the **ERP-02 model's** refusals (``core.errors.CODES``) — the surface re-raises
  them, so each is either provoked *and shown to arrive unchanged* (same code,
  same status), or declared unreachable with the reason it cannot be reached.
  The union must be the whole vocabulary, so a new model code forces a decision
  here instead of quietly shipping unproven.
* the **ERP-08 layer's** decision reasons (``auth.model.REFUSALS``) — same shape:
  provoked, or declared unreachable with a reason.

A declaration of unreachability is a claim like any other, so each one names the
mechanism that makes it true (a load-time failure, a store invariant, the absence
of a parameter), not a hand-wave.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, TextIO, Tuple

if __package__ in (None, ""):  # executed as a script, not imported as a package
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from integrations.erp.api import errors as err  # noqa: E402
from integrations.erp.api import fixtures, health as health_module  # noqa: E402
from integrations.erp.api.surface import Surface  # noqa: E402
from integrations.erp.auth import contract as auth_contract  # noqa: E402
from integrations.erp.auth.model import REFUSALS, Principal, Refused  # noqa: E402

__all__ = [
    "Provocation",
    "UNREACHABLE_AUTH",
    "UNREACHABLE_MODEL",
    "World",
    "provocations",
    "run",
]

#: The role the surface is driven with unless a provocation says otherwise.
ROLE = "ERP Clerk"

#: The model's refusals this surface cannot deliver, and why. Each reason names a
#: mechanism: a refusal raised while the *model loads* never reaches a request
#: (there is no surface to serve one), and a refusal about a document's state
#: cannot occur because the store only ever holds documents the model validated.
UNREACHABLE_MODEL: Mapping[str, str] = {
    "unknown_state": (
        "a document whose state its workflow does not declare cannot be stored: every "
        "write goes through DocumentModel.validate_document and every transition sets its "
        "state from the workflow, so no stored document can name an unknown state"
    ),
    "state_jumped": (
        "raised only when a caller asserts the destination state (next_state(target=...) "
        "or assert_move); this surface's transition route names the move, never the "
        "destination, so it has no way to assert one — an illegal move is refused a step "
        "earlier as unknown_action"
    ),
    "workflow_invalid": (
        "raised while the model loads its workflow data; a surface whose workflows cannot "
        "be interpreted is never constructed, so no request can receive it"
    ),
    "missing_provenance": (
        "raised while the model loads (provenance.json absent); as above, a model that "
        "cannot load serves nothing"
    ),
    "yaml_unavailable": (
        "raised while the model loads its workflows, when no YAML parser is importable; "
        "as above"
    ),
}

#: The ERP-08 reasons this surface cannot reach, grouped by the mechanism that makes
#: each group unreachable.
UNREACHABLE_AUTH: Mapping[str, Tuple[str, ...]] = {
    "the request's tenant is always the principal's own — there is no parameter, header "
    "or body field that could disagree with it — so the tenant gate cannot fire here": (
        "cross-tenant",
        "tenant-missing",
    ),
    "the principal is built by the adapter that mounts the surface, and the auth model "
    "refuses a malformed one at construction (before any request exists)": (
        "malformed-principal",
    ),
    "the role map's kind vocabulary is the model's own (fixtures derives it), and the "
    "surface resolves the kind against the model first — so an unknown kind is refused as "
    "the model's unknown_document_kind before the auth layer sees it": ("unknown-kind",),
    "the only actions the surface asks for are its route constants and routes.permission_for, "
    "and an unplaced move is refused as unavailable before the auth call": ("unknown-action",),
    "the role map is derived from the model and cannot be empty (roles.load refuses an empty "
    "declaration outright)": ("empty-role-map",),
    "raised by a declaration loader, which runs before any surface exists to serve a request": (
        "declaration-invalid",
        "schema-violation",
        "harvest-code-copied",
        "harvest-incomplete",
        "unknown-effect",
        "unknown-field-policy",
        "unknown-field",
    ),
}


@dataclass
class Expected:
    """What one provocation must produce.

    ``code`` is the *wire* code the client sees. ``reason`` is the ERP-08 layer's
    own decision reason, which rides in ``details.reason`` — separate fields
    because they are answers to different questions, and a coverage check that
    confused them would count a wire code as an auth reason (it did, once).
    """

    code: str
    status: int
    needle: str
    reason: str = ""
    note: str = ""


@dataclass
class Outcome:
    """What one provocation actually produced."""

    name: str
    expected: Expected
    envelope: Mapping[str, Any]

    @property
    def observed_code(self) -> str:
        return str(((self.envelope.get("error") or {}) or {}).get("code", ""))

    @property
    def observed_status(self) -> int:
        return int(self.envelope.get("status") or 0)

    @property
    def observed_message(self) -> str:
        return str(((self.envelope.get("error") or {}) or {}).get("message", "")).lower()

    @property
    def refused(self) -> bool:
        return self.envelope.get("ok") is False

    def problems(self) -> Tuple[str, ...]:
        found: List[str] = []
        if not self.refused:
            found.append(
                f"was NOT refused — it returned {self.observed_status} ok={self.envelope.get('ok')}"
            )
            return tuple(found)
        if self.observed_code != self.expected.code:
            found.append(
                f"was refused as {self.observed_code!r}, not {self.expected.code!r}"
            )
        if self.observed_status != self.expected.status:
            found.append(
                f"was refused with status {self.observed_status}, not {self.expected.status}"
            )
        if self.expected.needle.lower() not in self.observed_message:
            found.append(
                f"the refusal does not name {self.expected.needle!r}: {self.observed_message[:120]!r}"
            )
        return tuple(found)


@dataclass
class Provocation:
    """One refusal, the request that provokes it, and the reply it must earn."""

    name: str
    expected: Expected
    provoke: Callable[[], Mapping[str, Any]]
    origin: str = "surface"


class World:
    """A fresh, offline surface per provocation — no state leaks between them."""

    def __init__(self, *, role: str = ROLE, permissions: Optional[Sequence[str]] = None) -> None:
        self.model = fixtures.model()
        self.declarations = fixtures.declarations(self.model, role=role, permissions=permissions)
        self.documents = fixtures.seeded_store(self.model)
        self.role = role
        self.surface = Surface(
            model=self.model,
            documents=self.documents,
            role_map=self.declarations.role_map,
            policy_set=self.declarations.policy_set,
            rbac_store=self.declarations.rbac_store,
            team=self.declarations.team,
            root=fixtures.repository_root(),
        )

    def principal(
        self,
        roles: Optional[Sequence[str]] = None,
        *,
        tenant: Optional[str] = None,
        subject: Optional[str] = None,
    ) -> Principal:
        return Principal(
            tenant=tenant or self.declarations.tenant,
            subject=subject or fixtures.PLATFORM_SUBJECT,
            roles=tuple(roles) if roles is not None else (self.role,),
        )

    def call(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        principal: Optional[Principal] = None,
        roles: Optional[Sequence[str]] = None,
        tenant: Optional[str] = None,
        subject: Optional[str] = None,
    ) -> Mapping[str, Any]:
        return self.surface.handle(
            method,
            path,
            principal=principal
            if principal is not None
            else self.principal(roles, tenant=tenant, subject=subject),
            body=body,
        )


def _party(document_id: str = "CUST-9000") -> Dict[str, Any]:
    return {
        "doctype": "party",
        "id": document_id,
        "party_type": "customer",
        "name": "Northwind Traders",
        "currency": "USD",
    }


def provocations(base: World) -> Tuple[Provocation, ...]:
    """Every provocation, each against the same pristine world."""
    order = "SALES-ORDER-0001"
    documents = "/v1/erp/documents"

    # (A) the surface's own boundary codes — every one, no exceptions.
    boundary: Tuple[Provocation, ...] = (
        Provocation(
            "route-unknown",
            Expected("not_found", 404, "no route for GET"),
            lambda: base.call("GET", "/v1/erp/sprockets"),
        ),
        Provocation(
            "method-not-allowed",
            Expected("method_not_allowed", 405, "not allowed on this path"),
            lambda: base.call("PATCH", f"{documents}/party"),
        ),
        Provocation(
            "principal-absent",
            Expected("unauthorized", 401, "carries no principal"),
            lambda: base.surface.handle("GET", f"{documents}/party"),
        ),
        Provocation(
            "document-absent",
            Expected("document_not_found", 404, "no 'sales-order' document"),
            lambda: base.call("GET", f"{documents}/sales-order/SALES-ORDER-4242"),
        ),
        Provocation(
            "create-conflict",
            Expected("conflict", 409, "already exists"),
            lambda: base.call("POST", f"{documents}/item", fixtures.documents()["item"][0]),
        ),
        Provocation(
            "authorization-denied",
            Expected("forbidden", 403, "permission-denied"),
            lambda: base.call("POST", f"{documents}/party", _party("CUST-9001"), roles=("ERP Auditor",)),
        ),
        Provocation(
            "field-write-denied",
            Expected("forbidden", 403, "field-write-denied"),
            lambda: base.call(
                "PUT",
                f"{documents}/sales-order/{order}",
                {**fixtures.documents()["sales-order"][0], "total": 240.0},
            ),
        ),
        Provocation(
            "internal-failure",
            Expected("internal", 500, "failed while handling"),
            lambda: _with_broken_model(base).handle(
                "GET", f"{documents}/party", principal=base.principal()
            ),
        ),
    )

    # (B) the model's refusals the surface can reach — asserted to arrive UNCHANGED.
    def _model(code: str, needle: str, provoke: Callable[[], Mapping[str, Any]]) -> Provocation:
        return Provocation(
            f"model-{code}",
            Expected(code, err.MODEL_STATUS[code], needle),
            provoke,
            origin="model",
        )

    model: Tuple[Provocation, ...] = (
        _model(
            "invalid_body",
            "must be an object",
            lambda: base.call("POST", f"{documents}/party", [1, 2, 3]),
        ),
        _model(
            "schema_violation",
            "schema violation",
            lambda: base.call("POST", f"{documents}/party", {"doctype": "party", "id": "CUST-9002"}),
        ),
        _model(
            "unknown_document_kind",
            "unknown document kind",
            lambda: base.call("GET", f"{documents}/sprocket"),
        ),
        _model(
            "unknown_workflow",
            "no workflow for",
            lambda: base.call("POST", f"{documents}/party/CUST-0001/transitions/submit"),
        ),
        _model(
            "unknown_action",
            "has no action",
            lambda: base.call("POST", f"{documents}/sales-order/{order}/transitions/complete"),
        ),
        _model(
            "unbalanced_posting",
            "debits",
            lambda: base.call(
                "POST",
                f"{documents}/gl-posting",
                {
                    **fixtures.documents()["gl-posting"][0],
                    "id": "GL-POSTING-9001",
                    "lines": [
                        {"account": "DEBTORS", "debit": 100.0},
                        {"account": "REVENUE", "credit": 60.0},
                    ],
                },
            ),
        ),
        _model(
            "invalid_transfer",
            "material_transfer",
            lambda: base.call(
                "POST",
                f"{documents}/stock-entry",
                {
                    **fixtures.documents()["stock-entry"][0],
                    "id": "STOCK-ENTRY-9001",
                    "purpose": "material_transfer",
                    "from_warehouse": "MAIN",
                    "to_warehouse": "MAIN",
                },
            ),
        ),
    )

    # (C) the ERP-08 reasons the surface can reach.
    def _auth(name: str, reason: str, provoke: Callable[[], Mapping[str, Any]]) -> Provocation:
        status, code = err.AUTH_STATUS[reason]
        return Provocation(
            f"auth-{name}",
            Expected(code, status, reason, reason=reason),
            provoke,
            origin="auth",
        )

    auth: Tuple[Provocation, ...] = (
        _auth(
            "permission-denied",
            "permission-denied",
            lambda: World(role=ROLE, permissions=("erp.party:read",)).call(
                "POST", f"{documents}/party", _party("CUST-9003")
            ),
        ),
        _auth(
            "scope-denied",
            "scope-denied",
            lambda: base.call("GET", f"{documents}/party", subject="unbound-subject"),
        ),
        _auth(
            "unknown-role",
            "unknown-role",
            lambda: base.call("GET", f"{documents}/party", roles=("Nonexistent Role",)),
        ),
        _auth(
            "field-write-denied-reason",
            "field-write-denied",
            lambda: base.call(
                "PUT",
                f"{documents}/sales-order/{order}",
                {**fixtures.documents()["sales-order"][0], "total": 240.0},
            ),
        ),
        _auth(
            "contract-unavailable",
            "contract-unavailable",
            lambda: _with_unreadable_contract(base),
        ),
    )

    # (D) the honesty of the health read: a report that lies must be refused.
    honesty = (
        Provocation(
            "health-lying-ok",
            Expected("health-lying-ok", 0, "ok while"),
            lambda: _lying_health_envelope(base),
            origin="control",
        ),
    )
    return boundary + model + auth + honesty


def _with_broken_model(base: World) -> Surface:
    """A surface whose model raises something that is not a refusal."""

    class _Boom:
        def schema_for(self, kind: str) -> Any:
            raise RuntimeError("the model fell over")

    broken = Surface(
        model=_Boom(),
        documents=base.documents,
        role_map=base.declarations.role_map,
        policy_set=base.declarations.policy_set,
        rbac_store=base.declarations.rbac_store,
        team=base.declarations.team,
        root=fixtures.repository_root(),
    )
    return broken


class _UnreadableContract:
    """A stand-in for ``integrations.erp.auth.contract`` whose rbac is unreadable."""

    def __getattr__(self, name: str) -> Any:
        if name == "rbac":
            def rbac() -> Any:
                raise Refused("contract-unavailable", "provoked: identity/rbac is unreadable")

            return rbac
        return getattr(auth_contract, name)


def _with_unreadable_contract(base: World) -> Mapping[str, Any]:
    """Drive one request with the consumed contract unreadable, then restore it.

    The patch has to be *in force while the request runs*, which is why this
    returns the reply rather than a prepared surface: a surface handed back here
    would be used after the ``finally`` restored the contract, and the control
    would then measure a healthy system and report nothing (it did, once).
    """
    original = auth_contract.rbac
    auth_contract.rbac = _UnreadableContract().rbac  # type: ignore[assignment]
    try:
        return base.surface.handle(
            "GET", "/v1/erp/documents/party", principal=base.principal()
        )
    finally:
        auth_contract.rbac = original  # type: ignore[assignment]


def _lying_health_envelope(base: World) -> Mapping[str, Any]:
    """A health report that calls every dependency ok while a probe says otherwise.

    Three probes are supplied — one per dependency the report names — because
    ``check_report`` also refuses a report that names a dependency no probe reads,
    and that (correct) refusal would otherwise be the first finding and hide the
    one this control is about. The report is *honest about everything except*
    ``auth-declarations``, which the probes freshly read as missing.
    """
    def state(name: str, value: str, detail: str) -> health_module.DependencyState:
        return health_module.DependencyState(name, value, detail)

    probes = (
        lambda *_args: state("erp-02-model", health_module.STATE_OK, "readable"),
        lambda *_args: state("auth-declarations", health_module.STATE_MISSING, "provoked absent"),
        lambda *_args: state("openapi-artifact", health_module.STATE_OK, "byte-identical"),
    )
    report = health_module.HealthReport(
        status=health_module.STATUS_OK,
        http_status=200,
        dependencies=(
            state("erp-02-model", health_module.STATE_OK, "claimed ok"),
            state("auth-declarations", health_module.STATE_OK, "claimed ok"),
            state("openapi-artifact", health_module.STATE_OK, "claimed ok"),
        ),
    )
    findings = health_module.check_report(
        report,
        fixtures.repository_root(),
        model=base.model,
        role_map=base.declarations.role_map,
        probes=probes,
    )
    first = findings[0] if findings else "no finding"
    return {
        "ok": False,
        "status": 0,
        "requestId": "control",
        "data": None,
        "error": {"code": "health-lying-ok", "message": first, "details": None},
    }


def _coverage(vocabulary: Mapping[str, Any], provoked: Mapping[str, str], declared: Mapping[str, str], what: str) -> List[str]:
    """Every way ``provoked`` and ``declared`` fail to account for ``vocabulary``."""
    problems: List[str] = []
    accounted = dict(provoked)
    accounted.update(declared)
    for code in sorted(vocabulary):
        if code not in accounted:
            problems.append(
                f"the {what} refusal {code!r} is neither provoked nor declared unreachable — "
                "a refusal nobody demonstrates is a formality"
            )
    for code in sorted(set(accounted) - set(vocabulary)):
        problems.append(f"{code!r} is accounted for as a {what} refusal, but that vocabulary has no such code")
    for code in sorted(set(provoked) & set(declared)):
        problems.append(f"{code!r} is both provoked and declared unreachable")
    return problems


def run(sink: TextIO = sys.stdout) -> int:
    """Provoke every refusal; return 0 only when each is refused by name."""
    base = World()
    outcomes: List[Tuple[Provocation, Outcome]] = []
    for provocation in provocations(base):
        envelope = provocation.provoke()
        outcomes.append((provocation, Outcome(provocation.name, provocation.expected, envelope)))

    problems: List[str] = []
    for provocation, outcome in outcomes:
        found = outcome.problems()
        if found:
            problems.extend(f"{provocation.name}: {problem}" for problem in found)
        else:
            print(
                f"  refused -> {provocation.name} ({outcome.observed_status} {outcome.observed_code}): "
                f"{outcome.observed_message[:96]}",
                file=sink,
            )

    # Coverage, computed in both directions against each closed vocabulary. The
    # surface's own codes are provoked by the boundary provocations *and* by the
    # auth ones (a denied decision is the surface's `forbidden`, an unreadable
    # contract its `unavailable`), so the wire codes are collected across both.
    from_boundary = {
        p.expected.code: p.name
        for p, _o in outcomes
        if p.origin in ("surface", "auth") and p.expected.code in set(err.BOUNDARY_CODES)
    }
    provoked_model = {p.expected.code: p.name for p, _o in outcomes if p.origin == "model"}
    provoked_auth = {
        p.expected.reason: p.name
        for p, _o in outcomes
        if p.origin == "auth" and p.expected.reason
    }

    problems.extend(_coverage(err.BOUNDARY_CODES, from_boundary, {}, "surface"))
    problems.extend(_coverage(err.MODEL_STATUS, provoked_model, UNREACHABLE_MODEL, "ERP-02 model"))
    problems.extend(
        _coverage(
            {code: code for code in REFUSALS},
            provoked_auth,
            {code: reason for reason, codes in UNREACHABLE_AUTH.items() for code in codes},
            "ERP-08",
        )
    )

    if problems:
        print(f"negative-control: NOT-OK — {len(problems)} problem(s)", file=sys.stderr)
        for problem in problems:
            print(f"  FAIL  {problem}", file=sys.stderr)
        return 1

    print(
        f"negative-control: OK — {len(from_boundary)} boundary refusal(s), "
        f"{len(provoked_model)} model refusal(s) arriving unchanged, {len(provoked_auth)} "
        f"ERP-08 reason(s) all refused by name; {len(UNREACHABLE_MODEL)} model and "
        f"{sum(len(v) for v in UNREACHABLE_AUTH.values())} ERP-08 reason(s) declared unreachable "
        "with the mechanism that makes them so",
        file=sink,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
