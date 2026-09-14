"""The registry's append-only audit trail (issue #591).

The registry's refusals *are* its compliance signal, and until this module
existed they were printed by a script and lost: nothing durable recorded that the
registry refused a name, or which state it judged a name to be in. This module
turns the document the registry just built into a **durable, deterministic,
append-only** record set:

* :func:`records` projects a built document into one record per refusal and one
  per judged name. The dispositions are **not** hard-coded here — they come from
  the declared acceptance policy (`policy.py`), so the trail says what the policy
  declared rather than what this module assumed. A refusal that reached the
  document unstamped (no declared condition) is refused rather than recorded:
  a record nobody judged is worse than no record, because it looks like evidence.
* :func:`render` is the canonical serialisation — compact JSON, sorted keys, one
  object per line, no clock and no absolute path, so two builds over one revision
  produce byte-identical records.
* :func:`append` writes them and then **proves the trail grew rather than
  changed**: it re-reads the file and requires the bytes it read first to be an
  exact prefix of what is on disk afterwards. A trail that is rewritten (by a
  second writer, by a truncating open, by a bug) fails the write instead of
  silently replacing the evidence. A trail carrying another schema tag is refused
  rather than mixed into.

The record shape is frozen by ``module-registry.schema.json``
(``$defs.auditRecord``), so a consumer can rely on the keys whether the trail was
written today or a year ago.

Exit-code contract (repository convention): a trail that cannot be read or safely
appended to raises :class:`AuditUnavailable`, a
:class:`~governance.modules.model.CannotAssess` — the CLI maps it to exit 2.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

from governance.modules import policy as acceptance
from governance.modules.model import NOT_A_MODULE, STATES, CannotAssess

#: The schema tag every record carries.
SCHEMA = "ao.module-registry/audit-v1"

#: The two kinds of record: a refusal (a defect) and a judgment (evidence).
KIND_REFUSAL = "refusal"
KIND_JUDGMENT = "judgment"
KINDS = (KIND_REFUSAL, KIND_JUDGMENT)

#: The frozen record shape. Every record carries every key; absent values are
#: explicit nulls so a consumer never has to guess whether a key exists.
RECORD_KEYS = (
    "schema",
    "kind",
    "subject",
    "code",
    "state",
    "condition",
    "disposition",
    "detail",
    "source",
)


class AuditUnavailable(CannotAssess):
    """The audit trail cannot be read, or cannot be appended to safely."""


def _record(**kw: Any) -> Dict[str, Any]:
    record = {key: None for key in RECORD_KEYS}
    record.update(kw)
    record["schema"] = SCHEMA
    missing = [key for key in RECORD_KEYS if key not in record]
    if missing:  # pragma: no cover - the dict above is built from RECORD_KEYS
        raise AuditUnavailable("the record shape lost {}".format(", ".join(missing)))
    return record


def records(doc: Mapping[str, Any], controls: acceptance.Policy) -> List[Dict[str, Any]]:
    """One record per refusal, then one per judged name — in a stable order.

    The order is the document's own order (refusals are sorted by the registry,
    modules and refusals-not-modules by id), so the trail is reproducible without
    re-sorting here.
    """
    out: List[Dict[str, Any]] = []

    for finding in doc.get("refusals") or []:
        code = str(finding.get("code") or "")
        disposition = str(finding.get("disposition") or "")
        condition = str(finding.get("condition") or "")
        if not disposition or not condition:
            raise AuditUnavailable(
                "the refusal {} reached the audit trail unjudged (no declared "
                "condition) — the registry must stamp every refusal from the "
                "acceptance policy before it is recorded".format(code or finding.get("subject"))
            )
        if not controls.is_fatal(disposition):
            # Belt and braces: `policy.load` refuses this, and a refusal that
            # does not fail the gate must never be recorded as if it did.
            raise AuditUnavailable(
                "the refusal {} carries disposition {!r}, which is not {}".format(
                    code, disposition, acceptance.DISPOSITION_FATAL
                )
            )
        out.append(
            _record(
                kind=KIND_REFUSAL,
                subject=str(finding.get("subject") or ""),
                code=code,
                condition=condition,
                disposition=disposition,
                detail=str(finding.get("detail") or ""),
                source=str(finding.get("source") or ""),
            )
        )

    for entry in doc.get("modules") or []:
        state = str(entry.get("state") or "")
        if state not in STATES:
            raise AuditUnavailable(
                "the entry {} carries state {!r}, which the policy cannot judge".format(
                    entry.get("id"), state
                )
            )
        out.append(
            _record(
                kind=KIND_JUDGMENT,
                subject=str(entry.get("id") or ""),
                state=state,
                disposition=controls.judged_disposition(state),
                detail=str(entry.get("note") or ""),
            )
        )

    for entry in doc.get("not_modules") or []:
        state = str(entry.get("state") or "")
        if state != NOT_A_MODULE:
            raise AuditUnavailable(
                "the refused name {} carries state {!r}, expected {!r}".format(
                    entry.get("id"), state, NOT_A_MODULE
                )
            )
        out.append(
            _record(
                kind=KIND_JUDGMENT,
                subject=str(entry.get("id") or ""),
                state=state,
                disposition=controls.judged_disposition(NOT_A_MODULE),
                detail=str(entry.get("detail") or ""),
            )
        )

    return out


def summary(records_in: Sequence[Mapping[str, Any]]) -> Dict[str, int]:
    """``{"refusal": n, "judgment": m}`` — what the trail holds, by kind."""
    counts = {kind: 0 for kind in KINDS}
    for record in records_in:
        kind = str(record.get("kind") or "")
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def render(records_in: Sequence[Mapping[str, Any]]) -> str:
    """The canonical serialisation: compact JSON, sorted keys, one line each."""
    return "".join(
        json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n" for record in records_in
    )


def _check_shape(record: Any, line: int, path: Path) -> Dict[str, Any]:
    if not isinstance(record, Mapping):
        raise AuditUnavailable(
            "the audit trail {} is not a record set: line {} is not an object".format(path, line)
        )
    if record.get("schema") != SCHEMA:
        raise AuditUnavailable(
            "the audit trail {} carries schema {!r} on line {}, expected {!r} — a "
            "trail of another shape is refused, never mixed into".format(
                path, record.get("schema"), line, SCHEMA
            )
        )
    missing = [key for key in RECORD_KEYS if key not in record]
    if missing:
        raise AuditUnavailable(
            "the audit trail {} line {} is missing {}".format(path, line, ", ".join(missing))
        )
    unknown = sorted(set(record) - set(RECORD_KEYS))
    if unknown:
        raise AuditUnavailable(
            "the audit trail {} line {} carries unexpected key(s) {}".format(
                path, line, ", ".join(unknown)
            )
        )
    if record.get("kind") not in KINDS:
        raise AuditUnavailable(
            "the audit trail {} line {} carries kind {!r}, expected one of {}".format(
                path, line, record.get("kind"), ", ".join(KINDS)
            )
        )
    return dict(record)


def read(path: Path) -> List[Dict[str, Any]]:
    """Every record in a trail, in file order; a malformed trail raises."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise AuditUnavailable("the audit trail {} is unreadable: {}".format(path, exc)) from exc
    out: List[Dict[str, Any]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError as exc:
            raise AuditUnavailable(
                "the audit trail {} line {} is not JSON: {}".format(path, number, exc)
            ) from exc
        out.append(_check_shape(record, number, path))
    return out


def append(path: Path, records_in: Sequence[Mapping[str, Any]]) -> int:
    """Append records, and prove the trail was appended to rather than rewritten.

    Returns the number of records written. The pre-existing bytes must survive as
    an exact prefix — checked after the write, from the file itself, so the
    guarantee is measured rather than asserted.
    """
    path = Path(path)
    before = b""
    if path.is_file():
        try:
            before = path.read_bytes()
        except OSError as exc:
            raise AuditUnavailable("the audit trail {} is unreadable: {}".format(path, exc)) from exc
        if before:
            # Never mix into a trail of another shape (or a malformed one).
            read(path)
    payload = render(records_in).encode("utf-8")
    try:
        with path.open("ab") as handle:
            handle.write(payload)
    except OSError as exc:
        raise AuditUnavailable("the audit trail {} cannot be appended to: {}".format(path, exc)) from exc
    try:
        after = path.read_bytes()
    except OSError as exc:  # pragma: no cover - the write above just succeeded
        raise AuditUnavailable("the audit trail {} vanished: {}".format(path, exc)) from exc
    if not after.startswith(before) or len(after) != len(before) + len(payload):
        raise AuditUnavailable(
            "the audit trail {} is not append-only: the {} byte(s) read before the "
            "write are not an exact prefix of the {} byte(s) on disk after "
            "it".format(path, len(before), len(after))
        )
    return len(records_in)
