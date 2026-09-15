"""Live registry + telemetry projection tests (issue #348).

The console must project the REAL registry and telemetry, never a seeded demo
`ConsoleState`: the agent roster's identity comes from
``registry/profiles/seeds`` (fail closed on drift) and budgets/quota/usage come
from ``telemetry/budgets|metering``. These tests read the very same store files
the projection reads, so they fail if the portal ever diverges from them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from conftest import REPO_ROOT, console_sso, login_as

from portal.server import state as state_mod
from portal.server.app import ConsoleApplication
from portal.server.livestore import (
    RegistryDriftError,
    RegistrySnapshot,
    TelemetrySnapshot,
)
from portal.server.state import seed_state
from telemetry.metering.model import UsageRecord
from telemetry.metering.store import append_records

_PROFILES = REPO_ROOT / "registry" / "profiles"
_SEEDS = _PROFILES / "seeds"
_TELEMETRY = REPO_ROOT / "telemetry"


def _seed(profile_id: str) -> dict:
    """The revision the live registry resolves for ``profile_id``.

    A profile may publish more than one version — ``paperclip`` publishes 1.0.0
    and 1.1.0 (issue #447) — and ``RegistrySnapshot`` resolves the highest
    published version. ``Path.glob`` yields entries in *filesystem* order, so
    ``next(...)`` would compare the projection against an arbitrary revision and
    this suite would go red on a correct build depending on the order the
    checkout happened to create the seeds directory in. Sort and take the last,
    which is the registry's own rule.
    """
    paths = sorted(_SEEDS.glob(f"{profile_id}.*.yaml"))
    assert paths, f"no AgentProfile seed for {profile_id!r} under {_SEEDS}"
    return yaml.safe_load(paths[-1].read_text(encoding="utf-8"))


def _tier_ladder() -> dict:
    return yaml.safe_load((_PROFILES / "catalog.yaml").read_text(encoding="utf-8"))["tiers"]


# -- roster == the real registry -------------------------------------------

def test_roster_identity_is_resolved_from_the_live_registry():
    """Every agent's profile/capabilities/tier equals its registry seed."""
    state = seed_state(REPO_ROOT)
    assert state.roster_profile_ids() <= set(state.registry.profile_ids())
    for agents in state.agents.values():
        for agent in agents:
            seed = _seed(agent.profile_id)
            assert agent.profile_id == seed["id"], agent.id
            assert agent.capabilities == seed["capabilitySet"], agent.id
            assert agent.model_tier == _tier_ladder()[seed["defaultModelTier"]]["model"]
            # The registry is the only source: the tier is NOT a literal.
            assert agent.model_tier in {"flash", "pro"}


def test_roster_never_references_a_profile_the_registry_lacks(monkeypatch):
    """An unknown profile reference fails closed at load (no silent fallback)."""
    bogus = {tenant: list(rows) for tenant, rows in state_mod._ROSTER.items()}
    bogus["acme"] = list(bogus["acme"]) + [("ghost-1", "platform", "no-such-profile", "active")]
    monkeypatch.setattr(state_mod, "_ROSTER", bogus)
    with pytest.raises(RegistryDriftError, match="no-such-profile"):
        seed_state(REPO_ROOT)


def test_registry_snapshot_fails_closed_on_unknown_profile():
    snapshot = RegistrySnapshot(REPO_ROOT)
    assert snapshot.has("coder")
    assert "coder" in snapshot.profile_ids()
    with pytest.raises(RegistryDriftError):
        snapshot.profile("definitely-not-a-profile")


def test_agents_endpoint_serves_the_registry_resolved_roster():
    app = ConsoleApplication(repo_root=REPO_ROOT, sso=console_sso())
    api = login_as(app, "root@platform.example.com", "acme")
    status, payload = api.get("/api/tenants/acme/agents")
    assert status == 200
    rows = [a for team in payload["data"]["teams"] for a in team["agents"]]
    assert rows, "acme must have a roster"
    for row in rows:
        seed = _seed(row["profileId"])
        assert row["capabilities"] == seed["capabilitySet"], row["agentId"]
        assert row["modelTier"] == _tier_ladder()[seed["defaultModelTier"]]["model"]


# -- budgets/quota == the real telemetry -----------------------------------

def test_budget_and_quota_are_read_from_telemetry_config():
    policies = yaml.safe_load(
        (_TELEMETRY / "budgets" / "config" / "policies.yaml").read_text(encoding="utf-8")
    )
    acme = next(p for p in policies["policies"] if p["tenantId"] == "acme")
    state = seed_state(REPO_ROOT)
    tenant = state.tenants["acme"]
    assert tenant.monthly_budget_usd == acme["cost"]["limitUsd"]
    assert tenant.daily_token_limit == acme["tokens"]["limit"]

    telemetry = TelemetrySnapshot(REPO_ROOT)
    quota = telemetry.effective_quota("acme")
    assert quota["requests"]["softLimit"] == 4000  # tenant override
    assert quota["tokens"]["softLimit"] == 10_000_000  # enterprise plan default


def test_undeclared_tenant_reads_as_no_budget_not_an_invented_one():
    state = seed_state(REPO_ROOT)
    initech = state.tenants["initech"]
    assert initech.monthly_budget_usd == 0.0
    assert initech.daily_token_limit == 0
    assert state.usage["initech"] == []
    assert state.telemetry.effective_quota("initech") == {}


# -- usage == the real metering feed ---------------------------------------

def _record(
    tenant: str, day: str, provider: str, model: str, tokens: int, cost: float, n: int = 0
) -> UsageRecord:
    return UsageRecord(
        tenant_id=tenant,
        agent_id="coder-1",
        provider=provider,
        model=model,
        route=None,
        outcome="ok",
        input_tokens=tokens,
        output_tokens=0,
        billable=True,
        metered=True,
        ts=f"{day}T12:00:00Z",
        source_type="call_record",
        source_key=f"{tenant}-{day}-{provider}-{model}-{n}",
        cost_usd=cost,
        cost_source="rate_card",
    )


def test_usage_projection_reads_the_metering_feed(tmp_path: Path):
    store_path = tmp_path / "metering.jsonl"
    records = [
        _record("acme", "2026-09-10", "anthropic", "claude-3-5-sonnet", 1200, 1.5, n=1),
        _record("acme", "2026-09-10", "anthropic", "claude-3-5-sonnet", 800, 0.5, n=2),
        _record("acme", "2026-09-11", "deepseek", "deepseek-chat", 500, 0.2, n=3),
        _record("globex", "2026-09-11", "openai", "gpt-4o-mini", 300, 0.1, n=4),
    ]
    written = append_records(store_path, records)
    assert written == 4

    state = seed_state(REPO_ROOT, usage_store_path=store_path)
    # acme's feed: two days, two vendors — nothing invented, nothing dropped.
    points = state.usage["acme"]
    assert {(p.day, p.vendor, p.model) for p in points} == {
        ("2026-09-10", "anthropic", "claude-3-5-sonnet"),
        ("2026-09-11", "deepseek", "deepseek-chat"),
    }
    totals = state.telemetry.usage_totals("acme")
    assert totals["calls"] == 3
    assert totals["tokens"] == 2500
    assert totals["cost_usd"] == 2.2
    assert state.tenants["acme"].usage_month_usd == 2.2

    # ...and the HTTP surface serves exactly that feed.
    app = ConsoleApplication(repo_root=REPO_ROOT, state=state, sso=console_sso())
    api = login_as(app, "alice@acme.example.com", "acme")
    status, payload = api.get("/api/tenants/acme/usage")
    assert status == 200
    assert payload["data"]["totals"]["calls"] == 3
    assert payload["data"]["totals"]["tokens"] == 2500
    status, payload = api.get("/api/tenants/acme/budgets")
    assert payload["data"]["usageMonthUsd"] == 2.2
    assert payload["data"]["quota"]["calls"]["used"] == 3


def test_usage_feed_is_tenant_isolated(tmp_path: Path):
    store_path = tmp_path / "metering.jsonl"
    append_records(store_path, [_record("globex", "2026-09-11", "openai", "gpt-4o-mini", 9, 0.1)])
    state = seed_state(REPO_ROOT, usage_store_path=store_path)
    assert state.usage["acme"] == []
    assert state.usage["globex"] and state.usage["globex"][0].tokens == 9


def test_malformed_usage_lines_are_skipped_not_fabricated(tmp_path: Path):
    store_path = tmp_path / "metering.jsonl"
    append_records(store_path, [_record("acme", "2026-09-11", "deepseek", "deepseek-chat", 100, 0.1)])
    with open(store_path, "a", encoding="utf-8") as handle:
        handle.write("not json\n")
        handle.write(json.dumps({"kind": "something-else"}) + "\n")
    state = seed_state(REPO_ROOT, usage_store_path=store_path)
    assert state.telemetry.usage_totals("acme")["calls"] == 1
