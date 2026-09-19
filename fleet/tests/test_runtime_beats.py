"""Every runtime's producer, asserted by effect (issue #1412).

WHY THIS MODULE EXISTS
----------------------
`fleet/runtimes.yaml` (7 ids), the beat adapter (`.fleet/runtime-beats/<id>.json`)
and `scripts/check-runtime-liveness.sh` all landed in #1376, and NOTHING WROTE A
BEAT: on the real tree the judge could only report `no-beats-yet`, so a runtime
that had died was indistinguishable from one that never reported. The gate's
producers stage (`--producers`) drives every producer end to end on a scratch
fleet; this module asserts the same producers one by one, so a producer that stops
working names itself here rather than only in the gate's transcript.

WHAT IS ASSERTED, AND HOW
-------------------------
* the Claude hook writes the RUNNING commit (the fixture repository's own HEAD),
  rate-limits a redundant refresh, honours `--force`, and — the part that matters
  for a hook — exits 1, never 2: in Claude's hook contract exit 2 BLOCKS the
  session, and a liveness stamp must never be able to block the work it reports on;
* the one-line install the hook prints covers all three events and both Claude
  rungs, and the runbook quotes the command that prints it (so the documented
  install cannot drift from the script);
* the loop's own producer posts both DeepSeek rungs, and the run beater refreshes
  the executor's beat while a child runs (a 40-minute run must not look dead);
* the hermes and paperclip live-call producers beat when the call SUCCEEDED, and
  the paperclip one writes nothing when its read is unhealthy — the record means
  "this runtime is alive", so an unhealthy read must let the old beat age out.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

import beats
import terminal

REPO_ROOT = Path(__file__).resolve().parents[2]
# The producers span packages (`fleet/`, `integrations/`): the repo root goes on the
# path here so this module does not depend on how the suite was invoked.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
HOOK = REPO_ROOT / "fleet" / "hooks" / "claude-beat.sh"
CONTRACT = REPO_ROOT / "fleet" / "runtimes.yaml"


def _git(fx: Path, *args: str) -> str:
    """Run git in the fixture with an identity that is OURS, never the lane's.

    A lane's exported `GIT_AUTHOR_*`/`GIT_COMMITTER_*` pair must not decide what a
    fixture commit is (that class of leak was measured in #1404), and the dates are
    fixed so two runs produce the same commit.
    """
    proc = subprocess.run(
        ["git", "-C", str(fx), *args],
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin",
            "GIT_AUTHOR_NAME": "producer-fixture",
            "GIT_AUTHOR_EMAIL": "producer@example.invalid",
            "GIT_COMMITTER_NAME": "producer-fixture",
            "GIT_COMMITTER_EMAIL": "producer@example.invalid",
            "GIT_AUTHOR_DATE": "2026-09-19T00:00:00Z",
            "GIT_COMMITTER_DATE": "2026-09-19T00:00:00Z",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
        },
    )
    assert proc.returncode == 0, f"git {' '.join(args)} failed: {proc.stderr}"
    return proc.stdout.strip()


@pytest.fixture()
def fleet_tree(tmp_path: Path) -> Path:
    """A scratch fleet: the real contract, in a real repository."""
    fx = tmp_path / "fleet-tree"
    (fx / "fleet").mkdir(parents=True)
    (fx / "fleet" / "runtimes.yaml").write_text(CONTRACT.read_text(encoding="utf-8"), encoding="utf-8")
    _git(fx, "init", "-q")
    _git(fx, "add", "-A")
    _git(fx, "commit", "-q", "-m", "fixture")
    return fx


def _beat(fx: Path, runtime_id: str) -> dict:
    path = fx / ".fleet" / "runtime-beats" / f"{runtime_id}.json"
    assert path.is_file(), f"no beat was written for {runtime_id}"
    return json.loads(path.read_text(encoding="utf-8"))


def _hook(fx: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(HOOK), "--root", str(fx), "--cwd", str(fx), *args],
        capture_output=True,
        text=True,
    )


# -- the Claude hook ----------------------------------------------------------


def test_the_hook_writes_the_running_commit(fleet_tree: Path) -> None:
    head = _git(fleet_tree, "rev-parse", "HEAD")
    proc = _hook(fleet_tree)
    assert proc.returncode == 0, proc.stderr
    record = _beat(fleet_tree, "claude-session")
    assert record["commit"] == head, "the beat must name the commit the session is RUNNING"
    assert record["state"] == "running"


def test_the_hook_rate_limits_and_force_bypasses_it(fleet_tree: Path) -> None:
    assert _hook(fleet_tree).returncode == 0
    first = _beat(fleet_tree, "claude-session")["ts"]
    second = _hook(fleet_tree, "--verbose")
    assert second.returncode == 0
    assert "is fresh" in second.stdout, "a skip is a decision and must say so"
    assert _beat(fleet_tree, "claude-session")["ts"] == first, "the beat was rewritten inside the interval"
    assert _hook(fleet_tree, "--force", "--verbose").returncode == 0
    assert _beat(fleet_tree, "claude-session")["ts"] >= first


def test_the_hook_never_blocks_the_session(fleet_tree: Path) -> None:
    """Exit 2 would BLOCK a Claude session: a refusal must be 1, and named."""
    proc = _hook(fleet_tree, "--runtime", "ghost")
    assert proc.returncode == 1, f"wanted a non-blocking refusal, got {proc.returncode}"
    assert "REFUSED" in proc.stderr and "runtime-unregistered:ghost" in proc.stderr


def test_the_subagent_rung_is_the_same_hook_with_its_own_id(fleet_tree: Path) -> None:
    assert _hook(fleet_tree, "--runtime", "claude-subagent").returncode == 0
    record = _beat(fleet_tree, "claude-subagent")
    assert record["runtime"] == "claude-subagent"
    assert not (fleet_tree / ".fleet" / "runtime-beats" / "claude-session.json").exists()


def test_the_one_line_install_covers_every_event_and_both_rungs() -> None:
    proc = subprocess.run(["bash", str(HOOK), "--print-install"], capture_output=True, text=True)
    assert proc.returncode == 0
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    assert len(lines) == 1, "the install is ONE line: a paste, never a hand-edit"
    hooks = json.loads(lines[0])
    assert set(hooks) == {"SessionStart", "PostToolUse", "SubagentStop"}
    commands = [
        entry["command"]
        for event in hooks.values()
        for group in event
        for entry in group["hooks"]
    ]
    assert any("--runtime claude-subagent" in command for command in commands)
    assert sum(1 for command in commands if "--runtime claude-subagent" not in command) == 2
    assert all(str(HOOK) in command for command in commands), "each event invokes THIS script"


def test_the_runbook_quotes_the_install_command() -> None:
    """The documented install is the command that PRINTS the line, pinned here."""
    readme = (REPO_ROOT / "fleet" / "README.md").read_text(encoding="utf-8")
    assert "fleet/hooks/claude-beat.sh --print-install" in readme, (
        "fleet/README.md must document the one-line install by the command that prints it"
    )
    assert "hooks" in readme, "the runbook must say where the line goes (~/.claude/settings.json)"


# -- the loop's producers -----------------------------------------------------


def test_the_loop_posts_both_deepseek_rungs(fleet_tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(terminal, "ROOT", fleet_tree)
    monkeypatch.setattr(beats, "ROOT", fleet_tree)
    for runtime_id in (terminal.SISTER_RUNTIME_ID, terminal.EXECUTOR_RUNTIME_ID):
        terminal.beat_runtime(runtime_id)
        record = _beat(fleet_tree, runtime_id)
        assert record["commit"] == _git(fleet_tree, "rev-parse", "HEAD")
        assert record["state"] == "running"


def test_the_run_beater_keeps_the_executor_beat_fresh(fleet_tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A long run must not look like a dead runtime (#1412)."""
    monkeypatch.setattr(terminal, "ROOT", fleet_tree)
    monkeypatch.setattr(beats, "ROOT", fleet_tree)
    beater = terminal.RunBeater("directive-fixture", 0.05, {})
    first = beats.post(terminal.EXECUTOR_RUNTIME_ID, root=fleet_tree, cwd=fleet_tree, now=1000.0)
    beater.start()
    try:
        deadline = time.time() + 5.0
        while time.time() < deadline:
            if _beat(fleet_tree, terminal.EXECUTOR_RUNTIME_ID)["ts"] > first.record["ts"]:
                break
            time.sleep(0.05)
    finally:
        beater.stop()
    assert _beat(fleet_tree, terminal.EXECUTOR_RUNTIME_ID)["ts"] > first.record["ts"], (
        "the run beater never refreshed the executor's runtime beat"
    )


# -- the adapters' live calls -------------------------------------------------


def test_the_hermes_live_call_beats(fleet_tree: Path) -> None:
    from integrations.hermes import cli as hermes_cli

    hermes_cli._beat_live_call(fleet_tree)
    record = _beat(fleet_tree, "hermes")
    assert record["runtime"] == "hermes"


def test_the_paperclip_live_read_beats_only_when_the_surface_answered(fleet_tree: Path) -> None:
    from integrations.paperclip.api import cli as api_cli
    from integrations.paperclip.api import health as health_mod

    api_cli._beat_live_read(
        fleet_tree,
        health_mod.HealthReport(status=health_mod.STATUS_OK, http_status=200, dependencies=()),
    )
    assert _beat(fleet_tree, "paperclip")["state"] == "running"

    # An unhealthy read is NOT liveness: the old beat must age out instead.
    stale = fleet_tree / ".fleet" / "runtime-beats" / "paperclip.json"
    before = stale.read_text(encoding="utf-8")
    api_cli._beat_live_read(
        fleet_tree,
        health_mod.HealthReport(
            status=health_mod.STATUS_UNHEALTHY, http_status=503, dependencies=()
        ),
    )
    assert stale.read_text(encoding="utf-8") == before, (
        "an unhealthy read refreshed the beat, which claims a liveness the surface cannot honour"
    )


# -- the producers and the judge, together ------------------------------------


def test_seven_fresh_beats_are_judged_live_and_a_quiet_runtime_is_named(
    fleet_tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pair the issue's acceptance asks for, in one test the gate can also run."""
    from fleet import runtime_liveness, runtimes

    monkeypatch.setattr(terminal, "ROOT", fleet_tree)
    monkeypatch.setattr(beats, "ROOT", fleet_tree)
    ids = runtimes.ids(fleet_tree)
    assert len(ids) == 7, "the contract registers seven runtimes"
    for runtime_id in ids:
        assert beats.post(runtime_id, root=fleet_tree, cwd=fleet_tree).wrote

    findings, reason = runtime_liveness.gather_and_judge(fleet_tree)
    assert reason is None
    assert findings == [], f"seven fresh beats must be judged live: {[f.name for f in findings]}"

    quiet = fleet_tree / ".fleet" / "runtime-beats" / "deepseek-sister.json"
    record = json.loads(quiet.read_text(encoding="utf-8"))
    record["ts"] = time.time() - 7200.0
    quiet.write_text(json.dumps(record), encoding="utf-8")

    findings, reason = runtime_liveness.gather_and_judge(fleet_tree)
    assert reason is None
    assert [finding.name for finding in findings] == ["runtime-stale:deepseek-sister"], (
        "the control must name the runtime that went quiet, and ONLY it"
    )
