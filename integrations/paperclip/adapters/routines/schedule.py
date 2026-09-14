"""Read the schedule from ``fleet/cron.py`` — the code **is** the schedule (issue #418).

There is no second copy of the fleet's schedule anywhere in this adapter. The
authority is ``fleet/cron.py``: it declares the marked crontab lines and
``install_lines`` returns them. This module **executes that declaration** — it
imports the module and calls ``install_lines([], interval)`` — and parses each
line it gets back into a trigger plus its params (the argv, the log target and
the working directory). Nothing here duplicates a cadence or a command.

Two consequences the acceptance criteria demand, and where they are enforced:

* a scheduled entry that cannot be read (no marker, no cron fields, no argv) or
  whose schedule cannot be expressed as a :class:`~.model.Trigger` is a
  **refusal**, collected per entry so the projection can report it by name;
* the import is **scoped** exactly like ``governance/pmo/graph.py`` scopes the
  ticket projection: ``fleet/`` is put on ``sys.path`` only for the import (so
  ``import runtime`` resolves) and every name it can shadow is restored
  afterwards, so reading one tree's schedule can never leak into another's.

The reader is offline and deterministic: the only clock or path in the output is
normalised to ``<ROOT>`` / ``<FLEET_DIR>``, so the same revision renders the same
bytes from any checkout.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .model import CannotAssess, Finding, RoutineRefused, Trigger, parse_trigger

#: The module that owns the schedule, relative to a tree's root.
CRON_RELPATH = "fleet/cron.py"

#: The private module name the reader imports ``fleet/cron.py`` under, so a real
#: third-party ``cron`` module (if one is ever installed) can never be shadowed.
CRON_MODULE_NAME = "_ao418_fleet_cron"

#: The names ``fleet/cron.py`` imports flat and that a fixture tree may also
#: own; they are restored after the scoped import.
_SHADOWABLE = ("runtime",)

#: The normalised tokens substituted for the two absolute paths a cron line
#: carries, so the projection is a function of the revision, not the checkout.
ROOT_TOKEN = "<ROOT>"
FLEET_DIR_TOKEN = "<FLEET_DIR>"


@dataclass(frozen=True)
class ScheduledEntry:
    """One marked crontab line, as the code declares it."""

    marker: str
    raw: str
    trigger: Trigger
    argv: tuple[str, ...]
    log: str
    cwd: str

    @property
    def params(self) -> dict[str, Any]:
        """The routine's params — read from the code, never restated here."""
        return {"argv": list(self.argv), "log": self.log, "cwd": self.cwd}


@dataclass
class Schedule:
    """What the code declares: the entries plus any entry that could not be read."""

    entries: list[ScheduledEntry] = field(default_factory=list)
    refusals: list[Finding] = field(default_factory=list)
    source: str = CRON_RELPATH
    interval_minutes: Optional[int] = None


def _load_cron_module(root: Path):
    """Import ``<root>/fleet/cron.py`` without leaking its module names."""
    fleet_dir = Path(root) / "fleet"
    path = fleet_dir / "cron.py"
    if not path.is_file():
        raise CannotAssess(f"schedule source missing: {CRON_RELPATH} under {root}")
    saved_path = list(sys.path)
    saved_modules = {name: sys.modules.get(name) for name in _SHADOWABLE}
    try:
        sys.path.insert(0, str(fleet_dir))
        for name in _SHADOWABLE:
            sys.modules.pop(name, None)
        spec = importlib.util.spec_from_file_location(CRON_MODULE_NAME, path)
        if spec is None or spec.loader is None:
            raise CannotAssess(f"schedule source unloadable: {CRON_RELPATH} under {root}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except CannotAssess:
        raise
    except Exception as exc:  # a source that will not import is CANNOT-ASSESS
        raise CannotAssess(f"schedule source unimportable: {CRON_RELPATH} ({exc})") from exc
    finally:
        sys.path[:] = saved_path
        for name in _SHADOWABLE:
            sys.modules.pop(name, None)
        for name, module in saved_modules.items():
            if module is not None:
                sys.modules[name] = module


def _default_interval(module) -> int:
    """The install cadence the code itself defaults to.

    ``fleet/cron.py`` declares it once, as the ``--interval`` default on the
    ``install`` subparser. Re-declaring it here would let the seam drift, so it
    is read from the parser.
    """
    try:
        args = module.build_parser().parse_args(["install"])
    except SystemExit as exc:  # a parser that will not accept its own default
        raise CannotAssess(f"could not read the default interval from {CRON_RELPATH}: {exc}") from exc
    interval = getattr(args, "interval", None)
    if not isinstance(interval, int) or interval < 1:
        raise CannotAssess(f"{CRON_RELPATH} declares no usable install interval")
    return interval


def _normalise(text: str, root: Path, fleet_dir: Optional[Path]) -> str:
    """Replace the two absolute paths a cron line carries with stable tokens.

    The fleet directory is normalised first because it nests inside the root, so
    a log target under ``<root>/.fleet`` becomes ``<FLEET_DIR>/…`` rather than
    ``<ROOT>/.fleet/…`` — either is deterministic, but this keeps the log target
    independent of ``AO_FLEET_DIR``.
    """
    out = text
    if fleet_dir is not None:
        out = out.replace(str(fleet_dir), FLEET_DIR_TOKEN)
    return out.replace(str(root), ROOT_TOKEN)


def _parse_entry(line: str, root: Path, fleet_dir: Optional[Path], source: str) -> ScheduledEntry:
    """Parse one marked crontab line, or refuse it by name.

    The line shape is the one ``fleet/cron.py`` builds:
    ``<5 cron fields> cd <root> && <argv> >> <log> 2>&1 # <marker>``.
    """
    head, hashed, marker_text = line.rpartition("#")
    marker = marker_text.strip()
    if not hashed or not marker:
        raise RoutineRefused(
            "unmarked-entry",
            f"crontab line carries no trailing marker: {line!r}",
            subject=source,
        )
    head = head.strip()
    fields = head.split(None, 5)
    if len(fields) < 6:
        raise RoutineRefused(
            "unparseable-entry",
            f"line for marker {marker!r} has no command after its cron fields: {line!r}",
            subject=marker,
        )
    expression = " ".join(fields[:5])
    trigger = parse_trigger(expression, marker)

    remainder = fields[5]
    if not remainder.startswith("cd "):
        raise RoutineRefused(
            "unparseable-entry",
            f"line for marker {marker!r} does not start its command with a directory: {line!r}",
            subject=marker,
        )
    _, _, after_cd = remainder.partition(" && ")
    if not after_cd:
        raise RoutineRefused(
            "unparseable-entry",
            f"line for marker {marker!r} names no command after its directory: {line!r}",
            subject=marker,
        )
    command, separator, redirect = after_cd.partition(" >> ")
    if not separator:
        raise RoutineRefused(
            "unparseable-entry",
            f"line for marker {marker!r} has no log target: {line!r}",
            subject=marker,
        )
    argv = tuple(command.split())
    if not argv:
        raise RoutineRefused(
            "unparseable-entry",
            f"line for marker {marker!r} names an empty command: {line!r}",
            subject=marker,
        )
    log = redirect.split()[0] if redirect.split() else ""
    if not log:
        raise RoutineRefused(
            "unparseable-entry",
            f"line for marker {marker!r} has an empty log target: {line!r}",
            subject=marker,
        )
    return ScheduledEntry(
        marker=marker,
        raw=_normalise(line.strip(), root, fleet_dir),
        trigger=trigger,
        argv=tuple(_normalise(token, root, fleet_dir) for token in argv),
        log=_normalise(log, root, fleet_dir),
        cwd=ROOT_TOKEN,
    )


def read_schedule(root: Path | str) -> Schedule:
    """Read the fleet's schedule out of ``<root>/fleet/cron.py``.

    Raises :class:`~.model.CannotAssess` when the source itself cannot be read;
    an individual entry that cannot be parsed or whose schedule cannot be
    expressed is collected in ``Schedule.refusals`` instead, so the projection
    can report it **by name** (a finding) rather than losing it.
    """
    root = Path(root)
    module = _load_cron_module(root)
    interval = _default_interval(module)
    try:
        lines = list(module.install_lines([], interval))
    except Exception as exc:
        raise CannotAssess(f"{CRON_RELPATH} offered no schedule: {exc}") from exc
    is_ours = getattr(module, "_is_ours", None)
    if not callable(is_ours):
        raise CannotAssess(f"{CRON_RELPATH} declares no `_is_ours` schedule predicate")

    fleet_dir = None
    log = getattr(module, "LOG", None)
    if isinstance(log, Path):
        fleet_dir = log.parent

    schedule = Schedule(interval_minutes=interval)
    for line in lines:
        if not is_ours(str(line)):
            continue
        try:
            schedule.entries.append(_parse_entry(str(line), root, fleet_dir, CRON_RELPATH))
        except RoutineRefused as exc:
            schedule.refusals.append(Finding(exc.reason, exc.subject or CRON_RELPATH, exc.detail))
    return schedule
