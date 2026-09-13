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
