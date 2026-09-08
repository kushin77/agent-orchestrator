"""Scope resolution and permission checks - the two gates.

This module is where the platform's central RBAC doctrine lives: **the scope
gate is separate from the permission gate** (the cross-tenant read incident
doctrine, saas-rbac #107). Two distinct questions, two distinct functions:

1. ``resolve_scope`` - *is this node in the subject's org tree at all?* It
   answers which Org/Team/Agent scope a principal is entitled to act within,
   resolving bindings down the Org -> Team -> Agent tree (org-wide grants cover
   every node of the Org; a team grant covers that team and its agents). It
   never looks outside the requested Org, so a principal with no binding there
   is out of scope no matter what its role strings would grant.

2. ``authorize`` - *may this principal do this thing here?* It checks a
   ``resource:action`` permission against the union of the roles that cover an
   already-resolved scope.

The two are composed by ``guard`` (guard.py) which runs the scope gate first
and only reaches the permission gate when scope resolved - a principal with a
permission can never apply it outside its resolved scope, because the scope
gate denies first and there is no cross-tenant (or cross-team) fallback.

Resolution never throws for a deny: a principal with no bindings, an
unrecognized scope, or a malformed permission resolves to a denial so callers
deny by default.
"""

from __future__ import annotations

from dataclasses import dataclass

from rbac.model import ScopeNode, is_permission, permission_granted


@dataclass(frozen=True)
class ScopeResolution:
    """Outcome of the scope gate (``resolve_scope``).

    ``ok`` is True when the requested node is inside the subject's reachable
    org tree; ``role_ids`` then names every role covering that node (the
    permission gate unions their grants). ``code`` is the machine reason when
    ``ok`` is False (unknown org/team/agent, or out of scope).
    """

    ok: bool
    node: ScopeNode
    role_ids: tuple[str, ...] = ()
    code: str | None = None

    @property
    def reason(self) -> str | None:
        """Normalized denial reason: ``"scope"`` always for the scope gate."""
        return None if self.ok else (self.code or "out_of_scope")


def resolve_scope(store, subject: str, node: ScopeNode) -> ScopeResolution:
    """Scope gate: resolve whether ``subject`` may act within ``node``.

    Coverage rules (strictly inside the requested Org):

    - An org-wide binding covers every node of the Org, including org-level
      nodes (managing the Org itself) and every team and agent beneath it.
    - A team-scoped binding covers that team's team-level node and the agents
      under it. It never covers another team, an org-level node, or another
      Org.

    Any requested scope the subject cannot reach - an Org it holds no binding
    in, a team it is not granted - resolves to a denial, regardless of the
    permissions its role strings would otherwise carry.
    """
    org = store.org(node.org_id)
    if org is None:
        return ScopeResolution(ok=False, node=node, code="unknown_org")

    if node.team_id is not None:
        team = store.team(node.team_id)
        if team is None or team.org_id != node.org_id:
            return ScopeResolution(ok=False, node=node, code="unknown_team")

    if node.agent_id is not None:
        agent = store.agent(node.agent_id)
        if (
            agent is None
            or agent.org_id != node.org_id
            or agent.team_id != node.team_id
        ):
            return ScopeResolution(ok=False, node=node, code="unknown_agent")

    covering: set[str] = set()
    for binding in store.bindings_for_subject(node.org_id, subject):
        if binding.team_id is None:
            # Org-wide grant: reaches every node of the Org.
            covering.add(binding.role_id)
        elif binding.team_id == node.team_id:
            # Team grant: reaches the team and the agents under it. An
            # org-level node has team_id None, so this branch never grants an
            # org-level node through a team-scoped binding.
            covering.add(binding.role_id)
        # A grant for a different team does not cover this node.

    if not covering:
        return ScopeResolution(ok=False, node=node, code="out_of_scope")

    return ScopeResolution(ok=True, node=node, role_ids=tuple(sorted(covering)))


def effective_permissions(store, subject: str, node: ScopeNode) -> frozenset[str]:
    """Union of every permission the subject holds at ``node``.

    Runs the scope gate first; an out-of-scope subject has the empty set.
    """
    resolution = resolve_scope(store, subject, node)
    if not resolution.ok:
        return frozenset()
    return _permissions_for_resolution(store, resolution)


def authorize(store, subject: str, resolution: ScopeResolution, permission: str) -> bool:
    """Permission gate: does ``permission`` hold within an already-resolved scope?

    Deliberately takes a successful ``ScopeResolution`` rather than a bare
    node: a permission is only ever checked *inside* a scope the subject has
    already been granted, never as a standalone cross-org predicate. Passing an
    unsuccessful resolution (or a malformed permission) is a deny - never an
    exception.
    """
    if not resolution.ok:
        return False
    if not is_permission(permission):
        return False
    granted = _permissions_for_resolution(store, resolution)
    return any(permission_granted(g, permission) for g in granted)


def _permissions_for_resolution(store, resolution: ScopeResolution) -> frozenset[str]:
    """Union of the grants of every role covering a resolved scope."""
    permissions: set[str] = set()
    for role_id in resolution.role_ids:
        role = store.role_by_id(role_id)
        if role is not None and role.org_id == resolution.node.org_id:
            permissions.update(role.permissions)
    return frozenset(permissions)
