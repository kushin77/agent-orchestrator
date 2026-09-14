"""Hardenings for the claim gate (#170): snapshot staleness + conflict-free storage.

Each refusal is mutation-proven: the same snapshot with only ``generated_at``
shifted past the threshold is refused, and a fresh snapshot still succeeds. The
concurrency tests prove a duplicate claim cannot be lost and two lanes on
different issues never share a path.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone

import claims
import cli
import pytest
import snapshot as snapshot_mod
from model import REASON_NEXT_IN_MILESTONE, Issue, Snapshot


def _at(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _frontier_snapshot(generated_at: str) -> Snapshot:
    return Snapshot(
        generated_at=generated_at,
        source="test",
        issues={601: Issue(601, "frontier", milestone="M25", labels=("type:task",))},
    )


# --- snapshot staleness ------------------------------------------------------


def test_age_minutes_measures_snapshot_age():
    snap = Snapshot(generated_at="2026-09-13T11:45:00Z", source="test", issues={})
    now = datetime(2026, 9, 13, 12, 0, 0, tzinfo=timezone.utc)
    assert snapshot_mod.age_minutes(snap, now) == 15.0
    assert snapshot_mod.is_stale(snap, 15, now) is False  # at the threshold: fresh
    assert snapshot_mod.is_stale(snap, 14, now) is True  # past the threshold: stale


def test_unparseable_generated_at_fails_closed_as_stale():
    snap = Snapshot(generated_at="", source="test", issues={})
    assert snapshot_mod.age_minutes(snap) == float("inf")
    assert snapshot_mod.is_stale(snap) is True


def test_stale_snapshot_claim_is_refused_and_leaves_no_state(tmp_path, snapshot, base_time):
    stale = Snapshot(
        generated_at=_at(base_time - timedelta(minutes=20)),
        source="test",
        issues=snapshot.issues,
    )
    ledger = tmp_path / "claims"
    locks = tmp_path / "locks"

    with pytest.raises(claims.ClaimRefused) as excinfo:
        claims.claim(601, "agent-a", "governance", stale, ledger=ledger, lock_dir=locks, now=base_time)

    assert excinfo.value.reason == "snapshot-stale"
    assert "refresh first" in excinfo.value.detail
    assert not ledger.exists()
    assert not claims.lock_path(601, locks).exists()


def test_staleness_refusal_is_mutation_proven(tmp_path, snapshot, base_time):
    """Same snapshot content; only ``generated_at`` differs: fresh accepts, stale refuses."""
    fresh = Snapshot(generated_at=_at(base_time), source="test", issues=snapshot.issues)
    event = claims.claim(
        601, "agent-a", "governance", fresh, ledger=tmp_path / "fresh", lock_dir=tmp_path / "la", now=base_time
    )
    assert event.event == "claim"

    stale = Snapshot(
        generated_at=_at(base_time - timedelta(minutes=20)),
        source="test",
        issues=snapshot.issues,
    )
    with pytest.raises(claims.ClaimRefused) as excinfo:
        claims.claim(
            601, "agent-b", "governance", stale, ledger=tmp_path / "stale", lock_dir=tmp_path / "lb", now=base_time
        )
    assert excinfo.value.reason == "snapshot-stale"


def test_cli_eligible_refuses_a_stale_snapshot(tmp_path, capsys):
    snap_path = tmp_path / "snapshot.json"
    snapshot_mod.save(_frontier_snapshot("2020-01-01T00:00:00Z"), snap_path)
    rc = cli.main(
        [
            "eligible", "--issue", "601", "--agent", "me",
            "--snapshot", str(snap_path), "--ledger", str(tmp_path / "claims"),
            "--locks", str(tmp_path / "locks"), "--stale-minutes", "15",
        ]
    )
    err = capsys.readouterr().err
    assert rc == 2
    assert "snapshot-stale" in err
    assert "refresh first" in err


def test_cli_eligible_accepts_a_fresh_snapshot(tmp_path, capsys):
    snap_path = tmp_path / "snapshot.json"
    snapshot_mod.save(_frontier_snapshot(snapshot_mod.now_iso()), snap_path)
    rc = cli.main(
        [
            "eligible", "--issue", "601", "--agent", "me",
            "--snapshot", str(snap_path), "--ledger", str(tmp_path / "claims"),
            "--locks", str(tmp_path / "locks"), "--stale-minutes", "15",
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert json.loads(out)["eligible"] is True


def test_cli_claim_refuses_a_stale_snapshot_with_exit_2(tmp_path, capsys):
    snap_path = tmp_path / "snapshot.json"
    snapshot_mod.save(_frontier_snapshot("2020-01-01T00:00:00Z"), snap_path)
    rc = cli.main(
        [
            "claim", "--issue", "601", "--agent", "me", "--lane", "gov",
            "--snapshot", str(snap_path), "--ledger", str(tmp_path / "claims"),
            "--locks", str(tmp_path / "locks"), "--stale-minutes", "15",
        ]
    )
    err = capsys.readouterr().err
    assert rc == 2
    assert "snapshot-stale" in err


# --- conflict-free claim storage --------------------------------------------


def test_claims_are_stored_one_file_per_event(tmp_path, snapshot, base_time):
    ledger = tmp_path / "claims"
    locks = tmp_path / "locks"
    claims.claim(601, "agent-a", "governance", snapshot, ledger=ledger, lock_dir=locks, now=base_time)
    claims.release(601, "agent-a", ledger=ledger, lock_dir=locks, now=base_time)

    files = sorted(ledger.glob("*.json"))
    assert len(files) == 2
    stored = claims.read_ledger(ledger)
    assert [(event.event, event.issue) for event in stored] == [("claim", 601), ("release", 601)]


def test_concurrent_duplicate_claim_is_refused_without_lost_update(tmp_path, snapshot, base_time):
    """Two lanes race for the same issue: exactly one claim lands, one is refused."""
    ledger = tmp_path / "claims"
    locks = tmp_path / "locks"
    barrier = threading.Barrier(2)
    results: dict[str, str] = {}

    def attempt(agent: str) -> None:
        barrier.wait()
        try:
            claims.claim(601, agent, "governance", snapshot, ledger=ledger, lock_dir=locks, now=base_time)
            results[agent] = "accepted"
        except claims.ClaimRefused as exc:
            results[agent] = exc.reason

    threads = [threading.Thread(target=attempt, args=(agent,)) for agent in ("agent-a", "agent-b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    accepted = [agent for agent, outcome in results.items() if outcome == "accepted"]
    refused = [agent for agent, outcome in results.items() if outcome == "already-claimed"]
    assert len(accepted) == 1
    assert len(refused) == 1
    assert len(list(ledger.glob("*.json"))) == 1
    live = claims.active_claims(claims.read_ledger(ledger), base_time)
    assert set(live) == {601}


def test_concurrent_lanes_on_different_issues_touch_disjoint_paths(tmp_path, snapshot, base_time):
    ledger = tmp_path / "claims"
    locks = tmp_path / "locks"
    claims.claim(601, "agent-a", "governance", snapshot, ledger=ledger, lock_dir=locks, now=base_time)
    claims.claim(603, "agent-b", "governance", snapshot, ledger=ledger, lock_dir=locks, now=base_time)

    files = sorted(ledger.glob("*.json"))
    assert len(files) == 2
    assert len({path.name for path in files}) == 2  # no shared path
    stored = claims.read_ledger(ledger)
    assert {event.issue for event in stored} == {601, 603}


def test_read_ledger_merges_legacy_file_and_claims_directory(tmp_path, snapshot, base_time):
    """A frozen legacy file and the claims directory replay as one ordered history."""
    board = tmp_path / "board"
    board.mkdir()
    legacy = board / "claims.jsonl"
    claims_dir = board / "claims"

    claims.claim(601, "agent-a", "governance", snapshot, ledger=legacy, lock_dir=tmp_path / "locks", now=base_time)
    claims.release(601, "agent-a", ledger=claims_dir, lock_dir=tmp_path / "locks", now=base_time)

    for path in (claims_dir, legacy):
        events = claims.read_ledger(path)
        assert [(event.event, event.issue) for event in events] == [("claim", 601), ("release", 601)]


# --- the audit over the new storage form ------------------------------------


def test_audit_ledger_accepts_a_clean_directory(tmp_path, snapshot, base_time):
    ledger = tmp_path / "claims"
    claims.claim(601, "agent-a", "governance", snapshot, ledger=ledger, lock_dir=tmp_path / "locks", now=base_time)
    assert claims.audit_ledger(ledger, snapshot, base_time) == []


def test_audit_ledger_reports_a_malformed_directory_record(tmp_path, snapshot, base_time):
    ledger = tmp_path / "claims"
    ledger.mkdir()
    (ledger / "00000000000000000001-00601-agent-a-claim.json").write_text("{not json\n", encoding="utf-8")
    problems = claims.audit_ledger(ledger, snapshot, base_time)
    assert any("malformed record" in problem for problem in problems)
