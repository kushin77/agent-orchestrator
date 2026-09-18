"""The five derived views, asserted against a ticket set with a known shape."""

from __future__ import annotations

import json

import pytest
import importlib.util as _importlib_util  # noqa: E402
from pathlib import Path as _ConftestPath  # noqa: E402

# A bare ``from conftest import ...`` is not safe here: when this suite is
# collected alongside other governance suites, every one of their
# ``tests/conftest.py`` files lands under the same bare module identity
# ``conftest`` in ``sys.modules``, so whichever conftest is imported LAST
# silently wins the name for the rest of collection (issues #699, #702, #1042).
# Loading this file's own conftest by absolute path guarantees this module
# always gets ITS directory's conftest regardless of collection order.
_conftest_spec = _importlib_util.spec_from_file_location(
    "governance_pmo_tests_conftest", _ConftestPath(__file__).with_name("conftest.py")
)
_conftest = _importlib_util.module_from_spec(_conftest_spec)
_conftest_spec.loader.exec_module(_conftest)
claim = _conftest.claim
issue = _conftest.issue
release = _conftest.release
write_board = _conftest.write_board
write_claims = _conftest.write_claims
write_lessons = _conftest.write_lessons

from graph import load
from views import aging, deps, lanes, raid, reconcile, report

T10 = "kushin77/agent-orchestrator#10"
T11 = "kushin77/agent-orchestrator#11"


def codes(view) -> set[str]:
    return {finding.code for finding in view.findings}


def subjects(view, code: str) -> list[str]:
    return sorted(f.subject for f in view.findings if f.code == code)


# --- deps -------------------------------------------------------------------

def test_deps_reports_blocked_and_goal_edges_exactly(root):
    write_board(root, [issue(4), issue(10, parent=4, blocked_by=[9]), issue(9)])
    view = deps(load(root))
    assert view.ok
    assert view.document["blocked"] == [
        {"ticket": T10, "blocked_by": ["#9"], "status": "", "owner": ""}
    ]
    assert view.document["goals"] == [{"ticket": T10, "goal": "#4"}]


def test_deps_flags_an_orphan_blocked_by_edge(root):
    write_board(root, [issue(10, blocked_by=[999999])])
    view = deps(load(root))
    assert codes(view) == {"deps-orphan-edge"}
    assert subjects(view, "deps-orphan-edge") == [f"{T10} → #999999"]


def test_deps_flags_a_cycle(root):
    write_board(root, [issue(10, blocked_by=[11]), issue(11, blocked_by=[10])])
    view = deps(load(root))
    assert "deps-cycle" in codes(view)


# --- lanes ------------------------------------------------------------------

def test_lanes_groups_by_owner_and_lane(root):
    write_board(root, [issue(10), issue(11, blocked_by=[])])
    write_claims(root, [claim(10, agent="agent-a", lane="lane-a"), claim(11, agent="agent-a", lane="lane-a")])
    view = lanes(load(root))
    assert view.ok
    assert view.document["lanes"] == [
        {
            "owner": "agent-a",
            "lane": "lane-a",
            "tickets": [T10, T11],
            "in_progress": 2,
            "blocked": 0,
            "in_review": 0,
            "done": 0,
        }
    ]


def test_lanes_flags_in_progress_work_with_no_owner(root):
    write_board(root, [issue(10)])
    write_claims(root, [claim(10, agent="")])
    view = lanes(load(root))
    assert codes(view) == {"lanes-unowned-work"}
    assert subjects(view, "lanes-unowned-work") == [T10]


# --- report -----------------------------------------------------------------

def test_report_buckets_by_goal_status_and_owner(root):
    write_board(root, [issue(4), issue(10, parent=4), issue(11, parent=4)])
    write_claims(root, [claim(10, agent="agent-a")])
    view = report(load(root))
    buckets = {row["key"]: row["count"] for row in view.document["by_status"]}
    assert buckets == {"in-progress": 1, "(no status)": 2}
    by_goal = {row["key"]: row["count"] for row in view.document["by_goal"]}
    assert by_goal["#4"] == 2
    assert {row["key"]: row["count"] for row in view.document["by_owner"]} == {
        "agent-a": 1,
        "(unowned)": 2,
    }
    assert view.document["counted"] == 3


# --- raid -------------------------------------------------------------------

def test_raid_live_risk_set_equals_the_graph_exactly(root):
    """The acceptance rule: the derived RAID set equals the expected set exactly."""
    write_board(
        root,
        [
            issue(4),
            issue(10, parent=4, blocked_by=[9], labels=["priority:P0"]),
            issue(9, parent=4),
        ],
    )
    write_claims(root, [claim(10, agent="agent-a", lane="lane-a")])
    view = raid(load(root))
    assert view.ok
    assert [row["ticket"] for row in view.document["risks"]] == [T10]
    assert [row["ticket"] for row in view.document["assumptions"]] == []


def test_raid_flags_a_live_risk_that_names_no_owner(root):
    write_board(root, [issue(10, labels=["priority:P0"])])
    write_claims(root, [claim(10, agent="")])
    view = raid(load(root))
    assert codes(view) == {"raid-unowned-risk"}
    assert subjects(view, "raid-unowned-risk") == [T10]


def test_raid_dormant_risk_becomes_an_assumption_not_a_failure(root):
    write_board(root, [issue(11, labels=["priority:P2"])])
    view = raid(load(root))
    assert view.ok
    assert [row["ticket"] for row in view.document["assumptions"]] == [T11]


def test_raid_flags_an_orphan_remediation_edge(root):
    # A live risk whose remediation is a corrective action no ticket carries.
    write_board(root, [issue(10, labels=["priority:P1"])])
    write_claims(root, [claim(10, agent="agent-a")])
    graph = load(root)
    graph.tickets[T10]["facets"] = {"raid": {"risk": "high", "remediation": "CA-9999"}}
    view = raid(graph)
    assert "raid-orphan-remediation" in codes(view)


def test_raid_sections_cover_incidents_and_decisions(root):
    write_board(root, [issue(4), issue(10, parent=4, blocked_by=[9]), issue(9)])
    write_lessons(
        root,
        [
            {"id": "INC-0001", "kind": "incident", "status": "closed", "date": "2026-08-01"},
            {"id": "RCA-0001", "kind": "rca", "incident": "INC-0001", "status": "closed", "date": "2026-08-02"},
            {
                "id": "CA-0001",
                "kind": "corrective-action",
                "rca": "RCA-0001",
                "status": "open",
                "date": "2026-08-03",
            },
            {"id": "SUGGEST-0001", "kind": "lesson", "rca": "RCA-0001", "status": "open", "date": "2026-08-04"},
        ],
    )
    view = raid(load(root))
    kinds = {row["ticket"]: row["kind"] for row in view.document["incidents"]}
    assert kinds == {"INC-0001": "incident", "RCA-0001": "rca", "CA-0001": "corrective-action"}
    assert [row["ticket"] for row in view.document["decisions"]] == ["SUGGEST-0001"]
    relations = {(row["ticket"], row["relation"]) for row in view.document["dependencies"]}
    assert (T10, "blocked_by") in relations and (T10, "goal") in relations


# --- aging ------------------------------------------------------------------

def test_aging_tiers_are_explicit_and_name_their_owner(root):
    write_board(root, [issue(10)], generated_at="2026-09-14T00:00:00Z")
    write_claims(root, [claim(10, agent="agent-a", at="2026-06-01T00:00:00Z")])
    view = aging(load(root))
    assert view.ok
    assert view.document["items"] == [
        {
            "ticket": T10,
            "tier": "postmortem",
            "age_days": 105.0,
            "anchor": "2026-06-01T00:00:00Z",
            "kind": "task",
            "status": "in-progress",
            "owner": "agent-a",
        }
    ]


def test_aging_item_with_no_owner_fails(root):
    write_board(root, [issue(10)], generated_at="2026-09-14T00:00:00Z")
    write_claims(root, [release(10, at="2026-06-01T00:00:00Z")])
    view = aging(load(root))
    assert codes(view) == {"aging-unowned-item"}
    assert subjects(view, "aging-unowned-item") == [T10]


@pytest.mark.parametrize(
    ("days", "tier"),
    [(10.0, None), (14.0, "watch"), (30.0, "attention"), (60.0, "red"), (90.0, "postmortem")],
)
def test_aging_tier_thresholds(root, days, tier):
    clock = "2026-09-14T00:00:00Z"
    write_board(root, [issue(10)], generated_at=clock)
    write_claims(root, [claim(10, agent="agent-a", at=_shift(clock, -days))])
    view = aging(load(root))
    tiers = [row["tier"] for row in view.document["items"]]
    assert tiers == ([] if tier is None else [tier])


def _shift(iso: str, days: float) -> str:
    from datetime import timedelta

    from graph import parse_timestamp

    stamp = parse_timestamp(iso)
    assert stamp is not None
    return (stamp + timedelta(days=days)).isoformat()


def test_aging_reports_candidates_it_cannot_date(root):
    write_board(root, [issue(10, labels=["priority:P0"])])
    view = aging(load(root))
    assert view.ok
    assert view.document["items"] == []
    assert view.document["unauditable"] == [
        {"ticket": T10, "reason": "the board snapshot carries no filed-at timestamp"}
    ]
    assert view.document["candidates"] == 1


# --- reconcile --------------------------------------------------------------

def test_reconcile_names_a_ticket_left_behind_in_a_stale_view(root):
    write_board(root, [issue(10, labels=["priority:P0"]), issue(11, labels=["priority:P0"])])
    saved = json.loads(raid(load(root)).text())
    write_board(root, [issue(10, labels=["priority:P0"])])
    findings = reconcile(raid(load(root)), saved)
    assert "view-stale-ticket" in {f.code for f in findings}
    assert subjects_for(findings, "view-stale-ticket") == [T11]


def subjects_for(findings, code: str) -> list[str]:
    return sorted(f.subject for f in findings if f.code == code)


def test_reconcile_flags_a_stale_rollup_count(root):
    write_board(root, [issue(4), issue(10, parent=4)])
    saved = json.loads(report(load(root)).text())
    write_board(root, [issue(4), issue(10, parent=4), issue(11, parent=4)])
    findings = reconcile(report(load(root)), saved)
    assert findings, "a rollup whose counts moved must not reconcile"
    assert "view-stale-count" in {f.code for f in findings}


def test_reconcile_accepts_a_view_of_the_same_graph(root):
    write_board(root, [issue(10, labels=["priority:P0"])])
    saved = json.loads(raid(load(root)).text())
    assert reconcile(raid(load(root)), saved) == []
