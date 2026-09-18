#!/usr/bin/env python3
"""Bounded runaway guard for directive dispatch (issue #723).

WHY THIS EXISTS (measured)
--------------------------
An order the fleet cannot execute used to be re-read every cycle for ever.
``fleet/terminal.py`` had four such paths and none of them carried an attempt
counter, a delay or a terminal state:

* the ``if not claimed:`` branch escalated ONCE — keyed on the refusal TEXT, so
  a second, differently-worded refusal escalated again — and ``continue``d,
  leaving the directive pending;
* the ``held_action == "self-heal"`` branch reaped an own-dead claim and
  re-dispatched it in the SAME cycle;
* the ``in-flight`` and ``orphaned`` branches both reported and ``continue``d.

A refused claim or a crashed run therefore burned one watch cycle per loop
iteration for ever, and the escalation dedupe (``report_once``) hid it: it
dedupes the *report*, never the *attempt*. This module is the bound.

THE CONTRACT (harvested, never invented — GR-10)
------------------------------------------------
The budget and the backoff are harvested from the CMR hub's
``vendor/CMR/ops/retry.sh``, which declares ``delay = BACKOFF * 2^(attempt-1)``
seconds capped at ``300``, and ``--attempts N`` as the budget after which the
command gives up. That contract is reimplemented here for a *directive* rather
than a command; no second backoff is invented. (``scripts/check-runaway-guard.sh``
pins the formula numerically and re-reads the vendored script whenever the
``vendor/CMR`` submodule is initialised, so the two cannot drift apart.)

The knobs are configurable, and their defaults are documented here and nowhere
else:

============ ====================== ======= ==================================
knob         env                    default meaning
============ ====================== ======= ==================================
K (cap)      ``AO_RUNAWAY_ATTEMPTS``  ``5``  attempts before the directive is
                                            dead-lettered; a refused claim and a
                                            crashed run count against the SAME
                                            counter, and a self-heal counts as
                                            an attempt rather than a fresh start
base         ``AO_RUNAWAY_BACKOFF``   ``30`` seconds; the delay after attempt
                                            *n* is ``min(base * 2**(n-1), 300)``
============ ====================== ======= ==================================

``30`` and not ``retry.sh``'s ``5``: the dispatcher loop's poll cycle is ~30s
(``terminal.py --watch-timeout 30``), so a 5s base would elapse inside a single
cycle and the "spacing" would be decorative. With the defaults the four waits
are 30/60/120/240s and the fifth failure is terminal.

An unusable value (``AO_RUNAWAY_ATTEMPTS=zero``) is REFUSED with
:class:`RunawayConfigError` — a typo must never silently disable the guard. The
refusal is raised where the DECISION is taken (:func:`record_attempt`); the READ
paths (:func:`dispatchable`, :func:`load`) stay tolerant, because a misconfigured
guard must fail LOUD and OPEN — it must never wedge the work queue by refusing
to dispatch healthy orders. That asymmetry is deliberate and is proved by
``scripts/check-runaway-guard.sh``.

STATE (survives a loop restart)
-------------------------------
Everything is persisted as small JSON files under the fleet's runtime directory,
beside the run markers (the same shape as ``terminal.mark_run`` / ``run_state``):

    <fleet>/attempts/<directive>.json     the persisted attempt count
    <fleet>/dead-letter/<directive>.json  the terminal artifact: the directive's
                                          own envelope, its attempt history, the
                                          reason it was retired — the audit

The counter is read from disk on every call and written atomically (tmp +
rename), so it survives a loop restart (acceptance criterion 1) and a torn write
cannot corrupt it. The dead-letter ARTIFACT is authoritative: a directive whose
order landed in ``dead-letter/`` is terminal even if its counter file is torn,
missing or re-planted in the inbox.

BACKOFF MUST NOT BLOCK THE LOOP
-------------------------------
Nothing here sleeps. A directive whose ``next_attempt_at`` has not arrived is
simply not returned by ``channel watch``: ``cmd_watch`` filters on
:func:`dispatchable` exactly as it already filters ``--skip``ped ids, so the loop
keeps polling, the order stays in the inbox, and the worker slot stays free.

CLI (principal)::

    python3 fleet/runaway.py status                    # counts, no judgement
    python3 fleet/runaway.py show --directive <id>     # one directive's history
    python3 fleet/runaway.py rearm --directive <id>    # re-arm after a fix
    python3 fleet/runaway.py dead-letter [--directive <id>]  # the mailbox, by verb

Exit codes are the repo tri-state: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.

A SECOND CALLER (issue #754)
----------------------------
Retiring a directive is not only the guard's business. A peer agent or an
principal can *know* an order is dead — the issue's work already landed on
`master`, the directive was re-minted from a stale queue — and must be able to
say so over the control channel instead of `mv`-ing a file out of
`.fleet/inbox/` while the loop reads it. That is `control:drop`, and it calls
the SAME :func:`dead_letter` used here: only the ``dropped_by`` label differs.
:data:`RECORD_FIELDS` names the shape both callers produce, and
``scripts/check-dead-letter.sh`` asserts the two records are identical field-
for-field, so a change to one path cannot silently miss the other.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import runtime

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from governance.dispatch.model import TERMINAL_CLAIM_REASONS  # noqa: E402

#: The harvested cap: `vendor/CMR/ops/retry.sh` never sleeps longer than this.
BACKOFF_CAP_SECONDS = 300

#: The delay after the first failed attempt, in seconds (see the table above).
DEFAULT_BACKOFF_SECONDS = 30

#: How many attempts a directive gets before it is retired, by default.
DEFAULT_ATTEMPT_CAP = 5

ENV_ATTEMPT_CAP = "AO_RUNAWAY_ATTEMPTS"
ENV_BACKOFF = "AO_RUNAWAY_BACKOFF"

#: How many reasons a record keeps; the history is for the principal's eye, and an
#: unbounded list on a wedged directive is itself a (small) runaway.
REASON_HISTORY = 8

STATE_PENDING = "pending"
STATE_DEAD_LETTER = "dead-letter"

#: Tri-state exit codes (repo convention, GR-12 / guardrails/honesty).
EXIT_OK = 0
EXIT_NOT_OK = 1
EXIT_CANNOT_ASSESS = 2


class RunawayConfigError(ValueError):
    """The declared budget cannot be read — refuse, never guess."""


# --- the harvested budget ----------------------------------------------------


def _positive_int(name: str, default: int) -> int:
    """Read a positive integer from the environment; refuse anything else.

    An absent or empty variable is the default (the documented contract). Any
    other unreadable value raises: silently substituting the default for a typo
    is how a guard becomes decorative, and silently substituting ``0`` is how a
    runaway is re-enabled.
    """
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        raise RunawayConfigError(
            f"{name}={raw.strip()!r} is not an integer — the runaway guard refuses an "
            "unreadable budget rather than silently disabling its own bound"
        ) from None
    if value < 1:
        raise RunawayConfigError(f"{name}={value} must be >= 1")
    return value


def attempt_cap() -> int:
    """K: the attempt budget before a directive is retired. Raises on a bad value."""
    return _positive_int(ENV_ATTEMPT_CAP, DEFAULT_ATTEMPT_CAP)


def backoff_base() -> int:
    """The backoff base, in seconds. Raises on a bad value."""
    return _positive_int(ENV_BACKOFF, DEFAULT_BACKOFF_SECONDS)


def cap_or_default() -> int:
    """K for a READ path — tolerant, so a misconfigured guard cannot wedge the queue.

    See the module docstring: the decision path refuses loudly, the read path
    fails open. A wrong cap on a read only mis-states ``exhausted``; refusing to
    read at all would stop ``channel watch`` from returning healthy orders.
    """
    try:
        return attempt_cap()
    except RunawayConfigError:
        return DEFAULT_ATTEMPT_CAP


def backoff_delay(attempt: int, base: int | None = None) -> int:
    """The delay after attempt *n*: ``min(base * 2**(n-1), 300)``.

    The harvested formula, verbatim. ``attempt`` is the number of attempts
    already made (1 after the first failure), so the first wait is the base.
    """
    if attempt < 1:
        raise ValueError(f"attempt must be >= 1, got {attempt!r}")
    delay = (backoff_base() if base is None else int(base)) * (2 ** (attempt - 1))
    return min(delay, BACKOFF_CAP_SECONDS)


# --- paths (declared once in fleet/runtime.py, re-based here) ----------------


def _root(base: Path | str | None) -> Path:
    return Path(base) if base is not None else Path(runtime.FLEET_DIR)


def attempts_dir(base: Path | str | None = None) -> Path:
    """``<fleet>/attempts`` — the persisted counters."""
    return _root(base) / runtime.ATTEMPTS.name


def dead_letter_dir(base: Path | str | None = None) -> Path:
    """``<fleet>/dead-letter`` — the terminal store."""
    return _root(base) / runtime.DEAD_LETTER.name


def inbox_dir(base: Path | str | None = None) -> Path:
    """``<fleet>/inbox`` — the mailbox ``channel.INBOX`` also derives from the root."""
    return _root(base) / "inbox"


# --- time (ISO in the file, epoch for the arithmetic) ------------------------


def _now_epoch(now: float | None = None) -> float:
    return time.time() if now is None else float(now)


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _epoch(stamp: object) -> float | None:
    """Parse an ISO-8601 UTC stamp; anything else reads as unknown (None)."""
    if not isinstance(stamp, str):
        return None
    try:
        return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        ).timestamp()
    except ValueError:
        return None


# --- the persisted record ----------------------------------------------------


@dataclass(frozen=True)
class Attempt:
    """One directive's persisted attempt history."""

    directive_id: str
    attempts: int
    cap: int
    first_seen: str
    last_attempt: str
    next_attempt_at: str | None
    reasons: tuple[str, ...] = ()
    state: str = STATE_PENDING
    dead_lettered_at: str | None = None

    @property
    def exhausted(self) -> bool:
        """True once the cap is reached — the caller must retire the directive."""
        return self.attempts >= self.cap

    @property
    def dead_lettered(self) -> bool:
        return self.state == STATE_DEAD_LETTER

    @property
    def next_attempt_epoch(self) -> float | None:
        return _epoch(self.next_attempt_at)

    def as_dict(self) -> dict:
        return {
            "directive_id": self.directive_id,
            "attempts": self.attempts,
            "cap": self.cap,
            "state": self.state,
            "first_seen": self.first_seen,
            "last_attempt": self.last_attempt,
            "next_attempt_at": self.next_attempt_at,
            "reasons": list(self.reasons),
            "dead_lettered_at": self.dead_lettered_at,
        }

    @classmethod
    def from_dict(cls, data: dict, *, cap: int) -> "Attempt":
        """Rebuild from disk, tolerating a partial or older record.

        A torn record must not crash the loop that reads it: missing fields fall
        back to safe values, and an unreadable ``attempts`` counts as 0 (due),
        never as exhausted.
        """
        try:
            attempts = max(0, int(data.get("attempts", 0)))
        except (TypeError, ValueError):
            attempts = 0
        reasons = data.get("reasons")
        if not isinstance(reasons, list):
            reasons = []
        return cls(
            directive_id=str(data.get("directive_id") or ""),
            attempts=attempts,
            cap=cap,
            first_seen=str(data.get("first_seen") or ""),
            last_attempt=str(data.get("last_attempt") or ""),
            next_attempt_at=(
                data.get("next_attempt_at")
                if isinstance(data.get("next_attempt_at"), str)
                else None
            ),
            reasons=tuple(str(reason) for reason in reasons),
            state=str(data.get("state") or STATE_PENDING),
            dead_lettered_at=(
                data.get("dead_lettered_at")
                if isinstance(data.get("dead_lettered_at"), str)
                else None
            ),
        )


def _write_json(path: Path, payload: dict) -> None:
    """Write atomically (tmp + rename), so a reader never sees a torn file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def dead_lettered(directive_id: str, base: Path | str | None = None) -> bool:
    """True when the directive's order is in the terminal store."""
    return (dead_letter_dir(base) / f"{directive_id}.json").exists()


#: The keys every dead-letter record carries, in one place (issue #754).
#:
#: The record has TWO callers — the automatic path (`terminal.guard_retire`, when
#: the attempt budget is exhausted) and the operator/A2A verb
#: (`control:drop`, when a peer tells the dispatcher an order is dead). They must
#: produce the *same shape*, and the only way to guarantee that is for the shape
#: to be built in one function that both call. This tuple is the contract the
#: gate asserts against, so a field cannot be added to one caller's record and
#: silently missed by the other.
RECORD_FIELDS = (
    "id",
    "issue",
    "reason",
    "attempts",
    "dropped_by",
    "ts",
)


def record_shape(base: Path | str | None = None, directive_id: str | None = None) -> dict:
    """The dead-letter record for a directive, normalised to :data:`RECORD_FIELDS`.

    The store's own payload (``Attempt.as_dict()`` plus ``reason``/``envelope``)
    is the durable artifact; this is its *named* projection, and it exists so the
    auto path and the A2A verb agree by construction rather than by convention.
    ``id``/``issue`` come from the stored envelope when it is present, so a
    dropped directive's issue is recorded even though the caller may only have
    had the directive id.
    """
    payload = _read_json(dead_letter_dir(base) / f"{directive_id or ''}.json") or {}
    envelope = payload.get("envelope") if isinstance(payload.get("envelope"), dict) else {}
    task = envelope.get("task") if isinstance(envelope.get("task"), dict) else {}
    return {
        "id": str(envelope.get("id") or directive_id or payload.get("directive_id") or ""),
        "issue": task.get("issue"),
        "reason": payload.get("reason"),
        "attempts": payload.get("attempts"),
        "dropped_by": payload.get("dropped_by"),
        "ts": payload.get("dead_lettered_at"),
    }


def load(directive_id: str, base: Path | str | None = None) -> Attempt | None:
    """The directive's record, or None when it has never failed.

    The dead-letter artifact is authoritative: if the order was retired, the
    returned record says so even when the counter file is absent or torn.
    """
    cap = cap_or_default()
    data = _read_json(attempts_dir(base) / f"{directive_id}.json")
    record = Attempt.from_dict(data, cap=cap) if data is not None else None
    if dead_lettered(directive_id, base):
        if record is None:
            artifact = _read_json(dead_letter_dir(base) / f"{directive_id}.json") or {}
            record = Attempt.from_dict(artifact, cap=cap)
        return Attempt(
            directive_id=record.directive_id or directive_id,
            attempts=record.attempts,
            cap=cap,
            first_seen=record.first_seen,
            last_attempt=record.last_attempt,
            next_attempt_at=None,
            reasons=record.reasons,
            state=STATE_DEAD_LETTER,
            dead_lettered_at=record.dead_lettered_at,
        )
    return record


def record_attempt(
    directive_id: str,
    reason: str,
    *,
    base: Path | str | None = None,
    now: float | None = None,
) -> Attempt:
    """Count one failed attempt and stamp when the next one may be made.

    A refused claim and a crashed run increment the SAME counter, and the
    self-heal path counts as an attempt rather than restarting the budget (an
    acceptance criterion, not an implementation detail). ``reason`` is kept in a
    bounded history so the principal sees WHY the directive is being retired
    instead of a bare number.
    """
    cap = attempt_cap()
    epoch = _now_epoch(now)
    stamp = _iso(epoch)
    current = _read_json(attempts_dir(base) / f"{directive_id}.json")
    previous = Attempt.from_dict(current, cap=cap) if current is not None else None
    attempts = (previous.attempts if previous else 0) + 1
    reasons = list(previous.reasons) if previous else []
    if reason:
        reasons.append(reason)
    record = Attempt(
        directive_id=directive_id,
        attempts=attempts,
        cap=cap,
        first_seen=(previous.first_seen if previous and previous.first_seen else stamp),
        last_attempt=stamp,
        next_attempt_at=_iso(epoch + backoff_delay(attempts)),
        reasons=tuple(reasons[-REASON_HISTORY:]),
        state=STATE_PENDING,
        dead_lettered_at=None,
    )
    _write_json(attempts_dir(base) / f"{directive_id}.json", record.as_dict())
    return record


def remaining(directive_id: str, base: Path | str | None = None, now: float | None = None) -> float:
    """Seconds until the directive may be dispatched again (0.0 when due)."""
    record = load(directive_id, base)
    if record is None or record.next_attempt_epoch is None:
        return 0.0
    return max(0.0, record.next_attempt_epoch - _now_epoch(now))


def due(directive_id: str, base: Path | str | None = None, now: float | None = None) -> bool:
    """True when the backoff has elapsed and the directive may be retried."""
    return remaining(directive_id, base, now) == 0.0


def dispatchable(
    directive_id: str, base: Path | str | None = None, now: float | None = None
) -> bool:
    """The predicate ``channel watch`` filters on.

    False for a dead-lettered directive (which must NEVER be dispatched again)
    and for one whose backoff has not elapsed (which would otherwise be a
    busy-wait). True for a directive the guard has never seen.
    """
    if dead_lettered(directive_id, base):
        return False
    return due(directive_id, base, now)


def terminal_classification(reason: str) -> str | None:
    """The named terminal reason a refusal carries, or None when it is retryable.

    Issue #861: a refusal for a closed issue, a closed epic, or an unowned unit
    is terminal BY DEFINITION — no attempt, backoff or board refresh can ever
    cure it — and is not distinguishable, by the generic bounded retry, from a
    refusal worth retrying (``already-claimed``, ``blocked``, the
    ``snapshot-stale`` case #727 already self-heals). Left to the generic path,
    such a refusal is retried up to K times (30 + 60 + 120 + 240 ≈ 450s of a
    held queue slot with the defaults) before landing in the SAME dead-letter
    store it could have reached on attempt 1.

    The classification is read from :data:`governance.dispatch.model.TERMINAL_CLAIM_REASONS`
    — the claim layer's own vocabulary (``governance/dispatch/cli.py`` prints
    ``claim REFUSED: <reason> — <detail>``) — rather than a second vocabulary
    invented here, so the two cannot drift apart. Matched by substring because
    the caller passes free text (``claim_output``), not a structured refusal.
    """
    if not reason:
        return None
    for terminal_reason in TERMINAL_CLAIM_REASONS:
        if terminal_reason in reason:
            return terminal_reason
    return None


def dead_letter(
    directive_id: str,
    reason: str,
    *,
    base: Path | str | None = None,
    inbox: Path | str | None = None,
    now: float | None = None,
    dropped_by: str = "runaway-guard",
) -> Path:
    """Retire a directive: stamp the terminal state, move the order, return the artifact.

    The order is MOVED out of the inbox into ``<fleet>/dead-letter/`` (so it can
    never be returned again) and the artifact carries the original envelope, the
    attempt history and the reason — the audit a principal needs to re-order it.
    The counter record is stamped terminal too, so a reader of ``attempts/``
    cannot mistake it for a live budget.

    ``dropped_by`` names WHO retired the order. There are two callers and they
    share this function (issue #754): the automatic path (the attempt budget was
    exhausted) passes the default, and the operator/A2A ``control:drop`` verb
    passes the sender. Because it is a *parameter of the one implementation*
    rather than a second implementation, the two records cannot drift in shape —
    which is the acceptance criterion, not a nicety.
    """
    epoch = _now_epoch(now)
    stamp = _iso(epoch)
    cap = cap_or_default()
    record = load(directive_id, base) or Attempt.from_dict({}, cap=cap)
    reasons = list(record.reasons)
    if reason and reason not in reasons:
        reasons.append(reason)
    source = (Path(inbox) if inbox is not None else inbox_dir(base)) / f"{directive_id}.json"
    envelope = _read_json(source)
    terminal = Attempt(
        directive_id=directive_id,
        attempts=record.attempts,
        cap=cap,
        first_seen=record.first_seen or stamp,
        last_attempt=record.last_attempt or stamp,
        next_attempt_at=None,
        reasons=tuple(reasons[-REASON_HISTORY:]),
        state=STATE_DEAD_LETTER,
        dead_lettered_at=stamp,
    )
    payload = {
        **terminal.as_dict(),
        "reason": reason,
        "dropped_by": dropped_by,
        "envelope": envelope,
    }
    target = dead_letter_dir(base) / f"{directive_id}.json"
    _write_json(target, payload)
    _write_json(
        attempts_dir(base) / f"{directive_id}.json",
        {**terminal.as_dict(), "reason": reason, "dropped_by": dropped_by},
    )
    try:
        source.unlink()
    except OSError:
        pass
    return target


def forget(directive_id: str, base: Path | str | None = None) -> bool:
    """Drop the counter of a HANDLED directive; never the dead-letter artifact.

    Called when a directive is consumed (reported or handled as a control): the
    order is finished, so its budget is history and the state directory must not
    grow a file per directive ever seen. Terminal evidence survives — a retired
    directive keeps its artifact.
    """
    path = attempts_dir(base) / f"{directive_id}.json"
    try:
        path.unlink()
        return True
    except OSError:
        return False


def rearm(directive_id: str, base: Path | str | None = None) -> bool:
    """Return a retired directive to the queue — the principal's explicit override.

    The ONLY route out of the terminal state, and deliberately a human verb:
    the guard's whole point is that a directive does not come back on its own.
    """
    removed = forget(directive_id, base)
    path = dead_letter_dir(base) / f"{directive_id}.json"
    try:
        path.unlink()
        removed = True
    except OSError:
        pass
    return removed


def inventory(base: Path | str | None = None) -> dict:
    """Counts and names — a status view, never a judgement."""

    def stems(directory: Path) -> list[str]:
        if not directory.exists():
            return []
        return sorted(path.stem for path in directory.glob("*.json"))

    return {
        "counters": stems(attempts_dir(base)),
        "dead_letters": stems(dead_letter_dir(base)),
    }


# --- principal CLI ------------------------------------------------------------


def cmd_status(args: argparse.Namespace) -> int:
    state = inventory(args.fleet_dir)
    print(
        f"runaway guard: cap={cap_or_default()} attempt(s), base={DEFAULT_BACKOFF_SECONDS}s, "
        f"cap-delay={BACKOFF_CAP_SECONDS}s ({ENV_ATTEMPT_CAP}, {ENV_BACKOFF} override both)"
    )
    print(
        f"runaway guard: {len(state['counters'])} directive(s) carrying a budget, "
        f"{len(state['dead_letters'])} dead-lettered"
    )
    for directive_id in state["dead_letters"]:
        print(f"  dead-letter  {directive_id}")
    return EXIT_OK


def cmd_show(args: argparse.Namespace) -> int:
    record = load(args.directive, args.fleet_dir)
    artifact = _read_json(dead_letter_dir(args.fleet_dir) / f"{args.directive}.json")
    if record is None and artifact is None:
        print(f"runaway guard: no state for {args.directive}", file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    payload = dict(record.as_dict()) if record is not None else dict(artifact or {})
    if artifact is not None:
        payload["reason"] = artifact.get("reason")
        payload["has_envelope"] = artifact.get("envelope") is not None
    print(json.dumps(payload, indent=2, sort_keys=True))
    return EXIT_OK


def cmd_rearm(args: argparse.Namespace) -> int:
    if rearm(args.directive, args.fleet_dir):
        print(f"runaway guard: re-armed {args.directive} (budget reset, retryable again)")
        return EXIT_OK
    print(f"runaway guard: nothing to re-arm for {args.directive}", file=sys.stderr)
    return EXIT_NOT_OK


def cmd_dead_letter(args: argparse.Namespace) -> int:
    """List/inspect the dead-letter mailbox — a verb, never a filesystem read.

    The issue asks for this explicitly: a principal (or the director) must be able
    to see WHAT was dropped and WHY without reaching into the runtime directory.
    With no ``--directive`` it lists every retired order with its one-line
    summary; with one, it prints the full normalised record (the same
    :data:`RECORD_FIELDS` shape the verb and the automatic path both write).
    """
    state = inventory(args.fleet_dir)
    if not state["dead_letters"]:
        print("runaway guard: dead-letter mailbox is empty")
        return EXIT_NOT_OK
    if args.directive:
        if args.directive not in state["dead_letters"]:
            print(f"runaway guard: {args.directive} is not in the dead-letter mailbox", file=sys.stderr)
            return EXIT_CANNOT_ASSESS
        print(json.dumps(record_shape(args.fleet_dir, args.directive), indent=2, sort_keys=True))
        return EXIT_OK
    print(f"runaway guard: {len(state['dead_letters'])} dead-lettered directive(s)")
    for directive_id in state["dead_letters"]:
        record = record_shape(args.fleet_dir, directive_id)
        print(
            f"  {directive_id}  issue={record.get('issue')}  attempts={record.get('attempts')}  "
            f"by={record.get('dropped_by')}  at={record.get('ts')}\n"
            f"    reason: {record.get('reason')}"
        )
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fleet-runaway", description=__doc__)
    parser.add_argument(
        "--fleet-dir",
        default=None,
        help="the fleet runtime directory (default AO_FLEET_DIR, else <repo>/.fleet)",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    status = sub.add_parser("status", help="count the counters and the dead letters")
    status.set_defaults(func=cmd_status)
    show = sub.add_parser("show", help="print one directive's guard state")
    show.add_argument("--directive", required=True)
    show.set_defaults(func=cmd_show)
    rearm_cmd = sub.add_parser("rearm", help="return a retired directive to the queue")
    rearm_cmd.add_argument("--directive", required=True)
    rearm_cmd.set_defaults(func=cmd_rearm)
    mailbox = sub.add_parser("dead-letter", help="list/inspect the dead-letter mailbox")
    mailbox.add_argument("--directive", default=None, help="print one record in full")
    mailbox.set_defaults(func=cmd_dead_letter)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except RunawayConfigError as exc:
        print(f"runaway guard: NOT-OK — {exc}", file=sys.stderr)
        return EXIT_NOT_OK


if __name__ == "__main__":
    raise SystemExit(main())
