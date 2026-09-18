"""Project ``fleet/cron.py``'s schedules as routine objects (issue #418).

The projection joins the two authorities, and originates neither:

* **the schedule** — read from the code by :mod:`.schedule` (the code IS the
  schedule; no trigger, argv or log target is restated in this adapter);
* **the accountable owner** — the routine registry in :mod:`.model`, checked
  against the PMO graph by :mod:`.pmo` so the routine view cannot become a
  second answer about who owns the work.

Every acceptance criterion is a check with a real failure path:

=========================================  ============================================
criterion                                  how it fails
=========================================  ============================================
one routine per scheduled entry            ``duplicate-marker``; a schedule the registry
                                           does not carry is ``schedule-unprojected``
a schedule only the routine knows about    ``routine-orphan-schedule`` (refused)
deterministic projection                   ``verify`` derives it twice and compares bytes
a routine with no owner                    ``routine-unowned`` (fails closed)
a schedule with no trigger shape           ``inexpressible-trigger`` (refused by name)
the routine view agrees with the PMO view  ``pmo-owner-disagreement`` /
                                           ``pmo-lane-disagreement`` (by ticket)
no new store                               the projection writes only its own stdout
=========================================  ============================================

A finding is NOT-OK (exit 1); CANNOT-ASSESS (exit 2) is reserved for an input
that cannot be read at all. Neither is ever reported as a pass.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Optional

from . import pmo as pmo_mod
from .model import Finding, Projection, Routine, RoutineSpec, ROUTINES
from .schedule import Schedule, read_schedule


def _ordered(findings: Iterable[Finding]) -> list[Finding]:
    """Findings in a stable order, so two derivations render the same bytes."""
    return sorted(findings, key=lambda f: (f.code, f.subject, f.detail))


def _normalise_note(text: str, *roots: Path) -> str:
    """Strip absolute checkout paths out of a diagnostic note.

    A note is part of the rendered document, so it must not embed a path that
    depends on *where* the tree happens to live — the projection is a function of
    the revision, not of the checkout.
    """
    out = str(text)
    for candidate in roots:
        out = out.replace(str(candidate), "<ROOT>")
    return out


def _specs_by_marker(specs: Iterable[RoutineSpec]) -> tuple[dict[str, RoutineSpec], list[Finding]]:
    by_marker: dict[str, RoutineSpec] = {}
    findings: list[Finding] = []
    for spec in specs:
        if spec.marker in by_marker:
            findings.append(
                Finding(
                    "duplicate-marker",
                    spec.marker,
                    f"registry declares marker {spec.marker!r} more than once "
                    f"({by_marker[spec.marker].id!r} and {spec.id!r})",
                )
            )
            continue
        by_marker[spec.marker] = spec
    return by_marker, findings


def project(
    root: Path | str,
    pmo_root: Path | str | None = None,
    specs: Optional[Iterable[RoutineSpec]] = None,
) -> Projection:
    """Derive the routine view for ``root``, cross-checked against the PMO graph.

    ``pmo_root`` is the tree the PMO graph is read from (default ``root``) — it
    is separate so a scratch tree carrying a mutated ``fleet/cron.py`` can still
    be checked against the real committed graph.
    """
    root = Path(root)
    pmo_root = Path(pmo_root) if pmo_root is not None else root
    registry = tuple(ROUTINES if specs is None else specs)

    schedule: Schedule = read_schedule(root)
    by_marker, findings = _specs_by_marker(registry)
    findings = list(findings)

    # (0) an entry the code declares but the reader could not express at all.
    findings.extend(schedule.refusals)

    # (1) the code is the schedule: a marker scheduled more than once is not one
    #     routine, and a scheduled marker the registry does not carry is the
    #     drift the acceptance criteria require to be REPORTED, never silent.
    grouped: dict[str, list] = {}
    for entry in schedule.entries:
        grouped.setdefault(entry.marker, []).append(entry)
    for marker in sorted(grouped):
        if len(grouped[marker]) != 1:
            findings.append(
                Finding(
                    "duplicate-marker",
                    marker,
                    f"the code schedules marker {marker!r} {len(grouped[marker])} time(s); "
                    "a routine has exactly one schedule",
                )
            )
        if marker not in by_marker:
            findings.append(
                Finding(
                    "schedule-unprojected",
                    marker,
                    "the code schedules this marker but no routine claims it — the "
                    "schedule drifted without a routine change",
                )
            )

    # (2) a routine whose schedule exists only on the routine side is refused:
    #     the code is the schedule, never a duplicate of it.
    for marker in sorted(by_marker):
        if marker not in grouped:
            findings.append(
                Finding(
                    "routine-orphan-schedule",
                    marker,
                    "the routine claims a schedule the code does not declare — a "
                    "schedule that exists only on the routine side is refused",
                )
            )

    # (3) a routine with no owner fails closed.
    for marker in sorted(by_marker):
        spec = by_marker[marker]
        if not spec.owner.strip():
            findings.append(
                Finding(
                    "routine-unowned",
                    spec.id,
                    f"routine {spec.id!r} (marker {spec.marker!r}) names no owner",
                )
            )

    # (4) the routine view must agree with the PMO view where they overlap.
    try:
        pmo = pmo_mod.load(pmo_root)
    except pmo_mod.PmoUnavailable as exc:
        pmo = pmo_mod.PmoView(available=False, detail=str(exc))

    notes: list[str] = []
    if not pmo.available:
        notes.append(f"pmo-unavailable: {_normalise_note(pmo.detail, root, pmo_root)}")

    routines: list[Routine] = []
    for marker in sorted(grouped):
        group = grouped[marker]
        spec = by_marker.get(marker)
        if len(group) != 1 or spec is None:
            continue
        entry = group[0]
        routines.append(
            Routine(
                id=spec.id,
                marker=spec.marker,
                owner=spec.owner,
                lane=spec.lane,
                anchor=spec.anchor,
                trigger=entry.trigger,
                params=entry.params,
            )
        )
        if pmo.available:
            if not pmo.carries(spec.anchor):
                notes.append(
                    f"anchor-unauditable: {spec.anchor} — the PMO graph does not carry "
                    f"the ticket routine {spec.id!r} is anchored to"
                )
                continue
            graph_owner = pmo.owner(spec.anchor)
            if graph_owner and graph_owner != spec.owner:
                findings.append(
                    Finding(
                        "pmo-owner-disagreement",
                        spec.anchor,
                        f"the routine names owner {spec.owner!r} where the PMO graph "
                        f"names {graph_owner!r}",
                    )
                )
            graph_lane = pmo.lane(spec.anchor)
            if graph_lane and graph_lane != spec.lane:
                findings.append(
                    Finding(
                        "pmo-lane-disagreement",
                        spec.anchor,
                        f"the routine names lane {spec.lane!r} where the PMO graph "
                        f"derives {graph_lane!r}",
                    )
                )

    if pmo.available:
        notes.append(f"pmo: agreed against {len(pmo.tickets)} ticket(s) in the ticket graph")

    return Projection(routines=routines, findings=_ordered(findings), notes=sorted(notes))


def render(projection: Projection) -> str:
    """The routine view as canonical JSON — deterministic, sorted, indent=2."""
    document = {
        "view": "routines",
        "source": "fleet/cron.py",
        "count": len(projection.routines),
        "routines": [routine.to_dict() for routine in projection.routines],
        "notes": list(projection.notes),
    }
    return json.dumps(document, indent=2, sort_keys=True) + "\n"


def deterministic(root: Path | str, pmo_root: Path | str | None = None) -> bool:
    """True when two derivations over one revision are byte-identical."""
    first = render(project(root, pmo_root=pmo_root))
    second = render(project(root, pmo_root=pmo_root))
    return first == second
