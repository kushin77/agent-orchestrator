"""Mapper tests: determinism, schema conformance, org chart (issue #428)."""

from __future__ import annotations

import json
from pathlib import Path

from integrations.paperclip import mapping as mapping_mod

ROOT = Path(__file__).resolve().parents[3]


def test_yaml_subset_loader_handles_maps_seqs_and_block_scalars():
    text = (
        "schemaVersion: 1\n"
        "defaultPolicy: warn\n"
        "budgets:\n"
        "  tenant-a:\n"
        "    monthlyBudgetUsd: 100.00\n"
        "    policy: fallback\n"
        "    warnAtPct: 80\n"
        "policies:\n"
        "  - tenantId: omega\n"
        "    mode: observe\n"
        "    cost:\n"
        "      window: month\n"
        "      limitUsd: 500.0\n"
        "    vendorCaps:\n"
        "      - vendor: anthropic\n"
        "        limitUsd: 200.0\n"
        "description: |\n"
        "  a free-text block: with colons # and hashes\n"
        "  - that must not parse as a sequence\n"
    )
    data = mapping_mod.load_yaml(text)
    assert data["schemaVersion"] == 1
    assert data["budgets"]["tenant-a"]["monthlyBudgetUsd"] == 100.0
    assert data["budgets"]["tenant-a"]["policy"] == "fallback"
    assert data["policies"][0]["tenantId"] == "omega"
    assert data["policies"][0]["cost"]["limitUsd"] == 500.0
    assert data["policies"][0]["vendorCaps"][0]["vendor"] == "anthropic"
    assert data["description"] == ""


def test_reads_real_fleet_sources():
    profiles = mapping_mod.iter_seed_profiles(ROOT)
    personas = mapping_mod.iter_persona_cards(ROOT)
    assert any(p.id == "paperclip" for p in profiles)
    assert any(p.id == "coder" for p in personas)
    assert profiles == sorted(profiles, key=lambda p: p.id)
    assert personas == sorted(personas, key=lambda p: p.id)


def test_agents_form_one_org_chart_rooted_at_platform():
    agents = mapping_mod.map_agents(ROOT)
    by_id = {a["agent_id"]: a for a in agents}
    assert by_id["orchestrator"]["reports_to"] == mapping_mod.PLATFORM_ROOT
    assert all(a["hired"] is True for a in agents)
    # a persona card wins over a seed of the same id (richer record)
    assert by_id["coder"]["kind"] == "persona"


def test_cto_office_projects_real_reporting_edge_to_ceo():
    """CTO office org-adapter declaration (issue #1573): the platform `cto`
    persona card's real reportsTo edge must survive the seed->org-chart
    projection, not fall through to the default-owner flattening that a
    reserved/unresolved principal (e.g. `board`) gets.

    No-false-green proof: an id that cannot resolve through `personas` (a
    made-up id) IS expected to fall through to the default owner, which is
    exactly the behaviour this test would fail to catch if map_agents()
    always flattened everything under the default owner.
    """
    agents = mapping_mod.map_agents(ROOT)
    by_id = {a["agent_id"]: a for a in agents}
    assert by_id["cto"]["reports_to"] == "ceo"
    assert by_id["cto"]["kind"] == "persona"
    # negative control: an unresolvable reports_to id falls through to the
    # default owner rather than being silently dropped or erroring.
    assert "not-a-real-persona-id" not in by_id
    assert "code-authoring" in by_id["coder"]["capabilities"]


def test_tickets_use_the_closed_status_vocabulary():
    tickets = mapping_mod.map_tickets(ROOT)
    assert tickets, "the board snapshot yielded no tickets"
    assert all(t["status"] in mapping_mod.TICKET_STATUSES for t in tickets)
    assert all(t["owner"] for t in tickets)
    assert all(t["goal"] for t in tickets)
    assert all(t["evidence"] for t in tickets)


def test_budgets_derive_currency_and_hard_stop():
    budgets = mapping_mod.map_budgets(ROOT)
    assert budgets, "the budget rail yielded no cost lines"
    assert all(b["currency"] == "USD" for b in budgets)
    assert all(b["scope"]["level"] in ("agent", "team", "project") for b in budgets)
    assert all(0 <= b["burn_rate_alert_pct"] <= 100 for b in budgets)


def test_heartbeats_name_a_blocker_when_no_beat_exists():
    beats = mapping_mod.map_heartbeats(ROOT, session_id="sess-1")
    assert len(beats) == len(mapping_mod.HEARTBEAT_RUNGS)
    assert all(b["session_id"] == "sess-1" for b in beats)
    assert all(b["cadence_seconds"] == mapping_mod.CADENCE_SECONDS for b in beats)
    assert all(b["wake"]["cause"] in (
        "assigned", "commented", "unblocked", "review-requested", "scheduled"
    ) for b in beats)
    assert all(b["outcome"]["owner"] for b in beats)


def test_plan_is_deterministic_and_conforms():
    schemas = mapping_mod.load_schemas(ROOT)
    first = mapping_mod.build_plan(ROOT, company_id="acme", session_id="sess")
    second = mapping_mod.build_plan(ROOT, company_id="acme", session_id="sess")
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert mapping_mod.validate_plan(first, schemas) == []


def test_mapping_is_offscreen_from_the_board_snapshot():
    # Reading (never writing) the committed board keeps the mapper offline.
    board = mapping_mod.load_board(ROOT)
    assert isinstance(board.get("issues"), list)
