"""Load the ticket graph for the PMO views (issue #403).

---knowledge---
module_id: governance.pmo.graph
system: governance
app: pmo
solution_class: enterprise
patterns: [provoked-negative-control, offline-hermetic]
derives_from: null
owner_sme: pmo-sme
tier: L1
interfaces: [CannotAssess, Graph, issue_number, parse_timestamp, age_days, load]
invariants: ""
gotchas: ""
related: ["#401", "#403"]
do_not_duplicate: null
---knowledge---

The PMO layer is **queries over the ticket graph** (ADR-0014, issue #401), never
a store of its own. This module is the one place that materialises the graph:
it builds the projection **in memory** from the committed ledgers — the same
inputs ``governance/ticket`` reads — and hands the resulting ticket documents to
:mod:`views`.

Two deliberate rules follow from "never a store of its own":

* the graph is *rebuilt*, never read from ``.verify/ticket/tickets.json``. That
  file is a rebuildable cache; a PMO view computed from it could be stale, which
  is exactly the failure the gate provokes. A projection that refuses to build is
  a **CANNOT-ASSESS** for the PMO, never a pass.
* the PMO reads the projection's own committed inputs directly for the two facts
  the frozen contract deliberately does not carry on the ticket node — the
  board's ``state`` (open / closed lifecycle, distinct from the claim-derived
  ``status``) and the *timestamps* (``.board/snapshot.json`` ``generated_at`` and
  ``closed_at``, the claim ledger's ``at``, the lessons register's ``date``).
  Those are the graph's sources, not a second source of truth, and no view writes
  any of them.
"""

from __future__ import annotations

import importlib
import json
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

BOARD_RELPATH = ".board/snapshot.json"
CLAIMS_DIR_RELPATH = ".board/claims"
CLAIMS_FILE_RELPATH = ".board/claims.jsonl"
LESSONS_RELPATH = "governance/lessons/ledger.jsonl"

#: The ticket projection's modules, imported under their flat names.
_TICKET_MODULES = ("model", "sources", "builder")

#: Claim events that hold an issue, and events that clear it (mirrors
#: ``governance/ticket/sources.py``: the projection is the one writer of status).
_HOLD_EVENTS = ("claim", "take-over")
_CLEAR_EVENTS = ("release", "reap")


class CannotAssess(Exception):
    """The graph's inputs are unreadable, so no honest PMO verdict exists."""


@dataclass
class Graph:
    """The ticket graph plus the two facts the contract leaves to its sources.

    ``tickets`` maps ticket id → the projected ticket document. ``anchors`` maps
    a ticket id → the ISO timestamp at which it entered its *current* state (the
    claim/release that set its status, or the ledger record's own ``date``).
    ``lanes`` maps a ticket id → the lane its live claim names. ``state`` maps a
    board issue number → the board's lifecycle state.
    """

    root: Path
    tickets: dict[str, dict[str, Any]] = field(default_factory=dict)
    anchors: dict[str, str] = field(default_factory=dict)
    lanes: dict[str, str] = field(default_factory=dict)
    state: dict[int, str] = field(default_factory=dict)
    labels: dict[int, tuple[str, ...]] = field(default_factory=dict)
    generated_at: str = ""
    clock: str = ""

    def issue_number(self, ticket_id: str) -> int | None:
        """The issue number a ticket id names, or ``None`` for a ledger node."""
        return issue_number(ticket_id)

    def is_closed(self, ticket_id: str) -> bool:
        """True when the ticket is done — by verdict (``status``) or by the board.

        ``done`` is the claim ledger's verdict; the board's ``CLOSED`` state is
        the lifecycle fact. Either one means the ticket is no longer open work.
        """
        if (self.tickets.get(ticket_id) or {}).get("status") == "done":
            return True
        number = self.issue_number(ticket_id)
        return number is not None and self.state.get(number, "").strip().lower() == "closed"

    def status(self, ticket_id: str) -> str:
        value = (self.tickets.get(ticket_id) or {}).get("status")
        return value if isinstance(value, str) else ""

    def owner(self, ticket_id: str) -> str:
        value = (self.tickets.get(ticket_id) or {}).get("owner")
        return value if isinstance(value, str) else ""

    def lane(self, ticket_id: str) -> str:
        return self.lanes.get(ticket_id, "")

    def kind(self, ticket_id: str) -> str:
        value = (self.tickets.get(ticket_id) or {}).get("kind")
        return value if isinstance(value, str) else "task"

    def raid(self, ticket_id: str) -> dict[str, Any]:
        facets = (self.tickets.get(ticket_id) or {}).get("facets") or {}
        facet = facets.get("raid")
        return facet if isinstance(facet, dict) else {}

    def lessons(self, ticket_id: str) -> dict[str, Any]:
        facets = (self.tickets.get(ticket_id) or {}).get("facets") or {}
        facet = facets.get("lessons")
        return facet if isinstance(facet, dict) else {}

    def goal(self, ticket_id: str) -> str:
        value = (self.tickets.get(ticket_id) or {}).get("goal")
        return value if isinstance(value, str) else ""

    def blocked_by(self, ticket_id: str) -> list[str]:
        value = (self.tickets.get(ticket_id) or {}).get("blocked_by")
        return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []

    def labels_for(self, ticket_id: str) -> tuple[str, ...]:
        """The board issue's labels, when the ticket names a board issue.

        Labels are the board's own per-issue facts (they are not ``status``, so
        they are not a second source of the claim ledger's verdict).  The gate
        view derives *review-gate state* from them plus the ticket's ``status``
        — a derivation, never a store, exactly like the other views.
        """
        number = self.issue_number(ticket_id)
        if number is None:
            return ()
        return self.labels.get(number, ())


# --- ids ---------------------------------------------------------------------

def issue_number(ticket_id: str) -> int | None:
    text = str(ticket_id)
    if "#" not in text:
        return None
    tail = text.rsplit("#", 1)[1]
    return int(tail) if tail.isdigit() else None


# --- timestamps --------------------------------------------------------------

def parse_timestamp(value: Any) -> datetime | None:
    """Parse an ISO 8601 timestamp (or a bare date) as UTC, or ``None``.

    The committed inputs carry ``...Z`` timestamps and bare ``YYYY-MM-DD`` dates.
    Anything else is *unparseable*, and an unparseable timestamp is reported as
    such rather than silently treated as "now".
    """
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.combine(date.fromisoformat(text), datetime.min.time())
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def age_days(anchor: str, clock: str) -> float | None:
    """Whole days between an anchor and the clock, or ``None`` if unparseable."""
    start = parse_timestamp(anchor)
    end = parse_timestamp(clock)
    if start is None or end is None:
        return None
    return (end - start).total_seconds() / 86400.0


# --- inputs ------------------------------------------------------------------

def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_board(root: Path) -> tuple[dict[int, dict[str, Any]], str]:
    path = root / BOARD_RELPATH
    try:
        payload = _read_json(path)
    except FileNotFoundError as exc:
        raise CannotAssess(f"board snapshot missing: {BOARD_RELPATH}") from exc
    except (OSError, ValueError) as exc:
        raise CannotAssess(f"board snapshot unreadable: {BOARD_RELPATH} ({exc})") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("issues"), list):
        raise CannotAssess(f"board snapshot malformed: {BOARD_RELPATH}")
    board: dict[int, dict[str, Any]] = {}
    for issue in payload["issues"]:
        if isinstance(issue, dict) and isinstance(issue.get("number"), int):
            board[issue["number"]] = issue
    generated_at = payload.get("generated_at")
    return board, generated_at if isinstance(generated_at, str) else ""


def _claim_events(root: Path) -> list[dict[str, Any]]:
    """Every claim event, in write order (legacy file first, then the directory).

    Mirrors ``governance/ticket/sources.py``: the directory filename embeds the
    nanosecond write time, so lexical order is write order.
    """
    events: list[dict[str, Any]] = []
    legacy = root / CLAIMS_FILE_RELPATH
    if legacy.is_file():
        try:
            lines = legacy.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise CannotAssess(f"claim ledger unreadable: {CLAIMS_FILE_RELPATH} ({exc})") from exc
        for line in lines:
            if line.strip():
                try:
                    events.append(json.loads(line))
                except ValueError:
                    continue
    directory = root / CLAIMS_DIR_RELPATH
    if directory.is_dir():
        for path in sorted(directory.glob("*.json")):
            try:
                events.append(_read_json(path))
            except (OSError, ValueError):
                continue
    return events


def _ledger_records(root: Path) -> list[dict[str, Any]]:
    path = root / LESSONS_RELPATH
    if not path.is_file():
        return []
    records: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise CannotAssess(f"lessons register unreadable: {LESSONS_RELPATH} ({exc})") from exc
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and isinstance(record.get("id"), str):
            records.append(record)
    return records


# --- the ticket projection, imported in isolation ----------------------------

def _load_ticket_builder(root: Path):
    """Import ``governance/ticket/builder.py`` without leaking its module names.

    The projection's modules use *flat* imports (``from model import …``), the
    repo's per-suite convention. Loading them from this process therefore has to
    be scoped: the ticket directory is prepended to ``sys.path`` only for the
    import, and any module of the same name is restored afterwards, so a PMO view
    can never shadow (or be shadowed by) another package's ``model``/``sources``.

    The projection is taken from ``root`` when it carries one — a checkout is
    self-consistent with its own ledgers — and otherwise from this package's own
    repository, which is what lets the gate point the PMO at a minimal fixture
    root (the real projection code, a hand-written ledger). The schema the
    projection reads still comes from ``root``, so a fixture must carry it.
    """
    candidates = [
        root / "governance" / "ticket",
        Path(__file__).resolve().parents[1] / "ticket",
    ]
    ticket_dir = next((path for path in candidates if (path / "builder.py").is_file()), None)
    if ticket_dir is None:
        raise CannotAssess(
            "ticket projection missing: no governance/ticket/builder.py under "
            f"{candidates[0]} or {candidates[1]}"
        )
    saved_path = list(sys.path)
    saved_modules = {name: sys.modules.get(name) for name in _TICKET_MODULES}
    try:
        sys.path.insert(0, str(ticket_dir))
        for name in _TICKET_MODULES:
            sys.modules.pop(name, None)
        return importlib.import_module("builder")
    except CannotAssess:
        raise
    except Exception as exc:  # a broken projection is CANNOT-ASSESS, never a pass
        raise CannotAssess(f"ticket projection unimportable: {exc}") from exc
    finally:
        sys.path[:] = saved_path
        for name in _TICKET_MODULES:
            sys.modules.pop(name, None)
        for name, module in saved_modules.items():
            if module is not None:
                sys.modules[name] = module


def load(root: Path | str = ".") -> Graph:
    """Build the ticket graph and its timestamp/lane/state side-inputs.

    Raises :class:`CannotAssess` when an input is unreadable or the projection
    refuses to build — the PMO has no honest view over a graph that is not there.
    """
    root = Path(root)
    board, generated_at = _read_board(root)

    builder = _load_ticket_builder(root)
    projection = builder.build(root)
    if not projection.ok:
        rendered = "; ".join(violation.render() for violation in projection.violations[:3])
        raise CannotAssess(f"the ticket projection refuses to build: {rendered}")

    graph = Graph(root=root, generated_at=generated_at)
    graph.tickets = dict(projection.tickets)
    graph.state = {
        number: str(issue.get("state", "")) for number, issue in board.items()
    }
    graph.labels = {
        number: tuple(
            str(label)
            for label in (issue.get("labels") or ())
            if isinstance(label, str)
        )
        for number, issue in board.items()
    }

    # anchors + lanes: the last event that set each ticket's current state wins.
    for event in _claim_events(root):
        if not isinstance(event, dict) or not isinstance(event.get("issue"), int):
            continue
        if event["issue"] not in board:
            continue
        kind = event.get("event")
        if kind not in _HOLD_EVENTS and kind not in _CLEAR_EVENTS:
            continue
        ticket = _issue_ticket(event["issue"])
        stamp = event.get("at")
        if isinstance(stamp, str) and stamp:
            graph.anchors[ticket] = stamp
        lane = event.get("lane")
        if kind in _HOLD_EVENTS and isinstance(lane, str) and lane:
            graph.lanes[ticket] = lane

    # ledger nodes age from the record's own date.
    for record in _ledger_records(root):
        stamp = record.get("date")
        if isinstance(stamp, str) and stamp:
            graph.anchors.setdefault(str(record["id"]), stamp)

    # the clock: the latest timestamp the committed inputs themselves carry. No
    # wall clock — an offline view must be rebuildable byte-identically.
    candidates = [value for value in [generated_at, *graph.anchors.values()] if value]
    parsed = [(parse_timestamp(value), value) for value in candidates]
    parsed = [(stamp, value) for stamp, value in parsed if stamp is not None]
    graph.clock = max(parsed)[1] if parsed else ""
    if not graph.clock:
        raise CannotAssess("no timestamp in the committed inputs can anchor an age")
    return graph


def _issue_ticket(number: int) -> str:
    return f"kushin77/agent-orchestrator#{number}"
