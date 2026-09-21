"""Typed ticket edges over the lessons ledger (issue #402).

---knowledge---
module_id: governance.lessons.edges
system: governance
app: lessons
solution_class: enterprise
patterns: [deterministic]
derives_from: null
owner_sme: qa-sme
tier: L1
interfaces: [load_records, ticket_edges, TypedEdge, ticket_kind, normalize_origin, remediation_issue, edges, edge_dicts, as_dict, pmo_rows, (+2 more)]
invariants: ""
gotchas: ""
related: ["#402"]
do_not_duplicate: null
---knowledge---

The lessons register is not a silo joined by hand: it is an **edge source** in
the ticket graph (ADR-0014). This module is that edge emission, and it is the
**single** place a lessons cross-record reference is turned into a node id —
``origin`` and ``remediation_issue`` stop being free strings that every reader
re-parses for itself.

Three things live here, and nothing else:

* :func:`ticket_kind` — the ticket-contract ``kind`` for a ledger record, so an
  open ``SUGGEST-*`` is a ticket of kind ``suggestion`` (the measured gap this
  issue closes: a valid ledger id that was not an addressable target).
* :func:`edges` — the typed edges (``caused-by`` / ``origin`` / ``mitigates`` /
  ``remediation-of``), derived deterministically from the ledger fields. The
  ledger stays the single **write** surface; the typed edge is the single
  **read** surface (dual-read, single-write — no second ledger).
* :func:`pmo_rows` — the PMO view: every learning with its owner, status, class
  and the goal it belongs to, so the PMO lane derives risk and aging without a
  second store.

The edges are derived, never stored: a stored copy would be a second source of
truth the ledger could drift from, and the whole point of the typed edge is that
there is exactly one place the reference means something.

The vocabulary is closed (:data:`model.TICKET_EDGE_TYPES`); an edge whose type
is not one of the four cannot be constructed here.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence


def _sibling(name: str):
    """Load a sibling module by file path under a private name.

    Both ``governance/lessons`` and ``governance/knowledge`` ship a ``model.py``
    and both are imported as the bare module name ``model`` (the repo's
    standalone-module convention). A host process that has already imported the
    knowledge ``model`` would otherwise satisfy this module's ``from model
    import ...`` with the wrong file. Loading the sibling explicitly keeps the
    lessons vocabulary the lessons vocabulary, whatever the host imported.
    """
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), name + ".py")
    spec = importlib.util.spec_from_file_location("ao_lessons_" + name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError("cannot load %s" % path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_model = _sibling("model")

EDGE_CAUSED_BY = _model.EDGE_CAUSED_BY
EDGE_ORIGIN = _model.EDGE_ORIGIN
EDGE_MITIGATES = _model.EDGE_MITIGATES
EDGE_REMEDIATION_OF = _model.EDGE_REMEDIATION_OF
TICKET_EDGE_TYPES = _model.TICKET_EDGE_TYPES
KIND_CORRECTIVE_ACTION = _model.KIND_CORRECTIVE_ACTION
KIND_INCIDENT = _model.KIND_INCIDENT
KIND_LESSON = _model.KIND_LESSON
KIND_RCA = _model.KIND_RCA
ticket_kind_for = _model.ticket_kind_for

SCHEMA = "cmr.lessons/ticket-edges-v1"

#: The canonical ledger, declared here so this edge source is self-contained.
LEDGER_RELPATH = "governance/lessons/ledger.jsonl"

RE_ISSUE_REF = re.compile(r"^#(\d+)$")
RE_SHA = re.compile(r"^[0-9a-fA-F]{7,40}$")


def load_records(root: Any) -> List[Dict[str, Any]]:
    """Read the canonical ledger as a list of record dicts (order preserved)."""
    path = Path(root) / LEDGER_RELPATH
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    records: List[Dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return records


def ticket_edges(root: Any) -> List["TypedEdge"]:
    """The typed ticket edges declared by the lessons register at ``root``.

    The entry point a generic edge consumer (``governance/knowledge/crossref``)
    calls: it takes a repository root and returns typed edges, knowing nothing
    about how the register is stored.
    """
    return edges(load_records(root))


@dataclass(frozen=True)
class TypedEdge:
    """One typed ticket edge: ``from_id -type-> to_id``."""

    from_id: str
    type: str
    to_id: str

    def as_dict(self) -> Dict[str, str]:
        return {"from_id": self.from_id, "type": self.type, "to_id": self.to_id}

    def key(self) -> tuple:
        return (self.type, self.from_id, self.to_id)


def _records_by_id(records: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    by_id: Dict[str, Dict[str, Any]] = {}
    for record in records:
        record_id = str(record.get("id", ""))
        if record_id and record_id not in by_id:
            by_id[record_id] = record
    return by_id


def ticket_kind(record: Mapping[str, Any]) -> str:
    """The ticket-contract kind for a ledger record (``""`` when unknown)."""
    return ticket_kind_for(dict(record))


def normalize_origin(origin: Any) -> str:
    """Normalize a ledger ``origin`` dict to a ticket node id, else ``""``.

    This is the one normalization the lessons lane owns; no reader normalizes an
    origin for itself any more.
    """
    if not isinstance(origin, dict):
        return ""
    kind = str(origin.get("kind", "")).strip()
    ref = str(origin.get("ref", "")).strip()
    if not kind or not ref:
        return ""
    if kind == "issue":
        match = RE_ISSUE_REF.match(ref)
        return ("issue-" + match.group(1)) if match else ""
    if kind == "pr":
        match = RE_ISSUE_REF.match(ref)
        return ("pr-" + match.group(1)) if match else ""
    if kind == "commit":
        return ("commit-" + ref) if RE_SHA.match(ref) else ""
    if kind == "event":
        return "event-" + ref
    return ""


def remediation_issue(record: Mapping[str, Any]) -> str:
    """The ticket id an open action is remediated by, else ``""``.

    Replaces a free-string read of ``remediation_issue``: the value is typed as
    an ``issue-<n>`` node, and a value that cannot be typed resolves to nothing
    rather than being passed through as text.
    """
    ref = str(record.get("remediation_issue", "")).strip()
    match = RE_ISSUE_REF.match(ref) if ref else None
    return ("issue-" + match.group(1)) if match else ""


def _origin_edge(record: Mapping[str, Any]) -> Optional[TypedEdge]:
    record_id = str(record.get("id", ""))
    target = normalize_origin(record.get("origin"))
    if not record_id or not target:
        return None
    if str(record.get("kind", "")) not in (KIND_INCIDENT, KIND_RCA):
        return None
    return TypedEdge(record_id, EDGE_ORIGIN, target)


def edges(records: Iterable[Dict[str, Any]]) -> List[TypedEdge]:
    """The typed ticket edges, sorted and deduplicated.

    Deterministic: it is a pure function of the ledger records, and its output is
    ordered by ``(type, from_id, to_id)`` so two runs over one revision agree.
    """
    records = list(records)
    by_id = _records_by_id(records)
    found: List[TypedEdge] = []

    for record in by_id.values():
        record_id = str(record.get("id", ""))
        kind = record.get("kind")

        if kind == KIND_RCA:
            incident = str(record.get("incident", "")).strip()
            if incident and incident in by_id:
                found.append(TypedEdge(record_id, EDGE_CAUSED_BY, incident))
        elif kind == KIND_CORRECTIVE_ACTION:
            rca = str(record.get("rca", "")).strip()
            if rca and rca in by_id:
                found.append(TypedEdge(record_id, EDGE_MITIGATES, rca))
            target = remediation_issue(record)
            if target:
                found.append(TypedEdge(record_id, EDGE_REMEDIATION_OF, target))
        elif kind == KIND_LESSON:
            rca = str(record.get("rca", "")).strip()
            rca_record = by_id.get(rca)
            if rca_record and rca_record.get("kind") == KIND_RCA:
                incident = str(rca_record.get("incident", "")).strip()
                if incident and incident in by_id:
                    found.append(TypedEdge(record_id, EDGE_MITIGATES, incident))

        origin_edge = _origin_edge(record)
        if origin_edge is not None:
            found.append(origin_edge)

    unique = sorted({(e.type, e.from_id, e.to_id) for e in found})
    return [TypedEdge(type=rel_type, from_id=from_id, to_id=to_id)
            for rel_type, from_id, to_id in unique]


def edge_dicts(records: Iterable[Dict[str, Any]]) -> List[Dict[str, str]]:
    """The typed edges as JSON-ready dicts (stable order)."""
    return [edge.as_dict() for edge in edges(records)]


def as_dict(records: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """The whole ticket-graph view of the lessons register."""
    records = list(records)
    return {
        "schema": SCHEMA,
        "edge_types": list(TICKET_EDGE_TYPES),
        "nodes": [
            {
                "id": str(record.get("id", "")),
                "kind": ticket_kind_for(record),
                "status": str(record.get("status", "")),
                "class": str(record.get("class", "")),
                "owner": str(record.get("owner", "")),
            }
            for record in sorted(records, key=lambda r: str(r.get("id", "")))
        ],
        "edges": edge_dicts(records),
    }


def _goal_of(record: Mapping[str, Any], by_id: Mapping[str, Dict[str, Any]]) -> str:
    """The epic/milestone a record's origin issue belongs to, else ``""``.

    The goal is derived from the origin ref only — the lessons lane never invents
    one, and the ticket contract's ``goal`` stays written by the board snapshot.
    """
    origin = record.get("origin")
    if not isinstance(origin, dict):
        return ""
    if str(origin.get("kind", "")) != "issue":
        return ""
    match = RE_ISSUE_REF.match(str(origin.get("ref", "")).strip())
    return match.group(0) if match else ""


def pmo_rows(
    records: Iterable[Dict[str, Any]],
    snapshot: Optional[Mapping[int, Mapping[str, Any]]] = None,
) -> List[Dict[str, str]]:
    """The PMO view of every learning: owner, status, class and goal.

    A learning's ``goal`` is the epic it belongs to, resolved from its origin
    issue through the committed board snapshot (``parent`` when set, else the
    milestone). When the goal cannot be resolved it is reported empty rather than
    guessed; the ticket projection supplies ``owner`` for a learning that does
    not carry one, which is why the lessons lane leaves it blank instead of
    inventing an authority.
    """
    records = list(records)
    by_id = _records_by_id(records)
    rows: List[Dict[str, str]] = []
    for record in sorted(records, key=lambda r: str(r.get("id", ""))):
        record_id = str(record.get("id", ""))
        kind = ticket_kind_for(record)
        if kind not in ("lesson", "suggestion"):
            continue
        goal = _goal_of(record, by_id)
        if not goal:
            rca = str(record.get("rca", "")).strip()
            rca_record = by_id.get(rca)
            if rca_record is not None:
                goal = _goal_of(rca_record, by_id)
        goal_ref = ""
        if goal and snapshot:
            issue = snapshot.get(int(goal[1:]))
            if issue is not None:
                parent = issue.get("parent")
                if isinstance(parent, int):
                    goal_ref = "#%d" % parent
                elif str(issue.get("milestone", "")).strip():
                    goal_ref = str(issue["milestone"]).strip()
        rows.append(
            {
                "id": record_id,
                "kind": kind,
                "owner": str(record.get("owner", "")),
                "status": str(record.get("status", "")),
                "class": str(record.get("class", "")),
                "goal": goal_ref,
                "origin": goal,
            }
        )
    return rows


def findings(records: Iterable[Dict[str, Any]]) -> List[str]:
    """Every cross-record reference that cannot be typed into a ticket edge.

    A reference a reader cannot type is named here instead of being passed
    through as free text — the honesty rule the typed edge exists to enforce.
    """
    out: List[str] = []
    by_id = _records_by_id(records)
    for record in sorted(by_id.values(), key=lambda r: str(r.get("id", ""))):
        record_id = str(record.get("id", ""))
        ref = str(record.get("remediation_issue", "")).strip()
        if ref and not remediation_issue(record):
            out.append(
                "%s: remediation_issue %r is not a ticket reference (#<n>)"
                % (record_id, ref)
            )
        origin = record.get("origin")
        if isinstance(origin, dict):
            kind = str(origin.get("kind", "")).strip()
            ref = str(origin.get("ref", "")).strip()
            if kind and ref and not normalize_origin(origin) and kind != "event":
                out.append(
                    "%s: origin %r is not a ticket reference" % (record_id, ref)
                )
    return out


def validate_edge_types(edges_seen: Sequence[TypedEdge]) -> List[str]:
    """Findings for any edge whose type is outside the closed vocabulary."""
    return [
        "edge %s -> %s has unknown type %r" % (e.from_id, e.to_id, e.type)
        for e in edges_seen
        if e.type not in TICKET_EDGE_TYPES
    ]
