"""The invariant survives the lane: a verified commit is re-measured, not mourned (#786).

``record-verification`` required the **lane worktree**, and ``reclaim-lane`` removes
it. Once a lane was reclaimed the invariant was permanently unsatisfiable, and
``close`` reported ``REMAINS VERIFY_EVIDENCE_MISSING`` for work whose gate *was* green
— the record discarded, not absent. Measured on #622, #623 and #626.

These tests drive the real port (``GhOps``) against a real repository in which a real
``git worktree`` was really removed — the machinery that destroyed the evidence, not a
fixture standing in for it. The only fiction is ``make`` on ``PATH``, exactly as
``test_verify_port.py`` does it, because running the composite gate twice per test is
not a thing a gate can do.

What is pinned:

* the verified **head commit** is what the invariant names, and it is re-measured;
* the re-measurement *is* a gate run, at that commit, in its own detached tree —
  proved from the tree the gate reports it ran in, not from the return value;
* the throwaway tree does not survive the measurement, so a recovery cannot itself
  leave a lane behind;
* every way the re-measurement can fail is refused **by name**: a non-green gate stays
  a failure, and a commit that is not in the repository names the ordering that would
  have prevented it.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from governance.lifecycle import gate
from governance.lifecycle.cli import SCRATCH_ENV, GhOps, journal_path

LANE_ISSUE = 786


def _git(cwd: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "lifecycle-test",
        "GIT_AUTHOR_EMAIL": "lifecycle-test@agents.invalid",
        "GIT_COMMITTER_NAME": "lifecycle-test",
        "GIT_COMMITTER_EMAIL": "lifecycle-test@agents.invalid",
    }
    result = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, env=env, check=True
    )
    return result.stdout.strip()


def _reclaimed_lane(root: Path) -> tuple[Path, str]:
    """A repository whose lane has been reclaimed, leaving only the verified commit.

    The lane is a *real* linked worktree, committed to, and then removed with
    ``git worktree remove`` — the exact operation ``reclaim-lane`` performs in the
    incident. Its record is left behind, which is what the port reads to learn the lane
    is gone.
    """
    main = root / "repo"
    main.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "master"], cwd=main, check=True)
    (main / "README.md").write_text("repo\n", encoding="utf-8")
    _git(main, "add", "README.md")
    _git(main, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "base")

    lane = root / "lane"
    _git(main, "worktree", "add", "-b", "issue-786", str(lane), "HEAD")
    (lane / "verified.txt").write_text("the verified work\n", encoding="utf-8")
    _git(lane, "add", "verified.txt")
    _git(lane, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "the verified head")
    head = _git(lane, "rev-parse", "HEAD")

    _git(main, "worktree", "remove", "--force", str(lane))
    assert not lane.exists(), "the fixture must really have removed the tree"

    lanes = main / ".fleet" / "lanes"
    lanes.mkdir(parents=True)
    (lanes / "s-786.json").write_text(
        '{"session_id": "s-786", "issue": 786, "worktree": "%s"}\n' % lane, encoding="utf-8"
    )
    return main, head


def _stub_gate(root: Path, monkeypatch, outcome: str) -> Path:
    """A ``make`` on ``PATH`` that records the tree it ran in and reports one outcome.

    It records the resolved HEAD too, because the tree is *removed* before the caller
    can look at it — the port throws the re-measurement tree away on the way out, which
    is itself a property under test. So the proof that the gate ran at the verified
    commit has to be taken from inside the run.
    """
    binary_dir = root / "bin"
    binary_dir.mkdir(exist_ok=True)
    ran_in = root / "gate-cwd"
    snippets = {
        "passed": "printf 'verify: PASS (120 of 120 checks)\\n'\nexit 0\n",
        "failed": (
            "printf 'verify: FAIL (1 of 120 checks failed)\\n' >&2\n"
            "printf 'make: *** [Makefile:146: verify] Error 1\\n' >&2\n"
            "exit 2\n"
        ),
    }
    stub = binary_dir / "make"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'printf \'%s %s\\n\' "$PWD" "$(git -C "$PWD" rev-parse HEAD)" >> "$STUB_GATE_CWD"\n'
        + snippets[outcome],
        encoding="utf-8",
    )
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{binary_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("STUB_GATE_CWD", str(ran_in))
    monkeypatch.setenv("AO_LIFECYCLE_GATE_RETRIES", "0")
    return ran_in


def _gate_ran_in(ran_in: Path) -> tuple[Path, str]:
    """``(tree, commit)`` the stub gate last ran in."""
    where, commit = ran_in.read_text(encoding="utf-8").split()[-2:]
    return Path(where), commit


def _scratch_dir(root: Path, monkeypatch) -> Path:
    scratch = root / "scratch"
    scratch.mkdir()
    monkeypatch.setenv(SCRATCH_ENV, str(scratch))
    return scratch


def test_a_reclaimed_lane_is_re_measured_at_the_verified_commit(tmp_path, monkeypatch):
    """The whole point: the record is produced, not declared missing."""
    main, head = _reclaimed_lane(tmp_path)
    ran_in = _stub_gate(tmp_path, monkeypatch, "passed")
    scratch = _scratch_dir(tmp_path, monkeypatch)

    detail = GhOps(root=main).record_verification(LANE_ISSUE, head)

    assert "re-measured" in detail
    journal = json.loads(journal_path(LANE_ISSUE, main).read_text(encoding="utf-8"))
    assert journal["verify"] == {"ok": True, "commit": head, "source": "reclaimed-lane"}

    # The gate really ran somewhere else — and at *this* commit, which is the only
    # thing that makes the record evidence rather than an assertion.
    where, measured = _gate_ran_in(ran_in)
    assert where != main
    assert str(where).startswith(str(scratch))
    assert measured == head


def test_the_re_measurement_tree_does_not_survive_it(tmp_path, monkeypatch):
    """A recovery must not be able to leak a lane: the tree is thrown away."""
    main, head = _reclaimed_lane(tmp_path)
    _stub_gate(tmp_path, monkeypatch, "passed")
    scratch = _scratch_dir(tmp_path, monkeypatch)

    GhOps(root=main).record_verification(LANE_ISSUE, head)

    assert list(scratch.iterdir()) == []
    assert _git(main, "worktree", "list", "--porcelain").count("worktree ") == 1
    assert str(scratch) not in _git(main, "worktree", "list")
def test_a_non_green_re_measurement_is_still_a_failure(tmp_path, monkeypatch):
    """Negative control: the recovery must not turn a red tree green."""
    main, head = _reclaimed_lane(tmp_path)
    _stub_gate(tmp_path, monkeypatch, "failed")
    _scratch_dir(tmp_path, monkeypatch)

    with pytest.raises(RuntimeError) as raised:
        GhOps(root=main).record_verification(LANE_ISSUE, head)

    assert not isinstance(raised.value, gate.CannotAssess)
    assert "a check failed" in str(raised.value)
    assert not journal_path(LANE_ISSUE, main).exists()


def test_a_commit_that_is_not_in_the_repository_is_refused_by_name(tmp_path, monkeypatch):
    """Negative control: re-measurement is not a blanket pass.

    A commit the repository does not hold cannot be measured, and the refusal names the
    *ordering* — the fact an operator can act on — instead of the old silent
    "the verified tree no longer exists".
    """
    main, _head = _reclaimed_lane(tmp_path)
    _stub_gate(tmp_path, monkeypatch, "passed")
    _scratch_dir(tmp_path, monkeypatch)

    with pytest.raises(RuntimeError) as raised:
        GhOps(root=main).record_verification(LANE_ISSUE, "c" * 40)

    message = str(raised.value)
    assert "c" * 12 in message
    assert "is not in" in message
    assert "close-out BEFORE lane teardown" in message
    assert not journal_path(LANE_ISSUE, main).exists()


def test_a_lane_less_item_with_no_verified_head_is_refused_by_name(tmp_path, monkeypatch):
    """Negative control: nothing to measure is still nothing to record."""
    main, _head = _reclaimed_lane(tmp_path)
    _stub_gate(tmp_path, monkeypatch, "passed")
    _scratch_dir(tmp_path, monkeypatch)

    with pytest.raises(RuntimeError) as raised:
        GhOps(root=main).record_verification(LANE_ISSUE, "")

    assert "nothing to re-measure" in str(raised.value)
    assert "close-out BEFORE lane teardown" in str(raised.value)
    assert not journal_path(LANE_ISSUE, main).exists()


def test_the_lane_is_still_preferred_when_it_exists(tmp_path, monkeypatch):
    """Negative control for the fallback: it is a *fallback*, not a replacement.

    While the lane is present the gate must run **in it** — the tree and the run are
    the same object, which no re-measurement can be.
    """
    main, head = _reclaimed_lane(tmp_path)
    lane = tmp_path / "lane"
    _git(main, "worktree", "add", "--detach", str(lane), head)
    ran_in = _stub_gate(tmp_path, monkeypatch, "passed")
    _scratch_dir(tmp_path, monkeypatch)

    GhOps(root=main).record_verification(LANE_ISSUE, head)

    assert _gate_ran_in(ran_in)[0] == lane
    journal = json.loads(journal_path(LANE_ISSUE, main).read_text(encoding="utf-8"))
    assert journal["verify"]["source"] == "lane"
