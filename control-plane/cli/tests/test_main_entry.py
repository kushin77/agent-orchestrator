"""The entry point itself, run the way the issue's ``Verify:`` runs it.

``python3 control-plane/cli/main.py --help && make verify`` is the issue's own
verification command, so ``--help`` is asserted here for real — as a subprocess,
from a directory that is not the repo, with nothing injected. Everything else in
the suite goes through ``aoctl.cli.main``; this file is what proves the *shipped
entry point* resolves its own package and its own registry without a bootstrap
step from the caller.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from aoctl import vocabulary

ROOT = Path(__file__).resolve().parents[3]
MAIN = ROOT / "control-plane" / "cli" / "main.py"


def run_cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(MAIN), *args],
        cwd=str(cwd or ROOT),
        capture_output=True,
        text=True,
    )


def test_the_issues_verify_command_is_green():
    """``python3 control-plane/cli/main.py --help`` — exit 0, and the table."""
    done = run_cli("--help")
    assert done.returncode == 0, done.stderr
    for verb, verb_id in vocabulary.SURFACE:
        assert f"{verb}" in done.stdout, verb
        assert verb_id in done.stdout, verb_id
    assert "exactly one declared command id" in done.stdout


def test_the_entry_point_works_from_any_directory(tmp_path):
    """An operator runs this from their own cwd, not from the checkout."""
    done = run_cli("--dry-run", "pause", cwd=tmp_path)
    assert done.returncode == 0, done.stderr
    assert "DRY-RUN" in done.stdout
    assert "/api/control/fleet/pause" in done.stdout


def test_a_usage_error_is_non_zero():
    done = run_cli("deploy")
    assert done.returncode != 0
    assert "deploy" in done.stderr


def test_an_unreachable_plane_is_non_zero_on_the_command_line(tmp_path):
    """No session, no plane: the CLI refuses locally and names the reason."""
    done = run_cli("--plane", "http://127.0.0.1:9", "pause", cwd=tmp_path)
    assert done.returncode != 0
    assert "no_session" in done.stderr or "plane_unreachable" in done.stderr


def test_a_malformed_plane_address_is_named_on_the_command_line(tmp_path):
    done = run_cli("--plane", "127.0.0.1:8787", "--session", "x", "status", cwd=tmp_path)
    assert done.returncode != 0
    assert "address_invalid" in done.stderr


@pytest.mark.parametrize("verb", [name for name, _ in vocabulary.SURFACE])
def test_every_verb_is_reachable_from_the_command_line(verb):
    done = run_cli("--dry-run", verb)
    assert done.returncode == 0, done.stderr
    assert "DRY-RUN" in done.stdout
