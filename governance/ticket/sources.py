"""Read-only source readers for the ticket projection (issue #401).

Each reader is a **producer** whose name is exactly the ``authority`` value the
frozen contract pins for the fields it supplies — that is what makes the
``authority{}`` rule checkable: the projection compares the producers that
actually wrote a field against the writer the contract declares.

Producers (name → fields):

============================  ================================
producer                      fields
============================  ================================
``.board/snapshot.json``      ``goal``, ``blocked_by`` (and ``kind`` = task)
``governance/dispatch``       ``status`` (claim / lifecycle)
``governance/isolation``      ``owner`` (the minted session identity)
``governance/lessons``        ``facets.lessons`` (the register join)
``telemetry/budgets``         ``facets.budget``
``derived``                   ``facets.raid``
``.verify/attestation.json``  ``evidence`` (not authority-tracked)
============================  ================================

Every reader is offline and takes the repository root, so the whole projection
is a pure function of the committed ledgers. No reader writes anything, and no
reader consults the wall clock: lease expiry is the live claim gate's concern
(``governance/dispatch``), not a projection's — a projection that read the clock
could not be rebuilt byte-identically.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from model import (
    ATTESTATION_RELPATH,
    BOARD_RELPATH,
    BUDGET_LEDGER_RELPATH,
    CLAIMS_DIR_RELPATH,
    CLAIMS_FILE_RELPATH,
    CODE_EVIDENCE_UNATTACHED,
    CODE_EVIDENCE_UNREADABLE,
    CODE_LEDGER_DUPLICATE,
    CODE_LESSON_JOIN_AMBIGUOUS,
    CODE_UNRESOLVED_REFERENCE,
    LESSONS_RELPATH,
    CannotAssess,
    Contribution,
    ProjectionWarning,
    Violation,
    issue_id,
    issue_number,
    short_ref,
)

PRODUCER_SNAPSHOT = ".board/snapshot.json"
PRODUCER_DISPATCH = "governance/dispatch"
PRODUCER_ISOLATION = "governance/isolation"
PRODUCER_LESSONS = "governance/lessons"
PRODUCER_BUDGETS = "telemetry/budgets"
PRODUCER_DERIVED = "derived"
PRODUCER_ATTESTATION = ".verify/attestation.json"

#: Claim events that *hold* an issue, and events that clear it.
_HOLD_EVENTS = ("claim", "take-over")
_CLEAR_EVENTS = ("release", "reap")

#: Ledger ``kind`` → ticket ``kind`` (contract v2's closed enum).
_LEDGER_TICKET_KIND = {
    "incident": "incident",
    "rca": "rca",
    "corrective-action": "corrective-action",
    "lesson": "lesson",
}

#: ``priority:Pn`` → the RAID risk level the ticket carries.
_PRIORITY_RISK = {"P0": "critical", "P1": "high", "P2": "medium", "P3": "low"}

RE_ISSUE_REF = re.compile(r"^#?(\d+)$")
RE_BRANCH_ISSUE = re.compile(r"^issue-(\d+)")


def _read_json_object(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _rel(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


# -- board snapshot ----------------------------------------------------------

def read_board(root: Path | str) -> dict[int, dict[str, Any]]:
    """The committed board snapshot, keyed by issue number.

    Raises :class:`CannotAssess` when the snapshot is absent or unparseable —
    without it there is no honest ticket graph to build.
    """
    root = Path(root)
    payload = _read_json_object(root / BOARD_RELPATH)
    if not isinstance(payload, dict) or not isinstance(payload.get("issues"), list):
        raise CannotAssess(f"board snapshot missing or malformed: {BOARD_RELPATH}")
    board: dict[int, dict[str, Any]] = {}
    for issue in payload["issues"]:
        if not isinstance(issue, dict):
            continue
        number = issue.get("number")
        if isinstance(number, int) and not isinstance(number, bool):
            board[number] = issue
    return board


def board_contributions(board: dict[int, dict[str, Any]]) -> list[Contribution]:
    """``kind``/``goal``/``blocked_by`` — the board's slice of every ticket."""
    out: list[Contribution] = []
    for number, issue in sorted(board.items()):
        ticket = issue_id(number)
        out.append(Contribution(ticket, "kind", PRODUCER_SNAPSHOT, "task", BOARD_RELPATH))
        parent = issue.get("parent")
        if isinstance(parent, int) and not isinstance(parent, bool):
            out.append(
                Contribution(ticket, "goal", PRODUCER_SNAPSHOT, f"#{parent}", BOARD_RELPATH)
            )
        blocked = [
            f"#{item}"
            for item in (issue.get("blocked_by") or [])
            if isinstance(item, int) and not isinstance(item, bool)
        ]
        if blocked:
            out.append(
                Contribution(
                    ticket, "blocked_by", PRODUCER_SNAPSHOT, sorted(blocked), BOARD_RELPATH
                )
            )
    return out


# -- claim ledger ------------------------------------------------------------

def _claim_events(root: Path) -> tuple[list[tuple[dict[str, Any], str]], list[Violation]]:
    """Every claim event in replay order, with its provenance.

    Two sources are merged exactly as ``governance/dispatch`` writes them: the
    frozen legacy single-file ledger first (``.board/claims.jsonl``), then the
    one-file-per-event directory (``.board/claims/``) in lexical order — the
    filename embeds the nanosecond write time, so lexical order *is* write order.
    """
    violations: list[Violation] = []
    events: list[tuple[dict[str, Any], str]] = []

    legacy = root / CLAIMS_FILE_RELPATH
    if legacy.is_file():
        try:
            lines = legacy.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            violations.append(
                Violation(
                    CODE_UNRESOLVED_REFERENCE,
                    CLAIMS_FILE_RELPATH,
                    f"claim ledger unreadable ({exc})",
                )
            )
            lines = []
        for lineno, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            where = f"{CLAIMS_FILE_RELPATH}:{lineno}"
            try:
                obj = json.loads(line)
            except ValueError as exc:
                violations.append(
                    Violation(CODE_UNRESOLVED_REFERENCE, where, f"malformed claim record ({exc})")
                )
                continue
            events.append((obj, where))

    claims_dir = root / CLAIMS_DIR_RELPATH
    if claims_dir.is_dir():
        for path in sorted(claims_dir.glob("*.json")):
            where = _rel(root, path)
            try:
                obj = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                violations.append(
                    Violation(CODE_UNRESOLVED_REFERENCE, where, f"unreadable claim record ({exc})")
                )
                continue
            events.append((obj, where))
    return events, violations


def read_claims(
    root: Path | str, board: dict[int, dict[str, Any]]
) -> tuple[list[Contribution], list[Violation]]:
    """``status`` (dispatch) and ``owner`` (isolation) from the claim ledger.

    A claim for an issue the board snapshot does not carry is a **failure naming
    the file**, never a skip: a claim that joins nothing is a broken edge.
    """
    root = Path(root)
    events, violations = _claim_events(root)
    holders: dict[int, tuple[str, str]] = {}
    released: dict[int, str] = {}

    for obj, where in events:
        if not isinstance(obj, dict):
            violations.append(Violation(CODE_UNRESOLVED_REFERENCE, where, "claim record is not an object"))
            continue
        number = obj.get("issue")
        if not isinstance(number, int) or isinstance(number, bool):
            violations.append(
                Violation(CODE_UNRESOLVED_REFERENCE, where, "claim record carries no integer issue")
            )
            continue
        if number not in board:
            violations.append(
                Violation(
                    CODE_UNRESOLVED_REFERENCE,
                    f"#{number}",
                    "claim for an issue the board snapshot does not carry",
                    where,
                )
            )
            continue
        event = obj.get("event")
        if not isinstance(event, str):
            violations.append(
                Violation(CODE_UNRESOLVED_REFERENCE, where, f"claim record for #{number} has no event")
            )
            continue
        if event in _HOLD_EVENTS:
            holders[number] = (str(obj.get("agent") or ""), where)
        elif event in _CLEAR_EVENTS:
            holders.pop(number, None)
            released[number] = where

    contributions: list[Contribution] = []
    status_by_issue: dict[int, tuple[str, str]] = {}
    for number in sorted(set(holders) | set(released)):
        issue = board[number]
        if str(issue.get("state", "")).strip().lower() == "closed":
            # ``done`` is a verdict, not a self-report: the board says the work
            # landed, so a stale claim does not reopen it (the claim gate audits
            # a claim on a closed issue separately).
            where = holders[number][1] if number in holders else released[number]
            status_by_issue[number] = ("done", where)
        elif number in holders:
            status_by_issue[number] = ("in-progress", holders[number][1])
        else:
            status_by_issue[number] = ("in-review", released[number])

    for number in sorted(status_by_issue):
        value, where = status_by_issue[number]
        contributions.append(
            Contribution(issue_id(number), "status", PRODUCER_DISPATCH, value, where)
        )
    for number in sorted(holders):
        agent, where = holders[number]
        if agent:
            contributions.append(Contribution(issue_id(number), "owner", PRODUCER_ISOLATION, agent, where))
    return contributions, violations


# -- lessons register --------------------------------------------------------

@dataclass
class LessonIndex:
    """Indexes over the lessons register the raid facet and the joins need."""

    rca_of_incident: dict[str, str] = field(default_factory=dict)
    incident_of_rca: dict[str, str] = field(default_factory=dict)
    cas_by_rca: dict[str, list[str]] = field(default_factory=dict)
    lesson_by_rca: dict[str, list[str]] = field(default_factory=dict)
    open_cas_by_issue: dict[int, list[str]] = field(default_factory=dict)


def _ledger_records(
    root: Path,
) -> tuple[list[tuple[dict[str, Any], str]], list[Violation]]:
    records: list[tuple[dict[str, Any], str]] = []
    violations: list[Violation] = []
    path = root / LESSONS_RELPATH
    if not path.is_file():
        return records, violations
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        where = f"{LESSONS_RELPATH}:{lineno}"
        try:
            obj = json.loads(line)
        except ValueError as exc:
            violations.append(
                Violation(CODE_UNRESOLVED_REFERENCE, where, f"malformed ledger record ({exc})")
            )
            continue
        if not isinstance(obj, dict) or not isinstance(obj.get("id"), str) or not obj["id"]:
            violations.append(
                Violation(CODE_UNRESOLVED_REFERENCE, where, "ledger record carries no id")
            )
            continue
        records.append((obj, where))
    return records, violations


def _ledger_facet(record: dict[str, Any], index: LessonIndex) -> dict[str, Any]:
    """The ``facets.lessons`` join for one ledger record, per contract v2."""
    kind = record.get("kind")
    own = record["id"]
    incident: Any = None
    rca: Any = None
    if kind == "incident":
        incident = own
        rca = index.rca_of_incident.get(own)
    elif kind == "rca":
        rca = own
        incident = record.get("incident")
    elif kind == "corrective-action":
        rca = record.get("rca")
        incident = index.incident_of_rca.get(str(rca))
    elif kind == "lesson":
        rca = record.get("rca")
        incident = index.incident_of_rca.get(str(rca))

    facet: dict[str, Any] = {}
    if isinstance(incident, str) and incident:
        facet["incident"] = incident
    if isinstance(rca, str) and rca:
        facet["rca"] = rca
        actions = index.cas_by_rca.get(rca)
        if actions:
            facet["corrective_actions"] = sorted(actions)
    if kind == "lesson":
        solution_class = record.get("class")
        if isinstance(solution_class, str) and solution_class:
            facet["class"] = solution_class
    return facet


def _ticket_kind(record: dict[str, Any]) -> str:
    ledger_kind = record.get("kind")
    if ledger_kind == "lesson":
        return "suggestion" if str(record["id"]).startswith("SUGGEST-") else "lesson"
    return _LEDGER_TICKET_KIND.get(str(ledger_kind), "task")


def read_lessons(
    root: Path | str,
    board: dict[int, dict[str, Any]],
) -> tuple[list[Contribution], list[Violation], list[Warning], LessonIndex]:
    """``kind`` + ``facets.lessons`` from the lessons register.

    A record whose ``origin`` is an **issue** reference must resolve in the board
    snapshot; an unresolvable edge is a failure that names the ledger file and
    line. A ``pr`` origin is deliberately *not* a ticket edge — a pull request is
    not a ticket, and the fleet's issue and PR numbering share one sequence, so
    coercing one into the other would invent an edge.

    The register is read **defensively**: ``kind``, ``origin`` and the typed-edge
    fields are read with ``.get`` and type-checked, so a field that is absent — or
    an edge vocabulary a parallel lane adds later (issue #402 extends this same
    ledger) — is treated as *absent*, never as an error. Unknown top-level keys
    are ignored and only the frozen v2 facet vocabulary is emitted, so the
    projection is correct both before and after that vocabulary lands.
    """
    root = Path(root)
    records, violations = _ledger_records(root)
    warnings: list[Warning] = []
    index = LessonIndex()

    by_id: dict[str, tuple[dict[str, Any], str]] = {}
    for record, where in records:
        if record["id"] in by_id:
            warnings.append(
                ProjectionWarning(
                    CODE_LEDGER_DUPLICATE,
                    record["id"],
                    "the register carries this id more than once; "
                    f"the first record ({by_id[record['id']][1]}) is projected",
                    where,
                )
            )
            continue
        by_id[record["id"]] = (record, where)

    for record, _where in by_id.values():
        kind = record.get("kind")
        if kind == "rca" and isinstance(record.get("incident"), str):
            index.rca_of_incident.setdefault(record["incident"], record["id"])
            index.incident_of_rca[record["id"]] = record["incident"]
        elif kind == "corrective-action" and isinstance(record.get("rca"), str):
            index.cas_by_rca.setdefault(record["rca"], []).append(record["id"])
        elif kind == "lesson" and isinstance(record.get("rca"), str):
            index.lesson_by_rca.setdefault(record["rca"], []).append(record["id"])

    # origin edges: only an issue origin is a ticket edge
    incidents_by_issue: dict[int, list[tuple[str, str]]] = {}
    for record, where in by_id.values():
        origin = record.get("origin")
        if not isinstance(origin, dict) or origin.get("kind") != "issue":
            continue
        match = RE_ISSUE_REF.match(str(origin.get("ref", "")).strip())
        if not match:
            violations.append(
                Violation(
                    CODE_UNRESOLVED_REFERENCE,
                    f"{record['id']} → {origin.get('ref')!r}",
                    "issue origin is not an issue reference",
                    where,
                )
            )
            continue
        number = int(match.group(1))
        if number not in board:
            violations.append(
                Violation(
                    CODE_UNRESOLVED_REFERENCE,
                    f"{record['id']} → #{number}",
                    "lesson names an issue the board snapshot does not carry",
                    where,
                )
            )
            continue
        if record.get("kind") == "incident":
            incidents_by_issue.setdefault(number, []).append((record["id"], where))

    contributions: list[Contribution] = []

    # ledger nodes: the register is addressable as tickets of their own kind
    for record, where in by_id.values():
        ticket = record["id"]
        contributions.append(
            Contribution(ticket, "kind", PRODUCER_LESSONS, _ticket_kind(record), where)
        )
        facet = _ledger_facet(record, index)
        if facet:
            contributions.append(
                Contribution(ticket, "facets.lessons", PRODUCER_LESSONS, facet, where)
            )

    # the issue-side join: incidents whose origin names the issue
    for number in sorted(incidents_by_issue):
        linked = sorted(incidents_by_issue[number])
        chosen_id, chosen_where = linked[0]
        if len(linked) > 1:
            warnings.append(
                ProjectionWarning(
                    CODE_LESSON_JOIN_AMBIGUOUS,
                    f"#{number}",
                    "the lessons register links %d incidents (%s); the facet carries the lowest "
                    "and each incident stays addressable as its own ticket node"
                    % (len(linked), ", ".join(item for item, _ in linked)),
                    chosen_where,
                )
            )
        incident = by_id[chosen_id][0]
        facet = _ledger_facet(incident, index)
        rca = facet.get("rca")
        if isinstance(rca, str):
            for lesson_id in sorted(index.lesson_by_rca.get(rca, [])):
                solution_class = by_id[lesson_id][0].get("class")
                if isinstance(solution_class, str) and solution_class:
                    facet["class"] = solution_class
                    break
        if facet:
            contributions.append(
                Contribution(
                    issue_id(number), "facets.lessons", PRODUCER_LESSONS, facet, chosen_where
                )
            )

        # open corrective actions, for the derived raid facet's remediation
        for ca_id in sorted(index.cas_by_rca.get(str(rca), [])):
            ca_record = by_id[ca_id][0]
            if str(ca_record.get("status", "")).lower() == "open":
                index.open_cas_by_issue.setdefault(number, []).append(ca_id)

    return contributions, violations, warnings, index


# -- budget rail -------------------------------------------------------------

def read_budgets(
    root: Path | str, board: dict[int, dict[str, Any]]
) -> tuple[list[Contribution], list[Violation]]:
    """``facets.budget`` from the budget rail's optional ticket ledger.

    The rail is a rollup store with no per-ticket file today, so the projection
    reads a documented JSON Lines ledger when one exists
    (``telemetry/budgets/ledger.jsonl``, one record per ticket charge) and
    contributes nothing when it does not. Absence of data is not a failure.
    """
    root = Path(root)
    path = root / BUDGET_LEDGER_RELPATH
    if not path.is_file():
        return [], []
    contributions: list[Contribution] = []
    violations: list[Violation] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        where = f"{BUDGET_LEDGER_RELPATH}:{lineno}"
        try:
            record = json.loads(line)
        except ValueError as exc:
            violations.append(
                Violation(CODE_UNRESOLVED_REFERENCE, where, f"malformed budget record ({exc})")
            )
            continue
        if not isinstance(record, dict):
            violations.append(
                Violation(CODE_UNRESOLVED_REFERENCE, where, "budget record is not an object")
            )
            continue
        number = issue_number(str(record.get("ticket", "")))
        if number is None or number not in board:
            violations.append(
                Violation(
                    CODE_UNRESOLVED_REFERENCE,
                    short_ref(str(record.get("ticket") or "")),
                    "budget charge names an issue the board snapshot does not carry",
                    where,
                )
            )
            continue
        value: dict[str, Any] = {}
        scope = record.get("scope")
        if isinstance(scope, dict):
            value["scope"] = {
                "level": scope.get("level"),
                "id": scope.get("id"),
            }
        for key in ("spent", "cap"):
            if isinstance(record.get(key), (int, float)) and not isinstance(record.get(key), bool):
                value[key] = record[key]
        if isinstance(record.get("receipt"), str) and record["receipt"]:
            value["receipt"] = record["receipt"]
        contributions.append(
            Contribution(issue_id(number), "facets.budget", PRODUCER_BUDGETS, value, where)
        )
    return contributions, violations


# -- gate attestations -------------------------------------------------------

def read_attestations(
    root: Path | str, board: dict[int, dict[str, Any]]
) -> tuple[list[Contribution], list[ProjectionWarning]]:
    """``evidence`` receipts from the gate attestation of the lane's own branch.

    ``evidence`` is not authority-tracked (ADR-0014): it is appended by whichever
    lane ran the proof, so this producer is named for the artifact, not a lane.
    It is therefore *optional* — an unreadable attestation, or one naming an
    issue the committed snapshot does not carry (a stale snapshot is normal: the
    board is refreshed mid-``make verify``), is reported as a warning naming the
    file rather than failing the whole projection.
    """
    root = Path(root)
    path = root / ATTESTATION_RELPATH
    if not path.is_file():
        return [], []
    payload = _read_json_object(path)
    if not isinstance(payload, dict):
        return [], [
            ProjectionWarning(
                CODE_EVIDENCE_UNREADABLE,
                ATTESTATION_RELPATH,
                "gate attestation is not a JSON object; no receipt is attached",
            )
        ]
    match = RE_BRANCH_ISSUE.match(str(payload.get("branch") or ""))
    if not match:
        return [], []
    number = int(match.group(1))
    if number not in board:
        return [], [
            ProjectionWarning(
                CODE_EVIDENCE_UNATTACHED,
                f"#{number}",
                "gate attestation names a branch for an issue the board snapshot does "
                "not carry; no receipt is attached",
                ATTESTATION_RELPATH,
            )
        ]
    raw_result = payload.get("result")
    if raw_result in (0, "0", False):
        result = "PASS"
    elif raw_result in (1, "1", True):
        result = "FAIL"
    else:
        result = "CANNOT-ASSESS"
    receipt = {
        "kind": "gate-run",
        "ref": str(payload.get("git_sha") or "unknown"),
        "result": result,
        "checks": int(payload.get("check_count") or 0),
    }
    return [Contribution(issue_id(number), "evidence", PRODUCER_ATTESTATION, receipt, ATTESTATION_RELPATH)], []


# -- derived RAID ------------------------------------------------------------

def read_derived(
    board: dict[int, dict[str, Any]], index: LessonIndex
) -> list[Contribution]:
    """``facets.raid`` — *derived*, not written by a lane (ADR-0014).

    The risk the ticket carries comes from its priority label; the remediation is
    the lowest-numbered **open** corrective action the register links to it, when
    there is one. A ticket with no risk signal carries no raid facet at all.
    """
    contributions: list[Contribution] = []
    for number, issue in sorted(board.items()):
        labels = [str(label) for label in (issue.get("labels") or [])]
        risk = ""
        for label in labels:
            priority = label.split(":", 1)[1].strip() if ":" in label else label.strip()
            if label.startswith("priority:") and priority in _PRIORITY_RISK:
                risk = _PRIORITY_RISK[priority]
                break
        if not risk:
            continue
        facet: dict[str, Any] = {"risk": risk}
        open_actions = sorted(index.open_cas_by_issue.get(number, []))
        if open_actions:
            facet["remediation"] = open_actions[0]
        contributions.append(
            Contribution(issue_id(number), "facets.raid", PRODUCER_DERIVED, facet, BOARD_RELPATH)
        )
    return contributions
