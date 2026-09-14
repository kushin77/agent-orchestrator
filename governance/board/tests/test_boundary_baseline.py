"""Behavioural tests for the boundary baseline and export shape (issue #388).

These are offline: they drive ``build_boundary_records``, ``load_boundary_baseline``,
``apply_boundary_baseline`` and ``run_boundary_check`` with in-memory payloads and
never call the network. The suite pins:

* the export shape (the 9 fields the committed boundary snapshot carries,
  including the body the detector reads, and the parent/blocked_by edges parsed
  from the body);
* the quarantine honour rule (an entry excused only while its tracker is open);
* the stale rule (a quarantine whose tracker closed is itself a finding);
* the tri-state contract of the gate (a missing body is CANNOT-ASSESS, never OK;
  a fresh non-quarantined child is NOT-OK; a clean quarantined board is OK).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from boundary import (
    EXIT_CANNOT_ASSESS,
    EXIT_NOT_OK,
    EXIT_OK,
    FINDING_FOREIGN_REPO_DECLARATION,
    FINDING_SELF_PARENT,
    Finding,
)
from cli import (
    BOUNDARY_VALID_CODES,
    apply_boundary_baseline,
    build_boundary_records,
    load_boundary_baseline,
    run_boundary_check,
)

ROOT = Path(__file__).resolve().parents[3]


def _snapshot(tmp_path: Path, items: list) -> Path:
    path = tmp_path / "boundary-snapshot.json"
    path.write_text(json.dumps({"items": items}), encoding="utf-8")
    return path


def _baseline(tmp_path: Path, entries: list) -> Path:
    path = tmp_path / "boundary-baseline.json"
    path.write_text(json.dumps({"version": 1, "quarantine": entries}), encoding="utf-8")
    return path


def _child(number: int, body: str, state: str = "open") -> dict:
    return {"number": number, "title": "gap", "state": state, "body": body}


def _tracker(number: int, state: str = "open") -> dict:
    return {"number": number, "title": "tracker", "state": state, "body": ""}


# --- export shape -----------------------------------------------------------


def test_build_boundary_records_carries_the_nine_fields():
    records = [
        {
            "number": 126,
            "title": "gap",
            "state": "OPEN",
            "milestone": {"title": "M26"},
            "labels": [{"name": "pillar:autonomous-ops"}, {"name": "priority:P2"}],
            "body": "Parent: #125\nBlocked-by: #124, #123\n\n## Repo\nsaas-rbac\n",
            "closedAt": "2026-09-13T00:00:00Z",
        }
    ]
    built = build_boundary_records(records)
    assert len(built) == 1
    record = built[0]
    assert sorted(record.keys()) == [
        "blocked_by",
        "body",
        "closed_at",
        "labels",
        "milestone",
        "number",
        "parent",
        "state",
        "title",
    ]
    assert record["number"] == 126
    assert record["state"] == "OPEN"
    assert record["milestone"] == "M26"
    assert record["labels"] == ["pillar:autonomous-ops", "priority:P2"]
    assert record["parent"] == 125
    assert record["blocked_by"] == [123, 124]
    assert record["closed_at"] == "2026-09-13T00:00:00Z"
    assert "## Repo\nsaas-rbac" in record["body"]


def test_build_boundary_records_defaults_missing_fields():
    records = [{"number": 7, "title": "bare", "body": ""}]
    record = build_boundary_records(records)[0]
    assert record["state"] == "open"
    assert record["labels"] == []
    assert record["milestone"] == ""
    assert record["parent"] is None
    assert record["blocked_by"] == []
    assert record["closed_at"] == ""
    assert record["body"] == ""


def test_committed_baseline_quarantines_the_eleven_children_by_name():
    entries = load_boundary_baseline(ROOT / "governance/board/boundary-baseline.json")
    subjects = sorted({entry["subject"] for entry in entries})
    assert subjects == ["#126", "#127", "#128", "#129", "#131", "#132", "#133", "#134", "#135", "#136", "#137"]
    assert all(entry["tracked_by"] == "#358" for entry in entries)
    assert all(entry["code"] in BOUNDARY_VALID_CODES for entry in entries)
    # Each child is excused for BOTH kinds the live body actually fires.
    for subject in subjects:
        kinds = {entry["code"] for entry in entries if entry["subject"] == subject}
        assert kinds == {FINDING_SELF_PARENT, FINDING_FOREIGN_REPO_DECLARATION}


# --- quarantine honour / stale ----------------------------------------------


def _finding(number: int, kind: str) -> Finding:
    return Finding(issue=number, title="t", finding=kind, detail="d")


def test_quarantined_finding_is_excused_while_tracker_is_open():
    entries = [
        {"code": FINDING_SELF_PARENT, "subject": "#126", "tracked_by": "#358", "reason": "legacy"},
    ]
    surviving, stale = apply_boundary_baseline(
        [_finding(126, FINDING_SELF_PARENT)], entries, {"#358": "open"}
    )
    assert surviving == []
    assert stale == []


def test_non_quarantined_finding_survives():
    entries = [
        {"code": FINDING_SELF_PARENT, "subject": "#126", "tracked_by": "#358", "reason": "legacy"},
    ]
    surviving, stale = apply_boundary_baseline(
        [_finding(127, FINDING_SELF_PARENT)], entries, {"#358": "open"}
    )
    assert [f.issue for f in surviving] == [127]
    assert stale == []


def test_quarantine_is_stale_once_its_tracker_closes():
    entries = [
        {"code": FINDING_SELF_PARENT, "subject": "#126", "tracked_by": "#358", "reason": "legacy"},
    ]
    surviving, stale = apply_boundary_baseline([], entries, {"#358": "closed"})
    assert surviving == []
    assert len(stale) == 1
    assert "#126" in stale[0] and "#358 is closed" in stale[0]


def test_quarantine_naming_an_unknown_kind_is_stale():
    entries = [{"code": "not-a-kind", "subject": "#126", "tracked_by": "#358", "reason": "legacy"}]
    surviving, stale = apply_boundary_baseline([], entries, {"#358": "open"})
    assert surviving == []
    assert len(stale) == 1
    assert "unknown finding kind" in stale[0]


# --- the tri-state gate -----------------------------------------------------


def test_clean_quarantined_board_is_ok(tmp_path: Path):
    items = [
        _child(126, "Parent: #125\n## Repo\nsaas-rbac\n"),
        _tracker(358, state="open"),
    ]
    entries = [
        {"code": FINDING_SELF_PARENT, "subject": "#126", "tracked_by": "#358", "reason": "legacy"},
        {"code": FINDING_FOREIGN_REPO_DECLARATION, "subject": "#126", "tracked_by": "#358", "reason": "legacy"},
    ]
    snapshot = _snapshot(tmp_path, items)
    baseline = _baseline(tmp_path, entries)
    assert run_boundary_check(snapshot, baseline) == EXIT_OK


def test_fresh_child_is_not_ok_and_is_named(tmp_path: Path, capsys):
    items = [
        _child(9999, "## Repo\nprovoked-foreign-repo\n"),
        _tracker(358, state="open"),
    ]
    baseline = _baseline(tmp_path, [])
    snapshot = _snapshot(tmp_path, items)
    rc = run_boundary_check(snapshot, baseline)
    assert rc == EXIT_NOT_OK
    err = capsys.readouterr().err
    assert "#9999" in err and "provoked-foreign-repo" in err


def test_stale_quarantine_is_not_ok(tmp_path: Path, capsys):
    items = [
        _child(126, "Parent: #125\n## Repo\nsaas-rbac\n"),
        _tracker(358, state="closed"),
    ]
    entries = [
        {"code": FINDING_SELF_PARENT, "subject": "#126", "tracked_by": "#358", "reason": "legacy"},
        {"code": FINDING_FOREIGN_REPO_DECLARATION, "subject": "#126", "tracked_by": "#358", "reason": "legacy"},
    ]
    snapshot = _snapshot(tmp_path, items)
    baseline = _baseline(tmp_path, entries)
    rc = run_boundary_check(snapshot, baseline)
    assert rc == EXIT_NOT_OK
    err = capsys.readouterr().err
    assert "tracking issue #358 is closed" in err


def test_missing_body_is_cannot_assess_never_ok(tmp_path: Path, capsys):
    items = [{"number": 126, "title": "gap", "state": "open"}]
    baseline = _baseline(tmp_path, [])
    snapshot = _snapshot(tmp_path, items)
    rc = run_boundary_check(snapshot, baseline)
    assert rc == EXIT_CANNOT_ASSESS
    err = capsys.readouterr().err
    assert "cannot assess" in err


def test_missing_snapshot_is_cannot_assess(tmp_path: Path, capsys):
    baseline = _baseline(tmp_path, [])
    missing = tmp_path / "not-there.json"
    rc = run_boundary_check(missing, baseline)
    assert rc == EXIT_CANNOT_ASSESS
    assert "CANNOT-ASSESS" in capsys.readouterr().err


def test_committed_snapshot_and_baseline_are_clean(tmp_path: Path):
    snapshot = ROOT / ".board/boundary-snapshot.json"
    baseline = ROOT / "governance/board/boundary-baseline.json"
    assert snapshot.exists(), "committed boundary snapshot must exist"
    assert baseline.exists(), "committed boundary baseline must exist"
    assert run_boundary_check(snapshot, baseline) == EXIT_OK
