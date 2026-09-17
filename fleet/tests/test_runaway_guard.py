"""The fleet runaway guard, asserted by its own refusals (issue #723).

The defect this suite exists for
-------------------------------
``fleet/terminal.py`` had five paths that left a directive pending without ever
reporting a result — a refused claim, a run that did not land, the self-heal
re-dispatch, an in-flight hold by another loop, and an untracked foreign claim.
None carried an attempt counter, a delay or a terminal state, so an order the
fleet could never execute was re-read by every loop cycle for ever, and
``report_once`` hid it by deduping the *report* while the *attempt* repeated.

What is asserted here
---------------------
* the count is **persisted** — proved across two OS processes, which is what
  "survives a loop restart" means, not by re-reading a dict in memory;
* the backoff follows the harvested contract (``min(base * 2**(n-1), 300)`` from
  ``vendor/CMR/ops/retry.sh``) rather than a locally invented curve;
* a refused claim, a crashed run and a self-heal all increment the **same**
  counter, and a self-heal is an attempt rather than a fresh start;
* K is configurable, its default is declared, and a typo is REFUSED rather than
  silently disabling the bound;
* the NEGATIVE CONTROL the issue asks for: a directive that always fails is
  dead-lettered — moved out of the inbox — and ``channel watch`` never returns it
  again, **even when its envelope is deliberately re-planted in the inbox**, so
  the assertion rests on the guard's filter and not merely on the file move;
* a directive inside its backoff is HELD by ``watch`` (not dispatched early, and
  not slept on) while a healthy directive is still dispatched immediately — the
  two halves that keep those assertions from passing vacuously;
* the assertions above CAN FAIL: scratch copies of the module with the cap
  disabled and with the backoff flattened are driven through the same scenario
  and must reproduce the runaway (a gate that cannot fail is a formality, GR-12);
* the guard writes nothing outside the root it was given.

Isolation: every test points ``terminal.RUNS`` and ``channel.INBOX`` at a tmp
directory, and the guard derives its root from ``RUNS``/``INBOX`` — so no test
touches the live fleet's ``.fleet/`` (the invariant ``test_isolation_guard.py``
polices for the rest of the suite).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import channel
import runaway
import terminal

#: The module under test, as a path: the mutant controls load scratch copies of
#: it, so the tests must not assume the package import is the file on disk.
RUNAWAY_PATH = Path(runaway.__file__).resolve()
FLEET_DIR = RUNAWAY_PATH.parent
REPO_ROOT = FLEET_DIR.parent

#: A record always wins over a stale ``.pyc``: a mutant run that imported a
#: cached module would report the ORIGINAL behaviour and look like a guard that
#: "caught" nothing (measured elsewhere in this repo, on a mutation proof).
sys.dont_write_bytecode = True


# --- helpers -----------------------------------------------------------------


def wire_scratch(tmp_path: Path, monkeypatch, *, cap: int | None = None) -> Path:
    """Point the loop's guard and the mailbox at ONE scratch fleet directory.

    ``terminal.RUNS`` and ``channel.INBOX`` are the two paths the guard derives
    its root from (``RUNS.parent`` beside the run markers, ``INBOX.parent`` beside
    the mailbox), so redirecting them redirects the guard's attempt counters and
    its dead-letter store with them.
    """
    if cap is not None:
        monkeypatch.setenv(runaway.ENV_ATTEMPT_CAP, str(cap))
    monkeypatch.setattr(terminal, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(channel, "INBOX", tmp_path / "inbox")
    monkeypatch.setattr(channel, "DONE", tmp_path / "done")
    monkeypatch.setattr(terminal, "stream_run_event", lambda *a, **k: None)
    return tmp_path


def record_reporting(calls: list[dict]):
    """A ``report_once`` stand-in that records instead of shelling out to channel."""

    def report_once(directive_id, key, message_type, body, severity="warn"):
        calls.append(
            {
                "directive": directive_id,
                "key": key,
                "type": message_type,
                "body": body,
                "severity": severity,
            }
        )
        return True

    return report_once


def plant(directory: Path, directive_id: str, *, ts: str = "2026-09-14T00:00:00Z") -> Path:
    """Put one pending directive envelope in `directory` (the mailbox shape)."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{directive_id}.json"
    path.write_text(
        json.dumps(
            {
                "id": directive_id,
                "ts": ts,
                "type": "directive",
                "from": "brain",
                "to": "sister",
                "correlation_id": f"c-{directive_id}",
                "model": {"tier": "flash", "thinking": "none"},
                "task": {"kind": "work", "issue": 723, "lane": "fleet"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def watch(capsys, *, skip: list[str] | None = None) -> tuple[int, str]:
    """Run the real ``channel watch`` once; return (exit code, stdout)."""
    rc = channel.cmd_watch(
        argparse.Namespace(timeout_seconds=0.05, interval=0.01, skip=skip or [])
    )
    return rc, capsys.readouterr().out


def run_child(script: str, state: Path) -> str:
    """Run `script` in a SEPARATE interpreter pointed at `state` — a restart.

    ``PYTHONPATH`` points at the fleet package directory and ``AO_FLEET_DIR`` at
    the scratch root, so the child behaves exactly like a restarted loop: it
    imports the module fresh and finds the same runtime directory.
    """
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "AO_FLEET_DIR": str(state),
            "PYTHONPATH": str(FLEET_DIR),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
    )
    assert result.returncode == 0, f"child failed rc={result.returncode}: {result.stderr}"
    return result.stdout.strip()


def load_mutant(tmp_path: Path, old: str, new: str) -> object:
    """Import a mutated copy of the module — the anchor must actually match.

    A mutation harness that reports "the gate caught it" while the mutation never
    applied proves nothing, so the anchor is required to be unique and the
    rewrite is asserted to have changed the file before it is imported.
    """
    source = RUNAWAY_PATH.read_text(encoding="utf-8")
    assert source.count(old) == 1, f"the mutation anchor is not unique: {old!r}"
    mutated = source.replace(old, new)
    assert mutated != source, "the mutation did not change the source"
    target = tmp_path / "mutant" / "runaway.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(mutated, encoding="utf-8")
    for cache in target.parent.rglob("__pycache__"):
        for cached in sorted(cache.glob("runaway*.pyc")):
            cached.unlink()
    # The module must be REGISTERED before it executes: `@dataclass` resolves a
    # class's own annotations through `sys.modules[cls.__module__]`, so an
    # unregistered spec raises instead of importing.
    name = f"runaway_mutant_{abs(hash((old, new)))}"
    spec = importlib.util.spec_from_file_location(name, target)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    assert Path(module.__file__).resolve() == target.resolve(), (
        "the mutant did not import the scratch copy it wrote"
    )
    return module


#: The two mutations the controls use. Both are single, unique lines.
CAP_DISABLED = (
    "return _positive_int(ENV_ATTEMPT_CAP, DEFAULT_ATTEMPT_CAP)",
    "return 10 ** 9",
)
BACKOFF_FLAT = ("return min(delay, BACKOFF_CAP_SECONDS)", "return 0")


# --- 1. the count is persisted, across a real restart ------------------------


def test_attempts_persist_across_a_loop_restart(tmp_path, monkeypatch):
    """AC1: the count survives a loop restart — proved across two OS processes."""
    state = wire_scratch(tmp_path, monkeypatch)
    script = (
        "import json\n"
        "import runaway\n"
        "for reason in ('claim refused', 'run did not land'):\n"
        "    runaway.record_attempt('d-restart', reason)\n"
        "print((runaway.attempts_dir() / 'd-restart.json').read_text())\n"
    )
    first = json.loads(run_child(script, state))
    second = json.loads(run_child(script, state))
    assert first["attempts"] == 2, f"the first loop run did not persist two attempts: {first}"
    assert second["attempts"] == 4, (
        "a restarted loop re-started the budget instead of continuing it: "
        f"{first['attempts']} then {second['attempts']}"
    )
    assert second["reasons"] == ["claim refused", "run did not land"] * 2, (
        f"the reason history did not survive the restart: {second['reasons']}"
    )
    assert runaway.load("d-restart", base=state).attempts == 4, (
        "the restarted module and this process disagree about the count"
    )


# --- 2. the backoff is the harvested contract, not a local curve -------------


def test_backoff_follows_the_harvested_contract():
    """AC2: ``min(base * 2**(n-1), 300)`` — the contract ``retry.sh`` declares."""
    assert runaway.DEFAULT_BACKOFF_SECONDS == 30
    assert runaway.BACKOFF_CAP_SECONDS == 300
    series = [runaway.backoff_delay(n, base=30) for n in range(1, 7)]
    assert series == [30, 60, 120, 240, 300, 300], (
        f"the backoff is not the harvested exponential curve: {series}"
    )
    with pytest.raises(ValueError):
        runaway.backoff_delay(0)


def test_the_harvested_contract_is_source_backed_when_the_submodule_is_present():
    """The harvest is a citation, so it is re-read when the source is available.

    A fresh worktree has no ``vendor/CMR`` checkout (it is a submodule), which is
    the normal state — hence the declared pin above is the authority. When the
    file IS present the claim becomes checkable, and this asserts the vendored
    formula verbatim: a drift in either direction fails here.
    """
    vendored = REPO_ROOT / "vendor" / "CMR" / "ops" / "retry.sh"
    if not vendored.exists():
        assert "vendor/CMR/ops/retry.sh" in runaway.__doc__, (
            "the harvest must name its source even when the submodule is absent"
        )
        return
    text = vendored.read_text(encoding="utf-8")
    assert "delay=$((BACKOFF * (2 ** (n - 1))))" in text, (
        "the vendored retry.sh no longer declares the formula this module harvested"
    )
    assert '[ "$delay" -gt 300 ] && delay=300' in text, (
        "the vendored retry.sh no longer caps the delay at 300s"
    )


# --- 3. one counter, three kinds of failure ----------------------------------


def test_a_refused_claim_a_crashed_run_and_a_self_heal_share_one_counter(
    tmp_path, monkeypatch
):
    """AC4: the SAME counter — and a self-heal is an attempt, not a fresh start."""
    state = wire_scratch(tmp_path, monkeypatch, cap=99)
    monkeypatch.setattr(terminal, "report_once", record_reporting([]))
    plant(state / "inbox", "d-shared")

    assert terminal.guard_retire("d-shared", 723, "claim refused: refused") is False
    assert terminal.guard_retire("d-shared", 723, "run did not land: failed (rc=1)") is False
    assert terminal.guard_retire("d-shared", 723, "self-heal: our own run no longer exists") is False

    record = runaway.load("d-shared", base=state)
    assert record.attempts == 3, (
        f"a refused claim, a crashed run and a self-heal must share ONE counter: {record}"
    )
    assert len(list((state / "attempts").glob("*.json"))) == 1, (
        "the three failures wrote more than one counter file"
    )
    assert record.reasons == (
        "claim refused: refused",
        "run did not land: failed (rc=1)",
        "self-heal: our own run no longer exists",
    ), f"the reason history does not name each attempt: {record.reasons}"


# --- 4. K is configurable, declared, and a typo is refused -------------------


def test_the_cap_is_configurable_and_a_typo_is_refused(tmp_path, monkeypatch):
    """AC3: K is configurable (default documented) and unusable values are REFUSED."""
    assert runaway.DEFAULT_ATTEMPT_CAP == 5
    assert runaway.ENV_ATTEMPT_CAP in runaway.__doc__
    assert runaway.ENV_BACKOFF in runaway.__doc__

    monkeypatch.setenv(runaway.ENV_ATTEMPT_CAP, "2")
    assert runaway.attempt_cap() == 2
    first = runaway.record_attempt("d-cap", "claim refused", base=tmp_path)
    second = runaway.record_attempt("d-cap", "claim refused", base=tmp_path)
    assert first.exhausted is False and second.exhausted is True, (
        "AO_RUNAWAY_ATTEMPTS=2 did not bound the budget at two attempts"
    )

    monkeypatch.setenv(runaway.ENV_ATTEMPT_CAP, "zero")
    with pytest.raises(runaway.RunawayConfigError):
        runaway.record_attempt("d-cap", "claim refused", base=tmp_path)
    assert runaway.load("d-cap", base=tmp_path).attempts == 2, (
        "the refused write still changed the persisted count"
    )
    # ...and the READ path stays tolerant, so a typo cannot wedge the queue: an
    # unreadable budget must never stop `channel watch` from returning work.
    assert runaway.dispatchable("d-clean", base=tmp_path) is True


# --- 5. the negative control: an always-failing directive is retired ---------


def test_an_always_failing_directive_is_dead_lettered_and_never_watched_again(
    tmp_path, monkeypatch, capsys
):
    """AC3+AC5: after K attempts the order is dead-lettered, never returned again.

    The failing envelope is RE-PLANTED in the inbox after retirement, so a pass
    can only come from the guard's filter — not from the fact that the file
    happened to move. A healthy directive is present throughout, so "watch
    returned nothing" cannot be mistaken for a working filter either.
    """
    state = wire_scratch(tmp_path, monkeypatch, cap=3)
    monkeypatch.setattr(terminal, "report_once", record_reporting([]))
    plant(state / "inbox", "d-always-fails", ts="2026-09-14T00:00:00Z")
    plant(state / "inbox", "d-healthy", ts="2026-09-14T00:00:01Z")

    for _ in range(2):
        assert terminal.guard_retire("d-always-fails", 723, "claim refused: always") is False
    assert runaway.dead_lettered("d-always-fails", base=state) is False, (
        "the directive was retired before its budget was exhausted"
    )

    assert terminal.guard_retire("d-always-fails", 723, "claim refused: always") is True
    artifact = state / "dead-letter" / "d-always-fails.json"
    assert artifact.exists(), "the exhausted directive did not reach the dead-letter store"
    assert not (state / "inbox" / "d-always-fails.json").exists(), (
        "the retired order is still in the inbox"
    )
    assert runaway.dispatchable("d-always-fails", base=state) is False

    rc, out = watch(capsys)
    assert rc == channel.EXIT_OK, f"watch returned {rc} instead of the healthy directive"
    assert '"id": "d-healthy"' in out, f"watch did not return the healthy directive: {out!r}"
    assert "d-always-fails" not in out, "watch returned the dead-lettered directive"

    # The move is not what is being tested: put the order BACK and re-watch.
    plant(state / "inbox", "d-always-fails", ts="2026-09-14T00:00:00Z")
    rc, out = watch(capsys, skip=["d-healthy"])
    assert rc == channel.EXIT_NOT_OK, (
        f"watch dispatched a dead-lettered directive that was back in the inbox (rc={rc}): {out!r}"
    )
    assert "d-always-fails" not in out


# --- 5b. terminal classification (#861): dead-lettered on the FIRST refusal --
#
# A closed issue, a closed epic or an unowned unit can never be cured by a
# retry — so it must not consume the K-attempt budget the way a transient
# refusal (``already-claimed``, ``blocked``, a stale snapshot) legitimately
# does. These are the controls that prove the classification, and prove it is
# NARROW: a transient refusal still takes the slow (budgeted) road.


def test_terminal_classification_names_the_three_reasons_and_nothing_else():
    assert runaway.terminal_classification("claim REFUSED: issue-closed — #692 is closed") == "issue-closed"
    assert runaway.terminal_classification("claim REFUSED: epic-closed — parent #613 is closed") == "epic-closed"
    assert runaway.terminal_classification("claim REFUSED: unowned — no lane owns #601") == "unowned"
    # transient refusals are NOT terminal — the negative half of the control.
    assert runaway.terminal_classification("claim REFUSED: already-claimed — held by sister-2") is None
    assert runaway.terminal_classification("claim REFUSED: blocked — #900 blocked by #901") is None
    assert runaway.terminal_classification("claim REFUSED: snapshot-stale — refresh it") is None
    assert runaway.terminal_classification("") is None


def test_a_closed_issue_refusal_is_dead_lettered_on_the_first_refusal(tmp_path, monkeypatch):
    """The measured cost (#861): the generic path burns 450s (K=5, defaults) before
    retiring a directive that was dead on arrival. The terminal path retires it on
    attempt ZERO — no counter is even written."""
    state = wire_scratch(tmp_path, monkeypatch, cap=5)
    plant(state / "inbox", "d-closed-234")

    reason = "claim REFUSED: issue-closed — #234 is closed on the committed board"
    terminal_reason = runaway.terminal_classification(reason)
    assert terminal_reason == "issue-closed"

    retired = terminal.guard_retire_terminal("d-closed-234", 234, reason, terminal_reason)

    assert retired is True
    assert runaway.dead_lettered("d-closed-234", base=state) is True
    record = runaway.load("d-closed-234", base=state)
    assert record.attempts == 0, "the terminal path must not consume any of the K-attempt budget"
    assert not (state / "inbox" / "d-closed-234.json").exists()


def test_the_692_style_negative_control_a_transient_refusal_still_takes_the_slow_road(
    tmp_path, monkeypatch
):
    """The negative control this issue requires: an ``already-claimed`` refusal
    (retryable) must NOT be classified terminal, and must still take K attempts
    to retire — proving the fast path is narrow, not a general dead-letter-on-
    first-refusal shortcut."""
    state = wire_scratch(tmp_path, monkeypatch, cap=3)
    monkeypatch.setattr(terminal, "report_once", record_reporting([]))
    plant(state / "inbox", "d-transient")

    reason = "claim REFUSED: already-claimed — held by sister-2"
    assert runaway.terminal_classification(reason) is None

    assert terminal.guard_retire("d-transient", 723, reason) is False
    assert terminal.guard_retire("d-transient", 723, reason) is False
    assert runaway.dead_lettered("d-transient", base=state) is False, (
        "a transient refusal was dead-lettered before its budget was exhausted"
    )
    assert terminal.guard_retire("d-transient", 723, reason) is True
    assert runaway.dead_lettered("d-transient", base=state) is True


# --- 6/7. the backoff holds a directive; healthy work is never held ----------


def test_a_not_yet_due_directive_is_held_by_watch_and_then_released(
    tmp_path, monkeypatch, capsys
):
    """AC2: the backoff SPACES the attempts — held while due, released when elapsed."""
    state = wire_scratch(tmp_path, monkeypatch, cap=9)
    monkeypatch.setattr(terminal, "report_once", record_reporting([]))
    plant(state / "inbox", "d-backed-off")

    terminal.guard_retire("d-backed-off", 723, "claim refused: transient")
    assert runaway.due("d-backed-off", base=state) is False
    assert runaway.remaining("d-backed-off", base=state) > 0
    rc, out = watch(capsys)
    assert rc == channel.EXIT_NOT_OK, f"a directive still inside its backoff was dispatched (rc={rc})"
    assert "d-backed-off" not in out
    assert (state / "inbox" / "d-backed-off.json").exists(), (
        "the held directive was consumed — the order must stay the operator's record"
    )

    # Pretend the wait elapsed by re-stamping the attempt in the past: the same
    # directive must become dispatchable again, or the hold would be permanent.
    runaway.record_attempt("d-backed-off", "claim refused: transient", base=state, now=0)
    assert runaway.due("d-backed-off", base=state) is True
    rc, out = watch(capsys)
    assert rc == channel.EXIT_OK, f"the directive was not released after its backoff (rc={rc})"
    assert '"id": "d-backed-off"' in out


def test_a_healthy_directive_is_never_held(tmp_path, monkeypatch, capsys):
    """The non-vacuity control for the two tests above: fresh work is dispatched at once."""
    state = wire_scratch(tmp_path, monkeypatch, cap=3)
    monkeypatch.setattr(terminal, "report_once", record_reporting([]))
    plant(state / "inbox", "d-fresh")
    assert runaway.load("d-fresh", base=state) is None
    rc, out = watch(capsys)
    assert rc == channel.EXIT_OK and '"id": "d-fresh"' in out, (
        f"the guard held a directive it had never seen: rc={rc} {out!r}"
    )


# --- 8. the terminal artifact is the audit -----------------------------------


def test_the_dead_letter_artifact_carries_the_order_and_its_history(tmp_path, monkeypatch):
    """AC3: the retired order is auditable — envelope, count, reasons, reason."""
    state = wire_scratch(tmp_path, monkeypatch, cap=2)
    monkeypatch.setattr(terminal, "report_once", record_reporting([]))
    envelope = json.loads(plant(state / "inbox", "d-audit").read_text(encoding="utf-8"))

    terminal.guard_retire("d-audit", 723, "claim refused: first")
    terminal.guard_retire("d-audit", 723, "claim refused: second")

    artifact = json.loads((state / "dead-letter" / "d-audit.json").read_text(encoding="utf-8"))
    assert artifact["state"] == runaway.STATE_DEAD_LETTER
    assert artifact["attempts"] == 2 and artifact["cap"] == 2
    assert artifact["reason"] == "claim refused: second"
    assert artifact["reasons"] == ["claim refused: first", "claim refused: second"]
    assert artifact["envelope"] == envelope, "the retired order itself was not preserved"
    assert artifact["dead_lettered_at"]


def test_the_dead_letter_is_escalated_to_the_brain_and_names_the_remedy(
    tmp_path, monkeypatch
):
    """A terminal state nobody is told about is indistinguishable from a stuck order."""
    state = wire_scratch(tmp_path, monkeypatch, cap=1)
    calls: list[dict] = []
    monkeypatch.setattr(terminal, "report_once", record_reporting(calls))
    plant(state / "inbox", "d-escalate")

    assert terminal.guard_retire("d-escalate", 723, "claim refused: permanent") is True
    assert len(calls) == 1, f"expected exactly one report for the retirement: {calls}"
    call = calls[0]
    assert call["severity"] == "critical", "a retired directive is not a warning"
    assert "DEAD-LETTERED" in call["body"] and "#723" in call["body"]
    assert str(state / "dead-letter" / "d-escalate.json") in call["body"]
    assert "runaway.py rearm" in call["body"], "the operator is not told how to recover"


# --- 9. the guard stays inside its own root ----------------------------------


def test_the_guard_only_writes_below_the_root_it_is_given(tmp_path, monkeypatch):
    """The state directory is a parameter, so a test can never write the live fleet."""
    state = wire_scratch(tmp_path, monkeypatch, cap=2)
    monkeypatch.setattr(terminal, "report_once", record_reporting([]))
    plant(state / "inbox", "d-scoped")

    terminal.guard_retire("d-scoped", 723, "claim refused: scoped")
    terminal.guard_retire("d-scoped", 723, "claim refused: scoped")

    written = [path for path in state.rglob("*") if path.is_file()]
    assert written, "the guard wrote nothing at all — the assertion would be vacuous"
    for path in written:
        assert path.is_relative_to(state), f"the guard wrote outside its root: {path}"
    assert sorted(path.name for path in (state / "attempts").glob("*.json")) == ["d-scoped.json"]
    assert sorted(path.name for path in (state / "dead-letter").glob("*.json")) == ["d-scoped.json"]


def test_consuming_a_directive_drops_the_counter_but_keeps_the_artifact(tmp_path, monkeypatch):
    """A handled order's budget is history; a RETIRED order's evidence is not."""
    state = wire_scratch(tmp_path, monkeypatch, cap=2)
    monkeypatch.setattr(terminal, "report_once", record_reporting([]))
    plant(state / "inbox", "d-consumed")
    plant(state / "inbox", "d-retired")

    terminal.guard_retire("d-consumed", 723, "claim refused: transient")
    terminal.guard_retire("d-retired", 723, "claim refused: permanent")
    terminal.guard_retire("d-retired", 723, "claim refused: permanent")

    assert channel.consume_directive("d-consumed") is True
    assert not (state / "attempts" / "d-consumed.json").exists(), (
        "a consumed directive's counter was left behind"
    )
    assert (state / "attempts" / "d-retired.json").exists()
    assert (state / "dead-letter" / "d-retired.json").exists(), (
        "consuming the inbox copy erased the terminal evidence"
    )
    assert runaway.rearm("d-retired", base=state) is True
    assert runaway.rearm("d-retired", base=state) is False, "re-arm was not idempotent"


# --- 10/11. the assertions above can fail (mutant controls) ------------------


def test_the_dead_letter_assertion_can_fail_when_the_cap_is_disabled(
    tmp_path, monkeypatch, capsys
):
    """NEGATIVE CONTROL: with the cap disabled, the runaway really does happen.

    A scratch copy of the module whose ``attempt_cap()`` returns a huge number is
    driven through the SAME scenario as the dead-letter test. The directive must
    NOT be retired and must STILL be dispatched: that is the failure the guard
    prevents, and the gate that provokes it must be able to see it.
    """
    state = wire_scratch(tmp_path, monkeypatch, cap=3)
    mutant = load_mutant(tmp_path, *CAP_DISABLED)
    monkeypatch.setattr(terminal, "report_once", record_reporting([]))
    monkeypatch.setattr(terminal, "runaway", mutant)
    plant(state / "inbox", "d-runaway")

    for _ in range(30):
        assert terminal.guard_retire("d-runaway", 723, "claim refused: always") is False
    assert mutant.dead_lettered("d-runaway", base=state) is False, (
        "a cap-disabled guard retired the directive — the mutation did not apply"
    )
    # The mutant's counter makes the next attempt due 30s out; re-stamp it in the
    # past so the ONLY reason the order is not retired is the disabled cap.
    runaway.record_attempt("d-runaway", "claim refused: always", base=state, now=0)
    rc, out = watch(capsys)
    assert rc == channel.EXIT_OK and '"id": "d-runaway"' in out, (
        "the cap-disabled control did not reproduce the runaway the guard exists to stop"
    )


def test_the_backoff_assertion_can_fail_when_the_backoff_is_flat(
    tmp_path, monkeypatch, capsys
):
    """NEGATIVE CONTROL: a flat backoff re-dispatches inside the same cycle."""
    state = wire_scratch(tmp_path, monkeypatch, cap=9)
    mutant = load_mutant(tmp_path, *BACKOFF_FLAT)
    monkeypatch.setattr(terminal, "report_once", record_reporting([]))
    monkeypatch.setattr(terminal, "runaway", mutant)
    plant(state / "inbox", "d-flat")

    assert terminal.guard_retire("d-flat", 723, "claim refused: always") is False
    assert mutant.due("d-flat", base=state) is True, (
        "the flattened backoff was not applied — the mutation did not take effect"
    )
    rc, out = watch(capsys)
    assert rc == channel.EXIT_OK and '"id": "d-flat"' in out, (
        "a directive inside its backoff was dispatched: the hold is not what stops the loop"
    )
