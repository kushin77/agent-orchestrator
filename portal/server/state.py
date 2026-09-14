"""portal.server.state — console data model projected from the LIVE stores.

The console is a *projection* over the control-plane entities, never a second
source of truth (CMR portal doctrine). This module holds the console state and
hydrates it from the control plane's real stores on this checkout (issue #348):

* **agent roster** — every agent's profile identity (capabilities, model tier,
  owner) is resolved from the live AgentProfile registry
  (``registry/profiles/seeds`` via :class:`~portal.server.livestore.RegistrySnapshot`).
  A roster referencing a profile the registry does not publish **fails closed**
  at load, so the console can never drift from the registry.
* **budgets/quota/usage** — read from the live telemetry policy + durable
  metering feed (``telemetry/budgets|metering`` via
  :class:`~portal.server.livestore.TelemetrySnapshot`). A tenant with no declared
  telemetry policy reads as no declared budget and no metered spend — an honest
  "nothing declared", never an invented number.
* tenant (agent org) — ``identity/rbac`` Org-as-tenant model (issue #12); the
  org directory + per-tenant agent *bindings* are console configuration.
* persona cards + prompt modules (versions) — ``registry/personas|prompts``
* approval-gated destructive ops — ``identity/cpapi`` approvals shape
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from portal.server.auditlog import AuditLedger
from portal.server.livestore import (
    RegistrySnapshot,
    TelemetrySnapshot,
    UsagePoint,
)

__all__ = [
    "Approval",
    "ConsoleState",
    "OrgAgent",
    "OrgBinding",
    "PersonaCard",
    "PromptModule",
    "PromptVersion",
    "Tenant",
    "UsagePoint",
    "seed_state",
]


@dataclass
class Tenant:
    id: str
    name: str
    plan: str = "startup"
    subscription_status: str = "active"
    paused: bool = False
    primary_domain: str = ""
    monthly_budget_usd: float = 0.0
    daily_token_limit: int = 0
    usage_month_usd: float = 0.0
    usage_today_tokens: int = 0


@dataclass
class OrgAgent:
    id: str
    team: str
    profile_id: str
    status: str = "registered"  # registered | active | paused | retired
    model_tier: str = "flash"
    capabilities: list[str] = field(default_factory=list)


@dataclass
class PersonaCard:
    id: str
    name: str
    description: str = ""
    owner: str = "platform"
    capability_tags: list[str] = field(default_factory=list)


@dataclass
class PromptVersion:
    version: str
    task_type: str = ""
    status: str = "published"  # draft | published | active | retired
    fp: int = 0
    fn: int = 0
    samples: int = 0

    def accuracy(self) -> float:
        tp = max(self.samples - self.fp - self.fn, 0)
        if self.samples <= 0:
            return 1.0
        return round(tp / self.samples, 4)


@dataclass
class PromptModule:
    id: str
    versions: list[PromptVersion] = field(default_factory=list)


@dataclass
class Approval:
    id: str
    tenant_id: str
    action: str
    resource: str
    requested_by: str
    reason: str = ""
    status: str = "pending"  # pending | approved | denied
    decided_by: Optional[str] = None


@dataclass
class OrgBinding:
    email: str
    tenant_id: str
    role: str  # owner | admin | team-admin | agent-operator (rbac platform pack)


class ConsoleState:
    """All console state + server-side stores (audit chain, policy controls).

    ``registry`` and ``telemetry`` are the read-only views of the live stores
    the projection is hydrated from (issue #348). They are carried on the state
    so every read-model stays a pure projection of the same live sources.
    """

    def __init__(
        self,
        *,
        registry: Optional[RegistrySnapshot] = None,
        telemetry: Optional[TelemetrySnapshot] = None,
    ) -> None:
        self.registry = registry
        self.telemetry = telemetry
        self.tenants: dict[str, Tenant] = {}
        self.agents: dict[str, list[OrgAgent]] = {}
        self.personas: list[PersonaCard] = []
        self.prompts: list[PromptModule] = []
        self.approvals: list[Approval] = []
        self.usage: dict[str, list[UsagePoint]] = {}
        self.bindings: list[OrgBinding] = []
        self.audit: dict[str, AuditLedger] = {}

    # -- convenience --------------------------------------------------------
    def tenant_ids(self) -> list[str]:
        return sorted(self.tenants)

    def agents_for(self, tenant_id: str) -> list[OrgAgent]:
        return list(self.agents.get(tenant_id, []))

    def approvals_for(self, tenant_id: str) -> list[Approval]:
        return [approval for approval in self.approvals if approval.tenant_id == tenant_id]

    def roles_for(self, email: str) -> list[OrgBinding]:
        normalized = email.strip().lower()
        return [binding for binding in self.bindings if binding.email == normalized]

    def roster_profile_ids(self) -> set[str]:
        """Every profile id the roster resolves to (the registry-backed roster)."""
        return {
            agent.profile_id
            for agents in self.agents.values()
            for agent in agents
        }


#: The console's per-tenant agent *bindings*: which agent instances a tenant
#: runs, their team and lifecycle status. The agent's *identity* — its closed
#: capability set and model tier — is NOT stated here; it is resolved from the
#: live registry (fail closed) so the roster can never drift from it.
_ROSTER: dict[str, list[tuple[str, str, str, str]]] = {
    "acme": [
        ("coder-1", "platform", "coder", "active"),
        ("reviewer-1", "platform", "reviewer", "active"),
        ("ci-ops", "platform", "orchestrator", "registered"),
        ("researcher-1", "research", "researcher", "active"),
        ("data-extract", "research", "data-agent", "paused"),
        ("sales-copilot", "revenue", "orchestrator", "registered"),
    ],
    "globex": [
        ("engineer-1", "platform", "coder", "active"),
        ("docbot", "platform", "paperclip", "active"),
        ("triage-1", "support", "data-agent", "registered"),
    ],
    "initech": [
        ("tps-1", "platform", "coder", "paused"),
        ("cover-1", "platform", "researcher", "active"),
    ],
    # -- purebliss team (issue #256) ----------------------------------------
    # The platform's own five-agent ecosystem, frozen by EPIC #253: agent ids
    # ollama/paperclip/hermes/deepseek/claude under team id `purebliss`. Each
    # agent's capabilities + model tier come from its live registry profile
    # (registry/profiles/seeds/), never from a hand-written literal here.
    "purebliss": [
        ("ollama", "purebliss", "ollama", "active"),
        ("paperclip", "purebliss", "paperclip", "active"),
        ("hermes", "purebliss", "hermes", "active"),
        ("deepseek", "purebliss", "deepseek", "active"),
        ("claude", "purebliss", "claude", "active"),
    ],
}


def _default_repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def seed_state(
    repo_root: Path | str | None = None,
    *,
    usage_store_path: Path | str | None = None,
) -> ConsoleState:
    """Hydrate console state from the LIVE registry + telemetry stores.

    The roster's agent identity is resolved from the live registry and the
    budgets/quota/usage from the live telemetry policy + metering feed; a
    roster profile the registry does not publish fails closed.
    """
    root = Path(repo_root) if repo_root is not None else _default_repo_root()
    state = ConsoleState(
        registry=RegistrySnapshot(root),
        telemetry=TelemetrySnapshot(root, usage_store_path=usage_store_path),
    )
    registry = state.registry
    telemetry = state.telemetry
    assert registry is not None and telemetry is not None  # for type checkers


    # -- tenants (org-as-tenant, issue #12) ---------------------------------
    # The console owns the org *directory* (id, display name, subscription
    # status, primary domain). Every budget/quota figure is READ from the live
    # telemetry policy + metering feed — the console restates none of it.
    tenants = [
        Tenant(id="acme", name="Acme Platform", subscription_status="active",
               primary_domain="acme.example.com"),
        Tenant(id="globex", name="Globex Corp", subscription_status="active",
               primary_domain="globex.example.com"),
        Tenant(id="initech", name="Initech", subscription_status="trial",
               primary_domain="initech.example.com"),
        Tenant(id="purebliss", name="Purebliss", subscription_status="active",
               primary_domain="ai.purebliss.app"),
    ]
    for tenant in tenants:
        budget = telemetry.budget(tenant.id)
        tenant.plan = budget.plan
        tenant.monthly_budget_usd = budget.monthly_budget_usd
        tenant.daily_token_limit = budget.daily_token_limit
        tenant.usage_month_usd = telemetry.usage_totals(tenant.id)["cost_usd"]
        tenant.usage_today_tokens = telemetry.daily_tokens(tenant.id)
        state.tenants[tenant.id] = tenant
        state.audit[tenant.id] = AuditLedger(tenant.id)
        state.usage[tenant.id] = telemetry.usage_series(tenant.id)

    # -- agents (registry/service status vocabulary) ------------------------
    # Each agent's identity is RESOLVED from the live registry, never stated
    # here: an unknown profile reference raises (fail closed), so the console
    # roster cannot drift from registry/profiles/seeds.
    for tenant_id, bindings in _ROSTER.items():
        state.agents[tenant_id] = [
            OrgAgent(agent_id, team, profile_ref, status, profile.model,
                     list(profile.capabilities))
            for agent_id, team, profile_ref, status in bindings
            for profile in (registry.profile(profile_ref),)
        ]

    # -- personas (registry/personas cards) ---------------------------------
    for pid, pname, powner, tags in [
        ("coder", "Coder", "platform", ["code", "review"]),
        ("reviewer", "Code Reviewer", "platform", ["review", "audit"]),
        ("orchestrator", "Orchestrator", "platform", ["plan", "dispatch"]),
        ("researcher", "Researcher", "research", ["research", "summarize"]),
        ("data-agent", "Data Agent", "research", ["extract", "transform"]),
        ("qa-sme", "QA SME", "quality", ["test", "gate"]),
        ("security-sme", "Security SME", "security", ["audit", "guard"]),
        ("iac-sme", "IaC SME", "platform", ["infra", "code"]),
        ("auditor", "Auditor", "compliance", ["audit", "verify"]),
        ("docs-author", "Docs Author", "platform", ["docs", "summarize"]),
    ]:
        state.personas.append(
            PersonaCard(pid, pid.replace("-", " ").title(), owner=powner,
                        capability_tags=tags)
        )

    # -- prompts (modules + versions + FP/FN, issue #13 feedback vocab) -----
    state.prompts = [
        PromptModule(
            "classify-route",
            [
                PromptVersion("v1", "classify", "active", fp=38, fn=29, samples=620),
                PromptVersion("v2", "classify", "published", fp=21, fn=17, samples=620),
            ],
        ),
        PromptModule(
            "code-review-verdict",
            [
                PromptVersion("v1", "review", "active", fp=14, fn=9, samples=240),
            ],
        ),
        PromptModule(
            "summarize",
            [
                PromptVersion("v1", "summarize", "active", fp=3, fn=5, samples=410),
            ],
        ),
    ]

    # -- approvals (approval-gated destructive ops, cpapi shape) ------------
    state.approvals = [
        Approval(
            id="ap_1", tenant_id="globex", action="agent.retire",
            resource="agent:docbot",
            requested_by="user:carol@globex.example.com",
            reason="retiring stale docs agent", status="pending",
        ),
        Approval(
            id="ap_2", tenant_id="initech", action="tenant.pause",
            resource="tenant:initech",
            requested_by="user:dan@initech.example.com",
            reason="trial hold (denied in triage)", status="denied",
            decided_by="user:root@platform.example.com",
        ),
    ]

    # -- usage series -------------------------------------------------------
    # Populated above, straight from the live metering feed
    # (telemetry/metering usage store) — never a hardcoded series.

    # -- org directory (rbac role vocabulary, issue #12) --------------------
    state.bindings = [
        OrgBinding("root@platform.example.com", "acme", "owner"),
        OrgBinding("alice@acme.example.com", "acme", "owner"),
        OrgBinding("bob@acme.example.com", "acme", "admin"),
        OrgBinding("erin@acme.example.com", "acme", "agent-operator"),
        OrgBinding("carol@globex.example.com", "globex", "owner"),
        OrgBinding("dan@initech.example.com", "initech", "owner"),
        OrgBinding("root@platform.example.com", "purebliss", "owner"),
    ]

    # -- seed audit chains (telemetry/ledger actor kind:id vocabulary) ------
    _seed_audit(state, "acme", [
        ("system:provision", "registry.register", "agent:coder-1", None),
        ("user:root@platform.example.com", "registry.activate", "agent:coder-1",
         "activated on onboarding"),
        ("system:policy", "policy.decision", "model.call", "allow (no control on)"),
        ("user:alice@acme.example.com", "control.toggle", "control:model-call-budget",
         "enabled=true"),
        ("user:bob@acme.example.com", "budget.notify", "tenant:acme",
         "utilization 56%"),
    ])
    _seed_audit(state, "globex", [
        ("system:provision", "registry.register", "agent:engineer-1", None),
        ("user:carol@globex.example.com", "registry.activate", "agent:engineer-1",
         "activated"),
        ("user:carol@globex.example.com", "agent.pause", "agent:docbot", "paused"),
        ("system:policy", "policy.decision", "egress.send", "blocked by data-egress-guard"),
    ])
    _seed_audit(state, "initech", [
        ("system:provision", "registry.register", "agent:cover-1", None),
        ("user:dan@initech.example.com", "registry.activate", "agent:cover-1",
         "activated"),
    ])
    _seed_audit(state, "purebliss", [
        ("system:provision", "registry.register", "agent:claude", None),
        ("user:root@platform.example.com", "registry.activate", "agent:claude",
         "activated on onboarding"),
        ("system:policy", "policy.decision", "model.call", "allow (no control on)"),
    ])
    return state


def _seed_audit(state: ConsoleState, tenant_id: str, rows: list[tuple[Any, ...]]) -> None:
    ledger = state.audit[tenant_id]
    for actor, action, resource, detail in rows:
        ledger.append(str(actor), str(action), resource=resource, detail=detail)
