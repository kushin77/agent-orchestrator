"""Lane-base drift preflight — the claim-path half of issue #2046.

---knowledge---
module_id: governance.isolation.lane_drift
system: governance
app: isolation
solution_class: class
patterns: [adapter, one-implementation, preflight-report]
derives_from: scripts/check-lane-base-drift.sh
owner_sme: qa-sme
tier: L1
interfaces: [report, PREDICATE_SCRIPT, DRIFT_TIMEOUT_SECONDS]
invariants: "the claim path REPORTS drift, never blocks on it; the predicate lives once, in scripts/check-lane-base-drift.sh"
gotchas: "the predicate's --lane verb is a pure probe (mode dispatch precedes its own provoked controls), so invoking it here cannot recurse; a hung predicate must not hang a lane-open"
related: ["#2046", "#740", "#1642"]
do_not_duplicate: scripts/check-lane-collision.sh
---knowledge---

``scripts/check-lane-base-drift.sh`` (issue #2046) is the ONE implementation of
the drift predicate: for a lane branch cut from a fork point, intersect the
lane's changed files with what ``origin/master`` changed since that fork point.
The gate refuses by name in ``make verify``; the live-set scan is advisory; and
this module is the adapter the **lane-open path** uses to ask the same predicate
the same question at the moment it is cheapest to ask — before a line of code is
written, not after a PR exists.

This is deliberately the same shape as ``governance/isolation/trailer.py``
(issue #288): the rule lives once in the script, and this module is the adapter
that runs it — never a second implementation. Two implementations of one rule
silently disagree, and the disagreement stays invisible until a real lane slips
through the weaker one.

The contract here is REPORT, never block: the lane-open path surfaces the
intersection in its payload and stderr as a named finding, but provisioning
still succeeds — a drift is a *candidate* duplicate or a rebase need, decided by
a reader, and the refusing verb stays the gate's (``make verify`` /
``--scan``). A preflight that refused would strand every rebase-needed lane
behind a red it cannot clear at open time, which is not the defect this exists
for.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The one implementation of the drift predicate (issue #2046).
PREDICATE_SCRIPT = REPO_ROOT / "scripts" / "check-lane-base-drift.sh"

#: A lane-open must not hang on a slow predicate. The probe's measured cost is
#: sub-second on this box (one merge-base + two diffs + one log per drifted
#: file); 30s is an order of magnitude of headroom, same spirit as
#: ``trailer.py``'s ``_TIMEOUT_SECONDS``.
DRIFT_TIMEOUT_SECONDS = 30

#: ``lane-base-drift: <file> changed on master at <sha> since issue-<n> forked — <subject>``
_FINDING_RE = re.compile(
    r"^lane-base-drift: (?P<file>\S+) changed on master at (?P<sha>\S+) "
    r"since issue-(?P<issue>\d+) forked"
)


@dataclass(frozen=True)
class DriftFinding:
    """One file a lane's set shares with post-fork master changes."""

    file: str
    master_commit: str
    issue: int


@dataclass(frozen=True)
class DriftReport:
    """The preflight answer for one lane.

    ``status`` is one of ``clean`` (no intersection), ``drift`` (the lane's
    files intersect post-fork master changes — reported, never blocking),
    ``cannot-assess`` (the predicate could not resolve the fork point or run at
    all — named, never silently clean), or ``absent`` (the predicate script is
    not installed in this checkout).
    """

    status: str
    findings: tuple[DriftFinding, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status == "clean"


def report(
    issue: int,
    main: Path | str,
    *,
    timeout: int = DRIFT_TIMEOUT_SECONDS,
    predicate: Path | str = PREDICATE_SCRIPT,
) -> DriftReport:
    """Ask the one predicate about one lane's base drift.

    Runs ``check-lane-base-drift.sh --lane <n>`` in the repository at ``main``
    and parses its verdict. Every non-clean answer is reported with its
    findings; no answer here ever raises for a drift — a drift is a candidate
    duplicate, decided by a reader at merge time, not at open time.
    """
    script = Path(predicate)
    if not script.exists():
        return DriftReport(status="absent")
    # The predicate's --lane verb measures the repository it is told about via
    # its documented env seam (AO_LANE_DRIFT_ROOT / _MASTER / _BRANCH — the
    # same seam its own mutation control uses), because the verb otherwise
    # resolves the repo from the SCRIPT'S OWN location, which is this
    # checkout, not the caller's fixture. `main` names the repository; the
    # master ref is whatever that repository's default branch is named —
    # resolved, never assumed `origin/master` (a fixture has no remote).
    env = dict(os.environ)
    env["AO_LANE_DRIFT_ROOT"] = str(main)
    # The fixture convention (the script's own controls) names the default
    # branch `master`; a real checkout may carry `origin/master`. Resolve the
    # best available: origin/master when it exists, else master, else HEAD.
    probe = subprocess.run(
        ["git", "-C", str(main), "rev-parse", "--verify", "-q", "origin/master"],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if probe.returncode == 0 and probe.stdout.strip():
        env["AO_LANE_DRIFT_MASTER"] = "origin/master"
    else:
        probe = subprocess.run(
            ["git", "-C", str(main), "rev-parse", "--verify", "-q", "master"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if probe.returncode == 0 and probe.stdout.strip():
            env["AO_LANE_DRIFT_MASTER"] = "master"
        # else: leave unset — the predicate defaults to origin/master and will
        # answer CANNOT-ASSESS, which is the honest answer for a repo whose
        # default branch cannot be resolved.
    try:
        proc = subprocess.run(
            ["bash", str(script), "--lane", str(issue)],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(main),
            env=env,
        )
    except subprocess.TimeoutExpired:
        return DriftReport(status="cannot-assess")
    if proc.returncode == 0:
        return DriftReport(status="clean")
    if proc.returncode == 2:
        return DriftReport(status="cannot-assess")
    # rc 1: the refusal — parse the named findings so the caller can surface
    # them verbatim (file, master commit, subject) rather than a bare "drift".
    findings = tuple(
        DriftFinding(file=m.group("file"), master_commit=m.group("sha"), issue=int(m.group("issue")))
        for m in (_FINDING_RE.match(line) for line in proc.stderr.splitlines())
        if m
    )
    return DriftReport(status="drift", findings=findings)
