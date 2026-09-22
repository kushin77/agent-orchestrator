#!/usr/bin/env python3
"""Brain-side control plane — the human-override terminal.

---knowledge---
module_id: fleet.control
system: fleet
app: fleet
solution_class: pattern
patterns: []
derives_from: null
owner_sme: unassigned
tier: L1
interfaces: [cmd_refresh, cmd_update, cmd_poke, cmd_start, cmd_status, cmd_pause, cmd_resume, cmd_stop, (+17 more)]
invariants: ""
gotchas: ""
related: []
do_not_duplicate: null
---knowledge---

Run these from the brain/human terminal to steer and maintain the fleet without
stopping it:

    python3 fleet/control.py start      # start both rungs (director + dispatcher)
    python3 fleet/control.py status     # rungs, pause/stop flags, tracked runs
    python3 fleet/control.py pause      # hold the queue (the run in flight finishes)
    python3 fleet/control.py resume     # pull the next order again
    python3 fleet/control.py stop       # exit after the current run, cleanly
    python3 fleet/control.py kill       # terminate the run, release its claim, escalate
    python3 fleet/control.py restart    # re-exec the same code (fast)
    python3 fleet/control.py refresh    # git pull --ff-only + snapshot + verify + re-exec
    python3 fleet/control.py update     # refresh + rebuild the knowledge index
    python3 fleet/control.py override --issue N   # force #N past a live claim
    python3 fleet/control.py drop --directive <id> --reason "<text>"  # dead-letter a wedged directive
    python3 fleet/control.py dead-letter [--directive <id>]  # inspect the dead-letter mailbox
    python3 fleet/control.py poke       # ping the dispatcher; it acks (liveness)
    python3 fleet/control.py halt       # stop the fleet
    python3 fleet/control.py debug      # full non-destructive state dump
    python3 fleet/control.py watch      # idle-watch the slog (same as listen)
    python3 fleet/control.py health     # tri-state signal: 0 healthy/1 degraded/2 failing
    python3 fleet/control.py cron <sub> # fleet cron job → python3 fleet/cron.py install|status|run|respawn|disable|enable|uninstall
    python3 fleet/control.py live       # ensure the rungs, then attach to the live `fleet` session
    python3 fleet/control.py attach     # alias for live

`live` is the principal's way in. It starts only the rungs that are missing (the
same rule `start` follows) and then attaches to a tmux session named `fleet` with
a `dashboard` window (`fleet/console.py`), a `brain` window, a `sister` window and
a `monitor` window — the last three tailing `.fleet/<rung>.log`. The session is a
VIEW, never the host: the rungs run detached and the watchdog/cron owns their
lifecycle, so attaching, detaching or killing the session never touches a run in
flight. `--dry-run` prints the exact tmux commands instead of running them.

Roles (see fleet/profiles/brain.md and fleet/directive.json): the dispatcher is a
DUMB terminal (DeepSeek v4.1 Flash, no thinking); the director is DSv4PM with human
override and issues every order; THIS terminal is where the principal overrides
the entire fleet. The principal orders the director — never the dispatcher directly
(the channel refuses it).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

import runaway
import runtime

ROOT = Path(__file__).resolve().parent.parent
CHANNEL = str(ROOT / "fleet" / "channel.py")
# The principal's live session. One name, used by the verb, by run-fleet.sh and by
# the header of the dashboard, so "attach to the fleet" means exactly one thing.
SESSION = runtime.SESSION
# The runtime dir relative to the checkout (or absolute when AO_FLEET_DIR points
# outside it). control.py keeps deriving `ROOT / FLEET_SUBDIR` at call time so the
# tests' `control.ROOT` redirect still works, while a second fleet points at its
# own tree.
try:
    FLEET_SUBDIR = runtime.FLEET_DIR.relative_to(runtime.ROOT)
except ValueError:
    FLEET_SUBDIR = runtime.FLEET_DIR
# The rungs the principal wants to see, in the order the windows are created.
LIVE_RUNGS = ("brain", "sister", "monitor")

# The mailboxes a directive can occupy, and the two terminal stores. The NAMES
# are re-derived from the same runtime root `fleet/runaway.py`,
# `fleet/channel.py` and `governance/lifecycle/directive.py` derive their own
# from, so a drop acts on the file the drain path actually reads and there is no
# second layout to keep in step (the reason `fleet/runtime.py` exists at all).
INBOX_DIR = "inbox"
SENT_DIR = "sent"
DONE_DIR = "done"
DEAD_LETTER_DIR = "dead-letter"

#: The board snapshot the LANDED test reads (issue #821). Committed, so it is
#: readable offline; refreshed by `governance/dispatch/cli.py snapshot`.
SNAPSHOT = Path(".board") / "snapshot.json"


def _run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if check and result.returncode != 0:
        print(f"  FAIL {cmd[0]} rc={result.returncode}: {(result.stdout + result.stderr)[-400:]}", file=sys.stderr)
        raise SystemExit(result.returncode or 1)
    return result


def _send_control(action: str, task: dict | None = None, body: str | None = None) -> None:
    """Send one control verb to the dispatcher over the existing channel.

    ``task`` carries the target of a control that acts on a NAMED directive
    (``drop`` names the order it is retiring) — the control's own id and the
    directive it acts on are different things, and conflating them would retire
    the control instead of the wedged order (issue #754).
    """
    message = {
        "from": "brain",
        "to": "sister",
        "type": "directive",
        "control": action,
        "body": body or f"control:{action}",
    }
    if task:
        message["task"] = task
    path = ROOT / FLEET_SUBDIR / f"control-{uuid.uuid4().hex[:8]}.json"
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
    """The dispatcher loop's pid, from its heartbeat — the handle for out-of-band signals.

    Process levers cannot travel by mailbox alone: the loop is blocked in the
    child run while a task is in flight, so a control message would sit unread
    until the task ends. Signals are how a caller takes effect immediately.
    """
    beat = ROOT / FLEET_SUBDIR / "sister.heartbeat.json"
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
    """Start whichever rungs are missing; clear stale queue flags when the dispatcher starts.

    Three traps found by sweeping the controls live: a `stopping` flag left behind by
    a loop that died would kill every newly started loop at its first cycle; a
    combined pgrep reported "already running" because it matched the *other* rung;
    and refusing to start anything while one rung lived left the fleet half up.

    Each rung is started detached with stdout+stderr appended to `.fleet/<rung>.log`
    — the same capture path the watchdog uses. Starting a rung into `DEVNULL` made
    the window the principal then attaches to show nothing at all.
    """
    rungs = (
        ("brain", "fleet/brain.py", "fleet/brain.sh"),
        ("sister", "fleet/terminal.py", "fleet/terminal.sh"),
    )
    ensure_logs()
    live = {}
    for rung, pattern, _script in rungs:
        probe = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True)
        live[rung] = probe.stdout.split()[0] if probe.returncode == 0 and probe.stdout.split() else None

    missing = [(rung, script) for rung, _pattern, script in rungs if live[rung] is None]
    print("fleet state: " + ", ".join(f"{rung}={pid or 'down'}" for rung, pid in live.items()))
    if not missing:
        print("both rungs are already up — use status, or stop/kill/restart")
        return 0

    fleet = ROOT / FLEET_SUBDIR
    starting_sister = any(rung == "sister" for rung, _ in missing)
    if starting_sister:
        for flag in ("paused", "stopping"):
            if (fleet / flag).exists():
                (fleet / flag).unlink()
                print(f"  cleared stale {flag} flag (queue state from the previous loop)")

    for rung, script in missing:
        handle = rung_log(rung).open("a", encoding="utf-8", buffering=1)
        try:
            subprocess.Popen(
                ["setsid", "bash", script], cwd=ROOT, stdout=handle, stderr=subprocess.STDOUT
            )
        finally:
            handle.close()
        print(f"  started {script} (stream: {rung_log(rung)})")
    time.sleep(3)
    _run(["python3", CHANNEL, "status"], check=False)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    print("== status: rungs, flags, runs ==")
    _run(["python3", CHANNEL, "status"], check=False)
    fleet = ROOT / FLEET_SUBDIR
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
    """Principal override: order the DIRECTOR to force a named issue past a live claim.

    The hierarchy holds even for an override — the principal orders the director, the
    director issues the control. The director reaps the holder and the loop dispatches.
    """
    order = {
        "type": "directive",
        "task": {"issue": args.issue, "lane": args.lane or "override", "override": True},
        "body": args.body or f"operator override: dispatch #{args.issue} now, taking over any live claim",
    }
    path = ROOT / FLEET_SUBDIR / f"override-{uuid.uuid4().hex[:8]}.json"
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


def cmd_drop(args: argparse.Namespace) -> int:
    """Principal lever: retire a wedged directive to the dead-letter mailbox.

    THE DEFECT THIS ROUTE EXISTS FOR (#799, measured on 2026-09-15)
    The verb used to *only* order the dispatcher to dead-letter the directive, and
    answered "the dispatcher will dead-letter directive …". That is a message the loop
    must PROCESS, so the remedy for a loop that cannot drain its mailbox had to
    travel through the mailbox that loop was not draining: the principal issued the
    drop and, 13 cycles later, the runaway guard retired the order instead — the
    principal's own lever was redundant, and it failed with ``FileNotFoundError``
    once the dispatcher had consumed the message anyway. A control that only takes
    effect once the loop is healthy is not a control.

    THE ROUTE IS CHOSEN BY WHERE THE ORDER IS, because that is what decides
    whether a message can have any effect at all:

    * **in the inbox** — this process retires it *now*, through the ONE
      implementation the automatic path uses (:func:`runaway.dead_letter`, issue
      #754), which moves the order out of the inbox and stamps the terminal
      artifact. It is effective with no loop running, and it is NOT relayed: a
      relay would make the loop re-retire an already-retired order, and
      ``dead_letter`` reads the envelope from the inbox — so the second write
      would overwrite the artifact's ``envelope`` with null and destroy the
      evidence of what the order carried. The mailbox is the interface, not the
      message.
    * **already in the dead-letter store** — terminal. Reported idempotently and
      never rewritten, so a second drop cannot degrade the first's record.
    * **a brain-minted authorisation** (``.fleet/sent/``) — this process will not
      move that record: ``governance/lifecycle/directive.py`` is its single owner
      and nothing else in this repository may move a directive between ``sent``
      and ``done`` (#821). The verb therefore RELAYS the control over the channel
      and says so, which is the one case where the mailbox message is still the
      only route.
    * **nowhere** — refused BY NAME. A drop naming an order that exists nowhere is
      not a drop, and silently succeeding would be indistinguishable from a dead
      loop.

    THE #821 INVARIANT IS PRESERVED: a drop can never retire an order whose change
    has already landed. Landed work is *finished*, not dead, and relabelling it a
    dead-letter would record a failure for work that succeeded and mask the
    authorisation path's own terminal move. Two hermetic sources answer it (the
    directive already consumed into ``done/``; the issue CLOSED in the board
    snapshot), and an undecidable subject is refused rather than defaulted to
    allowed.

    Exit codes are the repo tri-state: 0 retired / 1 refused (a measured reason) /
    2 CANNOT-ASSESS (the landed state or the target cannot be established).
    """
    fleet = ROOT / FLEET_SUBDIR
    directive = args.directive

    if not _directive_id_ok(directive):
        print(f"drop REFUSED: {directive!r} is not a directive id", file=sys.stderr)
        return 1

    where, path = _directive_location(fleet, directive)
    artifact = _dead_letter_artifact(fleet, directive)
    if where == "nowhere" and not artifact.exists():
        print(
            f"drop REFUSED: no record of {directive} in {fleet / INBOX_DIR}, "
            f"{fleet / SENT_DIR} or {fleet / DEAD_LETTER_DIR} — the named order "
            "exists nowhere, so there is nothing to retire",
            file=sys.stderr,
        )
        return 1

    # Already terminal: report it and touch nothing. Re-retiring an order is not
    # "more dead", it is a second write over the evidence the first one produced.
    if where == "dead-letter":
        record = runaway.record_shape(fleet, directive)
        print(
            f"drop: {directive} was already dead-lettered at {record.get('ts')} "
            f"(dropped_by={record.get('dropped_by')}) — terminal, nothing re-written"
        )
        return 0

    issue = args.issue if args.issue is not None else _directive_issue(path)
    landed = _order_landed(ROOT, issue, fleet, directive)
    if landed is True:
        print(
            f"drop REFUSED: #{issue} has already landed — the order is FINISHED, not dead. "
            "Dead-lettering it would record a failure for work that succeeded and mask the "
            "authorisation path's terminal move (#821). Retire the record where it is owned: "
            f"python3 governance/lifecycle/cli.py close --issue {issue}",
            file=sys.stderr,
        )
        return 1
    if landed is None:
        print(
            f"drop CANNOT-ASSESS: whether #{issue} has landed cannot be established, so refusing "
            f"to guess (#821 never defaults to allowed). Refresh the board snapshot with "
            "`python3 governance/dispatch/cli.py snapshot --from-github` and re-run",
            file=sys.stderr,
        )
        return 2

    if where == "inbox":
        reason = args.reason or "operator dead-letter (control:drop)"
        target = runaway.dead_letter(
            directive,
            reason,
            base=fleet,
            dropped_by=f"operator:{os.environ.get('AO_AGENT_ID') or 'unknown'}",
        )
        record = runaway.record_shape(fleet, directive)
        print(f"drop RETIRED {directive} (#{issue}) — the order is out of the inbox and terminal")
        print(f"  artifact: {target}")
        print(f"  record:   dropped_by={record.get('dropped_by')} reason={record.get('reason')}")
        print("  effective now, with no loop running; inspect with: python3 fleet/control.py dead-letter")
        return 0

    # A brain-minted authorisation: not this process's record to move (#821).
    reason = args.reason or "operator dead-letter (control:drop)"
    task: dict = {"directive": directive, "reason": reason}
    if args.issue is not None:
        task["issue"] = args.issue
    _send_control("drop", task=task, body=f"control:drop — dead-letter {directive}: {reason}")
    print(
        f"drop RELAYED: {directive} is a brain-minted authorisation in {fleet / SENT_DIR}, which no "
        "verb but the lifecycle may move (#821) — the control went to the loop over the channel"
    )
    print("inspect the mailbox with: python3 fleet/control.py dead-letter")
    return 0


def _directive_id_ok(directive_id: str) -> bool:
    """A directive id becomes a filename, so it must be a safe mailbox name.

    The rule is the channel's own (`channel.DIRECTIVE_ID_RE`), imported lazily:
    control.py otherwise talks to the channel by subprocess, and the validator
    must be the SAME one the transport applies rather than a second copy that can
    drift.
    """
    import channel

    return bool(channel.DIRECTIVE_ID_RE.fullmatch(directive_id))


def _directive_location(fleet: Path, directive_id: str) -> tuple[str, Path | None]:
    """Where the named order sits: ``(inbox|sent|dead-letter|nowhere, path)``.

    `dead-letter` is reported before `sent` so a retired order can never be read
    as a live authorisation, and the inbox is checked first because that is the
    mailbox the drain path reads — the one a refusal can leave re-drainable.
    """
    for label, directory in (
        ("inbox", fleet / INBOX_DIR),
        ("dead-letter", fleet / DEAD_LETTER_DIR),
        ("sent", fleet / SENT_DIR),
    ):
        candidate = directory / f"{directive_id}.json"
        if candidate.exists():
            return label, candidate
    return "nowhere", None


def _dead_letter_artifact(fleet: Path, directive_id: str) -> Path:
    return fleet / DEAD_LETTER_DIR / f"{directive_id}.json"


def _read_json(path: Path | None) -> dict | None:
    if path is None:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _directive_issue(path: Path | None) -> int | None:
    """The issue an order names, or None when it names none we can act on."""
    payload = _read_json(path) or {}
    try:
        number = int((payload.get("task") or {}).get("issue") or 0)
    except (TypeError, ValueError):
        return None
    return number or None


def _order_landed(
    root: Path, issue: int | None, fleet: Path, directive_id: str
) -> bool | None:
    """Whether the order's change has LANDED — the #821 fact a drop must respect.

    Two hermetic sources, no network:

    * an identical record already in ``<fleet>/done/`` — the authorisation path
      consumed it, and `governance/lifecycle/directive.py` may only do that for a
      landed change, so the order is finished;
    * the issue is CLOSED in the committed board snapshot.

    ``None`` means neither source can answer (no issue named, no readable
    snapshot, an issue the snapshot does not carry), and the caller refuses: an
    undecidable subject is never defaulted to allowed. NAMED LIMIT: the snapshot
    carries issue state only, so a merged pull request on a still-open issue —
    which `governance/lifecycle/model.py::owes_closure` also calls landed — is not
    visible here; the check can therefore miss landed work that the lifecycle
    record would catch, which is why the refusal names the lifecycle route.
    """
    if (fleet / DONE_DIR / f"{directive_id}.json").exists():
        return True
    if issue is None:
        return None
    snapshot = _read_json(root / SNAPSHOT)
    if snapshot is None:
        return None
    for item in snapshot.get("issues") or []:
        if not isinstance(item, dict) or item.get("number") != issue:
            continue
        state = str(item.get("state") or "").strip().upper()
        if state == "CLOSED":
            return True
        return False if state == "OPEN" else None
    return None


def cmd_dead_letter(args: argparse.Namespace) -> int:
    """Read the dead-letter mailbox by verb, never by walking the runtime dir."""
    result = _run(
        ["python3", "fleet/runaway.py", "dead-letter"]
        + (["--directive", args.directive] if args.directive else []),
        check=False,
    )
    print((result.stdout + result.stderr).rstrip())
    return result.returncode


def cmd_debug(args: argparse.Namespace) -> int:
    print("== channel ==")
    _run(["python3", CHANNEL, "status"], check=False)
    print("== board ==")
    _run(["python3", "governance/dispatch/cli.py", "status"], check=False)
    print("== slog tail ==")
    slog = ROOT / FLEET_SUBDIR / "slog.jsonl"
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


def rung_log(name: str) -> Path:
    """`.fleet/<rung>.log` — the capture log `fleet/watchdog.py` writes when it spawns a rung.

    Declared here as well as in the watchdog on purpose: the path IS the contract
    between the spawner, the starter (`cmd_start` below), the reader
    (`fleet/console.py`) and the tail windows, and `fleet/monitor.py` sets the
    precedent of each module naming the runtime paths it touches.
    """
    return ROOT / FLEET_SUBDIR / f"{name}.log"


def ensure_logs() -> list[Path]:
    """Create each rung's capture log so `tail -f` has a file to follow immediately."""
    paths = []
    for name in LIVE_RUNGS:
        path = rung_log(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)
        paths.append(path)
    return paths


def live_layout() -> list[list[str]]:
    """The tmux argv list that builds the principal's live session.

    Pure construction, so `--dry-run` can print exactly what would run and the
    tests can assert the layout without a tmux server or an attached terminal.

    The layout is one pane per surface: the summary dashboard (the console), the
    three rung capture logs, and the audit stream (the slog) — everything the
    single-pane-of-glass summarizes, with a status bar naming the session.
    """
    return [
        ["tmux", "new-session", "-d", "-s", SESSION, "-n", "dashboard", "python3", "fleet/console.py"],
        ["tmux", "new-window", "-t", SESSION, "-n", "brain", "tail", "-f", f"{FLEET_SUBDIR}/brain.log"],
        ["tmux", "new-window", "-t", SESSION, "-n", "sister", "tail", "-f", f"{FLEET_SUBDIR}/sister.log"],
        ["tmux", "new-window", "-t", SESSION, "-n", "monitor", "tail", "-f", f"{FLEET_SUBDIR}/monitor.log"],
        ["tmux", "new-window", "-t", SESSION, "-n", "events", "tail", "-f", f"{FLEET_SUBDIR}/slog.jsonl"],
        ["tmux", "set-option", "-t", SESSION, "status-right", f"{SESSION} · 3 rungs · dashboard"],
        ["tmux", "select-window", "-t", f"{SESSION}:dashboard"],
    ]


def tmux_session_exists() -> bool:
    probe = subprocess.run(["tmux", "has-session", "-t", SESSION], capture_output=True, text=True)
    return probe.returncode == 0


def _tmux(argv: list[str]) -> int:
    return subprocess.call(argv, cwd=ROOT)


def _attach() -> int:
    """Attach the principal's terminal to the session. Kept separate so tests can
    prove the layout without ever running `tmux attach`."""
    return subprocess.call(["tmux", "attach", "-t", SESSION], cwd=ROOT)


def cmd_live(args: argparse.Namespace) -> int:
    """Ensure the rungs are up, then put the principal in front of the live fleet."""
    logs = ensure_logs()
    # The events window tails the audit stream; create it so `tail -f` has a
    # file to follow even before the first directive is written.
    slog = ROOT / FLEET_SUBDIR / "slog.jsonl"
    slog.parent.mkdir(parents=True, exist_ok=True)
    slog.touch(exist_ok=True)
    layout = live_layout()
    if args.dry_run:
        print(f"fleet live --dry-run — the tmux session this would build (rung logs: {logs[0].parent}):")
        for argv in layout:
            print("  " + " ".join(argv))
        print(f"  tmux attach -t {SESSION}")
        return 0

    started = cmd_start(args)
    if started != 0:
        return started
    if shutil.which("tmux") is None:
        print("tmux is not installed — the fleet is running and every rung's stream is captured:")
        for path in logs:
            print(f"  {path}")
        print("Watch it live with: python3 fleet/console.py")
        return 1
    if not tmux_session_exists():
        for argv in layout:
            _tmux(argv)
    print("attaching to the live fleet session — detach with Ctrl-b d")
    return _attach()


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
        ("drop", cmd_drop),
        ("dead-letter", cmd_dead_letter),
        ("debug", cmd_debug),
        ("watch", cmd_watch),
        ("health", cmd_health),
        ("cron", cmd_cron),
        ("live", cmd_live),
        ("attach", cmd_live),
    ):
        sub.add_parser(name, help=f"control.{name}").set_defaults(func=func)
    debug = sub.choices["debug"]
    debug.add_argument("--tail", type=int, default=20)
    override = sub.choices["override"]
    override.add_argument("--issue", type=int, required=True)
    override.add_argument("--lane", default=None)
    override.add_argument("--body", default=None)
    drop = sub.choices["drop"]
    drop.add_argument("--directive", required=True, help="the id of the wedged directive to dead-letter")
    drop.add_argument("--reason", default=None, help="why it is dead — recorded durably")
    drop.add_argument("--issue", type=int, default=None, help="the issue the directive carried")
    mailbox = sub.choices["dead-letter"]
    mailbox.add_argument("--directive", default=None, help="print one record in full")
    health = sub.choices["health"]
    health.add_argument("--stale-minutes", type=float, default=30.0)
    cron = sub.choices["cron"]
    cron.add_argument("cron_args", nargs=argparse.REMAINDER, help="passed through to fleet/cron.py")
    for name in ("live", "attach"):
        sub.choices[name].add_argument(
            "--dry-run", action="store_true", help="print the tmux commands instead of building the session"
        )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
