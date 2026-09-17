"""Point the disk audit at the REAL tree, with a reviewed, provenanced baseline
(#740, item 1 of the "Bounded proposal").

Every invocation of ``audit()`` in ``scripts/check-reconcile.sh`` runs against a
synthetic scratch repository. The gate proves the mechanism, but never once
points it at the repository it actually governs — so the real tree's 130+
unmatched worktrees and 300+ unmatched branches (``reconcile-audit: NOT-OK —
432 artifact(s)`` when this was measured) never fail anything.

This module closes that gap the same way this repo already handles a gate that
must start failing on pre-existing reality
(``governance/isolation/landed-baseline.json``, ``scripts/gate-coverage-baseline.txt``):
an explicit, reviewed, provenanced baseline file naming every accepted
exception, checked in **both directions**:

* an artifact the real-tree audit reports UNMATCHED that is **not** in the
  baseline is a NEW violation — the gate fails, named;
* a baseline entry whose artifact the audit no longer reports UNMATCHED (it was
  matched, or it is simply gone from disk) is STALE — the gate fails, named,
  because the baseline is not a permanent allow-list; it must shrink as
  artifacts get explained or cleaned up, by an explicit reviewed edit, never
  silently.

This is deliberately **not** a blanket allow: a baseline that only grows, or
that accepts "anything currently unmatched" without listing each one by name
and reason, would be exactly the fail-open condition issue #628 already fixed
once for the "no session beat at all" case. Growing the baseline (accepting a
new pre-existing violation) is itself a reviewed, explicit edit — it happens
in this file, in a commit a human reads, never automatically.

The audit invoked here is READ-ONLY, exactly like every other ``audit()`` call
in this package: it names artifacts, it never removes anything.

## Age grace (#740 follow-up)

A snapshot audit with no age concept goes red the instant ANY agent creates a
worktree or pushes a branch — i.e. always, on a repository whose fleet is
actively working. Measured: `#1050` merged the baseline-only version and the
very next gate run on `master` was red on three brand-new artifacts (a running
subagent's worktree, the detached scratch worktree that was running the gate
itself, and a `/tmp` scratch worktree) — none of them orphans, all of them just
young.

An unmatched artifact younger than ``lease.REAL_TREE_GRACE_HOURS`` is reported
as ``young`` (counted, described, exit code unaffected) rather than failing.
Only an artifact **older** than the grace window is a real candidate for
"nothing explains this" — and even then it fails only when it is not already
baselined. Age is measured off the artifact itself, never the audit run:

* a **worktree**'s age is its directory's mtime;
* a **branch**'s age is its tip commit's committer date (``%ct``).

An artifact whose age cannot be measured (a stat failure, an unreadable git
log) is treated as **old** — fail-closed, the same direction every other
CANNOT-ASSESS-adjacent decision in this package takes: an unmeasurable age
must never be read as "young enough to ignore".
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from governance.policy import lease
from governance.reconcile.audit import AuditReport, audit as run_audit

#: kind/name pairs the baseline can carry entries for.
_KEY = tuple[str, str]

#: Read from the single declared policy (governance/policy/lease.py), never
#: restated as a bare literal here (the policy scan would refuse that).
DEFAULT_GRACE_HOURS = lease.REAL_TREE_GRACE_HOURS

YOUNG = "young"


class BaselineUnavailable(Exception):
    """The baseline file itself could not be read or is malformed."""


@dataclass(frozen=True)
class BaselineEntry:
    kind: str
    name: str
    reason: str

    @property
    def key(self) -> _KEY:
        return (self.kind, self.name)


@dataclass
class RealTreeVerdict:
    """The result of checking the real tree's audit against its baseline."""

    assessable: bool
    new_violations: tuple[BaselineEntry, ...] = field(default_factory=tuple)
    stale_entries: tuple[BaselineEntry, ...] = field(default_factory=tuple)
    young: tuple[BaselineEntry, ...] = field(default_factory=tuple)
    baseline_count: int = 0
    unmatched_count: int = 0
    grace_hours: float = 0.0
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.assessable and not self.new_violations and not self.stale_entries

    def describe(self) -> str:
        if not self.assessable:
            return f"real-tree-baseline: CANNOT-ASSESS — {self.reason}"
        lines = [
            f"real-tree-baseline: {self.unmatched_count} unmatched artifact(s) on disk, "
            f"{self.baseline_count} baselined, {len(self.young)} young (< {self.grace_hours:g}h, not failed)"
        ]
        for entry in self.young:
            lines.append(f"  YOUNG    {entry.kind} {entry.name} — {entry.reason}")
        for entry in self.new_violations:
            lines.append(f"  NEW      {entry.kind} {entry.name} — not in the baseline (add it, reviewed, or fix it)")
        for entry in self.stale_entries:
            lines.append(f"  STALE    {entry.kind} {entry.name} — baselined but no longer unmatched (remove it)")
        if self.ok:
            lines.append("real-tree-baseline: OK — every unmatched artifact is baselined, young, or explained; no stale entries")
        return "\n".join(lines)


def _worktree_age_seconds(path: str, *, at: float) -> float | None:
    try:
        return at - os.stat(path).st_mtime
    except OSError:
        return None


def _branch_age_seconds(root: Path | str, branch: str, *, at: float) -> float | None:
    # `branch` names a ref, not a path — no `--` pathspec separator here, or
    # git treats it as a path filter against HEAD and silently returns nothing.
    result = subprocess.run(
        ["git", "-C", str(root), "log", "-1", "--format=%ct", branch],
        capture_output=True,
        text=True,
    )
    text = result.stdout.strip()
    if result.returncode != 0 or not text:
        return None
    try:
        return at - float(text)
    except ValueError:
        return None


def artifact_age_seconds(root: Path | str, kind: str, name: str, *, at: float) -> float | None:
    """The age of one artifact, or ``None`` when it cannot be measured.

    ``None`` is deliberately NOT "young" — see the module docstring's
    fail-closed rule.
    """
    if kind == "worktree":
        return _worktree_age_seconds(name, at=at)
    if kind == "branch":
        return _branch_age_seconds(root, name, at=at)
    return None


def load_baseline(path: Path | str) -> list[BaselineEntry]:
    path = Path(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise BaselineUnavailable(f"{path}: {type(exc).__name__}: {exc}") from exc
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise BaselineUnavailable(f"{path}: malformed baseline — 'entries' must be a list")
    result: list[BaselineEntry] = []
    for row in entries:
        try:
            result.append(BaselineEntry(kind=row["kind"], name=row["name"], reason=row["reason"]))
        except (KeyError, TypeError) as exc:
            raise BaselineUnavailable(f"{path}: malformed entry {row!r}: {exc}") from exc
    return result


def check_real_tree(
    root: Path | str,
    baseline_path: Path | str,
    *,
    ops=None,
    grace_hours: float | None = None,
    at: float | None = None,
) -> RealTreeVerdict:
    """Audit the REAL repository at ``root`` (read-only) against ``baseline_path``.

    Raises nothing on a bad baseline or an unreadable disk: both come back as a
    non-assessable verdict, same tri-state discipline as the rest of this
    package (CANNOT-ASSESS is never spelled as a clean pass).

    ``grace_hours`` defaults to the declared policy
    (``governance/policy/lease.REAL_TREE_GRACE_HOURS``); a caller may override
    it (the self-control / mutation tests pass ``0`` to prove the grace window
    is the thing keeping a young artifact from failing).
    """
    from governance.reconcile.sweep import RepoOps

    report: AuditReport = run_audit(root, ops=ops or RepoOps(root))
    if not report.assessable:
        return RealTreeVerdict(assessable=False, reason=f"disk audit CANNOT-ASSESS: {report.reason}")

    try:
        baseline = load_baseline(baseline_path)
    except BaselineUnavailable as exc:
        return RealTreeVerdict(assessable=False, reason=str(exc))

    resolved_grace_hours = DEFAULT_GRACE_HOURS if grace_hours is None else grace_hours
    grace_seconds = resolved_grace_hours * 3600.0
    now = time.time() if at is None else at

    baseline_by_key = {entry.key: entry for entry in baseline}
    unmatched_keys = {(item.artifact.kind, item.artifact.name) for item in report.unmatched}

    unbaselined = sorted(unmatched_keys - set(baseline_by_key))
    young: list[BaselineEntry] = []
    new_violations: list[BaselineEntry] = []
    for kind, name in unbaselined:
        age = artifact_age_seconds(root, kind, name, at=now)
        if age is not None and age < grace_seconds:
            young.append(
                BaselineEntry(
                    kind=kind,
                    name=name,
                    reason=f"unmatched but only {age / 3600.0:.1f}h old (< {resolved_grace_hours:g}h grace)",
                )
            )
        else:
            reason = (
                "not baselined and its age could not be measured (fail-closed: treated as old)"
                if age is None
                else f"not baselined and {age / 3600.0:.1f}h old (>= {resolved_grace_hours:g}h grace)"
            )
            new_violations.append(BaselineEntry(kind=kind, name=name, reason=reason))

    stale_entries = tuple(
        baseline_by_key[key] for key in sorted(set(baseline_by_key) - unmatched_keys)
    )

    return RealTreeVerdict(
        assessable=True,
        new_violations=tuple(new_violations),
        stale_entries=stale_entries,
        young=tuple(young),
        baseline_count=len(baseline),
        unmatched_count=len(unmatched_keys),
        grace_hours=resolved_grace_hours,
    )
