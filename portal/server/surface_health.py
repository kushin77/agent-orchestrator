"""portal.server.surface_health — a console surface's own readiness signal (#802).


WHY this exists. The console ships a liveness route (``GET /api/healthz``: "the
process answers") and nothing else about itself. A surface can be *promoted* and
still be unable to serve — the view it redirects to is gone, the vocabulary its
steer half consumes is unreadable — and no signal said so. Issue #802 asks for a
**readiness/health signal of its own** for the console surface, on the existing
health rails (ADR-0022: no second dashboard).

What is measured, and what is deliberately *not*:

* the surface's **declaration** — ``infra/feature-flags/registry.yaml``
  ``surfaces.<name>`` — read through the same fail-closed reader as every other
  surface (``portal.server.fleet``), so this module cannot disagree with the
  route gate about whether the surface exists;
* an **engaged rollback** — the runtime overlay ``portal.server.surface_state``
  consults; a rolled-back surface reads ``off`` here too;
* the surface's **own served artifacts** (the view and script it redirects to and
  serves, declared once in :data:`SURFACES` and consumed by the app) and the
  **declarations its steer half consumes** (``control-plane/control/verbs.yaml``,
  the closed vocabulary the control API serves rather than re-declares).

Honest reporting is the contract, and it is a four-state vocabulary — never a
boolean, because "we could not tell" is neither ready nor broken:

===============  ============================================================
state            meaning
===============  ============================================================
``off``          not promoted (or rolled back) — *absent* by declaration. Not a
                 health claim: there is nothing to serve.
``ready``        promoted, no rollback engaged, every declared dependency present.
``not-ready``    promoted, but a declared dependency is missing/unusable — the
                 surface will answer while unable to serve. This is a **failed**
                 health check, and it is what the rollback anchor acts on.
``cannot-assess`` the declaration or the overlay could not be read. Never
                 ``ready``: a surface that cannot be measured is not a healthy
                 surface, and the rollback anchor refuses to act on it (acting
                 on an unreadable signal would fabricate a failure).
===============  ============================================================

An unpromoted surface stays **anonymous** on the HTTP rail: the readiness route
names only the surfaces it actually serves, for the same reason ``/console``
answers ``404 feature_disabled`` while the flag is off — an unpromoted surface is
*absent*, not merely unauthorised, so a probe must not be able to enumerate what
does not exist yet.


---knowledge---
module_id: portal.server.surface_health
system: portal
app: server
solution_class: pattern
patterns: [four-state-vocabulary, not-a-boolean, fail-closed]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [readiness, console_readiness, SurfaceSpec, Readiness, spec]
invariants: "a surface that cannot be measured is never ready; the readiness vocabulary is four states, never a boolean"
gotchas: "an unpromoted surface stays anonymous on the HTTP rail"
related: ["#802"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Tuple

from portal.server import surface_state
from portal.server import fleet as fleet_module

#: The console surface (the AgentConsole, issue #774) — the surface this lane
#: hardens, and the first one with a readiness signal of its own.
SURFACE_OPERATOR_TERMINAL = "operator_terminal"

#: The readiness states (a closed vocabulary — see the module docstring).
SURFACE_OFF = "off"
SURFACE_READY = "ready"
SURFACE_NOT_READY = "not-ready"
SURFACE_CANNOT_ASSESS = "cannot-assess"
SURFACE_STATES = (SURFACE_OFF, SURFACE_READY, SURFACE_NOT_READY, SURFACE_CANNOT_ASSESS)


@dataclass(frozen=True)
class SurfaceSpec:
    """What a console surface *is*: its flag, its artifacts, its inputs.

    ``assets`` are served under the console's static root and ``requires`` are
    repo-root-relative declarations the surface consumes; a missing entry in
    either is a failed readiness check. ``composes`` names the surfaces this one
    is built out of — reported, never gating, because an unpromoted half renders
    the disabled condition naming its own flag (the cockpit precedent) rather
    than breaking the console.
    """

    surface: str
    flag: str
    service: str
    assets: Tuple[str, ...] = ()
    requires: Tuple[str, ...] = ()
    composes: Tuple[str, ...] = ()


#: Every console surface with a readiness signal, by registry surface key.
SURFACES: Mapping[str, SurfaceSpec] = {
    SURFACE_OPERATOR_TERMINAL: SurfaceSpec(
        surface=SURFACE_OPERATOR_TERMINAL,
        flag="surfaces.operator_terminal",
        service="portal",
        assets=("views/console.html", "js/operator.js"),
        requires=("control-plane/control/verbs.yaml",),
        composes=("fleet_projection", "remote_control"),
    )
}


def spec(surface: str) -> SurfaceSpec:
    """The spec for ``surface``; an undeclared surface is a programming error."""
    try:
        return SURFACES[surface]
    except KeyError as exc:
        raise ValueError(
            f"unknown console surface {surface!r} "
            f"(declared: {', '.join(sorted(SURFACES))})"
        ) from exc


@dataclass(frozen=True)
class Readiness:
    """One surface's readiness, with the evidence a reader can act on."""

    surface: str
    state: str
    detail: str
    declared: str = "unknown"
    missing: Tuple[str, ...] = ()
    rolled_back: Optional[Mapping[str, Any]] = None
    dependencies: Mapping[str, str] = field(default_factory=dict)

    @property
    def promoted(self) -> bool:
        """True when the surface is not ``off`` — i.e. it should be serving."""
        return self.state != SURFACE_OFF

    @property
    def healthy(self) -> bool:
        """True only for an explicit ``ready`` — never for an unreadable state."""
        return self.state == SURFACE_READY

    def as_dict(self) -> dict:
        return {
            "state": self.state,
            "declared": self.declared,
            "detail": self.detail,
            "missing": list(self.missing),
            "rolledBack": dict(self.rolled_back) if self.rolled_back else None,
            "composes": dict(self.dependencies),
        }


def readiness(
    repo_root: Path | str,
    surface: str = SURFACE_OPERATOR_TERMINAL,
    *,
    registry_path: Optional[Path | str] = None,
    overlay_path: Optional[Path | str] = None,
    static_dir: Optional[Path | str] = None,
) -> Readiness:
    """The readiness of ``surface``, with the reason it holds that state.

    Order matters: an unreadable overlay is ``cannot-assess`` before the
    declaration is even read, and an engaged rollback is reported as ``off`` with
    the record that engaged it — so "why is it dark?" is answered by the signal
    rather than by convention.
    """
    target = spec(surface)
    root = Path(repo_root)

    rollbacks = surface_state.read_rollbacks(root, path=overlay_path)
    if rollbacks is None:
        return Readiness(
            surface=surface,
            state=SURFACE_CANNOT_ASSESS,
            declared="unknown",
            detail=(
                "the rollback overlay cannot be read "
                f"({surface_state.overlay_path(root, path=overlay_path)}); "
                "fail closed — the surface reads off and its readiness is unknown"
            ),
        )

    declarations = fleet_module.read_registry_surfaces(root, registry_path=registry_path)
    if declarations is None:
        return Readiness(
            surface=surface,
            state=SURFACE_CANNOT_ASSESS,
            declared="unknown",
            detail=(
                "the surface declaration could not be read "
                "(infra/feature-flags/registry.yaml); an unmeasurable surface is "
                "never reported ready"
            ),
        )

    entry = declarations.get(surface)
    if entry is None:
        return Readiness(
            surface=surface,
            state=SURFACE_OFF,
            declared="off",
            detail=(
                f"the declaration carries no surfaces.{surface} entry; "
                "an absent declaration reads as off"
            ),
        )

    declared = "on" if fleet_module.declares_on(entry) else "off"
    record = rollbacks.get(surface)
    if record is not None:
        return Readiness(
            surface=surface,
            state=SURFACE_OFF,
            declared=declared,
            rolled_back=record,
            detail=(
                "an engaged rollback keeps the surface off whatever the registry "
                f"declares (reason={record.get('reason', '')!r}, "
                f"actor={record.get('actor', '')!r}, at={record.get('at', '')!r})"
            ),
        )

    dependencies = {
        name: fleet_module.read_surface_default(
            root, registry_path=registry_path, surface=name
        )
        for name in target.composes
    }

    if declared == "off":
        return Readiness(
            surface=surface,
            state=SURFACE_OFF,
            declared="off",
            dependencies=dependencies,
            detail="declared off — unpromoted, so there is nothing to serve",
        )

    static_root = Path(static_dir) if static_dir else root / "portal" / "static"
    missing = tuple(
        [f"{static_root / asset}" for asset in target.assets if not (static_root / asset).is_file()]
        + [f"{root / required}" for required in target.requires if not (root / required).is_file()]
    )
    if missing:
        return Readiness(
            surface=surface,
            state=SURFACE_NOT_READY,
            declared=declared,
            missing=missing,
            dependencies=dependencies,
            detail=(
                "promoted but its declared dependenc"
                f"{'y is' if len(missing) == 1 else 'ies are'} missing: "
                + ", ".join(missing)
            ),
        )

    return Readiness(
        surface=surface,
        state=SURFACE_READY,
        declared=declared,
        dependencies=dependencies,
        detail=(
            "promoted, no rollback engaged, and every declared dependency is present"
        ),
    )


def console_readiness(repo_root: Path | str, **kwargs: Any) -> Tuple[Readiness, ...]:
    """Every declared console surface's readiness, in a stable order."""
    return tuple(
        readiness(repo_root, name, **kwargs) for name in sorted(SURFACES)
    )
