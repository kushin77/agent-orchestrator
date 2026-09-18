"""governance/landing/cli.py write-master-attestation (RCA fix #5 follow-up, #1114).

An OPERATOR's manual `gh pr merge` never goes through `land()`, so this
subcommand is the other direction a green verify can publish master's own
health from. These tests drive it against a REAL tiny git repo (rev-parse is
the whole point of the guard), never a mock of git.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from governance.landing import cli, evidence as evidence_mod  # noqa: E402


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"
    return result.stdout.strip()


def _init_repo(repo: Path) -> str:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "test")
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "initial")
    return _git(repo, "rev-parse", "HEAD")


def _mark_as_master_head(repo: Path, sha: str) -> None:
    """Fake `origin/master` locally — no actual remote is needed for rev-parse."""
    _git(repo, "update-ref", "refs/remotes/origin/master", sha)


def _write_verify_attestation(path: Path, *, exit_code: int, git_sha: str, result: str = "PASS") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "gate": "verify",
                "result": result,
                "exit_code": exit_code,
                "git_sha": git_sha,
                "branch": "master",
                "timestamp": "2026-09-17T00:00:00Z",
                "verified_by": "test-agent",
            }
        ),
        encoding="utf-8",
    )


def _args(**overrides):
    defaults = {"attestation": ".verify/attestation.json", "root": "", "out": ""}
    defaults.update(overrides)
    return cli.argparse.Namespace(**defaults)


def test_a_green_verify_at_master_head_writes_it(tmp_path):
    repo = tmp_path / "repo"
    head = _init_repo(repo)
    _mark_as_master_head(repo, head)
    _write_verify_attestation(repo / ".verify" / "attestation.json", exit_code=0, git_sha=head)

    rc = cli.cmd_write_master_attestation(_args(root=str(repo)))

    assert rc == cli.EXIT_OK
    target = repo / evidence_mod.MASTER_ATTESTATION_REL
    assert target.is_file()
    written = json.loads(target.read_text(encoding="utf-8"))
    assert written["commit"] == head
    assert written["exit_code"] == 0


def test_a_green_verify_on_a_lane_head_does_not_write_it(tmp_path):
    repo = tmp_path / "repo"
    master_head = _init_repo(repo)
    _mark_as_master_head(repo, master_head)
    # A lane commit on top — HEAD now differs from origin/master.
    (repo / "lane.txt").write_text("lane work\n", encoding="utf-8")
    _git(repo, "add", "lane.txt")
    _git(repo, "commit", "-q", "-m", "lane work")
    lane_head = _git(repo, "rev-parse", "HEAD")
    assert lane_head != master_head
    _write_verify_attestation(repo / ".verify" / "attestation.json", exit_code=0, git_sha=lane_head)

    rc = cli.cmd_write_master_attestation(_args(root=str(repo)))

    assert rc == cli.EXIT_OK  # a no-op is not an error
    assert not (repo / evidence_mod.MASTER_ATTESTATION_REL).is_file()


def test_a_red_verify_at_master_head_never_writes_it(tmp_path):
    repo = tmp_path / "repo"
    head = _init_repo(repo)
    _mark_as_master_head(repo, head)
    _write_verify_attestation(repo / ".verify" / "attestation.json", exit_code=1, git_sha=head, result="FAIL")

    rc = cli.cmd_write_master_attestation(_args(root=str(repo)))

    assert rc == cli.EXIT_OK
    assert not (repo / evidence_mod.MASTER_ATTESTATION_REL).is_file()


def test_an_unresolvable_origin_master_is_a_no_op_not_a_failure(tmp_path):
    repo = tmp_path / "repo"
    head = _init_repo(repo)
    # No refs/remotes/origin/master at all.
    _write_verify_attestation(repo / ".verify" / "attestation.json", exit_code=0, git_sha=head)

    rc = cli.cmd_write_master_attestation(_args(root=str(repo)))

    assert rc == cli.EXIT_OK
    assert not (repo / evidence_mod.MASTER_ATTESTATION_REL).is_file()


def test_a_missing_attestation_is_cannot_assess(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)

    rc = cli.cmd_write_master_attestation(_args(root=str(repo), attestation=".verify/does-not-exist.json"))

    assert rc == cli.EXIT_CANNOT_ASSESS
