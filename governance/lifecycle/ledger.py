"""The lifecycle's append-only decision trail (issue #885).

``governance/lifecycle/audit.py`` already carries an audit — the *board hygiene*
audit, which names every item that did not close cleanly. This is a different
trail, at a different layer: not "what is broken on the board" but "what this
package itself decided", one line per close/retire/consume outcome, written by
the refusal and success sites themselves (``directive.py``, ``cli.py``) rather
than reconstructed after the fact from log scrollback.

Two properties carried over from ``governance/modules/audit.py`` (issue #591),
the reference shape this package is held to:

* **every record is schema-validated before it is written.** A record that
  does not satisfy ``lifecycle.schema.json``'s ``ledgerRecord`` shape is
  refused rather than appended — a malformed record is worse than no record,
  because it looks like evidence.
* **append proves the trail grew, never changed.** :func:`append` re-reads the
  file after writing and requires the bytes it read *before* the write to be an
  exact prefix of what is on disk afterwards. A trail rewritten by a second
  writer, a truncating open, or a bug fails the write instead of silently
  replacing evidence.

Exit-code contract (repository convention): an unwritable, unvalidatable, or
disturbed ledger raises :class:`LedgerUnavailable` — the lifecycle CLI's
``RuntimeError`` -> CANNOT-ASSESS mapping applies.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from governance.modules import schema as _schema

#: The schema tag every record carries.
SCHEMA = "ao.lifecycle/ledger-v1"

KIND_REFUSAL = "refusal"
KIND_DECISION = "decision"
KINDS = (KIND_REFUSAL, KIND_DECISION)

OUTCOME_OK = "ok"
OUTCOME_REFUSED = "refused"

#: The frozen record shape, as a schema document ``governance.modules.schema``
#: can validate against directly: a synthetic root that ``$ref``s the real
#: ``$defs/ledgerRecord`` declared in ``lifecycle.schema.json``, carrying that
#: file's own ``$defs`` map so the pointer resolves. The stdlib-only validator
#: (``governance/modules/schema.py``, issue #591) is reused rather than
#: re-implemented, per the reference shape this package is held to.
LIFECYCLE_SCHEMA_PATH = Path(__file__).resolve().parent / "lifecycle.schema.json"

#: Overridable for the gate's own schema-drift provocation (a scratch copy of
#: the schema with the shape loosened or broken).
SCHEMA_ENV = "AO_LIFECYCLE_SCHEMA"

#: Default location of the trail, relative to a repository root. Runtime
#: state, like ``.fleet/lifecycle/<issue>.json`` beside it — never committed.
DEFAULT_LEDGER = Path(".fleet") / "lifecycle" / "ledger.jsonl"


class LedgerUnavailable(RuntimeError):
    """The ledger could not be validated, written, or safely appended to."""


def _schema_path() -> Path:
    import os

    override = os.environ.get(SCHEMA_ENV, "").strip()
    return Path(override) if override else LIFECYCLE_SCHEMA_PATH


def _record_schema(def_name: str) -> Dict[str, Any]:
    path = _schema_path()
    try:
        full = _schema.load(path)
    except _schema.SchemaUnavailable as exc:
        raise LedgerUnavailable(str(exc)) from exc
    defs = full.get("$defs") or {}
    if def_name not in defs:
        raise LedgerUnavailable(
            "the frozen schema {} declares no $defs/{}".format(path, def_name)
        )
    return {"$schema": _schema.DIALECT, "$ref": "#/$defs/{}".format(def_name), "$defs": defs}


def render(*, kind: str, action: str, subject: str, outcome: str, detail: str, code: str = "") -> Dict[str, Any]:
    """Build one ledger record. Raises :class:`LedgerUnavailable` for a shape the
    schema does not accept — before anything is written."""
    if kind not in KINDS:
        raise LedgerUnavailable("unknown ledger record kind {!r}; expected one of {}".format(kind, KINDS))
    if outcome not in (OUTCOME_OK, OUTCOME_REFUSED):
        raise LedgerUnavailable("unknown ledger outcome {!r}".format(outcome))
    record = {
        "schema": SCHEMA,
        "kind": kind,
        "action": str(action),
        "subject": str(subject),
        "code": str(code),
        "outcome": outcome,
        "detail": str(detail),
    }
    schema_doc = _record_schema("ledgerRecord")
    problems = _schema.problems(record, schema_doc)
    if problems:
        raise LedgerUnavailable(
            "a ledger record for {} would not satisfy the frozen shape: {}".format(action, "; ".join(problems))
        )
    return record


def _line(record: Dict[str, Any]) -> str:
    return json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def append(path: Path, record: Dict[str, Any]) -> Path:
    """Append one validated record, proving the file grew rather than changed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    before = path.read_bytes() if path.exists() else b""
    line = (_line(record) + "\n").encode("utf-8")
    with path.open("ab") as handle:
        handle.write(line)
    after = path.read_bytes()
    if not after.startswith(before) or after != before + line:
        raise LedgerUnavailable(
            "the ledger {} was rewritten by another writer while this record was appended — refusing to "
            "trust it; the file must be re-inspected by hand".format(path)
        )
    return path


def record_decision(
    root: Path,
    *,
    action: str,
    subject: str,
    outcome: str,
    detail: str,
    code: str = "",
    ledger_path: Path | None = None,
) -> Path:
    """Validate and append one decision/refusal record for a close/retire/consume outcome."""
    kind = KIND_REFUSAL if outcome == OUTCOME_REFUSED else KIND_DECISION
    record = render(kind=kind, action=action, subject=subject, outcome=outcome, detail=detail, code=code)
    target = ledger_path if ledger_path is not None else (root / DEFAULT_LEDGER)
    return append(target, record)


def read_all(path: Path) -> list[Dict[str, Any]]:
    """Every record in the trail, in append order. An absent trail is empty, not an error."""
    if not path.exists():
        return []
    records: list[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        records.append(json.loads(line))
    return records


__all__ = [
    "SCHEMA",
    "KIND_REFUSAL",
    "KIND_DECISION",
    "OUTCOME_OK",
    "OUTCOME_REFUSED",
    "LedgerUnavailable",
    "render",
    "append",
    "record_decision",
    "read_all",
]
