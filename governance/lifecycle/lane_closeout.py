"""The lane's close-out — the ONE terminal verb for every artifact a lane bound (#1301).

``closeout.py`` drives an *issue* to hygiene: merge, verify, delete the branch,
consume the directive, release the claim, close the issue, reclaim the lane. That
is the item's close-out, and it needs the board. This module is the **lane's**:
given a lane record — whatever runtime opened it, alive or dead — it verifies the
evidence that the lane's work reached its terminal state and, only then, retires
the artifacts the lane bound at creation, in this order:

1. ``pr-merged`` — a pull request for the lane's branch is MERGED and its landing
   commit classifies clean under the shared trailer predicate
   (``governance/isolation/trailer.classify_commit == None``);
2. ``issue-closed`` — the lane's issue is CLOSED (composed ``Closes #n`` by the
   squash guard, #1266; the item close-out closes it otherwise);
3. ``branch-reaped`` — the lane branch is deleted locally and remotely, each tip
   recorded to ``.fleet/reaped-branches.jsonl`` FIRST (#1335's rule, consumed
   read-only) and only when its content is on the default branch
   (``worktree.content_landed``); a tip that is not content-landed blocks — it
   is never deleted;
4. ``worktree-removed`` — the lane worktree is removed when its only dirt is
   machine-managed (#1285/#834); the lane's own uncommitted work blocks;
5. ``lane-archived`` — the record, the session beat and the evidence bundle are
   written to ``.fleet/lifecycle/lanes/<lane_id>.json`` and the live record and
   beat are removed.

The steps are REPORTED in that order; mechanically the worktree is removed
before the local branch is deleted, because git refuses to delete a branch a
worktree still has checked out (measured in ``scripts/check-lifecycle-closeout.sh``'s
own scratch repository) — and the worktree's evidence (no lane-authored dirt)
is therefore read before either is touched.

Any step it cannot evidence **stays open and names itself**:
``closeout-blocked:<step>``. The steps after a blocked one are WITHHELD, not
attempted — a branch is not reaped for a lane whose pull request is not merged,
a worktree is not removed for a lane whose branch holds unlanded work. The lane
record stays, so the next close-out (from any runtime) finishes the job from the
same evidence. Nothing here reads a runtime's memory: lane records, git, the
board (through the port) and the mailbox are the only inputs, so a lane opened
by DeepSeek is closed by a Claude session and vice-versa, and a dead runtime's
lanes are still closable.

Why ``.fleet/lifecycle/lanes/`` and not ``.fleet/lifecycle/<lane>.json``:
``governance/reconcile/audit.py::landed_issues`` reads every ``*.json`` directly
under ``.fleet/lifecycle/`` as an ISSUE journal and raises ``AuditUnavailable``
on a non-numeric stem, which would turn the whole disk audit CANNOT-ASSESS the
moment a lane archived. A subdirectory keeps the two record kinds apart.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

#: Where an archived lane's evidence bundle lives, under the runtime state root.
ARCHIVE_DIR = ".fleet/lifecycle/lanes"

PERFORMED = "performed"
SKIPPED = "skipped"
BLOCKED = "blocked"
WITHHELD = "withheld"
PLANNED = "planned"

STEP_PR_MERGED = "pr-merged"
STEP_ISSUE_CLOSED = "issue-closed"
STEP_BRANCH_REAPED = "branch-reaped"
STEP_WORKTREE_REMOVED = "worktree-removed"
STEP_LANE_ARCHIVED = "lane-archived"
STEPS = (STEP_PR_MERGED, STEP_ISSUE_CLOSED, STEP_BRANCH_REAPED, STEP_WORKTREE_REMOVED, STEP_LANE_ARCHIVED)

BLOCKED_PREFIX = "closeout-blocked"


class LaneUnavailable(RuntimeError):
    """A source the close-out needs could not be read: CANNOT-ASSESS, never a verdict."""


@dataclass(frozen=True)
class PullRequest:
    number: int
    state: str  # "merged" / "open" / "closed"
    merge_commit: str = ""


@dataclass
class Step:
    name: str
    outcome: str
    detail: str = ""

    def to_json(self) -> dict:
        return {"step": self.name, "outcome": self.outcome, "detail": self.detail}


@dataclass
class LaneCloseout:
    lane_id: str
    issue: int
    branch: str
    worktree: str
    applied: bool
    steps: list[Step] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)
    evidence: dict = field(default_factory=dict)
    archived: str = ""

    @property
    def ok(self) -> bool:
        return not self.blocked

    def to_json(self) -> dict:
        return {
            "lane_id": self.lane_id,
            "issue": self.issue,
            "branch": self.branch,
            "worktree": self.worktree,
            "applied": self.applied,
            "ok": self.ok,
            "blocked": list(self.blocked),
            "steps": [step.to_json() for step in self.steps],
            "evidence": self.evidence,
            "archived": self.archived,
        }


class LaneOps(Protocol):
    """Everything the lane close-out reads or changes. Injected, so the order and
    the refusals are proven offline; ``RepoLaneOps`` in ``cli.py`` is the real one."""

    def pull_request_for(self, branch: str) -> PullRequest | None: ...

    def trailer_finding(self, sha: str) -> str | None:
        """The shared predicate's finding for ``sha``, or ``None`` when it is clean."""

    def issue_state(self, issue: int) -> str: ...

    def local_tip(self, branch: str) -> str: ...

    def remote_tip(self, branch: str) -> str: ...

    def content_landed(self, ref: str) -> bool: ...

    def record_reaped(self, *, branch: str, head_sha: str, worktree: str, reason: str) -> None: ...

    def delete_local_branch(self, branch: str) -> str: ...

    def delete_remote_branch(self, branch: str) -> str: ...

    def worktree_present(self, path: str) -> bool: ...

    def foreign_dirt(self, path: str) -> list[str]: ...

    def machine_dirt(self, path: str) -> list[str]: ...

    def remove_worktree(self, path: str) -> str: ...

    def gate_tails(self, issue: int, lane: str) -> dict:
        """The lane's result record's ``gate_tails`` (#1270), or ``{}``."""

    def archive(self, lane_id: str, bundle: dict) -> str: ...

    def forget_lane(self, lane_id: str) -> None: ...

    def clear_session(self, lane_id: str) -> None: ...


def _block(result: LaneCloseout, step: str, detail: str) -> None:
    result.steps.append(Step(step, BLOCKED, detail))
    result.blocked.append(f"{BLOCKED_PREFIX}:{step}")


def _withhold_rest(result: LaneCloseout, after: str) -> None:
    index = STEPS.index(after)
    for name in STEPS[index + 1:]:
        result.steps.append(Step(name, WITHHELD, f"withheld: {after} is blocked"))


def closeout_lane(record: dict, ops: LaneOps, *, apply: bool = False, now: str = "") -> LaneCloseout:
    """Drive one lane to its terminal state from evidence alone.

    ``record`` is the lane record as written by ``governance/isolation`` (the
    JSON document, so no runtime's object model is needed to read it).
    """
    lane_id = str(record.get("lane_id") or record.get("session_id") or "")
    issue = int(record.get("issue") or 0)
    branch = str(record.get("branch") or "")
    worktree = str(record.get("worktree") or "")
    result = LaneCloseout(lane_id=lane_id, issue=issue, branch=branch, worktree=worktree, applied=apply)
    if not lane_id or not issue or not branch:
        _block(result, STEP_PR_MERGED, "the lane record names no lane_id, issue or branch, so nothing can be evidenced")
        _withhold_rest(result, STEP_PR_MERGED)
        return result

    # 1. the pull request is merged, and its landing carries the ticket trailer.
    pr = ops.pull_request_for(branch)
    if pr is None:
        _block(result, STEP_PR_MERGED, f"no pull request exists for {branch}; open one, or `close --issue {issue}` if the work landed another way")
        _withhold_rest(result, STEP_PR_MERGED)
        return result
    if pr.state != "merged" or not pr.merge_commit:
        _block(result, STEP_PR_MERGED, f"PR #{pr.number} for {branch} is {pr.state}, not merged")
        _withhold_rest(result, STEP_PR_MERGED)
        return result
    finding = ops.trailer_finding(pr.merge_commit)
    if finding:
        _block(result, STEP_PR_MERGED, f"PR #{pr.number} landed as {pr.merge_commit[:12]} but the landing does not classify clean: {finding}")
        _withhold_rest(result, STEP_PR_MERGED)
        return result
    result.evidence.update({"pr": pr.number, "merge_commit": pr.merge_commit})
    result.steps.append(Step(STEP_PR_MERGED, SKIPPED, f"PR #{pr.number} merged as {pr.merge_commit[:12]}, trailer clean"))

    # 2. the issue is closed by that merge.
    state = str(ops.issue_state(issue) or "").lower()
    if state != "closed":
        _block(result, STEP_ISSUE_CLOSED, f"#{issue} is {state or 'unknown'}; the squash guard composes `Closes #{issue}` (#1266) — `close --issue {issue}` drives it")
        _withhold_rest(result, STEP_ISSUE_CLOSED)
        return result
    result.evidence["issue_state"] = "closed"
    result.steps.append(Step(STEP_ISSUE_CLOSED, SKIPPED, f"#{issue} is closed"))

    # 3. the branch, local and remote, each tip recorded before it goes.
    local_tip = ops.local_tip(branch)
    remote_tip = ops.remote_tip(branch)
    reaped: dict[str, str] = {}
    for where, tip in (("local", local_tip), ("remote", remote_tip)):
        if not tip:
            continue
        if not ops.content_landed(tip):
            _block(
                result,
                STEP_BRANCH_REAPED,
                f"{where} {branch} at {tip[:12]} is not content-landed on the default branch — kept, never deleted; "
                "push or merge it, or record the decision by hand",
            )
            _withhold_rest(result, STEP_BRANCH_REAPED)
            return result
        reaped[where] = tip
    result.evidence["reaped"] = reaped

    # 4. the worktree's evidence is read BEFORE the branch is acted on: git
    #    refuses to delete a branch a worktree still has checked out (measured
    #    in this gate's own scratch repository), so the worktree goes first
    #    mechanically while the steps are still REPORTED in the contract's
    #    order — and a worktree holding the lane's own work blocks both.
    present = bool(worktree) and ops.worktree_present(worktree)
    machine_note = ""
    if present:
        foreign = ops.foreign_dirt(worktree)
        if foreign:
            listed = ", ".join(foreign[:5]) + ("" if len(foreign) <= 5 else f" (+{len(foreign) - 5} more)")
            if reaped:
                result.steps.append(Step(STEP_BRANCH_REAPED, WITHHELD, f"withheld: {STEP_WORKTREE_REMOVED} is blocked and the branch is checked out there"))
            else:
                result.steps.append(Step(STEP_BRANCH_REAPED, SKIPPED, f"{branch} exists neither locally nor on origin"))
            _block(result, STEP_WORKTREE_REMOVED, f"{worktree} holds the lane's own uncommitted work ({listed}); commit or discard it first")
            _withhold_rest(result, STEP_WORKTREE_REMOVED)
            return result
        machine = ops.machine_dirt(worktree)
        machine_note = f" (ignored machine-managed state: {', '.join(machine)})" if machine else ""

    if not apply:
        if reaped:
            result.steps.append(Step(STEP_BRANCH_REAPED, PLANNED, "would reap " + ", ".join(f"{w} {t[:12]}" for w, t in reaped.items())))
        else:
            result.steps.append(Step(STEP_BRANCH_REAPED, SKIPPED, f"{branch} exists neither locally nor on origin"))
        if present:
            result.steps.append(Step(STEP_WORKTREE_REMOVED, PLANNED, f"would remove {worktree}{machine_note}"))
        else:
            result.steps.append(Step(STEP_WORKTREE_REMOVED, SKIPPED, "no worktree on disk"))
    else:
        worktree_detail = "no worktree on disk"
        if present:
            try:
                worktree_detail = ops.remove_worktree(worktree) + machine_note
            except Exception as exc:  # noqa: BLE001 - a failed removal is a blocked step, not a crash
                if reaped:
                    result.steps.append(Step(STEP_BRANCH_REAPED, WITHHELD, f"withheld: {STEP_WORKTREE_REMOVED} is blocked"))
                _block(result, STEP_WORKTREE_REMOVED, f"{type(exc).__name__}: {exc}"[:300])
                _withhold_rest(result, STEP_WORKTREE_REMOVED)
                return result
        branch_detail = f"{branch} exists neither locally nor on origin"
        if reaped:
            details = []
            try:
                for where, tip in reaped.items():
                    ops.record_reaped(branch=branch, head_sha=tip, worktree=worktree, reason=f"lane-closeout:{lane_id}:pr-{pr.number}")
                    details.append(ops.delete_local_branch(branch) if where == "local" else ops.delete_remote_branch(branch))
            except Exception as exc:  # noqa: BLE001 - the tip is recorded; the deletion is retried next pass
                _block(result, STEP_BRANCH_REAPED, f"{type(exc).__name__}: {exc}"[:300])
                result.steps.append(Step(STEP_WORKTREE_REMOVED, PERFORMED if present else SKIPPED, worktree_detail))
                result.steps.append(Step(STEP_LANE_ARCHIVED, WITHHELD, f"withheld: {STEP_BRANCH_REAPED} is blocked"))
                return result
            branch_detail = "; ".join(details)
        result.steps.append(Step(STEP_BRANCH_REAPED, PERFORMED if reaped else SKIPPED, branch_detail))
        result.steps.append(Step(STEP_WORKTREE_REMOVED, PERFORMED if present else SKIPPED, worktree_detail))
    result.evidence["worktree"] = worktree

    # 5. the archive: the record plus everything above, then the live record goes.
    bundle = {
        "lane": record,
        "lane_id": lane_id,
        "issue": issue,
        "closed_at": now or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "evidence": {**result.evidence, "gate_tails": ops.gate_tails(issue, str(record.get("lane") or ""))},
    }
    if not apply:
        result.steps.append(Step(STEP_LANE_ARCHIVED, PLANNED, f"would archive to {ARCHIVE_DIR}/{lane_id}.json and forget the record"))
        return result
    result.archived = ops.archive(lane_id, bundle)
    ops.forget_lane(lane_id)
    ops.clear_session(lane_id)
    result.steps.append(Step(STEP_LANE_ARCHIVED, PERFORMED, f"archived to {result.archived}; record and session cleared"))
    return result


def describe(result: LaneCloseout) -> str:
    verdict = "OK" if result.ok else "NOT-OK"
    mode = "apply" if result.applied else "dry-run"
    lines = [f"lane close-out {result.lane_id} (#{result.issue}, {result.branch}, {mode}): {verdict}"]
    for step in result.steps:
        lines.append(f"  {step.outcome:<10} {step.name}: {step.detail}")
    for name in result.blocked:
        lines.append(f"  BLOCKED    {name}")
    return "\n".join(lines)


def archive_path(root: Path | str, lane_id: str) -> Path:
    return Path(root) / ARCHIVE_DIR / f"{lane_id}.json"


def write_archive(root: Path | str, lane_id: str, bundle: dict) -> Path:
    path = archive_path(root, lane_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(bundle, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def archived_lanes(root: Path | str) -> set[str]:
    directory = Path(root) / ARCHIVE_DIR
    if not directory.exists():
        return set()
    return {path.stem for path in directory.glob("*.json")}
