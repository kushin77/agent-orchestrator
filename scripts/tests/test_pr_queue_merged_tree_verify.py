"""The merge queue's merged-tree verify must judge a TREE, never the invocation.

Issue: #1471.

`scripts/pr-queue.sh` runs the merged-tree verify (evidence source (b)) in a
detached scratch worktree of `master`'s tip plus the PR head, and reports the
result as `merged-tree-red:<check>` — a red ON THE PR. Measured 2026-09-19 on
master `a7518cff` for PRs #1430 and #1432: the default command exec'd
`scripts/verify.sh`, which is committed mode `100644`, so every
`AO_QUEUE_VERIFY_MERGED=1` run died instantly with

    bash: line 1: scripts/verify.sh: Permission denied

and the queue reported `merged-tree-red:verify`. The log file held exactly that
one line. The red named the invocation, not the tree — and a control that
reports a red it cannot attribute is worse than one that reports nothing, since
it is the queue's own signal for "this PR must not land".

These tests pin both halves of the fix:

1. the default invocation must not depend on a mode bit. The mode bit belongs to
   the SCRATCH MERGE-TREE (and `git merge` may take it from either side), so the
   queue cannot control it; naming the interpreter (`bash scripts/verify.sh
   verify`, exactly as the Makefile's `verify` target does) removes the
   dependency. PROVED BEHAVIOURALLY against a repo whose `scripts/verify.sh` is
   mode 644: pre-fix the gate never starts, post-fix it runs.
2. a log that is ONLY a shell error judged no tree, so it is CANNOT-ASSESS, not
   a red. The two anti-over-reach arms below keep that from becoming a blanket
   "any failure is CANNOT-ASSESS": an empty log with a non-zero exit stays a
   red, and so does real gate output carrying a shell error alongside it.

3. the venue must carry the helper's WHOLE load-time layer, not just its entry
   point. `scripts/pr-queue.sh` `source`s `scripts/lib/common.sh` for
   `find_repo_root` (since #1753), so a venue holding only the entry point fails
   that `source`, leaves the root empty and exits 2 on the bare `cd` — a
   CANNOT-ASSESS that is an artifact of the HARNESS, which every arm below would
   otherwise read as a verdict on the tree. Same shape, and the same fix, as
   `governance/futureproof/tests/test_futureproof.py`'s `DISCOVERY_LAYER`
   (#1840, PR #1844) and `check-code-headers.sh`'s scratch tree (#1777).
"""

from __future__ import annotations

import os
import posixpath
import re
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "pr-queue.sh"
SCRIPT_REL = SCRIPT.relative_to(REPO).as_posix()

# The SET of files `scripts/pr-queue.sh` resolves at LOAD time: its entry point
# plus everything it `source`s. The fixture below must carry the whole set —
# copying only the entry point makes the `source` fail in the venue, so the
# helper cannot resolve a repo root and every arm degrades to a CANNOT-ASSESS
# for a reason that has nothing to do with the tree under test. The control
# `test_the_venue_carries_the_helpers_whole_load_time_layer` derives this set
# from the script itself, so a new load-time dependency fails BY NAME rather
# than silently hollowing out every arm here.
PR_QUEUE_LAYER = (SCRIPT_REL, "scripts/lib/common.sh")

# The fleet's load-time source idiom, as used by scripts/*.sh since #1753:
#   source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
_SOURCE_RE = re.compile(
    r'^source "\$\(dirname "\$\{BASH_SOURCE\[0\]\}"\)/(?P<rel>[^"]+)"$',
    re.MULTILINE,
)


def source_paths(script_text: str, script_rel: str) -> tuple[str, ...]:
    """The files `script_text` `source`s at load time, repo-root-relative.

    The idiom resolves the captured path against the SCRIPT's own directory, so
    `scripts/pr-queue.sh` sourcing `lib/common.sh` resolves
    `scripts/lib/common.sh`.
    """
    base = posixpath.dirname(script_rel)
    return tuple(
        posixpath.normpath(posixpath.join(base, m.group("rel")))
        for m in _SOURCE_RE.finditer(script_text)
    )


# A stand-in for scripts/verify.sh. Committed at mode 100644 — the whole point.
# It records that it ran, so a PASS can never be satisfied vacuously by a gate
# that never started.
STAND_IN_GATE = """#!/usr/bin/env bash
# stand-in for scripts/verify.sh, mode 100644 exactly like the real one.
set -u
marker="${AO1471_VERIFY_MARKER:-/dev/null}"
printf '%s\\n' "$*" >> "$marker"
exit 0
"""


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def make_repo(tmp_path: Path) -> Path:
    """A throwaway repo with an origin-like remote and a 100644 verify.sh.

    `scripts/pr-queue.sh` is copied in WITHOUT a sibling `scripts/gate-status.sh`,
    so merged-tree evidence source (a) is skipped by construction (no `gh` call,
    no network) and source (b) — the local merge-tree verify — is what runs. It
    is copied WITH the rest of its load-time layer (`PR_QUEUE_LAYER`): without
    `scripts/lib/common.sh` the helper's own `source` fails and nothing here
    would ever reach the code under test.
    """
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "--bare", "-q", str(origin))
    repo = tmp_path / "repo"
    git(tmp_path, "init", "-q", str(repo))
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "test")
    scripts = repo / "scripts"
    scripts.mkdir()
    gate = scripts / "verify.sh"
    gate.write_text(STAND_IN_GATE, encoding="utf-8")
    os.chmod(gate, 0o644)
    for rel in PR_QUEUE_LAYER:
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / rel, target)
    os.chmod(repo / SCRIPT_REL, 0o755)
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "seed")
    git(repo, "branch", "-M", "master")
    git(repo, "remote", "add", "origin", str(origin))
    git(repo, "push", "-q", "origin", "master")
    return repo


def merged_tree(
    repo: Path,
    tmp_path: Path,
    number: int,
    *,
    verify_cmd: str | None = None,
    marker: Path | None = None,
) -> subprocess.CompletedProcess:
    """Drive the offline `--check-merged-tree` seam with the real code path.

    head == the repo's tip, so the merge-base check passes trivially (the same
    device scripts/check-pr-queue-squash-guard.sh's controls use).
    """
    scratch = tmp_path / "scratch"
    scratch.mkdir(exist_ok=True)
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("AO_QUEUE_", "AO1471_"))
    }
    env["TMPDIR"] = str(scratch)  # disk-backed, never the shared /tmp
    env["AO_QUEUE_VERIFY_MERGED"] = "1"
    if verify_cmd is not None:
        env["AO_QUEUE_VERIFY_CMD"] = verify_cmd
    if marker is not None:
        env["AO1471_VERIFY_MARKER"] = str(marker)
    tip = git(repo, "rev-parse", "HEAD").stdout.strip()
    return subprocess.run(
        [
            "bash",
            "scripts/pr-queue.sh",
            "--check-merged-tree",
            str(number),
            "--head",
            tip,
            "--against-base",
            "origin/master",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
    )


def emits(lines: list[str], rc: int) -> str:
    """An AO_QUEUE_VERIFY_CMD that writes `lines` to stderr and exits `rc`."""
    return "; ".join(f"echo '{line}' >&2" for line in lines) + f"; exit {rc}"


# The fixture's own contract, derived from the helper rather than restated: the
# venue must carry every file `pr-queue.sh` resolves at load time. A future
# #1753-style migration that adds another `source` would otherwise turn every
# arm in this file into a CANNOT-ASSESS that reads like a verdict on the tree.
def test_the_venue_carries_the_helpers_whole_load_time_layer(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    needed = {
        SCRIPT_REL,
        *source_paths(SCRIPT.read_text(encoding="utf-8"), SCRIPT_REL),
    }

    missing = sorted(rel for rel in needed if not (repo / rel).is_file())
    assert not missing, (
        "the venue is missing pr-queue.sh's load-time "
        f"dependencies: {missing} — the helper would exit 2 before reaching the "
        "code under test"
    )
    assert needed == set(PR_QUEUE_LAYER), (
        "PR_QUEUE_LAYER has drifted from what pr-queue.sh actually sources: "
        f"the script needs {sorted(needed)}, the fixture copies "
        f"{sorted(PR_QUEUE_LAYER)}"
    )


# The primitive, measured rather than assumed: the two invocations differ ONLY
# in whether they depend on a mode bit, and only one of them survives.
def test_the_primitive_a_direct_exec_fails_where_naming_the_interpreter_does_not(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    gate = repo / "scripts" / "verify.sh"
    assert gate.stat().st_mode & 0o111 == 0, "the stand-in gate must not be executable"

    marker = tmp_path / "primitive-marker.txt"
    direct = subprocess.run(
        ["bash", "-c", "scripts/verify.sh verify"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    # 126 = found but not executable (bash -n uses 2 for a syntax error; this is
    # a different failure and must not be confused with one).
    assert direct.returncode == 126, direct
    assert "Permission denied" in direct.stderr

    viashell = subprocess.run(
        ["bash", "-c", "bash scripts/verify.sh verify"],
        cwd=repo,
        capture_output=True,
        text=True,
        env={**os.environ, "AO1471_VERIFY_MARKER": str(marker)},
    )
    assert viashell.returncode == 0, viashell
    assert marker.read_text(encoding="utf-8").strip() == "verify"


# THE FALSIFIER for half 1. On the pre-fix default (`scripts/verify.sh verify`)
# the gate never starts and the run reports a red; the marker proves which.
def test_the_default_merged_tree_verify_runs_a_non_executable_verify_sh(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    marker = tmp_path / "default-marker.txt"
    got = merged_tree(repo, tmp_path, 4242, marker=marker)

    assert marker.exists(), (
        "the default merged-tree verify never started the gate — "
        f"stdout={got.stdout!r} stderr={got.stderr!r}"
    )
    assert marker.read_text(encoding="utf-8").strip() == "verify"
    assert got.returncode == 0, got.stderr
    assert "merged-tree evidence for #4242" in got.stdout
    assert "merged-tree-red" not in got.stderr


# THE FALSIFIER for half 2: a log of nothing but a shell error is not a verdict
# on the tree, and the refusal must say WHY (its own reason, not just "no red").
def test_a_log_that_is_only_a_shell_error_is_cannot_assess_not_a_red(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    got = merged_tree(
        repo,
        tmp_path,
        4243,
        verify_cmd=emits(["bash: line 1: scripts/verify.sh: Permission denied"], 126),
    )

    assert got.returncode == 1, got
    assert "merged-tree-unverified:4243" in got.stderr
    assert "nothing but a shell error" in got.stderr
    assert "CANNOT-ASSESS" in got.stderr
    assert "merged-tree-red" not in got.stderr, "a shell error must not read as a red"


# ANTI-OVER-REACH 1: the predicate must not swallow the real red path. An empty
# log with a non-zero exit is what `AO_QUEUE_VERIFY_CMD="exit 1"` produces, and
# it is still a red — this is the arm that keeps "solely" honest.
def test_an_empty_log_with_a_nonzero_exit_is_still_a_red(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    got = merged_tree(repo, tmp_path, 4244, verify_cmd="exit 1")

    assert got.returncode == 1, got
    assert "merged-tree-red:verify" in got.stderr
    assert "merged-tree-unverified" not in got.stderr


# ANTI-OVER-REACH 2: real gate output that merely MENTIONS a shell error is not
# "solely" a shell error, so it stays a red.
def test_gate_output_carrying_a_shell_error_alongside_it_is_still_a_red(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    got = merged_tree(
        repo,
        tmp_path,
        4245,
        verify_cmd=emits(["shell-syntax: OK", "bash: x: Permission denied"], 1),
    )

    assert got.returncode == 1, got
    assert "merged-tree-red:verify" in got.stderr
    assert "merged-tree-unverified" not in got.stderr
