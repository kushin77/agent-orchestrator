"""`scripts/check-squash-message.sh` — the pre-merge squash-message gate
(issue #1001, child of #882/#878 lane L3).

`gh pr merge --squash` composes the LANDED commit message from the PR title
and body, not from the branch's own commit trailers, so the trailer paragraph
could still be lost between a clean branch and a red `check-isolation-landed`
run — measured three times in one day (#960/#976, then #996/#991). This gate
renders the same message and asks the ONE shared trailer predicate
(`governance/isolation/trailer.py`) about it before the merge happens.

This suite drives the script's own `--self-test`, which is the artifact's
proof that it can genuinely fail: a passing fixture is accepted and both
failing mutants (no trailer at all; reference only in the subject) are
refused BY NAME. Testing through the CLI, not by importing shell as Python,
matches this repo's convention for `scripts/check-*.sh` gates (see
`test_landed.py`, which drives `cli.py` the same way).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "check-squash-message.sh"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_script_exists_and_is_executable_shape() -> None:
    assert SCRIPT.is_file(), f"{SCRIPT} is missing"


def test_self_test_passes() -> None:
    """The script's own fixture + mutant proof: 0 OK, both mutants named."""
    result = _run("--self-test")
    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    assert "OK    passing fixture" in combined
    assert "commit-missing-ticket-trailer" in combined
    assert "commit-ref-only-in-subject" in combined


def test_no_arguments_is_usage_error() -> None:
    """Neither `--pr` nor `--self-test`: CANNOT-ASSESS (usage), never a pass."""
    result = _run()
    assert result.returncode == 2, result.stdout + result.stderr


def test_missing_predicate_is_cannot_assess(tmp_path: Path) -> None:
    """No `governance/isolation/trailer.py` in the checkout under test: rc 2."""
    scratch = tmp_path / "scratch-repo"
    scratch.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(scratch)], check=True, capture_output=True)
    # Copy only the script, into a checkout with no governance/ package at all.
    scripts_dir = scratch / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "check-squash-message.sh").write_bytes(SCRIPT.read_bytes())
    result = subprocess.run(
        ["bash", str(scripts_dir / "check-squash-message.sh"), "--self-test"],
        cwd=str(scratch),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert "shared predicate" in (result.stdout + result.stderr)
