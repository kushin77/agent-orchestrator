"""cockpit — the terminal cockpit (issue #566, RC-11 of EPIC #551).

A Bloomberg-terminal-grade operator surface for the fleet: live, dense,
keyboard-first, drillable, role-tiered. It is a CLIENT of the served API and
never an owner of fleet state (ADR-0026 D5/D6):

* it renders only what the RC-10 function registry declares
  (``control-plane/functions/functions.yaml``) — no ad-hoc verbs, no
  hand-built panels;
* it commands through the RC-3 control API using the RC-5 client half
  (``control-plane/cli/aoctl``) — one receipt or one named refusal per action;
* it watches through the two authenticated SSE streams
  (``surfaces.fleet_projection``, ``surfaces.telemetry_live_feed``) with
  ``--follow``, holding no credential of its own (ADR-0025 D2, ADR-0026 D5.3);
* it ships flag-gated OFF: while ``surfaces.cockpit`` is off it refuses to
  start with the named ``FLAG_OFF`` condition.

The stack is the one ADR-0026 D5.1 fixes: Python stdlib only (plus the repo's
accepted PyYAML), ANSI alternate-screen rendering — no TUI framework. The
issue's "Python + Textual" parenthetical is superseded by ADR-0026 D5.1
(Textual would be a new ADR about dependencies, not an import line).
"""

from __future__ import annotations

__all__ = ["__version__"]

#: The cockpit client's own version; the contracts it speaks have their own
#: (RC-2's registry schema, RC-3's route family, RC-10's function registry).
__version__ = "0.1.0"
