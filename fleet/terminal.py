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
import subprocess
import sys
import time
from pathlib import Path

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


def build_prompt(directive: dict) -> str:
    """The subagent prompt: one issue, claim-first, evidence over assertion."""
    task = directive.get("task") or {}
    issue = task.get("issue")
    lane = task.get("lane") or ""
    directive_id = directive.get("id", "")
    model = directive.get("model") or {}
    body = directive.get("body", "")
    return (
        "You are an epic-focused subagent in the kushin77/agent-orchestrator fleet, "
        "steered by the brain through the sister session. "
        "Work in the repo checkout; disk worktrees under ~/ao-worktrees, never /tmp.\n\n"
        f"BRAIN DIRECTIVE {directive_id} — model {model.get('tier', 'flash')}/{model.get('thinking', 'none')}:\n{body}\n\n"
        "Do exactly this, nothing else:\n"
        f"1. Claim it: python3 governance/dispatch/cli.py claim --issue {issue} --agent subagent --lane {lane} --directive {directive_id}\n"
        "   (a REFUSED claim means STOP and report the exact reason — never work around it).\n"
        f"2. Implement issue #{issue} to completion; open a PR whose body carries 'Closes #{issue}' and the ACTUAL 'make verify' output as evidence.\n"
        "3. Release the claim when done.\n"
        "Return a short report: PR number, verify output summary, files touched. "
        "If anything fails, report the exact error instead of improvising."
    )


def build_command(directive: dict, runner: str) -> list[str]:
    """Runner must accept the prompt as its final argument (e.g. `claude -p`)."""
    return shlex.split(runner) + [build_prompt(directive)]


def run_once(directive: dict, runner: str, timeout: float, dry_run: bool) -> tuple[int, str]:
    """Run one subagent for one directive; return (exit code, captured output)."""
    command = build_command(directive, runner)
    if dry_run:
        print("DRY-RUN:", " ".join(shlex.quote(part) for part in command), flush=True)
        return 0, "DRY-RUN (not executed)"
    try:
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, f"runner timed out after {timeout}s"
    return result.returncode, (result.stdout or "") + (result.stderr or "")


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


def loop(args: argparse.Namespace) -> int:
    idle_printed = False
    while True:
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

        print(f"[terminal] executing directive {directive_id} (issue {issue})", flush=True)
        rc, output = run_once(directive, args.runner, args.timeout, args.dry_run)
        tail = (output.strip()[-600:]) or f"runner exited {rc} with no output"
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
