"""Board-snapshot freshness — the age the committed dispatch queue tolerates.

---knowledge---
module_id: governance.dispatch.queue_freshness
system: governance
app: dispatch
solution_class: enterprise
patterns: [provoked-negative-control, fail-closed, offline-hermetic, deterministic]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [CannotAssess, Finding, Freshness, tolerance_minutes, parse_iso, assess]
invariants: ""
gotchas: ""
related: ["#170", "#727", "#1077", "#1189"]
do_not_duplicate: null
---knowledge---

``.board/snapshot.json`` is the **committed, offline stand-in for the live
board**: the gate of record runs offline (``scripts/verify.sh`` makes no network
call), so every "is this issue still open?" answer is resolved against a
point-in-time artifact whose frontier the board has already left. That is not a
theoretical hazard — it is a defect this repository has now recorded four times:

* ``INC-0005`` / ``CA-0007`` / ``SUGGEST-0002`` (#170) — the **reader** side:
  ``governance/dispatch`` refuses a snapshot past its threshold with
  ``snapshot-stale`` rather than answering from it;
* ``INC-0014`` / ``CA-0018`` / ``SUGGEST-0012`` (#727) — the **writer** side: *"a
  committed snapshot needs a trigger, or its freshness rule becomes an outage…
  a stated tolerance … and a refresh the invalidating event itself triggers."*
* **#1077** (``governance/ticket/freshness.py``) — the projection's consumer half;
* **#1189** — this module, the last consumer with no stated tolerance.

## The defect this closes

``check-dispatch-queue`` ran ``queue --check`` with the **liveness** threshold
(``governance/policy/lease.SNAPSHOT_STALENESS_MINUTES`` = 15 minutes, read through
``controls.yaml``) — a bound for a *running dispatch loop*, which can refresh and
re-check inside one cycle. A committed artifact verified offline can never meet
it: measured on this lane, the artifact was **20.4 hours old** at the tip, so the
check refused ``snapshot-stale`` on **every** run — rc 2 CANNOT-ASSESS, which
``scripts/verify.sh`` records as **SKIP** while the verdict line still reads as a
pass for everything else. The measured cost is the finding the check exists for:
**six queued issues that had already closed** stayed invisible behind a
permanently-skipped check, and the skipped rc 2 sat in every one of the 13 open
PRs' composite runs.

## The stated tolerance, and why it is not the liveness one

:data:`DEFAULT_MAX_AGE_HOURS` = **72 hours** — the same tolerance the ticket
projection declares for the same artifact, for the same reason: it is the age a
*committed* artifact can honour. Two measured facts fix the number:

* the artifact's refresh cadence is **incidental**, not scheduled — measured in
  this lane, 12 commits touch it in the whole history, with gaps of minutes,
  ~9h, ~22h, and a 54h worst case, and the version this defect was filed against
  was 20.4h old. 72h sits above the worst observed gap and still fires on an
  unambiguous failure of the refresh discipline;
* the **primary** detector for this consumer is already exact and clock-free:
  ``queue --check``'s exists/open rule fires the moment a queued issue closes
  (measured here: the six queued issues closed ``2026-09-17T15:27Z``, and the
  queue's last ``--fix`` prune predated that). The age bound is the backstop for
  the case that rule *cannot* see — the board moved (new issues, new dependency
  edges) while no queued issue changed state, which stales the queue silently.

A refusal is **actionable** in the RCA-0014 sense: it names the file, the
timestamp, the age, the tolerance, and the one refresh verb.

## Where this assertion lives

It is deliberately a separate assertion, not a change to ``claims.arbitrate``'s
15-minute liveness default: those are two different questions about the same
file (a *loop* re-checking now vs. a *committed artifact* read offline), and
collapsing them would either red the fleet loop's real guard or green the gate's
real finding. ``--now`` exists so a gate can provoke the refusal
deterministically; the gate's assertion on the real artifact never passes it.

Everything here is stdlib-only and offline: it reads the snapshot's own
``generated_at``. It cannot fetch — ``scripts/verify.sh`` is offline by design —
so it *detects* staleness and names the remedy instead of performing it.

The refusal codes are the ones ``governance/ticket/model.py`` declares for this
same condition on this same artifact (``board-snapshot-stale`` /
``board-snapshot-unaged``), mirrored here rather than re-invented, and
``tests/test_queue_freshness.py`` proves the mirror equal so the two consumers
cannot drift into two vocabularies for one file. They are deliberately NOT
``model.REASON_SNAPSHOT_STALE`` (``snapshot-stale``): that is the *liveness*
refusal a running loop retries in-cycle, not the committed-artifact age refusal.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

#: The age this consumer tolerates on the committed board snapshot. See the
#: module docstring for the measurement behind the number: it is a backstop for
#: the rot the exists/open rule cannot see, not the primary detector.
DEFAULT_MAX_AGE_HOURS = 72

#: Repository-relative path of the artifact this asserts on.
SNAPSHOT_RELPATH = ".board/snapshot.json"

#: The one refresh verb (``governance/dispatch``, #727). Named in every refusal
#: so the remedy travels with the finding — RCA-0014's "the refusal is correct and
#: unactionable" is the failure mode this string exists to prevent.
REFRESH_COMMAND = "python3 governance/dispatch/cli.py snapshot --from-github"

#: Mirrored from ``governance/ticket/model.py`` (same condition, same artifact).
#: ``tests/test_queue_freshness.py`` asserts these equal the ticket package's.
CODE_BOARD_STALE = "board-snapshot-stale"
CODE_BOARD_UNAGED = "board-snapshot-unaged"


class CannotAssess(Exception):
    """The artifact cannot be read at all, so no honest verdict exists (exit 2)."""


@dataclass(frozen=True)
class Finding:
    """A refused freshness assertion, in the repository's code/subject/detail shape."""

    code: str
    subject: str
    detail: str

    def render(self) -> str:
        return f"{self.code}: {self.subject} — {self.detail}"


@dataclass(frozen=True)
class Freshness:
    """The assessed age of the committed board snapshot."""

    ok: bool
    generated_at: str
    age_hours: float
    max_age_hours: float
    findings: tuple[Finding, ...] = ()

    def render(self) -> str:
        stamp = self.generated_at or "no generated_at"
        return (
            f"{SNAPSHOT_RELPATH}: generated_at {stamp}, "
            f"age {self.age_hours:.1f}h (tolerance {self.max_age_hours:g}h)"
        )


def tolerance_minutes(max_age_hours: float | None = None) -> int:
    """The tolerance in MINUTES — the declaration a caller passes to
    ``queue --check --stale-minutes`` so the two halves of the check cannot
    disagree about which age is tolerable.
    """
    hours = DEFAULT_MAX_AGE_HOURS if max_age_hours is None else float(max_age_hours)
    return int(round(hours * 60))


def parse_iso(value: str) -> datetime:
    """Parse a snapshot timestamp; raise ``ValueError`` when it is not one.

    ``Z`` is accepted because that is the form the writer emits, and a naive
    timestamp is read as UTC rather than local time (the fleet is UTC-everywhere).
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


def _unaged(subject: str, detail: str) -> tuple[Finding, ...]:
    return (
        Finding(
            CODE_BOARD_UNAGED,
            subject,
            f"{detail} — a snapshot whose age cannot be established is not trusted "
            f"(refresh it with: {REFRESH_COMMAND})",
        ),
    )


def assess(
    path: Path | str,
    *,
    max_age_hours: float | None = None,
    now: datetime | None = None,
) -> Freshness:
    """Assert the committed board snapshot carries an age inside the tolerance.

    Fails closed at every step: a snapshot that is missing or unreadable raises
    :class:`CannotAssess` (exit 2, never a pass), one whose ``generated_at`` is
    absent or unparseable is a violation naming it, and one older than the
    tolerance is a violation naming the file, the timestamp, the age, the
    tolerance and the refresh verb. Only a *dated* snapshot inside the tolerance
    is OK.
    """
    tolerance = DEFAULT_MAX_AGE_HOURS if max_age_hours is None else float(max_age_hours)
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    moment = moment.astimezone(timezone.utc)

    subject = str(path)
    snapshot_path = Path(path)
    if not snapshot_path.is_file():
        raise CannotAssess(f"board snapshot missing: {subject}")
    try:
        payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CannotAssess(f"board snapshot unreadable: {subject} ({exc})") from exc
    if not isinstance(payload, dict):
        raise CannotAssess(f"board snapshot is not a JSON object: {subject}")

    raw = payload.get("generated_at")
    if not isinstance(raw, str) or not raw.strip():
        return Freshness(
            False, "", float("inf"), tolerance, _unaged(subject, "the snapshot carries no generated_at")
        )
    try:
        generated = parse_iso(raw)
    except ValueError as exc:
        return Freshness(
            False,
            raw,
            float("inf"),
            tolerance,
            _unaged(subject, f"the snapshot's generated_at {raw!r} is not a timestamp ({exc})"),
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
            Finding(
                CODE_BOARD_STALE,
                subject,
                f"generated_at {raw} is {age_hours:.1f}h old, beyond the {tolerance:g}h this "
                f"consumer tolerates — the board has moved since it was written, so every "
                f"exists/open answer the committed queue is checked against is a stale "
                f"frontier (refresh it with: {REFRESH_COMMAND})",
            ),
        ),
    )
