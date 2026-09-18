"""governance/reconcile/cli.py `watch --once` — the #977 lease wrap (issue #706 D5).

Lives in `fleet/tests/` (not `governance/reconcile/tests/`) because this lane
owns `fleet/tests/**`, not the reconcile package's own test directory; the
change under test is scoped to the lease wrap in `cmd_watch`, not the
reconcile/sweep logic those other tests already cover.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "fleet") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "fleet"))

from governance.reconcile import cli  # noqa: E402


def _args(root: Path, *, once: bool = True, apply: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        root=str(root),
        ttl_minutes=15.0,
        interval_seconds=0.0,
        apply=apply,
        once=once,
        json=False,
    )


def test_watch_once_skips_the_lease_entirely_on_a_dry_run(tmp_path, monkeypatch):
    """#1126: only `--apply` takes the lease — a dry run never writes, so it
    must not need the single-writer wrap either (fleet/prune.py's own
    dry-run-untouched convention). `make_lease` must not even be CALLED.
    """

    def _refuse(**kwargs):
        raise AssertionError("a dry-run watch --once must never call make_lease")

    from governance.reconcile.sweep import SweepReport

    monkeypatch.setattr(cli.lease, "make_lease", _refuse)
    monkeypatch.setattr(cli, "sweep", lambda *a, **k: SweepReport())

    rc = cli.cmd_watch(_args(tmp_path, apply=False))

    assert rc == cli.EXIT_OK


def test_watch_once_noops_and_logs_skip_when_lease_lost(tmp_path, monkeypatch, capsys):
    class _LosingLease:
        def acquire(self):
            return False

        def release(self):  # pragma: no cover
            raise AssertionError("release must not be called when acquire never won")

    monkeypatch.setattr(cli.lease, "make_lease", lambda **kwargs: _LosingLease())
    monkeypatch.setattr(
        cli, "sweep", lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("must not sweep when the lease was lost")
        )
    )

    rc = cli.cmd_watch(_args(tmp_path))

    assert rc == cli.EXIT_OK
    out = capsys.readouterr().out
    assert '"status": "skipped"' in out
    assert '"job": "reconcile"' in out


def test_watch_once_releases_the_lease_even_when_sweep_raises(tmp_path, monkeypatch):
    released = []

    class _WinningLease:
        def acquire(self):
            return True

        def release(self):
            released.append(True)

    monkeypatch.setattr(cli.lease, "make_lease", lambda **kwargs: _WinningLease())
    monkeypatch.setattr(cli, "sweep", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))

    rc = cli.cmd_watch(_args(tmp_path))

    # The worker survives a bad pass (existing contract); the lease must still
    # be released so the next tick — or the other replica — is not wedged.
    assert rc == cli.EXIT_OK
    assert released == [True]
