"""The enforcement core: scope gate, then permission gate, plus sessions.

This module is the guard every tool/API call must pass through before acting
(the enforcement core; real HTTP middleware arrives in later phases). It
composes the two gates from resolve.py in the only order that is safe:

    1. scope gate     - ``resolve_scope``: is the requested Org/Team/Agent node
                        inside the subject's reachable org tree?
    2. permission gate - ``authorize``: does the requested ``resource:action``
                        hold within that resolved scope?

A ``Decision`` records which gate denied. A principal with a permission can
never apply it outside its resolved scope: when scope fails, the scope gate
returns a denial with ``reason == "scope"`` and the permission gate is never
reached - there is no cross-tenant or cross-team fallback.

## The middleware contract

Every tool/API call in a later phase (state-machine engine, model gateway,
guardrail middleware) that acts on a resource on behalf of a principal or a
session MUST:

1. Call ``guard`` (single permission) or ``guard_required`` (several, mode
   all/any) for one-shot checks, or ``guard_session`` for a call made inside an
   agent session, BEFORE performing the action. No action happens unguarded.
2. Treat a denial (``decision.allowed is False``) as an unconditional stop.
   A later HTTP layer maps it to 403 with a body carrying the Decision fields
   (see README.md), never to a fallback that re-tries in another scope.
3. Never skip the scope gate. A scope-only caller (reason ``"scope"``) must not
   be confused with a permission-only caller (reason ``"permission"``): the
   fix for one is not the fix for the other.
4. Emit the denial through ``authorization_denied_payload`` so observability
   (phase 5, issues #31-#34) can count denials. The reserved fields - event,
   orgId (tenantId), permission - mirror the cannibalized saas-rbac
   ``rbac/authorization_denials`` metric contract and must not be renamed.

## Sessions

``start_agent_session`` mints a session only after an authorized principal ran
an agent (``agent:run`` at the agent's node). The session carries the role keys
resolved at start time - a snapshot for audit and context. Enforcement never
trusts the snapshot: ``guard_session`` re-resolves live bindings from the store
on every call, so a revocation takes effect immediately (the dangerous failure
mode of a cache - a revoked subject keeping access - produces fewer denials
and is invisible to monitoring, so there is deliberately no caching here).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from rbac.model import (
    PERMISSIONS,
    ScopeNode,
    Session,
)
from rbac.resolve import authorize, resolve_scope

# Reserved denial-log fields (observability contract, see module docstring).
AUTHORIZATION_DENIED_EVENT = "authorization_denied"
# Scalar used in the ``permission`` field of a denial whose cause is scope
# rather than a permission - names the real cause so a denial spike can be
# attributed (mirrors saas-rbac's ``tenant:scope`` pseudo-permission).
SCOPE_DENIAL_PERMISSION = "scope:resolve"


class UnknownAgentError(ValueError):
    """No agent with the requested id exists."""

    def __init__(self, agent_id: str) -> None:
        super().__init__(f"no agent with id {agent_id!r} exists")
        self.agent_id = agent_id


class ScopeDeniedError(RuntimeError):
    """A provisioning action was refused because the subject is out of scope."""

    def __init__(self, subject: str, node: ScopeNode, code: str) -> None:
        super().__init__(
            f"subject {subject!r} is not in scope for node {node}: {code}"
        )
        self.subject = subject
        self.node = node
        self.code = code


class PermissionDeniedError(RuntimeError):
    """A provisioning action was refused by the permission gate."""

    def __init__(self, subject: str, node: ScopeNode, permission: str) -> None:
        super().__init__(
            f"subject {subject!r} lacks {permission!r} at node {node}"
        )
        self.subject = subject
        self.node = node
        self.permission = permission


@dataclass(frozen=True)
class Decision:
    """Outcome of one guard check: allowed, or denied by exactly one gate.

    ``reason`` is ``"scope"`` when the scope gate denied (the subject cannot
    act in the requested node at all), ``"permission"`` when the scope gate
    passed but the permission did not, and None when allowed.
    """

    allowed: bool
    subject: str
    node: ScopeNode
    permission: str
    reason: str | None = None
    code: str | None = None
    required_permissions: tuple[str, ...] = ()
    missing_permissions: tuple[str, ...] = ()

    @property
    def denied(self) -> bool:
        return not self.allowed


def _denied(
    subject: str,
    node: ScopeNode,
    required: list[str],
    reason: str,
    code: str,
    missing: list[str] | None = None,
) -> Decision:
    return Decision(
        allowed=False,
        subject=subject,
        node=node,
        permission=required[0],
        reason=reason,
        code=code,
        required_permissions=tuple(required),
        missing_permissions=tuple(missing if missing is not None else []),
    )


def _allowed(subject: str, node: ScopeNode, required: list[str]) -> Decision:
    return Decision(
        allowed=True,
        subject=subject,
        node=node,
        permission=required[0],
        required_permissions=tuple(required),
    )


def guard(store, subject: str, node: ScopeNode, permission: str) -> Decision:
    """Require a single permission at ``node`` - the scope gate, then the gate.

    The combined two-gate check every tool/API call runs. Deny-by-default: any
    failure - out of scope, no binding, malformed permission, missing grant -
    is a denial, never an exception.
    """
    return guard_required(store, subject, node, [permission], mode="all")


def guard_required(
    store,
    subject: str,
    node: ScopeNode,
    permissions: list[str],
    *,
    mode: str = "all",
) -> Decision:
    """Require several permissions at ``node``.

    ``mode`` ``"all"`` (default, the conservative reading) requires every
    permission; ``"any"`` grants when at least one holds. A guard that
    requires nothing is almost certainly a mistake, so an empty set is
    rejected.
    """
    required = list(permissions)
    if not required:
        raise ValueError("guard_required: at least one permission is required")
    if mode not in ("all", "any"):
        raise ValueError(f"guard_required: unknown mode {mode!r}")

    # Gate 1: scope. The permission gate is never reached when the subject
    # cannot act in this node at all - no cross-tenant, cross-team fallback.
    resolution = resolve_scope(store, subject, node)
    if not resolution.ok:
        return _denied(subject, node, required, reason="scope", code=resolution.reason)

    # Gate 2: permission within the resolved scope.
    missing = [p for p in required if not authorize(store, subject, resolution, p)]
    if mode == "any":
        denied = len(missing) == len(required)
    else:
        denied = bool(missing)
    if denied:
        return _denied(
            subject,
            node,
            required,
            reason="permission",
            code="denied",
            missing=missing,
        )
    return _allowed(subject, node, required)


def start_agent_session(
    store,
    subject: str,
    agent_id: str,
    *,
    subject_type: str = "user",
) -> Session:
    """Mint a session running ``agent_id`` on behalf of ``subject``.

    Refuses (raises) unless the subject is in scope for the agent's node AND
    holds ``agent:run`` there. The returned session carries the resolved role
    keys as a snapshot; enforcement re-checks live bindings on every call.
    """
    agent = store.agent(agent_id)
    if agent is None:
        raise UnknownAgentError(agent_id)

    node = ScopeNode(org_id=agent.org_id, team_id=agent.team_id, agent_id=agent.id)
    resolution = resolve_scope(store, subject, node)
    if not resolution.ok:
        raise ScopeDeniedError(subject, node, resolution.reason)

    run_permission = PERMISSIONS["AGENT_RUN"]
    if not authorize(store, subject, resolution, run_permission):
        raise PermissionDeniedError(subject, node, run_permission)

    role_keys: list[str] = []
    for role_id in resolution.role_ids:
        role = store.role_by_id(role_id)
        if role is not None and role.org_id == node.org_id:
            role_keys.append(role.key)
    role_keys = sorted(set(role_keys))

    session = Session(
        id=f"session_{uuid4().hex[:12]}",
        org_id=node.org_id,
        team_id=node.team_id or "",
        agent_id=node.agent_id or "",
        subject_id=subject,
        subject_type=subject_type,
        roles=tuple(role_keys),
        started_at=datetime.now(timezone.utc).isoformat(),
    )
    return session


def guard_session(
    store,
    session: Session,
    permission: str,
    *,
    permissions: list[str] | None = None,
    mode: str = "all",
) -> Decision:
    """Guard a tool/API call made inside an agent session.

    Re-resolves the session subject's live bindings at the session's node, so
    a revocation between calls takes effect immediately. The roles the session
    carries are context for audit, not a grant in themselves.
    """
    node = ScopeNode(
        org_id=session.org_id,
        team_id=session.team_id or None,
        agent_id=session.agent_id or None,
    )
    required = (
        [permission] if permissions is None else list(permissions)
    )
    return guard_required(store, session.subject_id, node, required, mode=mode)


def authorization_denied_payload(decision: Decision) -> dict[str, object]:
    """Reserved payload for a denial's observability record.

    ``event``, ``orgId`` and ``permission`` mirror the saas-rbac
    ``rbac/authorization_denials`` metric contract and must not be renamed;
    everything else is additive context. For a scope denial the ``permission``
    field is the scalar ``SCOPE_DENIAL_PERMISSION`` so the cause is named.
    """
    if not decision.denied:
        raise ValueError("cannot build a denial payload for an allowed decision")
    payload: dict[str, object] = {
        "event": AUTHORIZATION_DENIED_EVENT,
        "orgId": decision.node.org_id,
        "tenantId": decision.node.org_id,
        "permission": (
            SCOPE_DENIAL_PERMISSION if decision.reason == "scope" else decision.permission
        ),
        "subject": decision.subject,
        "reason": decision.reason,
        "code": decision.code,
        "requiredPermissions": list(decision.required_permissions),
        "missingPermissions": list(decision.missing_permissions),
    }
    return payload
