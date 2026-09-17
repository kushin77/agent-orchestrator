"""fleet/cron.py — the worktree-reap marker (issue #830/#901).

The reaper (`scripts/prune-worktrees.sh`, #207/#516) shipped fully tested and
scheduled nowhere: #830 measured 142 worktrees rebuilt because no crontab line
ever ran it. These tests hold `fleet/cron.py`'s new `ao-fleet-reap` marker to
the same contract the existing `ao-fleet-prune`/`ao-fleet-reconcile` markers
already prove: it installs, it is disable/enable-able and uninstallable by
name alone, and a foreign crontab line is never touched by any of it.
"""

from __future__ import annotations

import cron


def test_reap_marker_is_in_markers():
    assert cron.REAP_MARKER == "ao-fleet-reap"
    assert cron.REAP_MARKER in cron.MARKERS


def test_reap_line_carries_its_own_marker_and_uses_the_shipped_tool():
    entry = cron.reap_line()
    assert entry.rstrip().endswith(f"# {cron.REAP_MARKER}")
    assert "scripts/prune-worktrees.sh" in entry
    assert "--apply" in entry  # the scheduled (live) line applies; dev-run's dry-run form does not


def test_reap_line_is_a_sane_daily_schedule():
    # Five cron fields, and not a sub-hourly cadence like the watchdog/reconcile
    # lines — a worktree pile grows on the scale of a day's lanes, matching the
    # existing daily prune line's cadence.
    fields = cron.REAP_SCHEDULE.split()
    assert len(fields) == 5
    minute, hour = fields[0], fields[1]
    assert minute.isdigit() and 0 <= int(minute) <= 59
    assert hour.isdigit() and 0 <= int(hour) <= 23
    assert fields[2:] == ["*", "*", "*"]


def test_install_lines_adds_all_four_marked_lines_and_keeps_foreign_lines():
    foreign = "0 0 * * * echo not-ours # some-other-job"
    merged = cron.install_lines([foreign], interval=2)
    assert foreign in merged
    installed_markers = {marker for marker in cron.MARKERS if any(m.rstrip().endswith(f"# {marker}") for m in merged)}
    assert installed_markers == set(cron.MARKERS)
    assert len(merged) == 1 + len(cron.MARKERS)


def test_install_lines_is_idempotent_on_reinstall():
    once = cron.install_lines([], interval=2)
    twice = cron.install_lines(once, interval=2)
    assert twice == once


def test_reap_marker_recognized_by_is_ours():
    assert cron._is_ours(cron.reap_line())
    assert cron._is_ours("# " + cron.reap_line())  # disabled form


def test_remove_lines_strips_reap_line_and_keeps_foreign_lines():
    foreign = "0 0 * * * echo not-ours # some-other-job"
    merged = cron.install_lines([foreign], interval=2)
    kept, ours = cron.remove_lines(merged)
    assert kept == [foreign]
    assert any(entry.rstrip().endswith(f"# {cron.REAP_MARKER}") for entry in ours)


def test_cmd_reap_dry_run_by_default(monkeypatch):
    calls = []

    def fake_call(command, cwd=None):
        calls.append(command)
        return 0

    monkeypatch.setattr(cron.subprocess, "call", fake_call)
    import argparse

    rc = cron.cmd_reap(argparse.Namespace(apply=False))
    assert rc == 0
    assert calls, "cmd_reap must dispatch the reaper"
    assert "--apply" not in calls[0]
    assert calls[0][0] == "bash"
    assert calls[0][1].endswith("scripts/prune-worktrees.sh")


def test_cmd_reap_apply_passes_through(monkeypatch):
    calls = []

    def fake_call(command, cwd=None):
        calls.append(command)
        return 0

    monkeypatch.setattr(cron.subprocess, "call", fake_call)
    import argparse

    rc = cron.cmd_reap(argparse.Namespace(apply=True))
    assert rc == 0
    assert "--apply" in calls[0]


def test_build_parser_exposes_reap_subcommand():
    parser = cron.build_parser()
    args = parser.parse_args(["reap"])
    assert args.func is cron.cmd_reap
    assert args.apply is False
    args = parser.parse_args(["reap", "--apply"])
    assert args.apply is True
