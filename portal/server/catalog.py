"""portal.server.catalog — read-model projections over :class:`ConsoleState`.

Each view's data is a pure projection (never a second source of truth) over
the seeded console state. Shapes mirror the merged pillar vocabularies so the
front-end JSON matches what the control-plane REST surface (issue #38) would
return: agent statuses from registry/service, budgets from telemetry/budgets,
audit records from telemetry/ledger, prompt FP/FN from registry/prompts.
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
        "quota": {
            "calls": {"soft": 120_000, "hard": 150_000, "used": 41_300},
            "tokens": {
                "soft": tenant.daily_token_limit,
                "hard": tenant.daily_token_limit * 2,
                "used": tenant.usage_today_tokens,
            },
            "concurrency": {"soft": 24, "hard": 48, "used": 11},
            "storage": {"soft": 100_000, "hard": 200_000, "used": 28_400},
        },
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
