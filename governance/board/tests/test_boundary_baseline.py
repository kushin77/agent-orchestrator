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
import cli  # noqa: F401 — imported first: inserts governance/ onto sys.path for board_selfheal
import board_selfheal
from cli import (
    BOUNDARY_VALID_CODES,
    apply_boundary_baseline,
    build_boundary_records,
    load_boundary_baseline,
    run_boundary_check,
)

ROOT = Path(__file__).resolve().parents[3]


#: A generated_at these fixtures share, always inside the freshness tolerance
#: relative to `_FRESH_NOW` below (issue #1631 gave the snapshot an age
#: dimension; these fixtures exist to test the BOUNDARY-LOGIC dimension only).
_FRESH_GENERATED_AT = "2026-09-21T00:00:00Z"
_FRESH_NOW = "2026-09-21T12:00:00Z"


def _snapshot(tmp_path: Path, items: list, generated_at: str | None = _FRESH_GENERATED_AT) -> Path:
    path = tmp_path / "boundary-snapshot.json"
    payload: dict = {"items": items}
    if generated_at is not None:
        payload["generated_at"] = generated_at
    path.write_text(json.dumps(payload), encoding="utf-8")
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
    assert run_boundary_check(snapshot, baseline, now=cli._parse_boundary_iso(_FRESH_NOW)) == EXIT_OK


def test_fresh_child_is_not_ok_and_is_named(tmp_path: Path, capsys):
    items = [
        _child(9999, "## Repo\nprovoked-foreign-repo\n"),
        _tracker(358, state="open"),
    ]
    baseline = _baseline(tmp_path, [])
    snapshot = _snapshot(tmp_path, items)
    rc = run_boundary_check(snapshot, baseline, now=cli._parse_boundary_iso(_FRESH_NOW))
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
    rc = run_boundary_check(snapshot, baseline, now=cli._parse_boundary_iso(_FRESH_NOW))
    assert rc == EXIT_NOT_OK
    err = capsys.readouterr().err
    assert "tracking issue #358 is closed" in err


def test_missing_body_is_cannot_assess_never_ok(tmp_path: Path, capsys):
    items = [{"number": 126, "title": "gap", "state": "open"}]
    baseline = _baseline(tmp_path, [])
    snapshot = _snapshot(tmp_path, items)
    rc = run_boundary_check(snapshot, baseline, now=cli._parse_boundary_iso(_FRESH_NOW))
    assert rc == EXIT_CANNOT_ASSESS
    err = capsys.readouterr().err
    assert "cannot assess" in err


def test_an_unaged_snapshot_is_cannot_assess_never_ok(tmp_path: Path, capsys):
    """The freshness dimension itself (issue #1631): no generated_at at all is
    refused, never read as OK-with-zero-findings."""
    items = [_child(126, "Parent: #125\n## Repo\nsaas-rbac\n")]
    baseline = _baseline(tmp_path, [])
    snapshot = _snapshot(tmp_path, items, generated_at=None)
    rc = run_boundary_check(snapshot, baseline)
    assert rc == EXIT_CANNOT_ASSESS
    err = capsys.readouterr().err
    assert "snapshot stale and refresh impossible" in err
    assert "no generated_at" in err


def test_missing_snapshot_is_cannot_assess(tmp_path: Path, capsys):
    baseline = _baseline(tmp_path, [])
    missing = tmp_path / "not-there.json"
    rc = run_boundary_check(missing, baseline)
    assert rc == EXIT_CANNOT_ASSESS
    assert "CANNOT-ASSESS" in capsys.readouterr().err


def test_committed_snapshot_and_baseline_are_clean(tmp_path: Path):
    """Boundary-LOGIC cleanliness, independent of the committed artifact's real
    wall-clock age (that dimension is asserted separately, issue #1631) — pin
    ``now`` to the snapshot's own ``generated_at`` so this test's verdict never
    flips just because time passed since the file was last refreshed."""
    snapshot = ROOT / ".board/boundary-snapshot.json"
    baseline = ROOT / "governance/board/boundary-baseline.json"
    assert snapshot.exists(), "committed boundary snapshot must exist"
    assert baseline.exists(), "committed boundary baseline must exist"
    generated_at = json.loads(snapshot.read_text(encoding="utf-8"))["generated_at"]
    assert run_boundary_check(snapshot, baseline, now=cli._parse_boundary_iso(generated_at)) == EXIT_OK


# --- snapshot freshness fails closed, never OK-with-zero-findings (#1631) ---


def test_a_stale_snapshot_self_heals_and_the_findings_do_not_vanish(tmp_path, monkeypatch, capsys):
    """THE DEFECT THIS CLOSES (issue #1631): the old code read a rotted
    snapshot as a clean OK, silently dropping real findings. Stale-but-
    refreshable must re-assess against the REFRESHED content and report the
    real findings (NOT-OK here) — never fall back to OK-with-zero-findings."""
    items = [
        _child(126, "Parent: #125\n## Repo\nsaas-rbac\n"),
        _tracker(358, state="closed"),
    ]
    entries = [
        {"code": FINDING_SELF_PARENT, "subject": "#126", "tracked_by": "#358", "reason": "legacy"},
        {"code": FINDING_FOREIGN_REPO_DECLARATION, "subject": "#126", "tracked_by": "#358", "reason": "legacy"},
    ]
    baseline = _baseline(tmp_path, entries)
    snapshot = _snapshot(tmp_path, items, generated_at="2026-01-01T00:00:00Z")

    def _fake_refresh(repo=None, *, runner=None, timeout=None, command=None):
        # The refresh writes the SAME (live-truth) content back, dated fresh —
        # the tracker's closure survives the refresh; it must not disappear.
        fresh = json.loads(snapshot.read_text(encoding="utf-8"))
        fresh["generated_at"] = _FRESH_NOW
        snapshot.write_text(json.dumps(fresh), encoding="utf-8")
        return True, "wrote fresh boundary snapshot"

    monkeypatch.setattr(board_selfheal, "refresh", _fake_refresh)
    rc = run_boundary_check(snapshot, baseline, refresh=True, now=cli._parse_boundary_iso(_FRESH_NOW))
    err = capsys.readouterr().err
    assert rc == EXIT_NOT_OK, err
    assert "tracking issue #358 is closed" in err


def test_a_stale_unrefreshable_snapshot_is_cannot_assess_never_ok(tmp_path, monkeypatch, capsys):
    """FIX's other half: stale-and-unrefreshable is CANNOT-ASSESS (rc 2) and
    names why — never OK-with-zero-findings on a rotted snapshot."""
    items = [{"number": 126, "title": "gap", "state": "open", "body": "no markers here"}]
    baseline = _baseline(tmp_path, [])
    snapshot = _snapshot(tmp_path, items)
    stale = json.loads(snapshot.read_text(encoding="utf-8"))
    stale["generated_at"] = "2026-01-01T00:00:00Z"
    snapshot.write_text(json.dumps(stale), encoding="utf-8")

    def _offline_refresh(repo=None, *, runner=None, timeout=None, command=None):
        return False, "gh: not logged into any GitHub hosts"

    monkeypatch.setattr(board_selfheal, "refresh", _offline_refresh)
    rc = run_boundary_check(
        snapshot, baseline, refresh=True, now=cli._parse_boundary_iso("2026-09-21T12:00:00Z")
    )
    assert rc == EXIT_CANNOT_ASSESS
    err = capsys.readouterr().err
    assert "boundary: CANNOT-ASSESS — snapshot stale and refresh impossible" in err
    assert "gh: not logged into any GitHub hosts" in err
