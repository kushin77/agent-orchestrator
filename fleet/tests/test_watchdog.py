"""The fleet watchdog and its crontab manager — the cron-owned keeper."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cron  # noqa: E402
import watchdog  # noqa: E402


def _beat(commit="abc1234", age=10):
    return {"pid": 111, "state": "idle", "commit": commit, "ts": "2026-09-13T00:00:00Z"}


# --- the decision -------------------------------------------------------------


def test_decide_classifies_all_five_states(monkeypatch):
    monkeypatch.setattr(watchdog.channel, "heartbeat_age_seconds", lambda beat, moment=None: 10)
    assert watchdog.decide(None, None, "base") == ("missing", "no loop process")
    assert watchdog.decide(111, None, "base") == ("stale", "no heartbeat from a live loop")
    assert watchdog.decide(111, _beat(commit="old0000"), "base1111") == (
        "drifted",
        "running old0000, origin/master base1111",
    )
    assert watchdog.decide(111, _beat(commit="base1111"), "base1111") == ("healthy", "")
    # #739 / AO-GR-25: an unreadable baseline is CANNOT-ASSESS, never healthy.
    state, reason = watchdog.decide(111, _beat(commit="base1111"), "unknown")
    assert state == watchdog.CANNOT_ASSESS
    assert "origin/master" in reason


def test_decide_flags_a_stale_beat(monkeypatch):
    monkeypatch.setattr(watchdog.channel, "heartbeat_age_seconds", lambda beat, moment=None: 999)
    state, _reason = watchdog.decide(111, _beat(), "head")
    assert state == "stale"


# --- the measured defect (#739, AO-GR-25) -------------------------------------
#
# The 2026-09-14 measurement: the sister loop (pid 17797, started 19:18Z) was
# executing code from before a fix that merged at ~23:00Z, while the watchdog
# logged `sister: healthy` every tick. Both sides of the comparison were the
# *shared checkout* — `running == head == 592b132` — so the loop's own start
# commit read back as the baseline it was judged against.


def test_local_stale_checkout_masquerading_as_running_commit_is_drifted(monkeypatch):
    """THE regression test: running == local-stale, but != origin/master ⇒ DRIFTED.

    This is the exact measured case. `head_commit()` (the local checkout) is
    asserted *equal* to the loop's commit, so the only correct way to reach
    DRIFTED is to compare against the remote — the comparison the bug got wrong.
    """
    monkeypatch.setattr(watchdog.channel, "heartbeat_age_seconds", lambda beat, moment=None: 10)
    stale = "592b132"
    monkeypatch.setattr(watchdog.channel, "head_commit", lambda: stale)
    monkeypatch.setattr(watchdog.channel, "remote_head_commit", lambda: "47a068b")
    state, reason = watchdog.decide(111, _beat(commit=stale), "47a068b")
    assert state == "drifted", "a loop on pre-fix code must not read healthy just because the checkout is behind too"
    assert reason == f"running {stale}, origin/master 47a068b"


def test_an_unreadable_baseline_is_cannot_assess_not_healthy(monkeypatch):
    """Fail-closed: the old `head != "unknown"` guard disabled drift detection."""
    monkeypatch.setattr(watchdog.channel, "heartbeat_age_seconds", lambda beat, moment=None: 10)
    for baseline in ("unknown", ""):
        state, reason = watchdog.decide(111, _beat(commit="592b132"), baseline)
        assert state == watchdog.CANNOT_ASSESS, f"baseline {baseline!r} must not read healthy"
        assert state != watchdog.HEALTHY
        assert "unreadable" in reason


def test_a_loop_reporting_no_commit_is_cannot_assess(monkeypatch):
    """The other half of fail-closed: we cannot compare a commit nobody reported."""
    monkeypatch.setattr(watchdog.channel, "heartbeat_age_seconds", lambda beat, moment=None: 10)
    state, reason = watchdog.decide(111, _beat(commit="unknown"), "47a068b")
    assert state == watchdog.CANNOT_ASSESS
    assert "47a068b" in reason


def test_a_current_loop_is_healthy(monkeypatch):
    monkeypatch.setattr(watchdog.channel, "heartbeat_age_seconds", lambda beat, moment=None: 10)
    assert watchdog.decide(111, _beat(commit="47a068b"), "47a068b") == ("healthy", "")


def test_the_watchdog_line_names_both_commits(monkeypatch):
    """Requirement 3: an operator must see the comparison that was made."""
    monkeypatch.setattr(watchdog.channel, "heartbeat_age_seconds", lambda beat, moment=None: 10)
    monkeypatch.setattr(watchdog, "loop_pid", lambda pattern: 111)
    monkeypatch.setattr(watchdog, "read_beat", lambda path: _beat(commit="592b132"))
    monkeypatch.setattr(watchdog, "respawn", lambda *a, **k: True)
    monkeypatch.setattr(
        watchdog.channel,
        "capability_line",
        lambda finding: f"{finding.rung}: {finding.case}",
    )
    line = watchdog.rung_action(
        "brain", "fleet/brain.py", "fleet/brain.sh", Path("/tmp/x"), False, "47a068b"
    )
    assert "running 592b132" in line
    assert "origin/master 47a068b" in line


def test_a_healthy_line_names_both_commits(monkeypatch):
    monkeypatch.setattr(watchdog.channel, "heartbeat_age_seconds", lambda beat, moment=None: 10)
    monkeypatch.setattr(watchdog, "loop_pid", lambda pattern: 111)
    monkeypatch.setattr(watchdog, "read_beat", lambda path: _beat(commit="47a068b"))
    monkeypatch.setattr(
        watchdog.channel, "capability_line", lambda finding: f"{finding.rung}: {finding.case}"
    )
    line = watchdog.rung_action(
        "brain", "fleet/brain.py", "fleet/brain.sh", Path("/tmp/x"), False, "47a068b"
    )
    assert line.startswith("brain: healthy (running 47a068b, origin/master 47a068b)")


def test_watchdog_once_exits_2_when_the_baseline_is_unreadable(monkeypatch, capsys):
    """CANNOT-ASSESS is exit 2 — never 0. A control that cannot fail is a formality."""
    monkeypatch.setattr(watchdog.channel, "heartbeat_age_seconds", lambda beat, moment=None: 10)
    monkeypatch.setattr(watchdog.channel, "remote_head_commit", lambda: "unknown")
    monkeypatch.setattr(watchdog, "loop_pid", lambda pattern: 111)
    monkeypatch.setattr(watchdog, "read_beat", lambda path: _beat(commit="592b132"))
    monkeypatch.setattr(watchdog, "respawn", lambda *a, **k: True)
    monkeypatch.setattr(watchdog, "monitor_missing", lambda: False)
    assert watchdog.watchdog_once() == watchdog.channel.EXIT_CANNOT_ASSESS
    assert "cannot-assess" in capsys.readouterr().out


def test_watchdog_once_exits_1_over_2_when_a_respawn_also_failed(monkeypatch, capsys):
    """A known failure outranks an unassessable one."""
    monkeypatch.setattr(watchdog.channel, "heartbeat_age_seconds", lambda beat, moment=None: 10)
    monkeypatch.setattr(watchdog.channel, "remote_head_commit", lambda: "unknown")
    monkeypatch.setattr(watchdog, "loop_pid", lambda pattern: None)
    monkeypatch.setattr(watchdog, "read_beat", lambda path: None)
    monkeypatch.setattr(watchdog, "loop_pids", lambda pattern: [])
    monkeypatch.setattr(watchdog, "RESPAWN_VERIFY_SECONDS", 0.0)
    monkeypatch.setattr(watchdog, "spawn", lambda name, command: None)
    monkeypatch.setattr(watchdog, "monitor_missing", lambda: False)
    assert watchdog.watchdog_once() == watchdog.channel.EXIT_NOT_OK
    assert "RESPAWN FAILED" in capsys.readouterr().out


def test_a_missing_loop_is_respawned(monkeypatch):
    monkeypatch.setattr(watchdog, "loop_pid", lambda pattern: None)
    monkeypatch.setattr(watchdog, "read_beat", lambda path: None)
    calls = []
    monkeypatch.setattr(watchdog, "respawn", lambda pattern, script, name="": calls.append((script, name)) or True)
    line = watchdog.rung_action("sister", "fleet/terminal.py", "fleet/terminal.sh", Path("/tmp/x"), False, "head")
    # The sister's log follows its RUNG name, not its launcher's filename:
    # `terminal.sh` starts the rung the operator knows as `sister`.
    assert "missing" in line and calls == [("fleet/terminal.sh", "sister")]


def test_a_drifted_sister_with_a_run_in_flight_is_left_alone(monkeypatch):
    """The watchdog's one rule: never restart a run just to update code."""
    monkeypatch.setattr(watchdog.channel, "heartbeat_age_seconds", lambda beat, moment=None: 10)
    monkeypatch.setattr(watchdog, "loop_pid", lambda pattern: 111)
    monkeypatch.setattr(watchdog, "read_beat", lambda path: _beat(commit="old0000"))
    monkeypatch.setattr(watchdog, "run_in_flight", lambda: True)
    monkeypatch.setattr(watchdog, "respawn", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not respawn")))
    line = watchdog.rung_action("sister", "fleet/terminal.py", "fleet/terminal.sh", Path("/tmp/x"), False, "head1111")
    assert "left alone" in line


def test_a_drifted_idle_sister_is_respawned(monkeypatch):
    monkeypatch.setattr(watchdog.channel, "heartbeat_age_seconds", lambda beat, moment=None: 10)
    monkeypatch.setattr(watchdog, "loop_pid", lambda pattern: 111)
    monkeypatch.setattr(watchdog, "read_beat", lambda path: _beat(commit="old0000"))
    monkeypatch.setattr(watchdog, "run_in_flight", lambda: False)
    calls = []
    monkeypatch.setattr(watchdog, "respawn", lambda pattern, script, name="": calls.append(script) or True)
    watchdog.rung_action("sister", "fleet/terminal.py", "fleet/terminal.sh", Path("/tmp/x"), False, "head1111")
    assert calls == ["fleet/terminal.sh"]


def test_a_cannot_assess_rung_is_respawned_but_says_why(monkeypatch):
    """Fail-closed still acts: it cannot certify the rung, so it respawns and says why."""
    monkeypatch.setattr(watchdog.channel, "heartbeat_age_seconds", lambda beat, moment=None: 10)
    monkeypatch.setattr(watchdog, "loop_pid", lambda pattern: 111)
    monkeypatch.setattr(watchdog, "read_beat", lambda path: _beat(commit="old0000"))
    monkeypatch.setattr(watchdog, "run_in_flight", lambda: False)
    calls = []
    monkeypatch.setattr(watchdog, "respawn", lambda pattern, script, name="": calls.append(script) or True)
    line = watchdog.rung_action(
        "sister", "fleet/terminal.py", "fleet/terminal.sh", Path("/tmp/x"), False, "unknown"
    )
    assert calls == ["fleet/terminal.sh"], "an unassessable rung is not left running unjudged"
    assert "respawned" in line
    assert "cannot-assess" in line
    assert "unreadable" in line


# --- the capture log (A: the rung's stream must survive the spawn) ------------


def test_rung_log_is_the_per_rung_capture_path(tmp_path, monkeypatch):
    monkeypatch.setattr(watchdog, "FLEET_DIR", tmp_path)
    assert watchdog.rung_log("brain") == tmp_path / "brain.log"
    assert watchdog.rung_log("sister") == tmp_path / "sister.log"
    assert watchdog.rung_log("monitor") == tmp_path / "monitor.log"


def test_open_log_creates_the_file_and_appends(tmp_path, monkeypatch):
    """Append, never truncate: a respawn must not erase the run before it."""
    monkeypatch.setattr(watchdog, "FLEET_DIR", tmp_path)
    with watchdog.open_log("brain") as fh:
        fh.write("first run\n")
    with watchdog.open_log("brain") as fh:
        fh.write("second run\n")
    assert (tmp_path / "brain.log").read_text(encoding="utf-8") == "first run\nsecond run\n"


def test_open_log_is_line_buffered_so_the_window_is_never_stale(tmp_path, monkeypatch):
    monkeypatch.setattr(watchdog, "FLEET_DIR", tmp_path)
    handle = watchdog.open_log("brain")
    try:
        assert handle.line_buffering is True
    finally:
        handle.close()


def test_spawn_appends_both_streams_to_the_rung_log(tmp_path, monkeypatch):
    """The measured gap: stdout and stderr used to go to DEVNULL, so nothing
    about the brain was visible from any window or log."""
    monkeypatch.setattr(watchdog, "FLEET_DIR", tmp_path)
    calls = []

    def fake_popen(*args, **kwargs):
        calls.append((args, kwargs))
        return type("Proc", (), {"pid": 999})()

    monkeypatch.setattr(watchdog.subprocess, "Popen", fake_popen)
    watchdog.spawn("sister", ["setsid", "bash", "fleet/terminal.sh"])
    _args, kwargs = calls[0]
    assert kwargs["stdout"] is not subprocess.DEVNULL
    assert kwargs["stderr"] is subprocess.STDOUT
    assert kwargs["stdout"].name == str(tmp_path / "sister.log")


def test_respawn_starts_the_rung_into_its_capture_log(monkeypatch):
    monkeypatch.setattr(watchdog, "loop_pid", lambda pattern: None)
    monkeypatch.setattr(watchdog, "loop_pids", lambda pattern: [999])
    monkeypatch.setattr(watchdog, "RESPAWN_SETTLE_SECONDS", 0.0)
    spawned = []
    monkeypatch.setattr(watchdog, "spawn", lambda name, command: spawned.append((name, command)))
    assert watchdog.respawn("fleet/terminal.py", "fleet/terminal.sh", "sister") is True
    assert spawned == [("sister", ["setsid", "bash", str(watchdog.ROOT / "fleet" / "terminal.sh")])]


def test_a_default_rung_name_falls_back_to_the_launcher_stem(monkeypatch):
    monkeypatch.setattr(watchdog, "loop_pid", lambda pattern: None)
    monkeypatch.setattr(watchdog, "loop_pids", lambda pattern: [999])
    monkeypatch.setattr(watchdog, "RESPAWN_SETTLE_SECONDS", 0.0)
    spawned = []
    monkeypatch.setattr(watchdog, "spawn", lambda name, command: spawned.append(name))
    watchdog.respawn("fleet/brain.py", "fleet/brain.sh")
    assert spawned == ["brain"]


# --- respawn verification (issue #276: a claim of success must be measured) ---


def test_respawn_reports_failure_when_the_rung_never_comes_up(monkeypatch):
    """P1a: a no-op spawn must NOT read as `respawned`."""
    monkeypatch.setattr(watchdog, "loop_pid", lambda pattern: None)
    monkeypatch.setattr(watchdog, "loop_pids", lambda pattern: [])
    monkeypatch.setattr(watchdog, "RESPAWN_VERIFY_SECONDS", 0.0)
    spawned = []
    monkeypatch.setattr(watchdog, "spawn", lambda name, command: spawned.append(name))
    assert watchdog.respawn("fleet/terminal.py", "fleet/terminal.sh", "sister") is False
    assert spawned == ["sister"], "the spawn was attempted"


def test_respawn_reports_failure_when_spawn_raises(monkeypatch):
    """P1b: an OSError from spawn is reported, not allowed to crash the pass."""
    monkeypatch.setattr(watchdog, "loop_pid", lambda pattern: None)

    def boom(*args, **kwargs):
        raise OSError("No space left on device")

    monkeypatch.setattr(watchdog, "spawn", boom)
    assert watchdog.respawn("fleet/terminal.py", "fleet/terminal.sh", "sister") is False


def test_rung_came_up_requires_the_rung_to_survive_the_settle_window(monkeypatch):
    """A rung that starts and immediately dies (singleton refusal) is a failure."""
    seq = [[111], []]
    monkeypatch.setattr(watchdog, "loop_pids", lambda pattern: seq.pop(0) if seq else [])
    now = {"t": 0.0}
    ok = watchdog.rung_came_up(
        "fleet/terminal.py",
        None,
        window=10.0,
        settle=1.0,
        clock=lambda: now["t"],
        sleep=lambda s: now.__setitem__("t", now["t"] + s),
    )
    assert ok is False


def test_rung_came_up_is_true_when_the_process_survives(monkeypatch):
    monkeypatch.setattr(watchdog, "loop_pids", lambda pattern: [111])
    now = {"t": 0.0}
    ok = watchdog.rung_came_up(
        "fleet/terminal.py",
        None,
        window=10.0,
        settle=1.0,
        clock=lambda: now["t"],
        sleep=lambda s: now.__setitem__("t", now["t"] + s),
    )
    assert ok is True


def test_rung_action_surfaces_respawn_failed(monkeypatch):
    """The `RESPAWN FAILED` branch is reachable for the loop rungs now."""
    monkeypatch.setattr(watchdog, "loop_pid", lambda pattern: None)
    monkeypatch.setattr(watchdog, "read_beat", lambda path: None)
    monkeypatch.setattr(watchdog, "loop_pids", lambda pattern: [])
    monkeypatch.setattr(watchdog, "RESPAWN_VERIFY_SECONDS", 0.0)
    monkeypatch.setattr(watchdog, "spawn", lambda name, command: None)
    line = watchdog.rung_action("sister", "fleet/terminal.py", "fleet/terminal.sh", Path("/tmp/x"), False, "head")
    assert "RESPAWN FAILED" in line


def test_watchdog_once_exits_nonzero_when_a_rung_respawn_fails(monkeypatch, capsys):
    monkeypatch.setattr(watchdog.channel, "head_commit", lambda: "head1111")
    monkeypatch.setattr(watchdog, "rung_action", lambda *a, **k: "sister: missing (no loop process) — RESPAWN FAILED")
    monkeypatch.setattr(watchdog, "monitor_missing", lambda: False)
    assert watchdog.watchdog_once() == 1
    assert "RESPAWN FAILED" in capsys.readouterr().out


# --- the monitor rung ---------------------------------------------------------


def test_monitor_missing_reflects_process_presence(monkeypatch):
    monkeypatch.setattr(watchdog, "loop_pid", lambda pattern: None)
    assert watchdog.monitor_missing() is True
    monkeypatch.setattr(watchdog, "loop_pid", lambda pattern: 123)
    assert watchdog.monitor_missing() is False


def test_start_monitor_spawns_a_detached_python_process(monkeypatch):
    monkeypatch.setattr(watchdog, "loop_pids", lambda pattern: [999])
    monkeypatch.setattr(watchdog, "RESPAWN_SETTLE_SECONDS", 0.0)
    calls = []

    def fake_popen(*args, **kwargs):
        calls.append((args, kwargs))
        return type("Proc", (), {"pid": 999})()

    monkeypatch.setattr(watchdog.subprocess, "Popen", fake_popen)
    assert watchdog.start_monitor() is True
    assert calls and "fleet/monitor.py" in str(calls[0][0][0])
    assert calls[0][1]["stdout"].name.endswith("monitor.log")


def test_start_monitor_reports_failure_when_spawn_fails(monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("spawn denied")

    monkeypatch.setattr(watchdog.subprocess, "Popen", boom)
    assert watchdog.start_monitor() is False


def test_start_monitor_reports_failure_when_the_monitor_never_comes_up(monkeypatch):
    monkeypatch.setattr(watchdog, "loop_pids", lambda pattern: [])
    monkeypatch.setattr(watchdog, "RESPAWN_VERIFY_SECONDS", 0.0)
    monkeypatch.setattr(watchdog.subprocess, "Popen", lambda *a, **k: type("Proc", (), {"pid": 1})())
    assert watchdog.start_monitor() is False


def test_watchdog_once_ensures_a_missing_monitor_is_respawned(monkeypatch, capsys):
    monkeypatch.setattr(watchdog.channel, "head_commit", lambda: "head1111")
    monkeypatch.setattr(watchdog, "rung_action", lambda *a, **k: "healthy")
    monkeypatch.setattr(watchdog, "monitor_missing", lambda: True)
    monkeypatch.setattr(watchdog, "start_monitor", lambda: True)
    assert watchdog.watchdog_once() == 0
    assert "monitor: missing — respawned" in capsys.readouterr().out


def test_watchdog_once_reports_a_failed_monitor_respawn(monkeypatch, capsys):
    monkeypatch.setattr(watchdog.channel, "head_commit", lambda: "head1111")
    monkeypatch.setattr(watchdog, "rung_action", lambda *a, **k: "healthy")
    monkeypatch.setattr(watchdog, "monitor_missing", lambda: True)
    monkeypatch.setattr(watchdog, "start_monitor", lambda: False)
    assert watchdog.watchdog_once() == 1
    assert "RESPAWN FAILED" in capsys.readouterr().out


def test_watchdog_once_leaves_a_present_monitor_alone(monkeypatch, capsys):
    monkeypatch.setattr(watchdog.channel, "head_commit", lambda: "head1111")
    monkeypatch.setattr(watchdog, "rung_action", lambda *a, **k: "healthy")
    monkeypatch.setattr(watchdog, "monitor_missing", lambda: False)
    monkeypatch.setattr(
        watchdog, "start_monitor", lambda: (_ for _ in ()).throw(AssertionError("must not start"))
    )
    assert watchdog.watchdog_once() == 0
    assert "monitor: healthy" in capsys.readouterr().out


# --- the crontab manager ------------------------------------------------------


def test_the_cron_line_is_identifiable_and_self_contained():
    ln = cron.line(2)
    assert ln.endswith("# ao-fleet-watchdog")
    assert "fleet/watchdog.py run" in ln
    assert "*/2 * * * *" in ln


def test_install_replaces_an_existing_line_and_keeps_others(monkeypatch):
    state = {"written": None}
    monkeypatch.setattr(cron, "read_crontab", lambda: [
        "0 2 * * * other-job # other",
        "*/5 * * * * old fleet line # ao-fleet-watchdog",
    ])
    monkeypatch.setattr(cron, "write_crontab", lambda lines: state.__setitem__("written", lines))
    assert cron.cmd_install(type("Args", (), {"interval": 2})()) == 0
    written = state["written"]
    # The install owns TWO marked lines now — the watchdog and the `.fleet`
    # retention job (issue #280) — but the properties are unchanged: the foreign
    # line survives, the stale watchdog line is replaced, and exactly one
    # watchdog line remains.
    assert written[0] == "0 2 * * * other-job # other"
    watchdog_lines = [entry for entry in written if entry.endswith("# ao-fleet-watchdog")]
    assert len(watchdog_lines) == 1
    assert watchdog_lines[0].startswith("*/2 * * * *")
    assert "old fleet line" not in "\n".join(written)
    assert [entry for entry in written if entry.endswith("# ao-fleet-prune")] == [cron.prune_line()]


def test_the_install_owns_the_reconcile_worker_line_too():
    """The orphan sweep is automated by cron (issue #304), on the watchdog's cadence.

    It is a marked line of its own rather than a step inside the watchdog's pass:
    a sweep acts on real lanes, and the watchdog's own tests must never be able to
    reclaim a live worktree as a side effect of a pass.
    """
    entries = cron.install_lines([], 2)
    reconcile = [entry for entry in entries if entry.endswith(f"# {cron.RECONCILE_MARKER}")]
    assert len(reconcile) == 1
    assert "governance/reconcile/cli.py" in reconcile[0]
    assert "watch --once --apply" in reconcile[0]
    assert reconcile[0].startswith("*/2 * * * *")
    assert cron.RECONCILE_MARKER in cron.MARKERS


def test_uninstall_removes_only_the_fleet_line(monkeypatch):
    state = {"written": None}
    monkeypatch.setattr(cron, "read_crontab", lambda: [
        "0 2 * * * other-job # other",
        "*/2 * * * * fleet # ao-fleet-watchdog",
    ])
    monkeypatch.setattr(cron, "write_crontab", lambda lines: state.__setitem__("written", lines))
    assert cron.cmd_uninstall(type("Args", (), {})()) == 0
    assert state["written"] == ["0 2 * * * other-job # other"]


def test_disable_comments_out_and_enable_restores(monkeypatch):
    state = {"lines": ["*/2 * * * * fleet # ao-fleet-watchdog"]}
    monkeypatch.setattr(cron, "read_crontab", lambda: state["lines"])
    monkeypatch.setattr(cron, "write_crontab", lambda lines: state.__setitem__("lines", lines))
    cron.cmd_disable(type("Args", (), {})())
    assert state["lines"][0].lstrip().startswith("#")
    cron.cmd_enable(type("Args", (), {})())
    assert not state["lines"][0].lstrip().startswith("#")
    assert "# ao-fleet-watchdog" in state["lines"][0]


def test_status_reports_installed(monkeypatch, capsys):
    monkeypatch.setattr(cron, "read_crontab", lambda: ["*/2 * * * * fleet # ao-fleet-watchdog"])
    monkeypatch.setattr(cron, "LOG", Path("/nonexistent-watchdog-log"))
    assert cron.cmd_status(type("Args", (), {})()) == 0
    assert "installed" in capsys.readouterr().out
