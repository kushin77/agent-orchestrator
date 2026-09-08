"""Views + lifecycle + approvals-feed tests (issue #39 AC #2).

Each console surface is served by a data endpoint returning the envelope, and
every view document exists as a static frame. Destructive ops are
approval-gated (cpapi, issue #38 shape): retire creates a pending approval,
approve executes it, deny never executes it.
"""

from __future__ import annotations

import pytest
from conftest import ApiClient, login_as


def _data(payload):
    return payload["data"]


VIEW_NAMES = [
    "login", "shell", "tenants", "overview", "agents", "personas",
    "prompts", "policies", "budgets", "usage", "audit", "approvals",
]


@pytest.mark.parametrize("view", VIEW_NAMES)
def test_view_document_is_served(app, view):
    api = ApiClient(app)
    status, payload = api.get(f"/views/{view}.html")
    assert status == 200
    assert isinstance(payload, bytes)
    assert b"<!DOCTYPE html>" in payload or b"<html" in payload


def test_shell_and_view_assets_are_served(app):
    api = ApiClient(app)
    for path in (
        "/css/console.css",
        "/js/api.js",
        "/design-tokens/tokens.css",
        "/design-tokens/tokens.json",
    ):
        status, payload = api.get(path)
        assert status == 200, path
        assert isinstance(payload, bytes) and payload


def test_tenant_overview_and_agent_tree(app):
    api = login_as(app, "alice@acme.example.com", "acme")
    status, payload = api.get("/api/tenants/acme/overview")
    assert status == 200
    stats = _data(payload)["stats"]
    assert stats["agents"] >= 5
    assert stats["teams"]
    status, payload = api.get("/api/tenants/acme/agents")
    teams = _data(payload)["teams"]
    teams_by_name = {team["team"]: team for team in teams}
    assert "platform" in teams_by_name
    profiles = {agent["profileId"] for agent in teams_by_name["platform"]["agents"]}
    assert {"coder", "reviewer", "orchestrator"} <= profiles


def test_personas_and_prompts_surfaces(app):
    api = login_as(app, "bob@acme.example.com", "acme")
    status, payload = api.get("/api/tenants/acme/personas")
    persona_ids = {p["personaId"] for p in _data(payload)["personas"]}
    assert "coder" in persona_ids and "orchestrator" in persona_ids
    status, payload = api.get("/api/tenants/acme/prompts")
    prompts = _data(payload)["prompts"]
    by_id = {module["moduleId"]: module for module in prompts}
    assert "classify-route" in by_id
    versions = by_id["classify-route"]["versions"]
    assert len(versions) >= 2  # versions + FP/FN
    assert all("fp" in version and "fn" in version for version in versions)
    # v2 lowers FP/FN vs v1 (the feedback loop's improvement signal).
    v1 = next(v for v in versions if v["version"] == "v1")
    v2 = next(v for v in versions if v["version"] == "v2")
    assert (v2["fp"] + v2["fn"]) < (v1["fp"] + v1["fn"])


def test_budgets_and_usage_surfaces(app):
    api = login_as(app, "alice@acme.example.com", "acme")
    status, payload = api.get("/api/tenants/acme/budgets")
    budgets = _data(payload)
    assert budgets["monthlyBudgetUsd"] == 2500.0
    assert 0 < budgets["budgetUtilizationPct"] < 100
    assert "quota" in budgets
    status, payload = api.get("/api/tenants/acme/usage")
    usage = _data(payload)
    assert usage["series"]
    assert usage["totals"]["calls"] > 0


def test_agent_activate_pause_lifecycle(app):
    api = login_as(app, "alice@acme.example.com", "acme")
    # ci-ops is registered -> activate.
    status, payload = api.post("/api/tenants/acme/agents/ci-ops/activate", {})
    assert status == 200
    assert payload["data"]["status"] == "active"
    # active -> pause.
    status, payload = api.post("/api/tenants/acme/agents/ci-ops/pause", {})
    assert status == 200
    assert payload["data"]["status"] == "paused"
    # paused -> pause is an invalid transition.
    status, payload = api.post("/api/tenants/acme/agents/ci-ops/pause", {})
    assert status == 409
    assert payload["error"]["code"] == "bad_transition"


def test_retire_is_approval_gated_and_approve_executes(app):
    api = login_as(app, "alice@acme.example.com", "acme")
    # Request retire -> approval required (202), agent NOT yet retired.
    status, payload = api.post("/api/tenants/acme/agents/sales-copilot/retire", {})
    assert status == 202
    approval_id = payload["data"]["approvalId"]
    assert payload["data"]["status"] == "approval_required"
    status, payload = api.get("/api/tenants/acme/approvals")
    ids = [a["approvalId"] for a in _data(payload)["approvals"]]
    assert approval_id in ids
    # Approve (alice is owner, approval:approve) -> retire executes.
    status, payload = api.post(
        f"/api/tenants/acme/approvals/{approval_id}/approve", {}
    )
    assert status == 200
    assert payload["data"]["status"] == "approved"
    status, payload = api.get("/api/tenants/acme/agents")
    flat = [
        agent for team in _data(payload)["teams"] for agent in team["agents"]
    ]
    retired = next(agent for agent in flat if agent["agentId"] == "sales-copilot")
    assert retired["status"] == "retired"


def test_deny_never_executes(app):
    api = login_as(app, "alice@acme.example.com", "acme")
    status, payload = api.post("/api/tenants/acme/agents/coder-1/retire", {})
    assert status == 202
    approval_id = payload["data"]["approvalId"]
    status, payload = api.post(
        f"/api/tenants/acme/approvals/{approval_id}/deny", {}
    )
    assert status == 200
    assert payload["data"]["status"] == "denied"
    status, payload = api.get("/api/tenants/acme/agents")
    flat = [
        agent for team in _data(payload)["teams"] for agent in team["agents"]
    ]
    coder = next(agent for agent in flat if agent["agentId"] == "coder-1")
    assert coder["status"] != "retired"


def test_approvals_feed_shows_pending_and_new_events(app):
    """The approvals feed (real-time via polling) reflects server events."""
    api = login_as(app, "root@platform.example.com", "acme")
    status, payload = api.get("/api/tenants/globex/approvals")
    pending = [
        a for a in _data(payload)["approvals"] if a["status"] == "pending"
    ]
    assert any(a["action"] == "agent.retire" for a in pending)
    # A new retire request on globex appears in the feed.
    status, payload = api.post(
        "/api/tenants/globex/agents/triage-1/retire", {}
    )
    assert status == 202
    status, payload = api.get("/api/tenants/globex/approvals")
    ids = [a["approvalId"] for a in _data(payload)["approvals"]]
    assert payload["data"]["approvals"][0]["approvalId"] in ids


def test_trial_tenant_pause_is_approval_gated(app):
    api = login_as(app, "dan@initech.example.com", "initech")
    status, payload = api.post("/api/tenants/initech/budgets/pause", {})
    assert status == 202
    approval_id = payload["data"]["approvalId"]
    status, payload = api.post(
        f"/api/tenants/initech/approvals/{approval_id}/approve", {}
    )
    assert status == 200
    status, payload = api.get("/api/tenants/initech/budgets")
    assert _data(payload)["paused"] is True
