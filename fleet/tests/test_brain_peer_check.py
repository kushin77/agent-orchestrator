"""The cadence A2A peer-check (issue #1625, cadence point 2).

The brain loop must run the peer-check on its cadence and RECORD the verdict,
readable back — a wiring constant nothing reads is not done. These tests drive
``brain.record_peer_check`` / ``brain.peer_check_state`` against a fixture claim
ledger (the real one-file-per-event record shape, replayed by the engine's own
reader) and a fixture ready set, and pin the three outcomes: an overlap is
REFUSED by name, a disjoint set reads back as disjoint, and an unresolvable
ledger is CANNOT-ASSESS — never a silent empty "disjoint" that reads as clean.

The real ``.fleet/`` is never touched: every test passes an explicit
``verdict_path`` under ``tmp_path``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import brain


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ledger_record(issue: int, agent: str, files: list[str], *, at: str) -> dict:
    """One claim event in the shape ``claims.parse_claim_event`` requires."""
    return {
        "event": "claim",
        "issue": issue,
        "agent": agent,
        "at": at,
        "lane": "lane",
        "reason": "fixture",
        "files": [{"path": path, "regions": None} for path in files],
    }


def _write_ledger(root, records: list[dict]) -> None:
    claims_dir = root / ".board" / "claims"
    claims_dir.mkdir(parents=True, exist_ok=True)
    for index, record in enumerate(records):
        name = f"{index:04d}-{record['issue']:05d}-{record['agent']}-claim.json"
        (claims_dir / name).write_text(json.dumps(record, sort_keys=True), encoding="utf-8")


def test_a_ready_issue_overlapping_a_live_sibling_is_refused_by_name(tmp_path):
    _write_ledger(tmp_path, [_ledger_record(7012, "ao-sub-7012", ["scripts/peer-check.sh"], at=_now())])
    verdict_path = tmp_path / "peer-check.json"

    verdict = brain.record_peer_check(
        root=tmp_path,
        ready=[(9001, ("scripts/peer-check.sh",))],
        verdict_path=verdict_path,
    )

    assert verdict["refused"], "an overlapping ready file must be refused, not dropped"
    entry = verdict["refused"][0]
    assert entry["issue"] == 9001
    assert entry["sibling"] == 7012
    assert entry["path"] == "scripts/peer-check.sh"
    assert brain.peer_check_state(verdict_path) == "overlap:1"


def test_a_disjoint_ready_set_reads_back_as_disjoint(tmp_path):
    _write_ledger(tmp_path, [_ledger_record(7012, "ao-sub-7012", ["scripts/peer-check.sh"], at=_now())])
    verdict_path = tmp_path / "peer-check.json"

    verdict = brain.record_peer_check(
        root=tmp_path,
        ready=[(9001, ("registry/personas/README.md",))],
        verdict_path=verdict_path,
    )

    assert verdict["refused"] == []
    assert verdict["checked"] == 1 and verdict["disjoint"] == 1
    assert brain.peer_check_state(verdict_path) == "disjoint:1"


def test_an_unresolvable_ledger_is_cannot_assess_never_a_clean_disjoint(tmp_path, monkeypatch):
    # No `.board/claims/` anywhere, and no main-worktree fallback to find one.
    monkeypatch.setattr(brain, "_main_worktree", lambda root: None)
    verdict_path = tmp_path / "peer-check.json"

    verdict = brain.record_peer_check(
        root=tmp_path,
        ready=[(9001, ("scripts/peer-check.sh",))],
        verdict_path=verdict_path,
    )

    assert verdict["refused"] == []
    assert verdict["note"].startswith("CANNOT-ASSESS")
    assert brain.peer_check_state(verdict_path) == "cannot-assess"


def test_the_verdict_is_written_atomically_with_the_documented_shape(tmp_path):
    _write_ledger(tmp_path, [_ledger_record(7012, "ao-sub-7012", ["scripts/peer-check.sh"], at=_now())])
    verdict_path = tmp_path / "peer-check.json"

    brain.record_peer_check(root=tmp_path, ready=[], verdict_path=verdict_path)

    payload = json.loads(verdict_path.read_text(encoding="utf-8"))
    assert set(payload) == {"checked", "disjoint", "refused", "note", "at"}
    assert not (tmp_path / "peer-check.tmp").exists(), "the tmp file must be replaced, not left behind"
