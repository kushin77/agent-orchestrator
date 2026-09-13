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
import time
from datetime import datetime, timezone
from pathlib import Path

import singleton

ROOT = Path(__file__).resolve().parent.parent
CHANNEL = str(ROOT / "fleet" / "channel.py")


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


def build_prompt(directive: dict, agent_id: str = "subagent", worktree: Path | None = None) -> str:
    """The subagent prompt: one issue, one worktree, evidence over assertion.

    The claim is *owned by the loop*, not by the subagent: a subagent that died
    mid-task used to leave its claim wedged until the 24h TTL, because nobody
    was left to release it. The loop claims before the spawn and releases in a
    `finally`, so a dead subagent can no longer strand an issue.
    """
    task = directive.get("task") or {}
    issue = task.get("issue")
    lane = task.get("lane") or ""
    directive_id = directive.get("id", "")
    model = directive.get("model") or {}
    body = directive.get("body", "")
    where = (
        f"Your worktree is {worktree} (branch {worktree.name}). Work ONLY there — never in the "
        "shared checkout, which other lanes are using.\n"
        if worktree is not None
        else "Work in the repo checkout; disk worktrees under ~/ao-worktrees, never /tmp.\n"
    )
    return (
        "You are an epic-focused subagent in the kushin77/agent-orchestrator fleet, "
        "steered by the brain through the sister session. "
        f"{where}\n"
        f"BRAIN DIRECTIVE {directive_id} — model {model.get('tier', 'flash')}/{model.get('thinking', 'none')}:\n{body}\n\n"
        "Do exactly this, nothing else:\n"
        f"1. Issue #{issue} is ALREADY CLAIMED for you as `{agent_id}` (lane {lane or 'n/a'}) — do NOT "
        "run claim and do NOT run release; the loop manages the claim around your run.\n"
        f"2. Implement issue #{issue} to completion; open a PR whose body carries 'Closes #{issue}' and the ACTUAL 'make verify' output as evidence.\n"
        "Return a short report: PR number, verify output summary, files touched. "
        "If anything fails, report the exact error instead of improvising."
    )


def build_command(directive: dict, runner: str, agent_id: str, worktree: Path | None = None) -> list[str]:
    """Runner must accept the prompt as its final argument (e.g. `claude -p`)."""
    return shlex.split(runner) + [build_prompt(directive, agent_id, worktree)]


def run_once(
    directive: dict,
    runner: str,
    timeout: float,
    dry_run: bool,
    agent_id: str,
    worktree: Path | None = None,
) -> tuple[int, str]:
    """Run one subagent for one directive; return (exit code, captured output).

    Uses Popen rather than `subprocess.run` so the child is reachable from the
    stop handler: a stopped loop must take its subagent down with it instead of
    orphaning it.
    """
    command = build_command(directive, runner, agent_id, worktree)
    cwd = str(worktree) if worktree is not None else str(ROOT)
    if dry_run:
        print("DRY-RUN:", " ".join(shlex.quote(part) for part in command), flush=True)
        return 0, f"DRY-RUN (not executed) in {cwd}"
    try:
        child = subprocess.Popen(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
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


def provision_worktree(issue: int, directive_id: str) -> tuple[Path, str] | None:
    """One lane = one branch = one working tree, provisioned before the spawn.

    Subagents used to run in the shared checkout, so a single dispatch checked
    that checkout out onto a feature branch and two lanes shared one working
    tree. Returns (path, branch), or None when provisioning failed (the caller
    then runs in the shared checkout and must say so).
    """
    slug = f"ao-{issue}-{directive_id[:8]}"
    path = WORKTREE_ROOT / slug
    branch = f"issue-{issue}-{directive_id[:8]}"
    if path.exists():
        return path, branch
    try:
        WORKTREE_ROOT.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"[terminal] cannot create {WORKTREE_ROOT}: {exc}", file=sys.stderr, flush=True)
        return None
    subprocess.run(["git", "-C", str(ROOT), "fetch", "origin", "master"], capture_output=True, text=True)
    add = subprocess.run(
        ["git", "-C", str(ROOT), "worktree", "add", "-b", branch, str(path), "origin/master"],
        capture_output=True,
        text=True,
    )
    if add.returncode != 0:
        print(f"[terminal] worktree add failed: {add.stderr.strip()[-300:]}", file=sys.stderr, flush=True)
        return None
    return path, branch


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


def write_heartbeat(state: str, *, started_at: str, commit: str) -> None:
    """Publish liveness *and* the commit this process is running.

    A loop left running stale code made a healthy fleet look broken: the status
    surface could not tell "not running" from "running the pre-fix build".
    """
    entry = {
        "pid": os.getpid(),
        "state": state,
        "started_at": started_at,
        "commit": commit,
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    HEARTBEAT.parent.mkdir(parents=True, exist_ok=True)
    tmp = HEARTBEAT.with_suffix(".tmp")
    tmp.write_text(json.dumps(entry) + "\n", encoding="utf-8")
    tmp.replace(HEARTBEAT)


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
    while True:
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
        if control:
            if control == "halt":
                print("[terminal] HALT received — stopping the loop")
                return 0
            if control == "poke":
                print("[terminal] poke received — loop alive", flush=True)
                subprocess.run(
                    ["python3", CHANNEL, "report", "--from", "sister", "--correlation", directive_id,
                     "--type", "ack", "--body", "poke received — loop alive"],
                    cwd=ROOT,
                )
                if args.once:
                    return 0
                continue
            if control == "refresh":
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
            subprocess.run(
                ["python3", CHANNEL, "escalate", "--from", "sister", "--correlation", directive_id,
                 "--severity", "warn", "--body", f"unknown control action {control}"],
                cwd=ROOT,
            )
            if args.once:
                return 1
            continue

        issue = directive_issue(directive)
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

        agent_id = f"subagent-{directive_id[:8]}"
        IN_FLIGHT["issue"] = issue
        IN_FLIGHT["agent_id"] = agent_id
        IN_FLIGHT["directive"] = directive_id
        held = subprocess.run(
            ["python3", str(ROOT / "governance" / "dispatch" / "cli.py"), "held", "--issue", str(issue)],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if held.returncode == 0:
            try:
                holder = json.loads(held.stdout).get("agent")
            except json.JSONDecodeError:
                holder = "unknown"
            print(f"[terminal] #{issue} already held by {holder} — not re-dispatching", flush=True)
            subprocess.run(
                ["python3", CHANNEL, "report", "--from", "sister", "--correlation", directive_id,
                 "--type", "result", "--body", f"#{issue} already in-flight (held by {holder}) — not re-dispatched"],
                cwd=ROOT,
            )
            if args.once:
                return 0
            continue

        print(f"[terminal] executing directive {directive_id} (issue {issue})", flush=True)
        write_heartbeat("working", started_at=started_at, commit=commit)
        lane = (directive.get("task") or {}).get("lane") or ""
        claimed, claim_output = claim_issue(issue, agent_id, lane, directive_id)
        if not claimed:
            print(f"[terminal] claim refused for #{issue}: {claim_output}", file=sys.stderr, flush=True)
            subprocess.run(
                ["python3", CHANNEL, "escalate", "--from", "sister", "--correlation", directive_id,
                 "--severity", "warn", "--body", f"claim refused: {claim_output[-400:]}"],
                cwd=ROOT,
            )
            if args.once:
                return 1
            continue

        tree = provision_worktree(issue, directive_id)
        if tree is None:
            subprocess.run(
                ["python3", CHANNEL, "escalate", "--from", "sister", "--correlation", directive_id,
                 "--severity", "warn",
                 "--body", f"no isolated worktree for #{issue} — running in the shared checkout"[:200]],
                cwd=ROOT,
            )
        worktree, branch = tree if tree else (None, None)
        try:
            rc, output = run_once(directive, args.runner, args.timeout, args.dry_run, agent_id, worktree)
        finally:
            IN_FLIGHT["issue"] = None
            IN_FLIGHT["agent_id"] = None
            IN_FLIGHT["directive"] = None
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
