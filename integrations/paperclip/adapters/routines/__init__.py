"""Routines as a projected resource (issue #418, EPIC #410).

Upstream paperclip.ing models *routines* — scheduled recurring work — as a
first-class object: a **trigger**, its **params**, and an **owner**. The fleet
already owns the mechanism: ``fleet/cron.py`` is the code-native scheduler
(GR-15 — there are no GitHub Actions), and its marked crontab lines *are* the
schedule. What the fleet has no object for is the routine an operator can name.

This adapter publishes that object by **projecting the code**, never by copying
it. The schedule — every trigger, argv and log target — is read from
``fleet/cron.py`` by :mod:`.schedule`; the adapter adds only the facts the code
has no field for (who is accountable, which lane, which ticket it is anchored
to), and checks even those against the PMO graph so the routine view cannot
become a second answer about what is scheduled or who owns it.

See ``README.md`` for the refusal and drift cases, and the
``integration/paperclip/adapters/approvals`` package for the sibling seam this
one is modelled on.
"""

from __future__ import annotations

from .model import (
    ROUTINES,
    ROUTINE_PRUNE,
    ROUTINE_RECONCILE,
    ROUTINE_WATCHDOG,
    TRIGGER_DAILY,
    TRIGGER_INTERVAL,
    CannotAssess,
    Finding,
    Projection,
    Routine,
    RoutineRefused,
    RoutineSpec,
    Trigger,
    parse_trigger,
    specs_from_records,
)
from .projection import deterministic, project, render
from .schedule import Schedule, ScheduledEntry, read_schedule

__all__ = [
    "ROUTINES",
    "ROUTINE_PRUNE",
    "ROUTINE_RECONCILE",
    "ROUTINE_WATCHDOG",
    "TRIGGER_DAILY",
    "TRIGGER_INTERVAL",
    "CannotAssess",
    "Finding",
    "Projection",
    "Routine",
    "RoutineRefused",
    "RoutineSpec",
    "Schedule",
    "ScheduledEntry",
    "Trigger",
    "deterministic",
    "parse_trigger",
    "project",
    "read_schedule",
    "render",
    "specs_from_records",
]
