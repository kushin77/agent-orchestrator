"""Anti-formality: the audit must be able to fail (issue #157, GR-12 / AO-GR-19)."""

from __future__ import annotations

import claims
import order
from model import REASON_CHILD_OF_CLAIM, REASON_NEXT_IN_MILESTONE


def test_self_control_is_clean_when_the_audit_is_honest():
    assert claims.self_control() == []


def test_control_fixture_frontier_is_where_the_mutants_assume_it_is():
    snapshot = claims.control_snapshot()
    frontier = order.frontier(snapshot, "CONTROL")
    assert frontier is not None
    assert frontier.number == 601
    assert snapshot.blockers_open(snapshot.get(602)) == [603]


def test_scavenged_claim_is_rejected_by_the_audit():
    snapshot = claims.control_snapshot()
    moment = claims.control_snapshot().generated_at
    events = [
        claims.ClaimEvent(
            event="claim", issue=603, agent="agent-a", at=moment, reason=REASON_NEXT_IN_MILESTONE
        )
    ]
    assert claims.audit(events, snapshot) != []


def test_duplicate_claim_is_rejected_by_the_audit():
    snapshot = claims.control_snapshot()
    moment = snapshot.generated_at
    events = [
        claims.ClaimEvent(event="claim", issue=601, agent="agent-a", at=moment, reason=REASON_NEXT_IN_MILESTONE),
        claims.ClaimEvent(event="claim", issue=601, agent="agent-b", at=moment, reason=REASON_NEXT_IN_MILESTONE),
    ]
    problems = claims.audit(events, snapshot)
    assert any("single-claim lock violated" in problem for problem in problems)


def test_malformed_record_is_rejected_by_audit_text():
    assert claims.audit_text("definitely not json\n", claims.control_snapshot()) != []


def test_epic_claim_is_rejected_by_the_audit():
    snapshot = claims.control_snapshot()
    moment = snapshot.generated_at
    events = [
        claims.ClaimEvent(event="claim", issue=607, agent="agent-a", at=moment, reason=REASON_NEXT_IN_MILESTONE)
    ]
    problems = claims.audit(events, snapshot)
    assert any("claimed an epic" in problem for problem in problems)


def test_double_release_is_rejected_by_the_audit():
    snapshot = claims.control_snapshot()
    moment = snapshot.generated_at
    events = [
        claims.ClaimEvent(event="claim", issue=601, agent="agent-a", at=moment, reason=REASON_NEXT_IN_MILESTONE),
        claims.ClaimEvent(event="release", issue=601, agent="agent-a", at=moment),
        claims.ClaimEvent(event="release", issue=601, agent="agent-a", at=moment),
    ]
    assert claims.audit(events, snapshot) != []


def test_child_claim_without_a_held_parent_is_rejected():
    snapshot = claims.control_snapshot()
    moment = snapshot.generated_at
    events = [
        claims.ClaimEvent(event="claim", issue=605, agent="agent-a", at=moment, reason=REASON_CHILD_OF_CLAIM)
    ]
    problems = claims.audit(events, snapshot)
    assert any("never claimed by agent-a" in problem for problem in problems)


def test_self_control_fails_loudly_if_the_audit_stops_reporting(monkeypatch):
    """If the audit is neutered, the self-control has to say so.

    This is the property the gate depends on: an audit that cannot fail must not
    be able to install itself as a passing gate.
    """
    monkeypatch.setattr(claims, "audit", lambda *args, **kwargs: [])
    monkeypatch.setattr(claims, "audit_text", lambda *args, **kwargs: [])
    problems = claims.self_control()
    assert problems, "a neutered audit was reported as healthy"
    assert any("cannot fail" in problem for problem in problems)
