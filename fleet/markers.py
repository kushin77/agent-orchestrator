#!/usr/bin/env python3
"""The dispatch-marker state machine, reconciled against reality (issue #796).

WHY THIS EXISTS (measured, 2026-09-15)
--------------------------------------
``fleet/brain.py::dispatch`` refused on marker *existence*::

    marker = order_marker(order)
    if marker is not None and marker.exists():
        return False, f"{DUPLICATE_SUPPRESSED} — ... was already sent"

and the marker it wrote (``write_marker(marker, order, "sent")``) was permanent.
At-most-once is CORRECT for delivery — the docstring's "restart-safe by
construction" property is the one #274 exists for and this module does not
weaken it. The defect was that **``sent`` is not ``done``**: a directive that
completed and one that DIED (quarantined, dead-lettered, a run whose
``child_pid`` was never set, a runner crash) were indistinguishable, because the
only surviving evidence was that the send happened.

Measured on this box: the director reported itself idle for 1535s while the board
carried 63 open issues, and its own idle path found three ready
(``#132``/``#133``/``#786``) — every one permanently suppressed by
``.fleet/brain/dispatched/advance-*.json``. For ``#786`` the terminal artifact
``.fleet/dead-letter/brain-directive-38136664….json`` (``attempts: 5``, the cap)
proved the directive had been retired by the attempt budget while its marker
still read ``{"state": "sent"}``. A pipeline welded shut by a marker that says
only *"we sent something once"*.

THE THREE STATES (acceptance criterion 2)
-----------------------------------------
``state`` is the marker's own word for where the ORDER is, and the marker set
alone distinguishes all three:

===================  ==================================================  ==========
state                written when                                        suppresses
===================  ==================================================  ==========
``sending``          the marker is persisted *before* the channel call   yes
``sent``             the channel accepted the directive                  yes
``in-flight``        live evidence exists: a live claim, a live run, or   yes
                     the directive is still queued in a live inbox
``completed``        the issue is CLOSED — the work landed (terminal)     yes
``dead``             the directive was retired (terminal) or its re-arm  yes
                     budget was exhausted; parked WITH THE ISSUE NAMED
===================  ==================================================  ==========

A ``re-armed`` marker additionally carries a one-shot ``rearm`` token, which is
what authorises exactly one more send (see below) — the state vocabulary alone
still separates "genuinely in flight" from "died", which is the property that
was missing.

HOW STALENESS IS DECIDED (acceptance criterion 1)
-------------------------------------------------
Not from the marker. From four questions about the world, asked by
:class:`FleetProbe`, each answered ``True``/``False``/**``None``** — and ``None``
(an unreadable store) is CANNOT-ASSESS, which NEVER re-arms: an unknown answer
must fail towards the delivery guarantee, not away from it:

* ``issue_closed``   — is the issue closed? Closed means the work landed: the
  marker becomes terminal ``completed``. Not on the board ⇒ ``None``.
* ``live_claim``     — does the claim ledger hold a live claim for the issue?
* ``live_run``       — is there a run marker for the issue that is ALIVE? Alive
  is a live ``child_pid`` or a fresh ``ts`` (the run's own beater refreshes it).
  The loop's own ``pid`` is deliberately NOT evidence: a leftover marker whose
  loop is alive would read as a live run, which is the #366 defect this probe
  must not repeat.
* ``in_flight``      — is the directive still queued for the dispatcher? The LIVE
  queue is the inbox; ``.fleet/sent/`` is an ARCHIVE (118 files here, most also
  in ``done/``) and reading it as "still in flight" would suppress everything for
  ever.
* ``dead_lettered``  — was THIS ORDER (matched on the envelope's
  ``correlation_id``, never by issue, so a fresh order for the same issue is not
  falsely parked) retired by the runaway guard? Then the marker is terminal
  ``dead``.

A marker with none of those, older than :data:`REARM_GRACE_SECONDS`, is **stale**.

HOW THE RE-ARM IS BOUNDED (acceptance criterion 1, harnessed from #723)
-----------------------------------------------------------------------
The re-arm count lives in the marker and the BOUNDS are harvested from
``fleet/runaway.py`` — :func:`runaway.cap_or_default` (K) and
:func:`runaway.backoff_delay` (``min(base * 2**(n-1), 300)``) — so there is one
budget vocabulary in the fleet, not two. The COUNTER is the marker's own because
it counts a different thing: the runaway store counts failures of a *directive*
(keyed by directive id, consumed by the dispatcher's watch), while this counts
re-arms of an ORDER — an order that never got a directive has no directive id to
count against.

So a stale marker is re-armed at most K times, spaced by the harvested backoff;
the K-th re-arm leaving the marker stale again is terminal ``dead`` — **parked
with the issue named**, printed, and never re-dispatched until a principal
re-arms it BY NAME (:func:`rearm`). A park is reversible; it is not a weld.

AT-MOST-ONCE IS NOT WEAKENED (acceptance criterion 4)
-----------------------------------------------------
``dispatch()`` proceeds past an existing marker only when the marker carries a
``rearm`` token — a one-shot authorisation the reconciliation grants, consumed by
the send itself. A restart that re-reads a plan, a principal re-ordering the same
order, or any caller that did not reconcile therefore still finds the marker and
is still suppressed. Two further layers back it up:

* the directive's identity is derived from the order reference (#274), so a
  second send of the same order is refused by the channel's own replay guard
  (``SENT``/``INBOX``/``DONE`` are all scanned) — a false "stale" therefore
  degrades to a printed refusal, never to a second execution;
* a marker younger than :data:`REARM_GRACE_SECONDS` is presumed in flight:
  delivery is not instantaneous, and the evidence of pickup (a claim, a run)
  appears a loop cycle later.

REPORTING (acceptance criterion 3)
----------------------------------
Never a silent ``continue``. :func:`reconcile` returns a :class:`Finding` for
every re-arm, every park, every held re-arm (once per backoff window, deduped on
``held_announced_at``), every cannot-assess and every state *transition*, and
``fleet/brain.py`` prints them; the advance path additionally prints a line for
each candidate it did NOT dispatch, naming the marker state.

CLI (principal)::

    python3 fleet/markers.py status [--limit N]        # what every marker says
    python3 fleet/markers.py show --reference <ref>    # one marker, verbatim
    python3 fleet/markers.py rearm --reference <ref>   # un-park, by name

Exit codes are the repo tri-state: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol

import runaway
import runtime

#: The fleet's runtime root, from the one module that declares it (#361).
FLEET_DIR = runtime.FLEET_DIR

#: Where a dispatched order's marker lives. ``fleet/brain.py`` derives the same
#: path (``DISPATCH_MARKERS``) and passes it here explicitly when it reconciles,
#: so the two cannot disagree about which directory is the marker set.
DISPATCHED = FLEET_DIR / "brain" / "dispatched"
#: The live run registry (``fleet/terminal.py`` owns the writers).
RUNS = FLEET_DIR / "runs"
#: The runaway guard's terminal store (``fleet/runaway.py``).
DEAD_LETTER = FLEET_DIR / "dead-letter"
#: The dispatcher's live queue — the only mailbox whose membership means "not yet
#: consumed". ``.fleet/sent``/``.fleet/outbox`` are archives and are NOT read.
INBOX = FLEET_DIR / "inbox"
#: The director's own queue, for principal orders (whose reference is the order id).
BRAIN_INBOX = FLEET_DIR / "brain" / "inbox"

# --- the state vocabulary ----------------------------------------------------

SENDING = "sending"
SENT = "sent"
IN_FLIGHT = "in-flight"
COMPLETED = "completed"
DEAD = "dead"

STATES = (SENDING, SENT, IN_FLIGHT, COMPLETED, DEAD)
#: A terminal marker is evidence the order is finished — completed or retired.
TERMINAL_STATES = (COMPLETED, DEAD)

# --- the bounds --------------------------------------------------------------

#: A re-arm is only granted to a marker older than this. Delivery is not
#: instantaneous: between the director's send and the dispatcher's pickup there is a
#: window (up to a watch cycle) where none of the four probes can yet see the
#: work, and re-arming inside it would race a healthy directive.
REARM_GRACE_SECONDS = 300.0

#: How fresh a run marker's ``ts`` must be to count as a live run. The run's own
#: beater refreshes it every ``terminal.HEARTBEAT_INTERVAL_SECONDS`` (15s), so
#: this is eight missed beats — generous, and still far below any human timescale.
LIVE_RUN_SECONDS = 120.0

#: Characters that may not appear in a marker filename: the reference becomes a
#: path component, so ``../`` must not walk out of the marker directory.
_MARKER_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")

# --- the verdict vocabulary --------------------------------------------------

FRESH = "fresh"
TERMINAL = "terminal"
LIVE = "in-flight"
RECENT = "recent"
RE_ARMED = "re-armed"
HELD = "held"
PARKED = "parked"
CANNOT_ASSESS = "cannot-assess"

#: Tri-state exit codes (repo convention, GR-12 / guardrails/honesty).
EXIT_OK = 0
EXIT_NOT_OK = 1
EXIT_CANNOT_ASSESS = 2


# --- time --------------------------------------------------------------------


def now_iso(moment: float | None = None) -> str:
    """An ISO-8601 UTC stamp, the shape every fleet record writes."""
    return datetime.fromtimestamp(_epoch_now(moment), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _epoch_now(moment: float | None = None) -> float:
    return time.time() if moment is None else float(moment)


def parse_epoch(stamp: object) -> float | None:
    """Parse an ISO-8601 UTC stamp; anything else reads as unknown (None)."""
    if not isinstance(stamp, str):
        return None
    try:
        return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def age_seconds(stamp: object, moment: float | None = None) -> float | None:
    """How long ago ``stamp`` was, or None when it cannot be read.

    ``None`` is not "old": an unreadable stamp makes the marker's recency
    unknown, and every caller treats that as "cannot assess" rather than as a
    licence to re-arm.
    """
    seen = parse_epoch(stamp)
    if seen is None:
        return None
    return _epoch_now(moment) - seen


# --- paths -------------------------------------------------------------------


def safe_reference(reference: str) -> str:
    """The reference as a path component: unsafe characters replaced, bounded."""
    return _MARKER_UNSAFE.sub("_", reference)[:120]


def path_for(reference: str, directory: Path | str | None = None) -> Path:
    """The marker path for an order reference."""
    root = DISPATCHED if directory is None else Path(directory)
    return root / f"{safe_reference(reference)}.json"


#: The keys every marker carries, in one place. The set is a contract: a reader
#: that needs a field this module does not write is a drift, and the gate names it.
MARKER_FIELDS = (
    "reference",
    "issue",
    "state",
    "ts",
    "sent_at",
    "attempts",
    "next_attempt_at",
    "rearm",
    "reason",
    "evidence",
    "announced",
)


@dataclass(frozen=True)
class Marker:
    """One dispatched order's marker, and what the director knows about it."""

    reference: str
    state: str
    issue: int | None = None
    ts: str = ""
    sent_at: str | None = None
    attempts: int = 0
    next_attempt_at: str | None = None
    rearm: dict | None = None
    reason: str | None = None
    evidence: dict | None = None
    #: The last *repeating* verdict announced for this marker revision, so a hold
    #: and a cannot-assess are reported ONCE per window instead of once per idle
    #: tick. `{"verdict": ..., "key": ..., "at": ...}`; the verdicts that happen
    #: once (re-arm, park, complete) are not deduped — they cannot repeat.
    announced: dict | None = None

    @property
    def terminal(self) -> bool:
        """True once the order is finished — completed, or retired/parked."""
        return self.state in TERMINAL_STATES

    @property
    def armed(self) -> bool:
        """True when a re-arm has been granted and not yet consumed."""
        return bool(self.rearm)

    def payload(self) -> dict:
        return {
            "reference": self.reference,
            "issue": self.issue,
            "state": self.state,
            "ts": self.ts,
            "sent_at": self.sent_at,
            "attempts": self.attempts,
            "next_attempt_at": self.next_attempt_at,
            "rearm": self.rearm,
            "reason": self.reason,
            "evidence": self.evidence,
            "announced": self.announced,
        }

    @classmethod
    def from_payload(cls, data: dict, *, reference: str = "") -> "Marker":
        """Rebuild from disk, tolerating a partial or older record.

        A torn marker must never crash the loop that reads it: an unreadable
        ``attempts`` counts as 0 and an unknown ``state`` reads as ``sent`` —
        i.e. "suppressed, and safe to assess again", never as terminal and never
        as an authorisation.
        """
        try:
            attempts = max(0, int(data.get("attempts", 0)))
        except (TypeError, ValueError):
            attempts = 0
        state = str(data.get("state") or SENT)
        if state not in STATES:
            state = SENT
        issue = data.get("issue")
        if not isinstance(issue, int):
            issue = None
        rearm = data.get("rearm") if isinstance(data.get("rearm"), dict) else None
        evidence = data.get("evidence") if isinstance(data.get("evidence"), dict) else None
        return cls(
            reference=str(data.get("reference") or reference or ""),
            state=state,
            issue=issue,
            ts=str(data.get("ts") or ""),
            sent_at=data.get("sent_at") if isinstance(data.get("sent_at"), str) else None,
            attempts=attempts,
            next_attempt_at=(
                data.get("next_attempt_at") if isinstance(data.get("next_attempt_at"), str) else None
            ),
            rearm=rearm,
            reason=data.get("reason") if isinstance(data.get("reason"), str) else None,
            evidence=evidence,
            announced=data.get("announced") if isinstance(data.get("announced"), dict) else None,
        )


def read(path: Path | str) -> Marker | None:
    """The marker at ``path``, or None when it is absent or unreadable."""
    target = Path(path)
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return Marker.from_payload(data, reference=target.stem)


def write(path: Path | str, marker: Marker) -> None:
    """Persist a marker atomically (tmp + rename), so no reader sees a torn file."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(marker.payload()) + "\n", encoding="utf-8")
    tmp.replace(target)


def unlink(path: Path | str) -> None:
    """Drop a marker; absent is success, an unreadable directory is not fatal."""
    try:
        Path(path).unlink()
    except OSError:
        pass


# --- the reality seam --------------------------------------------------------


class Probe(Protocol):
    """What the reconciler asks the world about one marker's order.

    Four questions, each answering ``True``/``False``/**``None``** — and ``None``
    means CANNOT-ASSESS, which never re-arms (see the module docstring). The
    protocol is structural on purpose: a gate or a test drives it with a stub
    that answers from literals, with no live fleet, board or network anywhere.
    """

    def issue_closed(self, issue: int) -> bool | None: ...

    def live_claim(self, issue: int) -> bool | None: ...

    def live_run(self, issue: int) -> bool | None: ...

    def in_flight(self, reference: str) -> bool | None: ...

    def dead_lettered(self, reference: str) -> str | None: ...


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _pid_alive(pid: object) -> bool:
    """True when ``pid`` names a process that exists right now."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # It exists and belongs to someone else — that is alive enough.
        return True
    return True


class FleetProbe:
    """The live fleet's answer to the four questions, read from its own stores.

    Every path is an override with a module-global default, so a gate can drive
    the real probe against a scratch runtime root with no live fleet, no board
    fetch and no network — the same reason ``fleet/runaway.py`` re-bases its
    locations onto a caller's ``base``. The defaults are read at CALL time, so a
    test that redirects :data:`RUNS` (as ``fleet/tests/conftest.py`` does for
    every fleet module) redirects this probe with it.
    """

    def __init__(
        self,
        *,
        board: object | None = None,
        closed_lookup: Callable[[int], bool | None] | None = None,
        runs_dir: Path | str | None = None,
        dead_letter_dir: Path | str | None = None,
        inbox_dirs: tuple[Path | str, ...] | None = None,
        ledger: Path | str | None = None,
    ) -> None:
        self.board = board
        self.closed_lookup = closed_lookup
        self._runs_dir = runs_dir
        self._dead_letter_dir = dead_letter_dir
        self._inbox_dirs = inbox_dirs
        self._ledger = ledger
        self._claims: frozenset[int] | None = None
        self._claims_read = False
        self._messages: dict[str, dict | None] | None = None

    # -- board ---------------------------------------------------------------

    def issue_closed(self, issue: int) -> bool | None:
        """Closed? Not on the board ⇒ None (unknown), never "open" by default."""
        if self.board is not None:
            record = self.board.get(issue)
            if record is None:
                return None
            return str(getattr(record, "state", "")).lower() == "closed"
        if self.closed_lookup is not None:
            try:
                answer = self.closed_lookup(issue)
            except (OSError, RuntimeError):
                return None
            return None if answer is None else bool(answer)
        return None

    # -- claims --------------------------------------------------------------

    def _claim_set(self) -> frozenset[int] | None:
        if self._claims_read:
            return self._claims
        self._claims_read = True
        try:
            dispatch_dir = Path(runtime.ROOT) / "governance" / "dispatch"
            if str(dispatch_dir) not in sys.path:
                sys.path.insert(0, str(dispatch_dir))
            import claims as claims_mod  # local import: the dispatch package is not a fleet sibling

            events = claims_mod.read_ledger() if self._ledger is None else claims_mod.read_ledger(self._ledger)
            self._claims = frozenset(int(issue) for issue in claims_mod.active_claims(events))
        except Exception:  # noqa: BLE001 — an unreadable ledger is CANNOT-ASSESS, not "no claim"
            self._claims = None
        return self._claims

    def live_claim(self, issue: int) -> bool | None:
        """A live claim for the issue? None when the ledger cannot be read."""
        ledger = self._claim_set()
        if ledger is None:
            return None
        return issue in ledger

    # -- runs ----------------------------------------------------------------

    def _runs_root(self) -> Path:
        return Path(RUNS if self._runs_dir is None else self._runs_dir)

    def live_run(self, issue: int) -> bool | None:
        """A run marker for the issue that is alive — live child, or fresh beat."""
        root = self._runs_root()
        if not root.is_dir():
            return False
        moment = _epoch_now()
        for path in sorted(root.glob("*.json")):
            record = _read_json(path)
            if record is None or record.get("issue") != issue:
                continue
            if _pid_alive(record.get("child_pid")):
                return True
            age = age_seconds(record.get("ts"), moment)
            if age is not None and age <= LIVE_RUN_SECONDS:
                return True
        return False

    # -- directives ----------------------------------------------------------

    def _mailboxes(self) -> tuple[Path, ...]:
        if self._inbox_dirs is not None:
            return tuple(Path(path) for path in self._inbox_dirs)
        return (Path(INBOX), Path(BRAIN_INBOX))

    def _live_messages(self) -> dict[str, dict | None]:
        """Every LIVE queued message, keyed by id AND by correlation id."""
        if self._messages is not None:
            return self._messages
        index: dict[str, dict | None] = {}
        for directory in self._mailboxes():
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.json")):
                message = _read_json(path)
                if message is None:
                    continue
                for key in ("id", "correlation_id"):
                    value = str(message.get(key) or "")
                    if value:
                        index[value] = message
        self._messages = index
        return index

    def in_flight(self, reference: str) -> bool | None:
        """Is the order's directive still queued, unconsumed, for the dispatcher?"""
        if not reference:
            return False
        return reference in self._live_messages()

    def dead_lettered(self, reference: str) -> str | None:
        """The retirement reason when THIS order was dead-lettered, else None.

        Matched on the envelope's ``correlation_id`` — the order reference — and
        never on the issue: a fresh order for an issue whose earlier directive was
        retired must not be parked by it. An artifact with no envelope (the order
        file was already gone when it was retired) cannot be correlated and is
        therefore not evidence.
        """
        if not reference:
            return None
        root = Path(DEAD_LETTER if self._dead_letter_dir is None else self._dead_letter_dir)
        if not root.is_dir():
            return None
        for path in sorted(root.glob("*.json")):
            record = _read_json(path)
            if record is None:
                continue
            envelope = record.get("envelope")
            if not isinstance(envelope, dict):
                continue
            if str(envelope.get("correlation_id") or "") != reference:
                continue
            reason = record.get("reason")
            return str(reason) if reason else "retired by the runaway guard"
        return None


# --- the judgement -----------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    """One reconciliation outcome, printable as-is (never a silent continue)."""

    reference: str
    issue: int | None
    verdict: str
    detail: str
    state: str

    def render(self) -> str:
        issue = f" (#{self.issue})" if self.issue else ""
        return f"[brain] marker reconcile: {self.verdict.upper()} {self.reference}{issue} — {self.detail}"


@dataclass(frozen=True)
class Verdict:
    """The judgement on one marker, plus the marker to persist (None = unchanged)."""

    verdict: str
    detail: str
    marker: Marker | None = None
    finding: bool = False


def _evidence(probe: Probe, marker: Marker, closed: bool | None) -> dict:
    """The four answers, recorded on the marker: what the re-arm rested on."""
    return {
        "issue_closed": closed,
        "live_claim": probe.live_claim(marker.issue) if marker.issue else False,
        "live_run": probe.live_run(marker.issue) if marker.issue else False,
        "in_flight": probe.in_flight(marker.reference),
        "checked_at": now_iso(),
    }


def _announced(marker: Marker, verdict: str, key: str, moment: float | None) -> tuple[bool, Marker]:
    """Should this *repeating* verdict be printed, and the marker that records it.

    A hold and a cannot-assess recur on every idle tick by construction; printing
    them every 30s is how a finding turns back into noise. They are announced once
    per *window* — the hold's ``next_attempt_at``, or the marker revision it was
    judged at — and the marker remembers which one it announced. The verdicts
    that happen once (re-arm, park, complete) go through :class:`Finding`
    directly and are never deduped: they cannot repeat.
    """
    previous = marker.announced or {}
    if previous.get("verdict") == verdict and previous.get("key") == key:
        return False, marker
    updated = Marker(**{**marker.payload(), "announced": {"verdict": verdict, "key": key, "at": now_iso(moment)}})
    return True, updated


def judge(
    marker: Marker | None,
    probe: Probe,
    *,
    moment: float | None = None,
    cap: int | None = None,
    base: int | None = None,
    grace: float = REARM_GRACE_SECONDS,
) -> Verdict:
    """Decide what is true about one marker, and what (if anything) to persist.

    The order of the questions IS the contract:

    1. terminal marker      → ``terminal``; nothing to do, and nothing to say.
    2. live evidence        → ``in-flight``. A claim, a run or a queued directive
                              all mean the work is out there: suppress, and (when
                              this *changes* the state) report the transition.
    3. any probe unreadable → ``cannot-assess``: NEVER re-arm on an unknown.
    4. retired directive    → ``dead``: parked at the issue, printed, by name.
    5. younger than grace   → ``recent``: presumed in flight, no write.
    6. budget exhausted     → ``dead``: parked at the issue, printed, by name.
    7. backoff not elapsed  → ``held``: printed once per window.
    8. otherwise            → ``re-armed``: counted, spaced, and printed.
    """
    budget = runaway.cap_or_default() if cap is None else int(cap)
    if marker is None:
        return Verdict(FRESH, "no marker: the order has never been sent")
    if marker.terminal:
        return Verdict(
            TERMINAL,
            f"state={marker.state}{' — ' + marker.reason if marker.reason else ''}",
        )
    if marker.issue is None:
        # A marker with no issue cannot be reconciled against the board at all:
        # unknown, never stale.
        return Verdict(
            CANNOT_ASSESS,
            "the marker names no issue — there is nothing to reconcile it against",
            finding=True,
        )

    closed = probe.issue_closed(marker.issue)
    if closed is True:
        # The work landed. This is the one transition that makes a marker
        # terminal for a good reason, and it is PRINTED: "we sent it once" is not
        # evidence that it landed, the closed issue is.
        updated = Marker(**{**marker.payload(), "state": COMPLETED, "ts": now_iso(moment), "reason": "the issue is closed — the work landed", "rearm": None})
        return Verdict(COMPLETED, f"#{marker.issue} is closed — the marker is terminal, not merely sent", updated, finding=True)

    live_claim = probe.live_claim(marker.issue)
    live_run = probe.live_run(marker.issue)
    in_flight = probe.in_flight(marker.reference)
    if live_claim or live_run or in_flight:
        where = ", ".join(
            name
            for name, value in (("live claim", live_claim), ("live run", live_run), ("queued directive", in_flight))
            if value
        )
        if marker.state == IN_FLIGHT:
            return Verdict(LIVE, f"{where} — suppressed, at-most-once holds")
        updated = Marker(
            **{**marker.payload(), "state": IN_FLIGHT, "ts": now_iso(moment), "evidence": _evidence(probe, marker, closed), "rearm": None}
        )
        return Verdict(LIVE, f"{where} — suppressed ({SENT} → {IN_FLIGHT}, at-most-once holds)", updated, finding=True)

    if closed is None or live_claim is None or live_run is None or in_flight is None:
        unknown = [
            name
            for name, value in (("the board", closed), ("the claim ledger", live_claim), ("the run registry", live_run), ("the live queue", in_flight))
            if value is None
        ]
        detail = f"{' and '.join(unknown)} could not be read — suppressed, never re-armed on an unknown"
        # Deduped on the marker revision: an unreadable store is reported once per
        # change, not once per idle tick.
        announce, updated = _announced(marker, CANNOT_ASSESS, marker.ts or "-", moment)
        return Verdict(CANNOT_ASSESS, detail, updated if announce else None, finding=announce)

    retired = probe.dead_lettered(marker.reference)
    if retired:
        updated = Marker(
            **{**marker.payload(), "state": DEAD, "ts": now_iso(moment), "reason": f"the directive was retired: {retired}", "rearm": None}
        )
        return Verdict(
            PARKED,
            f"the directive was retired — {retired}; the issue stays parked until an operator re-arms it "
            f"by name: python3 fleet/markers.py rearm --reference {marker.reference}",
            updated,
            finding=True,
        )

    recency = age_seconds(marker.ts, moment)
    if recency is None or recency < grace:
        return Verdict(
            RECENT,
            f"sent {int(recency) if recency is not None else '?'}s ago (< {int(grace)}s grace) — delivery is not instantaneous",
        )

    if marker.attempts >= budget:
        updated = Marker(
            **{**marker.payload(), "state": DEAD, "ts": now_iso(moment), "reason": f"the re-arm budget is exhausted ({marker.attempts}/{budget})", "rearm": None}
        )
        return Verdict(PARKED, f"the re-arm budget is exhausted ({marker.attempts}/{budget}) — parked; set it going again by name: python3 fleet/markers.py rearm --reference {marker.reference}", updated, finding=True)

    due_at = parse_epoch(marker.next_attempt_at)
    if due_at is not None and due_at > _epoch_now(moment):
        wait = int(due_at - _epoch_now(moment))
        # Deduped per backoff window: the hold is reported ONCE, not per idle tick.
        announce, updated = _announced(marker, HELD, marker.next_attempt_at or "-", moment)
        return Verdict(
            HELD,
            f"stale, held for another {wait}s by the harvested backoff (attempt {marker.attempts}/{budget})",
            updated if announce else None,
            finding=announce,
        )

    attempt = marker.attempts + 1
    next_at = now_iso(_epoch_now(moment) + runaway.backoff_delay(attempt, base=base))
    updated = Marker(
        **{
            **marker.payload(),
            "state": SENT,
            "ts": marker.ts or now_iso(moment),
            "attempts": attempt,
            "next_attempt_at": next_at,
            "rearm": {"at": now_iso(moment), "attempt": attempt, "by": "reconcile"},
            "reason": None,
            "evidence": _evidence(probe, marker, closed),
        }
    )
    return Verdict(
        RE_ARMED,
        f"STALE — the issue is open with no live claim, no live run and no queued directive; "
        f"re-armed (attempt {attempt}/{budget}, next backoff {runaway.backoff_delay(attempt, base=base)}s)",
        updated,
        finding=True,
    )


def reconcile(
    probe: Probe,
    *,
    directory: Path | str | None = None,
    references: frozenset[str] | set[str] | None = None,
    moment: float | None = None,
    cap: int | None = None,
    base: int | None = None,
    grace: float = REARM_GRACE_SECONDS,
) -> list[Finding]:
    """One reconciliation pass over the marker set; returns what must be printed.

    ``references`` restricts the pass to a set of order references (the wave path
    reconciles only the children it is about to dispatch, so an idle tick never
    pays for a `gh` call per historical marker). ``None`` means the whole marker
    set — which is what the board-backed advance path does, for free, since it has
    the board in memory already.
    """
    root = Path(DISPATCHED if directory is None else directory)
    findings: list[Finding] = []
    if not root.is_dir():
        return findings
    for path in sorted(root.glob("*.json")):
        marker = read(path)
        if marker is None:
            continue
        if references is not None and marker.reference not in references:
            continue
        verdict = judge(marker, probe, moment=moment, cap=cap, base=base, grace=grace)
        if verdict.marker is not None and verdict.marker != marker:
            try:
                write(path, verdict.marker)
            except OSError as exc:
                # A marker we cannot persist is a reconciliation we cannot claim:
                # report it rather than pretending the write happened.
                findings.append(Finding(marker.reference, marker.issue, CANNOT_ASSESS, f"the marker could not be written: {exc}", marker.state))
                continue
        if verdict.finding:
            state = verdict.marker.state if verdict.marker is not None else marker.state
            findings.append(Finding(marker.reference, marker.issue, verdict.verdict, verdict.detail, state))
    return findings


def reconcile_reference(
    reference: str,
    issue: int | None,
    probe: Probe,
    *,
    directory: Path | str | None = None,
    moment: float | None = None,
    cap: int | None = None,
    base: int | None = None,
    grace: float = REARM_GRACE_SECONDS,
) -> list[Finding]:
    """Reconcile ONE reference (the wave path's per-child form of :func:`reconcile`)."""
    return reconcile(
        probe,
        directory=directory,
        references={reference},
        moment=moment,
        cap=cap,
        base=base,
        grace=grace,
    )


def rearm(reference: str, *, directory: Path | str | None = None, reason: str = "", moment: float | None = None) -> Marker:
    """Un-park a marker BY NAME: a fresh budget and one authorised send.

    The principal's half of the contract. A park is terminal only until someone
    says otherwise — ``runaway.py rearm`` does this for a directive, and this does
    it for the order's marker. ``attempts`` resets: the principal has changed
    something the automated pass could not (the board, the runner, the directive),
    so the budget starts again rather than instantly re-parking.
    """
    path = path_for(reference, directory)
    marker = read(path)
    updated = Marker(
        reference=reference,
        issue=marker.issue if marker else None,
        state=SENT,
        ts=now_iso(moment),
        sent_at=marker.sent_at if marker else None,
        attempts=0,
        next_attempt_at=None,
        rearm={"at": now_iso(moment), "attempt": 0, "by": "operator", "reason": reason or "re-armed by name"},
        reason=None,
        evidence=marker.evidence if marker else None,
        announced=None,
    )
    write(path, updated)
    return updated


# --- CLI ---------------------------------------------------------------------


def _markers(directory: Path | str | None = None) -> list[Marker]:
    root = Path(DISPATCHED if directory is None else directory)
    if not root.is_dir():
        return []
    found = [read(path) for path in sorted(root.glob("*.json"))]
    return [marker for marker in found if marker is not None]


def cmd_status(args: argparse.Namespace) -> int:
    """What every marker says, with no judgement and no writes."""
    markers = _markers()
    if not markers:
        print("markers: none — the brain has dispatched nothing")
        return EXIT_OK
    counts: dict[str, int] = {}
    for marker in markers:
        counts[marker.state] = counts.get(marker.state, 0) + 1
    print(f"markers: {len(markers)} | " + " ".join(f"{state}={counts[state]}" for state in sorted(counts)))
    for marker in markers[: args.limit] if args.limit else markers:
        issue = f" #{marker.issue}" if marker.issue else ""
        armed = " ARMED" if marker.armed else ""
        print(
            f"  {marker.state:<9}{issue:<7} attempt={marker.attempts} "
            f"ts={marker.ts or '-'} {marker.reference}{armed}"
        )
    return EXIT_OK


def cmd_show(args: argparse.Namespace) -> int:
    """One marker, verbatim — the evidence the verdict was reached from."""
    marker = read(path_for(args.reference))
    if marker is None:
        print(f"markers: no marker for {args.reference}", file=sys.stderr)
        return EXIT_NOT_OK
    print(json.dumps(marker.payload(), indent=2))
    return EXIT_OK


def cmd_rearm(args: argparse.Namespace) -> int:
    """Un-park a marker by name (see :func:`rearm`)."""
    updated = rearm(args.reference, reason=args.reason or "")
    print(
        f"markers: re-armed {updated.reference} — state={updated.state}, budget reset "
        f"(attempts={updated.attempts}); the next advance will send it once"
    )
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fleet-markers", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    status = sub.add_parser("status", help="what every dispatch marker says (no judgement)")
    status.add_argument("--limit", type=int, default=0)
    status.set_defaults(func=cmd_status)
    show = sub.add_parser("show", help="one marker, verbatim")
    show.add_argument("--reference", required=True)
    show.set_defaults(func=cmd_show)
    rearm_cmd = sub.add_parser("rearm", help="un-park a marker by name, with a fresh budget")
    rearm_cmd.add_argument("--reference", required=True)
    rearm_cmd.add_argument("--reason", default="")
    rearm_cmd.set_defaults(func=cmd_rearm)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
