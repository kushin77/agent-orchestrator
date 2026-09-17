"""The frozen dispatch record shapes, validated with the repo's stdlib-only subset validator (issue #885).

Three shapes leave this package for disk (a ``ClaimEvent`` in the claims
ledger, an ``Issue`` row in the board snapshot, the ``queue.yaml`` wave
document) plus the new audit record (``audit.py``). Before this module existed
none of the four was frozen anywhere: `model.py`'s ``to_json``/``parse_*``
functions were the only proof of the shape, and nothing stopped them drifting
from each other.

Rather than declare a second, competing JSON-Schema validator, this module
reuses `governance/modules/schema.py`'s stdlib-only subset validator (the
convention `guardrails/policy` and `gateway/sme-routing` already established) —
imported, never re-implemented. If it ever stops being importable this module
fails loudly (:class:`SchemaUnavailable`) rather than silently falling back to
an unenforced shape.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Mapping, Tuple

_PKG_DIR = Path(__file__).resolve().parent
if str(_PKG_DIR) not in sys.path:
    sys.path.insert(0, str(_PKG_DIR))

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from governance.modules import schema as _modules_schema  # noqa: E402
from governance.modules.model import CannotAssess  # noqa: E402

DEFAULT_SCHEMA = _PKG_DIR / "dispatch.schema.json"

SHAPE_CLAIM_EVENT = "claimEvent"
SHAPE_ISSUE_ROW = "issueRow"
SHAPE_QUEUE_DOCUMENT = "queueDocument"
SHAPE_AUDIT_RECORD = "auditRecord"
SHAPES = (SHAPE_CLAIM_EVENT, SHAPE_ISSUE_ROW, SHAPE_QUEUE_DOCUMENT, SHAPE_AUDIT_RECORD)


class SchemaUnavailable(CannotAssess):
    """The frozen schema is missing, malformed, or names an undeclared shape."""


class SchemaViolation(CannotAssess):
    """A document does not satisfy its frozen shape."""


def load(path: Path | str | None = None) -> Mapping[str, Any]:
    """Read and self-check the frozen schema document."""
    path = Path(path) if path else DEFAULT_SCHEMA
    try:
        return _modules_schema.load(path)
    except _modules_schema.SchemaUnavailable as exc:
        raise SchemaUnavailable(str(exc)) from exc


def _shape(document: Mapping[str, Any], shape: str, path: Path | str | None) -> Mapping[str, Any]:
    if shape not in SHAPES:
        raise SchemaUnavailable(f"{shape!r} is not one of the declared shapes {SHAPES}")
    defs = document.get("$defs") or {}
    if shape not in defs:
        raise SchemaUnavailable(f"the frozen schema {path or DEFAULT_SCHEMA} declares no $defs/{shape}")
    return defs[shape]


def problems(document: Any, shape: str, path: Path | str | None = None) -> Tuple[str, ...]:
    """Every violation of ``document`` against the named frozen shape."""
    schema = load(path)
    sub = _shape(schema, shape, path)
    found: list[str] = []
    # `_validate` is the module's private worker; reused rather than
    # duplicated, with the full schema document as the `$ref` root so
    # `$defs/region`, `$defs/provenance` etc. resolve.
    _modules_schema._validate(document, sub, schema, [], found)  # noqa: SLF001
    return tuple(sorted(set(found)))


def validate(document: Any, shape: str, path: Path | str | None = None) -> None:
    """Raise :class:`SchemaViolation` naming every violation, or return silently."""
    found = problems(document, shape, path)
    if found:
        raise SchemaViolation(
            "the document violates the frozen shape $defs/{}: {}".format(shape, "; ".join(found))
        )
