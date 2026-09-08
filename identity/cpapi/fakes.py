"""In-memory fakes for the control-plane dependency seams (offline tests).

The fakes mirror the merged pillar vocabularies and their fail-closed
behaviors so handler tests are deterministic and exercise real semantics:
tenant-scoped agent stores (an agent in one tenant is invisible in another),
closed lifecycle transitions (retire is terminal), fail-closed unknown lookups
(404), permission/scope separation on the authorizer, and a per-tenant
kill-switch on the budget rail. Production wiring replaces each fake with a
thin adapter over the real merged modules (see ``wiring.py``) — the handlers
do not change.

``build_test_app`` wires a full :class:`ControlPlane` from fakes in one call
so tests stay short.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple
from uuid import uuid4

from . import errors as err
from .access import AuthenticatedPrincipal
from .approvals import ApprovalGate, ApprovalStore
from .audit import AuditStore
from .control import ControlPlane
from .model import (
    AgentView,
    PersonaView,
    PolicyView,
    ProfileView,
    PromptModuleView,
    QuotaView,
    TaskView,
    TenantView,
    UsageView,
)
from .outbox import Outbox
from .ports import DecisionView

# --- clock -------------------------------------------------------------------


class FakeClock:
    """Deterministic RFC-3339 UTC clock; ``advance`` drives lease/redelivery tests."""

    def __init__(self, start: str = "2026-09-08T00:00:00Z") -> None:
        text = start[:-1] + "+00:00" if start.endswith("Z") else start
        self._t = datetime.fromisoformat(text)
        if self._t.tzinfo is None:
            self._t = self._t.replace(tzinfo=timezone.utc)

    def now_utc(self) -> str:
        return self._t.strftime("%Y-%m-%dT%H:%M:%SZ")

    def advance(self, seconds: int) -> "FakeClock":
        self._t = self._t + timedelta(seconds=seconds)
        return self


def fake_rng(prefix: str = ""):
    """Deterministic-ish rng factory (uuid hex, optional prefix for readability)."""

    def _rng() -> str:
        return f"{prefix}{uuid4().hex}" if prefix else uuid4().hex

    return _rng


# --- authN --------------------------------------------------------------------


class FakeSessionVerifier:
    """Issues + verifies opaque session tokens mapped to principals."""

    def __init__(self) -> None:
        self._tokens: Dict[str, AuthenticatedPrincipal] = {}

    def issue(
        self,
        tenant_id: str,
        subject_id: str,
        *,
        subject_type: str = "user",
        roles: Tuple[str, ...] = (),
        claims: Optional[Dict[str, Any]] = None,
    ) -> str:
        token = f"tok_{uuid4().hex[:16]}"
        principal = AuthenticatedPrincipal(
            subject_id=subject_id,
            subject_type=subject_type,
            tenant_id=tenant_id,
            roles=roles,
            claims=dict(claims or {}),
        )
        self._tokens[token] = principal
        return token

    def verify(self, token: str, expected_tenant: Optional[str] = None) -> AuthenticatedPrincipal:
        principal = self._tokens.get(token)
        if principal is None:
            raise err.invalid_token()
        if expected_tenant is not None and principal.tenant_id != expected_tenant:
            raise err.cross_tenant()
        return principal


# --- authZ --------------------------------------------------------------------


def _permission_matches(required: str, held: str) -> bool:
    """Wildcard-aware ``resource:action`` match (mirrors the rbac model)."""
    if held == "*:*" or held == required:
        return True
    req_res, _, req_act = required.partition(":")
    held_res, _, held_act = held.partition(":")
    return (held_res == req_res and held_act == "*") or (held_res == "*" and held_act == req_act)


class FakeAuthorizer:
    """In-memory two-gate authorizer: scope gate, then permission gate.

    ``add_principal(subject, tenant, *permissions)`` both scopes the subject
    to the tenant and grants the permissions (``"*:*"`` grants everything).
    """

    def __init__(self) -> None:
        self._in_scope: Set[Tuple[str, str]] = set()
        self._grants: Dict[Tuple[str, str], Set[str]] = {}

    def add_principal(self, subject: str, tenant_id: str, *permissions: str) -> None:
        self._in_scope.add((subject, tenant_id))
        self._grants.setdefault((subject, tenant_id), set()).update(permissions)

    def add_admin(self, subject: str, tenant_id: str) -> None:
        self.add_principal(subject, tenant_id, "*:*")

    def authorize(
        self,
        subject: str,
        tenant_id: str,
        permission: str,
        *,
        subject_type: str = "user",
    ) -> DecisionView:
        if (subject, tenant_id) not in self._in_scope:
            return DecisionView(
                allowed=False,
                subject=subject,
                tenant_id=tenant_id,
                permission=permission,
                reason="scope",
                code="out_of_scope",
                missing_permissions=(permission,),
            )
        held = self._grants.get((subject, tenant_id), set())
        if any(_permission_matches(permission, h) for h in held):
            return DecisionView(
                allowed=True,
                subject=subject,
                tenant_id=tenant_id,
                permission=permission,
            )
        return DecisionView(
            allowed=False,
            subject=subject,
            tenant_id=tenant_id,
            permission=permission,
            reason="permission",
            code="denied",
            missing_permissions=(permission,),
        )


# --- agents (registry/service vocabulary) --------------------------------------

# Closed lifecycle status vocabulary (registry/service issue #10).
STATUS_REGISTERED = "registered"
STATUS_ACTIVE = "active"
STATUS_PAUSED = "paused"
STATUS_RETIRED = "retired"

_VALID_TRANSITIONS = {
    "activate": {STATUS_REGISTERED, STATUS_PAUSED},
    "pause": {STATUS_ACTIVE},
    "retire": {STATUS_REGISTERED, STATUS_ACTIVE, STATUS_PAUSED},
}


class FakeAgentOps:
    """Tenant-scoped agent store + lifecycle + dispatch (registry #10 shape)."""

    def __init__(self, clock: FakeClock, rng: Any = None) -> None:
        self._clock = clock
        self._rng = rng or (lambda: uuid4().hex)
        self._agents: Dict[Tuple[str, str], AgentView] = {}
        self._tasks: Dict[Tuple[str, str], TaskView] = {}
        self._task_keys: Dict[Tuple[str, str], str] = {}

    # -- lifecycle ----------------------------------------------------------

    def register(
        self, tenant_id: str, agent_id: str, profile_ref: str, *, actor: str
    ) -> AgentView:
        key = (tenant_id, agent_id)
        if key in self._agents:
            raise err.conflict(f"agent {agent_id!r} is already registered", code="already_registered")
        agent = AgentView(
            agentId=agent_id,
            tenantId=tenant_id,
            profileRef=profile_ref,
            profileVersion="1.0.0",
            status=STATUS_REGISTERED,
            capabilities=["code-author"],
            modelTier="LOW",
            owner="platform/execution",
            registeredAt=self._clock.now_utc(),
        )
        self._agents[key] = agent
        return agent

    def _transition(
        self, tenant_id: str, agent_id: str, action: str, target: str, actor: str
    ) -> AgentView:
        agent = self._require(tenant_id, agent_id)
        if agent.status not in _VALID_TRANSITIONS[action]:
            raise err.refused(
                f"invalid transition {action} from {agent.status!r} "
                f"(valid from: {sorted(_VALID_TRANSITIONS[action])})"
            )
        if action == "retire" and agent.status == STATUS_RETIRED:
            raise err.refused("agent is already retired (terminal)")
        updated = AgentView(
            agentId=agent.agentId,
            tenantId=agent.tenantId,
            profileRef=agent.profileRef,
            profileVersion=agent.profileVersion,
            status=target,
            capabilities=list(agent.capabilities),
            modelTier=agent.modelTier,
            owner=agent.owner,
            lastSeen=agent.lastSeen,
            registeredAt=agent.registeredAt,
        )
        self._agents[(tenant_id, agent_id)] = updated
        return updated

    def activate(self, tenant_id: str, agent_id: str, *, actor: str) -> AgentView:
        return self._transition(tenant_id, agent_id, "activate", STATUS_ACTIVE, actor)

    def pause(self, tenant_id: str, agent_id: str, *, actor: str) -> AgentView:
        return self._transition(tenant_id, agent_id, "pause", STATUS_PAUSED, actor)

    def retire(self, tenant_id: str, agent_id: str, *, actor: str) -> AgentView:
        return self._transition(tenant_id, agent_id, "retire", STATUS_RETIRED, actor)

    def get(self, tenant_id: str, agent_id: str) -> AgentView:
        return self._require(tenant_id, agent_id)

    def list(self, tenant_id: str, *, status: Optional[str] = None) -> List[AgentView]:
        matches = [a for (t, _), a in self._agents.items() if t == tenant_id]
        if status is not None:
            matches = [a for a in matches if a.status == status]
        return sorted(matches, key=lambda a: a.agentId)

    def _require(self, tenant_id: str, agent_id: str) -> AgentView:
        agent = self._agents.get((tenant_id, agent_id))
        if agent is None:
            raise err.not_found(
                f"no agent {agent_id!r} in tenant {tenant_id!r}", code="unknown_agent"
            )
        return agent

    # -- task dispatch (engine/queue shape) -----------------------------------

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
        agent = self._require(tenant_id, agent_id)
        if agent.status != STATUS_ACTIVE:
            raise err.refused(
                f"agent {agent_id!r} is {agent.status!r}; only active agents dispatch"
            )
        if idempotency_key is not None:
            existing_id = self._task_keys.get((tenant_id, idempotency_key))
            if existing_id is not None:
                return self.task_status(tenant_id, existing_id)
        task_id = f"task_{self._rng()[:12]}"
        task = TaskView(
            taskId=task_id,
            tenantId=tenant_id,
            agentId=agent_id,
            taskType=task_type,
            status="PENDING",
        )
        self._tasks[(tenant_id, task_id)] = task
        if idempotency_key is not None:
            self._task_keys[(tenant_id, idempotency_key)] = task_id
        return task

    def task_status(self, tenant_id: str, task_id: str) -> TaskView:
        task = self._tasks.get((tenant_id, task_id))
        if task is None:
            raise err.not_found(f"no task {task_id!r} in tenant {tenant_id!r}", code="unknown_task")
        return task


# --- registry reads --------------------------------------------------------------


class FakeProfileCatalog:
    """Frozen AgentProfile seeds (registry/profiles #9 ids)."""

    def __init__(self) -> None:
        self._seeds: Dict[str, ProfileView] = {
            "coder": ProfileView(
                id="coder", version="1.0.0", owner="platform/execution",
                systemPromptRef="coder/primary@v1", toolAllowlist=["file_write", "shell_exec"],
                capabilitySet=["code-author", "test-author"], defaultModelTier="LOW",
                guardrailPolicyRef="worker-bundle",
            ),
            "reviewer": ProfileView(
                id="reviewer", version="1.0.0", owner="platform/quality",
                systemPromptRef="reviewer/primary@v1", toolAllowlist=["file_read"],
                capabilitySet=["code-review"], defaultModelTier="MED",
                guardrailPolicyRef="reviewer-bundle",
            ),
            "orchestrator": ProfileView(
                id="orchestrator", version="1.0.0", owner="platform/execution",
                systemPromptRef="orchestrator/primary@v1",
                toolAllowlist=["gh_issue", "gh_pr"],
                capabilitySet=["plan", "dispatch"], defaultModelTier="MED",
                guardrailPolicyRef="worker-bundle",
            ),
        }

    def list(self, tenant_id: str) -> List[ProfileView]:
        return [self._seeds[k] for k in sorted(self._seeds)]

    def get(self, tenant_id: str, profile_id: str) -> ProfileView:
        profile = self._seeds.get(profile_id)
        if profile is None:
            raise err.not_found(f"unknown profile {profile_id!r}", code="unknown_profile")
        return profile


class FakePersonaRegistry:
    """Persona cards (registry/personas #11)."""

    def __init__(self) -> None:
        self._cards: Dict[str, PersonaView] = {
            "coder": PersonaView(
                id="coder", version="1.0.0", tenant="platform", name="Coder",
                posture="executor", defaultModelTier="LOW", systemPromptRef="coder/primary@v1",
                ownedLanes=["execution"],
            ),
            "security-sme": PersonaView(
                id="security-sme", version="1.0.0", tenant="platform",
                name="Security SME", posture="reviewer", defaultModelTier="HIGH",
                systemPromptRef="security-sme/primary@v1", ownedLanes=["security", "guardrails"],
            ),
        }

    def list(self, tenant_id: str) -> List[PersonaView]:
        return [self._cards[k] for k in sorted(self._cards)]

    def get(self, tenant_id: str, persona_id: str) -> PersonaView:
        card = self._cards.get(persona_id)
        if card is None:
            raise err.not_found(f"unknown persona {persona_id!r}", code="unknown_persona")
        return card


class FakePromptLibrary:
    """Published prompt modules (registry/prompts #13)."""

    def __init__(self) -> None:
        self._modules: Dict[str, PromptModuleView] = {
            "classify-route": PromptModuleView(
                taskType="classify-route", version="v1", status="published",
                description="route a request to a task type", modelTierHint="low",
            ),
            "code-review-verdict": PromptModuleView(
                taskType="code-review-verdict", version="v1", status="published",
                description="produce a structured code-review verdict", modelTierHint="med",
            ),
        }

    def list(self, tenant_id: str) -> List[PromptModuleView]:
        return [self._modules[k] for k in sorted(self._modules)]

    def get(self, tenant_id: str, module_id: str) -> PromptModuleView:
        module = self._modules.get(module_id)
        if module is None:
            raise err.not_found(f"unknown prompt module {module_id!r}", code="unknown_prompt_module")
        return module


class FakePolicyStore:
    """Guardrail policy bindings (guardrails/policy)."""

    def __init__(self) -> None:
        self._policies: Dict[str, PolicyView] = {
            "worker-bundle": PolicyView(id="worker-bundle", description="default worker guardrail bundle", mode="enforce", controls=4),
            "reviewer-bundle": PolicyView(id="reviewer-bundle", description="reviewer posture guardrails", mode="enforce", controls=3),
        }

    def list(self, tenant_id: str) -> List[PolicyView]:
        return [self._policies[k] for k in sorted(self._policies)]

    def get(self, tenant_id: str, policy_id: str) -> PolicyView:
        policy = self._policies.get(policy_id)
        if policy is None:
            raise err.not_found(f"unknown policy {policy_id!r}", code="unknown_policy")
        return policy


# --- tenant / budget ------------------------------------------------------------


class FakeTenantOps:
    """Tenant/org + plan view (Org-as-tenant + entitlements plan)."""

    def __init__(self) -> None:
        self._tenants: Dict[str, TenantView] = {}

    def add(self, tenant_id: str, name: str, *, tenant_type: str = "platform", plan: str = "enterprise") -> None:
        self._tenants[tenant_id] = TenantView(
            tenantId=tenant_id, name=name, tenantType=tenant_type, plan=plan,
            subscriptionStatus="active", killSwitch=False,
        )

    def get(self, tenant_id: str) -> TenantView:
        tenant = self._tenants.get(tenant_id)
        if tenant is None:
            raise err.not_found(f"unknown tenant {tenant_id!r}", code="unknown_tenant")
        return tenant


class FakeBudgetOps:
    """Per-tenant budget/quota + kill-switch rail (telemetry/budgets #34 shape)."""

    def __init__(self, clock: FakeClock) -> None:
        self._clock = clock
        self._paused: Set[str] = set()
        self._spend: Dict[str, Dict[str, Any]] = {}

    def seed_usage(self, tenant_id: str, usage_usd: float = 0.0) -> None:
        self._spend[tenant_id] = {"spendUsd": usage_usd}

    def usage(self, tenant_id: str) -> UsageView:
        usage = self._spend.get(tenant_id, {"spendUsd": 0.0})
        return UsageView(
            tenantId=tenant_id,
            usage=dict(usage),
            budgetPositions={
                "monthlyLimitUsd": 1000.0,
                "position": "warn" if usage["spendUsd"] >= 800.0 else "allow",
                "blocked": self._paused.__contains__(tenant_id),
            },
            warnAtPct=0.8,
        )

    def quotas(self, tenant_id: str) -> QuotaView:
        return QuotaView(
            tenantId=tenant_id, plan="enterprise",
            quotas={"requestsPerDay": 100000, "tokensPerDay": 50000000},
        )

    def pause(self, tenant_id: str, *, actor: str, reason: str) -> TenantView:
        self._paused.add(tenant_id)
        return TenantView(
            tenantId=tenant_id, name=tenant_id, tenantType="platform",
            plan="enterprise", subscriptionStatus="active", killSwitch=True,
        )

    def resume(self, tenant_id: str, *, actor: str) -> TenantView:
        self._paused.discard(tenant_id)
        return TenantView(
            tenantId=tenant_id, name=tenant_id, tenantType="platform",
            plan="enterprise", subscriptionStatus="active", killSwitch=False,
        )

    @property
    def paused(self) -> Set[str]:
        return set(self._paused)


# --- app builder ------------------------------------------------------------------


@dataclass
class TestRig:
    """Everything a test needs to drive one offline control plane."""

    clock: FakeClock
    verifier: FakeSessionVerifier
    authorizer: FakeAuthorizer
    agent_ops: FakeAgentOps
    budgets: FakeBudgetOps
    tenants: FakeTenantOps
    audit: AuditStore
    outbox: Outbox
    approvals: ApprovalGate
    app: ControlPlane

    def principal(self, tenant_id: str, subject_id: str, *, roles: Tuple[str, ...] = ()) -> AuthenticatedPrincipal:
        return AuthenticatedPrincipal(
            subject_id=subject_id, subject_type="user", tenant_id=tenant_id, roles=roles
        )


def build_test_app(
    *,
    tenant_id: str = "acme",
    admin_subject: str = "u_admin",
    lease_seconds: int = 300,
    clock_start: str = "2026-09-08T00:00:00Z",
    **overrides: Any,
) -> TestRig:
    """Wire a full ControlPlane from fakes (override any seam via ``**overrides``)."""
    clock = FakeClock(clock_start)
    rng = fake_rng()
    verifier = FakeSessionVerifier()
    authorizer = FakeAuthorizer()
    agent_ops = FakeAgentOps(clock, rng)
    tenants = FakeTenantOps()
    tenants.add(tenant_id, tenant_id.capitalize(), tenant_type="platform", plan="enterprise")
    budgets = FakeBudgetOps(clock)
    budgets.seed_usage(tenant_id, usage_usd=0.0)
    profiles = FakeProfileCatalog()
    personas = FakePersonaRegistry()
    prompts = FakePromptLibrary()
    policies = FakePolicyStore()
    audit = AuditStore(clock=clock)
    outbox = Outbox(clock=clock, lease_seconds=lease_seconds, rng=rng)
    approvals = ApprovalGate(ApprovalStore(clock=clock, rng=rng))
    app = ControlPlane(
        session_verifier=verifier,
        authorizer=authorizer,
        agent_ops=agent_ops,
        profiles=profiles,
        personas=personas,
        prompts=prompts,
        policies=policies,
        tenants=tenants,
        budgets=budgets,
        audit=audit,
        outbox=outbox,
        approvals=approvals,
        clock=clock,
        rng=rng,
        **overrides,
    )
    authorizer.add_admin(admin_subject, tenant_id)
    return TestRig(
        clock=clock,
        verifier=verifier,
        authorizer=authorizer,
        agent_ops=agent_ops,
        budgets=budgets,
        tenants=tenants,
        audit=audit,
        outbox=outbox,
        approvals=approvals,
        app=app,
    )
