"""The graph loader: the ticket graph plus its timestamp/lane side-inputs (#403)."""

from __future__ import annotations

from datetime import timezone

import pytest
from conftest import claim, issue, release, write_board, write_claims, write_lessons

from graph import CannotAssess, age_days, issue_number, load, parse_timestamp


def test_parse_timestamp_handles_z_and_bare_dates():
    zulu = parse_timestamp("2026-09-13T21:24:51Z")
    assert zulu is not None and zulu.tzinfo == timezone.utc
    assert zulu.hour == 21
    bare = parse_timestamp("2026-09-13")
    assert bare is not None and bare.year == 2026 and bare.hour == 0


@pytest.mark.parametrize("value", ["", "not-a-date", None, 7, "2026-13-45T99:99:99Z"])
def test_parse_timestamp_refuses_what_it_cannot_read(value):
    assert parse_timestamp(value) is None


def test_issue_number_reads_issue_tickets_only():
    assert issue_number("kushin77/agent-orchestrator#403") == 403
    assert issue_number("INC-0001") is None


def test_age_days_is_none_when_an_end_is_unreadable():
    assert age_days("", "2026-09-14T00:00:00Z") is None
    assert age_days("2026-09-01T00:00:00Z", "2026-09-14T00:00:00Z") == 13.0


def test_load_builds_the_ticket_graph(root):
    write_board(root, [issue(10, parent=4, labels=["priority:P1"])])
    write_claims(root, [claim(10, agent="agent-a", lane="lane-a")])
    graph = load(root)
    assert "kushin77/agent-orchestrator#10" in graph.tickets
    assert graph.owner("kushin77/agent-orchestrator#10") == "agent-a"
    assert graph.status("kushin77/agent-orchestrator#10") == "in-progress"
    assert graph.lane("kushin77/agent-orchestrator#10") == "lane-a"


def test_load_without_a_board_is_cannot_assess(root):
    with pytest.raises(CannotAssess):
        load(root)


def test_clock_is_the_latest_timestamp_the_inputs_carry(root):
    write_board(root, [issue(10)], generated_at="2026-09-14T00:00:00Z")
    write_claims(root, [claim(10, at="2026-09-01T00:00:00Z")])
    assert load(root).clock == "2026-09-14T00:00:00Z"


def test_anchor_follows_the_last_event_that_set_the_state(root):
    write_board(root, [issue(10)])
    write_claims(root, [claim(10, at="2026-09-01T00:00:00Z"), release(10, at="2026-09-02T00:00:00Z")])
    graph = load(root)
    assert graph.status("kushin77/agent-orchestrator#10") == "in-review"
    assert graph.anchors["kushin77/agent-orchestrator#10"] == "2026-09-02T00:00:00Z"


def test_ledger_nodes_age_from_their_own_date(root):
    write_board(root, [issue(10)])
    write_lessons(root, [{"id": "INC-0001", "kind": "incident", "date": "2026-08-01"}])
    graph = load(root)
    assert graph.anchors["INC-0001"] == "2026-08-01"


def test_a_projection_that_refuses_to_build_is_cannot_assess(root):
    # A claim for an issue the board does not carry is a refusal in the
    # projection, so the PMO has no honest view over it.
    write_board(root, [issue(10)])
    write_claims(root, [claim(999999)])
    with pytest.raises(CannotAssess):
        load(root)


def test_closed_is_the_verdict_or_the_board_state(root):
    write_board(root, [issue(10, state="CLOSED", closed_at="2026-09-10T00:00:00Z")])
    graph = load(root)
    assert graph.is_closed("kushin77/agent-orchestrator#10") is True
