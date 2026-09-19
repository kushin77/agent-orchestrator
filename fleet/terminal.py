#!/usr/bin/env python3
"""Dumb-terminal loop (dispatcher side) — never idles, always watches, escalates.

This is the loop the dispatcher runs so the fleet never stops: it watches the
inbox, runs a code-native executor per directive via the agent CLI, reports
the result, and escalates any failure to the director. An empty inbox is just
another poll cycle — there is no IDLE exit.

The directive's FinOps block is NOT decorative (#218): `model.tier` selects the
model the runner is invoked with, the tier/model/thinking are exported to the
child environment so a BYOK-wired wrapper can honour them, and a block this build
cannot turn into a runner REFUSES the dispatch instead of silently falling back
to the default runner.

Usage:
    python3 fleet/terminal.py run [--runner "claude -p"] [--watch-timeout 30]
                                 [--timeout 1800] [--dry-run] [--once]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import capacity
import channel
import routing
import runaway
import runners
import runtime
import singleton
import runslog

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from governance import spawn  # noqa: E402  (the spawn envelope, issue #793)

#: The standing directive (`fleet/directive.json`) — "the DSv4FNone order, in code".
#: Every executor prompt carries its standing clauses verbatim rather than a
#: paraphrase, so the mandate travels with the order and cannot drift from it.
DIRECTIVE_PATH = ROOT / "fleet" / "directive.json"


def load_standing_body(path: Path | None = None) -> str:
    """The standing directive's body, or '' when it cannot be read.

    The prompt frontload must survive a directive file that is missing or
    malformed — the dispatch itself is more important than the preamble — so a
    read failure degrades to the inline default in `build_prompt` instead of
    killing the spawn.
    """
    try:
        return str(json.loads((path or DIRECTIVE_PATH).read_text(encoding="utf-8")).get("body") or "")
    except (OSError, json.JSONDecodeError):
        return ""
CHANNEL = str(ROOT / "fleet" / "channel.py")
#: The institutional lane provisioner: mints the session identity before each spawn.
ISOLATION_CLI = str(ROOT / "governance" / "isolation" / "cli.py")
#: The institutional close-out: drives every artifact of a finished item to terminal.
LIFECYCLE_CLI = str(ROOT / "governance" / "lifecycle" / "cli.py")
#: Session reconciliation (#304): a per-lane heartbeat, so a lane whose agent died
#: is visible as a dead lane rather than as work in progress.
sys.path.insert(0, str(ROOT))
from governance.reconcile.heartbeat import DEFAULT_BEAT_SECONDS, Beater as SessionBeater  # noqa: E402
#: The reconciler's own vocabulary for "the sweep ENDED this orphan" (issue #694).
#: Its `Action.touched` is `outcome in {RECLAIMED, PARKED}`, and the loop reads the
#: same constants rather than restating the strings, so a change on the
#: reconciler's side cannot silently re-label the loop's decision about its own.
#:
#: The names are imported FROM THE SUBMODULE deliberately: `governance.reconcile`
#: re-exports a *function* called `sweep` from its `__init__`, so
#: `from governance.reconcile import sweep` binds that function and the constants
#: would only surface as an AttributeError at the first real orphan.
from governance.reconcile.sweep import PARKED as RECONCILE_PARKED  # noqa: E402
from governance.reconcile.sweep import RECLAIMED as RECONCILE_RECLAIMED  # noqa: E402
#: The board trigger (issue #727): on a `snapshot-stale` refusal the loop runs
#: ONE bounded refresh, and when freshness does not return it PARKS the
#: directive instead of re-dispatching it every cycle.
#:
#: The resolver lives in `channel` (already imported above) so there is exactly
#: one implementation of "where is governance/dispatch from here" in the fleet.
#: `board_snapshot()` also memoizes the module, so both callers share one copy.
board_snapshot = channel.board_snapshot

#: The board a stale snapshot is refreshed from.
BOARD_REPO = channel.BOARD_REPO

SESSION_BEAT_SECONDS = DEFAULT_BEAT_SECONDS

# --- the FinOps block selects the runner (issue #218) ------------------------
#
# The director's `model.tier`/`model.thinking` used to be printed into the prompt and
# otherwise ignored: every executor ran `claude -p`, so the `pro/low` floor raised
# for a security lane bought nothing and the FinOps block was decorative. It now
# selects the runner invocation, and a block this build cannot execute REFUSES the
# dispatch rather than falling back to the default runner.
#
# The vocabulary is harvested, not invented (GR-10):
#   * the tiers and the model ids are `governance/finops/policy.json`'s
#     (`vocabulary.tiers`, `tier_models`) — the FinOps chooser's own manifest;
#   * the `--model <tier-model>` argv shape and the `ANTHROPIC_MODEL` variable are
#     kushin77/deepseek's Claude-CLI-on-DeepSeek BYOK contract (#88, #91:
#     `claude --model <tier-model>` with the BYOK environment applied), whose #54
#     asks for exactly the refusal below: "a profile the module does not know is
#     refused explicitly rather than silently mapped to a default".
# The tier vocabulary itself is NOT copied here — it is read from the routing
# policy (`fleet/routing.py`, ADR-0012), the fleet's single source of dispatch
# vocabulary — so a tier the policy adds and this map forgets REFUSES by name
# instead of being dispatched at whatever the map happens to hold.
#
# BYOK credentials are deliberately absent: `ANTHROPIC_AUTH_TOKEN` and
# `ANTHROPIC_BASE_URL` come from the principal's environment or a secret manager
# and are never written here (GR-6). This module exports only the tier, the model
# and the thinking effort — which is what a BYOK-wired wrapper needs to honour it.
TIER_RUNNERS: dict[str, dict[str, str]] = {
    "flash": {"model": "deepseek-v4-flash", "flag": "--model"},
    "pro": {"model": "deepseek-v4-pro", "flag": "--model"},
    "auditor": {"model": "deepseek-v4-pro", "flag": "--model"},
}

#: The base runner command when the principal passes none. The tier does not
#: replace it — it selects the model that command is invoked with. ``FLEET_RUNNER``
#: (documented in ``fleet/README.md`` and, until #733, read by nothing) sets the same
#: default, so a principal can point the fleet at an absolute runner path from the
#: environment cron gives it.
DEFAULT_RUNNER = "claude -p"

#: The exit code a refused dispatch reports (EX_CONFIG: the order cannot be
#: executed as declared). Distinct from 127 (could not start) and 124 (timeout).
RC_REFUSED = 78


class FinOpsRefusal:
    """A refusal with a stable code and its reason — never a silent fallback.

    The posture of ``routing.RoutingRefusal`` and the channel's own refusals: a
    declaration this build cannot honour is reported by name, because the
    alternative — dispatching at the default runner while the record says `pro` —
    is the decorative-FinOps defect #218 exists to remove.
    """

    def __init__(self, code: str, reason: str) -> None:
        self.code = code
        self.reason = reason

    def __str__(self) -> str:
        return f"{self.code}: {self.reason}"


def finops_policy():
    """The routing policy (ADR-0012): the tier/thinking vocabulary of record.

    Loaded on use rather than at import: the policy reads the registry persona
    cards, so a malformed policy must refuse a *dispatch*, not the terminal's
    import (and the module stays safe to import in a context with no I/O).
    """
    return routing.policy()


def resolve_dispatch(
    directive: dict, base_runner: str | None = None
) -> tuple[dict | None, FinOpsRefusal | None]:
    """Resolve the directive's FinOps block into the real runner invocation.

    Returns ``(dispatch, None)`` or ``(None, refusal)``, where the dispatch is::

        {tier, thinking, model, risk, runner, env}

    ``runner`` is the base command plus the tier's model flag; ``env`` carries the
    FinOps variables exported to the child. Nothing is guessed: an ABSENT block
    takes the policy's declared default (start cheap — the doctrine's default, not
    a fallback for a declared tier), while a declared value this build cannot
    route is refused by name. The high floor is honoured too: a security/secrets/
    auth/IaC lane whose block sits below the policy's floor is refused rather than
    silently raised, so the principal sees what was ordered.
    """
    try:
        policy = finops_policy()
    except routing.RoutingRefusal as exc:
        return None, FinOpsRefusal(
            "policy-unreadable", f"the routing policy cannot be loaded: {exc}"
        )
    tiers = policy.tier_vocabulary
    thinkings = policy.thinking_vocabulary
    block = directive.get("model")
    block = block if isinstance(block, dict) else {}
    tier = block.get("tier") or policy.default_block[0]
    thinking = block.get("thinking") or policy.default_block[1]
    if tier not in tiers:
        return None, FinOpsRefusal(
            "tier-unknown",
            f"model.tier {tier!r} is not one of the FinOps tiers ({', '.join(tiers)})",
        )
    if thinking not in thinkings:
        return None, FinOpsRefusal(
            "thinking-unknown",
            f"model.thinking {thinking!r} is not one of ({', '.join(thinkings)})",
        )
    entry = TIER_RUNNERS.get(str(tier))
    if entry is None:
        return None, FinOpsRefusal(
            "tier-unmapped",
            f"FinOps tier {tier!r} has no runner mapped (mapped: {', '.join(sorted(TIER_RUNNERS))}) — "
            "refusing rather than dispatching at the default runner",
        )
    task = directive.get("task")
    task = task if isinstance(task, dict) else {}
    lane = str(task.get("lane") or "")
    title = str(task.get("title") or "")
    risk = policy.risk_for(lane, title)
    if risk == routing.HIGH:
        floored = policy.floor(str(tier), str(thinking))
        if floored != (str(tier), str(thinking)):
            return None, FinOpsRefusal(
                "floor-violated",
                f"'{lane} {title}' bears risk but the block declares {tier}/{thinking}; the "
                f"policy's high floor is {floored[0]}/{floored[1]} — a floor, never a ceiling. Refused "
                "rather than silently raised: the brain applies this floor before it sends (fleet/brain.py)",
            )
    model = str(entry["model"])
    # The model switch is the PROFILE's, not a second hard-coded flag (#841). A runner is
    # a (binary, argv shape, model vocabulary, environment) quadruple, and this map carried
    # the vocabulary while `DEFAULT_RUNNER`/`FLEET_RUNNER` carried the binary — so the two
    # could disagree and nothing said so. The native DeepSeek CLI spells the switch `-m`.
    # `entry["flag"]` stays the fallback; the capability preflight has already refused an
    # unknown profile by name before we reach here.
    chosen = runners.profile_for(base_runner or DEFAULT_RUNNER)
    model_flag = chosen.model_flag if chosen is not None else str(entry["flag"])
    runner = shlex.join(
        [*shlex.split(base_runner or DEFAULT_RUNNER), model_flag, model]
    )
    env = {
        "AO_TIER": str(tier),
        "AO_THINKING": str(thinking),
        "AO_MODEL": model,
        "AO_RISK": risk,
        "AO_RUNNER": runner,
        # deepseek #88's own BYOK variable, so a wrapper honours the tier's model
        # with no fleet-specific glue.
        "ANTHROPIC_MODEL": model,
    }
    return (
        {
            "tier": str(tier),
            "thinking": str(thinking),
            "model": model,
            "risk": risk,
            "runner": runner,
            "env": env,
        },
        None,
    )


def finops_line(dispatch: dict | None) -> str:
    """What the FinOps block selected, as one line — logged on every dispatch.

    #218's own ``Verify:`` asks for a dispatch log line that NAMES the
    tier-selected runner, so the FinOps claim is measurable rather than asserted.
    """
    if not isinstance(dispatch, dict):
        return "FinOps unknown (no block resolved)"
    return (
        f"FinOps tier={dispatch.get('tier')}/{dispatch.get('thinking')} "
        f"model={dispatch.get('model')} runner={dispatch.get('runner')} "
        f"risk={dispatch.get('risk')}"
    )


def extract_json(text: str) -> dict:
    """Pull the first balanced JSON object out of `watch` output."""
    start = text.find("{")
    if start == -1:
        return {}
    depth = 0
    in_str = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_str:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_str = False
            continue
        if char == '"':
            in_str = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    data = json.loads(text[start : index + 1])
                except json.JSONDecodeError:
                    return {}
                return data if isinstance(data, dict) else {}
    return {}


def build_envelope(
    directive: dict,
    agent_id: str = "subagent",
    worktree: Path | None = None,
    env: dict[str, str] | None = None,
    context: dict | None = None,
) -> dict:
    """The spawn envelope for one directive: every governance fact, in one document.

    Before #793 the governance a fleet executor obeyed was PROSE inlined into the
    prompt, where nothing could check that a spawn had carried it, and a locally
    spawned executor shared none of it. The envelope is produced by
    `governance/spawn` — the SAME producer the local path calls — so the two
    regimes converge by construction, and a document missing a required field is
    refused by name before any child exists.

    Only facts this loop already holds are supplied; everything else (the claim,
    the issue's epic, the capacity permit, the attempt budget, the issue's own
    `Verify:` clause) is read from the institution that OWNS it, so the envelope
    cannot state something the ledger does not.
    """
    task = directive.get("task") or {}
    identity = dict(env or {})
    pack = context if isinstance(context, dict) else {}
    return spawn.produce(
        issue=task.get("issue"),
        lane=task.get("lane") or "",
        agent_id=agent_id,
        path="fleet",
        directive_id=str(directive.get("id") or ""),
        env=identity,
        worktree=str(worktree) if worktree is not None else str(identity.get("AO_WORKTREE") or ""),
        body=str(pack.get("body") or ""),
        files=spawn.sources.worktree_files(task.get("files")),
        root=ROOT,
    )


def build_prompt(
    directive: dict,
    agent_id: str = "subagent",
    worktree: Path | None = None,
    env: dict[str, str] | None = None,
    context: dict | None = None,
) -> str:
    """The executor prompt: one issue, one worktree, one session identity, one envelope.

    The prompt CONSUMES the spawn envelope (#793) instead of restating governance.
    It used to inline the standing mandate, the identity, the trailer and the
    lane discipline as prose written out here — so the guarantee was "somebody
    remembered to write it into this string", nothing checked that a spawn had
    carried it, and the locally spawned path shared none of it. Now
    `governance/spawn` is the ONE producer of that text, the same document governs
    both spawn paths, and an envelope that cannot be validated raises
    `spawn.EnvelopeRefused` rather than being spawned with a warning.

    The claim is *owned by the loop*, not by the executor: an executor that died
    mid-task used to leave its claim wedged until the 24h TTL, because nobody
    was left to release it. The loop claims before the spawn and releases in a
    `finally`, so a dead executor can no longer strand an issue.

    The session identity travels with the order too. An agent that does not know
    which branch it is on cannot keep its commits traceable to the ticket, so the
    id, the branch and the required trailer are stated rather than assumed.

    The mandate comes FIRST. The standing directive's live-CI/CD-SDLC and
    replaceability clauses are frontloaded ahead of the order itself, because a
    executor that reads only the first lines must still know that it is gated
    (real `Verify:` + `make verify` output is the evidence), that its commit must
    be atomic and green, and that it may be replaced at any moment — so it
    executes only this directive, keeps every fact it needs in artifacts, and
    leaves every artifact terminal.

    The prompt also carries a CONTEXT PACK (#220) — the issue's title, body and
    acceptance criteria, its lane and its own ``Verify:`` clause, plus the lessons
    a previous lane already paid for — so the executor does not have to rediscover
    the work it was ordered to do. `context` is supplied by the loop, which also
    records it with the run; when absent it is built from the committed board
    snapshot (offline).
    """
    task = directive.get("task") or {}
    issue = task.get("issue")
    lane = task.get("lane") or ""
    model = directive.get("model") or {}
    pack = context if isinstance(context, dict) else (
        issue_context(issue, lane) if isinstance(issue, int) else None
    )
    envelope = build_envelope(directive, agent_id, worktree, env, pack)
    return spawn.render.prompt(
        envelope,
        standing=load_standing_body(),
        directive_body=str(directive.get("body", "")),
        directive_id=str(directive.get("id", "")),
        tier=str(model.get("tier", "flash")),
        thinking=str(model.get("thinking", "none")),
        context_block=render_context_pack(pack) if pack else "",
    )


def build_command(
    directive: dict,
    runner: str | list[str],
    agent_id: str,
    worktree: Path | None = None,
    env: dict[str, str] | None = None,
    context: dict | None = None,
) -> list[str]:
    """Runner must accept the prompt as its final argument (e.g. `claude -p`).

    ``runner`` is either the documented string form or an argv already resolved by
    :func:`resolve_runner` — re-splitting a resolved path through ``shlex`` would
    break a legitimate install path that contains a space.
    """
    argv = list(runner) if isinstance(runner, (list, tuple)) else shlex.split(runner)
    return argv + [build_prompt(directive, agent_id, worktree, env, context)]


def resolve_runner(runner: str) -> tuple[list[str] | None, str]:
    """Resolve a runner command to an absolute argv, or say why it cannot be.

    The documented form is ``--runner "claude -p"``: only ``argv[0]`` is the
    executable and the rest is that runner's own vocabulary, passed through
    untouched. A runner that already names a path is used exactly as given — an
    principal who writes a file means that file.

    Resolution is explicit here rather than inherited from the shell because the
    loop is cron's child and inherits cron's minimal PATH (#733: ``~/.local/bin``
    was not on it, so every directive died with ``FileNotFoundError: 'claude'``).
    The directories searched are declared once in ``fleet/runtime.py`` so the
    preflight and this belt cannot drift apart.
    """
    argv = shlex.split(runner)
    if not argv:
        return None, "the runner command is empty"
    executable = argv[0]
    if os.sep in executable:
        if os.access(executable, os.X_OK):
            return argv, ""
        return None, f"'{executable}' is not an executable file"
    search = runtime.runner_search_path()
    found = shutil.which(executable, path=os.pathsep.join(search))
    if found:
        return [found, *argv[1:]], ""
    return None, f"'{executable}' is not on PATH (searched: {os.pathsep.join(search)})"


def preflight(runner: str) -> tuple[bool, str]:
    """Can this loop spawn at all? Resolved BEFORE the loop reads the inbox (#733).

    Returns ``(True, the resolved executable)`` or ``(False, one actionable line)``.
    It runs every cycle rather than once at startup: a loop that only checked at
    startup would keep the queue held after the runner was installed, and a check
    that only ran at dispatch time failed once per directive per cycle — the
    runaway amplifier this replaces.
    """
    argv, problem = resolve_runner(runner)
    if problem:
        return False, f"runner unresolvable: {problem}"
    return True, f"runner resolved: {argv[0]}"


#: The runtime id this loop IS, and the one it SPAWNS (fleet/runtimes.yaml).
SISTER_RUNTIME_ID = "deepseek-sister"
EXECUTOR_RUNTIME_ID = "deepseek-executor"


def beat_runtime(runtime_id: str, state: str = "running") -> None:
    """Post this loop's runtime beat (#1412) — at start and on every poll cycle.

    The sibling of `write_heartbeat` above and a different question: that beat says
    what this LOOP is doing for the fleet's own status surface, this one is the
    liveness record `scripts/check-runtime-liveness.sh` judges against
    `fleet/runtimes.yaml` (`runtime-stale:<id>` when it stops arriving). Until
    #1412 nothing produced it, so the judge could only ever report `no-beats-yet`.

    Never fatal: a liveness stamp must not be able to kill the thing it reports on.
    A refusal (an unregistered id, an unreadable registry) is printed and the loop
    carries on — the judge then reports the runtime stale, which is the truth.

    `beats` is resolved LAZILY, through a function rather than a module-level
    import: gate fixtures copy `fleet/terminal.py` alone into a scratch tree
    (`scripts/check-orphan-handoff.sh` provokes its mutation that way), and a hard
    import would make the loop unloadable there — a scratch tree that cannot import
    the loop fails a vacuity control for the wrong reason.

    The beat lands in `beats.ROOT` (the fleet tree the producer owns) rather than in
    `terminal.ROOT`, and that is the difference between a test that is isolated and
    one that reds the gate of record: `fleet/tests/conftest.py` redirects
    `beats.ROOT` for every test, so a suite that drives this loop cannot leave a
    beat behind — one stray beat engages the judge on the next gate run and reports
    every OTHER registered runtime `runtime-stale`. Measured, on this lane, before
    the split: two suite runs left `deepseek-sister`/`deepseek-executor` beats in
    the worktree and `check-runtime-liveness` went red on five innocent runtimes.
    """
    try:
        import beats
    except ImportError as exc:
        print(f"[beats] {runtime_id} beat REFUSED — fleet/beats.py is not importable: {exc}", file=sys.stderr, flush=True)
        return
    beats.best_effort(runtime_id, state, root=beats.ROOT, cwd=ROOT)


def start_session_beat(env: dict | None, pid: int) -> object | None:
    """Beat a per-session heartbeat for the lane this dispatch owns (#304).

    The pid recorded is the **executor's**, not this loop's: a lane has to go
    stale when *its* process dies, and the loop outlives every lane it dispatches,
    so a loop pid would keep every orphan looking alive forever.
    """
    session_id = (env or {}).get("AO_SESSION_ID", "")
    if not session_id:
        return None
    try:
        return SessionBeater(
            session_id=session_id,
            issue=int((env or {}).get("AO_ISSUE") or 0),
            agent=(env or {}).get("AO_AGENT_ID", ""),
            root=ROOT,
            lane=(env or {}).get("AO_LANE", ""),
            worktree=(env or {}).get("AO_WORKTREE", ""),
            branch=(env or {}).get("AO_BRANCH", ""),
            pid=pid,
            interval=SESSION_BEAT_SECONDS,
        ).start()
    except (OSError, ValueError) as exc:
        print(f"[terminal] session heartbeat not started for {session_id}: {exc}", file=sys.stderr, flush=True)
        return None


def stop_session_beat(beater: object | None) -> None:
    """Stop beating and remove the heartbeat: a clean exit is not an orphan."""
    if beater is not None:
        try:
            beater.stop()  # type: ignore[attr-defined]
        except (OSError, ValueError):
            pass


def _pump_child_stdout(child, directive_id: str, captured: list[str]) -> None:
    """Tee the child's stdout into the run's live log stream, line by line.

    The pipe makes the child line-buffer its output (a plain file redirect would
    block-buffer it and the `follow` view would lag); this thread drains it so
    the loop can ``wait`` without a pipe-buffer deadlock, and every line lands
    in `.fleet/runs/<directive>.log` the moment the executor writes it — the
    per-directive live log stream `channel follow --directive <id>` tails
    (issue #367).
    """
    stream = getattr(child, "stdout", None)
    if stream is None:
        return
    try:
        for line in stream:
            captured.append(line)
            try:
                channel.append_directive_log(
                    directive_id, str(line).rstrip("\n"), source="subagent"
                )
            except (OSError, ValueError):
                pass
    except (OSError, ValueError):
        pass


def run_once(
    directive: dict,
    runner: str,
    timeout: float,
    dry_run: bool,
    agent_id: str,
    worktree: Path | None = None,
    env: dict[str, str] | None = None,
    slot: dict | None = None,
    context: dict | None = None,
) -> tuple[int, str]:
    """Run one executor for one directive; return (exit code, captured output).

    Uses Popen rather than `subprocess.run` so the child is reachable from the
    stop handler: a stopped loop must take its executor down with it instead of
    orphaning it. The session environment is injected here, so the executor and
    everything it spawns commit under its own identity.

    The run's context pack (#220) is `context`, or the pack the loop parked on the
    slot. Resolving it from the slot keeps this call's shape unchanged for callers
    that predate the pack, while still putting what the executor was told into the
    prompt it receives.

    The FinOps block selects the runner here (#218): `runner` is the base command
    and the directive's `model.tier` appends the model it is invoked with, so the
    argument this function is handed can no longer be the whole story of what the
    child runs. The resolved dispatch is taken from the slot when the loop has
    already made the decision (so the recorded tier and the dispatched tier cannot
    disagree), and resolved here otherwise. A block this build cannot route returns
    `RC_REFUSED` with the reason and spawns nothing.
    """
    pack = context if context is not None else (slot or {}).get("context")
    dispatch = (slot or {}).get("dispatch")
    if not isinstance(dispatch, dict):
        dispatch, refusal = resolve_dispatch(directive, runner)
        if refusal is not None:
            print(f"[terminal] dispatch REFUSED — {refusal}", file=sys.stderr, flush=True)
            return RC_REFUSED, f"dispatch refused: {refusal}"
    # The spawn envelope is a PRECONDITION, not a suggestion (#793): `build_command`
    # consumes it, and an envelope that cannot be validated refuses the spawn here
    # — before any child exists, with its own exit code, naming every field at
    # fault. Before this, the governance an executor obeyed was prose in the prompt
    # and nothing could refuse a spawn that omitted it.
    try:
        command = build_command(directive, str(dispatch["runner"]), agent_id, worktree, env, pack)
    except spawn.EnvelopeRefused as refused:
        detail = "; ".join(refused.lines())
        print(f"[terminal] #{directive.get('task', {}).get('issue')} spawn REFUSED — {detail}", file=sys.stderr, flush=True)
        return RC_REFUSED, f"spawn refused (rc {RC_REFUSED}): {detail}"
    cwd = str(worktree) if worktree is not None else str(ROOT)
    directive_id = str(directive.get("id") or "unknown")
    print(f"[terminal] {finops_line(dispatch)}", flush=True)
    if dry_run:
        print("DRY-RUN:", " ".join(shlex.quote(part) for part in command), flush=True)
        return 0, f"DRY-RUN (not executed) in {cwd}"
    # The executor runtime beats the moment it is about to exist (#1412): the
    # dispatch reached a spawn, which is exactly what `deepseek-executor` being
    # alive means. `RunBeater` above keeps it fresh for as long as the child runs.
    beat_runtime(EXECUTOR_RUNTIME_ID)
    # Resolve the executable in code, not by inheriting whatever PATH happened to
    # start this loop (#733): argv[0] is handed to the child as an ABSOLUTE path,
    # so the spawn cannot fail on a PATH the fleet does not control. The loop's
    # preflight has already refused the queue when this cannot resolve; this is the
    # belt for a runner that vanished mid-flight.
    resolved, problem = resolve_runner(command[0])
    if problem:
        return 127, f"runner could not start in {cwd}: {problem}"
    command[0] = resolved[0]
    try:
        child = subprocess.Popen(
            command,
            cwd=cwd,
            # stdin stays a pipe: the loop writes a mid-run steer into it
            # (`deliver_pending_steers`), and stdout is drained live by the pump.
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            # The FinOps block is exported LAST: it is the authority the principal
            # asked for, the lane environment carries identity. PATH comes from
            # `runtime.runner_env`, so the runner's own directory is on the child's
            # PATH and anything the runner resolves by name still resolves for it.
            env={**runtime.runner_env(), **(env or {}), **dict(dispatch.get("env") or {})},
        )
    except OSError as exc:
        return 127, f"runner could not start in {cwd}: {exc}"
    if slot is not None:
        slot["child"] = child
    captured: list[str] = []
    pumper = threading.Thread(
        target=_pump_child_stdout, args=(child, directive_id, captured), daemon=True
    )
    pumper.start()
    beater = start_session_beat(env, child.pid)
    try:
        try:
            rc = child.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            child.kill()
            rc = child.wait()
            pumper.join(timeout=5.0)
            return 124, f"runner timed out after {timeout}s: {(''.join(captured) or '')[-400:]}"
        pumper.join(timeout=5.0)
    finally:
        stop_session_beat(beater)
        if slot is not None:
            slot["child"] = None
    return rc, "".join(captured) or ""


def provision_worktree(
    issue: int,
    directive_id: str,
    agent_id: str,
    lane: str,
) -> tuple[Path, str, dict[str, str]] | None:
    """One lane = one session identity = one branch = one working tree.

    Provisioning goes through ``governance/isolation`` rather than creating a
    worktree here, because the point is not the directory: the module mints the
    session id and stamps the session's signature into that worktree's *own*
    config, so the executor's commits are attributable to the agent that made
    them instead of to whoever's checkout they happen to share. Returns
    (path, branch, env), or None when provisioning failed — the caller then runs
    in the shared checkout and must say so.
    """
    result = subprocess.run(
        [
            "python3", ISOLATION_CLI, "open",
            "--issue", str(issue),
            "--agent", agent_id,
            "--lane", lane or "fleet",
            "--suffix", directive_id[:8],
            "--main", str(ROOT),
            "--base", "origin/master",
            "--fetch",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[-300:]
        print(f"[terminal] lane not provisioned for #{issue}: {detail}", file=sys.stderr, flush=True)
        return None
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        print(f"[terminal] lane provisioning returned unreadable JSON for #{issue}", file=sys.stderr, flush=True)
        return None
    identity = payload.get("identity") or {}
    worktree = identity.get("worktree")
    branch = identity.get("branch")
    if not worktree or not branch:
        print(f"[terminal] lane provisioning named no worktree for #{issue}", file=sys.stderr, flush=True)
        return None
    for problem in payload.get("problems") or []:
        print(f"[terminal] lane {identity.get('session_id')} isolation problem: {problem}", file=sys.stderr, flush=True)
    return Path(worktree), str(branch), dict(payload.get("env") or {})


def claim_issue(issue: int, agent_id: str, lane: str, directive_id: str) -> tuple[bool, str]:
    """The loop takes the claim, so a dead executor can never strand one."""
    result = subprocess.run(
        [
            "python3", str(ROOT / "governance" / "dispatch" / "cli.py"), "claim",
            "--issue", str(issue), "--agent", agent_id, "--lane", lane or "fleet",
            "--directive", directive_id,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0, (result.stdout + result.stderr).strip()


def release_issue(issue: int, agent_id: str) -> tuple[bool, str]:
    """Release a claim; report the outcome rather than swallowing it.

    A silent release failure is how a finished run left #167 held for the next
    principal to find, so the caller now gets the channel's own words. A claim
    that is *already* free is not a failure, though: ``not-claimed`` is a benign
    no-op (the graceful-stop path releases once and the run's ``finally`` used to
    report the second, refused release as "release FAILED" — #281).
    ``not-owner`` — a release of someone else's claim — is still a real failure
    and is surfaced.
    """
    result = subprocess.run(
        [
            "python3", str(ROOT / "governance" / "dispatch" / "cli.py"), "release",
            "--issue", str(issue), "--agent", agent_id,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    output = (result.stdout + result.stderr).strip()
    if result.returncode == 0:
        return True, output
    if "not-claimed" in output:
        return True, f"already released (benign): {output}"
    return False, output


def _mark_released(slot: dict) -> bool:
    """Atomically take ownership of a run's release: True exactly once per slot.

    Two writers race for one claim on a graceful stop — ``stop_and_release`` (the
    signal handler) and the run's ``finally`` — and ``release`` is deliberately
    not idempotent. The per-slot ``released`` flag, guarded by ``RUNS_LOCK``,
    makes one caller the single owner: the first releases, the second is a no-op.
    """
    with RUNS_LOCK:
        if slot.get("released"):
            return False
        slot["released"] = True
        return True


def release_in_flight(slot: dict) -> None:
    """Release one run's claim exactly once, whichever path gets there first.

    The loop owns the claim and a worker releases it in its ``finally``, so a
    dead child can never strand it; ``stop_and_release`` races the same release
    and ``_mark_released`` picks a single owner (#281).
    """
    if not _mark_released(slot):
        return
    issue = slot.get("issue")
    agent_id = slot.get("agent_id")
    directive_id = slot.get("directive")
    released, release_output = release_issue(issue, agent_id)
    if not released:
        print(f"[terminal] release of #{issue} FAILED: {release_output}", file=sys.stderr, flush=True)
        subprocess.run(
            ["python3", CHANNEL, "escalate", "--from", "sister", "--correlation", directive_id,
             "--severity", "warn", "--body", f"release of #{issue} failed: {release_output[-400:]}"],
            cwd=ROOT,
        )


def closeout_issue(issue: int) -> str:
    """Drive the item's remaining artifacts to their terminal state.

    A run that stopped at "the PR is merged" used to be reported as a success
    while the source branch, the claim, the authorisation directive and the lane
    worktree were all still live — five separate drifts, none of them noticed by
    a gate. Close-out runs here and its verdict travels with the report, so a
    partial close reaches the director instead of being found later by hand.

    The step itself is `governance/lifecycle` (`LIFECYCLE_CLI` above): it names
    the closure invariants and drives them in dependency order, reporting what
    remains rather than a success it cannot evidence. This loop only carries its
    verdict.
    """
    result = subprocess.run(
        ["python3", LIFECYCLE_CLI, "close", "--issue", str(issue)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = (result.stdout + result.stderr).strip()[-600:]
        print(f"[terminal] close-out of #{issue} left invariants broken:\n{detail}", file=sys.stderr, flush=True)
        return "NOT-OK"
    return "OK"


def stop_and_release(reason: str) -> None:
    """A stopped loop must not strand any claim: take every executor down, free all.

    Observed live: restarting the dispatcher loop mid-run killed it before the
    `finally`, so #167 stayed claimed by an agent that no longer existed — the
    exact wedge the reap tool exists to clean, recreated by a principal restart.
    With a pool the same must hold for *every* in-flight child: a stop takes all
    N down and releases each claim exactly once.
    """
    for slot in active_run_slots():
        slot["stopped"] = True
        child = slot.get("child")
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
        # Single owner: whichever of this handler and the run's own `finally`
        # wins `_mark_released` releases the claim; the other is a no-op (#281).
        if not _mark_released(slot):
            continue
        issue = slot.get("issue")
        agent_id = slot.get("agent_id")
        directive_id = slot.get("directive")
        held = subprocess.run(
            ["python3", str(ROOT / "governance" / "dispatch" / "cli.py"), "held", "--issue", str(issue)],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if held.returncode != 0:
            # Nothing was held (the claim was refused, or already released): say
            # so rather than reporting a release that never happened.
            subprocess.run(
                ["python3", CHANNEL, "escalate", "--from", "sister", "--correlation", directive_id or "unknown",
                 "--severity", "warn", "--body", f"operator stopped the loop mid-run on #{issue} ({reason}); "
                 "no live claim to release"[:2000]],
                cwd=ROOT,
            )
            continue
        ok, output = release_issue(issue, agent_id)
        body = (
            f"operator stopped the loop mid-run on #{issue} ({reason}); claim released"
            + (" — re-dispatch is safe" if ok else f" — RELEASE FAILED: {output[-200:]}")
        )
        subprocess.run(
            ["python3", CHANNEL, "escalate", "--from", "sister", "--correlation", directive_id or "unknown",
             "--severity", "warn", "--body", body[:2000]],
            cwd=ROOT,
        )


def handle_stop(signum: int, frame: object) -> None:
    print(f"[terminal] signal {signum} — releasing the in-flight claim and stopping", flush=True)
    stop_and_release(f"signal {signum}")
    raise SystemExit(128 + signum)


def directive_issue(directive: dict) -> int | None:
    """The issue a directive names; None when it is not executable work."""
    task = directive.get("task") or {}
    issue = task.get("issue")
    if isinstance(issue, bool) or not isinstance(issue, int) or issue < 1:
        return None
    return issue


#: The repository whose board carries the truth about whether a run's work landed.
REPO = "kushin77/agent-orchestrator"
#: The gate of record, run by the loop itself — never inferred from model prose.
GATE_OF_RECORD = "make verify"
#: The gate vocabulary (``guardrails/honesty``): a gate either assessed the work or
#: it did not. CANNOT-ASSESS is never a pass and never a failure of the work.
#: Measured (#733): the loop's 1800s ``make verify`` on a loaded box raised
#: ``TimeoutExpired``, which was reported as a gate FAILURE and re-dispatched the
#: directive every cycle — a timeout is the loop not knowing, not the work failing.
GATE_OK = "OK"
GATE_NOT_OK = "NOT-OK"
GATE_CANNOT_ASSESS = "CANNOT-ASSESS"
#: A ``Verify:`` line is only executed when it is command-shaped: its first token
#: must be an executable the fleet can run. The rule itself lives in
#: `governance/spawn/sources.py` (#793) so the clause the loop RUNS, the clause the
#: context pack SHOWS and the clause the envelope CARRIES are one implementation.


def looks_refused(output: str) -> bool:
    """A *hint* that an executor stopped on a refusal — never the verdict.

    Kept deliberately as a hint only (#279): used as a verdict it matched a
    quoted ``REFUSED`` in an otherwise successful run and downgraded it, while
    matching nothing in a run that printed confident prose and did no work. The
    verdict is ``verdict()``, derived from evidence the loop runs itself.
    """
    upper = output.upper()
    return "REFUSED" in upper or "NO WORK DONE" in upper or "NO REAL ISSUE" in upper


def extract_verify_command(body: str) -> str | None:
    """The issue's own ``Verify:`` command — only when it is command-shaped.

    One implementation, in the envelope producer (#793): the clause the loop
    EXECUTES and the clause the spawn envelope CARRIES must not be two readers
    that can drift, so this delegates rather than restating the rule.
    """
    return spawn.sources.verify_command(body)


def gh_issue_field(issue: int, jq: str) -> str | None:
    """Read one field of issue #issue from the real board; None when unreachable.

    The loop reads the board itself so the verdict rests on GitHub's state, not
    on an executor's claim about it. ``None`` means "cannot assess", which is
    never a pass.
    """
    try:
        result = subprocess.run(
            ["gh", "api", f"repos/{REPO}/issues/{issue}", "--jq", jq],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    read = (result.stdout or "").strip()
    return read if read else None


def issue_verify_command(issue: int) -> str | None:
    """The issue's own ``Verify:`` command, read from the board the loop trusts."""
    return extract_verify_command(gh_issue_field(issue, ".body") or "")


# --- context pack (#220): what the executor is TOLD, not just ordered ---------
#
# A directive used to carry the principal's prose and nothing else, so the executor
# rediscovered the issue it was ordered to do — its acceptance criteria, its lane,
# and, most expensively, the lessons a previous lane already paid for. The pack is
# assembled here, from artifacts that are committed (the board snapshot and the
# lessons ledger), so a dispatch can describe the work without the network.

#: The committed board state — the offline source of an issue's identity.
BOARD_SNAPSHOT = ROOT / ".board" / "snapshot.json"
#: The lessons ledger — the single record of what a previous lane already learned.
LESSONS_LEDGER = ROOT / "governance" / "lessons" / "ledger.jsonl"
#: How many relevant lessons one dispatch carries; more than a handful is noise.
CONTEXT_LESSON_LIMIT = 5

#: Words too common to signal relevance between a lesson and an issue title.
_STOPWORDS = frozenset(
    "a an and are as at be but by can did do does for from had has have if in into is it its "
    "more most not of on or other our over own same than that the their them then there these "
    "they this to too under until up use used using via was we were what when where which while "
    "who will with would you your".split()
)


def _keywords(text: str) -> set[str]:
    """The content words of `text`: lower-cased, stopword-free, three characters or more."""
    return {
        word
        for word in re.findall(r"[a-z0-9][a-z0-9_-]{2,}", (text or "").lower())
        if word not in _STOPWORDS
    }


def pack_verify_clause(body: str) -> str | None:
    """The issue's own ``Verify:`` clause as TEXT, for the context pack.

    Delegates to the envelope producer (#793) for the same reason as
    :func:`extract_verify_command`: the clause the pack SHOWS, the clause the
    envelope CARRIES and the clause the loop RUNS are three views of one rule, and
    three readers would be three chances to disagree. Here the clause is only ever
    *told* to the executor, so it is read tolerantly (``**Verify:** ...`` plus
    prose still yields its clause).
    """
    return spawn.sources.verify_text(body)


def snapshot_issue(issue: int, path: Path | str | None = None) -> dict | None:
    """One issue's entry from the committed board snapshot; None when unusable.

    Reads the snapshot rather than GitHub, so the dispatch path stays
    offline-safe. An unreadable, torn or malformed snapshot is *cannot assess*: it
    returns None, and the caller turns that into a named warning instead of a
    crash or a silently empty pack (#220).
    """
    target = Path(path) if path is not None else BOARD_SNAPSHOT
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    entries = data.get("issues") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return None
    for entry in entries:
        if isinstance(entry, dict) and entry.get("number") == issue:
            return entry
    return None


def read_ledger(path: Path | str | None = None) -> list[dict]:
    """Every parseable record in the lessons ledger; a torn line is skipped."""
    target = Path(path) if path is not None else LESSONS_LEDGER
    try:
        raw = target.read_text(encoding="utf-8")
    except OSError:
        return []
    records: list[dict] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _lesson_text(record: dict) -> str:
    """The human-readable part of any ledger record kind (lesson, incident, action)."""
    return " ".join(
        str(record.get(field, "") or "") for field in ("title", "summary", "action", "class")
    ).strip()


def relevant_lessons(
    issue: int,
    terms_text: str,
    records: list[dict] | None = None,
    limit: int = CONTEXT_LESSON_LIMIT,
) -> list[dict]:
    """Up to `limit` ledger records relevant to this issue, most relevant first.

    Relevance is deliberately mechanical and honest: a record that names this issue
    in its origin wins outright; otherwise it must share vocabulary with the issue's
    title and lane. A record that shares nothing is not context — it is padding — so
    it is dropped rather than filling the pack to a quota.
    """
    if limit <= 0:
        return []
    pool = read_ledger() if records is None else records
    terms = _keywords(terms_text)
    scored: list[tuple[int, str, dict]] = []
    for record in pool:
        if not isinstance(record, dict):
            continue
        origin = record.get("origin")
        ref = str(origin.get("ref", "")) if isinstance(origin, dict) else ""
        score = 100 if f"#{issue}" in ref else 0
        score += 10 * len(terms & _keywords(_lesson_text(record)))
        if score > 0:
            scored.append((score, str(record.get("id", "")), record))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [record for _, _, record in scored[:limit]]


def issue_context(
    issue: int,
    lane: str = "",
    snapshot_path: Path | str | None = None,
    ledger_path: Path | str | None = None,
    lesson_limit: int = CONTEXT_LESSON_LIMIT,
    body_reader: Callable[[int], str | None] | None = None,
) -> dict:
    """The context pack one dispatch carries: identity, acceptance, prior lessons.

    The title and body come from the board snapshot (offline) and the issue's own
    ``Verify:`` clause is extracted from that body. The snapshot records no body
    today — it carries title/state/edges — so when the snapshot has none the pack
    may fall back to `body_reader` (the loop passes the live board read it already
    performs for its gates). When neither yields a body the pack carries a NAMED
    WARNING: a missing body is reported, never silently rendered as empty (#220).
    """
    target = Path(snapshot_path) if snapshot_path is not None else BOARD_SNAPSHOT
    ledger = Path(ledger_path) if ledger_path is not None else LESSONS_LEDGER
    entry = snapshot_issue(issue, target)
    warnings: list[str] = []
    if entry is None:
        warnings.append(
            f"issue #{issue} is not in the board snapshot ({target}) — its title, body and "
            "acceptance criteria are NOT in this pack; refresh the board snapshot and re-dispatch"
        )
    title = str(entry.get("title", "") or "").strip() if entry else ""
    if entry is not None and not title:
        warnings.append(f"issue #{issue} title is missing from the board snapshot ({target})")
    body = str(entry.get("body", "") or "").strip() if entry else ""
    if body:
        body_source = "board-snapshot"
    elif body_reader is not None:
        body = (body_reader(issue) or "").strip()
        body_source = "live-board" if body else "unavailable"
    else:
        body_source = "unavailable"
    if not body:
        warnings.append(
            f"issue #{issue} body is missing — absent from the board snapshot ({target}) and no "
            "live read was available, so the acceptance criteria are NOT in this pack"
        )
    verify = pack_verify_clause(body) if body else None
    lessons = relevant_lessons(issue, f"{title} {lane}", read_ledger(ledger), lesson_limit)
    return {
        "issue": issue,
        "lane": lane,
        "title": title,
        "body": body,
        "verify": verify,
        "lessons": [
            {
                "id": str(record.get("id", "")),
                "kind": str(record.get("kind", "")),
                "class": str(record.get("class", "")),
                "text": _lesson_text(record) or str(record.get("id", "")),
            }
            for record in lessons
        ],
        "warnings": warnings,
        "sources": {
            "snapshot": str(target),
            "ledger": str(ledger),
            "title": "board-snapshot" if title else "unavailable",
            "body": body_source,
            "verify": "issue-body" if verify else "unavailable",
            "lessons": "ledger" if lessons else "ledger (none matched)",
        },
    }


def render_context_pack(pack: dict) -> str:
    """The pack as the text the executor reads — a warning is never omitted."""
    lines = [f"CONTEXT PACK — issue #{pack.get('issue')} (assembled offline by the sister):"]
    lines.append(f"Title: {pack.get('title') or '(unavailable)'}")
    lines.append(f"Lane: {pack.get('lane') or 'unassigned'}")
    if pack.get("body"):
        lines.append(f"Issue body (acceptance criteria):\n{pack['body']}")
    if pack.get("verify"):
        lines.append(f"This issue's own Verify: `{pack['verify']}` — run it before claiming done.")
    lessons = pack.get("lessons") or []
    if lessons:
        lines.append(f"Prior lessons already paid for ({len(lessons)}), most relevant first:")
        for lesson in lessons:
            label = ", ".join(part for part in (lesson.get("kind"), lesson.get("class")) if part)
            lines.append(f"  - {lesson.get('id')} ({label or 'lesson'}): {lesson.get('text')}")
    else:
        lines.append("Prior lessons: none matched this issue's title and lane.")
    for warning in pack.get("warnings") or []:
        lines.append(f"WARNING: {warning}")
    return "\n".join(lines) + "\n\n"


def context_summary(pack: dict) -> str:
    """A one-line digest of the pack for the run record (the full pack rides the marker)."""
    return (
        f"context-pack issue=#{pack.get('issue')} "
        f"title={'yes' if pack.get('title') else 'missing'} "
        f"body={'yes' if pack.get('body') else 'missing'} "
        f"verify={'yes' if pack.get('verify') else 'no'} "
        f"lessons={len(pack.get('lessons') or [])} "
        f"warnings={len(pack.get('warnings') or [])}"
    )


def live_issue_body(issue: int) -> str | None:
    """The issue's body from the live board — the pack's fallback, never its default."""
    return gh_issue_field(issue, ".body")


#: Push-on-commit (issue #740, dispatch half): a lane that commits and is never
#: pushed is how #708's 31-lane, 46-commit stranding happened — the loop's
#: success path required the gate to pass first, and a gate that never ran (or
#: never passed) meant the push step, which lived AFTER the gate, never ran
#: either. The fix pushes immediately after the runner exits, BEFORE gating, so
#: the branch survives on the remote regardless of what the gate later decides.
PUSH_STRANDED = "stranded"
PUSH_OK = "pushed"
PUSH_SKIPPED = "skipped"


def push_lane_branch(
    branch: str | None, worktree: Path | None, timeout: float, directive_id: str = ""
) -> tuple[str, str]:
    """Push ``branch`` from ``worktree`` to its remote right after the run.

    Returns ``(outcome, detail)`` with outcome one of ``PUSH_OK``,
    ``PUSH_SKIPPED`` (no isolated lane / no branch — nothing to push) or
    ``PUSH_STRANDED`` (a push that was owed and failed). ``PUSH_STRANDED`` is
    NEVER silent: the caller names it, by directive, in the run record.
    """
    if not branch or worktree is None:
        return PUSH_SKIPPED, "no isolated lane branch to push"
    cwd = str(worktree)
    try:
        done = subprocess.run(
            ["git", "push", "--set-upstream", "origin", branch],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return PUSH_STRANDED, f"stranded: `git push origin {branch}` timed out after {timeout}s"
    except (OSError, subprocess.SubprocessError) as exc:
        return PUSH_STRANDED, f"stranded: `git push origin {branch}` could not run ({exc})"
    if done.returncode == 0:
        return PUSH_OK, f"pushed {branch} to origin"
    tail = ((done.stdout or "") + (done.stderr or "")).strip()[-300:]
    return PUSH_STRANDED, f"stranded: `git push origin {branch}` rc={done.returncode}: {tail or 'no output'}"


def run_gate(command: str, cwd: str, timeout: float) -> tuple[str, str]:
    """Run one gate the loop owns; its exit code — not the prose — is the signal.

    Returns ``(outcome, detail)`` with the outcome in the honesty vocabulary. A
    gate that ran out of time, or could not run at all, attested nothing: it is
    CANNOT-ASSESS, so it can neither pass the run nor be escalated as a failure.
    """
    try:
        done = subprocess.run(
            ["bash", "-lc", command], cwd=cwd, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return GATE_CANNOT_ASSESS, (
            f"`{command}` timed out after {timeout}s — CANNOT-ASSESS: a gate that ran out of time "
            "attested nothing, so this is neither a pass nor a failure of the work"
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return GATE_CANNOT_ASSESS, f"`{command}` could not run ({exc}) — CANNOT-ASSESS"
    tail = ((done.stdout or "") + (done.stderr or "")).strip()[-300:]
    if done.returncode == 0:
        return GATE_OK, f"`{command}` rc=0: {tail or 'no output'}"
    return GATE_NOT_OK, f"`{command}` rc={done.returncode}: {tail or 'no output'}"


def gate_evidence(issue: int, worktree: Path | None, timeout: float) -> tuple[str, str]:
    """Run the gates the loop checks itself: the issue's Verify: and ``make verify``.

    The runner's own text is never consulted here — a run that only *says* it
    verified the work cannot make either gate exit 0. The aggregate is fail-closed
    exactly as ``guardrails/honesty`` aggregates: any NOT-OK makes the answer
    NOT-OK; otherwise any CANNOT-ASSESS keeps it from reading OK. So a timed-out
    gate of record is reported as CANNOT-ASSESS, and the run is never ``done``.
    """
    cwd = str(worktree) if worktree is not None else str(ROOT)
    declared = issue_verify_command(issue)
    commands = [declared] if declared else []
    if GATE_OF_RECORD not in commands:
        commands.append(GATE_OF_RECORD)
    pieces = [f"issue Verify: `{declared}`" if declared else "issue declares no runnable Verify: command"]
    outcomes = []
    for command in commands:
        outcome, detail = run_gate(command, cwd, timeout)
        outcomes.append(outcome)
        pieces.append(detail)
    if GATE_NOT_OK in outcomes:
        return GATE_NOT_OK, " | ".join(pieces)
    if GATE_CANNOT_ASSESS in outcomes:
        return GATE_CANNOT_ASSESS, " | ".join(pieces)
    return GATE_OK, " | ".join(pieces)


def escalation_severity(rc: int, gate_outcome: str) -> str:
    """How loudly a failed run is escalated: ``critical`` only for a real failure.

    A gate the loop could not assess — a timeout, an unrunnable gate — is
    CANNOT-ASSESS and is escalated at ``warn``: at ``critical`` it read as a gate
    failure and the directive was re-dispatched every cycle (#733). The run is
    still not ``done``; only the alarm changes.
    """
    if rc != 0 or gate_outcome == GATE_NOT_OK:
        return "critical"
    return "warn"


def landed_evidence(issue: int) -> tuple[bool, str]:
    """The real board state: #issue is closed, so the work actually landed.

    GitHub reports its canonical casing (``OPEN``/``CLOSED``), so the state is
    lower-cased before comparison — the trap fixed fleet-wide by #290, where an
    uppercase ``CLOSED`` read as "not landed" and a landed change was misread.
    """
    state = gh_issue_field(issue, ".state")
    if state is None:
        return False, f"issue #{issue} state unavailable (gh could not read the board)"
    normalised = state.strip().lower()
    return normalised == "closed", f"issue #{issue} is {normalised}"


def verdict(rc: int, output: str, gate_ok: bool, landed: bool) -> tuple[str, str]:
    """The run's status — ``done`` only on evidence the loop established itself.

    ``done`` requires the runner to have exited 0 *and* the loop's own gates to
    have passed *and* the issue to be closed. The model's prose is carried as a
    labelled hint, never as the basis: confident prose with no work is not a
    success, and a run that merely quotes ``REFUSED`` is not a failure (#279).
    """
    verified = rc == 0 and gate_ok and landed
    hint = "refusal language present" if looks_refused(output) else "none"
    return ("done" if verified else "failed"), hint


HEARTBEAT = runtime.FLEET_DIR / "sister.heartbeat.json"
WORKTREE_ROOT = Path(os.environ.get("AO_WORKTREE_ROOT", str(Path.home() / "ao-worktrees")))
# Run registry: who is tracking which directive. Without it a claim's holder is
# just a string — indistinguishable from an agent that died mid-run, which is how
# a directive got consumed as "already in-flight" while nothing was running.
RUNS = runtime.FLEET_DIR / "runs"
REPORTED = runtime.FLEET_DIR / "reported"

#: The bounded worker pool: this many directives run concurrently, env-overridable
#: so a principal can widen or narrow the fleet without a code change.
DEFAULT_POOL_SIZE = 10

#: The pinned focus (governance/dispatch/focus.py owns its schema). Read for one
#: field, ``max_agents`` — the board's word for this epic's fan-out default.
FOCUS_PATH = ROOT / ".board" / "focus.json"


def pool_size() -> int:
    """Up to this many executors run at once; ``FLEET_SISTER_POOL`` overrides it."""
    raw = os.environ.get("FLEET_SISTER_POOL", str(DEFAULT_POOL_SIZE))
    try:
        size = int(raw)
    except (TypeError, ValueError):
        size = DEFAULT_POOL_SIZE
    return max(1, size)


def capacity_lanes() -> list[capacity.Lane]:
    """The in-flight lanes, with the file set each one declared when it was admitted."""
    lanes: list[capacity.Lane] = []
    for slot in active_run_slots():
        lane = slot.get("capacity_lane")
        if isinstance(lane, capacity.Lane):
            lanes.append(lane)
        else:
            # A slot registered before this build cannot name its files; it is
            # reported as unattributable rather than assumed disjoint.
            lanes.append(
                capacity.Lane(
                    id=str(slot.get("directive") or "unknown"),
                    issue=slot.get("issue") if isinstance(slot.get("issue"), int) else None,
                    files=None,
                )
            )
    return lanes


def resolve_capacity(directive: dict) -> tuple[capacity.Capacity, capacity.Lane]:
    """Resolve the fan-out ceiling for ``directive`` against the live pool (#718).

    Re-resolved EVERY cycle, not once at startup, because two of the three bounds
    move: the ready-lane set changes as lanes land, and the RAM / ``/tmp``
    headroom moves with everything else sharing the box. The incoming lane is
    part of the ready set — otherwise it would be measured against a ceiling that
    does not include the work it is about to add.
    """
    lane = capacity.Lane.from_directive(directive)
    try:
        focus_max = capacity.read_focus_max_agents(FOCUS_PATH)
    except capacity.CapacityConfigError as exc:
        print(f"[terminal] focus unreadable for the fan-out default: {exc}", file=sys.stderr, flush=True)
        focus_max = None
    resolved = capacity.resolve_capacity(
        ready=[*capacity_lanes(), lane],
        focus_max_agents=focus_max,
        pool_size=pool_size(),
    )
    return resolved, lane


#: Live per-directive run slots, keyed by directive id. The old single ``IN_FLIGHT``
#: dict could track exactly one run; a pool of N concurrent executors needs one
#: slot per child, each carrying its own issue, agent id, child process, release
#: flag and heartbeat thread. Guarded by ``RUNS_LOCK``: the loop thread, each
#: worker thread and the signal handler all touch it.
RUNS_LOCK = threading.Lock()
IN_FLIGHT: dict[str, dict[str, object]] = {}
#: Serialises telemetry appends so N workers cannot interleave a JSON line.
RECORD_LOCK = threading.Lock()


def register_run(directive_id: str, issue: int, agent_id: str) -> dict:
    """Open a per-directive slot; the worker and the stop handler find it here."""
    slot: dict[str, object] = {
        "issue": issue,
        "agent_id": agent_id,
        "directive": directive_id,
        "child": None,
        "released": False,
        "stopped": False,
        "beater": None,
    }
    with RUNS_LOCK:
        IN_FLIGHT[directive_id] = slot
    return slot


def unregister_run(directive_id: str) -> None:
    with RUNS_LOCK:
        IN_FLIGHT.pop(directive_id, None)


def stream_run_event(directive_id: str, text: str) -> None:
    """Append one loop-owned event to a run's live log stream (issue #367).

    The executor's stdout rides the pump (`_pump_child_stdout`); this carries
    the loop's own events — dispatch resolved, claim taken, lane provisioned,
    steer delivered, verdict — so `channel follow` shows the whole run, not
    only the child's half of it.
    """
    try:
        channel.append_directive_log(directive_id, text, source="sister")
    except (OSError, ValueError):
        pass


def record_steer(directive_id: str, steer: dict) -> None:
    """Stamp the delivered steer into the run marker — proof the run honoured it.

    The marker `.fleet/runs/<directive>.json` already travels with the run
    (issue #304's beat, #220's context pack); the `steered` list makes the
    mid-run delivery auditable from outside the loop instead of only visible
    in the log stream.
    """
    target = RUNS / f"{directive_id}.json"
    try:
        record = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    steered = record.get("steered")
    if not isinstance(steered, list):
        steered = []
    steered.append(
        {
            "id": str(steer.get("id") or ""),
            "ts": str(steer.get("ts") or _now()),
            "body": str(steer.get("body") or "")[:400],
        }
    )
    record["steered"] = steered
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(record) + "\n", encoding="utf-8")
    tmp.replace(target)


def deliver_pending_steers() -> list[str]:
    """Deliver every queued steer to its live run; returns the delivered ids.

    Called by the loop every cycle (issue #367): the steer is injected into the
    running child's stdin (the mid-run hint), echoed into the run's live log
    stream, stamped into the run marker, and consumed from the queue — so the
    director steers a stuck run without killing or re-dispatching it. A steer
    whose run is not live yet stays pending (an early steer is not lost); one
    whose run already finished is consumed, because a hint for a finished run
    must never steer the NEXT run of the same directive.
    """
    delivered: list[str] = []
    for path in channel.pending_steers():
        try:
            steer = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            channel.consume_steer(path)
            continue
        if not isinstance(steer, dict):
            channel.consume_steer(path)
            continue
        directive_id = str(steer.get("correlation_id") or "")
        with RUNS_LOCK:
            slot = IN_FLIGHT.get(directive_id)
            child = slot.get("child") if slot is not None else None
        if child is None:
            continue
        try:
            running = child.poll() is None
        except (OSError, AttributeError):
            running = getattr(child, "returncode", None) is None
        if not running:
            channel.consume_steer(path)
            continue
        hint = " ".join(str(steer.get("body") or "").split())
        if hint:
            try:
                stdin = getattr(child, "stdin", None)
                if stdin is not None:
                    stdin.write(f"\n[STEER from the brain] {hint}\n")
                    stdin.flush()
            except (OSError, ValueError):
                pass
        try:
            channel.append_directive_log(directive_id, f"STEER delivered: {hint}", source="steer")
        except (OSError, ValueError):
            pass
        record_steer(directive_id, steer)
        channel.consume_steer(path)
        delivered.append(directive_id)
    return delivered


def active_run_slots() -> list[dict]:
    """A snapshot of every in-flight run slot, so N runs are visible at once."""
    with RUNS_LOCK:
        return list(IN_FLIGHT.values())


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def held_action(holder: str | None, agent_id: str, state: str) -> str:
    """What to do when an issue is already claimed by someone.

    * ``in-flight`` — a tracked run owns it: report it and leave the directive
      PENDING. Consuming it would erase the only record that the work was
      ordered, and nobody would ever report the result.
    * ``self-heal`` — our own run died mid-task (a restart): reap the dead claim
      and dispatch in the same cycle, because the work was never reported.
    * ``orphaned`` — someone else's untracked claim: escalate so the director
      decides; never consume, never steal.
    """
    if state == "live":
        return "in-flight"
    if holder is not None and holder == agent_id:
        return "self-heal"
    return "orphaned"


def handled(directive_id: str) -> None:
    """Take a control message off the queue before acting on it.

    A process control has no result to report, but it must still leave the inbox:
    a `kill` that acted and stayed pending kills the *next* loop too, and a
    `refresh` that stayed pending re-execs forever.
    """
    subprocess.run(
        ["python3", CHANNEL, "consume", "--id", directive_id], cwd=ROOT, capture_output=True, text=True
    )


def work_held(directive: dict, is_paused: bool) -> bool:
    """Pause holds WORK, never controls.

    A paused loop that also stopped reading its inbox could not be resumed: the
    principal's `resume` was delivered and sat unread until the flag was cleared by
    hand. Controls are therefore always processed; only dispatch waits.
    """
    return bool(is_paused and not directive.get("control"))


def agent_id_for(directive_id: str) -> str:
    """The agent id for a directive: one lane, one name, derived from the order."""
    return f"subagent-{directive_id[:8]}"


def mark_run(directive_id: str, issue: int, agent_id: str, context: dict | None = None) -> None:
    """Record that this loop is tracking a run for a directive.

    Written atomically (tmp + rename): readers outside the loop (the JSON gate,
    the director, a principal) can otherwise catch a torn file mid-write — which is
    exactly how it was caught, by `json-lint` reading a half-written registry.

    The marker is per-directive, so N concurrent runs produce N markers. ``pid``
    stays the loop's — that is what prune/watchdog read for liveness — while
    ``child_pid`` and ``ts`` are refreshed by the run's own beater (see
    ``refresh_run``) so the marker doubles as that child's heartbeat.

    The run's CONTEXT PACK rides here too (#220): a reviewer can read
    `.fleet/runs/<directive>.json` and see exactly what the executor was told —
    the issue's title and acceptance text, its lane, its ``Verify:`` clause and
    the prior lessons, warnings included.
    """
    RUNS.mkdir(parents=True, exist_ok=True)
    target = RUNS / f"{directive_id}.json"
    tmp = target.with_suffix(".tmp")
    payload = {
        "issue": issue,
        "agent": agent_id,
        "pid": os.getpid(),
        "child_pid": None,
        "started_at": _now(),
        "ts": _now(),
    }
    if context is not None:
        payload["context_pack"] = context
    tmp.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    tmp.replace(target)


def refresh_run(directive_id: str, child_pid: int | None = None) -> None:
    """Refresh one run marker's heartbeat; N live runs read as N live beats.

    The single ``sister.heartbeat.json`` can only name one child, so each run's
    own marker carries its liveness instead: the beater advances ``ts`` and
    records the executor pid while the child works. ``pid`` is left untouched,
    so the prune/watchdog liveness semantics are unchanged.
    """
    target = RUNS / f"{directive_id}.json"
    try:
        record = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    record["ts"] = _now()
    if child_pid is not None:
        record["child_pid"] = child_pid
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(record) + "\n", encoding="utf-8")
    tmp.replace(target)


def record_run(
    directive_id: str,
    issue: int,
    agent_id: str,
    status: str,
    started_at: str,
    finished_at: str | None = None,
    detail: str = "",
    dispatch: dict | None = None,
) -> None:
    """Append one per-run telemetry record; a bad append must not kill the loop.

    Telemetry is an observability signal, not the run itself — if the log write
    fails (disk full, bad permissions), the run outcome still gets reported over
    the channel; only the extra record is lost.

    The FinOps block the run actually dispatched at rides here too (#218) so the
    claim is measurable rather than asserted: `tier`, `thinking` and `runner` are
    the very field names `fleet/summary.py` (#219/#234) already aggregates, and
    `model` names the model the tier selected. `runslog.build_record` fixes the
    schema's REQUIRED fields; these are additive, so an older reader is unaffected.
    """
    try:
        with RECORD_LOCK:
            record = runslog.build_record(
                run_id=directive_id,
                issue=str(issue),
                agent=agent_id,
                status=status,
                started_at=started_at,
                finished_at=finished_at,
                detail=detail,
            )
            if isinstance(dispatch, dict):
                record["tier"] = dispatch.get("tier")
                record["thinking"] = dispatch.get("thinking")
                record["model"] = dispatch.get("model")
                record["runner"] = dispatch.get("runner")
            runslog.append_record(runslog.RUNS_LOG, record)
    except (runslog.TelemetryError, OSError) as exc:
        print(f"[terminal] telemetry record for {directive_id} rejected: {exc}", file=sys.stderr, flush=True)


def clear_run(directive_id: str) -> None:
    try:
        (RUNS / f"{directive_id}.json").unlink()
    except OSError:
        pass


def run_state(directive_id: str) -> str:
    """'live' (a loop is tracking it), 'orphaned' (nobody is), or 'none'."""
    marker = RUNS / f"{directive_id}.json"
    try:
        record = json.loads(marker.read_text(encoding="utf-8"))
        pid = int(record.get("pid", 0))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return "none"
    try:
        os.kill(pid, 0)
    except OSError:
        return "orphaned"
    return "live"


def report_once(
    directive_id: str, key: str, message_type: str, body: str, severity: str = "warn"
) -> bool:
    """Say something about a directive once, not once per watch cycle.

    A directive that is left pending is re-read every cycle; without this the
    loop would repeat the same report forever. ``severity`` is the escalation
    severity for a non-``result`` message (ignored for ``result``); the runaway
    guard's terminal notice raises it to ``critical`` (#723), because a retired
    directive is work that will never be done unless a principal acts.
    """
    REPORTED.mkdir(parents=True, exist_ok=True)
    path = REPORTED / f"{directive_id}.json"
    try:
        last = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        last = {}
    if last.get("key") == key:
        return False
    path.write_text(json.dumps({"key": key, "at": _now()}) + "\n", encoding="utf-8")
    if message_type == "result":
        command = ["python3", CHANNEL, "report", "--from", "sister", "--correlation", directive_id,
                   "--type", "result", "--body", body]
    else:
        command = ["python3", CHANNEL, "escalate", "--from", "sister", "--correlation", directive_id,
                   "--severity", severity, "--body", body]
    subprocess.run(command, cwd=ROOT)
    return True


def clear_reported(directive_id: str) -> None:
    try:
        (REPORTED / f"{directive_id}.json").unlink()
    except OSError:
        pass


# --- the runaway guard (issue #723) ------------------------------------------
#
# Five paths in this loop leave a directive PENDING without ever reporting a
# result: a refused claim, a run that did not finish `done`, our own dead claim
# (self-heal), a hold by another loop (in-flight) and an untracked foreign claim
# (orphaned). Re-reading them every cycle for ever was the runaway: no attempt
# counter, no delay, no terminal state — and `report_once` deduped only the
# *report*, never the *attempt*.
#
# Every one of those paths now goes through `guard_retire`: it counts the
# attempt against the directive's ONE persisted counter, spaces the next attempt
# with the backoff `vendor/CMR/ops/retry.sh` declares, and on the K-th failure
# retires the order to `.fleet/dead-letter/`, where `channel watch` can never
# return it again.
#
# The state directory is derived from RUNS — beside the run markers — and never
# from `runtime.FLEET_DIR` directly: the fleet suite redirects `terminal.RUNS` to
# a tmp directory, so driving this loop in a test cannot write guard state into
# the live fleet's `.fleet/`.


def guard_base() -> Path:
    """The guard's root: beside the run markers, so test isolation carries over."""
    return RUNS.parent


def guard_attempt(directive_id: str, reason: str) -> "runaway.Attempt | None":
    """Count one failed attempt; None when the guard is misconfigured.

    A typo'd ``AO_RUNAWAY_ATTEMPTS``/``AO_RUNAWAY_BACKOFF`` raises inside the
    guard. The loop must not die on it — the order would be stranded with no
    report at all — so the misconfiguration is printed by name and the directive
    is left pending, which is the loud version of the pre-guard behaviour.
    """
    try:
        return runaway.record_attempt(directive_id, reason, base=guard_base())
    except runaway.RunawayConfigError as exc:
        print(f"[terminal] runaway guard misconfigured — {exc}", file=sys.stderr, flush=True)
        return None


def guard_retire(directive_id: str, issue: int, reason: str) -> bool:
    """Count the attempt, and retire the directive once its budget is exhausted.

    Returns True when the directive is now a dead letter: the caller must stop
    re-dispatching it and move on. False means "counted, still retryable" — the
    directive stays in the inbox and ``channel watch`` holds it until its
    backoff has elapsed, so nothing here sleeps and the loop keeps polling.
    """
    record = guard_attempt(directive_id, reason)
    if record is None:
        return False
    if not record.exhausted:
        report_once(
            directive_id,
            key=f"attempt:{record.attempts}",
            message_type="escalate",
            body=(
                f"#{issue} attempt {record.attempts}/{record.cap} failed — {reason}. The next "
                f"attempt is held until {record.next_attempt_at} (exponential backoff, cap "
                f"{runaway.BACKOFF_CAP_SECONDS}s); the directive stays pending meanwhile."
            ),
        )
        return False
    # The SAME retire path the operator/A2A `control:drop` verb uses (issue
    # #754): one implementation, two callers. Only `dropped_by` differs — the
    # automatic path names the guard, the verb names the sender — so the two
    # records cannot drift in shape.
    return drop_directive(
        directive_id,
        issue,
        reason,
        dropped_by="runaway-guard",
        report=(
            f"#{issue} DEAD-LETTERED after {record.attempts} attempt(s) (cap {record.cap}) — "
            f"{reason}."
        ),
    )


def guard_retire_terminal(directive_id: str, issue: int, reason: str, terminal_reason: str) -> bool:
    """Dead-letter a directive on its FIRST refusal — no attempt counted, no backoff.

    Issue #861: a refusal named terminal by :func:`runaway.terminal_classification`
    (a closed issue, a closed epic, an unowned unit) can never be cured by a
    retry, so it goes straight to the dead-letter store the SAME
    :func:`drop_directive` every other terminal path uses — one implementation,
    now three callers (the automatic budget path, the A2A ``control:drop`` verb,
    and this one), so the record shape cannot drift between them. This is the
    difference from :func:`guard_retire`: that function counts an attempt first
    and only retires once the budget (default K=5, ~450s of a held slot) is
    exhausted; this one retires on attempt zero, because the classification
    already answers the question the budget exists to discover by attrition.
    """
    return drop_directive(
        directive_id,
        issue,
        reason,
        dropped_by="runaway-guard-terminal",
        report=(
            f"#{issue} DEAD-LETTERED on first refusal — {terminal_reason} is terminal by definition "
            f"(#861): {reason}."
        ),
    )


def drop_directive(
    directive_id: str,
    issue: int | None,
    reason: str,
    *,
    dropped_by: str,
    report: str | None = None,
) -> bool:
    """Retire one directive to the dead-letter mailbox — the ONE implementation.

    Two callers need this and they must not diverge (issue #754, an acceptance
    criterion): the automatic path (:func:`guard_retire`, budget exhausted) and
    the A2A/operator ``control:drop`` verb (a peer says the order is dead). Both
    come here, so the durable record, the reason and the ``dropped_by`` label are
    produced by one function and the shape is identical by construction.

    Returns True when the order is now terminal. The caller is responsible for
    the *envelope* — this function retires the work; it does not consume the
    control message that asked for it.
    """
    target = runaway.dead_letter(
        directive_id, reason, base=guard_base(), dropped_by=dropped_by
    )
    print(
        f"[terminal] directive {directive_id} DEAD-LETTERED by {dropped_by} — {reason}",
        file=sys.stderr,
        flush=True,
    )
    stream_run_event(
        directive_id, f"DEAD-LETTERED by {dropped_by}: {reason}"
    )
    if report:
        report_once(
            directive_id,
            key=f"dead-letter:{dropped_by}:{reason}",
            message_type="escalate",
            severity="critical",
            body=(
                f"{report} The order was moved to {target} and will never be dispatched again. "
                f"Inspect it with `python3 fleet/runaway.py dead-letter --directive {directive_id}`, "
                f"then re-order it, or re-arm the budget with `python3 fleet/runaway.py rearm "
                f"--directive {directive_id}` once the cause is fixed."
            ),
        )
    return True


# --- the orphan handoff (issue #694) -----------------------------------------
#
# `held_action` returns `orphaned` when a claim is held by someone else and no
# live run is tracking it. Until #694 the loop answered that with an escalation
# and an attempt count — "escalating, left pending" — and the claim stayed
# wedged: the reconciler that OWNS orphan teardown (issue #304 — per-session
# heartbeat, TTL sweep, worktree/branch teardown, lock release) was never asked
# to do its job. A claim whose lane is already gone is precisely the wedge that
# worker exists to clear (`sweep._teardown`: no worktree -> forget-lane,
# release-claim, clear-heartbeat).
#
# So the orphan is HANDED OVER before the escalation:
#
#   * one sweep per orphan EPISODE per directive. A sweep on every poll would be
#     its own runaway — the thing the guard exists to bound — so the episode is
#     marked once, and the mark is cleared only when the handoff ended the
#     orphan, i.e. when a fresh episode would be a genuinely new one;
#   * the handoff DECIDES nothing. The reconciler keeps the claim when the orphan
#     carries unmerged work (`SHELVED_OUTCOME`: "keep the lane, keep the claim,
#     escalate"), and reclaims or releases only when that work is safe. The
#     loop's bounded escalation therefore still runs, unchanged, in that case;
#   * "the reconciler ended it" is read from the reconciler's own vocabulary,
#     never restated here.

ORPHAN_HANDOFFS = "orphan-handoffs"
RECONCILE_CLI = str(ROOT / "governance" / "reconcile" / "cli.py")
#: How long one handoff may take. The sweep is local (git plus the repo's own
#: CLIs) but the loop must not block on it: a timeout is a failure to hand over,
#: never a reason to skip the escalation that follows.
ORPHAN_HANDOFF_TIMEOUT = float(os.environ.get("AO_ORPHAN_HANDOFF_TIMEOUT", "120"))


def orphan_handoff_path(directive_id: str) -> Path:
    """Where one directive's orphan handoff is recorded.

    Beside the guard's state (``RUNS.parent``) and never ``runtime.FLEET_DIR``
    directly, for the same reason the guard does it: the fleet suite redirects
    ``terminal.RUNS`` to a tmp directory, so driving this loop in a test cannot
    write handoff state into the live fleet's ``.fleet/``.
    """
    return guard_base() / ORPHAN_HANDOFFS / f"{directive_id}.json"


def orphan_handoff_done(directive_id: str) -> dict | None:
    """The recorded handoff for this directive, or None when there is none."""
    try:
        return json.loads(orphan_handoff_path(directive_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def record_orphan_handoff(directive_id: str, record: dict) -> None:
    """Persist one handoff. A failure to write is printed, never raised: the
    loop's next act is either a re-dispatch or a bounded escalation, and losing
    the mark must not cost the directive its only report."""
    path = orphan_handoff_path(directive_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    except OSError as exc:
        print(
            f"[terminal] could not record the orphan handoff for {directive_id}: {exc}",
            file=sys.stderr,
            flush=True,
        )


def clear_orphan_handoff(directive_id: str) -> None:
    """Forget the episode mark, so a NEW orphan episode may hand over again."""
    try:
        orphan_handoff_path(directive_id).unlink()
    except OSError:
        pass


def hand_orphan_to_reconciler(
    directive_id: str, issue: int, holder: str, *, timeout: float | None = None
) -> tuple[bool, str]:
    """Ask the reconciler to end one orphaned claim: ``(ended, detail)``.

    ``ended`` is True only when the sweep's own report says this issue's session
    was reclaimed or parked — read from ``governance.reconcile``'s vocabulary.
    False means either that the reconciler ran and deliberately kept the claim
    (unmerged work: shelved) or that it could not run; the caller escalates in
    both cases, so a broken reconciler degrades to the pre-#694 behaviour rather
    than to silence.

    One call per episode: a mark is written on the first attempt, and every later
    call returns False without sweeping, so the handoff can never become the
    runaway that the guard exists to bound.
    """
    already = orphan_handoff_done(directive_id)
    if already is not None:
        return False, (
            f"already handed over once for this episode ({already.get('detail') or 'no detail'})"
            " — the bound is one handoff per episode"
        )
    budget = ORPHAN_HANDOFF_TIMEOUT if timeout is None else timeout
    try:
        result = subprocess.run(
            ["python3", RECONCILE_CLI, "sweep", "--apply", "--json"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=budget,
        )
    except subprocess.TimeoutExpired:
        detail = f"the reconciler did not answer within {budget:g}s"
        record_orphan_handoff(directive_id, {"ended": False, "detail": detail, "at": _now()})
        return False, detail
    except OSError as exc:
        detail = f"the reconciler could not be run: {exc}"
        record_orphan_handoff(directive_id, {"ended": False, "detail": detail, "at": _now()})
        return False, detail

    report = extract_json(result.stdout or "")
    actions = report.get("actions") if isinstance(report, dict) else None
    if not actions:
        detail = (
            f"the reconciler reported no action for #{issue} (rc={result.returncode})"
            f"{(result.stderr or '').strip() and ' — ' + (result.stderr or '').strip()[-160:]}"
        )
        record_orphan_handoff(directive_id, {"ended": False, "detail": detail, "at": _now()})
        return False, detail

    action = next(
        (a for a in actions if isinstance(a, dict) and a.get("issue") == issue), None
    )
    if action is None:
        detail = f"the sweep judged no session for #{issue} (rc={result.returncode})"
        record_orphan_handoff(directive_id, {"ended": False, "detail": detail, "at": _now()})
        return False, detail

    outcome = str(action.get("outcome") or "")
    ended = outcome in {RECONCILE_RECLAIMED, RECONCILE_PARKED}
    detail = (
        f"reconcile sweep: #{issue} {outcome or 'no outcome'} for session "
        f"{action.get('session_id') or 'unknown'} — {action.get('reason') or 'no reason given'}"
    )
    record_orphan_handoff(
        directive_id,
        {"ended": ended, "outcome": outcome, "holder": holder, "detail": detail, "at": _now()},
    )
    return ended, detail


def dead_letter_inventory() -> list[dict]:
    """Every retired directive's normalised record, newest first (the list verb).

    Reads through ``runaway.record_shape`` rather than the raw files so the
    answer a principal gets from the verb is the same shape the store writes,
    whether the drop came from the guard or from a peer's ``control:drop``.
    """
    state = runaway.inventory(guard_base())
    records = [runaway.record_shape(guard_base(), name) for name in state["dead_letters"]]
    return sorted(records, key=lambda record: str(record.get("ts") or ""), reverse=True)


# --- the board trigger (issue #727) ------------------------------------------
#
# `claims` refuses a claim against a stale snapshot with `snapshot-stale` and
# prints the remedy ("refresh first: ... snapshot --from-github") — and nothing
# ran the remedy, so the refusal re-fired every cycle: a fail-closed refusal is
# only half a control. The trigger below is the other half — ONE bounded
# refresh, and when freshness does not return the directive is PARKED (held by
# `channel watch` until the board is fresh again) rather than re-dispatched.
#
# A PARK is not a dead letter: the dead letter retires an order for ever, a park
# keeps it as the principal's pending work. The two compose — the park holds the
# directive and the runaway guard still counts the attempt, so neither the park
# nor the budget can be bypassed.


def board_trigger(
    directive_id: str,
    issue: int,
    *,
    runner=None,
    snapshot_path: str | None = None,
    threshold_minutes: int | None = None,
) -> object:
    """Refresh the board ONCE on a stale-snapshot refusal, else PARK the directive.

    ``runner`` is injectable so the contract is provable offline (the real path
    shells out to ``gh``, which the gate cannot reach). A refused network is a
    first-class outcome — ``refresh`` reports it and the trigger parks — never an
    unhandled crash. The transition is reported ONCE, with the snapshot's
    ``generated_at`` and the threshold it tripped, so the principal reads the
    board's age instead of a refusal repeated every cycle.

    Returns the trigger's :class:`StaleTrigger`; the annotation is ``object``
    because the type lives in the lazily-imported module above. A checkout that
    does not ship the trigger has nothing to refresh for and reports none.
    """
    board = board_snapshot()
    if board is None:
        return None
    trigger = board.refresh_or_park(
        directive_id,
        snapshot_path=snapshot_path or board.DEFAULT_PATH,
        base=guard_base(),
        repo=BOARD_REPO,
        runner=runner,
        threshold_minutes=(
            board.DEFAULT_STALENESS_MINUTES
            if threshold_minutes is None
            else threshold_minutes
        ),
    )
    print(
        f"[terminal] #{issue} board trigger: {trigger.action} — {trigger.reason}",
        file=sys.stderr,
        flush=True,
    )
    stream_run_event(directive_id, f"BOARD-TRIGGER {trigger.action}: {trigger.reason}")
    report_once(
        directive_id,
        key=f"board-trigger:{trigger.action}",
        message_type="escalate",
        body=(
            f"#{issue} board snapshot generated_at={trigger.generated_at or '<unreadable>'} "
            f"(age {trigger.age_minutes:.1f}m > threshold {trigger.threshold_minutes}m) — "
            f"board trigger {trigger.action}: {trigger.reason}"
        ),
    )
    return trigger


# --- controls (the principal's levers, relayed by the director) -------------------
#
# The loop is the only place these can be honoured: it owns the run, the queue
# cursor and the process. `control.py` sends them; this decides what they mean.
PAUSED = runtime.FLEET_DIR / "paused"
STOPPING = runtime.FLEET_DIR / "stopping"
# How often a run refreshes its beat. Short enough that a stuck run still looks
# alive, long enough that the file is not rewritten constantly.
HEARTBEAT_INTERVAL_SECONDS = 15.0


def paused() -> bool:
    return PAUSED.exists()


def stopping() -> bool:
    return STOPPING.exists()


def set_flag(path: Path, on: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if on:
        path.write_text(_now() + "\n", encoding="utf-8")
    else:
        try:
            path.unlink()
        except OSError:
            pass


#: The preflight's once-per-condition escalation (#733): a loop that re-reads the
#: inbox every cycle must say this once, not once per directive per cycle.
RUNNER_PREFLIGHT_ID = "runner-preflight"
#: The CAPABILITY preflight's escalation (#841), keyed separately from the one above
#: because it is a different failure with a different remedy: `runner-preflight` means
#: the executable is missing (install it), this one means the executable cannot honour
#: the model (wire its environment, or select another profile). Collapsing them would
#: make one remedy's message wrong for the other's failure.
RUNNER_CAPABILITY_ID = "runner-capability"
#: Records the queue hold THIS loop took for an unresolvable runner, together with
#: the stamp it wrote into `.fleet/paused`, so the hold can be released when the
#: runner resolves — and never releases a principal's pause.
RUNNER_HOLD = runtime.FLEET_DIR / "runner-hold.json"


def read_runner_hold() -> dict:
    try:
        record = json.loads(RUNNER_HOLD.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return record if isinstance(record, dict) else {}


def _flag_stamp() -> str:
    try:
        return PAUSED.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def hold_queue_for_runner(detail: str) -> bool:
    """Hold the queue because no runner can be spawned; True when first held.

    The hold IS `.fleet/paused`, deliberately: `work_held` then holds exactly the
    WORK while every control is still read, because a loop that stopped reading its
    inbox could not be resumed (the measured failure `work_held` documents). We
    record the stamp we wrote, so the hold is *ours* — a principal's pause is never
    released by the preflight, and ours is released the moment the runner resolves.
    """
    record = read_runner_hold()
    stamp = str(record.get("stamp") or "")
    if stamp and paused() and _flag_stamp() == stamp:
        return False
    if paused():
        # Somebody else paused: the work is already held; change nothing.
        return False
    set_flag(PAUSED, True)
    stamp = f"runner-preflight {_now()} {os.getpid()}"
    PAUSED.write_text(stamp + "\n", encoding="utf-8")
    RUNNER_HOLD.parent.mkdir(parents=True, exist_ok=True)
    tmp = RUNNER_HOLD.with_suffix(".tmp")
    tmp.write_text(json.dumps({"stamp": stamp, "detail": detail}) + "\n", encoding="utf-8")
    tmp.replace(RUNNER_HOLD)
    return True


def release_runner_hold() -> str:
    """Clear a queue hold this loop took; never a principal's pause.

    Returns the line to print when one was released, or "" when there was nothing
    of ours to release (a principal who ran `resume` leaves no flag; a pause they
    set themselves carries their own stamp and is left alone).
    """
    record = read_runner_hold()
    if not record:
        return ""
    stamp = str(record.get("stamp") or "")
    try:
        RUNNER_HOLD.unlink()
    except OSError:
        pass
    if not stamp or not paused() or _flag_stamp() != stamp:
        return ""
    set_flag(PAUSED, False)
    return "runner resolvable again — queue hold released"


def apply_control(action: str, directive: dict, agent_id: str) -> str:
    """Translate a control action into a verdict the loop acts on.

    * ``continue`` — handled here; keep going.
    * ``dispatch-override`` — a principal override: skip the held-check (the
      caller already reaped the holder) and dispatch this directive.
    * ``drop`` — retire the named directive to the dead-letter mailbox (#754).
    * ``dead-letter`` — list the mailbox (a read, handled here).
    * ``stop`` / ``kill`` / ``halt`` / ``restart`` / ``refresh`` — act on the
      process.
    """
    if action == "pause":
        set_flag(PAUSED, True)
        return "continue"
    if action == "resume":
        set_flag(PAUSED, False)
        return "continue"
    if action == "stop":
        # Graceful: finish the run in flight, then exit. Never mid-run.
        set_flag(STOPPING, True)
        return "continue"
    if action == "kill":
        # Hard: take the run down with us, release its claim, escalate.
        stop_and_release("control:kill")
        return "kill"
    if action == "status":
        return "continue"
    if action == "override":
        return "dispatch-override"
    if action == "drop":
        return "drop"
    if action == "dead-letter":
        return "list-dead-letter"
    if action in ("refresh", "restart", "halt"):
        return action
    # Anything else cannot be handled by this build; the caller escalates ONCE and
    # consumes it. Measured: a control the loop did not understand stayed in the
    # inbox and was re-read every cycle, escalating hundreds of times a second.
    return "unknown"


def control_target_directive_id(directive: dict) -> str | None:
    """The directive a control acts ON, which is not the control's own id (#754).

    `control:drop` says "retire THAT order"; the message carrying the order has
    its own id, and confusing the two would dead-letter the control itself and
    leave the wedged directive in place — a silent no-op that looks like success.
    The target travels in ``task.directive`` (the same place `override` keeps its
    target issue), and it must be a safe mailbox name because it becomes a
    filename.
    """
    task = directive.get("task") or {}
    target = task.get("directive")
    if not isinstance(target, str) or not target.strip():
        return None
    if not channel.DIRECTIVE_ID_RE.fullmatch(target.strip()):
        return None
    return target.strip()


def write_heartbeat(
    state: str,
    *,
    started_at: str,
    commit: str,
    issue: int | None = None,
    agent: str | None = None,
    child_pid: int | None = None,
    runs: int | None = None,
) -> None:
    """Publish liveness *and* the commit this process is running.

    A loop left running stale code made a healthy fleet look broken: the status
    surface could not tell "not running" from "running the pre-fix build". A long
    run made it look broken too — the beat was written only *before* the child
    started, so `state: working` with a stale timestamp read as a dead loop. The
    run's identity and child pid are part of the beat now, and the run beats
    itself (see `start_beating`).
    """
    entry = {
        "pid": os.getpid(),
        "state": state,
        "started_at": started_at,
        "commit": commit,
        # What this build can READ on the envelope (issue #777). An emitter asks
        # the recipient's beat before choosing a dialect, so a rung that restarts
        # on this build switches the fleet's traffic to the current role names by
        # declaring it here — the deprecation window's terminus is this field.
        channel.ENVELOPE_SCHEMA_BEAT_KEY: channel.SCHEMA_VERSION_CURRENT,
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if issue is not None:
        entry["issue"] = issue
    if agent is not None:
        entry["agent"] = agent
    if child_pid is not None:
        entry["child_pid"] = child_pid
    if runs is not None:
        entry["runs"] = runs
    HEARTBEAT.parent.mkdir(parents=True, exist_ok=True)
    tmp = HEARTBEAT.with_suffix(".tmp")
    tmp.write_text(json.dumps(entry) + "\n", encoding="utf-8")
    tmp.replace(HEARTBEAT)


class RunBeater:
    """Keeps one run's marker fresh while its child works; `stop()` is synchronous.

    Each run in the pool has its own beater, keyed by directive id, so N
    concurrent children each advance their own marker (`refresh_run`) — the
    watchdog/monitor sees N live runs, not one shared heartbeat. `stop()` joins
    the thread rather than merely setting a flag: a beater that outlives its
    owner wrote the *owner's* idea of the world — measured, a test beater left
    running wrote `commit: abc1234` and a pytest pid into the live heartbeat when
    monkeypatch restored the real path.
    """

    def __init__(self, directive_id: str, interval: float, slot: dict | None = None) -> None:
        self._stop = threading.Event()
        self._slot = slot or {}
        self._directive_id = directive_id
        self._thread = threading.Thread(
            target=self._beat,
            args=(interval,),
            name=f"fleet-runbeat-{directive_id}",
            daemon=True,
        )

    def _beat(self, interval: float) -> None:
        while not self._stop.wait(interval):
            child = self._slot.get("child")
            refresh_run(self._directive_id, child_pid=getattr(child, "pid", None))
            # A run that outlives one interval keeps its runtime's beat fresh
            # (#1412): the executor IS alive while its child is, and a beat that
            # only marked the spawn would report a 40-minute run as a dead one.
            beat_runtime(EXECUTOR_RUNTIME_ID)

    def start(self) -> RunBeater:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=2.0)


def start_beating(
    directive_id: str,
    interval: float = HEARTBEAT_INTERVAL_SECONDS,
    slot: dict | None = None,
) -> RunBeater:
    """Start a run's per-directive beater; the caller MUST `stop()` it in finally."""
    return RunBeater(directive_id, interval, slot).start()


def _run_child(
    directive: dict,
    args: argparse.Namespace,
    slot: dict,
    agent_id: str,
    worktree: Path | None,
    lane_env: dict[str, str],
) -> tuple[int, str]:
    """Run one executor and release its lane; the loop owns the claim.

    This is the body of one pool worker up to and including the release: it
    executes the executor, then in ``finally`` clears the run marker, releases the
    claim exactly once and unregisters the slot, so a dead child can never strand
    an issue. The verdict and the report are the loop's own worker's job (the
    nested ``run_worker`` inside ``loop``), run on the same thread so the run path
    stays one place.
    """
    directive_id = slot["directive"]
    beater = start_beating(directive_id, HEARTBEAT_INTERVAL_SECONDS, slot)
    try:
        return run_once(
            directive, args.runner, args.timeout, args.dry_run, agent_id, worktree, lane_env, slot
        )
    finally:
        beater.stop()
        clear_run(directive_id)
        # Single owner (#281): a graceful stop already released the claim and set
        # the flag, so this must not release it a second time.
        release_in_flight(slot)
        unregister_run(directive_id)


def loop(args: argparse.Namespace) -> int:
    if not singleton.guard("sister", "bash fleet/run-fleet.sh (or: bash fleet/terminal.sh)"):
        return 1
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, handle_stop)
    started_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    commit = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True
    ).stdout.strip() or "unknown"
    # Resolve the runner BEFORE the loop reads the inbox (#733): a fleet that cannot
    # spawn says so once and holds, rather than failing once per directive per cycle.
    runner_ok, runner_detail = preflight(args.runner)
    if runner_ok:
        print(f"[terminal] preflight OK — {runner_detail}", flush=True)
    else:
        print(f"[terminal] PREFLIGHT FAILED — {runner_detail}", flush=True)
    # The dispatcher runtime beats at START and on every poll cycle (#1412) — the two
    # moments its liveness can honestly be asserted, and the reason the judge's
    # `runtime-stale:deepseek-sister` means "this loop stopped", not "nobody
    # implemented a producer for it" (which is what it meant before this landed).
    beat_runtime(SISTER_RUNTIME_ID)

    def run_worker(
        directive: dict,
        slot: dict,
        agent_id: str,
        issue: int,
        worktree: Path | None,
        lane_env: dict[str, str],
        run_started_at: str,
        dispatch: dict | None = None,
        branch: str | None = None,
    ) -> None:
        """One worker's full life: run the executor, then derive and report the verdict.

        The claim is already taken and the lane provisioned by the loop; this runs
        the executor (via ``_run_child``, which releases the claim in ``finally``),
        then derives the verdict from evidence the loop runs itself and reports it.
        One worker = one directive = one lane = one report.
        """
        directive_id = slot["directive"]
        rc, output = _run_child(directive, args, slot, agent_id, worktree, lane_env)
        if slot.get("stopped"):
            # A stop/kill took this run down; the stop path already escalated, and
            # a post-mortem gate run here would only manufacture a false result.
            return
        where = f"worktree {worktree}" if worktree else "shared checkout"
        tail = (output.strip()[-600:]) or f"runner exited {rc} with no output"
        # The FinOps block leads the report: the director's record of the run names
        # the tier the runner actually executed at, not the tier it asked for.
        tail = f"[{where}] {finops_line(dispatch)} | {tail}"
        # Push-on-commit (#740): immediately after the runner exits and BEFORE
        # gating, so a lane's commit survives on the remote regardless of what
        # the gate later decides. A failed/impossible push is named as
        # `stranded` in the run record — never silent (the #708 incident this
        # closes was 31 lanes, 46 commits, exactly none of them pushed).
        push_outcome, push_detail = push_lane_branch(branch, worktree, args.timeout, directive_id)
        tail = f"{tail} | push: {push_outcome} ({push_detail})"
        if push_outcome == PUSH_STRANDED:
            # Never silent (#740): a stranded push is streamed by directive NAME
            # the moment it is known, independent of any later truncation of the
            # run record's 200-char tail.
            stream_run_event(directive_id, f"{PUSH_STRANDED}: directive {directive_id} — {push_detail}")
        # The verdict comes from evidence the loop runs itself — the issue's own
        # Verify: command, `make verify`, and the real board state — never from
        # the runner's prose (#279).
        gate_outcome, gate_detail = gate_evidence(issue, worktree, args.timeout)
        gate_ok = gate_outcome == GATE_OK
        # "The PR is merged" is not "the item is closed": at this point the branch,
        # the claim, the directive and the lane are still live. Close them out and
        # carry the verdict, so a partial close is visible. Only a run whose gates
        # passed is worth closing out.
        closeout = closeout_issue(issue) if (rc == 0 and gate_ok) else f"SKIPPED (gate {gate_outcome})"
        landed, landing_detail = landed_evidence(issue)
        run_status, prose_hint = verdict(rc, output, gate_ok, landed)
        # `run_status` stays in runslog's own vocabulary (started/done/failed —
        # fleet/runslog.py), so a run the loop could not assess is recorded
        # `failed`, never `done`, with CANNOT-ASSESS named in the detail.
        tail = (
            f"{tail} | gate-outcome: {gate_outcome} | {gate_detail} | {landing_detail} | "
            f"close-out: {closeout} | prose-hint: {prose_hint}"
        )
        record_run(
            directive_id, issue, agent_id, run_status, run_started_at, _now(), tail[:200],
            dispatch=dispatch,
        )
        stream_run_event(directive_id, f"run finished: rc={rc} status={run_status} | {gate_detail}")
        clear_reported(directive_id)
        if run_status == "done":
            subprocess.run(
                ["python3", CHANNEL, "report", "--from", "sister", "--correlation", directive_id,
                 "--type", "result", "--body", tail[:2000]],
                cwd=ROOT,
            )
        else:
            # The run did not land: the directive is still in the inbox (the
            # failure path escalates, it does not report — and only a report
            # consumes), so this attempt is counted against the directive's
            # budget. A crashed run and a refused claim share that one counter
            # (#723), and the K-th failure retires the order to the dead-letter
            # store instead of re-dispatching it for ever.
            guard_retire(directive_id, issue, f"run did not land: {run_status} (rc={rc})")
            # The runner's exit code and a *genuinely failed* gate pick the
            # severity; the *evidence* decides whether this is a success at all.
            # A gate the loop could not assess (a timeout, an unrunnable gate) is
            # reported as CANNOT-ASSESS at `warn` — never at `critical`, because a
            # critical verdict on a gate that attested nothing is what re-dispatched
            # the directive every cycle (#733).
            severity = escalation_severity(rc, gate_outcome)
            subprocess.run(
                ["python3", CHANNEL, "escalate", "--from", "sister", "--correlation", directive_id,
                 "--severity", severity, "--body", tail[:2000]],
                cwd=ROOT,
            )

    idle_printed = False
    paused_printed = False
    while True:
        # The poll cycle's own beat (#1412), first in the cycle so it advances on
        # every path through it — including the `continue`s below, which is where
        # a beat written at the END of the cycle would silently stop arriving.
        beat_runtime(SISTER_RUNTIME_ID)
        # Mid-run steering (issue #367): deliver any steer the director queued for a
        # live run before this cycle does anything else.
        delivered_steers = deliver_pending_steers()
        if delivered_steers:
            print(f"[terminal] delivered steer(s) to {delivered_steers}", flush=True)
        active = len(active_run_slots())
        if stopping():
            # `stop` is graceful: it takes effect once every in-flight run has
            # finished — an idle pool is between runs. Checking only after a run
            # meant `stop` on an idle fleet did nothing, and returning while
            # workers still ran would strand their claims.
            if active:
                write_heartbeat("stopping", started_at=started_at, commit=commit, runs=active)
                time.sleep(args.idle_sleep)
                continue
            set_flag(STOPPING, False)
            write_heartbeat("stopped", started_at=started_at, commit=commit)
            print("[terminal] control:stop — stopping the loop cleanly", flush=True)
            return 0
        # The runner preflight (#733), BEFORE this cycle reads the inbox. An
        # unresolvable runner cannot be fixed by re-reading a directive, so the
        # queue is HELD (one escalation) instead of every directive failing and
        # escalating critical — the runaway amplifier this replaces. The hold is a
        # pause, so controls still arrive: `resume` is readable, and the hold is
        # released automatically once the runner resolves.
        runner_ok, runner_detail = preflight(args.runner)
        # NOTHING IS RELEASED HERE. This branch used to release the hold on this one
        # condition, and since #841 the hold is ALSO taken for a capability failure — so
        # the two sites released and re-took it every cycle and `.fleet/sister.log`
        # alternated "queue held" with "runner resolvable again — queue hold released",
        # a line asserting the opposite of the truth, forever (#845). The release lives
        # below now, on the CONJUNCTION: resolves AND can honour the model.
        if not runner_ok:
            if hold_queue_for_runner(runner_detail):
                print(
                    f"[terminal] PREFLIGHT FAILED — {runner_detail} — queue held, no directive "
                    "dispatched (one escalation, not one per directive)",
                    flush=True,
                )
            report_once(
                RUNNER_PREFLIGHT_ID,
                key=f"runner-unresolvable:{runner_detail}",
                message_type="escalate",
                body=f"{runner_detail} — the fleet cannot spawn a subagent, so the queue is held and no "
                "directive is dispatched. Fix: install the runner on PATH, or start the loop with "
                "`--runner <absolute path>` (the FLEET_RUNNER environment variable does the same). "
                "Reported once — not once per directive per cycle.",
            )
            if args.once:
                return 1
        # The runner CAPABILITY preflight (#841) — the SECOND half of the question above.
        # `preflight` proves the executable resolves; it never proved the executable could
        # honour the model, and that gap was measured: with `claude` on PATH and the BYOK
        # environment unset, `claude -p --model deepseek-v4-flash` resolved and then died in
        # ~10s with `[claude-code:unrecognized_model]` — 192 such lines, 40 `status=failed`,
        # 0 `status=ok`, while this loop and the watchdog both reported the fleet healthy.
        # Same machinery and the same reason as above: a dependency no directive can fix is
        # HELD and escalated ONCE, never re-tried per directive.
        # Asked only of a runner that RESOLVED: the two questions are ordered, and an
        # unresolvable runner has already been reported above. Two separate mistakes were
        # caught here by `scripts/check-fleet-runner-preflight.sh`, which is why the
        # ordering is written out rather than folded into one expression: asking the second
        # question of a runner that does not exist escalated twice for ONE condition, and
        # releasing the hold on the `elif` path ran for a runner that had not resolved —
        # which RELEASED the hold the preflight had just taken.
        capability_problem = runners.unhonourable(args.runner) if runner_ok else ""
        if capability_problem:
            if hold_queue_for_runner(capability_problem):
                print(
                    f"[terminal] RUNNER CANNOT HONOUR A DISPATCH — {capability_problem} — queue held, "
                    "no directive dispatched (one escalation, not one per directive)",
                    flush=True,
                )
            report_once(
                RUNNER_CAPABILITY_ID,
                key=f"runner-capability:{capability_problem}",
                message_type="escalate",
                body=f"{capability_problem} — the fleet can resolve a runner but cannot honour a "
                "dispatch with it, so the queue is held and no directive is dispatched; until this is "
                "fixed every dispatch would die with `unrecognized_model` and be recorded as "
                "`status=failed` in a run log. Reported once — not once per directive per cycle.",
            )
            if args.once:
                return 1
        elif runner_ok:
            # The ONE release site. A hold is taken for EITHER condition above (the runner
            # does not resolve, or it cannot honour the model) and released only when
            # NEITHER holds — one predicate for the pair, so the two cannot contradict each
            # other in the same log (#845). `release_runner_hold` still refuses to touch an
            # principal's own pause, whatever this decides.
            released = release_runner_hold()
            if released:
                print(f"[terminal] {released}", flush=True)
        if paused():
            write_heartbeat("paused", started_at=started_at, commit=commit, runs=active)
        else:
            write_heartbeat("working" if active else "idle", started_at=started_at, commit=commit, runs=active)
        watch_command = ["python3", CHANNEL, "watch", "--timeout-seconds", str(args.watch_timeout), "--interval", "1"]
        for slot in active_run_slots():
            watch_command += ["--skip", str(slot["directive"])]
        watch = subprocess.run(
            watch_command,
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        # A steer can land while the watch blocks; deliver it before the cycle
        # moves on, so a hint reaches its run on the next tick at worst.
        delivered_steers = deliver_pending_steers()
        if delivered_steers:
            print(f"[terminal] delivered steer(s) to {delivered_steers}", flush=True)
        if watch.returncode == 1:  # IDLE — just poll again (never stop)
            if not idle_printed:
                print("[terminal] idle — watching .fleet/inbox (never sleeps)", flush=True)
                idle_printed = True
            if args.once:
                return 0
            continue
        idle_printed = False
        if watch.returncode != 0:
            print(f"[terminal] watch rc={watch.returncode}: {watch.stderr.strip()}", file=sys.stderr, flush=True)
            if args.once:
                return watch.returncode
            time.sleep(args.idle_sleep)
            continue

        directive = extract_json(watch.stdout)
        if not directive:
            print("[terminal] unparseable directive — escalating", file=sys.stderr, flush=True)
            subprocess.run(
                ["python3", CHANNEL, "escalate", "--from", "sister", "--correlation", "unknown",
                 "--severity", "critical", "--body", "unparseable directive in the inbox"],
                cwd=ROOT,
            )
            if args.once:
                return 1
            continue

        if directive.get("type") == "halt":
            print("[terminal] HALT received — stopping the loop")
            return 0

        directive_id = directive.get("id", "")
        control = directive.get("control")
        override = False
        if control:
            # `poke` and `status` answer with the loop's own state; the rest are
            # process levers. Every one of them is acked or reported — a control
            # that silently does nothing is indistinguishable from a dead loop.
            if control in ("poke", "status"):
                body = (
                    "poke received — loop alive"
                    if control == "poke"
                    else (
                        f"loop state: paused={paused()} stopping={stopping()} "
                        f"active-runs={len(active_run_slots())} "
                        f"runs={len(list(RUNS.glob('*.json'))) if RUNS.exists() else 0}"
                    )
                )
                print(f"[terminal] control:{control} — answering", flush=True)
                subprocess.run(
                    ["python3", CHANNEL, "report", "--from", "sister", "--correlation", directive_id,
                     "--type", "ack", "--body", body],
                    cwd=ROOT,
                )
                if args.once:
                    return 0
                continue

            control_outcome = apply_control(control, directive, agent_id_for(directive_id))
            print(f"[terminal] control:{control} — {control_outcome}", flush=True)
            if control_outcome == "unknown":
                # Poison message: this build cannot execute it, and re-reading it
                # cannot change that. Say so once, then consume it.
                report_once(
                    directive_id,
                    key=f"unknown-control:{control}",
                    message_type="escalate",
                    body=f"unknown control action '{control}' — not in this build's vocabulary; consumed "
                    "so it cannot loop",
                )
                subprocess.run(
                    ["python3", CHANNEL, "report", "--from", "sister", "--correlation", directive_id,
                     "--type", "result", "--body", f"control '{control}' is not in this build's vocabulary; "
                     "escalated once and consumed (a message that can never be handled must not be re-read)"],
                    cwd=ROOT,
                )
                if args.once:
                    return 1
                continue
            if control_outcome == "kill":
                handled(directive_id)
                subprocess.run(
                    ["python3", CHANNEL, "escalate", "--from", "sister", "--correlation", directive_id,
                     "--severity", "warn", "--body", "control:kill — run terminated, claim released"],
                    cwd=ROOT,
                )
                return 128 + signal.SIGTERM
            if control_outcome == "halt":
                handled(directive_id)
                subprocess.run(
                    ["python3", CHANNEL, "report", "--from", "sister", "--correlation", directive_id,
                     "--type", "ack", "--body", "control:halt — stopping the fleet"],
                    cwd=ROOT,
                )
                return 0
            if control_outcome == "refresh":
                handled(directive_id)
                print("[terminal] refresh requested — pull + verify + restart", flush=True)
                pull = subprocess.run(["git", "pull", "--ff-only"], cwd=ROOT, capture_output=True, text=True)
                verify = subprocess.run(["make", "verify"], cwd=ROOT, capture_output=True, text=True)
                if pull.returncode == 0 and verify.returncode == 0:
                    print("[terminal] refresh OK — restarting with new code", flush=True)
                    subprocess.run(
                        ["python3", CHANNEL, "report", "--from", "sister", "--correlation", directive_id,
                         "--type", "ack", "--body", "refreshed + verify PASS; restarting"],
                        cwd=ROOT,
                    )
                    os.execv(sys.executable, [sys.executable, *sys.argv])
                subprocess.run(
                    ["python3", CHANNEL, "escalate", "--from", "sister", "--correlation", directive_id,
                     "--severity", "critical",
                     "--body", f"refresh FAILED: pull rc={pull.returncode}, verify rc={verify.returncode}: {(verify.stdout + verify.stderr)[-400:]}"],
                    cwd=ROOT,
                )
                if args.once:
                    return 1
                continue
            if control_outcome == "restart":
                # Re-exec the same code: no pull, no verify — the fast lever.
                handled(directive_id)
                subprocess.run(
                    ["python3", CHANNEL, "report", "--from", "sister", "--correlation", directive_id,
                     "--type", "ack", "--body", "control:restart — re-executing the loop"],
                    cwd=ROOT,
                )
                print("[terminal] restart requested — re-exec", flush=True)
                os.execv(sys.executable, [sys.executable, *sys.argv])
            if control_outcome == "dispatch-override":
                print("[terminal] control:override — reaping any holder, then dispatching", flush=True)
                issue_for_override = directive_issue(directive)
                if issue_for_override is not None:
                    reap = subprocess.run(
                        ["python3", str(ROOT / "governance" / "dispatch" / "cli.py"), "reap",
                         "--older-than-minutes", "0", "--issue", str(issue_for_override), "--reaper", "operator-override"],
                        cwd=ROOT,
                        capture_output=True,
                        text=True,
                    )
                    print(f"[terminal] override reap rc={reap.returncode}: {reap.stdout.strip()[:120]}", flush=True)
                override = True
            elif control_outcome == "drop":
                # Issue #754: a peer (or the principal, relaying through the director)
                # says a named directive is dead. Retire it through the SAME
                # implementation the automatic path uses, consume the control, and
                # ACK naming what was dropped — a control that silently did nothing
                # is indistinguishable from a dead loop.
                target = control_target_directive_id(directive)
                if target is None:
                    # A drop that names no target cannot be honoured, and saying so
                    # is mandatory: an unacked/unsupported control is a reported
                    # failure, never a silent no-op (the #754 acceptance criterion).
                    subprocess.run(
                        ["python3", CHANNEL, "escalate", "--from", "sister", "--correlation", directive_id,
                         "--severity", "warn",
                         "--body",
                         "control:drop named no directive (task.directive) — refused, not silently "
                         "ignored; nothing was dead-lettered"],
                        cwd=ROOT,
                    )
                    handled(directive_id)
                    if args.once:
                        return 1
                    continue
                reason = str((directive.get("task") or {}).get("reason") or "dropped by control:drop")
                sender = str(directive.get("from") or "brain")
                target_issue = directive_issue(directive) or 0
                drop_directive(
                    target,
                    target_issue,
                    reason,
                    dropped_by=sender,
                    report=f"control:drop retired directive {target} on instruction from {sender}.",
                )
                handled(directive_id)
                subprocess.run(
                    ["python3", CHANNEL, "report", "--from", "sister", "--correlation", directive_id,
                     "--type", "ack",
                     "--body", f"control:drop — {target} dead-lettered (reason: {reason}); it will never be dispatched again"],
                    cwd=ROOT,
                )
                print(f"[terminal] control:drop — {target} dead-lettered", flush=True)
                if args.once:
                    return 0
                continue
            elif control_outcome == "list-dead-letter":
                # A read: answer with the mailbox and consume, so the loop never
                # re-reads it. The mailbox is listed by verb, never by a principal
                # walking the runtime directory.
                records = dead_letter_inventory()
                body = (
                    "dead-letter mailbox is empty"
                    if not records
                    else "dead-lettered: "
                    + "; ".join(
                        f"{record.get('id')} issue={record.get('issue')} attempts={record.get('attempts')} "
                        f"by={record.get('dropped_by')} reason={record.get('reason')}"
                        for record in records
                    )
                )
                handled(directive_id)
                subprocess.run(
                    ["python3", CHANNEL, "report", "--from", "sister", "--correlation", directive_id,
                     "--type", "ack", "--body", body[:2000]],
                    cwd=ROOT,
                )
                print(f"[terminal] control:dead-letter — {len(records)} record(s)", flush=True)
                if args.once:
                    return 0
                continue
            else:
                # pause / resume / stop: the flag is set; ack and carry on.
                subprocess.run(
                    ["python3", CHANNEL, "report", "--from", "sister", "--correlation", directive_id,
                     "--type", "ack", "--body", f"control:{control} — {control_outcome}"],
                    cwd=ROOT,
                )
                if args.once:
                    return 0
                continue

        issue = directive_issue(directive)
        if work_held(directive, paused()):
            # Pause holds *work*, never controls: a paused loop that stopped reading
            # the inbox could not be resumed — measured, `resume` was delivered and
            # sat unread until a principal cleared the flag by hand.
            write_heartbeat("paused", started_at=started_at, commit=commit)
            if not paused_printed:
                print("[terminal] PAUSED — holding the queue (resume to continue)", flush=True)
                paused_printed = True
            if issue is not None:
                report_once(
                    directive_id,
                    key=f"paused:#{issue}",
                    message_type="result",
                    body=f"#{issue} held while paused — the order stays pending until `resume`",
                )
            if args.once:
                return 0
            continue
        paused_printed = False
        if issue is None:
            print(f"[terminal] directive {directive_id} names no issue — escalating malformed", file=sys.stderr, flush=True)
            subprocess.run(
                ["python3", CHANNEL, "escalate", "--from", "sister", "--correlation", directive_id,
                 "--severity", "warn", "--body", "directive names no issue (relay messages are not executable work)"],
                cwd=ROOT,
            )
            if args.once:
                return 1
            continue

        # The fan-out ceiling is resolved HERE, every cycle, because two of its
        # three bounds move: the ready-lane set changes as lanes land, and the RAM
        # / `/tmp` headroom moves with every other process on the box (#718). A
        # directive the ceiling cannot take is HELD, not consumed: it stays in the
        # inbox for a later slot, exactly as the pool-full path always did. The
        # decision NAMES the bound that held it, so a principal tuning one knob can
        # see whether that knob is the one binding.
        resolved, lane = resolve_capacity(directive)
        decision = capacity.admit(lane, capacity=resolved, active=capacity_lanes())
        if not decision.admitted:
            print(decision.line(lane), flush=True)
            if resolved.disjoint is not None and resolved.disjoint.unattributable:
                # Never silent: a lane whose file set could not be compared is
                # reported on every hold, so "disjoint" is never claimed for work
                # nobody could check (AO-GR-24).
                print(f"[terminal] capacity note: {resolved.disjoint.detail()}", flush=True)
            time.sleep(args.idle_sleep)
            continue
        print(
            f"[terminal] capacity: #{issue} admitted — {resolved.line()} [{decision.detail}]",
            flush=True,
        )

        # The FinOps block decides the runner BEFORE any claim is taken (#218): a
        # declaration this build cannot turn into a runner must cost nothing and
        # must never reach the default runner. The directive is left PENDING — the
        # order is real work, and consuming it would erase the only record that it
        # was ordered (the same posture as a refused claim).
        dispatch, finops_refusal = resolve_dispatch(directive, args.runner)
        if finops_refusal is not None:
            print(f"[terminal] #{issue} dispatch REFUSED — {finops_refusal}", file=sys.stderr, flush=True)
            report_once(
                directive_id,
                key=f"finops-refusal:{finops_refusal.code}",
                message_type="escalate",
                body=f"#{issue} refused: {finops_refusal}. The directive is left pending — re-order with a "
                f"tier/thinking this build can execute ({', '.join(sorted(TIER_RUNNERS))}).",
            )
            if args.once:
                return 1
            continue

        agent_id = agent_id_for(directive_id)
        held = subprocess.run(
            ["python3", str(ROOT / "governance" / "dispatch" / "cli.py"), "held", "--issue", str(issue)],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if held.returncode == 0 and not override:
            try:
                holder = json.loads(held.stdout).get("agent")
            except json.JSONDecodeError:
                holder = "unknown"
            state = run_state(directive_id)
            action = held_action(holder, agent_id, state)
            if action == "in-flight":
                # Someone (another loop) is tracking this run: report it, and leave
                # the directive PENDING — consuming it would erase the only record
                # that the work was ordered, and nobody would report its result.
                # Counted like every other held path (#723): a directive another
                # loop never finishes must not be re-read by this loop for ever.
                print(f"[terminal] #{issue} in flight (held by {holder}, run tracked) — left pending", flush=True)
                guard_retire(directive_id, issue, f"in flight (held by {holder}, run tracked)")
                if args.once:
                    return 0
                continue
            if action == "self-heal":
                # Our own orphan: a previous loop of ours was stopped mid-run. The
                # work was never reported, so self-heal — reap the dead claim, then
                # dispatch below in this same cycle.
                #
                # The re-dispatch is COUNTED as an attempt (#723), never a fresh
                # start: a directive whose dispatch keeps killing the loop is
                # exactly the runaway, and it must be retired rather than reaped
                # and re-spawned for ever. This cycle still re-dispatches (the work
                # was never reported, so the immediate retry is deliberate); from
                # the next cycle on, `channel watch` holds the directive until its
                # backoff elapses, so the retries are spaced rather than spun.
                if guard_retire(directive_id, issue, "self-heal: our own run no longer exists"):
                    if args.once:
                        return 1
                    continue
                print(f"[terminal] #{issue} orphaned by our own dead run — reaping and re-dispatching", flush=True)
                reap = subprocess.run(
                    ["python3", str(ROOT / "governance" / "dispatch" / "cli.py"), "reap",
                     "--older-than-minutes", "0", "--issue", str(issue), "--reaper", "sister-self-heal"],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                )
                if reap.returncode != 0:
                    print(f"[terminal] self-heal reap failed: {reap.stdout}{reap.stderr}", file=sys.stderr, flush=True)
                    if args.once:
                        return 1
                    continue
                clear_reported(directive_id)
            else:
                # A claim nobody is tracking, held by someone else (#694): hand the
                # orphan to the reconciler that owns orphan teardown (#304) — once per
                # episode — and escalate only when it does not end it. Counted (#723):
                # an orphan nobody reaps is another pending-forever directive, and the
                # guard is what bounds it.
                ended, detail = hand_orphan_to_reconciler(directive_id, issue, holder)
                if ended:
                    print(
                        f"[terminal] #{issue} held by {holder} with no live run — the "
                        f"reconciler ended the orphan ({detail}); re-dispatching next cycle",
                        flush=True,
                    )
                    report_once(
                        directive_id,
                        key="orphan-reconciled",
                        message_type="escalate",
                        body=(
                            f"#{issue} was held by {holder} with no live run. The reconciliation "
                            f"sweep ended the orphan, so the order can be dispatched again "
                            f"instead of waiting on the brain — {detail}."
                        ),
                    )
                    # The episode is over, so a fresh orphan may hand over again; the
                    # report mark goes too, because the next cycle re-dispatches.
                    clear_orphan_handoff(directive_id)
                    clear_reported(directive_id)
                    if args.once:
                        return 0
                    continue
                print(
                    f"[terminal] #{issue} held by {holder} with no live run — escalating, "
                    f"left pending ({detail})",
                    flush=True,
                )
                guard_retire(directive_id, issue, f"orphaned claim held by {holder}, no live run")
                if args.once:
                    return 0
                continue

        print(
            f"[terminal] executing directive {directive_id} (issue {issue}) — {finops_line(dispatch)}",
            flush=True,
        )
        stream_run_event(directive_id, f"executing directive (issue {issue}) — {finops_line(dispatch)}")
        lane = (directive.get("task") or {}).get("lane") or ""
        # What the executor is TOLD, not just ordered (#220): the issue's identity,
        # acceptance text and Verify: clause from the board snapshot (offline), the
        # lane, and the lessons a previous lane already paid for. The live board is
        # the body fallback only — the snapshot is the primary, offline source.
        context = issue_context(issue, lane, body_reader=live_issue_body)
        mark_run(directive_id, issue, agent_id, context=context)
        run_started_at = _now()
        record_run(
            directive_id, issue, agent_id, "started", run_started_at,
            detail=context_summary(context), dispatch=dispatch,
        )
        claimed, claim_output = claim_issue(issue, agent_id, lane, directive_id)
        if claimed:
            stream_run_event(directive_id, f"claim taken for #{issue} by {agent_id}")
        if not claimed:
            # The refusal is usually a stale snapshot (a freshly filed child), and
            # after a board refresh the next cycle claims it — but "the next cycle"
            # was every cycle, for ever, with no counter to stop it. The attempt is
            # counted and the next one is spaced by the harvested backoff; once the
            # budget is exhausted the order is retired to the dead-letter store and
            # `channel watch` never returns it again (#723).
            print(f"[terminal] claim refused for #{issue}: {claim_output}", file=sys.stderr, flush=True)
            if "snapshot-stale" in claim_output:
                # The refusal names its own remedy, so the loop TRIGGERS it
                # (#727): exactly one bounded refresh, and if freshness does not
                # return the directive is parked and held by `channel watch`
                # instead of re-dispatched every cycle. The attempt below still
                # counts, so the park and the budget compose rather than compete.
                board_trigger(directive_id, issue)
            terminal_reason = runaway.terminal_classification(claim_output)
            if terminal_reason is not None:
                # Terminal by definition (#861): a closed issue, a closed epic or
                # an unowned unit cannot be cured by a retry, so it is retired on
                # the FIRST refusal instead of consuming the K-attempt budget.
                guard_retire_terminal(
                    directive_id, issue, f"claim refused: {claim_output[-120:]}", terminal_reason
                )
            else:
                guard_retire(directive_id, issue, f"claim refused: {claim_output[-120:]}")
            if args.once:
                return 1
            continue

        tree = provision_worktree(issue, directive_id, agent_id, lane)
        if tree is None:
            subprocess.run(
                ["python3", CHANNEL, "escalate", "--from", "sister", "--correlation", directive_id,
                 "--severity", "warn",
                 "--body", f"no isolated lane for #{issue} — running in the shared checkout"[:200]],
                cwd=ROOT,
            )
            stream_run_event(directive_id, f"no isolated lane for #{issue} — shared checkout")
        else:
            stream_run_event(directive_id, f"isolated lane provisioned for #{issue}: {tree[0]}")
        worktree, lane_branch, lane_env = tree if tree else (None, None, {})
        # One worker = one lane = one claim = one run marker = one report. The
        # claim is taken here (by the loop) and released in the worker's `finally`,
        # so a dead child can never strand an issue.
        slot = register_run(directive_id, issue, agent_id)
        slot["context"] = context
        # The lane the capacity gate admitted travels with the slot, so the NEXT
        # cycle's disjointness check can see which files are already owned by an
        # in-flight run rather than having to re-derive them from the directive.
        slot["capacity_lane"] = lane
        # The resolved block travels with the slot: `_run_child`/`run_once` then
        # dispatch at exactly the tier this loop recorded, and neither has to
        # re-decide (or could disagree).
        slot["dispatch"] = dispatch
        worker = threading.Thread(
            target=run_worker,
            args=(directive, slot, agent_id, issue, worktree, lane_env, run_started_at, dispatch, lane_branch),
            name=f"fleet-run-{directive_id}",
            daemon=True,
        )
        slot["thread"] = worker
        worker.start()
        if args.once:
            # `--once` stays synchronous: one directive to completion, then out.
            worker.join()
            return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fleet-terminal", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run the never-idle loop")
    run.add_argument(
        "--runner",
        default=os.environ.get("FLEET_RUNNER") or DEFAULT_RUNNER,
        help="the base runner command (FLEET_RUNNER sets the same default); the directive's "
        "FinOps tier selects the model it is invoked with (--model <tier model>) and is "
        "exported to the child environment (#218). It is resolved to an absolute path at "
        "startup and on every cycle (#733), so it does not have to be on an inherited PATH.",
    )
    run.add_argument("--watch-timeout", type=float, default=30.0)
    run.add_argument("--timeout", type=float, default=1800.0)
    run.add_argument("--idle-sleep", type=float, default=2.0)
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--once", action="store_true")
    run.set_defaults(func=loop)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
