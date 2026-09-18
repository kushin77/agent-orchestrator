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

    ``subject_kind`` keeps applicability correct. ``item`` invariants are owed by
    a work item in a given state; ``baseline`` invariants are owed by the legacy
    quarantine itself; ``epic`` invariants are owed by a parent (an epic) whose
    declared children must all be terminal before the epic may close. The third
    kind exists because the subject of an epic rule is *the epic's child set* — a
    parent-child relationship the item's own artifacts do not carry — so charging
    it as an ``item`` invariant would make every leaf item owe a rule about
    children it has none of, and charging it as ``baseline`` would misname live
    work as legacy.
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
        requires=(
            "a green verification attestation naming the commit whose tree is the tree that landed - "
            "the pull request's head commit, or the commit the squash landed as when the branch "
            "advanced after it (#1149) - and, when the attestation records the tree it measured, a "
            "tree that is that commit's own tree or the merged tree the squash composed (#1003)"
        ),
        remediation=(
            "run `make verify` on the branch head before merging and record its attestation; evidence "
            "names a commit, and a summary is not evidence. Close out BEFORE the lane is torn down "
            "(`governance/lifecycle/cli.py close --issue <n>`), or keep the verified commit - or the "
            "merge commit - reachable: the attestation is measured from one of these, and the driver "
            "refuses to reclaim a lane while this invariant is unsatisfied (#786, #1003). For a "
            "SQUASH-merged pull request the lane may instead be a tree cut from the default branch "
            "after the merge: it is admitted when it contains the commit the squash landed as and that "
            "landing carries the verified work - the verified tree, or the change the verified commit "
            "introduced where a sibling landing moved the base under the squash - and the record then "
            "names all three commits (#1098, #1298). Where the branch advanced *after* the squash, the "
            "live head's tree never "
            "landed and the evidence names the commit the squash landed as instead, recording the "
            "drifted head it moved past (#1149). And when the frozen head's own tree is red, the merge "
            "commit itself is measured in a throwaway detached tree - the tree the change actually "
            "landed as - and the record says so (#1003)"
        ),
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
        remediation="close the item out (`governance/lifecycle/cli.py close --issue <n>`), which owns the terminal move of the directive from .fleet/sent/ to .fleet/done/, so the loop cannot re-execute the order",
    ),
    Invariant(
        code="LANE_NOT_RECLAIMED",
        requires="the item's lane worktree AND its session record are gone - a record whose worktree a reaper already removed is still a record, and the invariant is owed until the record is retired",
        remediation="close the lane (`governance/isolation/cli.py close --session <id>`), committing or discarding its work first; machine-managed board state (`.board/focus.json`) is ignored by that test and reported, never silently discarded",
    ),
    Invariant(
        code="CLOSING_EVIDENCE_MISSING",
        requires="the closing comment carries the real command output that proves the work",
        remediation="close with evidence: the Verify command and its actual output, never a summary",
    ),
    Invariant(
        code="ISSUE_NOT_CLOSED",
        requires="the issue itself is closed, so the item is off the board",
        remediation="close the issue with its evidence comment (`gh issue close <n> --comment ...`)",
    ),
    Invariant(
        code="CHILD_NOT_CLOSED",
        requires="an epic that is closed must not still have a declared child open - every child whose body names this epic as parent must itself be closed",
        remediation="close every declared child (an issue whose body's first line is `Parent: #<n>` naming this epic) before closing the epic",
        subject_kind="epic",
    ),
    Invariant(
        code="EPIC_CHILD_MARKER_MISSING",
        requires="an epic whose child set is checked must reach every child it is checked against - a supplied child that declares no `Parent: #<n>` marker anywhere in its body is unverifiable and is REPORTED, never silently passed",
        remediation="give the child the marker its parent is established by (a line starting `Parent: #<n>` naming the epic), or check the epic against the child that really declares it - an epic that cannot be shown to have children is not an epic whose children are all closed",
        subject_kind="epic",
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


def _state(item: dict, key: str, default: str = "") -> str:
    """A state/status value, canonicalised to lowercase.

    GitHub reports its canonical casing (``OPEN``/``CLOSED``/``MERGED``); every
    comparison in this package is against lowercase. Normalising at the point of
    comparison rather than trusting the collector means a record can come from
    anywhere - the collector, a fixture, a journal - and still be read the same.
    """
    return str((item or {}).get(key) or default).lower()


def owes_closure(item: dict) -> bool:
    """True when the change landed, so the closure invariants apply.

    Deliberately **not** ``state == "closed"``. Closing the issue is itself one of
    the closure steps, so an item whose pull request has merged but whose issue is
    still open is exactly the state close-out exists to finish. Keying
    applicability on the closed state exempts the very items that need closing —
    which is how the first end-to-end run of this module reported OK on an item it
    had not closed at all.
    """
    if _state(item, "state") == "closed":
        return True
    return _state(item.get("pr") or {}, "state") == "merged"


def invariants_for(item: dict) -> Iterable[Invariant]:
    """The invariants an item owes, given the artifacts it actually has.

    Applicability is a function of artifacts, not of a mutable state field: a
    landed change owes the closure invariants, and work still in flight owes only
    the filing rule. That distinction is what keeps the audit honest — it cannot
    charge an unmerged change with a missing merge, and it cannot exempt a merged
    one from being closed out.
    """
    if owes_closure(item):
        return tuple(inv for inv in ITEM_INVARIANTS if inv.code != "FILING_LABELS_MISSING")
    return (INVARIANTS_BY_CODE["FILING_LABELS_MISSING"],)


def verified_head(item: dict) -> str:
    """The commit the item's evidence is held against: its pull request's head.

    The *subject* of the verification invariant, and deliberately not the merge commit:
    a squash merge composes a new commit, so demanding the merge commit would fail every
    correctly-merged item (``audit._closure_findings``).
    """
    return str((item.get("pr") or {}).get("head_commit") or "")


def measurement_venues(item: dict) -> Tuple[str, ...]:
    """Every tree the item's attestation may record as the one it measured.

    Two, and no more, and both bounded by the item's own record:

    1. the **verified head commit** — the tree the lane is gated at, which is what the
       invariant has always named and what every record written before #1003 names;
    2. the **merge commit** — for an item whose pull request is already merged, the tree
       that actually landed. It is the only honest venue when the frozen branch head is
       permanently red because it predates a commit the squash was composed on (#1003):
       no other commit in the object store holds the landed tree.

    A commit the record does not carry is omitted rather than counted as an empty
    string, so an absent merge commit cannot legitimise an empty answer.
    """
    pr = item.get("pr") or {}
    return tuple(commit for commit in (verified_head(item), str(pr.get("merge_commit") or "")) if commit)


def evidence_problem(item: dict) -> str:
    """Why the item's attestation does not count, or ``""`` when it does.

    **One** reader for the whole rule, so the audit's finding and the close-out's
    decision to run (or skip) step 2 cannot drift into two answers — the drift that
    would let a step be skipped on evidence the audit refuses, or re-run on the
    verification it already holds.

    Three ways to fail. The attestation must be green; it must name the verified head
    commit; and — new with #1003 — when it records *which tree it measured*, that tree
    must be one the item's own record legitimises (:func:`measurement_venues`). A
    record that does not say which tree it measured is read exactly as before, so every
    attestation this repo has already written keeps its meaning; a record that says it
    was measured somewhere the item's record does not carry is refused where it used to
    be believed. The clause is a **strengthening**, never a relaxation: the invariant's
    subject is still the verified head commit.

    A record whose ``via`` is ``"contains"`` (#1098) is exempt from the venue check:
    its ``measured`` names a lane HEAD cut from the default branch after a squash
    merge, a commit this offline record cannot itself re-derive — that lane was
    already made to prove it contains the landing *and* carries the verified tree,
    by :meth:`GhOps._admissible`, before the record was ever written. Re-deriving
    that proof here would need the git history this module deliberately never reads
    (audit is offline, #170); trusting the venue check instead would refuse the very
    record #1098 exists to admit.

    A record naming the **landing** rather than the head is admitted too, when it also
    discloses the drift (#1149): the branch advanced after the squash, so the live head's
    tree never landed and cannot be the subject, but the commit the squash landed as does
    hold it — and the record names that commit and the drifted head it moved past, never
    silently substituting one for the other (:func:`names_the_landed_tree`).
    """
    head = verified_head(item)
    verify = item.get("verify") or {}
    pr = item.get("pr") or {}
    if not verify.get("ok"):
        return "no green verification attestation is recorded"
    if not head:
        return "the item records no verified head commit to hold the evidence against"
    recorded = str(verify.get("commit") or "")
    if recorded != head and not names_the_landed_tree(pr, verify):
        return (
            f"the attestation names {recorded[:12] or 'none'}, not the verified head commit "
            f"{head[:12]} and not the commit its tree landed as"
        )
    measured = str(verify.get("measured") or "")
    if measured and str(verify.get("via") or "") != "contains" and measured not in measurement_venues(item):
        landed = str(pr.get("merge_commit") or "")
        return (
            f"the attestation was measured at {measured[:12]}, which is neither the verified head "
            f"commit {head[:12]} nor the merged tree {landed[:12] or 'none'}"
        )
    return ""


def names_the_landed_tree(pr: dict, verify: dict) -> bool:
    """Does the attestation name a commit whose tree is the tree that **landed**?

    Two shapes, and no more (#1149):

    * the **ordinary** one — it names the pull request's head commit, whose tree is the
      tree the squash landed (``verify.commit == pr.head_commit``). That is the
      convention every pre-existing record and the ``clean_item`` fixture use, and the
      reason this invariant must not demand the merge commit: a squash merge creates a
      new commit, so demanding equality there would fail every correctly-merged item.
    * the **drifted** one — the branch received commits after the squash, so the live
      head's tree never landed. The evidence then names the commit the squash landed as
      *as its subject* and records the live head it drifted from. Requiring the drift to
      be recorded is what keeps this honest: a record that merely names the merge commit,
      with no measured drift explaining the substitution, stays a finding.
    """
    commit = str(verify.get("commit") or "")
    landing = str(verify.get("landing") or "")
    drifted = str(verify.get("drifted_head") or "")
    head = str(pr.get("head_commit") or "")
    merge = str(pr.get("merge_commit") or "")
    return bool(commit) and commit != head and commit == landing == merge and drifted == head


def evidence_green(item: dict) -> bool:
    """Whether the item holds a green attestation the audit accepts."""
    return not evidence_problem(item)


def stage_of(item: dict) -> str:
    """Which lifecycle stage an item's own facts show it reached.

    A *display* helper, deliberately not the enforcement: the invariants in
    ``audit`` decide hygiene, and duplicating them here would give the repo a
    second rule to keep in sync. This reads only direct artifact facts (a claim, a
    lane, a pull request, evidence, a closed state) so a human can see where an
    item sits without reconstructing it by hand.
    """
    if not owes_closure(item):
        lane = item.get("lane") or {}
        if lane.get("session_id") or lane.get("worktree"):
            return "laned"
        if item.get("claim") or (item.get("pr") or {}).get("number"):
            return "claimed"
        return "filed"

    pr = item.get("pr") or {}
    if pr.get("state") != "merged":
        return "verified" if evidence_green(item) else "opened"
    if item.get("state") != "closed" or not item.get("branch_deleted", False):
        return "merged"
    if (item.get("claim") or {}).get("live") or (item.get("lane") or {}).get("present"):
        return "closed"
    return TERMINAL_STAGE


def _validate_against_policy() -> None:
    """Refuse, at import time, a closure vocabulary that has drifted from
    ``controls.yaml`` (issue #885). The check runs exactly once, when this
    module is first imported — the same "read at load time, refuse
    immediately" posture ``governance/modules/policy.py`` established for the
    module registry: a rule this file can emit that ``controls.yaml`` does not
    declare (or vice versa) is a policy defect, not a silent gap, so it is
    refused before a single audit or close-out runs against it.

    A gate provocation that wants a mutated policy to be read instead of the
    packaged one points ``AO_LIFECYCLE_CONTROLS`` (``policy.CONTROLS_ENV``) at
    its scratch copy; this import-time check then refuses with that file's
    problem, by name, before any audit or close-out runs.
    """
    from governance.lifecycle import policy as _policy  # noqa: PLC0415 - avoids a cycle

    _policy.load_for_model()


_validate_against_policy()
