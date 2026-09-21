"""telemetry.clock — the ONE clock seam for telemetry's money path (#1025).

---knowledge---
module_id: telemetry.clock
system: telemetry
app: telemetry
solution_class: pattern
patterns: [the-one-seam, freeze-for-tests]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [now, now_utc_iso, now_epoch, today_utc, this_month_utc, frozen, is_frozen, frozen_instant]
invariants: "the single clock seam of the telemetry money path: no money-path module reads the wall clock directly"
gotchas: "tests freeze it through the FROZEN env seam; it replaces nine private per-module timestamp helpers"
related: ["#1025", "#1510"]
do_not_duplicate: null
---knowledge---


WHY this module exists
----------------------
The metering/FinOps path used to read the live clock in nine places under
``telemetry/``, each behind its *own* copy of a timestamp helper
(``now_utc_iso`` in ``metering/model``, ``budgets/model``,
``observability/model``; ``now_utc`` in ``ledger/schema``; ``_now_iso`` in
``observability/dashboard`` and ``role_health``; ``_now_epoch`` in
``role_health``; ``_now_utc`` in ``budgets/chargeback`` — plus one read with no
helper at all, inline in ``role_health.RoleHealthReport.write_json``).  Two
consequences, both measured (``docs/PYTHON-PATTERNS.md`` PP-1/PP-3/PP-4):

* **no test could freeze the clock.**  A fixture could pin its *seed* while the
  *evaluation* silently resolved to whatever day the suite happened to run on —
  the #506 date bomb: green on 2026-09-14, red every day after, with no commit
  in between;
* nine reads meant nine answers to "what time is it?" — the seam could not be
  varied, mocked or audited in one place.

WHAT this module is
-------------------
The single place under ``telemetry/`` that reads the clock, and the single place
a test or a gate turns it off.  Every money-path timestamp is derived here:

    now()             the instant, as an aware UTC ``datetime``
    now_utc_iso()     RFC 3339 ``YYYY-MM-DDTHH:MM:SSZ`` — the repo's wire shape
    now_epoch()       epoch seconds (heartbeat ages)
    today_utc()       ``YYYY-MM-DD`` bucket
    this_month_utc()  ``YYYY-MM`` bucket

The sibling modules keep their public helper *names* and delegate here, so this
is a consolidation of the seam and **not** a change to any public behaviour.

HOW a test or a gate freezes it
-------------------------------
``frozen(instant)`` — a context manager — or the ``FROZEN_ENV`` environment
variable, which is what lets a *whole* pytest run (or a gate invocation) be
pinned without editing any suite's ``conftest.py``::

    AO_FROZEN_CLOCK=2026-01-02T03:04:05Z python3 -m pytest telemetry/chat -q

The pinned value is validated at read time: a malformed instant raises rather
than quietly reverting to the live clock, because a freeze that silently does
nothing is the very failure this seam exists to stop (a control that cannot
fail is a formality — GR-12).

The override lives in the **environment** rather than in a module global on
purpose.  ``telemetry/`` is a PEP-420 namespace carrying no ``__init__.py``, and
its packages are imported under *two* roots — ``telemetry.ledger`` (repo root on
``sys.path``: ``python3 -m telemetry.ledger.cli``) and flat ``ledger``
(``telemetry/`` on ``sys.path``: ``telemetry/ledger/tests``,
``telemetry/audit/read_model.py``, ``scripts/check-audit-read-model.sh``).
Measured: in that flat context ``import telemetry.clock`` fails, so the ledger
module reaches this file by its second name, and **two module objects of this
file can coexist in one process**.  An environment variable is the one piece of
state they share, so both agree on what time it is; a module global would not.

WHAT deliberately does NOT live here
------------------------------------
Latency.  The request-latency clocks in ``observability/intake.py`` and
``observability/exposition.py`` are injectable parameters defaulting to
``time.monotonic`` — a *duration* clock, which cannot expire with the calendar
and is already injectable by construction.  This seam is the wall clock.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Iterator, Optional, Union

#: The RFC 3339 ``Z`` shape every timestamp in this pillar is written in.
TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

#: Environment seam: an ISO 8601 instant that pins every read below.  Unset —
#: the normal case — the clock is live.
FROZEN_ENV = "AO_FROZEN_CLOCK"

#: What :func:`frozen` accepts: an ISO 8601 string, or an aware ``datetime``.
Instant = Union[str, datetime]


def _parse_instant(value: str) -> datetime:
    """Parse a pinned instant, refusing anything ambiguous.

    A value this seam cannot understand must never be read as "no freeze":
    a typo in a gate's environment would silently restore the live clock and
    turn the frozen run back into the date bomb it exists to catch.
    """
    text = value.strip()
    if not text:
        raise ValueError(f"{FROZEN_ENV} is set but empty")
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(
            f"{FROZEN_ENV}={value!r} is not an ISO 8601 instant"
        ) from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{FROZEN_ENV}={value!r} carries no timezone")
    # Whole seconds: the wire shape this seam writes has no sub-second field, so
    # truncating here keeps now() and now_utc_iso() in exact agreement.
    return parsed.astimezone(UTC).replace(microsecond=0)


def frozen_instant() -> Optional[datetime]:
    """The pinned instant, or ``None`` while the clock is live."""
    raw = os.environ.get(FROZEN_ENV)
    return None if raw is None else _parse_instant(raw)


def is_frozen() -> bool:
    """Whether every read below is currently pinned."""
    return frozen_instant() is not None


def now() -> datetime:
    """The current instant as an aware UTC ``datetime`` — the one clock read."""
    pinned = frozen_instant()
    return pinned if pinned is not None else datetime.now(UTC)


def now_utc_iso() -> str:
    """The current instant as ``YYYY-MM-DDTHH:MM:SSZ``."""
    return now().strftime(TS_FORMAT)


def now_epoch() -> float:
    """The current instant as epoch seconds."""
    return now().timestamp()


def today_utc() -> str:
    """Today's UTC ``YYYY-MM-DD`` bucket."""
    return now_utc_iso()[:10]


def this_month_utc() -> str:
    """This UTC month's ``YYYY-MM`` bucket."""
    return now_utc_iso()[:7]


@contextmanager
def frozen(instant: Instant) -> Iterator[None]:
    """Pin the clock for the duration of the block, restoring it after.

    Nests safely: an outer freeze is restored rather than dropped.
    """
    if isinstance(instant, datetime):
        if instant.tzinfo is None:
            raise ValueError("frozen() needs an aware datetime")
        value = instant.astimezone(UTC).strftime(TS_FORMAT)
    else:
        value = _parse_instant(instant).strftime(TS_FORMAT)
    previous = os.environ.get(FROZEN_ENV)
    os.environ[FROZEN_ENV] = value
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(FROZEN_ENV, None)
        else:
            os.environ[FROZEN_ENV] = previous
