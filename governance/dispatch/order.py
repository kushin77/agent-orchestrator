"""Eligibility: is this issue the next step in the active dependency chain?

---knowledge---
module_id: governance.dispatch.order
system: governance
app: dispatch
solution_class: enterprise
patterns: [provoked-negative-control, offline-hermetic, deterministic]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [frontier, claimable_frontier, unclaimable_frontier, active_milestone, eligible, advance_candidates, WavePlan, wave_plan, closed_parent_remedy, dangling_on_closed_parent, (+1 more)]
invariants: ""
gotchas: ""
related: ["#132", "#701", "#707", "#721", "#726", "#740"]
do_not_duplicate: null
---knowledge---

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

**`frontier` applies every issue-property refusal `eligible` applies (issue #1168).**
The two disagreed: `status` resolved the milestone frontier without asking whether
the candidate's declared epic was still open, so it advertised a #132-shaped issue
that `claim` refused. The issue-property refusals now live in ONE place
(`_refusal_before_grants`) and BOTH `eligible` and `frontier` apply them, so a
milestone frontier can never be an issue no reader could take. The epic-focus
refusal stays a separate question — :func:`claimable_frontier` is the
focus-aware one, and it is what a report advertising "do this next" must use —
and :func:`frontier_agreement` / :func:`unclaimable_frontier` exist so a gate can
PROVOKE the disagreement rather than assert the happy path.

**An `epic-closed` refusal names the remedy, not only the cause (issue #1259).**
The refusal is right — the epic that would own the work is gone — but it is
PERMANENT for the issue until the board is re-pointed, and the `Verify:` command
of #1259 runs `claim`, which used to answer with a bare cause. `eligible` (and the
claim-time arbitration, which calls the same helper) now append
:func:`closed_parent_remedy`, and the report `status`/`dangling` prints derives from
the same :data:`REMEDY_REPARENT` phrase, so the dead-end is actionable at the point
a lane actually hits it rather than only in a report it may never read. The refusal
itself is NOT relaxed.
"""

from __future__ import annotations

from pathlib import Path

import focus
import owner_queue as queue_mod
from dataclasses import dataclass
from typing import Sequence

from model import (
    MISSING,
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
    FileClaim,
    Issue,
    Snapshot,
    file_claims_conflict,
)


def _refusal_before_grants(
    snapshot: Snapshot,
    issue: Issue,
    claimed_by_others: frozenset[int] = frozenset(),
    queue_data: dict | None | object = MISSING,
) -> Eligibility | None:
    """The refusals ``eligible`` applies BEFORE any chain-edge grant, or ``None``.

    Extracted so ``frontier`` can apply the SAME predicate (issue #1168). It is
    one function, not two copies, because the two callers disagreeing is the
    defect: ``status`` advertised a #132-shaped issue that ``claim`` refused,
    precisely because the frontier never asked whether the issue's declared epic
    was still open.
    """
    if issue.closed:
        return Eligibility(issue.number, False, REASON_ISSUE_CLOSED, f"#{issue.number} is closed")
    if issue.number in claimed_by_others:
        return Eligibility(
            issue.number, False, REASON_ALREADY_CLAIMED, f"#{issue.number} is claimed by another agent"
        )
    if issue.is_epic:
        return Eligibility(
            issue.number,
            False,
            REASON_EPIC_NOT_WORKABLE,
            f"#{issue.number} is an epic: it closes with its children, it is never a claim target",
        )

    # An issue whose declared epic is closed has no owner to work under: the unit
    # cannot prove issue -> epic -> lane, so it is refused like any unowned unit.
    # The detail names the closed parent AND the remedy that clears it (issue
    # #1259): a refusal that is only a cause is a dead end, and this one is
    # PERMANENT until the board is re-pointed, so a lane that hits it must be told
    # what to do, not just what is wrong.
    epic = snapshot.get(issue.parent) if issue.parent is not None else None
    if epic is not None and epic.closed:
        return Eligibility(
            issue.number,
            False,
            REASON_EPIC_CLOSED,
            f"#{issue.number} declares Parent #{epic.number}, which is closed — "
            f"{closed_parent_remedy(epic)}",
        )

    open_blockers = snapshot.blockers_open(issue)
    if open_blockers:
        listed = ", ".join(f"#{number}" for number in open_blockers)
        detail = f"blocked by {listed}"
        queue_note = queue_mod.queue_detail(issue.number, queue_data, snapshot)
        if queue_note is not None:
            detail = f"{detail} ({queue_note})"
        return Eligibility(issue.number, False, REASON_BLOCKED, detail)
    return None


def _refusal_out_of_epic(
    snapshot: Snapshot,
    issue_number: int,
    focus_path: Path | str | None = None,
) -> Eligibility | None:
    """The epic-focus refusal (``out-of-epic-pooled``), or ``None``.

    Same extraction as :func:`_refusal_before_grants`, for the one refusal
    ``eligible`` applies AFTER its chain-edge grants. ``frontier`` applies it too:
    a frontier that hands out another epic's issue is the incoherence focus exists
    to prevent.

    ``focus_path`` is resolved to ``focus.DEFAULT_PATH`` *at call time* here too
    (rather than taken from the caller): ``frontier`` is a public entry point that
    may be called without one, and a swallowed ``None`` would surface as a
    ``TypeError`` from ``Path()`` instead of a verdict.
    """
    if focus_path is None:
        focus_path = focus.DEFAULT_PATH
    active_epic = focus.active(snapshot, focus_path)
    if active_epic is None:
        return None
    pooled_numbers = {candidate.number for candidate in focus.pooled(snapshot, active_epic.number)}
    if issue_number not in pooled_numbers:
        return None
    return Eligibility(
        issue_number,
        False,
        REASON_OUT_OF_EPIC_POOLED,
        f"#{issue_number} is outside the active epic #{active_epic.number} "
        "(parked in .board/pool.jsonl; promoted just-in-time by a brain directive "
        "when an active-epic child declares it as a blocker)",
    )


def frontier(
    snapshot: Snapshot,
    milestone: str,
    claimed_by_others: frozenset[int] = frozenset(),
    focus_path: Path | str | None = None,
    queue_data: dict | None | object = MISSING,
) -> Issue | None:
    """Lowest-numbered open, unblocked, unclaimed non-epic *claimable* issue in ``milestone``.

    ``frontier`` answers "what is the milestone's next issue", and it is the
    function ``eligible``'s ``next-in-milestone`` grant and the ledger audit both
    resolve against. It therefore applies every refusal that is a property of the
    ISSUE itself — closed, claimed by another agent, an epic, epic-closed, blocked
    — so a milestone frontier can never be an issue that no reader could ever take
    (issue #1168: it named an **epic-closed** issue that ``claim`` refused).

    It deliberately does NOT apply the epic-focus refusal: the milestone frontier
    and "the active epic's frontier" are different questions, and
    ``test_an_out_of_epic_issue_is_never_dispatched_on_the_milestone_frontier``
    pins that the milestone frontier may legitimately be out-of-epic work.
    :func:`claimable_frontier` is the focus-aware question, and is what a reader
    advertising "what to do next" must ask.
    """
    if queue_data is MISSING:
        queue_data = queue_mod.load()
    candidates = [
        issue
        for issue in snapshot.open_issues()
        if issue.milestone == milestone
        and not issue.is_epic
        and issue.number not in claimed_by_others
        and not snapshot.blockers_open(issue)
        and _refusal_before_grants(snapshot, issue, claimed_by_others, queue_data) is None
    ]
    candidates.sort(key=lambda issue: issue.number)
    return candidates[0] if candidates else None


def claimable_frontier(
    snapshot: Snapshot,
    milestone: str,
    claimed_by_others: frozenset[int] = frozenset(),
    focus_path: Path | str | None = None,
    queue_data: dict | None | object = MISSING,
) -> Issue | None:
    """The milestone frontier **that ``eligible`` would also grant** (issue #1168).

    This is the frontier a report must advertise: ``frontier`` applies the
    issue-property refusals, and this applies the epic-focus refusal on top, so
    whatever it names is claimable. The distinction is the whole point of #1168 —
    ``status`` printed the milestone frontier and the reader who acted on it was
    refused by ``claim``.
    """
    if queue_data is MISSING:
        queue_data = queue_mod.load()
    for issue in snapshot.open_issues():
        if issue.milestone != milestone or issue.is_epic:
            continue
        if issue.number in claimed_by_others or snapshot.blockers_open(issue):
            continue
        if _refusal_before_grants(snapshot, issue, claimed_by_others, queue_data) is not None:
            continue
        if _refusal_out_of_epic(snapshot, issue.number, focus_path) is not None:
            continue
        # The candidate survives every refusal, so `eligible` must grant it. Prove
        # it rather than assume it: this function's whole contract is that claim
        # will not refuse what it names.
        if eligible(snapshot, issue.number, claimed_by_others=claimed_by_others).eligible:
            return issue
    return None


def unclaimable_frontier(
    snapshot: Snapshot,
    milestone: str,
    claimed_by_others: frozenset[int] = frozenset(),
    focus_path: Path | str | None = None,
) -> str:
    """The pre-#1168 disagreement, named — or ``""`` when there is none.

    Reports the milestone frontier when ``eligible`` refuses it, which is exactly
    the state ``status`` used to advertise silently: the issue is the milestone's
    next work but no lane can take it.
    """
    milestone_frontier = frontier(snapshot, milestone, claimed_by_others, focus_path)
    if milestone_frontier is None:
        return ""
    verdict = eligible(snapshot, milestone_frontier.number, claimed_by_others=claimed_by_others)
    if verdict.eligible:
        return ""
    return (
        f"unclaimable-frontier: #{milestone_frontier.number} is the frontier of {milestone!r} "
        f"but eligible refuses it ({verdict.reason}) — {verdict.detail}"
    )



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
    queue_data: dict | None | object = MISSING,
) -> Eligibility:
    """Decide whether ``issue_number`` is the next eligible step for this agent.

    ``focus_path`` names the pinned ``.board/focus.json`` (epic focus, #707) so
    the active-epic edge is resolvable offline against a fixture. It defaults to
    ``None`` and is resolved to ``focus.DEFAULT_PATH`` *at call time*: a default
    argument would freeze the module constant at import, so a test (or a caller)
    that repoints the focus would silently keep judging against the old file.

    ``queue_data`` is the parsed owner queue (``owner_queue.load()``, #928),
    used only to name a queue-sourced blocker in the ``blocked`` detail — the
    refusal itself is decided by ``snapshot.blockers_open()``, which already
    carries the queue's edges once the caller has overlaid them (see
    ``owner_queue.overlay`` / ``cli._load_snapshot``). The sentinel default
    ``model.MISSING`` sentinel (rather than ``None``) lets a caller explicitly
    pass ``None`` to mean "no queue" without it being confused with "use the
    committed file".
    """
    if focus_path is None:
        focus_path = focus.DEFAULT_PATH
    if queue_data is MISSING:
        queue_data = queue_mod.load()
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

    # The refusals eligible applies before any chain-edge grant live in ONE place
    # (`_refusal_before_grants`), because `frontier` must apply exactly the same
    # predicate (issue #1168) — see the module docstring.
    refusal = _refusal_before_grants(snapshot, issue, claimed_by_others, queue_data)
    if refusal is not None:
        return refusal

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
    # Shared with `frontier` for the same reason as the refusals above.
    refusal = _refusal_out_of_epic(snapshot, issue_number, focus_path)
    if refusal is not None:
        return refusal

    milestone = active_milestone(snapshot, active_claims)
    if issue.milestone and issue.milestone == milestone:
        candidate = frontier(snapshot, milestone, claimed_by_others, focus_path, queue_data)
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


@dataclass(frozen=True)
class WavePlan:
    """A collision-aware ready wave (issue #740, dispatch half).

    ``admitted`` is provably pairwise file-disjoint: no two issues in it name an
    overlapping file. ``refusals`` names, BY the losing issue and the file and
    the winning issue, every candidate held back for a collision — never a
    silent drop. ``unverifiable`` names every candidate that declared no
    ``Files:`` line at all: it is admitted (an undeclared issue cannot be
    proven to collide with anything), but it is reported by name rather than
    silently treated as disjoint.
    """

    admitted: tuple[int, ...]
    refusals: tuple[str, ...]
    unverifiable: tuple[int, ...]


def wave_plan(candidates: Sequence[Issue]) -> WavePlan:
    """Greedy, deterministic, pairwise-disjoint subset of ``candidates``.

    The predicate is ``model.file_claims_conflict`` — the SAME function the
    claim-time (reconcile) path uses (``claims.find_file_conflict``), so the
    dispatch-time and claim-time notions of "these two lanes collide" cannot
    drift apart. Region-aware: an issue whose ``Files:`` line named per-file
    regions is *not* re-implemented here — declared files are whole-file
    ``FileClaim`` records (``regions=None``), which is the strict/conservative
    reading a wave-planning pass wants (it never speculatively admits two
    candidates that both touch a file just because their regions might not
    overlap once written).
    """
    admitted: list[int] = []
    unverifiable: list[int] = []
    refusals: list[str] = []
    owner: dict[str, int] = {}
    for issue in candidates:
        if not issue.files:
            unverifiable.append(issue.number)
            admitted.append(issue.number)
            continue
        collided = False
        for path in issue.files:
            holder = owner.get(path)
            if holder is not None:
                mine = FileClaim(path=path, regions=None)
                theirs = FileClaim(path=path, regions=None)
                if file_claims_conflict(mine, theirs):
                    refusals.append(
                        f"lane-file-collision: {path} already owned by #{holder}"
                    )
                    collided = True
        if collided:
            continue
        for path in issue.files:
            owner.setdefault(path, issue.number)
        admitted.append(issue.number)
    return WavePlan(
        admitted=tuple(admitted),
        refusals=tuple(refusals),
        unverifiable=tuple(unverifiable),
    )


# --- dangling-on-a-closed-epic reporting (issue #1179) ------------------------
#
# `REASON_EPIC_CLOSED` refuses an issue that declares `Parent:` to a CLOSED epic,
# and it is right to: the epic that would own the work is gone, so the unit cannot
# prove issue -> epic -> lane. What was missing is the other half of a refusal
# worth having — a *report*. Seven open issues sat in exactly that state, refused
# by `claim`/`eligible` and named by nothing: invisible AND permanently
# unclaimable. These helpers name them, with the remedy, so the dead-end is
# visible and reachable rather than silent. The refusal itself is NOT relaxed.
#
# Issue #1259 added the half #1179 left out: the *report* once existed but the
# *refusal* a lane actually hits at `claim`/`dispatch` still named only the cause,
# so a lane that ran the `Verify:` command was told the work was unclaimable and
# never what to do — the dead-end was visible to a reader of `status` and not to
# the agent that hit it. The remedy is now ONE phrase (`REMEDY_REPARENT`) that
# both the report and the refusal are built from, so the two consumers cannot
# drift into telling a reader two different things.

#: The ONE action that clears an `epic-closed` dead-end (issue #1259). Both the
#: *report* (`status`/`dangling`, via ``REMEDIATION_REPARENT`` below) and the
#: *refusal* a lane hits (`order.eligible`, and the claim-time arbitration in
#: ``claims._arbitrate_unaudited``, via :func:`closed_parent_remedy`) derive from
#: this single phrase — one source, so the two surfaces agree by construction.
REMEDY_REPARENT = (
    "re-point the issue's `Parent:` at the open epic that now owns the work "
    "(or close the issue if the work is gone)"
)

REMEDIATION_REPARENT = (
    f"remediate: {REMEDY_REPARENT} — an issue whose declared parent is closed "
    "is refused `epic-closed` and can never be claimed"
)


def closed_parent_remedy(parent: Issue) -> str:
    """The remedy an ``epic-closed`` refusal names, with the closed parent (#1259).

    A refusal whose detail is only the cause (``#'s epic is closed``) is a dead
    end: the lane that hit it is told the work cannot be claimed and never what to
    do about it. This names BOTH halves a refused lane needs — the closed parent,
    by number and title, so the reader does not have to look it up, and the one
    action that clears the refusal.

    It is called by ``order.eligible`` (and therefore ``order.frontier``, which
    applies the same predicate) AND by the claim-time arbitration
    (``claims._arbitrate_unaudited``). One function, not two copies: the pre-flight
    and the mutation point must not disagree about the reason *or* the remedy.
    """
    title = parent.title.strip() or "untitled"
    return (
        f"remedy: {REMEDY_REPARENT} — its declared parent #{parent.number} ({title}) is closed"
    )


def dangling_on_closed_parent(snapshot: Snapshot) -> list[tuple[Issue, Issue]]:
    """Open issues whose declared parent is CLOSED, as ``(issue, closed_parent)`` pairs.

    Sorted by issue number so the report is deterministic. An issue whose declared
    parent is not in the snapshot at all is NOT reported here: that is an
    `unknown-issue`-shaped edge (a stale snapshot), a different defect with a
    different remedy, and conflating the two would make this report lie.
    """
    pairs: list[tuple[Issue, Issue]] = []
    for issue in snapshot.open_issues():
        if issue.parent is None:
            continue
        parent = snapshot.get(issue.parent)
        if parent is not None and parent.closed:
            pairs.append((issue, parent))
    pairs.sort(key=lambda pair: pair[0].number)
    return pairs


def dangling_epic_findings(snapshot: Snapshot) -> list[str]:
    """One named finding per dangling issue — the string a gate asserts on."""
    return [
        f"dangling-epic: #{issue.number} declares Parent #{parent.number}, which is closed"
        f" ({parent.title.strip() or 'untitled'})"
        for issue, parent in dangling_on_closed_parent(snapshot)
    ]

