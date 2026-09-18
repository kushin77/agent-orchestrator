"""The fleet watchdog and its crontab manager — the cron-owned keeper."""

from __future__ import annotations

import json
import os
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


# --- the bounded remedy (#773, AO-GR-21) --------------------------------------
#
# Measured 2026-09-15 on the live fleet: `#739` compared the running commit to
# `origin/master` and RESPAWNED on a mismatch. When the mismatch was the *checkout*
# being behind, the respawn re-executed the same checkout, so the watchdog took an
# action that could not change the value it compared — 132 resprints decisions,
# 45 clean stops in one night, and no work done. These tests pin the two cases,
# the bound, and the pending record.

REMOTE = "remote99"
LOCAL = "local111"


def _drift_env(monkeypatch, *, attempts=3, backoff=60):
    """Pin the remedy budget so a developer's environment cannot change the count."""
    monkeypatch.setenv(watchdog.ENV_RESPAWN_ATTEMPTS, str(attempts))
    monkeypatch.setenv(watchdog.ENV_RESPAWN_BACKOFF, str(backoff))


def _wire_drift(monkeypatch, commit, *, local=LOCAL, baseline=REMOTE, inflight=False):
    """One drifting rung, with every side effect measured instead of performed."""
    monkeypatch.setattr(watchdog.channel, "heartbeat_age_seconds", lambda beat, moment=None: 10)
    monkeypatch.setattr(watchdog, "loop_pid", lambda pattern: 111)
    monkeypatch.setattr(watchdog, "read_beat", lambda path: _beat(commit=commit))
    monkeypatch.setattr(watchdog, "run_in_flight", lambda: inflight)
    monkeypatch.setattr(
        watchdog.channel, "capability_line", lambda finding: f"{finding.rung}: {finding.case}"
    )
    calls = []
    monkeypatch.setattr(
        watchdog, "respawn", lambda pattern, script, name="": calls.append((script, name)) or True
    )
    return calls, {"local_head": local, "baseline": baseline}


def _act(monkeypatch, *, commit, when, local=LOCAL, baseline=REMOTE, inflight=False, checkout_root=None):
    calls, kwargs = _wire_drift(monkeypatch, commit, local=local, baseline=baseline, inflight=inflight)
    line = watchdog.rung_action(
        "sister",
        "fleet/terminal.py",
        "fleet/terminal.sh",
        Path("/tmp/x"),
        False,
        kwargs["baseline"],
        local_head=kwargs["local_head"],
        when=when,
        checkout_root=checkout_root,
    )
    return line, calls


def test_running_the_local_head_while_the_remote_is_ahead_is_checkout_behind(monkeypatch):
    """Case (a): the rung is current *relative to the checkout*; the checkout is stale."""
    monkeypatch.setattr(watchdog.channel, "heartbeat_age_seconds", lambda beat, moment=None: 10)
    state, reason = watchdog.decide(111, _beat(commit=LOCAL), REMOTE, local_head=LOCAL)
    assert state == watchdog.CHECKOUT_BEHIND
    assert LOCAL in reason and REMOTE in reason
    # The SAME commits, judged without the local HEAD, are plain drift — so the new
    # case is additive and the #739 detection is not weakened.
    assert watchdog.decide(111, _beat(commit=LOCAL), REMOTE)[0] == watchdog.DRIFTED
    # A rung that is on neither the checkout's HEAD nor origin/master is drifted.
    assert watchdog.decide(111, _beat(commit="other22"), REMOTE, local_head=LOCAL)[0] == watchdog.DRIFTED


def test_checkout_behind_is_fast_forwarded_not_respawned_into_the_same_checkout(monkeypatch):
    """Case (a), remedy: the CHECKOUT moves, then the rung loads it — exactly once."""
    _drift_env(monkeypatch)
    forwarded = []

    def fake_ff(root=None, *, remote="origin/master"):
        forwarded.append(remote)
        return True, REMOTE, f"fast-forwarded {LOCAL} -> {REMOTE}"

    monkeypatch.setattr(watchdog, "fast_forward_checkout", fake_ff)
    line, calls = _act(monkeypatch, commit=LOCAL, when=0)
    assert forwarded == ["origin/master"], "the checkout must be fast-forwarded"
    assert calls == [("fleet/terminal.sh", "sister")], "and then the rung loads the new HEAD"
    assert "checkout-behind" in line and "fast-forwarded" in line


def test_a_respawn_that_cannot_change_the_commit_is_bounded_then_escalates_once(monkeypatch, tmp_path):
    """Case (b): the remedy is attempted `cap` times, escalated ONCE, then parked."""
    _drift_env(monkeypatch, attempts=3)
    attempts_seen = []
    lines = []
    for step in range(5):
        line, calls = _act(monkeypatch, commit="other22", when=step * 1000)
        attempts_seen.append(len(calls))
        lines.append(line)
    assert attempts_seen == [1, 1, 1, 0, 0], "no remedy after the cap: the watchdog stops retrying"
    assert sum(1 for line in lines if "ESCALATED ONCE" in line) == 1, "escalate exactly once"
    escalation = [line for line in lines if "ESCALATED ONCE" in line][0]
    assert "other22" in escalation and REMOTE in escalation, "both commits are named"
    assert watchdog.ROOT.as_posix() in escalation, "the checkout is named"
    assert "PARKED" in lines[-1], "and it stays parked instead of silently retrying"
    artifacts = list(watchdog.escalation_dir().glob("sister.*.json"))
    assert len(artifacts) == 1, f"exactly one escalation artifact, got {artifacts}"


def test_the_bound_counts_only_consecutive_unresolved_attempts(monkeypatch):
    """A remedy that WORKS resets the counter: the cap must not punish progress."""
    _drift_env(monkeypatch, attempts=2)
    calls, kwargs = _wire_drift(monkeypatch, "old0000")
    commits = iter(["old0000", "old0000", "new1111", "new1111"])
    monkeypatch.setattr(watchdog, "read_beat", lambda path: _beat(commit=next(commits, "new1111")))
    for step in range(4):
        line = watchdog.rung_action(
            "sister",
            "fleet/terminal.py",
            "fleet/terminal.sh",
            Path("/tmp/x"),
            False,
            kwargs["baseline"],
            local_head=kwargs["local_head"],
            when=step * 1000,
        )
    assert "ESCALATED ONCE" not in line, "the rung moved, so the bound must not fire"


def test_the_next_attempt_is_held_until_the_backoff_elapses(monkeypatch):
    """AO-GR-21's spacing: attempts are not fired back to back."""
    _drift_env(monkeypatch, attempts=3, backoff=60)
    first, calls = _act(monkeypatch, commit="other22", when=0)
    assert calls == [("fleet/terminal.sh", "sister")]
    too_soon, calls = _act(monkeypatch, commit="other22", when=10)
    assert calls == [], "a second attempt inside the backoff window must not run"
    assert "holding" in too_soon and "due in 50s" in too_soon
    due, calls = _act(monkeypatch, commit="other22", when=61)
    assert calls == [("fleet/terminal.sh", "sister")]


def test_a_genuinely_drifted_rung_is_still_respawned(monkeypatch):
    """Case (c): the fix must not stop repairing real drift."""
    _drift_env(monkeypatch)
    line, calls = _act(monkeypatch, commit="other22", when=0)
    assert calls == [("fleet/terminal.sh", "sister")]
    assert "drifted" in line and "respawned" in line


def test_a_busy_drifted_rung_is_recorded_pending_and_acted_on_when_the_run_ends(monkeypatch):
    """Case (d): a drifted-but-busy rung is not dropped every tick."""
    _drift_env(monkeypatch)
    busy, calls = _act(monkeypatch, commit="other22", when=0, inflight=True)
    assert calls == [], "the one rule the watchdog never breaks"
    assert "left alone" in busy
    record = watchdog.load_drift_record("sister")
    assert record is not None and record["phase"] == "pending"
    assert record["running"] == "other22" and record["baseline"] == REMOTE
    idle, calls = _act(monkeypatch, commit="other22", when=5, inflight=False)
    assert calls == [("fleet/terminal.sh", "sister")], "the pending finding is acted on, not re-dropped"
    assert "respawned" in idle


# --- the crash-loop escape (#366) ---------------------------------------------
#
# The measured deadlock: the hold in `rung_action` — "never restart a run just to
# update code" — was taken on EVERY tick, because a rung whose runs die before they
# can report presents flight every time it is asked. `.fleet/watchdog.log` shows
# the sister holding its own drift lock from 21:32 onward on code that predated
# five merged fixes, while the brain was respawned in the same tick. A hold that
# can be re-taken without bound is a policy, not a control. These tests pin the
# budget that ends it, the constants it names, and — just as important — that it
# did not become a second unbounded path.


def test_a_crash_looping_rung_is_respawned_despite_a_run_appearing_in_flight(monkeypatch):
    """THE regression test: N holds inside the window, no progress ⇒ the remedy proceeds."""
    _drift_env(monkeypatch)
    lines = []
    held_respawns = 0
    total_respawns = 0
    for step in range(watchdog.CRASH_LOOP_RESPAWNS + 1):
        line, calls = _act(monkeypatch, commit="other22", when=step * 10, inflight=True)
        if step < watchdog.CRASH_LOOP_RESPAWNS:
            held_respawns += len(calls)
        lines.append(line)
        total_respawns += len(calls)
    held = ["left alone" in line for line in lines[: watchdog.CRASH_LOOP_RESPAWNS]]
    assert held == [True] * watchdog.CRASH_LOOP_RESPAWNS, "the safety rule holds until the budget is spent"
    assert held_respawns == 0, "a run in flight is still not restarted just to update code — until it is looping"
    escape = lines[-1]
    assert "left alone" not in escape, "the hold must not be re-taken for a crash loop"
    assert total_respawns == 1 and "respawned" in escape
    assert f"N={watchdog.CRASH_LOOP_RESPAWNS}" in escape, "the line names N"
    assert str(int(watchdog.CRASH_LOOP_WINDOW_SECONDS)) in escape, "and the window"


def test_a_busy_rung_whose_holds_fall_outside_the_window_is_still_held(monkeypatch):
    """The budget is a window, not a lifetime tally: a slow hold never arms it."""
    _drift_env(monkeypatch)
    spacing = int(watchdog.CRASH_LOOP_WINDOW_SECONDS) + 1
    respawns = 0
    line = ""
    for step in range(watchdog.CRASH_LOOP_RESPAWNS + 2):
        line, calls = _act(monkeypatch, commit="other22", when=step * spacing, inflight=True)
        respawns += len(calls)
    assert respawns == 0, "holds spread wider than the window are not a crash loop"
    assert "left alone" in line


def test_a_busy_rung_that_makes_progress_never_arms_the_crash_loop_escape(monkeypatch):
    """A hold whose observation MOVES is progress: no budget is spent, nothing is respawned."""
    _drift_env(monkeypatch)
    respawns = 0
    line = ""
    for step in range(6):
        line, calls = _act(monkeypatch, commit=f"old{step}", when=step * 5, inflight=True)
        respawns += len(calls)
    assert respawns == 0
    assert "left alone" in line
    record = watchdog.load_drift_record("sister") or {}
    assert len(record.get("respawns") or []) == 1, "progress restarts the ledger, so it cannot accumulate"


def test_the_crash_loop_escape_is_bounded_by_the_attempt_cap(monkeypatch):
    """AO-GR-21: #366 must not open a second unbounded path to respawn."""
    _drift_env(monkeypatch, attempts=2)
    lines = []
    respawns = 0
    for step in range(8):
        line, calls = _act(monkeypatch, commit="other22", when=step * 5, inflight=True)
        lines.append(line)
        respawns += len(calls)
    assert respawns <= 2, "the escape goes through bounded_remedy, so the cap still applies"
    assert sum(1 for line in lines if "ESCALATED ONCE" in line) == 1, "and it escalates exactly once"
    assert any("PARKED" in line for line in lines), "then it parks instead of respawning forever"


def test_the_in_flight_hold_cannot_un_park_a_rung_the_remedy_already_parked(monkeypatch):
    """A deferral must not resurrect a park #773 has closed.

    Found by this lane's own gate probe rather than by reasoning: the escape's
    ledger is a sliding window, so the first tick after the park whose stamps had
    aged out of the window took the HOLD again — `record_pending` flipped the record
    back to `pending` and a crash loop that had already been escalated and parked
    would have been resurrected, one deferral at a time, by the check that bounds it.
    """
    _drift_env(monkeypatch, attempts=2)
    lines = []
    for step in range(8):
        line, _calls = _act(monkeypatch, commit="other22", when=step * 5, inflight=True)
        lines.append(line)
    assert any("ESCALATED ONCE" in line for line in lines), "the escape is still the bounded remedy"
    assert len([line for line in lines if "PARKED" in line]) == 2, "and the park is then terminal"
    record = watchdog.load_drift_record("sister") or {}
    assert record.get("phase") == "parked", "the hold must not flip the phase back to pending"
    assert len(list(watchdog.escalation_dir().glob("sister.*.json"))) == 1


def test_crash_loop_verdict_names_the_constants_it_counted():
    """A bound whose numbers are not in the log cannot be audited by the operator reading it."""
    stamp = 1000.0
    record = {"state": "drifted", "running": "other22", "respawns": [stamp, stamp + 1, stamp + 2]}
    looping, note = watchdog.crash_loop_verdict("sister", stamp + 3, record)
    assert looping is True
    assert f"N={watchdog.CRASH_LOOP_RESPAWNS}" in note
    assert str(int(watchdog.CRASH_LOOP_WINDOW_SECONDS)) in note


def test_crash_loop_verdict_counts_only_stamps_inside_the_window():
    """Two in the window are two: the verdict may not round a budget up or down."""
    stamp = 1000.0
    window = watchdog.CRASH_LOOP_WINDOW_SECONDS
    record = {"state": "drifted", "running": "other22"}
    two_recent = {**record, "respawns": [stamp, stamp + 1, stamp + window + 1]}
    looping, note = watchdog.crash_loop_verdict("sister", stamp + window + 2, two_recent)
    assert looping is False and note == "", "a stamp past the window does not count"
    three_recent = {**record, "respawns": [stamp, stamp + 1, stamp + 2]}
    assert watchdog.crash_loop_verdict("sister", stamp + 2, three_recent)[0] is True
    assert watchdog.crash_loop_verdict("sister", stamp, {})[0] is False, "no ledger is not a crash loop"


def test_a_healthy_rung_clears_the_record_so_a_fixed_rung_keeps_no_budget(monkeypatch):
    _drift_env(monkeypatch)
    _act(monkeypatch, commit="other22", when=0)
    assert watchdog.load_drift_record("sister") is not None
    monkeypatch.setattr(watchdog, "read_beat", lambda path: _beat(commit=REMOTE))
    line = watchdog.rung_action(
        "sister",
        "fleet/terminal.py",
        "fleet/terminal.sh",
        Path("/tmp/x"),
        False,
        REMOTE,
        local_head=REMOTE,
        when=1,
    )
    assert line.startswith("sister: healthy")
    assert watchdog.load_drift_record("sister") is None


def test_an_unusable_attempt_cap_refuses_the_pass_instead_of_disarming_the_bound(monkeypatch, capsys):
    monkeypatch.setenv(watchdog.ENV_RESPAWN_ATTEMPTS, "zero")
    assert watchdog.watchdog_once() == watchdog.channel.EXIT_CANNOT_ASSESS
    err = capsys.readouterr().err
    assert watchdog.ENV_RESPAWN_ATTEMPTS in err
    assert "CANNOT-ASSESS" in err


def test_rearm_clears_the_record_so_the_next_pass_judges_the_rung_again(monkeypatch, capsys):
    _drift_env(monkeypatch, attempts=1)
    _act(monkeypatch, commit="other22", when=0)
    parked, calls = _act(monkeypatch, commit="other22", when=1000)
    assert "ESCALATED ONCE" in parked and calls == []
    assert watchdog.cmd_rearm(type("A", (), {"rung": "sister"})()) == watchdog.channel.EXIT_OK
    assert watchdog.load_drift_record("sister") is None
    assert "cleared" in capsys.readouterr().out
    again, calls = _act(monkeypatch, commit="other22", when=2000)
    assert calls == [("fleet/terminal.sh", "sister")], "rearm restores the remedy"


def test_the_harvested_backoff_formula_is_the_vendored_one(monkeypatch):
    """`delay = min(base * 2**(n-1), 300)` — harvested from vendor/CMR/ops/retry.sh."""
    _drift_env(monkeypatch, backoff=30)
    assert [watchdog.respawn_backoff_seconds(n) for n in (1, 2, 3)] == [30.0, 60.0, 120.0]
    _drift_env(monkeypatch, backoff=200)
    assert watchdog.respawn_backoff_seconds(3) == float(watchdog.BACKOFF_CAP_SECONDS)


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


# --- the bootstrap (#780) -----------------------------------------------------
#
# Measured 2026-09-15: cron runs `cd <checkout> && python3 fleet/watchdog.py run`, so
# the watchdog IN FLIGHT is whatever copy the checkout holds. The shared checkout
# stood 5 commits behind (`e9cfc10` merged, `HEAD` `b95a8b7`) and the sister ran
# `592b132` for ~5.5 hours, missing every fix merged that day. #773 had given the
# `checkout-behind` case the right remedy — a fast-forward — but the code holding
# that remedy lives in the checkout, so a stale checkout executed a watchdog that
# could not see it; #773's own evidence records a HUMAN doing the move.
#
# These tests drive the remedy against REAL git repositories, because the code under
# test IS `git fetch` + `git merge --ff-only`: a stubbed repository would prove
# nothing about reachability. The stale clone's own `fleet/watchdog.py` is the
# PRE-bootstrap revision — the copy that does not contain the remedy — which is
# exactly the state cron found the shared checkout in.

#: The revision BEFORE the bootstrap: a stand-in for the pre-#780 module, in which
#: no remedy for the checkout's staleness exists at all.
_PRE_FIX_SOURCE = (
    '"""The fleet watchdog as it stood before the checkout remedy became reachable."""\n'
    "\n"
    'WATCHDOG_ERA = "no-remedy"\n'
)
#: The revision AT `origin/master`: a stand-in for this module, which carries the
#: kernel. Its `bootstrap` is a tripwire, so a test that executed the checkout's copy
#: of the remedy would fail loudly instead of passing for the wrong reason.
_FIXED_SOURCE = (
    '"""The fleet watchdog at origin/master, carrying the bootstrap (#780)."""\n'
    "\n"
    'WATCHDOG_ERA = "bootstrap"\n'
    "\n"
    "\n"
    "def bootstrap(*args, **kwargs):\n"
    "    raise AssertionError(\"the checkout's own copy of the remedy must never be executed\")\n"
)

_AGENT = ("-c", "user.email=agent780@agents.invalid", "-c", "user.name=agent780")


def _run(*argv):
    """Run a command, capturing output; never raises, so a refusal is an assertion's job."""
    return subprocess.run(list(argv), capture_output=True, text=True, check=False)


def _rev(repo, spec="HEAD"):
    return _run("git", "-C", str(repo), "rev-parse", spec).stdout.strip()


def _commit_all(repo, message):
    _run("git", "-C", str(repo), "add", "-A")
    result = _run("git", "-C", str(repo), *_AGENT, "commit", "-q", "-m", message)
    assert result.returncode == 0, f"the test's own commit failed: {result.stderr}"


def _stale_checkout(tmp_path):
    """A real origin, and a real clone of it left strictly one commit BEHIND.

    Returns `(seed, stale, behind_sha, remote_sha)`. The bare origin's HEAD is
    pointed at `refs/heads/master` explicitly, so `origin/master` is the
    remote-tracking ref this module reads whatever the host's `init.defaultBranch`.
    """
    origin = tmp_path / "origin.git"
    assert _run("git", "init", "--bare", "-q", str(origin)).returncode == 0
    assert _run("git", "-C", str(origin), "symbolic-ref", "HEAD", "refs/heads/master").returncode == 0

    seed = tmp_path / "seed"
    assert _run("git", "clone", "-q", str(origin), str(seed)).returncode == 0
    (seed / "fleet").mkdir(parents=True, exist_ok=True)
    (seed / "fleet" / "watchdog.py").write_text(_PRE_FIX_SOURCE, encoding="utf-8")
    _commit_all(seed, "the revision before the bootstrap")
    behind = _rev(seed)
    assert _run("git", "-C", str(seed), "push", "-q", "origin", "HEAD:master").returncode == 0

    (seed / "fleet" / "watchdog.py").write_text(_FIXED_SOURCE, encoding="utf-8")
    _commit_all(seed, "the bootstrap kernel")
    remote = _rev(seed)
    assert _run("git", "-C", str(seed), "push", "-q", "origin", "HEAD:master").returncode == 0

    stale = tmp_path / "stale"
    assert _run("git", "clone", "-q", str(origin), str(stale)).returncode == 0
    reset = _run("git", "-C", str(stale), "reset", "-q", "--hard", behind)
    assert reset.returncode == 0, reset.stderr
    assert _rev(stale) == behind
    return seed, stale, behind, remote


@pytest.fixture
def bootstrap_env(monkeypatch):
    """Restore `ENV_BOOTSTRAPPED`, which `reexec_watchdog` sets directly.

    An empty value is falsy, so the code under test still treats the process as
    un-bootstrapped — and pytest restores the variable afterwards instead of the
    suite leaking it into a later test.
    """
    monkeypatch.setenv(watchdog.ENV_BOOTSTRAPPED, "")


def test_a_checkout_that_is_behind_is_brought_forward_without_its_own_copy_of_the_remedy(tmp_path):
    """THE regression test for #780: the remedy must not be gated on the checkout being current.

    The stale clone's copy of this module is the PRE-bootstrap revision, which does
    not contain the remedy at all — the state the shared checkout was measured in.
    The remedy must still reach it, which is the whole defect: before this change the
    running watchdog was the copy in the stale tree, so the repair waited on the code
    it was supposed to repair.
    """
    seed, stale, behind, remote = _stale_checkout(tmp_path)

    # The premise, asserted rather than assumed.
    assert _rev(stale) == behind, "the checkout was not left behind"
    stale_source = (stale / "fleet" / "watchdog.py").read_text(encoding="utf-8")
    assert stale_source == _PRE_FIX_SOURCE
    assert "def bootstrap" not in stale_source, "the stale copy must genuinely LACK the remedy"
    remote_source = _run("git", "-C", str(stale), "show", "origin/master:fleet/watchdog.py").stdout
    assert "def bootstrap" in remote_source, "the remote's copy is the one carrying the remedy"

    before = watchdog.self_freshness(stale)
    assert before["verdict"] == watchdog.FRESH_BEHIND, before["reason"]
    # The verdict reports SHORT commits; the fixture works in full ones.
    assert behind.startswith(before["local_head"])
    assert remote.startswith(before["remote_head"])
    assert before["local_blob"] != before["remote_blob"]

    moved, head, detail = watchdog.bootstrap_checkout(stale)

    assert moved is True, detail
    assert remote.startswith(head), detail
    assert _rev(stale) == remote, "the checkout did not actually move"
    assert watchdog.self_freshness(stale)["verdict"] == watchdog.FRESH_CURRENT
    assert (stale / "fleet" / "watchdog.py").read_text(encoding="utf-8") == _FIXED_SOURCE


def test_the_verdict_is_answered_from_the_remote_not_from_the_working_tree(tmp_path):
    """The read that dissolves the circularity: a behind checkout can still ask the question."""
    _seed, stale, _behind, remote = _stale_checkout(tmp_path)
    from_remote = _run("git", "-C", str(stale), "rev-parse", "origin/master:fleet/watchdog.py").stdout.strip()

    assert watchdog.remote_source_blob(stale) == from_remote
    assert watchdog.source_blob(stale) != watchdog.remote_source_blob(stale)
    # The working tree's blob is the OLD file's, so the two sides are genuinely different
    # and the verdict is computed against the remote's — never against the copy asking.
    assert watchdog.source_blob(stale) == _run(
        "git", "-C", str(stale), "hash-object", "--", str(stale / "fleet" / "watchdog.py")
    ).stdout.strip()
    assert _rev(stale, "origin/master") == remote


def test_a_checkout_that_is_ahead_is_diverged_not_behind_and_is_never_moved(tmp_path):
    """No false positive: a lane's own commits are not staleness, and are never discarded."""
    _seed, stale, _behind, remote = _stale_checkout(tmp_path)
    assert _run("git", "-C", str(stale), "reset", "-q", "--hard", "origin/master").returncode == 0
    (stale / "fleet" / "watchdog.py").write_text(_FIXED_SOURCE + "\nLANE_WORK = True\n", encoding="utf-8")
    _commit_all(stale, "the lane's own work")
    lane_head = _rev(stale)

    freshness = watchdog.self_freshness(stale)
    assert freshness["verdict"] == watchdog.FRESH_DIVERGED, freshness["reason"]

    moved, _head, detail = watchdog.bootstrap_checkout(stale)
    assert moved is False, detail
    assert "REFUSED" in detail
    assert _rev(stale) == lane_head, "a diverged checkout must not be moved"
    assert remote != lane_head
    assert (stale / "fleet" / "watchdog.py").read_text(encoding="utf-8").endswith("LANE_WORK = True\n")


def test_uncommitted_edits_are_diverged_not_behind(tmp_path):
    """A commit is an ancestor of ITSELF, so HEAD == remote with different bytes is local work.

    Pinned because the first cut of the fix got this wrong: the ancestor test alone
    reported `behind` for a tree whose HEAD *equalled* the remote but whose file held
    uncommitted edits — which would have had the bootstrap try to overwrite them.
    """
    _seed, stale, _behind, remote = _stale_checkout(tmp_path)
    assert _run("git", "-C", str(stale), "reset", "-q", "--hard", "origin/master").returncode == 0
    (stale / "fleet" / "watchdog.py").write_text("locally edited, never committed\n", encoding="utf-8")

    assert _rev(stale) == remote, "the premise: HEAD is the remote's commit"
    freshness = watchdog.self_freshness(stale)
    assert freshness["verdict"] == watchdog.FRESH_DIVERGED, freshness["reason"]
    assert freshness["local_blob"] != freshness["remote_blob"]

    moved, _head, detail = watchdog.bootstrap_checkout(stale)
    assert moved is False and "REFUSED" in detail
    assert (stale / "fleet" / "watchdog.py").read_text(encoding="utf-8") == "locally edited, never committed\n"


def test_a_checkout_already_at_the_remote_is_not_moved(tmp_path):
    """Idempotence: a checkout that is current is left exactly as it is."""
    _seed, stale, _behind, remote = _stale_checkout(tmp_path)
    assert _run("git", "-C", str(stale), "reset", "-q", "--hard", "origin/master").returncode == 0

    moved, _head, detail = watchdog.bootstrap_checkout(stale)

    assert moved is False, detail
    assert "already" in detail
    assert _rev(stale) == remote


def test_an_unreadable_checkout_is_cannot_assess_never_current(tmp_path):
    """Fail-closed, #739's rule one level up: "I cannot tell" must never read as "I am current"."""
    plain = tmp_path / "not-a-repository"
    plain.mkdir()

    freshness = watchdog.self_freshness(plain)

    assert freshness["verdict"] == watchdog.FRESH_CANNOT_ASSESS, freshness
    assert freshness["verdict"] != watchdog.FRESH_CURRENT
    assert "cannot read" in freshness["reason"]

    moved, _head, detail = watchdog.bootstrap_checkout(plain)
    assert moved is False, "an unassessable checkout must never be reported as repaired"
    assert "REFUSED" in detail
    assert str(plain) in detail, "the refusal must name the tree it could not read"


def test_the_run_preflight_refuses_an_unassessable_checkout_instead_of_failing_open(tmp_path):
    """`run` from a directory git cannot read returns CANNOT-ASSESS — never a pass."""
    plain = tmp_path / "not-a-repository"
    plain.mkdir()

    rc = watchdog.bootstrap_preflight(["run"], root=plain)

    assert rc == watchdog.channel.EXIT_CANNOT_ASSESS
    assert rc != watchdog.channel.EXIT_OK


def test_the_run_preflight_reports_behind_as_not_ok_when_it_cannot_move(tmp_path, monkeypatch):
    """The measured state — a fleet silently pinned to old code — is a failure, not a note.

    Provoked by building a genuinely BEHIND checkout and then withholding the move, so
    the refusal branch is exercised on its own terms: the verdict is `behind`, nothing
    repaired it, and the pass must not proceed as though the code were current.
    """
    _seed, stale, _behind, _remote = _stale_checkout(tmp_path)
    assert watchdog.self_freshness(stale)["verdict"] == watchdog.FRESH_BEHIND
    monkeypatch.setattr(
        watchdog,
        "bootstrap_checkout",
        lambda root=None, *, remote="origin/master": (False, "deadbee", "the move was refused"),
    )

    rc = watchdog.bootstrap_preflight(["run"], root=stale)

    assert rc == watchdog.channel.EXIT_NOT_OK, "a checkout pinned to old code is NOT-OK, not a note"
    assert rc != watchdog.channel.EXIT_OK


def test_after_the_checkout_moves_the_preflight_re_execs_onto_the_code_that_arrived(
    tmp_path, monkeypatch, bootstrap_env
):
    """Durability: the process that moved the checkout had already imported the OLD module."""
    _seed, stale, behind, remote = _stale_checkout(tmp_path)
    calls = []
    monkeypatch.setattr(watchdog.os, "execv", lambda path, argv: calls.append((path, argv)))

    rc = watchdog.bootstrap_preflight(["run"], root=stale)

    assert rc is None, "the preflight returned instead of re-execing onto the new code"
    assert len(calls) == 1, f"exactly one re-exec, measured: {calls}"
    path, argv = calls[0]
    assert path == sys.executable
    assert Path(argv[1]).name == "watchdog.py"
    assert argv[2:] == ["run"], "the SAME verb is re-run, by the process that now holds the new code"
    assert os.environ.get(watchdog.ENV_BOOTSTRAPPED) == "1"
    assert _rev(stale) == remote, "the move still happened"
    assert behind != remote


def test_the_re_exec_is_bounded_to_one_per_invocation(tmp_path, monkeypatch, capsys):
    """A re-exec that could repeat is the runaway #773 exists to stop."""
    _seed, stale, _behind, remote = _stale_checkout(tmp_path)
    monkeypatch.setenv(watchdog.ENV_BOOTSTRAPPED, "1")  # the re-exec'd process's view
    calls = []
    monkeypatch.setattr(watchdog.os, "execv", lambda path, argv: calls.append(argv))

    rc = watchdog.bootstrap_preflight(["run"], root=stale)

    assert calls == [], "a second re-exec is exactly the unbounded remedy this bound prevents"
    assert rc is None
    assert _rev(stale) == remote, "only the re-exec is bounded; the move still happens"
    assert "already re-exec'd once" in capsys.readouterr().out


def test_the_run_verb_preflights_the_checkout_before_the_pass(monkeypatch):
    """#780: `run` asserts the precondition first — and a refusal stops the pass."""
    seen = []
    monkeypatch.setattr(
        watchdog,
        "bootstrap_preflight",
        lambda argv: seen.append(argv) or watchdog.channel.EXIT_CANNOT_ASSESS,
    )

    rc = watchdog.main(["run", "--force"])

    assert seen == [["run", "--force"]], "the preflight must run first, and carry the verb"
    assert rc == watchdog.channel.EXIT_CANNOT_ASSESS, "and its refusal must stop the pass"


def test_the_bootstrap_verb_moves_a_stale_checkout_and_names_both_commits(tmp_path, capsys, bootstrap_env):
    """The operator-facing entry point, driven through the real CLI dispatch."""
    seed, stale, behind, remote = _stale_checkout(tmp_path)
    behind_short = _run("git", "-C", str(stale), "rev-parse", "--short", behind).stdout.strip()
    remote_short = _run("git", "-C", str(seed), "rev-parse", "--short", remote).stdout.strip()

    rc = watchdog.main(["bootstrap", "--root", str(stale), "--no-reexec"])

    out = capsys.readouterr().out
    assert rc == watchdog.channel.EXIT_OK, out
    assert behind_short in out and remote_short in out, f"the finding must name both commits: {out}"
    assert _rev(stale) == remote


def test_the_bootstrap_verb_is_cannot_assess_on_a_checkout_it_cannot_read(tmp_path, capsys):
    """Tri-state, not boolean: the verb distinguishes "repaired" from "cannot be judged"."""
    plain = tmp_path / "not-a-repository"
    plain.mkdir()

    rc = watchdog.main(["bootstrap", "--root", str(plain), "--no-reexec"])

    assert rc == watchdog.channel.EXIT_CANNOT_ASSESS
    assert "CANNOT-ASSESS" in capsys.readouterr().err


# --- #977: the watchdog tick's own single-writer lease -------------------


def test_watchdog_once_noops_and_logs_skip_when_lease_lost(monkeypatch, capsys):
    """A lost lease race is not a failure: the pass no-ops and exits 0 (#977)."""

    class _LosingLease:
        def acquire(self):
            return False

        def release(self):  # pragma: no cover - never reached on loss
            raise AssertionError("release must not be called when acquire never won")

    monkeypatch.setattr(watchdog.lease, "make_lease", lambda **kwargs: _LosingLease())
    # If the pass proceeded anyway it would try to act on real rungs; make any
    # such action fail loudly instead of silently doing partial work.
    monkeypatch.setattr(
        watchdog, "_watchdog_once_locked", lambda force: (_ for _ in ()).throw(
            AssertionError("must not do any rung work when the lease was lost")
        )
    )

    rc = watchdog.watchdog_once()

    assert rc == watchdog.channel.EXIT_OK
    out = capsys.readouterr().out
    assert '"status": "skipped"' in out
    assert '"job": "watchdog"' in out


def test_watchdog_once_releases_the_lease_even_on_a_failed_pass(monkeypatch):
    """The lease is released in a `finally`, even when the guarded pass raises."""

    released = []

    class _WinningLease:
        def acquire(self):
            return True

        def release(self):
            released.append(True)

    monkeypatch.setattr(watchdog.lease, "make_lease", lambda **kwargs: _WinningLease())
    monkeypatch.setattr(
        watchdog, "_watchdog_once_locked", lambda force: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    with pytest.raises(RuntimeError):
        watchdog.watchdog_once()

    assert released == [True]


# --- #978: refuse_if_frozen wired in before every spawn ------------------


def test_a_missing_loop_is_not_respawned_while_frozen(monkeypatch):
    """`refuse_if_frozen` wins before `spawn()` — the frozen fleet dispatches nothing."""
    monkeypatch.setattr(watchdog, "loop_pid", lambda pattern: None)
    monkeypatch.setattr(watchdog, "read_beat", lambda path: None)
    monkeypatch.setattr(watchdog.freeze, "refuse_if_frozen", lambda rung: f"[{rung}] REFUSED — frozen")
    monkeypatch.setattr(
        watchdog, "spawn", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not spawn while frozen"))
    )

    line = watchdog.rung_action("sister", "fleet/terminal.py", "fleet/terminal.sh", Path("/tmp/x"), False, "head")

    assert "REFUSED (frozen)" in line
    assert "RESPAWN FAILED" not in line


def test_a_missing_loop_is_respawned_once_thawed(monkeypatch):
    """Thawed: normal dispatch, unaffected by the frozen-path wiring."""
    monkeypatch.setattr(watchdog, "loop_pid", lambda pattern: None)
    monkeypatch.setattr(watchdog, "read_beat", lambda path: None)
    monkeypatch.setattr(watchdog.freeze, "refuse_if_frozen", lambda rung: None)
    calls = []
    monkeypatch.setattr(watchdog, "respawn", lambda pattern, script, name="": calls.append((script, name)) or True)

    line = watchdog.rung_action("sister", "fleet/terminal.py", "fleet/terminal.sh", Path("/tmp/x"), False, "head")

    assert "missing" in line and calls == [("fleet/terminal.sh", "sister")]


def test_start_monitor_refuses_while_frozen(monkeypatch):
    monkeypatch.setattr(watchdog.freeze, "refuse_if_frozen", lambda rung: f"[{rung}] REFUSED — frozen")
    monkeypatch.setattr(
        watchdog, "spawn", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not spawn while frozen"))
    )

    with pytest.raises(watchdog.RespawnRefused):
        watchdog.start_monitor()


# --- RCA 2026-09-17 fix #3: a per-worktree, self-owned gate-lock reap ------
#
# RCA-0015 (governance/lessons/rca/RCA-0015-zero-byte-gate-lock-wedge.md)
# refused a box-wide auto-heal of gate-lock leftovers. `_self_reap_own_gate_lock`
# is the watchdog's worktree-SCOPED alternative: it must call
# `gatelock.reap_own_worktree` with THIS checkout's own worktree (`ROOT`),
# never anything box-wide, and it must fold an exception into its own
# CANNOT-ASSESS return rather than raising into the caller's pass.


def test_self_reap_own_gate_lock_reaps_only_this_checkouts_worktree(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(
        watchdog.gatelock,
        "reap_own_worktree",
        lambda worktree, **kwargs: calls.append(worktree) or True,
    )
    unassessable = watchdog._self_reap_own_gate_lock()
    assert unassessable is False
    assert calls == [watchdog.ROOT], "the self-reap must be scoped to this checkout's own worktree"
    assert "reaped own leftover" in capsys.readouterr().out


def test_self_reap_own_gate_lock_reports_nothing_to_reap_when_clean(monkeypatch, capsys):
    monkeypatch.setattr(watchdog.gatelock, "reap_own_worktree", lambda worktree, **kwargs: False)
    unassessable = watchdog._self_reap_own_gate_lock()
    assert unassessable is False
    assert "nothing to reap" in capsys.readouterr().out


def test_self_reap_own_gate_lock_folds_an_exception_into_cannot_assess(monkeypatch, capsys):
    def _boom(worktree, **kwargs):
        raise RuntimeError("store is unusable")

    monkeypatch.setattr(watchdog.gatelock, "reap_own_worktree", _boom)
    unassessable = watchdog._self_reap_own_gate_lock()
    assert unassessable is True
    out = capsys.readouterr().out
    assert "CANNOT-ASSESS" in out and "store is unusable" in out
