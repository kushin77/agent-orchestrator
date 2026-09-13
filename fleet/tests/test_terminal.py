"""The dumb-terminal loop and its helpers (issue for session launchers)."""

from __future__ import annotations

import json

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
