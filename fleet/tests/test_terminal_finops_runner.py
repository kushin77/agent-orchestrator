"""The FinOps block selects the runner, and a block it cannot execute REFUSES (#578).

Issue #218 replaced the decorative FinOps block with real behaviour: the brain's
``model.tier`` selects the model the runner is invoked with, the tier/model/thinking
are exported to the child environment, and a block this build cannot turn into a
runner refuses the dispatch (``RC_REFUSED``) instead of silently falling back to the
default runner. #218 landed green with nothing pinning any of it, so a future
refactor could revert the whole mechanism with every gate still green.

The shape here is the house style set by #286
(``test_isolation_guard.py::test_loop_wires_record_run_into_the_run_path``): drive the
REAL run path — ``terminal.loop`` for a whole cycle and ``terminal.run_once`` where the
run path is the subject — with every out-of-process seam stubbed, and assert on the
EFFECT (the argv and the environment the run path handed the child). ``run_once``,
``resolve_dispatch`` and the loop's dispatch are deliberately NOT stubbed: what they
hand the child IS the behaviour under test, so stubbing them would make the test assert
its own stub. A source-text grep would be green while the characters are present and the
call is dead, which is exactly the defect #286 removed.
"""

from __future__ import annotations

import argparse
import json
import signal

import terminal

DIRECTIVE_ID = "d-finops-578"
DIRECTIVE_ISSUE = 578
LANE = "fleet"
BASE_RUNNER = "claude -p"

#: The tier this suite declares, and the two models that must not be confused.
PRO_MODEL = "deepseek-v4-pro"
FLASH_MODEL = "deepseek-v4-flash"


class _Completed:
    """A ``subprocess.run`` result the loop can read."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _Beater:
    """Stands in for the run's heartbeat thread; ``stop`` is all the loop needs."""

    def stop(self) -> None:
        pass


class _FakeChild:
    """A ``subprocess.Popen`` stand-in: a real child would run a real model."""

    def __init__(self, command: list[str], env: dict[str, str]) -> None:
        self.command = list(command)
        self.env = dict(env)
        self.pid = 4242
        self.returncode = 0

    def communicate(self, timeout: float | None = None) -> tuple[str, None]:
        return "runner finished", None

    def poll(self) -> int:
        return 0


def _directive(tier: str, thinking: str) -> dict:
    """A directive carrying the FinOps block the brain chose for the lane."""
    return {
        "id": DIRECTIVE_ID,
        "ts": "2026-09-14T00:00:00Z",
        "type": "directive",
        "from": "brain",
        "to": "sister",
        "correlation_id": "c-finops-578",
        "model": {"tier": tier, "thinking": thinking},
        "task": {"kind": "work", "issue": DIRECTIVE_ISSUE, "lane": LANE},
        "body": "implement #578",
    }


class _Run:
    """What one driven ``terminal.loop`` cycle did, and what it handed a child."""

    def __init__(self) -> None:
        self.returned: int | None = None
        self.calls: list[list[str]] = []
        self.spawned: list[list[str]] = []
        self.envs: list[dict[str, str]] = []
        self.claimed: list[int] = []

    @property
    def escalations(self) -> list[list[str]]:
        return [command for command in self.calls if "escalate" in command]


def _stub_loop(monkeypatch, directive: dict, *, runner: str = BASE_RUNNER) -> _Run:
    """Drive ``terminal.loop --once`` once; report what it did and what it spawned.

    Every seam the loop reaches outside its own process is stubbed — the subagent
    runner, the lane provisioner, the claim ledger, the gates, the close-out and the
    board — while the FinOps resolution and the run path stay real. Nothing is caught,
    so an exception raised inside the loop fails the test rather than being absorbed.
    """
    run = _Run()

    def fake_run(command, **kwargs):  # noqa: ARG001 - the loop passes cwd/text
        run.calls.append(list(command))
        if command[:1] == ["git"]:
            return _Completed(stdout="abc1234\n")
        if "watch" in command:
            return _Completed(stdout=json.dumps(directive))
        if "held" in command:
            return _Completed(returncode=1, stdout="{}")
        return _Completed()

    def fake_popen(command, **kwargs):
        # The FinOps environment is exported LAST, so capture the merged mapping.
        env = dict(kwargs.get("env") or {})
        run.spawned.append(list(command))
        run.envs.append(env)
        return _FakeChild(list(command), env)

    def fake_claim(issue, agent_id, lane, directive_id):  # noqa: ARG001
        run.claimed.append(issue)
        return True, "claimed"

    monkeypatch.setattr(terminal.singleton, "guard", lambda *a, **k: True)
    monkeypatch.setattr(terminal.subprocess, "run", fake_run)
    monkeypatch.setattr(terminal.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(terminal, "write_heartbeat", lambda *a, **k: None)
    monkeypatch.setattr(terminal, "start_beating", lambda *a, **k: _Beater())
    monkeypatch.setattr(terminal, "claim_issue", fake_claim)
    monkeypatch.setattr(terminal, "provision_worktree", lambda *a, **k: None)
    monkeypatch.setattr(terminal, "release_in_flight", lambda *a, **k: None)
    monkeypatch.setattr(terminal, "gate_evidence", lambda *a, **k: (True, "`make verify` rc=0"))
    monkeypatch.setattr(
        terminal, "landed_evidence", lambda *a, **k: (True, f"#{DIRECTIVE_ISSUE} is closed")
    )
    monkeypatch.setattr(terminal, "closeout_issue", lambda *a, **k: "OK")

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
        run.returned = terminal.loop(args)
    finally:
        for sig, handler in saved.items():
            signal.signal(sig, handler)
    return run


def _unmap_tier(monkeypatch, tier: str = "pro") -> None:
    """A tier this build's vocabulary knows but its runner map cannot execute.

    The map is patched rather than the policy: the refusal under test is "the
    vocabulary declares it, no runner is mapped to it", which is what a tier the
    policy adds and this map forgets looks like at runtime.
    """
    runner_map = dict(terminal.TIER_RUNNERS)
    assert tier in runner_map, "this fixture assumes the tier starts out mapped"
    runner_map.pop(tier)
    monkeypatch.setattr(terminal, "TIER_RUNNERS", runner_map)


# --------------------------------------------------------------------------
# 1. the declared tier selects the model the runner is invoked with
# --------------------------------------------------------------------------


def test_a_declared_tier_selects_the_model_the_runner_is_invoked_with(monkeypatch):
    """`model.tier: pro` invokes the runner with the PRO model — measured at the child.

    The defect #218 removed: the FinOps block was printed into the prompt and the
    runner was invoked with no model at all, so a `pro` floor bought the lane the same
    default seat as everything else. Asserted on the real argv, so dropping the tier
    from the invocation fails by this name.
    """
    run = _stub_loop(monkeypatch, _directive("pro", "low"))

    assert run.returned == 0, "the loop did not complete its cycle"
    assert len(run.spawned) == 1, f"expected exactly one runner child, got {run.spawned}"
    command = run.spawned[0]
    assert command[:4] == ["claude", "-p", "--model", PRO_MODEL], (
        f"the runner was not invoked with the tier's model: {command!r}"
    )
    assert command[-1].startswith("You are an epic-focused subagent"), (
        "the prompt must remain the runner's final argument"
    )
    assert FLASH_MODEL not in command, (
        "a directive that declared `pro` was dispatched at the default tier's model"
    )


# --------------------------------------------------------------------------
# 2. the tier, the model and the thinking reach the child environment
# --------------------------------------------------------------------------


def test_the_tier_the_model_and_the_thinking_reach_the_child_environment(monkeypatch):
    """The FinOps values are exported to the child, which is what a BYOK wrapper reads.

    The environment is the second half of #218: a wrapper that honours the tier needs
    the tier, the model and the thinking effort, and `ANTHROPIC_MODEL` is
    kushin77/deepseek's own BYOK variable rather than fleet-specific glue.
    """
    run = _stub_loop(monkeypatch, _directive("pro", "medium"))

    assert len(run.envs) == 1, "no child was spawned, so there is no environment to assert"
    env = run.envs[0]
    assert env["AO_TIER"] == "pro", f"AO_TIER did not reach the child: {env.get('AO_TIER')!r}"
    assert env["AO_THINKING"] == "medium"
    assert env["AO_MODEL"] == PRO_MODEL
    assert env["ANTHROPIC_MODEL"] == PRO_MODEL, "the BYOK variable did not reach the child"
    assert env["AO_RUNNER"] == f"{BASE_RUNNER} --model {PRO_MODEL}"
    assert env["AO_RISK"] == "normal", "a plain lane must not be reported as risk-bearing"
    assert env.get("AO_TIER") != "flash", "the default tier leaked into the child environment"


# --------------------------------------------------------------------------
# 3. an unexecutable block REFUSES — rc RC_REFUSED, and nothing is spawned
# --------------------------------------------------------------------------


def test_a_tier_with_no_runner_mapped_refuses_with_rc_refused(monkeypatch):
    """A block this build cannot turn into a runner returns RC_REFUSED, not a fallback.

    Exercised on ``run_once`` because that is where the run path resolves a block for
    itself and where ``RC_REFUSED`` is returned. The silent-fallback regression this
    pins is "no runner is mapped, so dispatch at the default one" — the posture
    kushin77/deepseek #54 forbids and #218 removed.
    """
    _unmap_tier(monkeypatch)
    spawned: list[list[str]] = []

    def tripwire(command, **kwargs):  # noqa: ARG001
        spawned.append(list(command))
        return _FakeChild(list(command), dict(kwargs.get("env") or {}))

    monkeypatch.setattr(terminal.subprocess, "Popen", tripwire)

    rc, output = terminal.run_once(
        _directive("pro", "low"), BASE_RUNNER, 1.0, False, "subagent-abc"
    )

    assert rc == terminal.RC_REFUSED, f"expected a refusal, got rc={rc}: {output}"
    assert terminal.RC_REFUSED == 78, "RC_REFUSED is EX_CONFIG (78) and must not drift"
    assert spawned == [], f"a refused dispatch spawned a runner: {spawned}"
    assert "tier-unmapped" in output, f"the refusal does not name its code: {output}"
    assert "'pro'" in output, f"the refusal does not name the tier it could not execute: {output}"
    assert PRO_MODEL not in output and FLASH_MODEL not in output, (
        "a refusal must read as a refusal, never as a runner that was chosen anyway"
    )


def test_the_loop_refuses_an_unexecutable_block_before_it_claims_or_runs_anything(monkeypatch):
    """The refusal is decided before the claim, so it costs a lane nothing.

    The loop resolves the FinOps block BEFORE it checks the ledger and takes the
    claim. A block it cannot execute must therefore leave no claim, no lane and no
    child behind — and the directive must stay pending, because consuming it would
    erase the only record that the work was ordered.
    """
    _unmap_tier(monkeypatch)

    run = _stub_loop(monkeypatch, _directive("pro", "low"))

    assert run.returned == 1, "the loop must report the refusal instead of a finished cycle"
    assert run.spawned == [], f"a refused directive still reached a runner: {run.spawned}"
    assert run.claimed == [], "the loop took the claim for a directive it then refused"
    assert not any("held" in command for command in run.calls), (
        "the loop reached the dispatch stage (the ledger check) for a block it cannot execute"
    )
    assert run.escalations, "the refusal was never escalated to the brain"
    assert "tier-unmapped" in " ".join(run.escalations[-1]), (
        f"the escalation does not name the refusal: {run.escalations[-1]}"
    )
