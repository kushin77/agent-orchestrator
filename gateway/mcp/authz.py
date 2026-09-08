"""Authorization seam: the rbac scope gate, consumed via an injected guard.

The gateway never reimplements roles, permissions or the two-gate flow - it
consumes the identity/rbac engine (issue #12) through a narrow
:class:`PermissionGuard` interface. ``RbacScopeGuard`` adapts the real
``rbac.guard.guard_session`` semantics so a deployment can drop in an
identity/rbac ``store`` and every MCP tool call is gated scope-first then
permission-first, exactly as the rbac middleware contract requires:

1. scope gate - is the session's subject in scope for the requested tenant at
   all? A subject with no binding in that tenant is out of scope no matter
   what its role strings grant;
2. permission gate - within that scope, does the union of roles grant the
   requested ``resource:action``?

A denial keeps its cause (``scope`` vs ``permission``) so the transport can
map it and observability can attribute it; there is deliberately no
cross-tenant or cross-team fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

from .errors import GatewayError
from .model import SessionIdentity

# The permission the gateway requires for a tool call (rbac middleware
# contract: every tool/API call passes ``guard_session(store, session,
# "tool:call")`` before acting).
TOOL_CALL_PERMISSION = "tool:call"


@dataclass(frozen=True)
class AuthzDecision:
    """Outcome of one authorization check, mirroring the rbac ``Decision``."""

    allowed: bool
    permission: str
    reason: Optional[str] = None  # "scope" | "permission" | None when allowed
    code: Optional[str] = None  # rbac scope code or "denied"

    @property
    def denied(self) -> bool:
        return not self.allowed


class PermissionGuard(Protocol):
    """Injected authorization core; the gateway depends only on this shape."""

    def authorize(self, session: SessionIdentity, permission: str) -> AuthzDecision:
        """Return whether ``session`` may perform ``permission`` in its tenant."""
        ...


class RbacScopeGuard:
    """Adapts an identity/rbac ``store`` to :class:`PermissionGuard`.

    The identity/rbac package is imported lazily (identity/ is not on
    ``sys.path`` unless a consumer arranges it, as the rbac tests do), so this
    class can be constructed without identity/rbac importable. The first
    ``authorize`` call resolves ``rbac``; if identity/ is not importable the
    call fails closed with a descriptive error rather than granting anything.
    """

    def __init__(self, store) -> None:
        self._store = store
        self._rbac = None

    def _rbac_module(self):
        if self._rbac is not None:
            return self._rbac
        # importlib.import_module returns the submodule even when the rbac
        # package's __init__ re-exports a name (``guard``) that shadows the
        # submodule attribute on the package object.
        try:
            import importlib

            guard_module = importlib.import_module("rbac.guard")
            model = importlib.import_module("rbac.model")
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise GatewayError(
                "identity/rbac is not importable: add the identity/ directory "
                "to sys.path to consume the rbac scope gate (fail closed)"
            ) from exc
        self._rbac = (guard_module.guard_session, model)
        return self._rbac

    def authorize(self, session: SessionIdentity, permission: str) -> AuthzDecision:
        guard_session, model = self._rbac_module()
        # An agent that does not exist in the session's tenant has no resolvable
        # node (rbac requires an agent-level node to name its team) - deny at
        # the scope gate (fail closed) rather than inventing a node.
        agent = self._store.agent(session.agent_id) if hasattr(self._store, "agent") else None
        if agent is None or getattr(agent, "org_id", None) != session.tenant_id:
            return AuthzDecision(
                allowed=False,
                permission=permission,
                reason="scope",
                code="unknown_agent",
            )
        # Mirror start_agent_session node construction: the agent's own org +
        # team. guard_session re-resolves live bindings on every call
        # (immediate revocation).
        rbac_session = model.Session(
            id=f"mcp_{session.token_id or 'anon'}",
            org_id=session.tenant_id,
            team_id=agent.team_id or "",
            agent_id=session.agent_id,
            subject_id=session.subject,
            roles=tuple(sorted({session.role})) if session.role else (),
            started_at="",
        )
        decision = guard_session(self._store, rbac_session, permission)
        if decision.allowed:
            return AuthzDecision(allowed=True, permission=permission)
        return AuthzDecision(
            allowed=False,
            permission=permission,
            reason=decision.reason,
            code=decision.code or "denied",
        )
