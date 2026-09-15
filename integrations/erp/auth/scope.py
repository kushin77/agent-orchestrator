"""The scope middleware: tenant isolation, the platform's two gates, and fields.

This is the module's decision point, and the order of its checks is the design.

**1. The tenant gate runs first, and nothing can influence it.** ``Request.tenant``
is a claim; ``Principal.tenant`` is the authority. They are compared before any
role, policy or contract is consulted, and a mismatch returns
``cross-tenant``. There is deliberately no parameter to this function that turns
the gate off, and no declaration in either catalogue can express "allowed across
tenants" — the tenant-local action vocabulary in :mod:`model` has no entry that
could mean it. That is the difference between a rule and an invariant, and the
negative controls drive it from both directions (a foreign tenant's request is
refused *even for a principal whose roles grant everything*).

**2. ERPNext's model is a translation, not a second authorization system.**
The principal holds ERP *role names*. :mod:`roles` maps each to the platform's
``resource:action`` permission language, and this module then asks the consumed
``identity/rbac`` contract to decide — its scope gate first, then its permission
gate, in that safe order. The consequence is the property the epic needs: **a
role map cannot grant anything the platform does not grant.** A principal whose
ERP role claims ``read`` on a kind but whose platform binding lacks the
translated permission is refused by the platform, and there is no back door
because this module has no allow path that skips the contract.

**3. Fields are withheld by omission.** A field whose policy denies ``read`` is
absent from the projection, never blanked; the withheld names are returned so
the withholding is auditable. A write carrying a field whose policy denies
``write`` is refused, naming the rule.

**What raises versus what decides.** A declaration the middleware cannot read
(an unreadable contract, an invalid catalogue) raises :class:`Refused` and is
CANNOT-ASSESS — refusing to *decide* is not the same answer as deciding "no", and
collapsing them is the defect the tri-state contract exists to prevent. A request
that names an unknown kind, action or role is *decided* — the answer is denial
with a reason, because the request is well-formed enough to answer.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Tuple

from . import contract, policies as policies_module
from .model import Decision, Principal, Refused, Request, decision_from_refusal
from .roles import RoleMap


def _node(principal: Principal, request: Request) -> Any:
    """The scope node to resolve — the org **always** comes from the principal.

    ``request.tenant`` is never consulted here. It has already been checked
    against the authority, and building the node from the claim would make the
    gate above decorative on the very path it protects.
    """
    model = contract.rbac().model
    return model.ScopeNode(
        org_id=principal.tenant, team_id=request.team, agent_id=principal.subject
    )


def authorize(
    role_map: RoleMap,
    policy_set: policies_module.FieldPolicySet,
    store: Any,
    principal: Principal,
    request: Request,
) -> Decision:
    """Decide one request. Never raises for an authorization outcome.

    Returns :class:`Decision`; raises :class:`Refused` only when the
    *declarations* cannot be read, so a caller can tell "denied" from
    "un-assessable".
    """
    # --- 1. the tenant gate. Before anything else, and un-overridable. ------
    if not isinstance(request.tenant, str) or not request.tenant:
        return Decision(allowed=False, reason="tenant-missing", detail="the request names no tenant")
    if request.tenant != principal.tenant:
        return Decision(
            allowed=False,
            reason="cross-tenant",
            detail=(
                f"request claims tenant {request.tenant!r} but the principal is "
                f"{principal.tenant!r} — no role or policy can widen a principal's tenant"
            ),
        )

    # --- 2. the ERP role map translates the principal's roles. --------------
    try:
        granted_locally = role_map.granted(principal.roles, request.kind, request.action)
    except Refused as refusal:
        # An unknown role / kind / action is answerable: deny, and say which.
        return decision_from_refusal(refusal)

    if not granted_locally:
        return Decision(
            allowed=False,
            reason="permission-denied",
            detail=(
                f"none of the principal's ERP roles ({', '.join(sorted(principal.roles)) or 'none'}) "
                f"grants {request.action!r} on {request.kind!r} "
                f"(required permission: {role_map.permission_for(request.kind, request.action)})"
            ),
        )

    # --- 3. the platform's two gates, in the contract's safe order. ---------
    rbac = contract.rbac()
    permission = role_map.permission_for(request.kind, request.action)
    node = _node(principal, request)
    contract_decision = rbac.guard(store, principal.subject, node, permission)
    if not contract_decision.allowed:
        reason = "scope-denied" if contract_decision.reason == "scope" else "permission-denied"
        missing = getattr(contract_decision, "missing_permissions", None)
        return Decision(
            allowed=False,
            reason=reason,
            detail=(
                f"identity/rbac refused {permission!r} ({contract_decision.reason}: "
                f"{contract_decision.code}"
                + (f"; missing {', '.join(missing)}" if missing else "")
                + ")"
            ),
        )

    # --- 4. field-level policy ---------------------------------------------
    projection, redacted, advisories = _project(policy_set, principal, request)
    if request.action != "read":
        denied = _write_denial(policy_set, principal, request)
        if denied is not None:
            return Decision(allowed=False, reason="field-write-denied", detail=denied, advisories=advisories)

    return Decision(
        allowed=True,
        projection=projection,
        redacted=redacted,
        advisories=advisories,
    )


def _project(
    policy_set: policies_module.FieldPolicySet, principal: Principal, request: Request
) -> Tuple[Mapping[str, Any], Tuple[str, ...], Tuple[str, ...]]:
    """The visible field set, the withheld names, and the advisories that fired."""
    visible: dict[str, Any] = {}
    redacted: list[str] = []
    advisories: list[str] = []
    for name, value in request.fields.items():
        rule = policy_set.read_denied(request.kind, name, principal.roles)
        advisories.extend(policy_set.advisories(request.kind, name, principal.roles))
        if rule is not None:
            redacted.append(name)
            continue
        visible[name] = value
    return visible, tuple(sorted(redacted)), tuple(sorted(set(advisories)))


def _write_denial(
    policy_set: policies_module.FieldPolicySet, principal: Principal, request: Request
) -> Optional[str]:
    """A message naming the first field whose policy denies the write, if any."""
    for name in sorted(request.fields):
        rule = policy_set.write_denied(request.kind, name, principal.roles)
        if rule is not None:
            return (
                f"field {name!r} is not writable for this principal "
                f"(rule {rule.id!r}: {rule.reason})"
            )
    return None
