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


def run_once(
    directive: dict,
    runner: str,
    timeout: float,
    dry_run: bool,
    agent_id: str,
    worktree: Path | None = None,
    env: dict[str, str] | None = None,
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
    IN_FLIGHT["child"] = child
    try:
        output, _ = child.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        child.kill()
        output, _ = child.communicate()
        return 124, f"runner timed out after {timeout}s: {(output or '')[-400:]}"
    finally:
        IN_FLIGHT["child"] = None
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
    operator to find, so the caller now gets the channel's own words.
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
    return result.returncode == 0, (result.stdout + result.stderr).strip()


def stop_and_release(reason: str) -> None:
    """A stopped loop must not strand its claim: take the subagent down, free it.

    Observed live: restarting the sister loop mid-run killed it before the
    `finally`, so #167 stayed claimed by an agent that no longer existed — the
    exact wedge the reap tool exists to clean, recreated by an operator restart.
    """
    child = IN_FLIGHT.get("child")
    if child is not None and child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
    issue, agent_id, directive_id = IN_FLIGHT.get("issue"), IN_FLIGHT.get("agent_id"), IN_FLIGHT.get("directive")
    if issue is None or not agent_id:
        return
    held = subprocess.run(
        ["python3", str(ROOT / "governance" / "dispatch" / "cli.py"), "held", "--issue", str(issue)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if held.returncode != 0:
        # Nothing was held (the claim was refused, or already released): say so
        # rather than reporting a release that never happened.
        subprocess.run(
            ["python3", CHANNEL, "escalate", "--from", "sister", "--correlation", directive_id or "unknown",
             "--severity", "warn", "--body", f"operator stopped the loop mid-run on #{issue} ({reason}); "
             "no live claim to release"[:2000]],
            cwd=ROOT,
        )
        return
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


def looks_refused(output: str) -> bool:
    """A subagent that stopped on a refusal must not read as success."""
    upper = output.upper()
    return "REFUSED" in upper or "NO WORK DONE" in upper or "NO REAL ISSUE" in upper


HEARTBEAT = ROOT / ".fleet" / "sister.heartbeat.json"
WORKTREE_ROOT = Path(os.environ.get("AO_WORKTREE_ROOT", str(Path.home() / "ao-worktrees")))
# What the loop is currently executing, so a stop signal can free the claim.
IN_FLIGHT: dict[str, object] = {"issue": None, "agent_id": None, "directive": None, "child": None}
# Run registry: who is tracking which directive. Without it a claim's holder is
# just a string — indistinguishable from an agent that died mid-run, which is how
# a directive got consumed as "already in-flight" while nothing was running.
RUNS = ROOT / ".fleet" / "runs"
REPORTED = ROOT / ".fleet" / "reported"


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
    """
    RUNS.mkdir(parents=True, exist_ok=True)
    target = RUNS / f"{directive_id}.json"
    tmp = target.with_suffix(".tmp")
    tmp.write_text(
        json.dumps({"issue": issue, "agent": agent_id, "pid": os.getpid(), "started_at": _now()}) + "\n",
        encoding="utf-8",
    )
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
    except telemetry.TelemetryError as exc:
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
    HEARTBEAT.parent.mkdir(parents=True, exist_ok=True)
    tmp = HEARTBEAT.with_suffix(".tmp")
    tmp.write_text(json.dumps(entry) + "\n", encoding="utf-8")
    tmp.replace(HEARTBEAT)


class RunBeater:
    """Keeps the beat fresh while a child runs; `stop()` is synchronous.

    `stop()` joins the thread rather than merely setting a flag: a beater that
    outlives its owner writes the *owner's* idea of the world — measured, a test
    beater left running wrote `commit: abc1234` and a pytest pid into the live
    heartbeat when monkeypatch restored the real path.
    """

    def __init__(self, started_at: str, commit: str, issue: int, agent_id: str, interval: float) -> None:
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._beat,
            args=(started_at, commit, issue, agent_id, interval),
            name="fleet-heartbeat",
            daemon=True,
        )

    def _beat(self, started_at: str, commit: str, issue: int, agent_id: str, interval: float) -> None:
        while not self._stop.wait(interval):
            child = IN_FLIGHT.get("child")
            write_heartbeat(
                f"working:#{issue}",
                started_at=started_at,
                commit=commit,
                issue=issue,
                agent=agent_id,
                child_pid=getattr(child, "pid", None),
            )

    def start(self) -> RunBeater:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=2.0)


def start_beating(
    started_at: str,
    commit: str,
    issue: int,
    agent_id: str,
    interval: float = HEARTBEAT_INTERVAL_SECONDS,
) -> RunBeater:
    """Start a run's beater; the caller MUST `stop()` it in a finally block."""
    return RunBeater(started_at, commit, issue, agent_id, interval).start()


def loop(args: argparse.Namespace) -> int:
    if not singleton.guard("sister", "bash fleet/run-fleet.sh (or: bash fleet/terminal.sh)"):
        return 1
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, handle_stop)
    started_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    commit = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True
    ).stdout.strip() or "unknown"
    idle_printed = False
    paused_printed = False
    while True:
        if stopping():
            # `stop` takes effect between runs — and an idle loop is between runs.
            # Checking only after a run meant `stop` on an idle fleet did nothing.
            set_flag(STOPPING, False)
            write_heartbeat("stopped", started_at=started_at, commit=commit)
            print("[terminal] control:stop — stopping the loop cleanly", flush=True)
            return 0
        if paused():
            write_heartbeat("paused", started_at=started_at, commit=commit)
        else:
            write_heartbeat("idle", started_at=started_at, commit=commit)
        watch = subprocess.run(
            ["python3", CHANNEL, "watch", "--timeout-seconds", str(args.watch_timeout), "--interval", "1"],
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
                        f"in-flight={IN_FLIGHT.get('issue') or 'none'} runs={len(list(RUNS.glob('*.json'))) if RUNS.exists() else 0}"
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

            verdict = apply_control(control, directive, agent_id_for(directive_id))
            print(f"[terminal] control:{control} — {verdict}", flush=True)
            if verdict == "unknown":
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
            if verdict == "kill":
                handled(directive_id)
                subprocess.run(
                    ["python3", CHANNEL, "escalate", "--from", "sister", "--correlation", directive_id,
                     "--severity", "warn", "--body", "control:kill — run terminated, claim released"],
                    cwd=ROOT,
                )
                return 128 + signal.SIGTERM
            if verdict == "halt":
                handled(directive_id)
                subprocess.run(
                    ["python3", CHANNEL, "report", "--from", "sister", "--correlation", directive_id,
                     "--type", "ack", "--body", "control:halt — stopping the fleet"],
                    cwd=ROOT,
                )
                return 0
            if verdict == "refresh":
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
            if verdict == "restart":
                # Re-exec the same code: no pull, no verify — the fast lever.
                handled(directive_id)
                subprocess.run(
                    ["python3", CHANNEL, "report", "--from", "sister", "--correlation", directive_id,
                     "--type", "ack", "--body", "control:restart — re-executing the loop"],
                    cwd=ROOT,
                )
                print("[terminal] restart requested — re-exec", flush=True)
                os.execv(sys.executable, [sys.executable, *sys.argv])
            if verdict == "dispatch-override":
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
                     "--type", "ack", "--body", f"control:{control} — {verdict}"],
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

        agent_id = agent_id_for(directive_id)
        IN_FLIGHT["issue"] = issue
        IN_FLIGHT["agent_id"] = agent_id
        IN_FLIGHT["directive"] = directive_id
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
        write_heartbeat("working", started_at=started_at, commit=commit, issue=issue, agent=agent_id)
        mark_run(directive_id, issue, agent_id)
        run_started_at = _now()
        record_run(directive_id, issue, agent_id, "started", run_started_at)
        beater = start_beating(started_at, commit, issue, agent_id)
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
        try:
            rc, output = run_once(
                directive, args.runner, args.timeout, args.dry_run, agent_id, worktree, lane_env
            )
        finally:
            beater.stop()
            IN_FLIGHT["issue"] = None
            IN_FLIGHT["agent_id"] = None
            IN_FLIGHT["directive"] = None
            clear_run(directive_id)
            released, release_output = release_issue(issue, agent_id)
            if not released:
                print(f"[terminal] release of #{issue} FAILED: {release_output}", file=sys.stderr, flush=True)
                subprocess.run(
                    ["python3", CHANNEL, "escalate", "--from", "sister", "--correlation", directive_id,
                     "--severity", "warn", "--body", f"release of #{issue} failed: {release_output[-400:]}"],
                    cwd=ROOT,
                )
        where = f"worktree {worktree}" if worktree else "shared checkout"
        tail = (output.strip()[-600:]) or f"runner exited {rc} with no output"
        tail = f"[{where}] {tail}"
        refused = looks_refused(output)
        run_status = "done" if (rc == 0 and not refused) else "failed"
        record_run(directive_id, issue, agent_id, run_status, run_started_at, _now(), tail[:200])
        clear_reported(directive_id)
        if rc == 0 and not refused:
            subprocess.run(
                ["python3", CHANNEL, "report", "--from", "sister", "--correlation", directive_id,
                 "--type", "result", "--body", tail],
                cwd=ROOT,
            )
        elif refused:
            subprocess.run(
                ["python3", CHANNEL, "escalate", "--from", "sister", "--correlation", directive_id,
                 "--severity", "warn", "--body", tail],
                cwd=ROOT,
            )
        else:
            subprocess.run(
                ["python3", CHANNEL, "escalate", "--from", "sister", "--correlation", directive_id,
                 "--severity", "critical", "--body", tail],
                cwd=ROOT,
            )
        if args.once:
            return 0
        if stopping():
            # `stop` is graceful: it takes effect between runs, never mid-run.
            set_flag(STOPPING, False)
            write_heartbeat("stopped", started_at=started_at, commit=commit)
            print("[terminal] control:stop — run finished; stopping the loop cleanly", flush=True)
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
