"""Reconciliation sweep — turn an orphaned session into a clean workspace (#304).

Detection is the easy half (``heartbeat.judge``). This is the half that can
destroy work if it is wrong, so the rule is explicit and three-way. For an
orphaned lane, the question is not "is it dead?" but **"where does its work
live?"**:

===============  ==============================================  ===============
Work's location  Action                                          Outcome
===============  ==============================================  ===============
on ``master``    remove the worktree, delete the branch (local    ``reclaimed``
                 and remote), forget the lane, release the claim
only on a remote remove the worktree, **keep** the remote branch,  ``parked``
branch           forget the lane, release the claim
nowhere else     keep everything, mark the session shelved, keep   ``shelved``
                 the claim, report it
===============  ==============================================  ===============

The third row is the deliberate asymmetry worth stating plainly: **this worker
never trades unmerged work for an unlocked issue.** A lane whose commits exist
only inside its worktree keeps both its worktree and its claim, and is reported
every sweep until someone resolves it. Unlocking the issue there would invite a
second lane to start the same work while the first lane's only copy sits
unmerged — and the claim's own reaper already exists for the case where the issue
genuinely must move.

A ``parked`` or ``shelved`` session is re-evaluated on every sweep, not written
off: once its work lands on ``master`` (or is pushed), the next pass reclaims it.
That is what makes the worker self-healing rather than a one-shot cleanup.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from governance.lifecycle.report import (
    DEFAULT_LABELS,
    BoardReport,
    BoardReporter,
    finding_key,
)
from governance.reconcile.audit import (
    CLAIMS_DIR,
    LANDING_DIR,
    AuditUnavailable,
    WorktreeEntry,
    parse_worktrees,
)
from governance.reconcile.heartbeat import (
    DEFAULT_TTL_MINUTES,
    ORPHAN,
    SHELVED,
    SUSPECT,
    Session,
    Verdict,
    clear,
    judge,
    list_sessions,
    now_epoch,
    stamp,
)
from governance.reconcile import ledger as reconcile_ledger
from governance.reconcile import policy as reconcile_policy

PERFORMED = "performed"
SKIPPED = "skipped"
FAILED = "failed"

RECLAIMED = "reclaimed"
PARKED = "parked"
SHELVED_OUTCOME = "shelved"
REPORTED = "reported"
FAILED_OUTCOME = "failed"
#: A destructive decision the batch-limit control (`policy.py`,
#: `controls.yaml`: `sweep.max_actions_per_pass`) refused to act on this pass.
#: Re-evaluated next pass, never dropped.
REFUSED_OUTCOME = "refused"

#: The outcomes a batch-limit accounting counts as "acted on" — a REPORTED
#: session was never going to touch the disk, so it never consumes the budget.
_DESTRUCTIVE_OUTCOMES = frozenset({RECLAIMED, PARKED, SHELVED_OUTCOME, FAILED_OUTCOME})


@dataclass
class Step:
    """One attempted (or skipped) teardown action."""

    action: str
    outcome: str
    detail: str = ""


@dataclass
class Action:
    """What the sweep did (or deliberately did not do) for one session."""

    session_id: str
    issue: int
    agent: str
    status: str
    reason: str
    outcome: str
    steps: list[Step] = field(default_factory=list)

    @property
    def touched(self) -> bool:
        return self.outcome in {RECLAIMED, PARKED}

    def __str__(self) -> str:
        return f"{self.session_id} #{self.issue} {self.status} -> {self.outcome}: {self.reason}"


@dataclass
class SweepReport:
    """Every session's disposition for one pass."""

    actions: list[Action] = field(default_factory=list)
    board_reports: list[BoardReport] = field(default_factory=list)
    applied: bool = False
    at: float = 0.0

    def by_outcome(self, outcome: str) -> list[Action]:
        return [action for action in self.actions if action.outcome == outcome]

    @property
    def reclaimed(self) -> list[Action]:
        return self.by_outcome(RECLAIMED)

    @property
    def parked(self) -> list[Action]:
        return self.by_outcome(PARKED)

    @property
    def shelved(self) -> list[Action]:
        return self.by_outcome(SHELVED_OUTCOME)

    @property
    def reported(self) -> list[Action]:
        return self.by_outcome(REPORTED)

    @property
    def failed(self) -> list[Action]:
        return self.by_outcome(FAILED_OUTCOME)

    def to_json(self) -> dict:
        return {
            "at": self.at,
            "applied": self.applied,
            "counts": {outcome: len(self.by_outcome(outcome)) for outcome in
                       (RECLAIMED, PARKED, SHELVED_OUTCOME, REPORTED, FAILED_OUTCOME)},
            "board_reports": [
                {"key": r.key, "action": r.action, "number": r.number}
                for r in self.board_reports
            ],
            "actions": [
                {
                    "session_id": action.session_id,
                    "issue": action.issue,
                    "agent": action.agent,
                    "status": action.status,
                    "outcome": action.outcome,
                    "reason": action.reason,
                    "steps": [{"action": s.action, "outcome": s.outcome, "detail": s.detail} for s in action.steps],
                }
                for action in self.actions
            ],
        }


class ReconcileOps(Protocol):
    """The effects teardown needs. Injected, so the rules are testable offline."""

    def worktree_present(self, path: str) -> bool: ...

    def preserved_on_main(self, worktree: str) -> bool:
        """The lane's HEAD is an ancestor of origin/master — the work landed."""

    def preserved_remotely(self, worktree: str, branch: str) -> bool:
        """The lane's HEAD is contained in some remote branch — the work is safe."""

    def remove_worktree(self, path: str) -> str: ...

    def delete_branch(self, branch: str, *, remote: bool) -> str: ...

    def forget_lane(self, session_id: str) -> str: ...

    def release_claim(self, issue: int, agent: str) -> str: ...

    def mark_shelved(self, session: Session, reason: str) -> str: ...

    def clear_session(self, session_id: str) -> str: ...


def _attempt(action: Action, name: str, op, needed: bool = True) -> bool:
    """Run one step, recording the outcome instead of raising.

    A failing step must not abandon the rest: the point of a sweep is to leave the
    workspace as clean as it can and report what it could not finish.
    """
    if not needed:
        action.steps.append(Step(name, SKIPPED, "nothing to do"))
        return True
    try:
        detail = op()
    except Exception as exc:  # noqa: BLE001 - the outcome is data, not a crash
        action.steps.append(Step(name, FAILED, f"{type(exc).__name__}: {exc}"[:300]))
        return False
    action.steps.append(Step(name, PERFORMED, str(detail)[:300]))
    return True


def _teardown(session: Session, verdict: Verdict, ops: ReconcileOps, apply: bool) -> Action:
    """Drive one orphaned lane to the terminal state its work allows."""
    action = Action(
        session_id=session.session_id,
        issue=session.issue,
        agent=session.agent,
        status=verdict.status,
        reason=verdict.reason,
        outcome=REPORTED,
    )
    worktree = session.worktree or ""
    branch = session.branch or ""

    if not apply:
        action.steps.append(Step("plan", SKIPPED, "dry run: pass --apply to act"))
        # Even on a dry run the *decision* is computed, so the report says which
        # of the three cases this lane is in rather than only that it is old.
        if worktree and ops.worktree_present(worktree):
            if ops.preserved_on_main(worktree):
                action.outcome = RECLAIMED
            elif ops.preserved_remotely(worktree, branch):
                action.outcome = PARKED
            else:
                action.outcome = SHELVED_OUTCOME
        else:
            action.outcome = RECLAIMED
        return action

    if not worktree or not ops.worktree_present(worktree):
        # The worktree is already gone; finish the bookkeeping that the crash
        # skipped — a lane record or a claim with no lane is exactly the wedge
        # this worker exists to clear.
        _attempt(action, "forget-lane", lambda: ops.forget_lane(session.session_id), bool(session.session_id))
        _attempt(action, "release-claim", lambda: ops.release_claim(session.issue, session.agent), bool(session.issue))
        _attempt(action, "clear-heartbeat", lambda: ops.clear_session(session.session_id))
        action.outcome = FAILED_OUTCOME if action.steps and any(s.outcome == FAILED for s in action.steps) else RECLAIMED
        return action

    if not ops.preserved_on_main(worktree) and not ops.preserved_remotely(worktree, branch):
        # Unmerged work exists only here: keep the lane, keep the claim, escalate.
        _attempt(action, "mark-shelved", lambda: ops.mark_shelved(session, verdict.reason))
        action.outcome = SHELVED_OUTCOME
        return action

    landed = ops.preserved_on_main(worktree)
    _attempt(action, "remove-worktree", lambda: ops.remove_worktree(worktree))
    # A branch preserved only remotely must NOT be deleted remotely — that copy is
    # the work. It is parked: worktree reclaimed, branch kept on the server.
    _attempt(action, "delete-local-branch", lambda: ops.delete_branch(branch, remote=False), bool(branch))
    _attempt(action, "delete-remote-branch", lambda: ops.delete_branch(branch, remote=True), bool(branch) and landed)
    if not landed:
        action.steps.append(Step("keep-remote-branch", SKIPPED, f"origin/{branch} is the only copy of the work; parked"))
    _attempt(action, "forget-lane", lambda: ops.forget_lane(session.session_id))
    _attempt(action, "release-claim", lambda: ops.release_claim(session.issue, session.agent), bool(session.issue))
    _attempt(action, "clear-heartbeat", lambda: ops.clear_session(session.session_id))

    if any(step.outcome == FAILED for step in action.steps):
        action.outcome = FAILED_OUTCOME
    else:
        action.outcome = RECLAIMED if landed else PARKED
    return action


def sweep(
    root: Path | str,
    *,
    ttl_minutes: float = DEFAULT_TTL_MINUTES,
    apply: bool = False,
    at: float | None = None,
    alive: dict[str, bool] | None = None,
    ops: ReconcileOps | None = None,
    reporter: BoardReporter | None = None,
    controls: "reconcile_policy.Controls | None" = None,
) -> SweepReport:
    """One reconciliation pass over every session with a heartbeat.

    ``alive`` overrides pid liveness per session id (the seam that makes "kill the
    process and watch it get flagged" testable without killing anything).

    ``reporter`` is the board-reporting seam (issue #321): when it is given, a
    shelved, failed or suspect finding is filed on the board (idempotently, and
    only when ``apply`` is true), and a shelved lane whose work has since landed
    is resolved. The gate never passes one, so its offline proofs are untouched.

    ``controls`` is the batch-limit seam (issue #885, `policy.py`): defaults to
    the declared ``controls.yaml``. On an ``apply`` pass, once
    ``sweep.max_actions_per_pass`` destructive teardowns have been performed,
    every further orphaned session this pass is REFUSED rather than acted on —
    re-evaluated next pass, never dropped. Every decision (including a refusal)
    is written to the append-only ledger (`ledger.py`).
    """
    if ops is None:
        raise ValueError("sweep requires an operations port (see RepoOps)")
    resolved_controls = controls if controls is not None else reconcile_policy.load()
    moment = now_epoch() if at is None else at
    report = SweepReport(applied=apply, at=moment)

    destructive_count = 0
    for session in list_sessions(root):
        verdict = judge(
            session,
            ttl_minutes,
            at=moment,
            alive=(alive or {}).get(session.session_id) if alive else None,
        )
        # A shelved lane is re-evaluated every pass, not written off: once its
        # work lands or is pushed, the next sweep reclaims it.
        if verdict.reclaimable or session.state == SHELVED:
            if apply and destructive_count >= resolved_controls.max_actions_per_pass:
                action = Action(
                    session_id=session.session_id,
                    issue=session.issue,
                    agent=session.agent,
                    status=verdict.status,
                    reason=verdict.reason,
                    outcome=REFUSED_OUTCOME,
                    steps=[Step(
                        "refuse",
                        SKIPPED,
                        f"sweep.max_actions_per_pass ({resolved_controls.max_actions_per_pass}) "
                        "reached this pass; re-evaluated next pass",
                    )],
                )
            else:
                action = _teardown(session, verdict, ops, apply)
                if apply and action.outcome in _DESTRUCTIVE_OUTCOMES:
                    destructive_count += 1
        else:
            action = Action(
                session_id=session.session_id,
                issue=session.issue,
                agent=session.agent,
                status=verdict.status,
                reason=verdict.reason,
                outcome=REPORTED,
                steps=[Step("report", SKIPPED, "session is active; left alone")],
            )
        report.actions.append(action)
        try:
            reconcile_ledger.record_sweep_decision(
                root,
                session_id=action.session_id,
                issue=action.issue,
                agent=action.agent,
                outcome=action.outcome,
                code=resolved_controls.code_for(action.outcome),
                reason=action.reason,
                at=moment,
            )
        except reconcile_ledger.LedgerUnavailable:
            # The ledger records; it must never be the thing that stops a
            # reconciliation pass from protecting live work (fail-open only on
            # the *bookkeeping*, never on the destructive decision itself).
            # `ledger.verify()` (the gate's schema-invalid provocation) is how
            # this failure mode is made visible instead.
            pass
        if reporter is not None:
            report.board_reports.extend(board_report_action(reporter, session, action, apply))
    return report


def board_report_action(
    reporter: BoardReporter,
    session: Session,
    action: Action,
    apply: bool,
) -> list[BoardReport]:
    """Surface (or resolve) a reconciliation finding on the board (#321).

    A lane whose unmerged work exists nowhere else, a teardown that could not
    finish, and a session whose process is gone behind a fresh beat are each a
    finding the fleet must see. The opposite of a finding is a resolution: a
    previously shelved lane whose work has since landed is reclaimed (or parked),
    and its finding is then dropped so a genuinely new shelve files again.
    """
    shelved_key = finding_key("reconcile:shelved", f"#{session.issue}")
    if action.outcome == SHELVED_OUTCOME:
        return [
            reporter.report(
                shelved_key,
                title=f"[reconcile] shelved lane #{session.issue} — unmerged work at risk",
                body=_shelved_body(session, action),
                labels=DEFAULT_LABELS,
                apply=apply,
            )
        ]
    if action.outcome == FAILED_OUTCOME:
        return [
            reporter.report(
                finding_key("reconcile:failed", session.session_id),
                title=f"[reconcile] failed to reconcile #{session.issue}",
                body=_failed_body(session, action),
                labels=DEFAULT_LABELS,
                apply=apply,
            )
        ]
    if action.status == SUSPECT:
        return [
            reporter.report(
                finding_key("reconcile:suspect", session.session_id),
                title=f"[reconcile] suspect session #{session.issue}",
                body=_suspect_body(session, action),
                labels=DEFAULT_LABELS,
                apply=apply,
            )
        ]
    if session.state == SHELVED and action.outcome in (RECLAIMED, PARKED):
        reporter.resolve(
            shelved_key,
            comment=_resolved_body(session, action),
            apply=apply,
        )
    return []


def _shelved_body(session: Session, action: Action) -> str:
    return (
        "A reconciliation sweep shelved a lane whose unmerged work exists "
        "nowhere else.\n\n"
        f"- lane: `{session.lane or '(unknown)'}`\n"
        f"- branch: `{session.branch or '(unknown)'}`\n"
        f"- worktree: `{session.worktree or '(gone)'}`\n"
        f"- session: `{session.session_id}`\n"
        f"- issue: #{session.issue}\n"
        f"- agent: `{session.agent or '(unknown)'}`\n"
        f"- reason: {action.reason}\n\n"
        "The worker never trades unmerged work for an unlocked issue, so the "
        "lane, its branch and its claim are kept. The next sweep reclaims it "
        "automatically once the work lands on `master` (or is pushed); this "
        "finding resolves then.\n\n"
        "Reported by `governance/reconcile` (issue #321).\n"
    )


def _failed_body(session: Session, action: Action) -> str:
    failed = [f"  - {step.action}: {step.detail}" for step in action.steps if step.outcome == FAILED]
    return (
        "A reconciliation sweep could not finish teardown for a lane.\n\n"
        f"- session: `{session.session_id}`\n"
        f"- issue: #{session.issue}\n"
        f"- agent: `{session.agent or '(unknown)'}`\n"
        f"- lane: `{session.lane or '(unknown)'}`\n"
        f"- branch: `{session.branch or '(unknown)'}`\n"
        f"- reason: {action.reason}\n\n"
        "Failed step(s):\n"
        + ("\n".join(failed) or "  - (none recorded)")
        + "\n\nReported by `governance/reconcile` (issue #321).\n"
    )


def _suspect_body(session: Session, action: Action) -> str:
    return (
        "A session's heartbeat is fresh but its recorded process is gone.\n\n"
        f"- session: `{session.session_id}`\n"
        f"- issue: #{session.issue}\n"
        f"- agent: `{session.agent or '(unknown)'}`\n"
        f"- lane: `{session.lane or '(unknown)'}`\n"
        f"- pid: {session.pid}\n"
        f"- reason: {action.reason}\n\n"
        "Reported, not reclaimed: absence alone is weak evidence of death, and "
        "reclaiming a live lane on that evidence would destroy work. The sweep "
        "treats it as an orphan once its beat passes the TTL.\n\n"
        "Reported by `governance/reconcile` (issue #321).\n"
    )


def _resolved_body(session: Session, action: Action) -> str:
    return (
        "Resolved: the shelved lane's work has landed, so the sweep "
        f"{action.outcome} it.\n\n"
        f"- session: `{session.session_id}`\n"
        f"- issue: #{session.issue}\n"
        f"- branch: `{session.branch or '(unknown)'}`\n"
    )


class RepoOps:
    """The real effects, through git and the repo's own CLIs."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def _run(self, args: list[str], cwd: Path | None = None) -> str:
        result = subprocess.run(args, cwd=str(cwd or self.root), capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout).strip()[-200:] or "command failed")
        return (result.stdout or "").strip()

    def _git(self, *args: str) -> str:
        return self._run(["git", "-C", str(self.root), *args])

    def worktree_present(self, path: str) -> bool:
        return bool(path) and Path(path).exists()

    def _head(self, worktree: str) -> str:
        return self._run(["git", "-C", worktree, "rev-parse", "HEAD"])

    def preserved_on_main(self, worktree: str) -> bool:
        head = self._head(worktree)
        result = subprocess.run(
            ["git", "-C", str(self.root), "merge-base", "--is-ancestor", head, "origin/master"],
            capture_output=True, text=True,
        )
        return result.returncode == 0

    def preserved_remotely(self, worktree: str, branch: str) -> bool:
        head = self._head(worktree)
        contains = self._git("branch", "-r", "--contains", head)
        return bool(contains.strip())

    def remove_worktree(self, path: str) -> str:
        return self._git("worktree", "remove", "--force", path)

    def delete_branch(self, branch: str, *, remote: bool) -> str:
        if not branch:
            return "no branch"
        if remote:
            # Best effort: the branch may already be gone, which is the goal.
            subprocess.run(
                ["git", "-C", str(self.root), "push", "origin", "--delete", branch],
                capture_output=True, text=True,
            )
            return f"deleted origin/{branch} (if present)"
        return self._git("branch", "-D", branch)

    def forget_lane(self, session_id: str) -> str:
        # Import here: the lane record is the isolation module's, and this worker
        # must not reimplement what "forget a lane" means.
        import sys

        sys.path.insert(0, str(self.root / "governance" / "isolation"))
        import worktree as isolation_worktree  # noqa: PLC0415

        isolation_worktree.forget_record(session_id, self.root)
        return f"forgot lane record {session_id}"

    def release_claim(self, issue: int, agent: str) -> str:
        if not issue:
            return "no issue"
        return self._run(
            ["python3", str(self.root / "governance" / "dispatch" / "cli.py"), "reap",
             "--older-than-minutes", "0", "--issue", str(issue), "--reaper", "governance-reconcile"]
        )

    def mark_shelved(self, session: Session, reason: str) -> str:
        stamp(
            session.session_id,
            issue=session.issue,
            agent=session.agent,
            root=self.root,
            lane=session.lane,
            worktree=session.worktree,
            branch=session.branch,
            pid=session.pid,
            state=SHELVED,
            note=reason,
            at=session.at,  # keep the original beat time; the lane did not get newer
        )
        return f"shelved: {reason}"

    def clear_session(self, session_id: str) -> str:
        removed = clear(session_id, self.root)
        return "cleared heartbeat" if removed else "no heartbeat to clear"

    # -- the read-only half, for the worktree/branch audit (issue #628) --------
    #
    # These are the same injected port as the teardown effects above, so the
    # audit is driven by a test double in the suite and by real git in the gate —
    # the git calls are not buried inside the audit's own logic. Every one of
    # them raises ``AuditUnavailable`` rather than returning a plausible empty
    # answer, because "nothing is there" and "I could not look" must not be the
    # same verdict.

    def list_worktrees(self) -> list[WorktreeEntry]:
        try:
            porcelain = self._git("worktree", "list", "--porcelain")
        except RuntimeError as exc:
            raise AuditUnavailable(f"git worktree list failed: {exc}") from exc
        return parse_worktrees(porcelain)

    def list_local_branches(self) -> list[str]:
        try:
            names = self._git("for-each-ref", "--format=%(refname:short)", "refs/heads")
        except RuntimeError as exc:
            raise AuditUnavailable(f"git for-each-ref failed: {exc}") from exc
        return [name for name in names.splitlines() if name.strip()]

    def active_claims(self) -> dict[int, str]:
        """Live claims, keyed by issue — the dispatch ledger's own replay.

        The replay (claim / release / reap, and the TTL) is the dispatch
        package's, not reimplemented here: a second parser is a second answer.
        """
        import sys

        ledger = self.root / CLAIMS_DIR
        # The *code* comes from this checkout; only the *data* comes from the
        # audited root. Resolving the module under the root would make the audit
        # CANNOT-ASSESS on every scratch repository it is pointed at, which is
        # exactly where it is proven.
        dispatch_dir = str(Path(__file__).resolve().parents[1] / "dispatch")
        if dispatch_dir not in sys.path:
            sys.path.insert(0, dispatch_dir)
        try:
            # Qualified: the dispatch modules import their siblings by bare name,
            # so the directory must be on the path first, and this keeps a single
            # module identity rather than a second `claims` loaded by filename.
            from governance.dispatch import claims as dispatch_claims  # noqa: PLC0415
        except Exception as exc:  # noqa: BLE001 - unreadable, not empty
            raise AuditUnavailable(f"the claim ledger cannot be read: {exc}") from exc
        try:
            events = dispatch_claims.read_ledger(ledger)
            return {number: claim.agent for number, claim in dispatch_claims.active_claims(events).items()}
        except Exception as exc:  # noqa: BLE001 - a corrupt record is not "no claim"
            raise AuditUnavailable(f"the claim ledger is unreadable: {exc}") from exc

    def landed_issues(self) -> set[int]:
        """Issues whose work landed, from the lifecycle journals.

        The journal is written by the close-out once the item's change has been
        merged, so its presence *is* the landing record — the same fact the
        lifecycle audit reads rather than a snapshot that ages.
        """
        directory = self.root / LANDING_DIR
        if not directory.exists():
            return set()
        landed: set[int] = set()
        for path in sorted(directory.glob("*.json")):
            try:
                int(path.stem)
            except ValueError as exc:
                raise AuditUnavailable(f"{path.name} does not name an issue") from exc
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001 - a corrupt journal is not "not landed"
                raise AuditUnavailable(f"{path.name} is unreadable ({type(exc).__name__})") from exc
            landed.add(int(path.stem))
        return landed


def describe(report: SweepReport) -> str:
    """A one-block human summary of a pass."""
    mode = "apply" if report.applied else "dry-run"
    lines = [
        f"reconcile ({mode}): "
        + ", ".join(f"{outcome}={len(report.by_outcome(outcome))}"
                    for outcome in (RECLAIMED, PARKED, SHELVED_OUTCOME, REPORTED, FAILED_OUTCOME))
    ]
    for action in report.actions:
        lines.append(f"  {action.outcome:<9} {action}")
        for step in action.steps:
            if step.outcome != SKIPPED or step.action in {"keep-remote-branch", "plan"}:
                lines.append(f"      {step.outcome:<9} {step.action}: {step.detail}")
    return "\n".join(lines)


def load_report(path: Path | str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
