"""Read the PMO view so the routine projection *agrees* with it (issue #418).

The issue's own note is decisive: *"what is scheduled and who owns it" is the
same program-management truth the rollup reports; the routine view must agree
with it rather than become a second answer.* So this module does not derive a
second owner from anywhere — it reads the **PMO's own graph**
(``governance/pmo/graph.py`` over ``governance/ticket``, ADR-0014) and hands the
projection the facts it needs to check itself against it:

* whether the graph carries the ticket a routine is anchored to;
* the ``owner`` the graph names for it;
* the ``lane`` the graph derives for it from the claim ledger;
* whether the graph considers it closed.

The graph is imported **scoped** (``governance/pmo`` on ``sys.path`` only for the
import), because the PMO package uses flat imports by convention — exactly the
treatment ``governance/pmo/graph.py`` itself gives the ticket projection.

Nothing here writes: the PMO is a set of derived views over committed ledgers,
and the routine projection only reads it. A PMO that cannot be built is not
silently ignored — the caller renders it as ``pmo-unavailable`` and the gate
asserts the real root never degrades to that.

---knowledge---
module_id: integrations.paperclip.adapters.routines.pmo
system: integrations
app: paperclip
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: paperclip
tier: L1
interfaces: [PmoUnavailable, PmoView, load]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass, field
from pathlib import Path

#: The absolute module name the PMO graph is imported under, and the flat name
#: it must be reachable as (``graph``) while it is imported.
_GRAPH_ABSOLUTE = "governance/pmo/graph.py"
_GRAPH_FLAT = "graph"
_GRAPH_PRIVATE = "_ao418_pmo_graph"


class PmoUnavailable(Exception):
    """The PMO graph could not be built, so no agreement can be checked."""


@dataclass
class PmoView:
    """The facts from the PMO graph the routine projection checks itself against."""

    available: bool = False
    detail: str = ""
    tickets: set[str] = field(default_factory=set)
    _owners: dict[str, str] = field(default_factory=dict)
    _lanes: dict[str, str] = field(default_factory=dict)
    _closed: set[str] = field(default_factory=set)

    def carries(self, ticket: str) -> bool:
        """Does the graph carry this ticket at all?"""
        return ticket in self.tickets

    def owner(self, ticket: str) -> str:
        return self._owners.get(ticket, "")

    def lane(self, ticket: str) -> str:
        return self._lanes.get(ticket, "")

    def is_closed(self, ticket: str) -> bool:
        return ticket in self._closed


def _load_graph_module():
    """Import ``governance/pmo/graph.py`` without leaking its flat ``graph`` name."""
    package_dir = Path(__file__).resolve().parents[4] / "governance" / "pmo"
    source = package_dir / _GRAPH_ABSOLUTE.rsplit("/", 1)[1]
    if not source.is_file():
        raise PmoUnavailable(f"{_GRAPH_ABSOLUTE} is missing under {package_dir}")
    saved_path = list(sys.path)
    saved_graph = sys.modules.get(_GRAPH_FLAT)
    try:
        sys.path.insert(0, str(package_dir))
        sys.modules.pop(_GRAPH_FLAT, None)
        module = importlib.import_module(_GRAPH_FLAT)
        return module
    except Exception as exc:  # an unimportable PMO is no agreement, not a pass
        raise PmoUnavailable(f"{_GRAPH_ABSOLUTE} unimportable: {exc}") from exc
    finally:
        sys.path[:] = saved_path
        sys.modules.pop(_GRAPH_FLAT, None)
        if saved_graph is not None:
            sys.modules[_GRAPH_FLAT] = saved_graph


def load(root: Path | str) -> PmoView:
    """Build the PMO graph for ``root`` and expose the facts the projection needs.

    Raises :class:`PmoUnavailable` when the graph cannot be built (a missing
    board snapshot, an unreadable ledger, a projection that refuses to build) —
    the caller decides whether that is a note or a failure; it is never a silent
    pass.
    """
    try:
        module = _load_graph_module()
        graph = module.load(Path(root))
    except PmoUnavailable:
        raise
    except Exception as exc:
        raise PmoUnavailable(f"the PMO graph could not be built for {root}: {exc}") from exc

    view = PmoView(available=True)
    view.tickets = set(graph.tickets)
    for ticket in sorted(graph.tickets):
        try:
            owner = graph.owner(ticket)
            lane = graph.lane(ticket)
            closed = graph.is_closed(ticket)
        except Exception:  # a graph accessor that raises cannot be an agreement
            continue
        if owner:
            view._owners[ticket] = owner
        if lane:
            view._lanes[ticket] = lane
        if closed:
            view._closed.add(ticket)
    return view
