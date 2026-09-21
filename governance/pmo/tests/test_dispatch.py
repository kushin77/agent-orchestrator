"""``dispatch`` — priority order -> agent/lane/tier plan (issue #403 follow-on)."""

from __future__ import annotations

import importlib.util as _importlib_util
from pathlib import Path as _ConftestPath

_conftest_spec = _importlib_util.spec_from_file_location(
    "governance_pmo_tests_conftest", _ConftestPath(__file__).with_name("conftest.py")
)
_conftest = _importlib_util.module_from_spec(_conftest_spec)
_conftest_spec.loader.exec_module(_conftest)
claim = _conftest.claim
issue = _conftest.issue
write_board = _conftest.write_board
write_claims = _conftest.write_claims
write_lessons = _conftest.write_lessons

import json

import clusters as clusters_mod
from dispatch import dispatch, paperclip_ticket_record, validate_plan
from graph import load
from policy import load as load_policy
from views import Finding


def test_dispatch_assigns_at_most_one_task_per_lane_per_wave(root):
    write_board(
        root,
        [
            issue(10, labels=["priority:P0", "pillar:registry-profiling"]),
            issue(11, labels=["priority:P0", "pillar:registry-profiling"]),  # same lane: must defer
            issue(12, labels=["priority:P0", "pillar:model-gateway"]),
        ],
    )
    pol = load_policy(root)
    view = dispatch(load(root), pol, wave=1, wave_cap=10)
    assert view.ok
    lanes_assigned = [a["lane"] for a in view.document["assignments"]]
    assert len(lanes_assigned) == len(set(lanes_assigned))  # no lane appears twice
    assert {a["ticket"] for a in view.document["assignments"]} == {
        "kushin77/agent-orchestrator#10",
        "kushin77/agent-orchestrator#12",
    }
    deferred_tickets = {d["ticket"] for d in view.document["deferred"]}
    assert "kushin77/agent-orchestrator#11" in deferred_tickets


def test_dispatch_respects_the_wave_cap(root):
    write_board(root, [issue(n, labels=["priority:P1", "pillar:governance"]) for n in range(10, 15)])
    view = dispatch(load(root), load_policy(root), wave=1, wave_cap=1)
    assert len(view.document["assignments"]) == 1
    assert any(d["reason"] == "wave-cap-reached" for d in view.document["deferred"])


def test_dispatch_skips_an_already_owned_ticket(root):
    write_board(root, [issue(10, labels=["priority:P0"])])
    write_claims(root, [claim(10, agent="agent-a")])
    view = dispatch(load(root), load_policy(root), wave=1)
    assert view.document["assignments"] == []
    assert any(d["reason"] == "already-owned" for d in view.document["deferred"])


def test_dispatch_skips_a_not_ready_ticket(root):
    write_board(root, [issue(9), issue(10, blocked_by=[9])])
    view = dispatch(load(root), load_policy(root), wave=1)
    tickets = {a["ticket"] for a in view.document["assignments"]}
    assert "kushin77/agent-orchestrator#10" not in tickets
    assert any(d["reason"] == "not-ready-open-blocker" for d in view.document["deferred"])


def test_assignment_carries_a_goal_first_agent_brief(root):
    write_board(root, [issue(10, labels=["priority:P0", "pillar:guardrails-security"])])
    view = dispatch(load(root), load_policy(root), wave=1)
    entry = view.document["assignments"][0]
    assert entry["lane"] == "guardrails"
    assert entry["sme_profile"] == "security"
    assert entry["tier"] == "L1"
    assert entry["agent_brief"].startswith("GOAL:")
    assert "CONSTRAINTS:" in entry["agent_brief"]
    assert "STEPS:" in entry["agent_brief"]


def test_dispatch_is_deterministic_across_two_derivations(root):
    write_board(root, [issue(n, labels=["priority:P0"]) for n in range(10, 20)])
    pol = load_policy(root)
    graph = load(root)
    assert dispatch(graph, pol, wave=1).text() == dispatch(graph, pol, wave=1).text()


def test_dispatch_never_writes_anything(root):
    write_board(root, [issue(10, labels=["priority:P0"])])
    before = sorted(p.relative_to(root) for p in root.rglob("*") if p.is_file())
    dispatch(load(root), load_policy(root), wave=1)
    after = sorted(p.relative_to(root) for p in root.rglob("*") if p.is_file())
    assert before == after


# --- validate_plan: the falsifiable half (GR-12) ----------------------------

def test_validate_plan_refuses_a_real_lane_collision():
    document = {
        "wave": 1,
        "assignments": [
            {"ticket": "kushin77/agent-orchestrator#10", "lane": "registry"},
            {"ticket": "kushin77/agent-orchestrator#11", "lane": "registry"},
        ],
        "deferred": [],
        "unowned_risks": [],
    }
    findings = validate_plan(document)
    assert any(f.code == "dispatch-lane-collision" for f in findings)
    collision = next(f for f in findings if f.code == "dispatch-lane-collision")
    assert "#10" in collision.detail and "#11" in collision.detail


def test_validate_plan_accepts_one_task_per_lane():
    document = {
        "wave": 1,
        "assignments": [
            {"ticket": "kushin77/agent-orchestrator#10", "lane": "registry"},
            {"ticket": "kushin77/agent-orchestrator#11", "lane": "gateway"},
        ],
        "deferred": [],
        "unowned_risks": [],
    }
    assert validate_plan(document) == []


def test_validate_plan_refuses_a_silently_dropped_unowned_risk():
    document = {
        "wave": 1,
        "assignments": [],
        "deferred": [],
        "unowned_risks": [],
        "_assert_unowned_risks": ["kushin77/agent-orchestrator#99"],
    }
    findings = validate_plan(document)
    assert any(f.code == "dispatch-unowned-risk" and f.subject == "kushin77/agent-orchestrator#99" for f in findings)


def test_validate_plan_accepts_an_unowned_risk_named_in_the_plan():
    document = {
        "wave": 1,
        "assignments": [],
        "deferred": [],
        "unowned_risks": [{"ticket": "kushin77/agent-orchestrator#99", "reason": "deferred for triage"}],
        "_assert_unowned_risks": ["kushin77/agent-orchestrator#99"],
    }
    assert validate_plan(document) == []


# --- ADR-0012(b): persona routing, not runtime coupling ---------------------

def test_default_executor_is_hermes_for_a_task_kind_ticket(root):
    write_board(root, [issue(10, labels=["priority:P0"])])
    view = dispatch(load(root), load_policy(root), wave=1)
    assert view.document["assignments"][0]["executor"] == "hermes"


def test_default_executor_is_paperclip_for_the_lessons_rca_family(root):
    write_board(root, [issue(10, labels=["priority:P0"])])
    graph = load(root)
    # kind is a projected, read-only field in the real pipeline; assert the
    # classifier's own rule directly against a forced kind rather than
    # depending on how a board issue earns the "rca" kind end to end.
    from dispatch import _default_executor
    pol = load_policy(root)
    lane = pol.lane_for(())
    graph.tickets["kushin77/agent-orchestrator#10"]["kind"] = "rca"
    assert _default_executor(graph, "kushin77/agent-orchestrator#10", lane) == "paperclip"


def test_a_lane_executor_override_wins_over_the_kind_default(root):
    write_board(root, [issue(10, labels=["priority:P0", "pillar:guardrails-security"])])
    pol = load_policy(root)
    # guardrails' declared sme is security; no executor override is declared,
    # so the kind default (task -> hermes) still applies — assert that, then
    # assert an explicit override (constructed lane) wins.
    from dispatch import _default_executor
    lane = pol.lane_for(("pillar:guardrails-security",))
    graph = load(root)
    assert _default_executor(graph, "kushin77/agent-orchestrator#10", lane) == "hermes"
    overridden = lane.__class__(**{**lane.__dict__, "executor": "paperclip"})
    assert _default_executor(graph, "kushin77/agent-orchestrator#10", overridden) == "paperclip"


def test_paperclip_ticket_record_is_a_partial_frozen_contract_projection(root):
    write_board(root, [issue(9), issue(10, blocked_by=[9])])
    write_claims(root, [claim(9, agent="agent-a")])
    graph = load(root)
    record = paperclip_ticket_record(graph, "kushin77/agent-orchestrator#10")
    assert record["id"] == "kushin77/agent-orchestrator#10"
    assert record["blocked_by"] == ["#9"]
    assert "owner" not in record  # unclaimed: never fabricated
    assert "status" not in record  # unclaimed: "" is not a valid contract enum value


def test_paperclip_ticket_record_never_carries_an_unknown_contract_key(root):
    write_board(root, [issue(10)])
    record = paperclip_ticket_record(load(root), "kushin77/agent-orchestrator#10")
    frozen_keys = {"id", "owner", "status", "blocked_by", "goal", "evidence", "kind", "facets"}
    assert set(record) <= frozen_keys


# --- --by-cluster ------------------------------------------------------------

def _write_clusters(root, wave=1, batchable=True, priority_rank=1):
    document = {
        "generated_at": "2026-09-20T00:00:00Z",
        "source_repos": ["kushin77/agent-orchestrator"],
        "clusters": [
            {
                "id": "c-rca-backfill-1",
                "family": "RCA backfill",
                "title": "Backfill RCA docs",
                "recipe": "apply the standard RCA template",
                "sme": "sniper-generic",
                "tier": "L0",
                "batchable": batchable,
                "wave": wave,
                "priority_rank": priority_rank,
                "evidence": "2 open issues matched family 'rca' by label scan",
                "issues": [
                    {"repo": "kushin77/agent-orchestrator", "number": 100, "title": "rca 100",
                     "labels": ["type:task"], "age_days": 10, "parent": None},
                    {"repo": "kushin77/agent-orchestrator", "number": 101, "title": "rca 101",
                     "labels": ["type:task"], "age_days": 12, "parent": None},
                ],
            }
        ],
        "unclustered": [],
        "hygiene": {"duplicates": [], "orphan_children": [], "empty_epics": [], "template_gaps": []},
    }
    target = root / "governance" / "pmo"
    target.mkdir(parents=True, exist_ok=True)
    (target / "clusters.json").write_text(json.dumps(document), encoding="utf-8")


def test_by_cluster_dispatches_one_agent_per_batchable_cluster(root):
    write_board(root, [issue(10)])
    _write_clusters(root)
    clusters = clusters_mod.load(root)
    view = dispatch(load(root), load_policy(root), wave=1, by_cluster=True, clusters=clusters)
    assert view.ok
    assert view.document["by_cluster"] is True
    assert len(view.document["assignments"]) == 1
    entry = view.document["assignments"][0]
    assert entry["issue_count"] == 2
    assert entry["issues"] == ["kushin77/agent-orchestrator#100", "kushin77/agent-orchestrator#101"]
    assert entry["agent_brief"].startswith("GOAL:")


def test_by_cluster_defers_a_non_batchable_cluster(root):
    write_board(root, [issue(10)])
    _write_clusters(root, batchable=False)
    clusters = clusters_mod.load(root)
    view = dispatch(load(root), load_policy(root), wave=1, by_cluster=True, clusters=clusters)
    assert view.document["assignments"] == []
    assert any(d["reason"] == "cluster-not-batchable" for d in view.document["deferred"])


def test_by_cluster_defers_a_cluster_from_a_different_wave(root):
    write_board(root, [issue(10)])
    _write_clusters(root, wave=2)
    clusters = clusters_mod.load(root)
    view = dispatch(load(root), load_policy(root), wave=1, by_cluster=True, clusters=clusters)
    assert view.document["assignments"] == []
    assert any("cluster-wave-2-not-requested-wave-1" == d["reason"] for d in view.document["deferred"])


def test_by_cluster_with_no_clusters_json_falls_back_to_per_issue(root):
    write_board(root, [issue(10, labels=["priority:P0"])])
    view = dispatch(load(root), load_policy(root), wave=1, by_cluster=True, clusters=None)
    assert view.document["by_cluster"] is False
    assert view.document["assignments"][0]["ticket"] == "kushin77/agent-orchestrator#10"


def test_real_dispatch_never_silently_drops_an_unowned_live_risk(root):
    write_board(root, [issue(10, labels=["priority:P0"], blocked_by=[])])
    # a live, blocked, owner-less risk (mirrors views.raid's own R fixture)
    write_board(root, [issue(10, labels=["priority:P0"]), issue(11, labels=["priority:P0"], blocked_by=[10])])
    write_claims(root, [claim(11, agent="", at="2026-09-01T00:00:00Z")])
    view = dispatch(load(root), load_policy(root), wave=1)
    ticket11 = "kushin77/agent-orchestrator#11"
    named = {e["ticket"] for e in view.document["assignments"]} | {
        u["ticket"] for u in view.document["unowned_risks"]
    }
    assert ticket11 in named or ticket11 not in load(root).tickets
