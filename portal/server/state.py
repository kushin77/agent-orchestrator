"""portal.server.state — console data model + deterministic seed.

The console is a *projection* over the control-plane entities, never a second
source of truth (CMR portal doctrine). This module holds the offline demo
state seeded against the frozen vocabulary of the merged pillar lanes:

* tenant (agent org) — ``identity/rbac`` Org-as-tenant model (issue #12)
* agents + statuses registered/active/paused/retired — ``registry/service``
* persona cards + prompt modules (versions) — ``registry/personas|prompts``
* budgets/quota + usage — ``telemetry/budgets|metering``
* approval-gated destructive ops — ``identity/cpapi`` approvals shape
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from portal.server.auditlog import AuditLedger


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
class UsagePoint:
    day: str
    vendor: str
    model: str
    calls: int
    tokens: int
    cost_usd: float


@dataclass
class OrgBinding:
    email: str
    tenant_id: str
    role: str  # owner | admin | team-admin | agent-operator (rbac platform pack)


class ConsoleState:
    """All console state + server-side stores (audit chain, policy controls)."""

    def __init__(self) -> None:
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


def seed_state() -> ConsoleState:
    """Deterministic offline demo state (vocabulary-consumed seed rows)."""
    state = ConsoleState()

    # -- tenants (org-as-tenant, issue #12) ---------------------------------
    tenants = [
        Tenant(
            id="acme",
            name="Acme Platform",
            plan="enterprise",
            subscription_status="active",
            primary_domain="acme.example.com",
            monthly_budget_usd=2500.0,
            daily_token_limit=8_000_000,
            usage_month_usd=1412.30,
            usage_today_tokens=3_214_000,
        ),
        Tenant(
            id="globex",
            name="Globex Corp",
            plan="smb",
            subscription_status="active",
            primary_domain="globex.example.com",
            monthly_budget_usd=600.0,
            daily_token_limit=1_500_000,
            usage_month_usd=412.75,
            usage_today_tokens=702_000,
        ),
        Tenant(
            id="initech",
            name="Initech",
            plan="startup",
            subscription_status="trial",
            primary_domain="initech.example.com",
            monthly_budget_usd=300.0,
            daily_token_limit=800_000,
            usage_month_usd=148.10,
            usage_today_tokens=254_000,
        ),
    ]
    for tenant in tenants:
        state.tenants[tenant.id] = tenant
        state.audit[tenant.id] = AuditLedger(tenant.id)

    # -- agents (registry/service status vocabulary) ------------------------
    state.agents["acme"] = [
        OrgAgent("coder-1", "platform", "coder", "active", "flash",
                 ["code", "review"]),
        OrgAgent("reviewer-1", "platform", "reviewer", "active", "pro",
                 ["review", "audit"]),
        OrgAgent("ci-ops", "platform", "orchestrator", "registered", "flash",
                 ["plan", "dispatch"]),
        OrgAgent("researcher-1", "research", "researcher", "active", "pro",
                 ["research"]),
        OrgAgent("data-extract", "research", "data-agent", "paused", "flash",
                 ["extract", "transform"]),
        OrgAgent("sales-copilot", "revenue", "orchestrator", "registered", "flash",
                 ["plan", "dispatch"]),
    ]
    state.agents["globex"] = [
        OrgAgent("engineer-1", "platform", "coder", "active", "flash",
                 ["code", "review"]),
        OrgAgent("docbot", "platform", "docs-author", "active", "flash",
                 ["docs"]),
        OrgAgent("triage-1", "support", "data-agent", "registered", "flash",
                 ["classify"]),
    ]
    state.agents["initech"] = [
        OrgAgent("tps-1", "platform", "coder", "paused", "flash", ["code"]),
        OrgAgent("cover-1", "platform", "researcher", "active", "pro",
                 ["research", "summarize"]),
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

    # -- usage series (telemetry metering vocabulary) -----------------------
    state.usage["acme"] = [
        UsagePoint("2026-09-04", "anthropic", "claude-3-5-sonnet", 1810, 1_210_000, 21.4),
        UsagePoint("2026-09-05", "anthropic", "claude-3-5-sonnet", 1755, 1_185_000, 20.9),
        UsagePoint("2026-09-06", "openai", "gpt-4o-mini", 2400, 1_402_000, 12.1),
        UsagePoint("2026-09-07", "deepseek", "deepseek-chat", 6200, 2_040_000, 9.3),
        UsagePoint("2026-09-08", "anthropic", "claude-3-5-sonnet", 1620, 1_180_000, 20.6),
    ]
    state.usage["globex"] = [
        UsagePoint("2026-09-06", "deepseek", "deepseek-chat", 2100, 640_000, 2.9),
        UsagePoint("2026-09-07", "deepseek", "deepseek-chat", 2350, 705_000, 3.2),
        UsagePoint("2026-09-08", "openai", "gpt-4o-mini", 980, 420_000, 3.6),
    ]
    state.usage["initech"] = [
        UsagePoint("2026-09-07", "deepseek", "deepseek-chat", 900, 230_000, 1.1),
        UsagePoint("2026-09-08", "anthropic", "claude-3-5-sonnet", 310, 210_000, 3.4),
    ]

    # -- org directory (rbac role vocabulary, issue #12) --------------------
    state.bindings = [
        OrgBinding("root@platform.example.com", "acme", "owner"),
        OrgBinding("alice@acme.example.com", "acme", "owner"),
        OrgBinding("bob@acme.example.com", "acme", "admin"),
        OrgBinding("erin@acme.example.com", "acme", "agent-operator"),
        OrgBinding("carol@globex.example.com", "globex", "owner"),
        OrgBinding("dan@initech.example.com", "initech", "owner"),
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
    return state


def _seed_audit(state: ConsoleState, tenant_id: str, rows: list[tuple[Any, ...]]) -> None:
    ledger = state.audit[tenant_id]
    for actor, action, resource, detail in rows:
        ledger.append(str(actor), str(action), resource=resource, detail=detail)
