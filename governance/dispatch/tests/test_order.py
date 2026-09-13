"""Eligibility rules for claim-time issue ordering (issue #157)."""

from __future__ import annotations

import order
from model import (
    REASON_ALREADY_CLAIMED,
    REASON_BLOCKED,
    REASON_CHILD_OF_CLAIM,
    REASON_EPIC_NOT_WORKABLE,
    REASON_ISSUE_CLOSED,
    REASON_NEXT_IN_MILESTONE,
    REASON_NO_CHAIN_EDGE,
    REASON_SUCCESSOR_OF_CLAIM,
    REASON_UNKNOWN_ISSUE,
    Issue,
    Snapshot,
)


def with_closed(snapshot, *numbers):
    """Return a copy of the snapshot with ``numbers`` marked closed."""
    issues = dict(snapshot.issues)
    for number in numbers:
        issue = issues[number]
        issues[number] = Issue(
            number=issue.number,
            title=issue.title,
            state="closed",
            milestone=issue.milestone,
            labels=issue.labels,
            parent=issue.parent,
            blocked_by=issue.blocked_by,
        )
    return Snapshot(generated_at=snapshot.generated_at, source=snapshot.source, issues=issues)


def test_frontier_of_the_active_milestone_is_eligible(snapshot):
    verdict = order.eligible(snapshot, 601)
    assert verdict.eligible is True
    assert verdict.reason == REASON_NEXT_IN_MILESTONE
    assert "M25" in verdict.detail


def test_frontier_skips_an_open_epic(snapshot):
    """#600 is open, unblocked and lower-numbered, but an epic is not work."""
    frontier = order.frontier(snapshot, "M25")
    assert frontier is not None
    assert frontier.number == 601


def test_an_epic_is_never_a_claim_target(snapshot):
    verdict = order.eligible(snapshot, 600)
    assert verdict.eligible is False
    assert verdict.reason == REASON_EPIC_NOT_WORKABLE


def test_active_milestone_does_not_follow_an_epic(snapshot):
    """Once the real M25 work is gone, the epic does not hold the milestone open."""
    only_the_epic = with_closed(snapshot, 601, 602, 603, 605)
    assert order.active_milestone(only_the_epic, frozenset()) == "M24"


def test_visible_but_unrelated_issue_is_refused(snapshot):
    """The core rule: a board item that is not the next step is not work."""
    verdict = order.eligible(snapshot, 603)
    assert verdict.eligible is False
    assert verdict.reason == REASON_NO_CHAIN_EDGE


def test_issue_in_another_milestone_is_refused(snapshot):
    verdict = order.eligible(snapshot, 606)
    assert verdict.eligible is False
    assert verdict.reason == REASON_NO_CHAIN_EDGE


def test_blocked_issue_is_refused_and_names_the_blocker(snapshot):
    verdict = order.eligible(snapshot, 602)
    assert verdict.eligible is False
    assert verdict.reason == REASON_BLOCKED
    assert "#603" in verdict.detail


def test_blocker_becomes_reachable_only_when_it_is_the_next_step(snapshot):
    """Closing a blocker removes the block, not the ordering: the frontier still wins."""
    with_603_closed = with_closed(snapshot, 603)
    still_blocked = order.eligible(snapshot, 602)
    unblocked = order.eligible(with_603_closed, 602)

    assert still_blocked.reason == REASON_BLOCKED
    assert unblocked.reason == REASON_NO_CHAIN_EDGE

    # Once the earlier milestone items close, the chain reaches #602.
    advanced = with_closed(snapshot, 601, 603)
    verdict = order.eligible(advanced, 602)
    assert verdict.eligible is True
    assert verdict.reason == REASON_NEXT_IN_MILESTONE


def test_closed_issue_is_refused(snapshot):
    verdict = order.eligible(snapshot, 604)
    assert verdict.eligible is False
    assert verdict.reason == REASON_ISSUE_CLOSED


def test_unknown_issue_is_refused(snapshot):
    verdict = order.eligible(snapshot, 9999)
    assert verdict.eligible is False
    assert verdict.reason == REASON_UNKNOWN_ISSUE


def test_child_of_a_held_issue_is_eligible(snapshot):
    held = frozenset({601})
    verdict = order.eligible(snapshot, 605, active_claims=held)
    assert verdict.eligible is True
    assert verdict.reason == REASON_CHILD_OF_CLAIM


def test_child_of_a_held_issue_beats_milestone_order(snapshot):
    """A chain edge outranks the frontier's own next-sibling rule."""
    held = frozenset({601})
    child = order.eligible(snapshot, 605, active_claims=held)
    sibling = order.eligible(snapshot, 602, active_claims=held)
    assert child.eligible is True
    assert sibling.eligible is False
    assert sibling.reason == REASON_BLOCKED


def test_successor_of_a_previously_claimed_issue_is_eligible(snapshot):
    """A cleared blocker the agent already advanced makes the successor reachable."""
    unblocked = with_closed(snapshot, 603)
    verdict = order.eligible(unblocked, 602, agent_history=frozenset({603}))
    assert verdict.eligible is True
    assert verdict.reason == REASON_SUCCESSOR_OF_CLAIM


def test_a_blocker_still_being_open_outranks_chain_history(snapshot):
    """History is not a bypass: blocked work stays blocked."""
    verdict = order.eligible(snapshot, 602, agent_history=frozenset({603}))
    assert verdict.eligible is False
    assert verdict.reason == REASON_BLOCKED


def test_issue_held_by_another_agent_is_refused(snapshot):
    verdict = order.eligible(snapshot, 601, claimed_by_others=frozenset({601}))
    assert verdict.eligible is False
    assert verdict.reason == REASON_ALREADY_CLAIMED


def test_frontier_skips_an_issue_held_by_another_agent(snapshot):
    """Two agents must not both be told to take the same next step."""
    verdict = order.eligible(snapshot, 603, claimed_by_others=frozenset({601}))
    assert verdict.eligible is True
    assert verdict.reason == REASON_NEXT_IN_MILESTONE


def test_active_milestone_follows_the_agents_own_claims(snapshot):
    assert order.active_milestone(snapshot, frozenset({606})) == "M24"
    assert order.active_milestone(snapshot, frozenset()) == "M25"


def test_frontier_is_never_a_blocked_issue(snapshot):
    frontier = order.frontier(snapshot, "M25")
    assert frontier is not None
    assert frontier.number == 601
    assert snapshot.blockers_open(frontier) == []
