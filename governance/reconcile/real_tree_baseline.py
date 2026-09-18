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
  baseline (and is old enough — see "Age grace" below) is a NEW violation —
  the gate fails, named;
* a baseline entry whose artifact the audit no longer reports UNMATCHED (it was
  matched, reclaimed, or the worktree/branch was simply cleaned up) is STALE —
  reported by name, counted, but does **not** fail the gate (see "Stale is
  not fatal" below); it must still shrink by an explicit reviewed edit (or
  `--prune-stale`), never silently, but its mere presence is not a violation.

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

## Vanished-between-list-and-measure (#885 follow-up)

Age measurement runs *after* the disk listing (`audit()`'s `git worktree
list`/`git for-each-ref`), not atomically with it. Under concurrent lane
churn — exactly what three sibling lanes racing worktrees/branches during a
`make verify` run produces — an artifact the listing just reported UNMATCHED
can be **gone by the time its age is measured**: its worktree directory
removed, its branch ref deleted. Measured: `check-reconcile.sh`'s real-tree
step failed once during a concurrent `make verify` with 0 new violations and
`assessable=False`-shaped surprise, traced to exactly this race.

Before this artifact could only be **present-but-unmeasurable** (a `stat`
failure, an unreadable `git log`) — correctly fail-closed to OLD, because an
age this audit truly cannot read must never be read as "young enough to
ignore" (see "Age grace" above). But "the path no longer exists" / "the ref no
longer resolves" is not "I could not measure it" — it is "the artifact the
listing saw is no longer there", which is exactly the state a concurrent
cleanup produces and is never a violation on its own account.

`artifact_vanished()` checks existence/resolution *before* attempting to
measure age: a worktree whose path is gone, or a branch whose ref no longer
resolves (`git rev-parse --verify`), is **vanished** — reported and counted
(`RealTreeVerdict.vanished`), never fails the gate, and is never mistaken for
a genuinely present-but-unreadable artifact (`artifact_age_seconds` is
unchanged and still fail-closes to OLD for that case; it is now only reached
for artifacts `artifact_vanished()` says are still there).

## Stale is not fatal (second #740 follow-up)

Unlike ``governance/isolation/landed-baseline.json`` — whose entries are
**commits**, which never vanish once landed, so an entry there can only ever
be removed by an explicit reviewed edit once its shape stops matching — this
baseline's entries are **disk artifacts**: worktrees and branches, which are
*meant* to disappear once someone reclaims or cleans them up. Measured: a
worktree named in the baseline (``/tmp/ao-master-probe-wt``) was cleaned up —
the desired outcome — and the gate went red on it (``STALE ... 1 stale``).
Treating "the debris is gone" as a gate failure punishes the exact cleanup the
audit exists to prompt.

A stale entry is therefore **reported**, in the verdict's description and
count, so the baseline is visibly out of date and someone should prune it —
but it does not fail :attr:`RealTreeVerdict.ok` or the gate's exit code. The
baseline can only *grow* by an explicit reviewed edit (same as before); it can
*shrink* either by the same explicit edit, or mechanically via
:func:`prune_stale` (``status --disk --prune-stale``), which only ever removes
entries the audit no longer reports unmatched — it can never fabricate a drop
of a still-live artifact.

## Named quarantine, for work the worker is forbidden to discard (#1291)

A baseline entry says "this artifact was already unmatched when the baseline was
measured". It cannot honestly say the next thing the gate needs to say, because
the two are different kinds of fact:

* the baseline's ``young`` grace window **defers** the red by 24 hours rather
  than preventing it (measured at ``99f6b37``: 27 → 30 → 31 violations in seven
  minutes with no commit in between — every new one an artifact crossing the
  grace window while the fleet worked);
* and the residual are artifacts `AGENTS.md` **rule 17** forbids the worker to
  discard: an orphan whose work exists nowhere else keeps its worktree, its
  branch and its claim, and *is reported on every pass until someone resolves
  it*. A red `make verify` on this box is not a report — it is a fleet-wide
  serialization point (16 open pull requests at the time of measurement).

:file:`real-tree-quarantine.json` is how those two facts are reconciled
honestly, in the idiom this repository already uses for legacy drift
(``governance/lifecycle/baseline.json``, rule 16):

* **every exemption is named.** One entry per artifact — ``kind``, ``name``, the
  ``tip`` the exemption was *measured against*, and a ``reason`` a human wrote.
  There is no pattern, no prefix and no wildcard: an artifact is excused only by
  an entry naming it and the exact commit it was recorded at.
* **a new artifact is never absorbed.** A new branch or worktree is by
  definition not in the document, so it fails immediately. A branch whose tip has
  *moved* is a different artifact and fails immediately too — the recorded tip is
  what makes that detectable, and the lapsed entry is named as it fails.
* **an entry that excuses nothing FAILS.** If the artifact is gone, or is no
  longer unmatched, the exemption has outlived its need: the verdict names it in
  ``stale_quarantine`` and exits **1** until the entry is removed in a reviewed
  edit. The document can therefore only shrink, never rot in place.
* **it is a lease, not a permanent allow.** The document declares the issue
  tracking it and when that issue's state was last *measured*. An entry is
  honoured only while that measurement says the tracking issue is ``open`` and is
  younger than the declared ``max_age_hours``. A missing, malformed, expired or
  not-open declaration is **never** read as "still excused": no entry is honoured
  and the lease itself is reported as a violation. This is the same fail-closed
  direction ``governance/lifecycle`` takes (its quarantine is honoured only while
  its tracking issue is open, and *unknown* counts as stale) — a gate cannot read
  the board offline, so the age bound is what keeps the declaration falsifiable
  rather than decorative.

Quarantined artifacts are still **reported on every pass**, by name, in the
verdict: rule 17's "reported until someone resolves it" is satisfied by the
report, and the tracking issue carries the resolution.

## An exemption has a venue (#1317 → #1321)

An exemption's artifact is a **disk artifact of one repository instance** — a
local branch of this checkout, a worktree path on this machine. That is a fact
about a *venue*. The document is tracked, so it is read on checkouts where the
artifact was never there; read venue-blind, every entry there is "no longer an
unmatched artifact" and FAILS as a stale exemption. That is exactly what #1317
measured on a pristine clone (`0 new-and-old, 538 stale; 23 stale quarantine
exemption(s)`) and why it emptied the document — which un-quarantined all 23 on
the one box that has them, and red the fleet again. #1317's reading of the other
checkout was right, and its remedy was wrong: the two checkouts need *different
answers*, not one of them deleted.

So the document **declares the venue its exemptions were measured in**
(`venue.git_common_dir` — see :func:`repository_venue`) and the check takes one
of two branches:

* **the same venue** — every rule above applies unchanged, teeth included: an
  entry that excuses nothing (the artifact gone, or no longer unmatched) still
  fails by name, and a lapsed lease still honours nothing;
* **a different venue** — the whole document is **inert** here. Each entry is
  reported (:attr:`RealTreeVerdict.inapplicable_quarantine`, `NOT-APPLICABLE`),
  honours **nothing** — the fail-closed direction: an artifact that *is*
  unmatched here stays a finding, because this document does not speak for this
  checkout — and is not fatal, because a *stale* exemption is a claim about the
  declared venue's disk, which this checkout cannot observe at all. The lease is
  venue-scoped with it: a document that is not in force here cannot lapse here.

This cannot be used to buy a green. Naming a venue that is not yours honours
nothing, so the artifacts the entries were hiding here come back as findings;
declaring no venue at all (while holding entries) is CANNOT-ASSESS; and an
artifact the document does not name is a finding on every venue, because nothing
about a venue can absorb it. A venue this check cannot read is CANNOT-ASSESS
too, never "some other venue" — an unreadable identity must not silently stop
exemptions from being evaluated.

A document that declares **no exemptions at all** is inert everywhere (there is
nothing to honour), and so is its lease: a lease bounding no entry cannot turn
anything green, and failing on it would be the formality GR-12 refuses. Deleting
the entries to reach that state does not buy a green either — the artifacts they
named then fail by NAME as unbaselined-and-old.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from governance.policy import lease
from governance.reconcile import ledger as reconcile_ledger
from governance.reconcile.audit import AuditReport, audit as run_audit

#: kind/name pairs the baseline can carry entries for.
_KEY = tuple[str, str]

#: The reviewed, named exemptions (issue #1291). A tracked file on purpose:
#: an exemption is a reviewed edit in a commit a human reads, never gitignored
#: runtime state.
QUARANTINE_RELPATH = Path("governance") / "reconcile" / "real-tree-quarantine.json"

#: Read from the single declared policy (governance/policy/lease.py), never
#: restated as a bare literal here (the policy scan would refuse that).
DEFAULT_GRACE_HOURS = lease.REAL_TREE_GRACE_HOURS

YOUNG = "young"
VANISHED = "vanished"


class BaselineUnavailable(Exception):
    """The baseline file itself could not be read or is malformed."""


class QuarantineUnavailable(Exception):
    """The quarantine document could not be read or is malformed.

    Distinct from *absent*: no document means no exemptions (which can only add
    findings), while an unreadable one means the gate cannot say what is excused
    — CANNOT-ASSESS, never a pass.
    """


@dataclass(frozen=True)
class BaselineEntry:
    kind: str
    name: str
    reason: str

    @property
    def key(self) -> _KEY:
        return (self.kind, self.name)


@dataclass(frozen=True)
class QuarantineEntry:
    """One named exemption: this artifact, at this tip, for this reason (#1291).

    ``tip`` is load-bearing, not documentation. It is the commit the exemption
    was *measured against*, so a branch or worktree that has moved on is a
    different artifact: it fails immediately, and the lapsed entry is named as it
    fails. Without it, a name would excuse whatever later appeared under it.
    """

    kind: str
    name: str
    reason: str
    tip: str

    @property
    def key(self) -> _KEY:
        return (self.kind, self.name)


@dataclass(frozen=True)
class QuarantineVenue:
    """The repository instance an exemption's artifacts belong to (#1321).

    ``git_common_dir`` is load-bearing, not documentation: it is the one value
    every worktree of an instance shares — a lane worktree, the shared checkout,
    an operator's gate worktree — and that no other clone, and no pristine
    checkout of the same repository, has. It is what makes "this artifact is
    gone" (a resolution, in force at its venue) distinguishable from "this
    artifact was never here" (inert, at any other venue).
    """

    git_common_dir: str
    measured_on: str = ""

    def describe(self) -> str:
        if self.measured_on:
            return f"{self.git_common_dir} ({self.measured_on})"
        return self.git_common_dir


@dataclass(frozen=True)
class QuarantineLease:
    """The term of the exemptions: an open tracking issue, measured recently.

    ``max_age_hours`` is declared in the document rather than as a constant here
    because it is a property of *that review*, not a fleet-wide timing (the fleet
    timings live in ``governance/policy/lease.py``; the policy scan owns that
    surface and would refuse a restatement here).
    """

    tracked_by: str
    state: str
    measured_at: float
    measured_by: str
    max_age_hours: float

    def refusal(self, *, at: float) -> str:
        """``""`` when the lease holds; else the reason no entry may be honoured."""
        if self.state != "open":
            return (
                f"the tracking issue {self.tracked_by} is "
                f"{self.state or 'unstated'}, not open"
            )
        age_hours = (at - self.measured_at) / 3600.0
        if age_hours > self.max_age_hours:
            return (
                f"the tracking measurement is {age_hours:.1f}h old "
                f"(> {self.max_age_hours:g}h lease); re-measure {self.tracked_by} "
                "and record it, or retire the exemptions"
            )
        return ""

    def describe(self, *, at: float) -> str:
        age_hours = (at - self.measured_at) / 3600.0
        return (
            f"quarantine: honoured — {self.tracked_by} {self.state}, measured "
            f"{age_hours:.1f}h ago (lease {self.max_age_hours:g}h, by {self.measured_by})"
        )


@dataclass
class RealTreeVerdict:
    """The result of checking the real tree's audit against its baseline."""

    assessable: bool
    new_violations: tuple[BaselineEntry, ...] = field(default_factory=tuple)
    stale_entries: tuple[BaselineEntry, ...] = field(default_factory=tuple)
    young: tuple[BaselineEntry, ...] = field(default_factory=tuple)
    vanished: tuple[BaselineEntry, ...] = field(default_factory=tuple)
    quarantined: tuple[QuarantineEntry, ...] = field(default_factory=tuple)
    stale_quarantine: tuple[BaselineEntry, ...] = field(default_factory=tuple)
    #: Entries of a document that is not in force in this venue (#1321): reported
    #: by name, honouring nothing, and NOT fatal — see "An exemption has a venue".
    inapplicable_quarantine: tuple[QuarantineEntry, ...] = field(default_factory=tuple)
    #: The repository instance this verdict was measured in, and the one the
    #: exemptions declare. Equal (or undeclared, with no entries) is "in force".
    venue: str = ""
    quarantine_venue: str = ""
    quarantine_applicable: bool = True
    quarantine_note: str = ""
    baseline_count: int = 0
    unmatched_count: int = 0
    grace_hours: float = 0.0
    reason: str = ""

    @property
    def ok(self) -> bool:
        # Stale entries are reported, not fatal: they name debris the baseline
        # over-claims, not debris the disk still has to explain. Disk artifacts
        # are meant to disappear (unlike a landed commit, which never does) —
        # see the module docstring's "Stale is not fatal".
        #
        # A stale *quarantine* entry is the opposite: losing that artifact would
        # mean the worker discarded work that exists nowhere else, so the excuse
        # outliving its need — or its lease lapsing — is exactly what must bite.
        return self.assessable and not self.new_violations and not self.stale_quarantine

    def describe(self) -> str:
        if not self.assessable:
            return f"real-tree-baseline: CANNOT-ASSESS — {self.reason}"
        lines = [
            f"real-tree-baseline: {self.unmatched_count} unmatched artifact(s) on disk, "
            f"{self.baseline_count} baselined, {len(self.young)} young (< {self.grace_hours:g}h, not failed), "
            f"{len(self.stale_entries)} stale (not failed), {len(self.vanished)} vanished (not failed), "
            f"{len(self.quarantined)} quarantined by name (reported, not failed)"
        ]
        if self.quarantine_note:
            lines.append(f"  {self.quarantine_note}")
        for entry in self.inapplicable_quarantine:
            lines.append(
                f"  NOT-APPLICABLE {entry.kind} {entry.name} @{entry.tip[:12]} — the exemption "
                f"was measured in {self.quarantine_venue or '(an unstated venue)'}; it is not in "
                "force here (reported, honouring nothing, not fatal)"
            )
        for entry in self.vanished:
            lines.append(f"  VANISHED {entry.kind} {entry.name} — {entry.reason}")
        for entry in self.stale_quarantine:
            lines.append(f"  STALE-QUARANTINE {entry.kind} {entry.name} — {entry.reason}")
        for entry in self.young:
            lines.append(f"  YOUNG    {entry.kind} {entry.name} — {entry.reason}")
        for entry in self.new_violations:
            lines.append(f"  NEW      {entry.kind} {entry.name} — not in the baseline (add it, reviewed, or fix it)")
        for entry in self.quarantined:
            lines.append(
                f"  QUARANTINED {entry.kind} {entry.name} @{entry.tip[:12]} — {entry.reason}"
            )
        for entry in self.stale_entries:
            lines.append(f"  STALE    {entry.kind} {entry.name} — baselined but no longer unmatched (remove it, e.g. --prune-stale)")
        if self.ok:
            suffix = " (some stale entries remain — safe to --prune-stale)" if self.stale_entries else ""
            lines.append(f"real-tree-baseline: OK — no new unbaselined-and-old artifact{suffix}")
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


def _branch_resolves(root: Path | str, branch: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def branch_tip(root: Path | str, branch: str) -> str:
    """The commit a branch points at right now, or ``""`` when it does not resolve."""
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def worktree_head(path: str) -> str:
    """The commit a worktree's HEAD is at right now, or ``""`` when it is gone."""
    if not Path(path).exists():
        return ""
    result = subprocess.run(
        ["git", "-C", path, "rev-parse", "HEAD"], capture_output=True, text=True
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def artifact_tip(root: Path | str, kind: str, name: str) -> str:
    """The tip an exemption for this artifact would have to have been measured at."""
    if kind == "branch":
        return branch_tip(root, name)
    if kind == "worktree":
        return worktree_head(name)
    return ""


def repository_venue(root: Path | str) -> str:
    """The repository instance ``root`` belongs to, as an absolute path (#1321).

    Read from git rather than guessed: ``--git-common-dir`` is the one value
    every worktree of one instance shares (a lane worktree, the shared checkout,
    an operator's gate worktree all answer the same) and that a pristine clone
    of the same repository cannot have. That is what makes the document's
    ``venue`` falsifiable offline, with no network and no board access.

    ``""`` when git cannot answer. That is deliberately *not* treated as "some
    other venue": a venue this check cannot read is CANNOT-ASSESS, because
    reading it as "elsewhere" would silently stop evaluating exemptions.
    """
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--path-format=absolute", "--git-common-dir"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0 and result.stdout.strip():
        return os.path.realpath(result.stdout.strip())
    # `--path-format` needs git >= 2.31: the plain form is relative to `root`.
    fallback = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--git-common-dir"],
        capture_output=True,
        text=True,
    )
    if fallback.returncode != 0 or not fallback.stdout.strip():
        return ""
    return os.path.realpath(Path(str(root)) / fallback.stdout.strip())


def load_quarantine(
    path: Path | str,
) -> tuple[QuarantineLease, list[QuarantineEntry], QuarantineVenue]:
    """Read the reviewed, named exemptions, their lease and their venue (#1291, #1321).

    Raises :class:`QuarantineUnavailable` for anything it cannot read *strictly*:
    a malformed document must not be read as "nothing is excused" (that would be
    a silent red) nor as "everything is excused" (a silent green) — it is
    CANNOT-ASSESS, same discipline as :func:`load_baseline`.

    A document that holds entries but does not declare the venue they were
    measured in is *also* unreadable in the sense that matters: an exemption
    names a disk artifact of one repository instance, and without the venue
    there is no way to tell "the artifact is gone" (a resolution) from "the
    artifact was never here" (nothing to assess) — which is the whole reason
    #1317 emptied this document (#1321). CANNOT-ASSESS, never a pass.
    """
    path = Path(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise QuarantineUnavailable(f"{path}: {type(exc).__name__}: {exc}") from exc
    if not isinstance(payload, dict):
        raise QuarantineUnavailable(f"{path}: malformed quarantine — expected an object")
    tracked = payload.get("tracked_by")
    tracking = payload.get("tracking")
    if not isinstance(tracked, str) or not tracked:
        raise QuarantineUnavailable(f"{path}: malformed quarantine — 'tracked_by' is required")
    if not isinstance(tracking, dict):
        raise QuarantineUnavailable(f"{path}: malformed quarantine — 'tracking' is required")
    try:
        measured_at = datetime.strptime(
            str(tracking["measured_at"]), "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=timezone.utc).timestamp()
        quarantine_lease = QuarantineLease(
            tracked_by=tracked,
            state=str(tracking["state"]).lower(),
            measured_at=measured_at,
            measured_by=str(tracking["measured_by"]),
            max_age_hours=float(tracking["max_age_hours"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise QuarantineUnavailable(f"{path}: malformed 'tracking' block: {exc}") from exc

    rows = payload.get("quarantine")
    if not isinstance(rows, list):
        raise QuarantineUnavailable(f"{path}: malformed quarantine — 'quarantine' must be a list")
    entries: list[QuarantineEntry] = []
    for row in rows:
        try:
            entry = QuarantineEntry(
                kind=row["kind"], name=row["name"], reason=row["reason"], tip=row["tip"]
            )
        except (KeyError, TypeError) as exc:
            raise QuarantineUnavailable(f"{path}: malformed entry {row!r}: {exc}") from exc
        if not entry.tip:
            # The tip is what stops a name excusing whatever appears under it.
            raise QuarantineUnavailable(
                f"{path}: entry {entry.kind} {entry.name} has no 'tip' to pin it to"
            )
        entries.append(entry)

    venue_payload = payload.get("venue")
    if venue_payload is None:
        if entries:
            raise QuarantineUnavailable(
                f"{path}: {len(entries)} exemption(s) and no 'venue' — an exemption names a disk "
                "artifact of one repository instance, so a document that does not declare which "
                "one cannot be interpreted (re-measure it and record the venue, #1321)"
            )
        return quarantine_lease, entries, QuarantineVenue(git_common_dir="")
    if not isinstance(venue_payload, dict) or not str(venue_payload.get("git_common_dir") or ""):
        raise QuarantineUnavailable(
            f"{path}: malformed 'venue' — 'git_common_dir' is required (see repository_venue)"
        )
    return (
        quarantine_lease,
        entries,
        QuarantineVenue(
            git_common_dir=str(venue_payload["git_common_dir"]),
            measured_on=str(venue_payload.get("measured_on", "")),
        ),
    )


def artifact_vanished(root: Path | str, kind: str, name: str) -> bool:
    """The artifact the disk listing reported UNMATCHED is already gone.

    Checked *before* any age measurement is attempted, and deliberately
    narrower than "age could not be measured" (:func:`artifact_age_seconds`):
    a worktree path that no longer exists, or a branch ref that no longer
    resolves, is a concurrent cleanup racing the audit — never a violation on
    its own. An artifact that IS still there but whose age this audit cannot
    read (a `stat` failure, an unreadable `git log`) is unaffected by this
    check and stays fail-closed to OLD, exactly as before (#885).
    """
    if kind == "worktree":
        return not Path(name).exists()
    if kind == "branch":
        return not _branch_resolves(root, name)
    return False


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
    quarantine_path: Path | str | None = None,
    ops=None,
    grace_hours: float | None = None,
    at: float | None = None,
) -> RealTreeVerdict:
    """Audit the REAL repository at ``root`` (read-only) against ``baseline_path``.

    Raises nothing on a bad baseline or an unreadable disk: both come back as a
    non-assessable verdict, same tri-state discipline as the rest of this
    package (CANNOT-ASSESS is never spelled as a clean pass).

    ``quarantine_path`` is the reviewed, named-exemption document (#1291). When
    it is given and present, its entries excuse an artifact *only* while the
    lease it declares holds (an open tracking issue, measured recently); without
    it no artifact is excused — the fail-closed direction, since an exemption is
    the only thing here that can turn a finding into a pass.

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

    # --- the named, leased quarantine (#1291), scoped to its venue (#1321) ---
    quarantine_entries: list[QuarantineEntry] = []
    stale_quarantine: list[BaselineEntry] = []
    inapplicable_quarantine: list[QuarantineEntry] = []
    quarantine_note = ""
    honoured: dict[_KEY, QuarantineEntry] = {}
    venue = ""
    declared_venue = ""
    quarantine_applicable = True
    document = Path(quarantine_path) if quarantine_path is not None else None
    if document is not None and document.exists():
        try:
            quarantine_lease, quarantine_entries, quarantine_venue = load_quarantine(document)
        except QuarantineUnavailable as exc:
            return RealTreeVerdict(assessable=False, reason=str(exc))
        declared_venue = quarantine_venue.git_common_dir
        if not quarantine_entries:
            # Nothing is excused and nothing can be: a lease bounding no entry
            # has no power to turn a finding into a pass, so it is reported and
            # evaluated by nothing (GR-12: a check that cannot fail is a
            # formality). Deleting entries cannot buy a green either — the
            # artifacts they named then fail by NAME as unbaselined-and-old.
            quarantine_note = (
                "quarantine: empty — the document declares no exemptions "
                f"(tracked by {quarantine_lease.tracked_by}); nothing is excused"
            )
        else:
            venue = repository_venue(root)
            if not venue:
                return RealTreeVerdict(
                    assessable=False,
                    reason=(
                        "this repository instance's identity could not be read "
                        "(`git rev-parse --git-common-dir`), so no exemption's venue can be "
                        f"assessed; {document} declares {declared_venue or '(no venue)'}"
                    ),
                )
            quarantine_applicable = os.path.realpath(declared_venue) == venue
            if not quarantine_applicable:
                # The document is not in force here (#1321): every entry is
                # reported and honours nothing (fail-closed — an artifact that
                # IS unmatched here stays a finding), and none of it is fatal,
                # because a stale exemption is a claim about the declared
                # venue's disk, which this checkout cannot observe at all. The
                # lease is venue-scoped with it: a document that is not in force
                # here cannot lapse here.
                inapplicable_quarantine = list(quarantine_entries)
                quarantine_note = (
                    f"quarantine: NOT IN FORCE — the {len(quarantine_entries)} exemption(s) were "
                    f"measured in {quarantine_venue.describe()}; this gate is running in {venue} "
                    "— each is reported by name and honours nothing here, and its staleness "
                    "cannot be observed from here (not fatal)"
                )
            else:
                refusal = quarantine_lease.refusal(at=now)
                if refusal:
                    # No entry is honoured, and the lease itself is the named
                    # violation: an exemption nobody can show is still current
                    # is not an exemption.
                    quarantine_note = f"quarantine: NOT HONOURED — {refusal}"
                    stale_quarantine.append(
                        BaselineEntry(
                            kind="lease", name=quarantine_lease.tracked_by, reason=refusal
                        )
                    )
                else:
                    quarantine_note = quarantine_lease.describe(at=now)
                    for entry in quarantine_entries:
                        if entry.key not in unmatched_keys:
                            stale_quarantine.append(
                                BaselineEntry(
                                    kind=entry.kind,
                                    name=entry.name,
                                    reason=(
                                        "quarantined but no longer an unmatched artifact at its "
                                        "declared venue. Confirm which happened before removing this "
                                        "entry: the work reached the default branch (a resolution — "
                                        "remove it), or the artifact was reclaimed (then the "
                                        "exemption is the last record that work existing nowhere "
                                        "else was discarded, which is the owner's call, not a "
                                        f"cleanup). Tracked by {quarantine_lease.tracked_by}."
                                    ),
                                )
                            )
                            continue
                        current = artifact_tip(root, entry.kind, entry.name)
                        if current != entry.tip:
                            # A moved branch/worktree is a different artifact: it must
                            # fail immediately rather than be absorbed by the name it
                            # reuses.
                            stale_quarantine.append(
                                BaselineEntry(
                                    kind=entry.kind,
                                    name=entry.name,
                                    reason=(
                                        f"quarantined at {entry.tip[:12]} but now at "
                                        f"{current[:12] or '(gone)'} — a different artifact; "
                                        "re-measure it and re-record the exemption"
                                    ),
                                )
                            )
                            continue
                        honoured[entry.key] = entry

    unbaselined = sorted(unmatched_keys - set(baseline_by_key))
    young: list[BaselineEntry] = []
    new_violations: list[BaselineEntry] = []
    vanished: list[BaselineEntry] = []
    quarantined: list[QuarantineEntry] = []
    for kind, name in unbaselined:
        if (kind, name) in honoured:
            quarantined.append(honoured[(kind, name)])
            continue
        if artifact_vanished(root, kind, name):
            vanished.append(
                BaselineEntry(
                    kind=kind,
                    name=name,
                    reason="gone between the disk listing and age measurement "
                    "(concurrent cleanup); not a violation",
                )
            )
            continue
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

    verdict = RealTreeVerdict(
        assessable=True,
        new_violations=tuple(new_violations),
        stale_entries=stale_entries,
        young=tuple(young),
        vanished=tuple(vanished),
        quarantined=tuple(quarantined),
        stale_quarantine=tuple(stale_quarantine),
        inapplicable_quarantine=tuple(inapplicable_quarantine),
        venue=venue,
        quarantine_venue=declared_venue,
        quarantine_applicable=quarantine_applicable,
        quarantine_note=quarantine_note,
        baseline_count=len(baseline),
        unmatched_count=len(unmatched_keys),
        grace_hours=resolved_grace_hours,
    )
    try:
        reconcile_ledger.record_real_tree_verdict(root, verdict, at=now)
    except reconcile_ledger.LedgerUnavailable:
        # Recording is bookkeeping, not the verdict itself (#885): the ledger
        # never turns a real disk verdict into CANNOT-ASSESS. `ledger.verify()`
        # is how a corrupt/missing record is made visible.
        pass
    return verdict


def prune_stale(baseline_path: Path | str, verdict: RealTreeVerdict) -> int:
    """Rewrite the baseline, dropping exactly ``verdict.stale_entries``.

    The reap is one reviewed command (``status --disk --prune-stale``) instead
    of a hand-edit: it can only ever drop entries the audit *itself* just
    reported as no-longer-unmatched — it never fabricates a drop of a still-
    live artifact, and it never touches ``new_violations`` or ``young``.
    Returns the number of entries removed. A no-op (0) when there is nothing
    stale, or when ``verdict`` is not assessable.
    """
    if not verdict.assessable or not verdict.stale_entries:
        return 0
    path = Path(baseline_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    drop_keys = {entry.key for entry in verdict.stale_entries}
    before = payload.get("entries", [])
    after = [row for row in before if (row.get("kind"), row.get("name")) not in drop_keys]
    payload["entries"] = after
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return len(before) - len(after)
