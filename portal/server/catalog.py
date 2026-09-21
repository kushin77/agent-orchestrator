"""portal.server.catalog — read-model projections over :class:`ConsoleState`.

Each view's data is a pure projection (never a second source of truth) over the
console state, which is itself hydrated from the **live** registry +
telemetry stores (issue #348): the agent roster resolves its identity from
``registry/profiles/seeds`` and budgets/quota/usage are read from
``telemetry/budgets|metering``. Shapes mirror the merged pillar vocabularies so
the front-end JSON matches what the control-plane REST surface (issue #38)
would return: agent statuses from registry/service, budgets from
telemetry/budgets, audit records from telemetry/ledger, prompt FP/FN from
registry/prompts.


---knowledge---
module_id: portal.server.catalog
system: portal
app: server
solution_class: pattern
patterns: [read-model, projection, no-second-source-of-truth]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [tenants_overview, tenant_overview, agents_tree, personas, prompts, budgets, usage, audit_records, approvals]
invariants: "every view is a pure projection over ConsoleState, never a second source of truth"
gotchas: ""
related: ["#38", "#348"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from typing import Any

from portal.server.state import ConsoleState, OrgAgent


def tenants_overview(state: ConsoleState) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for tenant_id in state.tenant_ids():
        tenant = state.tenants[tenant_id]
        agents = state.agents_for(tenant_id)
        active = sum(1 for agent in agents if agent.status == "active")
        rows.append(
            {
                "tenantId": tenant.id,
                "name": tenant.name,
                "plan": tenant.plan,
                "subscriptionStatus": tenant.subscription_status,
                "paused": tenant.paused,
                "primaryDomain": tenant.primary_domain,
                "agents": len(agents),
                "agentsActive": active,
                "agentsRegistered": sum(
                    1 for agent in agents if agent.status == "registered"
                ),
                "agentsPaused": sum(1 for agent in agents if agent.status == "paused"),
                "budgetUsd": tenant.monthly_budget_usd,
                "usageUsd": round(tenant.usage_month_usd, 2),
                "budgetUtilizationPct": round(
                    100.0 * tenant.usage_month_usd / tenant.monthly_budget_usd, 1
                )
                if tenant.monthly_budget_usd
                else 0.0,
                "pendingApprovals": len(
                    [a for a in state.approvals_for(tenant_id) if a.status == "pending"]
                ),
            }
        )
    return rows


def tenant_overview(state: ConsoleState, tenant_id: str) -> dict[str, Any]:
    tenant = state.tenants[tenant_id]
    agents = state.agents_for(tenant_id)
    team_names = sorted({agent.team for agent in agents})
    return {
        "tenantId": tenant.id,
        "name": tenant.name,
        "plan": tenant.plan,
        "subscriptionStatus": tenant.subscription_status,
        "paused": tenant.paused,
        "primaryDomain": tenant.primary_domain,
        "stats": {
            "agents": len(agents),
            "active": sum(1 for agent in agents if agent.status == "active"),
            "registered": sum(1 for agent in agents if agent.status == "registered"),
            "paused": sum(1 for agent in agents if agent.status == "paused"),
            "teams": team_names,
            "auditEvents": len(state.audit[tenant_id]),
            "budgetUtilizationPct": round(
                100.0 * tenant.usage_month_usd / tenant.monthly_budget_usd, 1
            )
            if tenant.monthly_budget_usd
            else 0.0,
            "pendingApprovals": len(
                [a for a in state.approvals_for(tenant_id) if a.status == "pending"]
            ),
        },
    }


def agents_tree(state: ConsoleState, tenant_id: str) -> list[dict[str, Any]]:
    """Org tree: teams -> agents (Org->Team->Agent, issue #12)."""
    agents = state.agents_for(tenant_id)
    by_team: dict[str, list[OrgAgent]] = {}
    for agent in agents:
        by_team.setdefault(agent.team, []).append(agent)
    return [
        {
            "team": team,
            "agents": [
                {
                    "agentId": agent.id,
                    "profileId": agent.profile_id,
                    "status": agent.status,
                    "modelTier": agent.model_tier,
                    "capabilities": agent.capabilities,
                }
                for agent in sorted(team_agents, key=lambda a: a.id)
            ],
        }
        for team in sorted(by_team)
        for team_agents in [by_team[team]]
    ]


def personas(state: ConsoleState) -> list[dict[str, Any]]:
    return [
        {
            "personaId": persona.id,
            "name": persona.name,
            "owner": persona.owner,
            "capabilityTags": persona.capability_tags,
            "description": persona.description,
        }
        for persona in state.personas
    ]


def prompts(state: ConsoleState) -> list[dict[str, Any]]:
    """Prompt modules with per-version FP/FN (registry/prompts feedback)."""
    result: list[dict[str, Any]] = []
    for module in state.prompts:
        versions = []
        for version in module.versions:
            versions.append(
                {
                    "version": version.version,
                    "taskType": version.task_type,
                    "status": version.status,
                    "fp": version.fp,
                    "fn": version.fn,
                    "samples": version.samples,
                    "accuracy": version.accuracy(),
                }
            )
        active = next(
            (v for v in versions if v["status"] == "active"), versions[0] if versions else {}
        )
        result.append(
            {
                "moduleId": module.id,
                "activeVersion": active.get("version", ""),
                "versions": versions,
            }
        )
    return result


def _quota_projection(state: ConsoleState, tenant_id: str) -> dict[str, Any]:
    """The tenant's effective quota, READ from the live telemetry config.

    ``soft``/``hard`` come from ``telemetry/budgets/config/quotas.yaml``
    (plan defaults overlaid with the tenant's explicit overrides); ``used``
    comes from the live metering feed for the metered resources (calls/tokens)
    and is 0 for the resources with no live probe (concurrency/storage) — an
    honest zero, never an invented figure. A tenant with no declared plan reads
    as zeros across the board (nothing declared).
    """
    declared: dict[str, dict[str, Any]] = {}
    if state.telemetry is not None:
        declared = state.telemetry.effective_quota(tenant_id)
    totals = (
        state.telemetry.usage_totals(tenant_id)
        if state.telemetry is not None
        else {"calls": 0, "tokens": 0}
    )
    tenant = state.tenants[tenant_id]

    def spec(resource: str, used: int) -> dict[str, int]:
        entry = declared.get(resource) or {}
        return {
            "soft": int(entry.get("softLimit") or 0),
            "hard": int(entry.get("hardLimit") or 0),
            "used": int(used),
        }

    return {
        # telemetry calls the request resource `requests`; the console's
        # vocabulary (and the Budgets view) calls it `calls`.
        "calls": spec("requests", totals["calls"]),
        "tokens": spec("tokens", tenant.usage_today_tokens),
        "concurrency": spec("concurrency", 0),
        "storage": spec("storage", 0),
    }


def budgets(state: ConsoleState, tenant_id: str) -> dict[str, Any]:
    tenant = state.tenants[tenant_id]
    utilization = (
        round(100.0 * tenant.usage_month_usd / tenant.monthly_budget_usd, 1)
        if tenant.monthly_budget_usd
        else 0.0
    )
    token_utilization = (
        round(100.0 * tenant.usage_today_tokens / tenant.daily_token_limit, 1)
        if tenant.daily_token_limit
        else 0.0
    )
    return {
        "tenantId": tenant_id,
        "monthlyBudgetUsd": tenant.monthly_budget_usd,
        "usageMonthUsd": round(tenant.usage_month_usd, 2),
        "budgetUtilizationPct": utilization,
        "dailyTokenLimit": tenant.daily_token_limit,
        "usageTodayTokens": tenant.usage_today_tokens,
        "tokenUtilizationPct": token_utilization,
        "quota": _quota_projection(state, tenant_id),
        "paused": tenant.paused,
    }


def usage(state: ConsoleState, tenant_id: str) -> dict[str, Any]:
    points = state.usage.get(tenant_id, [])
    return {
        "tenantId": tenant_id,
        "series": [
            {
                "day": point.day,
                "vendor": point.vendor,
                "model": point.model,
                "calls": point.calls,
                "tokens": point.tokens,
                "costUsd": round(point.cost_usd, 4),
            }
            for point in points
        ],
        "totals": {
            "calls": sum(point.calls for point in points),
            "tokens": sum(point.tokens for point in points),
            "costUsd": round(sum(point.cost_usd for point in points), 4),
        },
    }


def audit_records(state: ConsoleState, tenant_id: str, limit: int = 200) -> list[dict[str, Any]]:
    records = state.audit[tenant_id].records()
    return [record.as_json() for record in records[-limit:]]


def approvals(state: ConsoleState, tenant_id: str) -> list[dict[str, Any]]:
    return [
        {
            "approvalId": approval.id,
            "action": approval.action,
            "resource": approval.resource,
            "requestedBy": approval.requested_by,
            "reason": approval.reason,
            "status": approval.status,
            "decidedBy": approval.decided_by,
        }
        for approval in reversed(state.approvals_for(tenant_id))
    ]
