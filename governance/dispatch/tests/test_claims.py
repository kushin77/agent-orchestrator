"""Claim records, the single-claim lock, TTL recovery and the ledger audit (issue #157)."""

from __future__ import annotations

import json
from datetime import timedelta

import claims
import pytest
from model import REASON_BLOCKED, REASON_BRAIN_DIRECTED, REASON_CHILD_OF_CLAIM, REASON_NO_CHAIN_EDGE, REASON_NEXT_IN_MILESTONE


def test_claim_records_the_reason_and_creates_the_lock(tmp_path, snapshot, base_time):
    ledger = tmp_path / "claims.jsonl"
    locks = tmp_path / "locks"

    event = claims.claim(601, "agent-a", "governance", snapshot, ledger=ledger, lock_dir=locks, now=base_time)

    assert event.event == "claim"
    assert event.reason == REASON_NEXT_IN_MILESTONE
    assert claims.lock_path(601, locks).exists()
    stored = claims.read_ledger(ledger)
    assert [entry.issue for entry in stored] == [601]
    assert claims.active_claims(stored, base_time)[601].agent == "agent-a"


def test_out_of_order_claim_is_refused_and_leaves_no_state(tmp_path, snapshot, base_time):
    ledger = tmp_path / "claims.jsonl"
    locks = tmp_path / "locks"

    with pytest.raises(claims.ClaimRefused) as excinfo:
        claims.claim(603, "agent-a", "governance", snapshot, ledger=ledger, lock_dir=locks, now=base_time)

    assert excinfo.value.reason == REASON_NO_CHAIN_EDGE
    assert not ledger.exists()
    assert not claims.lock_path(603, locks).exists()


def test_second_agent_cannot_claim_a_held_issue(tmp_path, snapshot, base_time):
    ledger = tmp_path / "claims.jsonl"
    locks = tmp_path / "locks"
    claims.claim(601, "agent-a", "governance", snapshot, ledger=ledger, lock_dir=locks, now=base_time)

    with pytest.raises(claims.ClaimRefused) as excinfo:
        claims.claim(
            601, "agent-b", "governance", snapshot, ledger=ledger, lock_dir=locks, now=base_time + timedelta(hours=1)
        )

    assert excinfo.value.reason == "already-claimed"
    assert "agent-a" in excinfo.value.detail


def test_the_same_agent_cannot_double_claim(tmp_path, snapshot, base_time):
    ledger = tmp_path / "claims.jsonl"
    locks = tmp_path / "locks"
    claims.claim(601, "agent-a", "governance", snapshot, ledger=ledger, lock_dir=locks, now=base_time)

    with pytest.raises(claims.ClaimRefused) as excinfo:
        claims.claim(601, "agent-a", "governance", snapshot, ledger=ledger, lock_dir=locks, now=base_time)

    assert excinfo.value.reason == "already-claimed"


def test_an_expired_claim_can_be_taken_over(tmp_path, snapshot, base_time):
    ledger = tmp_path / "claims.jsonl"
    locks = tmp_path / "locks"
    claims.claim(601, "agent-a", "governance", snapshot, ledger=ledger, lock_dir=locks, ttl_hours=1, now=base_time)

    later = base_time + timedelta(hours=2)
    event = claims.claim(601, "agent-b", "governance", snapshot, ledger=ledger, lock_dir=locks, now=later)

    assert event.event == "take-over"
    assert claims.active_claims(claims.read_ledger(ledger), later)[601].agent == "agent-b"


def test_release_requires_ownership_then_frees_the_issue(tmp_path, snapshot, base_time):
    ledger = tmp_path / "claims.jsonl"
    locks = tmp_path / "locks"
    claims.claim(601, "agent-a", "governance", snapshot, ledger=ledger, lock_dir=locks, now=base_time)

    with pytest.raises(claims.ClaimRefused) as excinfo:
        claims.release(601, "agent-b", ledger=ledger, lock_dir=locks, now=base_time)
    assert excinfo.value.reason == "not-owner"

    claims.release(601, "agent-a", ledger=ledger, lock_dir=locks, now=base_time)
    assert not claims.lock_path(601, locks).exists()
    assert claims.active_claims(claims.read_ledger(ledger), base_time) == {}

    event = claims.claim(601, "agent-b", "governance", snapshot, ledger=ledger, lock_dir=locks, now=base_time)
    assert event.event == "claim"


def test_release_without_a_claim_is_refused(tmp_path, snapshot, base_time):
    with pytest.raises(claims.ClaimRefused) as excinfo:
        claims.release(601, "agent-a", ledger=tmp_path / "claims.jsonl", lock_dir=tmp_path / "locks", now=base_time)
    assert excinfo.value.reason == "not-claimed"


def test_malformed_ledger_line_is_reported_with_its_line_number(tmp_path):
    ledger = tmp_path / "claims.jsonl"
    ledger.write_text('{"event": "claim", "issue": 601, "agent": "a", "at": "2026-09-13T12:00:00Z"}\nnot json\n')

    with pytest.raises(ValueError) as excinfo:
        claims.read_ledger(ledger)
    assert "claims.jsonl:2" in str(excinfo.value)
    assert "invalid JSON" in str(excinfo.value)


def test_audit_accepts_a_clean_chain(snapshot, base_time):
    moment = base_time.strftime("%Y-%m-%dT%H:%M:%SZ")
    events = [
        claims.ClaimEvent(event="claim", issue=601, agent="agent-a", at=moment, reason=REASON_NEXT_IN_MILESTONE),
        claims.ClaimEvent(event="claim", issue=605, agent="agent-a", at=moment, reason=REASON_CHILD_OF_CLAIM),
    ]
    assert claims.audit(events, snapshot, base_time) == []


def test_audit_flags_a_release_without_a_claim(snapshot, base_time):
    moment = base_time.strftime("%Y-%m-%dT%H:%M:%SZ")
    events = [claims.ClaimEvent(event="release", issue=601, agent="agent-a", at=moment)]
    problems = claims.audit(events, snapshot, base_time)
    assert any("no prior claim" in problem for problem in problems)


def test_audit_flags_a_claim_after_the_issue_closed(snapshot, base_time):
    moment = base_time.strftime("%Y-%m-%dT%H:%M:%SZ")
    events = [
        claims.ClaimEvent(event="claim", issue=604, agent="agent-a", at=moment, reason=REASON_NEXT_IN_MILESTONE)
    ]
    problems = claims.audit(events, snapshot, base_time)
    assert any("claimed after it was closed" in problem for problem in problems)


def test_audit_flags_an_expired_claim_that_was_never_released(snapshot, base_time):
    moment = base_time.strftime("%Y-%m-%dT%H:%M:%SZ")
    events = [
        claims.ClaimEvent(
            event="claim",
            issue=601,
            agent="agent-a",
            at=moment,
            reason=REASON_NEXT_IN_MILESTONE,
            ttl_hours=1,
        )
    ]
    problems = claims.audit(events, snapshot, base_time + timedelta(hours=3))
    assert any("expired at TTL" in problem for problem in problems)


def test_audit_text_flags_a_malformed_record(snapshot, base_time):
    problems = claims.audit_text('{"event": "claim"}\n', snapshot, base_time)
    assert any("malformed record" in problem for problem in problems)


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


def test_a_brain_directive_authorizes_an_off_frontier_claim(tmp_path, monkeypatch, snapshot, base_time):
    sent = tmp_path / "sent"
    monkeypatch.setattr(claims, "SENT_DIR", sent)
    _write_directive(sent, "d-1", 606)

    event = claims.claim(
        606,
        "subagent-x",
        "lessons",
        snapshot,
        ledger=tmp_path / "claims.jsonl",
        lock_dir=tmp_path / "locks",
        now=base_time,
        directive_id="d-1",
    )

    assert event.reason == REASON_BRAIN_DIRECTED
    assert event.directive_id == "d-1"
    assert event.directive_from == "brain"


def test_a_brain_directive_must_name_this_issue(tmp_path, monkeypatch, snapshot, base_time):
    sent = tmp_path / "sent"
    monkeypatch.setattr(claims, "SENT_DIR", sent)
    _write_directive(sent, "d-1", 601)

    with pytest.raises(claims.ClaimRefused) as excinfo:
        claims.claim(
            606,
            "subagent-x",
            "lessons",
            snapshot,
            ledger=tmp_path / "claims.jsonl",
            lock_dir=tmp_path / "locks",
            now=base_time,
            directive_id="d-1",
        )
    assert excinfo.value.reason == "invalid-directive"


def test_a_missing_directive_file_is_refused(tmp_path, monkeypatch, snapshot, base_time):
    monkeypatch.setattr(claims, "SENT_DIR", tmp_path / "sent")
    with pytest.raises(claims.ClaimRefused) as excinfo:
        claims.claim(
            606,
            "subagent-x",
            "lessons",
            snapshot,
            ledger=tmp_path / "claims.jsonl",
            lock_dir=tmp_path / "locks",
            now=base_time,
            directive_id="never-sent",
        )
    assert excinfo.value.reason == "invalid-directive"


def test_a_directive_does_not_bypass_a_blocked_issue(tmp_path, monkeypatch, snapshot, base_time):
    sent = tmp_path / "sent"
    monkeypatch.setattr(claims, "SENT_DIR", sent)
    _write_directive(sent, "d-1", 602)

    with pytest.raises(claims.ClaimRefused) as excinfo:
        claims.claim(
            602,
            "subagent-x",
            "lessons",
            snapshot,
            ledger=tmp_path / "claims.jsonl",
            lock_dir=tmp_path / "locks",
            now=base_time,
            directive_id="d-1",
        )
    assert excinfo.value.reason == REASON_BLOCKED


def test_audit_flags_a_brain_directed_claim_without_a_directive_ref(snapshot, base_time):
    moment = base_time.strftime("%Y-%m-%dT%H:%M:%SZ")
    events = [
        claims.ClaimEvent(event="claim", issue=601, agent="agent-a", at=moment, reason=REASON_BRAIN_DIRECTED)
    ]
    problems = claims.audit(events, snapshot, base_time)
    assert any("no directive_id" in problem for problem in problems)
