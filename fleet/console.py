#!/usr/bin/env python3
"""Fleet console — the live dashboard for the `fleet` tmux session.

WHY it exists: the brain, sister and monitor rungs run DETACHED (the watchdog and
cron own their lifecycle) and each writes its stream to `.fleet/<rung>.log`. The
operator's view of "is the fleet doing what I asked?" was therefore spread across
a heartbeat JSON, the dispatch ledger, the wave plan files, the event log and the
watchdog log — five commands, none of them live, none of them showing the brain.
This renders all of it in one frame, redrawn every ``REFRESH_SECONDS``.

Design rule: every section is built by a PURE function over a plain dict, and
``snapshot()`` is the only part that reads the repo. That is what lets the tests
assert exactly what the operator sees, without a TTY and without a running fleet.

Usage:
    python3 fleet/console.py            # self-refreshing dashboard (Ctrl-C exits)
    python3 fleet/console.py --once     # one frame, then exit
"""

from __future__ import annotations

import argparse
import json
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "fleet"))

import channel  # noqa: E402
import runtime  # noqa: E402

FLEET_DIR = runtime.FLEET_DIR
SESSION = runtime.SESSION
REFRESH_SECONDS = 4.0
REPO = "kushin77/agent-orchestrator"
# `gh` is asked for closed-issue state — the only fact on this dashboard that
# lives on the network — and only this often: the frame is redrawn every few
# seconds, and the network must never be part of the refresh loop.
CLOSED_TTL_SECONDS = 30.0
# Wave glyphs: a child that is closed, one that has been dispatched, one waiting.
GLYPH_CLOSED = "\u2713"
GLYPH_DISPATCHED = "\u25b6"
GLYPH_PENDING = "\u00b7"
WIDTH = 96

# --- colour (interactive only) ----------------------------------------------
# The frame is built by PURE functions the tests assert, so colour is opt-in:
# `render` stays colourless until `enable_color(True)`, which only the
# interactive loop calls when stdout is a TTY. Tests therefore never see ANSI.
ANSI = {
    "green": "\033[32m",
    "yellow": "\033[33m",
    "red": "\033[31m",
    "dim": "\033[2m",
    "bold": "\033[1m",
    "reset": "\033[0m",
}
_COLOR = False

# Health -> colour, per rung state. Unknown is dim, never red: a state the frame
# has not met before must not read as a failure.
HEALTHY_STATES = frozenset({"healthy", "idle", "working", "running", "alive", "ok"})
DEGRADED_STATES = frozenset({"degraded", "stale", "drifted", "suspect", "paused", "stopping"})
FAILING_STATES = frozenset({"failing", "down", "no-heartbeat", "missing", "dead", "error", "critical"})


def enable_color(on: bool) -> None:
    global _COLOR
    _COLOR = bool(on)


def paint(text: str, code: str) -> str:
    if not _COLOR:
        return text
    return f"{ANSI[code]}{text}{ANSI['reset']}"


def state_color(state: str) -> str:
    if state in FAILING_STATES:
        return "red"
    if state in DEGRADED_STATES:
        return "yellow"
    if state in HEALTHY_STATES:
        return "green"
    return "dim"


# --- paths (resolved per call, so a test can redirect the whole tree) --------


def waves_dir() -> Path:
    return FLEET_DIR / "waves"


def watchdog_log() -> Path:
    return FLEET_DIR / "watchdog.log"


def monitor_heartbeat() -> Path:
    return FLEET_DIR / "monitor.heartbeat.json"


def rung_log(name: str) -> Path:
    """`.fleet/<rung>.log` — the capture log the watchdog writes (deliverable A)."""
    return FLEET_DIR / f"{name}.log"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fit_width() -> int:
    """Narrow the frame to the terminal it is drawn in, and return the width.

    The dashboard's home is a tmux pane, which is narrow: a fixed 96-column frame
    wraps mid-section and the operator reads a scrambled screen. Only a real
    terminal moves the width — piped output (a log, a script, a test) keeps the
    deterministic default.
    """
    global WIDTH
    columns = shutil.get_terminal_size(fallback=(WIDTH, 24)).columns
    WIDTH = max(60, min(columns, 120))
    return WIDTH


# --- readers ----------------------------------------------------------------


def read_json(path: Path) -> dict | None:
    """A JSON object from `path`, or None — a missing/corrupt file is not a crash."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def count_json(directory: Path) -> int:
    return len(list(directory.glob("*.json"))) if directory.exists() else 0


def loop_pid(pattern: str) -> int | None:
    """The first pid matching `pattern`, or None."""
    try:
        result = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    for line in result.stdout.splitlines():
        if line.strip().isdigit():
            return int(line.strip())
    return None


def tail_lines(path: Path, limit: int, *, max_bytes: int = 65536) -> list[str]:
    """The last `limit` lines of a file, reading at most `max_bytes` from the end.

    The event log is append-only and multi-megabyte; a full read on every refresh
    would make the dashboard the load it exists to explain. The first line of a
    mid-file block is dropped because it is usually a partial record.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return []
    try:
        with path.open("rb") as handle:
            handle.seek(max(0, size - max_bytes))
            chunk = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    lines = chunk.splitlines()
    if size > max_bytes and lines:
        lines = lines[1:]
    return lines[-limit:]


def newest_json(directory: Path, limit: int) -> list[dict]:
    """The `limit` newest messages in a mailbox directory, oldest first.

    Ordered by the message timestamp, not by filename: message ids are uuid4, so a
    filename sort returns them in arbitrary order and the operator reads a stale
    line as if it were the current one.
    """
    messages: list[dict] = []
    if not directory.exists():
        return messages
    for path in directory.glob("*.json"):
        payload = read_json(path)
        if payload is not None:
            messages.append(payload)
    messages.sort(key=lambda message: str(message.get("ts") or ""))
    return messages[-limit:]


def rung_specs() -> tuple[tuple[str, str, Path], ...]:
    """(name, process pattern, heartbeat path) per rung, resolved on every call.

    Resolved here rather than at import so the heartbeat paths follow `channel`'s
    own constants: the tests redirect those, and the dashboard must read whatever
    the rung actually wrote.
    """
    return (
        ("brain", "fleet/brain.py", channel.BRAIN_HEARTBEAT),
        ("sister", "fleet/terminal.py", channel.HEARTBEAT),
        ("monitor", "fleet/monitor.py", monitor_heartbeat()),
    )


def rungs_snapshot() -> dict:
    snapshot: dict[str, dict] = {}
    for name, pattern, beat_path in rung_specs():
        pid = loop_pid(pattern)
        beat = read_json(beat_path)
        age = channel.heartbeat_age_seconds(beat) if beat else None
        if beat is None:
            # Two very different failures: nothing is running, or something is but
            # it never wrote a heartbeat (a process older than the heartbeat check).
            state = "down" if pid is None else "no-heartbeat"
        else:
            state = str(beat.get("state") or "unknown")
        snapshot[name] = {
            "pid": pid,
            "state": state,
            "commit": str((beat or {}).get("commit") or "-"),
            "beat_age": None if age is None else int(age),
            "started_at": (beat or {}).get("started_at") or None,
        }
    return snapshot


def orders_snapshot() -> dict:
    latest = newest_json(channel.BRAIN_SENT, 1)
    return {
        "pending": count_json(channel.BRAIN_INBOX),
        "latest": latest[0] if latest else None,
    }


def dispatches_snapshot(limit: int = 6) -> list[dict]:
    return newest_json(channel.BRAIN_OUTBOX, limit)


def claims_snapshot() -> list[str]:
    """Live claims, read from the dispatch ledger's own status output."""
    try:
        result = subprocess.run(
            ["python3", str(ROOT / "governance" / "dispatch" / "cli.py"), "status"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.strip() for line in result.stdout.splitlines() if "held by" in line]


def _as_int(value: object) -> int | None:
    """A value coerced to int, or None — a malformed wave file must not crash the frame."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def wave_plans() -> list[dict]:
    """Every wave plan, lowest parent first."""
    plans: list[dict] = []
    directory = waves_dir()
    if not directory.exists():
        return plans
    for path in sorted(directory.glob("*.json")):
        plan = read_json(path)
        if plan is not None and "parent" in plan:
            plans.append(plan)
    plans.sort(key=lambda plan: _as_int(plan.get("parent")) or 0)
    return plans


_CLOSED_CACHE: dict = {"at": 0.0, "issues": frozenset()}


def gh_closed_issues() -> set[int]:
    """Closed issue numbers, from one `gh` call. Raises on any failure."""
    result = subprocess.run(
        [
            "gh", "issue", "list",
            "--repo", REPO,
            "--state", "closed",
            "--limit", "500",
            "--json", "number",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip()[-200:] or "gh issue list failed")
    return {int(item["number"]) for item in json.loads(result.stdout or "[]")}


def closed_issues(*, force: bool = False, moment: float | None = None) -> set[int]:
    """Closed issues, cached for ``CLOSED_TTL_SECONDS``.

    A wave's closed glyph is the only fact here that needs the network, so it
    degrades to dispatched/pending rather than blanking the frame: every other
    section reads the fleet's own files.
    """
    now = time.monotonic() if moment is None else moment
    cached = set(_CLOSED_CACHE["issues"])
    if not force and cached and now - float(_CLOSED_CACHE["at"]) < CLOSED_TTL_SECONDS:
        return cached
    try:
        issues = gh_closed_issues()
    except (OSError, subprocess.SubprocessError, RuntimeError, ValueError, TypeError, KeyError):
        return cached
    _CLOSED_CACHE["at"] = now
    _CLOSED_CACHE["issues"] = frozenset(issues)
    return issues


def events_snapshot(limit: int = 8) -> list[dict]:
    events: list[dict] = []
    for line in tail_lines(channel.SLOG, limit):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events


def human_duration(seconds: int) -> str:
    """A compact age: `up 42s` / `up 3m` / `up 1h05m`."""
    if seconds < 60:
        return f"up {seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"up {minutes}m"
    return f"up {minutes // 60}h{minutes % 60:02d}m"


def fleet_uptime(rungs: dict, now: str) -> str:
    """The age of the oldest live rung — how long the fleet has been up."""
    started = [
        info.get("started_at")
        for info in rungs.values()
        if isinstance(info, dict) and info.get("started_at")
    ]
    if not started:
        return "up ?"
    try:
        born = datetime.strptime(min(started), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        current = datetime.strptime(now, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return "up ?"
    return human_duration(max(0, int((current - born).total_seconds())))


def snapshot() -> dict:
    """Everything the frame shows, gathered once. The only part that does I/O."""
    rungs = rungs_snapshot()
    now = now_iso()
    return {
        "repo": REPO,
        "head": channel.head_commit(),
        "now": now,
        "uptime": fleet_uptime(rungs, now),
        "rungs": rungs,
        "orders": orders_snapshot(),
        "dispatches": dispatches_snapshot(),
        "claims": claims_snapshot(),
        "waves": wave_plans(),
        "closed": sorted(closed_issues()),
        "events": events_snapshot(),
        "watchdog": tail_lines(watchdog_log(), 5),
    }


# --- rendering (pure: asserted by the tests, so it must not do I/O) ---------


def truncate(text: object, width: int = 72) -> str:
    """One line, whitespace collapsed, ellipsised at `width`."""
    collapsed = " ".join(str(text).split())
    if len(collapsed) <= width:
        return collapsed
    return collapsed[: width - 1].rstrip() + "\u2026"


def section(title: str, body: list[str]) -> str:
    return "\n".join([f"── {title} " + "─" * max(0, WIDTH - len(title) - 4), *body])


def header_section(snap: dict) -> str:
    return "\n".join(
        (
            "═" * WIDTH,
            f"  {snap.get('repo', REPO)} — {SESSION} session · HEAD {snap.get('head', 'unknown')} · {snap.get('now', '')} · {snap.get('uptime', 'up ?')}",
            f"  attach: tmux attach -t {SESSION}      detach: Ctrl-b d      logs: {FLEET_DIR.name}/<rung>.log",
            "═" * WIDTH,
        )
    )


def rungs_section(rungs: dict) -> str:
    body = []
    for name, info in rungs.items():
        pid = info.get("pid")
        age = info.get("beat_age")
        state = str(info.get("state", "?"))
        beat = "no beat" if age is None else f"beat {age}s ago"
        state_label = paint(f"{state:<10}", state_color(state))
        body.append(
            f"  {name:<8} pid {str(pid) if pid else '-':<8} {state_label} "
            f"commit {str(info.get('commit', '-')):<10} {beat}"
        )
    return section("RUNGS", body)


def orders_section(orders: dict) -> str:
    body = [f"  pending: {orders.get('pending', 0)}"]
    latest = orders.get("latest") or {}
    if latest:
        reference = latest.get("id") or latest.get("correlation_id") or "-"
        task = json.dumps(latest.get("task") or {}, sort_keys=True)
        body.append(f"  latest:  {truncate(reference, 12)} · {truncate(task, 40)} · {truncate(latest.get('body'), 60)}")
    else:
        body.append("  latest:  (no operator order yet)")
    return section("ORDERS (operator \u2192 brain)", body)


def dispatches_section(dispatches: list[dict]) -> str:
    body = [
        f"  {str(message.get('type', '-')):<7} order {truncate(message.get('correlation_id') or '-', 10):<11}"
        f" {truncate(message.get('body'), 58)}"
        for message in dispatches
    ]
    return section("DISPATCHES (brain \u2192 operator)", body or ["  (no brain acks yet)"])


MAX_CLAIMS = 8


def claims_section(claims: list[str]) -> str:
    """The live claims, bounded: an unbounded claim list is the one input that
    can push the frame past the screen, so the tail is summarised instead."""
    if not claims:
        return section("LIVE CLAIMS", ["  (none)"])
    body = [f"  {truncate(claim, WIDTH - 4)}" for claim in claims[:MAX_CLAIMS]]
    if len(claims) > MAX_CLAIMS:
        body.append(f"  \u2026 +{len(claims) - MAX_CLAIMS} more claim(s)")
    return section("LIVE CLAIMS", body)


def wave_line(plan: dict, closed: set[int]) -> str:
    """`#219  #232 ✓  #233 ▶  #234 ·` — closed / dispatched / still pending."""
    dispatched = {_as_int(issue) for issue in plan.get("dispatched") or []}
    dispatched.discard(None)
    parts = []
    for child in plan.get("children") or []:
        if not isinstance(child, dict):
            continue
        issue = _as_int(child.get("issue"))
        if issue is None:
            continue
        if issue in closed:
            glyph = GLYPH_CLOSED
        elif issue in dispatched:
            glyph = GLYPH_DISPATCHED
        else:
            glyph = GLYPH_PENDING
        parts.append(f"#{issue} {glyph}")
    return f"  #{plan.get('parent', '?')}  " + "  ".join(parts)


def waves_section(waves: list[dict], closed: set[int]) -> str:
    return section("WAVES", [wave_line(plan, closed) for plan in waves] or ["  (no wave plan)"])


def events_section(events: list[dict]) -> str:
    body = []
    for event in events:
        stamp = str(event.get("ts") or "")[11:19]
        body.append(
            f"  {stamp} {str(event.get('from', '?'))}\u2192{str(event.get('to', '?'))} "
            f"{str(event.get('type', '?')):<9} {truncate(event.get('body'), 52)}"
        )
    return section("RECENT EVENTS (slog.jsonl)", body or ["  (no events yet)"])


def watchdog_section(lines: list[str]) -> str:
    return section("WATCHDOG", [f"  {truncate(line, WIDTH - 4)}" for line in lines] or ["  (no pass yet)"])


def fleet_status(rungs: dict) -> str:
    """The fleet's single tri-state: any failing rung fails the fleet; else any
    rung that is not healthy degrades it; else healthy."""
    states = [str(info.get("state", "?")) for info in rungs.values() if isinstance(info, dict)]
    if any(state in FAILING_STATES for state in states):
        return "failing"
    if any(state not in HEALTHY_STATES for state in states):
        return "degraded"
    return "healthy"


def wave_progress(plan: dict, closed: set[int]) -> tuple[int, int]:
    """(done, total) children of one wave — closed or dispatched counts as done."""
    dispatched = {_as_int(issue) for issue in plan.get("dispatched") or []}
    dispatched.discard(None)
    done = 0
    total = 0
    for child in plan.get("children") or []:
        if not isinstance(child, dict):
            continue
        issue = _as_int(child.get("issue"))
        if issue is None:
            continue
        total += 1
        if issue in closed or issue in dispatched:
            done += 1
    return done, total


def status_line(snap: dict) -> str:
    """The one-line summary under the header, e.g.
    `fleet: healthy · 3 rungs · 0 claims · wave #219 2/3 done`."""
    rungs = snap.get("rungs") or {}
    status = fleet_status(rungs)
    n_claims = len(snap.get("claims") or [])
    waves = snap.get("waves") or []
    closed = {int(issue) for issue in snap.get("closed") or []}
    wave = ""
    if waves:
        done, total = wave_progress(waves[0], closed)
        wave = f" · wave #{waves[0].get('parent', '?')} {done}/{total} done"
    return f"{SESSION}: {status} · {len(rungs)} rungs · {n_claims} claims{wave}"


def render(snap: dict) -> str:
    """The whole frame. Pure: everything it needs is already in `snap`."""
    closed = {int(issue) for issue in snap.get("closed") or []}
    summary = status_line(snap)
    if _COLOR:
        status = fleet_status(snap.get("rungs") or {})
        summary = summary.replace(f"{SESSION}: {status}", f"{SESSION}: {paint(status, state_color(status))}", 1)
    return "\n".join(
        (
            header_section(snap),
            "  " + summary,
            rungs_section(snap.get("rungs") or {}),
            orders_section(snap.get("orders") or {}),
            dispatches_section(snap.get("dispatches") or []),
            claims_section(snap.get("claims") or []),
            waves_section(snap.get("waves") or [], closed),
            events_section(snap.get("events") or []),
            watchdog_section(snap.get("watchdog") or []),
        )
    )


# --- the loop ---------------------------------------------------------------

_stop = False


def _request_stop(signum: int, frame: object) -> None:
    global _stop
    _stop = True


def refresh_loop(interval: float = REFRESH_SECONDS) -> int:
    """Redraw until interrupted.

    Non-flickering: the alternate screen is entered once, and each frame homes
    the cursor and erases only what is left below it, so a shorter frame does
    not blank-and-redraw the whole terminal. The previous screen is restored on
    exit, and colour is on only when stdout is a real TTY.
    """
    enable_color(sys.stdout.isatty())
    out = sys.stdout
    out.write("\033[?1049h\033[?25l")  # alternate screen + hide cursor
    out.flush()
    try:
        while not _stop:
            out.write("\033[H" + render(snapshot()) + "\n\033[J")
            out.flush()
            # Sleep in one-second slices so SIGINT/SIGTERM is honoured
            # immediately instead of after a whole refresh interval.
            for _ in range(max(1, int(interval))):
                if _stop:
                    break
                time.sleep(1)
    finally:
        out.write("\033[?25h\033[?1049l")  # show cursor + leave alternate screen
        out.flush()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fleet-console", description=__doc__)
    parser.add_argument("--once", action="store_true", help="render one frame and exit (no TTY needed)")
    parser.add_argument("--refresh", type=float, default=REFRESH_SECONDS, help="seconds between frames")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    fit_width()
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    enable_color(sys.stdout.isatty())
    if args.once:
        print(render(snapshot()))
        return 0
    return refresh_loop(args.refresh)


if __name__ == "__main__":
    raise SystemExit(main())
