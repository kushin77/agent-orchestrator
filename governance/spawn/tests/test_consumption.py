"""Both spawn paths consume ONE envelope, and the loop refuses a broken one (#793).

This is the convergence proof at unit level: `fleet/terminal.py::build_prompt`'s
output is EXACTLY `governance/spawn`'s rendering of the document it just built —
not a paraphrase, not a prompt that happens to mention the same facts. The
sources are injected, so the test is offline and deterministic; the shell gate
`scripts/check-spawn-envelope.sh` drives both paths for real.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from governance.spawn import EnvelopeRefused, render, sources

REPO_ROOT = Path(__file__).resolve().parents[3]


def liveness_stamp(seconds_ago: float) -> str:
    """A beat in the exact form `fleet/terminal.py::_now` writes."""
    moment = datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def terminal_module():
    """Import the loop as the standalone script it is (``fleet/`` on sys.path)."""
    import terminal  # noqa: PLC0415

    return terminal


def directive(issue: int = 793, lane: str = "spawn-envelope", body: str = "order body") -> dict:
    return {
        "id": "fixture-directive",
        "model": {"tier": "flash", "thinking": "none"},
        "task": {"issue": issue, "lane": lane},
        "body": body,
        "control": None,
    }


def test_the_fleet_prompt_IS_the_rendered_envelope(
    monkeypatch: pytest.MonkeyPatch, envelope_fields: dict[str, Any]
) -> None:
    """Exact equality: a paraphrase would fail this, which is the point."""
    terminal = terminal_module()
    fields = dict(envelope_fields)
    monkeypatch.setattr(sources, "collect", lambda **_: dict(fields))

    prompt = terminal.build_prompt(
        directive(), "fixture-agent", Path(fields["worktree"]), {}, {}
    )

    document = dict(fields)
    spawn_meta = document.pop("spawn")
    expected = render.prompt(
        terminal.spawn.model.assemble(document, spawn=spawn_meta),
        standing=terminal.load_standing_body(),
        directive_body="order body",
        directive_id="fixture-directive",
        tier="flash",
        thinking="none",
        context_block="",
    )

    assert prompt == expected


def test_the_fleet_prompt_carries_the_envelope_marker_and_every_field(
    monkeypatch: pytest.MonkeyPatch, envelope_fields: dict[str, Any]
) -> None:
    terminal = terminal_module()
    fields = dict(envelope_fields)
    monkeypatch.setattr(sources, "collect", lambda **_: dict(fields))

    prompt = terminal.build_prompt(directive(), "fixture-agent", Path(fields["worktree"]), {}, {})

    assert render.MARKER in prompt
    assert fields["trailer"] in prompt
    assert fields["session"]["id"] in prompt
    assert fields["gate"]["bound"] in prompt
    assert fields["verify"]["command"] in prompt


def test_build_prompt_refuses_a_malformed_envelope_by_name(
    monkeypatch: pytest.MonkeyPatch, envelope_fields: dict[str, Any]
) -> None:
    terminal = terminal_module()
    fields = dict(envelope_fields)
    fields["claim"] = {"owner": "", "state": "unclaimed", "lane": ""}
    monkeypatch.setattr(sources, "collect", lambda **_: fields)

    with pytest.raises(EnvelopeRefused) as refused:
        terminal.build_prompt(directive(), "fixture-agent", Path(fields["worktree"]), {}, {})

    assert "claim.owner" in str(refused.value)


def test_the_loop_refuses_the_spawn_before_any_child_exists(
    monkeypatch: pytest.MonkeyPatch, envelope_fields: dict[str, Any]
) -> None:
    """Fail-closed, with the refusal's own exit code — nothing is spawned."""
    terminal = terminal_module()
    fields = dict(envelope_fields)
    fields["worktree"] = ""
    fields["session"] = {**fields["session"], "id": "", "branch": "", "agent": ""}
    monkeypatch.setattr(sources, "collect", lambda **_: fields)
    monkeypatch.setattr(
        terminal.subprocess,
        "Popen",
        lambda *a, **k: pytest.fail("a child was spawned for a refused envelope"),
    )

    rc, output = terminal.run_once(
        directive(),
        "claude -p",
        5.0,
        False,
        "fixture-agent",
        Path(fields["worktree"]),
        {},
        {"dispatch": {"runner": "claude -p"}},
        {},
    )

    assert rc == terminal.RC_REFUSED
    assert "spawn refused" in output
    assert "worktree" in output and "session.id" in output


def test_the_loop_still_spawns_when_the_envelope_is_well_formed(
    monkeypatch: pytest.MonkeyPatch, envelope_fields: dict[str, Any]
) -> None:
    """The positive control: the same path with a valid envelope reaches a child."""
    terminal = terminal_module()
    fields = dict(envelope_fields)
    Path(fields["worktree"]).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(sources, "collect", lambda **_: fields)
    monkeypatch.setattr(terminal, "start_session_beat", lambda env, pid: None)
    monkeypatch.setattr(terminal, "stop_session_beat", lambda beater: None)

    rc, output = terminal.run_once(
        directive(),
        "claude -p",
        20.0,
        False,
        "fixture-agent",
        Path(fields["worktree"]),
        {},
        {"dispatch": {"runner": "/bin/echo"}},
        {},
    )
    assert rc == 0
    assert render.MARKER in output


# --- the watchdog: flight is the marker's own evidence (#793, criterion 4) ----


def test_run_in_flight_ignores_a_leftover_marker_of_a_crashed_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The REAL function, against the measured box state — the loop pid is alive."""
    watchdog = watchdog_module()
    from governance.spawn import liveness

    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "leftover.json").write_text(
        json.dumps({"issue": 234, "pid": os.getpid(), "child_pid": None, "ts": liveness_stamp(16200)}),
        encoding="utf-8",
    )
    monkeypatch.setattr(watchdog, "RUNS_DIR", runs)

    assert watchdog.run_in_flight() is False


def test_run_in_flight_sees_a_live_child(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    watchdog = watchdog_module()

    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "working.json").write_text(
        json.dumps({"issue": 234, "pid": os.getpid(), "child_pid": os.getpid(), "ts": "1999-01-01T00:00:00Z"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(watchdog, "RUNS_DIR", runs)

    assert watchdog.run_in_flight() is True


def test_the_watchdog_reports_the_heartbeat_contradiction_on_a_pass(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`idle` beside a live child is reported — never a silent win for the marker."""
    watchdog = watchdog_module()

    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "working.json").write_text(
        json.dumps({"issue": 234, "pid": os.getpid(), "child_pid": os.getpid(), "ts": "1999-01-01T00:00:00Z"}),
        encoding="utf-8",
    )
    beat = tmp_path / "sister.heartbeat.json"
    beat.write_text(json.dumps({"pid": os.getpid(), "state": "idle", "commit": "abc1234"}), encoding="utf-8")

    monkeypatch.setattr(watchdog, "RUNS_DIR", runs)
    monkeypatch.setattr(watchdog, "respawn_attempt_cap", lambda: 3)
    monkeypatch.setattr(watchdog.channel, "remote_head_commit", lambda: "abc1234")
    monkeypatch.setattr(watchdog.channel, "head_commit", lambda: "abc1234")
    monkeypatch.setattr(watchdog, "rung_action", lambda *a, **k: "sister: healthy (fixture)")
    monkeypatch.setattr(watchdog, "monitor_missing", lambda: False)
    monkeypatch.setattr(
        watchdog, "RUNGS", (("sister", "fleet/terminal.py", "fleet/terminal.sh", beat),)
    )

    watchdog.watchdog_once()

    out = capsys.readouterr().out
    assert "CONTRADICTION" in out
    assert "idle" in out and "keeps the lock" in out


def watchdog_module():
    import watchdog  # noqa: PLC0415

    return watchdog


def test_an_unreadable_envelope_refuses_a_spawn_through_the_cli(
    envelope_fields: dict[str, Any], tmp_path: Path
) -> None:
    """The local path refuses in its own process, with its own exit code."""
    from governance.spawn import model

    fields = dict(envelope_fields)
    spawn_meta = fields.pop("spawn")
    document = model.assemble(fields, spawn=spawn_meta)
    target = tmp_path / "envelope.json"
    target.write_text(model.dumps(document), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "governance" / "spawn" / "cli.py"),
         "check", "--file", str(target), "--without", "claim"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == model.EXIT_REFUSED
    assert "claim" in result.stderr
    assert "REFUSED" in result.stderr


def test_the_cli_admits_the_same_document_when_nothing_is_removed(
    envelope_fields: dict[str, Any], tmp_path: Path
) -> None:
    from governance.spawn import model

    fields = dict(envelope_fields)
    spawn_meta = fields.pop("spawn")
    document = model.assemble(fields, spawn=spawn_meta)
    target = tmp_path / "envelope.json"
    target.write_text(model.dumps(document), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "governance" / "spawn" / "cli.py"),
         "check", "--file", str(target)],
        capture_output=True,
        text=True,
    )

    assert result.returncode == model.EXIT_OK, result.stderr
    assert "spawn: OK" in result.stdout
