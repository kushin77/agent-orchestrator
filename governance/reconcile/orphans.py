"""The orphan walk — every artifact kind, classified by name, reclaimed only with

---knowledge---
module_id: governance.reconcile.orphans
system: governance
app: reconcile
solution_class: enterprise
patterns: [honesty-tri-state, fail-closed, deterministic, dry-run-default, injected-effects]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [venue_classify, Signal, Use, judge_use, Worktree, PullRequest, Directive, Orphan, OrphanReport, Unmeasured, (+6 more)]
invariants: ""
gotchas: ""
related: ["#628", "#1254", "#1265", "#1301", "#1335", "#1436"]
do_not_duplicate: null
---knowledge---

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
(``worktree.content_landed``, #1265/#1335), holds no lane-authored dirt, and is
**not in use**, and an ``orphan-branch`` whose tip is content-landed. Each tip is
recorded to ``.fleet/reaped-branches.jsonl`` (``worktree.record_reaped``) only
after the removal it describes actually happened. Everything else is a finding
with its remedy, never touched — a lane and a directive reach terminal only
through the lane close-out and the item close-out respectively, because those
verbs are where the evidence is.

**"In use" is a different question from "landed" (#1440).**
``content_landed`` and ``foreign_dirt`` between them answer *"would reclaiming
this lose work?"*. Neither answers *"is anything USING this?"*, and on
2026-09-18 the walk answered the second question anyway: ``sweep --orphans
--apply`` reclaimed 17 artifacts and had to be stopped from removing two more
that were demonstrably in use — a running *Claude agent session's* worktree, and
``ao-worktrees/ao-master-1789820219``, which another lane had **recorded** as its
venue (``/tmp/ao-orch/master-venue.txt``, read by
``V=$(cat …); cd $V; python3 $V/governance/...``). Two trees, two different kinds
of use, one missing signal — and nothing wrong with the *work*: the walk had no
signal about *use*.

So a reclaim also requires :func:`judge_use` to have MEASURED the artifact free.
Three signals are read, and they are deliberately unequal:

* **git's own worktree lock** — the strong one. Measured on this box with git
  2.53.0: ``git worktree list --porcelain`` reports ``locked <reason>``, and
  ``git worktree remove --force`` then fails ``rc 128``, ``fatal: cannot remove a
  locked working tree, lock reason: <reason>`` (only ``-f -f`` overrides it).
  That is the signal that saved the agent's tree, and the walk now reads it
  instead of discovering it as a failed removal.
* **a live process whose working directory is inside the path** — measured from
  ``/proc/<pid>/cwd``. This one is weaker, and the issue measured HOW: a holder
  that ``cd``s away between the measure and the apply reads "clear", and a
  directory that is already gone reads ``/path (deleted)`` and stops matching. It
  is also Linux-only, which is why it is declared rather than assumed — where
  ``/proc`` cannot be listed the signal is *unreadable*, never "clear".
* **a recorded venue** — an ownership record naming the path, which is what the
  deleted venue actually had. Read from the runtime spool (``$AO_VENUE_SPOOL``,
  default ``/tmp/ao-orch``) and from ``.fleet/venues/`` beside the fleet's own
  state, so "no lane record names it" stops being the same claim as "nobody needs
  it".

The verdict is a tri-state and it fails closed: ANY positive signal is IN USE and
the artifact is refused **by name**, naming which process or which record;
NOT IN USE requires every signal to have been read AND negative; a signal that
could not be read at all is CANNOT-ASSESS — which refuses the reclaim and names
the walk unmeasured, never "not in use". A refusal is always cheaper than a
reclaim nobody can undo.

**The budget.** ``orphan-budget.yaml`` declares, per kind, the count measured on
the day the walk shipped and the date that allowance expires (the same ratchet
shape ``governance/isolation/worktree-cap.yaml`` uses, #1335). Above the budget
the walk is NOT-OK, ``orphan-budget-exceeded:<kind>:<n>/<budget>``; after the
expiry the budget is 0 and every orphan is red. A budget is never re-measured
by the code — raising it is a reviewed edit.

**Unmeasured is not clean.** A kind whose source could not be read (``gh`` is
absent, the mailbox unreadable) is reported ``unmeasured`` by name and the walk
is CANNOT-ASSESS; it never counts as zero orphans. The liveness read is held to
the same rule (#1440): a worktree whose ``in use`` question could not be measured
is reported under ``orphan-worktree-liveness`` and the walk is CANNOT-ASSESS,
rather than that worktree being reclaimed on an unread signal.
"""

from __future__ import annotations

import os
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

#: Kinds whose budget-exceeded finding is advisory in the default "lane"
#: venue (#1620, #1655): each is counted against the WHOLE box's state
#: (every concurrent session's worktrees/branches/lane records/open PRs),
#: not this checkout's own diff, so it is non-deterministic under concurrent
#: load — measured swinging orphan-branch 107->97, orphan-issue-lane 44->45
#: and orphan-worktree 46->54 between two runs with no action taken in
#: between, and orphan-pr climbing 20->29->30 across ~20 minutes of ordinary
#: fleet churn (opening/closing PRs, including the merge trains themselves —
#: see #1655) with none of it stale or closeable. orphan-pr joined this set
#: 2026-09-20 by explicit owner decision: it is the same box-wide census as
#: its three siblings, not a per-artifact fact — a PR without a `lane:` line
#: is common for perfectly live work (#1655 is the fix for THAT gap).
#: orphan-directive is the one kind that stays blocking in every venue: one
#: directive names one closed issue, a fact this checkout can settle on its
#: own without racing any other session.
ADVISORY_IN_LANE_VENUE = (ORPHAN_WORKTREE, ORPHAN_BRANCH, ORPHAN_ISSUE_LANE, ORPHAN_PR)


def venue_classify(exceeded: Iterable[str], venue: str) -> tuple[list[str], list[str]]:
    """Split ``exceeded`` (``orphan-budget-exceeded:<kind>:<n>/<budget>`` strings)
    into ``(blocking, advisory)`` for the given ``AO_GATE_VENUE`` (#1620).

    ``venue == "attestation"`` blocks on everything (the serial, post-merge
    run this repo's box-wide hygiene is actually enforced by); any other
    venue (the default, "lane") downgrades :data:`ADVISORY_IN_LANE_VENUE`
    kinds to advisory, since a single PR's `make verify` measures the whole
    box, not its own diff.
    """
    blocking: list[str] = []
    advisory: list[str] = []
    for name in exceeded:
        kind = name.split(":")[1] if name.count(":") >= 1 else ""
        if venue != "attestation" and kind in ADVISORY_IN_LANE_VENUE:
            advisory.append(name)
        else:
            blocking.append(name)
    return blocking, advisory

#: The line a PR body carries to bind itself to a lane (#1254 step 5).
LANE_LINE = re.compile(r"^\s*lane:\s*([0-9a-z][0-9a-z_-]{5,63})\s*$", re.IGNORECASE | re.MULTILINE)

ISSUE_BRANCH = re.compile(r"^issue-(\d+)")

RECLAIMED = "reclaimed"
WOULD_RECLAIM = "would-reclaim"
REPORTED = "reported"
FAILED = "failed"

#: The three liveness verdicts :func:`judge_use` returns. ``LIVENESS_CANNOT_ASSESS``
#: is a REFUSAL and a report, never a "clear" (#1440).
IN_USE = "in-use"
NOT_IN_USE = "not-in-use"
LIVENESS_CANNOT_ASSESS = "cannot-assess"

#: The key the walk reports under ``OrphanReport.unmeasured`` when it could not
#: measure whether a worktree is in use. Deliberately NOT one of ``KINDS``: the
#: worktree kind is still counted and still held to its budget, so this only
#: makes the walk CANNOT-ASSESS rather than quietly skipping the kind.
LIVENESS_UNMEASURED = "orphan-worktree-liveness"

#: Where a runtime venue record lives: a file naming the worktree an operator or
#: a lane is working in. Measured shape (#1440): ``/tmp/ao-orch/master-venue.txt``
#: holding one absolute path. The env seam exists so a gate can point the reader
#: at a fixture instead of the box's real spool.
VENUE_SPOOL_ENV = "AO_VENUE_SPOOL"
VENUE_SPOOL_DEFAULT = "/tmp/ao-orch"

#: The declared venue store, beside the fleet's own runtime state.
VENUE_DIR = ".fleet/venues"


def _path_forms(value: str) -> set[str]:
    """Every spelling of one path this module will compare: the normalised form
    and the symlink-resolved form, so a venue recorded through a symlinked parent
    still names the artifact it names."""
    text = (value or "").strip().rstrip("/")
    if not text:
        return set()
    forms = {os.path.normpath(text)}
    try:
        forms.add(os.path.normpath(os.path.realpath(text)))
    except OSError:  # a path the filesystem cannot even resolve
        pass
    return forms


def _same_path(declared: str, path: str) -> bool:
    return bool(_path_forms(declared) & _path_forms(path))


def _declared_paths(text: str, *, expect_json: bool = False) -> list[str]:
    """Every absolute path a venue record declares.

    The measured shape (#1440) is a one-line text file holding the path
    (``/tmp/ao-orch/master-venue.txt``); a JSON record carries the same path in a
    value, at any depth. Raises ``ValueError`` when a record that should be JSON
    cannot be parsed — the caller must report that as an UNREADABLE signal, never
    as "this record names nothing".
    """
    stripped = text.strip()
    if expect_json:
        import json  # noqa: PLC0415 - same lazy import the rest of this module uses

        payload = json.loads(stripped)  # ValueError on a malformed record
        found: list[str] = []
        stack = [payload]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                stack.extend(item.values())
            elif isinstance(item, list):
                stack.extend(item)
            elif isinstance(item, str) and item.startswith("/"):
                found.append(item.strip())
        return found
    return [line.strip() for line in stripped.splitlines() if line.strip().startswith("/")]


def _process_name(pid: str) -> str:
    """A short, human-actionable identity for ``pid``, from ``/proc``."""
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return "command line unreadable"
    parts = [part for part in raw.decode("utf-8", "replace").split("\0") if part]
    if not parts:
        return "no command line"
    head = Path(parts[0]).name or parts[0]
    return f"{head} {' '.join(parts[1:3])}".strip()[:80]


def _holders(path: str, proc: str = "/proc") -> tuple[list[str], int, int, bool]:
    """``(holders, pids_seen, cwds_unreadable, proc_readable)``.

    A holder is a live process whose working directory IS the artifact or sits
    inside it. Read from ``/proc/<pid>/cwd`` — Linux-only, and the WEAKER of the
    three signals (#1440): a holder that ``cd``s away between this read and the
    removal reads clear, and a directory that is already gone reads
    ``/path (deleted)`` and stops matching. It is read anyway, because it is the
    only signal that catches a *plain terminal* — the second artifact the walk had
    to be stopped from removing had no git lock to catch it.

    ``proc_readable`` is False only when ``/proc`` itself cannot be listed, which
    is an UNREADABLE signal and must never be read as "clear". A single
    ``/proc/<pid>/cwd`` that cannot be read is counted instead of treated as
    blind: the process may simply have exited, and this box has other users'
    processes, so raising the whole signal for each one would leave the walk
    permanently CANNOT-ASSESS and therefore permanently unable to reclaim
    anything — the opposite of a useful control.
    """
    try:
        entries = list(Path(proc).iterdir())
    except OSError:
        return [], 0, 0, False
    pids = sorted((entry.name for entry in entries if entry.name.isdigit()), key=int)
    wanted = _path_forms(path)
    holders: list[str] = []
    unreadable = 0
    for pid in pids:
        try:
            cwd = os.readlink(f"{proc}/{pid}/cwd")
        except OSError:
            unreadable += 1
            continue
        inside = False
        for form in _path_forms(cwd):
            if any(form == want or form.startswith(want + "/") for want in wanted):
                inside = True
                break
        if inside:
            holders.append(f"{pid} ({_process_name(pid)})")
    return holders, len(pids), unreadable, True


def _lock_reasons(porcelain: str) -> dict[str, str]:
    """``path -> reason`` for every entry git reports as ``locked``.

    Measured with git 2.53.0: the porcelain block carries ``locked`` alone when
    no reason was given, and ``locked <reason>`` when one was. Only entries git
    itself calls locked appear here, so an absent path means "git reports no
    lock" — the strong, portable half of the liveness read.
    """
    reasons: dict[str, str] = {}
    for block in porcelain.strip().split("\n\n"):
        path = ""
        reason: str | None = None
        for line in block.splitlines():
            key, _, value = line.partition(" ")
            if key == "worktree":
                path = value.strip()
            elif key == "locked":
                reason = value.strip()
        if path and reason is not None:
            reasons[path] = reason
    return reasons


@dataclass(frozen=True)
class Signal:
    """One measurement of "is anything using this artifact?".

    ``read`` is False only when the signal could not be measured at all — never
    when it was measured and found clear. ``hit`` is non-empty when the signal is
    POSITIVE and says WHAT it names (the process, the lock reason, the record).
    ``note`` carries coverage, and the reason when the signal could not be read.
    """

    name: str
    read: bool
    hit: str = ""
    note: str = ""


@dataclass(frozen=True)
class Use:
    verdict: str
    evidence: str = ""

    @property
    def in_use(self) -> bool:
        return self.verdict == IN_USE

    @property
    def known(self) -> bool:
        return self.verdict != LIVENESS_CANNOT_ASSESS

    def __str__(self) -> str:
        return f"{self.verdict}: {self.evidence}" if self.evidence else self.verdict


def judge_use(signals: Iterable[Signal]) -> Use:
    """``IN USE`` if any signal is positive; ``NOT IN USE`` only if every signal
    was read and every one was negative; ``CANNOT-ASSESS`` otherwise.

    The order is the whole point (#1440): a positive signal is a *measurement*
    of use, so it wins over a signal that could not be read at all; and an
    unreadable signal can never be reported as "nothing is using it". An empty
    signal list is CANNOT-ASSESS too — no signal measured is not an absence of
    holders, it is an absence of measurement.
    """
    measured = list(signals)
    positive = [signal for signal in measured if signal.hit]
    if positive:
        return Use(IN_USE, "; ".join(f"{s.name}: {s.hit}" for s in positive))
    blind = [signal for signal in measured if not signal.read]
    if blind:
        return Use(
            LIVENESS_CANNOT_ASSESS,
            "; ".join(f"{s.name}: {s.note or 'could not be measured'}" for s in blind),
        )
    if not measured:
        return Use(LIVENESS_CANNOT_ASSESS, "no liveness signal was available")
    return Use(NOT_IN_USE, "; ".join(signal.note for signal in measured if signal.note))


@dataclass(frozen=True)
class Worktree:
    path: str
    branch: str = ""
    head: str = ""
    primary: bool = False
    locked: bool = False
    lock_reason: str = ""


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
    #: The liveness verdict's own words (``Use.__str__``) for an artifact kind
    #: that has one — machine-readable naming of WHAT is in use, not only prose
    #: in ``detail`` (#1440). Empty for the kinds liveness does not apply to.
    use: str = ""

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
            "use": self.use,
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

    def in_use(self, entry: Worktree) -> Use:
        """Is anything USING ``entry``? Never raises: a signal that cannot be
        read is part of the :class:`Use` verdict, not an exception."""

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
    unmeasured_liveness: list[str] = []
    for entry in worktrees:
        if entry.primary or entry.path in lane_worktrees:
            continue
        landed = bool(entry.head) and ops.content_landed(entry.head)
        dirt = ops.foreign_dirt(entry.path)
        use = ops.in_use(entry)
        reclaimable = landed and not dirt and use.verdict == NOT_IN_USE
        refused: list[str] = []
        if not landed:
            refused.append("HEAD is NOT content-landed")
        if dirt:
            refused.append(f"it holds uncommitted work: {', '.join(dirt[:5])}")
        if use.in_use:
            refused.append(f"it is IN USE — {use.evidence}")
        elif not use.known:
            refused.append(f"its liveness could NOT be measured — {use.evidence}")
            unmeasured_liveness.append(f"{entry.path}: {use.evidence}")
        if reclaimable:
            detail = (
                f"no lane record names {entry.path} (branch {entry.branch or 'detached'}, HEAD {entry.head[:12]}); "
                f"its content is on the default branch, it holds no lane-authored dirt, and nothing is using it"
                f" ({use.evidence})"
            )
            remedy = "reclaimed by the walk under --apply (content-landed and not in use, tip recorded after removal)"
        elif use.in_use:
            detail = (
                f"no lane record names {entry.path} (branch {entry.branch or 'detached'}, HEAD {entry.head[:12]}); "
                + "; ".join(refused)
            )
            remedy = (
                "left alone: something is using it — name the claim the walk can read "
                "(`isolation open`, or a `.fleet/venues/` record) and stop the holder, then re-walk"
            )
        elif not use.known:
            detail = (
                f"no lane record names {entry.path} (branch {entry.branch or 'detached'}, HEAD {entry.head[:12]}); "
                + "; ".join(refused)
            )
            remedy = (
                "left alone: its liveness could not be measured, and an unmeasured artifact is never "
                "reclaimed — re-run where /proc and the venue spool are readable"
            )
        else:
            detail = (
                f"no lane record names {entry.path} (branch {entry.branch or 'detached'}, HEAD {entry.head[:12]}); "
                + "; ".join(refused)
            )
            remedy = "open a lane for it (`isolation open`) or commit/push its work; never deleted unevidenced"
        report.orphans.append(Orphan(
            ORPHAN_WORKTREE, entry.path, detail, remedy=remedy,
            sha=entry.head, reclaimable=reclaimable, use=str(use),
        ))
    if unmeasured_liveness:
        report.unmeasured[LIVENESS_UNMEASURED] = "; ".join(unmeasured_liveness)

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
                ops.remove_worktree(entry.path)
                # Recorded only AFTER a removal that happened (#1440). Recorded
                # first, a removal that then FAILS leaves a ledger entry claiming
                # a reap that never took place — true about the content, false
                # about the removal. Measured: `git worktree remove --force`
                # refuses a locked tree (rc 128), and a git lock is one of the
                # ways an artifact turns out to be in use.
                ops.record_reaped(branch=entry.branch, head_sha=entry.head, worktree=entry.path, reason="orphan-walk:content-landed")
            elif orphan.kind == ORPHAN_BRANCH:
                ops.delete_local_branch(orphan.name)
                ops.record_reaped(branch=orphan.name, head_sha=orphan.sha, worktree="", reason="orphan-walk:content-landed")
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

    def __init__(
        self,
        root: Path | str,
        *,
        snapshot: Path | str | None = None,
        venue_roots: Iterable[Path | str] | None = None,
    ) -> None:
        import subprocess  # noqa: PLC0415

        self.root = fleet_root(root)
        self.snapshot = Path(snapshot) if snapshot is not None else self.root / ".board" / "snapshot.json"
        self._subprocess = subprocess
        #: Where declared venues are read from (#1440). The env seam exists so a
        #: gate can point the reader at its own fixture instead of the box's real
        #: spool; ``venue_roots=[]`` means "measure no venue store at all".
        self.venue_roots = (
            [Path(entry) for entry in venue_roots]
            if venue_roots is not None
            else [self.root / VENUE_DIR, Path(os.environ.get(VENUE_SPOOL_ENV) or VENUE_SPOOL_DEFAULT)]
        )

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
        # One read, two consumers: the shared parser answers "what worktrees are
        # there and which are locked", and _lock_reasons recovers git's own lock
        # REASON from the same porcelain — the strong liveness signal (#1440),
        # which the shared parser deliberately does not carry.
        reasons = _lock_reasons(result.stdout)
        return [
            Worktree(
                entry.path, entry.branch, entry.head, entry.primary,
                locked=bool(entry.locked), lock_reason=reasons.get(entry.path, ""),
            )
            for entry in parse_worktrees(result.stdout)
        ]

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

    # -- is anything USING it? (#1440) ----------------------------------------
    def in_use(self, entry: Worktree) -> Use:
        """Compose the three liveness signals into one fail-closed verdict."""
        signals: list[Signal] = []

        # 1. git's OWN lock — the strong, portable signal. Measured with git
        # 2.53.0: porcelain carries `locked <reason>`, and `git worktree remove
        # --force` then refuses with rc 128, so the walk reading it here is the
        # difference between a named refusal and a failed removal.
        if entry.locked:
            signals.append(Signal(
                "git-worktree-lock", True,
                hit=f"git holds this worktree's lock (reason: {entry.lock_reason or 'none recorded'})",
            ))
        else:
            signals.append(Signal("git-worktree-lock", True, note="git reports no worktree lock"))

        # 2. a live process whose working directory is inside it — weaker, and
        # Linux-only, so an unlistable /proc is UNREADABLE rather than "clear".
        holders, seen, unreadable, proc_readable = _holders(entry.path)
        if not proc_readable:
            signals.append(Signal(
                "holder-process", False,
                note="no readable /proc on this platform, so no process could be checked for a working directory inside it",
            ))
        elif holders:
            signals.append(Signal(
                "holder-process", True,
                hit="live process with a working directory inside it: " + ", ".join(holders[:5]),
            ))
        else:
            signals.append(Signal(
                "holder-process", True,
                note=f"{seen} process(es) checked, none with a working directory inside it ({unreadable} cwd unreadable)",
            ))

        # 3. a recorded venue — the OWNERSHIP signal, which is the one the venue
        # deleted on 2026-09-18 actually had.
        naming, blind = self._venue_declarations(entry.path)
        if naming:
            signals.append(Signal("venue-record", True, hit="; ".join(naming[:5])))
        elif blind:
            signals.append(Signal("venue-record", False, note="; ".join(blind[:5])))
        else:
            signals.append(Signal(
                "venue-record", True,
                note=f"no venue record names it ({len(self.venue_roots)} store(s) read)",
            ))
        return judge_use(signals)

    def _venue_declarations(self, path: str) -> tuple[list[str], list[str]]:
        """``(records naming this path, stores that could not be read)``.

        A store that does not exist is a measured ZERO — the same direction
        ``lane_records`` takes for a missing ``.fleet/lanes`` — because the spool
        is a runtime convention, not a requirement. A store that exists and cannot
        be listed, or a record that cannot be parsed, IS unreadable and widens
        nothing: it must never read as "this path is unclaimed".
        """
        naming: list[str] = []
        blind: list[str] = []
        for root in self.venue_roots:
            try:
                entries = sorted(Path(root).iterdir())
            except FileNotFoundError:
                continue
            except OSError as exc:
                blind.append(f"venue store {root} could not be listed: {exc}")
                continue
            for record in entries:
                if record.suffix not in (".txt", ".json"):
                    continue
                try:
                    if not record.is_file():
                        continue
                    text = record.read_text(encoding="utf-8", errors="replace")
                    declared = _declared_paths(text, expect_json=record.suffix == ".json")
                except OSError as exc:
                    blind.append(f"venue record {record} could not be read: {exc}")
                    continue
                except ValueError as exc:
                    blind.append(f"venue record {record} could not be parsed: {exc}")
                    continue
                if any(_same_path(candidate, path) for candidate in declared):
                    naming.append(f"venue record {record} names it")
        return naming, blind

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
