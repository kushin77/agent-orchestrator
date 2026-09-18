"""Ticket projection model — the paperclip ticket as the single join node.

Issue #401 (EPIC #399, M26); the decision is
:doc:`docs/decision-records/ADR-0014-ticket-single-join-node-contract-v2.md` and
the frozen shape is ``docs/contracts/paperclip/ticket.schema.json`` (contract v2).

The ticket is a **projection, never authority** (ADR-0014): the fleet's claim
ledger, board snapshot, lessons register and budget rail stay authoritative
exactly where they are, and this package *maps* them onto one node so every
consumer reads the join instead of rebuilding it. Contract v2's load-bearing
rule is ``authority{}`` — **one writer per field** — and this module makes that
rule a machine-checkable property rather than a slogan:

* a populated authority-tracked field with **no** declared writer fails;
* a populated authority-tracked field supplied by **two** producers fails;
* a populated field that the contract does not know at all fails.

Everything here is stdlib-only and offline. The vocabulary is read from the
frozen schema (never restated), so the projection cannot silently drift from the
contract it enforces.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

CONTRACT = "paperclip-ticket/v2"

#: Repository-relative inputs. This package reads them; it never writes them.
SCHEMA_RELPATH = "docs/contracts/paperclip/ticket.schema.json"
BOARD_RELPATH = ".board/snapshot.json"
CLAIMS_DIR_RELPATH = ".board/claims"
CLAIMS_FILE_RELPATH = ".board/claims.jsonl"
LESSONS_RELPATH = "governance/lessons/ledger.jsonl"
BUDGET_LEDGER_RELPATH = "telemetry/budgets/ledger.jsonl"
ATTESTATION_RELPATH = ".verify/attestation.json"

#: The projected store. Runtime state under ``.verify/`` (gitignored) — the
#: projection is rebuildable, so it is never committed as a second source of
#: truth.
STORE_RELPATH = ".verify/ticket/tickets.json"

#: The fleet issue id form the contract's own example uses.
ISSUE_ID_PREFIX = "kushin77/agent-orchestrator#"

# --- violation / warning vocabulary (stable; tests assert on these) ----------

CODE_TWO_WRITERS = "authority-two-writers"
CODE_PRODUCER_MISMATCH = "authority-producer-mismatch"
CODE_VALUE_CONFLICT = "authority-value-conflict"
CODE_UNKNOWN_FIELD = "contract-unknown-field"
CODE_SCHEMA_ENUM = "contract-schema-enum"
CODE_UNRESOLVED_REFERENCE = "reference-unresolved"
CODE_TICKET_UNBACKED = "ticket-unbacked"
CODE_BUDGET_RECEIPT_UNBACKED = "budget-receipt-unbacked"
CODE_STORE_MISMATCH = "store-mismatch"
CODE_NON_DETERMINISTIC = "projection-non-deterministic"

#: Input freshness (issue #1077): the committed board snapshot is a
#: point-in-time artifact, and a consumer that never states the age it tolerates
#: cannot tell "the board does not carry this reference" from "my copy is old".
CODE_BOARD_UNAGED = "board-snapshot-unaged"
CODE_BOARD_STALE = "board-snapshot-stale"

CODE_LESSON_JOIN_AMBIGUOUS = "lesson-join-ambiguous"
CODE_LEDGER_DUPLICATE = "ledger-duplicate-id"
CODE_EVIDENCE_UNATTACHED = "evidence-unattached"
CODE_EVIDENCE_UNREADABLE = "evidence-unreadable"

#: A populated value is one that carries something. An empty list/object/string
#: is *not* a writer's contribution, so it needs no ``authority`` entry; a
#: boolean is a value even when it is ``False``.
_EMPTY: tuple[Any, ...] = (None, "", (), [], {})


@dataclass(frozen=True)
class Contribution:
    """One producer's claim about one field of one ticket.

    ``field`` is the dotted contract path (``status``, ``facets.lessons``, …) or
    ``None`` for a bare reference that names a ticket without supplying a field
    (the projection's negative-control seam). ``where`` is the provenance the
    failure message names (``path:line``).
    """

    ticket: str
    field: str | None
    producer: str
    value: Any
    where: str


@dataclass(frozen=True)
class Violation:
    """A refused projection. ``subject`` is the offending id/field."""

    code: str
    subject: str
    detail: str
    where: str = ""

    def render(self) -> str:
        location = f" [{self.where}]" if self.where else ""
        return f"{self.code}: {self.subject} — {self.detail}{location}"


@dataclass(frozen=True)
class ProjectionWarning:
    """A reported-but-tolerated projection fact (never a silent skip)."""

    code: str
    subject: str
    detail: str
    where: str = ""

    def render(self) -> str:
        location = f" [{self.where}]" if self.where else ""
        return f"{self.code}: {self.subject} — {self.detail}{location}"


class CannotAssess(Exception):
    """The projection's inputs are unreadable, so no honest verdict exists."""


@dataclass(frozen=True)
class Contract:
    """The frozen contract v2 vocabulary, read from the schema.

    Never restated here: :func:`load_contract` reads
    ``docs/contracts/paperclip/ticket.schema.json`` so the projection and the
    contract it enforces cannot drift apart.
    """

    authority: dict[str, str]
    tracked: frozenset[str]
    untracked: frozenset[str]
    facet_names: frozenset[str]
    enums: dict[str, tuple[str, ...]]


def _schema_payload(root: Path | str) -> dict[str, Any]:
    path = Path(root) / SCHEMA_RELPATH
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CannotAssess(f"contract schema missing: {SCHEMA_RELPATH}") from exc
    except (OSError, ValueError) as exc:
        raise CannotAssess(f"contract schema unreadable: {SCHEMA_RELPATH} ({exc})") from exc
    if not isinstance(payload, dict):
        raise CannotAssess(f"contract schema is not an object: {SCHEMA_RELPATH}")
    return payload


def _enum_at(payload: dict[str, Any], path: tuple[str, ...]) -> tuple[str, ...]:
    node: Any = payload
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return ()
        node = node[key]
    if isinstance(node, dict) and isinstance(node.get("enum"), list):
        return tuple(str(item) for item in node["enum"])
    return ()


def load_contract(root: Path | str = ".") -> Contract:
    """Load contract v2's closed vocabulary from the frozen schema."""
    payload = _schema_payload(root)
    try:
        properties = payload["properties"]
        authority_props = properties["authority"]["properties"]
        defs = payload["$defs"]
    except (KeyError, TypeError) as exc:
        raise CannotAssess(f"contract schema is missing the v2 shape: {exc}") from exc

    authority: dict[str, str] = {}
    for field, spec in sorted(authority_props.items()):
        if not isinstance(spec, dict) or not isinstance(spec.get("const"), str):
            raise CannotAssess(f"authority[{field}] has no const writer")
        authority[field] = spec["const"]

    tracked = frozenset(authority)
    tracked_top = {field.split(".")[0] for field in tracked}
    top_level = {name for name in properties if name != "authority"}
    untracked = frozenset(name for name in top_level if name not in tracked_top)

    facet_names = frozenset(item for item in tracked if item.startswith("facets."))
    facet_props = (properties.get("facets") or {}).get("properties") or {}
    enums: dict[str, tuple[str, ...]] = {
        "status": _enum_at(payload, ("properties", "status")),
        "kind": _enum_at(payload, ("properties", "kind")),
        "facets.lessons.class": _enum_at(
            defs, ("facet_lessons", "properties", "class")
        ),
        "facets.raid.risk": _enum_at(defs, ("facet_raid", "properties", "risk")),
        "facets.budget.scope.level": _enum_at(
            defs, ("facet_budget", "properties", "scope", "properties", "level")
        ),
        "evidence.result": _enum_at(
            defs, ("evidence_receipt", "properties", "result")
        ),
    }
    # A facet the schema declares but the authority map does not track would be
    # an ungoverned extension; refuse to build a contract that admits one.
    for name in facet_props:
        if f"facets.{name}" not in facet_names:
            raise CannotAssess(f"facet '{name}' has no authority writer")

    return Contract(
        authority=authority,
        tracked=tracked,
        untracked=untracked,
        facet_names=facet_names,
        enums=enums,
    )


def issue_id(number: int) -> str:
    return f"{ISSUE_ID_PREFIX}{number}"


def issue_number(ticket_id: str) -> int | None:
    """The issue number a ticket id names, or ``None`` for a ledger node."""
    match = re.search(r"#(\d+)$", str(ticket_id))
    if match:
        return int(match.group(1))
    return None


def short_ref(ticket_id: str) -> str:
    """``#N`` for an issue ticket, else the ticket id unchanged."""
    number = issue_number(ticket_id)
    return f"#{number}" if number is not None else ticket_id


def is_populated(value: Any) -> bool:
    """True when a value actually carries something (never a bare empty form)."""
    if isinstance(value, float) and value == 0.0:
        return True
    if isinstance(value, int) and not isinstance(value, bool):
        return True
    return value not in _EMPTY


def canonical(document: Any) -> str:
    """The one serialization of a projection: sorted keys, ASCII, trailing NL.

    Two builds over one revision must be byte-identical, so the serialization is
    fully determined by the document: no timestamps, no set iteration order, no
    locale-dependent escaping.
    """
    return json.dumps(document, indent=2, sort_keys=True) + "\n"


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _ticket_sort_key(ticket_id: str) -> tuple[int, int, str]:
    number = issue_number(ticket_id)
    if number is not None:
        return (0, number, ticket_id)
    return (1, 0, str(ticket_id))


def sorted_tickets(tickets: Iterable[str]) -> list[str]:
    return sorted(tickets, key=_ticket_sort_key)


def first_difference(stored: Any, rebuild: Any, path: str = "$") -> str:
    """The JSON path of the first difference between a store and a rebuild.

    The orientation matters to the message: ``stored`` is the projection that
    was on disk and ``rebuild`` is what the ledgers re-derived, so a key that
    only the store carries is *stale* while a key only the rebuild carries is
    *missing* from the store.
    """
    if type(stored) is not type(rebuild):
        return f"{path} (type {type(stored).__name__} vs {type(rebuild).__name__})"
    if isinstance(stored, dict):
        for key in sorted(set(stored) | set(rebuild)):
            if key not in rebuild:
                return f"{path}.{key} (stale: present in the store, absent from the rebuild)"
            if key not in stored:
                return f"{path}.{key} (missing: absent from the store, present in the rebuild)"
            found = first_difference(stored[key], rebuild[key], f"{path}.{key}")
            if found:
                return found
        return ""
    if isinstance(stored, list):
        if len(stored) != len(rebuild):
            return f"{path} (length {len(stored)} vs {len(rebuild)})"
        for index, (one, two) in enumerate(zip(stored, rebuild)):
            found = first_difference(one, two, f"{path}[{index}]")
            if found:
                return found
        return ""
    if stored != rebuild:
        return f"{path} ({stored!r} vs {rebuild!r})"
    return ""
