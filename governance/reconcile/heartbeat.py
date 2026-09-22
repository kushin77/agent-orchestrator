"""Per-session heartbeats — how a dead lane becomes visible (issue #304).

---knowledge---
module_id: governance.reconcile.heartbeat
system: governance
app: reconcile
solution_class: enterprise
patterns: [lane-isolation]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [now_epoch, iso, Session, Verdict, sessions_dir, path_for, stamp, read, list_sessions, clear, (+5 more)]
invariants: ""
gotchas: ""
related: ["#304", "#917"]
do_not_duplicate: null
---knowledge---

The fleet already beats per *rung*: `sister.heartbeat.json` and
`brain.heartbeat.json` prove the loops are up. What did not exist is a beat per
*dispatched lane*, so an orphaned worktree, a dead agent's claim and a stale
branch were indistinguishable from work in progress — the operator found them by
hand (the orphaned `ao-263-*` lane, the manually reaped claim).

A session heartbeat is deliberately small and dumb: one JSON file per session
under `.fleet/sessions/`, refreshed on an interval while the session runs, and
removed when it exits cleanly. Writes are atomic (temp file + rename) so a reader
never observes a half-written record, and every timestamp is written as both an
ISO string (for a human) and epoch seconds (for the arithmetic).

Two signals, deliberately not one:

* **staleness** — the heartbeat has not advanced past the time-to-live; this
  catches a session that is *alive but wedged*, which a pid check alone cannot;
* **absence** — the recorded pid is gone.

Absence is decisive only once the beat has also gone stale. A fresh heartbeat
behind a missing pid is reported as ``suspect`` and left alone, because the most
likely explanations are a handover or a re-exec, and reclaiming a live lane on
that evidence would destroy the work the rule exists to protect.
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from governance.policy import lease  # noqa: E402

#: Where session heartbeats live, relative to the repository root.
SESSIONS_DIR = ".fleet/sessions"

#: A session that has not beaten for this long is orphaned. Declared once in
#: governance/policy/lease.py, where it is constrained to exceed the rung
#: heartbeat (or a live lane would be judged dead between beats).
DEFAULT_TTL_MINUTES = lease.SESSION_TTL_MINUTES

#: How often a running session refreshes its beat.
DEFAULT_BEAT_SECONDS = lease.SESSION_HEARTBEAT_SECONDS

LIVE = "live"
SUSPECT = "suspect"
ORPHAN = "orphan"

SHELVED = "shelved"
RUNNING = "running"

#: The name of the disposition this worker takes when it cannot read state that a
#: destructive decision rests on -- "no gate is held" and "I could not look" must
#: not share a verdict (issue #1897). Deliberately a token in the *reason*, not a
#: fourth ``status``: ``live``/``suspect``/``orphan`` is a closed three that
#: ``fleet/health_signals.py`` mirrors and ``fleet/schema/health-signal.schema.json``
#: enumerates, so a fourth status minted in this package alone would be refused on
#: the health plane. The refusal is carried instead by the existing,
#: non-destructive verdicts plus this word, which every consumer already renders.
CANNOT_ASSESS = "cannot-assess"


def now_epoch() -> float:
    return time.time()


def iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class Session:
    """One agent session's beat: who, what lane, which process, and when."""

    session_id: str
    issue: int
    agent: str
    lane: str
    worktree: str
    branch: str
    pid: int | None
    at: float
    state: str = RUNNING
    note: str = ""

    def to_json(self) -> dict:
        return {
            "session_id": self.session_id,
            "issue": self.issue,
            "agent": self.agent,
            "lane": self.lane,
            "worktree": self.worktree,
            "branch": self.branch,
            "pid": self.pid,
            "at": self.at,
            "at_iso": iso(self.at),
            "state": self.state,
            "note": self.note,
        }

    @classmethod
    def from_json(cls, payload: dict) -> "Session":
        # An undated beat must not read as "live": epoch 0 makes it maximally
        # stale, so a malformed record fails towards the audit rather than away.
        raw_at = payload.get("at") or 0.0
        return cls(
            session_id=str(payload["session_id"]),
            issue=int(payload.get("issue") or 0),
            agent=str(payload.get("agent") or ""),
            lane=str(payload.get("lane") or ""),
            worktree=str(payload.get("worktree") or ""),
            branch=str(payload.get("branch") or ""),
            pid=int(payload["pid"]) if payload.get("pid") else None,
            at=float(raw_at),
            state=str(payload.get("state") or RUNNING),
            note=str(payload.get("note") or ""),
        )


@dataclass(frozen=True)
class Verdict:
    """What the sweep decided about one session, and why."""

    session: Session
    status: str
    reason: str

    @property
    def reclaimable(self) -> bool:
        """Only a session whose beat has gone stale may be torn down."""
        return self.status == ORPHAN

    def __str__(self) -> str:
        return f"{self.session_id} {self.status}: {self.reason}"


def sessions_dir(root: Path | str) -> Path:
    return Path(root) / SESSIONS_DIR


def path_for(session_id: str, root: Path | str) -> Path:
    # The session id is minted by governance/isolation (hex), but a caller can
    # pass anything: refuse a path separator rather than write outside the dir.
    if not session_id or "/" in session_id or "\\" in session_id or session_id.startswith("."):
        raise ValueError(f"unsafe session id {session_id!r}")
    return sessions_dir(root) / f"{session_id}.json"


def stamp(
    session_id: str,
    *,
    issue: int,
    agent: str,
    root: Path | str,
    lane: str = "",
    worktree: str = "",
    branch: str = "",
    pid: int | None = None,
    state: str = RUNNING,
    note: str = "",
    at: float | None = None,
) -> Session:
    """Write (or refresh) a session's heartbeat, atomically.

    ``pid`` is the durable identity of the process being watched and is recorded
    exactly as given — ``stamp`` never substitutes ``os.getpid()``. The old
    default silently recorded the *ticking* process, so a per-beat subprocess (a
    ``setsid`` loop that re-stamps every 45 s) left a pid that was dead by the
    next sweep and manufactured a ``suspect`` finding on every pass (#1966). A
    caller that wants a live verdict passes the long-lived loop's pid; one that
    omits it records no pid at all, and the sweep reads that as it would any
    unverifiable process.
    """
    moment = now_epoch() if at is None else at
    session = Session(
        session_id=session_id,
        issue=int(issue),
        agent=agent,
        lane=lane,
        worktree=worktree,
        branch=branch,
        pid=int(pid) if pid else None,
        at=moment,
        state=state,
        note=note,
    )
    path = path_for(session_id, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Temp file + rename: a concurrent sweep either sees the previous complete
    # beat or the new one, never a truncated record it would have to guess about.
    temp = path.with_suffix(f".{os.getpid()}.tmp")
    temp.write_text(json.dumps(session.to_json(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, path)
    return session


def read(session_id: str, root: Path | str) -> Session | None:
    """The session's last beat, or None when it never beat (or was cleared)."""
    try:
        path = path_for(session_id, root)
    except ValueError:
        return None
    if not path.exists():
        return None
    try:
        return Session.from_json(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError):
        return None


def list_sessions(root: Path | str) -> list[Session]:
    """Every session with a heartbeat on disk, newest beat first."""
    directory = sessions_dir(root)
    if not directory.exists():
        return []
    sessions = []
    for path in directory.glob("*.json"):
        try:
            sessions.append(Session.from_json(json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, ValueError, KeyError):
            continue
    sessions.sort(key=lambda session: session.at, reverse=True)
    return sessions


def clear(session_id: str, root: Path | str) -> bool:
    """Remove a session's heartbeat. Returns whether one was there."""
    try:
        path = path_for(session_id, root)
    except ValueError:
        return False
    if not path.exists():
        return False
    path.unlink(missing_ok=True)
    return True


#: Where ``governance/isolation`` writes a lane record — read here by NAME only,
#: never through that package, so the sweeper's view of the lane plane cannot
#: depend on importing the mint.
LANES_DIR = ".fleet/lanes"


def lane_records_without_beat(root: Path | str, beating: set[str] | None = None) -> tuple[int, list[str]]:
    """``(lane record count, session ids of the records with no beat)`` (#917).

    The sweeper's input is the session plane; the lane plane is what it was
    blind to. A record it cannot parse still counts as a record (the file is
    there) and is listed by file name, so an unreadable lane never reads as
    absent. ``beating`` defaults to every session id with a beat on disk.
    """
    directory = Path(root) / LANES_DIR
    if not directory.exists():
        return 0, []
    if beating is None:
        beating = {session.session_id for session in list_sessions(root)}
    total = 0
    unbeaten: list[str] = []
    for path in sorted(directory.glob("*.json")):
        total += 1
        try:
            session_id = str(json.loads(path.read_text(encoding="utf-8")).get("session_id") or path.stem)
        except (OSError, ValueError, AttributeError):
            session_id = path.stem
        if session_id not in beating:
            unbeaten.append(session_id)
    return total, unbeaten


def age_seconds(session: Session, at: float | None = None) -> float:
    return (now_epoch() if at is None else at) - session.at


def pid_alive(pid: int | None) -> bool:
    """Whether a pid is alive, without killing it or needing permission.

    ``os.kill(pid, 0)`` raises ``ProcessLookupError`` for a dead pid and
    ``PermissionError`` for one owned by another user — a live pid we cannot
    signal. Anything else is treated as alive, so the check fails *safe*.
    """
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def judge(
    session: Session,
    ttl_minutes: float = DEFAULT_TTL_MINUTES,
    *,
    at: float | None = None,
    alive: bool | None = None,
    gate_in_flight: bool | None = False,
) -> Verdict:
    """Decide a session's status: ``live``, ``suspect`` or ``orphan``.

    * a beat older than the TTL is **orphan** — the session is not running, or is
      running and wedged, and either way nobody is coming back for the lane;
    * a fresh beat whose process is gone is **suspect** — reported, never torn
      down, because absence alone is weak evidence;
    * otherwise the session is **live**.

    ``gate_in_flight`` is the seam that lets a **held gate permit outrank a stale
    beat** (issue #1897). ``scripts/verify.sh`` runs for ~13 minutes against a
    15-minute TTL, so a lane inside its own gate looks orphaned while it is in
    fact being worked in — and the gate lock already records exactly that state.
    Like a refusal and unlike a boolean it is tri-state:

    * ``True`` — a permit is HELD for this session's worktree, so the lane is
      **live**: reported, never reclaimed (``sweep.py``'s ``gate_in_flight()``
      reads it through ``fleet/gatelock.probe``);
    * ``False`` — no permit holds it, and the TTL arm applies unchanged. This is
      the default, so every caller predating the seam keeps the verdict it had;
    * ``None`` — the permit store could not be read, which is **CANNOT-ASSESS**,
      never a reclaim: "no permit is held" and "I could not look" must not share
      a verdict. Nothing is asserted about the lane — only that this worker
      refuses to tear it down on state it could not read.
    """
    moment = now_epoch() if at is None else at
    age = moment - session.at
    ttl_seconds = ttl_minutes * 60.0
    process_alive = pid_alive(session.pid) if alive is None else alive

    if age > ttl_seconds:
        if gate_in_flight:
            return Verdict(
                session,
                LIVE,
                f"heartbeat is {int(age)}s old, past the {ttl_minutes:g}m TTL, but a composite gate "
                f"holds worktree {session.worktree or '(none)'} right now, so the lane is live; "
                "reported, not reclaimed",
            )
        if gate_in_flight is None:
            return Verdict(
                session,
                LIVE,
                f"{CANNOT_ASSESS}: the gate permit store could not be read, so a live gate in "
                f"worktree {session.worktree or '(none)'} cannot be ruled out; refusing to "
                "reclaim a stale-looking lane (reported, not reclaimed)",
            )
        return Verdict(session, ORPHAN, f"heartbeat is {int(age)}s old, past the {ttl_minutes:g}m TTL")
    if not process_alive:
        return Verdict(
            session,
            SUSPECT,
            f"process {session.pid} is gone but the heartbeat is fresh ({int(age)}s); "
            "reported, not reclaimed — it is orphaned once the beat passes the TTL",
        )
    return Verdict(session, LIVE, f"process {session.pid} is alive, last beat {int(age)}s ago")


@dataclass
class Beater:
    """Refreshes a session's heartbeat on an interval while the session runs.

    The pid recorded is the process being watched — for a dispatched lane that is
    the subagent, not the loop that spawned it. Recording the loop's pid would
    make every lane look alive for as long as the loop runs, which is precisely
    the signal the sweep needs to be able to lose.
    """

    session_id: str
    issue: int
    agent: str
    root: Path | str
    lane: str = ""
    worktree: str = ""
    branch: str = ""
    pid: int | None = None
    interval: float = DEFAULT_BEAT_SECONDS
    _stop: object | None = None
    _thread: object | None = None

    def start(self) -> "Beater":
        import threading

        # Beat once *before* the thread starts: a session must be visible the
        # instant it begins, not after the scheduler first runs the refresher.
        # (Observed: a caller that stamped then immediately listed sessions saw
        # none, because the thread had not been scheduled yet.)
        self.beat()
        self._stop = threading.Event()

        def run() -> None:
            while not (self._stop and self._stop.is_set()):  # type: ignore[union-attr]
                try:
                    self.beat()
                except (OSError, ValueError):
                    pass  # a failed beat is the sweep's business, not a crash here
                if self._stop and self._stop.wait(self.interval):  # type: ignore[union-attr]
                    return

        thread = threading.Thread(target=run, name=f"beat-{self.session_id[:8]}", daemon=True)
        thread.start()
        self._thread = thread
        return self

    def beat(self) -> Session:
        return stamp(
            self.session_id,
            issue=self.issue,
            agent=self.agent,
            root=self.root,
            lane=self.lane,
            worktree=self.worktree,
            branch=self.branch,
            pid=self.pid,
        )

    def stop(self, *, clear_session: bool = True) -> None:
        if self._stop is not None:
            self._stop.set()  # type: ignore[union-attr]
        if self._thread is not None:
            self._thread.join(timeout=2.0)  # type: ignore[union-attr]
        if clear_session:
            clear(self.session_id, self.root)
