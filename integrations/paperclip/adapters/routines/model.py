"""The routine model — trigger + owner + params, and the routine registry (issue #418).

Upstream models *routines* (scheduled recurring work) as a first-class object:
a **trigger**, its **params**, and an **owner**. The fleet already has the
mechanism — ``fleet/cron.py``, the code-native scheduler (GR-15: there are no
GitHub Actions) — but nothing an operator can see or name.

This module holds the two things a projection needs that the code does **not**
carry, and deliberately nothing else:

* :class:`Trigger`, the normalised shape of a cron schedule, and
  :func:`parse_trigger`, which refuses a schedule it cannot express;
* :data:`ROUTINES`, the **routine registry**: each routine's identity, its
  accountable ``owner``, its ``lane`` and the ticket it is ``anchor``ed to.

The registry never carries a schedule or a command. **The code is the schedule**
— ``fleet/cron.py`` is the only place a trigger, an argv or a log target is
declared, and :mod:`schedule` reads them from there. The registry exists only to
say *who is accountable for* the schedule the code already declares, which is
exactly the fact the code has no field for.

A routine registry entry that names a schedule the code does not carry is a
refusal (``routine-orphan-schedule``); a scheduled entry the registry does not
carry is a reported drift (``schedule-unprojected``). Neither is silent.

---knowledge---
module_id: integrations.paperclip.adapters.routines.model
system: integrations
app: paperclip
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: paperclip
tier: L1
interfaces: [RoutineRefused, CannotAssess, Trigger, parse_trigger, RoutineSpec, specs_from_records, Finding, Routine, (+1 more)]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

#: The trigger kinds the fleet's cron vocabulary admits. A schedule outside this
#: vocabulary cannot be expressed as a trigger and is refused, not guessed.
TRIGGER_INTERVAL = "interval"
TRIGGER_DAILY = "daily"

#: The registry id of the watchdog routine (the modulo-interval rung respawner).
ROUTINE_WATCHDOG = "fleet-watchdog"
#: The registry id of the retention (mailbox-aging / ledger-rotation) routine.
ROUTINE_PRUNE = "fleet-prune"
#: The registry id of the orphan-reconciliation sweep.
ROUTINE_RECONCILE = "fleet-reconcile"
#: The registry id of the daily worktree-reap rung.
ROUTINE_REAP = "fleet-reap"


class RoutineRefused(Exception):
    """The adapter refused to project a routine. ``reason`` is machine-readable."""

    def __init__(self, reason: str, detail: str = "", subject: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail
        self.subject = subject


class CannotAssess(Exception):
    """An input the projection must read is missing or unreadable.

    Distinct from :class:`RoutineRefused`: a refusal is a *finding* (NOT-OK, exit
    1), while CANNOT-ASSESS (exit 2) means there is no honest projection at all.
    CANNOT-ASSESS is never reported as a pass.
    """


@dataclass(frozen=True)
class Trigger:
    """A cron schedule, normalised — or the refusal that stopped it.

    Two shapes are expressible in the fleet's vocabulary:

    * ``interval`` — ``*/N * * * *``, a tick every ``N`` minutes;
    * ``daily`` — ``M H * * *``, once a day at ``H:M``.

    Anything else (``@reboot``, a day-of-month/month/day-of-week restriction, a
    malformed field) has no trigger shape here and :func:`parse_trigger` refuses
    it by name rather than coercing it into one of the two.
    """

    kind: str
    raw: str
    every_minutes: Optional[int] = None
    at_hour: Optional[int] = None
    at_minute: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        document: dict[str, Any] = {"kind": self.kind, "raw": self.raw}
        if self.every_minutes is not None:
            document["every_minutes"] = self.every_minutes
        if self.at_hour is not None:
            document["at_hour"] = self.at_hour
        if self.at_minute is not None:
            document["at_minute"] = self.at_minute
        return document


def parse_trigger(expression: str, subject: str) -> Trigger:
    """Normalise a 5-field cron expression, or refuse it by name.

    ``subject`` names the offender (the routine's marker) so a refusal points at
    the schedule that caused it.
    """
    text = str(expression or "").strip()
    fields = text.split()
    if len(fields) != 5:
        raise RoutineRefused(
            "inexpressible-trigger",
            f"cron expression {text!r} has {len(fields)} field(s), expected 5 "
            "(minute hour day-of-month month day-of-week)",
            subject=subject,
        )
    minute, hour, dom, month, dow = fields
    if (dom, month, dow) != ("*", "*", "*"):
        raise RoutineRefused(
            "inexpressible-trigger",
            f"cron expression {text!r} restricts day-of-month/month/day-of-week, "
            "which the fleet's trigger vocabulary cannot express",
            subject=subject,
        )
    if minute.startswith("*/") and minute[2:].isdigit():
        step = int(minute[2:])
        if step < 1:
            raise RoutineRefused(
                "inexpressible-trigger",
                f"cron expression {text!r} ticks every {step} minute(s)",
                subject=subject,
            )
        if hour != "*":
            raise RoutineRefused(
                "inexpressible-trigger",
                f"cron expression {text!r} fixes an hour on an interval minute",
                subject=subject,
            )
        return Trigger(kind=TRIGGER_INTERVAL, raw=text, every_minutes=step)
    if minute.isdigit() and hour.isdigit():
        hour_value, minute_value = int(hour), int(minute)
        if hour_value > 23 or minute_value > 59:
            raise RoutineRefused(
                "inexpressible-trigger",
                f"cron expression {text!r} is outside a clock (hour 0-23, minute 0-59)",
                subject=subject,
            )
        return Trigger(kind=TRIGGER_DAILY, raw=text, at_hour=hour_value, at_minute=minute_value)
    raise RoutineRefused(
        "inexpressible-trigger",
        f"cron expression {text!r} is neither an interval (``*/N * * * *``) nor "
        "a daily time (``M H * * *``)",
        subject=subject,
    )


@dataclass(frozen=True)
class RoutineSpec:
    """Who is accountable for a schedule the *code* declares.

    The spec names the schedule by its **marker** — the stable tail comment
    ``fleet/cron.py`` itself attaches to each line (``# ao-fleet-watchdog``) —
    and says who owns it (``owner``), which lane it belongs to (``lane``) and the
    ticket it is anchored to (``anchor``). It carries no trigger and no argv.
    """

    id: str
    marker: str
    owner: str
    lane: str
    anchor: str

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "marker": self.marker,
            "owner": self.owner,
            "lane": self.lane,
            "anchor": self.anchor,
        }


#: The routine registry — the accountable owner of each schedule the code
#: declares. ``anchor`` is the issue the schedule's own source cites as its
#: reason to exist (``fleet/cron.py`` names #280 for the pruner, #304 for the
#: sweep and #830 for the reaper; #237 is the cron-owned watchdog), so the routine
#: view and the PMO view describe the same ticket rather than two answers.
#:
#: Every ENABLED rung the schedule carries must appear here. The registry was
#: written for the three rungs issue #418 shipped and was never extended when
#: #830 added the fourth (the daily worktree reaper) to the manifest, so the
#: projection reported ``schedule-unprojected: ao-fleet-reap`` — a real drift, and
#: the reason `scripts/check-paperclip-routines.sh` had no verdict to give
#: (issue #1176). The rung is live: `config/fleet-jobs.json` enables it,
#: `infra/fleet/inventory.yaml` declares it, `infra/fleet/dev_run.py` carries a
#: dry-run form for it, its cron line shells out to `scripts/prune-worktrees.sh`
#: and its log is gitignored.
ROUTINES: tuple[RoutineSpec, ...] = (
    RoutineSpec(
        id=ROUTINE_WATCHDOG,
        marker="ao-fleet-watchdog",
        owner="fleet/watchdog",
        lane="fleet-ops",
        anchor="kushin77/agent-orchestrator#237",
    ),
    RoutineSpec(
        id=ROUTINE_PRUNE,
        marker="ao-fleet-prune",
        owner="fleet/prune",
        lane="fleet-ops",
        anchor="kushin77/agent-orchestrator#280",
    ),
    RoutineSpec(
        id=ROUTINE_RECONCILE,
        marker="ao-fleet-reconcile",
        owner="governance/reconcile",
        lane="fleet-ops",
        anchor="kushin77/agent-orchestrator#304",
    ),
    # The reaper's owner is the tool that does the work — `scripts/prune-worktrees.sh`
    # — exactly as `fleet/prune` names the job `fleet/prune.py` runs and
    # `governance/reconcile` names the sweep's package. The rung declares no work
    # of its own: `fleet/cron.py`'s reap comment says it "shells out to" that tool
    # and "adds no second copy of its reclaim policy, only the schedule", so the
    # owner is the tool, not a fleet module that does not exist.
    RoutineSpec(
        id=ROUTINE_REAP,
        marker="ao-fleet-reap",
        owner="scripts/prune-worktrees",
        lane="fleet-ops",
        anchor="kushin77/agent-orchestrator#830",
    ),
)


def specs_from_records(records: Iterable[dict[str, Any]]) -> tuple[RoutineSpec, ...]:
    """Build a registry from plain records (the ``--registry FILE`` seam).

    The gate and the tests hand in an alternate registry this way — never a
    schedule. A record missing a field is refused by name, so a malformed
    registry surfaces as a finding instead of a traceback.
    """
    specs: list[RoutineSpec] = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise RoutineRefused("bad-registry", f"record {index} is not an object")
        missing = [
            key
            for key in ("id", "marker", "owner", "lane", "anchor")
            if not isinstance(record.get(key), str)
        ]
        if missing:
            raise RoutineRefused(
                "bad-registry",
                f"record {index} is missing string field(s): {', '.join(missing)}",
            )
        specs.append(
            RoutineSpec(
                id=record["id"],
                marker=record["marker"],
                owner=record["owner"],
                lane=record["lane"],
                anchor=record["anchor"],
            )
        )
    return tuple(specs)


@dataclass(frozen=True)
class Finding:
    """A reason the projection is NOT-OK. ``subject`` is the offending routine."""

    code: str
    subject: str
    detail: str

    def line(self) -> str:
        return f"{self.code}: {self.subject} — {self.detail}"


@dataclass(frozen=True)
class Routine:
    """One projected routine: the schedule the code declares, plus its owner."""

    id: str
    marker: str
    owner: str
    lane: str
    anchor: str
    trigger: Trigger
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "marker": self.marker,
            "owner": self.owner,
            "lane": self.lane,
            "anchor": self.anchor,
            "trigger": self.trigger.to_dict(),
            "params": dict(self.params),
        }


@dataclass
class Projection:
    """The routine view: a deterministic document plus its findings and notes."""

    routines: list[Routine] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.findings
