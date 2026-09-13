"""The operator's control vocabulary: every lever must do something observable.

The ask was explicit — start / stop / pause / override / restart / kill / other,
tested with one full task. A control that silently does nothing is
indistinguishable from a dead loop, so each one either changes state the next
test can observe, or returns a verdict the loop acts on.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import channel  # noqa: E402
import terminal  # noqa: E402

PROFILE = Path(__file__).resolve().parents[1] / "profiles" / "brain.profile.json"


def test_every_control_the_profile_advertises_is_enforced():
    """The profile may not promise a lever the transport refuses."""
    profile = json.loads(PROFILE.read_text(encoding="utf-8"))
    assert set(profile["controls"]) <= set(channel.CONTROL_ACTIONS)


def test_the_control_vocabulary_covers_the_operator_list():
    for action in ("stop", "pause", "resume", "restart", "kill", "halt", "override", "poke", "status", "refresh"):
        assert action in channel.CONTROL_ACTIONS, f"{action} is not in the vocabulary"
    # `start` is a process verb: a control message needs a live loop to read it.
    control_source = (Path(__file__).resolve().parents[1] / "control.py").read_text(encoding="utf-8")
    assert '("start", cmd_start)' in control_source


def test_only_the_brain_may_issue_control():
    problems = channel.validate(
        {"from": "sister", "to": "brain", "type": "directive", "control": "pause", "correlation_id": "x"}
    )
    assert any("only the brain may issue control" in problem for problem in problems)


def test_an_unknown_control_is_refused():
    problems = channel.validate({"from": "brain", "to": "sister", "type": "directive", "control": "detonate"})
    assert any("control must be one of" in problem for problem in problems)


def test_override_must_name_the_issue_it_overrides():
    problems = channel.validate({"from": "brain", "to": "sister", "type": "directive", "control": "override"})
    assert any("must name the task.issue it overrides" in problem for problem in problems)
    assert (
        channel.validate(
            {"from": "brain", "to": "sister", "type": "directive", "control": "override", "task": {"issue": 142}}
        )
        == []
    )


@pytest.mark.parametrize(
    ("action", "verdict"),
    [
        ("pause", "continue"),
        ("resume", "continue"),
        ("stop", "continue"),
        ("kill", "kill"),
        ("status", "continue"),
        ("override", "dispatch-override"),
        ("refresh", "refresh"),
        ("restart", "restart"),
        ("halt", "halt"),
    ],
)
def test_each_control_maps_to_an_action_the_loop_takes(action, verdict):
    assert terminal.apply_control(action, {}, "subagent-x") == verdict


def test_pause_and_resume_are_observable_state(tmp_path, monkeypatch):
    monkeypatch.setattr(terminal, "PAUSED", tmp_path / "paused")
    assert terminal.paused() is False

    terminal.apply_control("pause", {}, "subagent-x")
    assert terminal.paused() is True, "pause must leave a durable flag the loop reads"

    terminal.apply_control("resume", {}, "subagent-x")
    assert terminal.paused() is False


def test_stop_is_a_flag_that_takes_effect_between_runs(tmp_path, monkeypatch):
    """`stop` must never abandon the run in flight."""
    monkeypatch.setattr(terminal, "PAUSED", tmp_path / "paused")
    monkeypatch.setattr(terminal, "STOPPING", tmp_path / "stopping")
    assert terminal.stopping() is False
    terminal.apply_control("stop", {}, "subagent-x")
    assert terminal.stopping() is True
    assert terminal.paused() is False, "stopping is not pausing the queue"


def test_kill_releases_the_run_before_it_takes_the_loop_down(monkeypatch):
    calls = []
    monkeypatch.setattr(terminal, "stop_and_release", lambda reason: calls.append(reason))
    assert terminal.apply_control("kill", {}, "subagent-x") == "kill"
    assert calls == ["control:kill"], "kill must go through the release path, not just exit"


# --- the control plane: process levers must work while a run blocks ------------


def test_pause_holds_work_but_never_controls():
    """The deadlock found live: a paused loop that stopped reading controls could
    not be resumed — `resume` sat unread until an operator cleared the flag."""
    assert terminal.work_held({"id": "d-1", "task": {"issue": 142}}, True) is True
    assert terminal.work_held({"id": "d-1", "control": "resume"}, True) is False
    assert terminal.work_held({"id": "d-1", "control": "poke"}, True) is False
    assert terminal.work_held({"id": "d-1", "task": {"issue": 142}}, False) is False


def test_stop_takes_effect_on_an_idle_loop(tmp_path, monkeypatch):
    """`stop` used to be checked only after a run, so an idle fleet never stopped."""
    monkeypatch.setattr(terminal, "STOPPING", tmp_path / "stopping")
    monkeypatch.setattr(terminal, "HEARTBEAT", tmp_path / "sister.heartbeat.json")
    import singleton

    monkeypatch.setattr(singleton, "FLEET", tmp_path / "singleton")
    (tmp_path / "stopping").write_text("2026-09-13T00:00:00Z\n", encoding="utf-8")
    args = type(
        "Args",
        (),
        {
            "once": True,
            "watch_timeout": 1.0,
            "idle_sleep": 0.01,
            "timeout": 1.0,
            "runner": "true",
            "dry_run": True,
        },
    )()
    assert terminal.loop(args) == 0, "an idle loop must honour stop immediately"
    assert not (tmp_path / "stopping").exists(), "the stop flag is cleared as it is honoured"


def test_the_loop_pid_comes_from_the_heartbeat(tmp_path, monkeypatch):
    import control

    fleet = tmp_path / ".fleet"
    fleet.mkdir(parents=True, exist_ok=True)
    (fleet / "sister.heartbeat.json").write_text(json.dumps({"pid": 4242}), encoding="utf-8")
    monkeypatch.setattr(control, "ROOT", tmp_path)
    assert control._loop_pid() == 4242


def test_kill_signals_the_loop_because_a_message_would_wait_for_the_run(tmp_path, monkeypatch):
    """The loop blocks in the child run, so a mailbox-only kill would sit unread."""
    import control

    sent, signalled = [], []
    monkeypatch.setattr(control, "ROOT", tmp_path)
    (tmp_path / ".fleet").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".fleet" / "sister.heartbeat.json").write_text(json.dumps({"pid": 4242}), encoding="utf-8")
    monkeypatch.setattr(control, "_send_control", lambda action: sent.append(action))
    monkeypatch.setattr(control.os, "kill", lambda pid, sig: signalled.append((pid, sig)))

    assert control.cmd_kill(type("Args", (), {})()) == 0
    assert sent == ["kill"], "the order must still be recorded in the channel"
    assert signalled == [(4242, control.signal.SIGTERM)], "and it must take effect immediately"


def test_status_reports_the_flags_and_the_loop_pid(tmp_path, monkeypatch, capsys):
    import control

    monkeypatch.setattr(control, "ROOT", tmp_path)
    fleet = tmp_path / ".fleet"
    (fleet / "runs").mkdir(parents=True, exist_ok=True)
    (fleet / "paused").write_text("x", encoding="utf-8")
    (fleet / "sister.heartbeat.json").write_text(json.dumps({"pid": 99}), encoding="utf-8")
    monkeypatch.setattr(control, "_run", lambda cmd, check=True: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())

    control.cmd_status(type("Args", (), {})())
    out = capsys.readouterr().out
    assert "paused: yes" in out and "stopping: no" in out
    assert "loop pid (from heartbeat): 99" in out


def test_the_brain_profile_drives_the_floors_and_the_kb(tmp_path):
    """A profile that nothing reads is decoration."""
    import brain

    assert brain.DEFAULT_TIER == brain.PROFILE["finops"]["default_tier"]
    assert "security" in brain.HIGH_FLOOR_LANES
    assert brain.KB_SOURCES, "the brain must know which KB it steers by"


def test_the_brain_attaches_the_kb_and_the_report_contract_to_a_directive(monkeypatch):
    import brain

    order = {
        "id": "o-1",
        "task": {"issue": 5, "lane": "fleet"},
        "body": "do the thing",
    }
    directive = brain.build_directive(order)
    assert "Read first (fleet KB)" in directive["body"]
    assert brain.KB_SOURCES[0] in directive["body"]
    assert "Return:" in directive["body"]


def test_a_missing_profile_is_refused_not_defaulted(tmp_path):
    """Steering a fleet on a half-loaded doctrine is worse than not starting."""
    import brain

    with pytest.raises(SystemExit):
        brain.load_profile(tmp_path / "absent.json")

    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps({"mission": "x"}), encoding="utf-8")
    with pytest.raises(SystemExit):
        brain.load_profile(broken)


# --- micro-decomposition: the brain files children and advances waves ----------


def test_decompose_files_children_and_dispatches_only_wave_1(tmp_path, monkeypatch):
    import brain

    monkeypatch.setattr(brain, "WAVES", tmp_path / "waves")
    filed = []
    monkeypatch.setattr(brain, "gh_issue_create", lambda title, body: (filed.append(title), 300 + len(filed))[1])
    dispatched = []
    monkeypatch.setattr(brain, "dispatch", lambda order: (dispatched.append(order["task"]["issue"]), (True, "ok"))[1])

    spec = {
        "parent_issue": 219,
        "children": [
            {"title": "a", "lane": "fleet", "verify": "pytest a", "files": ["a.py"], "depends_on": []},
            {"title": "b", "lane": "fleet", "verify": "pytest b", "files": ["b.py"], "depends_on": [0]},
            {"title": "c", "lane": "fleet", "verify": "pytest c", "files": ["c.py"], "depends_on": [0]},
        ],
    }
    ok, report = brain.handle_decompose({"task": {"decompose": spec}})
    assert ok is True and len(filed) == 3
    assert dispatched == [301], "only the dependency-free wave is dispatched now; b and c wait"


def test_advance_waves_dispatches_children_whose_deps_are_closed(tmp_path, monkeypatch):
    import brain

    monkeypatch.setattr(brain, "WAVES", tmp_path / "waves")
    plan = {
        "parent": 219,
        "children": [
            {"index": 0, "issue": 301, "lane": "fleet", "verify": "x", "depends_on": []},
            {"index": 1, "issue": 302, "lane": "fleet", "verify": "y", "depends_on": [0]},
        ],
        "dispatched": [301],
    }
    brain.WAVES.mkdir(parents=True, exist_ok=True)
    (brain.WAVES / "219.json").write_text(json.dumps(plan), encoding="utf-8")
    monkeypatch.setattr(brain, "issue_is_closed", lambda n: n == 301)
    dispatched = []
    monkeypatch.setattr(brain, "dispatch", lambda order: (dispatched.append(order["task"]["issue"]), (True, "ok"))[1])
    assert brain.advance_waves() == [302]
    assert brain.issue_is_closed(301) is True and brain.issue_is_closed(999) is False


def test_decompose_refuses_a_spec_without_children(tmp_path):
    import brain

    ok, report = brain.handle_decompose({"task": {"decompose": {"parent_issue": 219}}})
    assert ok is False and "no children" in report
