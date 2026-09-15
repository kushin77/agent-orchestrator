"""Is a run in flight? The marker's OWN evidence — never the loop's pid (#793).

`fleet/watchdog.py::run_in_flight()` used to ask whether a run marker's ``pid``
was alive. That ``pid`` is the **loop's**, not the run's, and a loop outlives
every run it dispatches — so a crashed run left a marker that read as "in
flight" for as long as the loop lived. Measured on this box: four markers ~5.6
hours old, every one with ``child_pid: null``, each naming the live sister loop's
pid. The sister's drift lock was held open on every tick, its own heartbeat
(``{"state": "idle", "runs": 0}``) was ignored for that decision, the rung stayed
on pre-#723 code **with no attempt budget**, and it re-dispatched without bound —
which is the causal chain that regenerates the gate storms #724 was filed for.

So flight is the marker's own evidence, and there are exactly two forms of it:

* a **live `child_pid`** — the subagent itself, running; or
* a **beat no older than `stale_seconds`** — the run's own beater advanced it,
  which a crashed run cannot do. This also protects a run that has only just
  started, before any child exists (`terminal.mark_run` writes ``child_pid:
  null`` first).

A marker with neither is a **crashed run**: it does not hold the lock, and the
remedy proceeds through the same bounded path as every other remedy.

A **missing or unreadable** beat is judged the same way, and deliberately: both
writers store an ISO stamp atomically (tmp + rename), so an unreadable ``ts``
means a corrupted or foreign marker that nothing in the fleet will ever advance,
and counting it as work would restore the very deadlock this removes. The
fail-safe direction is intact, because a child that is genuinely running is
caught by the live-child test, which needs no timestamp at all.

**The contradiction is reported, never silently resolved.** When the heartbeat
says ``idle`` while a marker says a run is in flight, the marker used to win in
silence. It still holds the lock — a live child is real work — but the
disagreement is named on the same line that reports the decision, so an operator
sees two artifacts of the fleet contradicting each other instead of one of them
quietly beating the other.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

#: How old a run marker's beat may be and still mean "the run is alive". The
#: run's own beater advances it on an interval well under this.
DEFAULT_STALE_SECONDS = 120

#: The environment override, so a test (or an operator) can shorten the window
#: without editing code.
ENV_STALE_SECONDS = "AO_RUN_STALE_SECONDS"

#: The heartbeat states that mean "the loop is not driving anything right now".
IDLE_STATES = frozenset({"idle", "paused", "stopped"})


def stale_seconds(env: Mapping[str, str] | None = None) -> int:
    """The beat window, ``AO_RUN_STALE_SECONDS``, default 120s."""
    source = os.environ if env is None else env
    raw = str((source or {}).get(ENV_STALE_SECONDS, "") or "").strip()
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_STALE_SECONDS
    return value if value > 0 else DEFAULT_STALE_SECONDS


def process_alive(pid: Any) -> bool:
    """True when `pid` names a live process. A non-pid is never alive."""
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def beat_age_seconds(stamp: Any, *, now: float | None = None) -> float | None:
    """The age of an ISO-8601 beat stamp in seconds, or None when unreadable."""
    if not isinstance(stamp, str) or not stamp.strip():
        return None
    text = stamp.strip()
    try:
        moment = datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    reference = datetime.now(timezone.utc) if now is None else datetime.fromtimestamp(now, timezone.utc)
    return (reference - moment).total_seconds()


@dataclass(frozen=True)
class Verdict:
    """One marker's flight verdict, with the evidence that produced it."""

    in_flight: bool
    marker: str
    evidence: str

    def line(self) -> str:
        return f"{self.marker} ({self.evidence})"


def marker_verdict(
    marker: str,
    record: Any,
    *,
    now: float | None = None,
    window: int | None = None,
) -> Verdict:
    """Decide ONE run marker on its own evidence.

    Order matters and is deliberate: the live child is checked first because it
    is the only evidence that needs no clock, so a clock that is wrong (or a beat
    that has not been written yet) cannot turn a running subagent into "crashed".
    """
    if not isinstance(record, Mapping):
        return Verdict(False, marker, "unreadable record")
    child = record.get("child_pid")
    if process_alive(child):
        return Verdict(True, marker, f"live child pid {child}")
    age = beat_age_seconds(record.get("ts"), now=now)
    limit = stale_seconds() if window is None else window
    if age is None:
        return Verdict(
            False,
            marker,
            f"child_pid {child!r} is not alive and the beat is unreadable "
            "(a marker nothing advances cannot be a run in flight)",
        )
    if age <= limit:
        return Verdict(True, marker, f"fresh beat {int(age)}s old (the run's own beater advanced it)")
    return Verdict(
        False,
        marker,
        f"crashed run — child_pid {child!r} is not alive and the beat is {int(age)}s old "
        f"(> {limit}s)",
    )


def read_marker(path: Path | str) -> Any:
    """Parse one run marker; None when it is missing, torn or not JSON."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def runs_in_flight(
    runs_dir: Path | str,
    *,
    now: float | None = None,
    window: int | None = None,
) -> tuple[bool, str]:
    """Would ANY run marker in `runs_dir` hold the lock open? And on what evidence.

    Returns ``(held, note)``. The note names the marker that decided it — so a
    leftover marker can never hold the lock invisibly — or names every crashed
    marker it refused, so a decision that *acts* says what it is acting on.
    """
    directory = Path(runs_dir)
    if not directory.exists():
        return False, "no run markers"
    verdicts: list[Verdict] = []
    for path in sorted(directory.glob("*.json")):
        verdicts.append(marker_verdict(path.stem, read_marker(path), now=now, window=window))
    for verdict in verdicts:
        if verdict.in_flight:
            return True, f"held by {verdict.line()}"
    if not verdicts:
        return False, "no run markers"
    crashed = [verdict for verdict in verdicts if not verdict.in_flight]
    return False, "no run in flight — refused " + "; ".join(verdict.line() for verdict in crashed)


def contradiction(heartbeat: Any, runs_dir: Path | str, *, window: int | None = None) -> str | None:
    """The heartbeat/marker disagreement, or None when the two artifacts agree.

    A heartbeat that says ``idle`` while a marker's own evidence says a run is in
    flight is TWO ARTIFACTS OF THE FLEET CONTRADICTING EACH OTHER. Before #793
    the marker won silently. It still wins — a live child is real work, and the
    lock exists to protect exactly that — but the disagreement is reported on the
    line that reports the decision, and can never be inferred only from a
    log that says "left alone".
    """
    if not isinstance(heartbeat, Mapping):
        held, note = runs_in_flight(runs_dir, window=window)
        if held:
            return f"the heartbeat is unreadable while {note} — the marker is kept, the disagreement is unproven"
        return None
    state = str(heartbeat.get("state") or "").strip().lower()
    if state not in IDLE_STATES:
        return None
    held, note = runs_in_flight(runs_dir, window=window)
    if not held:
        return None
    return (
        f"CONTRADICTION: the heartbeat says {state!r} while {note} — a run in flight is real "
        "work and keeps the lock, but the two artifacts disagree and that is reported"
    )
