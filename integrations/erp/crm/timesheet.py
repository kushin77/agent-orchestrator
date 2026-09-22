"""Timesheet accumulation against projects and tasks (issue #650, AC2).

Acceptance criterion 2 requires that "timesheet entries accumulate against
projects/tasks". Accumulation is a **derived value**, never a stored total: a
rollup is computed from the documents that exist *now*, so it cannot disagree
with them. There is no counter to increment and therefore no counter to drift —
the same discipline ``governance/pmo`` applies to its rollups.

Five rules, each of which is a way a naive total goes wrong, and each of which
refuses by name:

* ``unknown-document`` / ``unknown-task`` — the project or the task being
  accumulated against does not exist. A rollup against a missing parent is a
  total nobody can audit, so it is refused rather than returned as zero.
* ``inactive-parent`` — the project or task is not in one of its kind's declared
  ``openStates``. Work booked against a completed project is exactly the leak a
  cost report must not contain.
* ``wrong-project`` — the task belongs to a different project than the one being
  accumulated, or a counted entry names a different project than its task. Both
  are the same class of mistake and both name the ids involved.
* ``duplicate-entry`` — one timesheet id is counted twice. Accepting the second
  would silently double a cost.
* ``currency-mismatch`` — the counted entries do not all agree on one currency.
  Summing minor units across currencies produces a number that means nothing;
  the refusal names both codes.

Only entries in ``COUNTED_STATES`` are accumulated. A ``draft`` timesheet has
not been logged yet and a ``rejected`` one was refused, so counting either would
inflate a cost report with work that was never accepted. Money is integer minor
units throughout and the per-line amount is :func:`amount_minor`, whose rounding
rule (half up, applied once per line) is documented and pinned by tests — a float
total is a total that differs between two machines.

---knowledge---
module_id: integrations.erp.crm.timesheet
system: integrations
app: erp
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [amount_minor, Line, Rollup, accumulate]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .definitions import DefinitionSet
from .model import (
    KIND_PROJECT,
    KIND_TASK,
    KIND_TIMESHEET,
    Document,
    Refused,
)
from .workflow import accepts_child_work

#: Minutes in one logged day. An entry above this is a data-entry error.
MINUTES_MAX = 1440

#: The timesheet states that count toward a rollup, in the declaration's own
#: vocabulary: submitted work is owed, approved work is settled, and draft or
#: rejected work is neither.
COUNTED_STATES: Tuple[str, ...] = ("approved", "submitted")


def amount_minor(minutes: int, rate_minor: int) -> int:
    """The cost of ``minutes`` at ``rate_minor`` per hour, rounded half up.

    Integer-only: ``(minutes * rate_minor + 30) // 60``. Half-up rounds a
    half-minute up, so a total never under-reports by a cent, and the rounding
    is applied once per line (never to a sum of already-rounded parts).
    """
    return (minutes * rate_minor + 30) // 60


@dataclass(frozen=True)
class Line:
    """One counted timesheet line, with the rate it was priced at."""

    entry: str
    task: str
    work_date: str
    minutes: int
    rate_minor: int
    amount_minor: int

    def to_dict(self) -> Dict[str, object]:
        return {
            "entry": self.entry,
            "task": self.task,
            "workDate": self.work_date,
            "minutes": self.minutes,
            "rateMinor": self.rate_minor,
            "amountMinor": self.amount_minor,
        }


@dataclass(frozen=True)
class Rollup:
    """A project/task cost rollup, derived from the documents it names."""

    project: str
    task: str
    currency: str
    minutes: int
    amount_minor: int
    lines: Tuple[Line, ...]

    def to_dict(self) -> Dict[str, object]:
        return {
            "project": self.project,
            "task": self.task,
            "currency": self.currency,
            "minutes": self.minutes,
            "amountMinor": self.amount_minor,
            "lines": [line.to_dict() for line in self.lines],
        }


def _fail(code: str, detail: str) -> "Refused":
    return Refused(code, detail)


def _index(documents: Iterable[Document]) -> Dict[str, Document]:
    index: Dict[str, Document] = {}
    for document in documents:
        if document.id in index:
            raise _fail(
                "duplicate-entry",
                f"{document.id} appears more than once in the document set "
                f"(kinds {index[document.id].kind} and {document.kind})",
            )
        index[document.id] = document
    return index


def _require(index: Mapping[str, Document], doc_id: str, kind: str) -> Document:
    document = index.get(doc_id)
    if document is None:
        if kind == KIND_TASK:
            raise _fail("unknown-task", f"no task {doc_id!r} in the document set")
        raise _fail("unknown-document", f"no {kind} {doc_id!r} in the document set")
    if document.kind != kind:
        raise _fail(
            "unknown-document",
            f"{doc_id!r} is a {document.kind}, not a {kind}",
        )
    return document


def _require_open(document: Document, definitions: DefinitionSet) -> None:
    if not accepts_child_work(document, definitions):
        raise _fail(
            "inactive-parent",
            f"{document.id} is a {document.kind} in state {document.state!r}, which "
            f"does not accept child work (open states: "
            f"{', '.join(definitions.kind(document.kind).open_states) or 'none'})",
        )


def _resolve(
    *names: Optional[object], where: str, code: str, label: str
) -> str:
    for name in names:
        if isinstance(name, str) and name:
            return name
    raise _fail(code, f"{where}: no usable {label} is declared by the entry, task or project")


def accumulate(
    documents: Sequence[Document],
    *,
    project: str,
    task: str,
    definitions: DefinitionSet,
) -> Rollup:
    """Accumulate every counted entry against ``task`` inside ``project``."""
    index = _index(documents)
    project_document = _require(index, project, KIND_PROJECT)
    task_document = _require(index, task, KIND_TASK)

    _require_open(project_document, definitions)
    _require_open(task_document, definitions)

    declared_project = task_document.fields.get("project")
    if declared_project != project_document.id:
        raise _fail(
            "wrong-project",
            f"{task_document.id} belongs to project {declared_project!r}, not "
            f"{project_document.id!r}",
        )

    candidates: List[Document] = [
        document
        for document in documents
        if document.kind == KIND_TIMESHEET
        and document.state in COUNTED_STATES
        and document.fields.get("task") == task_document.id
    ]
    # Deterministic order: by work date, then by id. Two runs of the same input
    # therefore produce byte-identical rollups.
    candidates.sort(key=lambda document: (str(document.fields.get("work_date")), document.id))

    declared_currency = project_document.fields.get("currency")
    task_currency = task_document.fields.get("currency")
    task_rate = task_document.fields.get("rate_minor")

    currency: Optional[str] = declared_currency if isinstance(declared_currency, str) else None
    if task_currency:
        currency = _agree(
            currency,
            task_currency,
            where=task_document.id,
            code="currency-mismatch",
        )

    lines: List[Line] = []
    for entry in candidates:
        entry_project = entry.fields.get("project")
        if entry_project != project_document.id:
            raise _fail(
                "wrong-project",
                f"{entry.id} is booked to project {entry_project!r}, not "
                f"{project_document.id!r}",
            )
        minutes = entry.fields.get("minutes")
        if isinstance(minutes, bool) or not isinstance(minutes, int):
            raise _fail(
                "invalid-value", f"{entry.id}.minutes: expected an integer, got {minutes!r}"
            )
        if not 1 <= minutes <= MINUTES_MAX:
            raise _fail(
                "invalid-value",
                f"{entry.id}.minutes: {minutes} is outside 1..{MINUTES_MAX}",
            )
        entry_currency = entry.fields.get("currency")
        if entry_currency:
            currency = _agree(
                currency, entry_currency, where=entry.id, code="currency-mismatch"
            )
        entry_rate = entry.fields.get("rate_minor")
        rate = entry_rate if isinstance(entry_rate, int) and not isinstance(entry_rate, bool) else None
        if rate is None:
            rate = task_rate if isinstance(task_rate, int) and not isinstance(task_rate, bool) else None
        if rate is None:
            raise _fail(
                "invalid-value",
                f"{entry.id}: no rate can be resolved — neither the entry nor "
                f"{task_document.id} declares rate_minor",
            )
        if rate < 0:
            raise _fail("invalid-value", f"{entry.id}.rate_minor: {rate} is negative")
        lines.append(
            Line(
                entry=entry.id,
                task=task_document.id,
                work_date=str(entry.fields.get("work_date", "")),
                minutes=minutes,
                rate_minor=rate,
                amount_minor=amount_minor(minutes, rate),
            )
        )

    resolved_currency = _resolve(
        currency,
        where=f"{project_document.id}/{task_document.id}",
        code="invalid-value",
        label="currency",
    )
    return Rollup(
        project=project_document.id,
        task=task_document.id,
        currency=resolved_currency,
        minutes=sum(line.minutes for line in lines),
        amount_minor=sum(line.amount_minor for line in lines),
        lines=tuple(lines),
    )


def _agree(current: Optional[str], candidate: str, *, where: str, code: str) -> str:
    if current is None:
        return candidate
    if current != candidate:
        raise _fail(
            code,
            f"{where}: currency {candidate!r} disagrees with {current!r} already "
            "resolved for this rollup",
        )
    return current
