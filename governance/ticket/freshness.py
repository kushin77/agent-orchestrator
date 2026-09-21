"""Board-snapshot freshness — the age this consumer tolerates (issue #1077).

---knowledge---
module_id: governance.ticket.freshness
system: governance
app: ticket
solution_class: pattern
patterns: [provoked-negative-control, no-false-green, fail-closed, offline-hermetic, deterministic]
derives_from: null
owner_sme: pmo-sme
tier: L1
interfaces: [Freshness, parse_iso, assess]
invariants: ""
gotchas: ""
related: ["#170", "#665", "#727", "#1029", "#1034", "#1043"]
do_not_duplicate: null
---knowledge---

``.board/snapshot.json`` is the **committed, offline stand-in for the live
board**. Every consumer of it is therefore reading a *point-in-time* artifact
whose frontier falls behind the board the moment the board moves. That is not a
theoretical hazard for this package: it is the defect that has now been recorded
three times.

* ``INC-0005`` / ``CA-0007`` / ``SUGGEST-0002`` (#170) — the **reader** side:
  ``governance/dispatch`` refuses a snapshot past its threshold with
  ``snapshot-stale`` rather than answering from it.
* ``INC-0014`` / ``CA-0018`` / ``SUGGEST-0012`` (#727) — the **writer** side, and
  the framing this module implements: *"a committed snapshot needs a trigger, or
  its freshness rule becomes an outage… a stated tolerance … and a refresh the
  invalidating event itself triggers. Two of three is worse than none, because
  the refusal is correct and unactionable."*
* **#1077** — this package, the last consumer with **no stated tolerance**. The
  snapshot sat at ``2026-09-15T04:28:44Z`` (460 issues) while the board reached
  571, so ``INC-0007``'s ``origin`` of ``#1029`` no longer resolved and the
  projection refused to build. Because ``check-pmo-rollup.sh`` derives every view
  from that same projection, **one stale file reddened two gates of record**
  (``check-ticket-projection``, ``check-pmo-rollup``) and, being unattributable
  to a lane, starved *every* lane's landing. Three separate lanes (#1034, #1043,
  #665) mis-measured "master red" from it.

## The stated tolerance, and why it is not the dispatch one

``governance/policy/lease.SNAPSHOT_STALENESS_MINUTES`` is **15 minutes**. That is
a *liveness* tolerance for a running dispatch loop, which can refresh and re-check
inside one cycle — a committed artifact verified offline cannot possibly meet it
(the file is already hours old when any clean checkout reads it), and a check
armed with it would be red always, i.e. a formality (GR-12).

The tolerance here is the one a **committed artifact** can honour:
:data:`DEFAULT_MAX_AGE_HOURS` = **72 hours**. Two measured facts fix that number:

* the artifact's observed refresh cadence is *incidental* — 9 commits in the
  whole history, gaps of ~9h, ~12h, ~21h, and the 45h+ staleness that produced
  #1077 — so 72h is a **backstop** rather than the primary detector;
* the primary detector is already exact and clock-free: an authority-tracked
  reference to an issue the snapshot does not carry fails with
  ``reference-unresolved``. It fired within hours of the rot (``#1029`` was
  created and referenced the same day). The age bound exists for the case that
  reference check cannot see: **the board moved but no committed ledger
  references the new issues**, which silently stales the PMO views
  (``deps``/``lanes``/``report``/``raid``/``aging``) without any reference
  failing.

72 hours is ~1.6x the worst observed gap and ~3x the typical one, so it fires on
an unambiguous failure of the refresh discipline rather than on a quiet stretch,
and its refusal is **actionable**: it names the file, the timestamp, the age, the
tolerance and the one refresh verb.

## Where this assertion lives, and where it deliberately does not

``sources.py`` documents that **no reader consults the wall clock**, because the
projection must rebuild byte-identically. This module therefore sits *beside* the
projection, not inside it: ``build()`` and ``verify()`` stay pure functions of the
committed ledgers, and freshness is a separate, explicit assertion the gate runs.
``--now`` exists so the gate can provoke the refusal deterministically; no caller
in the fleet passes it, and the gate's assertion on the real snapshot never does.

Everything here is stdlib-only and offline: it reads the snapshot's own
``generated_at``. It cannot fetch — ``scripts/verify.sh`` is offline by design —
so it *detects* staleness and names the remedy instead of performing it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from model import (
    BOARD_RELPATH,
    CODE_BOARD_STALE,
    CODE_BOARD_UNAGED,
    CannotAssess,
    Violation,
)

#: The age this consumer tolerates on the committed board snapshot. See the
#: module docstring for the measurement behind the number: it is a backstop for
#: rot the reference check cannot see, not the primary detector.
DEFAULT_MAX_AGE_HOURS = 72

#: The one refresh verb (``governance/dispatch``, #727). Named in every refusal
#: so the remedy travels with the finding — RCA-0014's "the refusal is correct and
#: unactionable" is the failure mode this string exists to prevent.
REFRESH_COMMAND = "python3 governance/dispatch/cli.py snapshot --from-github"


@dataclass(frozen=True)
class Freshness:
    """The assessed age of the committed board snapshot.

    A snapshot that cannot be read at all never reaches here: ``assess`` raises
    :class:`CannotAssess` for it (rc 2), which is the same fail-closed posture as
    ``read_board``. Everything that *can* be assessed is dated — or is a violation
    naming why it is not.
    """

    ok: bool
    generated_at: str
    age_hours: float
    max_age_hours: float
    violations: tuple[Violation, ...] = ()

    def render(self) -> str:
        stamp = self.generated_at or "no generated_at"
        return (
            f"{BOARD_RELPATH}: generated_at {stamp}, "
            f"age {self.age_hours:.1f}h (tolerance {self.max_age_hours:g}h)"
        )


def parse_iso(value: str) -> datetime:
    """Parse the snapshot's ``generated_at``; raise ``ValueError`` when it is not.

    ``Z`` is accepted because that is the form the writer emits, and a naive
    timestamp is read as UTC rather than as local time (the fleet is
    UTC-everywhere).
    """
    text = str(value).strip()
    if not text:
        raise ValueError("empty timestamp")
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _unaged(detail: str) -> tuple[Violation, ...]:
    return (
        Violation(
            CODE_BOARD_UNAGED,
            BOARD_RELPATH,
            f"{detail} — a snapshot whose age cannot be established is not trusted "
            f"(refresh it with: {REFRESH_COMMAND})",
        ),
    )


def assess(
    root: Path | str,
    *,
    max_age_hours: float | None = None,
    now: datetime | None = None,
) -> Freshness:
    """Assert the committed board snapshot carries an age inside the tolerance.

    Fails closed at every step: a snapshot that is missing or unreadable is
    ``CannotAssess`` (rc 2, never a pass), one whose ``generated_at`` is absent or
    unparseable is a violation naming it, and one older than the tolerance is a
    violation naming the file, the timestamp, the age, the tolerance and the
    refresh verb. Only a *dated* snapshot inside the tolerance is OK.
    """
    tolerance = DEFAULT_MAX_AGE_HOURS if max_age_hours is None else float(max_age_hours)
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    moment = moment.astimezone(timezone.utc)

    path = Path(root) / BOARD_RELPATH
    if not path.is_file():
        raise CannotAssess(f"board snapshot missing: {BOARD_RELPATH}")

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CannotAssess(f"board snapshot unreadable: {BOARD_RELPATH} ({exc})") from exc
    if not isinstance(payload, dict):
        raise CannotAssess(f"board snapshot is not a JSON object: {BOARD_RELPATH}")

    raw = payload.get("generated_at")
    if not isinstance(raw, str) or not raw.strip():
        return Freshness(
            False,
            "",
            float("inf"),
            tolerance,
            _unaged("the snapshot carries no generated_at"),
        )
    try:
        generated = parse_iso(raw)
    except ValueError as exc:
        return Freshness(
            False,
            raw,
            float("inf"),
            tolerance,
            _unaged(f"the snapshot's generated_at {raw!r} is not a timestamp ({exc})"),
        )

    age_hours = max(0.0, (moment - generated).total_seconds() / 3600.0)
    if age_hours <= tolerance:
        return Freshness(True, raw, age_hours, tolerance)

    return Freshness(
        False,
        raw,
        age_hours,
        tolerance,
        (
            Violation(
                CODE_BOARD_STALE,
                BOARD_RELPATH,
                f"generated_at {raw} is {age_hours:.1f}h old, beyond the "
                f"{tolerance:g}h this consumer tolerates — the board has moved since "
                f"it was written, so the projection resolves against a stale frontier "
                f"(refresh it with: {REFRESH_COMMAND})",
                BOARD_RELPATH,
            ),
        ),
    )
