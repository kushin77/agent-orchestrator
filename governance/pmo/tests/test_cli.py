"""The CLI's tri-state contract and its ``--check`` gate mode (issue #403)."""

from __future__ import annotations

import json

import pytest
from conftest import claim, issue, write_board, write_claims

import cli


def run(argv) -> int:
    return cli.main(argv)


def test_every_view_runs_offline_over_the_graph(root):
    write_board(root, [issue(4), issue(10, parent=4, blocked_by=[9]), issue(9)])
    write_claims(root, [claim(10, agent="agent-a", lane="lane-a")])
    for view in ("deps", "lanes", "report", "raid", "aging"):
        assert run([view, "--root", str(root), "--json"]) == 0


def test_not_ok_exits_one_and_names_the_offender(root, capsys):
    write_board(root, [issue(10, labels=["priority:P0"])])
    write_claims(root, [claim(10, agent="")])
    assert run(["raid", "--root", str(root), "--check"]) == 1
    captured = capsys.readouterr()
    assert "raid-unowned-risk" in captured.err
    assert "kushin77/agent-orchestrator#10" in captured.err


def test_cannot_assess_exits_two_and_is_never_a_pass(root):
    # No board snapshot at all: the graph cannot be built, so the PMO has no
    # honest view — and the exit code says so rather than reporting OK.
    assert run(["raid", "--root", str(root), "--check"]) == 2


def test_check_reconciles_against_a_saved_view_and_names_a_removed_ticket(root, tmp_path, capsys):
    write_board(root, [issue(10, labels=["priority:P0"]), issue(11, labels=["priority:P0"])])
    saved = tmp_path / "saved-raid.json"
    assert run(["raid", "--root", str(root), "--json"]) == 0
    saved.write_text(capsys.readouterr().out, encoding="utf-8")

    write_board(root, [issue(10, labels=["priority:P0"])])
    assert run(["raid", "--root", str(root), "--check", "--against", str(saved)]) == 1
    captured = capsys.readouterr()
    assert "view-stale-ticket" in captured.err
    assert "kushin77/agent-orchestrator#11" in captured.err


def test_check_reconciles_a_stale_rollup(root, tmp_path, capsys):
    write_board(root, [issue(4), issue(10, parent=4)])
    assert run(["report", "--root", str(root), "--json"]) == 0
    stale = tmp_path / "saved-report.json"
    stale.write_text(capsys.readouterr().out, encoding="utf-8")

    write_board(root, [issue(4), issue(10, parent=4), issue(11, parent=4)])
    assert run(["report", "--root", str(root), "--check", "--against", str(stale)]) == 1
    assert "view-stale-count" in capsys.readouterr().err


def test_json_is_the_whole_of_stdout(root, capsys):
    write_board(root, [issue(10)])
    assert run(["report", "--root", str(root), "--json"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["view"] == "report"


def test_now_overrides_the_clock(root, capsys):
    write_board(root, [issue(10)], generated_at="2026-09-14T00:00:00Z")
    write_claims(root, [claim(10, agent="agent-a", at="2026-06-01T00:00:00Z")])
    assert run(["aging", "--root", str(root), "--json", "--now", "2026-06-10T00:00:00Z"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["clock"] == "2026-06-10T00:00:00Z"
    assert document["items"] == [], "nine days is below the watch tier"


def test_against_a_missing_view_is_cannot_assess(root, tmp_path):
    write_board(root, [issue(10)])
    assert run(["raid", "--root", str(root), "--check", "--against", str(tmp_path / "nope.json")]) == 2
