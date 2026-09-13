"""The dumb-terminal loop and its helpers (issue for session launchers)."""

from __future__ import annotations

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
    assert "claim --issue 163" in prompt


def test_extract_json_parses_watch_output():
    text = '{\n "id": "d-1",\n "type": "directive"\n}\nchannel watch: DIRECTIVE d-1 — 1 pending'
    assert terminal.extract_json(text) == {"id": "d-1", "type": "directive"}


def test_extract_json_returns_empty_for_garbage():
    assert terminal.extract_json("no json here") == {}


def test_build_command_appends_the_prompt_as_the_final_argument():
    command = terminal.build_command({"id": "d-1", "task": {"issue": 163}, "body": "x"}, "claude -p")
    assert command[0] == "claude"
    assert command[1] == "-p"
    assert "issue #163" in command[-1]


def test_run_once_dry_run_prints_and_returns_zero():
    directive = {"id": "d-1", "task": {"issue": 163}, "body": "x"}
    rc, output = terminal.run_once(directive, "claude -p", 10.0, dry_run=True)
    assert rc == 0
    assert "DRY-RUN" in output
