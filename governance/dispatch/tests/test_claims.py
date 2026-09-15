"""Claim records, the single-claim lock, TTL recovery and the ledger audit (issue #157).

The autouse ``no_ambient_focus`` fixture pins "no active focus" for every test, so
a test that does not ask for one is unaffected by the epic-focus rules (lane F6 /
#721); ``focused`` supplies an explicit pin.
"""

from __future__ import annotations

import json
from datetime import timedelta

import claims
import focus
import order
import pool
import pytest
from model import (
    REASON_ACTIVE_EPIC_CHILD,
    REASON_BLOCKED,
    REASON_BRAIN_DIRECTED,
    REASON_CHILD_OF_CLAIM,
    REASON_NO_CHAIN_EDGE,
    REASON_NEXT_IN_MILESTONE,
    REASON_OUT_OF_EPIC_POOLED,
    Issue,
    Snapshot,
)


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
    fresh = Snapshot(generated_at=later.strftime("%Y-%m-%dT%H:%M:%SZ"), source="test", issues=snapshot.issues)
    event = claims.claim(601, "agent-b", "governance", fresh, ledger=ledger, lock_dir=locks, now=later)

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


def test_audit_flags_a_live_claim_on_a_closed_issue(snapshot, base_time):
    moment = base_time.strftime("%Y-%m-%dT%H:%M:%SZ")
    events = [
        claims.ClaimEvent(event="claim", issue=604, agent="agent-a", at=moment, reason=REASON_NEXT_IN_MILESTONE)
    ]
    problems = claims.audit(events, snapshot, base_time)
    assert any("never released" in problem for problem in problems)


def test_audit_clears_a_closed_issue_claim_ended_by_a_reap(snapshot, base_time):
    """A reap settles a claim the same as a release — the audit must not accuse it."""
    moment = base_time.strftime("%Y-%m-%dT%H:%M:%SZ")
    events = [
        claims.ClaimEvent(event="claim", issue=604, agent="agent-a", at=moment, reason=REASON_NEXT_IN_MILESTONE),
        claims.ClaimEvent(event="reap", issue=604, agent="brain", at=moment, reaped_agent="agent-a"),
    ]
    assert claims.audit(events, snapshot, base_time) == []


def test_audit_is_chronological_when_closed_at_is_known(base_time):
    """A claim made before the issue closed was legitimate at the time (the #140 case)."""
    moment = base_time.strftime("%Y-%m-%dT%H:%M:%SZ")
    later = base_time + timedelta(hours=1)
    earlier = base_time - timedelta(hours=1)
    snapshot = Snapshot(
        generated_at=moment,
        source="test",
        issues={
            701: Issue(701, "closed after the claim", state="closed",
                       closed_at=later.strftime("%Y-%m-%dT%H:%M:%SZ")),
            702: Issue(702, "closed before the claim", state="closed",
                       closed_at=earlier.strftime("%Y-%m-%dT%H:%M:%SZ")),
        },
    )
    before_closure = [claims.ClaimEvent(event="claim", issue=701, agent="a", at=moment, reason=REASON_NEXT_IN_MILESTONE)]
    assert claims.audit(before_closure, snapshot, base_time) == []

    after_closure = [claims.ClaimEvent(event="claim", issue=702, agent="a", at=moment, reason=REASON_NEXT_IN_MILESTONE)]
    problems = claims.audit(after_closure, snapshot, base_time)
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


def test_sent_dir_follows_the_namespaced_fleet_runtime():
    """The claim gate reads the claiming fleet's OWN sent dir, not `<repo>/.fleet`.

    #363: namespacing (``AO_FLEET_DIR``) moved the sister's sent mailbox, but
    this module kept validating against the hardcoded ``.fleet/sent``, so a
    second fleet's claim was refused ``invalid-directive`` for its own brain's
    directive. Proved in a fresh interpreter so a re-hardcode fails even though
    the two paths coincide when the env var is unset.
    """
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    code = (
        "import sys\n"
        "sys.path.insert(0, 'governance/dispatch')\n"
        "sys.path.insert(0, 'fleet')\n"
        "import claims\n"
        "print(claims.SENT_DIR)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=root,
        env={**os.environ, "AO_FLEET_DIR": str(root / ".fleet-claims")},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert result.stdout.strip() == str(root / ".fleet-claims" / "sent")


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


def test_reap_releases_a_claim_wedged_by_a_dead_agent(tmp_path, snapshot, base_time):
    ledger = tmp_path / "claims.jsonl"
    locks = tmp_path / "locks"
    claims.claim(601, "dead-agent", "fleet", snapshot, ledger=ledger, lock_dir=locks, now=base_time)
    assert claims.lock_path(601, locks).exists()

    later = base_time + timedelta(minutes=90)
    reaped = claims.reap(45, ledger=ledger, lock_dir=locks, now=later)

    assert [event.issue for event in reaped] == [601]
    assert reaped[0].reaped_agent == "dead-agent"
    assert claims.active_claims(claims.read_ledger(ledger), later) == {}
    assert not claims.lock_path(601, locks).exists()


def test_reap_leaves_fresh_claims_alone(tmp_path, snapshot, base_time):
    ledger = tmp_path / "claims.jsonl"
    locks = tmp_path / "locks"
    claims.claim(601, "busy-agent", "fleet", snapshot, ledger=ledger, lock_dir=locks, now=base_time)

    reaped = claims.reap(45, ledger=ledger, lock_dir=locks, now=base_time + timedelta(minutes=5))

    assert reaped == []
    assert claims.active_claims(claims.read_ledger(ledger), base_time + timedelta(minutes=5))[601].agent == "busy-agent"


def test_audit_flags_a_reap_that_names_no_agent(snapshot, base_time):
    moment = base_time.strftime("%Y-%m-%dT%H:%M:%SZ")
    events = [claims.ClaimEvent(event="reap", issue=601, agent="brain", at=moment)]
    problems = claims.audit(events, snapshot, base_time)
    assert any("does not name the reaped agent" in problem for problem in problems)


# --- #717: the active epic is a chain edge (epic focus #707) -----------------


def _epic_snapshot() -> Snapshot:
    """#707 is the epic; #710 is its child, #712 is a child of the other epic #713."""
    return Snapshot(
        generated_at="2026-09-13T12:00:00Z",
        source="test",
        issues={
            707: Issue(707, "the active epic", labels=("type:epic",)),
            710: Issue(710, "child of the active epic", parent=707),
            712: Issue(712, "child of another epic", parent=713),
            713: Issue(713, "another epic", labels=("type:epic",)),
        },
    )


def test_audit_accepts_a_claim_on_a_child_of_the_active_epic(tmp_path, focused, base_time):
    moment = base_time.strftime("%Y-%m-%dT%H:%M:%SZ")
    events = [
        claims.ClaimEvent(event="claim", issue=710, agent="agent-a", at=moment, reason=REASON_ACTIVE_EPIC_CHILD)
    ]
    assert claims.audit(events, _epic_snapshot(), base_time, focused(707)) == []


def test_audit_rejects_active_epic_child_on_another_epics_child(tmp_path, focused, base_time):
    moment = base_time.strftime("%Y-%m-%dT%H:%M:%SZ")
    events = [
        claims.ClaimEvent(event="claim", issue=712, agent="agent-a", at=moment, reason=REASON_ACTIVE_EPIC_CHILD)
    ]
    problems = claims.audit(events, _epic_snapshot(), base_time, focused(707))
    assert any("not the active epic" in problem for problem in problems)


def test_claim_grants_active_epic_child_when_the_parent_is_the_active_epic(tmp_path, focused, base_time):
    event = claims.claim(
        710,
        "agent-a",
        "epic",
        _epic_snapshot(),
        ledger=tmp_path / "claims.jsonl",
        lock_dir=tmp_path / "locks",
        now=base_time,
        focus_path=focused(707),
    )
    assert event.reason == REASON_ACTIVE_EPIC_CHILD


def test_claim_still_refuses_an_unrelated_board_item(tmp_path, focused, snapshot, base_time):
    """Regression: the epic edge is additive, never a relaxation."""
    with pytest.raises(claims.ClaimRefused) as excinfo:
        claims.claim(
            603,
            "agent-a",
            "governance",
            snapshot,
            ledger=tmp_path / "claims.jsonl",
            lock_dir=tmp_path / "locks",
            now=base_time,
            focus_path=focused(600),
        )
    assert excinfo.value.reason == REASON_NO_CHAIN_EDGE


# --- #721: out-of-epic work is pooled, never dropped -------------------------


def _pool_board() -> Snapshot:
    """#900 epic, #901 its child, #902/#903 outside it (lane F6 / #721)."""
    return Snapshot(
        generated_at="2026-09-13T12:00:00Z",
        source="test",
        issues={
            900: Issue(900, "the active epic", milestone="M25", labels=("type:epic",)),
            901: Issue(901, "child of the epic", milestone="M99", parent=900),
            902: Issue(902, "outside the epic", milestone="M25"),
            903: Issue(903, "outside, lowest open number", milestone="M25"),
        },
    )


def test_claim_parks_an_out_of_epic_issue_and_still_refuses(tmp_path, focused, pool_rail, base_time):
    """The refusal stands AND the deferral is recorded — parked, not dropped."""
    with pytest.raises(claims.ClaimRefused) as excinfo:
        claims.claim(
            902,
            "agent-a",
            "fleet",
            _pool_board(),
            ledger=tmp_path / "claims.jsonl",
            lock_dir=tmp_path / "locks",
            now=base_time,
            focus_path=focused(900),
            pool_path=pool_rail,
        )

    assert excinfo.value.reason == REASON_OUT_OF_EPIC_POOLED
    records = pool.read(pool_rail)
    assert [r.issue for r in records] == [902]
    assert records[0].reason == "out-of-epic"
    assert records[0].at
    assert not claims.lock_path(902, tmp_path / "locks").exists()


def test_a_refusal_that_is_not_out_of_epic_parks_nothing(tmp_path, focused, pool_rail, base_time):
    """The pool records ONE reason. A blocked claim is not deferred work."""
    board = Snapshot(
        generated_at="2026-09-13T12:00:00Z",
        source="test",
        issues={
            **_pool_board().issues,
            901: Issue(901, "child, blocked", milestone="M25", parent=900, blocked_by=(999,)),
        },
    )
    with pytest.raises(claims.ClaimRefused) as excinfo:
        claims.claim(
            901,
            "agent-a",
            "fleet",
            board,
            ledger=tmp_path / "claims.jsonl",
            lock_dir=tmp_path / "locks",
            now=base_time,
            focus_path=focused(900),
            pool_path=pool_rail,
        )

    assert excinfo.value.reason == REASON_BLOCKED
    assert pool.read(pool_rail) == []


def test_an_out_of_epic_issue_is_parked_once_per_attempt(tmp_path, focused, pool_rail, base_time):
    """Two refusals are two records — the rail is an append-only decision log."""
    for _ in range(2):
        with pytest.raises(claims.ClaimRefused):
            claims.claim(
                902,
                "agent-a",
                "fleet",
                _pool_board(),
                ledger=tmp_path / "claims.jsonl",
                lock_dir=tmp_path / "locks",
                now=base_time,
                focus_path=focused(900),
                pool_path=pool_rail,
            )
    assert pool.numbers(pool_rail) == [902]
    assert len(pool.read(pool_rail)) == 2


def test_the_pool_drains_when_the_resolver_returns_none(tmp_path, focused, pool_rail, base_time):
    """focus == None: the parked work comes back, and the drain REPORTS it.

    ``focus.active`` falls back to the lowest open workable epic, so "no focus"
    means the BOARD has no workable epic — close them. The drained numbers are the
    evidence that the parked work was released rather than dropped.
    """
    pool.note(902, "out-of-epic", at="2026-09-13T00:00:00Z", path=pool_rail)
    pool.note(903, "out-of-epic", at="2026-09-13T00:00:01Z", path=pool_rail)

    # Both epics closed -> no active focus. #902 is then the M25 frontier, so the
    # claim that was refused while the epic was active now proceeds.
    board = Snapshot(
        generated_at="2026-09-13T12:00:00Z",
        source="test",
        issues={
            900: Issue(900, "the active epic", state="closed", milestone="M25", labels=("type:epic",)),
            902: Issue(902, "outside the epic", milestone="M25"),
            903: Issue(903, "outside, lowest open number", milestone="M25"),
        },
    )
    assert focus.active(board, focused(900)) is None

    frontier = order.frontier(board, "M25")
    assert frontier is not None and frontier.number == 902, "premise: #902 IS the frontier"

    event = claims.claim(
        902,
        "agent-a",
        "fleet",
        board,
        ledger=tmp_path / "claims.jsonl",
        lock_dir=tmp_path / "locks",
        now=base_time,
        focus_path=focused(900),
        pool_path=pool_rail,
    )

    assert event.reason == REASON_NEXT_IN_MILESTONE
    assert pool.numbers(pool_rail) == [], "the pool must be empty once the focus is gone"


def test_a_pool_is_untouched_while_a_focus_is_active(tmp_path, focused, pool_rail, base_time):
    """The drain is conditional: an active focus keeps its parked work parked."""
    pool.note(902, "out-of-epic", at="2026-09-13T00:00:00Z", path=pool_rail)

    claims.claim(
        901,
        "agent-a",
        "fleet",
        _pool_board(),
        ledger=tmp_path / "claims.jsonl",
        lock_dir=tmp_path / "locks",
        now=base_time,
        focus_path=focused(900),
        pool_path=pool_rail,
    )
    assert pool.numbers(pool_rail) == [902]


def test_drain_pool_when_no_focus_is_the_shared_helper(tmp_path, focused, pool_rail):
    """The drain is one named function, so the brain and the CLI cannot diverge."""
    pool.note(902, "out-of-epic", at="2026-09-13T00:00:00Z", path=pool_rail)

    no_epic = Snapshot(
        generated_at="2026-09-13T12:00:00Z",
        source="test",
        issues={
            number: (
                issue
                if not issue.is_epic
                else Issue(number, issue.title, state="closed", milestone=issue.milestone, labels=issue.labels)
            )
            for number, issue in _pool_board().issues.items()
        },
    )
    assert claims.drain_pool_when_no_focus(no_epic, focused(900), pool_rail) == [902]
    assert pool.numbers(pool_rail) == []

    pool.note(903, "out-of-epic", at="2026-09-13T00:00:02Z", path=pool_rail)
    assert claims.drain_pool_when_no_focus(_pool_board(), focused(900), pool_rail) == []
    assert pool.numbers(pool_rail) == [903]


def test_a_directive_promotes_an_out_of_epic_blocker_just_in_time(
    tmp_path, monkeypatch, focused, pool_rail, base_time
):
    """#721 acceptance: the EXISTING `claim --directive` path promotes pooled work.

    An active-epic child declares `Blocked-by: #902`; #902 is out-of-epic, so it is
    pooled while a focus is active. The brain names it in a directive and the claim
    succeeds with `brain-directed` — no new authorisation mechanism is invented,
    and the directive path bypasses `eligible()` entirely.
    """
    sent = tmp_path / "sent"
    monkeypatch.setattr(claims, "SENT_DIR", sent)
    _write_directive(sent, "d-promote", 902)

    board = _pool_board()
    with pytest.raises(claims.ClaimRefused) as excinfo:
        claims.claim(
            902,
            "subagent-x",
            "fleet",
            board,
            ledger=tmp_path / "claims.jsonl",
            lock_dir=tmp_path / "locks",
            now=base_time,
            focus_path=focused(900),
            pool_path=pool_rail,
        )
    assert excinfo.value.reason == REASON_OUT_OF_EPIC_POOLED
    assert pool.numbers(pool_rail) == [902], "premise: the blocker really was pooled first"

    event = claims.claim(
        902,
        "subagent-x",
        "fleet",
        board,
        ledger=tmp_path / "claims.jsonl",
        lock_dir=tmp_path / "locks",
        now=base_time,
        directive_id="d-promote",
        focus_path=focused(900),
        pool_path=pool_rail,
    )

    assert event.reason == REASON_BRAIN_DIRECTED
    assert event.directive_id == "d-promote"
    assert event.directive_from == "brain"


def test_an_active_epic_child_may_declare_a_pooled_issue_as_its_blocker(tmp_path, focused):
    """The promotion chain is readable off the board: child --Blocked-by--> pooled."""
    board = Snapshot(
        generated_at="2026-09-13T12:00:00Z",
        source="test",
        issues={
            900: Issue(900, "the active epic", labels=("type:epic",)),
            901: Issue(901, "child, waiting on out-of-epic work", parent=900, blocked_by=(902,)),
            902: Issue(902, "outside the epic"),
        },
    )
    verdict = order.eligible(board, 901, focus_path=focused(900))
    assert verdict.eligible is False
    assert verdict.reason == REASON_BLOCKED
    assert "#902" in verdict.detail
    # ...and the blocker it waits on is exactly what the pool holds.
    assert [i.number for i in focus.pooled(board, 900)] == [902]
