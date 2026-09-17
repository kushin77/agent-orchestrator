"""Close-out executor — drive an item to hygiene, and refuse a false success.

``audit`` says what is broken. This says what to *do* about it, in an order that
is derived from the incident that motivated the module rather than from taste:

1. **merge** first, because the verification evidence must name the merged commit;
2. **record verification** for that commit, so "green" is evidence and not a claim —
   measured in the lane at its verified head, or (when that frozen tree is red only
   because it predates a commit its own squash was composed on) at the merge commit,
   which the record then names as the tree it measured (#1003);
3. **delete the source branch**, which the local merge command measurably fails to
   do while the main checkout holds ``master``;
4. **consume the authorisation directive** *before* releasing the claim — on #263
   the still-pending directive was re-executed by the fleet the moment the claim
   freed, so the ordering is load-bearing, not cosmetic;
5. **release the claim**;
6. **journal the closing evidence**, then 7. **close the issue** — two invariants,
   two actions, because fusing them made an already-closed issue read as a failed
   step while the finding set was empty;
8. **reclaim the lane last**, so a failure anywhere earlier leaves the worktree
   available for the re-run that finishes the job.

Step 8 is not merely *ordered* last, it is **gated** (#786). Ordering alone was an
assertion the code did not implement: the step reclaimed the worktree whether or not
step 2 had recorded anything, and step 2 *measures that same worktree*. So a park, or
a failure, destroyed the only tree the item's evidence could come from — and the
invariant naming that evidence became permanently unsatisfiable for work that
*was* verified. The lane is therefore kept while the item still owes its
verification, refused by name, and the next pass finishes the job; the invariant's
retry is reachable rather than assumed.

Every step is idempotent: it first asks the operations port for the current state
and skips when the invariant already holds. The result is never success by
assertion — the executor re-derives the findings afterwards and reports ``ok``
only when the set is empty. A partial close reports the steps it could not
finish, which is exactly what the caller needs to act on.

The verdict is a **tri-state**, because a step can end in a way that is *neither a
pass nor a failure*: the gate was parked at its box-wide permit cap, its permit
store could not be trusted, or it was killed by a signal. Nothing was measured in
any of those cases, and reporting one as a broken invariant asserts a fact nobody
measured (#840). An unassessed step therefore yields ``CANNOT-ASSESS`` — never
``NOT-OK``, and never ``OK``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol

from governance.lifecycle.audit import Finding, audit_item
from governance.lifecycle.gate import UNASSESSED_VERDICTS, CannotAssess
from governance.lifecycle.model import evidence_green, owes_closure, verified_head
from governance.lifecycle.report import (
    BoardReport,
    BoardReporter,
    board_report_findings,
)

#: How a step ended: performed, skipped, failed — or one of the unassessed
#: verdicts (``parked``/``unassessed``), which are a step's own outcome rather than
#: a failure of it. ``REFUSED`` is a fifth: the step was deliberately *not* run,
#: because running it would destroy evidence a step above it still owes (#786).
PERFORMED = "performed"
SKIPPED = "skipped"
FAILED = "failed"
REFUSED = "refused"

#: The invariant a parked verification leaves unmeasured (#840).
VERIFY_INVARIANT = "VERIFY_EVIDENCE_MISSING"

#: The three verdicts close-out can reach.
OK = "ok"
NOT_OK = "not-ok"
CANNOT_ASSESS = "cannot-assess"


class CloseOutOps(Protocol):
    """The effects close-out needs. Injected, so the executor is testable offline."""

    def merge_pull_request(self, number: int) -> str:
        """Squash-merge the pull request; return the merge commit."""

    def record_verification(self, issue: int, commit: str, landing: str = "", drifted: str = "") -> str:
        """Record a green verification attestation naming ``commit``.

        ``landing`` is the commit the pull request was **squash-merged as**, and is
        given only when it *was* merged. A squash merge creates a new commit on the
        default branch that the verified head is not an ancestor of, so the port may
        admit a lane cut from the default branch when it contains ``landing`` and
        ``landing`` carries the same tree as ``commit`` (#1098). An unmerged item has no
        landing, and therefore still requires the lane to be *at* ``commit``.

        ``drifted`` is the pull request's live head where the branch advanced *after* the
        squash carried it, so that head's tree never landed and ``commit`` is the landing
        instead (#1149). It is journalled as provenance and never widens what is admitted.

        Raises ``governance.lifecycle.gate.CannotAssess`` — and *not* a generic
        error — when the gate produced no verification result at all (it was parked,
        its permit store was unusable, or it was killed by a signal), so the executor
        can report that outcome as unassessed instead of as a failure (#840).
        """

    def tree_relation(self, left: str, right: str) -> str:
        """Compare two commits' trees: ``"same"``, ``"different"`` or ``"unknown"``.

        Asked by :func:`evidence_subject` to decide *which* commit a merged item's evidence
        is against (#1149). The driver owns the decision; the port owns reading the
        repository the decision is made about — so the driver never shells out itself.
        """

    def commit_is_superset(self, larger: str, smaller: str) -> bool:
        """Does ``larger``'s tree carry every path ``smaller``'s does, unchanged?

        Asked by :func:`evidence_subject` before it trusts a tree difference as the
        branch-advanced drift (#1149): that shape is specifically the live head adding
        content on top of what the squash carried, never touching it — so the squash's
        tree is fully, unchanged, still sitting inside the live head's. A difference
        that removes or changes any of ``smaller``'s content (#1003: the frozen head
        predates a commit its own squash was composed on, so the squash carries content
        — the sibling declaration — the head never had) is not this shape at all.
        """

    def delete_branch(self, branch: str) -> str:
        """Delete the remote source branch."""

    def consume_directive(self, directive_id: str) -> str:
        """Mark the authorisation directive handled."""

    def release_claim(self, issue: int, agent: str) -> str:
        """Release (or reap) the claim held on ``issue``."""

    def close_issue(self, issue: int, evidence: str) -> str:
        """Close the issue, with the evidence that proves the work."""

    def record_closing_evidence(self, issue: int, evidence: str) -> str:
        """Journal the evidence that justifies closing the item."""

    def reclaim_lane(self, session_id: str) -> str:
        """Remove the lane worktree and its record."""

    def refresh(self, item: dict) -> dict:
        """The item's current state, after the effects just performed.

        The item passed in is the *pre-close* state; once the operations above
        have run it is stale. The final audit must read the world the ops
        changed, not the snapshot it started from - otherwise a fully successful
        close reads as NOT-OK, the inverse of the false green this module exists
        to prevent.
        """


@dataclass
class Step:
    """One attempted (or skipped) close-out action."""

    action: str
    outcome: str
    detail: str = ""


@dataclass(frozen=True)
class Unassessed:
    """Something close-out could not assess, and what clears it.

    Deliberately not a ``Finding``. A finding asserts a fact — "this invariant is
    broken" — and is filed on the board as a defect; an unassessed rule asserts the
    *absence* of the fact required to decide it (the gate never ran, so whether a
    green attestation exists is unknown). Reporting the second as the first is the
    defect of #840, so they are different types with different verdicts rather than
    two shades of the same message.
    """

    action: str
    code: str = ""
    subject: str = ""
    detail: str = ""
    remediation: str = ""

    def __str__(self) -> str:
        head = "  ".join(part for part in (self.code, self.subject) if part) or self.action
        return f"{head}  {self.detail}"


@dataclass
class CloseOutResult:
    """What close-out did, and what is still broken (or unmeasured) afterwards."""

    issue: int
    steps: list[Step] = field(default_factory=list)
    remaining: list[Finding] = field(default_factory=list)
    not_assessed: list[Unassessed] = field(default_factory=list)
    #: Steps withheld on purpose, with the reason: not run, and not because the
    #: invariant already held. Kept beside ``not_assessed`` rather than inside it,
    #: because a withheld step *was* assessed — the driver decided not to take it.
    withheld: list[str] = field(default_factory=list)
    board_reports: list[BoardReport] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        """``ok`` / ``not-ok`` / ``cannot-assess``, *derived* from what remains.

        A known-broken invariant outranks an unmeasured one: if something is
        measurably broken the item is NOT-OK whatever else could not be measured,
        and the unassessed entries are still printed beside it.
        """
        if self.remaining:
            return NOT_OK
        if self.not_assessed:
            return CANNOT_ASSESS
        return OK

    @property
    def ok(self) -> bool:
        """Success is the *absence of findings*, never the absence of exceptions."""
        return self.verdict == OK

    @property
    def cannot_assess(self) -> bool:
        """True when nothing is known broken and something could not be measured.

        The caller's exit code depends on this: a parked close-out is rc 2, never
        rc 0 (a pass it did not measure) and never rc 1 (a failure it did not
        measure).
        """
        return self.verdict == CANNOT_ASSESS

    @property
    def failed_steps(self) -> list[Step]:
        return [step for step in self.steps if step.outcome == FAILED]

    @property
    def unassessed_steps(self) -> list[Step]:
        return [step for step in self.steps if step.outcome in UNASSESSED_VERDICTS]


def _run(result: CloseOutResult, action: str, op: Callable[[], str], needed: bool) -> bool:
    """Attempt one action, recording the outcome instead of raising.

    A failing step must not abort the remaining ones: the point of close-out is to
    finish everything it can and report the rest, not to stop at the first
    obstacle and leave the other artifacts dangling.

    An unassessed step is caught *before* the generic handler, because it is not a
    failure and must not be recorded as one: since #840 a step can end ``parked``
    (the gate was refused a permit and ran nothing) or ``unassessed`` (its permit
    store could not be trusted, or it was killed by a signal). Those carry their
    own outcome and land in ``not_assessed``, so the report can say CANNOT-ASSESS
    instead of a failure nobody measured.
    """
    if not needed:
        result.steps.append(Step(action, SKIPPED, "already satisfied"))
        return True
    try:
        detail = op()
    except CannotAssess as exc:
        result.steps.append(Step(action, exc.verdict, exc.detail[:300]))
        result.not_assessed.append(
            Unassessed(action=action, detail=exc.detail, remediation=exc.remediation)
        )
        return False
    except Exception as exc:  # noqa: BLE001 - the outcome is data, not a crash
        result.steps.append(Step(action, FAILED, f"{type(exc).__name__}: {exc}"[:300]))
        return False
    result.steps.append(Step(action, PERFORMED, str(detail)[:300]))
    return True


def evidence_subject(item: dict, ops: CloseOutOps) -> tuple[str, str, str]:
    """``(subject, landing, drifted)`` — the commit a merged item's evidence must name.

    The subject is the commit whose tree is the tree that **landed**, because that is what
    "the tree which was verified is the tree that landed" means once the branch moves on.
    For an ordinary merged item the pull request's head commit *is* that commit and nothing
    changes: its tree and the landing's are the same object, so the convention every
    existing record and the ``clean_item`` fixture use keeps its meaning exactly.

    A branch that received commits **after** the squash is the case this exists for
    (#1149, measured on #977/#978 through PR #984). GitHub's ``head_commit`` for such a
    pull request is a tree that **never landed and never gated** — measured 54 files and
    4731 insertions away from the commit the squash landed as — so it cannot be the subject
    of evidence for work that landed. The landing carries the landed tree by construction
    and is the canonical commit that does, so it becomes the subject while the live head is
    returned as ``drifted`` and **disclosed** in the attestation, never silently dropped.

    Fail-closed direction: an **unreadable** comparison leaves the subject where it was.
    Nothing is then *claimed* that was not measured — the subject is the pull request's
    head, exactly as before this rule — and the admissibility rule still refuses any lane
    that would need the tree nobody could read. Treating an unreadable pair as a measured
    drift would report a mismatch nobody measured; treating it as equal would admit a tree
    nobody compared.

    A tree difference is substituted only when ``head``'s tree is a **superset** of
    ``landing``'s — the branch genuinely continued *past* the squash, adding content
    without touching what was squashed, so everything the landing carries is still
    sitting in the live head unchanged. A different tree that is **not** in that
    direction (#1003: the frozen head predates a commit its own squash was composed on,
    so the landing carries content — the sibling declaration — the head never had, and
    a squash disconnects the two commits' ancestry either way) is not this shape at
    all: it is a lane whose head may still be gated directly, and whose fallback
    (measuring the merge commit only once that gate measures red) is
    `record-verification`'s own, not this function's to pre-empt. Pre-empting it here
    would skip the lane's own run entirely and misname a red-and-fixed-on-arrival lane
    as "drifted" for a head that never carried the landing's content in the first place.
    """
    pr = item.get("pr") or {}
    head = str(pr.get("head_commit") or "")
    merged = str(pr.get("state") or "").lower() == "merged"
    landing = str(pr.get("merge_commit") or "") if merged else ""
    if not landing or not head or head == landing:
        # Not merged, no landing recorded, or the live head *is* the landing: the ordinary
        # shape, and the one where no repository need be read at all.
        return head, landing, ""
    if ops.tree_relation(head, landing) == "different" and ops.commit_is_superset(head, landing):
        return landing, landing, head
    return head, landing, ""


def closeout(
    item: dict,
    ops: CloseOutOps,
    evidence: str = "",
    reporter: BoardReporter | None = None,
    apply: bool = False,
) -> CloseOutResult:
    """Drive one item to hygiene in dependency order.

    Returns the steps taken and the invariants still broken. ``ok`` is true only
    when nothing remains — the caller may never treat it as advisory.

    ``reporter`` is the board-reporting seam (issue #321): when it is given and a
    non-terminal artifact remains, each finding is filed on the board —
    idempotently, and only when ``apply`` is true.
    """
    issue = int(item.get("issue") or 0)
    result = CloseOutResult(issue=issue)

    if not owes_closure(item):
        # Nothing has landed yet: the item is still in flight and close-out has
        # nothing to drive. Note this is deliberately *not* "the issue is open" —
        # closing the issue is one of the steps below.
        result.steps.append(
            Step("inspect", SKIPPED, "the change has not landed (no merged pull request); close-out applies once it has")
        )
        result.remaining = audit_item(item)
        return _finish(result, reporter, apply)

    pr = item.get("pr") or {}
    claim = item.get("claim") or {}
    directive = item.get("directive") or {}
    verified_commit, landing, drifted = evidence_subject(item, ops)

    # 1. merge (the verified head is what lands).
    _run(result, "merge-pull-request", lambda: ops.merge_pull_request(int(pr.get("number") or 0)),
         pr.get("state") != "merged")

    # 2. verification evidence for the verified head commit. The question "is it
    #    already recorded" is the audit's own rule (``model.evidence_green``), not a
    #    second copy of it: a record that names the *merged* tree as the tree it
    #    measured is a recorded verification too (#1003), and reading it as absent
    #    would re-gate a green item on every pass. ``model.evidence_green`` is offline
    #    and reads the same subject convention ``evidence_subject`` resolves here — the
    #    verified head, or (#1149) the landing when the branch drifted past the squash
    #    — so the two can never disagree about what is already on record.
    _run(
        result,
        "record-verification",
        lambda: ops.record_verification(issue, verified_commit, landing, drifted),
        not evidence_green(item),
    )

    # 3. the source branch, which a local squash-merge reliably leaves behind.
    _run(result, "delete-branch", lambda: ops.delete_branch(str(pr.get("branch") or "")),
         not item.get("branch_deleted", False))

    # 4. consume the directive BEFORE freeing the claim, or the pending order is
    #    re-dispatched the moment the claim is released (observed on #263).
    _run(
        result,
        "consume-directive",
        lambda: ops.consume_directive(str(directive.get("id") or "")),
        bool(directive) and directive.get("state") != "done",
    )

    # 5. release the claim.
    _run(result, "release-claim", lambda: ops.release_claim(issue, str(claim.get("agent") or "")), bool(claim.get("live")))

    # 6. journal the evidence, then 7. close the issue - two invariants, two
    #    actions, each idempotent on its own. Fusing them made an already-closed
    #    issue look like a failed step while the finding set was empty.
    _run(
        result,
        "record-closing-evidence",
        lambda: ops.record_closing_evidence(issue, evidence or _default_evidence(issue)),
        not item.get("closing_evidence", False),
    )
    # Always reported, so the step set is uniform: a caller sees the same eight
    # steps whether or not each one needed to do anything.
    _run(
        result,
        "close-issue",
        lambda: ops.close_issue(issue, evidence or _default_evidence(issue)),
        str(item.get("state") or "").lower() != "closed",
    )

    # 8. reclaim the lane last — and only while it is not the last copy of evidence
    #    the item still owes. The comment here has always said "a failure above must
    #    leave the worktree for the re-run"; until #786 the code reclaimed
    #    unconditionally, so step 2's failure destroyed the only tree step 2's retry
    #    could have measured from — which is what made the invariant permanently
    #    unsatisfiable, and the retry the eight-step design assumes unreachable.
    _reclaim_lane(result, item, ops, verified_commit)

    # Never success by assertion: re-collect the item through the operations
    # port and re-derive from its *fresh* facts. The pre-close item is stale once
    # the effects above have run; auditing it would report a successful close as
    # NOT-OK.
    fresh = ops.refresh(item)
    result.remaining = audit_item(fresh)
    verification = next((step for step in result.steps if step.action == "record-verification"), None)
    if verification is not None and verification.outcome in UNASSESSED_VERDICTS:
        _retire_unmeasured_verification(result, fresh, verification, evidence_subject(fresh, ops)[0])
    _retire_withheld_reclaim(result)
    return _finish(result, reporter, apply)


def _verification_owed(item: dict, result: CloseOutResult, subject: str) -> str:
    """Why the item must keep its lane, or ``""`` when the lane may go.

    The question is deliberately not "did a step fail": a failure five steps above
    holds nothing the lane is the only source of. It is precisely whether the
    attestation ``record-verification`` owes is **on record for the verified head**,
    because while it is not, this worktree is the only tree it can be measured from.
    ``subject`` is the commit the record must name — resolved the same way the step
    resolved it (#1149), so the two can never disagree about what is owed.

    Step 2's own outcome is read first: a ``performed`` verification is the record
    itself, and only then does the item's pre-close state decide (a step that was
    skipped as already satisfied is a record that was already there). The state half is
    the audit's rule itself (``model.evidence_green``) — including the #1003 clause that
    lets an attestation name the *merged* tree as the tree it measured, which is still a
    recorded verification of the verified head.
    """
    step = next((entry for entry in result.steps if entry.action == "record-verification"), None)
    if step is not None and step.outcome == PERFORMED:
        return ""
    if evidence_green(item):
        return ""
    outcome = step.outcome if step is not None else "not attempted"
    detail = (step.detail if step is not None else "")[:160]
    return (
        f"the item still owes record-verification ({outcome}: {detail}) — reclaiming the lane would "
        "remove the worktree that attestation is measured from, permanently, so the lane is kept and "
        "the item is closed out again once the verification is recorded"
    )


def _reclaim_lane(result: CloseOutResult, item: dict, ops: CloseOutOps, subject: str) -> None:
    """Reclaim the lane — unless doing so would destroy evidence the item still owes.

    ``record-verification`` measures the lane worktree; step 8 removes it. The order
    between them is therefore load-bearing *and irreversible*: measured on
    #622/#623/#626 (#786) and #793 (#854), a park or a failure left the attestation
    unrecorded, step 8 removed the tree, and the invariant could never be satisfied
    again — a permanently red record for work that *was* verified, with a remediation
    ("run make verify on the branch head") that had no branch left to run on.

    So the step is refused, by name, while the item still owes its verification: the
    lane stays, the finding that remains is the *root cause*, and the next close-out
    finishes the job — the retry the eight-step design already assumed, made
    reachable instead of asserted.
    """
    lane = item.get("lane") or {}
    if not lane.get("present"):
        result.steps.append(Step("reclaim-lane", SKIPPED, "already satisfied"))
        return
    owed = _verification_owed(item, result, subject)
    if owed:
        result.steps.append(Step("reclaim-lane", REFUSED, owed))
        result.withheld.append(f"reclaim-lane: {owed}")
        return
    _run(result, "reclaim-lane", lambda: ops.reclaim_lane(str(lane.get("session_id") or "")), True)


def _retire_withheld_reclaim(result: CloseOutResult) -> None:
    """A lane kept *on purpose* is not the finding it looks like (#786).

    ``audit`` is offline and sees only that the lane is still provisioned, so it
    charges ``LANE_NOT_RECLAIMED`` — whose remediation ("close the lane, committing or
    discarding its work first") is *harmful* in this state: following it destroys the
    tree the item's missing evidence has to be measured from, which is the wedge this
    fix removes. Reporting a finding whose remedy is the defect would be worse than
    reporting nothing.

    Nothing is hidden by retiring it: the refusal is printed as a step and named in
    ``withheld``, and the item's remaining finding is the root cause that actually
    blocks it. The invariant is retired only when the driver itself withheld the
    step — a lane another process left behind is still charged.
    """
    if not any(step.action == "reclaim-lane" and step.outcome == REFUSED for step in result.steps):
        return
    result.remaining = [finding for finding in result.remaining if finding.code != "LANE_NOT_RECLAIMED"]


def _retire_unmeasured_verification(result: CloseOutResult, fresh: dict, step: Step, subject: str) -> None:
    """A verification the gate never measured is *unassessed*, not missing (#840).

    ``audit`` is offline by design and reads only the item, so it cannot know that
    the gate was refused a permit; where the verification invariant fired for
    exactly that reason, the finding asserted something nobody measured. It is
    retired from ``remaining`` and the invariant is carried in ``not_assessed``
    instead, which changes the verdict to CANNOT-ASSESS and files nothing on the
    board — a defect report generated by capacity is a false report.

    Two shapes are deliberately **not** retired, because a gate that never ran does
    not explain either: an attestation that is recorded and names the wrong commit
    is a measured mismatch, and an item that records no verified head commit at all
    is broken for a reason no gate run could fix.
    """
    verify = fresh.get("verify") or {}
    if verify.get("ok") or not subject:
        return
    retired = [finding for finding in result.remaining if finding.code == VERIFY_INVARIANT]
    if not retired:
        return
    result.remaining = [finding for finding in result.remaining if finding.code != VERIFY_INVARIANT]
    gate_record = next((record for record in result.not_assessed if record.action == step.action), None)
    result.not_assessed.append(
        Unassessed(
            action=step.action,
            code=VERIFY_INVARIANT,
            subject=f"#{result.issue}",
            detail=(
                f"no attestation is recorded and the gate was {step.outcome}, so whether the "
                "verified head commit is green is unmeasured, not missing"
            ),
            remediation=gate_record.remediation if gate_record else "",
        )
    )


def _finish(result: CloseOutResult, reporter: BoardReporter | None, apply: bool) -> CloseOutResult:
    """Surface any remaining findings on the board, then return the result.

    Only *findings* are filed. An unassessed rule is not filed, on purpose: the
    board is for defects, and a capacity condition is not one. The verdict the
    caller receives says CANNOT-ASSESS instead.
    """
    if reporter is not None and result.remaining:
        result.board_reports = board_report_findings(result.remaining, reporter, apply=apply)
    return result


def _default_evidence(issue: int) -> str:
    return f"Closed by the lifecycle close-out for #{issue}; verification evidence is recorded in the verify attestation."


def describe(result: CloseOutResult) -> str:
    """A one-block human summary: what ran, what remains, and what was not measured."""
    verdict = {OK: "OK", NOT_OK: "NOT-OK", CANNOT_ASSESS: "CANNOT-ASSESS"}[result.verdict]
    lines = [f"close-out #{result.issue}: {verdict}"]
    for step in result.steps:
        lines.append(f"  {step.outcome:<11} {step.action}: {step.detail}")
    for record in result.withheld:
        lines.append(f"  WITHHELD    {record}")
    for finding in result.remaining:
        lines.append(f"  REMAINS  {finding}")
        lines.append(f"           -> {finding.remediation}")
    for record in result.not_assessed:
        lines.append(f"  UNASSESSED  {record}")
        if record.remediation:
            lines.append(f"           -> {record.remediation}")
    return "\n".join(lines)
