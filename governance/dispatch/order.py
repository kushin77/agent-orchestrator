"""Eligibility: is this issue the next step in the active dependency chain?

Rule (AGENTS.md golden rule 14 / GR-20): an agent may work an issue only when it
is (a) a child of an issue the agent already holds open, (b) the successor of an
issue the agent already took through the chain, (c) a child of the ACTIVE epic
(epic focus, issue #707), or (d) the frontier of the active milestone — the
lowest-numbered open, unblocked, unclaimed issue in the milestone the agent is
already working. Anything else is kanban scavenging and is refused.

Epic focus adds one *refusal* beside those edges (lane F6 / issue #721): while a
focus is active, work OUTSIDE the active epic is refused `out-of-epic-pooled` and
parked in `.board/pool.jsonl`, so the fleet concentrates on one epic without the
deferred work being lost. The check sits before the milestone frontier, because a
frontier that hands out another epic's issue is the incoherence focus prevents.
All of it stays a pure function of the snapshot plus the focus file.

One structural rule belongs here as well as in the A2A arbitration (issue #726):
an issue whose declared epic is closed is not eligible, because the epic that
would own the work is gone. Keeping it in the pre-flight means `eligible` and the
claim-time refusal cannot disagree — the trap this rule exists to close.
"""

from __future__ import annotations

from pathlib import Path

import focus
from model import (
    REASON_ACTIVE_EPIC_CHILD,
    REASON_ALREADY_CLAIMED,
    REASON_BLOCKED,
    REASON_CHILD_OF_CLAIM,
    REASON_EPIC_CLOSED,
    REASON_EPIC_NOT_WORKABLE,
    REASON_ISSUE_CLOSED,
    REASON_NEXT_IN_MILESTONE,
    REASON_NO_CHAIN_EDGE,
    REASON_OUT_OF_EPIC_POOLED,
    REASON_SUCCESSOR_OF_CLAIM,
    REASON_UNKNOWN_ISSUE,
    Eligibility,
    Issue,
    Snapshot,
)


def frontier(snapshot: Snapshot, milestone: str, claimed_by_others: frozenset[int] = frozenset()) -> Issue | None:
    """Lowest-numbered open, unblocked, unclaimed non-epic issue in ``milestone``."""
    candidates = [
        issue
        for issue in snapshot.open_issues()
        if issue.milestone == milestone
        and not issue.is_epic
        and issue.number not in claimed_by_others
        and not snapshot.blockers_open(issue)
    ]
    candidates.sort(key=lambda issue: issue.number)
    return candidates[0] if candidates else None


def active_milestone(snapshot: Snapshot, active_claims: frozenset[int] = frozenset()) -> str:
    """The milestone the agent is already in: its claim's milestone, else the earliest frontier's."""
    for number in sorted(active_claims):
        issue = snapshot.get(number)
        if issue is not None and issue.milestone:
            return issue.milestone
    for issue in sorted(snapshot.open_issues(), key=lambda issue: issue.number):
        if issue.milestone and not issue.is_epic and not snapshot.blockers_open(issue):
            return issue.milestone
    return ""


def eligible(
    snapshot: Snapshot,
    issue_number: int,
    active_claims: frozenset[int] = frozenset(),
    agent_history: frozenset[int] = frozenset(),
    claimed_by_others: frozenset[int] = frozenset(),
    focus_path: Path | str | None = None,
) -> Eligibility:
    """Decide whether ``issue_number`` is the next eligible step for this agent.

    ``focus_path`` names the pinned ``.board/focus.json`` (epic focus, #707) so
    the active-epic edge is resolvable offline against a fixture. It defaults to
    ``None`` and is resolved to ``focus.DEFAULT_PATH`` *at call time*: a default
    argument would freeze the module constant at import, so a test (or a caller)
    that repoints the focus would silently keep judging against the old file.
    """
    if focus_path is None:
        focus_path = focus.DEFAULT_PATH
    issue = snapshot.get(issue_number)
    if issue is None:
        return Eligibility(issue_number, False, REASON_UNKNOWN_ISSUE, "not present in .board/snapshot.json")
    if issue.closed:
        return Eligibility(issue_number, False, REASON_ISSUE_CLOSED, f"#{issue_number} is closed")
    if issue_number in claimed_by_others:
        return Eligibility(issue_number, False, REASON_ALREADY_CLAIMED, f"#{issue_number} is claimed by another agent")
    if issue.is_epic:
        return Eligibility(
            issue_number,
            False,
            REASON_EPIC_NOT_WORKABLE,
            f"#{issue_number} is an epic: it closes with its children, it is never a claim target",
        )

    # An issue whose declared epic is closed has no owner to work under: the unit
    # cannot prove issue -> epic -> lane, so it is refused like any unowned unit.
    epic = snapshot.get(issue.parent) if issue.parent is not None else None
    if epic is not None and epic.closed:
        return Eligibility(
            issue_number,
            False,
            REASON_EPIC_CLOSED,
            f"#{issue_number} declares Parent #{epic.number}, which is closed",
        )

    open_blockers = snapshot.blockers_open(issue)
    if open_blockers:
        listed = ", ".join(f"#{number}" for number in open_blockers)
        return Eligibility(issue_number, False, REASON_BLOCKED, f"blocked by {listed}")

    if issue.parent is not None and issue.parent in active_claims:
        return Eligibility(
            issue_number,
            True,
            REASON_CHILD_OF_CLAIM,
            f"child of #{issue.parent}, which this agent holds open",
        )

    # Epic focus (#707): a `Parent: #<active-epic>` edge is a real chain edge even
    # though the agent does not hold the epic — the fleet is driving exactly this
    # epic. This is stricter than the milestone frontier, never a relaxation.
    if issue.parent is not None:
        active_epic = focus.active(snapshot, focus_path)
        if active_epic is not None and active_epic.number == issue.parent:
            return Eligibility(
                issue_number,
                True,
                REASON_ACTIVE_EPIC_CHILD,
                f"child of #{issue.parent}, the active epic",
            )

    owners = sorted(set(issue.blocked_by) & set(agent_history))
    if owners:
        listed = ", ".join(f"#{number}" for number in owners)
        return Eligibility(
            issue_number,
            True,
            REASON_SUCCESSOR_OF_CLAIM,
            f"successor of {listed}, which this agent already advanced",
        )

    # Epic focus (#707, lane F6): while a focus is ACTIVE the fleet drives exactly
    # that epic, so out-of-epic work is parked — never dispatched. This is checked
    # BEFORE the milestone-frontier branch: a frontier can interleave several
    # epics, which is precisely the incoherence the focus exists to prevent, so an
    # out-of-epic issue must never be handed out on the frontier. The pooled set
    # is `focus.pooled` (the epic, its children and other epics excluded), and
    # with no active epic it is a no-op — the branch is a no-relaxation guard.
    active_epic = focus.active(snapshot, focus_path)
    if active_epic is not None:
        pooled_numbers = {candidate.number for candidate in focus.pooled(snapshot, active_epic.number)}
        if issue_number in pooled_numbers:
            return Eligibility(
                issue_number,
                False,
                REASON_OUT_OF_EPIC_POOLED,
                f"#{issue_number} is outside the active epic #{active_epic.number} "
                "(parked in .board/pool.jsonl; promoted just-in-time by a brain directive "
                "when an active-epic child declares it as a blocker)",
            )

    milestone = active_milestone(snapshot, active_claims)
    if issue.milestone and issue.milestone == milestone:
        candidate = frontier(snapshot, milestone, claimed_by_others)
        if candidate is not None and candidate.number == issue_number:
            return Eligibility(
                issue_number,
                True,
                REASON_NEXT_IN_MILESTONE,
                f"frontier of {milestone!r} (lowest open, unblocked issue)",
            )

    return Eligibility(
        issue_number,
        False,
        REASON_NO_CHAIN_EDGE,
        "no chain edge to the active work (kanban scavenging) and not the milestone frontier",
    )


def advance_candidates(
    snapshot: Snapshot,
    claimed: frozenset[int] = frozenset(),
) -> list[Issue]:
    """The dependency-free ready set a completion can newly unlock (issue #701).

    Graph advance, not kanban scavenging (GR-20): only issues that declare a
    chain edge — a ``Parent`` or a ``Blocked-by`` — are candidates. An issue is
    ready when it is open, not an epic, unclaimed, has no open blockers, and its
    declared parent (if any) is closed.

    A bare open issue with no chain edge is the milestone's own frontier, reached
    by ``next-in-milestone``, not by a completion-triggered advance: its readiness
    never *changed* when a blocker closed, so it is excluded here. Closing a
    parent/blocker is what flips a candidate from blocked to ready, which is
    exactly the set the brain must re-dispatch in the same cycle.
    """
    ready: list[Issue] = []
    for issue in snapshot.open_issues():
        if issue.number in claimed or issue.is_epic:
            continue
        # Only graph edges resolve into an advance; a bare issue is the frontier.
        if issue.parent is None and not issue.blocked_by:
            continue
        if issue.parent is not None:
            parent = snapshot.get(issue.parent)
            if parent is None or not parent.closed:
                continue
        if snapshot.blockers_open(issue):
            continue
        ready.append(issue)
    ready.sort(key=lambda issue: issue.number)
    return ready
