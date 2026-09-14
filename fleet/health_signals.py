#!/usr/bin/env python3
"""The declared machine shape of the fleet-health signal family (issue #498).

WHY this module exists: the fleet computes verdicts about **itself** — which rung
is up, how old its beat is, what a reconciliation sweep did with an orphaned lane,
what the watchdog found missing — and every one of them dies inside the process
(`.fleet/*.json`, a tmux pane, a log line). EPIC #494 gives this repo one exit
(ADR-0022: OTLP/HTTP push, plane-owned endpoint, flag-gated OFF) and one rule for
what may travel: a **closed, bounded** label set, never an id.

This module is the fleet family's half of that rule, and it is deliberately
separate from the publisher (`fleet/health_publish.py`) so the *shape* is
declared in one place and the *rendering* in another. It does no I/O: it is a
vocabulary, a validator, and the refusal that makes the vocabulary real.

## What the family publishes — four signal kinds, one per fact

| Kind | Labels | Value | Source of truth (imported, never re-declared) |
|---|---|---|---|
| `fleet.rung_state` | `rung`, `state` | 1 | `fleet/watchdog.decide` — the one rung classifier |
| `fleet.rung_beat_age_seconds` | `rung`, `bucket` | seconds | `fleet/channel.heartbeat_age_seconds` |
| `fleet.reconcile_outcome` | `outcome` | count | `governance/reconcile` `SweepReport`/`Action` |
| `fleet.watchdog_verdict` | `rung`, `verdict` | 1 | `fleet/channel.capability_finding` |

**Counts and closed-set states, never an identity.** ADR-0022 D5 says it plainly
for this family: *"a session id is this fleet's `pod_id`"*, and the vendor's
`kushin77/monitoring-stack#178` drops that dimension for exactly that reason.
So a lane is never a label: `reconcile_outcome` publishes **how many** lanes ended
in each closed outcome, and the lane's session id, worktree, branch and agent stay
in the ticket and the log, which is where per-session detail belongs.

## The two refusals that are the whole point

1. **A stale beat is never `healthy`.** `rung_state` carries the token
   `watchdog.decide` returned. The publisher may not soften it, and this module
   cannot render a `state` outside the closed set at all.
2. **An orphan with unmerged work is never `reclaimed`.** `governance/reconcile`
   keeps the worktree, the branch and the claim of a lane whose work exists
   nowhere else (`AGENTS.md` rule 17). The outcome token travels verbatim through
   this shape: `shelved` in, `shelved` out.

## Honest emptiness

`NO_DATA` (`no-data`) is a first-class member of every closed state set — the
repo's tri-state 0/1/2 precedent: *a surface that cannot be established is never a
pass*. When the fleet's own store cannot be read, the publisher emits
`no-data`; it never emits a `healthy` it did not measure.

Usage::

    from health_signals import Signal, SIGNAL_RUNG_STATE, LABEL_RUNG, LABEL_STATE
    Signal(kind=SIGNAL_RUNG_STATE, labels={LABEL_RUNG: "brain", LABEL_STATE: NO_DATA})
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

ROOT = Path(__file__).resolve().parent.parent
for _entry in (str(ROOT / "fleet"), str(ROOT)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import channel  # noqa: E402
import watchdog  # noqa: E402
from governance.reconcile.heartbeat import (  # noqa: E402
    LIVE,
    ORPHAN,
    SUSPECT,
)
from governance.reconcile.sweep import (  # noqa: E402
    FAILED_OUTCOME,
    PARKED,
    RECLAIMED,
    REPORTED,
    SHELVED_OUTCOME,
)

# ── identity ────────────────────────────────────────────────────────────────
#: The constant `service` label. ADR-0022 D5 bounds it to exactly one value.
SERVICE = "agent-orchestrator"

#: Rung names, derived from the watchdog's own supervision table plus the monitor
#: rung it keeps — never a second, parallel list of rungs.
RUNG_NAMES = tuple(sorted({name for name, _pattern, _script, _beat in watchdog.RUNGS} | {watchdog.MONITOR_NAME}))

# ── the closed vocabularies ─────────────────────────────────────────────────
#: The honest-emptiness token: a state that could not be established. It is a
#: member of every closed set below, so "we could not measure it" is always
#: publishable and never has to be approximated by a healthy-looking token.
NO_DATA = "no-data"

#: Signal kinds. One fact per kind, so a consumer never has to read two.
SIGNAL_RUNG_STATE = "rung_state"
SIGNAL_RUNG_BEAT_AGE = "rung_beat_age_seconds"
SIGNAL_RECONCILE_OUTCOME = "reconcile_outcome"
SIGNAL_WATCHDOG_VERDICT = "watchdog_verdict"
SIGNAL_KINDS = (
    SIGNAL_RUNG_STATE,
    SIGNAL_RUNG_BEAT_AGE,
    SIGNAL_RECONCILE_OUTCOME,
    SIGNAL_WATCHDOG_VERDICT,
)

#: Rung states — the watchdog's own decided vocabulary (imported), plus the
#: honest no-data member. `forced` is deliberately absent: `decide` never returns
#: it (it is an operator action, not a measured state), so it cannot be published.
RUNG_STATES = frozenset(watchdog.RUNG_STATES) | {NO_DATA}

#: Beat-age buckets. A readable beat is `fresh` or `stale`; an unreadable one is
#: *omitted* rather than bucketed, because a bucket for "no reading" would carry
#: no information the rung's own `no-data` state does not already carry.
BUCKET_FRESH = "fresh"
BUCKET_STALE = "stale"
BEAT_AGE_BUCKETS = (BUCKET_FRESH, BUCKET_STALE)

#: Reconciliation vocabularies — `Action.status` (the judge's verdict: exactly
#: the three `heartbeat.judge` can return) and `Action.outcome` (what the sweep
#: did with the work), both imported from `governance/reconcile`, plus the honest
#: no-data member. The two sets are disjoint, which is why one closed `outcome`
#: label can carry both facets without ambiguity.
RECONCILE_STATUSES = frozenset({LIVE, SUSPECT, ORPHAN})
RECONCILE_OUTCOMES_DECIDED = frozenset({RECLAIMED, PARKED, SHELVED_OUTCOME, REPORTED, FAILED_OUTCOME})
RECONCILE_OUTCOMES = RECONCILE_STATUSES | RECONCILE_OUTCOMES_DECIDED | {NO_DATA}

#: Watchdog capability verdicts — `channel.capability_finding`'s closed kinds,
#: imported verbatim (#319), plus the honest no-data member.
WATCHDOG_VERDICTS = frozenset(
    {
        channel.KIND_CURRENT,
        channel.KIND_DOWN,
        channel.KIND_DRIFTED,
        channel.KIND_CAPABILITY_STALE,
        channel.KIND_UNKNOWN,
    }
) | {NO_DATA}

# ── the closed label set ────────────────────────────────────────────────────
LABEL_SERVICE = "service"
LABEL_SIGNAL = "signal"
LABEL_RUNG = "rung"
LABEL_STATE = "state"
LABEL_BUCKET = "bucket"
LABEL_OUTCOME = "outcome"
LABEL_VERDICT = "verdict"

#: The ONLY label keys this family may emit. Seven, all closed-set or constant.
LABEL_KEYS = frozenset(
    {
        LABEL_SERVICE,
        LABEL_SIGNAL,
        LABEL_RUNG,
        LABEL_STATE,
        LABEL_BUCKET,
        LABEL_OUTCOME,
        LABEL_VERDICT,
    }
)

#: The closed value set per label key. Every emitted value is validated against
#: its key's set, so a free-form value cannot reach the plane at all.
LABEL_VALUES: dict[str, frozenset[str]] = {
    LABEL_SERVICE: frozenset({SERVICE}),
    LABEL_SIGNAL: frozenset(SIGNAL_KINDS),
    LABEL_RUNG: frozenset(RUNG_NAMES),
    LABEL_STATE: RUNG_STATES,
    LABEL_BUCKET: frozenset(BEAT_AGE_BUCKETS),
    LABEL_OUTCOME: RECONCILE_OUTCOMES,
    LABEL_VERDICT: WATCHDOG_VERDICTS,
}

#: Every signal's exact label set: `service`, `signal`, then these. A signal
#: carrying an extra label is refused, so the shape cannot grow silently.
SIGNAL_LABELS: dict[str, tuple[str, ...]] = {
    SIGNAL_RUNG_STATE: (LABEL_RUNG, LABEL_STATE),
    SIGNAL_RUNG_BEAT_AGE: (LABEL_RUNG, LABEL_BUCKET),
    SIGNAL_RECONCILE_OUTCOME: (LABEL_OUTCOME,),
    SIGNAL_WATCHDOG_VERDICT: (LABEL_RUNG, LABEL_VERDICT),
}

#: Refused as identity, by name (ADR-0022 D5 / vendor #178). Listing them makes
#: the refusal *named* rather than merely "not in the allow-list": a caller that
#: reaches for a session id is told which rule it broke.
REFUSED_LABEL_KEYS = frozenset(
    {
        "session",
        "session_id",
        "issue",
        "agent",
        "lane",
        "worktree",
        "branch",
        "pid",
        "host",
        "hostname",
        "instance",
        "node",
        "pod",
        "pod_id",
        "commit",
        "sha",
        "request_id",
        "trace_id",
        "span_id",
        "run_id",
        "model",
        "prompt",
        "error",
        "path",
        "at",
        "timestamp",
        "ts",
    }
)

#: The OTLP metric name prefix and the declared unit per kind (UCUM: `s` for
#: seconds, `1` for a dimensionless gauge, `{lane}` for a count of lanes).
METRIC_PREFIX = "fleet"
UNITS = {
    SIGNAL_RUNG_STATE: "1",
    SIGNAL_RUNG_BEAT_AGE: "s",
    SIGNAL_RECONCILE_OUTCOME: "{lane}",
    SIGNAL_WATCHDOG_VERDICT: "1",
}


class ShapeRefused(ValueError):
    """A signal does not fit the declared machine shape — refused, not coerced."""


def metric_name(kind: str) -> str:
    """The metric name a signal kind renders as (`rung_state` -> `fleet.rung_state`)."""
    if kind not in SIGNAL_KINDS:
        raise ShapeRefused(f"unknown signal kind {kind!r}; declared: {', '.join(SIGNAL_KINDS)}")
    return f"{METRIC_PREFIX}.{kind}"


@dataclass(frozen=True)
class Signal:
    """One published fact: a declared kind, its closed labels, and a value.

    Validation happens at construction, so an ill-shaped signal cannot be built
    and then carried around hoping someone checks it before the push.
    """

    kind: str
    labels: Mapping[str, str]
    value: float
    unit: str = ""

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if self.kind not in SIGNAL_KINDS:
            raise ShapeRefused(
                f"unknown signal kind {self.kind!r}; declared: {', '.join(SIGNAL_KINDS)}"
            )
        labels = dict(self.labels)
        refused = sorted(key for key in labels if key in REFUSED_LABEL_KEYS)
        if refused:
            raise ShapeRefused(
                f"{self.kind}: label(s) {', '.join(refused)} are refused as identity "
                "(ADR-0022 D5 / kushin77/monitoring-stack#178: never an id, path, commit or host) "
                "— per-session detail belongs to the ticket, not to a metric label"
            )
        unexpected = sorted(key for key in labels if key not in LABEL_KEYS)
        if unexpected:
            raise ShapeRefused(
                f"{self.kind}: label key(s) {', '.join(unexpected)} are outside the closed "
                f"label set ({', '.join(sorted(LABEL_KEYS))})"
            )
        expected = {LABEL_SERVICE, LABEL_SIGNAL} | set(SIGNAL_LABELS[self.kind])
        missing = sorted(expected - set(labels))
        if missing:
            raise ShapeRefused(f"{self.kind}: label(s) {', '.join(missing)} are required")
        extra = sorted(set(labels) - expected)
        if extra:
            raise ShapeRefused(
                f"{self.kind}: label(s) {', '.join(extra)} do not belong to this signal "
                f"(declared labels: {', '.join(sorted(expected))})"
            )
        for key, value in labels.items():
            if not isinstance(value, str) or not value:
                raise ShapeRefused(f"{self.kind}: label {key!r} must be a non-empty string")
            allowed = LABEL_VALUES[key]
            if value not in allowed:
                raise ShapeRefused(
                    f"{self.kind}: {key}={value!r} is outside the closed set for {key} "
                    f"({', '.join(sorted(allowed))})"
                )
        if self.value is None or not isinstance(self.value, (int, float)) or isinstance(self.value, bool):
            raise ShapeRefused(f"{self.kind}: value must be a number, got {self.value!r}")
        if not math.isfinite(float(self.value)):
            raise ShapeRefused(f"{self.kind}: value must be finite, got {self.value!r}")
        if float(self.value) < 0:
            raise ShapeRefused(f"{self.kind}: value must not be negative, got {self.value!r}")
        if self.unit and self.unit != UNITS[self.kind]:
            raise ShapeRefused(
                f"{self.kind}: unit {self.unit!r} is not the declared unit {UNITS[self.kind]!r}"
            )

    def as_attributes(self) -> list[dict]:
        """The labels as OTLP attribute key/value pairs, in a stable order."""
        return [
            {"key": key, "value": {"stringValue": self.labels[key]}}
            for key in sorted(self.labels)
        ]

    def __str__(self) -> str:
        rendered = " ".join(f"{key}={self.labels[key]}" for key in sorted(self.labels))
        return f"{metric_name(self.kind)} {rendered} = {self.value:g}"


def make(kind: str, value: float, **labels: str) -> Signal:
    """Build a signal, stamping the constant `service` and `signal` labels.

    A convenience for the publisher, not a second entry point: it constructs the
    same validated `Signal`, so there is exactly one place the shape is enforced.
    """
    return Signal(
        kind=kind,
        labels={LABEL_SERVICE: SERVICE, LABEL_SIGNAL: kind, **labels},
        value=value,
        unit=UNITS[kind],
    )
