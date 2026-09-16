"""Issue #694 — an orphaned claim is handed to the reconciler before escalating.

The defect: `held_action` returns `orphaned` for a claim held by someone else with
no live run, and the loop answered that with an escalation and an attempt count —
"escalating, left pending" — while the reconciler that OWNS orphan teardown
(issue #304) was never asked to do its job. The claim stayed wedged.

These tests pin the three properties that make the handoff safe:

* it reads the reconciler's OWN verdict (`reclaimed`/`parked` mean ended) and
  imports that vocabulary rather than restating it;
* it DELEGATES the decision — a `shelved` orphan (unmerged work) is not ended, so
  the loop's bounded escalation still runs, which is the pre-#694 behaviour
  preserved rather than replaced;
* it is BOUNDED to one sweep per episode per directive, or the handoff would
  itself be the runaway the guard exists to bound.
"""

from __future__ import annotations

import json
import subprocess
import types
from pathlib import Path

import terminal


def _result(actions, *, rc=0, stderr=""):
    """A `reconcile sweep --apply --json` invocation's outcome.

    The real command prints its report and then a human line on stdout, so the
    fixture does too: the parse must survive the trailing text, exactly as it
    must survive it in the loop.
    """
    return types.SimpleNamespace(
        returncode=rc,
        stdout=json.dumps(
            {"at": 0, "applied": True, "counts": {}, "board_reports": [], "actions": actions}
        )
        + "\nreconcile: OK\n",
        stderr=stderr,
    )


def _action(issue=694, session_id="1a28ceea", outcome="reclaimed", reason="heartbeat absent past TTL"):
    return {
        "session_id": session_id,
        "issue": issue,
        "agent": "other-agent",
        "status": "orphaned",
        "reason": reason,
        "outcome": outcome,
        "steps": [],
    }


def _recorder(result):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return result

    return calls, fake_run


def test_the_reconciler_ending_the_orphan_is_read_as_ended(tmp_path, monkeypatch):
    monkeypatch.setattr(terminal, "RUNS", tmp_path / "runs")
    calls, fake_run = _recorder(_result([_action()]))
    monkeypatch.setattr(terminal.subprocess, "run", fake_run)

    ended, detail = terminal.hand_orphan_to_reconciler("d-1", 694, "other-agent")

    assert ended is True, "a reclaimed session is the reconciler ending the orphan"
    assert "reclaimed" in detail and "1a28ceea" in detail
    assert calls[0][1:4] == [terminal.RECONCILE_CLI, "sweep", "--apply"], (
        "the sweep must be asked to ACT: a dry run decides nothing and releases no claim"
    )
    assert terminal.orphan_handoff_done("d-1")["ended"] is True


def test_a_parked_orphan_is_ended_too(tmp_path, monkeypatch):
    """`parked` is the reconciler's other touching outcome: worktree reclaimed,
    the branch kept because origin is the only copy. The claim is released."""
    monkeypatch.setattr(terminal, "RUNS", tmp_path / "runs")
    _, fake_run = _recorder(_result([_action(outcome="parked")]))
    monkeypatch.setattr(terminal.subprocess, "run", fake_run)

    ended, detail = terminal.hand_orphan_to_reconciler("d-1", 694, "other-agent")
    assert ended is True
    assert "parked" in detail


def test_a_shelved_orphan_is_NOT_ended_so_the_escalation_still_runs(tmp_path, monkeypatch):
    """The reconciler keeps a claim whose lane holds unmerged work. The handoff
    must not turn that into a pass — the loop's bounded escalation is what
    handles it, exactly as before #694."""
    monkeypatch.setattr(terminal, "RUNS", tmp_path / "runs")
    _, fake_run = _recorder(
        _result([_action(outcome="shelved", reason="unmerged work exists only here")])
    )
    monkeypatch.setattr(terminal.subprocess, "run", fake_run)

    ended, detail = terminal.hand_orphan_to_reconciler("d-1", 694, "other-agent")
    assert ended is False, "shelved is a deliberate non-ending, not a failure to notice"
    assert "shelved" in detail and "unmerged work" in detail
    assert terminal.orphan_handoff_done("d-1")["ended"] is False


def test_the_handoff_runs_once_per_episode(tmp_path, monkeypatch):
    """The bound: a sweep on every poll would be its own runaway."""
    monkeypatch.setattr(terminal, "RUNS", tmp_path / "runs")
    calls, fake_run = _recorder(_result([_action(outcome="shelved")]))
    monkeypatch.setattr(terminal.subprocess, "run", fake_run)

    first = terminal.hand_orphan_to_reconciler("d-1", 694, "other-agent")
    second = terminal.hand_orphan_to_reconciler("d-1", 694, "other-agent")
    third = terminal.hand_orphan_to_reconciler("d-1", 694, "other-agent")

    assert first[0] is False and second[0] is False and third[0] is False
    assert len(calls) == 1, f"three cycles must sweep once, not {len(calls)} times"
    assert "already handed over once" in second[1]


def test_a_new_episode_may_hand_over_again(tmp_path, monkeypatch):
    """Clearing the mark is what makes a genuinely new orphan a new episode."""
    monkeypatch.setattr(terminal, "RUNS", tmp_path / "runs")
    calls, fake_run = _recorder(_result([_action()]))
    monkeypatch.setattr(terminal.subprocess, "run", fake_run)

    assert terminal.hand_orphan_to_reconciler("d-1", 694, "other-agent")[0] is True
    terminal.clear_orphan_handoff("d-1")
    assert terminal.hand_orphan_to_reconciler("d-1", 694, "other-agent")[0] is True
    assert len(calls) == 2


def test_a_reconciler_that_cannot_answer_degrades_to_the_escalation(tmp_path, monkeypatch):
    """A broken reconciler must not silence the loop: every failure mode returns
    False so the caller escalates, which is the pre-#694 behaviour."""
    monkeypatch.setattr(terminal, "RUNS", tmp_path / "runs")

    cases = {
        "garbage": _result([], rc=1, stderr="Traceback: boom"),
        "no-action-for-this-issue": _result([_action(issue=999)]),
        "empty-report": types.SimpleNamespace(returncode=0, stdout="", stderr=""),
    }
    for name, result in cases.items():
        terminal.clear_orphan_handoff("d-1")
        _, fake_run = _recorder(result)
        monkeypatch.setattr(terminal.subprocess, "run", fake_run)
        ended, detail = terminal.hand_orphan_to_reconciler("d-1", 694, "other-agent")
        assert ended is False, f"{name} must not read as ended"
        assert detail, f"{name} must still explain itself"


def test_a_timeout_is_a_failed_handoff_and_never_an_exception(tmp_path, monkeypatch):
    monkeypatch.setattr(terminal, "RUNS", tmp_path / "runs")

    def slow(command, **kwargs):
        raise subprocess.TimeoutExpired(cmd=command, timeout=kwargs.get("timeout", 0))

    monkeypatch.setattr(terminal.subprocess, "run", slow)
    ended, detail = terminal.hand_orphan_to_reconciler("d-1", 694, "other-agent", timeout=0.01)
    assert ended is False
    assert "did not answer" in detail


def test_the_orphan_classification_the_handoff_hangs_off_is_unchanged():
    """#694 hangs off the `orphaned` verdict; that verdict must not drift."""
    assert terminal.held_action("other-agent", "subagent-d1", "orphaned") == "orphaned"
    assert terminal.held_action(None, "subagent-d1", "none") == "orphaned"
    assert terminal.held_action("subagent-d1", "subagent-d1", "orphaned") == "self-heal"


def test_the_handoff_state_lives_beside_the_guard_state_and_not_in_the_live_fleet(
    tmp_path, monkeypatch
):
    """Same rule as the guard: a test that redirects RUNS must not write into the
    live fleet's `.fleet/`."""
    monkeypatch.setattr(terminal, "RUNS", tmp_path / "runs")
    path = Path(terminal.orphan_handoff_path("d-9"))
    assert path == tmp_path / "runs" / ".." / terminal.ORPHAN_HANDOFFS / "d-9.json" or (
        str(tmp_path) in str(path)
    ), f"handoff state left the redirected root: {path}"
    terminal.record_orphan_handoff("d-9", {"ended": False, "detail": "x"})
    assert path.exists()
    assert terminal.orphan_handoff_done("d-9")["detail"] == "x"
