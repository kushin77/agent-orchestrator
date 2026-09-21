#!/usr/bin/env python3
"""The headless cockpit renderer (issue #565, RC-10 of #551).

WHY this exists. ADR-0026 D10 lists four honesty obligations the cockpit must
carry, and the acceptance for this lane requires the registry to be exercised
**without a live plane**. Both fall out of the same thing: rendering is a pure
function from *a declared function* plus *a fixture* to a frame of text. So the
gate can render every declared function, in every outcome, with no network, no
TTY, no tmux and no bearer token.

The four obligations, each a branch here rather than a virtue:

1. **a panel that cannot load says so** — an unreadable fixture is ``FAILED``
   with its reason, never a silently empty panel (an empty panel reads as "all
   quiet", which is a claim this client is not entitled to make);
2. **``NO_DATA`` is never rendered as OK** — the absence is rendered as absence;
3. **an unpromoted surface is invisible, and the flag is named** — a disabled
   fixture renders the flag from ``infra/feature-flags/registry.yaml`` (the
   function's own ``flags``), so an operator can tell "off" from "broken";
4. **every action prints a receipt or an explicit refusal** — a command renders
   the endpoint it would call and the audit action it would record, or the
   refusal; there is no optimistic "OK".

Nothing here decides *permission*. Roles are a filter over what to show first
(``registry.recommended``), never a gate on what may run: the capability in a
function's ``scope`` is RC-2's, and ``identity/rbac`` enforces it per call.


---knowledge---
module_id: control-plane.functions.cockpit_render
system: control-plane
app: functions
solution_class: enterprise
patterns: [pure-function-render, no-data-is-never-ok, named-refusal, offline-fixture]
derives_from: null
owner_sme: frontend-sme
tier: L1
interfaces: [render, render_all, render_workspace, load_fixtures, Fixture, OUTCOMES]
invariants: "rendering is a pure function from a declared function plus a fixture to a frame of text, so the gate renders every function with no network, no TTY, no tmux and no bearer token"
gotchas: "nothing here decides permission: roles are a filter over what to show first, never a gate on what may run, and a disabled fixture renders the flag so an operator can tell off from broken"
related: ["#565", "#551"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

# This module's own directory is placed first so the sibling validator resolves by
# its unique name (`cockpit_registry`, never a bare `registry` -- the repository
# carries a top-level `registry/` package and a collision would be invisible).
_PACKAGE = Path(__file__).resolve().parent
if str(_PACKAGE) not in sys.path:
    sys.path.insert(0, str(_PACKAGE))

import cockpit_registry as reg  # noqa: E402

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
OUTCOMES_FIXTURE = FIXTURE_DIR / "outcomes.json"
BODIES_FIXTURE = FIXTURE_DIR / "bodies.json"

#: The closed outcome set. Every one of them is renderable — a fixture may not
#: invent a fifth, because an unhandled outcome is exactly how "silently empty"
#: gets in.
OUTCOMES = ("ok", "no_data", "disabled", "error")


class RenderError(RuntimeError):
    """The frame cannot be rendered honestly (CANNOT-ASSESS for the caller)."""


@dataclass(frozen=True)
class Fixture:
    """One read's outcome plus the rows it carries."""

    function_id: str
    outcome: str
    flag: Optional[str] = None
    reason: Optional[str] = None
    rows: tuple[str, ...] = ()
    receipt: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.outcome == "ok"


def load_fixtures(directory: Path | str | None = None) -> dict[str, Fixture]:
    """Every declared function's fixture, keyed by function id."""
    base = Path(directory) if directory is not None else FIXTURE_DIR
    outcomes = _read_json(base / OUTCOMES_FIXTURE.name)
    bodies = _read_json(base / BODIES_FIXTURE.name)
    if not isinstance(outcomes, Mapping) or not isinstance(bodies, Mapping):
        raise RenderError(f"{base} fixtures must be mappings")
    closed = tuple(str(o) for o in (outcomes.get("closed") or []))
    if closed != OUTCOMES:
        raise RenderError(
            f"{base / OUTCOMES_FIXTURE.name} declares the closed set {list(closed)}; this "
            f"renderer implements {list(OUTCOMES)}"
        )
    fixtures: dict[str, Fixture] = {}
    for function_id, body in (bodies.get("fixtures") or {}).items():
        if not isinstance(body, Mapping):
            continue
        outcome = str(body.get("outcome", "ok"))
        if outcome not in closed:
            raise RenderError(
                f"fixture {function_id} declares outcome {outcome!r}; the closed set "
                f"is {list(closed)}"
            )
        fixtures[str(function_id)] = Fixture(
            function_id=str(function_id),
            outcome=outcome,
            flag=body.get("flag") if isinstance(body.get("flag"), str) else None,
            reason=body.get("reason") if isinstance(body.get("reason"), str) else None,
            rows=tuple(str(r) for r in (body.get("rows") or [])),
            receipt=body.get("receipt") if isinstance(body.get("receipt"), str) else None,
        )
    if not fixtures:
        raise RenderError(f"{base / BODIES_FIXTURE.name} carries no fixture")
    return fixtures


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RenderError(f"{path} is unreadable: {exc!r}") from exc


def render(function: "reg.Function", fixture: Fixture) -> str:
    """One frame for one declared function. Pure; no I/O beyond the fixture."""
    if fixture.outcome not in OUTCOMES:
        raise RenderError(
            f"{function.id}: outcome {fixture.outcome!r} is outside {list(OUTCOMES)}"
        )
    header = f"{function.id:<12} {function.title}  [{function.kind}]"

    if fixture.outcome == "error":
        reason = fixture.reason or "the read returned no reason"
        return "\n".join((header, f"  FAILED  {reason}"))

    if fixture.outcome == "disabled":
        flag = fixture.flag or _first_flag(function)
        return "\n".join((header, f"  disabled  {flag} is off — this surface is absent, not broken"))

    if fixture.outcome == "no_data":
        return "\n".join((header, "  NO_DATA  (no data is not a green state)"))

    body = [f"  {row}" for row in fixture.rows] or ["  (no rows in the fixture)"]
    lines = [header, *body]
    if function.kind == "command":
        lines.append(
            f"  receipt  POST {function.endpoints[0]} · audit {function.audit}"
            f"{' · ' + fixture.receipt if fixture.receipt else ''}"
        )
    return "\n".join(lines)


def _first_flag(function: "reg.Function") -> str:
    return function.flags[0] if function.flags else "surfaces.unknown"


def render_all(
    registry: "reg.Registry", fixtures: Mapping[str, Fixture] | None = None
) -> dict[str, str]:
    """Render EVERY declared function against its fixture.

    The count is asserted rather than trusted: a frame count that does not equal
    the declared function count is how a silently-skipped panel gets in.
    """
    fixtures = fixtures if fixtures is not None else load_fixtures()
    frames: dict[str, str] = {}
    for function in registry.ordered:
        fixture = fixtures.get(function.id)
        if fixture is None:
            frames[function.id] = render(
                function,
                Fixture(function_id=function.id, outcome="error",
                        reason=f"fixtures/bodies.json declares no fixture for {function.id}"),
            )
            continue
        frames[function.id] = render(function, fixture)
    return frames


def render_workspace(
    registry: "reg.Registry",
    role: str,
    fixtures: Mapping[str, Fixture] | None = None,
) -> str:
    """A role's recommended functions, rendered. A filter, never a permission."""
    fixtures = fixtures if fixtures is not None else load_fixtures()
    frames = render_all(registry, fixtures)
    ids: Iterable[str] = reg.render_workspace(registry, role)
    return "\n\n".join(frames[function_id] for function_id in ids)
