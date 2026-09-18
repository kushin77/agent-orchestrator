"""Ledger -> board linkage (issue #1178).

The ledger is *the* register of what went wrong and what was learned, but a
register that cannot be reached from the board is an island: a reader of the
board cannot find the incident, and a reader of the ledger cannot find the
epic the work belongs to. The direction the ontology needs is
**ledger -> issue -> epic**, and until this module it did not exist: the only
enforced direction was *labelled issue -> ledger record*, and the label set was
small enough that the rule could not fail on real data.

Three things live here, and nothing else:

* :func:`linkage_map` — the measured census: for every ledger record, the board
  issue it reaches (if any), the epic/milestone that issue belongs to, whether
  it is an orphan, and whether its declaration is explicit.
* :func:`findings` — the rules that make the census binding. An unreachable
  record is named; a ``LESSON-*``/``SUGGEST-*`` that is unreachable *and silent*
  is an error (silence is what left twenty records unreachable); a reference
  that names a board issue that is not on the committed snapshot is an error;
  and an issue the ledger names as an incident's origin must carry the
  ``incident`` record label, so the scope declaration selects a set the ledger
  derives rather than a set that happens to be empty.
* :func:`counts` — the numbers the report and the CLI print.

Everything is offline: the ledger, the committed board snapshot, and (for the
cross-check) nothing else. No reader consults the network or the clock, so the
census is reproducible from a revision alone.

**A pull request is not a ticket.** A ``pr`` (or ``commit``/``event``) origin
does declare where a record came from, but it is not a board node, so it does
not make the record reachable. That is reported, not hidden: the record is an
orphan with an *explicit provenance* rather than a silent one, and the map says
which of the two it is.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from model import (
    CODE_BOARD_LINK_DANGLING,
    CODE_BOARD_LINK_GOAL_UNRESOLVED,
    CODE_BOARD_LINK_MISSING,
    CODE_BOARD_LINK_ORPHAN,
    CODE_BOARD_LINK_UNLABELLED,
    KIND_CORRECTIVE_ACTION,
    KIND_INCIDENT,
    KIND_LESSON,
    KIND_RCA,
    ORPHAN_KEY,
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    Finding,
    is_lesson,
    is_suggestion,
)

#: The link kinds a path may travel. ``origin``/``rca``/``incident`` are ledger
#: fields; ``remediation-issue`` is the typed ref an open action carries.
LINK_ORIGIN = "origin"
LINK_RCA = "rca"
LINK_INCIDENT = "incident"
LINK_REMEDIATION = "remediation-issue"

RE_ISSUE_REF = re.compile(r"^#(\d+)$")

#: The record kinds whose provenance is a board node. A ``lesson`` is not here:
#: its reachability is derived from the RCA it names, never from itself.
_PROVENANCE_KINDS = (KIND_INCIDENT, KIND_RCA)


def _issue_number(value: Any) -> Optional[int]:
    """The issue number a ``#<n>`` reference names, else ``None``."""
    match = RE_ISSUE_REF.match(str(value or "").strip())
    return int(match.group(1)) if match else None


def origin_of(record: Mapping[str, Any]) -> Tuple[str, str]:
    """``(kind, ref)`` of a record's ``origin``, or ``("", "")``."""
    origin = record.get("origin")
    if not isinstance(origin, dict):
        return ("", "")
    return (str(origin.get("kind", "")).strip(), str(origin.get("ref", "")).strip())


def orphan_declaration(record: Mapping[str, Any]) -> Tuple[bool, str, str]:
    """``(declared, reason, ticket)`` for a record's explicit orphan block."""
    block = record.get(ORPHAN_KEY)
    if not isinstance(block, dict):
        return (False, "", "")
    return (
        True,
        str(block.get("reason", "")).strip(),
        str(block.get("ticket", "")).strip(),
    )


def _links(record: Mapping[str, Any]) -> List[Tuple[str, str]]:
    """The outgoing ``(link, target)`` pairs a record declares, in a fixed order.

    Deterministic, so two runs over one revision agree. The order is the one a
    reader would follow: the record's own origin, the action that remediates it,
    then the records it names.
    """
    links: List[Tuple[str, str]] = []
    kind, ref = origin_of(record)
    if kind == "issue" and ref:
        links.append((LINK_ORIGIN, ref))
    remediation = str(record.get("remediation_issue", "")).strip()
    if remediation:
        links.append((LINK_REMEDIATION, remediation))
    for field_name in ("rca", "incident"):
        target = str(record.get(field_name, "")).strip()
        if target:
            links.append((field_name, target))
    return links


def reachable_issue(
    record: Mapping[str, Any],
    by_id: Mapping[str, Mapping[str, Any]],
    board: Mapping[int, Mapping[str, Any]],
) -> Tuple[Optional[int], List[str]]:
    """The board issue ``record`` reaches, with the path that reached it.

    Breadth-first over the typed links, so the shortest and most direct route
    wins and the result does not depend on dict ordering. A cycle (two records
    naming each other) terminates: a record is visited once.
    """
    start = str(record.get("id", ""))
    queue: List[Tuple[Mapping[str, Any], List[str]]] = [(record, [start])]
    seen = {start}
    while queue:
        current, path = queue.pop(0)
        for link, target in _links(current):
            number = _issue_number(target)
            if number is not None and number in board:
                return number, path + ["%s:#%d" % (link, number)]
            if link in (LINK_ORIGIN, LINK_REMEDIATION):
                continue
            nxt = by_id.get(target)
            if nxt is None or str(nxt.get("id", "")) in seen:
                continue
            seen.add(str(nxt.get("id", "")))
            queue.append((nxt, path + ["%s:%s" % (link, target)]))
    return None, [start]


def goal_of(number: int, board: Mapping[int, Mapping[str, Any]]) -> str:
    """The epic or milestone an issue belongs to, else ``""``.

    The epic relation of record is the line-1 ``Parent: #N`` body marker the
    board snapshot captures as ``parent`` (see ``governance/dispatch``); a
    milestone is the fallback, because a record reachable only as far as an
    unparented, unmilestoned issue has not reached a goal.
    """
    issue = board.get(number)
    if issue is None:
        return ""
    parent = issue.get("parent")
    if isinstance(parent, int) and not isinstance(parent, bool):
        return "#%d" % parent
    milestone = str(issue.get("milestone", "") or "").strip()
    return milestone


@dataclass(frozen=True)
class Row:
    """One ledger record's place in the board graph."""

    id: str
    kind: str
    origin_kind: str
    origin_ref: str
    issue: Optional[int] = None
    goal: str = ""
    path: Tuple[str, ...] = ()
    orphan_declared: bool = False
    orphan_reason: str = ""
    orphan_ticket: str = ""

    @property
    def reachable(self) -> bool:
        return self.issue is not None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "origin_kind": self.origin_kind,
            "origin_ref": self.origin_ref,
            "issue": self.issue,
            "goal": self.goal,
            "path": list(self.path),
            "orphan_declared": self.orphan_declared,
            "orphan_reason": self.orphan_reason,
            "orphan_ticket": self.orphan_ticket,
        }


def linkage_map(
    records: Iterable[Mapping[str, Any]],
    board: Optional[Mapping[int, Mapping[str, Any]]],
) -> List[Row]:
    """The census, one row per record, sorted by id."""
    board = board or {}
    by_id: Dict[str, Mapping[str, Any]] = {}
    for record in records:
        record_id = str(record.get("id", ""))
        if record_id and record_id not in by_id:
            by_id[record_id] = record

    rows: List[Row] = []
    for record in by_id.values():
        number, path = reachable_issue(record, by_id, board)
        declared, reason, ticket = orphan_declaration(record)
        kind, ref = origin_of(record)
        rows.append(
            Row(
                id=str(record.get("id", "")),
                kind=str(record.get("kind", "")),
                origin_kind=kind,
                origin_ref=ref,
                issue=number,
                goal=goal_of(number, board) if number is not None else "",
                path=tuple(path),
                orphan_declared=declared,
                orphan_reason=reason,
                orphan_ticket=ticket,
            )
        )
    return sorted(rows, key=lambda row: row.id)


def _declaration_codes(record: Mapping[str, Any], board: Mapping[int, Mapping[str, Any]]):
    """Every issue-shaped reference that must resolve, with the field it is on.

    The RCA's own ``origin`` is deliberately absent: it is already validated by
    the checker's ``origin-unresolved`` rule, and reporting one defect twice
    under two codes would make the finding count meaningless. Everything else —
    an incident's origin, an orphan declaration's tracking ticket, an open
    action's remediation issue — had no resolution check at all before this
    issue, which is how an incident can name an issue that does not exist.
    """
    out: List[Tuple[str, str, str]] = []
    kind = str(record.get("kind", ""))
    origin_kind, origin_ref = origin_of(record)
    if origin_kind == "issue" and kind != KIND_RCA:
        out.append((LINK_ORIGIN, origin_ref, "origin"))
    remediation = str(record.get("remediation_issue", "")).strip()
    if remediation:
        out.append((LINK_REMEDIATION, remediation, "remediation_issue"))
    declared, _reason, ticket = orphan_declaration(record)
    if declared and ticket:
        out.append((ORPHAN_KEY, ticket, "orphan.ticket"))
    return out


def ledger_named_issues(
    records: Iterable[Mapping[str, Any]],
    board: Mapping[int, Mapping[str, Any]],
) -> List[int]:
    """The issues the ledger *names* as an incident's origin (kind ``issue``).

    This is the holder set the label rule is derived from: the ledger says these
    issues record an incident, so the board must show it. Deriving it from the
    record rather than from the label is what keeps the rule from being vacuous
    — a label-only scope is empty exactly until someone exercises the rule.
    """
    out = set()
    for record in records:
        if str(record.get("kind", "")) != KIND_INCIDENT:
            continue
        kind, ref = origin_of(record)
        if kind != "issue":
            continue
        number = _issue_number(ref)
        if number is not None and number in board:
            out.add(number)
    return sorted(out)


def findings(
    records: Iterable[Mapping[str, Any]],
    board: Optional[Mapping[int, Mapping[str, Any]]],
    *,
    label: str,
) -> List[Finding]:
    """The ledger -> board linkage rules.

    Returns no findings when there is no board: without a snapshot the linkage
    cannot be assessed at all, and a rule that guessed would be worse than one
    that abstains (the caller reports CANNOT-ASSESS for the board as a whole).
    """
    if board is None:
        return []
    records = list(records)
    rows = linkage_map(records, board)
    by_id: Dict[str, Mapping[str, Any]] = {}
    for record in records:
        record_id = str(record.get("id", ""))
        by_id.setdefault(record_id, record)

    out: List[Finding] = []

    for row in rows:
        if not row.reachable:
            if row.orphan_declared:
                out.append(
                    Finding(
                        code=CODE_BOARD_LINK_ORPHAN,
                        message=(
                            "%s declares itself board-orphaned (%s); reason: %s"
                            % (row.id, row.kind, row.orphan_reason or "no reason given")
                        ),
                        subject=row.id,
                        severity=SEVERITY_WARNING,
                        remediation=(
                            "attach the record to a board issue when one exists%s"
                            % (
                                ", or record its tracking issue as orphan.ticket"
                                if not row.orphan_ticket
                                else " (tracked by %s)" % row.orphan_ticket
                            )
                        ),
                    )
                )
            elif is_lesson(row.id) or is_suggestion(row.id):
                out.append(
                    Finding(
                        code=CODE_BOARD_LINK_MISSING,
                        message=(
                            "%s reaches no board issue and declares no orphan reason "
                            "(origin: %s)"
                            % (
                                row.id,
                                "%s %s" % (row.origin_kind, row.origin_ref)
                                if row.origin_kind
                                else "absent",
                            )
                        ),
                        subject=row.id,
                        severity=SEVERITY_ERROR,
                        remediation=(
                            'declare `"%s": {"reason": "..."}` on the record, or give it '
                            "an origin that resolves to a board issue" % ORPHAN_KEY
                        ),
                    )
                )
            else:
                out.append(
                    Finding(
                        code=CODE_BOARD_LINK_ORPHAN,
                        message=(
                            "%s reaches no board issue; its origin is %s, which is not "
                            "a board node"
                            % (
                                row.id,
                                "%s %s" % (row.origin_kind, row.origin_ref)
                                if row.origin_kind
                                else "absent",
                            )
                        ),
                        subject=row.id,
                        severity=SEVERITY_WARNING,
                        remediation=(
                            "name a board issue the record can be reached from, or "
                            'declare `"%s": {"reason": "..."}`' % ORPHAN_KEY
                        ),
                    )
                )
        elif not row.goal:
            out.append(
                Finding(
                    code=CODE_BOARD_LINK_GOAL_UNRESOLVED,
                    message=(
                        "%s reaches issue #%d, which has neither an epic nor a milestone"
                        % (row.id, row.issue)
                    ),
                    subject=row.id,
                    severity=SEVERITY_WARNING,
                    remediation=(
                        "parent #%d to an epic (a line-1 `Parent: #N` marker) or give it "
                        "a milestone" % row.issue
                    ),
                )
            )

        for link, ref, field_name in _declaration_codes(by_id[row.id], board):
            number = _issue_number(ref)
            if number is not None and number in board:
                continue
            out.append(
                Finding(
                    code=CODE_BOARD_LINK_DANGLING,
                    message=(
                        "%s: %s %r names a board issue that is not on the committed "
                        "snapshot" % (row.id, field_name, ref)
                    ),
                    subject=row.id,
                    severity=SEVERITY_ERROR,
                    remediation=(
                        "fix the reference, or refresh .board/snapshot.json if the "
                        "issue postdates it"
                    ),
                )
            )

    for number in ledger_named_issues(records, board):
        issue = board.get(number, {})
        if label in (issue.get("labels") or []):
            continue
        out.append(
            Finding(
                code=CODE_BOARD_LINK_UNLABELLED,
                message=(
                    "issue #%d is named by the ledger as an incident's origin but does "
                    "not carry the `%s` record label, so the board rule cannot select it"
                    % (number, label)
                ),
                subject="#%d" % number,
                severity=SEVERITY_ERROR,
                remediation=(
                    "add the `%s` label to #%d and refresh the committed board snapshot "
                    "(the label is the scope declaration the board rule reads)" % (label, number)
                ),
            )
        )
    return out


def counts(
    records: Iterable[Mapping[str, Any]],
    board: Optional[Mapping[int, Mapping[str, Any]]],
    *,
    label: str = "incident",
) -> Dict[str, int]:
    """The census numbers: reach, goal, orphans, declared orphans, labelled."""
    records = list(records)
    board = board or {}
    rows = linkage_map(records, board)
    named = ledger_named_issues(records, board)
    return {
        "records": len(rows),
        "with_issue": sum(1 for row in rows if row.reachable),
        "with_goal": sum(1 for row in rows if row.goal),
        "orphans": sum(1 for row in rows if not row.reachable),
        "orphans_declared": sum(
            1 for row in rows if not row.reachable and row.orphan_declared
        ),
        "ledger_named_issues": len(named),
        "ledger_named_labelled": sum(
            1 for number in named if label in ((board.get(number) or {}).get("labels") or [])
        ),
    }
