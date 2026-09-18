"""Board-snapshot freshness tests (issue #1077).

Every case pins ``now`` and writes its own snapshot, so the verdict is a property
of the fixture and never of the day the suite happens to run — the wall clock is
read exactly once, by the gate's assertion on the real snapshot, and never here.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from freshness import DEFAULT_MAX_AGE_HOURS, REFRESH_COMMAND, assess, parse_iso
from model import CODE_BOARD_STALE, CODE_BOARD_UNAGED, CannotAssess

NOW = datetime(2026, 9, 17, 13, 0, tzinfo=timezone.utc)


def write_snapshot(root: Path, generated_at: str | None) -> None:
    payload: dict = {"source": "kushin77/agent-orchestrator", "issues": []}
    if generated_at is not None:
        payload["generated_at"] = generated_at
    path = root / ".board" / "snapshot.json"
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def test_the_declared_tolerance_is_the_documented_one() -> None:
    # The number is load-bearing documentation: freshness.py explains why it is 72h
    # and not the dispatch loop's 15-minute liveness tolerance.
    assert DEFAULT_MAX_AGE_HOURS == 72


def test_a_snapshot_inside_the_tolerance_is_ok(root: Path) -> None:
    write_snapshot(root, "2026-09-17T12:00:00Z")
    report = assess(root, now=NOW)
    assert report.ok
    assert report.age_hours == pytest.approx(1.0)
    assert report.violations == ()
    assert "2026-09-17T12:00:00Z" in report.render()


def test_the_tolerance_boundary_is_inclusive(root: Path) -> None:
    write_snapshot(root, "2026-09-14T13:00:00Z")  # exactly 72h
    assert assess(root, now=NOW).ok
    write_snapshot(root, "2026-09-14T12:59:59Z")  # one second past
    assert not assess(root, now=NOW).ok


def test_a_snapshot_past_the_tolerance_is_refused_and_names_the_remedy(root: Path) -> None:
    write_snapshot(root, "2026-09-01T00:00:00Z")
    report = assess(root, now=NOW)
    assert not report.ok
    (violation,) = report.violations
    assert violation.code == CODE_BOARD_STALE
    assert violation.subject == ".board/snapshot.json"
    assert violation.where == ".board/snapshot.json"
    assert "2026-09-01T00:00:00Z" in violation.render()
    assert REFRESH_COMMAND in violation.render()


def test_an_absent_age_is_refused(root: Path) -> None:
    write_snapshot(root, None)
    report = assess(root, now=NOW)
    assert not report.ok
    assert report.violations[0].code == CODE_BOARD_UNAGED
    assert report.age_hours == float("inf")
    assert REFRESH_COMMAND in report.violations[0].render()


def test_an_unparseable_age_is_refused(root: Path) -> None:
    write_snapshot(root, "not-a-timestamp")
    report = assess(root, now=NOW)
    assert not report.ok
    assert report.violations[0].code == CODE_BOARD_UNAGED
    assert "not-a-timestamp" in report.violations[0].render()


def test_a_missing_snapshot_cannot_be_assessed(root: Path) -> None:
    with pytest.raises(CannotAssess):
        assess(root, now=NOW)


def test_an_unparseable_snapshot_cannot_be_assessed(root: Path) -> None:
    (root / ".board" / "snapshot.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(CannotAssess):
        assess(root, now=NOW)


def test_a_snapshot_that_is_not_an_object_cannot_be_assessed(root: Path) -> None:
    (root / ".board" / "snapshot.json").write_text("[1, 2]\n", encoding="utf-8")
    with pytest.raises(CannotAssess):
        assess(root, now=NOW)


def test_the_tolerance_override_is_honoured(root: Path) -> None:
    write_snapshot(root, "2026-09-17T12:00:00Z")
    assert assess(root, now=NOW).ok
    assert not assess(root, max_age_hours=0, now=NOW).ok


def test_parse_iso_reads_the_writers_form_and_a_naive_one_as_utc() -> None:
    assert parse_iso("2026-09-17T13:00:00Z") == NOW
    assert parse_iso("2026-09-17T13:00:00") == NOW
    assert parse_iso("2026-09-17T09:00:00-04:00") == NOW
    with pytest.raises(ValueError):
        parse_iso("")


def test_the_verdict_does_not_drift_between_identical_evaluations(root: Path) -> None:
    write_snapshot(root, "2026-09-01T00:00:00Z")
    first = assess(root, now=NOW)
    second = assess(root, now=NOW)
    assert first.render() == second.render()
    assert first.age_hours == second.age_hours
    assert [violation.render() for violation in first.violations] == [
        violation.render() for violation in second.violations
    ]
