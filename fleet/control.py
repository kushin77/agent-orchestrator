#!/usr/bin/env python3
"""Brain-side control plane — the human-override terminal.

Run these from the brain/human terminal to steer and maintain the fleet without
stopping it:

    python3 fleet/control.py start      # start both rungs (brain + sister)
    python3 fleet/control.py status     # rungs, pause/stop flags, tracked runs
    python3 fleet/control.py pause      # hold the queue (the run in flight finishes)
    python3 fleet/control.py resume     # pull the next order again
    python3 fleet/control.py stop       # exit after the current run, cleanly
    python3 fleet/control.py kill       # terminate the run, release its claim, escalate
    python3 fleet/control.py restart    # re-exec the same code (fast)
    python3 fleet/control.py refresh    # git pull --ff-only + snapshot + verify + re-exec
    python3 fleet/control.py update     # refresh + rebuild the knowledge index
    python3 fleet/control.py override --issue N   # force #N past a live claim
    python3 fleet/control.py poke       # ping the sister; it acks (liveness)
    python3 fleet/control.py halt       # stop the fleet
    python3 fleet/control.py debug      # full non-destructive state dump
    python3 fleet/control.py watch      # idle-watch the slog (same as listen)
    python3 fleet/control.py health     # tri-state signal: 0 healthy/1 degraded/2 failing
    python3 fleet/control.py cron <sub> # fleet cron job → python3 fleet/cron.py install|status|run|respawn|disable|enable|uninstall

Roles (see fleet/profiles/brain.md and fleet/directive.json): the sister is a
DUMB terminal (DeepSeek v4.1 Flash, no thinking); the brain is DSv4PM with human
override and issues every order; THIS terminal is where the operator overrides
the entire fleet. The operator orders the brain — never the sister directly
(the channel refuses it).
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHANNEL = str(ROOT / "fleet" / "channel.py")


def _run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if check and result.returncode != 0:
        print(f"  FAIL {cmd[0]} rc={result.returncode}: {(result.stdout + result.stderr)[-400:]}", file=sys.stderr)
        raise SystemExit(result.returncode or 1)
    return result


def _send_control(action: str) -> None:
    message = {
        "from": "brain",
        "to": "sister",
        "type": "directive",
        "control": action,
        "body": f"control:{action}",
    }
    path = ROOT / ".fleet" / f"control-{uuid.uuid4().hex[:8]}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(message))
    _run(["python3", CHANNEL, "send", "--message", str(path)])
    path.unlink(missing_ok=True)


def cmd_refresh(args: argparse.Namespace) -> int:
    print("== refresh: pull --ff-only ==")
    _run(["git", "pull", "--ff-only"], check=False)
    print("== refresh: board snapshot ==")
    _run(["python3", "governance/dispatch/cli.py", "snapshot", "--from-github"], check=False)
    print("== refresh: make verify ==")
    _run(["make", "verify"], check=False)
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    cmd_refresh(args)
    print("== update: rebuild knowledge index ==")
    _run(["python3", "governance/knowledge/cli.py", "build"], check=False)
    return 0


def _loop_pid() -> int | None:
    """The sister loop's pid, from its heartbeat — the handle for out-of-band signals.

    Process levers cannot travel by mailbox alone: the loop is blocked in the
    child run while a task is in flight, so a control message would sit unread
    until the task ends. Signals are how a caller takes effect immediately.
    """
    beat = ROOT / ".fleet" / "sister.heartbeat.json"
    try:
        return int(json.loads(beat.read_text(encoding="utf-8")).get("pid"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def _signal_loop(signum: int, label: str) -> None:
    """Signal the loop after logging the order in the channel (audit trail)."""
    pid = _loop_pid()
    if pid is None:
        print(f"  no loop heartbeat — nothing to {label}; the fleet is not running")
        return
    try:
        os.kill(pid, signum)
    except OSError as exc:
        print(f"  cannot {label} pid {pid}: {exc}", file=sys.stderr)
        raise SystemExit(1)
    print(f"  {label}: signalled pid {pid} — the loop releases its claim and escalates")


def cmd_poke(args: argparse.Namespace) -> int:
    _send_control("poke")
    print("poke sent — the sister will ack on its next poll")
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    """Start whichever rungs are missing; clear stale queue flags when the sister starts.

    Three traps found by sweeping the controls live: a `stopping` flag left behind by
    a loop that died would kill every newly started loop at its first cycle; a
    combined pgrep reported "already running" because it matched the *other* rung;
    and refusing to start anything while one rung lived left the fleet half up.
    """
    rungs = (
        ("brain", "fleet/brain.py", "fleet/brain.sh"),
        ("sister", "fleet/terminal.py", "fleet/terminal.sh"),
    )
    live = {}
    for rung, pattern, _script in rungs:
        probe = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True)
        live[rung] = probe.stdout.split()[0] if probe.returncode == 0 and probe.stdout.split() else None

    missing = [(rung, script) for rung, _pattern, script in rungs if live[rung] is None]
    print("fleet state: " + ", ".join(f"{rung}={pid or 'down'}" for rung, pid in live.items()))
    if not missing:
        print("both rungs are already up — use status, or stop/kill/restart")
        return 0

    fleet = ROOT / ".fleet"
    starting_sister = any(rung == "sister" for rung, _ in missing)
    if starting_sister:
        for flag in ("paused", "stopping"):
            if (fleet / flag).exists():
                (fleet / flag).unlink()
                print(f"  cleared stale {flag} flag (queue state from the previous loop)")

    for rung, script in missing:
        subprocess.Popen(
            ["setsid", "bash", script], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        print(f"  started {script}")
    time.sleep(3)
    _run(["python3", CHANNEL, "status"], check=False)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    print("== status: rungs, flags, runs ==")
    _run(["python3", CHANNEL, "status"], check=False)
    fleet = ROOT / ".fleet"
    for flag in ("paused", "stopping"):
        print(f"  {flag}: {'yes' if (fleet / flag).exists() else 'no'}")
    runs = sorted(p.name for p in (fleet / "runs").glob("*.json")) if (fleet / "runs").exists() else []
    print(f"  tracked runs: {', '.join(runs) if runs else 'none'}")
    pid = _loop_pid()
    print(f"  loop pid (from heartbeat): {pid if pid else 'none'}")
    return 0


def cmd_pause(args: argparse.Namespace) -> int:
    _send_control("pause")
    print("pause sent — the loop stops pulling new orders; the run in flight finishes")
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    _send_control("resume")
    print("resume sent — the loop pulls the next order again")
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    """Graceful: the loop exits *between* runs, so the task in flight survives."""
    _send_control("stop")
    print("stop sent — the loop exits after the current run finishes (never mid-run)")
    return 0


def cmd_kill(args: argparse.Namespace) -> int:
    """Immediate: signal the loop; its handler releases the run's claim and escalates."""
    _send_control("kill")
    print("kill sent — taking the loop down now")
    _signal_loop(signal.SIGTERM, "kill")
    return 0


def cmd_restart(args: argparse.Namespace) -> int:
    """Re-exec the same code: signal, wait for the release, relaunch."""
    pid = _loop_pid()
    if pid is None:
        print("no loop heartbeat — starting instead")
        return cmd_start(args)
    print(f"restart requested — signalling pid {pid}")
    _signal_loop(signal.SIGTERM, "restart")
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            break
        time.sleep(0.5)
    return cmd_start(args)


def cmd_override(args: argparse.Namespace) -> int:
    """Operator override: order the BRAIN to force a named issue past a live claim.

    The hierarchy holds even for an override — the operator orders the brain, the
    brain issues the control. The brain reaps the holder and the loop dispatches.
    """
    order = {
        "type": "directive",
        "task": {"issue": args.issue, "lane": args.lane or "override", "override": True},
        "body": args.body or f"operator override: dispatch #{args.issue} now, taking over any live claim",
    }
    path = ROOT / ".fleet" / f"override-{uuid.uuid4().hex[:8]}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(order))
    _run(["python3", CHANNEL, "order", "--message", str(path)])
    path.unlink(missing_ok=True)
    print(f"override for #{args.issue} ordered through the brain")
    return 0


def cmd_halt(args: argparse.Namespace) -> int:
    _send_control("halt")
    print("halt sent — the sister loop will stop cleanly")
    return 0


def cmd_debug(args: argparse.Namespace) -> int:
    print("== channel ==")
    _run(["python3", CHANNEL, "status"], check=False)
    print("== board ==")
    _run(["python3", "governance/dispatch/cli.py", "status"], check=False)
    print("== slog tail ==")
    slog = ROOT / ".fleet" / "slog.jsonl"
    if slog.exists():
        lines = slog.read_text().splitlines()
        for line in lines[-args.tail :]:
            entry = json.loads(line)
            print(f"  {entry.get('ts')} {entry.get('type')} {entry.get('from')}->{entry.get('to')} corr={entry.get('correlation_id','')[:8]} {entry.get('severity') or '-'}")
    else:
        print("  (no slog yet)")
    print("== sister loop process ==")
    _run(["pgrep", "-af", "fleet/terminal.py"], check=False)
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    return subprocess.call(["python3", CHANNEL, "listen", "--timeout-seconds", "0"], cwd=ROOT)


def cmd_health(args: argparse.Namespace) -> int:
    return subprocess.call(
        ["python3", str(ROOT / "fleet" / "health.py"), "check", "--stale-minutes", str(args.stale_minutes)],
        cwd=ROOT,
    )


def cmd_cron(args: argparse.Namespace) -> int:
    """Passthrough to the fleet cron manager (install/status/run/respawn/disable/enable/uninstall)."""
    return subprocess.call(
        ["python3", str(ROOT / "fleet" / "cron.py"), *args.cron_args],
        cwd=ROOT,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fleet-control", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, func in (
        ("start", cmd_start),
        ("status", cmd_status),
        ("refresh", cmd_refresh),
        ("update", cmd_update),
        ("poke", cmd_poke),
        ("pause", cmd_pause),
        ("resume", cmd_resume),
        ("stop", cmd_stop),
        ("kill", cmd_kill),
        ("restart", cmd_restart),
        ("halt", cmd_halt),
        ("override", cmd_override),
        ("debug", cmd_debug),
        ("watch", cmd_watch),
        ("health", cmd_health),
        ("cron", cmd_cron),
    ):
        sub.add_parser(name, help=f"control.{name}").set_defaults(func=func)
    debug = sub.choices["debug"]
    debug.add_argument("--tail", type=int, default=20)
    override = sub.choices["override"]
    override.add_argument("--issue", type=int, required=True)
    override.add_argument("--lane", default=None)
    override.add_argument("--body", default=None)
    health = sub.choices["health"]
    health.add_argument("--stale-minutes", type=float, default=30.0)
    cron = sub.choices["cron"]
    cron.add_argument("cron_args", nargs=argparse.REMAINDER, help="passed through to fleet/cron.py")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
