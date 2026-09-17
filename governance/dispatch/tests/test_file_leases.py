"""Per-file leases: disjoint regions coexist, overlapping regions refuse, and a
TTL-expired lease frees its files automatically (issue #702).

DG-6: whole-file ownership (the pre-#702 shape) serializes two lanes that touch
different parts of the same file. A claim may now name the files it touches,
each with an optional list of ``[start, end]`` line regions. Two LIVE claims
naming the same file conflict only when a region overlaps (or either omits
regions, meaning "the whole file").
"""

from __future__ import annotations

import json
from datetime import timedelta

import claims
import pytest
from model import REASON_FILE_REGION_CLAIMED, FileClaim, Snapshot


def _write_directive(sent_dir, directive_id, issue):
    sent_dir.mkdir(parents=True, exist_ok=True)
    directive = {
        "from": "brain",
        "to": "sister",
        "type": "directive",
        "id": directive_id,
        "task": {"issue": issue},
    }
    (sent_dir / f"{directive_id}.json").write_text(json.dumps(directive), encoding="utf-8")


# ---------------------------------------------------------------------------
# Two-lane fixture (measured, not code-exists): two claims name the SAME file.
# ---------------------------------------------------------------------------


def test_two_lanes_disjoint_regions_of_one_file_are_both_accepted(tmp_path, monkeypatch, snapshot, base_time):
    """#601 (frontier) and #606 (brain-directed) both touch shared.py, disjoint regions."""
    ledger = tmp_path / "claims.jsonl"
    locks = tmp_path / "locks"
    sent = tmp_path / "sent"
    monkeypatch.setattr(claims, "SENT_DIR", sent)
    _write_directive(sent, "d-1", 606)

    first = claims.claim(
        601,
        "agent-a",
        "governance",
        snapshot,
        ledger=ledger,
        lock_dir=locks,
        now=base_time,
        files=(FileClaim(path="shared.py", regions=((1, 20),)),),
    )
    second = claims.claim(
        606,
        "agent-b",
        "governance",
        snapshot,
        ledger=ledger,
        lock_dir=locks,
        now=base_time,
        directive_id="d-1",
        files=(FileClaim(path="shared.py", regions=((21, 40),)),),
    )

    assert first.event == "claim"
    assert second.event == "claim"
    live = claims.active_claims(claims.read_ledger(ledger), base_time)
    assert set(live) == {601, 606}


def test_two_lanes_overlapping_regions_of_one_file_are_refused_by_name(tmp_path, monkeypatch, snapshot, base_time):
    ledger = tmp_path / "claims.jsonl"
    locks = tmp_path / "locks"
    sent = tmp_path / "sent"
    monkeypatch.setattr(claims, "SENT_DIR", sent)
    _write_directive(sent, "d-1", 606)

    claims.claim(
        601,
        "agent-a",
        "governance",
        snapshot,
        ledger=ledger,
        lock_dir=locks,
        now=base_time,
        files=(FileClaim(path="shared.py", regions=((1, 20),)),),
    )

    with pytest.raises(claims.ClaimRefused) as excinfo:
        claims.claim(
            606,
            "agent-b",
            "governance",
            snapshot,
            ledger=ledger,
            lock_dir=locks,
            now=base_time,
            directive_id="d-1",
            files=(FileClaim(path="shared.py", regions=((15, 25),)),),
        )

    assert excinfo.value.reason == REASON_FILE_REGION_CLAIMED
    assert "shared.py" in excinfo.value.detail
    assert "#601" in excinfo.value.detail
    # The refusal never wrote a second claim: #606 is still free.
    live = claims.active_claims(claims.read_ledger(ledger), base_time)
    assert set(live) == {601}


def test_whole_file_claim_conflicts_with_any_region(tmp_path, monkeypatch, snapshot, base_time):
    ledger = tmp_path / "claims.jsonl"
    locks = tmp_path / "locks"
    sent = tmp_path / "sent"
    monkeypatch.setattr(claims, "SENT_DIR", sent)
    _write_directive(sent, "d-1", 606)

    claims.claim(
        601,
        "agent-a",
        "governance",
        snapshot,
        ledger=ledger,
        lock_dir=locks,
        now=base_time,
        files=(FileClaim(path="shared.py", regions=None),),
    )

    with pytest.raises(claims.ClaimRefused) as excinfo:
        claims.claim(
            606,
            "agent-b",
            "governance",
            snapshot,
            ledger=ledger,
            lock_dir=locks,
            now=base_time,
            directive_id="d-1",
            files=(FileClaim(path="shared.py", regions=((900, 901),)),),
        )

    assert excinfo.value.reason == REASON_FILE_REGION_CLAIMED


# ---------------------------------------------------------------------------
# TTL reaping frees the region; a lease inside TTL is NOT reaped (negative control).
# ---------------------------------------------------------------------------


def test_a_stalled_lease_past_ttl_is_reaped_and_the_region_becomes_reclaimable(
    tmp_path, monkeypatch, snapshot, base_time
):
    ledger = tmp_path / "claims.jsonl"
    locks = tmp_path / "locks"
    sent = tmp_path / "sent"
    monkeypatch.setattr(claims, "SENT_DIR", sent)
    _write_directive(sent, "d-1", 606)

    claims.claim(
        601,
        "agent-a",
        "governance",
        snapshot,
        ledger=ledger,
        lock_dir=locks,
        ttl_hours=1,
        now=base_time,
        files=(FileClaim(path="shared.py", regions=((1, 20),)),),
    )

    past_ttl = base_time + timedelta(hours=2)
    fresh = Snapshot(generated_at=past_ttl.strftime("%Y-%m-%dT%H:%M:%SZ"), source="test", issues=snapshot.issues)

    # The overlap check is against LIVE claims: an expired holder no longer
    # blocks the region, before any explicit reap runs.
    second = claims.claim(
        606,
        "agent-b",
        "governance",
        fresh,
        ledger=ledger,
        lock_dir=locks,
        now=past_ttl,
        directive_id="d-1",
        files=(FileClaim(path="shared.py", regions=((1, 20),)),),
    )
    assert second.event == "claim"
    # #601's own TTL already dropped it out of the live set (this is what freed
    # the region above) — active_claims proves it, independent of the second claim.
    live = claims.active_claims(claims.read_ledger(ledger), past_ttl)
    assert 601 not in live
    assert 606 in live


def test_a_lease_inside_ttl_is_not_reaped_negative_control(tmp_path, monkeypatch, snapshot, base_time):
    ledger = tmp_path / "claims.jsonl"
    locks = tmp_path / "locks"
    sent = tmp_path / "sent"
    monkeypatch.setattr(claims, "SENT_DIR", sent)
    _write_directive(sent, "d-1", 606)

    claims.claim(
        601,
        "agent-a",
        "governance",
        snapshot,
        ledger=ledger,
        lock_dir=locks,
        ttl_hours=24,
        now=base_time,
        files=(FileClaim(path="shared.py", regions=((1, 20),)),),
    )

    soon = base_time + timedelta(minutes=30)
    fresh = Snapshot(generated_at=soon.strftime("%Y-%m-%dT%H:%M:%SZ"), source="test", issues=snapshot.issues)

    with pytest.raises(claims.ClaimRefused) as excinfo:
        claims.claim(
            606,
            "agent-b",
            "governance",
            fresh,
            ledger=ledger,
            lock_dir=locks,
            now=soon,
            directive_id="d-1",
            files=(FileClaim(path="shared.py", regions=((1, 20),)),),
        )
    assert excinfo.value.reason == REASON_FILE_REGION_CLAIMED

    # Negative control: #601 is still well inside its 24h TTL, so it is NOT
    # dropped from the live set and the lock is untouched.
    live = claims.active_claims(claims.read_ledger(ledger), soon)
    assert 601 in live
    assert claims.lock_path(601, locks).exists()


def test_ledger_round_trips_files(tmp_path, monkeypatch, snapshot, base_time):
    ledger = tmp_path / "claims.jsonl"
    locks = tmp_path / "locks"

    claims.claim(
        601,
        "agent-a",
        "governance",
        snapshot,
        ledger=ledger,
        lock_dir=locks,
        now=base_time,
        files=(FileClaim(path="shared.py", regions=((1, 20),)), FileClaim(path="whole.py", regions=None)),
    )

    stored = claims.read_ledger(ledger)
    assert len(stored) == 1
    assert stored[0].files == (
        FileClaim(path="shared.py", regions=((1, 20),)),
        FileClaim(path="whole.py", regions=None),
    )
