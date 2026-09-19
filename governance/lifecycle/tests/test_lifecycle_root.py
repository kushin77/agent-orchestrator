"""The state-root seam: which repository the lifecycle reads and writes (#1247).

``ROOT`` is derived from this file's location, so a checkout's own copy operates on its
own state. The override exists because the driver has two requirements a lane worktree
cannot satisfy at once — it must run the **fixed** code, and it must run against the
**fleet's** state (the journals, lane records and ledger the fleet loops write in the
shared checkout, without which an item is not even in the audit's scope).

Measured on this box while diagnosing #1247: the shared checkout sat on
``issue-708-wire-runaway-guard``, 3.7 hours behind ``origin/master``, so its copy of
``cli.py`` still refuses a red frozen head outright and could not exercise the fix for
this class at all.

These tests run the module in a subprocess, because the seam is read once at import —
which is the property that has to hold: the root decides where landing records are
written, so it must be settled before any of them is opened, and an override that names
something that is not a repository must stop the run rather than scatter them.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import REPO_ROOT

_PRINT_ROOT = "import governance.lifecycle.cli as cli; print(cli.ROOT)"


def _import_root(env_root: str | None) -> subprocess.CompletedProcess:
    """Import the module in a fresh interpreter and report the root it settled on."""
    env = {k: v for k, v in os.environ.items() if k != "AO_LIFECYCLE_ROOT"}
    env["PYTHONPATH"] = str(REPO_ROOT)
    if env_root is not None:
        env["AO_LIFECYCLE_ROOT"] = env_root
    return subprocess.run(
        [sys.executable, "-c", _PRINT_ROOT],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )


def test_without_the_override_the_checkout_own_copy_is_used(tmp_path):
    """The default path is unchanged: no override means this checkout's own state."""
    done = _import_root(None)

    assert done.returncode == 0, done.stderr
    assert Path(done.stdout.strip()) == REPO_ROOT


def test_the_override_names_the_repository_the_state_is_read_from(tmp_path):
    """A lane's copy — the one carrying the fix — can be pointed at the fleet's state."""
    other = tmp_path / "fleet-state"
    (other / ".git").mkdir(parents=True)

    done = _import_root(str(other))

    assert done.returncode == 0, done.stderr
    assert Path(done.stdout.strip()) == other


def test_an_override_that_is_not_a_repository_is_refused_by_name(tmp_path):
    """Not a fallback: a wrong root would write another module's landing records nowhere.

    ``.fleet/lifecycle``'s *presence* is what ``governance/reconcile`` reads as "this
    issue's work landed", so an unvalidated override puts landing records outside the
    fleet's state — the substitution golden rule 17 forbids, arriving by typo.
    """
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()

    done = _import_root(str(not_a_repo))

    assert done.returncode != 0
    assert "AO_LIFECYCLE_ROOT" in done.stderr
    assert "is not a repository root" in done.stderr
    assert "no .git at" in done.stderr


def test_the_refusal_is_not_swallowed_by_an_empty_override(tmp_path, monkeypatch):
    """Whitespace is unset, not a root: the seam is opt-in and never guessed."""
    done = _import_root("   ")

    assert done.returncode == 0, done.stderr
    assert Path(done.stdout.strip()) == REPO_ROOT


@pytest.mark.parametrize("value", ["/nonexistent/ao-lifecycle-root"])
def test_a_missing_directory_is_refused_like_any_other_non_repository(value):
    done = _import_root(value)

    assert done.returncode != 0
    assert "is not a repository root" in done.stderr


#: The seam's other half, and the defect #1438 measured: the override must not
#: decide which code runs. ``governance/`` is a namespace package, so its
#: ``__path__`` is recomputed from ``sys.path`` on every submodule import — with the
#: override inserted there, a state root that is *another checkout* of this
#: repository wins the import.
_RUN_CLI = "close --lane deadbeefcafe"


def test_the_override_decides_the_state_and_never_which_code_runs(tmp_path):
    """The one use the seam exists for: the FIXED code against the fleet's state.

    Measured on this box with the shared checkout as the override (#1438): the verb
    imported the state root's ``governance.lifecycle`` (its older branch has no
    ``lane_closeout``, and its ``isolation.worktree`` has no ``content_landed``), so a
    lane's fixed copy died with ``ImportError`` / ``AttributeError`` instead of closing
    anything — the fixed code was unreachable through the seam that exists to reach it.
    The override is a data root: it may not say what code runs.
    """
    other = tmp_path / "other-checkout"
    (other / ".git").mkdir(parents=True)
    (other / "governance" / "lifecycle").mkdir(parents=True)
    # The state root's copy of this package, older than ours: a `lifecycle` package
    # with no `lane_closeout` module in it.
    (other / "governance" / "lifecycle" / "__init__.py").write_text("", encoding="utf-8")
    assert not (other / "governance" / "lifecycle" / "lane_closeout.py").exists()

    env = {k: v for k, v in os.environ.items() if k != "AO_LIFECYCLE_ROOT"}
    env["AO_LIFECYCLE_ROOT"] = str(other)

    done = subprocess.run(
        [sys.executable, str(REPO_ROOT / "governance" / "lifecycle" / "cli.py"), *_RUN_CLI.split()],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )

    # The verb REFUSES BY NAME (an unknown lane is CANNOT-ASSESS, rc 2) instead of
    # tracebacking out of somebody else's tree.
    assert "Traceback" not in done.stderr, done.stderr
    assert done.returncode == 2, (done.returncode, done.stdout, done.stderr)
    assert "CANNOT-ASSESS" in done.stdout + done.stderr
