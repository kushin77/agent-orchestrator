"""The dumb-terminal loop and its helpers (issue for session launchers)."""

from __future__ import annotations

import json
import subprocess
import time

import terminal


def test_build_prompt_contains_issue_and_directive():
    directive = {
        "id": "d-1",
        "task": {"issue": 163, "lane": "fleet"},
        "model": {"tier": "flash", "thinking": "low"},
        "body": "harvest-first",
    }
    prompt = terminal.build_prompt(directive)
    assert "#163" in prompt
    assert "d-1" in prompt
    assert "harvest-first" in prompt
    # The loop owns the claim, so the subagent must not re-claim or release it.
    assert "ALREADY CLAIMED" in prompt
    assert "do NOT run claim" in prompt


def test_build_prompt_names_the_isolated_worktree(tmp_path):
    tree = tmp_path / "ao-163-dabc1234"
    prompt = terminal.build_prompt({"id": "d-abc12345", "task": {"issue": 163}, "body": "x"}, "subagent-dabc1234", tree)
    assert str(tree) in prompt
    assert "never in the " in prompt
    assert "shared checkout" in prompt


def test_extract_json_parses_watch_output():
    text = '{\n "id": "d-1",\n "type": "directive"\n}\nchannel watch: DIRECTIVE d-1 — 1 pending'
    assert terminal.extract_json(text) == {"id": "d-1", "type": "directive"}


def test_extract_json_returns_empty_for_garbage():
    assert terminal.extract_json("no json here") == {}


def test_build_command_appends_the_prompt_as_the_final_argument():
    command = terminal.build_command({"id": "d-1", "task": {"issue": 163}, "body": "x"}, "claude -p", "subagent-d1")
    assert command[0] == "claude"
    assert command[1] == "-p"
    assert "issue #163" in command[-1]
    assert "subagent-d1" in command[-1]


def test_run_once_dry_run_prints_and_returns_zero():
    directive = {"id": "d-1", "task": {"issue": 163}, "body": "x"}
    rc, output = terminal.run_once(directive, "claude -p", 10.0, dry_run=True, agent_id="subagent-d1")
    assert rc == 0
    assert "DRY-RUN" in output


def test_build_prompt_uses_the_unique_agent_id():
    directive = {"id": "d-abc12345", "task": {"issue": 163, "lane": "fleet"}, "body": "x"}
    prompt = terminal.build_prompt(directive, "subagent-dabc1234")
    assert "subagent-dabc1234" in prompt


def test_provision_worktree_returns_none_when_git_refuses(tmp_path, monkeypatch):
    """A failed provisioning must be visible, not silently shared-checkout."""
    monkeypatch.setattr(terminal, "WORKTREE_ROOT", tmp_path / "ao-worktrees")

    class Failed:
        returncode = 128
        stdout = ""
        stderr = "fatal: boom"

    monkeypatch.setattr(terminal.subprocess, "run", lambda *a, **k: Failed())
    assert terminal.provision_worktree(163, "d-abc12345") is None


def test_provision_worktree_names_a_per_directive_branch(tmp_path, monkeypatch):
    monkeypatch.setattr(terminal, "WORKTREE_ROOT", tmp_path / "ao-worktrees")

    class Ok:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(terminal.subprocess, "run", lambda *a, **k: Ok())
    path, branch = terminal.provision_worktree(163, "d-abc12345")
    assert path == tmp_path / "ao-worktrees" / "ao-163-d-abc123"
    assert branch == "issue-163-d-abc123"


def test_claim_issue_reports_the_refusal_verbatim(monkeypatch):
    class Refused:
        returncode = 1
        stdout = ""
        stderr = "no-chain-edge: #5 is not the next step"

    monkeypatch.setattr(terminal.subprocess, "run", lambda *a, **k: Refused())
    ok, output = terminal.claim_issue(5, "subagent-x", "fleet", "d-1")
    assert ok is False and "no-chain-edge" in output


class _Ok:
    returncode = 0


def test_held_action_distinguishes_in_flight_from_orphaned():
    """The regression: an untracked holder read as 'in flight' and the directive
    was consumed, so the run was recorded nowhere and the claim stayed wedged."""
    assert terminal.held_action("subagent-abc", "subagent-abc", "live") == "in-flight"
    assert terminal.held_action("other-agent", "subagent-abc", "live") == "in-flight"
    assert terminal.held_action("subagent-abc", "subagent-abc", "orphaned") == "self-heal"
    assert terminal.held_action("other-agent", "subagent-abc", "orphaned") == "orphaned"
    assert terminal.held_action("other-agent", "subagent-abc", "none") == "orphaned"
    assert terminal.held_action(None, "subagent-abc", "none") == "orphaned"


def test_a_run_marker_is_live_then_orphaned_when_its_loop_dies(tmp_path, monkeypatch):
    monkeypatch.setattr(terminal, "RUNS", tmp_path / "runs")
    assert terminal.run_state("d-1") == "none"

    terminal.mark_run("d-1", 142, "subagent-d1")
    assert terminal.run_state("d-1") == "live", "our own live loop must read as tracked"

    dead = subprocess.Popen(["true"])
    dead.wait()
    (terminal.RUNS / "d-2.json").write_text(
        json.dumps({"issue": 142, "agent": "subagent-d2", "pid": dead.pid}), encoding="utf-8"
    )
    assert terminal.run_state("d-2") == "orphaned", "a run whose loop is gone is not in flight"

    terminal.clear_run("d-1")
    assert terminal.run_state("d-1") == "none"


def test_report_once_says_it_once_per_state(tmp_path, monkeypatch):
    """A pending directive is re-read every cycle; the report must not repeat."""
    sent = []

    def fake_run(command, **kwargs):
        sent.append(command)
        return _Ok()

    monkeypatch.setattr(terminal, "REPORTED", tmp_path / "reported")
    monkeypatch.setattr(terminal.subprocess, "run", fake_run)

    assert terminal.report_once("d-1", "orphaned:agent-x", "escalate", "body") is True
    assert terminal.report_once("d-1", "orphaned:agent-x", "escalate", "body") is False
    assert len(sent) == 1, "the same state must not be reported twice"
    assert terminal.report_once("d-1", "orphaned:agent-y", "escalate", "body") is True
    assert len(sent) == 2, "a changed state is news again"


def test_clear_reported_lets_a_new_cycle_speak(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(terminal, "REPORTED", tmp_path / "reported")
    monkeypatch.setattr(terminal.subprocess, "run", lambda command, **kwargs: (sent.append(command), _Ok())[1])
    terminal.report_once("d-1", "in-flight:agent-x", "result", "body")
    terminal.clear_reported("d-1")
    terminal.report_once("d-1", "in-flight:agent-x", "result", "body")
    assert len(sent) == 2


def test_a_run_keeps_its_beat_fresh_while_the_child_works(tmp_path, monkeypatch):
    """A long run must not look like a dead loop — the misread cost two restarts."""
    monkeypatch.setattr(terminal, "HEARTBEAT", tmp_path / "sister.heartbeat.json")
    monkeypatch.setattr(terminal, "IN_FLIGHT", {"child": None, "issue": 142, "agent_id": "subagent-x", "directive": "d"})

    beater = terminal.start_beating("2026-09-13T00:00:00Z", "abc1234", 142, "subagent-x", interval=0.05)
    try:
        time.sleep(0.2)
        beat = json.loads(terminal.HEARTBEAT.read_text(encoding="utf-8"))
        first_ts = beat["ts"]
        assert beat["state"] == "working:#142" and beat["issue"] == 142 and beat["agent"] == "subagent-x"

        time.sleep(0.2)
        assert json.loads(terminal.HEARTBEAT.read_text(encoding="utf-8"))["ts"] >= first_ts
    finally:
        beater.stop()

    # `stop()` must be synchronous: a beater that outlived the test wrote the
    # test's values into the LIVE heartbeat once monkeypatch restored the path.
    after_stop = json.loads(terminal.HEARTBEAT.read_text(encoding="utf-8"))["ts"]
    time.sleep(0.25)
    assert json.loads(terminal.HEARTBEAT.read_text(encoding="utf-8"))["ts"] == after_stop


def test_the_heartbeat_can_name_the_child_process(tmp_path, monkeypatch):
    monkeypatch.setattr(terminal, "HEARTBEAT", tmp_path / "sister.heartbeat.json")
    terminal.write_heartbeat(
        "working:#142", started_at="2026-09-13T00:00:00Z", commit="abc1234",
        issue=142, agent="subagent-x", child_pid=4242,
    )
    beat = json.loads(terminal.HEARTBEAT.read_text(encoding="utf-8"))
    assert beat["child_pid"] == 4242 and beat["issue"] == 142


def test_stop_and_release_frees_the_in_flight_claim(monkeypatch):
    """A restart must not strand the claim — the wedge, recreated by the operator."""
    calls = []

    def fake_release(issue, agent):
        calls.append(("release", issue, agent))
        return True, "ok"

    def fake_run(command, **kwargs):
        calls.append(("escalate", command))
        return _Ok()

    monkeypatch.setattr(terminal, "release_issue", fake_release)
    monkeypatch.setattr(terminal.subprocess, "run", fake_run)
    terminal.IN_FLIGHT.update({"issue": 167, "agent_id": "subagent-abc12345", "directive": "d-1", "child": None})
    try:
        terminal.stop_and_release("signal 15")
    finally:
        terminal.IN_FLIGHT.update({"issue": None, "agent_id": None, "directive": None, "child": None})
    assert ("release", 167, "subagent-abc12345") in calls
    assert any(kind == "escalate" for kind, *_ in calls), "the brain must be told the loop stopped mid-run"


def test_stop_and_release_reports_a_failed_release(monkeypatch):
    bodies = []

    def fake_run(command, **kwargs):
        bodies.append(command[-1])
        return _Ok()

    monkeypatch.setattr(terminal, "release_issue", lambda issue, agent: (False, "REFUSED: not the holder"))
    monkeypatch.setattr(terminal.subprocess, "run", fake_run)
    terminal.IN_FLIGHT.update({"issue": 167, "agent_id": "subagent-abc12345", "directive": "d-1", "child": None})
    try:
        terminal.stop_and_release("signal 15")
    finally:
        terminal.IN_FLIGHT.update({"issue": None, "agent_id": None, "directive": None, "child": None})
    assert any("RELEASE FAILED" in body for body in bodies), f"bodies={bodies}"


def test_stop_with_nothing_held_says_so_instead_of_claiming_a_release(monkeypatch):
    """The handler must not report a release that never happened."""
    bodies = []

    def fake_run(command, **kwargs):
        bodies.append(command[-1])
        # `held` exits 1 when the issue is free — nothing to release.
        return type("R", (), {"returncode": 1, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(terminal, "release_issue", lambda issue, agent: (True, "should not be called"))
    monkeypatch.setattr(terminal.subprocess, "run", fake_run)
    terminal.IN_FLIGHT.update({"issue": 167, "agent_id": "subagent-abc12345", "directive": "d-1", "child": None})
    try:
        terminal.stop_and_release("signal 15")
    finally:
        terminal.IN_FLIGHT.update({"issue": None, "agent_id": None, "directive": None, "child": None})
    assert any("no live claim to release" in body for body in bodies), f"bodies={bodies}"


def test_an_idle_stop_releases_nothing(monkeypatch):
    calls = []

    def fake_release(issue, agent):
        calls.append(issue)
        return True, "ok"

    monkeypatch.setattr(terminal, "release_issue", fake_release)
    terminal.IN_FLIGHT.update({"issue": None, "agent_id": None, "directive": None, "child": None})
    terminal.stop_and_release("signal 15")
    assert calls == []


def test_run_once_reports_an_unstartable_runner(tmp_path, monkeypatch):
    monkeypatch.setattr(terminal, "ROOT", tmp_path)
    rc, output = terminal.run_once({"id": "d-1", "task": {"issue": 1}}, "definitely-not-a-command-xyz", 5.0, False, "subagent-d1")
    assert rc == 127
    assert "could not start" in output


def test_directive_issue_rejects_missing_or_invalid_issue():
    assert terminal.directive_issue({"id": "d-1", "task": {}}) is None
    assert terminal.directive_issue({"id": "d-1"}) is None
    assert terminal.directive_issue({"id": "d-1", "task": {"issue": 0}}) is None
    assert terminal.directive_issue({"id": "d-1", "task": {"issue": 163}}) == 163


def test_looks_refused_detects_a_stopped_subagent():
    assert terminal.looks_refused("Claim REFUSED: already-claimed") is True
    assert terminal.looks_refused("no work done — stopping") is True
    assert terminal.looks_refused("no real issue number") is True
    assert terminal.looks_refused("merged PR #163; verify PASS") is False
