#!/usr/bin/env python3
"""Dumb-terminal loop (sister side) — never idles, always watches, escalates.

This is the loop the sister runs so the fleet never stops: it watches the
inbox, runs a code-native subagent per directive via the agent CLI, reports
the result, and escalates any failure to the brain. An empty inbox is just
another poll cycle — there is no IDLE exit.

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
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import singleton
import telemetry

ROOT = Path(__file__).resolve().parent.parent
CHANNEL = str(ROOT / "fleet" / "channel.py")
#: The institutional lane provisioner: mints the session identity before each spawn.
ISOLATION_CLI = str(ROOT / "governance" / "isolation" / "cli.py")
#: The institutional close-out: drives every artifact of a finished item to terminal.
LIFECYCLE_CLI = str(ROOT / "governance" / "lifecycle" / "cli.py")
#: Session reconciliation (#304): a per-lane heartbeat, so a lane whose agent died
#: is visible as a dead lane rather than as work in progress.
sys.path.insert(0, str(ROOT))
from governance.reconcile.heartbeat import DEFAULT_BEAT_SECONDS, Beater as SessionBeater  # noqa: E402

SESSION_BEAT_SECONDS = DEFAULT_BEAT_SECONDS


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


def build_prompt(
    directive: dict,
    agent_id: str = "subagent",
    worktree: Path | None = None,
    env: dict[str, str] | None = None,
) -> str:
    """The subagent prompt: one issue, one worktree, one session identity.

    The claim is *owned by the loop*, not by the subagent: a subagent that died
    mid-task used to leave its claim wedged until the 24h TTL, because nobody
    was left to release it. The loop claims before the spawn and releases in a
    `finally`, so a dead subagent can no longer strand an issue.

    The session identity travels with the order too. An agent that does not know
    which branch it is on cannot keep its commits traceable to the ticket, so the
    id, the branch and the required trailer are stated rather than assumed.
    """
    task = directive.get("task") or {}
    issue = task.get("issue")
    lane = task.get("lane") or ""
    directive_id = directive.get("id", "")
    model = directive.get("model") or {}
    body = directive.get("body", "")
    identity = env or {}
    session = identity.get("AO_SESSION_ID", "")
    branch = identity.get("AO_BRANCH", "")
    where = (
        f"Your worktree is {worktree} (branch {branch or worktree.name}). Work ONLY there — never in the "
        "shared checkout, which other lanes are using.\n"
        if worktree is not None
        else "Work in the repo checkout; disk worktrees under ~/ao-worktrees, never /tmp.\n"
    )
    who = (
        f"Your session identity is {session} (agent {agent_id}) on branch {branch}. Every commit you "
        f"author MUST carry 'Refs kushin77/agent-orchestrator#{issue}' — the lane audit checks each "
        "commit, and one that omits it stays a violation even after a later good commit.\n"
        if session
        else ""
    )
    return (
        "You are an epic-focused subagent in the kushin77/agent-orchestrator fleet, "
        "steered by the brain through the sister session. "
        f"{where}{who}\n"
        f"BRAIN DIRECTIVE {directive_id} — model {model.get('tier', 'flash')}/{model.get('thinking', 'none')}:\n{body}\n\n"
        "Do exactly this, nothing else:\n"
        f"1. Issue #{issue} is ALREADY CLAIMED for you as `{agent_id}` (lane {lane or 'n/a'}) — do NOT "
        "run claim and do NOT run release; the loop manages the claim around your run.\n"
        f"2. If a PR for issue #{issue} ALREADY exists, do NOT bail out: check out its branch, run "
        "`make verify`, and if it is green squash-merge the PR and close the issue. Only implement "
        "from scratch if no PR exists.\n"
        f"3. Implement issue #{issue} to completion; open a PR whose body carries 'Closes #{issue}' and "
        "the ACTUAL 'make verify' output as evidence.\n"
        f"4. After `make verify` is green and the PR is open, squash-merge it with "
        "`gh pr merge <number> --squash --delete-branch`, then close the issue. "
        "NEVER leave a completed PR unmerged or the issue open.\n"
        "5. Leave every artifact terminal: the loop then runs the lifecycle close-out "
        "(`governance/lifecycle`) and its verdict travels with your report, so a "
        "surviving branch, a wedged claim, an unconsumed directive or a lane left "
        "behind is reported as NOT-OK rather than passing as done.\n"
        "Return a short report: PR number, verify output summary, files touched, AND the merge result "
        "(PR number + merged/closed). If anything fails, report the exact error instead of improvising."
    )


def build_command(
    directive: dict,
    runner: str,
    agent_id: str,
    worktree: Path | None = None,
    env: dict[str, str] | None = None,
) -> list[str]:
    """Runner must accept the prompt as its final argument (e.g. `claude -p`)."""
    return shlex.split(runner) + [build_prompt(directive, agent_id, worktree, env)]


def start_session_beat(env: dict | None, pid: int) -> object | None:
    """Beat a per-session heartbeat for the lane this dispatch owns (#304).

    The pid recorded is the **subagent's**, not this loop's: a lane has to go
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


def run_once(
    directive: dict,
    runner: str,
    timeout: float,
    dry_run: bool,
    agent_id: str,
    worktree: Path | None = None,
    env: dict[str, str] | None = None,
    slot: dict | None = None,
) -> tuple[int, str]:
    """Run one subagent for one directive; return (exit code, captured output).

    Uses Popen rather than `subprocess.run` so the child is reachable from the
    stop handler: a stopped loop must take its subagent down with it instead of
    orphaning it. The session environment is injected here, so the subagent and
    everything it spawns commit under its own identity.
    """
    command = build_command(directive, runner, agent_id, worktree, env)
    cwd = str(worktree) if worktree is not None else str(ROOT)
    if dry_run:
        print("DRY-RUN:", " ".join(shlex.quote(part) for part in command), flush=True)
        return 0, f"DRY-RUN (not executed) in {cwd}"
    try:
        child = subprocess.Popen(
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env={**os.environ, **(env or {})},
        )
    except OSError as exc:
        return 127, f"runner could not start in {cwd}: {exc}"
    if slot is not None:
        slot["child"] = child
    beater = start_session_beat(env, child.pid)
    try:
        output, _ = child.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        child.kill()
        output, _ = child.communicate()
        return 124, f"runner timed out after {timeout}s: {(output or '')[-400:]}"
    finally:
        stop_session_beat(beater)
        if slot is not None:
            slot["child"] = None
    return child.returncode, output or ""


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
    config, so the subagent's commits are attributable to the agent that made
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
    """The loop takes the claim, so a dead subagent can never strand one."""
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
    operator to find, so the caller now gets the channel's own words. A claim
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
    partial close reaches the brain instead of being found later by hand.
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
    """A stopped loop must not strand any claim: take every subagent down, free all.

    Observed live: restarting the sister loop mid-run killed it before the
    `finally`, so #167 stayed claimed by an agent that no longer existed — the
    exact wedge the reap tool exists to clean, recreated by an operator restart.
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
#: A ``Verify:`` line is only executed when it is command-shaped: its first token
#: must be an executable the fleet can run. Issue bodies mix real commands with
#: prose ("Verify: the new test fails against today's code"), and running a
#: sentence as a command would manufacture the false verdict this code removes.
COMMAND_PREFIXES = frozenset(
    {
        "make", "bash", "sh", "python3", "python", "pytest", "node", "npm",
        "npx", "git", "gh", "go", "cargo", "terraform", "docker",
    }
)


def looks_refused(output: str) -> bool:
    """A *hint* that a subagent stopped on a refusal — never the verdict.

    Kept deliberately as a hint only (#279): used as a verdict it matched a
    quoted ``REFUSED`` in an otherwise successful run and downgraded it, while
    matching nothing in a run that printed confident prose and did no work. The
    verdict is ``verdict()``, derived from evidence the loop runs itself.
    """
    upper = output.upper()
    return "REFUSED" in upper or "NO WORK DONE" in upper or "NO REAL ISSUE" in upper


def extract_verify_command(body: str) -> str | None:
    """The issue's own ``Verify:`` command — only when it is command-shaped.

    A body that declares no runnable command (or declares prose) yields None, so
    the loop falls back to ``make verify`` rather than executing a sentence.
    """
    match = re.search(r"^[\s>*`-]*Verify:\s*(.*)$", body or "", re.IGNORECASE | re.MULTILINE)
    if not match:
        return None
    candidate = re.sub(r"^[`\s]+|[`\s]+$", "", match.group(1))
    if not candidate:
        for line in (body or "")[match.end():].splitlines():
            candidate = re.sub(r"^[`\s]+|[`\s]+$", "", line)
            if candidate:
                break
    if not candidate or "\n" in candidate:
        return None
    first = candidate.split()[0]
    if first not in COMMAND_PREFIXES and not first.startswith(("./", "/")):
        return None
    return candidate


def gh_issue_field(issue: int, jq: str) -> str | None:
    """Read one field of issue #issue from the real board; None when unreachable.

    The loop reads the board itself so the verdict rests on GitHub's state, not
    on a subagent's claim about it. ``None`` means "cannot assess", which is
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


def run_gate(command: str, cwd: str, timeout: float) -> tuple[bool, str]:
    """Run one gate the loop owns; its exit code — not the prose — is the signal."""
    try:
        done = subprocess.run(
            ["bash", "-lc", command], cwd=cwd, capture_output=True, text=True, timeout=timeout
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"`{command}` could not run ({exc})"
    tail = ((done.stdout or "") + (done.stderr or "")).strip()[-300:]
    return done.returncode == 0, f"`{command}` rc={done.returncode}: {tail or 'no output'}"


def gate_evidence(issue: int, worktree: Path | None, timeout: float) -> tuple[bool, str]:
    """Run the gates the loop checks itself: the issue's Verify: and ``make verify``.

    The runner's own text is never consulted here — a run that only *says* it
    verified the work cannot make either gate exit 0.
    """
    cwd = str(worktree) if worktree is not None else str(ROOT)
    declared = issue_verify_command(issue)
    commands = [declared] if declared else []
    if GATE_OF_RECORD not in commands:
        commands.append(GATE_OF_RECORD)
    pieces = [f"issue Verify: `{declared}`" if declared else "issue declares no runnable Verify: command"]
    ok = True
    for command in commands:
        passed, detail = run_gate(command, cwd, timeout)
        pieces.append(detail)
        ok = ok and passed
    return ok, " | ".join(pieces)


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


HEARTBEAT = ROOT / ".fleet" / "sister.heartbeat.json"
WORKTREE_ROOT = Path(os.environ.get("AO_WORKTREE_ROOT", str(Path.home() / "ao-worktrees")))
# Run registry: who is tracking which directive. Without it a claim's holder is
# just a string — indistinguishable from an agent that died mid-run, which is how
# a directive got consumed as "already in-flight" while nothing was running.
RUNS = ROOT / ".fleet" / "runs"
REPORTED = ROOT / ".fleet" / "reported"

#: The bounded worker pool: this many directives run concurrently, env-overridable
#: so an operator can widen or narrow the fleet without a code change.
DEFAULT_POOL_SIZE = 10


def pool_size() -> int:
    """Up to this many subagents run at once; ``FLEET_SISTER_POOL`` overrides it."""
    raw = os.environ.get("FLEET_SISTER_POOL", str(DEFAULT_POOL_SIZE))
    try:
        size = int(raw)
    except (TypeError, ValueError):
        size = DEFAULT_POOL_SIZE
    return max(1, size)


#: Live per-directive run slots, keyed by directive id. The old single ``IN_FLIGHT``
#: dict could track exactly one run; a pool of N concurrent subagents needs one
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
    * ``orphaned`` — someone else's untracked claim: escalate so the brain
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
    operator's `resume` was delivered and sat unread until the flag was cleared by
    hand. Controls are therefore always processed; only dispatch waits.
    """
    return bool(is_paused and not directive.get("control"))


def agent_id_for(directive_id: str) -> str:
    """The agent id for a directive: one lane, one name, derived from the order."""
    return f"subagent-{directive_id[:8]}"


def mark_run(directive_id: str, issue: int, agent_id: str) -> None:
    """Record that this loop is tracking a run for a directive.

    Written atomically (tmp + rename): readers outside the loop (the JSON gate,
    the brain, an operator) can otherwise catch a torn file mid-write — which is
    exactly how it was caught, by `json-lint` reading a half-written registry.

    The marker is per-directive, so N concurrent runs produce N markers. ``pid``
    stays the loop's — that is what prune/watchdog read for liveness — while
    ``child_pid`` and ``ts`` are refreshed by the run's own beater (see
    ``refresh_run``) so the marker doubles as that child's heartbeat.
    """
    RUNS.mkdir(parents=True, exist_ok=True)
    target = RUNS / f"{directive_id}.json"
    tmp = target.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(
            {
                "issue": issue,
                "agent": agent_id,
                "pid": os.getpid(),
                "child_pid": None,
                "started_at": _now(),
                "ts": _now(),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    tmp.replace(target)


def refresh_run(directive_id: str, child_pid: int | None = None) -> None:
    """Refresh one run marker's heartbeat; N live runs read as N live beats.

    The single ``sister.heartbeat.json`` can only name one child, so each run's
    own marker carries its liveness instead: the beater advances ``ts`` and
    records the subagent pid while the child works. ``pid`` is left untouched,
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
) -> None:
    """Append one per-run telemetry record; a bad append must not kill the loop.

    Telemetry is an observability signal, not the run itself — if the log write
    fails (disk full, bad permissions), the run outcome still gets reported over
    the channel; only the extra record is lost.
    """
    try:
        with RECORD_LOCK:
            telemetry.append_record(
                telemetry.RUNS_LOG,
                telemetry.build_record(
                    run_id=directive_id,
                    issue=str(issue),
                    agent=agent_id,
                    status=status,
                    started_at=started_at,
                    finished_at=finished_at,
                    detail=detail,
                ),
            )
    except (telemetry.TelemetryError, OSError) as exc:
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


def report_once(directive_id: str, key: str, message_type: str, body: str) -> bool:
    """Say something about a directive once, not once per watch cycle.

    A directive that is left pending is re-read every cycle; without this the
    loop would repeat the same report forever.
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
                   "--severity", "warn", "--body", body]
    subprocess.run(command, cwd=ROOT)
    return True


def clear_reported(directive_id: str) -> None:
    try:
        (REPORTED / f"{directive_id}.json").unlink()
    except OSError:
        pass


# --- controls (the operator's levers, relayed by the brain) -------------------
#
# The loop is the only place these can be honoured: it owns the run, the queue
# cursor and the process. `control.py` sends them; this decides what they mean.
PAUSED = ROOT / ".fleet" / "paused"
STOPPING = ROOT / ".fleet" / "stopping"
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


def apply_control(action: str, directive: dict, agent_id: str) -> str:
    """Translate a control action into a verdict the loop acts on.

    * ``continue`` — handled here; keep going.
    * ``dispatch-override`` — an operator override: skip the held-check (the
      caller already reaped the holder) and dispatch this directive.
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
    if action in ("refresh", "restart", "halt"):
        return action
    # Anything else cannot be handled by this build; the caller escalates ONCE and
    # consumes it. Measured: a control the loop did not understand stayed in the
    # inbox and was re-read every cycle, escalating hundreds of times a second.
    return "unknown"


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
    """Run one subagent and release its lane; the loop owns the claim.

    This is the body of one pool worker up to and including the release: it
    executes the subagent, then in ``finally`` clears the run marker, releases the
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

    def run_worker(
        directive: dict,
        slot: dict,
        agent_id: str,
        issue: int,
        worktree: Path | None,
        lane_env: dict[str, str],
        run_started_at: str,
    ) -> None:
        """One worker's full life: run the subagent, then derive and report the verdict.

        The claim is already taken and the lane provisioned by the loop; this runs
        the subagent (via ``_run_child``, which releases the claim in ``finally``),
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
        tail = f"[{where}] {tail}"
        # The verdict comes from evidence the loop runs itself — the issue's own
        # Verify: command, `make verify`, and the real board state — never from
        # the runner's prose (#279).
        gate_ok, gate_detail = gate_evidence(issue, worktree, args.timeout)
        # "The PR is merged" is not "the item is closed": at this point the branch,
        # the claim, the directive and the lane are still live. Close them out and
        # carry the verdict, so a partial close is visible. Only a run whose gates
        # passed is worth closing out.
        closeout = closeout_issue(issue) if (rc == 0 and gate_ok) else "SKIPPED (gates did not pass)"
        landed, landing_detail = landed_evidence(issue)
        run_status, prose_hint = verdict(rc, output, gate_ok, landed)
        tail = f"{tail} | {gate_detail} | {landing_detail} | close-out: {closeout} | prose-hint: {prose_hint}"
        record_run(directive_id, issue, agent_id, run_status, run_started_at, _now(), tail[:200])
        clear_reported(directive_id)
        if run_status == "done":
            subprocess.run(
                ["python3", CHANNEL, "report", "--from", "sister", "--correlation", directive_id,
                 "--type", "result", "--body", tail[:2000]],
                cwd=ROOT,
            )
        else:
            # The runner's exit code picks the severity; the *evidence* decides
            # whether this is a success at all. Prose no longer picks either.
            severity = "warn" if rc == 0 else "critical"
            subprocess.run(
                ["python3", CHANNEL, "escalate", "--from", "sister", "--correlation", directive_id,
                 "--severity", severity, "--body", tail[:2000]],
                cwd=ROOT,
            )

    idle_printed = False
    paused_printed = False
    pool = pool_size()
    while True:
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
            # sat unread until an operator cleared the flag by hand.
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

        if active >= pool:
            # The pool is full: this directive is work but there is no free worker.
            # Leave it pending (do NOT consume it) and let a freed slot take it next
            # cycle. The skip list keeps `watch` from re-returning only the already
            # running directives; this one stays queued for a later slot.
            print(f"[terminal] pool full ({active}/{pool}) — holding #{issue} pending", flush=True)
            time.sleep(args.idle_sleep)
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
                print(f"[terminal] #{issue} in flight (held by {holder}, run tracked) — left pending", flush=True)
                report_once(
                    directive_id,
                    key=f"in-flight:{holder}",
                    message_type="result",
                    body=f"#{issue} in flight (held by {holder}, run tracked) — directive left pending; "
                    "the tracking loop reports the result",
                )
                if args.once:
                    return 0
                continue
            if action == "self-heal":
                # Our own orphan: a previous loop of ours was stopped mid-run. The
                # work was never reported, so self-heal — reap the dead claim, then
                # dispatch below in this same cycle.
                print(f"[terminal] #{issue} orphaned by our own dead run — reaping and re-dispatching", flush=True)
                reap = subprocess.run(
                    ["python3", str(ROOT / "governance" / "dispatch" / "cli.py"), "reap",
                     "--older-than-minutes", "0", "--issue", str(issue), "--reaper", "sister-self-heal"],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                )
                report_once(
                    directive_id,
                    key=f"self-heal:{holder}:{reap.returncode}",
                    message_type="escalate",
                    body=f"#{issue} claimed by our own run that no longer exists; reaped "
                    f"(rc={reap.returncode}) and re-dispatching — the previous run was never reported",
                )
                if reap.returncode != 0:
                    print(f"[terminal] self-heal reap failed: {reap.stdout}{reap.stderr}", file=sys.stderr, flush=True)
                    if args.once:
                        return 1
                    continue
                clear_reported(directive_id)
            else:
                # A claim nobody is tracking, held by someone else: the brain decides.
                print(f"[terminal] #{issue} held by {holder} with no live run — escalating, left pending", flush=True)
                report_once(
                    directive_id,
                    key=f"orphaned:{holder}",
                    message_type="escalate",
                    body=f"#{issue} is held by {holder} but no loop is tracking that run — the claim is "
                    "orphaned; reap it (dispatch reap) so the directive can be dispatched. Directive left pending.",
                )
                if args.once:
                    return 0
                continue

        print(f"[terminal] executing directive {directive_id} (issue {issue})", flush=True)
        mark_run(directive_id, issue, agent_id)
        run_started_at = _now()
        record_run(directive_id, issue, agent_id, "started", run_started_at)
        lane = (directive.get("task") or {}).get("lane") or ""
        claimed, claim_output = claim_issue(issue, agent_id, lane, directive_id)
        if not claimed:
            # Escalate ONCE and leave the directive pending: the refusal is usually
            # a stale snapshot (a freshly filed child), and after a board refresh the
            # next cycle claims it. Re-escalating every cycle was measured — four
            # lines a second for one refusal.
            print(f"[terminal] claim refused for #{issue}: {claim_output}", file=sys.stderr, flush=True)
            report_once(
                directive_id,
                key=f"claim-refused:{claim_output[-80:]}",
                message_type="escalate",
                body=f"claim refused: {claim_output[-400:]}",
            )
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
        worktree, _branch, lane_env = tree if tree else (None, None, {})
        # One worker = one lane = one claim = one run marker = one report. The
        # claim is taken here (by the loop) and released in the worker's `finally`,
        # so a dead child can never strand an issue.
        slot = register_run(directive_id, issue, agent_id)
        worker = threading.Thread(
            target=run_worker,
            args=(directive, slot, agent_id, issue, worktree, lane_env, run_started_at),
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
    run.add_argument("--runner", default="claude -p")
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
