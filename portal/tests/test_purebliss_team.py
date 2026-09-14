"""Purebliss five-agent team tests (issue #256, converted by issue #348).

The portal is the control-plane projection for the platform's own agent
ecosystem. EPIC #253 froze the team contract: agent ids
ollama/paperclip/hermes/deepseek/claude under team id ``purebliss``, all
active. These tests prove the Agents view's data endpoint exposes exactly that
team — grouped team -> agents, each agent carrying profile/status/model-tier/
capabilities — while RBAC (scope + permission gates) stays intact.

Issue #348 de-seeded the demo state: model tier and capabilities are now
**resolved from the live registry** (``registry/profiles/seeds``), not written
by hand. The former expectation that ``claude`` runs at ``pro`` was demo drift —
the registry seed declares ``defaultModelTier: MED``, which the closed
vocabulary (``catalog.yaml``) maps to the ``flash`` model. The tier test below
therefore asserts the portal equals the registry, reading the seed itself,
rather than restating a hardcoded tier.
"""

from __future__ import annotations

import yaml
from conftest import REPO_ROOT, login_as

PUREBLISS_AGENTS = {"ollama", "paperclip", "hermes", "deepseek", "claude"}

#: The closed tier -> model ladder the registry declares (catalog.yaml).
_CATALOG = REPO_ROOT / "registry" / "profiles" / "catalog.yaml"
_SEEDS = REPO_ROOT / "registry" / "profiles" / "seeds"


def _registry_tier_model(agent_profile: str) -> str:
    """The model the live registry resolves for a profile (via catalog.yaml)."""
    ladder = yaml.safe_load(_CATALOG.read_text(encoding="utf-8"))["tiers"]
    seed = next(_SEEDS.glob(f"{agent_profile}.*.yaml"))
    tier = yaml.safe_load(seed.read_text(encoding="utf-8"))["defaultModelTier"]
    return ladder[tier]["model"]


def _data(payload):
    return payload["data"]


def test_purebliss_tenant_is_listed_for_super_admin(super_client):
    status, payload = super_client.get("/api/tenants")
    assert status == 200
    rows = {row["tenantId"]: row for row in _data(payload)["tenants"]}
    assert "purebliss" in rows
    assert rows["purebliss"]["agents"] == 5
    assert rows["purebliss"]["agentsActive"] == 5


def test_purebliss_agents_tree_has_exactly_the_five_agents(app):
    api = login_as(app, "root@platform.example.com", "purebliss")
    status, payload = api.get("/api/tenants/purebliss/agents")
    assert status == 200
    teams = _data(payload)["teams"]
    assert len(teams) == 1
    team = teams[0]
    assert team["team"] == "purebliss"
    agent_ids = {agent["agentId"] for agent in team["agents"]}
    assert agent_ids == PUREBLISS_AGENTS


def test_each_purebliss_agent_shows_profile_status_tier_capabilities(app):
    api = login_as(app, "root@platform.example.com", "purebliss")
    status, payload = api.get("/api/tenants/purebliss/agents")
    assert status == 200
    team = _data(payload)["teams"][0]
    by_id = {agent["agentId"]: agent for agent in team["agents"]}
    for agent_id in PUREBLISS_AGENTS:
        agent = by_id[agent_id]
        assert agent["profileId"], agent_id
        assert agent["status"] == "active", agent_id
        assert agent["modelTier"] in ("flash", "pro"), agent_id
        assert isinstance(agent["capabilities"], list), agent_id
        assert agent["capabilities"], agent_id


def test_model_tiers_match_the_registry_profiles(app):
    """Every agent's tier equals the tier its live registry profile declares.

    (Converted by issue #348: the old expectation hardcoded ``claude: pro``,
    which was demo drift — the registry seed declares MED -> flash.)
    """
    api = login_as(app, "root@platform.example.com", "purebliss")
    status, payload = api.get("/api/tenants/purebliss/agents")
    assert status == 200
    team = _data(payload)["teams"][0]
    by_id = {agent["agentId"]: agent for agent in team["agents"]}
    for agent_id in PUREBLISS_AGENTS:
        assert by_id[agent_id]["modelTier"] == _registry_tier_model(agent_id), agent_id


def test_purebliss_team_appears_in_tenant_overview(app):
    api = login_as(app, "root@platform.example.com", "purebliss")
    status, payload = api.get("/api/tenants/purebliss/overview")
    assert status == 200
    stats = _data(payload)["stats"]
    assert stats["agents"] == 5
    assert "purebliss" in stats["teams"]


def test_scope_gate_still_blocks_unbound_user_from_purebliss(app):
    # alice is bound to acme only; purebliss is out of scope -> 403.
    api = login_as(app, "alice@acme.example.com", "acme")
    status, payload = api.get("/api/tenants/purebliss/agents")
    assert status == 403
    assert payload["error"]["code"] == "scope_denied"
