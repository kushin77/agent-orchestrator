"""Eligibility rules for claim-time issue ordering (issue #157).

The shared ``focused`` fixture supplies an offline ``.board/focus.json`` pinning
a given epic; the autouse ``no_ambient_focus`` fixture means a test that does not
ask for one sees NO active focus (lane F6 / #721).
"""

from __future__ import annotations

import json

import focus
import order
from model import (
    REASON_ACTIVE_EPIC_CHILD,
    REASON_ALREADY_CLAIMED,
    REASON_BLOCKED,
    REASON_CHILD_OF_CLAIM,
    REASON_EPIC_CLOSED,
    REASON_EPIC_NOT_WORKABLE,
    REASON_ESCALATED,
    REASON_ISSUE_CLOSED,
    REASON_NEXT_IN_MILESTONE,
    REASON_NO_CHAIN_EDGE,
    REASON_OUT_OF_EPIC_POOLED,
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
    """An OPEN epic is refused as unworkable; a closed one is simply closed."""
    board = Snapshot(
        generated_at=snapshot.generated_at,
        source=snapshot.source,
        issues={
            **{n: i for n, i in snapshot.issues.items() if n != 600},
            600: Issue(600, "epic of the milestone", milestone="M24", labels=("type:epic",)),
        },
    )
    verdict = order.eligible(board, 600)
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


# --- #701: completion-triggered advance (graph advance, not kanban scavenging) -


def test_nothing_is_ready_while_the_parent_and_blocker_are_still_open(snapshot):
    """#605's parent (#601) and #602's blocker (#603) are open, so neither is
    dependency-free yet — the ready set is empty before any completion."""
    assert order.advance_candidates(snapshot) == []


def test_closing_a_parent_makes_its_child_ready_to_advance(snapshot):
    """#605 declares `Parent: #601`; closing #601 flips it ready in the same cycle."""
    ready = order.advance_candidates(with_closed(snapshot, 601))
    assert [issue.number for issue in ready] == [605]


def test_closing_a_blocker_makes_its_dependent_ready_to_advance(snapshot):
    """#602 declares `Blocked-by: #603`; closing #603 unblocks it in the same cycle."""
    ready = order.advance_candidates(with_closed(snapshot, 603))
    assert [issue.number for issue in ready] == [602]


def test_an_unrelated_open_issue_does_not_advance(snapshot):
    """A bare open issue with no chain edge is the milestone frontier, not a
    completion-triggered advance — kanban scavenging is refused by construction."""
    ready = {issue.number for issue in order.advance_candidates(snapshot)}
    assert 601 not in ready and 603 not in ready and 606 not in ready


def test_a_claimed_or_epic_issue_never_advances(snapshot):
    """A ready child that another agent already holds is skipped, and an epic is
    never a candidate even when its own blockers are gone."""
    closed_parent = with_closed(snapshot, 601)
    ready = order.advance_candidates(closed_parent, claimed=frozenset({605}))
    assert 605 not in [issue.number for issue in ready]
    assert 600 not in [issue.number for issue in ready]


def test_frontier_is_never_a_blocked_issue(snapshot):
    frontier = order.frontier(snapshot, "M25")
    assert frontier is not None
    assert frontier.number == 601
    assert snapshot.blockers_open(frontier) == []


# --- #717: the active epic is a chain edge (epic focus #707) -----------------


def _epic_board() -> Snapshot:
    """#600 is the pinned epic with child #607; #609 is an unrelated epic with #608."""
    issues = {
        600: Issue(600, "the active epic", milestone="M25", labels=("type:epic",)),
        601: Issue(601, "frontier", milestone="M25"),
        607: Issue(607, "child of the active epic", milestone="M99", parent=600),
        608: Issue(608, "child of another epic", milestone="M99", parent=609),
        609: Issue(609, "another epic", milestone="M99", labels=("type:epic",)),
    }
    return Snapshot(generated_at="2026-09-13T12:00:00Z", source="test", issues=issues)


def test_child_of_the_active_epic_is_eligible(focused):
    verdict = order.eligible(_epic_board(), 607, focus_path=focused(600))
    assert verdict.eligible is True
    assert verdict.reason == REASON_ACTIVE_EPIC_CHILD
    assert "#600" in verdict.detail


def test_child_of_a_non_active_epic_is_still_refused(focused):
    """Another epic's child is out-of-epic while our focus is active (#721)."""
    verdict = order.eligible(_epic_board(), 608, focus_path=focused(600))
    assert verdict.eligible is False
    assert verdict.reason == REASON_OUT_OF_EPIC_POOLED


def test_a_non_active_epic_child_is_out_of_order_without_a_focus(tmp_path):
    """With no active epic, another epic's child is refused.

    Every epic here is closed, so the refusal is the *specific* one #726 added:
    an issue whose declared epic is closed has no owner to work under. Reporting
    ``epic-closed`` — rather than falling through to the generic
    ``no-chain-edge`` — is what keeps ``order.eligible`` and the claim-time
    arbitration from disagreeing about the same board.
    """
    board = Snapshot(
        generated_at="2026-09-13T12:00:00Z",
        source="test",
        issues={
            number: (issue if not issue.is_epic else Issue(number, issue.title, state="closed", labels=issue.labels))
            for number, issue in _epic_board().issues.items()
        },
    )
    verdict = order.eligible(board, 608, focus_path=tmp_path / "absent.json")
    assert verdict.eligible is False
    assert verdict.reason == REASON_EPIC_CLOSED


def test_an_epic_closed_refusal_names_the_parent_and_the_remedy():
    """Issue #1259: the refusal names the parent AND the way out, not only the cause.

    The state is PERMANENT until someone re-points the board, so a refusal that
    stops at `epic-closed` is a dead end the reader cannot act on.
    """
    board = Snapshot(
        generated_at="2026-09-13T12:00:00Z",
        source="test",
        issues={
            10: Issue(10, "the closed epic", state="closed", labels=("type:epic",)),
            11: Issue(11, "child of a closed epic", milestone="M1", parent=10),
        },
    )
    verdict = order.eligible(board, 11)
    assert verdict.eligible is False
    assert verdict.reason == REASON_EPIC_CLOSED
    assert "#10" in verdict.detail, "the closed parent must be named by number"
    assert "the closed epic" in verdict.detail, "the closed parent must be named by title"
    assert order.REMEDY_REPARENT in verdict.detail, "the ONE shared remedy phrase must be named"


def test_the_report_and_the_refusal_name_the_same_remedy():
    """One source, two consumers (#1259): the report's remedy and the refusal's agree."""
    board = Snapshot(
        generated_at="2026-09-13T12:00:00Z",
        source="test",
        issues={10: Issue(10, "the closed epic", state="closed", labels=("type:epic",))},
    )
    remedy = order.closed_parent_remedy(board.get(10))
    assert order.REMEDY_REPARENT in remedy
    assert order.REMEDY_REPARENT in order.REMEDIATION_REPARENT
    assert "#10" in remedy and "the closed epic" in remedy


def test_an_unrelated_open_board_item_is_still_refused(focused, snapshot):
    """Regression: epic focus adds an edge, it does not open the board."""
    verdict = order.eligible(snapshot, 603, focus_path=focused(600))
    assert verdict.eligible is False
    assert verdict.reason == REASON_NO_CHAIN_EDGE


def test_child_of_a_held_epic_is_child_of_claim_not_active_epic_child(focused):
    """Precedence: the agent's own held parent still wins (child-of-claim first)."""
    verdict = order.eligible(
        _epic_board(), 607, active_claims=frozenset({600}), focus_path=focused(600)
    )
    assert verdict.eligible is True
    assert verdict.reason == REASON_CHILD_OF_CLAIM


def test_a_blocked_active_epic_child_is_still_refused(focused):
    """The new edge never bypasses a blocker."""
    issues = dict(_epic_board().issues)
    issues[607] = Issue(607, "child, blocked", milestone="M99", parent=600, blocked_by=(699,))
    board = Snapshot(generated_at="2026-09-13T12:00:00Z", source="test", issues=issues)
    verdict = order.eligible(board, 607, focus_path=focused(600))
    assert verdict.eligible is False
    assert verdict.reason == REASON_BLOCKED


# --- #721: out-of-epic work is pooled, never dispatched on the frontier -------


def _pooled_board() -> Snapshot:
    """#900 is the epic; #901 is its child; #902/#903/#904 are outside it.

    #903 is the lowest-numbered open issue in M25, so a frontier-only rule would
    hand it out — which is precisely the incoherence epic focus exists to prevent.
    """
    issues = {
        900: Issue(900, "the active epic", milestone="M25", labels=("type:epic",)),
        901: Issue(901, "child of the epic", milestone="M25", parent=900),
        902: Issue(902, "outside the epic", milestone="M25"),
        903: Issue(903, "outside, and the lowest open number", milestone="M25"),
        904: Issue(904, "child of another epic", milestone="M25", parent=905),
        905: Issue(905, "another epic", milestone="M25", labels=("type:epic",)),
        906: Issue(906, "a second epic", milestone="M25", labels=("type:epic",)),
    }
    return Snapshot(generated_at="2026-09-13T12:00:00Z", source="test", issues=issues)


def test_an_out_of_epic_issue_is_refused_while_a_focus_is_active(focused):
    verdict = order.eligible(_pooled_board(), 902, focus_path=focused(900))
    assert verdict.eligible is False
    assert verdict.reason == REASON_OUT_OF_EPIC_POOLED
    assert "#902" in verdict.detail and "900" in verdict.detail


def test_an_out_of_epic_issue_is_never_dispatched_on_the_milestone_frontier(focused):
    """The regression that matters: out-of-epic work is refused even at the frontier.

    The out-of-epic check must sit BEFORE the frontier branch. If it sat after,
    the frontier would hand out another epic's work and the focus would be
    decorative. The epic's own children are parked in another milestone (M99), as
    a real epic's children are, so the M25 frontier really is out-of-epic work.
    """
    issues = dict(_pooled_board().issues)
    issues[901] = Issue(901, "child of the epic, another milestone", milestone="M99", parent=900)
    board = Snapshot(generated_at="2026-09-13T12:00:00Z", source="test", issues=issues)

    frontier = order.frontier(board, "M25")
    assert frontier is not None and frontier.number == 902, "premise: #902 IS the frontier"

    verdict = order.eligible(board, 902, focus_path=focused(900))
    assert verdict.eligible is False
    assert verdict.reason == REASON_OUT_OF_EPIC_POOLED

    # ...and the epic's own child is eligible even though it is not the frontier.
    child = order.eligible(board, 901, focus_path=focused(900))
    assert child.eligible is True
    assert child.reason == REASON_ACTIVE_EPIC_CHILD


def test_a_child_of_another_epic_is_pooled_not_dispatched(focused):
    """Another epic's child is out-of-epic too — its parent edge is not our edge."""
    verdict = order.eligible(_pooled_board(), 904, focus_path=focused(900))
    assert verdict.eligible is False
    assert verdict.reason == REASON_OUT_OF_EPIC_POOLED


def test_the_active_epics_own_child_is_still_eligible(focused):
    """The refusal contrasts with the child edge, it does not replace it."""
    verdict = order.eligible(_pooled_board(), 901, focus_path=focused(900))
    assert verdict.eligible is True
    assert verdict.reason == REASON_ACTIVE_EPIC_CHILD


def test_nothing_is_pooled_when_no_focus_resolves(tmp_path):
    """No active epic -> the pool branch is inert and normal ordering resumes.

    ``focus.active`` falls back to the lowest open workable epic, so "no focus"
    means the BOARD has no such epic — close them all. Then #902, which is the
    lowest-numbered open unblocked issue in M25, is the frontier and is eligible.
    """
    board = Snapshot(
        generated_at="2026-09-13T12:00:00Z",
        source="test",
        issues={
            number: (issue if not issue.is_epic else Issue(number, issue.title, state="closed", labels=issue.labels))
            for number, issue in _pooled_board().issues.items()
            if issue.parent is None
        },
    )
    absent = tmp_path / "absent.json"
    assert focus.active(board, absent) is None
    verdict = order.eligible(board, 902, focus_path=absent)
    assert verdict.eligible is True
    assert verdict.reason == REASON_NEXT_IN_MILESTONE


def test_a_focus_pinning_a_non_epic_does_not_pool_anything(focused):
    """A non-epic pin is not an active focus, so the fallback epic still applies."""
    verdict = order.eligible(_pooled_board(), 903, focus_path=focused(902))
    assert verdict.eligible is False
    assert verdict.reason == REASON_OUT_OF_EPIC_POOLED


def test_the_default_focus_does_not_pool_when_the_board_has_no_workable_epic(tmp_path, snapshot):
    """The pool branch needs an ACTIVE epic; with none it must not fire.

    ``focus.active`` falls back to the lowest open workable epic when nothing is
    pinned, so "no focus" is a board property, not a file property: close the only
    epic and the branch is inert.
    """
    no_epics = Snapshot(
        generated_at=snapshot.generated_at,
        source=snapshot.source,
        issues={n: i for n, i in snapshot.issues.items() if not i.is_epic},
    )
    assert focus.active(no_epics, tmp_path / "absent.json") is None
    verdict = order.eligible(no_epics, 601, focus_path=tmp_path / "absent.json")
    assert verdict.eligible is True
    assert verdict.reason == REASON_NEXT_IN_MILESTONE


def test_a_board_with_no_epic_pools_nothing(snapshot):
    """No epic -> no focus -> the pool branch never fires, so the rule is unchanged."""
    no_epics = Snapshot(
        generated_at=snapshot.generated_at,
        source=snapshot.source,
        issues={n: i for n, i in snapshot.issues.items() if not i.is_epic},
    )
    verdict = order.eligible(no_epics, 601)
    assert verdict.eligible is True
    assert verdict.reason == REASON_NEXT_IN_MILESTONE


def test_eligible_is_pure(focused, snapshot):
    """The pool branch must not make `eligible` mutate anything (it is a predicate)."""
    before = json.dumps(snapshot.to_json(), sort_keys=True)
    order.eligible(snapshot, 603, focus_path=focused(600))
    order.eligible(snapshot, 601, focus_path=focused(600))
    assert json.dumps(snapshot.to_json(), sort_keys=True) == before


# --- collision-aware wave planning (issue #740, dispatch half) --------------


def _issue(number, *, files=(), milestone="M"):
    return Issue(number, f"issue {number}", milestone=milestone, files=files)


def test_wave_plan_admits_pairwise_disjoint_candidates():
    plan = order.wave_plan([
        _issue(1, files=("a.py",)),
        _issue(2, files=("b.py",)),
        _issue(3, files=("c.py", "d.py")),
    ])
    assert plan.admitted == (1, 2, 3)
    assert plan.refusals == ()
    assert plan.unverifiable == ()


def test_wave_plan_refuses_second_child_by_name_on_negative_control():
    """Negative control: two ready children share a file; the second is refused BY NAME."""
    plan = order.wave_plan([
        _issue(716, files=("Makefile", "scripts/verify.sh")),
        _issue(717, files=("Makefile",)),
    ])
    assert plan.admitted == (716,)
    assert plan.refusals == ("lane-file-collision: Makefile already owned by #716",)


def test_wave_plan_reports_unverifiable_children_and_still_admits_them():
    plan = order.wave_plan([
        _issue(1, files=()),
        _issue(2, files=("a.py",)),
    ])
    assert 1 in plan.admitted
    assert plan.unverifiable == (1,)
    assert plan.refusals == ()


def test_wave_plan_is_deterministic_in_input_order():
    forward = order.wave_plan([_issue(1, files=("x",)), _issue(2, files=("x",))])
    backward = order.wave_plan([_issue(2, files=("x",)), _issue(1, files=("x",))])
    assert forward.admitted == (1,)
    assert backward.admitted == (2,)
    assert forward.refusals[0].endswith("already owned by #1")
    assert backward.refusals[0].endswith("already owned by #2")


# --- #1851: an `escalate:*` issue is not a frontier candidate ----------------
#
# The defect: the active-epic frontier named an issue the tier climb had already
# exhausted, so no reader could take it (its only remedy is a governance action,
# not another tier). `escalate:*` is the terminal marker; `tiered.py` climbs
# tiers in-process and never reads the frontier, so the refusal is frontier-only.


def _escalated_board() -> Snapshot:
    """Mirrors the live board (issue #1851): #1529 is the lowest open issue and
    carries ``escalate:L1``; #1531 is the next takeable one behind it."""
    issues = {
        1527: Issue(1527, "closed before the frontier", state="closed", milestone="M1"),
        1529: Issue(
            1529,
            "escalated by the tier climb",
            milestone="M1",
            labels=("type:task", "tier:L0", "escalate:L1"),
        ),
        1531: Issue(1531, "the next takeable issue", milestone="M1", labels=("type:task", "tier:L0")),
    }
    return Snapshot(generated_at="2026-09-13T12:00:00Z", source="test", issues=issues)


def test_an_escalated_issue_is_refused_by_name():
    """The refusal is named, in the style of the other issue-property refusals."""
    verdict = order.eligible(_escalated_board(), 1529)
    assert verdict.eligible is False
    assert verdict.reason == REASON_ESCALATED
    assert "escalate:L1" in verdict.detail


def test_the_frontier_advances_past_an_escalated_issue():
    """frontier() and claimable_frontier() both skip it (issue #1851)."""
    board = _escalated_board()
    frontier = order.frontier(board, "M1")
    assert frontier is not None and frontier.number == 1531, "the frontier advanced past #1529"

    claimable = order.claimable_frontier(board, "M1")
    assert claimable is not None and claimable.number == 1531


def test_the_escalate_refusal_matches_the_prefix_not_one_label():
    """`escalate:L2` is refused too — the match is the `escalate:` prefix."""
    for label in ("escalate:L1", "escalate:L2"):
        board = Snapshot(
            generated_at="2026-09-13T12:00:00Z",
            source="test",
            issues={1529: Issue(1529, "escalated", milestone="M1", labels=("type:task", label))},
        )
        assert order.eligible(board, 1529).reason == REASON_ESCALATED, label


def test_negative_control_an_unlabelled_issue_is_unaffected():
    """NEGATIVE CONTROL: an issue with no labels at all is not refused."""
    board = Snapshot(
        generated_at="2026-09-13T12:00:00Z",
        source="test",
        issues={
            1529: Issue(1529, "no labels at all", milestone="M1"),
            1531: Issue(1531, "behind it", milestone="M1", labels=("type:task", "tier:L0")),
        },
    )
    assert order.frontier(board, "M1").number == 1529
    assert order.eligible(board, 1529).eligible is True


def test_negative_control_non_escalate_labels_are_not_refused():
    """NEGATIVE CONTROL: the refusal reads `escalate:`, never the `tier:` prefix.

    Every issue in the chain carries ``tier:L0`` (and the other house labels), so
    matching ``tier:`` would refuse the whole board.
    """
    board = Snapshot(
        generated_at="2026-09-13T12:00:00Z",
        source="test",
        issues={
            1529: Issue(
                1529,
                "tiered but not escalated",
                milestone="M1",
                labels=("type:task", "tier:L0", "governance-tier", "priority:P2"),
            ),
        },
    )
    verdict = order.eligible(board, 1529)
    assert verdict.eligible is True
    assert verdict.reason == REASON_NEXT_IN_MILESTONE
    assert order.frontier(board, "M1").number == 1529
