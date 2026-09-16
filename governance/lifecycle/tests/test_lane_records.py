"""The lane a close-out resolves, when an issue holds more than one record (#834).

Measured on #287, closing it out by hand: ``.fleet/lanes/`` held two records for
the issue — the live ``3acf396f43ea`` (worktree present, isolation audit rc 0,
HEAD equal to the verified commit) and a dead ``d9caf9d11ee2`` whose worktree had
been removed (isolation audit rc 1, ``worktree-missing``). ``_lane_records`` mapped
by issue and the later-sorted record won, so ``record-verification`` refused:

    failed  record-verification: RuntimeError: no lane worktree for #287; the
            verified tree no longer exists

while the live lane existed, audited clean, and held the verified commit. These
tests drive that exact shape against a **real repository** — real worktrees, a
real removed one, the driver's own operations — because a fixture that only
asserts a preference would not have caught the original wedge, which was a record
*ordering* accident.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from governance.isolation.identity import mint
from governance.isolation.worktree import provision, write_record
from governance.lifecycle.audit import audit_item
from governance.lifecycle.cli import GhOps, _lane_records, lane_view, read_journal, select_lane

ISSUE = 834


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    assert result.returncode == 0, f"git {' '.join(args)} failed: {result.stderr}"
    return result.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A real repository, with the isolation module present so the driver can reclaim."""
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "master", str(root)], check=True, capture_output=True, text=True)
    _git(root, "config", "user.name", "Human Dev")
    _git(root, "config", "user.email", "human@example.com")
    (root / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(root, "add", "seed.txt")
    _git(root, "commit", "-q", "-m", "seed")
    # `GhOps.reclaim_lane` shells out to the repository's own isolation CLI, so the
    # scratch repository has to carry one: the driver is exercised, not simulated.
    shutil.copytree(Path(__file__).resolve().parents[3] / "governance" / "isolation", root / "governance" / "isolation")
    return root


@pytest.fixture
def mounts(tmp_path: Path) -> Path:
    """A declared mount table, so the lane is not refused for a RAM-backed tmp_path."""
    table = tmp_path / "mounts"
    table.write_text("/dev/root / ext4 rw,relatime 0 0\n", encoding="utf-8")
    return table


def _lane(repo: Path, tmp_path: Path, mounts: Path, suffix: str):
    """A provisioned, recorded lane on the real repository."""
    identity = mint(ISSUE, f"copilot-brain-{suffix}", "lifecycle", suffix=suffix, worktree_root=tmp_path / "lanes")
    provision(identity, repo, base="HEAD", mounts=mounts)
    write_record(identity, repo)
    return identity


@pytest.fixture
def live_and_dead(repo: Path, tmp_path: Path, mounts: Path):
    """The #287 shape: two records, the **later-sorted** one dead.

    The ordering is the wedge, so the fixture does not choose which lane dies by
    hand: whichever session id sorts last is the one whose worktree is removed.
    That is what makes this a control for the accident rather than for a fixture.
    """
    first = _lane(repo, tmp_path, mounts, "a")
    second = _lane(repo, tmp_path, mounts, "b")
    live, dead = (first, second) if first.session_id < second.session_id else (second, first)
    subprocess.run(["git", "-C", str(repo), "worktree", "remove", str(dead.worktree)], check=True, capture_output=True)
    assert not dead.worktree.exists()
    assert live.worktree.exists()
    return live, dead


def test_every_record_for_an_issue_is_kept_not_collapsed(repo: Path, live_and_dead):
    live, dead = live_and_dead
    records = _lane_records(repo)[ISSUE]
    assert {record["session_id"] for record in records} == {live.session_id, dead.session_id}
    assert sorted(record["session_id"] for record in records)[-1] == dead.session_id, (
        "the fixture no longer reproduces the wedge: the dead record must sort last"
    )


def test_a_dead_record_never_shadows_a_live_lane(repo: Path, live_and_dead):
    live, dead = live_and_dead
    chosen = select_lane(_lane_records(repo)[ISSUE])
    assert chosen is not None and chosen["session_id"] == live.session_id
    assert chosen["worktree_exists"] is True


def test_the_record_whose_head_is_the_verified_commit_wins(repo: Path, live_and_dead):
    """Two live lanes for one issue is a violation — but the verified tree decides."""
    live, dead = live_and_dead
    write_record(live, repo)
    records = _lane_records(repo)[ISSUE]
    head = _git(live.worktree, "rev-parse", "HEAD")
    assert select_lane(records, head)["session_id"] == live.session_id
    assert select_lane(records, "f" * 40)["session_id"] == live.session_id, "a commit nobody holds still resolves to a lane"


def test_record_verification_runs_the_gate_in_the_live_lane(
    repo: Path, live_and_dead, tmp_path: Path, monkeypatch
):
    """The driver's step 2 reaches the live tree, and journals the attestation."""
    live, dead = live_and_dead
    shim = tmp_path / "bin"
    shim.mkdir()
    log = tmp_path / "make.log"
    make = shim / "make"
    make.write_text(f'#!/bin/sh\necho "$PWD $*" >> {log}\nexit 0\n', encoding="utf-8")
    make.chmod(0o755)
    monkeypatch.setenv("PATH", f"{shim}:{Path('/usr/bin')}:{Path('/bin')}")

    head = _git(live.worktree, "rev-parse", "HEAD")
    detail = GhOps(root=repo).record_verification(ISSUE, head)

    assert "verify green" in detail
    assert str(live.worktree) in log.read_text(encoding="utf-8"), "the gate ran in the LIVE lane, not the dead one"
    assert str(dead.worktree) not in log.read_text(encoding="utf-8")
    # The record names which source produced it (#786): a green attestation can be
    # measured in the lane or re-measured at the commit, and the two are only
    # distinguishable if the record says which. Keeping the lane preferred is what
    # this test is for; the source is what makes the two paths auditable.
    assert read_journal(ISSUE, repo)["verify"] == {"ok": True, "commit": head, "source": "lane"}


def test_record_verification_names_a_dead_lane_rather_than_denying_it(repo: Path, live_and_dead):
    """The vacuity control: with only the dead record left, the refusal says why."""
    live, dead = live_and_dead
    subprocess.run(["git", "-C", str(repo), "worktree", "remove", str(live.worktree)], check=True, capture_output=True)

    with pytest.raises(RuntimeError, match="worktree-missing"):
        GhOps(root=repo).record_verification(ISSUE, "a" * 40)


def test_the_close_out_reclaims_the_dead_records_beside_the_live_lane(repo: Path, live_and_dead):
    """Step 8 leaves no record behind, or the next audit re-reports the item."""
    live, dead = live_and_dead
    detail = GhOps(root=repo).reclaim_lane(live.session_id)

    assert live.session_id in detail and dead.session_id in detail
    assert _lane_records(repo).get(ISSUE) in (None, [])
    assert not live.worktree.exists()
    assert not (repo / ".fleet" / "lanes" / f"{dead.session_id}.json").exists()


def test_the_audit_reports_a_dead_record_by_name(repo: Path):
    """A dead record is a finding, not nothing — treating it as nothing is the wedge."""
    view = lane_view([{"session_id": "deadbeef", "worktree": "/gone/ao-287-deadbeef", "worktree_exists": False}])
    problems = audit_item({"issue": ISSUE, "pr": {"state": "merged", "head_commit": "a" * 40}, "lane": view})

    lane_findings = [finding for finding in problems if finding.code == "LANE_NOT_RECLAIMED"]
    assert len(lane_findings) == 1
    assert "worktree-missing" in lane_findings[0].detail
    assert "deadbeef" in lane_findings[0].detail
    assert "present" in json.dumps(view)


def test_an_item_with_no_lane_has_no_lane_view(repo: Path):
    """Vacuity control: the reporting above is about records, not about the rule firing always."""
    assert lane_view([]) == {}
    assert lane_view(None) == {}
    problems = audit_item({"issue": ISSUE, "pr": {"state": "merged", "head_commit": "a" * 40}, "lane": {}})
    assert [finding for finding in problems if finding.code == "LANE_NOT_RECLAIMED"] == []
