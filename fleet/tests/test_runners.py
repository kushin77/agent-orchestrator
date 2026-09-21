"""The runner capability contract: a dispatch is refused when the quadruple disagrees (#841).

Measured 2026-09-15 on ``master`` @ ``dd8cbfc``: **every subagent run the fleet
dispatched died about ten seconds after it started**, because the loop asked
whether its runner *resolved* and never whether that runner could honour the
*model*:

    [preflight] runner resolved: /home/akushnir/.local/bin/claude      # the only question asked
    [subagent]  [claude-code:unrecognized_model] {"model":"deepseek-v4-flash","query_source":"sdk"}
    [sister]    run finished: rc=1 status=failed
    .fleet/runs/*.log: 192 model-rejection lines, 40 status=failed, 0 status=ok

`claude` was on PATH, `deepseek-v4-flash` was the declared model, and the BYOK
environment that reconciles the two was unset. Three of the four parts of a runner
were individually fine and the dispatch still could not work.

What is pinned here:

* the profile is inferred from the **executable**, never from an argument;
* a runner whose declared environment is absent is refused **by name**, naming the
  variables, the model vocabulary and the remedy;
* the **two-sided** control — the same runner, the same tier, the environment
  flipped — so a check that cannot fail is caught by this file and not by luck;
* the model switch is the **profile's** (``-m`` for the native CLI, ``--model`` for
  the BYOK one), so the vocabulary and the argv shape cannot drift apart again;
* the loop **holds the queue and escalates once** instead of dispatching a
  directive that will die — the AO-GR-26 shape, reused rather than re-invented.

The loop is driven with every out-of-process seam stubbed, in the house style of
``test_runner_preflight.py``.
"""

from __future__ import annotations

import argparse
import signal

import pytest

import runners
import terminal

#: A runner this build can identify, so the capability question is the one under test.
KNOWN_RUNNER = "claude -p"


class _Completed:
    """A ``subprocess.run`` result the loop can read."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# ---------------------------------------------------------------------------
# 1. the profile: which contract governs this command line
# ---------------------------------------------------------------------------


def test_the_profile_is_inferred_from_the_executable_not_from_an_argument():
    """``claude -p`` and a resolved ``/usr/bin/claude`` are the same profile."""
    assert runners.profile_for("claude -p").id == "claude-byok"
    assert runners.profile_for("/usr/bin/claude").id == "claude-byok"
    assert runners.profile_for("deepseek").id == "deepseek-native"
    assert runners.profile_for("deepseek -m pro") is None or (
        runners.profile_for("deepseek -m pro").id == "deepseek-native"
    ), "the executable is argv[0]; its own flags are the runner's business"


def test_an_argument_that_merely_mentions_a_known_runner_is_not_one():
    """Inference matches argv[0]'s basename, so a wrapper is never guessed at."""
    assert runners.profile_for("my-wrapper --agent claude") is None
    assert runners.profile_for("") is None


def test_an_explicit_profile_wins_over_inference():
    """A wrapper's filename cannot identify it — the operator's declaration does."""
    assert runners.profile_for("true", profile_id="claude-byok").id == "claude-byok"
    assert runners.profile_for("claude", profile_id="deepseek-native").id == "deepseek-native"


def test_an_unidentifiable_runner_is_refused_and_told_how_to_name_itself():
    """Fail closed: an executable nothing can identify claims nothing."""
    problem = runners.unhonourable("true", env={})

    assert problem, "an unknown runner must be refused"
    assert runners.PROFILE_ENV in problem, "the remedy must name the variable that fixes it"
    assert "claude-byok" in problem and "deepseek-native" in problem, (
        "the refusal must say which profiles this build does know"
    )


# ---------------------------------------------------------------------------
# 2. the measured failure, and its two-sided control
# ---------------------------------------------------------------------------


def test_the_measured_failure_is_now_refused_by_name():
    """`claude` + the DeepSeek vocabulary + no BYOK environment = the 192 deaths.

    The refusal must be actionable, because the failure it replaces was not: the
    reason sat in a run log while the health surface said the fleet was healthy.
    """
    problem = runners.unhonourable(KNOWN_RUNNER, env={})

    assert problem, "this pairing cannot work and must be refused"
    assert "ANTHROPIC_BASE_URL" in problem, "the missing variable must be named"
    assert "ANTHROPIC_AUTH_TOKEN" in problem, "every missing variable must be named"
    assert "deepseek-v4-flash" in problem, "the vocabulary the runner cannot honour must be named"
    assert "claude-byok" in problem, "the profile must be named"


def test_the_same_runner_is_allowed_once_its_declared_environment_is_present():
    """The control for the test above: flip the environment, not the runner.

    Without this, a check that refuses unconditionally would pass the test above and
    prove nothing — the defect this whole change is about.
    """
    satisfied = {"ANTHROPIC_BASE_URL": "x", "ANTHROPIC_AUTH_TOKEN": "y"}

    assert runners.unhonourable(KNOWN_RUNNER, env=satisfied) == "", (
        "the same runner with the declared environment present must be allowed"
    )
    assert runners.unhonourable(KNOWN_RUNNER, env={}) != "", "and refused without it"


def test_an_empty_value_is_not_a_satisfied_declaration():
    """A variable set to the empty string is not wired, and must not read as present."""
    blank = {"ANTHROPIC_BASE_URL": "", "ANTHROPIC_AUTH_TOKEN": "   "}

    assert runners.unhonourable(KNOWN_RUNNER, env=blank) != "", (
        "an empty value is the unset case wearing a set variable"
    )


def test_the_native_profile_needs_no_environment_and_spells_the_switch_differently():
    """The fourth part of the quadruple: argv shape is per-runner, not per-fleet."""
    assert runners.unhonourable("deepseek", env={}) == "", (
        "the native CLI carries its own credential, so an empty environment is enough"
    )
    assert runners.DEEPSEEK_NATIVE.model_flag == "-m"
    assert runners.CLAUDE_BYOK.model_flag == "--model"
    assert runners.DEEPSEEK_NATIVE.model_for("flash") == runners.CLAUDE_BYOK.model_for("flash"), (
        "the vocabulary is DeepSeek's in both cases; only the runner differs"
    )


def test_a_tier_outside_the_profile_is_refused_by_tier():
    """The tier refusal that existed before is kept, and now lives beside the new one."""
    satisfied = {"ANTHROPIC_BASE_URL": "x", "ANTHROPIC_AUTH_TOKEN": "y"}

    problem = runners.capability_gap(runners.CLAUDE_BYOK, "quantum", env=satisfied)

    assert "quantum" in problem, "the unmapped tier must be named"
    assert "flash" in problem, "the mapped tiers must be listed so the fix is obvious"


def test_describe_names_all_four_parts_of_the_quadruple():
    """The operator line: binary, switch, model, and what is missing."""
    line = runners.describe(
        runners.CLAUDE_BYOK, "flash",
        env={"ANTHROPIC_BASE_URL": "x", "ANTHROPIC_AUTH_TOKEN": "y"},
    )

    assert "claude" in line and "--model" in line and "deepseek-v4-flash" in line
    assert "missing: none" in line, "a satisfied profile says so rather than omitting the field"


# ---------------------------------------------------------------------------
# 3. the argv: the profile's switch, not a second hard-coded flag
# ---------------------------------------------------------------------------


def test_the_argv_uses_the_profiles_model_switch():
    """`deepseek` takes `-m`; asking it for `--model` is a different runner's argv.

    The DEFAULT is the native DeepSeek CLI since #1787, so the default block is the
    `-m` one. The BYOK profile is then exercised by NAMING it, rather than by relying
    on it being the default — so both halves of the property stay pinned, and a future
    change to the default moves one assertion instead of quietly retiring the other.
    """
    directive = {"task": {"lane": "erp-tx", "title": "ERP-03"}, "model": {"tier": "flash", "thinking": "none"}}

    default, refusal = terminal.resolve_dispatch(dict(directive))
    assert refusal is None, f"the default block must resolve: {refusal}"
    assert " -m deepseek-v4-flash" in default["runner"], (
        f"the native CLI's switch is -m, and the default profile is what says so: {default['runner']}"
    )
    assert "--model" not in default["runner"], "the wrong switch must not survive alongside"

    byok, refusal = terminal.resolve_dispatch(dict(directive), base_runner="claude -p")
    assert refusal is None, f"the BYOK profile's block must resolve too: {refusal}"
    assert " --model deepseek-v4-flash" in byok["runner"], (
        f"the BYOK profile's switch is --model: {byok['runner']}"
    )


def test_the_model_variable_is_exported_only_to_a_profile_that_reads_it():
    """#1787: `reads_model_env` decides, instead of being declared and ignored.

    The field exists to say "this runner does NOT read its model from the
    environment", and nothing consulted it — so the native DeepSeek CLI was handed
    an Anthropic-named variable it never looks at (the same declared-but-unenforced
    shape this repository keeps finding). Both sides are pinned: the native profile
    must not receive one, and the BYOK profile, which declares it reads it, must.
    """
    directive = {"task": {"lane": "erp-tx", "title": "ERP-03"}, "model": {"tier": "flash", "thinking": "none"}}

    native, refusal = terminal.resolve_dispatch(dict(directive))
    assert refusal is None, f"the default block must resolve: {refusal}"
    assert "ANTHROPIC_MODEL" not in native["env"], (
        "a profile declaring reads_model_env=False must not be handed a model "
        f"variable it never reads: {native['env']}"
    )
    assert native["env"]["AO_MODEL"] == "deepseek-v4-flash", (
        "the tier's model must still reach the child by the fleet's own variable"
    )

    byok, refusal = terminal.resolve_dispatch(dict(directive), base_runner="claude -p")
    assert refusal is None, f"the BYOK profile's block must resolve too: {refusal}"
    assert byok["env"].get("ANTHROPIC_MODEL") == "deepseek-v4-flash", (
        "the BYOK profile declares it reads the model from the environment, so it "
        f"must receive one: {byok['env']}"
    )


# ---------------------------------------------------------------------------
# 4. the loop: hold the queue, escalate once, read no inbox
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


@pytest.fixture
def no_byok(monkeypatch):
    """The measured environment: the runner resolves and nothing wires the model."""
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)


def test_the_loop_holds_the_queue_and_escalates_once_when_the_model_cannot_be_honoured(
    monkeypatch, no_byok
):
    """The defect, inverted: no directive is dispatched at a runner that cannot run it."""
    calls: list[list[str]] = []

    rc = _drive_loop(monkeypatch, calls, KNOWN_RUNNER)

    assert rc == 1, "the loop must report that it cannot dispatch"
    assert terminal.paused() is True, "the queue must be held, not drained into a failing runner"
    escalations = [command for command in calls if "escalate" in command]
    assert len(escalations) == 1, f"exactly one escalation, got {len(escalations)}: {escalations}"
    body = " ".join(escalations[0])
    assert terminal.RUNNER_CAPABILITY_ID in body, "the escalation must be keyed as a capability refusal"
    assert "ANTHROPIC_BASE_URL" in body, "it must name the variable to set"
    assert "queue is held" in body, "it must say what the loop did about it"
    assert not any("watch" in command for command in calls), (
        "a directive this runner cannot honour must not even be read"
    )


def test_a_second_cycle_does_not_escalate_the_capability_refusal_again(monkeypatch, no_byok):
    """One escalation per condition, never one per cycle — the runaway shape."""
    calls: list[list[str]] = []

    _drive_loop(monkeypatch, calls, KNOWN_RUNNER)
    _drive_loop(monkeypatch, calls, KNOWN_RUNNER)

    escalations = [command for command in calls if "escalate" in command]
    assert len(escalations) == 1, f"two cycles escalated {len(escalations)} times: {escalations}"


def test_a_wired_runner_leaves_the_loop_free_to_work(monkeypatch):
    """The positive control for the loop: with the environment present, nothing is held."""
    calls: list[list[str]] = []

    _drive_loop(monkeypatch, calls, KNOWN_RUNNER)

    assert terminal.paused() is False, "a runner that can honour the dispatch must not hold the queue"
    assert not [command for command in calls if "escalate" in command], (
        "and there is nothing to escalate"
    )


def test_the_hold_does_not_flap_when_the_runner_resolves_but_cannot_honour(
    monkeypatch, no_byok, capsys
):
    """The release predicate is the CONJUNCTION, not resolvability alone (#845).

    Measured live, minutes after #841 merged: the preflight released the hold on
    *resolvability* while the capability check took it again in the same cycle, so
    ``.fleet/sister.log`` alternated forever between

        RUNNER CANNOT HONOUR A DISPATCH — … — queue held, no directive dispatched
        runner resolvable again — queue hold released

    The second line asserts the opposite of the truth — the runner still cannot honour a
    dispatch — and it was printed every cycle. The work stayed held (the capability hold is
    taken after the release, and the cycle checks `paused()` afterwards), so what this pins is
    the SIGNAL: one predicate for the hold and its release, or the surface lies.
    """
    calls: list[list[str]] = []

    _drive_loop(monkeypatch, calls, KNOWN_RUNNER)
    _drive_loop(monkeypatch, calls, KNOWN_RUNNER)

    out = capsys.readouterr().out
    assert "queue hold released" not in out, (
        "the hold was released while the runner still cannot honour a dispatch — "
        f"the two sites are fighting again:\n{out}"
    )
    assert terminal.paused() is True, (
        "the queue must STAY held across cycles when the runner cannot honour a dispatch"
    )
    escalations = [command for command in calls if "escalate" in command]
    assert len(escalations) == 1, (
        f"holding across cycles must not re-escalate: got {len(escalations)}"
    )


def test_every_profile_credential_is_an_iac_declared_secret() -> None:
    """#1784: the credential a profile needs is a declared IaC secret.

    The operator's directive — "all creds need to be iac secrets" — is made
    mechanical here rather than left as prose in a docstring: a profile names the
    mount in ``infra/fleet/secrets_contract.py`` that PROVISIONS its credential,
    and that name must resolve. A profile that needs a credential and names
    nothing, or names a mount that does not exist, is refused.

    This is the cross-file half of the contract: the runner half declares which
    secret backs it, and the IaC half declares where that secret comes from. Two
    files agreeing is the whole of the claim, so it is checked rather than
    assumed.
    """
    import importlib
    import pathlib
    import sys

    repo_root = pathlib.Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo_root / "infra" / "fleet"))
    try:
        secrets_contract = importlib.import_module("secrets_contract")
    finally:
        sys.path.pop(0)

    for profile in runners.PROFILES.values():
        assert profile.iac_secret, (
            f"{profile.id} declares no IaC secret — its credential would have to be "
            "exported by hand, which is the defect (issue #1784)"
        )
        assert profile.iac_secret in secrets_contract.BY_NAME, (
            f"{profile.id}: {profile.iac_secret!r} is not declared in "
            "infra/fleet/secrets_contract.py — a profile may only name a mount that "
            "exists, or its remedy points at nothing"
        )

    # The arm must be able to fail: a name that is genuinely absent is absent, so
    # the assertion above is not vacuously true for every string.
    bogus = runners.RunnerProfile(
        id="bogus",
        executable="bogus",
        model_flag="-m",
        models={},
        iac_secret="not-a-declared-mount",
    )
    assert bogus.iac_secret not in secrets_contract.BY_NAME, (
        "the membership arm is vacuous unless an undeclared name is genuinely absent"
    )
