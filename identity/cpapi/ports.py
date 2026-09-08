"""Control-plane dependency seams (injectable ports).

The control-plane handlers never touch the merged pillar modules directly;
they depend on the narrow duck-typed interfaces declared here. That is what
makes the whole surface unit-testable offline: tests inject in-memory fakes
(``fakes.py``), production injects adapters over the real merged modules
(``wiring.py``), and the handlers stay identical in both worlds. Field and
method names mirror the frozen upstream vocabularies so the real adapters are
thin (see each adapter in ``wiring.py``).

Views returned by ports are the typed ``model.View`` dataclasses so a handler
can render a response without knowing the backing store's own shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from .model import (
    AgentView,
    AuditRecordView,
    PersonaView,
    PolicyView,
    ProfileView,
    PromptModuleView,
    QuotaView,
    TaskView,
    TenantView,
    UsageView,
)


class Clock(Protocol):
    """Source of RFC-3339 UTC timestamps (injected so tests are deterministic)."""

    def now_utc(self) -> str: ...


class SessionVerifier(Protocol):
    """AuthN seam: verify a bearer session token into a scoped principal.

    Real adapters wrap the merged session verifiers (``identity/sso``
    ``SsoService.verify_session`` or ``registry/service``
    ``IdentityService.verify_session``) and map their claim dict onto a
    :class:`VerifiedPrincipal`. Verification always fails closed: an invalid,
    expired, revoked, or cross-tenant token raises ``ApiError`` (401).
    """

    def verify(self, token: str, expected_tenant: Optional[str] = None) -> "VerifiedPrincipal": ...


class Authorizer(Protocol):
    """AuthZ seam: the RBAC two-gate guard at a tenant's org node.

    Real adapters wrap ``identity/rbac`` ``guard(store, subject,
    ScopeNode(tenant_id), permission)`` so the scope gate and the permission
    gate are enforced by the merged enforcement core, never re-implemented.
    """

    def authorize(
        self,
        subject: str,
        tenant_id: str,
        permission: str,
        *,
        subject_type: str = "user",
    ) -> "DecisionView": ...


class AgentOps(Protocol):
    """Agent identity/lifecycle operations (registry/service issue #10)."""

    def register(
        self, tenant_id: str, agent_id: str, profile_ref: str, *, actor: str
    ) -> AgentView: ...

    def activate(self, tenant_id: str, agent_id: str, *, actor: str) -> AgentView: ...

    def pause(self, tenant_id: str, agent_id: str, *, actor: str) -> AgentView: ...

    def retire(self, tenant_id: str, agent_id: str, *, actor: str) -> AgentView: ...

    def get(self, tenant_id: str, agent_id: str) -> AgentView: ...

    def list(self, tenant_id: str, *, status: Optional[str] = None) -> List[AgentView]: ...

    def dispatch_task(
        self,
        tenant_id: str,
        agent_id: str,
        task_type: str,
        input_: Dict[str, Any],
        *,
        actor: str,
        idempotency_key: Optional[str] = None,
    ) -> TaskView: ...

    def task_status(self, tenant_id: str, task_id: str) -> TaskView: ...


class ProfileCatalog(Protocol):
    """Read model over the frozen AgentProfile seeds (registry/profiles #9)."""

    def list(self, tenant_id: str) -> List[ProfileView]: ...

    def get(self, tenant_id: str, profile_id: str) -> ProfileView: ...


class PersonaRegistryPort(Protocol):
    """Read model over persona cards (registry/personas #11)."""

    def list(self, tenant_id: str) -> List[PersonaView]: ...

    def get(self, tenant_id: str, persona_id: str) -> PersonaView: ...


class PromptLibrary(Protocol):
    """Read model over published prompt modules (registry/prompts #13)."""

    def list(self, tenant_id: str) -> List[PromptModuleView]: ...

    def get(self, tenant_id: str, module_id: str) -> PromptModuleView: ...


class PolicyStore(Protocol):
    """Read model over guardrail policy bindings (guardrails/policy)."""

    def list(self, tenant_id: str) -> List[PolicyView]: ...

    def get(self, tenant_id: str, policy_id: str) -> PolicyView: ...


class TenantOps(Protocol):
    """Tenant/org + subscription/entitlement read model (identity phase 6)."""

    def get(self, tenant_id: str) -> TenantView: ...


class BudgetOps(Protocol):
    """Budget/quota/usage + per-tenant pause rail (telemetry/budgets #34)."""

    def usage(self, tenant_id: str) -> UsageView: ...

    def quotas(self, tenant_id: str) -> QuotaView: ...

    def pause(self, tenant_id: str, *, actor: str, reason: str) -> TenantView: ...

    def resume(self, tenant_id: str, *, actor: str) -> TenantView: ...


class AuditLedger(Protocol):
    """Append + query the per-tenant audit ledger (telemetry/ledger #31).

    ``append`` returns the written record so handlers can echo it; ``query``
    filters by tenant (mandatory when the caller is tenant-scoped) and
    optional action/actor, newest-first.
    """

    def append(
        self,
        *,
        tenant_id: str,
        actor: str,
        action: str,
        resource: Optional[str] = None,
        detail: Optional[Dict[str, Any]] = None,
    ) -> AuditRecordView: ...

    def query(
        self,
        *,
        tenant_id: Optional[str] = None,
        action: Optional[str] = None,
        actor: Optional[str] = None,
        limit: int = 100,
    ) -> List[AuditRecordView]: ...


@runtime_checkable
class VerifiedPrincipal(Protocol):
    """The authenticated principal a session token maps to.

    ``subject_id`` is the id the RBAC store keys bindings on; ``subject_type``
    is ``user`` or ``agent``; ``tenant_id`` is the mandatory session-tenant
    claim (a session is never usable in another tenant).
    """

    subject_id: str
    subject_type: str
    tenant_id: str
    roles: tuple
    claims: Dict[str, Any]


@dataclass(frozen=True)
class DecisionView:
    """Outcome of one authorization check (mirrors rbac ``Decision``)."""

    allowed: bool
    subject: str
    tenant_id: str
    permission: str
    reason: Optional[str] = None
    code: Optional[str] = None
    missing_permissions: tuple = ()

