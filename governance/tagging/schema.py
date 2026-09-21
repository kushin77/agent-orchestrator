"""Validate the authority's artifacts against their frozen shapes (issue #1175).

---knowledge---
module_id: governance.tagging.schema
system: governance
app: tagging
solution_class: class
patterns: [offline-hermetic, deterministic]
derives_from: governance/modules/schema.py
owner_sme: platform-sme
tier: L1
interfaces: [SchemaUnavailable, shapes, problems, validate, as_json]
invariants: ""
gotchas: ""
related: ["#1175"]
do_not_duplicate: null
---knowledge---

`tagging.schema.json` freezes the shape of the taxonomy, the rules, the controls
and one ledger row. This module is the thin reader that applies it, reusing the
repository's stdlib-only JSON-Schema subset validator
(:mod:`governance.modules.schema`) rather than adding a third-party dependency —
the same convention ``guardrails/policy`` and ``gateway/sme-routing`` follow, and
the reason the gate stays offline and deterministic.

The subtlety worth naming: a document is validated as ``{shape: document}``
against the **root** schema, not against the sub-schema. Validating the
sub-schema directly would move the `$ref` resolution root with it, so
``#/properties/gate`` would stop resolving — and a `$ref` that cannot resolve is
exactly the "requirement nobody measures" the validator's own contract refuses.
"""

from __future__ import annotations

import json
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Tuple

# The repository root, so the shared validator resolves as `governance.modules.schema`
# and not as a bare `modules.schema` — the package it lives in imports its own
# sibling absolutely (`from governance.modules.model import ...`), so the root is
# the only correct entry on the path.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import governance.modules.schema as _shared  # noqa: E402

DEFAULT_SCHEMA = Path(__file__).resolve().parent / "tagging.schema.json"

SHAPES = ("taxonomy", "rules", "controls", "ledger_row")


class SchemaUnavailable(_shared.SchemaUnavailable):
    """The shape file itself is unreadable or uses an unsupported keyword."""


@lru_cache(maxsize=1)
def _root_schema(path: str = "") -> Mapping[str, Any]:
    target = Path(path) if path else DEFAULT_SCHEMA
    document = _shared.load(target)
    _shared.check_schema(document, target)
    return document


def shapes() -> Tuple[str, ...]:
    """The shape names this schema declares."""
    declared = tuple((_root_schema().get("properties") or {}).keys())
    # `gate` is a reusable sub-shape, not a document shape.
    return tuple(name for name in declared if name in SHAPES)


def problems(document: Any, shape: str, path: Path | None = None) -> Tuple[str, ...]:
    """Every violation of ``document`` against the named shape, or a refusal."""
    if shape not in SHAPES:
        raise SchemaUnavailable(
            "unknown shape %r — this schema declares %s" % (shape, ", ".join(SHAPES))
        )
    schema = _root_schema(str(path) if path else "")
    # Wrap the document so the `$ref` root is the schema, not the sub-schema.
    return _shared.problems({shape: document}, schema)


def validate(document: Any, shape: str, path: Path | None = None) -> None:
    """Raise :class:`SchemaUnavailable` naming every violation, or return."""
    found = problems(document, shape, path)
    if found:
        raise SchemaUnavailable(
            "%s does not satisfy the frozen %s shape:\n  - %s"
            % (shape, shape, "\n  - ".join(found))
        )


def as_json() -> str:
    """The schema as pretty JSON — what `cli.py schema` prints."""
    return json.dumps(_root_schema(), indent=2, sort_keys=False)
