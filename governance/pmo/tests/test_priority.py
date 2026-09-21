"""``priority`` — a single explainable order over open work (issue #403 follow-on)."""

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

from graph import load
from policy import load as load_policy
from priority import priority


def test_p0_outranks_p1_all_else_equal(root):
    write_board(root, [issue(10, labels=["priority:P0"]), issue(11, labels=["priority:P1"])])
    pol = load_policy(root)
    view = priority(load(root), pol)
    ranked = [item["number"] for item in view.document["items"]]
    assert ranked.index(10) < ranked.index(11)
    assert view.ok


def test_ready_work_outranks_blocked_work_at_the_same_priority(root):
    write_board(
        root,
        [
            issue(9, labels=["priority:P0"]),
            issue(10, labels=["priority:P0"], blocked_by=[9]),
            issue(11, labels=["priority:P0"]),
        ],
    )
    view = priority(load(root), load_policy(root))
    ranked = {item["number"]: item for item in view.document["items"]}
    assert ranked[10]["ready"] is False
    assert ranked[11]["ready"] is True
    assert ranked[11]["score"] > ranked[10]["score"]


def test_a_closed_blocker_makes_the_dependent_ready(root):
    write_board(
        root,
        [
            issue(9, state="CLOSED"),
            issue(10, blocked_by=[9]),
        ],
    )
    view = priority(load(root), load_policy(root))
    ranked = {item["number"]: item for item in view.document["items"]}
    assert ranked[10]["ready"] is True


def test_fanout_rewards_a_ticket_that_unblocks_more_work(root):
    write_board(
        root,
        [
            issue(9),  # unblocks two others
            issue(10),  # unblocks one other
            issue(20, blocked_by=[9]),
            issue(21, blocked_by=[9]),
            issue(22, blocked_by=[10]),
        ],
    )
    view = priority(load(root), load_policy(root))
    ranked = {item["number"]: item for item in view.document["items"]}
    assert ranked[9]["terms"]["fanout"]["blockers_of_others"] == 2
    assert ranked[10]["terms"]["fanout"]["blockers_of_others"] == 1
    assert ranked[9]["score"] > ranked[10]["score"]


def test_owner_capacity_depriotises_but_never_zeroes_the_score(root):
    write_board(root, [issue(10, labels=["priority:P0"]), issue(11, labels=["priority:P0"])])
    write_claims(root, [claim(10, agent="agent-a", at="2026-09-01T00:00:00Z")])
    view = priority(load(root), load_policy(root))
    ranked = {item["number"]: item for item in view.document["items"]}
    assert ranked[10]["score"] > 0
    assert ranked[10]["score"] < ranked[11]["score"]


def test_sla_breach_is_carried_as_unsourced_not_fabricated(root):
    write_board(root, [issue(10)])
    view = priority(load(root), load_policy(root))
    assert view.document["unsourced"]["sla_breach"]["weight"] == 0
    assert "no SLA ledger" in view.document["unsourced"]["sla_breach"]["reason"]


def test_a_closed_ticket_is_not_ranked(root):
    write_board(root, [issue(10, state="CLOSED", closed_at="2026-09-01T00:00:00Z")])
    view = priority(load(root), load_policy(root))
    assert view.document["items"] == []


def test_priority_is_deterministic_across_two_derivations(root):
    write_board(
        root,
        [issue(n, labels=["priority:P1"]) for n in range(10, 30)],
    )
    pol = load_policy(root)
    graph = load(root)
    first = priority(graph, pol).text()
    second = priority(graph, pol).text()
    assert first == second
