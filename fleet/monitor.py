#!/usr/bin/env python3
"""Fleet monitor — the cron-owned, change-only progress watcher (the third rung).

WHY it exists: the fleet's brain/sister loops are long-running, and their
observable progress (which rungs are up, which claims are held, what the wave
dispatch list is, what code is at HEAD) used to be watched only by an ad-hoc,
gitignored shell script (``.fleet/open-eye.sh``) that no gate or watchdog knew
about. This module makes that monitor a first-class, TRACKED rung: it runs the
same change-only polling, and ``fleet/watchdog.py`` now respawns it when it is
missing — so the monitor survives a crash or a reboot without a human.

It polls every ``POLL_SECONDS`` and appends ONE timestamped line to
``.fleet/open-eye.log`` only when the observable state changed since the previous
tick (sister/brain state, held claims, wave dispatch list, git HEAD, and — since
issue #695 — the queue's liveness). Every tick it rewrites
``.fleet/monitor.heartbeat.json`` with a JSON liveness beat in the
same shape the brain and sister publish (``pid``, ``state``, ``commit``, ``ts``),
so the dashboard's RUNGS row reads the monitor the same way it reads its sibling
rungs. It exits cleanly on SIGTERM/SIGINT. Runtime output lives entirely under
the gitignored ``.fleet/`` directory; this module itself is tracked.

THE QUEUE-LIVENESS SIGNAL (issue #695)
--------------------------------------
WHY it exists: on 2026-09-14 the terminal loop sat in an infinite retry on a
closed-issue directive for many turns while **every** health surface read idle.
The wedge was invisible because nothing measured the *queue*: `fleet/health.py`
probes the RUNG (is the loop alive? is its beat fresh?), and an idle loop whose
mailbox is wedged has a live pid, a fresh beat and no claim — so it reads
healthy. This module is that missing measurement.

It is derived from the fleet's own stores, and it only reads them:

* ``.fleet/inbox/`` — the mailbox the sister drains, one JSON per pending
  directive. ``inbox_depth`` and ``oldest_directive_age`` come from here.
* ``.fleet/attempts/`` — the runaway guard's persisted retry counter, one small
  JSON per directive that has failed at least once (``fleet/runaway.py``,
  AO-GR-21), whose ``reasons`` record the STAGE each attempt failed at.
  ``wedge_count`` comes from here: this is the only durable per-attempt history
  the fleet keeps.
* ``.fleet/runs/`` — one marker per run IN FLIGHT (``terminal.mark_run``). It
  cannot evidence a *retry* — ``terminal.clear_run`` runs in ``_run_child``'s
  ``finally`` on every attempt, success or failure, so a finished run leaves no
  marker — so it is reported as ``run_markers`` and nothing more is inferred from
  it. ``.fleet/runs.jsonl`` (the per-run telemetry log) is deliberately NOT read:
  it is 33 MB / 177 222 records on this box and this rung ticks every 20s.

Three states, the `fleet/health.py` vocabulary: ``degraded`` when the oldest
pending directive is past the declared directive lifetime **or** a directive has
been retried with no stage change; ``failing`` when BOTH hold. A signal that
never leaves ``healthy`` is the exact defect this exists to fix, so the arm is
provoked in the tests rather than asserted.

**ADR-0022 refusal 4 / ADR-0025 D3 — the signal drives a TICKET, never a silent
action.** A monitoring verb may read a signal, evaluate a threshold and drive a
ticket; it may never write fleet state. So this signal opens those three stores
and writes none of them, takes no action on the queue (no drop, no re-arm, no
pause, no dead-letter), and names the ticket it should drive. Its one write is
its own published artifact, ``.fleet/queue-liveness.json``, beside the beat this
rung already publishes — the file-on-disk shape the hub's observability model
uses for a signal a joiner reads and never mutates.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import runtime

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import runaway  # noqa: E402
from governance.policy import lease  # noqa: E402

FLEET_DIR = runtime.FLEET_DIR
LOG = FLEET_DIR / "open-eye.log"
# The monitor's liveness, in the same JSON shape the brain/sister rungs publish:
# `fleet/console.py` reads exactly this path as the monitor row's beat.
HEARTBEAT = FLEET_DIR / "monitor.heartbeat.json"
WAVES_DIR = FLEET_DIR / "waves"
SISTER_HEARTBEAT = FLEET_DIR / "sister.heartbeat.json"
BRAIN_HEARTBEAT = FLEET_DIR / "brain.heartbeat.json"
POLL_SECONDS = 20

_stop = False

# ── the queue-liveness signal (issue #695) ──────────────────────────────────
#
# The health states, at the values `fleet/health.py` uses for the same words.
HEALTHY, DEGRADED, FAILING = 0, 1, 2

#: The honest-emptiness token: a state that could not be established. Declared in
#: `fleet/health_signals.py` for the fleet family (`NO_DATA`) and re-used here by
#: name. It is deliberately NOT an import: `health_signals` pulls in
#: `watchdog` + `governance.reconcile`, and the monitor is the rung the watchdog
#: respawns — inheriting someone else's import error here would kill the rung
#: that exists to notice things. This module adds only `runaway` and the lease
#: constants, which are leaf modules.
NO_DATA = "no-data"

#: CANNOT-ASSESS, kept OUTSIDE the 0/1/2 health tri-state above so it can be read
#: as neither a pass nor a verdict. The precedent is the gate's own refusal codes
#: (`scripts/gate-lock.sh`: 10 refused / 11 parked / 12 cannot-assess, stated to
#: stay outside the gate's 0/1/2). A code that collided with `failing` would be a
#: silent failure by construction.
NO_DATA_LEVEL = 3

LABELS = {HEALTHY: "healthy", DEGRADED: "degraded", FAILING: "failing", NO_DATA_LEVEL: NO_DATA}

#: Consecutive attempts at the SAME stage that make a directive wedged. Three is
#: chosen against the guard's budget: `runaway.DEFAULT_ATTEMPT_CAP` is 5, so three
#: identical outcomes is past the midpoint of a directive's budget and still two
#: attempts short of retirement — the signal fires while an operator can still act
#: on the directive rather than after the guard has already dead-lettered it.
WEDGE_RETRY_THRESHOLD = 3

#: A directive the sister never consumed is abandoned after this long. Imported
#: from its declaration site (`governance/policy/lease.py`, whose ordering
#: invariants constrain it) rather than re-declared, so `channel status`,
#: `channel.expired_directives` and this signal can never disagree about when a
#: directive is stale mail.
DIRECTIVE_TTL_SECONDS = lease.DIRECTIVE_LIFETIME_SECONDS

#: The published artifact: what a projection joins. The file is this rung's own
#: output, in the shape the hub's observability model names for a producer that
#: nobody scrapes (a signal is a file on disk; the joiner never writes).
QUEUE_LIVENESS_NAME = "queue-liveness.json"

#: Volatile numbers inside a recorded attempt reason (a duration, an exit code, a
#: pid, a holder's ordinal). Masked before two reasons are compared as stages.
_VOLATILE_NUMBER = re.compile(r"\d+")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_heartbeat(*, started_at: str, commit: str) -> None:
    """Publish the monitor's liveness as a JSON beat (brain/sister shape).

    Written atomically (tmp + rename) so a reader never sees a half-written
    beat, and in JSON so `fleet/console.py` reads it with the same code path as
    the other rungs — the plain-text `open-eye.heartbeat` this replaced was
    unparseable as JSON, which is why the dashboard printed `monitor
    no-heartbeat` while the monitor was alive.
    """
    entry = {
        "pid": os.getpid(),
        "state": "healthy",
        "started_at": started_at,
        "commit": commit,
        "ts": now_iso(),
    }
    HEARTBEAT.parent.mkdir(parents=True, exist_ok=True)
    tmp = HEARTBEAT.with_suffix(".tmp")
    tmp.write_text(json.dumps(entry) + "\n", encoding="utf-8")
    tmp.replace(HEARTBEAT)


def _heartbeat_state(path: Path) -> str:
    """The ``state`` field of a rung heartbeat, or ``?`` when unreadable/absent."""
    try:
        beat = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "?"
    state = beat.get("state") if isinstance(beat, dict) else None
    return state if isinstance(state, str) and state else "?"


def sister_state() -> str:
    return _heartbeat_state(SISTER_HEARTBEAT)


def brain_state() -> str:
    return _heartbeat_state(BRAIN_HEARTBEAT)


def held_claims() -> str:
    """Live-claim lines from the dispatch ledger, joined like the old watcher."""
    try:
        result = subprocess.run(
            ["python3", "governance/dispatch/cli.py", "status"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    lines = [ln.strip() for ln in result.stdout.splitlines() if "held by" in ln]
    return "|".join(lines)


def wave_dispatch() -> str:
    """The union of ``dispatched`` issue numbers across every wave file."""
    dispatched: list[str] = []
    if WAVES_DIR.exists():
        for path in sorted(WAVES_DIR.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            issues = data.get("dispatched") if isinstance(data, dict) else None
            if isinstance(issues, list):
                dispatched.extend(str(item) for item in issues)
    return ",".join(dispatched)


def git_head() -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return "?"
    return result.stdout.strip() or "?"


def snapshot(liveness: QueueLiveness | None = None) -> str:
    """One line capturing the fleet's observable state (comparable across ticks)."""
    measured = queue_liveness() if liveness is None else liveness
    return (
        f"sister={sister_state()} brain={brain_state()} "
        f"held=[{held_claims()}] dispatched=[{wave_dispatch()}] head={git_head()} "
        f"{queue_line(measured)}"
    )


# ── the queue-liveness measurement (issue #695) ─────────────────────────────


def fleet_dir(root: Path | str | None = None) -> Path:
    """The fleet runtime directory: ``<root>/.fleet`` for a fixture, else the live one.

    ``root`` is the same seam ``fleet/state.py --root`` and
    ``fleet/health_publish.py --root`` expose, so a proof can drive this signal
    over a constructed queue without ever writing into the live ``.fleet``.
    """
    if root is None:
        return runtime.FLEET_DIR
    return Path(root) / ".fleet"


def parse_stamp(stamp: object) -> datetime | None:
    """An ISO-8601 UTC stamp, or ``None`` when it is absent or not in that form.

    The form ``channel.now_iso`` writes and ``channel.expired_directives`` parses,
    so an envelope this signal cannot date is one ``channel status`` cannot date
    either.
    """
    if not isinstance(stamp, str):
        return None
    try:
        return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def pending_directives(
    inbox: Path, moment: float | None = None
) -> list[tuple[str, float | None]]:
    """``(directive id, age in seconds)`` per pending directive, in id order.

    Age is ``None`` when the envelope carries no stamp this module can parse. The
    directive is still **counted** — it is pending, and that is the fact — but its
    age stays *unknown* and is never reported as 0, which would read as "just
    arrived" and hide the very staleness the signal exists to expose. An envelope
    that cannot be parsed at all is counted too: it still occupies the mailbox, and
    a reader that cannot parse it can never consume it.
    """
    reference = time.time() if moment is None else moment
    now = datetime.fromtimestamp(reference, tz=timezone.utc)
    found: list[tuple[str, float | None]] = []
    for path in sorted(inbox.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            found.append((path.stem, None))
            continue
        seen = parse_stamp(payload.get("ts")) if isinstance(payload, dict) else None
        found.append((path.stem, None if seen is None else (now - seen).total_seconds()))
    return found


def run_markers(runs: Path) -> int:
    """How many run markers ``.fleet/runs/`` holds right now — context, not a verdict.

    A marker is written before a dispatch and removed in the run's ``finally``, so
    this is roughly "dispatches that have not finished", and that is all it claims:
    it is NOT a liveness finding (a loop killed outright leaves a marker behind,
    which ``terminal.run_state`` calls ``orphaned``), and a marker's absence says
    nothing about whether a directive was retried. It is reported and never
    inferred from.
    """
    if not runs.is_dir():
        return 0
    return len(list(runs.glob("*.json")))


def stage_of(reason: str) -> str:
    """The STAGE a recorded attempt reason names, with volatile numbers masked.

    ``attempts/<id>.json``'s reasons are the guard's own free text ("run did not
    land: failed (rc=1)", "claim refused: stale — snapshot is 29.1m old …").
    Compared as raw text a changed duration, exit code, pid or holder ordinal reads
    as **progress** — and that is exactly how a wedge hides: measured on this box
    (2026-09-15), one directive retried five times recorded five
    ``snapshot is Nm old`` strings and an exact comparison excluded it. Masking the
    numbers compares what the attempt was stuck *at* rather than the numbers in its
    sentence.
    """
    return _VOLATILE_NUMBER.sub("#", " ".join(str(reason).split()))


def consecutive_same_stage(reasons: Sequence[str]) -> int:
    """How many of the trailing attempts recorded the SAME stage — 0 for none.

    This is the "no stage change" half of the wedge definition. A directive that
    failed three *different* ways scores 1 and is not wedged: it is being
    diagnosed, not stuck.
    """
    stages = [stage for stage in (stage_of(reason) for reason in reasons) if stage]
    if not stages:
        return 0
    run = 1
    for earlier in reversed(stages[:-1]):
        if earlier != stages[-1]:
            break
        run += 1
    return run


@dataclass(frozen=True)
class Wedge:
    """One pending directive that has been retried with no stage change."""

    directive: str
    attempts: int
    cap: int
    state: str
    retries: int
    stage: str


def wedged(
    pending: Sequence[str],
    fleet: Path | str,
    *,
    wedge_retries: int = WEDGE_RETRY_THRESHOLD,
) -> list[Wedge]:
    """The pending directives whose persisted attempts show no stage change.

    Only a directive that is STILL IN THE MAILBOX counts. A directive the queue has
    already shed (consumed, or dead-lettered and removed) is not the queue's
    problem, and reporting it would red the signal for work that is already
    terminal. A directive that *is* still pending after the guard dead-lettered it
    is the sharpest case of all: ``runaway.dispatchable`` excludes it from every
    future dispatch, so it will never be retried **and** never be consumed — it
    sits in the mailbox for good, and no other surface in the fleet reports it.
    """
    base = Path(fleet)
    out: list[Wedge] = []
    for directive_id in pending:
        record = runaway.load(directive_id, base)
        if record is None:
            # It has never failed once: it is waiting to be dispatched, which is
            # not a wedge.
            continue
        retries = consecutive_same_stage(record.reasons)
        if retries < wedge_retries:
            continue
        out.append(
            Wedge(
                directive=directive_id,
                attempts=record.attempts,
                cap=record.cap,
                state=record.state,
                retries=retries,
                stage=stage_of(record.reasons[-1]) if record.reasons else "",
            )
        )
    return out


@dataclass(frozen=True)
class QueueLiveness:
    """The queue's liveness: the measured fields, and the level they reduce to."""

    store_present: bool
    inbox_depth: int
    oldest_directive_age: float | None
    wedge_count: int
    run_markers: int
    wedges: tuple[Wedge, ...]
    level: int
    wedge_retries: int
    ttl_seconds: float
    reasons: tuple[str, ...]

    @property
    def status(self) -> str:
        """The level as its label — the word the signal reads as."""
        return LABELS[self.level]

    def as_dict(self) -> dict:
        """The published shape: the field names the CLI prints and a ticket quotes."""
        return {
            "signal": self.level,
            "status": self.status,
            "store_present": self.store_present,
            "inbox_depth": self.inbox_depth,
            "oldest_directive_age": self.oldest_directive_age,
            "wedge_count": self.wedge_count,
            "run_markers": self.run_markers,
            "wedges": [
                {
                    "directive": wedge.directive,
                    "attempts": wedge.attempts,
                    "cap": wedge.cap,
                    "state": wedge.state,
                    "retries": wedge.retries,
                    "stage": wedge.stage,
                }
                for wedge in self.wedges
            ],
            "wedge_retries": self.wedge_retries,
            "ttl_seconds": self.ttl_seconds,
            "reasons": list(self.reasons),
        }


def queue_liveness(
    root: Path | str | None = None,
    *,
    wedge_retries: int = WEDGE_RETRY_THRESHOLD,
    ttl_seconds: float = DIRECTIVE_TTL_SECONDS,
    moment: float | None = None,
) -> QueueLiveness:
    """Measure the queue's liveness. It reads only, and acts on nothing.

    An absent mailbox is CANNOT-ASSESS (``no-data``) and never a healthy empty
    queue: the mailbox is created by ``channel send`` on the first order, so "there
    is no directory to read" and "I read it and it was empty" are two different
    facts, and collapsing them is how a wedged queue reads idle.

    ``degraded`` when the oldest pending directive is past the declared directive
    lifetime **or** a directive has been retried with no stage change; ``failing``
    when *both* hold.
    """
    fleet = fleet_dir(root)
    inbox = fleet / "inbox"
    if not inbox.is_dir():
        return QueueLiveness(
            store_present=False,
            inbox_depth=0,
            oldest_directive_age=None,
            wedge_count=0,
            run_markers=run_markers(fleet / "runs"),
            wedges=(),
            level=NO_DATA_LEVEL,
            wedge_retries=wedge_retries,
            ttl_seconds=float(ttl_seconds),
            reasons=(
                f"no mailbox at {inbox}: the queue cannot be read, so its depth is unknown "
                "— CANNOT-ASSESS, never a healthy empty queue (the mailbox is created by "
                "`channel send` on the first order)",
                "ticket: create the mailbox, or confirm this fleet has never been ordered "
                "here; reproduce with `python3 fleet/monitor.py queue"
                f"{'' if root is None else f' --root {root}'}` "
                "(ADR-0022 refusal 4 — the signal drives a ticket and takes no action)",
            ),
        )

    pending = pending_directives(inbox, moment)
    ages = [age for _directive, age in pending if age is not None]
    oldest = max(ages) if ages else None
    wedges = wedged(
        [directive for directive, _age in pending], fleet, wedge_retries=wedge_retries
    )
    markers = run_markers(fleet / "runs")

    aged = oldest is not None and oldest > ttl_seconds
    wedge_arm = bool(wedges)
    reproduce = f"python3 fleet/monitor.py queue{'' if root is None else f' --root {root}'}"

    reasons: list[str] = []
    if aged:
        reasons.append(
            f"the oldest pending directive is {oldest:.0f}s old (> the {ttl_seconds:.0f}s "
            "declared directive lifetime) — the mailbox is not being drained"
        )
    if wedge_arm:
        # The directive's own `state` rides in the reason, not only in the JSON: a
        # `dead-letter` wedge needs an operator to decide whether to re-arm or drop
        # it, while a re-armable one needs whatever its stage names fixed. A reason
        # that cannot tell the two apart sends the operator to the wrong command.
        named = ", ".join(
            f"{wedge.directive} [{wedge.state}, {wedge.attempts}/{wedge.cap} attempts, "
            f"{wedge.retries} at one stage: {wedge.stage}]"
            for wedge in wedges
        )
        reasons.append(
            f"{len(wedges)} pending directive(s) retried with no stage change "
            f"(>= {wedge_retries} consecutive attempts at one stage): {named}"
        )
    if aged or wedge_arm:
        reasons.append(
            "ticket: a human decides — the monitor takes no action on the queue "
            "(ADR-0022 refusal 4 / ADR-0025 D3: a signal drives a ticket, never a silent "
            f"action). Reproduce with `{reproduce}`"
        )
    else:
        reasons.append(
            f"{len(pending)} pending directive(s), oldest "
            f"{'none' if oldest is None else f'{oldest:.0f}s'} (within the {ttl_seconds:.0f}s "
            "lifetime); no directive retried with no stage change"
        )

    level = HEALTHY
    if aged and wedge_arm:
        level = FAILING
    elif aged or wedge_arm:
        level = DEGRADED

    return QueueLiveness(
        store_present=True,
        inbox_depth=len(pending),
        oldest_directive_age=oldest,
        wedge_count=len(wedges),
        run_markers=markers,
        wedges=tuple(wedges),
        level=level,
        wedge_retries=wedge_retries,
        ttl_seconds=float(ttl_seconds),
        reasons=tuple(reasons),
    )


def publish_queue_liveness(liveness: QueueLiveness, root: Path | str | None = None) -> Path:
    """Write the signal where a projection can join it (atomic tmp + rename).

    This is the signal's only write, and it is the rung's OWN artifact — the queue
    stores are never touched, because ADR-0022 refusal 4 forbids a monitoring path
    from writing fleet state. A projection, a `cmr health`-style joiner or the SPoG
    reads this file and writes nothing itself.
    """
    path = fleet_dir(root) / QUEUE_LIVENESS_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = liveness.as_dict()
    entry["ts"] = now_iso()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(entry, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def queue_line(liveness: QueueLiveness) -> str:
    """The queue's contribution to the change-only snapshot line."""
    oldest = (
        "?"
        if liveness.oldest_directive_age is None
        else f"{int(liveness.oldest_directive_age)}s"
    )
    return (
        f"queue={liveness.status} depth={liveness.inbox_depth} oldest={oldest} "
        f"wedge={liveness.wedge_count} markers={liveness.run_markers}"
    )


def _request_stop(signum: int, frame: object) -> None:
    global _stop
    _stop = True


def run() -> int:
    FLEET_DIR.mkdir(parents=True, exist_ok=True)
    started_at = now_iso()
    # The monitor's stdout is captured to `.fleet/monitor.log` by whoever spawns
    # it (the watchdog, or `control.py`), and that is what the `monitor` window in
    # the `fleet` tmux session tails. Without these two prints the capture would be
    # an empty file that looks like a dead rung.
    print(f"[monitor] up {now_iso()} pid={os.getpid()} poll={POLL_SECONDS}s log={LOG}", flush=True)
    write_heartbeat(started_at=started_at, commit=git_head())
    last: str | None = None
    while not _stop:
        # Measured once per tick and used for both the log line and the published
        # artifact, so a reader never sees a line and a file that disagree.
        liveness = queue_liveness()
        publish_queue_liveness(liveness)
        current = snapshot(liveness)
        stamp = now_iso()
        if current != last:
            with LOG.open("a", encoding="utf-8") as fh:
                fh.write(f"{stamp} {current}\n")
            print(f"[monitor] {stamp} {current}", flush=True)
            if liveness.level != HEALTHY:
                # Say WHY, on the same stdout the watchdog captures to
                # `.fleet/monitor.log` and the dashboard's monitor window tails: a
                # state whose reason the operator cannot read is a light with no
                # label, which is how the wedge stayed invisible.
                for reason in liveness.reasons:
                    print(f"[monitor] {reason}", flush=True)
            last = current
        write_heartbeat(started_at=started_at, commit=git_head())
        # Sleep in small slices so a SIGTERM is honoured within a second.
        for _ in range(POLL_SECONDS):
            if _stop:
                break
            time.sleep(1)
    return 0


def cmd_queue(args: argparse.Namespace) -> int:
    """Print the queue-liveness signal and exit with it.

    The exit code is the signal: 0 healthy / 1 degraded / 2 failing, and 3 for
    CANNOT-ASSESS, which is deliberately outside that tri-state so a caller can
    never read "no measurement" as a verdict. This is the verb a ticket path
    calls, and the only thing it does is read.
    """
    liveness = queue_liveness(
        args.root, wedge_retries=args.wedge_retries, ttl_seconds=args.ttl_seconds
    )
    if args.json:
        print(json.dumps(liveness.as_dict(), indent=2, sort_keys=True))
    else:
        print(
            f"queue: {liveness.status} (signal {liveness.level}) — "
            f"inbox_depth={liveness.inbox_depth} "
            f"oldest_directive_age={liveness.oldest_directive_age} "
            f"wedge_count={liveness.wedge_count} "
            f"run_markers={liveness.run_markers}"
        )
        for reason in liveness.reasons:
            print(f"  {reason}")
    return liveness.level


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fleet-monitor", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("run", help="poll the fleet and log the changes (the rung; also the default)")
    queue = sub.add_parser("queue", help="print the queue-liveness signal and exit with it")
    queue.add_argument(
        "--root", default=None, help="project a fixture tree instead of this checkout"
    )
    queue.add_argument(
        "--wedge-retries",
        type=int,
        default=WEDGE_RETRY_THRESHOLD,
        help="consecutive same-stage attempts that make a directive wedged",
    )
    queue.add_argument(
        "--ttl-seconds",
        type=float,
        default=DIRECTIVE_TTL_SECONDS,
        help="the directive lifetime a pending directive must not exceed",
    )
    queue.add_argument("--json", action="store_true", help="print the published JSON shape")
    queue.set_defaults(func=cmd_queue)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "queue":
        return cmd_queue(args)
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
