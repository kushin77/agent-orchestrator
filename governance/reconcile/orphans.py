"""The orphan walk — every artifact kind, classified by name, reclaimed only with
evidence, red above a declared budget (#1301 step 3).

``sweep`` reconciles *sessions* that beat; ``audit`` (#628) explains worktrees
and branches against beats, claims and landings. Neither answers the contract's
question — **which artifacts have no live lane and no evidenced close-out?** —
across the five kinds an agent creates. Measured 2026-09-18: 126 lane records
for closed issues, 155 worktrees, 663 local + 168 remote branches, 27 squash
merges that closed 0 issues, 8 remote branches with commits and no PR, 40 local
branches with unpushed work and no lane, subagent worktrees no lane record
knows. Each runtime cleaned up "its own"; nothing owned the whole.

This module walks all five kinds from FILES ONLY (lane records, git, the board
snapshot, ``gh`` through the port, the mailbox) and names every orphan:

==================== ==========================================================
finding              an artifact that
==================== ==========================================================
``orphan-worktree``  is a linked worktree no lane record names as its worktree
``orphan-branch``    is a local ``issue-*`` branch no lane record names, checked
                     out in no recorded worktree, and the head of no open PR;
                     its tip SHA is recorded in the finding, never deleted
``orphan-pr``        is an OPEN pull request whose head is no lane record's
                     branch and whose body carries no ``lane: <lane_id>`` line
                     naming one
``orphan-issue-lane`` is a lane record whose issue is CLOSED — the lane was
                     never closed out (``lifecycle close --lane <id>`` finishes it)
``orphan-directive`` is a ``.fleet/sent/`` order naming an issue that is CLOSED
                     (``lifecycle close --issue n`` consumes it, or ``retire``)
==================== ==========================================================

**Reclaim only with evidence.** Under ``apply`` the walk reclaims exactly two
shapes, and only through the rules other lanes already landed: an
``orphan-worktree`` whose HEAD is content-landed on the default branch
(``worktree.content_landed``, #1265/#1335) and holds no lane-authored dirt, and
an ``orphan-branch`` whose tip is content-landed. Each tip is recorded to
``.fleet/reaped-branches.jsonl`` (``worktree.record_reaped``) BEFORE removal.
Everything else is a finding with its remedy, never touched — a lane and a
directive reach terminal only through the lane close-out and the item close-out
respectively, because those verbs are where the evidence is.

**The budget.** ``orphan-budget.yaml`` declares, per kind, the count measured on
the day the walk shipped and the date that allowance expires (the same ratchet
shape ``governance/isolation/worktree-cap.yaml`` uses, #1335). Above the budget
the walk is NOT-OK, ``orphan-budget-exceeded:<kind>:<n>/<budget>``; after the
expiry the budget is 0 and every orphan is red. A budget is never re-measured
by the code — raising it is a reviewed edit.

**Unmeasured is not clean.** A kind whose source could not be read (``gh`` is
absent, the mailbox unreadable) is reported ``unmeasured`` by name and the walk
is CANNOT-ASSESS; it never counts as zero orphans.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Iterable, Protocol

ORPHAN_WORKTREE = "orphan-worktree"
ORPHAN_BRANCH = "orphan-branch"
ORPHAN_PR = "orphan-pr"
ORPHAN_ISSUE_LANE = "orphan-issue-lane"
ORPHAN_DIRECTIVE = "orphan-directive"
KINDS = (ORPHAN_WORKTREE, ORPHAN_BRANCH, ORPHAN_PR, ORPHAN_ISSUE_LANE, ORPHAN_DIRECTIVE)

BUDGET_EXCEEDED = "orphan-budget-exceeded"

#: Where the budget is declared, relative to the repository root.
BUDGET_PATH = "governance/reconcile/orphan-budget.yaml"

#: The line a PR body carries to bind itself to a lane (#1254 step 5).
LANE_LINE = re.compile(r"^\s*lane:\s*([0-9a-z][0-9a-z_-]{5,63})\s*$", re.IGNORECASE | re.MULTILINE)

ISSUE_BRANCH = re.compile(r"^issue-(\d+)")

RECLAIMED = "reclaimed"
WOULD_RECLAIM = "would-reclaim"
REPORTED = "reported"
FAILED = "failed"


@dataclass(frozen=True)
class Worktree:
    path: str
    branch: str = ""
    head: str = ""
    primary: bool = False


@dataclass(frozen=True)
class PullRequest:
    number: int
    branch: str
    body: str = ""


@dataclass(frozen=True)
class Directive:
    id: str
    issue: int | None


@dataclass
class Orphan:
    kind: str
    name: str
    detail: str
    remedy: str
    sha: str = ""
    reclaimable: bool = False
    outcome: str = REPORTED

    def __str__(self) -> str:
        return f"{self.kind}:{self.name}"

    def to_json(self) -> dict:
        return {
            "kind": self.kind,
            "name": self.name,
            "detail": self.detail,
            "remedy": self.remedy,
            "sha": self.sha,
            "reclaimable": self.reclaimable,
            "outcome": self.outcome,
        }


@dataclass
class OrphanReport:
    orphans: list[Orphan] = field(default_factory=list)
    unmeasured: dict[str, str] = field(default_factory=dict)
    budget: dict[str, int] = field(default_factory=dict)
    budget_expired: bool = False
    exceeded: list[str] = field(default_factory=list)
    applied: bool = False

    @property
    def assessable(self) -> bool:
        return not self.unmeasured

    def by_kind(self, kind: str) -> list[Orphan]:
        return [orphan for orphan in self.orphans if orphan.kind == kind]

    @property
    def counts(self) -> dict[str, int]:
        return {kind: len(self.by_kind(kind)) for kind in KINDS}

    @property
    def ok(self) -> bool:
        return self.assessable and not self.exceeded

    def to_json(self) -> dict:
        return {
            "applied": self.applied,
            "assessable": self.assessable,
            "ok": self.ok,
            "counts": self.counts,
            "budget": self.budget,
            "budget_expired": self.budget_expired,
            "exceeded": list(self.exceeded),
            "unmeasured": dict(self.unmeasured),
            "orphans": [orphan.to_json() for orphan in self.orphans],
        }


class Unmeasured(Exception):
    """A source could not be read; the kind that depends on it is unmeasured."""


class OrphanOps(Protocol):
    """The reads (and the two evidence-gated writes) the walk needs. Injected."""

    def lane_records(self) -> list[dict]: ...

    def worktrees(self) -> list[Worktree]: ...

    def local_branches(self) -> list[str]: ...

    def branch_tip(self, branch: str) -> str: ...

    def open_pull_requests(self) -> list[PullRequest]:
        """Raises :class:`Unmeasured` when the board cannot be read."""

    def issue_states(self, issues: Iterable[int]) -> dict[int, str]:
        """``{issue: "open"|"closed"}`` for every issue asked; raises :class:`Unmeasured`."""

    def sent_directives(self) -> list[Directive]: ...

    def content_landed(self, ref: str) -> bool: ...

    def foreign_dirt(self, path: str) -> list[str]: ...

    def record_reaped(self, *, branch: str, head_sha: str, worktree: str, reason: str) -> None: ...

    def remove_worktree(self, path: str) -> str: ...

    def delete_local_branch(self, branch: str) -> str: ...


def _issue_of(branch: str) -> int | None:
    match = ISSUE_BRANCH.match(branch or "")
    return int(match.group(1)) if match else None


def load_budget(path: Path | str, today: date | None = None) -> tuple[dict[str, int], bool]:
    """``(budget per kind, expired)`` from the declared document.

    A missing or unreadable document is a budget of 0 for every kind — the
    fail-closed direction: a lost declaration reds the walk rather than excusing
    everything.
    """
    zero = {kind: 0 for kind in KINDS}
    path = Path(path)
    if not path.exists():
        return zero, False
    try:
        import yaml  # noqa: PLC0415 - optional at import time

        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 - unreadable is zero, never "unlimited"
        return zero, False
    budgets = document.get("budget") if isinstance(document, dict) else None
    expires = str(document.get("expires") or "") if isinstance(document, dict) else ""
    moment = today or date.today()
    expired = False
    if expires:
        try:
            expired = moment > date.fromisoformat(expires)
        except ValueError:
            expired = True
    if expired or not isinstance(budgets, dict):
        return zero, expired
    result = dict(zero)
    for kind in KINDS:
        try:
            result[kind] = max(0, int(budgets.get(kind, 0) or 0))
        except (TypeError, ValueError):
            result[kind] = 0
    return result, expired


def walk(
    ops: OrphanOps,
    *,
    apply: bool = False,
    budget: dict[str, int] | None = None,
    budget_expired: bool = False,
) -> OrphanReport:
    """Classify every artifact of the five kinds; reclaim only with evidence."""
    report = OrphanReport(applied=apply, budget=dict(budget or {kind: 0 for kind in KINDS}), budget_expired=budget_expired)

    records = ops.lane_records()
    lane_branches = {str(record.get("branch") or "") for record in records if record.get("branch")}
    lane_worktrees = {str(record.get("worktree") or "") for record in records if record.get("worktree")}
    lane_ids = {str(record.get("lane_id") or record.get("session_id") or "") for record in records}
    lane_issues = {int(record.get("issue") or 0) for record in records if record.get("issue")}

    # -- worktrees ------------------------------------------------------------
    worktrees = ops.worktrees()
    worktree_branches = {entry.branch for entry in worktrees if entry.branch}
    for entry in worktrees:
        if entry.primary or entry.path in lane_worktrees:
            continue
        landed = bool(entry.head) and ops.content_landed(entry.head)
        dirt = ops.foreign_dirt(entry.path)
        reclaimable = landed and not dirt
        detail = (
            f"no lane record names {entry.path} (branch {entry.branch or 'detached'}, HEAD {entry.head[:12]}); "
            + ("its content is on the default branch and it holds no lane-authored dirt" if reclaimable
               else ("HEAD is NOT content-landed" if not landed else f"it holds uncommitted work: {', '.join(dirt[:5])}"))
        )
        report.orphans.append(Orphan(
            ORPHAN_WORKTREE, entry.path, detail,
            remedy="reclaimed by the walk under --apply (content-landed, tip recorded first)" if reclaimable
            else "open a lane for it (`isolation open`) or commit/push its work; never deleted unevidenced",
            sha=entry.head, reclaimable=reclaimable,
        ))

    # -- branches -------------------------------------------------------------
    try:
        pull_requests = ops.open_pull_requests()
    except Unmeasured as exc:
        pull_requests = None
        report.unmeasured[ORPHAN_PR] = str(exc)
    pr_branches = {pr.branch for pr in pull_requests} if pull_requests is not None else set()
    for branch in ops.local_branches():
        if not branch.startswith("issue-") or branch in lane_branches or branch in worktree_branches:
            continue
        if pull_requests is not None and branch in pr_branches:
            continue
        tip = ops.branch_tip(branch)
        landed = bool(tip) and ops.content_landed(tip)
        report.orphans.append(Orphan(
            ORPHAN_BRANCH, branch,
            f"local {branch} at {tip[:12] or '(tip unread)'} — no lane record, no recorded worktree"
            + (", no open PR" if pull_requests is not None else ", PRs unmeasured")
            + ("; content-landed" if landed else "; NOT content-landed (holds work)"),
            remedy="reclaimed by the walk under --apply (tip recorded first)" if landed
            else "push it and open a PR, or open a lane for it; the SHA is recorded here, the branch is never deleted",
            sha=tip, reclaimable=landed,
        ))

    # -- pull requests --------------------------------------------------------
    if pull_requests is not None:
        for pr in pull_requests:
            match = LANE_LINE.search(pr.body or "")
            bound = match.group(1) if match else ""
            if pr.branch in lane_branches or (bound and bound in lane_ids):
                continue
            report.orphans.append(Orphan(
                ORPHAN_PR, str(pr.number),
                f"PR #{pr.number} ({pr.branch}) is no lane record's branch and its body carries no `lane: <id>` "
                + (f"line naming a live lane (it names {bound}, unknown)" if bound else "line"),
                remedy="add `lane: <lane_id>` to the body, or open the lane that owns the branch",
            ))

    # -- lane records whose issue is closed -----------------------------------
    directives = ops.sent_directives()
    asked = set(lane_issues) | {d.issue for d in directives if d.issue}
    try:
        states = ops.issue_states(sorted(asked)) if asked else {}
    except Unmeasured as exc:
        states = None
        report.unmeasured[ORPHAN_ISSUE_LANE] = str(exc)
        report.unmeasured[ORPHAN_DIRECTIVE] = str(exc)
    if states is not None:
        for record in records:
            issue = int(record.get("issue") or 0)
            lane_id = str(record.get("lane_id") or record.get("session_id") or "")
            if states.get(issue) == "closed":
                report.orphans.append(Orphan(
                    ORPHAN_ISSUE_LANE, lane_id,
                    f"lane {lane_id} (#{issue}, {record.get('branch')}) is still open while #{issue} is closed",
                    remedy=f"`governance/lifecycle/cli.py close --lane {lane_id} --apply` finishes it from evidence",
                ))
        for directive in directives:
            if directive.issue and states.get(directive.issue) == "closed":
                report.orphans.append(Orphan(
                    ORPHAN_DIRECTIVE, directive.id,
                    f"directive {directive.id} orders #{directive.issue}, which is closed, and still sits in .fleet/sent",
                    remedy=f"`governance/lifecycle/cli.py close --issue {directive.issue}` consumes it, or `retire --directive {directive.id}`",
                ))

    # -- reclaim, only with evidence -----------------------------------------
    for orphan in report.orphans:
        if not orphan.reclaimable:
            continue
        if not apply:
            orphan.outcome = WOULD_RECLAIM
            continue
        try:
            if orphan.kind == ORPHAN_WORKTREE:
                entry = next(w for w in worktrees if w.path == orphan.name)
                ops.record_reaped(branch=entry.branch, head_sha=entry.head, worktree=entry.path, reason="orphan-walk:content-landed")
                ops.remove_worktree(entry.path)
            elif orphan.kind == ORPHAN_BRANCH:
                ops.record_reaped(branch=orphan.name, head_sha=orphan.sha, worktree="", reason="orphan-walk:content-landed")
                ops.delete_local_branch(orphan.name)
            orphan.outcome = RECLAIMED
        except Exception as exc:  # noqa: BLE001 - a failed reclaim is data
            orphan.outcome = FAILED
            orphan.detail += f"; reclaim failed: {type(exc).__name__}: {exc}"[:200]

    # -- the budget -------------------------------------------------------------
    for kind in KINDS:
        if kind in report.unmeasured:
            continue
        remaining = [o for o in report.by_kind(kind) if o.outcome != RECLAIMED]
        allowed = report.budget.get(kind, 0)
        if len(remaining) > allowed:
            report.exceeded.append(f"{BUDGET_EXCEEDED}:{kind}:{len(remaining)}/{allowed}")
    return report


def describe(report: OrphanReport) -> str:
    mode = "apply" if report.applied else "dry-run"
    lines = [
        f"orphan-walk ({mode}): " + ", ".join(f"{kind}={count}" for kind, count in report.counts.items())
        + (" [budget expired — every orphan is red]" if report.budget_expired else "")
    ]
    for orphan in report.orphans:
        lines.append(f"  {orphan.outcome:<14} {orphan}")
        lines.append(f"      {orphan.detail}")
        lines.append(f"      -> {orphan.remedy}")
    for kind, reason in report.unmeasured.items():
        lines.append(f"  unmeasured     {kind}: {reason}")
    for name in report.exceeded:
        lines.append(f"  NOT-OK         {name}")
    return "\n".join(lines)


def fleet_root(root: Path | str) -> Path:
    """The MAIN checkout that owns the runtime state ``root`` describes (#1436).

    ``.fleet/`` (lane records, sent directives, the mailbox) and ``.board/`` are
    gitignored runtime state that lives **beside the main checkout's git dir**,
    never inside a linked worktree — so a walk started from a lane described a
    fleet that was not there. Measured 2026-09-19: run from a lane worktree the
    walk read **0 lane records**, which zeroed the two kinds read from
    ``.fleet/`` and *inflated* the three read from git, because the lane
    exclusions had nothing left to exclude (worktree 33->58, branch 102->114,
    pr 5->13). The gate walks the main checkout, so the two disagreed and the
    issue's own ``Verify:`` command could not reproduce the gate.

    Read from git rather than guessed: ``--git-common-dir`` is the one value
    every worktree of one instance shares and which no clone has — the same
    resolution :func:`governance.isolation.worktree.record_reaped` already
    performs for the reap ledger, so the walk and the ledger it writes agree on
    which checkout owns the state. A root git cannot answer for is returned
    unchanged, so this only ever widens what the walk can see and never makes a
    walk that worked before stop working.
    """
    import subprocess  # noqa: PLC0415

    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--path-format=absolute", "--git-common-dir"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        # `--path-format` needs git >= 2.31; the plain form is relative to `root`.
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--git-common-dir"], capture_output=True, text=True
        )
        if result.returncode != 0 or not result.stdout.strip():
            return Path(root)
        candidate = Path(root) / result.stdout.strip()
    else:
        candidate = Path(result.stdout.strip())
    common = candidate.resolve()
    return common.parent if common.name == ".git" else Path(root)


class RepoOrphanOps:
    """The real reads, over git, ``gh`` and the runtime state under ``root``.

    ``root`` is the MAIN checkout (``.fleet/`` lives beside its git dir); the
    ``gh`` reads resolve the repository from that directory. The root is
    resolved through :func:`fleet_root` so a walk run *from a lane worktree*
    reads the fleet's state rather than the lane's empty one (#1436) — the
    port's own contract, defended here rather than at the caller, the way
    ``isolation.worktree.record_reaped`` already defends the reap ledger.
    """

    def __init__(self, root: Path | str, *, snapshot: Path | str | None = None) -> None:
        import subprocess  # noqa: PLC0415

        self.root = fleet_root(root)
        self.snapshot = Path(snapshot) if snapshot is not None else self.root / ".board" / "snapshot.json"
        self._subprocess = subprocess

    def _git(self, *args: str):
        return self._subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, text=True)

    def _gh_json(self, args: list[str]):
        import json  # noqa: PLC0415

        try:
            result = self._subprocess.run(["gh", *args], cwd=str(self.root), capture_output=True, text=True, timeout=120)
        except (OSError, self._subprocess.SubprocessError) as exc:
            raise Unmeasured(f"gh could not be run: {exc}") from exc
        if result.returncode != 0:
            raise Unmeasured(f"gh {' '.join(args[:2])} failed: {(result.stderr or result.stdout).strip()[-160:]}")
        try:
            return json.loads(result.stdout or "null")
        except json.JSONDecodeError as exc:
            raise Unmeasured("gh returned unreadable JSON") from exc

    def lane_records(self) -> list[dict]:
        import json  # noqa: PLC0415

        directory = self.root / ".fleet" / "lanes"
        records: list[dict] = []
        if not directory.exists():
            return records
        for path in sorted(directory.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(payload, dict):
                records.append(payload)
        return records

    def worktrees(self) -> list[Worktree]:
        from governance.reconcile.audit import parse_worktrees  # noqa: PLC0415

        result = self._git("worktree", "list", "--porcelain")
        if result.returncode != 0:
            raise Unmeasured(f"the worktree list could not be read: {result.stderr.strip()[-160:]}")
        return [Worktree(e.path, e.branch, e.head, e.primary) for e in parse_worktrees(result.stdout)]

    def local_branches(self) -> list[str]:
        result = self._git("for-each-ref", "--format=%(refname:short)", "refs/heads")
        return [line for line in result.stdout.splitlines() if line.strip()] if result.returncode == 0 else []

    def branch_tip(self, branch: str) -> str:
        result = self._git("rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
        return result.stdout.strip() if result.returncode == 0 else ""

    def open_pull_requests(self) -> list[PullRequest]:
        rows = self._gh_json(["pr", "list", "--state", "open", "--limit", "200", "--json", "number,headRefName,body"]) or []
        return [PullRequest(int(r["number"]), str(r.get("headRefName") or ""), str(r.get("body") or "")) for r in rows]

    def issue_states(self, issues: Iterable[int]) -> dict[int, str]:
        """The board snapshot first (a file, #1301 step 4), ``gh`` for what it lacks."""
        import json  # noqa: PLC0415

        wanted = sorted(set(int(n) for n in issues))
        states: dict[int, str] = {}
        try:
            snapshot = json.loads(self.snapshot.read_text(encoding="utf-8"))
            for row in snapshot.get("issues") or []:
                number = int(row.get("number") or 0)
                if number in wanted:
                    states[number] = str(row.get("state") or "").lower()
        except (OSError, ValueError, AttributeError):
            pass
        for number in wanted:
            if number in states:
                continue
            payload = self._gh_json(["issue", "view", str(number), "--json", "state"]) or {}
            states[number] = str(payload.get("state") or "").lower()
        return states

    def sent_directives(self) -> list[Directive]:
        import json  # noqa: PLC0415

        directory = self.root / ".fleet" / "sent"
        found: list[Directive] = []
        if not directory.exists():
            return found
        for path in sorted(directory.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                found.append(Directive(path.stem, None))
                continue
            if not isinstance(payload, dict):
                continue
            try:
                issue = int((payload.get("task") or {}).get("issue") or 0) or None
            except (TypeError, ValueError, AttributeError):
                issue = None
            found.append(Directive(str(payload.get("id") or path.stem), issue))
        return found

    def content_landed(self, ref: str) -> bool:
        from governance.isolation import worktree as isolation_worktree  # noqa: PLC0415

        return isolation_worktree.content_landed(self.root, ref, "origin/master")

    def foreign_dirt(self, path: str) -> list[str]:
        from governance.isolation import worktree as isolation_worktree  # noqa: PLC0415

        return isolation_worktree.foreign_uncommitted(path) if Path(path).exists() else []

    def record_reaped(self, *, branch: str, head_sha: str, worktree: str, reason: str) -> None:
        from governance.isolation import worktree as isolation_worktree  # noqa: PLC0415

        isolation_worktree.record_reaped(self.root, branch=branch, head_sha=head_sha, worktree=worktree, reason=reason)

    def remove_worktree(self, path: str) -> str:
        result = self._git("worktree", "remove", "--force", path)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip()[-160:])
        return f"removed {path}"

    def delete_local_branch(self, branch: str) -> str:
        result = self._git("branch", "-D", branch)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip()[-160:])
        return f"deleted {branch}"
