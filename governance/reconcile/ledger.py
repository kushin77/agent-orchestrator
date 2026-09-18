"""The reconciliation ledger — an append-only trail of every decision (#885).

`sweep()` decides, per session, whether to reclaim, park, shelve, report, fail
— or, now, refuse under the batch-limit control (`policy.py`). And
`real_tree_baseline.check_real_tree()` renders a verdict on the whole disk
against the reviewed baseline. Until this module existed, both were visible
only in a process's stdout for the run that produced them: a decision made
last night by a cron tick left no record a human (or a later gate run) could
read back.

This module is deliberately dumb, on purpose, the same way `heartbeat.py` is:
one JSON object per line, appended, never rewritten and never truncated —
`.fleet/reconcile/ledger.jsonl` under the reconciled repository root. Every
record is validated against the frozen shape (`reconcile.schema.json`,
`ledger-record` def, read through `governance.modules.schema` — reused, not
copied) **before** it is written: a record this package cannot itself
validate is a record nobody else can trust, so :func:`append` refuses it
rather than writing a document that would fail its own schema.

Two record kinds, one file:

* ``sweep-decision`` — one per `Action` a sweep pass produces (including a
  refused one), written by `sweep.py` at the point the decision is made;
* ``real-tree-verdict`` — one per `check_real_tree()` call, written by
  `real_tree_baseline.py`.

Exit-code contract: a record that fails schema validation, or a ledger file
that cannot be appended to, raises :class:`LedgerUnavailable` — this package
never silently drops a decision it was asked to record.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from governance.modules import schema as frozen_schema  # noqa: E402

#: Where the ledger lives, relative to the reconciled repository root.
LEDGER_RELPATH = Path(".fleet") / "reconcile" / "ledger.jsonl"

#: The frozen shape every record is validated against.
SCHEMA_PATH = Path(__file__).resolve().parent / "reconcile.schema.json"

SWEEP_DECISION = "sweep-decision"
REAL_TREE_VERDICT = "real-tree-verdict"


class LedgerUnavailable(ValueError):
    """A record could not be validated or the ledger could not be appended to.

    A :class:`ValueError` subclass so ``cli.py``'s generic ``ValueError`` ->
    CANNOT-ASSESS mapping covers it, same tri-state contract as the rest of
    this package.
    """


def _iso(at: float) -> str:
    return datetime.fromtimestamp(at, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _record_schema() -> Mapping[str, Any]:
    document = frozen_schema.load(SCHEMA_PATH)
    try:
        return document["$defs"]["ledger-record"]
    except (KeyError, TypeError) as exc:  # pragma: no cover - the schema is packaged
        raise LedgerUnavailable(f"{SCHEMA_PATH} has no $defs/ledger-record") from exc


def ledger_path(root: Path | str) -> Path:
    return Path(root) / LEDGER_RELPATH


def validate_record(record: Mapping[str, Any]) -> None:
    """Raise :class:`LedgerUnavailable` unless ``record`` satisfies the frozen shape."""
    found = frozen_schema.problems(record, _record_schema())
    if found:
        shown = "; ".join(found[:3])
        raise LedgerUnavailable(
            f"record does not satisfy reconcile.schema.json#/$defs/ledger-record "
            f"({len(found)} violation(s)): {shown}"
        )


def append(root: Path | str, record: Mapping[str, Any]) -> dict:
    """Validate ``record`` and append it as one line. Returns the written dict.

    Append-only: the file is opened in append mode and never rewritten. A
    record that fails validation is never written — a corrupt ledger line is
    worse than a missing one, because it would look load-bearing.
    """
    document = dict(record)
    document.setdefault("schema", "ao.reconcile/ledger-record-v1")
    validate_record(document)
    path = ledger_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(document, sort_keys=True) + "\n")
    except OSError as exc:
        raise LedgerUnavailable(f"{path} could not be appended to: {exc}") from exc
    return document


def record_sweep_decision(
    root: Path | str,
    *,
    session_id: str,
    issue: int,
    agent: str,
    outcome: str,
    code: str,
    reason: str,
    at: float | None = None,
) -> dict:
    import time

    moment = time.time() if at is None else at
    return append(
        root,
        {
            "kind": SWEEP_DECISION,
            "at": moment,
            "at_iso": _iso(moment),
            "session_id": session_id,
            "issue": int(issue),
            "agent": agent,
            "outcome": outcome,
            "code": code,
            "reason": reason,
        },
    )


def record_real_tree_verdict(root: Path | str, verdict, *, at: float | None = None) -> dict:
    import time

    moment = time.time() if at is None else at
    return append(
        root,
        {
            "kind": REAL_TREE_VERDICT,
            "at": moment,
            "at_iso": _iso(moment),
            "assessable": bool(verdict.assessable),
            "ok": bool(verdict.ok) if verdict.assessable else False,
            "new_violations": len(verdict.new_violations),
            "stale_entries": len(verdict.stale_entries),
            "young": len(verdict.young),
            "vanished": len(getattr(verdict, "vanished", ()) or ()),
            # Named, leased exemptions (#1291): without these the ledger would
            # show a green verdict with no trace of what was excused.
            "quarantined": len(getattr(verdict, "quarantined", ()) or ()),
            "stale_quarantine": len(getattr(verdict, "stale_quarantine", ()) or ()),
            # Exemptions that are honoured but no longer needed (#1311): the work
            # they were protecting is on the default branch. Fatal, and recorded
            # here so a shrinking document is visible in the trail even after the
            # edit that removed them.
            "refuted_quarantine": len(getattr(verdict, "refuted_quarantine", ()) or ()),
            # Exemptions that are not in force in the venue this verdict was
            # measured in (#1321): reported, honouring nothing. Their count and
            # the venue make "which checkout was this read in?" answerable from
            # the audit trail alone.
            "inapplicable_quarantine": len(
                getattr(verdict, "inapplicable_quarantine", ()) or ()
            ),
            "venue": str(getattr(verdict, "venue", "") or ""),
        },
    )


def read(root: Path | str) -> list[dict]:
    path = ledger_path(root)
    if not path.exists():
        return []
    records = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except ValueError as exc:
            raise LedgerUnavailable(f"{path}:{lineno} is not valid JSON: {exc}") from exc
    return records


def verify(root: Path | str) -> tuple[bool, str]:
    """Every line parses and validates against the frozen shape.

    Returns ``(ok, description)``. Used by the gate's schema-invalid
    provocation: a hand-corrupted line must make this ``False``, named.
    """
    path = ledger_path(root)
    if not path.exists():
        return True, f"{path}: no ledger yet"
    problems: list[str] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError as exc:
            problems.append(f"line {lineno}: not valid JSON: {exc}")
            continue
        found = frozen_schema.problems(record, _record_schema())
        if found:
            problems.append(f"line {lineno}: {'; '.join(found)}")
    if problems:
        return False, f"{path}: {len(problems)} invalid record(s): " + " | ".join(problems[:5])
    return True, f"{path}: OK"
