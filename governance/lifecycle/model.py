"""The GitHub work-item lifecycle and its closure invariants (issue #269).

Isolation (#263) guarantees a lane *opens* correctly: one identity, one branch,
one worktree, one signature. This module covers the other half — the **close**.
A work item that reaches a terminal state must leave every artifact it created in
its terminal state, and the machine must be able to prove it.

The gap it closes was measured, not imagined. Closing the isolation lane drifted
in five separate ways, none of them caught by a gate: the merge command could not
delete the branch and the branch survived until deleted by hand; the fleet's own
loop re-claimed the closed issue and left a live claim and a worktree behind; the
authorisation directive had to be consumed manually; and the board refresh exposed
issues that had been filed without their declaring labels. Each was a step in a
process that nobody owned.

So the process is made explicit: a closed vocabulary of **stages** and a closed
vocabulary of **invariants**, each with the requirement it enforces and the
remediation that clears it. A name that exists only in prose cannot be gated; a
name in this list can.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Tuple

#: The stages a work item passes through, in order. ``reclaimed`` is terminal.
STAGES: Tuple[str, ...] = (
    "filed",
    "claimed",
    "laned",
    "opened",
    "verified",
    "merged",
    "closed",
    "reclaimed",
)

TERMINAL_STAGE = STAGES[-1]


@dataclass(frozen=True)
class Invariant:
    """One closure requirement: a stable name, what it demands, how to clear it.

    ``subject_kind`` keeps applicable them correctly. ``item`` invariants are owed
    by a work item in a given state; ``baseline`` invariants are owed by the
    legacy quarantine itself. Without the distinction, adding the quarantine rule
    to the vocabulary would charge every item with a rule about the baseline.
    """

    code: str
    requires: str
    remediation: str
    subject_kind: str = "item"


#: Every way a work item can fail to close hygienically. The tuple is closed: a
#: finding can only carry one of these codes, so the auditor cannot invent a
#: violation class the gate has not been taught to provoke.
INVARIANTS: Tuple[Invariant, ...] = (
    Invariant(
        code="PR_NOT_MERGED",
        requires="the pull request that closed the item is merged, with a recorded merge commit",
        remediation="merge the verified PR (squash) and record its merge commit on the item",
    ),
    Invariant(
        code="VERIFY_EVIDENCE_MISSING",
        requires="a green verification attestation naming the pull request's head commit - the tree that was verified and then merged",
        remediation="run `make verify` on the branch head before merging and record its attestation; evidence names a commit, and a summary is not evidence",
    ),
    Invariant(
        code="BRANCH_NOT_DELETED",
        requires="the source branch that carried the change no longer exists",
        remediation="delete the remote branch (`git push origin --delete <branch>`); the local merge command may have failed to",
    ),
    Invariant(
        code="CLAIM_STILL_HELD",
        requires="no live claim is held on the item",
        remediation="release the claim, or reap it if the holder no longer exists",
    ),
    Invariant(
        code="DIRECTIVE_NOT_CONSUMED",
        requires="an authorisation directive that dispatched the item is consumed, not left sent",
        remediation="consume the directive (`channel.py consume --id <id>`) so the loop cannot re-execute the order",
    ),
    Invariant(
        code="LANE_NOT_RECLAIMED",
        requires="the item's lane worktree and session record are gone",
        remediation="close the lane (`governance/isolation/cli.py close --session <id>`), committing or discarding its work first",
    ),
    Invariant(
        code="CLOSING_EVIDENCE_MISSING",
        requires="the closing comment carries the real command output that proves the work",
        remediation="close with evidence: the Verify command and its actual output, never a summary",
    ),
    Invariant(
        code="FILING_LABELS_MISSING",
        requires="an open, milestoned item declares the labels the conformance gate holds it to",
        remediation="add the declaring labels (`class:`, and the pillar the class expects) or close the item",
    ),
    Invariant(
        code="QUARANTINE_STALE",
        requires="a legacy quarantine entry is retired once the issue tracking it is no longer open",
        remediation="close the tracking issue and delete the quarantine entry from governance/lifecycle/baseline.json",
        subject_kind="baseline",
    ),
)

INVARIANTS_BY_CODE: Dict[str, Invariant] = {invariant.code: invariant for invariant in INVARIANTS}

#: Invariants a *work item* can owe, by the state it is in.
ITEM_INVARIANTS: Tuple[Invariant, ...] = tuple(inv for inv in INVARIANTS if inv.subject_kind == "item")

#: Labels the conformance gate requires on an open, milestoned item.
DECLARING_LABEL_PREFIXES: Tuple[str, ...] = ("class:",)


def invariant(code: str) -> Invariant:
    """Look up an invariant by name, refusing a code outside the closed set."""
    try:
        return INVARIANTS_BY_CODE[code]
    except KeyError:
        known = ", ".join(sorted(INVARIANTS_BY_CODE))
        raise KeyError(f"unknown invariant {code!r}; the vocabulary is closed: {known}") from None


def invariants_for(state: str) -> Iterable[Invariant]:
    """The invariants that apply to an item in ``state``.

    A closed item owes every closure invariant; an open item owes only the
    filing rules. Returning the applicable set (rather than all of them) is what
    keeps the audit honest: it cannot report a merged PR missing on work that has
    not been merged yet.
    """
    if state == "closed":
        return tuple(inv for inv in ITEM_INVARIANTS if inv.code != "FILING_LABELS_MISSING")
    return (INVARIANTS_BY_CODE["FILING_LABELS_MISSING"],)


def stage_of(item: dict) -> str:
    """Which lifecycle stage an item's own facts show it reached.

    A *display* helper, deliberately not the enforcement: the invariants in
    ``audit`` are what decide hygiene, and duplicating them here would give the
    repo a second rule to keep in sync. This reads only direct artifact facts (a
    claim, a lane, a pull request, a closed state) so a human can see where an
    item sits without reconstructing it by hand.
    """
    if item.get("state") != "closed":
        lane = item.get("lane") or {}
        if lane.get("session_id") or lane.get("worktree"):
            return "laned"
        if item.get("claim"):
            return "claimed"
        return "filed"
    if (item.get("pr") or {}).get("state") != "merged":
        return "opened"
    if not item.get("branch_deleted", False):
        return "merged"
    if (item.get("claim") or {}).get("live") or (item.get("lane") or {}).get("present"):
        return "closed"
    return TERMINAL_STAGE
