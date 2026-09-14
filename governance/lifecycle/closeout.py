"""Close-out executor — drive an item to hygiene, and refuse a false success.

``audit`` says what is broken. This says what to *do* about it, in an order that
is derived from the incident that motivated the module rather than from taste:

1. **merge** first, because the verification evidence must name the merged commit;
2. **record verification** for that commit, so "green" is evidence and not a claim;
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

Every step is idempotent: it first asks the operations port for the current state
and skips when the invariant already holds. The result is never success by
assertion — the executor re-derives the findings afterwards and reports ``ok``
only when the set is empty. A partial close reports the steps it could not
finish, which is exactly what the caller needs to act on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol

from governance.lifecycle.audit import Finding, audit_item
from governance.lifecycle.model import owes_closure
from governance.lifecycle.report import (
    BoardReport,
    BoardReporter,
    board_report_findings,
)

#: How a step ended.
PERFORMED = "performed"
SKIPPED = "skipped"
FAILED = "failed"


class CloseOutOps(Protocol):
    """The effects close-out needs. Injected, so the executor is testable offline."""

    def merge_pull_request(self, number: int) -> str:
        """Squash-merge the pull request; return the merge commit."""

    def record_verification(self, issue: int, commit: str) -> str:
        """Record a green verification attestation naming ``commit``."""

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


@dataclass
class CloseOutResult:
    """What close-out did, and what is still broken afterwards."""

    issue: int
    steps: list[Step] = field(default_factory=list)
    remaining: list[Finding] = field(default_factory=list)
    board_reports: list[BoardReport] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Success is the *absence of findings*, never the absence of exceptions."""
        return not self.remaining

    @property
    def failed_steps(self) -> list[Step]:
        return [step for step in self.steps if step.outcome == FAILED]


def _run(result: CloseOutResult, action: str, op: Callable[[], str], needed: bool) -> bool:
    """Attempt one action, recording the outcome instead of raising.

    A failing step must not abort the remaining ones: the point of close-out is to
    finish everything it can and report the rest, not to stop at the first
    obstacle and leave the other artifacts dangling.
    """
    if not needed:
        result.steps.append(Step(action, SKIPPED, "already satisfied"))
        return True
    try:
        detail = op()
    except Exception as exc:  # noqa: BLE001 - the outcome is data, not a crash
        result.steps.append(Step(action, FAILED, f"{type(exc).__name__}: {exc}"[:300]))
        return False
    result.steps.append(Step(action, PERFORMED, str(detail)[:300]))
    return True


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
    verify = item.get("verify") or {}
    claim = item.get("claim") or {}
    directive = item.get("directive") or {}
    lane = item.get("lane") or {}
    verified_commit = str(pr.get("head_commit") or "")

    # 1. merge (the verified head is what lands).
    _run(result, "merge-pull-request", lambda: ops.merge_pull_request(int(pr.get("number") or 0)),
         pr.get("state") != "merged")

    # 2. verification evidence for the verified head commit.
    verification_recorded = bool(verify.get("ok")) and str(verify.get("commit") or "") == verified_commit
    _run(result, "record-verification", lambda: ops.record_verification(issue, verified_commit), not verification_recorded)

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

    # 8. reclaim the lane last: a failure above must leave the worktree for the re-run.
    _run(
        result,
        "reclaim-lane",
        lambda: ops.reclaim_lane(str(lane.get("session_id") or "")),
        bool(lane.get("present")),
    )

    # Never success by assertion: re-collect the item through the operations
    # port and re-derive from its *fresh* facts. The pre-close item is stale once
    # the effects above have run; auditing it would report a successful close as
    # NOT-OK.
    result.remaining = audit_item(ops.refresh(item))
    return _finish(result, reporter, apply)


def _finish(result: CloseOutResult, reporter: BoardReporter | None, apply: bool) -> CloseOutResult:
    """Surface any remaining findings on the board, then return the result."""
    if reporter is not None and result.remaining:
        result.board_reports = board_report_findings(result.remaining, reporter, apply=apply)
    return result


def _default_evidence(issue: int) -> str:
    return f"Closed by the lifecycle close-out for #{issue}; verification evidence is recorded in the verify attestation."


def describe(result: CloseOutResult) -> str:
    """A one-block human summary: what ran, and what is still wrong."""
    lines = [f"close-out #{result.issue}: {'OK' if result.ok else 'NOT-OK'}"]
    for step in result.steps:
        lines.append(f"  {step.outcome:<9} {step.action}: {step.detail}")
    for finding in result.remaining:
        lines.append(f"  REMAINS  {finding}")
        lines.append(f"           -> {finding.remediation}")
    return "\n".join(lines)
