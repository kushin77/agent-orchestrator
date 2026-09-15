#!/usr/bin/env python3
"""Pre-existing suite reds: attributed by MEASUREMENT, never by a claim (#764).

WHY THIS EXISTS
    ``scripts/merge-gate.sh run`` (the pre-merge contract, issue #29) reports a
    ``tests`` signal that runs ``scripts/run-pytest-suites.sh``. That sweep is RED
    on clean ``origin/master`` itself, so the signal can never be satisfied by any
    lane and no lane could land without a human merging by hand — which is exactly
    the human step the landing driver exists to remove. Measured on
    ``origin/master`` 37f87f9: six declared suites fail there
    (``governance/modules``, ``governance/conformance``, ``governance/lessons``,
    ``integrations/paperclip``, ``integrations/paperclip/reporting``,
    ``telemetry/chat``) with no lane change in play.

WHAT THIS IS, AND WHAT IT IS NOT
    The repo already has a *declaration* surface for this: the ``## Pre-existing
    red`` section of the PR body, enforced at PR time by
    ``scripts/check-pr-contract.sh`` (''a claimed pre-existing red had no evidence
    standard''). That mechanism is a **prose reproduction** — a ``Reproduce:`` line
    plus a fenced block — and its enforcement is a shape test, because a PR body
    cannot be executed. So it is consumed here as the human-facing *declaration*,
    and it is deliberately NOT the thing that grants a waiver: "it fails upstream"
    asserted by hand is a claim, and a claim is not evidence.

    The grant here is a **measurement**: the lane's failing-suite set is compared
    against the same sweep run on clean ``origin/master``, and a suite is
    *attributable* only when it fails on BOTH. Three consequences, all of them the
    conservative direction:

    * a suite that fails in the lane and PASSES on clean master is
      **lane-caused** — the landing is refused, by name;
    * a suite with no verdict in the lane (a timeout) is never attributable, never
      a pass;
    * a suite that is not in the baseline at all — a newly declared suite, or one
      measured here and not there — is refused immediately. There is no
      grandfathering list to grow: nothing is written down to be extended, because
      the baseline is re-measured against the commit it names.

    This mirrors the repo's own provenanced-baseline discipline
    (``scripts/gate-coverage-baseline.txt``, #603/#698: a closed reason vocabulary,
    provenance per row, and a newly delivered artifact refused rather than
    grandfathered). It departs from it in one way, deliberately: rather than a
    committed row list that a lane could extend, the baseline is a *measurement*
    keyed by the commit it was taken at, so provenance is structural — a baseline
    measured at another commit is stale and is re-measured, not trusted.

CONTRACT-RECORD-FIRST
    :func:`attribute_contract` never looks at a sweep before it has read the
    contract's OWN per-signal record (``.verify/merge-attestation.json``, the
    ``checks`` array ``scripts/merge-gate.sh`` writes). Two reasons:

    * a red attestation with no per-signal record cannot be attributed at all —
      the driver must not guess which signal was red;
    * the ``tests`` signal must be the **only** red one. A red ``verify`` (the
      gate of record) is never attributable, whatever the suite sweep says: the
      grant is about suite reds that exist on master, not about the gate of
      record. That is the property that keeps this from becoming a blanket
      waiver, and it is enforced by re-aggregating the contract's own signal codes
      through the contract's own aggregation
      (:func:`governance.merge.gate.aggregate_exit_codes`), never by re-deciding.

Exit-code contract (the repo's honesty tri-state, issue #28): 0 OK /
1 NOT-OK / 2 CANNOT-ASSESS. "No baseline" and "no verdict" are CANNOT-ASSESS —
never a grant, and never a pass.

Usage:
    python3 -m governance.landing.attribution status --root <dir> [--json]
    python3 -m governance.landing.attribution measure --root <dir> [--rev <rev>] --out <path>
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

from governance.landing.evidence import ATTESTATION_REL, same_commit
from governance.merge.gate import aggregate_exit_codes

#: Where ``scripts/run-pytest-suites.sh`` writes its per-suite record (#29). This
#: is the machine-readable failing set the attribution compares — read, never
#: re-derived from a log, so the comparison cannot be talked around.
SWEEP_REL = Path(".verify") / "test-results.json"

#: The command that produces a sweep. Named once: the baseline is measured with
#: the SAME sweep as the lane, or the comparison would not be like-for-like.
SWEEP_COMMAND = ("bash", "scripts/run-pytest-suites.sh")
SWEEP_COMMAND_TEXT = "bash scripts/run-pytest-suites.sh"

#: The rev the baseline is measured at by default. "Clean master" is the merge
#: target: the failure set the lane would meet if it changed nothing.
DEFAULT_BASELINE_REV = "origin/master"

#: The seam that lets an operator (and the controls) supply a *recorded*
#: clean-master sweep instead of paying for a live one. It changes where the
#: measurement comes from, never whether there is one.
AO_BASELINE_ENV = "AO_LAND_BASELINE_RECORD"

#: The one contract signal this module may re-score. Everything else keeps the
#: contract's own verdict.
SIGNAL_TESTS = "tests"

#: Cache directory (repo-relative) for a measured baseline.
BASELINE_CACHE_DIR = Path(".verify") / "baseline"

STATE_READ = "read"
STATE_ABSENT = "absent"
STATE_UNREADABLE = "unreadable"
STATE_MALFORMED = "malformed"
STATE_EMPTY = "empty"

STATUS_OK = "OK"
STATUS_FAIL = "FAIL"
STATUS_NO_VERDICT = "CANNOT-ASSESS"

CODE_ALREADY_GREEN = "contract-already-green"
CODE_GRANTED = "pre-existing-red-attributed"
CODE_LANE_CAUSED = "lane-caused-suite-red"
CODE_SUITE_NO_VERDICT = "suite-with-no-verdict"
CODE_NO_LANE_RECORD = "no-lane-suite-record"
CODE_STALE_LANE_RECORD = "lane-suite-record-names-another-commit"
CODE_NO_BASELINE = "no-clean-master-baseline"
CODE_NO_CONTRACT_RECORD = "no-contract-signal-record"
CODE_SIGNAL_RED = "signal-red-is-not-attributable"
CODE_TESTS_NO_VERDICT = "tests-signal-has-no-verdict"

EXIT_OK = 0
EXIT_NOT_OK = 1
EXIT_CANNOT_ASSESS = 2

#: The codes that mean "no verdict could be reached" — the honesty tri-state's
#: CANNOT-ASSESS. Everything else is a refusal with real evidence behind it.
CANNOT_ASSESS_CODES = (
    CODE_NO_LANE_RECORD,
    CODE_STALE_LANE_RECORD,
    CODE_NO_BASELINE,
    CODE_NO_CONTRACT_RECORD,
    CODE_TESTS_NO_VERDICT,
)


def _canonical_status(raw: object) -> str:
    """Normalise a sweep row's status onto the tri-state; anything else is no verdict."""
    text = str(raw or "").strip().upper()
    if text == "OK":
        return STATUS_OK
    if text == "FAIL":
        return STATUS_FAIL
    return STATUS_NO_VERDICT


@dataclass(frozen=True)
class SuiteResult:
    """One suite's row from a sweep record."""

    suite: str
    status: str
    rc: int = 1
    detail: str = ""

    def as_dict(self) -> dict:
        return {"suite": self.suite, "status": self.status, "rc": self.rc, "detail": self.detail}


@dataclass(frozen=True)
class Sweep:
    """One read of a per-suite sweep record — the lane's, or clean master's."""

    suites: tuple[SuiteResult, ...] = ()
    sha: str = ""
    state: str = STATE_ABSENT
    source: str = ""
    detail: str = ""

    @property
    def readable(self) -> bool:
        return self.state == STATE_READ

    def failures(self) -> frozenset[str]:
        return frozenset(row.suite for row in self.suites if row.status == STATUS_FAIL)

    def no_verdict(self) -> frozenset[str]:
        return frozenset(row.suite for row in self.suites if row.status == STATUS_NO_VERDICT)

    def as_dict(self) -> dict:
        return {
            "source": self.source,
            "state": self.state,
            "sha": self.sha,
            "suites": len(self.suites),
            "failing": sorted(self.failures()),
            "no_verdict": sorted(self.no_verdict()),
            "detail": self.detail,
        }


def read_sweep(path: Path) -> Sweep:
    """Read a sweep record; never raises on a missing or malformed file.

    A missing record is *data* — "the sweep has not run" — so the caller reports
    it by name rather than treating it as an exception.
    """
    path = Path(path)
    if not path.is_file():
        return Sweep(state=STATE_ABSENT, source=str(path), detail="no such file")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return Sweep(state=STATE_UNREADABLE, source=str(path), detail=f"{type(exc).__name__}: {exc}")
    try:
        payload = json.loads(text)
    except ValueError as exc:
        return Sweep(state=STATE_MALFORMED, source=str(path), detail=f"not valid JSON ({exc})")
    if not isinstance(payload, dict):
        return Sweep(
            state=STATE_MALFORMED,
            source=str(path),
            detail=f"expected a JSON object, found {type(payload).__name__}",
        )
    rows = payload.get("suites")
    if not isinstance(rows, list):
        return Sweep(state=STATE_MALFORMED, source=str(path), detail="no 'suites' array (not a sweep record)")
    suites: list[SuiteResult] = []
    for row in rows:
        if not isinstance(row, dict):
            return Sweep(state=STATE_MALFORMED, source=str(path), detail=f"a row is not an object: {row!r}")
        name = row.get("suite")
        if not isinstance(name, str) or not name.strip():
            return Sweep(state=STATE_MALFORMED, source=str(path), detail=f"a row names no suite: {row!r}")
        rc = row.get("rc")
        suites.append(
            SuiteResult(
                suite=name.strip(),
                status=_canonical_status(row.get("status")),
                rc=rc if isinstance(rc, int) and not isinstance(rc, bool) else 1,
                detail=str(row.get("detail") or ""),
            )
        )
    # Provenance, when this is a record *this* module wrote: the measured commit
    # must be the commit the record itself names. A record whose provenance and
    # content disagree is not evidence.
    measured_sha = payload.get("measured_sha")
    sha = str(payload.get("sha") or "")
    if measured_sha is not None and not same_commit(str(measured_sha), sha):
        return Sweep(
            state=STATE_MALFORMED,
            source=str(path),
            detail=f"provenance mismatch: measured_sha={measured_sha!r} but sha={sha!r}",
        )
    if not suites:
        return Sweep(state=STATE_EMPTY, source=str(path), sha=sha, detail="the record declares no suites")
    return Sweep(suites=tuple(suites), sha=sha, state=STATE_READ, source=str(path))


def _clean_env() -> dict:
    """The measurement's environment: the lane's own identity is stripped.

    A lane's ``AO_FLEET_DIR`` / ``AO_SESSION_ID`` exported into the shell changes
    what sibling suites observe, so the baseline must not inherit them — a
    baseline measured under a lane's ambient state is not a baseline of clean
    master.
    """
    env = dict(os.environ)
    for name in ("AO_FLEET_DIR", "AO_SESSION_ID", "AO_ISSUE", "AO_BRANCH", "AO_WORKTREE"):
        env.pop(name, None)
    return env


def _git(root: Path, *args: str, timeout: int = 120) -> tuple[int, str]:
    """Run git in ``root``; return (rc, combined output). Never raises."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 2, f"{type(exc).__name__}: {exc}"
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def baseline_cache_path(root: Path, sha: str) -> Path:
    """The cache file for a baseline measured at ``sha``.

    The commit is in the NAME, which is what makes staleness structural: a
    baseline measured at another commit is a different file, so it cannot be
    mistaken for this one.
    """
    return Path(root) / BASELINE_CACHE_DIR / f"pytest-{sha[:12]}.json"


def measure_baseline(
    root: Path,
    *,
    rev: str = DEFAULT_BASELINE_REV,
    timeout: Optional[int] = None,
    scratch_parent: Optional[str] = None,
) -> Sweep:
    """Measure the sweep on clean ``rev``, in a scratch worktree of this repo.

    Never raises: every failure mode (an unresolvable rev, a worktree that cannot
    be created, a sweep that produced no record) comes back as a named non-READ
    sweep, so the caller reports CANNOT-ASSESS instead of granting on a missing
    measurement.
    """
    root = Path(root).resolve()
    limit = timeout if timeout is not None else int(os.environ.get("AO_LAND_BASELINE_TIMEOUT", "3600"))

    rc, out = _git(root, "rev-parse", "--verify", f"{rev}^{{commit}}")
    if rc != 0:
        return Sweep(state=STATE_ABSENT, source=f"{rev} (in {root})", detail=f"cannot resolve {rev}: {out.strip()[-200:]}")
    sha = out.strip().splitlines()[0].strip()
    if not sha:
        return Sweep(state=STATE_ABSENT, source=rev, detail=f"git resolved {rev} to an empty sha")

    cache = baseline_cache_path(root, sha)
    cached = read_sweep(cache)
    if cached.readable and same_commit(cached.sha, sha):
        return Sweep(
            suites=cached.suites,
            sha=cached.sha,
            state=STATE_READ,
            source=f"measured at {rev} ({sha[:12]}), cached at {cache}",
        )

    parent = tempfile.mkdtemp(prefix="ao-land-baseline.", dir=scratch_parent or "/tmp")
    tree = Path(parent) / "wt"
    try:
        rc, out = _git(root, "worktree", "add", "--detach", str(tree), sha, timeout=300)
        if rc != 0:
            return Sweep(
                state=STATE_UNREADABLE,
                source=f"{rev} ({sha[:12]})",
                detail=f"git worktree add failed: {out.strip()[-200:]}",
            )
        try:
            proc = subprocess.run(
                list(SWEEP_COMMAND),
                cwd=str(tree),
                env=_clean_env(),
                capture_output=True,
                text=True,
                timeout=limit,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return Sweep(
                state=STATE_UNREADABLE,
                source=f"{rev} ({sha[:12]})",
                detail=f"the baseline sweep exceeded {limit}s — no sweep, no verdict",
            )
        except OSError as exc:
            return Sweep(state=STATE_UNREADABLE, source=f"{rev} ({sha[:12]})", detail=f"{type(exc).__name__}: {exc}")
        record = read_sweep(tree / SWEEP_REL)
        if not record.readable:
            tail = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()[-4:]
            return Sweep(
                state=STATE_UNREADABLE,
                source=f"{rev} ({sha[:12]})",
                detail=f"the baseline sweep wrote no readable record ({record.detail}); rc={proc.returncode}; tail={' | '.join(tail)}",
            )
        write_baseline_record(
            cache,
            record,
            rev=rev,
            sha=sha,
            rc=proc.returncode,
        )
        return Sweep(
            suites=record.suites,
            sha=sha,
            state=STATE_READ,
            source=f"measured at {rev} ({sha[:12]}) in a clean worktree, cached at {cache}",
        )
    finally:
        # No force flag, ever — the driver's no-force property is asserted over
        # this whole package by scripts/check-landing.sh, and it is worth keeping
        # literal. Dropping the scratch tree and pruning the registration is
        # enough, and `prune` only drops entries whose directory is GONE, so a
        # live lane's worktree (whose path exists) is never touched.
        shutil.rmtree(parent, ignore_errors=True)
        _git(root, "worktree", "prune", timeout=300)


def write_baseline_record(path: Path, record: Sweep, *, rev: str, sha: str, rc: int) -> None:
    """Persist a measured baseline WITH its provenance, in the sweep's own shape.

    The file stays a sweep record (``sha`` + ``suites``) so :func:`read_sweep`
    reads it unchanged, and carries the provenance beside it: the rev measured,
    the commit, when, and the command. Provenance is not decoration —
    :func:`read_sweep` refuses a record whose ``measured_sha`` disagrees with its
    own ``sha``, so a hand-edited cache fails as malformed rather than granting.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "gate": "pytest-suites-baseline",
        "rev": rev,
        "sha": sha,
        "measured_sha": record.sha or sha,
        "measured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "command": SWEEP_COMMAND_TEXT,
        "sweep_rc": rc,
        "suites": [row.as_dict() for row in record.suites],
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


@dataclass(frozen=True)
class Attribution:
    """The measured answer: which suite reds are pre-existing, and which are not."""

    code: str = ""
    grant: bool = False
    summary: str = ""
    pre_existing: tuple[str, ...] = ()
    lane_caused: tuple[str, ...] = ()
    unmeasured: tuple[str, ...] = ()
    lane: Optional[Sweep] = None
    baseline: Optional[Sweep] = None
    signals: tuple[tuple[str, int], ...] = ()
    regraded_rc: Optional[int] = None

    @property
    def cannot_assess(self) -> bool:
        return self.code in CANNOT_ASSESS_CODES

    @property
    def rc(self) -> int:
        if self.grant:
            return EXIT_OK
        return EXIT_CANNOT_ASSESS if self.cannot_assess else EXIT_NOT_OK

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "grant": self.grant,
            "summary": self.summary,
            "pre_existing": list(self.pre_existing),
            "lane_caused": list(self.lane_caused),
            "unmeasured": list(self.unmeasured),
            "lane": self.lane.as_dict() if self.lane is not None else None,
            "baseline": self.baseline.as_dict() if self.baseline is not None else None,
            "signals": [{"name": name, "rc": rc} for name, rc in self.signals],
            "regraded_rc": self.regraded_rc,
        }


def attribute_suites(lane: Optional[Sweep], baseline: Optional[Sweep]) -> Attribution:
    """Compare a lane's failing set against clean master's — the measurement.

    A suite is attributable only when it fails in BOTH sweeps. Anything the lane
    fails that master passes is lane-caused and refuses; anything the lane has no
    verdict for is never attributable.
    """
    if lane is None or not lane.readable:
        return Attribution(
            code=CODE_NO_LANE_RECORD,
            summary=f"no readable lane sweep record ({getattr(lane, 'detail', '') or 'not read'}) — nothing to measure",
            lane=lane,
            baseline=baseline,
        )
    if baseline is None or not baseline.readable:
        return Attribution(
            code=CODE_NO_BASELINE,
            summary=(
                "no readable clean-master baseline "
                f"({getattr(baseline, 'detail', '') or 'not measured'}) — a red cannot be attributed "
                "without a measurement of master"
            ),
            lane=lane,
            baseline=baseline,
        )
    pre_existing = tuple(sorted(lane.failures() & baseline.failures()))
    lane_caused = tuple(sorted(lane.failures() - baseline.failures()))
    unmeasured = tuple(sorted(lane.no_verdict()))
    if lane_caused:
        return Attribution(
            code=CODE_LANE_CAUSED,
            summary=(
                f"{len(lane_caused)} suite(s) fail in this lane and PASS on clean master "
                f"({baseline.source}): {', '.join(lane_caused)}"
            ),
            pre_existing=pre_existing,
            lane_caused=lane_caused,
            unmeasured=unmeasured,
            lane=lane,
            baseline=baseline,
        )
    if unmeasured:
        return Attribution(
            code=CODE_SUITE_NO_VERDICT,
            summary=f"{len(unmeasured)} suite(s) reached no verdict and are never attributable: {', '.join(unmeasured)}",
            pre_existing=pre_existing,
            unmeasured=unmeasured,
            lane=lane,
            baseline=baseline,
        )
    return Attribution(
        code=CODE_GRANTED,
        grant=True,
        summary=(
            f"{len(pre_existing)} failing suite(s) are failing identically on clean master "
            f"({baseline.source}): {', '.join(pre_existing) if pre_existing else 'none'}"
        ),
        pre_existing=pre_existing,
        lane=lane,
        baseline=baseline,
    )


def read_contract_signals(path: Path) -> tuple[tuple[str, int], ...]:
    """The contract's OWN per-signal record (the ``checks`` array), or () when absent.

    ``scripts/merge-gate.sh`` writes this array into its attestation; it is what
    lets this module re-score one signal without re-deciding the contract. A
    record with no array is not a record: attributing a red whose signal is
    unknown would be a guess, so the caller refuses.
    """
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    if not isinstance(payload, dict):
        return ()
    rows = payload.get("checks")
    if not isinstance(rows, list):
        return ()
    signals: list[tuple[str, int]] = []
    for row in rows:
        if not isinstance(row, dict):
            return ()
        name = row.get("name")
        rc = row.get("rc")
        if not isinstance(name, str) or not name.strip():
            return ()
        if not isinstance(rc, int) or isinstance(rc, bool):
            return ()
        signals.append((name.strip(), rc))
    return tuple(signals)


def attribute_lane(
    root: Path,
    *,
    commit: str = "",
    lane_record: Optional[Path] = None,
    baseline: Optional[Sweep] = None,
    baseline_file: Optional[Path] = None,
    baseline_rev: str = DEFAULT_BASELINE_REV,
    timeout: Optional[int] = None,
) -> Attribution:
    """Measure the lane's failing set against clean master — no contract record needed.

    This is the *declaration* half: it answers "which of these suites are already
    failing on master?" and is what a PR body can state honestly before the
    contract has re-run. It grants nothing on its own — a landing still needs the
    contract's own signal record (:func:`attribute_contract`), because only that
    says the ``tests`` signal is the only red one.
    """
    root = Path(root)
    lane = read_sweep(Path(lane_record) if lane_record is not None else root / SWEEP_REL)
    if lane.readable and commit and not same_commit(lane.sha, commit):
        return Attribution(
            code=CODE_STALE_LANE_RECORD,
            summary=(
                f"the lane's sweep record names {lane.sha or 'no commit'}, but the commit being merged is "
                f"{commit} — a measurement of another commit is not evidence for this one"
            ),
            lane=lane,
            baseline=baseline,
        )

    measured = baseline
    if measured is None:
        override = baseline_file
        if override is None and os.environ.get(AO_BASELINE_ENV):
            override = Path(os.environ[AO_BASELINE_ENV])
        measured = read_sweep(override) if override is not None else measure_baseline(root, rev=baseline_rev, timeout=timeout)
    return attribute_suites(lane, measured)


def attribute_contract(
    root: Path,
    *,
    commit: str = "",
    lane_record: Optional[Path] = None,
    baseline: Optional[Sweep] = None,
    baseline_file: Optional[Path] = None,
    baseline_rev: str = DEFAULT_BASELINE_REV,
    timeout: Optional[int] = None,
) -> Attribution:
    """Decide whether the contract's red is entirely measured pre-existing.

    The order is the conservative one, cheapest check first:

    1. the contract's own per-signal record must exist — without it nothing is
       attributable;
    2. ``tests`` must be the ONLY red signal (a red ``verify`` is never
       attributable);
    3. the lane's sweep record must name the commit being landed;
    4. the lane's failing set must be a subset of clean master's failing set.
    """
    root = Path(root)
    attestation_path = root / ATTESTATION_REL
    signals = read_contract_signals(attestation_path)
    if not signals:
        return Attribution(
            code=CODE_NO_CONTRACT_RECORD,
            summary=(
                f"{attestation_path} carries no per-signal 'checks' record — a red whose signal is "
                "unknown is not attributable"
            ),
        )
    non_ok = tuple(name for name, rc in signals if rc != 0)
    if not non_ok:
        return Attribution(
            code=CODE_ALREADY_GREEN,
            grant=True,
            summary="the contract's own record is green — no signal needed attribution",
            signals=signals,
            regraded_rc=aggregate_exit_codes([rc for _, rc in signals]),
        )
    if non_ok != (SIGNAL_TESTS,):
        return Attribution(
            code=CODE_SIGNAL_RED,
            summary=(
                f"the contract is red on signal(s) {', '.join(non_ok)} — only '{SIGNAL_TESTS}' can be "
                "attributed to clean master, and the gate of record is never one of them"
            ),
            signals=signals,
        )
    tests_rc = dict(signals)[SIGNAL_TESTS]
    if tests_rc != 1:
        return Attribution(
            code=CODE_TESTS_NO_VERDICT,
            summary=(
                f"the '{SIGNAL_TESTS}' signal exited {tests_rc}: it reached no verdict, and no verdict is "
                "never a pass"
            ),
            signals=signals,
        )

    result = attribute_lane(
        root,
        commit=commit,
        lane_record=lane_record,
        baseline=baseline,
        baseline_file=baseline_file,
        baseline_rev=baseline_rev,
        timeout=timeout,
    )
    if not result.grant:
        return replace(result, signals=signals)
    # Re-score ONLY the 'tests' signal through the contract's OWN aggregation
    # (governance/merge/gate.py mirrors scripts/merge-gate.sh). A narrowing that
    # re-decided the aggregate itself would be a second, silent contract.
    regraded = aggregate_exit_codes([0 if name == SIGNAL_TESTS else rc for name, rc in signals])
    if regraded != 0:
        return replace(
            result,
            code=CODE_SIGNAL_RED,
            grant=False,
            regraded_rc=None,
            summary=f"re-aggregating the contract's own signals is still {regraded}, not green",
            signals=signals,
        )
    return replace(result, signals=signals, regraded_rc=regraded)


def format_report(result: Attribution) -> str:
    """The human-facing report: every pre-existing red is NAMED, never dropped."""
    lines = [f"attribution: {result.code} — {result.summary}"]
    if result.pre_existing:
        lines.append(f"  pre-existing (fails identically on clean master): {', '.join(result.pre_existing)}")
    if result.lane_caused:
        lines.append(f"  LANE-CAUSED (fails here, passes on clean master): {', '.join(result.lane_caused)}")
    if result.unmeasured:
        lines.append(f"  no verdict: {', '.join(result.unmeasured)}")
    if result.lane is not None and result.lane.readable:
        lines.append(f"  lane sweep: {result.lane.source} @ {result.lane.sha[:12] or 'unnamed'}")
    if result.baseline is not None and result.baseline.readable:
        lines.append(f"  baseline: {result.baseline.source}")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="governance.landing.attribution", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    status = sub.add_parser("status", help="attribute the contract's red against clean master")
    status.add_argument("--root", default=".", help="the lane's repository root")
    status.add_argument("--commit", default="", help="the commit being landed (the lane's sweep must name it)")
    status.add_argument("--lane-record", default="", help=f"the lane sweep record (default: <root>/{SWEEP_REL})")
    status.add_argument("--baseline", default="", help="a recorded clean-master sweep (default: measure it)")
    status.add_argument("--baseline-rev", default=DEFAULT_BASELINE_REV, help="the rev to measure the baseline at")
    status.add_argument("--timeout", type=int, default=None, help="seconds allowed for the baseline sweep")
    status.add_argument("--json", action="store_true", help="emit the machine-readable attribution")

    measure = sub.add_parser("measure", help="measure clean master's sweep and record it")
    measure.add_argument("--root", default=".", help="the repository root")
    measure.add_argument("--rev", default=DEFAULT_BASELINE_REV, help="the rev to measure")
    measure.add_argument("--out", default="", help="where to record it (default: the commit-keyed cache)")
    measure.add_argument("--timeout", type=int, default=None, help="seconds allowed for the sweep")

    args = parser.parse_args(argv)
    if args.cmd == "status":
        result = attribute_contract(
            Path(args.root),
            commit=args.commit,
            lane_record=Path(args.lane_record) if args.lane_record else None,
            baseline_file=Path(args.baseline) if args.baseline else None,
            baseline_rev=args.baseline_rev,
            timeout=args.timeout,
        )
        print(json.dumps(result.as_dict(), indent=2, sort_keys=True) if args.json else format_report(result))
        return result.rc

    measured = measure_baseline(Path(args.root), rev=args.rev, timeout=args.timeout)
    if not measured.readable:
        print(f"measure: CANNOT-ASSESS — {measured.detail}", file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    out = Path(args.out) if args.out else baseline_cache_path(Path(args.root), measured.sha)
    write_baseline_record(out, measured, rev=args.rev, sha=measured.sha, rc=0)
    print(f"measure: OK — {len(measured.suites)} suite(s) measured at {args.rev} ({measured.sha[:12]}); failing: "
          f"{', '.join(sorted(measured.failures())) or 'none'}")
    print(f"  recorded: {out}")
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - exercised through the CLI
    raise SystemExit(main())
