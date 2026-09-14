"""The ``gates`` view: per-task review-gate state + escalation rung (issue #635).

Acceptance criterion: "``governance/pmo`` views surface the gate state per
task".  The view is a *derivation* over facts the graph already carries — the
ticket's ``status`` (the claim ledger's verdict) plus the board issue's
``review-gate:*`` / ``review-escalation:*`` labels — never a second store.

The findings matter (GR-12): the view must be able to fail.  These tests prove
it fails on the exact condition the workbook forbids (a done task whose gate
reads closed), on a close with no verdict recorded, and on an escalation rung
the declared chain does not name.
"""

from __future__ import annotations

import pytest
from conftest import claim, issue, release, write_board, write_claims

from graph import load
from views import gates

T10 = "kushin77/agent-orchestrator#10"
T11 = "kushin77/agent-orchestrator#11"


def codes(view) -> set[str]:
    return {finding.code for finding in view.findings}


def subjects(view, code: str) -> list[str]:
    return sorted(f.subject for f in view.findings if f.code == code)


def row_for(view, ticket: str) -> dict:
    return next(row for row in view.document["gates"] if row["ticket"] == ticket)


# --- the view is registered and shaped --------------------------------------


def test_gates_is_registered_in_the_view_registry():
    from views import VIEWS

    assert "gates" in VIEWS


def test_gates_surfaces_one_row_per_task(root):
    write_board(root, [issue(10), issue(11)])
    view = gates(load(root))
    assert view.document["view"] == "gates"
    assert view.document["tickets"] == [T10, T11]
    assert [row["ticket"] for row in view.document["gates"]] == [T10, T11]


def test_the_declared_rungs_are_the_coo_then_ceo_chain(root):
    write_board(root, [issue(10)])
    view = gates(load(root))
    assert view.document["rungs"] == ["COO", "CEO"]


# --- gate state per task ----------------------------------------------------


def test_a_released_claim_reads_in_review_therefore_gate_open(root):
    write_board(root, [issue(10)])
    write_claims(root, [release(10)])
    view = gates(load(root))
    row = row_for(view, T10)
    assert row["status"] == "in-review"
    assert row["gate"] == "open"


def test_an_in_progress_task_has_a_pending_gate(root):
    write_board(root, [issue(10)])
    write_claims(root, [claim(10, agent="agent-a")])
    view = gates(load(root))
    assert row_for(view, T10)["gate"] == "pending"


def test_a_red_gate_label_closes_the_gate(root):
    write_board(root, [issue(10, labels=["review-gate:red"])])
    write_claims(root, [claim(10, agent="agent-a")])
    view = gates(load(root))
    row = row_for(view, T10)
    assert row["gate"] == "closed"
    assert row["verdict"] == "red"


def test_an_open_verdict_label_opens_the_gate(root):
    write_board(root, [issue(10, labels=["review-gate:open"])])
    write_claims(root, [claim(10, agent="agent-a")])
    view = gates(load(root))
    assert row_for(view, T10)["gate"] == "open"


def test_a_rejected_verdict_label_closes_the_gate(root):
    write_board(root, [issue(10, labels=["review-gate:rejected"])])
    write_claims(root, [claim(10, agent="agent-a")])
    view = gates(load(root))
    assert row_for(view, T10)["gate"] == "closed"


# --- the escalation rung reaches the view -----------------------------------


def test_a_closed_blocked_task_names_the_rung_it_escalated_to(root):
    write_board(
        root,
        [
            issue(
                10,
                labels=["review-gate:red", "review-escalation:CEO"],
            )
        ],
    )
    write_claims(root, [claim(10, agent="agent-a")])
    view = gates(load(root))
    row = row_for(view, T10)
    assert row["gate"] == "closed"
    assert row["escalation"] == "CEO"


def test_the_coo_rung_is_accepted(root):
    write_board(root, [issue(10, labels=["review-gate:red", "review-escalation:COO"])])
    write_claims(root, [claim(10, agent="agent-a")])
    view = gates(load(root))
    assert row_for(view, T10)["escalation"] == "COO"
    assert "gate-escalation-unrunged" not in codes(view)


# --- findings: the view can fail -------------------------------------------


def test_a_done_task_with_a_closed_gate_is_a_finding(root):
    """The exact condition the acceptance criteria forbid: a closed task with a
    red gate.  The view must name it, not report OK."""
    write_board(
        root,
        [issue(10, state="CLOSED", labels=["review-gate:red"])],
    )
    write_claims(root, [claim(10, agent="agent-a")])
    view = gates(load(root))
    assert row_for(view, T10)["status"] == "done"
    assert row_for(view, T10)["gate"] == "closed"
    assert "gate-closed-but-done" in codes(view)
    assert subjects(view, "gate-closed-but-done") == [T10]


def test_a_gate_scoped_done_task_with_no_verdict_is_a_finding(root):
    # the task opted into the gate (an escalation label) but recorded no verdict
    write_board(root, [issue(10, state="CLOSED", labels=["review-escalation:CEO"])])
    write_claims(root, [claim(10, agent="agent-a")])
    view = gates(load(root))
    assert "gate-verdict-missing" in codes(view)
    assert subjects(view, "gate-verdict-missing") == [T10]


def test_a_legacy_done_task_with_no_gate_labels_is_not_a_finding(root):
    """Adoption is explicit: history that predates the gate is not failed."""
    write_board(root, [issue(10, state="CLOSED")])
    write_claims(root, [claim(10, agent="agent-a")])
    view = gates(load(root))
    assert view.ok
    assert row_for(view, T10)["gate"] == "open"  # a done task's gate reads open
    assert row_for(view, T10)["verdict"] == ""


def test_a_done_task_with_an_open_verdict_is_clean(root):
    write_board(root, [issue(10, state="CLOSED", labels=["review-gate:open"])])
    write_claims(root, [claim(10, agent="agent-a")])
    view = gates(load(root))
    assert view.ok
    assert row_for(view, T10)["gate"] == "open"


def test_a_closed_gate_with_no_rung_is_a_finding(root):
    write_board(root, [issue(10, labels=["review-gate:red"])])
    write_claims(root, [claim(10, agent="agent-a")])
    view = gates(load(root))
    assert "gate-escalation-missing" in codes(view)
    assert subjects(view, "gate-escalation-missing") == [T10]


def test_an_unrunged_escalation_is_a_finding(root):
    write_board(
        root,
        [issue(10, labels=["review-gate:red", "review-escalation:CTO"])],
    )
    write_claims(root, [claim(10, agent="agent-a")])
    view = gates(load(root))
    assert "gate-escalation-unrunged" in codes(view)
    assert subjects(view, "gate-escalation-unrunged") == [T10]


def test_the_view_is_deterministic(root):
    write_board(
        root,
        [
            issue(10, labels=["review-gate:red", "review-escalation:CEO"]),
            issue(11),
        ],
    )
    write_claims(root, [claim(10, agent="agent-a"), claim(11, agent="agent-b")])
    graph = load(root)
    assert gates(graph).text() == gates(graph).text()
