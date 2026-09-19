"""verify.py — the PR runner's verify transport (issue #1343).

One head, one verify: fetch under a lock with explicit refspecs, materialise a
DETACHED scratch worktree and HOLD it for the whole run, run
`bash scripts/verify.sh verify` inside it, publish `ao/gate-of-record` through
`scripts/gate-status.sh post` (never for a PARKED run), record a local marker
and a ledger row, and remove the worktree ourselves.

THE LESSONS THIS FILE ENCODES (each a named control in tests/test_transports.py)
  6. `scripts/prune-worktrees.sh` reaps a detached worktree whose content is
     landed on master (#1335) unless a live process holds it — cwd OR an open
     fd under it. The prototype's worktrees were reaped mid-verify. So
     `HeldWorktree` opens an fd INSIDE the worktree before the run and keeps it
     until the run ends, then removes the worktree itself (`git worktree
     remove --force`) — never leaving one the reaper has to guess about.
  8. Two `git fetch`es on one clone race on `cannot lock ref`. Every fetch goes
     through `fetch_lock()` (an flock on `.fleet/runner/fetch.lock`) and names
     EXPLICIT refspecs, so `refs/remotes/origin/master` exists on a detached
     checkout (#1332's fix) instead of only FETCH_HEAD.
  9. Every outcome is a ledger row (`.fleet/runner/ledger.jsonl`), so `status`
     answers from the record, not from memory.
  10. The poster is `scripts/gate-status.sh`, whose context equals the one
     branch protection requires; a PARKED rc (10/11) is not a gate outcome
     (the mapper refuses it) and is never posted.
  11. A red recorded as `rc 1` and nothing else is NOT evidence (issue #1384).
     The worktree is removed when the run ends, so `.verify/verify.log` and the
     gate's own attestation die with it and the failing check's NAME becomes
     unknowable without re-running 15 minutes of gate by hand. Every verify
     therefore KEEPS a BOUNDED transcript of itself (`.fleet/runner/logs/
     <pr>-<sha>.log`) and records the failing check names + the gate's own
     summary line in the marker and the ledger row.

The published DESCRIPTION is no longer a fixed string per rc (issue #1407): a
red now carries the name of the FIRST failing check the run recorded, so the PR
page says WHY without anyone opening the build log. The detail is a SUFFIX the
poster appends to a description whose outcome is still `--rc`'s and nothing
else, so no detail can manufacture a pass on a CANNOT-ASSESS. It is offered to
the poster ONLY when the poster declares that it takes one, because the seam is
injected and a producer that predates the detail is still a valid producer.

All transports are injected: `git`, `sh` and `post_status` are callables, so
the tests drive this module with fakes and no test ever touches the real repo.
"""

from __future__ import annotations

import fcntl
import inspect
import json
import os
import re
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator

from fleet.runner.evidence import state_of_verify_rc, write_local_marker
from fleet.runner.model import CANNOT_ASSESS, PARKED_RCS

OK, NOT_OK, CANNOT_ASSESS_RC = 0, 1, 2

#: The explicit refspecs a fetch names (lesson 8). `{pr}` is substituted.
MASTER_REFSPEC = "+refs/heads/master:refs/remotes/origin/master"
PR_REFSPEC = "+refs/pull/{pr}/head:refs/remotes/origin/pr/{pr}"

#: The file a held worktree keeps open for the whole run (lesson 6).
HOLD_FILE = ".ao-runner-held"

# --- evidence bounds (lesson 11, issue #1384) ----------------------------------
#: A red recorded as `rc 1` and nothing else cannot be diagnosed or contested:
#: the worktree it ran in is removed, so the failing check's NAME is unknowable
#: without re-running the whole gate by hand. Every verify therefore keeps a
#: bounded transcript of itself beside its rc, and the names of the checks that
#: failed ride in the marker and the ledger row.
#:
#: THE BOUND, stated once and enforced in `_tail`/`prune_evidence_logs`:
#:   * the kept transcript is the LAST `EVIDENCE_LOG_MAX_BYTES` bytes of the run
#:     — the verdict, the skip-ratchet note and the failing check's own output
#:     are what a reader needs, and the gate prints its summary LAST, so a tail
#:     always carries it; the number of dropped bytes is written in the header;
#:   * the log directory keeps only the newest `EVIDENCE_LOG_KEEP` files;
#:   * at most `MAX_FAILING_CHECKS` names and `MAX_SUMMARY_CHARS` characters of
#:     the summary line ride in the marker + ledger.
#: `.fleet/runner/` is runtime state (gitignored) that outlives the process, so
#: an unbounded tail is its own defect: the rung verifies every open head, every
#: few minutes, forever.
EVIDENCE_LOG_MAX_BYTES = 256 * 1024
EVIDENCE_LOG_KEEP = 120
MAX_FAILING_CHECKS = 10
MAX_SUMMARY_CHARS = 240
#: Where a verify's transcript is kept, relative to the runner dir.
EVIDENCE_LOG_DIR = "logs"

#: The generated root and artifacts `scripts/verify.sh` writes INSIDE a verified
#: worktree — and which die with it, which is why they are read before removal.
VERIFY_DIR = ".verify"
ATTESTATION_NAME = "attestation.json"
TRANSCRIPT_NAME = "verify.log"

#: The summary line, taken from the END of the transcript: `verify:` also prefixes
#: the skip ratchet's own note earlier in the run, and the composite's summary is
#: printed last.
SUMMARY_RE = re.compile(r"^verify: (PASS|FAIL|CANNOT-ASSESS|PARKED)\b")
#: A failing check line (`<name>: FAIL` / `<name>: NOT-OK`) — the FALLBACK for a
#: run whose attestation is absent (a venue-invalid run writes none). The
#: attestation is preferred: it names checks from the gate's own record instead of
#: from a line shape that merely looks like one.
FAIL_LINE_RE = re.compile(r"^(?P<name>[a-z][a-z0-9]*(?:-[a-z0-9]+)*): (?P<verdict>FAIL|NOT-OK)\b")
#: Words that prefix the composite's OWN lines, never a check name.
NOT_CHECKS = frozenset({"verify", "gate", "attestation", "make"})


@dataclass(frozen=True)
class Result:
    """What a transport call returned."""

    rc: int
    out: str = ""
    err: str = ""

    @property
    def ok(self) -> bool:
        return self.rc == 0


Command = Callable[..., Result]


def real_command(binary: str) -> Command:
    """A subprocess-backed transport for `binary` (git, gh, gcloud, bash...)."""

    def run(argv: list[str], *, cwd: Path | str | None = None, env: dict | None = None, timeout: float | None = None) -> Result:
        merged = dict(os.environ)
        if env:
            merged.update(env)
        try:
            proc = subprocess.run(
                [binary, *argv],
                cwd=str(cwd) if cwd else None,
                env=merged,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError:
            return Result(127, "", f"{binary}: not found")
        except subprocess.TimeoutExpired:
            return Result(124, "", f"{binary}: timed out after {timeout}s")
        return Result(proc.returncode, proc.stdout, proc.stderr)

    return run


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- the kept transcript (lesson 11, issue #1384) -------------------------------
def _tail(text: str, cap: int) -> tuple[str, int]:
    """The LAST `cap` bytes of `text`, and how many bytes were dropped.

    Bytes, not lines: the bound has to hold for a transcript with one enormous
    line (a pytest failure dump) exactly as it holds for a long one. A split
    multi-byte character at the boundary is decoded with `errors="replace"` — a
    transcript is evidence, and mangling one byte of it at the cut is honest
    where silently keeping more than the bound is not.

    `cap <= 0` keeps NOTHING, and is spelled out because it is not what slicing
    says: `data[-0:]` is `data[0:]`, i.e. every byte (measured — the gate's
    MUTANT-5, which sets the bound to 0, was NOT caught until this arm existed;
    a bound of zero that keeps everything is a bound that fails OPEN).
    """
    data = text.encode("utf-8", errors="replace")
    if cap <= 0:
        return "", len(data)
    if len(data) <= cap:
        return data.decode("utf-8", errors="replace"), 0
    return data[-cap:].decode("utf-8", errors="replace"), len(data) - cap


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def prune_evidence_logs(log_dir: Path, keep: int = EVIDENCE_LOG_KEEP) -> list[str]:
    """Keep only the newest `keep` kept transcripts; returns the names removed.

    The directory is retained ACROSS cycles (that is the point — a red has to
    outlive the process that found it), so it is a bound like any other: a long
    night of verifies must not fill the box. A file that cannot be stat'ed or
    removed is skipped rather than raising — pruning is housekeeping, and it is
    never allowed to turn a verify's outcome into a crash.
    """
    try:
        files = [path for path in log_dir.iterdir() if path.is_file()]
    except OSError:
        return []
    if len(files) <= keep:
        return []
    files.sort(key=lambda path: (_mtime(path), path.name))
    removed: list[str] = []
    for path in files[: len(files) - keep]:
        try:
            path.unlink()
            removed.append(path.name)
        except OSError:
            continue
    return removed


def parse_verify_evidence(stdout: str, stderr: str = "", attestation: dict | None = None) -> dict:
    """What a red needs in order to be diagnosed later: the gate's own summary
    line, and the NAMES of the checks that failed (bounded).

    The attestation (`.verify/attestation.json`, written by `scripts/verify.sh`)
    is preferred — it carries a machine-readable verdict per check, so the names
    come from the gate's own record. The transcript is the fallback (a
    venue-invalid run writes no attestation), and WHICH source was used is
    recorded by name so a reader never has to guess the provenance.

    An empty `failing_checks` is a real answer, not a missing one: the measured
    #1405 shape is `verify: FAIL (0 of 215 checks failed ... skip ratchet FAIL: 1
    unnamed skip)` — the run is red and NO check failed. The summary line carries
    that, and the planner renders the empty set `none-named` rather than blank.
    """
    transcript = (stdout or "") + ("\n" + stderr if stderr else "")
    summary = ""
    for line in transcript.splitlines():
        if SUMMARY_RE.match(line.strip()):
            summary = line.strip()[:MAX_SUMMARY_CHARS]

    failing: list[str] = []
    source = "none"
    notes: list[str] = []
    checks = attestation.get("checks") if isinstance(attestation, dict) else None
    if isinstance(checks, list):
        source = "attestation"
        for check in checks:
            if not isinstance(check, dict) or str(check.get("status")) != "FAIL":
                continue
            name = str(check.get("name") or "").strip()
            if name and name not in failing:
                failing.append(name)
    else:
        # A GAP, named: the gate's own record could not be read (a venue-invalid
        # run writes none at all), so the names below come from line shapes.
        notes.append("attestation-has-no-checks" if isinstance(attestation, dict) else "attestation-absent")

    if not failing:
        for line in transcript.splitlines():
            match = FAIL_LINE_RE.match(line.strip())
            if not match or match.group("name") in NOT_CHECKS:
                continue
            if match.group("name") not in failing:
                failing.append(match.group("name"))
        if failing and source == "none":
            source = "transcript"

    # The note names a GAP in the evidence, never a normal outcome: a green run
    # legitimately names no failing check, and an attestation that assessed and
    # found nothing failing is silent about it — `failing_checks: []` says so.
    total = len(failing)
    kept = failing[:MAX_FAILING_CHECKS]
    if total > len(kept):
        notes.append(f"failing-checks-truncated:{total}")
    if not total and source == "none":
        notes.append("transcript-empty" if not transcript.strip() else "no-check-named")
    return {
        "verify_summary": summary,
        "failing_checks": kept,
        "failing_source": source,
        "failing_note": ",".join(notes),
        "failing_total": total,
    }


def retain_verify_evidence(*, runner_dir: Path, pr: int, sha: str, worktree: Path, result: Result) -> dict:
    """Keep a bounded transcript of ONE verify and name what it can about a red.

    Called INSIDE the held worktree: `.verify/` is removed with it, so the
    attestation and the transcript are read here or lost. Writes
    `.fleet/runner/logs/<pr>-<sha>.log` (the last `EVIDENCE_LOG_MAX_BYTES` bytes
    of the transcript behind a header naming the bound and the dropped byte
    count), prunes the directory to its newest `EVIDENCE_LOG_KEEP` files, and
    returns the record the marker + ledger carry.

    Keyed by (pr, sha) like every other piece of evidence: a pushed head writes
    its OWN file and can never inherit the previous head's tail.

    A missing or unreadable artifact is a NAMED note (`attestation-absent`,
    `attestation-unreadable:<why>`, `transcript-absent`), never a silent green.
    """
    runner_dir = Path(runner_dir)
    verify_dir = Path(worktree) / VERIFY_DIR

    attestation: dict | None = None
    try:
        loaded = json.loads((verify_dir / ATTESTATION_NAME).read_text(encoding="utf-8"))
        attestation = loaded if isinstance(loaded, dict) else None
    except OSError:
        pass
    except ValueError:
        attestation = None

    transcript = ""
    try:
        transcript = (verify_dir / TRANSCRIPT_NAME).read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    if not transcript:
        # The tee'd stdout/stderr of the run itself: the same bytes, in memory.
        transcript = (result.out or "") + ("\n" + result.err if result.err else "")

    parsed = parse_verify_evidence(transcript, attestation=attestation)
    kept, dropped = _tail(transcript, EVIDENCE_LOG_MAX_BYTES)

    log_dir = runner_dir / EVIDENCE_LOG_DIR
    log_path = log_dir / f"{pr}-{sha}.log"
    # The header is PROVENANCE, never evidence: it names what the file is and how
    # it was bounded. The summary line and the failing checks stay in the
    # transcript below it, so a truncated tail can never be confused with a run
    # whose names were dropped.
    header = (
        f"# verify evidence — pr {pr} head {sha} rc {result.rc} kept {now_iso()}\n"
        f"# bound: the LAST {EVIDENCE_LOG_MAX_BYTES} byte(s) of the transcript; {dropped} byte(s) dropped\n"
        f"# names: from {parsed['failing_source'] or 'none'}"
        f"{'; ' + parsed['failing_note'] if parsed['failing_note'] else ''}\n"
        f"# --- transcript tail ---\n"
    )
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path.write_text(header + kept, encoding="utf-8")
    prune_evidence_logs(log_dir)

    return {
        "failing_checks": parsed["failing_checks"],
        "failing_total": parsed["failing_total"],
        "failing_source": parsed["failing_source"],
        "verify_summary": parsed["verify_summary"],
        "evidence_log": f"{EVIDENCE_LOG_DIR}/{log_path.name}",
        "evidence_bytes": len((header + kept).encode("utf-8", errors="replace")),
        "evidence_truncated": dropped > 0,
        "evidence_note": parsed["failing_note"],
    }


# --- the ledger (lesson 9) -----------------------------------------------------
class Ledger:
    """Append-only JSONL: verify results, posts, merges, refusals — by name."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def record(self, event: str, **fields) -> dict:
        row = {"at": now_iso(), "event": event, **fields}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
        return row

    def rows(self) -> list[dict]:
        if not self.path.is_file():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                out.append({"event": "unreadable-row", "raw": line[:200]})
        return out


# --- the fetch lock (lesson 8) -------------------------------------------------
@contextmanager
def fetch_lock(lock_path: Path) -> Iterator[None]:
    """Serialise every `git fetch` on this clone. Blocking flock, released on exit."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def fetch_refs(git: Command, *, repo: Path, pr: int, lock_path: Path, hooks: dict | None = None) -> Result:
    """Fetch master + the PR head with EXPLICIT refspecs, under the fetch lock."""
    refspecs = [MASTER_REFSPEC, PR_REFSPEC.format(pr=pr)]
    with fetch_lock(lock_path):
        if hooks and "in_lock" in hooks:
            hooks["in_lock"]()
        return git(["fetch", "--quiet", "origin", *refspecs], cwd=repo)


# --- the held worktree (lesson 6) ----------------------------------------------
class HeldWorktree:
    """A detached scratch worktree that is HELD (open fd) for its whole life
    and removed by us on exit — success, failure or exception."""

    def __init__(self, git: Command, *, repo: Path, path: Path, sha: str):
        self.git = git
        self.repo = Path(repo)
        self.path = Path(path)
        self.sha = sha
        self._fd: int | None = None
        self.removed = False
        self.add_result: Result | None = None

    def __enter__(self) -> "HeldWorktree":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            # A leftover from a killed run: ours to remove, not the reaper's.
            self.git(["worktree", "remove", "--force", str(self.path)], cwd=self.repo)
        self.add_result = self.git(["worktree", "add", "--detach", str(self.path), self.sha], cwd=self.repo)
        if not self.add_result.ok:
            raise RuntimeError(f"worktree-add-failed:{self.sha[:12]}:{self.add_result.err.strip()[:200]}")
        self.path.mkdir(parents=True, exist_ok=True)  # a fake git may not create it
        self._fd = os.open(str(self.path / HOLD_FILE), os.O_RDWR | os.O_CREAT, 0o644)
        return self

    @property
    def held(self) -> bool:
        return self._fd is not None

    def __exit__(self, *exc) -> None:
        try:
            if self._fd is not None:
                os.close(self._fd)
                self._fd = None
        finally:
            self.git(["worktree", "remove", "--force", str(self.path)], cwd=self.repo)
            self.git(["worktree", "prune"], cwd=self.repo)
            self.removed = True


# --- the verify itself -----------------------------------------------------------
@dataclass(frozen=True)
class VerifyOutcome:
    pr: int
    sha: str
    rc: int
    state: str
    posted: bool
    worktree_removed: bool
    detail: str = ""
    marker: str = ""


def gate_rc_of(rc: int) -> int | None:
    """The gate-of-record rc to POST for a verify exit code, or None (not posted).

    0/1/2 map straight through. PARKED (10/11) is not an outcome and is never
    posted (the mapper would refuse it). Any other rc is a run that could not
    reach a verdict -> CANNOT-ASSESS (2), published as `error`, never a pass.
    """
    if rc in PARKED_RCS:
        return None
    if rc in (0, 1, 2):
        return rc
    return 2


def run_verify(
    pr: int,
    sha: str,
    *,
    repo: Path,
    runner_dir: Path,
    git: Command,
    sh: Command,
    post_status: Callable[..., Result],
    ledger: Ledger,
    now: Callable[[], str] = now_iso,
    timeout: float | None = None,
) -> VerifyOutcome:
    """Verify one head end to end. Every exit path writes a ledger row."""
    runner_dir = Path(runner_dir)
    lock_path = runner_dir / "fetch.lock"
    worktree = runner_dir / "worktrees" / f"{pr}-{sha[:12]}"
    marker_dir = runner_dir / "local-green"

    fetched = fetch_refs(git, repo=repo, pr=pr, lock_path=lock_path)
    if not fetched.ok:
        detail = f"fetch-failed:{pr}:{fetched.err.strip()[:200]}"
        ledger.record("verify", pr=pr, sha=sha, rc=None, state=CANNOT_ASSESS, detail=detail, posted=False)
        return VerifyOutcome(pr, sha, CANNOT_ASSESS_RC, CANNOT_ASSESS, False, True, detail)

    tip_result = git(["rev-parse", "refs/remotes/origin/master"], cwd=repo)
    master_tip = tip_result.out.strip() if tip_result.ok else None

    removed = False
    evidence: dict = {}
    try:
        with HeldWorktree(git, repo=repo, path=worktree, sha=sha) as held:
            result = sh(["bash", "scripts/verify.sh", "verify"], cwd=held.path, timeout=timeout)
            rc = int(result.rc)
            # INSIDE the worktree, because `.verify/` is removed with it (lesson
            # 11, #1384): after this line the failing check's name is gone.
            try:
                evidence = retain_verify_evidence(runner_dir=runner_dir, pr=pr, sha=sha, worktree=held.path, result=result)
            except OSError as exc:
                # Retaining evidence is housekeeping; it is never allowed to lose
                # the verdict or crash the cycle. A tail that could not be kept is
                # NAMED — a missing tail is never a green and never a silence.
                evidence = {"evidence_note": f"evidence-retain-failed:{type(exc).__name__}"}
        removed = held.removed
    except RuntimeError as exc:
        detail = str(exc)
        ledger.record("verify", pr=pr, sha=sha, rc=None, state=CANNOT_ASSESS, detail=detail, posted=False)
        return VerifyOutcome(pr, sha, CANNOT_ASSESS_RC, CANNOT_ASSESS, False, True, detail)

    state = state_of_verify_rc(rc)
    detail = f"verify rc {rc} ({state})"
    failing = [str(name) for name in (evidence.get("failing_checks") or [])]
    gate_rc = gate_rc_of(rc)
    posted = False
    if gate_rc is None:
        ledger.record("post-skipped", pr=pr, sha=sha, rc=rc, reason=f"parked:{rc}")
    else:
        # WHY it redded, for the PR page: the first check this run recorded as
        # failing (#1407). `gate_rc` is decided above and passed through
        # unchanged, so the detail reaches the description and nothing else. A
        # green names no failing check, and is posted exactly as it always was.
        post = post_with_detail(post_status, sha, gate_rc, failing[0] if failing else "")
        posted = post.ok
        ledger.record("post", pr=pr, sha=sha, rc=gate_rc, ok=post.ok, detail=(post.out or post.err).strip()[:200])

    # A head verify is head-level evidence: `base_tip` stays None so the
    # merged-tree seam judges it at merge time (lesson 3). The tip is recorded
    # in the detail for the reader.
    evidence_fields = {
        "failing_checks": failing,
        "failing_total": int(evidence.get("failing_total") or 0),
        "verify_summary": str(evidence.get("verify_summary") or ""),
        "evidence_log": str(evidence.get("evidence_log") or ""),
        "evidence_bytes": int(evidence.get("evidence_bytes") or 0),
        "evidence_truncated": bool(evidence.get("evidence_truncated")),
        "evidence_source": str(evidence.get("failing_source") or ""),
        "evidence_note": str(evidence.get("evidence_note") or ""),
    }
    marker = write_local_marker(
        marker_dir,
        pr,
        sha,
        rc,
        base_tip=None,
        detail=f"{detail}; master was {(master_tip or '?')[:12]}",
        recorded_at=now(),
        failing_checks=failing,
        verify_summary=evidence_fields["verify_summary"],
        evidence_log=evidence_fields["evidence_log"],
    )
    ledger.record(
        "verify", pr=pr, sha=sha, rc=rc, state=state, detail=detail, posted=posted, worktree_removed=removed, **evidence_fields
    )
    return VerifyOutcome(pr, sha, rc, state, posted, removed, detail, str(marker))


def gatelock_prune(sh: Command, *, repo: Path) -> tuple[bool, str]:
    """Lesson 7: `fleet/gatelock.py prune --apply` before each cycle.

    rc 0 = every leftover it found was provably dead and is gone (or none
    existed); rc 13 = it left a lock it could not classify (named on stdout)
    — still OK to run, reported; anything else = the store is unusable and no
    verify is planned this cycle.
    """
    result = sh(["python3", "fleet/gatelock.py", "prune", "--apply"], cwd=repo)
    tail = (result.out or result.err).strip().splitlines()[-1:] or [""]
    if result.rc == 0:
        return True, f"pruned:{tail[0][:120]}"
    if result.rc == 13:
        return True, f"unclassified-left:{tail[0][:120]}"
    return False, f"rc{result.rc}:{tail[0][:120]}"


def poster_takes_detail(post_status: Callable[..., Result]) -> bool:
    """Whether an injected poster declares the optional detail parameter (#1407).

    Asked of the CALLABLE rather than assumed. `post_status` is an injected
    transport, and a poster that declares only `(sha, rc)` is a producer that
    simply has nothing to say about WHY -- it must keep being called the way it
    declares, and receive no detail, rather than be broken by a widened seam.
    """
    try:
        parameters = list(inspect.signature(post_status).parameters.values())
    except (TypeError, ValueError):
        # A callable whose signature cannot be read is not assumed to accept
        # more than the original two arguments.
        return False
    positional = [
        p
        for p in parameters
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD, p.VAR_POSITIONAL)
    ]
    if any(p.kind is p.VAR_POSITIONAL for p in positional):
        return True
    return len(positional) >= 3


def post_with_detail(post_status: Callable[..., Result], sha: str, rc: int, detail: str) -> Result:
    """Publish ONE outcome, offering the reason to a poster that takes one.

    `rc` is decided before this call and is passed through unchanged: the result
    of the gate is an input here, never an output, so no detail can turn a
    CANNOT-ASSESS into a pass (the #739 class).
    """
    if detail and poster_takes_detail(post_status):
        return post_status(sha, rc, detail)
    return post_status(sha, rc)


def real_post_status(sh: Command, repo: Path) -> Callable[..., Result]:
    """`scripts/gate-status.sh post --sha <sha> --rc <rc> [--detail <text>]` — the ONE poster.

    `--detail` names WHY the gate redded; the poster appends it to the status
    description (bounded there to the API's 140-character cap) so a red PR page
    is diagnosable without the build log (#1407). It is omitted when empty, so
    the argv for a producer with nothing to say is byte-identical to before.
    """

    def post(sha: str, rc: int, detail: str = "") -> Result:
        argv = ["bash", "scripts/gate-status.sh", "post", "--sha", sha, "--rc", str(rc)]
        if detail:
            argv += ["--detail", detail]
        return sh(argv, cwd=repo)

    return post
