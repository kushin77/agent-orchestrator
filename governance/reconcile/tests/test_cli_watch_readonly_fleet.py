"""Regression: `watch --once` (dry-run) must never touch a read-only `.fleet`.

EPIC #706's dev-run harness (`infra/fleet/docker-compose.agent-cron.yml`) mounts
`.fleet` `read_only: true` by design (D1/D2). The reconcile rung's single-writer
lease (#977/#978, landed in `fed4e7d4`) used to call `lease.make_lease(...)
.acquire()` unconditionally in `cmd_watch` — `fcntl_try_lock` does `os.open(...,
O_CREAT)` on the lease file, which raises `OSError: [Errno 30] Read-only file
system` the moment `.fleet` is read-only, killing the dry-run pass before it
completed one iteration (root cause of the `check-fleet-cron-dev-run.sh` red
tracked in #1034, fixed by `ea730bee` / #1126).

#1148's "second finding" (`Parent: #706`) re-raised this as still-open: "the
`agent-cron` compose service mounts `.fleet` read-only... so the read-only
service shape can never answer 200." That claim predates (or overlooked) the
`if args.apply:` guard already in `cmd_watch` — a dry-run pass (`--apply` not
given, which is exactly what `infra/fleet/inventory.yaml`'s `ao-fleet-reconcile`
dev-run role dispatches) never calls `lease.make_lease(...)` at all, so it never
touches `.fleet`, read-only or not. This test pins that down as an executable
fact, against a real (non-empty) `.fleet` tree, so the guard cannot silently
regress to the unconditional-acquire shape without a red here.
"""

from __future__ import annotations

import shutil
import stat
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from governance.reconcile import cli  # noqa: E402


def _chmod_tree_readonly(path: Path) -> None:
    for p in [path, *path.rglob("*")]:
        mode = stat.S_IMODE(p.stat().st_mode)
        p.chmod(mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


def _chmod_tree_writable(path: Path) -> None:
    for p in [path, *path.rglob("*")]:
        mode = stat.S_IMODE(p.stat().st_mode)
        p.chmod(mode | stat.S_IWUSR)


@pytest.fixture
def readonly_fleet_repo(tmp_path: Path) -> Path:
    """A repo root whose `.fleet` mirrors this checkout's real state (non-empty:
    heartbeats, leases, board-reports.json if present) and is then made
    read-only, the way the dev-run harness's bind mount is (`read_only: true`).
    """
    fleet_src = REPO_ROOT / ".fleet"
    board_src = REPO_ROOT / ".board"
    fleet_dst = tmp_path / ".fleet"
    board_dst = tmp_path / ".board"
    if fleet_src.is_dir():
        shutil.copytree(fleet_src, fleet_dst)
    else:
        fleet_dst.mkdir()
    if board_src.is_dir():
        shutil.copytree(board_src, board_dst)
    else:
        board_dst.mkdir()
    _chmod_tree_readonly(fleet_dst)
    yield tmp_path
    _chmod_tree_writable(fleet_dst)


def test_dry_run_watch_once_never_touches_readonly_fleet(readonly_fleet_repo: Path) -> None:
    """`watch --once` with no `--apply` (the dev-run dispatch form) succeeds and
    writes nothing under a read-only `.fleet` — the exact shape #1148's second
    finding said could "never answer 200"."""
    fleet_dir = readonly_fleet_repo / ".fleet"
    before = sorted(p.relative_to(fleet_dir) for p in fleet_dir.rglob("*"))

    rc = cli.main(["--root", str(readonly_fleet_repo), "watch", "--once", "--interval-seconds", "1"])

    assert rc == cli.EXIT_OK
    after = sorted(p.relative_to(fleet_dir) for p in fleet_dir.rglob("*"))
    assert before == after, "a dry-run pass must not create or remove any path under .fleet"
    assert not (fleet_dir / "reconcile.lease").exists(), (
        "a dry-run pass (--apply not given) must never take the reconcile lease"
    )


def test_apply_watch_once_does_take_the_lease(tmp_path: Path) -> None:
    """The inverse: with `--apply`, the lease IS attempted. Proves the dry-run
    test above is exercising the guard (`if args.apply:`) and not a lease that
    never fires at all — removing that guard would make this one indistinguishable
    from a no-op, but would turn the read-only test above from a pass into an
    unhandled `OSError`.
    """
    fleet_dir = tmp_path / ".fleet"
    board_dir = tmp_path / ".board"
    fleet_dir.mkdir()
    board_dir.mkdir()

    rc = cli.main(["--root", str(tmp_path), "watch", "--once", "--apply", "--interval-seconds", "1"])

    assert rc == cli.EXIT_OK
    assert (fleet_dir / "reconcile.lease").exists(), "--apply must acquire (and create) the reconcile lease"
