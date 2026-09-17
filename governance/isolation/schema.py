"""Validate this surface's persisted records against `isolation.schema.json` (issue #885).

Reuses the stdlib-only JSON-Schema subset validator
``governance/modules/schema.py`` already implements (issue #591) rather than
re-implementing one: this module only knows *which* ``$defs`` entry corresponds
to which record shape (lane identity, speculative attestation, landed-baseline
entry, journal entry) and delegates the actual walk to it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Tuple

from governance.modules import schema as _subset

DEFAULT_SCHEMA = Path(__file__).resolve().parent / "isolation.schema.json"

LANE_IDENTITY = "lane_identity"
SPECULATIVE_ATTESTATION = "speculative_attestation"
LANDED_BASELINE_ENTRY = "landed_baseline_entry"
JOURNAL_ENTRY = "journal_entry"

KINDS = (LANE_IDENTITY, SPECULATIVE_ATTESTATION, LANDED_BASELINE_ENTRY, JOURNAL_ENTRY)


class RecordSchemaViolation(ValueError):
    """A persisted record does not satisfy its frozen shape."""


def _document(path: Path | None = None) -> Dict[str, Any]:
    schema_path = Path(path) if path else DEFAULT_SCHEMA
    text = schema_path.read_text(encoding="utf-8")
    document = json.loads(text)
    _subset.check_schema(document, schema_path)
    return document


def problems(kind: str, record: Any, path: Path | None = None) -> Tuple[str, ...]:
    """Every violation of ``record`` against the named ``$defs`` shape, sorted."""
    if kind not in KINDS:
        raise RecordSchemaViolation(f"{kind!r} is not one of the declared record kinds: {KINDS}")
    document = _document(path)
    sub_schema = document["$defs"][kind]
    return _subset.problems(record, sub_schema)


def validate(kind: str, record: Any, path: Path | None = None) -> None:
    """Raise :class:`RecordSchemaViolation` unless ``record`` satisfies its shape."""
    found = problems(kind, record, path)
    if not found:
        return
    shown = "; ".join(found[:3])
    if len(found) > 3:
        shown += f" (+{len(found) - 3} more)"
    raise RecordSchemaViolation(
        f"record does not satisfy the frozen {kind!r} shape in isolation.schema.json: "
        f"{len(found)} violation(s): {shown}"
    )
