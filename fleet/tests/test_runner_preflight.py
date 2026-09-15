"""The runner preflight: resolve in code, escalate once, hold the queue (#733).

Measured 2026-09-14: the sister could not spawn a single subagent. `claude` was
installed at ``~/.local/bin/claude``, but the loop is cron's child, so it inherited
cron's minimal PATH — ``~/.local/bin`` was not on it, ``subprocess`` raised
``FileNotFoundError: 'claude'``, and the failure was repeated **per directive, per
cycle**, escalating critical each time (a runaway amplifier) while four lanes with
committed work could not land.

Three properties, each pinned here:

* the runner is **resolved in code** — PATH first, then the documented per-user
  install directories derived from HOME, never a machine-specific literal;
* an unresolvable runner produces **exactly one escalation** and **holds the queue**
  (the existing ``.fleet/paused`` mechanism, so controls are still read and the hold
  is released automatically once the runner resolves);
* a gate the loop **could not assess** (the 1800s ``make verify`` timeout on a
  loaded box) is CANNOT-ASSESS — never a failure of the work, never re-dispatched at
  ``critical``.

The loop is driven with every out-of-process seam stubbed, in the house style of
``test_terminal_finops_runner.py``.
"""

from __future__ import annotations

import argparse
import shlex
import signal
from pathlib import Path

import pytest

import terminal

#: A runner name that resolves nowhere, used for every negative control.
ABSENT_RUNNER = "claude-733-absent"


class _Completed:
    """A ``subprocess.run`` result the loop can read."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


# ---------------------------------------------------------------------------
# 1. resolution: the runner cron's PATH cannot see is still found
# ---------------------------------------------------------------------------


def test_a_runner_in_the_user_local_bin_resolves_even_off_path(tmp_path, monkeypatch):
    """The measured root cause: `claude` is installed, and `~/.local/bin` is not on PATH.

    HOME is redirected, so this proves the *mechanism* (a HOME-derived install
    directory) rather than whatever this developer's machine happens to have.
    """
    home = tmp_path / "home"
    binary = _executable(home / ".local" / "bin" / "claude")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", str(tmp_path / "empty-path"))

    argv, problem = terminal.resolve_runner("claude -p")

    assert problem == "", f"a per-user runner must resolve off PATH: {problem}"
    assert argv == [str(binary), "-p"], f"the runner's own flags must pass through: {argv}"


def test_a_runner_that_cannot_be_found_names_where_we_looked(tmp_path, monkeypatch):
    """The failure has to be actionable: the missing binary AND the PATH searched."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", str(tmp_path / "empty-path"))

    argv, problem = terminal.resolve_runner(ABSENT_RUNNER)

    assert argv is None
    assert ABSENT_RUNNER in problem, "the message must name the binary"
    assert "searched:" in problem, "the message must say where it looked"
    assert str(tmp_path / "empty-path") in problem, "the directories searched must be listed"
    assert str(home) not in problem, "a per-user directory that does not exist is not claimed as searched"


def test_the_search_path_is_path_first_then_the_existing_user_dirs(tmp_path, monkeypatch):
    """PATH wins (an operator's own choice), the per-user dirs follow, only if real."""
    home = tmp_path / "home"
    (home / ".local" / "bin").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", "/usr/bin")

    search = terminal.runtime.runner_search_path()

    assert search[0] == "/usr/bin"
    assert str(home / ".local" / "bin") in search
    assert str(home / "bin") not in search, "a directory that does not exist is not claimed as searched"


def test_an_absolute_runner_path_is_used_exactly_as_given(tmp_path):
    """An operator who writes a file means that file."""
    binary = _executable(tmp_path / "my runner")

    argv, problem = terminal.resolve_runner(f"{shlex.quote(str(binary))} -p")

    assert problem == ""
    assert argv == [str(binary), "-p"], "a path with a space must survive resolution"


def test_a_non_executable_path_is_refused(tmp_path):
    not_executable = tmp_path / "not-a-runner"
    not_executable.write_text("just text\n", encoding="utf-8")

    argv, problem = terminal.resolve_runner(str(not_executable))

    assert argv is None
    assert "not an executable file" in problem


def test_preflight_reports_the_resolved_path_or_one_actionable_line(tmp_path, monkeypatch):
    binary = _executable(tmp_path / "bin" / "claude")
    monkeypatch.setenv("PATH", str(binary.parent))

    ok, detail = terminal.preflight("claude -p")
    assert ok is True and detail == f"runner resolved: {binary}"

    ok, detail = terminal.preflight(ABSENT_RUNNER)
    assert ok is False
    assert detail.startswith("runner unresolvable:") and ABSENT_RUNNER in detail


def test_the_fleet_runner_environment_variable_is_honoured(monkeypatch):
    """`FLEET_RUNNER` is documented in fleet/README.md — and must actually be read."""
    monkeypatch.setenv("FLEET_RUNNER", "/opt/custom/agent -p")

    args = terminal.build_parser().parse_args(["run"])

    assert args.runner == "/opt/custom/agent -p"


# ---------------------------------------------------------------------------
# 2. the hold: the queue is held, and the loop stays steerable
# ---------------------------------------------------------------------------


def test_holding_the_queue_pauses_work_but_never_controls(monkeypatch):
    """Reusing `.fleet/paused` is deliberate: `work_held` holds WORK, not controls.

    A loop that stopped reading its inbox could not be resumed — measured, a
    `resume` was delivered and sat unread until the flag was cleared by hand.
    """
    assert terminal.hold_queue_for_runner("runner unresolvable: no 'claude'") is True
    assert terminal.paused() is True, "the queue must actually be held"

    assert terminal.work_held({"id": "d-1", "task": {"issue": 733}}, terminal.paused()) is True
    assert terminal.work_held({"id": "d-1", "control": "resume"}, terminal.paused()) is False


def test_the_hold_is_taken_once_and_released_once_the_runner_resolves():
    """Not a latch: the flag is ours, so we release it when the condition clears."""
    assert terminal.hold_queue_for_runner("first") is True
    assert terminal.hold_queue_for_runner("again") is False, "a held queue is not re-held each cycle"
    assert terminal.paused() is True

    released = terminal.release_runner_hold()

    assert released, "the hold this loop took must be released"
    assert terminal.paused() is False

    # Nothing left to release: the second call is a no-op, not a second line.
    assert terminal.release_runner_hold() == ""


def test_an_operators_pause_is_never_released_by_the_preflight():
    """Ownership: we clear a pause we wrote, and only that one."""
    terminal.set_flag(terminal.PAUSED, True)
    terminal.PAUSED.write_text("operator pause\n", encoding="utf-8")

    assert terminal.hold_queue_for_runner("runner unresolvable") is False
    assert terminal.release_runner_hold() == ""
    assert terminal.paused() is True, "an operator's pause must survive the preflight"


# ---------------------------------------------------------------------------
# 3. the loop: one escalation, no dispatch, before the inbox is read
# ---------------------------------------------------------------------------


def _drive_loop(monkeypatch, calls: list[list[str]], runner: str) -> int:
    """Run `terminal.loop --once` with the out-of-process seams stubbed."""

    def fake_run(command, **kwargs):  # noqa: ARG001 - the loop passes cwd/text
        calls.append(list(command))
        if command[:1] == ["git"]:
            return _Completed(stdout="abc1234\n")
        if "watch" in command:
            return _Completed(returncode=1, stderr="IDLE — no directive")
        return _Completed()

    monkeypatch.setattr(terminal.singleton, "guard", lambda *a, **k: True)
    monkeypatch.setattr(terminal.subprocess, "run", fake_run)
    monkeypatch.setattr(terminal, "write_heartbeat", lambda *a, **k: None)

    args = argparse.Namespace(
        runner=runner,
        watch_timeout=0.1,
        timeout=1.0,
        idle_sleep=0.0,
        dry_run=False,
        once=True,
    )
    saved = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        return terminal.loop(args)
    finally:
        for sig, handler in saved.items():
            signal.signal(sig, handler)


def test_an_unresolvable_runner_escalates_once_and_reads_no_inbox(monkeypatch):
    """The runaway amplifier, inverted: one escalation, no directive read, queue held."""
    calls: list[list[str]] = []

    rc = _drive_loop(monkeypatch, calls, ABSENT_RUNNER)

    assert rc == 1, "the loop must report that it cannot spawn"
    assert terminal.paused() is True, "an unresolvable runner must hold the queue"
    escalations = [command for command in calls if "escalate" in command]
    assert len(escalations) == 1, f"exactly one escalation, got {len(escalations)}: {escalations}"
    body = " ".join(escalations[0])
    assert ABSENT_RUNNER in body, "the escalation must name the runner it could not resolve"
    assert "queue is held" in body, "the escalation must say what the loop did about it"
    assert not any("watch" in command for command in calls), (
        "the inbox must not be read before the runner is resolved"
    )


def test_a_second_cycle_does_not_escalate_again(monkeypatch):
    """Not once per cycle either: the escalation is keyed, so the storm cannot form."""
    calls: list[list[str]] = []

    _drive_loop(monkeypatch, calls, ABSENT_RUNNER)
    _drive_loop(monkeypatch, calls, ABSENT_RUNNER)

    escalations = [command for command in calls if "escalate" in command]
    assert len(escalations) == 1, f"two cycles escalated {len(escalations)} times: {escalations}"


def test_a_different_failure_condition_is_escalated_in_its_own_right(monkeypatch):
    """A *changed* reason is a new fact, not a repeat of the old one."""
    calls: list[list[str]] = []

    _drive_loop(monkeypatch, calls, ABSENT_RUNNER)
    _drive_loop(monkeypatch, calls, "claude-733-also-absent")

    escalations = [command for command in calls if "escalate" in command]
    assert len(escalations) == 2, f"a different missing runner deserves its own line: {escalations}"


def test_a_resolvable_runner_does_not_hold_the_queue(monkeypatch, resolvable_default_runner):
    """The positive control: a runner that resolves leaves the loop free to work."""
    calls: list[list[str]] = []

    rc = _drive_loop(monkeypatch, calls, str(resolvable_default_runner))

    assert terminal.paused() is False, "a resolvable runner must not hold the queue"
    assert [command for command in calls if "escalate" in command] == []
    # `--once` on an empty inbox is IDLE, which is 0 — the loop never refused.
    assert rc == 0


# ---------------------------------------------------------------------------
# 4. the gate timeout is CANNOT-ASSESS, not a failure of the work
# ---------------------------------------------------------------------------


def test_a_timed_out_gate_is_cannot_assess():
    """The measured 1800s timeout: the gate attested nothing, so it is not a failure."""
    outcome, detail = terminal.run_gate("sleep 5", str(Path.cwd()), 0.2)

    assert outcome == terminal.GATE_CANNOT_ASSESS
    assert "timed out" in detail and "CANNOT-ASSESS" in detail


def test_a_gate_that_exits_nonzero_is_not_ok_and_one_that_passes_is_ok():
    not_ok, detail = terminal.run_gate("exit 3", str(Path.cwd()), 10.0)
    ok, _ = terminal.run_gate("exit 0", str(Path.cwd()), 10.0)

    assert not_ok == terminal.GATE_NOT_OK and "rc=3" in detail
    assert ok == terminal.GATE_OK


def test_the_gate_aggregate_is_fail_closed_and_cannot_assess_is_never_ok(monkeypatch):
    """`guardrails/honesty` aggregation: NOT-OK wins; CANNOT-ASSESS never reads OK."""
    monkeypatch.setattr(terminal, "issue_verify_command", lambda issue: "bash scripts/check-secrets.sh")

    monkeypatch.setattr(
        terminal,
        "run_gate",
        lambda command, cwd, timeout: (terminal.GATE_CANNOT_ASSESS, "timed out"),
    )
    outcome, _ = terminal.gate_evidence(733, None, 1.0)
    assert outcome == terminal.GATE_CANNOT_ASSESS
    assert terminal.escalation_severity(0, outcome) == "warn"

    monkeypatch.setattr(
        terminal,
        "run_gate",
        lambda command, cwd, timeout: (
            (terminal.GATE_CANNOT_ASSESS if "check-secrets" in command else terminal.GATE_NOT_OK),
            "detail",
        ),
    )
    outcome, _ = terminal.gate_evidence(733, None, 1.0)
    assert outcome == terminal.GATE_NOT_OK, "a real failure outranks an unassessable gate"


def test_a_run_the_loop_could_not_assess_is_never_done():
    """The verdict axis is unchanged by this: CANNOT-ASSESS is not a pass."""
    status, _ = terminal.verdict(0, "work appears finished", False, True)

    assert status == "failed", "an unassessed gate can never read as `done`"


@pytest.mark.parametrize(
    ("rc", "outcome", "expected"),
    [
        (0, terminal.GATE_OK, "warn"),
        (0, terminal.GATE_CANNOT_ASSESS, "warn"),
        (0, terminal.GATE_NOT_OK, "critical"),
        (1, terminal.GATE_OK, "critical"),
    ],
)
def test_escalation_severity_only_goes_critical_for_a_real_failure(rc, outcome, expected):
    """A timeout must not be escalated the way a failed gate is (#733)."""
    assert terminal.escalation_severity(rc, outcome) == expected


def test_the_runner_directories_are_derived_and_not_machine_specific():
    """The candidate directories are HOME-derived (or standard system paths)."""
    for candidate in terminal.runtime.RUNNER_DIRS:
        assert candidate.startswith("~") or candidate in ("/usr/local/bin", "/opt/homebrew/bin"), candidate
