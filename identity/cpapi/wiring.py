"""Production wiring: thin adapters over the real merged pillar modules.

The control-plane handlers depend only on the injectable seams in
``ports.py``; this module builds those seams from the *real* merged modules —
the same RBAC enforcement core (``identity/rbac``), the agent identity
registry (``registry/service``), and so on — so a deployment composes the
control plane out of the platform's own contracts instead of the fakes used
in tests. Every adapter is a thin field/function-name mapping; no pillar
module is edited and no vocabulary is redefined.

The adapters import their backing module lazily (guarded) so this file stays
importable even when a pillar root is not yet on ``sys.path``; a missing
module raises a descriptive :class:`RuntimeError` at construction time, never
a silent partial wiring.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional, Tuple

from . import errors as err
from .access import AuthenticatedPrincipal
from .model import AgentView, TaskView
from .ports import DecisionView

# Pillar roots (repo-root-relative) the real modules live under. Each subtree
# is a PEP-420 namespace package importable when its parent is on sys.path
# (the conftest convention every merged lane uses).
_REPO_ROOTS = {
    "identity": "identity",
    "registry": "registry",
    "telemetry": "telemetry",
    "engine": "engine",
    "gateway": "gateway",
    "guardrails": "guardrails",
}


def add_pillar_roots(repo_root: str) -> None:
    """Insert the pillar parent dirs on ``sys.path`` (idempotent, front)."""
    for rel in _REPO_ROOTS.values():
        path = os.path.join(repo_root, rel)
        if os.path.isdir(path) and path not in sys.path:
            sys.path.insert(0, path)


def _repo_root() -> str:
    """Resolve the repo root from this file's location (identity/cpapi -> root)."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# --- authZ: real identity/rbac enforcement core --------------------------------------


class RbacAuthorizer:
    """Authorizer over the real ``identity/rbac`` two-gate guard.

    Delegates every check to ``rbac.guard(store, subject, ScopeNode(org_id),
    permission)`` so the scope gate and the permission gate are enforced by
    the merged enforcement core — never re-implemented here. A denial maps to
    :class:`DecisionView` with the rbac ``Decision`` fields preserved for the
    403 envelope and the observability denial log.
    """

    def __init__(self, rbac_store: Any) -> None:
        self._store = rbac_store

    @classmethod
    def build(cls, rbac_store: Any) -> "RbacAuthorizer":
        return cls(rbac_store)

    def authorize(
        self,
        subject: str,
        tenant_id: str,
        permission: str,
        *,
        subject_type: str = "user",
    ) -> DecisionView:
        try:
            from rbac.guard import guard
            from rbac.model import ScopeNode
        except ImportError as exc:  # pragma: no cover - env wiring error
            raise RuntimeError(
                "identity/rbac is not importable; add 'identity/' to sys.path "
                "or call add_pillar_roots(repo_root) first"
            ) from exc
        decision = guard(self._store, subject, ScopeNode(tenant_id), permission)
        return DecisionView(
            allowed=bool(decision.allowed),
            subject=decision.subject,
            tenant_id=tenant_id,
            permission=decision.permission,
            reason=decision.reason,
            code=decision.code,
            missing_permissions=tuple(decision.missing_permissions or ()),
        )


# --- authN: real session verifier adapter ----------------------------------------------


class SsoSessionVerifier:
    """SessionVerifier over the merged ``identity/sso`` SsoService.

    ``sso_service.verify_session(token)`` returns the claims dict
    (``iss``/``sub``/``tenantId``/``subjectType``/``email``/...). This adapter
    maps those claims onto an :class:`AuthenticatedPrincipal`. The sso session
    token carries a mandatory tenant claim and the underlying service refuses
    missing/invalid tenants — nothing here re-implements that.

    ``subject_type`` is ``user`` (console SSO principals) unless the claim
    says otherwise.
    """

    def __init__(self, sso_service: Any, *, tenant_claim: str = "tenantId") -> None:
        self._sso = sso_service
        self._tenant_claim = tenant_claim

    def verify(self, token: str, expected_tenant: Optional[str] = None) -> AuthenticatedPrincipal:
        try:
            claims = self._sso.verify_session(token, expected_tenant=expected_tenant)
        except err.ApiError:
            raise
        except Exception as exc:  # the sso verifier raises its own error types
            code = type(exc).__name__
            if "Revoked" in code:
                raise err.session_revoked() from exc
            raise err.invalid_token(str(exc)) from exc
        tenant_id = claims.get(self._tenant_claim)
        if not tenant_id:
            raise err.invalid_token("session token carries no tenant claim")
        return AuthenticatedPrincipal(
            subject_id=claims.get("sub", ""),
            subject_type=claims.get("subjectType", "user"),
            tenant_id=str(tenant_id),
            roles=tuple(claims.get("role") or ()),
            claims=dict(claims),
        )


# --- agents: real registry/service facade ----------------------------------------------


def _map_registry_error(exc: Exception) -> err.ApiError:
    """Map a registry/service exception onto the API error taxonomy."""
    name = type(exc).__name__
    message = str(exc)
    if name in ("AgentAlreadyRegisteredError", "DuplicateError"):
        return err.conflict(message, code="already_registered")
    if name in ("InvalidAgentIdError", "InvalidTenantIdError", "ValidationError"):
        return err.validation_error(message)
    if name == "UnknownAgentError":
        return err.not_found(message, code="unknown_agent")
    if name in ("UnknownProfileError", "ProfileResolutionError", "VocabularyUnavailableError"):
        return err.not_found(message, code="unknown_profile")
    if name == "UnknownTaskTypeError":
        return err.not_found(message, code="unknown_task_type")
    if name in ("InvalidTransitionError", "AgentNotActiveError", "RoutingError", "NoRoutableAgentError"):
        return err.refused(message)
    if name == "CrossTenantDenied":
        return err.cross_tenant()
    return err.ApiError(422, "refused", message)


def _agent_view(agent: Any) -> AgentView:
    return AgentView(
        agentId=agent.agent_id,
        tenantId=agent.tenant_id,
        profileRef=agent.profile_ref,
        profileVersion=agent.profile_version,
        status=agent.status,
        capabilities=list(agent.capabilities or ()),
        modelTier=agent.model_tier or "",
        owner=agent.owner or "",
        lastSeen=agent.last_seen,
        registeredAt=agent.registered_at or "",
    )


class RegistryAgentOps:
    """AgentOps over the real ``registry/service`` AgentRegistry facade.

    Lifecycle and reads delegate 1:1 to the merged facade (which enforces the
    closed lifecycle transitions and tenant scoping and appends to the
    registry audit log). Task dispatch is the gateway/engine surface, not the
    registry's, so an optional ``dispatch_backend`` may be injected; without
    one a dispatch request fails closed with a clear 503 rather than running
    an ad-hoc path.
    """

    def __init__(self, registry: Any, *, dispatch_backend: Optional[Any] = None) -> None:
        self._registry = registry
        self._dispatch_backend = dispatch_backend

    @classmethod
    def build(
        cls, *, repo_root: Optional[str] = None, dispatch_backend: Optional[Any] = None
    ) -> "RegistryAgentOps":
        root = repo_root or _repo_root()
        add_pillar_roots(root)
        try:
            from service import AgentRegistry
        except ImportError as exc:  # pragma: no cover - env wiring error
            raise RuntimeError(
                "registry/service is not importable; add 'registry/' to sys.path "
                "or pass repo_root"
            ) from exc
        return cls(AgentRegistry(), dispatch_backend=dispatch_backend)

    def _run(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except err.ApiError:
            raise
        except Exception as exc:  # noqa: BLE001 - registry raises its own taxonomy
            raise _map_registry_error(exc) from exc

    def register(self, tenant_id: str, agent_id: str, profile_ref: str, *, actor: str) -> AgentView:
        agent = self._run(self._registry.register, tenant_id, agent_id, profile_ref, actor=actor)
        return _agent_view(agent)

    def activate(self, tenant_id: str, agent_id: str, *, actor: str) -> AgentView:
        return _agent_view(self._run(self._registry.activate, tenant_id, agent_id, actor=actor))

    def pause(self, tenant_id: str, agent_id: str, *, actor: str) -> AgentView:
        return _agent_view(self._run(self._registry.pause, tenant_id, agent_id, actor=actor))

    def retire(self, tenant_id: str, agent_id: str, *, actor: str) -> AgentView:
        return _agent_view(self._run(self._registry.retire, tenant_id, agent_id, actor=actor))

    def get(self, tenant_id: str, agent_id: str) -> AgentView:
        return _agent_view(self._run(self._registry.get, tenant_id, agent_id))

    def list(self, tenant_id: str, *, status: Optional[str] = None) -> List[AgentView]:
        agents = self._run(self._registry.list, tenant_id, status=status)
        return [_agent_view(a) for a in agents]

    def dispatch_task(
        self,
        tenant_id: str,
        agent_id: str,
        task_type: str,
        input_: Dict[str, Any],
        *,
        actor: str,
        idempotency_key: Optional[str] = None,
    ) -> TaskView:
        if self._dispatch_backend is None:
            raise err.unavailable(
                "task dispatch requires an engine/queue or gateway backend wired "
                "at deployment (RegistryAgentOps.dispatch_backend)"
            )
        return self._dispatch_backend.dispatch(
            tenant_id, agent_id, task_type, input_,
            actor=actor, idempotency_key=idempotency_key,
        )

    def task_status(self, tenant_id: str, task_id: str) -> TaskView:
        if self._dispatch_backend is None:
            raise err.unavailable(
                "task status requires an engine/queue or gateway backend wired "
                "at deployment (RegistryAgentOps.dispatch_backend)"
            )
        return self._dispatch_backend.status(tenant_id, task_id)


# --- deployment assembly helper --------------------------------------------------------


def build_rbac_authorizer(rbac_store: Any) -> RbacAuthorizer:
    """Build the real authZ seam over an ``identity/rbac`` in-memory store."""
    return RbacAuthorizer(rbac_store)
