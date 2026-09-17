"""Model for the RCA + lessons ledger (issue #141).

The ledger is the *single* authoritative record of what went wrong, why, what
was done about it, and what the organization learned. It lives at
``governance/lessons/ledger.jsonl`` (one JSON object per line). This module owns
the vocabulary and the schema validation; :mod:`checker` owns the detection
logic and :mod:`cli` the operator surface.

Record kinds, one ``kind`` per line:

===============  =============  =============================================
kind             id prefix      meaning
===============  =============  =============================================
``incident``     ``INC-<n>``    a failure, breach, defect or preventable drift
``rca``          ``RCA-<n>``    the root-cause analysis of one incident
``corrective-``  ``CA-<n>``     an action the RCA requires (owned, evidenced)
``action``
``lesson``       ``LESSON-<n>`` a closed learning, proven by commit evidence
``lesson``       ``SUGGEST-<n>`` an open improvement idea (owner + remediation)
===============  =============  =============================================

Every record carries a ``date`` and every RCA carries ``reviewed_at``: a
learning that is never re-read is a document, not a practice, so the checker
fails (as a deviation) once the review cadence lapses.

Findings are stable machine-readable ``code`` values with a ``severity`` and an
actionable ``remediation`` — a violation that names no follow-up is the silent
ignore the issue exists to prevent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

SCHEMA = "cmr.lessons/ledger-v1"

# --- record kinds -----------------------------------------------------------

KIND_INCIDENT = "incident"
KIND_RCA = "rca"
KIND_CORRECTIVE_ACTION = "corrective-action"
KIND_LESSON = "lesson"

KINDS = (KIND_INCIDENT, KIND_RCA, KIND_CORRECTIVE_ACTION, KIND_LESSON)

ID_PREFIXES: Dict[str, Sequence[str]] = {
    KIND_INCIDENT: ("INC-",),
    KIND_RCA: ("RCA-",),
    KIND_CORRECTIVE_ACTION: ("CA-",),
    KIND_LESSON: ("LESSON-", "SUGGEST-"),
}

LESSON_PREFIX_CLOSED = "LESSON-"
LESSON_PREFIX_OPEN = "SUGGEST-"

STATUS_OPEN = "open"
STATUS_CLOSED = "closed"
STATUSES = (STATUS_OPEN, STATUS_CLOSED)

SEVERITIES = ("critical", "high", "medium", "low")

INCIDENT_CLASSES = (
    "false-green",
    "false-completion",
    "duplicate-work",
    "stale-data",
    "drift",
    "outage",
    "security",
    "data-loss",
    "process",
)

#: A lesson names the quality rung it moves the repo to — the same ladder
#: ``governance/conformance`` (issue #140) enforces on the board.
CLASS_LADDER = ("template", "class", "pattern", "enterprise", "faang", "elite")

#: What an RCA or an incident is traceable to. ``issue`` refs are resolved
#: against the committed board snapshot; ``pr`` / ``commit`` refs are validated
#: by shape and by history where the checkout allows it (see ``checker``).
ORIGIN_KINDS = ("issue", "pr", "commit", "event")

EVIDENCE_KINDS = ("commit", "issue", "pr", "artifact", "event")

#: The lessons register is an edge source in the ticket graph (ADR-0014,
#: issue #402): every ledger record is a ticket *node* and every cross-record
#: reference is a *typed edge*, never a free string a reader re-parses on its
#: own. The node kinds are the ticket contract's own ``kind`` enum
#: (``docs/contracts/paperclip/ticket.schema.json``), so an open ``SUGGEST-*``
#: is a ticket of kind ``suggestion`` — addressable like any other node.
TICKET_KIND_TASK = "task"
TICKET_KIND_INCIDENT = "incident"
TICKET_KIND_RCA = "rca"
TICKET_KIND_CORRECTIVE_ACTION = "corrective-action"
TICKET_KIND_LESSON = "lesson"
TICKET_KIND_SUGGESTION = "suggestion"

TICKET_KINDS = (
    TICKET_KIND_TASK,
    TICKET_KIND_INCIDENT,
    TICKET_KIND_RCA,
    TICKET_KIND_CORRECTIVE_ACTION,
    TICKET_KIND_LESSON,
    TICKET_KIND_SUGGESTION,
)

#: Ledger kind to ticket kind, for the ledger kinds that map one-to-one.
#: ``lesson`` is deliberately absent: one ledger kind splits into ``lesson``
#: (a closed ``LESSON-*``) and ``suggestion`` (an open ``SUGGEST-*``); see
#: :func:`ticket_kind_for`.
TICKET_KIND_BY_LEDGER_KIND: Dict[str, str] = {
    KIND_INCIDENT: TICKET_KIND_INCIDENT,
    KIND_RCA: TICKET_KIND_RCA,
    KIND_CORRECTIVE_ACTION: TICKET_KIND_CORRECTIVE_ACTION,
}

#: The ticket graph's closed edge vocabulary (issue #402). This is **not** the
#: cross-reference spine's nine-type vocabulary: the spine admits the subset it
#: can carry (see ``governance/knowledge/crossref.py``) and the rest stay
#: ticket-graph edges. ``origin`` replaces the free-string ``origin`` field and
#: ``remediation-of`` replaces the free-string ``remediation_issue`` field, so
#: no reader resolves either by itself.
EDGE_CAUSED_BY = "caused-by"
EDGE_ORIGIN = "origin"
EDGE_MITIGATES = "mitigates"
EDGE_REMEDIATION_OF = "remediation-of"

TICKET_EDGE_TYPES: Tuple[str, ...] = (
    EDGE_CAUSED_BY,
    EDGE_ORIGIN,
    EDGE_MITIGATES,
    EDGE_REMEDIATION_OF,
)

#: An RCA artifact must carry these section headings, so an "RCA" cannot be a
#: one-line note that says nothing about cause or remedy.
RCA_REQUIRED_SECTIONS = (
    "## Impact",
    "## Detection",
    "## Root cause",
    "## Corrective actions",
    "## Lessons",
    "## Evidence",
)

#: Review cadence: an RCA older than this is surfaced as a deviation.
REVIEW_CADENCE_DAYS = 180

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"

# --- finding codes (stable; the tests assert on these) -----------------------

CODE_LEDGER_INVALID = "ledger-invalid"
CODE_LEDGER_EMPTY = "ledger-empty"
CODE_UNKNOWN_KIND = "unknown-kind"
CODE_DUPLICATE_ID = "duplicate-id"
CODE_RECORD_INCOMPLETE = "record-incomplete"
CODE_INVALID_FIELD = "invalid-field"
CODE_UNKNOWN_REFERENCE = "unknown-reference"
CODE_EDGE_UNRESOLVED = "edge-unresolved"
CODE_INCIDENT_WITHOUT_RCA = "incident-without-rca"
CODE_RCA_WITHOUT_ORIGIN = "rca-without-origin"
CODE_ORIGIN_UNRESOLVED = "origin-unresolved"
CODE_RCA_ARTIFACT_MISSING = "rca-artifact-missing"
CODE_RCA_ARTIFACT_UNTRACKED = "rca-artifact-untracked"
CODE_RCA_ARTIFACT_INCOMPLETE = "rca-artifact-incomplete"
CODE_RCA_WITHOUT_CORRECTIVE_ACTION = "rca-without-corrective-action"
CODE_CORRECTIVE_ACTION_UNRECORDED = "corrective-action-unrecorded"
CODE_CORRECTIVE_ACTION_UNLINKED = "corrective-action-unlinked"
CODE_CORRECTIVE_ACTION_WITHOUT_EVIDENCE = "corrective-action-without-evidence"
CODE_CORRECTIVE_ACTION_WITHOUT_OWNER = "corrective-action-without-owner"
CODE_INCIDENT_CLOSED_WITHOUT_LESSON = "incident-closed-without-lesson"
CODE_LESSON_WITHOUT_EVIDENCE = "lesson-without-evidence"
CODE_LESSON_WITHOUT_COMMIT_EVIDENCE = "lesson-without-commit-evidence"
CODE_LESSON_INVALID_STATUS = "lesson-invalid-status"
CODE_SUGGESTION_WITHOUT_REMEDIATION = "suggestion-without-remediation"
CODE_SUGGESTION_WITHOUT_OWNER = "suggestion-without-owner"
CODE_POLICY_INVALID = "policy-invalid"
CODE_EVIDENCE_UNRESOLVABLE = "evidence-unresolvable"
CODE_BOARD_INCIDENT_WITHOUT_RCA = "board-incident-without-rca"
CODE_BOARD_INCIDENT_PENDING = "board-incident-pending"
CODE_RCA_REVIEW_OVERDUE = "rca-review-overdue"
CODE_CORRECTIVE_ACTION_OPEN = "corrective-action-open"
#: An open action whose own stated closure condition has already been met: the
#: board snapshot the ledger is checked against reports its ``remediation_issue``
#: CLOSED. The record and the board contradict each other, so this is an error
#: rather than the benign ``corrective-action-open`` deviation (#1028).
CODE_CORRECTIVE_ACTION_REMEDIATION_LANDED = "corrective-action-remediation-landed"
CODE_SUGGESTION_OPEN = "suggestion-open"
CODE_DOC_RCA_ID_UNKNOWN = "doc-rca-id-unknown"
CODE_DOC_RCA_ARTIFACT_MISMATCH = "doc-rca-artifact-mismatch"
CODE_README_INCIDENT_COUNT_MISMATCH = "readme-incident-count-mismatch"

#: Required keys per kind. Everything else is optional and preserved as-is.
REQUIRED_FIELDS: Dict[str, Sequence[str]] = {
    KIND_INCIDENT: (
        "id",
        "kind",
        "date",
        "summary",
        "severity",
        "class",
        "origin",
        "status",
    ),
    KIND_RCA: (
        "id",
        "kind",
        "incident",
        "date",
        "origin",
        "artifact",
        "corrective_actions",
        "status",
        "reviewed_at",
    ),
    KIND_CORRECTIVE_ACTION: (
        "id",
        "kind",
        "rca",
        "date",
        "action",
        "status",
    ),
    KIND_LESSON: (
        "id",
        "kind",
        "title",
        "rca",
        "date",
        "class",
        "status",
        "evidence",
    ),
}


def now_iso() -> str:
    """UTC timestamp for reports, second precision, ``Z`` suffix."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def parse_date(value: Any) -> Optional[date]:
    """Parse an ISO ``YYYY-MM-DD`` date, or return ``None``."""
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def days_since(value: Any, today: date) -> Optional[int]:
    """Whole days between an ISO date and ``today``, or ``None`` if unparseable."""
    parsed = parse_date(value)
    if parsed is None:
        return None
    return (today - parsed).days


def is_suggestion(record_id: Any) -> bool:
    """A ``SUGGEST-`` record states an improvement, not a closed lesson."""
    return isinstance(record_id, str) and record_id.startswith(LESSON_PREFIX_OPEN)


def is_lesson(record_id: Any) -> bool:
    """A ``LESSON-`` record is a learning that must be closed with evidence."""
    return isinstance(record_id, str) and record_id.startswith(LESSON_PREFIX_CLOSED)


def ticket_kind_for(record: Dict[str, Any]) -> str:
    """The ticket-graph node kind for one ledger record, or ``""``.

    A ``SUGGEST-*`` is a ``suggestion`` and a ``LESSON-*`` a ``lesson``, so the
    open improvement register is addressable exactly like a closed learning.
    """
    record_id = str(record.get("id", ""))
    if is_suggestion(record_id):
        return TICKET_KIND_SUGGESTION
    if is_lesson(record_id):
        return TICKET_KIND_LESSON
    return TICKET_KIND_BY_LEDGER_KIND.get(str(record.get("kind", "")), "")


@dataclass(frozen=True)
class Finding:
    """One enforcement finding: stable code, severity, subject, remediation."""

    code: str
    message: str
    subject: str = ""
    severity: str = SEVERITY_ERROR
    remediation: str = ""
    line: int = 0

    @property
    def is_error(self) -> bool:
        return self.severity == SEVERITY_ERROR

    def as_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "subject": self.subject,
            "remediation": self.remediation,
        }
        if self.line:
            data["line"] = self.line
        return data

    def render(self) -> str:
        where = " [%s]" % self.subject if self.subject else ""
        fix = " -> %s" % self.remediation if self.remediation else ""
        return "%s%s%s" % (self.message, where, fix)


def errors(findings: Iterable[Finding]) -> List[Finding]:
    """Only the findings that fail the gate."""
    return [f for f in findings if f.is_error]


def warnings(findings: Iterable[Finding]) -> List[Finding]:
    """The reported deviations — real, tracked, not fatal."""
    return [f for f in findings if not f.is_error]


@dataclass(frozen=True)
class Entry:
    """One ledger line: its number, the raw text and the parsed record."""

    line: int
    raw: str
    record: Optional[Dict[str, Any]] = None

    @property
    def id(self) -> str:
        if self.record is None:
            return ""
        return str(self.record.get("id", ""))

    @property
    def kind(self) -> str:
        if self.record is None:
            return ""
        return str(self.record.get("kind", ""))


def as_list(value: Any) -> List[Any]:
    """Coerce a JSON value to a list without inventing one."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def validate_record(entry: Entry) -> List[Finding]:
    """Schema-validate one parsed record, before any cross-record reasoning."""
    record = entry.record
    if record is None:
        return []
    findings: List[Finding] = []
    line = entry.line

    kind = record.get("kind")
    if kind not in KINDS:
        findings.append(
            Finding(
                code=CODE_UNKNOWN_KIND,
                message="record declares unknown kind %r" % (kind,),
                subject=str(record.get("id", "<no id>")),
                line=line,
                remediation="use one of: %s" % ", ".join(KINDS),
            )
        )
        return findings

    for key in REQUIRED_FIELDS[kind]:
        value = record.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            findings.append(
                Finding(
                    code=CODE_RECORD_INCOMPLETE,
                    message="%s record is missing required field %r" % (kind, key),
                    subject=entry.id or "<no id>",
                    line=line,
                    remediation="add %r to the record (see governance/lessons/README.md)"
                    % key,
                )
            )

    record_id = record.get("id")
    if isinstance(record_id, str):
        prefixes = ID_PREFIXES[kind]
        if not record_id.startswith(tuple(prefixes)):
            findings.append(
                Finding(
                    code=CODE_INVALID_FIELD,
                    message="%s id %r has the wrong prefix" % (kind, record_id),
                    subject=record_id,
                    line=line,
                    remediation="use one of: %s<n>" % ", ".join(prefixes),
                )
            )

    if parse_date(record.get("date")) is None:
        findings.append(
            Finding(
                code=CODE_INVALID_FIELD,
                message="date %r is not an ISO YYYY-MM-DD date" % (record.get("date"),),
                subject=entry.id or "<no id>",
                line=line,
                remediation="write the date as YYYY-MM-DD",
            )
        )

    status = record.get("status")
    if status not in STATUSES:
        findings.append(
            Finding(
                code=CODE_INVALID_FIELD,
                message="status %r is not one of %s" % (status, "/".join(STATUSES)),
                subject=entry.id or "<no id>",
                line=line,
                remediation="set status to open or closed",
            )
        )

    if kind == KIND_INCIDENT:
        findings.extend(_validate_incident(entry))
    elif kind == KIND_RCA:
        findings.extend(_validate_rca(entry))
    elif kind == KIND_CORRECTIVE_ACTION:
        findings.extend(_validate_corrective_action(entry))
    elif kind == KIND_LESSON:
        findings.extend(_validate_lesson(entry))

    return findings


def _validate_incident(entry: Entry) -> List[Finding]:
    record = entry.record or {}
    findings: List[Finding] = []
    severity = record.get("severity")
    if severity not in SEVERITIES:
        findings.append(
            Finding(
                code=CODE_INVALID_FIELD,
                message="incident severity %r is not one of %s"
                % (severity, "/".join(SEVERITIES)),
                subject=entry.id,
                line=entry.line,
                remediation="declare a severity from the incident vocabulary",
            )
        )
    incident_class = record.get("class")
    if incident_class not in INCIDENT_CLASSES:
        findings.append(
            Finding(
                code=CODE_INVALID_FIELD,
                message="incident class %r is not one of %s"
                % (incident_class, "/".join(INCIDENT_CLASSES)),
                subject=entry.id,
                line=entry.line,
                remediation="declare a class from the incident vocabulary",
            )
        )
    findings.extend(_validate_origin(entry, record.get("origin")))
    return findings


def _validate_rca(entry: Entry) -> List[Finding]:
    record = entry.record or {}
    findings = _validate_origin(entry, record.get("origin"))
    if parse_date(record.get("reviewed_at")) is None:
        findings.append(
            Finding(
                code=CODE_INVALID_FIELD,
                message="reviewed_at %r is not an ISO YYYY-MM-DD date"
                % (record.get("reviewed_at"),),
                subject=entry.id,
                line=entry.line,
                remediation="stamp the RCA with the date of its last review",
            )
        )
    actions = record.get("corrective_actions")
    if not isinstance(actions, list):
        findings.append(
            Finding(
                code=CODE_INVALID_FIELD,
                message="corrective_actions must be a list of CA ids",
                subject=entry.id,
                line=entry.line,
                remediation="list the corrective-action ids the RCA produced",
            )
        )
    return findings


def _validate_corrective_action(entry: Entry) -> List[Finding]:
    record = entry.record or {}
    evidence = record.get("evidence")
    if evidence is not None and not isinstance(evidence, list):
        return [
            Finding(
                code=CODE_INVALID_FIELD,
                message="evidence must be a list",
                subject=entry.id,
                line=entry.line,
                remediation="give evidence as a list of objects",
            )
        ]
    return []


def _validate_lesson(entry: Entry) -> List[Finding]:
    record = entry.record or {}
    findings: List[Finding] = []
    evidence = record.get("evidence")
    if evidence is not None and not isinstance(evidence, list):
        findings.append(
            Finding(
                code=CODE_INVALID_FIELD,
                message="evidence must be a list",
                subject=entry.id,
                line=entry.line,
                remediation="give evidence as a list of objects",
            )
        )
    elif evidence:
        findings.extend(_validate_evidence(entry, evidence))
    if record.get("class") not in CLASS_LADDER:
        findings.append(
            Finding(
                code=CODE_INVALID_FIELD,
                message="lesson class %r is not a rung of %s"
                % (record.get("class"), " -> ".join(CLASS_LADDER)),
                subject=entry.id,
                line=entry.line,
                remediation="declare the quality rung the lesson raises",
            )
        )
    return findings


def _validate_origin(entry: Entry, origin: Any) -> List[Finding]:
    if not isinstance(origin, dict):
        return [
            Finding(
                code=CODE_INVALID_FIELD,
                message="origin must be an object with kind and ref",
                subject=entry.id,
                line=entry.line,
                remediation='write origin as {"kind": "issue", "ref": "#152"}',
            )
        ]
    if origin.get("kind") not in ORIGIN_KINDS:
        return [
            Finding(
                code=CODE_INVALID_FIELD,
                message="origin kind %r is not one of %s"
                % (origin.get("kind"), "/".join(ORIGIN_KINDS)),
                subject=entry.id,
                line=entry.line,
                remediation="declare an origin kind from the vocabulary",
            )
        ]
    return []


def _validate_evidence(entry: Entry, evidence: Sequence[Any]) -> List[Finding]:
    findings: List[Finding] = []
    for index, item in enumerate(evidence):
        if not isinstance(item, dict):
            findings.append(
                Finding(
                    code=CODE_INVALID_FIELD,
                    message="evidence[%d] is not an object" % index,
                    subject=entry.id,
                    line=entry.line,
                    remediation='write evidence as {"kind": "commit", "ref": "<sha>"}',
                )
            )
            continue
        if item.get("kind") not in EVIDENCE_KINDS:
            findings.append(
                Finding(
                    code=CODE_INVALID_FIELD,
                    message="evidence[%d] kind %r is not one of %s"
                    % (index, item.get("kind"), "/".join(EVIDENCE_KINDS)),
                    subject=entry.id,
                    line=entry.line,
                    remediation="use an evidence kind from the vocabulary",
                )
            )
        if not str(item.get("ref", "")).strip():
            findings.append(
                Finding(
                    code=CODE_INVALID_FIELD,
                    message="evidence[%d] names no ref" % index,
                    subject=entry.id,
                    line=entry.line,
                    remediation="point the evidence at the commit, issue or artifact",
                )
            )
    return findings


@dataclass
class Report:
    """The enforcement report — auditable, machine-readable, replayable."""

    ledger: str
    generated_at: str
    counts: Dict[str, int] = field(default_factory=dict)
    findings: List[Finding] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "schema": SCHEMA,
            "ledger": self.ledger,
            "generated_at": self.generated_at,
            "counts": dict(sorted(self.counts.items())),
            "errors": [f.as_dict() for f in errors(self.findings)],
            "deviations": [f.as_dict() for f in warnings(self.findings)],
        }
