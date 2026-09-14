"""Pytest bootstrap for the governance/isolation suite (issue #263).

``governance/`` is a PEP-420 namespace package, so the repository root goes on
``sys.path`` and the modules are imported qualified (``governance.isolation.*``).
Importing by qualified name matters here: ``guardrails/isolation`` exists too,
and a bare ``import isolation`` would be ambiguous the moment both suites are
ever collected together.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from governance.isolation.identity import mint  # noqa: E402
from governance.isolation.worktree import provision, write_record  # noqa: E402


def git(cwd: Path | str, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run git in a test repository, never reading the developer's global config."""
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(cwd), "GIT_CONFIG_NOSYSTEM": "1"},
    )
    if check and result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A scratch repository whose shared config carries a *human* identity.

    The human signature is the bait: if the lane's identity ever lands in the
    shared config, these tests can see it.
    """
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "master", str(root)], check=True, capture_output=True, text=True)
    git(root, "config", "user.name", "Human Dev")
    git(root, "config", "user.email", "human@example.com")
    (root / "seed.txt").write_text("seed\n", encoding="utf-8")
    git(root, "add", "seed.txt")
    git(root, "commit", "-q", "-m", "seed")
    return root


@pytest.fixture
def mounts(tmp_path: Path) -> Path:
    """A mount table this suite declares instead of inheriting the runner's.

    The tmpfs refusal (issue #516) must be provable on any machine, and a lane
    fixture must not go red merely because the host's scratch directory happens
    to be RAM-backed. Injecting the table makes the filesystem an input rather
    than a property of whoever runs the tests; the refusal itself is provoked
    with a table that *does* declare a tmpfs.
    """
    table = tmp_path / "mounts"
    table.write_text("/dev/root / ext4 rw,relatime 0 0\n", encoding="utf-8")
    return table


@pytest.fixture
def lane(repo: Path, tmp_path: Path, mounts: Path):
    """A provisioned lane for issue #263: worktree, branch, lane-local signature."""
    identity = mint(263, "copilot-brain", "governance-isolation", worktree_root=tmp_path / "lanes")
    provision(identity, repo, base="HEAD", mounts=mounts)
    write_record(identity, repo)
    return identity


def commit(path: Path, filename: str, message: str, trailer: str = "") -> str:
    """Commit a file in a lane; optionally append a ticket trailer."""
    (path / filename).write_text(f"{filename}\n", encoding="utf-8")
    git(path, "add", filename)
    args = ["commit", "-q", "-m", message]
    if trailer:
        args += ["-m", trailer]
    git(path, *args)
    return git(path, "rev-parse", "HEAD").stdout.strip()
