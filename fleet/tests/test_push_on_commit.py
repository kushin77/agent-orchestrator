"""Push-on-commit (issue #740, dispatch half).

Every lane the fleet runs commits inside its own worktree; the #708 incident
(31 lanes, 46 unpushed commits) happened because nothing pushed that commit
until AFTER the gate, and the gate never ran clean. ``terminal.push_lane_branch``
is the fix: push immediately, before gating, and report a failed push as
``stranded`` by name rather than silently.

The fixture is a real local bare remote (a `git init --bare` directory), so the
push is a real ``git push``, not a mock.
"""

from __future__ import annotations

import subprocess

import terminal


def _run(cmd, cwd):
    result = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)
    assert result.returncode == 0, f"{cmd} failed: {result.stderr}"
    return result


def _make_bare_remote_and_lane(tmp_path):
    bare = tmp_path / "origin.git"
    bare.mkdir()
    _run(["git", "init", "--bare", "-q"], bare)

    lane = tmp_path / "lane"
    lane.mkdir()
    _run(["git", "init", "-q"], lane)
    _run(["git", "config", "user.email", "lane@example.com"], lane)
    _run(["git", "config", "user.name", "lane"], lane)
    _run(["git", "remote", "add", "origin", str(bare)], lane)
    (lane / "f.txt").write_text("hello\n", encoding="utf-8")
    _run(["git", "add", "f.txt"], lane)
    _run(["git", "commit", "-q", "-m", "lane commit"], lane)
    _run(["git", "checkout", "-q", "-b", "ao-740-lane"], lane)
    return bare, lane


def test_push_lane_branch_pushes_a_real_commit_to_a_bare_remote(tmp_path):
    bare, lane = _make_bare_remote_and_lane(tmp_path)
    outcome, detail = terminal.push_lane_branch("ao-740-lane", lane, timeout=30, directive_id="d1")
    assert outcome == terminal.PUSH_OK, detail
    branches = subprocess.run(
        ["git", "branch"], cwd=str(bare), capture_output=True, text=True
    ).stdout
    assert "ao-740-lane" in branches


def test_push_lane_branch_reports_stranded_by_name_on_failure(tmp_path):
    lane = tmp_path / "lane"
    lane.mkdir()
    _run(["git", "init", "-q"], lane)
    _run(["git", "config", "user.email", "lane@example.com"], lane)
    _run(["git", "config", "user.name", "lane"], lane)
    # No remote named 'origin' at all -> push must fail, never silently.
    (lane / "f.txt").write_text("hello\n", encoding="utf-8")
    _run(["git", "add", "f.txt"], lane)
    _run(["git", "commit", "-q", "-m", "lane commit"], lane)
    _run(["git", "checkout", "-q", "-b", "ao-740-orphan"], lane)

    outcome, detail = terminal.push_lane_branch("ao-740-orphan", lane, timeout=30, directive_id="d2")
    assert outcome == terminal.PUSH_STRANDED
    assert "stranded" in detail
    assert "ao-740-orphan" in detail


def test_push_lane_branch_skips_when_no_isolated_lane():
    outcome, detail = terminal.push_lane_branch(None, None, timeout=30, directive_id="d3")
    assert outcome == terminal.PUSH_SKIPPED
