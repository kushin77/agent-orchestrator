"""Provisioning: one lane, one worktree, one *lane-local* signature.

The load-bearing test here is the two-lane test. Creating a worktree is easy;
what actually isolates lanes is that each lane's commit signature lives in that
worktree's own config. If it ever lands in the shared config, lane B signs lane
A's commits and the identity is decoration.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from governance.isolation.identity import mint
from governance.isolation.worktree import (
    MACHINE_MANAGED_PATHS,
    ProvisionRefused,
    close,
    enable_worktree_config,
    filesystem_type,
    foreign_uncommitted,
    git,
    is_linked_worktree,
    list_records,
    machine_managed_uncommitted,
    provision,
    read_record,
    read_stamped_identity,
    shared_identity,
    uncommitted_paths,
    write_record,
)

from conftest import commit  # noqa: E402  (suite-local helper; conftest bootstraps sys.path)


def test_provision_creates_the_worktree_on_the_issue_branch(repo: Path, tmp_path: Path, mounts: Path):
    identity = mint(263, "copilot-brain", "governance-isolation", worktree_root=tmp_path / "lanes")
    result = provision(identity, repo, base="HEAD", mounts=mounts)

    assert result.created is True
    assert identity.worktree.exists()
    assert is_linked_worktree(identity.worktree)
    branch = git(identity.worktree, "symbolic-ref", "--short", "HEAD").stdout.strip()
    assert branch == "issue-263"


def test_provision_is_idempotent_and_never_forks_a_second_worktree(lane, repo: Path, mounts: Path):
    before = len(git(repo, "worktree", "list").stdout.strip().splitlines())
    again = provision(lane, repo, base="HEAD", mounts=mounts)
    after = len(git(repo, "worktree", "list").stdout.strip().splitlines())

    assert again.created is False
    assert before == after


def test_signature_is_lane_local_and_the_shared_config_is_untouched(lane, repo: Path):
    assert read_stamped_identity(lane.worktree) == (lane.author_name, lane.author_email)
    assert shared_identity(repo) == ("Human Dev", "human@example.com")


def test_two_lanes_hold_two_signatures_at_once(repo: Path, tmp_path: Path, mounts: Path):
    first = mint(263, "copilot-brain", "governance-isolation", worktree_root=tmp_path / "lanes")
    second = mint(264, "qa-sme", "verification", worktree_root=tmp_path / "lanes")
    provision(first, repo, base="HEAD", mounts=mounts)
    provision(second, repo, base="HEAD", mounts=mounts)

    assert read_stamped_identity(first.worktree) == (first.author_name, first.author_email)
    assert read_stamped_identity(second.worktree) == (second.author_name, second.author_email)
    assert first.author_email != second.author_email
    # The shared config is still the human's: neither lane leaked into it.
    assert shared_identity(repo) == ("Human Dev", "human@example.com")


def test_commits_in_a_lane_are_authored_by_the_session(lane):
    commit(lane.worktree, "work.txt", "do the work", trailer=lane.trailer)
    author = git(lane.worktree, "log", "--format=%an <%ae>", "-1").stdout.strip()
    assert author == f"{lane.author_name} <{lane.author_email}>"


def test_a_lane_may_not_be_rooted_inside_the_shared_checkout(repo: Path, mounts: Path):
    inside = mint(263, "copilot-brain", "governance-isolation", worktree_root=repo)
    with pytest.raises(ProvisionRefused):
        provision(inside, repo, base="HEAD", mounts=mounts)


def test_an_unresolvable_base_is_refused(repo: Path, tmp_path: Path, mounts: Path):
    identity = mint(263, "copilot-brain", "governance-isolation", worktree_root=tmp_path / "lanes")
    with pytest.raises(ProvisionRefused):
        provision(identity, repo, base="origin/master", mounts=mounts)


def test_close_refuses_to_discard_uncommitted_work(lane, repo: Path):
    (lane.worktree / "uncommitted.txt").write_text("work in progress\n", encoding="utf-8")
    kept = close(lane, repo)
    assert kept and "uncommitted" in kept[0]
    # The refusal NAMES the paths it refuses on: an operator should not have to
    # guess which file stood in the way of the reclaim.
    assert "uncommitted.txt" in kept[0]
    assert lane.worktree.exists()


def board_state(lane, value: int) -> None:
    """Write the fleet's machine-managed board state into a lane worktree."""
    board = lane.worktree / ".board"
    board.mkdir(parents=True, exist_ok=True)
    (board / "focus.json").write_text(json.dumps({"active_epic": value}) + "\n", encoding="utf-8")


def test_the_declared_machine_managed_set_is_exactly_the_board_state():
    """Widening what a reclaim may discard is a deliberate act, not a side effect."""
    assert MACHINE_MANAGED_PATHS == (".board/focus.json",)


def test_a_lane_dirty_only_in_machine_managed_state_is_reclaimed(lane, repo: Path):
    """The gate itself dirties `.board/focus.json`; refusing on it wedged the close-out (#834).

    Measured: `fleet/tests/test_brain.py` — which `make verify` runs — drives the
    real brain loop, whose `advance_epic_focus()` rewrites the checkout's own
    `.board/focus.json` (committed `active_epic: 707` -> working tree `160`). A
    lane is therefore dirty in exactly this path the moment the close-out's own
    verification step finishes, and step 8 then refused the tree step 2 had just
    written to. This test is that wedge, provoked: the board file is committed,
    then rewritten, then the lane is reclaimed.
    """
    board_state(lane, 707)
    git(lane.worktree, "add", ".board/focus.json")
    git(lane.worktree, "commit", "-q", "-m", "board state")
    board_state(lane, 160)

    assert uncommitted_paths(lane.worktree) == [".board/focus.json"]
    assert machine_managed_uncommitted(lane.worktree) == [".board/focus.json"]
    assert foreign_uncommitted(lane.worktree) == []
    assert close(lane, repo) == []
    assert not lane.worktree.exists()
    assert read_record(lane.session_id, repo) is None


def test_a_mixed_tree_refuses_on_the_lanes_own_file_only(lane, repo: Path):
    """The narrowing is not a blanket --force: lane work still refuses, and is named."""
    board_state(lane, 707)
    git(lane.worktree, "add", ".board/focus.json")
    git(lane.worktree, "commit", "-q", "-m", "board state")
    board_state(lane, 160)
    (lane.worktree / "wip.txt").write_text("half-finished\n", encoding="utf-8")

    kept = close(lane, repo)

    assert kept and "wip.txt" in kept[0]
    assert ".board/focus.json" not in kept[0], "the refusal names what it refuses on, not what it ignores"
    assert lane.worktree.exists()
    assert read_record(lane.session_id, repo) is not None


def test_force_actually_forces_the_removal(lane, repo: Path):
    """A forced reclaim must reach git: `git worktree remove` runs its own dirty test.

    Measured while fixing #834: `force=True` skipped only this module's check, so
    the removal still refused with `contains modified or untracked files, use
    --force to delete it` — the flag the caller was told to pass had no path to
    git. The lane is kept and its record preserved when that happens, which is at
    least not a silent loss, but the documented remedy did not work.
    """
    (lane.worktree / "wip.txt").write_text("half-finished\n", encoding="utf-8")

    assert close(lane, repo, force=True) == []
    assert not lane.worktree.exists()
    assert read_record(lane.session_id, repo) is None


def test_uncommitted_paths_reads_untracked_and_spaced_names(lane):
    """A quoted path could never match a declared set, so the read must be NUL-terminated."""
    (lane.worktree / "two words.txt").write_text("wip\n", encoding="utf-8")
    (lane.worktree / "untracked.txt").write_text("wip\n", encoding="utf-8")
    assert sorted(uncommitted_paths(lane.worktree)) == ["two words.txt", "untracked.txt"]
    assert foreign_uncommitted(lane.worktree) == ["two words.txt", "untracked.txt"]


def test_close_removes_a_clean_lane_and_its_record(lane, repo: Path):
    assert close(lane, repo) == []
    assert not lane.worktree.exists()
    assert read_record(lane.session_id, repo) is None


def test_records_are_scoped_to_the_repository(lane, repo: Path, tmp_path: Path):
    other = tmp_path / "other"
    other.mkdir()
    assert read_record(lane.session_id, repo) is not None
    assert list_records(other) == []


def test_worktree_config_can_be_disabled_and_the_identity_disappears(lane, repo: Path):
    """Without per-worktree config there is no lane-local identity to read.

    This is the failure mode the module exists to prevent, so it is asserted
    rather than assumed: an inherited signature must read as *absent*, never as
    a valid one.
    """
    enable_worktree_config(repo)
    assert read_stamped_identity(lane.worktree) is not None
    git(repo, "config", "--unset", "extensions.worktreeConfig")
    assert read_stamped_identity(lane.worktree) is None


def test_record_survives_a_reprovision(lane, repo: Path, mounts: Path):
    write_record(lane, repo)
    again = provision(lane, repo, base="HEAD", mounts=mounts)
    write_record(again.identity, repo)
    assert read_record(lane.session_id, repo) == lane


def declared_mounts(tmp_path: Path, tmpfs_at: Path | None = None) -> Path:
    """A mount table declaring ``tmpfs_at`` RAM-backed (issue #516).

    The mount point must already exist, because the probe classifies a path by
    its deepest existing ancestor — the filesystem a worktree would be created
    on, which is exactly the thing being guarded.
    """
    table = tmp_path / "mounts-with-tmpfs"
    lines = ["/dev/root / ext4 rw,relatime 0 0"]
    if tmpfs_at is not None:
        lines.append(f"tmpfs {tmpfs_at} tmpfs rw,nosuid,nodev 0 0")
    table.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return table


def test_filesystem_type_reads_the_deepest_existing_mount_point(tmp_path: Path):
    ram = tmp_path / "ram"
    ram.mkdir()
    mounts = declared_mounts(tmp_path, tmpfs_at=ram)

    # The lane root does not exist yet: it is classified by the filesystem it
    # would be created on, not by a path that is already there.
    assert filesystem_type(ram / "ao-516-lane", mounts) == "tmpfs"
    assert filesystem_type(tmp_path, mounts) == "ext4"


def test_provision_refuses_a_ram_backed_worktree_root(repo: Path, tmp_path: Path):
    """A lane on tmpfs costs RAM and inodes instead of disk and is lost on reboot."""
    ram = tmp_path / "ram"
    ram.mkdir()
    identity = mint(516, "copilot-brain", "worktree-reclaim", worktree_root=ram / "lanes")

    with pytest.raises(ProvisionRefused, match="lane-worktree-on-tmpfs"):
        provision(identity, repo, base="HEAD", mounts=declared_mounts(tmp_path, tmpfs_at=ram))

    assert not identity.worktree.exists(), "the refusal must land before anything is created"
    assert str(identity.worktree) not in git(repo, "worktree", "list").stdout
    assert read_record(identity.session_id, repo) is None


def test_the_same_root_is_provisioned_once_tmpfs_is_explicitly_accepted(repo: Path, tmp_path: Path):
    """Vacuity control: the refusal is about the filesystem, not about the path."""
    ram = tmp_path / "ram"
    ram.mkdir()
    mounts = declared_mounts(tmp_path, tmpfs_at=ram)
    identity = mint(516, "copilot-brain", "worktree-reclaim", worktree_root=ram / "lanes")

    result = provision(identity, repo, base="HEAD", mounts=mounts, allow_tmpfs=True)

    assert result.created is True
    assert identity.worktree.exists()


def test_a_disk_backed_root_is_not_refused(repo: Path, tmp_path: Path):
    mounts = declared_mounts(tmp_path)
    identity = mint(516, "copilot-brain", "worktree-reclaim", worktree_root=tmp_path / "lanes")

    result = provision(identity, repo, base="HEAD", mounts=mounts)

    assert result.created is True
    assert identity.worktree.exists()
