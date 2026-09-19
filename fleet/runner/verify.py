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

All transports are injected: `git`, `sh` and `post_status` are callables, so
the tests drive this module with fakes and no test ever touches the real repo.
"""

from __future__ import annotations

import fcntl
import json
import os
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
    post_status: Callable[[str, int], Result],
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
    try:
        with HeldWorktree(git, repo=repo, path=worktree, sha=sha) as held:
            result = sh(["bash", "scripts/verify.sh", "verify"], cwd=held.path, timeout=timeout)
            rc = int(result.rc)
        removed = held.removed
    except RuntimeError as exc:
        detail = str(exc)
        ledger.record("verify", pr=pr, sha=sha, rc=None, state=CANNOT_ASSESS, detail=detail, posted=False)
        return VerifyOutcome(pr, sha, CANNOT_ASSESS_RC, CANNOT_ASSESS, False, True, detail)

    state = state_of_verify_rc(rc)
    detail = f"verify rc {rc} ({state})"
    gate_rc = gate_rc_of(rc)
    posted = False
    if gate_rc is None:
        ledger.record("post-skipped", pr=pr, sha=sha, rc=rc, reason=f"parked:{rc}")
    else:
        post = post_status(sha, gate_rc)
        posted = post.ok
        ledger.record("post", pr=pr, sha=sha, rc=gate_rc, ok=post.ok, detail=(post.out or post.err).strip()[:200])

    # A head verify is head-level evidence: `base_tip` stays None so the
    # merged-tree seam judges it at merge time (lesson 3). The tip is recorded
    # in the detail for the reader.
    marker = write_local_marker(
        marker_dir, pr, sha, rc, base_tip=None, detail=f"{detail}; master was {(master_tip or '?')[:12]}", recorded_at=now()
    )
    ledger.record("verify", pr=pr, sha=sha, rc=rc, state=state, detail=detail, posted=posted, worktree_removed=removed)
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


def real_post_status(sh: Command, repo: Path) -> Callable[[str, int], Result]:
    """`scripts/gate-status.sh post --sha <sha> --rc <rc>` — the ONE poster."""

    def post(sha: str, rc: int) -> Result:
        return sh(["bash", "scripts/gate-status.sh", "post", "--sha", sha, "--rc", str(rc)], cwd=repo)

    return post
