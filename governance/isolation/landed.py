"""Landed-history enforcement — the ticket-trailer rule, over history that shipped.

The lane audit (:mod:`governance.isolation.audit`) re-derives isolation from the
*live* worktrees, so a commit that has already landed is never examined.
:mod:`governance.isolation.trailer` closed half of that gap by delegating one
commit's verdict to the one implementation of the rule (the predicate in
``scripts/check-pr-contract.sh``, issue #288) and pointing it at landed commits.
What it did not do is *enforce* anything: ``cli.py landed`` reports the shared
gate's verdict for a range, and nothing runs it. Issue #287 names that as the
missing half — the rule had a mechanism and no surface over real history.

This module turns a whole-range verdict into an **enforcement** verdict, and it
solves the problem that makes the naive version unusable: measured at ``9707146``
(thirteen post-boundary commits), landed history does **not** satisfy the rule,
and the shared predicate already says so. A check that merely failed on those
commits would turn ``make verify`` red for every lane on the day it landed, which
is how a gate gets disabled instead of obeyed. The residue is not frozen at that
measurement: a commit that lands later and still misses the rule is refused by
name, and reconciling one means recording it here with the finding that was
measured for it (``4e3d62da``, the #826 squash, recorded by issue #832).

THE DESIGN — a class boundary, plus a quarantine that can only shrink
--------------------------------------------------------------------
* **The legacy class is already frozen, and this module does not restate it.**
  ``scripts/check-pr-contract.sh --landed`` grandfathers every commit strictly
  before the commit that landed the PR-contract gate (#308, ``a7e73129``) and
  reports findings only after it. That boundary is single-sourced there; this
  module consumes the predicate's post-boundary findings and never maintains a
  second copy of the boundary. A boundary is provably frozen: a new commit is
  always a descendant, never an ancestor, so the grandfathering cannot grow.
* **The measured residue is quarantined by commit.** The commits that landed
  *after* that boundary and still do not comply are recorded in
  ``landed-baseline.json``, by full commit id, with the finding the shared
  predicate prints for each. They are named, not silently accepted: the check
  reports them as recorded legacy on every run. The quarantine is not closed to
  new measurements -- a commit that lands non-compliant is refused by name and
  the *recording* of it is the reconciliation, never a quiet pass.
* **The quarantine can only shrink.** An entry whose commit no longer produces a
  finding is *stale* — the predicate changed, or the commit was rewritten — and a
  stale entry is a **failure** (``quarantine-entry-stale``), never a quiet
  grandfather. Removing the entry is the only way to make it pass. An entry whose
  commit is outside the assessed range is *not assessed*
  (``quarantine-entry-not-assessed``) and the verdict is CANNOT-ASSESS: an
  unprovable baseline is not a pass.
* **Nothing here is optimistic about the predicate.** A range with no commits, an
  unresolvable range, an unavailable predicate, a predicate that fails without a
  parseable finding, a missing or malformed baseline — each is CANNOT-ASSESS or
  NOT-OK, never OK. The failure that ends a gate's usefulness is not a red; it is
  a green that was never measured (GR-12).

The verdict is still the shared predicate's. This module adds bookkeeping about
*which* findings are recorded legacy, and it can never turn a finding the
predicate did not make into a failure of its own.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .trailer import PredicateUnavailable, findings_in, run_landed
from .worktree import git

VERDICT_OK = "OK"
VERDICT_NOT_OK = "NOT-OK"
VERDICT_CANNOT_ASSESS = "CANNOT-ASSESS"

#: The repository's tri-state exit-code convention (0 OK / 1 NOT-OK / 2 CANNOT-ASSESS),
#: so every surface that prints a verdict maps it back to the same codes.
EXIT_CODES = {VERDICT_OK: 0, VERDICT_NOT_OK: 1, VERDICT_CANNOT_ASSESS: 2}

#: The default range: everything reachable from the branch under test. Not
#: ``origin/master..HEAD`` — a range relative to a remote-tracking ref is empty in
#: the shared checkout, and an empty range is exactly the vacuous pass this
#: surface exists to refuse.
DEFAULT_RANGE = "HEAD"

#: The recorded legacy residue, beside this module.
BASELINE_PATH = Path(__file__).resolve().parent / "landed-baseline.json"

LABEL = "isolation-enforce"


class BaselineMalformed(RuntimeError):
    """The recorded-legacy baseline cannot be trusted to answer the question."""


@dataclass(frozen=True)
class Entry:
    """One recorded legacy commit: what the predicate said, and why it is kept."""

    sha: str
    code: str
    why: str


@dataclass(frozen=True)
class Baseline:
    """The quarantine: commits that landed non-compliant before this rule existed."""

    measured_head: str
    measured_at: str
    entries: tuple[Entry, ...]

    def find(self, sha12: str) -> Entry | None:
        """The entry for a finding's (possibly abbreviated) commit id."""
        for entry in self.entries:
            if entry.sha.startswith(sha12):
                return entry
        return None


@dataclass(frozen=True)
class Finding:
    """A finding the shared predicate made about one landed commit."""

    code: str
    sha: str


@dataclass(frozen=True)
class Assessment:
    """The enforcement verdict: what the predicate said, and how it is booked.

    ``assessed`` is the number of non-merge commits *reachable* in the range, not
    the number the predicate examined: the shared predicate grandfathers the
    pre-boundary class itself, and this module deliberately does not re-derive
    that boundary (a second copy would be a second rule).
    """

    verdict: str
    range_: str
    assessed: int
    quarantined: tuple[Finding, ...]
    unenforced: tuple[Finding, ...]
    stale: tuple[Entry, ...]
    unassessed: tuple[Entry, ...]
    reason: str

    def lines(self) -> list[str]:
        """The report, one line per finding — named so it can be quoted."""
        out: list[str] = []
        if self.verdict == VERDICT_NOT_OK and self.reason:
            out.append(f"  FAIL  {self.reason}")
        for entry in self.stale:
            out.append(
                f"  FAIL  quarantine-entry-stale:{entry.sha[:12]} "
                f"({entry.code}) now complies, so it is no longer legacy — "
                f"remove it from the baseline"
            )
        for entry in self.unassessed:
            out.append(
                f"  FAIL  quarantine-entry-not-assessed:{entry.sha[:12]} "
                f"({entry.code}) is not in {self.range_} — the baseline cannot be reconciled"
            )
        for finding in self.unenforced:
            out.append(
                f"  FAIL  {finding.code}:{finding.sha} is not recorded legacy — if the commit has "
                f"not MERGED yet, put the reference in a trailing trailer paragraph and re-push; "
                f"if it is already landed on a protected branch the message can never be corrected "
                f"(history is never rewritten), so record it in {BASELINE_PATH.name} by an explicit "
                f"reviewed commit carrying this measured finding — never automatically, and never "
                f"to silence a run (#836)"
            )
        for finding in self.quarantined:
            out.append(f"  NOTE  recorded legacy  {finding.code}:{finding.sha}")
        out.append(
            f"  INFO  {self.assessed} non-merge commit(s) reachable in {self.range_}; the shared "
            f"predicate enforces those after its own boundary and grandfathers the earlier class "
            f"without a second copy of that boundary here"
        )
        if self.verdict == VERDICT_OK:
            out.append(
                f"{LABEL}: OK — {len(self.quarantined)} recorded legacy, 0 unrecorded, 0 stale in {self.range_}"
            )
        elif self.verdict == VERDICT_NOT_OK:
            out.append(
                f"{LABEL}: NOT-OK — {len(self.unenforced)} unrecorded and {len(self.stale)} stale "
                f"baseline entr(ies) in {self.range_}"
            )
        else:
            out.append(f"{LABEL}: CANNOT-ASSESS — {self.reason}")
        return out


def load_baseline(path: Path | str = BASELINE_PATH) -> Baseline:
    """Read the recorded-legacy baseline, refusing anything it cannot verify.

    A malformed baseline is a defect in this check's own input, so it is refused
    rather than ignored: ignoring it would silently un-grandfather every recorded
    commit, and guessing at it would silently grandfather ones nobody measured.
    """
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BaselineMalformed(f"baseline-missing: {path} cannot be read: {exc}") from exc
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise BaselineMalformed(f"baseline-malformed: {path} is not JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise BaselineMalformed(f"baseline-malformed: {path} is not a JSON object")
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise BaselineMalformed(f"baseline-malformed: {path} has no 'entries' list")
    parsed: list[Entry] = []
    for index, item in enumerate(entries):
        if not isinstance(item, dict):
            raise BaselineMalformed(f"baseline-malformed: {path} entry {index} is not an object")
        sha = str(item.get("sha", ""))
        code = str(item.get("code", ""))
        why = str(item.get("why", ""))
        if len(sha) != 40 or any(char not in "0123456789abcdef" for char in sha):
            raise BaselineMalformed(f"baseline-malformed: {path} entry {index} sha {sha!r} is not a 40-hex commit id")
        if not code or not why:
            raise BaselineMalformed(f"baseline-malformed: {path} entry {index} ({sha[:12]}) needs both 'code' and 'why'")
        parsed.append(Entry(sha=sha, code=code, why=why))
    shas = [entry.sha for entry in parsed]
    if len(set(shas)) != len(shas):
        raise BaselineMalformed(f"baseline-malformed: {path} records the same commit twice")
    return Baseline(
        measured_head=str(payload.get("measured_head", "")),
        measured_at=str(payload.get("measured_at", "")),
        entries=tuple(parsed),
    )


def range_commits(repo: Path | str, range_: str) -> list[str] | None:
    """The non-merge commits in ``range_``, or None when the range does not resolve.

    ``--no-merges`` matches the shared predicate's own scope: a merge message is
    generated by git, not authored, and demanding a trailer in it would fail a
    lane for merging ``origin/master`` into its own branch.
    """
    result = git(repo, "rev-list", "--no-merges", range_)
    if result.returncode != 0:
        return None
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def assess(
    repo: Path | str,
    range_: str = DEFAULT_RANGE,
    baseline_path: Path | str = BASELINE_PATH,
    gate: str = "",
) -> Assessment:
    """Enforce the ticket-trailer rule over landed history in ``range_``.

    ``gate`` overrides the shared predicate's grandfathering boundary and exists
    so the rule can be proved offline, against a scratch repository whose
    boundary is a commit the test controls — the same seam ``cli.py landed
    --gate`` exposes. The default ("") leaves the boundary to the shared
    predicate, which is the only place it is declared.
    """

    def cannot_assess(reason: str, assessed: int = 0) -> Assessment:
        return Assessment(VERDICT_CANNOT_ASSESS, range_, assessed, (), (), (), (), reason)

    commits = range_commits(repo, range_)
    if commits is None:
        return cannot_assess(f"the range {range_} does not resolve in {repo}")
    if not commits:
        # An empty range is the classic vacuous pass: no commits checked, nothing
        # complained, and the gate reports green (GR-12).
        return cannot_assess(f"no non-merge commit in {range_} — nothing assessed is not a pass")

    try:
        baseline = load_baseline(baseline_path)
    except BaselineMalformed as exc:
        # NOT-OK rather than CANNOT-ASSESS, and deliberately: deleting the
        # baseline would otherwise be a way to switch this check off, because a
        # skip is not a failure. A missing input to a gate is a defect in the
        # gate (the same rule ``scripts/check-gate-coverage.sh`` applies to its
        # own baseline file).
        return Assessment(VERDICT_NOT_OK, range_, len(commits), (), (), (), (), str(exc))

    try:
        result = run_landed(repo, range_, gate)
    except PredicateUnavailable as exc:
        return cannot_assess(f"the shared predicate is unavailable: {exc}", len(commits))

    if result.returncode == 2:
        detail = result.output.strip().splitlines()
        return cannot_assess(
            f"the shared predicate could not assess {range_}: {detail[-1] if detail else 'no output'}",
            len(commits),
        )

    findings = [Finding(code=code, sha=sha) for code, sha in findings_in(result.output)]
    if result.returncode != 0 and not findings:
        # The predicate failed and this module cannot say why: unparseable is
        # unproven, never clean.
        return cannot_assess(
            f"the shared predicate exited {result.returncode} without a parseable finding for {range_}",
            len(commits),
        )
    if result.returncode == 0 and findings:
        return cannot_assess(
            f"the shared predicate reported {len(findings)} finding(s) for {range_} but exited 0",
            len(commits),
        )

    present = set(commits)
    quarantined: list[Finding] = []
    unenforced: list[Finding] = []
    recorded: set[str] = set()
    for finding in findings:
        entry = baseline.find(finding.sha)
        if entry is None:
            unenforced.append(finding)
        else:
            recorded.add(entry.sha)
            quarantined.append(finding)

    stale: list[Entry] = []
    unassessed: list[Entry] = []
    for entry in baseline.entries:
        if entry.sha in recorded:
            continue
        if entry.sha in present:
            stale.append(entry)
        else:
            unassessed.append(entry)

    if unenforced or stale:
        verdict = VERDICT_NOT_OK
    elif unassessed:
        verdict = VERDICT_CANNOT_ASSESS
    else:
        verdict = VERDICT_OK
    reason = ""
    if verdict == VERDICT_CANNOT_ASSESS:
        reason = (
            f"{len(unassessed)} recorded legacy commit(s) are not in {range_}, so the baseline "
            f"cannot be reconciled against it"
        )
    return Assessment(
        verdict=verdict,
        range_=range_,
        assessed=len(commits),
        quarantined=tuple(quarantined),
        unenforced=tuple(unenforced),
        stale=tuple(stale),
        unassessed=tuple(unassessed),
        reason=reason,
    )
